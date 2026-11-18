import uuid
from collections import deque
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import UnitOfWork, verify_and_increment_version
from app.core.deps import TenantContext
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.domain.enums import (
    ActorType,
    BatchDispositionDecision,
    BatchMovementEventType,
    BatchStatus,
    MembershipStatus,
    ProductStatus,
    QualityReviewStatus,
    SecurityEventOutcome,
    SecurityEventSeverity,
    SupplierLotStatus,
)
from app.domain.state_machines import assert_transition
from app.models.domain.governance import ApprovalRecord
from app.models.domain.identity import OrganizationMembership
from app.models.domain.products_materials import Material, Product, SupplierLot
from app.models.domain.traceability import (
    Batch,
    BatchDisposition,
    BatchDocumentLink,
    BatchMaterialConsumption,
    BatchMovementEvent,
    BatchRelationship,
    BatchStatusHistory,
)
from app.models.schemas.traceability import (
    BatchCreateRequest,
    BatchDetailResponse,
    BatchDispositionRequest,
    BatchDocumentLinkCreateRequest,
    BatchMaterialConsumptionCreateRequest,
    BatchMaterialConsumptionVerifyRequest,
    BatchOperationalStatusRequest,
    BatchRelationshipCreateRequest,
    BatchRelationshipResponse,
    BatchUpdateRequest,
)
from app.repositories.document_sop_repo import DocumentSOPRepository
from app.repositories.governance_repo import OutboxRepository
from app.repositories.organization_repo import OrganizationRepository
from app.repositories.product_batch_repo import ProductBatchRepository
from app.repositories.product_material_repo import ProductMaterialRepository
from app.services.audit_service import AuditService

OPERATIONAL_BATCH_TRANSITIONS: Dict[str, Set[str]] = {
    BatchStatus.PLANNED.value: {
        BatchStatus.IN_PROCESS.value,
        BatchStatus.QUARANTINED.value,
    },
    BatchStatus.IN_PROCESS.value: {
        BatchStatus.QUARANTINED.value,
        BatchStatus.UNDER_QUALITY_REVIEW.value,
    },
    BatchStatus.QUARANTINED.value: {
        BatchStatus.UNDER_QUALITY_REVIEW.value,
    },
    BatchStatus.UNDER_QUALITY_REVIEW.value: {
        BatchStatus.QUARANTINED.value,
    },
}

DISPOSITION_CONTROLLED_STATUSES: FrozenSet[str] = frozenset(
    {
        BatchStatus.RELEASED.value,
        BatchStatus.REJECTED.value,
        BatchStatus.REWORKED.value,
        BatchStatus.RECALLED.value,
        BatchStatus.EXPIRED.value,
    }
)

DISPOSITION_ALLOWED_FROM: Dict[str, Set[str]] = {
    BatchDispositionDecision.RELEASED.value: {
        BatchStatus.IN_PROCESS.value,
        BatchStatus.QUARANTINED.value,
        BatchStatus.UNDER_QUALITY_REVIEW.value,
    },
    BatchDispositionDecision.REWORKED.value: {
        BatchStatus.IN_PROCESS.value,
        BatchStatus.QUARANTINED.value,
        BatchStatus.UNDER_QUALITY_REVIEW.value,
    },
    BatchDispositionDecision.QUARANTINED.value: {
        BatchStatus.IN_PROCESS.value,
        BatchStatus.QUARANTINED.value,
        BatchStatus.UNDER_QUALITY_REVIEW.value,
        BatchStatus.RELEASED.value,
    },
    BatchDispositionDecision.REJECTED.value: {
        BatchStatus.IN_PROCESS.value,
        BatchStatus.QUARANTINED.value,
        BatchStatus.UNDER_QUALITY_REVIEW.value,
        BatchStatus.RELEASED.value,
    },
}


