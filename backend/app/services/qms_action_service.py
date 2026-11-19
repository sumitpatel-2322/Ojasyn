import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import UnitOfWork, next_tenant_sequence, verify_and_increment_version
from app.core.deps import TenantContext
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.domain.enums import (
    ActorType,
    DeviationClassification,
    DeviationSourceType,
    DeviationStatus,
    MembershipStatus,
    SecurityEventOutcome,
    SecurityEventSeverity,
)
from app.domain.state_machines import assert_transition
from app.models.domain.complaints import Complaint
from app.models.domain.governance import ApprovalRecord
from app.models.domain.identity import OrganizationMembership
from app.models.domain.investigations import Investigation, SOPComplianceResult
from app.models.domain.manufacturing_execution import BatchExecutionStep
from app.models.domain.qc_equipment import QCTestResult
from app.models.domain.qms_loop import Deviation
from app.models.domain.tenancy import Organization, Site
from app.models.domain.traceability import Batch
from app.models.schemas.qms_loop import (
    DeviationCloseRequest,
    DeviationCreateRequest,
    DeviationSubmitReviewRequest,
    DeviationUpdateRequest,
)
from app.repositories.governance_repo import OutboxRepository
from app.repositories.qms_repo import QMSRepository
from app.services.audit_service import AuditService

DEVIATION_TRANSITIONS: Dict[str, Set[str]] = {
    DeviationStatus.OPEN.value: {
        DeviationStatus.UNDER_INVESTIGATION.value,
        DeviationStatus.CAPA_PENDING.value,
        DeviationStatus.QA_REVIEW.value,
    },
    DeviationStatus.UNDER_INVESTIGATION.value: {
        DeviationStatus.CAPA_PENDING.value,
        DeviationStatus.QA_REVIEW.value,
    },
    DeviationStatus.CAPA_PENDING.value: {
        DeviationStatus.QA_REVIEW.value,
        DeviationStatus.UNDER_INVESTIGATION.value,
    },
    DeviationStatus.QA_REVIEW.value: {
        DeviationStatus.CLOSED.value,
        DeviationStatus.UNDER_INVESTIGATION.value,
        DeviationStatus.CAPA_PENDING.value,
    },
    DeviationStatus.CLOSED.value: set(),
}


def _org_deviation_prefix(org: Optional[Organization]) -> str:
    if org is None:
        return "ORG-DEV"
    slug = re.sub(r"[^A-Z0-9]", "", (org.trade_name or org.legal_name or "ORG").upper())[:5] or "ORG"
    return f"{slug}-DEV"


