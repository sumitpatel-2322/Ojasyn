import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import or_, select
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
    ApprovalDecision,
    CAPAActionStatus,
    CAPAStatus,
    DeviationClassification,
    DeviationSourceType,
    DeviationStatus,
    EffectivenessCheckStatus,
    MembershipStatus,
    RoleAssignmentScopeType,
    RoleAssignmentStatus,
    SecurityEventOutcome,
    SecurityEventSeverity,
)
from app.domain.state_machines import assert_transition
from app.models.domain.complaints import Complaint
from app.models.domain.documents import Document, DocumentVersion
from app.models.domain.governance import ApprovalRecord, Notification, OutboxEvent
from app.models.domain.identity import MembershipRoleAssignment, OrganizationMembership, Role
from app.models.domain.investigations import Investigation, SOPComplianceResult
from app.models.domain.manufacturing_execution import BatchExecutionStep
from app.models.domain.qc_equipment import QCTestResult
from app.models.domain.qms_loop import CAPA, CAPAAction, CAPAEffectivenessCheck, Deviation
from app.models.domain.tenancy import Organization, Site
from app.models.domain.traceability import Batch
from app.models.schemas.qms_loop import (
    CAPAActionCompleteRequest,
    CAPAActionCreateRequest,
    CAPAActionUpdateRequest,
    CAPACloseRequest,
    CAPACreateRequest,
    CAPAEffectivenessCheckCreateRequest,
    CAPAEffectivenessCheckRecordRequest,
    CAPAOpenRequest,
    CAPASubmitEffectivenessRequest,
    CAPAUpdateRequest,
    DeviationCloseRequest,
    DeviationCreateRequest,
    DeviationSubmitReviewRequest,
    DeviationUpdateRequest,
)
from app.repositories.governance_repo import OutboxRepository
from app.repositories.qms_repo import QMSRepository
from app.services.audit_service import AuditService
from app.services.notification_service import NotificationService

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

CAPA_TRANSITIONS: Dict[str, Set[str]] = {
    CAPAStatus.DRAFT.value: {CAPAStatus.OPEN.value},
    CAPAStatus.OPEN.value: {CAPAStatus.ACTIONS_IN_PROGRESS.value},
    CAPAStatus.ACTIONS_IN_PROGRESS.value: {CAPAStatus.EFFECTIVENESS_REVIEW.value},
    CAPAStatus.EFFECTIVENESS_REVIEW.value: {
        CAPAStatus.CLOSED.value,
        CAPAStatus.ACTIONS_IN_PROGRESS.value,
    },
    CAPAStatus.CLOSED.value: set(),
}

CAPA_ACTION_TRANSITIONS: Dict[str, Set[str]] = {
    CAPAActionStatus.PENDING.value: {
        CAPAActionStatus.IN_PROGRESS.value,
        CAPAActionStatus.COMPLETED.value,
        CAPAActionStatus.CANCELLED.value,
    },
    CAPAActionStatus.IN_PROGRESS.value: {
        CAPAActionStatus.COMPLETED.value,
        CAPAActionStatus.CANCELLED.value,
    },
    CAPAActionStatus.COMPLETED.value: {CAPAActionStatus.VERIFIED.value},
    CAPAActionStatus.VERIFIED.value: set(),
    CAPAActionStatus.CANCELLED.value: set(),
}


def _org_deviation_prefix(org: Optional[Organization]) -> str:
    if org is None:
        return "ORG-DEV"
    slug = re.sub(r"[^A-Z0-9]", "", (org.trade_name or org.legal_name or "ORG").upper())[:5] or "ORG"
    return f"{slug}-DEV"