class ProductBatchService:
    """Tenant-scoped manufacturing Batch (C3), QA Disposition, Material Consumption & Genealogy service."""

    def __init__(
        self,
        repo: Optional[ProductBatchRepository] = None,
        pm_repo: Optional[ProductMaterialRepository] = None,
        org_repo: Optional[OrganizationRepository] = None,
        doc_repo: Optional[DocumentSOPRepository] = None,
        audit_service: Optional[AuditService] = None,
        outbox_repo: Optional[OutboxRepository] = None,
    ) -> None:
        self._repo = repo or ProductBatchRepository()
        self._pm_repo = pm_repo or ProductMaterialRepository()
        self._org_repo = org_repo or OrganizationRepository()
        self._doc_repo = doc_repo or DocumentSOPRepository()
        self._audit = audit_service or AuditService()
        self._outbox = outbox_repo or OutboxRepository()

    def _assert_site_access(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        site_id: uuid.UUID,
        *,
        source_ip: Optional[str] = None,
        target_resource: str = "/api/v1/batches",
    ) -> None:
        site = self._org_repo.get_site_for_org(db, tenant_ctx.organization_id, site_id)
        if site is None:
            raise NotFoundError("Site not found in this organization.")
        if not tenant_ctx.is_org_wide and site.id not in tenant_ctx.allowed_site_ids:
            self._audit.record_security_event(
                event_type="SITE_SCOPE_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.WARNING.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=source_ip,
                target_resource=target_resource,
                details_json={"site_id": str(site_id)},
            )
            raise PermissionDeniedError(
                "Not authorized to access or manage batches for this site.",
                error_code="SITE_SCOPE_DENIED",
            )

    def _assert_document_in_org(
        self, db: Session, organization_id: uuid.UUID, document_id: Optional[uuid.UUID]
    ) -> None:
        if document_id is None:
            return
        doc = self._doc_repo.get_document_for_org(db, organization_id, document_id)
        if doc is None:
            raise NotFoundError("Referenced document not found in this organization.")

    def _get_batch_or_404_with_site_check(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        *,
        with_children: bool = False,
        source_ip: Optional[str] = None,
        target_resource: Optional[str] = None,
    ) -> Batch:
        batch = self._repo.get_batch_for_org(
            db, tenant_ctx.organization_id, batch_id, with_children=with_children
        )
        if batch is None:
            raise NotFoundError("Batch not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            batch.site_id,
            source_ip=source_ip,
            target_resource=target_resource or f"/api/v1/batches/{batch_id}",
        )
        return batch

    def _get_active_membership(
        self, db: Session, organization_id: uuid.UUID, membership_id: uuid.UUID, label: str
    ) -> OrganizationMembership:
        member = db.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.id == membership_id,
            )
        )
        if member is None:
            raise NotFoundError(f"{label} membership not found in this organization.")
        if member.membership_status != MembershipStatus.ACTIVE.value:
            raise ValidationError(f"{label} membership must be ACTIVE.")
        return member

    # --- Batch CRUD & Operational Lifecycle ---

    def create_batch(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        payload: BatchCreateRequest,
        source_ip: Optional[str] = None,
    ) -> Batch:
        self._assert_site_access(
            db,
            tenant_ctx,
            payload.site_id,
            source_ip=source_ip,
            target_resource="/api/v1/batches",
        )

        product: Optional[Product] = self._pm_repo.get_product_for_org(
            db, tenant_ctx.organization_id, payload.product_id
        )
        if product is None:
            raise NotFoundError("Product not found in this organization.")
        if product.product_status in {ProductStatus.BLOCKED.value, ProductStatus.DISCONTINUED.value}:
            raise ConflictError(
                f"Cannot create batch for product in '{product.product_status}' status.",
                error_code="PRODUCT_NOT_ELIGIBLE",
            )
        if product.site_id is not None and product.site_id != payload.site_id:
            raise ValidationError("Product site_id does not match target batch site_id.")

        self._assert_document_in_org(db, tenant_ctx.organization_id, payload.primary_document_id)

        existing = self._repo.get_batch_by_composite_key(
            db, tenant_ctx.organization_id, payload.site_id, payload.product_id, payload.batch_number
        )
        if existing is not None:
            raise ConflictError(
                f"Batch '{payload.batch_number}' already exists for this site and product.",
                error_code="DUPLICATE_BATCH_NUMBER",
            )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            batch = Batch(
                organization_id=tenant_ctx.organization_id,
                site_id=payload.site_id,
                product_id=payload.product_id,
                batch_number=payload.batch_number.strip(),
                manufacturing_order_no=(
                    payload.manufacturing_order_no.strip() if payload.manufacturing_order_no else None
                ),
                manufacturing_date=payload.manufacturing_date,
                expiry_date=payload.expiry_date,
                batch_size_value=payload.batch_size_value,
                batch_size_unit=payload.batch_size_unit.strip(),
                distributed_quantity=Decimal("0.0000"),
                packaging_configuration=payload.packaging_configuration,
                batch_status=payload.initial_status,
                quality_review_status=QualityReviewStatus.PENDING.value,
                primary_document_id=payload.primary_document_id,
                master_batch_record_id=payload.master_batch_record_id,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(batch)
            db.flush()

            db.add(
                BatchStatusHistory(
                    organization_id=tenant_ctx.organization_id,
                    batch_id=batch.id,
                    from_status=None,
                    to_status=batch.batch_status,
                    changed_by_user_id=tenant_ctx.user_id,
                    changed_by_membership_id=tenant_ctx.membership_id,
                    reason=payload.reason or f"Batch {batch.batch_number} created.",
                    occurred_at=now,
                )
            )
            db.add(
                BatchMovementEvent(
                    organization_id=tenant_ctx.organization_id,
                    batch_id=batch.id,
                    event_type=BatchMovementEventType.PRODUCED.value,
                    quantity=batch.batch_size_value,
                    unit_code=batch.batch_size_unit,
                    actor_user_id=tenant_ctx.user_id,
                    occurred_at=now,
                    notes=f"Initial batch registration ({batch.batch_status}).",
                )
            )
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=batch.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="BATCH_CREATED",
                entity_type="batches",
                entity_id=batch.id,
                reason=payload.reason,
                source_ip=source_ip,
                after_state={
                    "batch_number": batch.batch_number,
                    "product_id": str(batch.product_id),
                    "batch_status": batch.batch_status,
                },
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="batch",
                aggregate_id=batch.id,
                event_type="BatchCreated",
                payload_json={
                    "batch_id": str(batch.id),
                    "batch_number": batch.batch_number,
                    "product_id": str(batch.product_id),
                    "site_id": str(batch.site_id),
                    "batch_status": batch.batch_status,
                },
            )
        db.refresh(batch)
        return batch

    def list_batches(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        site_id: Optional[uuid.UUID] = None,
        product_id: Optional[uuid.UUID] = None,
        batch_status: Optional[str] = None,
        quality_review_status: Optional[str] = None,
        batch_number: Optional[str] = None,
        q: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Batch], int]:
        return self._repo.list_batches_for_tenant(
            db,
            tenant_ctx,
            site_id=site_id,
            product_id=product_id,
            batch_status=batch_status,
            quality_review_status=quality_review_status,
            batch_number=batch_number,
            q=q,
            page=page,
            page_size=page_size,
        )

    def get_batch_detail(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> BatchDetailResponse:
        batch = self._get_batch_or_404_with_site_check(
            db, tenant_ctx, batch_id, with_children=True, source_ip=source_ip
        )
        parent_rels, child_rels = self._repo.list_relationships_for_batch(
            db, tenant_ctx.organization_id, batch.id
        )
        detail = BatchDetailResponse.model_validate(batch)
        detail.parent_relationships = [BatchRelationshipResponse.model_validate(r) for r in parent_rels]
        detail.child_relationships = [BatchRelationshipResponse.model_validate(r) for r in child_rels]
        return detail

    def _apply_operational_status_change(
        self,
        db: Session,
        *,
        batch: Batch,
        tenant_ctx: TenantContext,
        target_status: str,
        reason: Optional[str],
        now: datetime,
    ) -> None:
        if target_status in DISPOSITION_CONTROLLED_STATUSES:
            raise ConflictError(
                "Batch release, rejection, or rework decisions must use POST /batches/{batch_id}/disposition.",
                error_code="DISPOSITION_ENDPOINT_REQUIRED",
            )
        prev_status = batch.batch_status
        assert_transition(
            OPERATIONAL_BATCH_TRANSITIONS,
            prev_status,
            target_status,
            entity_name="Batch",
        )
        batch.batch_status = target_status
        if target_status == BatchStatus.UNDER_QUALITY_REVIEW.value:
            batch.quality_review_status = QualityReviewStatus.IN_REVIEW.value
        elif target_status == BatchStatus.QUARANTINED.value:
            batch.quality_review_status = QualityReviewStatus.HOLD.value

        db.add(
            BatchStatusHistory(
                organization_id=tenant_ctx.organization_id,
                batch_id=batch.id,
                from_status=prev_status,
                to_status=target_status,
                changed_by_user_id=tenant_ctx.user_id,
                changed_by_membership_id=tenant_ctx.membership_id,
                reason=reason or f"Operational status changed from {prev_status} to {target_status}.",
                occurred_at=now,
            )
        )

    def update_batch(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        payload: BatchUpdateRequest,
        source_ip: Optional[str] = None,
    ) -> Batch:
        batch = self._get_batch_or_404_with_site_check(
            db,
            tenant_ctx,
            batch_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/batches/{batch_id}",
        )

        # Explicitly reject attempts to sneak QA disposition decisions through PATCH
        if payload.batch_status is not None and payload.batch_status != batch.batch_status:
            if payload.batch_status in DISPOSITION_CONTROLLED_STATUSES:
                raise ConflictError(
                    "Batch release, rejection, or rework decisions must use POST /batches/{batch_id}/disposition.",
                    error_code="DISPOSITION_ENDPOINT_REQUIRED",
                )

        if batch.batch_status in {
            BatchStatus.RELEASED.value,
            BatchStatus.REJECTED.value,
            BatchStatus.RECALLED.value,
            BatchStatus.EXPIRED.value,
        }:
            raise ConflictError(
                f"Batch in '{batch.batch_status}' status cannot be modified.",
                error_code="BATCH_IMMUTABLE_IN_STATUS",
            )

        updates = payload.model_dump(exclude_unset=True, exclude={"row_version", "reason"})
        if "expiry_date" in updates and updates["expiry_date"] <= batch.manufacturing_date:
            raise ValidationError("expiry_date must be strictly after manufacturing_date.")
        if "primary_document_id" in updates:
            self._assert_document_in_org(db, tenant_ctx.organization_id, updates["primary_document_id"])

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            verify_and_increment_version(batch, payload.row_version)
            before_status = batch.batch_status
            for field in (
                "manufacturing_order_no",
                "expiry_date",
                "batch_size_value",
                "batch_size_unit",
                "actual_yield_value",
                "packaging_configuration",
                "primary_document_id",
                "master_batch_record_id",
            ):
                if field in updates:
                    val = updates[field]
                    if field == "batch_size_unit" and isinstance(val, str):
                        val = val.strip()
                    setattr(batch, field, val)

            if payload.batch_status is not None and payload.batch_status != before_status:
                self._apply_operational_status_change(
                    db,
                    batch=batch,
                    tenant_ctx=tenant_ctx,
                    target_status=payload.batch_status,
                    reason=payload.reason,
                    now=now,
                )

            batch.updated_by_user_id = tenant_ctx.user_id
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=batch.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="BATCH_UPDATED",
                entity_type="batches",
                entity_id=batch.id,
                reason=payload.reason,
                source_ip=source_ip,
                before_state={"batch_status": before_status},
                after_state={"batch_status": batch.batch_status, "row_version": batch.row_version},
            )
        db.refresh(batch)
        return batch

    def transition_operational_status(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        payload: BatchOperationalStatusRequest,
        source_ip: Optional[str] = None,
    ) -> Batch:
        batch = self._get_batch_or_404_with_site_check(
            db,
            tenant_ctx,
            batch_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/batches/{batch_id}/status",
        )
        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            verify_and_increment_version(batch, payload.row_version)
            prev_status = batch.batch_status
            self._apply_operational_status_change(
                db,
                batch=batch,
                tenant_ctx=tenant_ctx,
                target_status=payload.to_status,
                reason=payload.reason,
                now=now,
            )
            batch.updated_by_user_id = tenant_ctx.user_id
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=batch.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="BATCH_OPERATIONAL_STATUS_CHANGED",
                entity_type="batches",
                entity_id=batch.id,
                reason=payload.reason,
                source_ip=source_ip,
                before_state={"batch_status": prev_status},
                after_state={"batch_status": batch.batch_status},
            )
        db.refresh(batch)
        return batch

    # --- QA Disposition (Dual Maker-Checker) ---

    def record_disposition(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        payload: BatchDispositionRequest,
        source_ip: Optional[str] = None,
    ) -> BatchDisposition:
        batch = self._get_batch_or_404_with_site_check(
            db,
            tenant_ctx,
            batch_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/batches/{batch_id}/disposition",
        )

        decision = payload.disposition_decision.value
        allowed_from = DISPOSITION_ALLOWED_FROM.get(decision, set())
        if batch.batch_status not in allowed_from:
            raise ConflictError(
                f"Cannot apply disposition '{decision}' from batch status '{batch.batch_status}'.",
                error_code="INVALID_STATE_TRANSITION",
                details={
                    "current_state": batch.batch_status,
                    "target_decision": decision,
                    "allowed_from": sorted(allowed_from),
                },
            )

        # Dual DB + Service maker-checker: distinct membership AND distinct user_id
        proposer = self._get_active_membership(
            db, tenant_ctx.organization_id, payload.proposed_by_membership_id, "Proposer"
        )
        decider = self._get_active_membership(
            db, tenant_ctx.organization_id, tenant_ctx.membership_id, "Decider"
        )
        if proposer.id == decider.id or proposer.user_id == decider.user_id:
            self._audit.record_security_event(
                event_type="MAKER_CHECKER_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/batches/{batch_id}/disposition",
                details_json={
                    "batch_id": str(batch_id),
                    "proposed_by_membership_id": str(proposer.id),
                    "decided_by_membership_id": str(decider.id),
                },
            )
            raise PermissionDeniedError(
                "Maker-checker violation: batch disposition proposer and decider must be distinct users.",
                error_code="MAKER_CHECKER_VIOLATION",
            )

        if decision == BatchDispositionDecision.RELEASED.value:
            if (
                not payload.checklist.bmr_reviewed
                or not payload.checklist.qc_passed
                or payload.checklist.open_critical_deviations
            ):
                raise ValidationError(
                    "Batch release requires bmr_reviewed=true, qc_passed=true, and open_critical_deviations=false.",
                    error_code="INCOMPLETE_DISPOSITION_CHECKLIST",
                )
        elif decision in {
            BatchDispositionDecision.REJECTED.value,
            BatchDispositionDecision.QUARANTINED.value,
        }:
            if not payload.hold_or_rejection_reason or not payload.hold_or_rejection_reason.strip():
                raise ValidationError(
                    f"hold_or_rejection_reason is required for '{decision}' disposition."
                )

        self._assert_document_in_org(db, tenant_ctx.organization_id, payload.certificate_document_id)

        now = datetime.now(timezone.utc)
        if decision == BatchDispositionDecision.RELEASED.value:
            qr_status = QualityReviewStatus.APPROVED.value
            movement_type = BatchMovementEventType.RELEASED.value
        elif decision == BatchDispositionDecision.REJECTED.value:
            qr_status = QualityReviewStatus.REJECTED.value
            movement_type = BatchMovementEventType.QUARANTINE_HOLD.value
        elif decision == BatchDispositionDecision.QUARANTINED.value:
            qr_status = QualityReviewStatus.HOLD.value
            movement_type = BatchMovementEventType.QUARANTINE_HOLD.value
        else:
            qr_status = QualityReviewStatus.HOLD.value
            movement_type = BatchMovementEventType.REWORK.value

        reason_clean = (
            payload.hold_or_rejection_reason.strip() if payload.hold_or_rejection_reason else None
        )

        with UnitOfWork(db):
            verify_and_increment_version(batch, payload.row_version)
            prev_status = batch.batch_status

            approval = ApprovalRecord(
                organization_id=tenant_ctx.organization_id,
                entity_type="batch",
                entity_id=batch.id,
                approval_stage="BATCH_DISPOSITION",
                decision=decision,
                approver_membership_id=decider.id,
                decided_at=now,
                comments=payload.comments or reason_clean,
            )
            db.add(approval)
            db.flush()

            disposition = BatchDisposition(
                organization_id=tenant_ctx.organization_id,
                site_id=batch.site_id,
                batch_id=batch.id,
                from_status=prev_status,
                disposition_decision=decision,
                quality_review_status=qr_status,
                checklist_json=payload.checklist.model_dump(mode="json"),
                hold_or_rejection_reason=reason_clean,
                proposed_by_membership_id=proposer.id,
                decided_by_membership_id=decider.id,
                approval_record_id=approval.id,
                certificate_document_id=payload.certificate_document_id,
                decided_at=now,
                comments=payload.comments,
            )
            db.add(disposition)
            db.flush()

            batch.batch_status = decision
            batch.quality_review_status = qr_status
            batch.hold_or_rejection_reason = (
                None if decision == BatchDispositionDecision.RELEASED.value else reason_clean
            )
            if decision == BatchDispositionDecision.RELEASED.value:
                batch.released_at = now
                batch.released_by_membership_id = decider.id
            batch.updated_by_user_id = tenant_ctx.user_id

            db.add(
                BatchStatusHistory(
                    organization_id=tenant_ctx.organization_id,
                    batch_id=batch.id,
                    from_status=prev_status,
                    to_status=decision,
                    changed_by_user_id=tenant_ctx.user_id,
                    changed_by_membership_id=decider.id,
                    reason=reason_clean or payload.comments or f"Batch disposition decided: {decision}.",
                    occurred_at=now,
                )
            )
            db.add(
                BatchMovementEvent(
                    organization_id=tenant_ctx.organization_id,
                    batch_id=batch.id,
                    event_type=movement_type,
                    quantity=batch.actual_yield_value if batch.actual_yield_value is not None else batch.batch_size_value,
                    unit_code=batch.batch_size_unit,
                    actor_user_id=tenant_ctx.user_id,
                    occurred_at=now,
                    notes=f"QA disposition {decision}" + (f": {reason_clean}" if reason_clean else ""),
                )
            )
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=batch.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="BATCH_DISPOSITION_DECIDED",
                entity_type="batch_dispositions",
                entity_id=disposition.id,
                reason=reason_clean or payload.comments,
                source_ip=source_ip,
                before_state={"batch_status": prev_status},
                after_state={
                    "batch_status": batch.batch_status,
                    "disposition_decision": decision,
                    "quality_review_status": qr_status,
                    "approval_record_id": str(approval.id),
                },
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="batch",
                aggregate_id=batch.id,
                event_type="BatchDispositionDecided",
                payload_json={
                    "batch_id": str(batch.id),
                    "batch_number": batch.batch_number,
                    "disposition_id": str(disposition.id),
                    "disposition_decision": decision,
                    "quality_review_status": qr_status,
                },
            )
        db.refresh(disposition)
        return disposition

    # --- Material Consumption (Released SupplierLots Only) ---

    def consume_material(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        payload: BatchMaterialConsumptionCreateRequest,
        source_ip: Optional[str] = None,
    ) -> BatchMaterialConsumption:
        material: Optional[Material] = self._pm_repo.get_material_for_org(
            db, tenant_ctx.organization_id, payload.material_id
        )
        if material is None:
            raise NotFoundError("Material not found in this organization.")
        if material.material_status == "BLOCKED":
            raise ConflictError("Blocked material cannot be consumed.")

        issuer_membership_id = payload.issued_by_membership_id or tenant_ctx.membership_id
        issuer = self._get_active_membership(
            db, tenant_ctx.organization_id, issuer_membership_id, "Issuer"
        )
        if payload.verified_by_membership_id is not None:
            verifier = self._get_active_membership(
                db, tenant_ctx.organization_id, payload.verified_by_membership_id, "Verifier"
            )
            if issuer.id == verifier.id or issuer.user_id == verifier.user_id:
                self._audit.record_security_event(
                    event_type="MAKER_CHECKER_VIOLATION",
                    outcome=SecurityEventOutcome.DENIED.value,
                    severity=SecurityEventSeverity.HIGH.value,
                    organization_id=tenant_ctx.organization_id,
                    user_id=tenant_ctx.user_id,
                    source_ip=source_ip,
                    target_resource=f"/api/v1/batches/{batch_id}/materials",
                )
                raise PermissionDeniedError(
                    "Maker-checker violation: material issuer and verifier must be distinct users.",
                    error_code="MAKER_CHECKER_VIOLATION",
                )

        self._assert_document_in_org(db, tenant_ctx.organization_id, payload.evidence_document_id)

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            batch = self._repo.get_batch_for_org(
                db, tenant_ctx.organization_id, batch_id, for_update=True
            )
            if batch is None:
                raise NotFoundError("Batch not found.")
            self._assert_site_access(
                db,
                tenant_ctx,
                batch.site_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/batches/{batch_id}/materials",
            )
            if batch.batch_status in {
                BatchStatus.RELEASED.value,
                BatchStatus.REJECTED.value,
                BatchStatus.RECALLED.value,
                BatchStatus.EXPIRED.value,
            }:
                raise ConflictError(
                    f"Cannot record material consumption on a batch in '{batch.batch_status}' status.",
                    error_code="BATCH_IMMUTABLE_IN_STATUS",
                )

            supplier_lot: Optional[SupplierLot] = self._repo.get_supplier_lot_for_org(
                db, tenant_ctx.organization_id, payload.supplier_lot_id, for_update=True
            )
            if supplier_lot is None:
                raise NotFoundError("Supplier lot not found in this organization.")
            if supplier_lot.site_id is not None:
                if (
                    not tenant_ctx.is_org_wide
                    and supplier_lot.site_id not in tenant_ctx.allowed_site_ids
                ):
                    self._audit.record_security_event(
                        event_type="SITE_SCOPE_VIOLATION",
                        outcome=SecurityEventOutcome.DENIED.value,
                        severity=SecurityEventSeverity.WARNING.value,
                        organization_id=tenant_ctx.organization_id,
                        user_id=tenant_ctx.user_id,
                        source_ip=source_ip,
                        target_resource=f"/api/v1/batches/{batch_id}/materials",
                        details_json={"supplier_lot_site_id": str(supplier_lot.site_id)},
                    )
                    raise PermissionDeniedError(
                        "Not authorized to consume supplier lots from this site.",
                        error_code="SITE_SCOPE_DENIED",
                    )
                if supplier_lot.site_id != batch.site_id:
                    raise ValidationError(
                        "Supplier lot belongs to a different site than the target batch.",
                        error_code="CROSS_SITE_SUPPLIER_LOT",
                    )
            if supplier_lot.material_id != material.id:
                raise ValidationError("SupplierLot does not belong to the specified material.")
            if supplier_lot.lot_status != SupplierLotStatus.RELEASED.value:
                raise ConflictError(
                    f"Only RELEASED supplier lots can be consumed (current status: '{supplier_lot.lot_status}').",
                    error_code="SUPPLIER_LOT_NOT_RELEASED",
                )
            if (
                supplier_lot.expiry_or_retest_date is not None
                and supplier_lot.expiry_or_retest_date < date.today()
            ):
                raise ConflictError(
                    "Supplier lot has passed its expiry or retest date.",
                    error_code="SUPPLIER_LOT_EXPIRED",
                )
            if payload.unit_code.strip() != supplier_lot.unit_code:
                raise ValidationError(
                    f"Unit mismatch: supplier lot is tracked in '{supplier_lot.unit_code}', got '{payload.unit_code}'."
                )
            if payload.quantity_consumed > supplier_lot.remaining_quantity:
                raise ValidationError(
                    f"Requested consumption ({payload.quantity_consumed}) exceeds supplier lot remaining quantity ({supplier_lot.remaining_quantity}).",
                    error_code="INSUFFICIENT_SUPPLIER_LOT_QUANTITY",
                )

            prev_remaining = supplier_lot.remaining_quantity
            supplier_lot.remaining_quantity = prev_remaining - payload.quantity_consumed
            if supplier_lot.remaining_quantity == Decimal("0"):
                supplier_lot.lot_status = SupplierLotStatus.EXHAUSTED.value
            supplier_lot.updated_by_user_id = tenant_ctx.user_id

            consumption = BatchMaterialConsumption(
                organization_id=tenant_ctx.organization_id,
                batch_id=batch.id,
                material_id=material.id,
                supplier_lot_id=supplier_lot.id,
                quantity_consumed=payload.quantity_consumed,
                unit_code=payload.unit_code.strip(),
                stage_name=payload.stage_name,
                issued_at=now,
                issued_by_membership_id=issuer.id,
                verified_by_membership_id=payload.verified_by_membership_id,
                evidence_document_id=payload.evidence_document_id,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(consumption)
            db.flush()

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=batch.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="BATCH_MATERIAL_CONSUMED",
                entity_type="batch_material_consumptions",
                entity_id=consumption.id,
                source_ip=source_ip,
                after_state={
                    "batch_id": str(batch.id),
                    "material_id": str(material.id),
                    "supplier_lot_id": str(supplier_lot.id),
                    "quantity_consumed": str(payload.quantity_consumed),
                    "remaining_quantity": str(supplier_lot.remaining_quantity),
                },
            )
        db.refresh(consumption)
        return consumption

    def verify_material_consumption(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        consumption_id: uuid.UUID,
        payload: BatchMaterialConsumptionVerifyRequest,
        source_ip: Optional[str] = None,
    ) -> BatchMaterialConsumption:
        batch = self._get_batch_or_404_with_site_check(db, tenant_ctx, batch_id, source_ip=source_ip)
        consumption = self._repo.get_consumption_for_org(
            db, tenant_ctx.organization_id, batch.id, consumption_id
        )
        if consumption is None:
            raise NotFoundError("Batch material consumption record not found.")
        if consumption.verified_by_membership_id is not None:
            raise ConflictError("Material consumption record is already verified.")

        issuer = self._get_active_membership(
            db, tenant_ctx.organization_id, consumption.issued_by_membership_id, "Issuer"
        )
        if issuer.id == tenant_ctx.membership_id or issuer.user_id == tenant_ctx.user_id:
            self._audit.record_security_event(
                event_type="MAKER_CHECKER_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/batches/{batch_id}/materials/{consumption_id}/verify",
            )
            raise PermissionDeniedError(
                "Maker-checker violation: issuer cannot verify their own material consumption.",
                error_code="MAKER_CHECKER_VIOLATION",
            )

        with UnitOfWork(db):
            if payload.row_version is not None:
                verify_and_increment_version(consumption, payload.row_version)
            consumption.verified_by_membership_id = tenant_ctx.membership_id
            consumption.updated_by_user_id = tenant_ctx.user_id
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=batch.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="BATCH_MATERIAL_CONSUMPTION_VERIFIED",
                entity_type="batch_material_consumptions",
                entity_id=consumption.id,
                reason=payload.notes,
                source_ip=source_ip,
            )
        db.refresh(consumption)
        return consumption

    def list_material_consumptions(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> List[BatchMaterialConsumption]:
        batch = self._get_batch_or_404_with_site_check(db, tenant_ctx, batch_id, source_ip=source_ip)
        return self._repo.list_consumptions_for_batch(db, tenant_ctx.organization_id, batch.id)

    # --- Batch Relationships & Documents ---

    def _would_create_cycle(
        self, db: Session, organization_id: uuid.UUID, parent_id: uuid.UUID, child_id: uuid.UUID
    ) -> bool:
        rels = self._repo.list_all_relationships_for_org(db, organization_id)
        adj: Dict[uuid.UUID, List[uuid.UUID]] = {}
        for r in rels:
            adj.setdefault(r.parent_batch_id, []).append(r.child_batch_id)

        # If parent_id is reachable starting from child_id, adding parent_id -> child_id creates a cycle.
        visited: Set[uuid.UUID] = {child_id}
        queue: deque[uuid.UUID] = deque([child_id])
        while queue:
            cur = queue.popleft()
            if cur == parent_id:
                return True
            for nxt in adj.get(cur, ()):
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)
        return False

    def link_batch_relationship(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        payload: BatchRelationshipCreateRequest,
        source_ip: Optional[str] = None,
    ) -> BatchRelationship:
        primary_batch = self._get_batch_or_404_with_site_check(
            db, tenant_ctx, batch_id, source_ip=source_ip
        )
        related_batch = self._repo.get_batch_for_org(
            db, tenant_ctx.organization_id, payload.related_batch_id
        )
        if related_batch is None:
            raise NotFoundError("Related batch not found in this organization.")
        self._assert_site_access(
            db,
            tenant_ctx,
            related_batch.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/batches/{batch_id}/relationships",
        )

        if payload.direction == "CHILD":
            parent_id, child_id = primary_batch.id, related_batch.id
        else:
            parent_id, child_id = related_batch.id, primary_batch.id

        if parent_id == child_id:
            raise ValidationError("A batch cannot have a genealogy relationship with itself.")

        if self._would_create_cycle(db, tenant_ctx.organization_id, parent_id, child_id):
            raise ValidationError("Cyclic batch genealogy relationship is not permitted.")

        dup = self._repo.get_relationship_duplicate(
            db, tenant_ctx.organization_id, parent_id, child_id, payload.relation_type.value
        )
        if dup is not None:
            raise ConflictError("This batch relationship already exists.")

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            rel = BatchRelationship(
                organization_id=tenant_ctx.organization_id,
                parent_batch_id=parent_id,
                child_batch_id=child_id,
                relation_type=payload.relation_type.value,
                quantity=payload.quantity,
                unit_code=payload.unit_code.strip() if payload.unit_code else None,
                event_at=now,
                notes=payload.notes,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(rel)
            db.flush()
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=primary_batch.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="BATCH_RELATIONSHIP_LINKED",
                entity_type="batch_relationships",
                entity_id=rel.id,
                source_ip=source_ip,
                after_state={
                    "parent_batch_id": str(parent_id),
                    "child_batch_id": str(child_id),
                    "relation_type": rel.relation_type,
                },
            )
        db.refresh(rel)
        return rel

    def link_batch_document(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        payload: BatchDocumentLinkCreateRequest,
        source_ip: Optional[str] = None,
    ) -> BatchDocumentLink:
        batch = self._get_batch_or_404_with_site_check(
            db, tenant_ctx, batch_id, source_ip=source_ip
        )
        self._assert_document_in_org(db, tenant_ctx.organization_id, payload.document_id)

        existing = self._repo.get_batch_document_link(
            db, tenant_ctx.organization_id, batch.id, payload.document_id
        )
        if existing is not None:
            raise ConflictError("Document is already linked to this batch.")

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            link = BatchDocumentLink(
                organization_id=tenant_ctx.organization_id,
                batch_id=batch.id,
                document_id=payload.document_id,
                document_role=payload.document_role.value,
                linked_by_user_id=tenant_ctx.user_id,
                linked_at=now,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(link)
            db.flush()
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=batch.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="BATCH_DOCUMENT_LINKED",
                entity_type="batch_document_links",
                entity_id=link.id,
                source_ip=source_ip,
                after_state={
                    "batch_id": str(batch.id),
                    "document_id": str(payload.document_id),
                    "document_role": link.document_role,
                },
            )
        db.refresh(link)
        return link