class QMSActionService:
    """Tenant-scoped core Deviation lifecycle, SOP suggestion conversion & QA maker-checker closure (`E1`)."""

    def __init__(
        self,
        repo: Optional[QMSRepository] = None,
        audit_service: Optional[AuditService] = None,
        outbox_repo: Optional[OutboxRepository] = None,
    ) -> None:
        self._repo = repo or QMSRepository()
        self._audit = audit_service or AuditService()
        self._outbox = outbox_repo or OutboxRepository()

    def _assert_site_access(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        site_id: Optional[uuid.UUID],
        *,
        source_ip: Optional[str] = None,
        target_resource: str = "/api/v1/deviations",
    ) -> None:
        if site_id is None:
            return
        site = db.scalar(
            select(Site).where(Site.organization_id == tenant_ctx.organization_id, Site.id == site_id)
        )
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
                "Not authorized to access or manage deviations for this site.",
                error_code="SITE_SCOPE_DENIED",
            )

    def _validate_links(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        payload: DeviationCreateRequest,
        source_ip: Optional[str],
    ) -> Optional[uuid.UUID]:
        org_id = tenant_ctx.organization_id
        resolved_site_id = payload.site_id

        if payload.complaint_id is not None:
            comp = db.scalar(
                select(Complaint).where(
                    Complaint.organization_id == org_id, Complaint.id == payload.complaint_id
                )
            )
            if comp is None:
                raise NotFoundError("Linked complaint not found in this organization.")
            if resolved_site_id is None:
                resolved_site_id = comp.site_id

        if payload.investigation_id is not None:
            inv = db.scalar(
                select(Investigation).where(
                    Investigation.organization_id == org_id,
                    Investigation.id == payload.investigation_id,
                )
            )
            if inv is None:
                raise NotFoundError("Linked investigation not found in this organization.")
            if resolved_site_id is None:
                resolved_site_id = inv.site_id

        if payload.batch_id is not None:
            batch = db.scalar(
                select(Batch).where(Batch.organization_id == org_id, Batch.id == payload.batch_id)
            )
            if batch is None:
                raise NotFoundError("Linked batch not found in this organization.")
            if resolved_site_id is None:
                resolved_site_id = batch.site_id

        if payload.batch_execution_step_id is not None:
            step = db.scalar(
                select(BatchExecutionStep).where(
                    BatchExecutionStep.organization_id == org_id,
                    BatchExecutionStep.id == payload.batch_execution_step_id,
                )
            )
            if step is None:
                raise NotFoundError("Linked batch execution step not found in this organization.")
            if resolved_site_id is None:
                resolved_site_id = step.site_id

        if payload.sop_compliance_result_id is not None:
            comp_res = db.scalar(
                select(SOPComplianceResult).where(
                    SOPComplianceResult.organization_id == org_id,
                    SOPComplianceResult.id == payload.sop_compliance_result_id,
                )
            )
            if comp_res is None:
                raise NotFoundError("Linked SOP compliance result not found in this organization.")

        if payload.qc_test_result_id is not None:
            qcr = db.scalar(
                select(QCTestResult).where(
                    QCTestResult.organization_id == org_id,
                    QCTestResult.id == payload.qc_test_result_id,
                )
            )
            if qcr is None:
                raise NotFoundError("Linked QC test result not found in this organization.")
            if resolved_site_id is None:
                resolved_site_id = qcr.site_id

        if payload.owner_membership_id is not None:
            owner = db.scalar(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == org_id,
                    OrganizationMembership.id == payload.owner_membership_id,
                    OrganizationMembership.membership_status == MembershipStatus.ACTIVE.value,
                )
            )
            if owner is None:
                raise NotFoundError("Owner membership not found or not ACTIVE in this organization.")

        self._assert_site_access(
            db,
            tenant_ctx,
            resolved_site_id,
            source_ip=source_ip,
            target_resource="/api/v1/deviations",
        )
        return resolved_site_id

    def create_deviation(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        payload: DeviationCreateRequest,
        source_ip: Optional[str] = None,
    ) -> Deviation:
        resolved_site_id = self._validate_links(
            db, tenant_ctx=tenant_ctx, payload=payload, source_ip=source_ip
        )
        org = db.get(Organization, tenant_ctx.organization_id)
        now = datetime.now(timezone.utc)

        with UnitOfWork(db):
            dev_number = next_tenant_sequence(
                db,
                tenant_ctx.organization_id,
                "DEVIATION",
                period_year=now.year,
                prefix=_org_deviation_prefix(org),
            )
            deviation = Deviation(
                organization_id=tenant_ctx.organization_id,
                site_id=resolved_site_id,
                deviation_number=dev_number,
                source_type=payload.source_type.value,
                classification=payload.classification.value,
                is_mandatory_for_closure=payload.is_mandatory_for_closure,
                title=payload.title.strip(),
                description=payload.description.strip(),
                immediate_containment=(
                    payload.immediate_containment.strip() if payload.immediate_containment else None
                ),
                impact_assessment=(
                    payload.impact_assessment.strip() if payload.impact_assessment else None
                ),
                root_cause_summary=(
                    payload.root_cause_summary.strip() if payload.root_cause_summary else None
                ),
                complaint_id=payload.complaint_id,
                investigation_id=payload.investigation_id,
                batch_id=payload.batch_id,
                batch_execution_step_id=payload.batch_execution_step_id,
                sop_compliance_result_id=payload.sop_compliance_result_id,
                qc_test_result_id=payload.qc_test_result_id,
                owner_membership_id=payload.owner_membership_id or tenant_ctx.membership_id,
                status=DeviationStatus.OPEN.value,
                occurred_at=payload.occurred_at or now,
                due_at=payload.due_at,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(deviation)
            db.flush()

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=deviation.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="DEVIATION_CREATED",
                entity_type="deviations",
                entity_id=deviation.id,
                source_ip=source_ip,
                after_state={
                    "deviation_number": deviation.deviation_number,
                    "classification": deviation.classification,
                    "source_type": deviation.source_type,
                    "status": deviation.status,
                },
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="deviation",
                aggregate_id=deviation.id,
                event_type="DeviationCreated",
                payload_json={
                    "deviation_id": str(deviation.id),
                    "organization_id": str(tenant_ctx.organization_id),
                    "deviation_number": deviation.deviation_number,
                    "classification": deviation.classification,
                },
            )
        db.refresh(deviation)
        return deviation

    def create_from_compliance_result(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        compliance_result_id: uuid.UUID,
        classification: Optional[DeviationClassification] = None,
        source_ip: Optional[str] = None,
    ) -> Deviation:
        comp = db.scalar(
            select(SOPComplianceResult).where(
                SOPComplianceResult.organization_id == tenant_ctx.organization_id,
                SOPComplianceResult.id == compliance_result_id,
            )
        )
        if comp is None:
            raise NotFoundError("SOP compliance result not found.")
        if comp.status != "FAIL":
            raise ValidationError(
                "Deviations can only be generated from FAIL SOP compliance results.",
                error_code="COMPLIANCE_RESULT_NOT_FAIL",
            )
        inv = db.scalar(
            select(Investigation).where(
                Investigation.organization_id == tenant_ctx.organization_id,
                Investigation.id == comp.investigation_id,
            )
        )
        if inv is None:
            raise NotFoundError("Parent investigation not found.")

        suggested = dict(comp.suggested_deviation_payload_json or {})
        eff_class = classification or DeviationClassification(
            suggested.get("classification") or DeviationClassification.MAJOR.value
        )
        payload = DeviationCreateRequest(
            site_id=inv.site_id,
            source_type=DeviationSourceType.SOP_COMPLIANCE_RESULT,
            classification=eff_class,
            is_mandatory_for_closure=True,
            title=str(suggested.get("title") or f"SOP Compliance Failure: {comp.rule_identifier}")[:255],
            description=str(suggested.get("description") or comp.explanation),
            immediate_containment=suggested.get("immediate_containment"),
            impact_assessment=suggested.get("impact_assessment") or comp.explanation,
            complaint_id=inv.complaint_id,
            investigation_id=inv.id,
            batch_id=comp.batch_id or inv.batch_id,
            batch_execution_step_id=comp.batch_execution_step_id,
            sop_compliance_result_id=comp.id,
            qc_test_result_id=comp.qc_test_result_id,
        )
        return self.create_deviation(db, tenant_ctx=tenant_ctx, payload=payload, source_ip=source_ip)

    def list_deviations(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        status: Optional[str] = None,
        classification: Optional[str] = None,
        source_type: Optional[str] = None,
        complaint_id: Optional[uuid.UUID] = None,
        investigation_id: Optional[uuid.UUID] = None,
        batch_id: Optional[uuid.UUID] = None,
        site_id: Optional[uuid.UUID] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Deviation], int]:
        return self._repo.list_deviations_for_tenant(
            db,
            tenant_ctx,
            status=status,
            classification=classification,
            source_type=source_type,
            complaint_id=complaint_id,
            investigation_id=investigation_id,
            batch_id=batch_id,
            site_id=site_id,
            page=page,
            page_size=page_size,
        )

    def get_deviation(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        deviation_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> Deviation:
        dev = self._repo.get_deviation_for_org(db, tenant_ctx.organization_id, deviation_id)
        if dev is None:
            raise NotFoundError("Deviation not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            dev.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/deviations/{deviation_id}",
        )
        return dev

    def update_deviation(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        deviation_id: uuid.UUID,
        payload: DeviationUpdateRequest,
        source_ip: Optional[str] = None,
    ) -> Deviation:
        dev = self.get_deviation(db, tenant_ctx=tenant_ctx, deviation_id=deviation_id, source_ip=source_ip)
        if dev.status in {DeviationStatus.QA_REVIEW.value, DeviationStatus.CLOSED.value}:
            raise ConflictError(
                f"Deviation in '{dev.status}' status cannot be edited directly.",
                error_code="DEVIATION_IMMUTABLE_IN_STATUS",
            )
        if payload.status is not None and payload.status != dev.status:
            assert_transition(DEVIATION_TRANSITIONS, dev.status, payload.status, entity_name="Deviation")

        if payload.owner_membership_id is not None:
            owner = db.scalar(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == tenant_ctx.organization_id,
                    OrganizationMembership.id == payload.owner_membership_id,
                    OrganizationMembership.membership_status == MembershipStatus.ACTIVE.value,
                )
            )
            if owner is None:
                raise NotFoundError("Owner membership not found or not ACTIVE.")

        with UnitOfWork(db):
            locked = self._repo.get_deviation_for_org(
                db, tenant_ctx.organization_id, deviation_id, for_update=True
            )
            if locked is None:
                raise NotFoundError("Deviation not found.")
            verify_and_increment_version(locked, payload.row_version)
            prev_status = locked.status
            if payload.classification is not None:
                locked.classification = payload.classification.value
            if payload.status is not None:
                locked.status = payload.status
            if payload.is_mandatory_for_closure is not None:
                locked.is_mandatory_for_closure = payload.is_mandatory_for_closure
            if payload.title is not None:
                locked.title = payload.title.strip()
            if payload.description is not None:
                locked.description = payload.description.strip()
            if payload.immediate_containment is not None:
                locked.immediate_containment = payload.immediate_containment.strip()
            if payload.impact_assessment is not None:
                locked.impact_assessment = payload.impact_assessment.strip()
            if payload.root_cause_summary is not None:
                locked.root_cause_summary = payload.root_cause_summary.strip()
            if payload.owner_membership_id is not None:
                locked.owner_membership_id = payload.owner_membership_id
            if payload.due_at is not None:
                locked.due_at = payload.due_at
            locked.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=locked.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="DEVIATION_UPDATED",
                entity_type="deviations",
                entity_id=locked.id,
                reason=payload.reason,
                source_ip=source_ip,
                before_state={"status": prev_status},
                after_state={"status": locked.status, "classification": locked.classification},
            )
        db.refresh(locked)
        return locked

    def submit_for_review(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        deviation_id: uuid.UUID,
        payload: DeviationSubmitReviewRequest,
        source_ip: Optional[str] = None,
    ) -> Deviation:
        dev = self.get_deviation(db, tenant_ctx=tenant_ctx, deviation_id=deviation_id, source_ip=source_ip)
        assert_transition(
            DEVIATION_TRANSITIONS, dev.status, DeviationStatus.QA_REVIEW.value, entity_name="Deviation"
        )
        if tenant_ctx.membership_id is None:
            raise PermissionDeniedError("Active organization membership is required.")

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            locked = self._repo.get_deviation_for_org(
                db, tenant_ctx.organization_id, deviation_id, for_update=True
            )
            if locked is None:
                raise NotFoundError("Deviation not found.")
            verify_and_increment_version(locked, payload.row_version)
            prev_status = locked.status
            locked.status = DeviationStatus.QA_REVIEW.value
            locked.submitted_for_review_by_user_id = tenant_ctx.user_id
            locked.submitted_for_review_by_membership_id = tenant_ctx.membership_id
            locked.submitted_for_review_at = now
            locked.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=locked.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="DEVIATION_SUBMITTED_FOR_REVIEW",
                entity_type="deviations",
                entity_id=locked.id,
                reason=payload.comments,
                source_ip=source_ip,
                before_state={"status": prev_status},
                after_state={"status": locked.status},
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="deviation",
                aggregate_id=locked.id,
                event_type="DeviationSubmittedForReview",
                payload_json={
                    "deviation_id": str(locked.id),
                    "organization_id": str(tenant_ctx.organization_id),
                    "deviation_number": locked.deviation_number,
                },
            )
        db.refresh(locked)
        return locked

    def close_deviation(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        deviation_id: uuid.UUID,
        payload: DeviationCloseRequest,
        source_ip: Optional[str] = None,
    ) -> Deviation:
        dev = self.get_deviation(db, tenant_ctx=tenant_ctx, deviation_id=deviation_id, source_ip=source_ip)
        target_status = (
            DeviationStatus.CLOSED.value
            if payload.decision == "APPROVED"
            else DeviationStatus.UNDER_INVESTIGATION.value
        )
        assert_transition(DEVIATION_TRANSITIONS, dev.status, target_status, entity_name="Deviation")

        if tenant_ctx.membership_id is None:
            raise PermissionDeniedError("Active organization membership is required to close deviations.")

        # Dual user_id + membership_id maker-checker enforcement
        if (
            (dev.created_by_user_id is not None and dev.created_by_user_id == tenant_ctx.user_id)
            or (
                dev.submitted_for_review_by_user_id is not None
                and dev.submitted_for_review_by_user_id == tenant_ctx.user_id
            )
            or (
                dev.submitted_for_review_by_membership_id is not None
                and dev.submitted_for_review_by_membership_id == tenant_ctx.membership_id
            )
        ):
            self._audit.record_security_event(
                event_type="MAKER_CHECKER_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/deviations/{deviation_id}/close",
                details_json={
                    "deviation_id": str(dev.id),
                    "created_by_user_id": str(dev.created_by_user_id) if dev.created_by_user_id else None,
                    "submitted_for_review_by_user_id": (
                        str(dev.submitted_for_review_by_user_id)
                        if dev.submitted_for_review_by_user_id
                        else None
                    ),
                    "closer_user_id": str(tenant_ctx.user_id),
                },
            )
            raise PermissionDeniedError(
                "Maker-checker violation: deviation QA closer must be distinct from the creator and submitter.",
                error_code="MAKER_CHECKER_VIOLATION",
            )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            locked = self._repo.get_deviation_for_org(
                db, tenant_ctx.organization_id, deviation_id, for_update=True
            )
            if locked is None:
                raise NotFoundError("Deviation not found.")
            verify_and_increment_version(locked, payload.row_version)
            prev_status = locked.status

            approval = ApprovalRecord(
                organization_id=tenant_ctx.organization_id,
                entity_type="deviation",
                entity_id=locked.id,
                approval_stage="DEVIATION_QA_CLOSURE",
                decision=payload.decision,
                approver_membership_id=tenant_ctx.membership_id,
                decided_at=now,
                comments=payload.closure_comments.strip(),
            )
            db.add(approval)
            db.flush()

            locked.status = target_status
            locked.approval_record_id = approval.id
            locked.closure_comments = payload.closure_comments.strip()
            locked.updated_by_user_id = tenant_ctx.user_id
            if target_status == DeviationStatus.CLOSED.value:
                locked.closed_by_user_id = tenant_ctx.user_id
                locked.closed_by_membership_id = tenant_ctx.membership_id
                locked.closed_at = now

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=locked.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="DEVIATION_CLOSED" if target_status == DeviationStatus.CLOSED.value else "DEVIATION_REVIEW_RETURNED",
                entity_type="deviations",
                entity_id=locked.id,
                reason=payload.closure_comments.strip(),
                source_ip=source_ip,
                before_state={"status": prev_status},
                after_state={"status": locked.status, "approval_record_id": str(approval.id)},
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="deviation",
                aggregate_id=locked.id,
                event_type="DeviationClosed",
                payload_json={
                    "deviation_id": str(locked.id),
                    "organization_id": str(tenant_ctx.organization_id),
                    "deviation_number": locked.deviation_number,
                    "status": locked.status,
                },
            )
        db.refresh(locked)
        return locked
