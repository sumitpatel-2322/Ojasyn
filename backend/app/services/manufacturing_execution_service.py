import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

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
    BatchExecutionStatus,
    BatchStatus,
    CalibrationRecordStatus,
    CalibrationResultOutcome,
    CalibrationValidityStatus,
    DeterministicCheckOutcome,
    ExecutionStepStatus,
    MasterBatchRecordStatus,
    MembershipStatus,
    ObservationVerificationStatus,
    SOPResolutionOutcome,
    SOPRevisionStatus,
    SecurityEventOutcome,
    SecurityEventSeverity,
)
from app.domain.rules_engine import (
    DeterministicCheckEvaluation,
    convert_unit_value,
    evaluate_equipment_calibration_validity,
    evaluate_parameter_vs_sop_limit,
    evaluate_qc_result_vs_specification,
    evaluate_required_execution_step_presence,
    evaluate_required_signoff_presence,
    evaluate_sop_revision_applicability,
    evaluate_unit_compatibility,
)
from app.domain.state_machines import assert_transition
from app.models.domain.documents import Document, DocumentVersion
from app.models.domain.governance import ApprovalRecord
from app.models.domain.identity import OrganizationMembership
from app.models.domain.manufacturing_execution import (
    BatchExecutionRecord,
    BatchExecutionStep,
    DeterministicEvidenceCheck,
    MasterBatchRecord,
    ProcessParameterObservation,
)
from app.models.domain.master_data import UnitOfMeasure
from app.models.domain.products_materials import Product
from app.models.domain.qc_equipment import (
    CalibrationRecord,
    Equipment,
    EquipmentUsage,
    QCSample,
    QCTestDefinition,
    QCTestResult,
)
from app.models.domain.sops import SOP, SOPRevision, SOPStep
from app.models.domain.traceability import Batch, BatchDocumentLink
from app.models.schemas.manufacturing_execution import (
    BatchExecutionLifecycleActionRequest,
    BatchExecutionRecordCreateRequest,
    BatchExecutionRecordDetailResponse,
    BatchExecutionRecordUpdateRequest,
    BatchExecutionStepCreateRequest,
    BatchExecutionStepExecuteRequest,
    BatchExecutionStepUpdateRequest,
    BatchExecutionStepVerifyRequest,
    BatchManufacturingEvidenceResponse,
    DeterministicEvidenceCheckResponse,
    EvidenceCompletenessSummary,
    EvidenceDocumentVersionItem,
    EvidenceEquipmentCalibrationItem,
    EvidenceQCTestResultItem,
    EvidenceSOPResolutionItem,
    MasterBatchRecordActivateRequest,
    MasterBatchRecordApproveRequest,
    MasterBatchRecordCreateRequest,
    MasterBatchRecordResponse,
    MasterBatchRecordUpdateRequest,
    ProcessParameterObservationCreateRequest,
    ProcessParameterObservationVerifyRequest,
    SideBySideObservationSOPComparison,
)
from app.repositories.document_sop_repo import DocumentSOPRepository
from app.repositories.manufacturing_execution_repo import ManufacturingExecutionRepository
from app.repositories.organization_repo import OrganizationRepository
from app.repositories.product_batch_repo import ProductBatchRepository
from app.repositories.product_material_repo import ProductMaterialRepository
from app.services.audit_service import AuditService

MBR_STATUS_TRANSITIONS: Dict[str, Set[str]] = {
    MasterBatchRecordStatus.DRAFT.value: {
        MasterBatchRecordStatus.UNDER_REVIEW.value,
        MasterBatchRecordStatus.APPROVED.value,
    },
    MasterBatchRecordStatus.UNDER_REVIEW.value: {
        MasterBatchRecordStatus.APPROVED.value,
        MasterBatchRecordStatus.REJECTED.value,
    },
    MasterBatchRecordStatus.APPROVED.value: {
        MasterBatchRecordStatus.EFFECTIVE.value,
        MasterBatchRecordStatus.OBSOLETE.value,
    },
    MasterBatchRecordStatus.EFFECTIVE.value: {
        MasterBatchRecordStatus.SUPERSEDED.value,
        MasterBatchRecordStatus.OBSOLETE.value,
    },
    MasterBatchRecordStatus.REJECTED.value: {
        MasterBatchRecordStatus.DRAFT.value,
    },
    MasterBatchRecordStatus.SUPERSEDED.value: set(),
    MasterBatchRecordStatus.OBSOLETE.value: set(),
}

EXECUTION_RECORD_TRANSITIONS: Dict[str, Set[str]] = {
    BatchExecutionStatus.DRAFT.value: {
        BatchExecutionStatus.IN_PROGRESS.value,
        BatchExecutionStatus.COMPLETED.value,
        BatchExecutionStatus.VOIDED.value,
    },
    BatchExecutionStatus.IN_PROGRESS.value: {
        BatchExecutionStatus.COMPLETED.value,
        BatchExecutionStatus.VOIDED.value,
    },
    BatchExecutionStatus.COMPLETED.value: {
        BatchExecutionStatus.VERIFIED.value,
        BatchExecutionStatus.APPROVED.value,
    },
    BatchExecutionStatus.VERIFIED.value: {
        BatchExecutionStatus.APPROVED.value,
    },
    BatchExecutionStatus.APPROVED.value: set(),
    BatchExecutionStatus.VOIDED.value: set(),
}

