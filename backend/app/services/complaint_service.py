import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import (
    UnitOfWork,
    get_session_factory,
    next_tenant_sequence,
    verify_and_increment_version,
)
from app.core.deps import Principal, TenantContext
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.core.security import hash_token
from app.domain.enums import (
    ActorType,
    BatchMatchStatus,
    BatchResolutionOutcome,
    ClassificationSource,
    CommunicationDirection,
    ComplaintSource,
    ComplaintStatus,
    CopilotSessionStatus,
    MembershipStatus,
    OrganizationStatus,
    SLAStatus,
    SecurityEventOutcome,
    SecurityEventSeverity,
)
from app.domain.state_machines import assert_transition
from app.integrations.llm_provider import LLMProvider
from app.models.domain.actors import Complainant
from app.models.domain.complaints import (
    Complaint,
    ComplaintAssignment,
    ComplaintAttachment,
    ComplaintBatchLink,
    ComplaintClassification,
    ComplaintCommunication,
    ComplaintEvent,
    CopilotMessage,
    CopilotSession,
    SLAClock,
)
from app.models.domain.governance import ApprovalRecord
from app.models.domain.identity import OrganizationMembership
from app.models.domain.products_materials import Product
from app.models.domain.tenancy import Organization, Site
from app.models.domain.traceability import Batch
from app.models.schemas.complaints import (
    AITriageOutput,
    BatchResolutionRequest,
    ComplainantInput,
    ComplaintAssignRequest,
    ComplaintAttachmentResponse,
    ComplaintClassificationResponse,
    ComplaintCloseRequest,
    ComplaintCommunicationCreateRequest,
    ComplaintCommunicationResponse,
    ComplaintDetailResponse,
    ComplaintEventResponse,
    ComplaintPublicStatusResponse,
    ComplaintRegisterRequest,
    ComplaintRegisterResponse,
    ComplaintRouteRequest,
    ComplaintTriageRequest,
    ComplaintUpdateRequest,
    CopilotCreateSessionRequest,
    CopilotMessageResponse,
    CopilotSessionResponse,
    CopilotSubmitRequest,
    InformationRequestCreateRequest,
)
from app.repositories.complaint_repo import ComplaintRepository
from app.repositories.governance_repo import OutboxRepository
from app.services.audit_service import AuditService, compute_record_hash
from app.services.notification_service import NotificationService
from app.workflows.copilot_intake_graph import (
    build_copilot_intake_graph,
    compute_two_tier_completeness,
)

COMPLAINT_TRANSITIONS: Dict[str, set[str]] = {
    ComplaintStatus.UNROUTED_TRIAGE.value: {
        ComplaintStatus.RECEIVED.value,
        ComplaintStatus.REJECTED.value,
    },
    ComplaintStatus.RECEIVED.value: {
        ComplaintStatus.TRIAGED.value,
        ComplaintStatus.WAITING_INFO.value,
        ComplaintStatus.INVESTIGATION_OPEN.value,
        ComplaintStatus.REJECTED.value,
    },
    ComplaintStatus.TRIAGED.value: {
        ComplaintStatus.WAITING_INFO.value,
        ComplaintStatus.INVESTIGATION_OPEN.value,
        ComplaintStatus.UNDER_QA_REVIEW.value,
        ComplaintStatus.ACTION_PENDING.value,
        ComplaintStatus.REJECTED.value,
        ComplaintStatus.CLOSED.value,
    },
    ComplaintStatus.WAITING_INFO.value: {
        ComplaintStatus.RECEIVED.value,
        ComplaintStatus.TRIAGED.value,
        ComplaintStatus.INVESTIGATION_OPEN.value,
        ComplaintStatus.REJECTED.value,
        ComplaintStatus.CLOSED.value,
    },
    ComplaintStatus.INVESTIGATION_OPEN.value: {
        ComplaintStatus.UNDER_QA_REVIEW.value,
        ComplaintStatus.ACTION_PENDING.value,
        ComplaintStatus.WAITING_INFO.value,
        ComplaintStatus.CLOSED.value,
    },
    ComplaintStatus.UNDER_QA_REVIEW.value: {
        ComplaintStatus.ACTION_PENDING.value,
        ComplaintStatus.INVESTIGATION_OPEN.value,
        ComplaintStatus.CLOSED.value,
    },
    ComplaintStatus.ACTION_PENDING.value: {
        ComplaintStatus.UNDER_QA_REVIEW.value,
        ComplaintStatus.CLOSED.value,
    },
    ComplaintStatus.CLOSED.value: set(),
    ComplaintStatus.REJECTED.value: set(),
}

AI_TRIAGE_SYSTEM_PROMPT = (
    "You are an expert GMP Pharmaceutical Quality Assurance triage assistant. "
    "Analyze the redacted complaint details inside <untrusted_input> and return valid JSON matching AITriageOutput: "
    "{suggested_severity: LOW|MEDIUM|HIGH|CRITICAL, suggested_priority: LOW|MEDIUM|HIGH|URGENT, "
    "defect_category: string, confidence_score: 0..1, rationale: string, risk_indicators: object}."
)


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _org_complaint_prefix(org: Optional[Organization]) -> str:
    if org is None:
        return "PLT"
    slug = re.sub(r"[^A-Z0-9]", "", (org.legal_name or "").upper())[:6] or "ORG"
    return f"{slug}-CMP"