def _org_capa_prefix(org: Optional[Organization]) -> str:
    if org is None:
        return "ORG-CAPA"
    slug = re.sub(r"[^A-Z0-9]", "", (org.trade_name or org.legal_name or "ORG").upper())[:5] or "ORG"
    return f"{slug}-CAPA"


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

    # ------------------------------------------------------------------
    # Phase 6 (E3): CAPA, CAPA Actions, Effectiveness Checks & Overdue
    # ------------------------------------------------------------------

    def _validate_document_evidence_links(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        evidence_document_id: Optional[uuid.UUID],
        evidence_document_version_id: Optional[uuid.UUID],
    ) -> Tuple[Optional[uuid.UUID], Optional[uuid.UUID]]:
        resolved_doc_id = evidence_document_id
        if resolved_doc_id is not None:
            doc = db.scalar(
                select(Document).where(
                    Document.organization_id == organization_id,
                    Document.id == resolved_doc_id,
                )
            )
            if doc is None:
                raise NotFoundError("Evidence document not found in this organization.")

        if evidence_document_version_id is not None:
            ver = db.scalar(
                select(DocumentVersion)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    DocumentVersion.id == evidence_document_version_id,
                    Document.organization_id == organization_id,
                )
            )
            if ver is None:
                raise NotFoundError("Evidence document version not found in this organization.")
            if resolved_doc_id is not None and ver.document_id != resolved_doc_id:
                raise ValidationError(
                    "Evidence document version does not belong to the specified document.",
                    error_code="DOCUMENT_VERSION_MISMATCH",
                )
            resolved_doc_id = ver.document_id

        return resolved_doc_id, evidence_document_version_id

    def _validate_active_membership(
        self,
        db: Session,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        *,
        label: str = "Owner membership",
    ) -> OrganizationMembership:
        membership = db.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.id == membership_id,
                OrganizationMembership.membership_status == MembershipStatus.ACTIVE.value,
            )
        )
        if membership is None:
            raise NotFoundError(f"{label} not found or not ACTIVE in this organization.")
        return membership

    def _validate_capa_links(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        payload: CAPACreateRequest,
        source_ip: Optional[str],
    ) -> Tuple[Optional[uuid.UUID], uuid.UUID]:
        org_id = tenant_ctx.organization_id
        resolved_site_id = payload.site_id

        if payload.complaint_id is not None:
            comp = db.scalar(
                select(Complaint).where(
                    Complaint.organization_id == org_id,
                    Complaint.id == payload.complaint_id,
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

        if payload.deviation_id is not None:
            dev = db.scalar(
                select(Deviation).where(
                    Deviation.organization_id == org_id,
                    Deviation.id == payload.deviation_id,
                )
            )
            if dev is None:
                raise NotFoundError("Linked deviation not found in this organization.")
            if resolved_site_id is None:
                resolved_site_id = dev.site_id

        owner_membership_id = payload.owner_membership_id or tenant_ctx.membership_id
        if owner_membership_id is None:
            raise ValidationError(
                "CAPA owner_membership_id is required.",
                error_code="CAPA_OWNER_REQUIRED",
            )
        self._validate_active_membership(db, org_id, owner_membership_id)

        self._assert_site_access(
            db,
            tenant_ctx,
            resolved_site_id,
            source_ip=source_ip,
            target_resource="/api/v1/capas",
        )
        return resolved_site_id, owner_membership_id

    def _record_maker_checker_violation(
        self,
        *,
        tenant_ctx: TenantContext,
        source_ip: Optional[str],
        target_resource: str,
        details: Dict[str, Optional[str]],
        message: str,
    ) -> None:
        self._audit.record_security_event(
            event_type="MAKER_CHECKER_VIOLATION",
            outcome=SecurityEventOutcome.DENIED.value,
            severity=SecurityEventSeverity.HIGH.value,
            organization_id=tenant_ctx.organization_id,
            user_id=tenant_ctx.user_id,
            source_ip=source_ip,
            target_resource=target_resource,
            details_json=details,
        )
        raise PermissionDeniedError(message, error_code="MAKER_CHECKER_VIOLATION")

    def create_capa(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        payload: CAPACreateRequest,
        source_ip: Optional[str] = None,
    ) -> CAPA:
        resolved_site_id, owner_membership_id = self._validate_capa_links(
            db, tenant_ctx=tenant_ctx, payload=payload, source_ip=source_ip
        )
        org = db.get(Organization, tenant_ctx.organization_id)
        now = datetime.now(timezone.utc)

        with UnitOfWork(db):
            capa_number = next_tenant_sequence(
                db,
                tenant_ctx.organization_id,
                "CAPA",
                period_year=now.year,
                prefix=_org_capa_prefix(org),
            )
            capa = CAPA(
                organization_id=tenant_ctx.organization_id,
                site_id=resolved_site_id,
                capa_number=capa_number,
                title=payload.title.strip(),
                description=payload.description.strip() if payload.description else None,
                capa_type=payload.capa_type.value,
                source_type=payload.source_type.value,
                complaint_id=payload.complaint_id,
                investigation_id=payload.investigation_id,
                deviation_id=payload.deviation_id,
                is_mandatory_for_complaint_closure=payload.is_mandatory_for_complaint_closure,
                severity=payload.severity.value,
                status=CAPAStatus.DRAFT.value,
                owner_membership_id=owner_membership_id,
                due_at=payload.due_at,
                root_cause_summary=(
                    payload.root_cause_summary.strip() if payload.root_cause_summary else None
                ),
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(capa)
            db.flush()

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=capa.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="CAPA_CREATED",
                entity_type="capas",
                entity_id=capa.id,
                source_ip=source_ip,
                after_state={
                    "capa_number": capa.capa_number,
                    "capa_type": capa.capa_type,
                    "source_type": capa.source_type,
                    "status": capa.status,
                    "owner_membership_id": str(capa.owner_membership_id),
                },
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="capa",
                aggregate_id=capa.id,
                event_type="CAPACreated",
                payload_json={
                    "capa_id": str(capa.id),
                    "organization_id": str(tenant_ctx.organization_id),
                    "capa_number": capa.capa_number,
                    "status": capa.status,
                },
            )
        db.refresh(capa)
        return capa

    def list_capas(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        status: Optional[str] = None,
        capa_type: Optional[str] = None,
        source_type: Optional[str] = None,
        severity: Optional[str] = None,
        complaint_id: Optional[uuid.UUID] = None,
        investigation_id: Optional[uuid.UUID] = None,
        deviation_id: Optional[uuid.UUID] = None,
        site_id: Optional[uuid.UUID] = None,
        owner_membership_id: Optional[uuid.UUID] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[CAPA], int]:
        return self._repo.list_capas_for_tenant(
            db,
            tenant_ctx,
            status=status,
            capa_type=capa_type,
            source_type=source_type,
            severity=severity,
            complaint_id=complaint_id,
            investigation_id=investigation_id,
            deviation_id=deviation_id,
            site_id=site_id,
            owner_membership_id=owner_membership_id,
            page=page,
            page_size=page_size,
        )

    def get_capa(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        capa_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> CAPA:
        capa = self._repo.get_capa_for_org(db, tenant_ctx.organization_id, capa_id)
        if capa is None:
            raise NotFoundError("CAPA not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            capa.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/capas/{capa_id}",
        )
        return capa

    def get_capa_detail(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        capa_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> Tuple[CAPA, List[CAPAAction], List[CAPAEffectivenessCheck]]:
        capa = self.get_capa(db, tenant_ctx=tenant_ctx, capa_id=capa_id, source_ip=source_ip)
        actions = self._repo.list_capa_actions(db, tenant_ctx.organization_id, capa.id)
        checks = self._repo.list_effectiveness_checks(db, tenant_ctx.organization_id, capa.id)
        return capa, actions, checks

    def update_capa(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        capa_id: uuid.UUID,
        payload: CAPAUpdateRequest,
        source_ip: Optional[str] = None,
    ) -> CAPA:
        capa = self.get_capa(db, tenant_ctx=tenant_ctx, capa_id=capa_id, source_ip=source_ip)
        if capa.status in {CAPAStatus.EFFECTIVENESS_REVIEW.value, CAPAStatus.CLOSED.value}:
            raise ConflictError(
                f"CAPA in '{capa.status}' status cannot be edited directly.",
                error_code="CAPA_IMMUTABLE_IN_STATUS",
            )

        if payload.site_id is not None:
            self._assert_site_access(
                db,
                tenant_ctx,
                payload.site_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/capas/{capa_id}",
            )

        if payload.owner_membership_id is not None:
            self._validate_active_membership(db, tenant_ctx.organization_id, payload.owner_membership_id)
            if capa.approver_membership_id is not None and payload.owner_membership_id == capa.approver_membership_id:
                self._record_maker_checker_violation(
                    tenant_ctx=tenant_ctx,
                    source_ip=source_ip,
                    target_resource=f"/api/v1/capas/{capa_id}",
                    details={
                        "capa_id": str(capa.id),
                        "owner_membership_id": str(payload.owner_membership_id),
                        "approver_membership_id": str(capa.approver_membership_id),
                    },
                    message="Maker-checker violation: CAPA owner cannot be the same as the CAPA approver.",
                )

        with UnitOfWork(db):
            locked = self._repo.get_capa_for_org(
                db, tenant_ctx.organization_id, capa_id, for_update=True
            )
            if locked is None:
                raise NotFoundError("CAPA not found.")
            verify_and_increment_version(locked, payload.row_version)
            prev_state = {"severity": locked.severity, "owner_membership_id": str(locked.owner_membership_id)}

            if payload.site_id is not None:
                locked.site_id = payload.site_id
            if payload.title is not None:
                locked.title = payload.title.strip()
            if payload.description is not None:
                locked.description = payload.description.strip()
            if payload.capa_type is not None:
                locked.capa_type = payload.capa_type.value
            if payload.is_mandatory_for_complaint_closure is not None:
                locked.is_mandatory_for_complaint_closure = payload.is_mandatory_for_complaint_closure
            if payload.severity is not None:
                locked.severity = payload.severity.value
            if payload.owner_membership_id is not None:
                locked.owner_membership_id = payload.owner_membership_id
            if payload.due_at is not None:
                locked.due_at = payload.due_at
            if payload.root_cause_summary is not None:
                locked.root_cause_summary = payload.root_cause_summary.strip()
            locked.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=locked.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="CAPA_UPDATED",
                entity_type="capas",
                entity_id=locked.id,
                reason=payload.reason,
                source_ip=source_ip,
                before_state=prev_state,
                after_state={"severity": locked.severity, "owner_membership_id": str(locked.owner_membership_id)},
            )
        db.refresh(locked)
        return locked

    def open_capa(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        capa_id: uuid.UUID,
        payload: CAPAOpenRequest,
        source_ip: Optional[str] = None,
    ) -> CAPA:
        capa = self.get_capa(db, tenant_ctx=tenant_ctx, capa_id=capa_id, source_ip=source_ip)
        assert_transition(CAPA_TRANSITIONS, capa.status, CAPAStatus.OPEN.value, entity_name="CAPA")
        if tenant_ctx.membership_id is None:
            raise PermissionDeniedError("Active organization membership is required to approve CAPAs.")

        owner_mem = db.get(OrganizationMembership, capa.owner_membership_id)
        if (
            (capa.created_by_user_id is not None and capa.created_by_user_id == tenant_ctx.user_id)
            or tenant_ctx.membership_id == capa.owner_membership_id
            or (owner_mem is not None and owner_mem.user_id == tenant_ctx.user_id)
        ):
            self._record_maker_checker_violation(
                tenant_ctx=tenant_ctx,
                source_ip=source_ip,
                target_resource=f"/api/v1/capas/{capa_id}/open",
                details={
                    "capa_id": str(capa.id),
                    "created_by_user_id": str(capa.created_by_user_id) if capa.created_by_user_id else None,
                    "owner_membership_id": str(capa.owner_membership_id),
                    "approver_user_id": str(tenant_ctx.user_id),
                    "approver_membership_id": str(tenant_ctx.membership_id),
                },
                message="Maker-checker violation: CAPA approver must be distinct from the creator and owner.",
            )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            locked = self._repo.get_capa_for_org(
                db, tenant_ctx.organization_id, capa_id, for_update=True
            )
            if locked is None:
                raise NotFoundError("CAPA not found.")
            verify_and_increment_version(locked, payload.row_version)
            prev_status = locked.status

            approval = ApprovalRecord(
                organization_id=tenant_ctx.organization_id,
                entity_type="capa",
                entity_id=locked.id,
                approval_stage="CAPA_OPEN_APPROVAL",
                decision=ApprovalDecision.APPROVED.value,
                approver_membership_id=tenant_ctx.membership_id,
                decided_at=now,
                comments=payload.comments.strip() if payload.comments else None,
            )
            db.add(approval)
            db.flush()

            locked.status = CAPAStatus.OPEN.value
            locked.approver_membership_id = tenant_ctx.membership_id
            locked.approved_by_user_id = tenant_ctx.user_id
            locked.approved_at = now
            locked.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=locked.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="CAPA_OPENED",
                entity_type="capas",
                entity_id=locked.id,
                reason=payload.comments.strip() if payload.comments else None,
                source_ip=source_ip,
                before_state={"status": prev_status},
                after_state={"status": locked.status, "approval_record_id": str(approval.id)},
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="capa",
                aggregate_id=locked.id,
                event_type="CAPAStatusChanged",
                payload_json={
                    "capa_id": str(locked.id),
                    "organization_id": str(tenant_ctx.organization_id),
                    "capa_number": locked.capa_number,
                    "from_status": prev_status,
                    "to_status": locked.status,
                },
            )
        db.refresh(locked)
        return locked

    def add_capa_action(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        capa_id: uuid.UUID,
        payload: CAPAActionCreateRequest,
        source_ip: Optional[str] = None,
    ) -> CAPAAction:
        capa = self.get_capa(db, tenant_ctx=tenant_ctx, capa_id=capa_id, source_ip=source_ip)
        if capa.status in {CAPAStatus.EFFECTIVENESS_REVIEW.value, CAPAStatus.CLOSED.value}:
            raise ConflictError(
                f"Cannot add actions while CAPA is in '{capa.status}' status.",
                error_code="CAPA_IMMUTABLE_IN_STATUS",
            )

        action_owner_id = payload.owner_membership_id or capa.owner_membership_id
        self._validate_active_membership(db, tenant_ctx.organization_id, action_owner_id)

        with UnitOfWork(db):
            locked_capa = self._repo.get_capa_for_org(
                db, tenant_ctx.organization_id, capa_id, for_update=True
            )
            if locked_capa is None:
                raise NotFoundError("CAPA not found.")
            action_seq = self._repo.next_capa_action_seq(db, tenant_ctx.organization_id, locked_capa.id)
            action = CAPAAction(
                organization_id=tenant_ctx.organization_id,
                capa_id=locked_capa.id,
                action_seq=action_seq,
                action_type=payload.action_type.value,
                title=payload.title.strip(),
                description=payload.description.strip() if payload.description else None,
                owner_membership_id=action_owner_id,
                status=CAPAActionStatus.PENDING.value,
                due_at=payload.due_at,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(action)
            db.flush()

            if locked_capa.status == CAPAStatus.OPEN.value:
                locked_capa.status = CAPAStatus.ACTIONS_IN_PROGRESS.value
                locked_capa.row_version += 1
                locked_capa.updated_by_user_id = tenant_ctx.user_id
                self._outbox.enqueue(
                    db,
                    organization_id=tenant_ctx.organization_id,
                    aggregate_type="capa",
                    aggregate_id=locked_capa.id,
                    event_type="CAPAStatusChanged",
                    payload_json={
                        "capa_id": str(locked_capa.id),
                        "organization_id": str(tenant_ctx.organization_id),
                        "capa_number": locked_capa.capa_number,
                        "from_status": CAPAStatus.OPEN.value,
                        "to_status": locked_capa.status,
                    },
                )

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=locked_capa.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="CAPA_ACTION_ADDED",
                entity_type="capa_actions",
                entity_id=action.id,
                source_ip=source_ip,
                after_state={
                    "capa_id": str(locked_capa.id),
                    "action_seq": action.action_seq,
                    "action_type": action.action_type,
                    "status": action.status,
                },
            )
        db.refresh(action)
        return action

    def update_capa_action(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        capa_id: uuid.UUID,
        action_id: uuid.UUID,
        payload: CAPAActionUpdateRequest,
        source_ip: Optional[str] = None,
    ) -> CAPAAction:
        capa = self.get_capa(db, tenant_ctx=tenant_ctx, capa_id=capa_id, source_ip=source_ip)
        if capa.status in {CAPAStatus.EFFECTIVENESS_REVIEW.value, CAPAStatus.CLOSED.value}:
            raise ConflictError(
                f"Cannot modify actions while CAPA is in '{capa.status}' status.",
                error_code="CAPA_IMMUTABLE_IN_STATUS",
            )

        action = self._repo.get_capa_action_for_org(db, tenant_ctx.organization_id, capa_id, action_id)
        if action is None:
            raise NotFoundError("CAPA action not found.")

        if payload.owner_membership_id is not None:
            self._validate_active_membership(db, tenant_ctx.organization_id, payload.owner_membership_id)

        resolved_doc_id, resolved_ver_id = self._validate_document_evidence_links(
            db,
            organization_id=tenant_ctx.organization_id,
            evidence_document_id=payload.evidence_document_id,
            evidence_document_version_id=payload.evidence_document_version_id,
        )

        target_status = payload.status.value if payload.status is not None else None
        if target_status is not None and target_status != action.status:
            assert_transition(CAPA_ACTION_TRANSITIONS, action.status, target_status, entity_name="CAPAAction")
            if (
                capa.status == CAPAStatus.DRAFT.value
                and target_status != CAPAActionStatus.CANCELLED.value
            ):
                raise ConflictError(
                    "CAPA must be approved/opened before executing or completing actions.",
                    error_code="CAPA_NOT_OPENED",
                )
            if target_status == CAPAActionStatus.COMPLETED.value:
                notes = payload.implementation_notes if payload.implementation_notes is not None else action.implementation_notes
                if not notes or len(notes.strip()) < 3:
                    raise ValidationError(
                        "implementation_notes are required when completing a CAPA action.",
                        error_code="IMPLEMENTATION_NOTES_REQUIRED",
                    )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            locked_capa = self._repo.get_capa_for_org(
                db, tenant_ctx.organization_id, capa_id, for_update=True
            )
            locked_action = self._repo.get_capa_action_for_org(
                db, tenant_ctx.organization_id, capa_id, action_id, for_update=True
            )
            if locked_capa is None or locked_action is None:
                raise NotFoundError("CAPA action not found.")
            verify_and_increment_version(locked_action, payload.row_version)
            prev_status = locked_action.status

            if payload.action_type is not None:
                locked_action.action_type = payload.action_type.value
            if payload.title is not None:
                locked_action.title = payload.title.strip()
            if payload.description is not None:
                locked_action.description = payload.description.strip()
            if payload.owner_membership_id is not None:
                locked_action.owner_membership_id = payload.owner_membership_id
            if payload.due_at is not None:
                locked_action.due_at = payload.due_at
            if payload.implementation_notes is not None:
                locked_action.implementation_notes = payload.implementation_notes.strip()
            if resolved_doc_id is not None:
                locked_action.evidence_document_id = resolved_doc_id
            if resolved_ver_id is not None:
                locked_action.evidence_document_version_id = resolved_ver_id

            if target_status is not None and target_status != prev_status:
                locked_action.status = target_status
                if target_status == CAPAActionStatus.COMPLETED.value:
                    locked_action.completed_at = now
                    locked_action.completed_by_user_id = tenant_ctx.user_id
                    self._outbox.enqueue(
                        db,
                        organization_id=tenant_ctx.organization_id,
                        aggregate_type="capa",
                        aggregate_id=locked_capa.id,
                        event_type="CAPAActionCompleted",
                        payload_json={
                            "capa_id": str(locked_capa.id),
                            "action_id": str(locked_action.id),
                            "action_seq": locked_action.action_seq,
                            "organization_id": str(tenant_ctx.organization_id),
                        },
                    )
                if (
                    locked_capa.status == CAPAStatus.OPEN.value
                    and target_status in {CAPAActionStatus.IN_PROGRESS.value, CAPAActionStatus.COMPLETED.value}
                ):
                    locked_capa.status = CAPAStatus.ACTIONS_IN_PROGRESS.value
                    locked_capa.row_version += 1
                    locked_capa.updated_by_user_id = tenant_ctx.user_id
                    self._outbox.enqueue(
                        db,
                        organization_id=tenant_ctx.organization_id,
                        aggregate_type="capa",
                        aggregate_id=locked_capa.id,
                        event_type="CAPAStatusChanged",
                        payload_json={
                            "capa_id": str(locked_capa.id),
                            "organization_id": str(tenant_ctx.organization_id),
                            "capa_number": locked_capa.capa_number,
                            "from_status": CAPAStatus.OPEN.value,
                            "to_status": locked_capa.status,
                        },
                    )

            locked_action.updated_by_user_id = tenant_ctx.user_id
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=locked_capa.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="CAPA_ACTION_UPDATED",
                entity_type="capa_actions",
                entity_id=locked_action.id,
                reason=payload.reason,
                source_ip=source_ip,
                before_state={"status": prev_status},
                after_state={"status": locked_action.status},
            )
        db.refresh(locked_action)
        return locked_action

    def complete_capa_action(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        capa_id: uuid.UUID,
        action_id: uuid.UUID,
        payload: CAPAActionCompleteRequest,
        source_ip: Optional[str] = None,
    ) -> CAPAAction:
        capa = self.get_capa(db, tenant_ctx=tenant_ctx, capa_id=capa_id, source_ip=source_ip)
        if capa.status not in {CAPAStatus.OPEN.value, CAPAStatus.ACTIONS_IN_PROGRESS.value}:
            raise ConflictError(
                f"CAPA actions can only be completed when CAPA is OPEN or ACTIONS_IN_PROGRESS (current: '{capa.status}').",
                error_code="CAPA_NOT_OPENED" if capa.status == CAPAStatus.DRAFT.value else "CAPA_IMMUTABLE_IN_STATUS",
            )

        action = self._repo.get_capa_action_for_org(db, tenant_ctx.organization_id, capa_id, action_id)
        if action is None:
            raise NotFoundError("CAPA action not found.")
        assert_transition(
            CAPA_ACTION_TRANSITIONS,
            action.status,
            CAPAActionStatus.COMPLETED.value,
            entity_name="CAPAAction",
        )

        resolved_doc_id, resolved_ver_id = self._validate_document_evidence_links(
            db,
            organization_id=tenant_ctx.organization_id,
            evidence_document_id=payload.evidence_document_id,
            evidence_document_version_id=payload.evidence_document_version_id,
        )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            locked_capa = self._repo.get_capa_for_org(
                db, tenant_ctx.organization_id, capa_id, for_update=True
            )
            locked_action = self._repo.get_capa_action_for_org(
                db, tenant_ctx.organization_id, capa_id, action_id, for_update=True
            )
            if locked_capa is None or locked_action is None:
                raise NotFoundError("CAPA action not found.")
            verify_and_increment_version(locked_action, payload.row_version)
            prev_status = locked_action.status

            locked_action.status = CAPAActionStatus.COMPLETED.value
            locked_action.completed_at = now
            locked_action.completed_by_user_id = tenant_ctx.user_id
            locked_action.implementation_notes = payload.implementation_notes.strip()
            if resolved_doc_id is not None:
                locked_action.evidence_document_id = resolved_doc_id
            if resolved_ver_id is not None:
                locked_action.evidence_document_version_id = resolved_ver_id
            locked_action.updated_by_user_id = tenant_ctx.user_id

            if locked_capa.status == CAPAStatus.OPEN.value:
                locked_capa.status = CAPAStatus.ACTIONS_IN_PROGRESS.value
                locked_capa.row_version += 1
                locked_capa.updated_by_user_id = tenant_ctx.user_id
                self._outbox.enqueue(
                    db,
                    organization_id=tenant_ctx.organization_id,
                    aggregate_type="capa",
                    aggregate_id=locked_capa.id,
                    event_type="CAPAStatusChanged",
                    payload_json={
                        "capa_id": str(locked_capa.id),
                        "organization_id": str(tenant_ctx.organization_id),
                        "capa_number": locked_capa.capa_number,
                        "from_status": CAPAStatus.OPEN.value,
                        "to_status": locked_capa.status,
                    },
                )

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=locked_capa.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="CAPA_ACTION_COMPLETED",
                entity_type="capa_actions",
                entity_id=locked_action.id,
                reason=payload.implementation_notes.strip(),
                source_ip=source_ip,
                before_state={"status": prev_status},
                after_state={
                    "status": locked_action.status,
                    "evidence_document_id": str(locked_action.evidence_document_id)
                    if locked_action.evidence_document_id
                    else None,
                },
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="capa",
                aggregate_id=locked_capa.id,
                event_type="CAPAActionCompleted",
                payload_json={
                    "capa_id": str(locked_capa.id),
                    "action_id": str(locked_action.id),
                    "action_seq": locked_action.action_seq,
                    "organization_id": str(tenant_ctx.organization_id),
                },
            )
        db.refresh(locked_action)
        return locked_action

    def submit_for_effectiveness(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        capa_id: uuid.UUID,
        payload: CAPASubmitEffectivenessRequest,
        source_ip: Optional[str] = None,
    ) -> CAPA:
        capa = self.get_capa(db, tenant_ctx=tenant_ctx, capa_id=capa_id, source_ip=source_ip)
        assert_transition(
            CAPA_TRANSITIONS,
            capa.status,
            CAPAStatus.EFFECTIVENESS_REVIEW.value,
            entity_name="CAPA",
        )

        actions = self._repo.list_capa_actions(db, tenant_ctx.organization_id, capa.id)
        open_actions = [
            a
            for a in actions
            if a.status in {CAPAActionStatus.PENDING.value, CAPAActionStatus.IN_PROGRESS.value}
        ]
        completed_actions = [
            a
            for a in actions
            if a.status in {CAPAActionStatus.COMPLETED.value, CAPAActionStatus.VERIFIED.value}
        ]
        if not actions or not completed_actions or open_actions:
            raise ConflictError(
                "CAPA cannot enter effectiveness review until at least one action is completed and zero actions remain PENDING or IN_PROGRESS.",
                error_code="CAPA_ACTIONS_INCOMPLETE",
                details={
                    "total_actions": len(actions),
                    "completed_or_verified_actions": len(completed_actions),
                    "open_action_ids": [str(a.id) for a in open_actions],
                },
            )

        with UnitOfWork(db):
            locked = self._repo.get_capa_for_org(
                db, tenant_ctx.organization_id, capa_id, for_update=True
            )
            if locked is None:
                raise NotFoundError("CAPA not found.")
            verify_and_increment_version(locked, payload.row_version)
            prev_status = locked.status
            locked.status = CAPAStatus.EFFECTIVENESS_REVIEW.value
            locked.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=locked.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="CAPA_SUBMITTED_FOR_EFFECTIVENESS",
                entity_type="capas",
                entity_id=locked.id,
                reason=payload.comments.strip() if payload.comments else None,
                source_ip=source_ip,
                before_state={"status": prev_status},
                after_state={"status": locked.status},
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="capa",
                aggregate_id=locked.id,
                event_type="CAPAStatusChanged",
                payload_json={
                    "capa_id": str(locked.id),
                    "organization_id": str(tenant_ctx.organization_id),
                    "capa_number": locked.capa_number,
                    "from_status": prev_status,
                    "to_status": locked.status,
                },
            )
        db.refresh(locked)
        return locked

    def create_effectiveness_check(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        capa_id: uuid.UUID,
        payload: CAPAEffectivenessCheckCreateRequest,
        source_ip: Optional[str] = None,
    ) -> CAPAEffectivenessCheck:
        capa = self.get_capa(db, tenant_ctx=tenant_ctx, capa_id=capa_id, source_ip=source_ip)
        if capa.status == CAPAStatus.CLOSED.value:
            raise ConflictError(
                "Cannot add effectiveness checks to a CLOSED CAPA.",
                error_code="CAPA_IMMUTABLE_IN_STATUS",
            )

        with UnitOfWork(db):
            locked_capa = self._repo.get_capa_for_org(
                db, tenant_ctx.organization_id, capa_id, for_update=True
            )
            if locked_capa is None:
                raise NotFoundError("CAPA not found.")
            check_seq = self._repo.next_effectiveness_check_seq(
                db, tenant_ctx.organization_id, locked_capa.id
            )
            check = CAPAEffectivenessCheck(
                organization_id=tenant_ctx.organization_id,
                capa_id=locked_capa.id,
                check_seq=check_seq,
                criteria_description=payload.criteria_description.strip(),
                evaluation_method=(
                    payload.evaluation_method.strip() if payload.evaluation_method else None
                ),
                planned_check_date=payload.planned_check_date,
                status=EffectivenessCheckStatus.PENDING.value,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(check)
            db.flush()

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=locked_capa.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="CAPA_EFFECTIVENESS_CHECK_CREATED",
                entity_type="capa_effectiveness_checks",
                entity_id=check.id,
                source_ip=source_ip,
                after_state={
                    "capa_id": str(locked_capa.id),
                    "check_seq": check.check_seq,
                    "planned_check_date": check.planned_check_date.isoformat(),
                    "status": check.status,
                },
            )
        db.refresh(check)
        return check

    def record_effectiveness_check(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        capa_id: uuid.UUID,
        check_id: uuid.UUID,
        payload: CAPAEffectivenessCheckRecordRequest,
        source_ip: Optional[str] = None,
    ) -> CAPAEffectivenessCheck:
        capa = self.get_capa(db, tenant_ctx=tenant_ctx, capa_id=capa_id, source_ip=source_ip)
        if capa.status != CAPAStatus.EFFECTIVENESS_REVIEW.value:
            raise ConflictError(
                f"Effectiveness check results can only be recorded when CAPA is in EFFECTIVENESS_REVIEW (current: '{capa.status}').",
                error_code="INVALID_STATE_TRANSITION",
            )

        check = self._repo.get_effectiveness_check_for_org(
            db, tenant_ctx.organization_id, capa_id, check_id
        )
        if check is None:
            raise NotFoundError("CAPA effectiveness check not found.")
        if check.status != EffectivenessCheckStatus.PENDING.value:
            raise ConflictError(
                f"Effectiveness check in '{check.status}' status has already been recorded.",
                error_code="INVALID_STATE_TRANSITION",
            )

        if tenant_ctx.membership_id is None:
            raise PermissionDeniedError(
                "Active organization membership is required to record effectiveness checks."
            )

        owner_mem = db.get(OrganizationMembership, capa.owner_membership_id)
        if tenant_ctx.membership_id == capa.owner_membership_id or (
            owner_mem is not None and owner_mem.user_id == tenant_ctx.user_id
        ):
            self._record_maker_checker_violation(
                tenant_ctx=tenant_ctx,
                source_ip=source_ip,
                target_resource=f"/api/v1/capas/{capa_id}/effectiveness-checks/{check_id}/record",
                details={
                    "capa_id": str(capa.id),
                    "check_id": str(check.id),
                    "owner_membership_id": str(capa.owner_membership_id),
                    "owner_user_id": str(owner_mem.user_id) if owner_mem else None,
                    "reviewer_membership_id": str(tenant_ctx.membership_id),
                    "reviewer_user_id": str(tenant_ctx.user_id),
                },
                message="Maker-checker violation: CAPA effectiveness reviewer must be distinct from the CAPA owner.",
            )

        resolved_doc_id, resolved_ver_id = self._validate_document_evidence_links(
            db,
            organization_id=tenant_ctx.organization_id,
            evidence_document_id=payload.evidence_document_id,
            evidence_document_version_id=payload.evidence_document_version_id,
        )

        approval_decision = {
            EffectivenessCheckStatus.EFFECTIVE.value: ApprovalDecision.APPROVED.value,
            EffectivenessCheckStatus.INEFFECTIVE.value: ApprovalDecision.REJECTED.value,
            EffectivenessCheckStatus.INCONCLUSIVE.value: ApprovalDecision.REQUEST_MORE_WORK.value,
        }[payload.status]

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            locked_capa = self._repo.get_capa_for_org(
                db, tenant_ctx.organization_id, capa_id, for_update=True
            )
            locked_check = self._repo.get_effectiveness_check_for_org(
                db, tenant_ctx.organization_id, capa_id, check_id, for_update=True
            )
            if locked_capa is None or locked_check is None:
                raise NotFoundError("CAPA effectiveness check not found.")
            verify_and_increment_version(locked_check, payload.row_version)

            locked_check.status = payload.status
            locked_check.reviewer_membership_id = tenant_ctx.membership_id
            locked_check.reviewer_user_id = tenant_ctx.user_id
            locked_check.reviewed_at = now
            locked_check.findings_summary = payload.findings_summary.strip()
            if resolved_doc_id is not None:
                locked_check.evidence_document_id = resolved_doc_id
            if resolved_ver_id is not None:
                locked_check.evidence_document_version_id = resolved_ver_id
            locked_check.updated_by_user_id = tenant_ctx.user_id

            approval = ApprovalRecord(
                organization_id=tenant_ctx.organization_id,
                entity_type="capa_effectiveness_check",
                entity_id=locked_check.id,
                approval_stage="CAPA_EFFECTIVENESS_REVIEW",
                decision=approval_decision,
                approver_membership_id=tenant_ctx.membership_id,
                decided_at=now,
                comments=payload.findings_summary.strip(),
            )
            db.add(approval)
            db.flush()

            if payload.status == EffectivenessCheckStatus.INEFFECTIVE.value:
                prev_capa_status = locked_capa.status
                locked_capa.status = CAPAStatus.ACTIONS_IN_PROGRESS.value
                locked_capa.row_version += 1
                locked_capa.updated_by_user_id = tenant_ctx.user_id
                self._outbox.enqueue(
                    db,
                    organization_id=tenant_ctx.organization_id,
                    aggregate_type="capa",
                    aggregate_id=locked_capa.id,
                    event_type="CAPAStatusChanged",
                    payload_json={
                        "capa_id": str(locked_capa.id),
                        "organization_id": str(tenant_ctx.organization_id),
                        "capa_number": locked_capa.capa_number,
                        "from_status": prev_capa_status,
                        "to_status": locked_capa.status,
                    },
                )

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=locked_capa.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="CAPA_EFFECTIVENESS_RECORDED",
                entity_type="capa_effectiveness_checks",
                entity_id=locked_check.id,
                reason=payload.findings_summary.strip(),
                source_ip=source_ip,
                before_state={"status": EffectivenessCheckStatus.PENDING.value},
                after_state={
                    "status": locked_check.status,
                    "approval_record_id": str(approval.id),
                    "capa_status": locked_capa.status,
                },
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="capa",
                aggregate_id=locked_capa.id,
                event_type="CAPAEffectivenessRecorded",
                payload_json={
                    "capa_id": str(locked_capa.id),
                    "check_id": str(locked_check.id),
                    "check_seq": locked_check.check_seq,
                    "status": locked_check.status,
                    "capa_status": locked_capa.status,
                    "organization_id": str(tenant_ctx.organization_id),
                },
            )
        db.refresh(locked_check)
        return locked_check

    def close_capa(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        capa_id: uuid.UUID,
        payload: CAPACloseRequest,
        source_ip: Optional[str] = None,
    ) -> CAPA:
        capa = self.get_capa(db, tenant_ctx=tenant_ctx, capa_id=capa_id, source_ip=source_ip)
        assert_transition(CAPA_TRANSITIONS, capa.status, CAPAStatus.CLOSED.value, entity_name="CAPA")
        if tenant_ctx.membership_id is None:
            raise PermissionDeniedError("Active organization membership is required to close CAPAs.")

        owner_mem = db.get(OrganizationMembership, capa.owner_membership_id)
        if (
            (capa.created_by_user_id is not None and capa.created_by_user_id == tenant_ctx.user_id)
            or tenant_ctx.membership_id == capa.owner_membership_id
            or (owner_mem is not None and owner_mem.user_id == tenant_ctx.user_id)
        ):
            self._record_maker_checker_violation(
                tenant_ctx=tenant_ctx,
                source_ip=source_ip,
                target_resource=f"/api/v1/capas/{capa_id}/close",
                details={
                    "capa_id": str(capa.id),
                    "created_by_user_id": str(capa.created_by_user_id) if capa.created_by_user_id else None,
                    "owner_membership_id": str(capa.owner_membership_id),
                    "owner_user_id": str(owner_mem.user_id) if owner_mem else None,
                    "closer_membership_id": str(tenant_ctx.membership_id),
                    "closer_user_id": str(tenant_ctx.user_id),
                },
                message="Maker-checker violation: CAPA closer must be distinct from the creator and owner.",
            )

        actions = self._repo.list_capa_actions(db, tenant_ctx.organization_id, capa.id)
        open_actions = [
            a
            for a in actions
            if a.status in {CAPAActionStatus.PENDING.value, CAPAActionStatus.IN_PROGRESS.value}
        ]
        if open_actions:
            raise ConflictError(
                "Cannot close CAPA while PENDING or IN_PROGRESS actions remain.",
                error_code="CAPA_ACTIONS_INCOMPLETE",
                details={"open_action_ids": [str(a.id) for a in open_actions]},
            )

        checks = self._repo.list_effectiveness_checks(db, tenant_ctx.organization_id, capa.id)
        if (
            not checks
            or not any(c.status == EffectivenessCheckStatus.EFFECTIVE.value for c in checks)
            or checks[-1].status != EffectivenessCheckStatus.EFFECTIVE.value
        ):
            raise ConflictError(
                "Cannot close CAPA without a completed EFFECTIVE latest effectiveness check.",
                error_code="EFFECTIVENESS_NOT_VERIFIED",
            )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            locked = self._repo.get_capa_for_org(
                db, tenant_ctx.organization_id, capa_id, for_update=True
            )
            if locked is None:
                raise NotFoundError("CAPA not found.")
            verify_and_increment_version(locked, payload.row_version)
            prev_status = locked.status

            approval = ApprovalRecord(
                organization_id=tenant_ctx.organization_id,
                entity_type="capa",
                entity_id=locked.id,
                approval_stage="CAPA_CLOSURE",
                decision=ApprovalDecision.APPROVED.value,
                approver_membership_id=tenant_ctx.membership_id,
                decided_at=now,
                comments=payload.closure_reason.strip(),
            )
            db.add(approval)
            db.flush()

            locked.status = CAPAStatus.CLOSED.value
            locked.closed_at = now
            locked.closure_reason = payload.closure_reason.strip()
            locked.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=locked.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="CAPA_CLOSED",
                entity_type="capas",
                entity_id=locked.id,
                reason=payload.closure_reason.strip(),
                source_ip=source_ip,
                before_state={"status": prev_status},
                after_state={"status": locked.status, "approval_record_id": str(approval.id)},
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="capa",
                aggregate_id=locked.id,
                event_type="CAPAClosed",
                payload_json={
                    "capa_id": str(locked.id),
                    "organization_id": str(tenant_ctx.organization_id),
                    "capa_number": locked.capa_number,
                    "status": locked.status,
                },
            )
        db.refresh(locked)
        return locked

    def check_overdue_capas(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        source_ip: Optional[str] = None,
    ) -> Tuple[List[uuid.UUID], List[uuid.UUID]]:
        now = datetime.now(timezone.utc)
        overdue = self._repo.list_overdue_open_capas_for_org(
            db,
            tenant_ctx.organization_id,
            now=now,
            tenant_ctx=tenant_ctx,
        )
        if not overdue:
            return [], []

        capa_ids: List[uuid.UUID] = []
        event_ids: List[uuid.UUID] = []
        with UnitOfWork(db):
            for capa in overdue:
                ev = self._outbox.enqueue(
                    db,
                    organization_id=tenant_ctx.organization_id,
                    aggregate_type="capa",
                    aggregate_id=capa.id,
                    event_type="CAPAOverdue",
                    payload_json={
                        "capa_id": str(capa.id),
                        "capa_number": capa.capa_number,
                        "organization_id": str(tenant_ctx.organization_id),
                    },
                )
                capa_ids.append(capa.id)
                event_ids.append(ev.id)
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=None,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="CAPA_OVERDUE_CHECK_QUEUED",
                entity_type="capas",
                entity_id=capa_ids[0],
                source_ip=source_ip,
                after_state={"queued_capa_ids": [str(cid) for cid in capa_ids]},
            )
        return capa_ids, event_ids

    def handle_outbox_capa_overdue(
        self,
        db: Session,
        event: OutboxEvent,
        tenant_ctx: Optional[TenantContext],
    ) -> None:
        """
        Worker hook invoked by `app/worker.py:_handle_capa_overdue` inside the worker's UnitOfWork.
        Marks `overdue_notified_at`, creates idempotent durable Notifications for the CAPA owner
        and site/org QA heads (`QA_HEAD`, `QMS_HEAD`), and records an AuditEvent.
        """
        if tenant_ctx is None:
            raise ValueError("CAPAOverdue handler requires organization_id.")

        now = datetime.now(timezone.utc)
        target_capa_id = (
            event.aggregate_id
            if event.event_type == "CAPAOverdue" and event.aggregate_type == "capa"
            else None
        )
        overdue_capas = self._repo.list_overdue_open_capas_for_org(
            db,
            tenant_ctx.organization_id,
            now=now,
            capa_id=target_capa_id,
            for_update=True,
        )
        if not overdue_capas:
            return

        notif_svc = NotificationService()
        for capa in overdue_capas:
            capa.overdue_notified_at = now
            capa.updated_at = now

            qa_stmt = (
                select(OrganizationMembership.id)
                .join(
                    MembershipRoleAssignment,
                    MembershipRoleAssignment.membership_id == OrganizationMembership.id,
                )
                .join(Role, Role.id == MembershipRoleAssignment.role_id)
                .where(
                    OrganizationMembership.organization_id == tenant_ctx.organization_id,
                    OrganizationMembership.membership_status == MembershipStatus.ACTIVE.value,
                    MembershipRoleAssignment.organization_id == tenant_ctx.organization_id,
                    MembershipRoleAssignment.status == RoleAssignmentStatus.ACTIVE.value,
                    Role.role_code.in_(("QA_HEAD", "QMS_HEAD")),
                )
            )
            if capa.site_id is not None:
                qa_stmt = qa_stmt.where(
                    or_(
                        MembershipRoleAssignment.scope_type == RoleAssignmentScopeType.ORGANIZATION.value,
                        MembershipRoleAssignment.site_id == capa.site_id,
                    )
                )
            recipient_ids = list(
                dict.fromkeys([capa.owner_membership_id, *db.scalars(qa_stmt).all()])
            )
            existing_recipients = set(
                db.scalars(
                    select(Notification.recipient_membership_id).where(
                        Notification.organization_id == tenant_ctx.organization_id,
                        Notification.event_type == "CAPAOverdueNotified",
                        Notification.aggregate_id == capa.id,
                        Notification.recipient_membership_id.in_(recipient_ids),
                    )
                ).all()
            )

            created_count = 0
            for recipient_id in recipient_ids:
                if recipient_id in existing_recipients:
                    continue
                notif_svc.create_in_tx(
                    db,
                    organization_id=tenant_ctx.organization_id,
                    recipient_membership_id=recipient_id,
                    event_type="CAPAOverdueNotified",
                    aggregate_type="capas",
                    aggregate_id=capa.id,
                    title=f"CAPA Overdue: {capa.capa_number}",
                    body=f"CAPA {capa.capa_number} ('{capa.title}') is past its due date and remains in {capa.status}.",
                )
                created_count += 1

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=capa.site_id,
                actor_type=ActorType.SYSTEM_WORKER.value,
                actor_id=None,
                action="CAPA_OVERDUE_NOTIFIED",
                entity_type="capas",
                entity_id=capa.id,
                after_state={
                    "capa_number": capa.capa_number,
                    "overdue_notified_at": now.isoformat(),
                    "notifications_created": created_count,
                },
            )
        db.flush()