IMMUTABLE_EXECUTION_STATUSES = {
    BatchExecutionStatus.VERIFIED.value,
    BatchExecutionStatus.APPROVED.value,
    BatchExecutionStatus.VOIDED.value,
}


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class ManufacturingExecutionService:
    """Tenant-scoped MasterBatchRecord, BatchExecutionRecord/Step/Observation & Consolidated Evidence service (`C4`)."""

    def __init__(
        self,
        repo: Optional[ManufacturingExecutionRepository] = None,
        batch_repo: Optional[ProductBatchRepository] = None,
        pm_repo: Optional[ProductMaterialRepository] = None,
        org_repo: Optional[OrganizationRepository] = None,
        doc_repo: Optional[DocumentSOPRepository] = None,
        audit_service: Optional[AuditService] = None,
    ) -> None:
        self._repo = repo or ManufacturingExecutionRepository()
        self._batch_repo = batch_repo or ProductBatchRepository()
        self._pm_repo = pm_repo or ProductMaterialRepository()
        self._org_repo = org_repo or OrganizationRepository()
        self._doc_repo = doc_repo or DocumentSOPRepository()
        self._audit = audit_service or AuditService()

    def _assert_site_access(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        site_id: uuid.UUID,
        *,
        source_ip: Optional[str] = None,
        target_resource: str = "/api/v1/execution-records",
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
                "Not authorized to access or manage records for this site.",
                error_code="SITE_SCOPE_DENIED",
            )

    def _assert_document_and_version_in_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        document_id: Optional[uuid.UUID],
        document_version_id: Optional[uuid.UUID] = None,
    ) -> None:
        if document_id is not None:
            doc = self._doc_repo.get_document_for_org(db, organization_id, document_id)
            if doc is None:
                raise NotFoundError("Referenced document not found in this organization.")
        if document_version_id is not None:
            ver = db.get(DocumentVersion, document_version_id)
            if ver is None:
                raise NotFoundError("Referenced document version not found.")
            parent_doc = self._doc_repo.get_document_for_org(db, organization_id, ver.document_id)
            if parent_doc is None:
                raise NotFoundError("Referenced document version not found in this organization.")
            if document_id is not None and ver.document_id != document_id:
                raise ValidationError(
                    "document_version_id does not belong to the specified document_id."
                )

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

    def _assert_equipment_in_org_and_site(
        self,
        db: Session,
        organization_id: uuid.UUID,
        site_id: uuid.UUID,
        equipment_id: Optional[uuid.UUID],
    ) -> Optional[Equipment]:
        if equipment_id is None:
            return None
        equip = db.scalar(
            select(Equipment).where(
                Equipment.organization_id == organization_id,
                Equipment.id == equipment_id,
            )
        )
        if equip is None:
            raise NotFoundError("Equipment not found in this organization.")
        if equip.site_id != site_id:
            raise ValidationError(
                "Equipment belongs to a different site than the execution record.",
                error_code="CROSS_SITE_EQUIPMENT",
            )
        return equip

    def _assert_sop_site_applicability(
        self, sop: SOP, rev: Optional[SOPRevision], site_id: Optional[uuid.UUID]
    ) -> None:
        if site_id is None:
            return
        if rev is not None and rev.site_id is not None and rev.site_id != site_id:
            raise ValidationError(
                "Referenced SOP revision does not apply to the target site.",
                error_code="CROSS_SITE_SOP_REFERENCE",
            )
        if sop.applicable_site_ids:
            allowed = {str(s) for s in sop.applicable_site_ids}
            if str(site_id) not in allowed:
                raise ValidationError(
                    "Referenced SOP does not apply to the target site.",
                    error_code="CROSS_SITE_SOP_REFERENCE",
                )
        elif sop.site_id is not None and sop.site_id != site_id and (rev is None or rev.site_id is None):
            raise ValidationError(
                "Referenced SOP does not apply to the target site.",
                error_code="CROSS_SITE_SOP_REFERENCE",
            )

    def _assert_sop_revisions_in_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        revision_ids: List[uuid.UUID],
        *,
        site_id: Optional[uuid.UUID] = None,
    ) -> None:
        for rev_id in revision_ids:
            rev = db.scalar(
                select(SOPRevision).where(
                    SOPRevision.organization_id == organization_id,
                    SOPRevision.id == rev_id,
                )
            )
            if rev is None:
                raise NotFoundError("Referenced SOP revision not found in this organization.")
            sop = db.scalar(
                select(SOP).where(SOP.organization_id == organization_id, SOP.id == rev.sop_id)
            )
            if sop is None:
                raise NotFoundError("Referenced SOP not found in this organization.")
            self._assert_sop_site_applicability(sop, rev, site_id)

    def _assert_sop_step_links_in_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        *,
        site_id: Optional[uuid.UUID] = None,
        sop_id: Optional[uuid.UUID] = None,
        sop_revision_id: Optional[uuid.UUID] = None,
        sop_step_id: Optional[uuid.UUID] = None,
    ) -> None:
        sop_obj: Optional[SOP] = None
        rev_obj: Optional[SOPRevision] = None

        if sop_id is not None:
            sop_obj = db.scalar(
                select(SOP).where(SOP.organization_id == organization_id, SOP.id == sop_id)
            )
            if sop_obj is None:
                raise NotFoundError("Referenced SOP not found in this organization.")
            self._assert_sop_site_applicability(sop_obj, None, site_id)

        if sop_revision_id is not None:
            rev_obj = db.scalar(
                select(SOPRevision).where(
                    SOPRevision.organization_id == organization_id,
                    SOPRevision.id == sop_revision_id,
                )
            )
            if rev_obj is None:
                raise NotFoundError("Referenced SOP revision not found in this organization.")
            if sop_id is not None and rev_obj.sop_id != sop_id:
                raise ValidationError("sop_revision_id does not belong to sop_id.")
            parent_sop = sop_obj or db.scalar(
                select(SOP).where(SOP.organization_id == organization_id, SOP.id == rev_obj.sop_id)
            )
            if parent_sop is None:
                raise NotFoundError("Parent SOP not found in this organization.")
            self._assert_sop_site_applicability(parent_sop, rev_obj, site_id)

        if sop_step_id is not None:
            step_obj = db.scalar(
                select(SOPStep).where(
                    SOPStep.organization_id == organization_id,
                    SOPStep.id == sop_step_id,
                )
            )
            if step_obj is None:
                raise NotFoundError("Referenced SOP step not found in this organization.")
            if sop_revision_id is not None and step_obj.sop_revision_id != sop_revision_id:
                raise ValidationError("sop_step_id does not belong to sop_revision_id.")
            step_rev = rev_obj or db.scalar(
                select(SOPRevision).where(
                    SOPRevision.organization_id == organization_id,
                    SOPRevision.id == step_obj.sop_revision_id,
                )
            )
            if step_rev is None:
                raise NotFoundError("Referenced SOP revision not found in this organization.")
            if sop_id is not None and step_rev.sop_id != sop_id:
                raise ValidationError("sop_step_id does not belong to sop_id.")
            step_sop = sop_obj or db.scalar(
                select(SOP).where(SOP.organization_id == organization_id, SOP.id == step_rev.sop_id)
            )
            if step_sop is None:
                raise NotFoundError("Parent SOP not found in this organization.")
            self._assert_sop_site_applicability(step_sop, step_rev, site_id)

    # --- Master Batch Records (MBR / MPR) ---

    def create_mbr(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        payload: MasterBatchRecordCreateRequest,
        source_ip: Optional[str] = None,
    ) -> MasterBatchRecord:
        self._assert_site_access(
            db, tenant_ctx, payload.site_id, source_ip=source_ip, target_resource="/api/v1/mbrs"
        )
        product: Optional[Product] = self._pm_repo.get_product_for_org(
            db, tenant_ctx.organization_id, payload.product_id
        )
        if product is None:
            raise NotFoundError("Product not found in this organization.")
        if product.site_id is not None and product.site_id != payload.site_id:
            raise ValidationError("Product site_id does not match MBR site_id.")

        self._assert_document_and_version_in_org(
            db, tenant_ctx.organization_id, payload.document_id, payload.document_version_id
        )
        if payload.applicable_sop_revision_ids:
            self._assert_sop_revisions_in_org(
                db,
                tenant_ctx.organization_id,
                payload.applicable_sop_revision_ids,
                site_id=payload.site_id,
            )

        if payload.supersedes_mbr_id is not None:
            prior = self._repo.get_mbr_for_org(
                db, tenant_ctx.organization_id, payload.supersedes_mbr_id
            )
            if prior is None:
                raise NotFoundError("Superseded MBR not found in this organization.")

        existing = self._repo.get_mbr_by_composite_key(
            db,
            tenant_ctx.organization_id,
            payload.site_id,
            payload.product_id,
            payload.mbr_code,
            payload.version_number,
        )
        if existing is not None:
            raise ConflictError(
                f"MasterBatchRecord '{payload.mbr_code}' version '{payload.version_number}' already exists.",
                error_code="DUPLICATE_MBR_VERSION",
            )

        with UnitOfWork(db):
            mbr = MasterBatchRecord(
                organization_id=tenant_ctx.organization_id,
                site_id=payload.site_id,
                product_id=payload.product_id,
                mbr_code=payload.mbr_code.strip(),
                version_number=payload.version_number.strip(),
                product_version_context=payload.product_version_context,
                record_type=payload.record_type,
                title=payload.title.strip(),
                description=payload.description,
                target_batch_size_value=payload.target_batch_size_value,
                target_batch_size_unit=(
                    payload.target_batch_size_unit.strip() if payload.target_batch_size_unit else None
                ),
                document_id=payload.document_id,
                document_version_id=payload.document_version_id,
                applicable_sop_revision_ids=[str(rid) for rid in payload.applicable_sop_revision_ids],
                planned_steps_json=payload.planned_steps_json,
                effective_from=payload.effective_from,
                effective_to=payload.effective_to,
                approval_status=MasterBatchRecordStatus.DRAFT.value,
                supersedes_mbr_id=payload.supersedes_mbr_id,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(mbr)
            db.flush()

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=mbr.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="MBR_CREATED",
                entity_type="master_batch_records",
                entity_id=mbr.id,
                source_ip=source_ip,
                after_state={
                    "mbr_code": mbr.mbr_code,
                    "version_number": mbr.version_number,
                    "approval_status": mbr.approval_status,
                },
            )
        db.refresh(mbr)
        return mbr

    def list_mbrs(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        site_id: Optional[uuid.UUID] = None,
        product_id: Optional[uuid.UUID] = None,
        approval_status: Optional[str] = None,
        mbr_code: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[MasterBatchRecord], int]:
        return self._repo.list_mbrs_for_tenant(
            db,
            tenant_ctx,
            site_id=site_id,
            product_id=product_id,
            approval_status=approval_status,
            mbr_code=mbr_code,
            page=page,
            page_size=page_size,
        )

    def get_mbr(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        mbr_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> MasterBatchRecord:
        mbr = self._repo.get_mbr_for_org(db, tenant_ctx.organization_id, mbr_id)
        if mbr is None:
            raise NotFoundError("MasterBatchRecord not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            mbr.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/mbrs/{mbr_id}",
        )
        return mbr

    def update_mbr(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        mbr_id: uuid.UUID,
        payload: MasterBatchRecordUpdateRequest,
        source_ip: Optional[str] = None,
    ) -> MasterBatchRecord:
        mbr = self.get_mbr(db, tenant_ctx=tenant_ctx, mbr_id=mbr_id, source_ip=source_ip)
        if mbr.approval_status != MasterBatchRecordStatus.DRAFT.value:
            raise ConflictError(
                f"MasterBatchRecord in '{mbr.approval_status}' status is immutable.",
                error_code="MBR_IMMUTABLE",
            )

        updates = payload.model_dump(exclude_unset=True, exclude={"row_version", "reason"})
        if "document_id" in updates or "document_version_id" in updates:
            self._assert_document_and_version_in_org(
                db,
                tenant_ctx.organization_id,
                updates.get("document_id", mbr.document_id),
                updates.get("document_version_id", mbr.document_version_id),
            )
        if "applicable_sop_revision_ids" in updates and updates["applicable_sop_revision_ids"]:
            self._assert_sop_revisions_in_org(
                db,
                tenant_ctx.organization_id,
                payload.applicable_sop_revision_ids or [],
                site_id=mbr.site_id,
            )

        eff_from = updates.get("effective_from", mbr.effective_from)
        eff_to = updates.get("effective_to", mbr.effective_to)
        if eff_to is not None and (eff_from is None or _as_utc(eff_to) <= _as_utc(eff_from)):
            raise ValidationError("effective_to must be strictly greater than effective_from.")

        with UnitOfWork(db):
            verify_and_increment_version(mbr, payload.row_version)
            for field, val in updates.items():
                if field == "applicable_sop_revision_ids" and val is not None:
                    val = [str(x) for x in val]
                setattr(mbr, field, val)
            mbr.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=mbr.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="MBR_UPDATED",
                entity_type="master_batch_records",
                entity_id=mbr.id,
                reason=payload.reason,
                source_ip=source_ip,
            )
        db.refresh(mbr)
        return mbr

    def submit_mbr_review(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        mbr_id: uuid.UUID,
        row_version: int,
        source_ip: Optional[str] = None,
    ) -> MasterBatchRecord:
        mbr = self.get_mbr(db, tenant_ctx=tenant_ctx, mbr_id=mbr_id, source_ip=source_ip)
        assert_transition(
            MBR_STATUS_TRANSITIONS,
            mbr.approval_status,
            MasterBatchRecordStatus.UNDER_REVIEW.value,
            entity_name="MasterBatchRecord",
        )
        with UnitOfWork(db):
            verify_and_increment_version(mbr, row_version)
            mbr.approval_status = MasterBatchRecordStatus.UNDER_REVIEW.value
            mbr.updated_by_user_id = tenant_ctx.user_id
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=mbr.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="MBR_SUBMITTED_FOR_REVIEW",
                entity_type="master_batch_records",
                entity_id=mbr.id,
                source_ip=source_ip,
            )
        db.refresh(mbr)
        return mbr

    def approve_mbr(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        mbr_id: uuid.UUID,
        payload: MasterBatchRecordApproveRequest,
        source_ip: Optional[str] = None,
    ) -> MasterBatchRecord:
        mbr = self.get_mbr(db, tenant_ctx=tenant_ctx, mbr_id=mbr_id, source_ip=source_ip)
        target_status = (
            MasterBatchRecordStatus.APPROVED.value
            if payload.decision == "APPROVED"
            else MasterBatchRecordStatus.REJECTED.value
        )
        assert_transition(
            MBR_STATUS_TRANSITIONS,
            mbr.approval_status,
            target_status,
            entity_name="MasterBatchRecord",
        )

        if mbr.created_by_user_id is not None and mbr.created_by_user_id == tenant_ctx.user_id:
            self._audit.record_security_event(
                event_type="MAKER_CHECKER_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/mbrs/{mbr_id}/approve",
            )
            raise PermissionDeniedError(
                "Maker-checker violation: MBR author cannot approve their own MasterBatchRecord.",
                error_code="MAKER_CHECKER_VIOLATION",
            )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            verify_and_increment_version(mbr, payload.row_version)
            approval = ApprovalRecord(
                organization_id=tenant_ctx.organization_id,
                entity_type="master_batch_record",
                entity_id=mbr.id,
                approval_stage="MBR_APPROVAL",
                decision=payload.decision,
                approver_membership_id=tenant_ctx.membership_id,
                decided_at=now,
                comments=payload.comments,
            )
            db.add(approval)
            db.flush()

            mbr.approval_status = target_status
            mbr.approved_by_user_id = tenant_ctx.user_id
            mbr.approved_by_membership_id = tenant_ctx.membership_id
            mbr.approved_at = now
            mbr.approval_record_id = approval.id
            mbr.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=mbr.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="MBR_APPROVAL_DECIDED",
                entity_type="master_batch_records",
                entity_id=mbr.id,
                reason=payload.comments,
                source_ip=source_ip,
                after_state={"approval_status": mbr.approval_status},
            )
        db.refresh(mbr)
        return mbr

    def activate_mbr(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        mbr_id: uuid.UUID,
        payload: MasterBatchRecordActivateRequest,
        source_ip: Optional[str] = None,
    ) -> MasterBatchRecord:
        mbr = self.get_mbr(db, tenant_ctx=tenant_ctx, mbr_id=mbr_id, source_ip=source_ip)
        assert_transition(
            MBR_STATUS_TRANSITIONS,
            mbr.approval_status,
            MasterBatchRecordStatus.EFFECTIVE.value,
            entity_name="MasterBatchRecord",
        )
        now = datetime.now(timezone.utc)
        eff_from = payload.effective_from or mbr.effective_from or now
        eff_to = payload.effective_to if payload.effective_to is not None else mbr.effective_to
        if eff_to is not None and _as_utc(eff_to) <= _as_utc(eff_from):
            raise ValidationError("effective_to must be strictly greater than effective_from.")

        with UnitOfWork(db):
            verify_and_increment_version(mbr, payload.row_version)
            mbr.effective_from = eff_from
            mbr.effective_to = eff_to
            mbr.approval_status = MasterBatchRecordStatus.EFFECTIVE.value
            mbr.updated_by_user_id = tenant_ctx.user_id

            if mbr.supersedes_mbr_id is not None:
                prior = self._repo.get_mbr_for_org(
                    db, tenant_ctx.organization_id, mbr.supersedes_mbr_id, for_update=True
                )
                if prior is not None and prior.approval_status == MasterBatchRecordStatus.EFFECTIVE.value:
                    prior.approval_status = MasterBatchRecordStatus.SUPERSEDED.value
                    if prior.effective_to is None or _as_utc(prior.effective_to) > _as_utc(eff_from):
                        prior.effective_to = eff_from

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=mbr.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="MBR_ACTIVATED",
                entity_type="master_batch_records",
                entity_id=mbr.id,
                reason=payload.reason,
                source_ip=source_ip,
                after_state={"approval_status": mbr.approval_status},
            )
        db.refresh(mbr)
        return mbr

    # --- Batch Execution Records ---

    def create_execution_record(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        payload: BatchExecutionRecordCreateRequest,
        source_ip: Optional[str] = None,
    ) -> BatchExecutionRecord:
        batch = self._batch_repo.get_batch_for_org(db, tenant_ctx.organization_id, batch_id)
        if batch is None:
            raise NotFoundError("Batch not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            batch.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/batches/{batch_id}/execution-records",
        )
        if batch.batch_status in {
            BatchStatus.RELEASED.value,
            BatchStatus.REJECTED.value,
            BatchStatus.RECALLED.value,
            BatchStatus.EXPIRED.value,
        }:
            raise ConflictError(
                f"Cannot create execution record for batch in '{batch.batch_status}' status.",
                error_code="BATCH_IMMUTABLE_IN_STATUS",
            )

        mbr = self._repo.get_mbr_for_org(
            db, tenant_ctx.organization_id, payload.master_batch_record_id
        )
        if mbr is None:
            raise NotFoundError("MasterBatchRecord not found in this organization.")
        if mbr.site_id != batch.site_id:
            raise ValidationError("MasterBatchRecord site_id does not match batch site_id.")
        if mbr.product_id != batch.product_id:
            raise ValidationError("MasterBatchRecord product_id does not match batch product_id.")
        if mbr.approval_status not in {
            MasterBatchRecordStatus.APPROVED.value,
            MasterBatchRecordStatus.EFFECTIVE.value,
        }:
            raise ConflictError(
                f"MasterBatchRecord must be APPROVED or EFFECTIVE before execution (current: '{mbr.approval_status}').",
                error_code="MBR_NOT_APPROVED",
            )

        self._assert_document_and_version_in_org(
            db,
            tenant_ctx.organization_id,
            payload.primary_document_id,
            payload.primary_document_version_id,
        )
        for sup_ver_id in payload.supporting_document_version_ids:
            self._assert_document_and_version_in_org(
                db, tenant_ctx.organization_id, None, sup_ver_id
            )

        if (
            self._repo.get_execution_record_by_code(
                db, tenant_ctx.organization_id, batch.id, payload.execution_code
            )
            is not None
        ):
            raise ConflictError(
                f"Execution code '{payload.execution_code}' already exists for this batch.",
                error_code="DUPLICATE_EXECUTION_CODE",
            )
        if (
            self._repo.get_execution_record_by_sequence(
                db, tenant_ctx.organization_id, batch.id, payload.execution_sequence
            )
            is not None
        ):
            raise ConflictError(
                f"Execution sequence {payload.execution_sequence} already exists for this batch.",
                error_code="DUPLICATE_EXECUTION_SEQUENCE",
            )

        op_member_id = payload.operator_membership_id or tenant_ctx.membership_id
        op_member = self._get_active_membership(
            db, tenant_ctx.organization_id, op_member_id, "Operator"
        )

        initial_status = (
            BatchExecutionStatus.IN_PROGRESS.value
            if payload.started_at is not None
            else BatchExecutionStatus.DRAFT.value
        )

        with UnitOfWork(db):
            record = BatchExecutionRecord(
                organization_id=tenant_ctx.organization_id,
                site_id=batch.site_id,
                batch_id=batch.id,
                master_batch_record_id=mbr.id,
                execution_code=payload.execution_code.strip(),
                execution_type=payload.execution_type,
                manufacturing_stage=payload.manufacturing_stage.strip(),
                execution_sequence=payload.execution_sequence,
                started_at=payload.started_at,
                execution_status=initial_status,
                operator_user_id=op_member.user_id,
                operator_membership_id=op_member.id,
                primary_document_id=payload.primary_document_id,
                primary_document_version_id=payload.primary_document_version_id,
                supporting_document_version_ids=[
                    str(v) for v in payload.supporting_document_version_ids
                ],
                notes=payload.notes,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(record)
            db.flush()

            if batch.master_batch_record_id is None:
                batch.master_batch_record_id = mbr.id

            seen_codes: Set[str] = set()
            seen_seqs: Set[int] = set()
            for st in payload.steps:
                code_l = st.step_code.strip().lower()
                if code_l in seen_codes or st.step_sequence in seen_seqs:
                    raise ValidationError("Duplicate step_code or step_sequence in execution record steps.")
                seen_codes.add(code_l)
                seen_seqs.add(st.step_sequence)
                self._assert_equipment_in_org_and_site(
                    db, tenant_ctx.organization_id, batch.site_id, st.equipment_id
                )
                self._assert_document_and_version_in_org(
                    db, tenant_ctx.organization_id, None, st.supporting_document_version_id
                )
                self._assert_sop_step_links_in_org(
                    db,
                    tenant_ctx.organization_id,
                    site_id=batch.site_id,
                    sop_id=st.sop_id,
                    sop_revision_id=st.sop_revision_id,
                    sop_step_id=st.sop_step_id,
                )
                db.add(
                    BatchExecutionStep(
                        organization_id=tenant_ctx.organization_id,
                        site_id=batch.site_id,
                        batch_execution_record_id=record.id,
                        step_code=st.step_code.strip(),
                        step_sequence=st.step_sequence,
                        description=st.description.strip(),
                        sop_id=st.sop_id,
                        sop_code=st.sop_code.strip() if st.sop_code else None,
                        sop_revision_id=st.sop_revision_id,
                        sop_step_id=st.sop_step_id,
                        planned_requirement=st.planned_requirement,
                        requires_checker_signoff=st.requires_checker_signoff,
                        step_status=ExecutionStepStatus.PENDING.value,
                        equipment_id=st.equipment_id,
                        supporting_document_version_id=st.supporting_document_version_id,
                        supporting_evidence_json=st.supporting_evidence_json,
                        created_by_user_id=tenant_ctx.user_id,
                        updated_by_user_id=tenant_ctx.user_id,
                    )
                )

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=batch.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="BATCH_EXECUTION_RECORD_CREATED",
                entity_type="batch_execution_records",
                entity_id=record.id,
                source_ip=source_ip,
                after_state={
                    "batch_id": str(batch.id),
                    "execution_code": record.execution_code,
                    "execution_status": record.execution_status,
                },
            )
        return self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=record.id, source_ip=source_ip
        )

    def list_execution_records_for_batch(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> List[BatchExecutionRecord]:
        batch = self._batch_repo.get_batch_for_org(db, tenant_ctx.organization_id, batch_id)
        if batch is None:
            raise NotFoundError("Batch not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            batch.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/batches/{batch_id}/execution-records",
        )
        return self._repo.list_execution_records_for_batch(
            db, tenant_ctx.organization_id, batch.id
        )

    def get_execution_record(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        execution_record_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> BatchExecutionRecord:
        record = self._repo.get_execution_record_for_org(
            db, tenant_ctx.organization_id, execution_record_id, with_children=True
        )
        if record is None:
            raise NotFoundError("BatchExecutionRecord not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            record.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/execution-records/{execution_record_id}",
        )
        return record

    def update_execution_record(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        execution_record_id: uuid.UUID,
        payload: BatchExecutionRecordUpdateRequest,
        source_ip: Optional[str] = None,
    ) -> BatchExecutionRecord:
        record = self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=execution_record_id, source_ip=source_ip
        )
        if record.execution_status in IMMUTABLE_EXECUTION_STATUSES:
            raise ConflictError(
                f"BatchExecutionRecord in '{record.execution_status}' status is immutable.",
                error_code="EXECUTION_RECORD_IMMUTABLE",
            )

        updates = payload.model_dump(exclude_unset=True, exclude={"row_version"})
        if "primary_document_id" in updates or "primary_document_version_id" in updates:
            self._assert_document_and_version_in_org(
                db,
                tenant_ctx.organization_id,
                updates.get("primary_document_id", record.primary_document_id),
                updates.get("primary_document_version_id", record.primary_document_version_id),
            )
        if "supporting_document_version_ids" in updates and updates["supporting_document_version_ids"]:
            for vid in updates["supporting_document_version_ids"]:
                self._assert_document_and_version_in_org(db, tenant_ctx.organization_id, None, vid)

        st_at = updates.get("started_at", record.started_at)
        comp_at = updates.get("completed_at", record.completed_at)
        if comp_at is not None and (st_at is None or _as_utc(comp_at) < _as_utc(st_at)):
            raise ValidationError("completed_at cannot precede started_at.")

        with UnitOfWork(db):
            verify_and_increment_version(record, payload.row_version)
            for field, val in updates.items():
                if field == "supporting_document_version_ids" and val is not None:
                    val = [str(x) for x in val]
                setattr(record, field, val)
            record.updated_by_user_id = tenant_ctx.user_id
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=record.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="BATCH_EXECUTION_RECORD_UPDATED",
                entity_type="batch_execution_records",
                entity_id=record.id,
                source_ip=source_ip,
            )
        return self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=record.id, source_ip=source_ip
        )

    def start_execution_record(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        execution_record_id: uuid.UUID,
        payload: BatchExecutionLifecycleActionRequest,
        source_ip: Optional[str] = None,
    ) -> BatchExecutionRecord:
        record = self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=execution_record_id, source_ip=source_ip
        )
        assert_transition(
            EXECUTION_RECORD_TRANSITIONS,
            record.execution_status,
            BatchExecutionStatus.IN_PROGRESS.value,
            entity_name="BatchExecutionRecord",
        )
        now = payload.occurred_at or datetime.now(timezone.utc)
        with UnitOfWork(db):
            verify_and_increment_version(record, payload.row_version)
            record.execution_status = BatchExecutionStatus.IN_PROGRESS.value
            if record.started_at is None:
                record.started_at = now
            record.updated_by_user_id = tenant_ctx.user_id
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=record.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="BATCH_EXECUTION_RECORD_STARTED",
                entity_type="batch_execution_records",
                entity_id=record.id,
                reason=payload.comments,
                source_ip=source_ip,
            )
        return self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=record.id, source_ip=source_ip
        )

    def complete_execution_record(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        execution_record_id: uuid.UUID,
        payload: BatchExecutionLifecycleActionRequest,
        source_ip: Optional[str] = None,
    ) -> BatchExecutionRecord:
        record = self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=execution_record_id, source_ip=source_ip
        )
        assert_transition(
            EXECUTION_RECORD_TRANSITIONS,
            record.execution_status,
            BatchExecutionStatus.COMPLETED.value,
            entity_name="BatchExecutionRecord",
        )
        now = payload.occurred_at or datetime.now(timezone.utc)
        started = record.started_at or now
        if _as_utc(now) < _as_utc(started):
            raise ValidationError("completed_at cannot precede started_at.")

        with UnitOfWork(db):
            verify_and_increment_version(record, payload.row_version)
            if record.started_at is None:
                record.started_at = started
            record.completed_at = now
            record.execution_status = BatchExecutionStatus.COMPLETED.value
            record.updated_by_user_id = tenant_ctx.user_id
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=record.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="BATCH_EXECUTION_RECORD_COMPLETED",
                entity_type="batch_execution_records",
                entity_id=record.id,
                reason=payload.comments,
                source_ip=source_ip,
            )
        return self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=record.id, source_ip=source_ip
        )

    def verify_execution_record(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        execution_record_id: uuid.UUID,
        payload: BatchExecutionLifecycleActionRequest,
        source_ip: Optional[str] = None,
    ) -> BatchExecutionRecord:
        record = self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=execution_record_id, source_ip=source_ip
        )
        assert_transition(
            EXECUTION_RECORD_TRANSITIONS,
            record.execution_status,
            BatchExecutionStatus.VERIFIED.value,
            entity_name="BatchExecutionRecord",
        )

        if (
            record.operator_membership_id == tenant_ctx.membership_id
            or (record.operator_user_id is not None and record.operator_user_id == tenant_ctx.user_id)
        ):
            self._audit.record_security_event(
                event_type="MAKER_CHECKER_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/execution-records/{execution_record_id}/verify",
            )
            raise PermissionDeniedError(
                "Maker-checker violation: operator cannot verify their own BatchExecutionRecord.",
                error_code="MAKER_CHECKER_VIOLATION",
            )

        now = payload.occurred_at or datetime.now(timezone.utc)
        with UnitOfWork(db):
            verify_and_increment_version(record, payload.row_version)
            record.execution_status = BatchExecutionStatus.VERIFIED.value
            record.checker_user_id = tenant_ctx.user_id
            record.checker_membership_id = tenant_ctx.membership_id
            record.checked_at = now
            record.updated_by_user_id = tenant_ctx.user_id
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=record.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="BATCH_EXECUTION_RECORD_VERIFIED",
                entity_type="batch_execution_records",
                entity_id=record.id,
                reason=payload.comments,
                source_ip=source_ip,
            )
        return self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=record.id, source_ip=source_ip
        )

    def approve_execution_record(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        execution_record_id: uuid.UUID,
        payload: BatchExecutionLifecycleActionRequest,
        source_ip: Optional[str] = None,
    ) -> BatchExecutionRecord:
        record = self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=execution_record_id, source_ip=source_ip
        )
        assert_transition(
            EXECUTION_RECORD_TRANSITIONS,
            record.execution_status,
            BatchExecutionStatus.APPROVED.value,
            entity_name="BatchExecutionRecord",
        )

        if (
            record.operator_membership_id == tenant_ctx.membership_id
            or (record.operator_user_id is not None and record.operator_user_id == tenant_ctx.user_id)
        ):
            self._audit.record_security_event(
                event_type="MAKER_CHECKER_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/execution-records/{execution_record_id}/approve",
            )
            raise PermissionDeniedError(
                "Maker-checker violation: operator cannot approve their own BatchExecutionRecord.",
                error_code="MAKER_CHECKER_VIOLATION",
            )

        now = payload.occurred_at or datetime.now(timezone.utc)
        with UnitOfWork(db):
            verify_and_increment_version(record, payload.row_version)
            approval = ApprovalRecord(
                organization_id=tenant_ctx.organization_id,
                entity_type="batch_execution_record",
                entity_id=record.id,
                approval_stage="EXECUTION_RECORD_APPROVAL",
                decision="APPROVED",
                approver_membership_id=tenant_ctx.membership_id,
                decided_at=now,
                comments=payload.comments,
            )
            db.add(approval)
            db.flush()

            record.execution_status = BatchExecutionStatus.APPROVED.value
            record.approved_by_membership_id = tenant_ctx.membership_id
            record.approved_at = now
            record.approval_record_id = approval.id
            record.updated_by_user_id = tenant_ctx.user_id
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=record.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="BATCH_EXECUTION_RECORD_APPROVED",
                entity_type="batch_execution_records",
                entity_id=record.id,
                reason=payload.comments,
                source_ip=source_ip,
            )
        return self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=record.id, source_ip=source_ip
        )

    # --- Batch Execution Steps ---

    def add_execution_step(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        execution_record_id: uuid.UUID,
        payload: BatchExecutionStepCreateRequest,
        source_ip: Optional[str] = None,
    ) -> BatchExecutionStep:
        record = self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=execution_record_id, source_ip=source_ip
        )
        if record.execution_status in IMMUTABLE_EXECUTION_STATUSES:
            raise ConflictError(
                f"Cannot add steps to BatchExecutionRecord in '{record.execution_status}' status.",
                error_code="EXECUTION_RECORD_IMMUTABLE",
            )

        if (
            self._repo.get_step_by_code_or_seq(
                db, tenant_ctx.organization_id, record.id, step_code=payload.step_code
            )
            is not None
        ):
            raise ConflictError(f"Step code '{payload.step_code}' already exists on this record.")
        if (
            self._repo.get_step_by_code_or_seq(
                db, tenant_ctx.organization_id, record.id, step_sequence=payload.step_sequence
            )
            is not None
        ):
            raise ConflictError(f"Step sequence {payload.step_sequence} already exists on this record.")

        self._assert_equipment_in_org_and_site(
            db, tenant_ctx.organization_id, record.site_id, payload.equipment_id
        )
        self._assert_document_and_version_in_org(
            db, tenant_ctx.organization_id, None, payload.supporting_document_version_id
        )
        self._assert_sop_step_links_in_org(
            db,
            tenant_ctx.organization_id,
            site_id=record.site_id,
            sop_id=payload.sop_id,
            sop_revision_id=payload.sop_revision_id,
            sop_step_id=payload.sop_step_id,
        )

        with UnitOfWork(db):
            step = BatchExecutionStep(
                organization_id=tenant_ctx.organization_id,
                site_id=record.site_id,
                batch_execution_record_id=record.id,
                step_code=payload.step_code.strip(),
                step_sequence=payload.step_sequence,
                description=payload.description.strip(),
                sop_id=payload.sop_id,
                sop_code=payload.sop_code.strip() if payload.sop_code else None,
                sop_revision_id=payload.sop_revision_id,
                sop_step_id=payload.sop_step_id,
                planned_requirement=payload.planned_requirement,
                requires_checker_signoff=payload.requires_checker_signoff,
                step_status=ExecutionStepStatus.PENDING.value,
                equipment_id=payload.equipment_id,
                supporting_document_version_id=payload.supporting_document_version_id,
                supporting_evidence_json=payload.supporting_evidence_json,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(step)
            db.flush()
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=record.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="BATCH_EXECUTION_STEP_ADDED",
                entity_type="batch_execution_steps",
                entity_id=step.id,
                source_ip=source_ip,
                after_state={"step_code": step.step_code, "step_sequence": step.step_sequence},
            )
        return self._repo.get_step_for_org(db, tenant_ctx.organization_id, step.id) or step

    def list_execution_steps(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        execution_record_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> List[BatchExecutionStep]:
        record = self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=execution_record_id, source_ip=source_ip
        )
        return self._repo.list_steps_for_execution_record(
            db, tenant_ctx.organization_id, record.id
        )

    def update_execution_step(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        execution_record_id: uuid.UUID,
        step_id: uuid.UUID,
        payload: BatchExecutionStepUpdateRequest,
        source_ip: Optional[str] = None,
    ) -> BatchExecutionStep:
        record = self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=execution_record_id, source_ip=source_ip
        )
        if record.execution_status in IMMUTABLE_EXECUTION_STATUSES:
            raise ConflictError(
                f"Cannot modify step when BatchExecutionRecord is '{record.execution_status}'.",
                error_code="EXECUTION_RECORD_IMMUTABLE",
            )
        step = self._repo.get_step_for_org(
            db, tenant_ctx.organization_id, step_id, execution_record_id=record.id
        )
        if step is None:
            raise NotFoundError("BatchExecutionStep not found.")
        if step.step_status == ExecutionStepStatus.VERIFIED.value:
            raise ConflictError(
                "Verified execution step is immutable.",
                error_code="EXECUTION_STEP_IMMUTABLE",
            )

        updates = payload.model_dump(exclude_unset=True, exclude={"row_version"})
        if "equipment_id" in updates:
            self._assert_equipment_in_org_and_site(
                db, tenant_ctx.organization_id, record.site_id, updates["equipment_id"]
            )
        if "supporting_document_version_id" in updates:
            self._assert_document_and_version_in_org(
                db, tenant_ctx.organization_id, None, updates["supporting_document_version_id"]
            )
        if any(k in updates for k in ("sop_id", "sop_revision_id", "sop_step_id")):
            self._assert_sop_step_links_in_org(
                db,
                tenant_ctx.organization_id,
                site_id=record.site_id,
                sop_id=updates.get("sop_id", step.sop_id),
                sop_revision_id=updates.get("sop_revision_id", step.sop_revision_id),
                sop_step_id=updates.get("sop_step_id", step.sop_step_id),
            )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            verify_and_increment_version(step, payload.row_version)
            for field, val in updates.items():
                setattr(step, field, val)
            if payload.step_status in {
                ExecutionStepStatus.IN_PROGRESS.value,
                ExecutionStepStatus.COMPLETED.value,
                ExecutionStepStatus.EXCEPTION.value,
            }:
                if step.operator_membership_id is None:
                    step.operator_membership_id = tenant_ctx.membership_id
                    step.operator_user_id = tenant_ctx.user_id
                if payload.step_status == ExecutionStepStatus.COMPLETED.value and step.executed_at is None:
                    step.executed_at = now
                if record.execution_status == BatchExecutionStatus.DRAFT.value:
                    record.execution_status = BatchExecutionStatus.IN_PROGRESS.value
                    if record.started_at is None:
                        record.started_at = step.started_at or step.executed_at or now
            step.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=record.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="BATCH_EXECUTION_STEP_UPDATED",
                entity_type="batch_execution_steps",
                entity_id=step.id,
                source_ip=source_ip,
                after_state={"step_status": step.step_status},
            )
        return self._repo.get_step_for_org(db, tenant_ctx.organization_id, step.id) or step

    def execute_step(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        execution_record_id: uuid.UUID,
        step_id: uuid.UUID,
        payload: BatchExecutionStepExecuteRequest,
        source_ip: Optional[str] = None,
    ) -> BatchExecutionStep:
        record = self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=execution_record_id, source_ip=source_ip
        )
        if record.execution_status in IMMUTABLE_EXECUTION_STATUSES:
            raise ConflictError(
                f"Cannot execute step when BatchExecutionRecord is '{record.execution_status}'.",
                error_code="EXECUTION_RECORD_IMMUTABLE",
            )
        step = self._repo.get_step_for_org(
            db, tenant_ctx.organization_id, step_id, execution_record_id=record.id
        )
        if step is None:
            raise NotFoundError("BatchExecutionStep not found.")
        if step.step_status == ExecutionStepStatus.VERIFIED.value:
            raise ConflictError(
                "Verified execution step is immutable.",
                error_code="EXECUTION_STEP_IMMUTABLE",
            )
        if payload.outcome_status == "EXCEPTION" and not (
            payload.exception_reason and payload.exception_reason.strip()
        ):
            raise ValidationError("exception_reason is required when outcome_status is 'EXCEPTION'.")

        if payload.equipment_id is not None:
            self._assert_equipment_in_org_and_site(
                db, tenant_ctx.organization_id, record.site_id, payload.equipment_id
            )
        if payload.supporting_document_version_id is not None:
            self._assert_document_and_version_in_org(
                db, tenant_ctx.organization_id, None, payload.supporting_document_version_id
            )

        now = payload.executed_at or datetime.now(timezone.utc)
        with UnitOfWork(db):
            verify_and_increment_version(step, payload.row_version)
            step.step_status = payload.outcome_status
            step.operator_user_id = tenant_ctx.user_id
            step.operator_membership_id = tenant_ctx.membership_id
            if payload.started_at is not None:
                step.started_at = payload.started_at
            elif step.started_at is None:
                step.started_at = now
            step.executed_at = now
            if payload.equipment_id is not None:
                step.equipment_id = payload.equipment_id
            if payload.comments is not None:
                step.comments = payload.comments
            if payload.exception_reason is not None:
                step.exception_reason = payload.exception_reason
            if payload.supporting_document_version_id is not None:
                step.supporting_document_version_id = payload.supporting_document_version_id
            if payload.supporting_evidence_json is not None:
                step.supporting_evidence_json = payload.supporting_evidence_json
            step.updated_by_user_id = tenant_ctx.user_id

            if record.execution_status == BatchExecutionStatus.DRAFT.value:
                record.execution_status = BatchExecutionStatus.IN_PROGRESS.value
                if record.started_at is None:
                    record.started_at = step.started_at

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=record.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="BATCH_EXECUTION_STEP_EXECUTED",
                entity_type="batch_execution_steps",
                entity_id=step.id,
                reason=payload.comments or payload.exception_reason,
                source_ip=source_ip,
                after_state={"step_status": step.step_status},
            )
        return self._repo.get_step_for_org(db, tenant_ctx.organization_id, step.id) or step

    def verify_step(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        execution_record_id: uuid.UUID,
        step_id: uuid.UUID,
        payload: BatchExecutionStepVerifyRequest,
        source_ip: Optional[str] = None,
    ) -> BatchExecutionStep:
        record = self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=execution_record_id, source_ip=source_ip
        )
        if record.execution_status in IMMUTABLE_EXECUTION_STATUSES:
            raise ConflictError(
                f"Cannot verify step when BatchExecutionRecord is '{record.execution_status}'.",
                error_code="EXECUTION_RECORD_IMMUTABLE",
            )
        step = self._repo.get_step_for_org(
            db, tenant_ctx.organization_id, step_id, execution_record_id=record.id
        )
        if step is None:
            raise NotFoundError("BatchExecutionStep not found.")
        if step.step_status == ExecutionStepStatus.VERIFIED.value:
            raise ConflictError("Execution step is already verified.")
        if step.step_status != ExecutionStepStatus.COMPLETED.value:
            raise ConflictError(
                f"Only COMPLETED steps can be verified (current: '{step.step_status}')."
            )

        op_member_id = step.operator_membership_id or record.operator_membership_id
        op_user_id = step.operator_user_id or record.operator_user_id
        if (
            op_member_id == tenant_ctx.membership_id
            or (op_user_id is not None and op_user_id == tenant_ctx.user_id)
        ):
            self._audit.record_security_event(
                event_type="MAKER_CHECKER_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/execution-records/{execution_record_id}/steps/{step_id}/verify",
            )
            raise PermissionDeniedError(
                "Maker-checker violation: step operator cannot verify their own execution step.",
                error_code="MAKER_CHECKER_VIOLATION",
            )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            verify_and_increment_version(step, payload.row_version)
            if step.operator_membership_id is None:
                step.operator_membership_id = op_member_id
                step.operator_user_id = op_user_id
            step.checker_user_id = tenant_ctx.user_id
            step.checker_membership_id = tenant_ctx.membership_id
            step.checked_at = now
            step.step_status = ExecutionStepStatus.VERIFIED.value
            if payload.comments:
                step.comments = payload.comments
            step.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=record.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="BATCH_EXECUTION_STEP_VERIFIED",
                entity_type="batch_execution_steps",
                entity_id=step.id,
                reason=payload.comments,
                source_ip=source_ip,
            )
        return self._repo.get_step_for_org(db, tenant_ctx.organization_id, step.id) or step

    # --- Process Parameter Observations ---

    def record_observation(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        execution_record_id: uuid.UUID,
        step_id: uuid.UUID,
        payload: ProcessParameterObservationCreateRequest,
        source_ip: Optional[str] = None,
    ) -> ProcessParameterObservation:
        record = self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=execution_record_id, source_ip=source_ip
        )
        if record.execution_status in IMMUTABLE_EXECUTION_STATUSES:
            raise ConflictError(
                f"Cannot record observations when BatchExecutionRecord is '{record.execution_status}'.",
                error_code="EXECUTION_RECORD_IMMUTABLE",
            )
        step = self._repo.get_step_for_org(
            db, tenant_ctx.organization_id, step_id, execution_record_id=record.id
        )
        if step is None:
            raise NotFoundError("BatchExecutionStep not found on this execution record.")
        if step.step_status == ExecutionStepStatus.VERIFIED.value:
            raise ConflictError(
                "Cannot record observations on an already verified execution step.",
                error_code="EXECUTION_STEP_IMMUTABLE",
            )

        self._assert_equipment_in_org_and_site(
            db, tenant_ctx.organization_id, record.site_id, payload.equipment_id
        )
        self._assert_document_and_version_in_org(
            db,
            tenant_ctx.organization_id,
            payload.source_document_id,
            payload.source_document_version_id,
        )
        if payload.sop_step_id is not None:
            self._assert_sop_step_links_in_org(
                db,
                tenant_ctx.organization_id,
                site_id=record.site_id,
                sop_id=step.sop_id,
                sop_revision_id=step.sop_revision_id,
                sop_step_id=payload.sop_step_id,
            )

        observed_at = payload.observed_at or step.executed_at or datetime.now(timezone.utc)
        with UnitOfWork(db):
            obs = ProcessParameterObservation(
                organization_id=tenant_ctx.organization_id,
                site_id=record.site_id,
                batch_execution_record_id=record.id,
                batch_execution_step_id=step.id,
                sop_step_id=payload.sop_step_id or step.sop_step_id,
                parameter_code=payload.parameter_code.strip(),
                parameter_name=payload.parameter_name.strip(),
                value_type=payload.value_type,
                numeric_value=payload.numeric_value if payload.value_type == "NUMERIC" else None,
                text_value=payload.text_value.strip() if (payload.value_type == "TEXT" and payload.text_value) else None,
                boolean_value=payload.boolean_value if payload.value_type == "BOOLEAN" else None,
                unit_code=payload.unit_code.strip() if payload.unit_code else None,
                observed_at=observed_at,
                equipment_id=payload.equipment_id or step.equipment_id,
                operator_user_id=tenant_ctx.user_id,
                operator_membership_id=tenant_ctx.membership_id,
                verification_status=ObservationVerificationStatus.UNVERIFIED.value,
                source_document_id=payload.source_document_id,
                source_document_version_id=payload.source_document_version_id,
                source_page_number=payload.source_page_number,
                source_section_ref=payload.source_section_ref,
                extraction_method=payload.extraction_method,
                extraction_confidence=payload.extraction_confidence,
                notes=payload.notes,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(obs)
            db.flush()

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=record.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="PROCESS_PARAMETER_OBSERVED",
                entity_type="process_parameter_observations",
                entity_id=obs.id,
                source_ip=source_ip,
                after_state={
                    "parameter_code": obs.parameter_code,
                    "value_type": obs.value_type,
                    "unit_code": obs.unit_code,
                },
            )
        db.refresh(obs)
        return obs

    def verify_observation(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        observation_id: uuid.UUID,
        payload: ProcessParameterObservationVerifyRequest,
        execution_record_id: Optional[uuid.UUID] = None,
        source_ip: Optional[str] = None,
    ) -> ProcessParameterObservation:
        obs = self._repo.get_observation_for_org(
            db,
            tenant_ctx.organization_id,
            observation_id,
            execution_record_id=execution_record_id,
        )
        if obs is None:
            raise NotFoundError("ProcessParameterObservation not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            obs.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/observations/{observation_id}/verify",
        )
        parent_rec = self._repo.get_execution_record_for_org(
            db, tenant_ctx.organization_id, obs.batch_execution_record_id
        )
        if parent_rec is None:
            raise NotFoundError("Parent BatchExecutionRecord not found.")
        if parent_rec.execution_status in IMMUTABLE_EXECUTION_STATUSES:
            raise ConflictError(
                f"Cannot verify observation when BatchExecutionRecord is '{parent_rec.execution_status}'.",
                error_code="EXECUTION_RECORD_IMMUTABLE",
            )
        parent_step = self._repo.get_step_for_org(
            db,
            tenant_ctx.organization_id,
            obs.batch_execution_step_id,
            execution_record_id=parent_rec.id,
        )
        if parent_step is not None and parent_step.step_status == ExecutionStepStatus.VERIFIED.value:
            raise ConflictError(
                "Cannot verify observation when parent BatchExecutionStep is already VERIFIED.",
                error_code="EXECUTION_STEP_IMMUTABLE",
            )
        if obs.verification_status == ObservationVerificationStatus.VERIFIED.value:
            raise ConflictError(
                "Verified process parameter observation is immutable.",
                error_code="OBSERVATION_IMMUTABLE",
            )

        if (
            obs.operator_membership_id == tenant_ctx.membership_id
            or (obs.operator_user_id is not None and obs.operator_user_id == tenant_ctx.user_id)
        ):
            self._audit.record_security_event(
                event_type="MAKER_CHECKER_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/observations/{observation_id}/verify",
            )
            raise PermissionDeniedError(
                "Maker-checker violation: operator cannot verify their own process parameter observation.",
                error_code="MAKER_CHECKER_VIOLATION",
            )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            verify_and_increment_version(obs, payload.row_version)
            obs.verified_by_user_id = tenant_ctx.user_id
            obs.verified_by_membership_id = tenant_ctx.membership_id
            obs.verified_at = now
            obs.verification_status = payload.verification_status
            if payload.notes:
                obs.notes = payload.notes
            obs.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=obs.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="PROCESS_PARAMETER_OBSERVATION_VERIFIED",
                entity_type="process_parameter_observations",
                entity_id=obs.id,
                reason=payload.notes,
                source_ip=source_ip,
                after_state={"verification_status": obs.verification_status},
            )
        db.refresh(obs)
        return obs

    def list_observations_for_step(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        execution_record_id: uuid.UUID,
        step_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> List[ProcessParameterObservation]:
        record = self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=execution_record_id, source_ip=source_ip
        )
        step = self._repo.get_step_for_org(
            db, tenant_ctx.organization_id, step_id, execution_record_id=record.id
        )
        if step is None:
            raise NotFoundError("BatchExecutionStep not found.")
        return self._repo.list_observations_for_step(db, tenant_ctx.organization_id, step.id)

    def list_observations_for_record(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        execution_record_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> List[ProcessParameterObservation]:
        record = self.get_execution_record(
            db, tenant_ctx=tenant_ctx, execution_record_id=execution_record_id, source_ip=source_ip
        )
        return self._repo.list_observations_for_execution_record(
            db, tenant_ctx.organization_id, record.id
        )

    # --- Historical SOP Resolution, Calibration Validity & Consolidated Batch Evidence ---

    def _resolve_sop_at_timestamp(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        sop_code: str,
        site_id: Optional[uuid.UUID],
        at: datetime,
    ) -> Tuple[str, Optional[SOP], Optional[SOPRevision], str]:
        """
        Resolves the historical SOP revision effective at `at` via `SOPResolver.resolve()`
        (with deterministic fallback matching the 6 canonical outcomes).
        """
        at_utc = _as_utc(at) or datetime.now(timezone.utc)
        try:
            from app.services.sop_service import SOPResolver  # type: ignore

            res = SOPResolver.resolve(
                sop_code,
                site_id,
                at=at_utc,
                db=db,
                organization_id=organization_id,
            )
            outcome = getattr(res, "outcome", None) or getattr(res, "resolution_outcome", None)
            if hasattr(outcome, "value"):
                outcome = outcome.value
            rev = (
                getattr(res, "revision", None)
                or getattr(res, "sop_revision", None)
                or getattr(res, "resolved_revision", None)
            )
            if rev is None and getattr(res, "revision_id", None) is not None:
                rev = db.scalar(
                    select(SOPRevision).where(
                        SOPRevision.organization_id == organization_id,
                        SOPRevision.id == getattr(res, "revision_id"),
                    )
                )
            sop_obj = getattr(res, "sop", None)
            if sop_obj is None and getattr(res, "sop_id", None) is not None:
                sop_obj = db.scalar(
                    select(SOP).where(
                        SOP.organization_id == organization_id,
                        SOP.id == getattr(res, "sop_id"),
                    )
                )
            if sop_obj is None and rev is not None:
                sop_obj = db.scalar(
                    select(SOP).where(
                        SOP.organization_id == organization_id,
                        SOP.id == rev.sop_id,
                    )
                )
            expl = getattr(res, "explanation", None) or f"SOP resolution outcome: {outcome}"
            if outcome:
                return str(outcome), sop_obj, rev, str(expl)
        except Exception:
            pass

        # Deterministic historical resolution directly on SOP & SOPRevision
        sop = db.scalar(
            select(SOP).where(
                SOP.organization_id == organization_id,
                SOP.sop_code == sop_code.strip(),
            )
        )
        if sop is None:
            return (
                SOPResolutionOutcome.SOP_NOT_FOUND.value,
                None,
                None,
                f"SOP '{sop_code}' was not found in this organization.",
            )

        all_revs = list(
            db.scalars(
                select(SOPRevision)
                .where(
                    SOPRevision.organization_id == organization_id,
                    SOPRevision.sop_id == sop.id,
                )
                .order_by(SOPRevision.revision_sequence.asc())
            ).all()
        )
        if not all_revs:
            return (
                SOPResolutionOutcome.NO_APPLICABLE_REVISION.value,
                sop,
                None,
                f"SOP '{sop_code}' has no revisions.",
            )

        def _site_matches(rev: SOPRevision) -> bool:
            if site_id is None:
                return True
            if rev.site_id is not None and rev.site_id != site_id:
                return False
            if sop.site_id is not None and sop.site_id != site_id and rev.site_id is None:
                return False
            if sop.applicable_site_ids:
                allowed = {str(s) for s in sop.applicable_site_ids}
                if str(site_id) not in allowed:
                    return False
            return True

        site_revs = [r for r in all_revs if _site_matches(r)]
        active_statuses = {
            SOPRevisionStatus.EFFECTIVE.value,
            SOPRevisionStatus.SUPERSEDED.value,
        }
        matching_effective: List[SOPRevision] = []
        expired_candidates: List[SOPRevision] = []
        pending_candidates: List[SOPRevision] = []

        for r in site_revs:
            ef_from = _as_utc(r.effective_from)
            ef_to = _as_utc(r.effective_to)
            if r.revision_status in active_statuses and ef_from is not None:
                if ef_from <= at_utc and (ef_to is None or ef_to > at_utc):
                    matching_effective.append(r)
                elif ef_to is not None and ef_to <= at_utc:
                    expired_candidates.append(r)
            elif r.revision_status in {
                SOPRevisionStatus.DRAFT.value,
                SOPRevisionStatus.REVIEW.value,
                SOPRevisionStatus.APPROVED.value,
            }:
                pending_candidates.append(r)

        if len(matching_effective) == 1:
            rev = matching_effective[0]
            return (
                SOPResolutionOutcome.RESOLVED.value,
                sop,
                rev,
                f"Resolved SOP '{sop_code}' revision '{rev.revision_number}' effective at {at_utc.isoformat()}.",
            )
        if len(matching_effective) > 1:
            return (
                SOPResolutionOutcome.AMBIGUOUS_MULTIPLE_REVISIONS.value,
                sop,
                None,
                f"Multiple overlapping SOP revisions were active for '{sop_code}' at {at_utc.isoformat()}.",
            )
        if expired_candidates:
            return (
                SOPResolutionOutcome.EXPIRED_ONLY.value,
                sop,
                None,
                f"Only expired SOP revisions existed for '{sop_code}' at {at_utc.isoformat()}.",
            )
        if pending_candidates:
            return (
                SOPResolutionOutcome.PENDING_APPROVAL_ONLY.value,
                sop,
                None,
                f"Only draft/pending-approval SOP revisions existed for '{sop_code}' at {at_utc.isoformat()}.",
            )
        return (
            SOPResolutionOutcome.NO_APPLICABLE_REVISION.value,
            sop,
            None,
            f"No applicable SOP revision was effective for '{sop_code}' at {at_utc.isoformat()}.",
        )

    def _evaluate_historical_calibration_at(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        equipment: Equipment,
        at: datetime,
    ) -> Tuple[str, Optional[CalibrationRecord]]:
        """
        Deterministically evaluates equipment calibration validity at timestamp `at`
        from `CalibrationRecord` history, never from current `equipment.operational_status`.
        """
        at_utc = _as_utc(at) or datetime.now(timezone.utc)
        if not equipment.calibration_required:
            return CalibrationValidityStatus.VALID.value, None

        records = list(
            db.scalars(
                select(CalibrationRecord)
                .where(
                    CalibrationRecord.organization_id == organization_id,
                    CalibrationRecord.equipment_id == equipment.id,
                )
                .order_by(CalibrationRecord.calibrated_at.desc())
            ).all()
        )
        if not records:
            return CalibrationValidityStatus.MISSING_RECORD.value, None

        prior = [
            r
            for r in records
            if _as_utc(r.calibrated_at) is not None and _as_utc(r.calibrated_at) <= at_utc
        ]
        if not prior:
            return CalibrationValidityStatus.NOT_CALIBRATED.value, None

        latest = prior[0]
        if (
            latest.calibration_result
            in {
                CalibrationResultOutcome.FAIL.value,
                CalibrationResultOutcome.OUT_OF_TOLERANCE.value,
            }
            or latest.record_status == CalibrationRecordStatus.REJECTED.value
        ):
            return CalibrationValidityStatus.NOT_CALIBRATED.value, latest

        due_utc = _as_utc(latest.due_at)
        if due_utc is not None and at_utc > due_utc:
            return CalibrationValidityStatus.EXPIRED.value, latest

        if latest.record_status != CalibrationRecordStatus.APPROVED.value:
            return CalibrationValidityStatus.UNVERIFIED.value, latest

        return CalibrationValidityStatus.VALID.value, latest

    def _load_uom_lookup(
        self, db: Session
    ) -> Dict[str, Tuple[str, Optional[str], Decimal]]:
        lookup: Dict[str, Tuple[str, Optional[str], Decimal]] = {}
        for u in db.scalars(select(UnitOfMeasure).where(UnitOfMeasure.is_active.is_(True))).all():
            lookup[u.unit_code] = (u.dimension, u.base_unit_code, u.conversion_factor)
        return lookup

    def _make_check_row(
        self,
        *,
        organization_id: uuid.UUID,
        site_id: uuid.UUID,
        batch_id: uuid.UUID,
        eval_res: DeterministicCheckEvaluation,
        source_entity_type: str,
        source_entity_id: uuid.UUID,
        execution_timestamp: Optional[datetime] = None,
        batch_execution_record_id: Optional[uuid.UUID] = None,
        batch_execution_step_id: Optional[uuid.UUID] = None,
        observation_id: Optional[uuid.UUID] = None,
        sop_revision_id: Optional[uuid.UUID] = None,
        sop_step_id: Optional[uuid.UUID] = None,
        equipment_id: Optional[uuid.UUID] = None,
        calibration_record_id: Optional[uuid.UUID] = None,
        qc_test_definition_id: Optional[uuid.UUID] = None,
        qc_test_result_id: Optional[uuid.UUID] = None,
    ) -> DeterministicEvidenceCheck:
        now = datetime.now(timezone.utc)
        return DeterministicEvidenceCheck(
            organization_id=organization_id,
            site_id=site_id,
            batch_id=batch_id,
            check_type=eval_res.check_type,
            rule_identifier=eval_res.rule_identifier,
            rule_version=eval_res.rule_version,
            source_entity_type=source_entity_type,
            source_entity_id=source_entity_id,
            batch_execution_record_id=batch_execution_record_id,
            batch_execution_step_id=batch_execution_step_id,
            observation_id=observation_id,
            sop_revision_id=sop_revision_id,
            sop_step_id=sop_step_id,
            equipment_id=equipment_id,
            calibration_record_id=calibration_record_id,
            qc_test_definition_id=qc_test_definition_id,
            qc_test_result_id=qc_test_result_id,
            actual_value_text=eval_res.actual_value_text,
            actual_numeric_value=eval_res.actual_numeric_value,
            expected_requirement_text=eval_res.expected_requirement_text,
            expected_min_value=eval_res.expected_min_value,
            expected_max_value=eval_res.expected_max_value,
            unit_code=eval_res.unit_code,
            execution_timestamp=execution_timestamp,
            evaluated_at=now,
            outcome=eval_res.outcome,
            explanation=eval_res.explanation,
            metadata_json=eval_res.metadata_json,
            created_at=now,
        )

    def get_batch_manufacturing_evidence(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        persist_checks: bool = True,
        source_ip: Optional[str] = None,
    ) -> BatchManufacturingEvidenceResponse:
        batch = self._batch_repo.get_batch_for_org(db, tenant_ctx.organization_id, batch_id)
        if batch is None:
            raise NotFoundError("Batch not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            batch.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/batches/{batch_id}/manufacturing-evidence",
        )

        org_id = tenant_ctx.organization_id
        uom_lookup = self._load_uom_lookup(db)
        exec_records = self._repo.list_execution_records_for_batch(db, org_id, batch.id)

        # Collect MBRs referenced by batch or execution records
        mbr_ids: Set[uuid.UUID] = {r.master_batch_record_id for r in exec_records}
        if batch.master_batch_record_id is not None:
            mbr_ids.add(batch.master_batch_record_id)
        mbrs: List[MasterBatchRecord] = []
        if mbr_ids:
            mbrs = list(
                db.scalars(
                    select(MasterBatchRecord).where(
                        MasterBatchRecord.organization_id == org_id,
                        MasterBatchRecord.id.in_(mbr_ids),
                    )
                ).all()
            )

        checks_to_persist: List[DeterministicEvidenceCheck] = []
        sop_resolutions: List[EvidenceSOPResolutionItem] = []
        side_by_side_comparisons: List[SideBySideObservationSOPComparison] = []
        equipment_evidence: List[EvidenceEquipmentCalibrationItem] = []
        qc_evidence: List[EvidenceQCTestResultItem] = []
        doc_version_ids: Set[uuid.UUID] = set()
        doc_ids: Set[uuid.UUID] = set()

        if batch.primary_document_id is not None:
            doc_ids.add(batch.primary_document_id)
        for dl in db.scalars(
            select(BatchDocumentLink).where(
                BatchDocumentLink.organization_id == org_id,
                BatchDocumentLink.batch_id == batch.id,
            )
        ).all():
            doc_ids.add(dl.document_id)
        for m in mbrs:
            if m.document_id:
                doc_ids.add(m.document_id)
            if m.document_version_id:
                doc_version_ids.add(m.document_version_id)

        # Cache of resolved SOP revisions & their steps
        sop_steps_by_rev_id: Dict[uuid.UUID, List[SOPStep]] = {}

        def _get_rev_steps(rev_id: uuid.UUID) -> List[SOPStep]:
            if rev_id not in sop_steps_by_rev_id:
                sop_steps_by_rev_id[rev_id] = list(
                    db.scalars(
                        select(SOPStep)
                        .where(
                            SOPStep.organization_id == org_id,
                            SOPStep.sop_revision_id == rev_id,
                        )
                        .order_by(SOPStep.step_sequence.asc())
                    ).all()
                )
            return sop_steps_by_rev_id[rev_id]

        # Track equipment evaluations (source_id -> tuple) to avoid duplicate checks for identical usage
        equipment_usages_to_eval: List[
            Tuple[uuid.UUID, str, uuid.UUID, Optional[uuid.UUID], Optional[uuid.UUID], datetime]
        ] = []

        # 1. Process Execution Records, Historical SOP Resolutions, Steps, Signoffs & Observations
        for rec in exec_records:
            if rec.primary_document_id:
                doc_ids.add(rec.primary_document_id)
            if rec.primary_document_version_id:
                doc_version_ids.add(rec.primary_document_version_id)
            for svid in rec.supporting_document_version_ids or []:
                try:
                    doc_version_ids.add(uuid.UUID(str(svid)))
                except ValueError:
                    pass

            rec_default_ts = (
                rec.started_at
                or rec.completed_at
                or rec.created_at
                or datetime.now(timezone.utc)
            )
            steps_by_code: Dict[str, BatchExecutionStep] = {
                s.step_code.strip().upper(): s for s in rec.steps
            }
            evaluated_sop_step_ids: Set[uuid.UUID] = set()

            for step in rec.steps:
                if step.supporting_document_version_id:
                    doc_version_ids.add(step.supporting_document_version_id)
                step_ts = (
                    step.executed_at
                    or step.started_at
                    or rec_default_ts
                )
                if step.equipment_id is not None:
                    equipment_usages_to_eval.append(
                        (
                            step.id,
                            "batch_execution_steps",
                            step.equipment_id,
                            rec.id,
                            step.id,
                            step_ts,
                        )
                    )

                # Determine SOP code if linked by sop_code, sop_id, or sop_revision_id (strictly tenant-scoped)
                eff_sop_code = step.sop_code
                referenced_rev: Optional[SOPRevision] = None
                if step.sop_revision_id is not None:
                    referenced_rev = db.scalar(
                        select(SOPRevision).where(
                            SOPRevision.organization_id == org_id,
                            SOPRevision.id == step.sop_revision_id,
                        )
                    )
                if not eff_sop_code and step.sop_id is not None:
                    s_obj = db.scalar(
                        select(SOP).where(SOP.organization_id == org_id, SOP.id == step.sop_id)
                    )
                    if s_obj is not None:
                        eff_sop_code = s_obj.sop_code
                if not eff_sop_code and referenced_rev is not None:
                    s_obj = db.scalar(
                        select(SOP).where(
                            SOP.organization_id == org_id, SOP.id == referenced_rev.sop_id
                        )
                    )
                    if s_obj is not None:
                        eff_sop_code = s_obj.sop_code

                resolved_rev: Optional[SOPRevision] = None
                resolved_sop: Optional[SOP] = None
                res_outcome = SOPResolutionOutcome.NOT_APPLICABLE.value if hasattr(SOPResolutionOutcome, "NOT_APPLICABLE") else "NOT_APPLICABLE"
                res_expl = "Step does not reference an SOP."

                if eff_sop_code:
                    res_outcome, resolved_sop, resolved_rev, res_expl = self._resolve_sop_at_timestamp(
                        db,
                        organization_id=org_id,
                        sop_code=eff_sop_code,
                        site_id=batch.site_id,
                        at=step_ts,
                    )
                    if resolved_rev is not None:
                        if resolved_rev.document_id:
                            doc_ids.add(resolved_rev.document_id)
                        if resolved_rev.document_version_id:
                            doc_version_ids.add(resolved_rev.document_version_id)

                    sop_resolutions.append(
                        EvidenceSOPResolutionItem(
                            batch_execution_record_id=rec.id,
                            batch_execution_step_id=step.id,
                            step_code=step.step_code,
                            sop_code=eff_sop_code,
                            evaluated_at_timestamp=step_ts,
                            resolution_outcome=res_outcome,
                            resolved_sop_id=resolved_sop.id if resolved_sop else None,
                            resolved_sop_revision_id=resolved_rev.id if resolved_rev else None,
                            resolved_revision_number=resolved_rev.revision_number if resolved_rev else None,
                            effective_from=resolved_rev.effective_from if resolved_rev else None,
                            effective_to=resolved_rev.effective_to if resolved_rev else None,
                            explanation=res_expl,
                        )
                    )

                    sop_eval = evaluate_sop_revision_applicability(
                        sop_code=eff_sop_code,
                        resolution_outcome=res_outcome,
                        resolved_revision_id=resolved_rev.id if resolved_rev else None,
                        resolved_revision_number=resolved_rev.revision_number if resolved_rev else None,
                        referenced_revision_id=step.sop_revision_id,
                        referenced_revision_number=(
                            referenced_rev.revision_number if referenced_rev else None
                        ),
                        execution_timestamp=step_ts,
                    )
                    checks_to_persist.append(
                        self._make_check_row(
                            organization_id=org_id,
                            site_id=batch.site_id,
                            batch_id=batch.id,
                            eval_res=sop_eval,
                            source_entity_type="batch_execution_steps",
                            source_entity_id=step.id,
                            execution_timestamp=step_ts,
                            batch_execution_record_id=rec.id,
                            batch_execution_step_id=step.id,
                            sop_revision_id=(
                                resolved_rev.id
                                if resolved_rev is not None
                                else step.sop_revision_id
                            ),
                        )
                    )

                # Match historical SOPStep from the resolved historical SOPRevision!
                matched_sop_step: Optional[SOPStep] = None
                if resolved_rev is not None:
                    rev_steps = _get_rev_steps(resolved_rev.id)
                    for cand in rev_steps:
                        if step.sop_step_id is not None and cand.id == step.sop_step_id:
                            matched_sop_step = cand
                            break
                        if cand.step_code.strip().upper() == step.step_code.strip().upper():
                            matched_sop_step = cand
                            break
                    if matched_sop_step is None and len(rev_steps) == 1:
                        matched_sop_step = rev_steps[0]
                elif step.sop_step_id is not None and not eff_sop_code:
                    matched_sop_step = db.scalar(
                        select(SOPStep).where(
                            SOPStep.organization_id == org_id, SOPStep.id == step.sop_step_id
                        )
                    )
                    if matched_sop_step is not None:
                        resolved_rev = db.scalar(
                            select(SOPRevision).where(
                                SOPRevision.organization_id == org_id,
                                SOPRevision.id == matched_sop_step.sop_revision_id,
                            )
                        )

                if matched_sop_step is not None:
                    evaluated_sop_step_ids.add(matched_sop_step.id)

                is_mandatory = (
                    matched_sop_step.is_mandatory if matched_sop_step is not None else True
                )
                step_pres_eval = evaluate_required_execution_step_presence(
                    step_code=step.step_code,
                    is_mandatory=is_mandatory,
                    execution_step_present=True,
                    step_status=step.step_status,
                    executed_at=step.executed_at,
                )
                checks_to_persist.append(
                    self._make_check_row(
                        organization_id=org_id,
                        site_id=batch.site_id,
                        batch_id=batch.id,
                        eval_res=step_pres_eval,
                        source_entity_type="batch_execution_steps",
                        source_entity_id=step.id,
                        execution_timestamp=step_ts,
                        batch_execution_record_id=rec.id,
                        batch_execution_step_id=step.id,
                        sop_revision_id=resolved_rev.id if resolved_rev else step.sop_revision_id,
                        sop_step_id=matched_sop_step.id if matched_sop_step else step.sop_step_id,
                    )
                )

                # Signoff presence check
                req_op = (
                    matched_sop_step.requires_operator_signoff
                    if matched_sop_step is not None
                    else True
                )
                req_chk = bool(
                    step.requires_checker_signoff
                    or (matched_sop_step is not None and matched_sop_step.requires_checker_signoff)
                )
                req_qa = bool(
                    matched_sop_step is not None and matched_sop_step.requires_qa_signoff
                )
                signoff_eval = evaluate_required_signoff_presence(
                    step_code=step.step_code,
                    requires_operator_signoff=req_op,
                    requires_checker_signoff=req_chk,
                    requires_qa_signoff=req_qa,
                    operator_membership_id=step.operator_membership_id,
                    checker_membership_id=step.checker_membership_id,
                    qa_membership_id=rec.approved_by_membership_id,
                    operator_user_id=step.operator_user_id,
                    checker_user_id=step.checker_user_id,
                )
                checks_to_persist.append(
                    self._make_check_row(
                        organization_id=org_id,
                        site_id=batch.site_id,
                        batch_id=batch.id,
                        eval_res=signoff_eval,
                        source_entity_type="batch_execution_steps",
                        source_entity_id=step.id,
                        execution_timestamp=step_ts,
                        batch_execution_record_id=rec.id,
                        batch_execution_step_id=step.id,
                        sop_revision_id=resolved_rev.id if resolved_rev else step.sop_revision_id,
                        sop_step_id=matched_sop_step.id if matched_sop_step else step.sop_step_id,
                    )
                )

                # Check if resolved historical SOPRevision has additional mandatory SOPSteps missing from rec.steps
                if resolved_rev is not None:
                    for req_sop_step in _get_rev_steps(resolved_rev.id):
                        if (
                            req_sop_step.id not in evaluated_sop_step_ids
                            and req_sop_step.step_code.strip().upper() not in steps_by_code
                        ):
                            evaluated_sop_step_ids.add(req_sop_step.id)
                            missing_step_eval = evaluate_required_execution_step_presence(
                                step_code=req_sop_step.step_code,
                                is_mandatory=req_sop_step.is_mandatory,
                                execution_step_present=False,
                                step_status=None,
                            )
                            checks_to_persist.append(
                                self._make_check_row(
                                    organization_id=org_id,
                                    site_id=batch.site_id,
                                    batch_id=batch.id,
                                    eval_res=missing_step_eval,
                                    source_entity_type="sop_steps",
                                    source_entity_id=req_sop_step.id,
                                    execution_timestamp=step_ts,
                                    batch_execution_record_id=rec.id,
                                    sop_revision_id=resolved_rev.id,
                                    sop_step_id=req_sop_step.id,
                                )
                            )

                # Evaluate ProcessParameterObservations on this step
                step_obs = list(step.observations or [])
                if (
                    not step_obs
                    and matched_sop_step is not None
                    and (
                        (matched_sop_step.expected_data_type or "NONE").upper() != "NONE"
                        or matched_sop_step.min_value is not None
                        or matched_sop_step.max_value is not None
                        or matched_sop_step.expected_text_value is not None
                    )
                ):
                    missing_param_eval = evaluate_parameter_vs_sop_limit(
                        parameter_name=matched_sop_step.parameter_name or matched_sop_step.title,
                        parameter_code=matched_sop_step.parameter_code or matched_sop_step.step_code,
                        expected_data_type=matched_sop_step.expected_data_type,
                        expected_unit_code=matched_sop_step.unit_code,
                        min_value=matched_sop_step.min_value,
                        max_value=matched_sop_step.max_value,
                        target_value=matched_sop_step.target_value,
                        expected_text_value=matched_sop_step.expected_text_value,
                        allowed_values=matched_sop_step.allowed_values_json,
                        acceptance_criteria=matched_sop_step.acceptance_criteria,
                        uom_lookup=uom_lookup,
                    )
                    checks_to_persist.append(
                        self._make_check_row(
                            organization_id=org_id,
                            site_id=batch.site_id,
                            batch_id=batch.id,
                            eval_res=missing_param_eval,
                            source_entity_type="batch_execution_steps",
                            source_entity_id=step.id,
                            execution_timestamp=step_ts,
                            batch_execution_record_id=rec.id,
                            batch_execution_step_id=step.id,
                            sop_revision_id=resolved_rev.id if resolved_rev else None,
                            sop_step_id=matched_sop_step.id,
                        )
                    )
                for obs in step_obs:
                    if obs.source_document_id:
                        doc_ids.add(obs.source_document_id)
                    if obs.source_document_version_id:
                        doc_version_ids.add(obs.source_document_version_id)
                    if obs.equipment_id is not None and obs.equipment_id != step.equipment_id:
                        equipment_usages_to_eval.append(
                            (
                                obs.id,
                                "process_parameter_observations",
                                obs.equipment_id,
                                rec.id,
                                step.id,
                                obs.observed_at,
                            )
                        )

                    # Match SOPStep for this observation from resolved historical SOPRevision
                    obs_sop_step = matched_sop_step
                    if resolved_rev is not None:
                        for cand in _get_rev_steps(resolved_rev.id):
                            if (
                                cand.parameter_code
                                and cand.parameter_code.strip().upper()
                                == obs.parameter_code.strip().upper()
                            ):
                                obs_sop_step = cand
                                break

                    if obs_sop_step is not None:
                        unit_eval = evaluate_unit_compatibility(
                            actual_unit_code=obs.unit_code,
                            expected_unit_code=obs_sop_step.unit_code,
                            actual_numeric_value=obs.numeric_value,
                            uom_lookup=uom_lookup,
                        )
                        checks_to_persist.append(
                            self._make_check_row(
                                organization_id=org_id,
                                site_id=batch.site_id,
                                batch_id=batch.id,
                                eval_res=unit_eval,
                                source_entity_type="process_parameter_observations",
                                source_entity_id=obs.id,
                                execution_timestamp=obs.observed_at,
                                batch_execution_record_id=rec.id,
                                batch_execution_step_id=step.id,
                                observation_id=obs.id,
                                sop_revision_id=resolved_rev.id if resolved_rev else None,
                                sop_step_id=obs_sop_step.id,
                            )
                        )

                        param_eval = evaluate_parameter_vs_sop_limit(
                            parameter_name=obs.parameter_name,
                            parameter_code=obs.parameter_code,
                            value_type=obs.value_type,
                            numeric_value=obs.numeric_value,
                            text_value=obs.text_value,
                            boolean_value=obs.boolean_value,
                            actual_unit_code=obs.unit_code,
                            expected_data_type=obs_sop_step.expected_data_type,
                            expected_unit_code=obs_sop_step.unit_code,
                            min_value=obs_sop_step.min_value,
                            max_value=obs_sop_step.max_value,
                            target_value=obs_sop_step.target_value,
                            expected_text_value=obs_sop_step.expected_text_value,
                            allowed_values=obs_sop_step.allowed_values_json,
                            acceptance_criteria=obs_sop_step.acceptance_criteria,
                            verification_status=obs.verification_status,
                            require_verified=bool(obs_sop_step.requires_checker_signoff),
                            uom_lookup=uom_lookup,
                        )
                        checks_to_persist.append(
                            self._make_check_row(
                                organization_id=org_id,
                                site_id=batch.site_id,
                                batch_id=batch.id,
                                eval_res=param_eval,
                                source_entity_type="process_parameter_observations",
                                source_entity_id=obs.id,
                                execution_timestamp=obs.observed_at,
                                batch_execution_record_id=rec.id,
                                batch_execution_step_id=step.id,
                                observation_id=obs.id,
                                sop_revision_id=resolved_rev.id if resolved_rev else None,
                                sop_step_id=obs_sop_step.id,
                            )
                        )

                        converted_val: Optional[Decimal] = None
                        if obs.numeric_value is not None and obs_sop_step.unit_code:
                            conv_v, ok_c, _ = convert_unit_value(
                                obs.numeric_value,
                                obs.unit_code,
                                obs_sop_step.unit_code,
                                uom_lookup=uom_lookup,
                            )
                            if ok_c:
                                converted_val = conv_v

                        side_by_side_comparisons.append(
                            SideBySideObservationSOPComparison(
                                observation_id=obs.id,
                                batch_execution_record_id=rec.id,
                                batch_execution_step_id=step.id,
                                step_code=step.step_code,
                                parameter_code=obs.parameter_code,
                                parameter_name=obs.parameter_name,
                                value_type=obs.value_type,
                                actual_numeric_value=obs.numeric_value,
                                actual_text_value=obs.text_value,
                                actual_boolean_value=obs.boolean_value,
                                actual_unit_code=obs.unit_code,
                                observed_at=obs.observed_at,
                                verification_status=obs.verification_status,
                                sop_code=eff_sop_code,
                                sop_resolution_outcome=(
                                    res_outcome if eff_sop_code else SOPResolutionOutcome.RESOLVED.value
                                ),
                                sop_revision_id=resolved_rev.id if resolved_rev else None,
                                sop_revision_number=resolved_rev.revision_number if resolved_rev else None,
                                sop_step_id=obs_sop_step.id,
                                sop_step_code=obs_sop_step.step_code,
                                min_value=obs_sop_step.min_value,
                                max_value=obs_sop_step.max_value,
                                target_value=obs_sop_step.target_value,
                                expected_text_value=obs_sop_step.expected_text_value,
                                expected_unit_code=obs_sop_step.unit_code,
                                converted_numeric_value=converted_val,
                                acceptance_criteria=obs_sop_step.acceptance_criteria,
                                compliance_outcome=param_eval.outcome,
                                explanation=param_eval.explanation,
                                source_document_version_id=obs.source_document_version_id,
                                source_page_number=obs.source_page_number,
                                source_section_ref=obs.source_section_ref,
                            )
                        )
                    else:
                        # Observation recorded when no historical SOPStep could be resolved
                        fallback_outcome = (
                            DeterministicCheckOutcome.MISSING.value
                            if eff_sop_code
                            and res_outcome
                            in {
                                SOPResolutionOutcome.SOP_NOT_FOUND.value,
                                SOPResolutionOutcome.NO_APPLICABLE_REVISION.value,
                            }
                            else (
                                DeterministicCheckOutcome.UNVERIFIED.value
                                if eff_sop_code
                                and res_outcome == SOPResolutionOutcome.PENDING_APPROVAL_ONLY.value
                                else (
                                    DeterministicCheckOutcome.FAIL.value
                                    if eff_sop_code
                                    else DeterministicCheckOutcome.NOT_APPLICABLE.value
                                )
                            )
                        )
                        side_by_side_comparisons.append(
                            SideBySideObservationSOPComparison(
                                observation_id=obs.id,
                                batch_execution_record_id=rec.id,
                                batch_execution_step_id=step.id,
                                step_code=step.step_code,
                                parameter_code=obs.parameter_code,
                                parameter_name=obs.parameter_name,
                                value_type=obs.value_type,
                                actual_numeric_value=obs.numeric_value,
                                actual_text_value=obs.text_value,
                                actual_boolean_value=obs.boolean_value,
                                actual_unit_code=obs.unit_code,
                                observed_at=obs.observed_at,
                                verification_status=obs.verification_status,
                                sop_code=eff_sop_code,
                                sop_resolution_outcome=res_outcome,
                                compliance_outcome=fallback_outcome,
                                explanation=res_expl,
                                source_document_version_id=obs.source_document_version_id,
                                source_page_number=obs.source_page_number,
                                source_section_ref=obs.source_section_ref,
                            )
                        )

        # 2. Equipment Usage & Historical Calibration Validity
        explicit_usages = list(
            db.scalars(
                select(EquipmentUsage)
                .where(
                    EquipmentUsage.organization_id == org_id,
                    EquipmentUsage.batch_id == batch.id,
                )
                .order_by(EquipmentUsage.usage_start_at.asc())
            ).all()
        )
        seen_equip_step_pairs: Set[Tuple[uuid.UUID, Optional[uuid.UUID]]] = set()
        for eu in explicit_usages:
            if eu.supporting_document_id:
                doc_ids.add(eu.supporting_document_id)
            if eu.supporting_document_version_id:
                doc_version_ids.add(eu.supporting_document_version_id)
            seen_equip_step_pairs.add((eu.equipment_id, eu.batch_execution_step_id))
            equipment_usages_to_eval.insert(
                0,
                (
                    eu.id,
                    "equipment_usages",
                    eu.equipment_id,
                    eu.batch_execution_record_id,
                    eu.batch_execution_step_id,
                    eu.usage_start_at,
                ),
            )

        evaluated_equip_keys: Set[Tuple[str, uuid.UUID]] = set()
        for src_id, src_type, equip_id, rec_id, st_id, used_at in equipment_usages_to_eval:
            if src_type != "equipment_usages" and (equip_id, st_id) in seen_equip_step_pairs:
                continue
            key = (src_type, src_id)
            if key in evaluated_equip_keys:
                continue
            evaluated_equip_keys.add(key)

            equip = db.scalar(
                select(Equipment).where(
                    Equipment.organization_id == org_id, Equipment.id == equip_id
                )
            )
            if equip is None:
                continue

            cal_status, cal_rec = self._evaluate_historical_calibration_at(
                db, organization_id=org_id, equipment=equip, at=used_at
            )
            if cal_rec is not None:
                if cal_rec.certificate_document_id:
                    doc_ids.add(cal_rec.certificate_document_id)
                if cal_rec.certificate_document_version_id:
                    doc_version_ids.add(cal_rec.certificate_document_version_id)

            cal_eval = evaluate_equipment_calibration_validity(
                equipment_code=equip.equipment_code,
                calibration_required=equip.calibration_required,
                calibration_validity_status=cal_status,
                calibration_code=cal_rec.calibration_code if cal_rec else None,
                usage_timestamp=used_at,
            )
            checks_to_persist.append(
                self._make_check_row(
                    organization_id=org_id,
                    site_id=batch.site_id,
                    batch_id=batch.id,
                    eval_res=cal_eval,
                    source_entity_type=src_type,
                    source_entity_id=src_id,
                    execution_timestamp=used_at,
                    batch_execution_record_id=rec_id,
                    batch_execution_step_id=st_id,
                    equipment_id=equip.id,
                    calibration_record_id=cal_rec.id if cal_rec else None,
                )
            )
            equipment_evidence.append(
                EvidenceEquipmentCalibrationItem(
                    usage_or_source_id=src_id,
                    source_type=src_type,
                    equipment_id=equip.id,
                    equipment_code=equip.equipment_code,
                    equipment_name=equip.equipment_name,
                    batch_execution_record_id=rec_id,
                    batch_execution_step_id=st_id,
                    used_at=used_at,
                    calibration_validity_status=cal_status,
                    applicable_calibration_record_id=cal_rec.id if cal_rec else None,
                    calibration_code=cal_rec.calibration_code if cal_rec else None,
                    calibrated_at=cal_rec.calibrated_at if cal_rec else None,
                    due_at=cal_rec.due_at if cal_rec else None,
                    compliance_outcome=cal_eval.outcome,
                    explanation=cal_eval.explanation,
                )
            )

        # 3. QC Samples & Results vs Specification
        qc_samples = {
            s.id: s
            for s in db.scalars(
                select(QCSample).where(
                    QCSample.organization_id == org_id,
                    QCSample.batch_id == batch.id,
                )
            ).all()
        }
        for s in qc_samples.values():
            if s.supporting_document_id:
                doc_ids.add(s.supporting_document_id)
            if s.supporting_document_version_id:
                doc_version_ids.add(s.supporting_document_version_id)

        qc_results = list(
            db.scalars(
                select(QCTestResult)
                .where(
                    QCTestResult.organization_id == org_id,
                    QCTestResult.batch_id == batch.id,
                )
                .order_by(QCTestResult.tested_at.asc())
            ).all()
        )
        for qcr in qc_results:
            if qcr.supporting_document_id:
                doc_ids.add(qcr.supporting_document_id)
            if qcr.supporting_document_version_id:
                doc_version_ids.add(qcr.supporting_document_version_id)

            tdef = db.scalar(
                select(QCTestDefinition).where(
                    QCTestDefinition.organization_id == org_id,
                    QCTestDefinition.id == qcr.test_definition_id,
                )
            )
            if tdef is not None:
                if tdef.specification_document_id:
                    doc_ids.add(tdef.specification_document_id)
                if tdef.specification_document_version_id:
                    doc_version_ids.add(tdef.specification_document_version_id)

            qc_eval = evaluate_qc_result_vs_specification(
                test_code=tdef.test_code if tdef else "QC_TEST",
                test_type=tdef.test_type if tdef else "NUMERIC_RANGE",
                result_type=qcr.result_type,
                numeric_result=qcr.numeric_result,
                text_result=qcr.text_result,
                boolean_result=qcr.boolean_result,
                actual_unit_code=qcr.unit_code,
                expected_unit_code=tdef.expected_unit_code if tdef else None,
                min_limit=tdef.min_limit if tdef else None,
                max_limit=tdef.max_limit if tdef else None,
                expected_text_specification=tdef.expected_text_specification if tdef else None,
                acceptance_criteria=tdef.acceptance_criteria if tdef else None,
                review_status=qcr.review_status,
                require_approved_review=True,
                uom_lookup=uom_lookup,
            )
            checks_to_persist.append(
                self._make_check_row(
                    organization_id=org_id,
                    site_id=batch.site_id,
                    batch_id=batch.id,
                    eval_res=qc_eval,
                    source_entity_type="qc_test_results",
                    source_entity_id=qcr.id,
                    execution_timestamp=qcr.tested_at,
                    equipment_id=qcr.equipment_id,
                    qc_test_definition_id=qcr.test_definition_id,
                    qc_test_result_id=qcr.id,
                )
            )

            qc_equip_cal_status: Optional[str] = None
            if qcr.equipment_id is not None:
                q_equip = db.scalar(
                    select(Equipment).where(
                        Equipment.organization_id == org_id,
                        Equipment.id == qcr.equipment_id,
                    )
                )
                if q_equip is not None:
                    qc_equip_cal_status, q_cal_rec = self._evaluate_historical_calibration_at(
                        db, organization_id=org_id, equipment=q_equip, at=qcr.tested_at
                    )
                    q_cal_eval = evaluate_equipment_calibration_validity(
                        equipment_code=q_equip.equipment_code,
                        calibration_required=q_equip.calibration_required,
                        calibration_validity_status=qc_equip_cal_status,
                        calibration_code=q_cal_rec.calibration_code if q_cal_rec else None,
                        usage_timestamp=qcr.tested_at,
                    )
                    checks_to_persist.append(
                        self._make_check_row(
                            organization_id=org_id,
                            site_id=batch.site_id,
                            batch_id=batch.id,
                            eval_res=q_cal_eval,
                            source_entity_type="qc_test_results",
                            source_entity_id=qcr.id,
                            execution_timestamp=qcr.tested_at,
                            equipment_id=q_equip.id,
                            calibration_record_id=q_cal_rec.id if q_cal_rec else None,
                            qc_test_definition_id=qcr.test_definition_id,
                            qc_test_result_id=qcr.id,
                        )
                    )

            sample_obj = qc_samples.get(qcr.qc_sample_id)
            qc_evidence.append(
                EvidenceQCTestResultItem(
                    qc_test_result_id=qcr.id,
                    qc_sample_id=qcr.qc_sample_id,
                    sample_identifier=sample_obj.sample_identifier if sample_obj else None,
                    test_definition_id=qcr.test_definition_id,
                    test_code=tdef.test_code if tdef else None,
                    test_name=tdef.test_name if tdef else None,
                    specification_version_applied=qcr.specification_version_applied,
                    result_type=qcr.result_type,
                    numeric_result=qcr.numeric_result,
                    text_result=qcr.text_result,
                    boolean_result=qcr.boolean_result,
                    unit_code=qcr.unit_code,
                    expected_unit_code=tdef.expected_unit_code if tdef else None,
                    min_limit=tdef.min_limit if tdef else None,
                    max_limit=tdef.max_limit if tdef else None,
                    expected_text_specification=tdef.expected_text_specification if tdef else None,
                    tested_at=qcr.tested_at,
                    review_status=qcr.review_status,
                    result_outcome=qcr.result_outcome,
                    is_out_of_specification_flagged=qcr.is_out_of_specification_flagged,
                    equipment_id=qcr.equipment_id,
                    equipment_calibration_validity=qc_equip_cal_status,
                    deterministic_outcome=qc_eval.outcome,
                    explanation=qc_eval.explanation,
                )
            )

        # 4. Collect Immutable Referenced DocumentVersion Records (with SHA-256 checksums)
        referenced_doc_versions: List[EvidenceDocumentVersionItem] = []
        seen_ver_ids: Set[uuid.UUID] = set()

        if doc_version_ids:
            for dv in db.scalars(
                select(DocumentVersion).where(DocumentVersion.id.in_(doc_version_ids))
            ).all():
                parent_doc = db.scalar(
                    select(Document).where(
                        Document.organization_id == org_id, Document.id == dv.document_id
                    )
                )
                if parent_doc is not None:
                    seen_ver_ids.add(dv.id)
                    referenced_doc_versions.append(
                        EvidenceDocumentVersionItem(
                            document_id=parent_doc.id,
                            document_code=parent_doc.document_code,
                            document_type=parent_doc.document_type,
                            title=parent_doc.title,
                            document_version_id=dv.id,
                            version_number=dv.version_number,
                            version_label=dv.version_label,
                            original_filename=dv.original_filename,
                            mime_type=dv.mime_type,
                            byte_size=dv.byte_size,
                            checksum_sha256=dv.checksum_sha256,
                            approval_status=dv.approval_status,
                        )
                    )

        if doc_ids:
            for d_obj in db.scalars(
                select(Document).where(
                    Document.organization_id == org_id,
                    Document.id.in_(doc_ids),
                )
            ).all():
                latest_dv = db.scalar(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == d_obj.id)
                    .order_by(DocumentVersion.version_number.desc())
                )
                if latest_dv is not None and latest_dv.id not in seen_ver_ids:
                    seen_ver_ids.add(latest_dv.id)
                    referenced_doc_versions.append(
                        EvidenceDocumentVersionItem(
                            document_id=d_obj.id,
                            document_code=d_obj.document_code,
                            document_type=d_obj.document_type,
                            title=d_obj.title,
                            document_version_id=latest_dv.id,
                            version_number=latest_dv.version_number,
                            version_label=latest_dv.version_label,
                            original_filename=latest_dv.original_filename,
                            mime_type=latest_dv.mime_type,
                            byte_size=latest_dv.byte_size,
                            checksum_sha256=latest_dv.checksum_sha256,
                            approval_status=latest_dv.approval_status,
                        )
                    )

        if persist_checks:
            with UnitOfWork(db):
                persisted_checks = self._repo.replace_deterministic_checks_for_batch(
                    db, org_id, batch.id, checks_to_persist
                )
        else:
            persisted_checks = checks_to_persist

        # 5. Completeness Summary
        counts = {
            DeterministicCheckOutcome.PASS.value: 0,
            DeterministicCheckOutcome.FAIL.value: 0,
            DeterministicCheckOutcome.MISSING.value: 0,
            DeterministicCheckOutcome.NOT_APPLICABLE.value: 0,
            DeterministicCheckOutcome.UNVERIFIED.value: 0,
        }
        flagged_items: List[str] = []
        for chk in persisted_checks:
            counts[chk.outcome] = counts.get(chk.outcome, 0) + 1
            if chk.outcome in {
                DeterministicCheckOutcome.FAIL.value,
                DeterministicCheckOutcome.MISSING.value,
                DeterministicCheckOutcome.UNVERIFIED.value,
            }:
                flagged_items.append(f"{chk.check_type}:{chk.outcome} ({chk.explanation})")

        if not exec_records:
            flagged_items.append("NO_BATCH_EXECUTION_RECORDS")

        pass_c = counts[DeterministicCheckOutcome.PASS.value]
        fail_c = counts[DeterministicCheckOutcome.FAIL.value]
        miss_c = counts[DeterministicCheckOutcome.MISSING.value]
        na_c = counts[DeterministicCheckOutcome.NOT_APPLICABLE.value]
        unver_c = counts[DeterministicCheckOutcome.UNVERIFIED.value]

        summary = EvidenceCompletenessSummary(
            total_checks=len(persisted_checks),
            pass_count=pass_c,
            fail_count=fail_c,
            missing_count=miss_c,
            not_applicable_count=na_c,
            unverified_count=unver_c,
            is_fully_compliant=bool(
                exec_records and fail_c == 0 and miss_c == 0 and unver_c == 0
            ),
            has_blocking_failures=fail_c > 0,
            has_missing_or_unverified_evidence=(
                not exec_records or miss_c > 0 or unver_c > 0
            ),
            missing_or_failed_items=flagged_items,
        )

        return BatchManufacturingEvidenceResponse(
            batch_id=batch.id,
            organization_id=batch.organization_id,
            site_id=batch.site_id,
            product_id=batch.product_id,
            batch_number=batch.batch_number,
            batch_status=batch.batch_status,
            quality_review_status=batch.quality_review_status,
            manufacturing_date=batch.manufacturing_date,
            expiry_date=batch.expiry_date,
            master_batch_records=[MasterBatchRecordResponse.model_validate(m) for m in mbrs],
            execution_records=[
                BatchExecutionRecordDetailResponse.model_validate(r) for r in exec_records
            ],
            sop_resolutions=sop_resolutions,
            side_by_side_parameter_comparisons=side_by_side_comparisons,
            equipment_calibration_evidence=equipment_evidence,
            qc_results_evidence=qc_evidence,
            referenced_document_versions=referenced_doc_versions,
            deterministic_checks=[
                DeterministicEvidenceCheckResponse.model_validate(c) for c in persisted_checks
            ],
            completeness_summary=summary,
        )

    def list_deterministic_checks_for_batch(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> List[DeterministicEvidenceCheck]:
        batch = self._batch_repo.get_batch_for_org(db, tenant_ctx.organization_id, batch_id)
        if batch is None:
            raise NotFoundError("Batch not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            batch.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/batches/{batch_id}/deterministic-checks",
        )
        return self._repo.list_deterministic_checks_for_batch(
            db, tenant_ctx.organization_id, batch.id
        )