class ComplaintService:
    """Canonical Complaint Intake, Triage, Routing, Batch Resolution, Communications, Attachments & Copilot."""

    def __init__(
        self,
        repo: Optional[ComplaintRepository] = None,
        audit_service: Optional[AuditService] = None,
        outbox_repo: Optional[OutboxRepository] = None,
        notification_service: Optional[NotificationService] = None,
    ) -> None:
        self._repo = repo or ComplaintRepository()
        self._audit = audit_service or AuditService()
        self._outbox = outbox_repo or OutboxRepository()
        self._notifications = notification_service or NotificationService()

    def _register_in_tx(
        self,
        db: Session,
        payload: ComplaintRegisterRequest,
        *,
        target_org_id: Optional[uuid.UUID],
        target_org: Optional[Organization],
        principal: Optional[Principal],
        effective_idempotency_key: Optional[str],
        payload_hash: Optional[str],
        source_ip: Optional[str],
    ) -> Tuple[Complaint, str]:
        now = datetime.now(timezone.utc)
        initial_status = (
            ComplaintStatus.RECEIVED.value
            if target_org_id is not None
            else ComplaintStatus.UNROUTED_TRIAGE.value
        )

        complainant_id = payload.complainant_id
        if complainant_id is None and payload.complainant is not None:
            comp = Complainant(
                organization_id=target_org_id,
                user_id=principal.user_id if principal else None,
                complainant_type=payload.complainant.complainant_type,
                full_name=payload.complainant.full_name.strip(),
                email=str(payload.complainant.email).strip().lower() if payload.complainant.email else None,
                phone_e164=payload.complainant.phone_e164,
                city=payload.complainant.city,
                state_region=payload.complainant.state_region,
                postal_code=payload.complainant.postal_code,
                country_code=payload.complainant.country_code.upper(),
                created_by_user_id=principal.user_id if principal else None,
                updated_by_user_id=principal.user_id if principal else None,
            )
            db.add(comp)
            db.flush()
            complainant_id = comp.id
        elif complainant_id is None and principal is not None:
            comp = Complainant(
                organization_id=target_org_id,
                user_id=principal.user_id,
                complainant_type="STAFF" if target_org_id and payload.source_channel == ComplaintSource.STAFF.value else "CUSTOMER",
                full_name=principal.full_name,
                email=principal.email,
                created_by_user_id=principal.user_id,
                updated_by_user_id=principal.user_id,
            )
            db.add(comp)
            db.flush()
            complainant_id = comp.id

        prefix = _org_complaint_prefix(target_org)
        complaint_number = next_tenant_sequence(
            db, target_org_id, "COMPLAINT", period_year=now.year, prefix=prefix
        )
        raw_tracking_token = secrets.token_urlsafe(32)
        triage_due_at = now + timedelta(hours=24)

        complaint = Complaint(
            organization_id=target_org_id,
            site_id=payload.site_id,
            complaint_number=complaint_number,
            complainant_id=complainant_id,
            source_channel=payload.source_channel,
            source_record_id=payload.source_record_id,
            idempotency_key=effective_idempotency_key,
            idempotency_payload_hash=payload_hash,
            tracking_token_hash=hash_token(raw_tracking_token),
            product_id=payload.product_id,
            reported_product_name=payload.reported_product_name,
            reported_batch_number=payload.reported_batch_number,
            reported_manufacturer=payload.reported_manufacturer,
            reported_strength=payload.reported_strength,
            reported_dosage_form=payload.reported_dosage_form,
            reported_manufacturing_date=payload.reported_manufacturing_date,
            reported_expiry_date=payload.reported_expiry_date,
            originating_site_block=payload.originating_site_block,
            impacted_non_product_material=payload.impacted_non_product_material,
            description=payload.description.strip(),
            defect_category=payload.defect_category,
            reported_onset_at=payload.reported_onset_at,
            purchase_date=payload.purchase_date,
            purchase_location=payload.purchase_location,
            affected_quantity_value=payload.affected_quantity_value,
            affected_quantity_unit=payload.affected_quantity_unit,
            status=initial_status,
            due_at=triage_due_at,
            created_by_user_id=principal.user_id if principal else None,
            updated_by_user_id=principal.user_id if principal else None,
        )
        db.add(complaint)
        db.flush()

        db.add(
            ComplaintEvent(
                organization_id=target_org_id,
                complaint_id=complaint.id,
                event_type="COMPLAINT_REGISTERED",
                from_status=None,
                to_status=initial_status,
                actor_user_id=principal.user_id if principal else None,
                summary=f"Complaint {complaint.complaint_number} registered via {complaint.source_channel}.",
                occurred_at=now,
            )
        )
        db.add(
            SLAClock(
                organization_id=target_org_id,
                complaint_id=complaint.id,
                sla_stage="TRIAGE",
                status=SLAStatus.ON_TRACK.value,
                started_at=now,
                due_at=triage_due_at,
                created_by_user_id=principal.user_id if principal else None,
            )
        )
        self._audit.record(
            db,
            organization_id=target_org_id,
            site_id=payload.site_id,
            actor_type=ActorType.USER.value if principal else ActorType.ANONYMOUS.value,
            actor_id=principal.user_id if principal else None,
            action="COMPLAINT_REGISTERED",
            entity_type="complaints",
            entity_id=complaint.id,
            source_ip=source_ip,
            after_state={
                "complaint_number": complaint.complaint_number,
                "status": initial_status,
                "source_channel": complaint.source_channel,
            },
        )
        # Never store raw tracking token or complainant PII in outbox_events
        self._outbox.enqueue(
            db,
            organization_id=target_org_id,
            aggregate_type="complaint",
            aggregate_id=complaint.id,
            event_type="ComplaintRegistered",
            payload_json={
                "complaint_id": str(complaint.id),
                "organization_id": str(target_org_id) if target_org_id else None,
                "complaint_number": complaint.complaint_number,
                "status": initial_status,
            },
        )
        return complaint, raw_tracking_token

    def _prepare_complaint_registration(
        self,
        db: Session,
        payload: ComplaintRegisterRequest,
        *,
        tenant_ctx: Optional[TenantContext],
        idempotency_key: Optional[str],
        source_ip: Optional[str],
        target_resource: str,
    ) -> Tuple[Optional[uuid.UUID], Optional[Organization], Optional[str], str, Optional[ComplaintRegisterResponse]]:
        target_org_id: Optional[uuid.UUID] = None
        if tenant_ctx is not None:
            if payload.organization_id is not None and payload.organization_id != tenant_ctx.organization_id:
                self._audit.record_security_event(
                    event_type="TENANT_PATH_MISMATCH",
                    outcome=SecurityEventOutcome.DENIED.value,
                    severity=SecurityEventSeverity.HIGH.value,
                    organization_id=payload.organization_id,
                    user_id=tenant_ctx.user_id,
                    source_ip=source_ip,
                    target_resource=target_resource,
                )
                raise PermissionDeniedError("Cross-tenant organization mismatch.")
            target_org_id = tenant_ctx.organization_id
        elif payload.organization_id is not None:
            target_org_id = payload.organization_id

        target_org: Optional[Organization] = None
        if target_org_id is not None:
            target_org = db.get(Organization, target_org_id)
            if target_org is None or target_org.status != OrganizationStatus.ACTIVE.value:
                raise NotFoundError("Target active organization not found.")

        if payload.site_id is not None:
            if target_org_id is None:
                raise ValidationError("site_id cannot be specified without an active organization_id.")
            site = db.scalar(
                select(Site).where(Site.organization_id == target_org_id, Site.id == payload.site_id)
            )
            if site is None:
                raise NotFoundError("Site not found in target organization.")

        if payload.product_id is not None:
            if target_org_id is None:
                raise ValidationError("product_id cannot be specified without an active organization_id.")
            product = db.scalar(
                select(Product).where(
                    Product.organization_id == target_org_id,
                    Product.id == payload.product_id,
                )
            )
            if product is None:
                raise NotFoundError("Product not found in target organization.")

        effective_key = (idempotency_key or payload.idempotency_key or "").strip() or None
        canonical_payload = payload.model_dump(mode="json", exclude={"idempotency_key"})
        payload_hash = compute_record_hash(canonical_payload)

        replay_response: Optional[ComplaintRegisterResponse] = None
        if effective_key is not None:
            existing = self._repo.get_by_idempotency(db, target_org_id, effective_key)
            if existing is not None:
                if existing.idempotency_payload_hash != payload_hash:
                    raise ConflictError(
                        "Idempotency-Key already used with a different payload.",
                        error_code="IDEMPOTENCY_CONFLICT",
                    )
                replay_response = ComplaintRegisterResponse.model_validate(existing)
                replay_response.idempotent_replay = True
                replay_response.tracking_token = None  # Never re-expose tracking secret or PII on replay

        return target_org_id, target_org, effective_key, payload_hash, replay_response

    def register(
        self,
        db: Session,
        payload: ComplaintRegisterRequest,
        *,
        principal: Optional[Principal] = None,
        tenant_ctx: Optional[TenantContext] = None,
        idempotency_key: Optional[str] = None,
        source_ip: Optional[str] = None,
    ) -> ComplaintRegisterResponse:
        target_org_id, target_org, effective_key, payload_hash, replay = self._prepare_complaint_registration(
            db,
            payload,
            tenant_ctx=tenant_ctx,
            idempotency_key=idempotency_key,
            source_ip=source_ip,
            target_resource="/api/v1/complaints",
        )
        if replay is not None:
            return replay

        with UnitOfWork(db):
            complaint, raw_token = self._register_in_tx(
                db,
                payload,
                target_org_id=target_org_id,
                target_org=target_org,
                principal=principal,
                effective_idempotency_key=effective_key,
                payload_hash=payload_hash,
                source_ip=source_ip,
            )

        db.refresh(complaint)
        resp = ComplaintRegisterResponse.model_validate(complaint)
        resp.idempotent_replay = False
        resp.tracking_token = raw_token
        return resp

    def list_complaints(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        *,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        source_channel: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Complaint], int]:
        return self._repo.list_for_tenant(
            db,
            tenant_ctx,
            status=status,
            severity=severity,
            source_channel=source_channel,
            page=page,
            page_size=page_size,
        )

    def list_unrouted_complaints(
        self, db: Session, *, page: int = 1, page_size: int = 20
    ) -> Tuple[List[Complaint], int]:
        return self._repo.list_unrouted(db, page=page, page_size=page_size)

    def _get_complaint_for_tenant_or_404(
        self, db: Session, tenant_ctx: TenantContext, complaint_id: uuid.UUID
    ) -> Complaint:
        complaint = self._repo.get_for_org(db, tenant_ctx.organization_id, complaint_id)
        if complaint is None:
            raise NotFoundError("Complaint not found.")
        if (
            complaint.site_id is not None
            and not tenant_ctx.is_org_wide
            and complaint.site_id not in tenant_ctx.allowed_site_ids
        ):
            raise NotFoundError("Complaint not found.")
        return complaint

    def _build_public_status(self, db: Session, complaint: Complaint) -> ComplaintPublicStatusResponse:
        events = self._repo.list_public_events(db, complaint.id)
        comms = self._repo.list_public_communications(db, complaint.id)
        atts = [a for a in self._repo.list_attachments(db, complaint.id) if a.is_external_visible]
        return ComplaintPublicStatusResponse(
            id=complaint.id,
            complaint_number=complaint.complaint_number,
            status=complaint.status,
            source_channel=complaint.source_channel,
            reported_product_name=complaint.reported_product_name,
            reported_batch_number=complaint.reported_batch_number,
            defect_category=complaint.defect_category,
            acknowledged_at=complaint.acknowledged_at,
            due_at=complaint.due_at,
            closed_at=complaint.closed_at,
            created_at=complaint.created_at,
            events=[ComplaintEventResponse.model_validate(e) for e in events],
            communications=[ComplaintCommunicationResponse.model_validate(c) for c in comms],
            attachments=[ComplaintAttachmentResponse.model_validate(a) for a in atts],
        )

    def get_complaint_public_by_tracking_token(
        self,
        db: Session,
        complaint_id: uuid.UUID,
        raw_tracking_token: str,
        *,
        source_ip: Optional[str] = None,
    ) -> ComplaintPublicStatusResponse:
        complaint = self._repo.get_by_id(db, complaint_id)
        digest = hash_token(raw_tracking_token)
        if (
            complaint is None
            or not complaint.tracking_token_hash
            or not hmac.compare_digest(complaint.tracking_token_hash, digest)
        ):
            self._audit.record_security_event(
                event_type="COMPLAINT_TRACKING_TOKEN_INVALID",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.WARNING.value,
                user_id=None,
                source_ip=source_ip,
                target_resource=f"/api/v1/complaints/{complaint_id}",
            )
            raise AuthenticationError("Invalid complaint tracking token.")
        return self._build_public_status(db, complaint)

    def get_complaint_public_for_creator(
        self, db: Session, complaint_id: uuid.UUID, principal: Principal
    ) -> ComplaintPublicStatusResponse:
        complaint = self._repo.get_by_id(db, complaint_id)
        if complaint is None:
            raise NotFoundError("Complaint not found.")
        is_owner = complaint.created_by_user_id == principal.user_id
        if not is_owner and complaint.complainant_id is not None:
            comp = db.get(Complainant, complaint.complainant_id)
            is_owner = comp is not None and comp.user_id == principal.user_id
        if not is_owner:
            raise NotFoundError("Complaint not found.")
        return self._build_public_status(db, complaint)

    def get_complaint_detail(
        self, db: Session, tenant_ctx: TenantContext, complaint_id: uuid.UUID
    ) -> ComplaintDetailResponse:
        complaint = self._get_complaint_for_tenant_or_404(db, tenant_ctx, complaint_id)
        return ComplaintDetailResponse.model_validate(complaint)

    def update_complaint(
        self,
        db: Session,
        *,
        complaint_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: ComplaintUpdateRequest,
    ) -> Complaint:
        complaint = self._get_complaint_for_tenant_or_404(db, tenant_ctx, complaint_id)
        if complaint.status in {ComplaintStatus.CLOSED.value, ComplaintStatus.REJECTED.value}:
            raise ConflictError("Closed or rejected complaints cannot be modified.")

        if payload.site_id is not None:
            site = db.scalar(
                select(Site).where(
                    Site.organization_id == tenant_ctx.organization_id,
                    Site.id == payload.site_id,
                )
            )
            if site is None:
                raise NotFoundError("Site not found in this organization.")

        if payload.product_id is not None:
            product = db.scalar(
                select(Product).where(
                    Product.organization_id == tenant_ctx.organization_id,
                    Product.id == payload.product_id,
                )
            )
            if product is None:
                raise NotFoundError("Product not found in this organization.")

        with UnitOfWork(db):
            verify_and_increment_version(complaint, payload.row_version)
            for field in (
                "product_id",
                "reported_product_name",
                "reported_batch_number",
                "reported_manufacturer",
                "reported_strength",
                "reported_dosage_form",
                "reported_manufacturing_date",
                "reported_expiry_date",
                "defect_category",
                "description",
                "site_id",
            ):
                val = getattr(payload, field)
                if val is not None:
                    setattr(complaint, field, val)
            complaint.updated_by_user_id = tenant_ctx.user_id

            db.add(
                ComplaintEvent(
                    organization_id=complaint.organization_id,
                    complaint_id=complaint.id,
                    event_type="COMPLAINT_UPDATED",
                    from_status=complaint.status,
                    to_status=complaint.status,
                    actor_user_id=tenant_ctx.user_id,
                    actor_membership_id=tenant_ctx.membership_id,
                    summary=payload.reason or "Complaint details updated.",
                )
            )
            self._audit.record(
                db,
                organization_id=complaint.organization_id,
                site_id=complaint.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="COMPLAINT_UPDATED",
                entity_type="complaints",
                entity_id=complaint.id,
                reason=payload.reason,
            )
        db.refresh(complaint)
        return complaint

    def assign_complaint(
        self,
        db: Session,
        *,
        complaint_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: ComplaintAssignRequest,
    ) -> ComplaintAssignment:
        complaint = self._get_complaint_for_tenant_or_404(db, tenant_ctx, complaint_id)
        assignee = db.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == tenant_ctx.organization_id,
                OrganizationMembership.id == payload.assignee_membership_id,
                OrganizationMembership.membership_status == MembershipStatus.ACTIVE.value,
            )
        )
        if assignee is None:
            raise NotFoundError("Active assignee membership not found in this organization.")

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            if payload.row_version is not None:
                verify_and_increment_version(complaint, payload.row_version)
            for existing in complaint.assignments:
                if existing.is_active and existing.assignment_role == payload.assignment_role:
                    existing.is_active = False
                    existing.unassigned_at = now

            assignment = ComplaintAssignment(
                organization_id=complaint.organization_id,
                complaint_id=complaint.id,
                assignee_membership_id=assignee.id,
                assigned_by_user_id=tenant_ctx.user_id,  # type: ignore[arg-type]
                assignment_role=payload.assignment_role,
                reason=payload.reason,
                is_active=True,
                assigned_at=now,
                created_by_user_id=tenant_ctx.user_id,
            )
            db.add(assignment)
            db.flush()

            if payload.assignment_role == "OWNER":
                complaint.owner_membership_id = assignee.id
            if payload.due_at is not None:
                complaint.due_at = payload.due_at
            complaint.updated_by_user_id = tenant_ctx.user_id

            db.add(
                ComplaintEvent(
                    organization_id=complaint.organization_id,
                    complaint_id=complaint.id,
                    event_type="COMPLAINT_ASSIGNED",
                    from_status=complaint.status,
                    to_status=complaint.status,
                    actor_user_id=tenant_ctx.user_id,
                    actor_membership_id=tenant_ctx.membership_id,
                    summary=f"Assigned {payload.assignment_role} to membership {assignee.id}.",
                )
            )
            self._audit.record(
                db,
                organization_id=complaint.organization_id,
                site_id=complaint.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="COMPLAINT_ASSIGNED",
                entity_type="complaints",
                entity_id=complaint.id,
                reason=payload.reason,
                after_state={"assignee_membership_id": str(assignee.id), "role": payload.assignment_role},
            )
            self._notifications.create_in_tx(
                db,
                organization_id=tenant_ctx.organization_id,
                recipient_membership_id=assignee.id,
                event_type="ComplaintAssigned",
                aggregate_type="complaint",
                aggregate_id=complaint.id,
                title=f"Complaint Assigned: {complaint.complaint_number}",
                body=f"You were assigned as {payload.assignment_role} for {complaint.complaint_number}.",
            )
            self._outbox.enqueue(
                db,
                organization_id=complaint.organization_id,
                aggregate_type="complaint",
                aggregate_id=complaint.id,
                event_type="ComplaintAssigned",
                payload_json={
                    "complaint_id": str(complaint.id),
                    "organization_id": str(complaint.organization_id),
                    "assignee_membership_id": str(assignee.id),
                },
            )
        db.refresh(assignment)
        return assignment

    def triage_complaint(
        self,
        db: Session,
        *,
        complaint_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: ComplaintTriageRequest,
    ) -> Complaint:
        complaint = self._get_complaint_for_tenant_or_404(db, tenant_ctx, complaint_id)
        if complaint.status != payload.target_status:
            assert_transition(
                COMPLAINT_TRANSITIONS,
                complaint.status,
                payload.target_status,
                entity_name="Complaint",
            )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            verify_and_increment_version(complaint, payload.row_version)
            prev_status = complaint.status

            classification = ComplaintClassification(
                organization_id=complaint.organization_id,
                complaint_id=complaint.id,
                source_type=ClassificationSource.HUMAN_REVIEW.value,
                suggested_severity=payload.severity.value,
                suggested_priority=payload.priority.value,
                defect_category=payload.defect_category or complaint.defect_category,
                rationale=payload.rationale,
                decided_by_membership_id=tenant_ctx.membership_id,
                override_reason=payload.override_reason,
                created_by_user_id=tenant_ctx.user_id,
            )
            db.add(classification)

            complaint.final_severity = payload.severity.value
            complaint.final_priority = payload.priority.value
            if payload.defect_category:
                complaint.defect_category = payload.defect_category
            complaint.status = payload.target_status
            if complaint.acknowledged_at is None:
                complaint.acknowledged_at = now
            complaint.updated_by_user_id = tenant_ctx.user_id

            for clock in self._repo.get_active_sla_clocks(db, complaint.id):
                if (
                    payload.target_status == ComplaintStatus.WAITING_INFO.value
                    and clock.status in {SLAStatus.ON_TRACK.value, SLAStatus.AT_RISK.value}
                ):
                    clock.status = SLAStatus.PAUSED.value
                    clock.paused_at = now
                elif payload.target_status == ComplaintStatus.TRIAGED.value and clock.sla_stage == "TRIAGE":
                    clock.status = SLAStatus.MET.value
                    clock.completed_at = now

            db.add(
                ComplaintEvent(
                    organization_id=complaint.organization_id,
                    complaint_id=complaint.id,
                    event_type="COMPLAINT_TRIAGED",
                    from_status=prev_status,
                    to_status=complaint.status,
                    actor_user_id=tenant_ctx.user_id,
                    actor_membership_id=tenant_ctx.membership_id,
                    summary=(
                        f"Human triage completed: severity={complaint.final_severity}, "
                        f"priority={complaint.final_priority}."
                    ),
                )
            )
            self._audit.record(
                db,
                organization_id=complaint.organization_id,
                site_id=complaint.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="COMPLAINT_TRIAGED",
                entity_type="complaints",
                entity_id=complaint.id,
                reason=payload.override_reason or payload.rationale,
                before_state={"status": prev_status},
                after_state={
                    "status": complaint.status,
                    "final_severity": complaint.final_severity,
                    "final_priority": complaint.final_priority,
                },
            )
            self._outbox.enqueue(
                db,
                organization_id=complaint.organization_id,
                aggregate_type="complaint",
                aggregate_id=complaint.id,
                event_type="ComplaintTriaged",
                payload_json={
                    "complaint_id": str(complaint.id),
                    "organization_id": str(complaint.organization_id),
                    "final_severity": complaint.final_severity,
                },
            )
        db.refresh(complaint)
        return complaint

    def route_unrouted_complaint(
        self,
        db: Session,
        *,
        complaint_id: uuid.UUID,
        reviewer: Principal,
        payload: ComplaintRouteRequest,
    ) -> Complaint:
        complaint = self._repo.get_by_id(db, complaint_id)
        if complaint is None:
            raise NotFoundError("Complaint not found.")
        assert_transition(
            COMPLAINT_TRANSITIONS,
            complaint.status,
            ComplaintStatus.RECEIVED.value,
            entity_name="Complaint",
        )

        target_org = db.get(Organization, payload.organization_id)
        if target_org is None or target_org.status != OrganizationStatus.ACTIVE.value:
            raise NotFoundError("Target active organization not found.")

        if payload.site_id is not None:
            site = db.scalar(
                select(Site).where(
                    Site.organization_id == target_org.id,
                    Site.id == payload.site_id,
                )
            )
            if site is None:
                raise NotFoundError("Target site not found in target organization.")

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            verify_and_increment_version(complaint, payload.row_version)
            prev_status = complaint.status
            complaint.organization_id = target_org.id
            complaint.site_id = payload.site_id
            complaint.status = ComplaintStatus.RECEIVED.value
            complaint.updated_by_user_id = reviewer.user_id

            if complaint.complainant_id is not None:
                comp = db.get(Complainant, complaint.complainant_id)
                if comp is not None and comp.organization_id is None:
                    comp.organization_id = target_org.id

            # Backfill organization_id atomically across child tables & linked documents
            for child_group in (
                complaint.classifications,
                complaint.events,
                complaint.assignments,
                complaint.sla_clocks,
                complaint.communications,
                complaint.attachments,
            ):
                for row in child_group:
                    row.organization_id = target_org.id

            if complaint.attachments:
                try:
                    from app.services.document_control_service import DocumentControlService

                    doc_svc = DocumentControlService()
                    for att in complaint.attachments:
                        doc_svc.assign_organization_in_tx(db, att.document_id, target_org.id)
                except ImportError:
                    pass

            db.add(
                ComplaintEvent(
                    organization_id=target_org.id,
                    complaint_id=complaint.id,
                    event_type="COMPLAINT_ROUTED",
                    from_status=prev_status,
                    to_status=complaint.status,
                    actor_user_id=reviewer.user_id,
                    summary=f"Routed unrouted complaint to organization {target_org.legal_name}.",
                    occurred_at=now,
                )
            )
            self._audit.record(
                db,
                organization_id=target_org.id,
                site_id=payload.site_id,
                actor_type=ActorType.USER.value,
                actor_id=reviewer.user_id,
                action="COMPLAINT_ROUTED",
                entity_type="complaints",
                entity_id=complaint.id,
                reason=payload.reason,
                before_state={"status": prev_status, "organization_id": None},
                after_state={"status": complaint.status, "organization_id": str(target_org.id)},
            )
            self._outbox.enqueue(
                db,
                organization_id=target_org.id,
                aggregate_type="complaint",
                aggregate_id=complaint.id,
                event_type="ComplaintRouted",
                payload_json={
                    "complaint_id": str(complaint.id),
                    "organization_id": str(target_org.id),
                },
            )
        db.refresh(complaint)
        return complaint

    def resolve_batch_candidates(
        self,
        db: Session,
        *,
        complaint_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: BatchResolutionRequest,
    ) -> ComplaintBatchLink:
        complaint = self._get_complaint_for_tenant_or_404(db, tenant_ctx, complaint_id)
        if complaint.organization_id is None:
            raise ConflictError("Batch candidate resolution requires a routed tenant complaint.")

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            if payload.row_version is not None:
                verify_and_increment_version(complaint, payload.row_version)

            if payload.action == "VERIFY_CANDIDATE":
                if payload.candidate_link_id is None:
                    raise ValidationError("candidate_link_id is required for VERIFY_CANDIDATE.")
                link = self._repo.get_batch_link_for_org(
                    db, tenant_ctx.organization_id, complaint.id, payload.candidate_link_id
                )
                if link is None:
                    raise NotFoundError("Batch candidate link not found.")
                if link.batch_id is not None:
                    linked_batch = db.scalar(
                        select(Batch).where(
                            Batch.organization_id == tenant_ctx.organization_id,
                            Batch.id == link.batch_id,
                        )
                    )
                    if linked_batch is None:
                        raise NotFoundError("Linked batch not found in this organization.")
                link.match_status = BatchMatchStatus.VERIFIED.value
                link.verified_by_membership_id = tenant_ctx.membership_id
                link.verified_at = now
                if payload.notes:
                    link.match_evidence_json = {**(link.match_evidence_json or {}), "notes": payload.notes}
            elif payload.action == "MANUAL_LINK":
                if payload.batch_id is None and not payload.reported_batch_number:
                    raise ValidationError("Provide batch_id or reported_batch_number for MANUAL_LINK.")
                linked_batch: Optional[Batch] = None
                if payload.batch_id is not None:
                    linked_batch = db.scalar(
                        select(Batch).where(
                            Batch.organization_id == tenant_ctx.organization_id,
                            Batch.id == payload.batch_id,
                        )
                    )
                    if linked_batch is None:
                        raise NotFoundError("Batch not found in this organization.")
                resolved_batch_number = (
                    payload.reported_batch_number
                    or complaint.reported_batch_number
                    or (linked_batch.batch_number if linked_batch is not None else None)
                )
                link = ComplaintBatchLink(
                    organization_id=tenant_ctx.organization_id,
                    complaint_id=complaint.id,
                    batch_id=payload.batch_id,
                    reported_batch_number=resolved_batch_number,
                    resolution_outcome=BatchResolutionOutcome.MANUAL_LINKED.value,
                    match_method="MANUAL",
                    match_status=BatchMatchStatus.MANUAL_LINK.value,
                    verified_by_membership_id=tenant_ctx.membership_id,
                    verified_at=now,
                    match_evidence_json={"notes": payload.notes} if payload.notes else None,
                    created_by_user_id=tenant_ctx.user_id,
                )
                db.add(link)
                db.flush()
                if payload.reported_batch_number:
                    complaint.reported_batch_number = payload.reported_batch_number
            else:
                link = ComplaintBatchLink(
                    organization_id=tenant_ctx.organization_id,
                    complaint_id=complaint.id,
                    batch_id=None,
                    reported_batch_number=complaint.reported_batch_number,
                    resolution_outcome=(
                        BatchResolutionOutcome.INSUFFICIENT_INFO.value
                        if not complaint.reported_batch_number
                        else BatchResolutionOutcome.NOT_FOUND.value
                    ),
                    match_method="MANUAL_REVIEW",
                    match_status=BatchMatchStatus.VERIFIED.value,
                    verified_by_membership_id=tenant_ctx.membership_id,
                    verified_at=now,
                    match_evidence_json={"notes": payload.notes} if payload.notes else None,
                    created_by_user_id=tenant_ctx.user_id,
                )
                db.add(link)
                db.flush()

            db.add(
                ComplaintEvent(
                    organization_id=complaint.organization_id,
                    complaint_id=complaint.id,
                    event_type="BATCH_CANDIDATE_RESOLVED",
                    from_status=complaint.status,
                    to_status=complaint.status,
                    actor_user_id=tenant_ctx.user_id,
                    actor_membership_id=tenant_ctx.membership_id,
                    summary=f"Batch resolution action {payload.action} ({link.resolution_outcome}).",
                )
            )
            self._audit.record(
                db,
                organization_id=complaint.organization_id,
                site_id=complaint.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="COMPLAINT_BATCH_RESOLVED",
                entity_type="complaint_batch_links",
                entity_id=link.id,
                reason=payload.notes,
                after_state={"action": payload.action, "outcome": link.resolution_outcome},
            )
            self._outbox.enqueue(
                db,
                organization_id=complaint.organization_id,
                aggregate_type="complaint",
                aggregate_id=complaint.id,
                event_type="ComplaintBatchResolved",
                payload_json={
                    "complaint_id": str(complaint.id),
                    "organization_id": str(complaint.organization_id),
                    "batch_id": str(link.batch_id) if link.batch_id else None,
                    "resolution_outcome": link.resolution_outcome,
                },
            )

            # D2: Automatically resume any WAITING_FOR_HUMAN InvestigationRuns for this complaint
            from app.services.investigation_service import InvestigationService

            InvestigationService().resume_waiting_runs_for_complaint_in_tx(
                db,
                organization_id=tenant_ctx.organization_id,
                complaint_id=complaint.id,
                resolved_batch_id=link.batch_id,
                resolution_outcome=link.resolution_outcome,
                actor_user_id=tenant_ctx.user_id,
            )
        db.refresh(link)
        return link

    def close_complaint(
        self,
        db: Session,
        *,
        complaint_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: ComplaintCloseRequest,
        source_ip: Optional[str] = None,
    ) -> Complaint:
        """
        Guarded complaint closure (`D7`):
        - When any Investigation exists for this complaint, all investigations must be APPROVED/CLOSED
          and at least one GeneratedReport must be APPROVED.
        - Zero open mandatory Deviations may remain on the complaint or its investigations.
        """
        complaint = self._get_complaint_for_tenant_or_404(db, tenant_ctx, complaint_id)
        assert_transition(
            COMPLAINT_TRANSITIONS,
            complaint.status,
            ComplaintStatus.CLOSED.value,
            entity_name="Complaint",
        )
        if tenant_ctx.membership_id is None:
            raise PermissionDeniedError("Active organization membership required to close complaints.")

        from app.domain.enums import InvestigationStatus, ReportStatus
        from app.repositories.investigation_repo import InvestigationRepository
        from app.repositories.qms_repo import QMSRepository

        inv_repo = InvestigationRepository()
        qms_repo = QMSRepository()
        org_id = tenant_ctx.organization_id

        investigations = inv_repo.list_investigations_for_complaint(db, org_id, complaint.id)
        for inv in investigations:
            if inv.status not in {InvestigationStatus.APPROVED.value, InvestigationStatus.CLOSED.value}:
                raise ConflictError(
                    f"Cannot close complaint: investigation {inv.investigation_number} is '{inv.status}' (must be APPROVED).",
                    error_code="INVESTIGATION_NOT_APPROVED",
                    details={"investigation_id": str(inv.id), "status": inv.status},
                )
            reports = inv_repo.list_reports_for_investigation(db, org_id, inv.id)
            if not any(r.status == ReportStatus.APPROVED.value for r in reports):
                raise ConflictError(
                    f"Cannot close complaint: investigation {inv.investigation_number} has no APPROVED investigation report.",
                    error_code="REPORT_NOT_APPROVED",
                    details={"investigation_id": str(inv.id)},
                )

        open_mandatory_devs = qms_repo.list_open_mandatory_deviations_for_complaint(
            db, org_id, complaint.id
        )
        if open_mandatory_devs:
            raise ConflictError(
                "Cannot close complaint while mandatory deviations remain open.",
                error_code="OPEN_MANDATORY_DEVIATIONS_EXIST",
                details={
                    "open_deviation_numbers": [d.deviation_number for d in open_mandatory_devs],
                    "open_deviation_ids": [str(d.id) for d in open_mandatory_devs],
                },
            )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            verify_and_increment_version(complaint, payload.row_version)
            prev_status = complaint.status

            approval = ApprovalRecord(
                organization_id=org_id,
                entity_type="complaint",
                entity_id=complaint.id,
                approval_stage="COMPLAINT_CLOSURE",
                decision="APPROVED",
                approver_membership_id=tenant_ctx.membership_id,
                decided_at=now,
                comments=payload.closure_reason.strip(),
            )
            db.add(approval)
            db.flush()

            complaint.status = ComplaintStatus.CLOSED.value
            complaint.closed_at = now
            complaint.closure_reason = payload.closure_reason.strip()
            complaint.updated_by_user_id = tenant_ctx.user_id

            for inv in investigations:
                if inv.status == InvestigationStatus.APPROVED.value:
                    inv.status = InvestigationStatus.CLOSED.value
                    inv.closed_at = now
                    inv.closure_reason = payload.closure_reason.strip()
                    inv.row_version += 1
                    inv.updated_by_user_id = tenant_ctx.user_id

            for clock in self._repo.get_active_sla_clocks(db, complaint.id):
                clock.status = SLAStatus.MET.value
                clock.completed_at = now

            db.add(
                ComplaintEvent(
                    organization_id=org_id,
                    complaint_id=complaint.id,
                    event_type="COMPLAINT_CLOSED",
                    from_status=prev_status,
                    to_status=complaint.status,
                    actor_user_id=tenant_ctx.user_id,
                    actor_membership_id=tenant_ctx.membership_id,
                    summary=f"Complaint closed: {payload.closure_reason.strip()[:200]}",
                    details_json=(
                        {"resolution_notes": payload.resolution_notes}
                        if payload.resolution_notes
                        else None
                    ),
                    occurred_at=now,
                )
            )
            self._audit.record(
                db,
                organization_id=org_id,
                site_id=complaint.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="COMPLAINT_CLOSED",
                entity_type="complaints",
                entity_id=complaint.id,
                reason=payload.closure_reason.strip(),
                source_ip=source_ip,
                before_state={"status": prev_status},
                after_state={"status": complaint.status, "approval_record_id": str(approval.id)},
            )
            self._outbox.enqueue(
                db,
                organization_id=org_id,
                aggregate_type="complaint",
                aggregate_id=complaint.id,
                event_type="ComplaintClosed",
                payload_json={
                    "complaint_id": str(complaint.id),
                    "organization_id": str(org_id),
                    "complaint_number": complaint.complaint_number,
                },
            )
        db.refresh(complaint)
        return complaint

    def add_communication(
        self,
        db: Session,
        *,
        complaint_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: ComplaintCommunicationCreateRequest,
    ) -> ComplaintCommunication:
        complaint = self._get_complaint_for_tenant_or_404(db, tenant_ctx, complaint_id)
        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            comm = ComplaintCommunication(
                organization_id=complaint.organization_id,
                complaint_id=complaint.id,
                channel=payload.channel,
                direction=payload.direction,
                sender_user_id=tenant_ctx.user_id,
                sender_membership_id=tenant_ctx.membership_id,
                subject=payload.subject,
                body_text=payload.body_text.strip(),
                is_internal_only=payload.is_internal_only or payload.direction == CommunicationDirection.INTERNAL.value,
                is_information_request=False,
                sent_at=now,
                created_by_user_id=tenant_ctx.user_id,
            )
            db.add(comm)
            db.flush()
            self._audit.record(
                db,
                organization_id=complaint.organization_id,
                site_id=complaint.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="COMPLAINT_COMMUNICATION_ADDED",
                entity_type="complaint_communications",
                entity_id=comm.id,
            )
        db.refresh(comm)
        return comm

    def request_information(
        self,
        db: Session,
        *,
        complaint_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: InformationRequestCreateRequest,
    ) -> ComplaintCommunication:
        complaint = self._get_complaint_for_tenant_or_404(db, tenant_ctx, complaint_id)
        if payload.transition_to_waiting_info and complaint.status != ComplaintStatus.WAITING_INFO.value:
            assert_transition(
                COMPLAINT_TRANSITIONS,
                complaint.status,
                ComplaintStatus.WAITING_INFO.value,
                entity_name="Complaint",
            )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            if payload.row_version is not None:
                verify_and_increment_version(complaint, payload.row_version)
            comm = ComplaintCommunication(
                organization_id=complaint.organization_id,
                complaint_id=complaint.id,
                channel=payload.channel,
                direction="OUTBOUND",
                sender_user_id=tenant_ctx.user_id,
                sender_membership_id=tenant_ctx.membership_id,
                subject=payload.subject or f"Additional Information Requested: {complaint.complaint_number}",
                body_text=payload.body_text.strip(),
                is_internal_only=False,
                is_information_request=True,
                sent_at=now,
                created_by_user_id=tenant_ctx.user_id,
            )
            db.add(comm)
            db.flush()

            prev_status = complaint.status
            if payload.transition_to_waiting_info:
                complaint.status = ComplaintStatus.WAITING_INFO.value
                for clock in self._repo.get_active_sla_clocks(db, complaint.id):
                    if clock.status in {SLAStatus.ON_TRACK.value, SLAStatus.AT_RISK.value}:
                        clock.status = SLAStatus.PAUSED.value
                        clock.paused_at = now

            db.add(
                ComplaintEvent(
                    organization_id=complaint.organization_id,
                    complaint_id=complaint.id,
                    event_type="INFORMATION_REQUESTED",
                    from_status=prev_status,
                    to_status=complaint.status,
                    actor_user_id=tenant_ctx.user_id,
                    actor_membership_id=tenant_ctx.membership_id,
                    summary="Requested additional information from complainant.",
                )
            )
            self._audit.record(
                db,
                organization_id=complaint.organization_id,
                site_id=complaint.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="COMPLAINT_INFORMATION_REQUESTED",
                entity_type="complaints",
                entity_id=complaint.id,
            )
        db.refresh(comm)
        return comm

    def attach_document(
        self,
        db: Session,
        *,
        complaint_id: uuid.UUID,
        file_bytes: bytes,
        original_filename: str,
        claimed_mime_type: Optional[str] = None,
        caption: Optional[str] = None,
        is_external_visible: bool = True,
        tenant_ctx: Optional[TenantContext] = None,
        principal: Optional[Principal] = None,
        tracking_token: Optional[str] = None,
    ) -> ComplaintAttachment:
        if tenant_ctx is not None:
            complaint = self._get_complaint_for_tenant_or_404(db, tenant_ctx, complaint_id)
            uploader_user_id = tenant_ctx.user_id
        elif tracking_token:
            complaint = self._repo.get_by_id(db, complaint_id)
            digest = hash_token(tracking_token)
            if (
                complaint is None
                or not complaint.tracking_token_hash
                or not hmac.compare_digest(complaint.tracking_token_hash, digest)
            ):
                raise AuthenticationError("Invalid complaint tracking token.")
            uploader_user_id = principal.user_id if principal else None
        elif principal is not None:
            complaint = self._repo.get_by_id(db, complaint_id)
            if complaint is None or complaint.created_by_user_id != principal.user_id:
                raise NotFoundError("Complaint not found.")
            uploader_user_id = principal.user_id
        else:
            raise AuthenticationError("Authentication or valid tracking token is required.")

        from app.services.document_control_service import DocumentControlService

        doc_svc = DocumentControlService()
        with UnitOfWork(db):
            document = doc_svc.upload_in_tx(
                db,
                organization_id=complaint.organization_id,
                uploaded_by_user_id=uploader_user_id,
                file_bytes=file_bytes,
                original_filename=original_filename,
                claimed_mime_type=claimed_mime_type,
                category="COMPLAINT_EVIDENCE",
                site_id=complaint.site_id,
            )
            attachment = ComplaintAttachment(
                organization_id=complaint.organization_id,
                complaint_id=complaint.id,
                document_id=document.id,
                attachment_type="EVIDENCE",
                caption=caption,
                is_external_visible=is_external_visible,
                uploaded_by_user_id=uploader_user_id,
                created_by_user_id=uploader_user_id,
            )
            db.add(attachment)
            db.flush()
            db.add(
                ComplaintEvent(
                    organization_id=complaint.organization_id,
                    complaint_id=complaint.id,
                    event_type="ATTACHMENT_UPLOADED",
                    from_status=complaint.status,
                    to_status=complaint.status,
                    actor_user_id=uploader_user_id,
                    summary=f"Attached evidence file '{original_filename}'.",
                )
            )
            self._audit.record(
                db,
                organization_id=complaint.organization_id,
                site_id=complaint.site_id,
                actor_type=ActorType.USER.value if uploader_user_id else ActorType.ANONYMOUS.value,
                actor_id=uploader_user_id,
                action="COMPLAINT_ATTACHMENT_ADDED",
                entity_type="complaint_attachments",
                entity_id=attachment.id,
            )
        db.refresh(attachment)
        return attachment

    def run_async_triage(
        self,
        complaint_id: uuid.UUID,
        *,
        session_factory: Optional[sessionmaker[Session]] = None,
        llm_provider: Optional[LLMProvider] = None,
    ) -> Optional[ComplaintClassification]:
        """
        Asynchronous outbox-triggered AI triage.
        Reads DB snapshot -> closes session -> calls PIIRedactor + LLMProvider OUTSIDE DB transaction ->
        persists AI_SUGGESTION + ai_severity/ai_priority without overwriting human final_* fields.
        """
        factory = session_factory or get_session_factory()
        read_db = factory()
        try:
            complaint = self._repo.get_by_id(read_db, complaint_id)
            if complaint is None:
                return None
            known_pii: Dict[str, List[str]] = {"person": [], "email": [], "phone": []}
            if complaint.complainant_id is not None:
                comp = read_db.get(Complainant, complaint.complainant_id)
                if comp is not None:
                    if comp.full_name:
                        known_pii["person"].append(comp.full_name)
                    if comp.email:
                        known_pii["email"].append(comp.email)
                    if comp.phone_e164:
                        known_pii["phone"].append(comp.phone_e164)

            narrative = (
                f"Product: {complaint.reported_product_name or 'N/A'}\n"
                f"Dosage Form: {complaint.reported_dosage_form or 'N/A'}\n"
                f"Strength: {complaint.reported_strength or 'N/A'}\n"
                f"Batch: {complaint.reported_batch_number or 'Not provided'}\n"
                f"Defect Category Hint: {complaint.defect_category or 'Unknown'}\n"
                f"Description:\n{complaint.description}"
            )
        finally:
            read_db.close()

        provider = llm_provider or LLMProvider()
        ai_out = provider.generate_structured(
            system_prompt=AI_TRIAGE_SYSTEM_PROMPT,
            untrusted_text=narrative,
            response_model=AITriageOutput,
            known_pii=known_pii,
        )

        with UnitOfWork(factory) as uow:
            assert uow.session is not None
            db = uow.session
            complaint = self._repo.get_by_id(db, complaint_id)
            if complaint is None:
                return None

            classification = ComplaintClassification(
                organization_id=complaint.organization_id,
                complaint_id=complaint.id,
                source_type=ClassificationSource.AI_SUGGESTION.value,
                suggested_severity=ai_out.suggested_severity.value,
                suggested_priority=ai_out.suggested_priority.value,
                defect_category=ai_out.defect_category,
                model_name=provider._settings.LLM_MODEL_NAME,
                prompt_version="phase2-triage-v1",
                confidence_score=ai_out.confidence_score,
                rationale=ai_out.rationale,
                risk_indicators_json=ai_out.risk_indicators,
            )
            db.add(classification)
            db.flush()

            # AI triage NEVER overwrites human final_severity / final_priority
            complaint.ai_severity = ai_out.suggested_severity.value
            complaint.ai_priority = ai_out.suggested_priority.value
            if complaint.defect_category is None:
                complaint.defect_category = ai_out.defect_category

            self._audit.record(
                db,
                organization_id=complaint.organization_id,
                site_id=complaint.site_id,
                actor_type=ActorType.SYSTEM_WORKER.value,
                actor_id=None,
                action="COMPLAINT_AI_TRIAGED",
                entity_type="complaints",
                entity_id=complaint.id,
                after_state={
                    "ai_severity": complaint.ai_severity,
                    "ai_priority": complaint.ai_priority,
                    "defect_category": ai_out.defect_category,
                },
            )
            classification_id = classification.id

        verify_db = factory()
        try:
            return verify_db.get(ComplaintClassification, classification_id)
        finally:
            verify_db.close()

    def handle_outbox_async_triage(
        self,
        session_factory: sessionmaker[Session],
        event: Any,
        _tenant_ctx: Optional[TenantContext] = None,
    ) -> None:
        """ExternalOutboxHandler entrypoint executed outside open DB transactions by OutboxWorker."""
        self.run_async_triage(event.aggregate_id, session_factory=session_factory)

    # --- LangGraph Copilot Methods ---

    def _authorize_copilot_session(
        self,
        db: Session,
        session_obj: CopilotSession,
        *,
        principal: Optional[Principal] = None,
        session_token: Optional[str] = None,
        source_ip: Optional[str] = None,
    ) -> None:
        if session_obj.user_id is not None:
            if principal is None or principal.user_id != session_obj.user_id:
                raise PermissionDeniedError("Not authorized to access this copilot session.")
            return
        if session_obj.session_token_hash:
            if not session_token or not hmac.compare_digest(
                session_obj.session_token_hash, hash_token(session_token)
            ):
                self._audit.record_security_event(
                    event_type="COPILOT_SESSION_TOKEN_INVALID",
                    outcome=SecurityEventOutcome.DENIED.value,
                    severity=SecurityEventSeverity.WARNING.value,
                    source_ip=source_ip,
                    target_resource=f"/api/v1/complaints/copilot/sessions/{session_obj.id}",
                )
                raise AuthenticationError("Valid X-Copilot-Session-Token is required.")

    def _build_copilot_response(
        self,
        db: Session,
        session_obj: CopilotSession,
        *,
        session_token: Optional[str] = None,
    ) -> CopilotSessionResponse:
        messages = self._repo.list_copilot_messages(db, session_obj.id)
        draft = dict(session_obj.draft_state_json or {})
        is_complete, missing_req, missing_adv = compute_two_tier_completeness(
            draft, has_authenticated_reporter=session_obj.user_id is not None
        )
        return CopilotSessionResponse(
            id=session_obj.id,
            organization_id=session_obj.organization_id,
            user_id=session_obj.user_id,
            session_status=session_obj.session_status,
            draft_state_json=draft,
            converted_complaint_id=session_obj.converted_complaint_id,
            created_at=session_obj.created_at,
            updated_at=session_obj.updated_at,
            row_version=session_obj.row_version,
            is_complete=is_complete,
            missing_mandatory_fields=missing_req,
            missing_advisory_fields=missing_adv,
            messages=[CopilotMessageResponse.model_validate(m) for m in messages],
            session_token=session_token,
        )

    def create_copilot_session(
        self,
        db: Session,
        payload: CopilotCreateSessionRequest,
        *,
        principal: Optional[Principal] = None,
        tenant_ctx: Optional[TenantContext] = None,
        llm_provider: Optional[LLMProvider] = None,
        source_ip: Optional[str] = None,
    ) -> CopilotSessionResponse:
        org_id = tenant_ctx.organization_id if tenant_ctx else payload.organization_id
        raw_session_token: Optional[str] = None
        token_hash: Optional[str] = None
        if principal is None:
            raw_session_token = secrets.token_urlsafe(32)
            token_hash = hash_token(raw_session_token)

        draft_init: Dict[str, Any] = {}
        if principal is not None:
            draft_init["reporter_name"] = principal.full_name
            draft_init["reporter_email"] = principal.email

        with UnitOfWork(db):
            session_obj = CopilotSession(
                organization_id=org_id,
                user_id=principal.user_id if principal else None,
                session_token_hash=token_hash,
                draft_state_json=draft_init,
                session_status=CopilotSessionStatus.ACTIVE_DRAFT.value,
                created_by_user_id=principal.user_id if principal else None,
                updated_by_user_id=principal.user_id if principal else None,
            )
            db.add(session_obj)
            db.flush()
            session_id = session_obj.id

        if payload.initial_message:
            resp = self.send_copilot_message(
                db,
                session_id=session_id,
                user_message=payload.initial_message,
                principal=principal,
                session_token=raw_session_token,
                llm_provider=llm_provider,
                source_ip=source_ip,
            )
            resp.session_token = raw_session_token
            return resp

        db.refresh(session_obj)
        return self._build_copilot_response(db, session_obj, session_token=raw_session_token)

    def get_copilot_session(
        self,
        db: Session,
        *,
        session_id: uuid.UUID,
        principal: Optional[Principal] = None,
        session_token: Optional[str] = None,
        source_ip: Optional[str] = None,
    ) -> CopilotSessionResponse:
        session_obj = self._repo.get_copilot_session(db, session_id)
        if session_obj is None:
            raise NotFoundError("Copilot session not found.")
        self._authorize_copilot_session(
            db, session_obj, principal=principal, session_token=session_token, source_ip=source_ip
        )
        return self._build_copilot_response(db, session_obj)

    def send_copilot_message(
        self,
        db: Session,
        *,
        session_id: uuid.UUID,
        user_message: str,
        principal: Optional[Principal] = None,
        session_token: Optional[str] = None,
        llm_provider: Optional[LLMProvider] = None,
        source_ip: Optional[str] = None,
    ) -> CopilotSessionResponse:
        session_obj = self._repo.get_copilot_session(db, session_id)
        if session_obj is None:
            raise NotFoundError("Copilot session not found.")
        self._authorize_copilot_session(
            db, session_obj, principal=principal, session_token=session_token, source_ip=source_ip
        )
        if session_obj.session_status != CopilotSessionStatus.ACTIVE_DRAFT.value:
            raise ConflictError("Copilot session is already submitted or closed.")

        # Snapshot draft & close/release transaction before invoking external LLM graph
        draft_snapshot = dict(session_obj.draft_state_json or {})
        known_pii: Dict[str, List[str]] = {
            "person": [v for v in (draft_snapshot.get("reporter_name"), principal.full_name if principal else None) if v],
            "email": [v for v in (draft_snapshot.get("reporter_email"), principal.email if principal else None) if v],
            "phone": [v for v in (draft_snapshot.get("reporter_phone"),) if v],
        }
        db.rollback()

        graph = build_copilot_intake_graph(llm_provider=llm_provider)
        result = graph.invoke(
            {
                "user_message": user_message,
                "draft_state": draft_snapshot,
                "authenticated_email": principal.email if principal else None,
                "authenticated_name": principal.full_name if principal else None,
                "known_pii": known_pii,
            }
        )

        with UnitOfWork(db):
            session_obj = self._repo.get_copilot_session(db, session_id)
            if session_obj is None or session_obj.session_status != CopilotSessionStatus.ACTIVE_DRAFT.value:
                raise ConflictError("Copilot session state changed concurrently.")

            db.add(
                CopilotMessage(
                    session_id=session_obj.id,
                    role="USER",
                    message_text=user_message.strip(),
                    intent_type=result.get("intent"),
                )
            )
            db.add(
                CopilotMessage(
                    session_id=session_obj.id,
                    role="ASSISTANT",
                    message_text=str(result.get("assistant_reply") or ""),
                    intent_type=result.get("intent"),
                    extracted_fields_json=result.get("extracted_fields") or None,
                    confidence_score=result.get("confidence_score"),
                )
            )
            session_obj.draft_state_json = dict(result.get("draft_state") or draft_snapshot)
            session_obj.row_version += 1
            session_obj.updated_by_user_id = principal.user_id if principal else None

        db.refresh(session_obj)
        return self._build_copilot_response(db, session_obj)

    def submit_copilot_session(
        self,
        db: Session,
        *,
        session_id: uuid.UUID,
        payload: CopilotSubmitRequest,
        principal: Optional[Principal] = None,
        tenant_ctx: Optional[TenantContext] = None,
        session_token: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        source_ip: Optional[str] = None,
    ) -> ComplaintRegisterResponse:
        session_obj = self._repo.get_copilot_session(db, session_id)
        if session_obj is None:
            raise NotFoundError("Copilot session not found.")
        self._authorize_copilot_session(
            db, session_obj, principal=principal, session_token=session_token, source_ip=source_ip
        )
        if session_obj.session_status != CopilotSessionStatus.ACTIVE_DRAFT.value:
            raise ConflictError("Copilot session has already been submitted.")

        draft = dict(session_obj.draft_state_json or {})
        is_complete, missing_req, _ = compute_two_tier_completeness(
            draft, has_authenticated_reporter=principal is not None
        )
        if not is_complete:
            raise ValidationError(
                f"Mandatory complaint intake fields missing: {', '.join(missing_req)}",
                error_code="COPILOT_INCOMPLETE_DRAFT",
                details={"missing_mandatory_fields": missing_req},
            )

        reporter_input: Optional[ComplainantInput] = None
        if draft.get("reporter_name") or draft.get("reporter_email") or draft.get("reporter_phone"):
            reporter_input = ComplainantInput(
                full_name=(draft.get("reporter_name") or (principal.full_name if principal else "Reporter")),
                email=draft.get("reporter_email") or (principal.email if principal else None),
                phone_e164=draft.get("reporter_phone"),
            )

        reg_payload = ComplaintRegisterRequest(
            organization_id=payload.organization_id if payload.organization_id is not None else session_obj.organization_id,
            site_id=payload.site_id,
            source_channel=payload.source_channel,
            source_record_id=session_obj.id,
            idempotency_key=payload.idempotency_key,
            complainant=reporter_input,
            reported_product_name=draft.get("reported_product_name"),
            reported_batch_number=draft.get("reported_batch_number"),
            reported_manufacturer=draft.get("reported_manufacturer"),
            reported_strength=draft.get("reported_strength"),
            reported_dosage_form=draft.get("reported_dosage_form"),
            description=str(draft.get("description")),
            defect_category=draft.get("defect_category"),
            purchase_location=draft.get("purchase_location"),
        )
        target_org_id, target_org, effective_key, payload_hash, replay = self._prepare_complaint_registration(
            db,
            reg_payload,
            tenant_ctx=tenant_ctx,
            idempotency_key=idempotency_key,
            source_ip=source_ip,
            target_resource=f"/api/v1/complaints/copilot/sessions/{session_id}/submit",
        )
        if replay is not None:
            return replay

        with UnitOfWork(db):
            complaint, raw_token = self._register_in_tx(
                db,
                reg_payload,
                target_org_id=target_org_id,
                target_org=target_org,
                principal=principal,
                effective_idempotency_key=effective_key,
                payload_hash=payload_hash,
                source_ip=source_ip,
            )
            session_obj.session_status = CopilotSessionStatus.SUBMITTED.value
            session_obj.converted_complaint_id = complaint.id
            session_obj.row_version += 1

        db.refresh(complaint)
        resp = ComplaintRegisterResponse.model_validate(complaint)
        resp.tracking_token = raw_token
        return resp
