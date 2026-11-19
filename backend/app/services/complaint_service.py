import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
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
    ExternalProviderError,
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
    CommunicationChannel,
    CommunicationDeliveryStatus,
    CommunicationDirection,
    CommunicationType,
    ComplaintSource,
    ComplaintStatus,
    CopilotSessionStatus,
    EmailProviderType,
    InboundEmailClassification,
    InboundEmailTriageStatus,
    MembershipStatus,
    OrganizationStatus,
    SLAStatus,
    SecurityEventOutcome,
    SecurityEventSeverity,
)
from app.domain.state_machines import assert_transition
from app.integrations.email_provider import (
    EmailProvider,
    FetchedInboundEmail,
    OutboundEmailMessage,
    generate_webhook_secret,
    hash_webhook_secret,
    parse_dict_inbound_message,
    sanitize_email_html,
    verify_webhook_secret,
)
from app.integrations.llm_provider import LLMProvider
from app.integrations.storage_provider import (
    LocalPrivateVaultAdapter,
    ObjectStorageProvider,
    build_vault_object_key,
)
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
    EmailAttachment,
    EmailConnection,
    EmailProcessingAttempt,
    InboundEmail,
    SLAClock,
)
from app.models.domain.governance import ApprovalRecord
from app.models.domain.identity import OrganizationMembership
from app.models.domain.products_materials import Product
from app.models.domain.tenancy import Organization, Site
from app.models.domain.traceability import Batch
from app.models.schemas.complaints import (
    AITriageOutput,
    AcknowledgementCreateRequest,
    BatchResolutionRequest,
    ClosureMessageCreateRequest,
    ComplainantInput,
    CommunicationReplyRequest,
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
    EmailConnectionCreateRequest,
    EmailConnectionSyncResponse,
    EmailConnectionUpdateRequest,
    InboundEmailClassificationLLMOutput,
    InboundEmailTriageRequest,
    InboundEmailWebhookAckResponse,
    InboundEmailWebhookPayload,
    InformationRequestCreateRequest,
    StatusUpdateCreateRequest,
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

EMAIL_CLASSIFICATION_SYSTEM_PROMPT = (
    "You are a GMP Pharmaceutical Quality Assurance intake triage classifier. "
    "Classify the redacted inbound email inside <untrusted_input> and return valid JSON matching "
    "InboundEmailClassificationLLMOutput: "
    "{classification: LIKELY_COMPLAINT|NON_COMPLAINT|UNCERTAIN, confidence: 0..1, "
    "rationale: string, extracted_product_name: string|null, extracted_batch_number: string|null, "
    "defect_category: string|null}."
)

COMMUNICATION_TEMPLATES: Dict[str, Dict[str, str]] = {
    "ACK_STANDARD": {
        "communication_type": CommunicationType.ACKNOWLEDGEMENT.value,
        "subject": "Complaint Acknowledgement: {complaint_number}",
        "body": (
            "Dear {recipient_name},\n\n"
            "We acknowledge receipt of your quality complaint {complaint_number} "
            "regarding {product_name}. Our Quality Assurance team is reviewing the details."
        ),
    },
    "INFO_REQUEST_BATCH": {
        "communication_type": CommunicationType.INFO_REQUEST.value,
        "subject": "Additional Batch Information Requested: {complaint_number}",
        "body": (
            "Dear {recipient_name},\n\n"
            "To investigate complaint {complaint_number} ({product_name}), please provide the "
            "batch/lot number, expiry date, and any supporting photographs of the packaging."
        ),
    },
    "INFO_REQUEST_GENERAL": {
        "communication_type": CommunicationType.INFO_REQUEST.value,
        "subject": "Additional Information Requested: {complaint_number}",
        "body": (
            "Dear {recipient_name},\n\n"
            "We require additional information to progress investigation of complaint {complaint_number}: "
            "{details}"
        ),
    },
    "STATUS_UPDATE_STANDARD": {
        "communication_type": CommunicationType.STATUS_UPDATE.value,
        "subject": "Status Update for Complaint {complaint_number}",
        "body": (
            "Dear {recipient_name},\n\n"
            "Your complaint {complaint_number} ({product_name}) is currently in status '{status}'."
        ),
    },
    "CLOSURE_STANDARD": {
        "communication_type": CommunicationType.CLOSURE_NOTIFICATION.value,
        "subject": "Complaint Closure Notification: {complaint_number}",
        "body": (
            "Dear {recipient_name},\n\n"
            "The investigation and quality review for complaint {complaint_number} ({product_name}) "
            "have been completed. Summary: {closure_summary}"
        ),
    },
}

_COMM_TYPE_ALIASES: Dict[str, str] = {
    "CLOSURE": CommunicationType.CLOSURE_NOTIFICATION.value,
    "INBOUND_REPLY": CommunicationType.REPORTER_REPLY.value,
}

_COMPLAINT_KEYWORDS = (
    "complaint",
    "defect",
    "broken",
    "cracked",
    "discolor",
    "discolour",
    "contaminated",
    "contamination",
    "foreign particle",
    "tablet",
    "capsule",
    "blister",
    "vial",
    "batch",
    "lot",
    "expired",
    "leak",
    "leaking",
    "damaged",
    "wrong label",
    "mislabel",
    "adverse",
    "potency",
    "seal",
    "odor",
    "odour",
)

_NON_COMPLAINT_KEYWORDS = (
    "unsubscribe",
    "newsletter",
    "invoice",
    "billing statement",
    "promotional offer",
    "marketing",
    "out of office",
    "automatic reply",
    "auto-reply",
    "webinar",
)


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _org_complaint_prefix(org: Optional[Organization]) -> str:
    if org is None:
        return "PLT"
    slug = re.sub(r"[^A-Z0-9]", "", (org.legal_name or "").upper())[:6] or "ORG"
    return f"{slug}-CMP"


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _normalize_communication_type(raw_type: Optional[str], *, default: str) -> str:
    if not raw_type:
        return default
    upper = raw_type.strip().upper()
    mapped = _COMM_TYPE_ALIASES.get(upper, upper)
    try:
        return CommunicationType(mapped).value
    except ValueError as exc:
        raise ValidationError(f"Invalid communication_type '{raw_type}'.") from exc


def _render_communication_content(
    *,
    template_code: Optional[str],
    template_variables: Optional[Dict[str, Any]],
    subject: Optional[str],
    body_text: Optional[str],
    complaint: Complaint,
    recipient_name: Optional[str],
    default_subject: Optional[str] = None,
) -> Tuple[Optional[str], str, Optional[str]]:
    resolved_template = template_code.strip().upper() if template_code else None
    rendered_subject = subject
    rendered_body = body_text.strip() if body_text else None

    if resolved_template:
        tpl = COMMUNICATION_TEMPLATES.get(resolved_template)
        if tpl is None:
            raise ValidationError(
                f"Unknown communication template '{template_code}'.",
                error_code="UNKNOWN_TEMPLATE_CODE",
            )
        ctx_vars = _SafeFormatDict(
            {
                "complaint_number": complaint.complaint_number,
                "product_name": complaint.reported_product_name or "the reported product",
                "batch_number": complaint.reported_batch_number or "N/A",
                "status": complaint.status,
                "recipient_name": recipient_name or "Reporter",
                "closure_summary": complaint.closure_reason or "Completed quality review",
                "details": body_text or "",
            }
        )
        if template_variables:
            for k, v in template_variables.items():
                ctx_vars[str(k)] = "" if v is None else str(v)
        if not rendered_subject:
            rendered_subject = tpl["subject"].format_map(ctx_vars)[:200]
        if not rendered_body:
            rendered_body = tpl["body"].format_map(ctx_vars).strip()

    if not rendered_subject and default_subject:
        rendered_subject = default_subject[:200]
    if not rendered_body:
        raise ValidationError("body_text or a valid template_code is required.")
    return rendered_subject, rendered_body, resolved_template


def _classify_inbound_email_deterministic(
    subject: Optional[str], body_text: Optional[str]
) -> InboundEmailClassificationLLMOutput:
    combined = f"{subject or ''}\n{body_text or ''}".lower()
    comp_hits = [kw for kw in _COMPLAINT_KEYWORDS if kw in combined]
    non_hits = [kw for kw in _NON_COMPLAINT_KEYWORDS if kw in combined]

    batch_match = re.search(
        r"(?:batch|lot)\s*(?:no\.?|number|#|:)?\s*([A-Za-z0-9][A-Za-z0-9\-/]{2,30})",
        f"{subject or ''}\n{body_text or ''}",
        flags=re.IGNORECASE,
    )
    extracted_batch = batch_match.group(1).strip() if batch_match else None

    if len(comp_hits) >= 2 and not non_hits:
        return InboundEmailClassificationLLMOutput(
            classification=InboundEmailClassification.LIKELY_COMPLAINT.value,
            confidence=Decimal("0.8800"),
            rationale=f"Deterministic quality keywords matched: {', '.join(comp_hits[:5])}.",
            extracted_batch_number=extracted_batch,
        )
    if non_hits and not comp_hits:
        return InboundEmailClassificationLLMOutput(
            classification=InboundEmailClassification.NON_COMPLAINT.value,
            confidence=Decimal("0.9000"),
            rationale=f"Non-complaint indicators matched: {', '.join(non_hits[:4])}.",
        )
    return InboundEmailClassificationLLMOutput(
        classification=InboundEmailClassification.UNCERTAIN.value,
        confidence=Decimal("0.5000"),
        rationale="Insufficient or mixed complaint indicators; requires manual QA triage.",
        extracted_batch_number=extracted_batch,
    )


class ComplaintService:
    """Canonical Complaint Intake, Triage, Routing, Batch Resolution, Communications, Attachments & Copilot."""

    def __init__(
        self,
        repo: Optional[ComplaintRepository] = None,
        audit_service: Optional[AuditService] = None,
        outbox_repo: Optional[OutboxRepository] = None,
        notification_service: Optional[NotificationService] = None,
        email_provider: Optional[EmailProvider] = None,
        storage: Optional[ObjectStorageProvider] = None,
    ) -> None:
        self._repo = repo or ComplaintRepository()
        self._audit = audit_service or AuditService()
        self._outbox = outbox_repo or OutboxRepository()
        self._notifications = notification_service or NotificationService()
        self._email_provider = email_provider
        self._storage = storage

    def _get_email_provider(self) -> EmailProvider:
        if self._email_provider is None:
            self._email_provider = EmailProvider()
        return self._email_provider

    def _get_storage(self) -> ObjectStorageProvider:
        if self._storage is None:
            self._storage = LocalPrivateVaultAdapter()
        return self._storage

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
        actor_type: Optional[str] = None,
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
        resolved_actor_type = actor_type or (ActorType.USER.value if principal else ActorType.ANONYMOUS.value)
        self._audit.record(
            db,
            organization_id=target_org_id,
            site_id=payload.site_id,
            actor_type=resolved_actor_type,
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

        if hasattr(qms_repo, "list_open_mandatory_capas_for_complaint"):
            open_mandatory_capas = qms_repo.list_open_mandatory_capas_for_complaint(db, org_id, complaint.id)
            if open_mandatory_capas:
                raise ConflictError(
                    "Cannot close complaint while mandatory CAPAs remain open.",
                    error_code="OPEN_MANDATORY_CAPAS_EXIST",
                    details={
                        "open_capa_numbers": [c.capa_number for c in open_mandatory_capas],
                        "open_capa_ids": [str(c.id) for c in open_mandatory_capas],
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

    # --- B6 Complaint Communications & WAITING_INFO Loop ---

    def _resolve_recipient(
        self,
        db: Session,
        complaint: Complaint,
        *,
        recipient_email: Optional[str],
        recipient_name: Optional[str],
    ) -> Tuple[Optional[str], Optional[str]]:
        email_out = str(recipient_email).strip().lower() if recipient_email else None
        name_out = recipient_name.strip() if recipient_name else None
        if (not email_out or not name_out) and complaint.complainant_id is not None:
            comp = db.get(Complainant, complaint.complainant_id)
            if comp is not None:
                if not email_out and comp.email:
                    email_out = comp.email.strip().lower()
                if not name_out and comp.full_name:
                    name_out = comp.full_name.strip()
        return email_out, name_out

    def _create_outbound_communication_in_tx(
        self,
        db: Session,
        *,
        complaint: Complaint,
        tenant_ctx: TenantContext,
        channel: str,
        direction: str,
        communication_type: str,
        subject: Optional[str],
        body_text: str,
        template_code: Optional[str] = None,
        recipient_email: Optional[str] = None,
        recipient_name: Optional[str] = None,
        is_internal_only: bool = False,
        is_information_request: bool = False,
        in_reply_to_communication_id: Optional[uuid.UUID] = None,
        metadata_json: Optional[Dict[str, Any]] = None,
        now: datetime,
    ) -> ComplaintCommunication:
        normalized_channel = (channel or CommunicationChannel.PORTAL.value).strip().upper()
        should_enqueue_email = (
            normalized_channel == CommunicationChannel.EMAIL.value
            and direction == CommunicationDirection.OUTBOUND.value
            and not is_internal_only
        )
        if should_enqueue_email and not recipient_email:
            raise ValidationError(
                "recipient_email is required for outbound EMAIL communications.",
                error_code="RECIPIENT_EMAIL_REQUIRED",
            )
        delivery_status = (
            CommunicationDeliveryStatus.QUEUED.value
            if should_enqueue_email
            else CommunicationDeliveryStatus.NOT_APPLICABLE.value
        )

        comm = ComplaintCommunication(
            organization_id=complaint.organization_id,
            complaint_id=complaint.id,
            channel=normalized_channel,
            direction=direction,
            communication_type=communication_type,
            template_code=template_code,
            recipient_email=recipient_email,
            recipient_name=recipient_name,
            sender_user_id=tenant_ctx.user_id,
            sender_membership_id=tenant_ctx.membership_id,
            subject=subject,
            body_text=body_text,
            is_internal_only=is_internal_only,
            is_information_request=is_information_request,
            delivery_status=delivery_status,
            attempt_count=0,
            in_reply_to_communication_id=in_reply_to_communication_id,
            metadata_json=metadata_json,
            sent_at=now,
            created_by_user_id=tenant_ctx.user_id,
        )
        db.add(comm)
        db.flush()

        if should_enqueue_email:
            # Never put recipient email/name PII into outbox_events
            self._outbox.enqueue(
                db,
                organization_id=complaint.organization_id,
                aggregate_type="complaint_communication",
                aggregate_id=comm.id,
                event_type="OutboundCommunicationRequested",
                payload_json={
                    "communication_id": str(comm.id),
                    "complaint_id": str(complaint.id),
                    "organization_id": str(complaint.organization_id),
                    "communication_type": communication_type,
                },
            )
        return comm

    def _unpause_sla_clocks_in_tx(
        self,
        db: Session,
        complaint_id: uuid.UUID,
        *,
        now: datetime,
    ) -> int:
        unpaused = 0
        for clock in self._repo.get_active_sla_clocks(db, complaint_id):
            if clock.status == SLAStatus.PAUSED.value:
                pause_seconds = 0
                if clock.paused_at is not None:
                    pause_seconds = max(0, int((now - _as_utc(clock.paused_at)).total_seconds()))
                clock.accumulated_pause_seconds += pause_seconds
                clock.due_at = _as_utc(clock.due_at) + timedelta(seconds=pause_seconds)
                clock.paused_at = None
                clock.status = SLAStatus.ON_TRACK.value
                unpaused += 1
        return unpaused

    def list_communications(
        self,
        db: Session,
        *,
        complaint_id: uuid.UUID,
        tenant_ctx: TenantContext,
        include_internal: bool = True,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[ComplaintCommunication], int]:
        complaint = self._get_complaint_for_tenant_or_404(db, tenant_ctx, complaint_id)
        return self._repo.list_communications(
            db,
            complaint.organization_id,  # type: ignore[arg-type]
            complaint.id,
            include_internal=include_internal,
            page=page,
            page_size=page_size,
        )

    def add_communication(
        self,
        db: Session,
        *,
        complaint_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: ComplaintCommunicationCreateRequest,
    ) -> ComplaintCommunication:
        complaint = self._get_complaint_for_tenant_or_404(db, tenant_ctx, complaint_id)
        is_internal = payload.is_internal_only or payload.direction == CommunicationDirection.INTERNAL.value
        default_comm_type = (
            CommunicationType.INTERNAL_NOTE.value if is_internal else CommunicationType.GENERAL.value
        )
        comm_type = _normalize_communication_type(payload.communication_type, default=default_comm_type)

        if payload.in_reply_to_communication_id is not None:
            parent_comm = self._repo.get_communication_for_org(
                db,
                complaint.organization_id,  # type: ignore[arg-type]
                complaint.id,
                payload.in_reply_to_communication_id,
            )
            if parent_comm is None:
                raise NotFoundError("Referenced parent communication not found on this complaint.")

        rec_email, rec_name = self._resolve_recipient(
            db,
            complaint,
            recipient_email=str(payload.recipient_email) if payload.recipient_email else None,
            recipient_name=payload.recipient_name,
        )
        rendered_subject, rendered_body, resolved_template = _render_communication_content(
            template_code=payload.template_code,
            template_variables=payload.template_variables,
            subject=payload.subject,
            body_text=payload.body_text,
            complaint=complaint,
            recipient_name=rec_name,
        )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            comm = self._create_outbound_communication_in_tx(
                db,
                complaint=complaint,
                tenant_ctx=tenant_ctx,
                channel=payload.channel,
                direction=payload.direction,
                communication_type=comm_type,
                subject=rendered_subject,
                body_text=rendered_body,
                template_code=resolved_template,
                recipient_email=rec_email if not is_internal else None,
                recipient_name=rec_name if not is_internal else None,
                is_internal_only=is_internal,
                is_information_request=(comm_type == CommunicationType.INFO_REQUEST.value),
                in_reply_to_communication_id=payload.in_reply_to_communication_id,
                metadata_json=payload.metadata_json,
                now=now,
            )
            self._audit.record(
                db,
                organization_id=complaint.organization_id,
                site_id=complaint.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="COMPLAINT_COMMUNICATION_ADDED",
                entity_type="complaint_communications",
                entity_id=comm.id,
                after_state={"communication_type": comm.communication_type, "channel": comm.channel},
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

        rec_email, rec_name = self._resolve_recipient(
            db,
            complaint,
            recipient_email=str(payload.recipient_email) if payload.recipient_email else None,
            recipient_name=payload.recipient_name,
        )
        rendered_subject, rendered_body, resolved_template = _render_communication_content(
            template_code=payload.template_code,
            template_variables=payload.template_variables,
            subject=payload.subject,
            body_text=payload.body_text,
            complaint=complaint,
            recipient_name=rec_name,
            default_subject=f"Additional Information Requested: {complaint.complaint_number}",
        )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            if payload.row_version is not None:
                verify_and_increment_version(complaint, payload.row_version)
            comm = self._create_outbound_communication_in_tx(
                db,
                complaint=complaint,
                tenant_ctx=tenant_ctx,
                channel=payload.channel,
                direction=CommunicationDirection.OUTBOUND.value,
                communication_type=CommunicationType.INFO_REQUEST.value,
                subject=rendered_subject,
                body_text=rendered_body,
                template_code=resolved_template,
                recipient_email=rec_email,
                recipient_name=rec_name,
                is_internal_only=False,
                is_information_request=True,
                metadata_json=payload.metadata_json,
                now=now,
            )

            prev_status = complaint.status
            if payload.transition_to_waiting_info:
                complaint.status = ComplaintStatus.WAITING_INFO.value
                complaint.updated_by_user_id = tenant_ctx.user_id
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
                    details_json={"communication_id": str(comm.id), "channel": comm.channel},
                    occurred_at=now,
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
                before_state={"status": prev_status},
                after_state={"status": complaint.status, "communication_id": str(comm.id)},
            )
            self._outbox.enqueue(
                db,
                organization_id=complaint.organization_id,
                aggregate_type="complaint",
                aggregate_id=complaint.id,
                event_type="ComplaintInformationRequested",
                payload_json={
                    "complaint_id": str(complaint.id),
                    "organization_id": str(complaint.organization_id),
                    "communication_id": str(comm.id),
                    "status": complaint.status,
                },
            )
        db.refresh(comm)
        return comm

    def send_acknowledgement(
        self,
        db: Session,
        *,
        complaint_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: AcknowledgementCreateRequest,
    ) -> ComplaintCommunication:
        complaint = self._get_complaint_for_tenant_or_404(db, tenant_ctx, complaint_id)
        if complaint.status in {ComplaintStatus.UNROUTED_TRIAGE.value, ComplaintStatus.REJECTED.value}:
            raise ConflictError("Cannot send acknowledgement for an unrouted or rejected complaint.")

        rec_email, rec_name = self._resolve_recipient(
            db,
            complaint,
            recipient_email=str(payload.recipient_email) if payload.recipient_email else None,
            recipient_name=payload.recipient_name,
        )
        rendered_subject, rendered_body, resolved_template = _render_communication_content(
            template_code=payload.template_code,
            template_variables=payload.template_variables,
            subject=payload.subject,
            body_text=payload.body_text,
            complaint=complaint,
            recipient_name=rec_name,
            default_subject=f"Complaint Acknowledgement: {complaint.complaint_number}",
        )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            if payload.row_version is not None:
                verify_and_increment_version(complaint, payload.row_version)
            if complaint.acknowledged_at is None:
                complaint.acknowledged_at = now
                complaint.updated_by_user_id = tenant_ctx.user_id

            for clock in self._repo.get_active_sla_clocks(db, complaint.id):
                if clock.sla_stage == "ACKNOWLEDGEMENT" and clock.status != SLAStatus.MET.value:
                    clock.status = SLAStatus.MET.value
                    clock.completed_at = now

            comm = self._create_outbound_communication_in_tx(
                db,
                complaint=complaint,
                tenant_ctx=tenant_ctx,
                channel=payload.channel,
                direction=CommunicationDirection.OUTBOUND.value,
                communication_type=CommunicationType.ACKNOWLEDGEMENT.value,
                subject=rendered_subject,
                body_text=rendered_body,
                template_code=resolved_template,
                recipient_email=rec_email,
                recipient_name=rec_name,
                is_internal_only=False,
                is_information_request=False,
                metadata_json=payload.metadata_json,
                now=now,
            )
            db.add(
                ComplaintEvent(
                    organization_id=complaint.organization_id,
                    complaint_id=complaint.id,
                    event_type="COMPLAINT_ACKNOWLEDGED",
                    from_status=complaint.status,
                    to_status=complaint.status,
                    actor_user_id=tenant_ctx.user_id,
                    actor_membership_id=tenant_ctx.membership_id,
                    summary=f"Acknowledgement communication recorded via {comm.channel}.",
                    details_json={"communication_id": str(comm.id)},
                    occurred_at=now,
                )
            )
            self._audit.record(
                db,
                organization_id=complaint.organization_id,
                site_id=complaint.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="COMPLAINT_ACKNOWLEDGED",
                entity_type="complaint_communications",
                entity_id=comm.id,
                after_state={"complaint_id": str(complaint.id), "acknowledged_at": complaint.acknowledged_at},
            )
        db.refresh(comm)
        return comm

    def send_status_update(
        self,
        db: Session,
        *,
        complaint_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: StatusUpdateCreateRequest,
    ) -> ComplaintCommunication:
        complaint = self._get_complaint_for_tenant_or_404(db, tenant_ctx, complaint_id)
        rec_email, rec_name = self._resolve_recipient(
            db,
            complaint,
            recipient_email=str(payload.recipient_email) if payload.recipient_email else None,
            recipient_name=payload.recipient_name,
        )
        rendered_subject, rendered_body, resolved_template = _render_communication_content(
            template_code=payload.template_code,
            template_variables=payload.template_variables,
            subject=payload.subject,
            body_text=payload.body_text,
            complaint=complaint,
            recipient_name=rec_name,
            default_subject=f"Status Update for Complaint {complaint.complaint_number}",
        )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            comm = self._create_outbound_communication_in_tx(
                db,
                complaint=complaint,
                tenant_ctx=tenant_ctx,
                channel=payload.channel,
                direction=CommunicationDirection.OUTBOUND.value,
                communication_type=CommunicationType.STATUS_UPDATE.value,
                subject=rendered_subject,
                body_text=rendered_body,
                template_code=resolved_template,
                recipient_email=rec_email,
                recipient_name=rec_name,
                is_internal_only=False,
                is_information_request=False,
                metadata_json=payload.metadata_json,
                now=now,
            )
            db.add(
                ComplaintEvent(
                    organization_id=complaint.organization_id,
                    complaint_id=complaint.id,
                    event_type="STATUS_UPDATE_SENT",
                    from_status=complaint.status,
                    to_status=complaint.status,
                    actor_user_id=tenant_ctx.user_id,
                    actor_membership_id=tenant_ctx.membership_id,
                    summary=f"Status update communication recorded via {comm.channel}.",
                    details_json={"communication_id": str(comm.id)},
                    occurred_at=now,
                )
            )
            self._audit.record(
                db,
                organization_id=complaint.organization_id,
                site_id=complaint.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="COMPLAINT_STATUS_UPDATE_SENT",
                entity_type="complaint_communications",
                entity_id=comm.id,
            )
        db.refresh(comm)
        return comm

    def send_closure_message(
        self,
        db: Session,
        *,
        complaint_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: ClosureMessageCreateRequest,
    ) -> ComplaintCommunication:
        complaint = self._get_complaint_for_tenant_or_404(db, tenant_ctx, complaint_id)
        allowed_statuses = {
            ComplaintStatus.CLOSED.value,
            ComplaintStatus.UNDER_QA_REVIEW.value,
            ComplaintStatus.ACTION_PENDING.value,
        }
        if complaint.status not in allowed_statuses:
            raise ConflictError(
                f"Closure message requires complaint status in {sorted(allowed_statuses)} (got '{complaint.status}').",
                error_code="INVALID_COMPLAINT_STATUS_FOR_CLOSURE_MSG",
            )

        rec_email, rec_name = self._resolve_recipient(
            db,
            complaint,
            recipient_email=str(payload.recipient_email) if payload.recipient_email else None,
            recipient_name=payload.recipient_name,
        )
        rendered_subject, rendered_body, resolved_template = _render_communication_content(
            template_code=payload.template_code,
            template_variables=payload.template_variables,
            subject=payload.subject,
            body_text=payload.body_text,
            complaint=complaint,
            recipient_name=rec_name,
            default_subject=f"Complaint Closure Notification: {complaint.complaint_number}",
        )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            comm = self._create_outbound_communication_in_tx(
                db,
                complaint=complaint,
                tenant_ctx=tenant_ctx,
                channel=payload.channel,
                direction=CommunicationDirection.OUTBOUND.value,
                communication_type=CommunicationType.CLOSURE_NOTIFICATION.value,
                subject=rendered_subject,
                body_text=rendered_body,
                template_code=resolved_template,
                recipient_email=rec_email,
                recipient_name=rec_name,
                is_internal_only=False,
                is_information_request=False,
                metadata_json=payload.metadata_json,
                now=now,
            )
            db.add(
                ComplaintEvent(
                    organization_id=complaint.organization_id,
                    complaint_id=complaint.id,
                    event_type="CLOSURE_MESSAGE_SENT",
                    from_status=complaint.status,
                    to_status=complaint.status,
                    actor_user_id=tenant_ctx.user_id,
                    actor_membership_id=tenant_ctx.membership_id,
                    summary=f"Closure notification communication recorded via {comm.channel}.",
                    details_json={"communication_id": str(comm.id)},
                    occurred_at=now,
                )
            )
            self._audit.record(
                db,
                organization_id=complaint.organization_id,
                site_id=complaint.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="COMPLAINT_CLOSURE_MESSAGE_SENT",
                entity_type="complaint_communications",
                entity_id=comm.id,
            )
        db.refresh(comm)
        return comm

    def record_communication_reply(
        self,
        db: Session,
        *,
        complaint_id: uuid.UUID,
        communication_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: CommunicationReplyRequest,
    ) -> ComplaintCommunication:
        complaint = self._get_complaint_for_tenant_or_404(db, tenant_ctx, complaint_id)
        parent_comm = self._repo.get_communication_for_org(
            db,
            complaint.organization_id,  # type: ignore[arg-type]
            complaint.id,
            communication_id,
        )
        if parent_comm is None:
            raise NotFoundError("Target communication not found for this complaint.")

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            if payload.row_version is not None:
                verify_and_increment_version(complaint, payload.row_version)

            reply_comm = ComplaintCommunication(
                organization_id=complaint.organization_id,
                complaint_id=complaint.id,
                channel=(payload.channel or CommunicationChannel.PORTAL.value).strip().upper(),
                direction=CommunicationDirection.INBOUND.value,
                communication_type=CommunicationType.REPORTER_REPLY.value,
                recipient_email=str(payload.sender_email).strip().lower() if payload.sender_email else None,
                recipient_name=payload.sender_name,
                sender_user_id=tenant_ctx.user_id,
                sender_membership_id=tenant_ctx.membership_id,
                subject=payload.subject or (f"Re: {parent_comm.subject}" if parent_comm.subject else None),
                body_text=payload.body_text.strip(),
                is_internal_only=False,
                is_information_request=False,
                delivery_status=CommunicationDeliveryStatus.NOT_APPLICABLE.value,
                provider_message_id=payload.provider_message_id,
                in_reply_to_communication_id=parent_comm.id,
                metadata_json=payload.metadata_json,
                sent_at=now,
                created_by_user_id=tenant_ctx.user_id,
            )
            db.add(reply_comm)
            db.flush()

            prev_status = complaint.status
            unpaused_clocks = 0
            if complaint.status == ComplaintStatus.WAITING_INFO.value:
                complaint.status = ComplaintStatus.TRIAGED.value
                complaint.updated_by_user_id = tenant_ctx.user_id
                unpaused_clocks = self._unpause_sla_clocks_in_tx(db, complaint.id, now=now)

            db.add(
                ComplaintEvent(
                    organization_id=complaint.organization_id,
                    complaint_id=complaint.id,
                    event_type="REPORTER_REPLY_RECEIVED",
                    from_status=prev_status,
                    to_status=complaint.status,
                    actor_user_id=tenant_ctx.user_id,
                    actor_membership_id=tenant_ctx.membership_id,
                    summary="Recorded reporter reply and resumed complaint workflow."
                    if unpaused_clocks
                    else "Recorded reporter reply.",
                    details_json={
                        "reply_communication_id": str(reply_comm.id),
                        "in_reply_to_communication_id": str(parent_comm.id),
                        "unpaused_sla_clocks": unpaused_clocks,
                    },
                    occurred_at=now,
                )
            )
            self._audit.record(
                db,
                organization_id=complaint.organization_id,
                site_id=complaint.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="COMPLAINT_REPLY_RECORDED",
                entity_type="complaint_communications",
                entity_id=reply_comm.id,
                before_state={"complaint_status": prev_status},
                after_state={
                    "complaint_status": complaint.status,
                    "in_reply_to_communication_id": str(parent_comm.id),
                    "unpaused_sla_clocks": unpaused_clocks,
                },
            )
        db.refresh(reply_comm)
        return reply_comm

    def handle_outbox_outbound_communication(
        self,
        session_factory: sessionmaker[Session],
        event: Any,
        tenant_ctx: Optional[TenantContext] = None,
        *,
        email_provider: Optional[EmailProvider] = None,
    ) -> None:
        """
        ExternalOutboxHandler (`worker.py:190`):
        1. Short read UoW snapshots communication + connection config.
        2. Sends email via EmailProvider STRICTLY OUTSIDE open DB transactions.
        3. Short write UoW updates delivery_status, provider_message_id, delivered_at, failed_reason, attempt_count.
        """
        payload = getattr(event, "payload_json", None) or {}
        comm_id_raw = payload.get("communication_id") or event.aggregate_id
        comm_id = uuid.UUID(str(comm_id_raw))

        snapshot: Optional[Dict[str, Any]] = None
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            comm = self._repo.get_communication_by_id(uow.session, comm_id)
            if comm is None:
                return
            if tenant_ctx is not None and comm.organization_id != tenant_ctx.organization_id:
                return
            if comm.is_internal_only or comm.direction != CommunicationDirection.OUTBOUND.value:
                return
            if comm.delivery_status in {
                CommunicationDeliveryStatus.SENT.value,
                CommunicationDeliveryStatus.DELIVERED.value,
            }:
                return

            complaint = self._repo.get_by_id(uow.session, comm.complaint_id)
            conn = (
                self._repo.get_active_email_connection_for_org(
                    uow.session,
                    comm.organization_id,
                    site_id=complaint.site_id if complaint else None,
                )
                if comm.organization_id is not None
                else None
            )
            snapshot = {
                "comm_id": comm.id,
                "organization_id": comm.organization_id,
                "complaint_id": comm.complaint_id,
                "site_id": complaint.site_id if complaint else None,
                "recipient_email": comm.recipient_email,
                "recipient_name": comm.recipient_name,
                "subject": comm.subject or (
                    f"Quality Complaint Update: {complaint.complaint_number}" if complaint else "Quality Complaint Update"
                ),
                "body_text": comm.body_text,
                "provider_type": conn.provider_type if conn else EmailProviderType.MOCK.value,
                "from_address": conn.mailbox_address if conn else "qms-noreply@ojasyn.local",
                "config_json": dict(conn.config_json or {}) if conn else {},
            }

        if snapshot is None:
            return

        if not snapshot["recipient_email"]:
            with UnitOfWork(session_factory) as uow:
                assert uow.session is not None
                comm = self._repo.get_communication_by_id(uow.session, comm_id)
                if comm is not None:
                    comm.attempt_count += 1
                    comm.delivery_status = CommunicationDeliveryStatus.FAILED.value
                    comm.failed_reason = "Missing recipient_email on outbound communication."
                    comm.row_version += 1
            return

        provider = email_provider or self._get_email_provider()
        send_error: Optional[Exception] = None
        send_result = None
        try:
            send_result = provider.send_email(
                provider_type=snapshot["provider_type"],
                message=OutboundEmailMessage(
                    to_email=snapshot["recipient_email"],
                    to_name=snapshot["recipient_name"],
                    subject=snapshot["subject"],
                    body_text=snapshot["body_text"],
                    from_address=snapshot["from_address"],
                    metadata={
                        "complaint_id": str(snapshot["complaint_id"]),
                        "communication_id": str(snapshot["comm_id"]),
                    },
                ),
                config=snapshot["config_json"],
            )
        except Exception as exc:
            send_error = exc

        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            comm = self._repo.get_communication_by_id(uow.session, comm_id)
            if comm is None:
                return
            comm.attempt_count += 1
            comm.row_version += 1
            if send_error is None and send_result is not None:
                comm.delivery_status = CommunicationDeliveryStatus.SENT.value
                comm.provider_message_id = send_result.provider_message_id
                comm.delivered_at = send_result.sent_at
                comm.failed_reason = None
                self._audit.record(
                    uow.session,
                    organization_id=comm.organization_id,
                    site_id=snapshot["site_id"],
                    actor_type=ActorType.SYSTEM_WORKER.value,
                    actor_id=None,
                    action="OUTBOUND_COMMUNICATION_SENT",
                    entity_type="complaint_communications",
                    entity_id=comm.id,
                    after_state={
                        "delivery_status": comm.delivery_status,
                        "provider_message_id": comm.provider_message_id,
                        "attempt_count": comm.attempt_count,
                    },
                )
                self._outbox.enqueue(
                    uow.session,
                    organization_id=comm.organization_id,
                    aggregate_type="complaint_communication",
                    aggregate_id=comm.id,
                    event_type="OutboundCommunicationSent",
                    payload_json={
                        "communication_id": str(comm.id),
                        "complaint_id": str(comm.complaint_id),
                        "provider_message_id": comm.provider_message_id,
                    },
                )
            else:
                comm.delivery_status = CommunicationDeliveryStatus.FAILED.value
                comm.failed_reason = str(send_error)[:1000]
                self._audit.record(
                    uow.session,
                    organization_id=comm.organization_id,
                    site_id=snapshot["site_id"],
                    actor_type=ActorType.SYSTEM_WORKER.value,
                    actor_id=None,
                    action="OUTBOUND_COMMUNICATION_FAILED",
                    entity_type="complaint_communications",
                    entity_id=comm.id,
                    after_state={
                        "delivery_status": comm.delivery_status,
                        "attempt_count": comm.attempt_count,
                        "failed_reason": comm.failed_reason,
                    },
                )
        if send_error is not None:
            raise ExternalProviderError(str(send_error)) from send_error

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

    # --- B7 Email Intake (Connections, Webhook, Sync, Classification & Triage) ---

    def _assert_connection_site_access(
        self,
        tenant_ctx: TenantContext,
        site_id: Optional[uuid.UUID],
    ) -> None:
        if (
            site_id is not None
            and not tenant_ctx.is_org_wide
            and site_id not in tenant_ctx.allowed_site_ids
        ):
            raise PermissionDeniedError(
                "Not authorized to manage email intake for this site.",
                error_code="SITE_ACCESS_DENIED",
            )

    def _get_connection_for_tenant(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        connection_id: uuid.UUID,
    ) -> EmailConnection:
        conn = self._repo.get_email_connection_for_org(db, tenant_ctx.organization_id, connection_id)
        if conn is None:
            raise NotFoundError("Email connection not found.")
        self._assert_connection_site_access(tenant_ctx, conn.default_site_id)
        return conn

    def _get_inbound_for_tenant(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        inbound_email_id: uuid.UUID,
    ) -> InboundEmail:
        inbound = self._repo.get_inbound_email_for_org(db, tenant_ctx.organization_id, inbound_email_id)
        if inbound is None:
            raise NotFoundError("Inbound email not found.")
        conn = self._repo.get_email_connection_for_org(
            db, tenant_ctx.organization_id, inbound.email_connection_id
        )
        if conn is not None:
            self._assert_connection_site_access(tenant_ctx, conn.default_site_id)
        return inbound

    def create_email_connection(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        payload: EmailConnectionCreateRequest,
        source_ip: Optional[str] = None,
    ) -> Tuple[EmailConnection, str]:
        if payload.default_site_id is not None:
            site = db.scalar(
                select(Site).where(
                    Site.organization_id == tenant_ctx.organization_id,
                    Site.id == payload.default_site_id,
                )
            )
            if site is None:
                raise NotFoundError("Default site not found in this organization.")
            self._assert_connection_site_access(tenant_ctx, payload.default_site_id)

        mailbox = str(payload.mailbox_address).strip().lower()
        if self._repo.get_email_connection_by_mailbox(db, tenant_ctx.organization_id, mailbox) is not None:
            raise ConflictError(
                f"Email connection for mailbox '{mailbox}' already exists.",
                error_code="EMAIL_CONNECTION_DUPLICATE",
            )

        raw_secret = generate_webhook_secret()
        secret_hash = hash_webhook_secret(raw_secret)

        with UnitOfWork(db):
            conn = EmailConnection(
                organization_id=tenant_ctx.organization_id,
                default_site_id=payload.default_site_id,
                name=payload.name.strip(),
                provider_type=payload.provider_type.value,
                mailbox_address=mailbox,
                inbound_secret_hash=secret_hash,
                is_active=payload.is_active,
                auto_create_threshold=payload.auto_create_threshold,
                config_json=payload.config_json,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(conn)
            db.flush()
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=payload.default_site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="EMAIL_CONNECTION_CREATED",
                entity_type="email_connections",
                entity_id=conn.id,
                source_ip=source_ip,
                after_state={
                    "name": conn.name,
                    "provider_type": conn.provider_type,
                    "mailbox_address": conn.mailbox_address,
                    "is_active": conn.is_active,
                },
            )
        db.refresh(conn)
        return conn, raw_secret

    def list_email_connections(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        is_active: Optional[bool] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[EmailConnection], int]:
        return self._repo.list_email_connections(
            db, tenant_ctx, is_active=is_active, page=page, page_size=page_size
        )

    def get_email_connection(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        connection_id: uuid.UUID,
    ) -> EmailConnection:
        return self._get_connection_for_tenant(db, tenant_ctx, connection_id)

    def update_email_connection(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        connection_id: uuid.UUID,
        payload: EmailConnectionUpdateRequest,
        source_ip: Optional[str] = None,
    ) -> Tuple[EmailConnection, Optional[str]]:
        conn = self._get_connection_for_tenant(db, tenant_ctx, connection_id)
        if payload.default_site_id is not None:
            site = db.scalar(
                select(Site).where(
                    Site.organization_id == tenant_ctx.organization_id,
                    Site.id == payload.default_site_id,
                )
            )
            if site is None:
                raise NotFoundError("Default site not found in this organization.")
            self._assert_connection_site_access(tenant_ctx, payload.default_site_id)

        rotated_secret: Optional[str] = None
        with UnitOfWork(db):
            verify_and_increment_version(conn, payload.row_version)
            if payload.name is not None:
                conn.name = payload.name.strip()
            if payload.default_site_id is not None:
                conn.default_site_id = payload.default_site_id
            if payload.is_active is not None:
                conn.is_active = payload.is_active
            if payload.auto_create_threshold is not None:
                conn.auto_create_threshold = payload.auto_create_threshold
            if payload.config_json is not None:
                conn.config_json = payload.config_json
            if payload.rotate_webhook_secret:
                rotated_secret = generate_webhook_secret()
                conn.inbound_secret_hash = hash_webhook_secret(rotated_secret)
            conn.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=conn.default_site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="EMAIL_CONNECTION_UPDATED",
                entity_type="email_connections",
                entity_id=conn.id,
                source_ip=source_ip,
                after_state={
                    "is_active": conn.is_active,
                    "secret_rotated": bool(rotated_secret),
                },
            )
        db.refresh(conn)
        return conn, rotated_secret

    def _pre_store_email_attachments(
        self,
        *,
        organization_id: uuid.UUID,
        attachments: List[Any],
        storage: ObjectStorageProvider,
    ) -> List[Tuple[uuid.UUID, uuid.UUID, str, str, str, str, bytes]]:
        from app.services.document_control_service import DocumentControlService

        if len(attachments) > 10:
            raise ValidationError("Too many email attachments (maximum 10 per message).")
        doc_svc = DocumentControlService(storage=storage)
        stored_items: List[Tuple[uuid.UUID, uuid.UUID, str, str, str, str, bytes]] = []
        try:
            for att in attachments:
                raw_bytes = att.content_bytes
                safe_name, detected_mime, checksum = doc_svc._validate_upload_bytes(
                    raw_bytes,
                    original_filename=att.original_filename,
                    claimed_mime_type=att.mime_type,
                    organization_id=organization_id,
                    user_id=None,
                )
                doc_id = uuid.uuid4()
                ver_id = uuid.uuid4()
                object_key = build_vault_object_key(organization_id, doc_id, ver_id)
                storage.put_object(object_key, raw_bytes, content_type=detected_mime)
                stored_items.append(
                    (doc_id, ver_id, object_key, safe_name, detected_mime, checksum, raw_bytes)
                )
            return stored_items
        except Exception:
            for _, _, object_key, *_ in stored_items:
                storage.delete_object(object_key)
            raise

    def _persist_inbound_email_in_tx(
        self,
        db: Session,
        *,
        conn: EmailConnection,
        fetched: FetchedInboundEmail,
        pre_stored_attachments: List[Tuple[uuid.UUID, uuid.UUID, str, str, str, str, bytes]],
        storage: ObjectStorageProvider,
        actor_type: str,
        actor_user_id: Optional[uuid.UUID],
        source_ip: Optional[str] = None,
    ) -> InboundEmail:
        from app.services.document_control_service import DocumentControlService

        inbound = InboundEmail(
            organization_id=conn.organization_id,
            email_connection_id=conn.id,
            provider_message_id=fetched.provider_message_id,
            thread_id=fetched.thread_id,
            in_reply_to_header=fetched.in_reply_to_header,
            from_email=fetched.from_email,
            from_name=fetched.from_name,
            to_addresses_json=fetched.to_addresses or None,
            cc_addresses_json=fetched.cc_addresses or None,
            subject=fetched.subject,
            body_text=fetched.body_text,
            body_html_sanitized=sanitize_email_html(fetched.body_html),
            received_at=fetched.received_at,
            triage_status=InboundEmailTriageStatus.RECEIVED.value,
            raw_headers_json=fetched.raw_headers or None,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
        db.add(inbound)
        db.flush()

        doc_svc = DocumentControlService(storage=storage)
        for doc_id, ver_id, object_key, safe_name, detected_mime, checksum, raw_bytes in pre_stored_attachments:
            doc = doc_svc.upload_in_tx(
                db,
                organization_id=conn.organization_id,
                uploaded_by_user_id=actor_user_id,
                file_bytes=raw_bytes,
                original_filename=safe_name,
                claimed_mime_type=detected_mime,
                category="COMPLAINT_ATTACHMENT",
                site_id=conn.default_site_id,
                pre_stored=(doc_id, ver_id, object_key, safe_name, detected_mime, checksum),
            )
            db.add(
                EmailAttachment(
                    organization_id=conn.organization_id,
                    inbound_email_id=inbound.id,
                    document_id=doc.id,
                    document_version_id=ver_id,
                    original_filename=safe_name,
                    mime_type=detected_mime,
                    byte_size=len(raw_bytes),
                    checksum_sha256=checksum,
                    storage_status="STORED",
                    created_by_user_id=actor_user_id,
                    updated_by_user_id=actor_user_id,
                )
            )

        self._audit.record(
            db,
            organization_id=conn.organization_id,
            site_id=conn.default_site_id,
            actor_type=actor_type,
            actor_id=actor_user_id,
            action="INBOUND_EMAIL_RECEIVED",
            entity_type="inbound_emails",
            entity_id=inbound.id,
            source_ip=source_ip,
            after_state={
                "email_connection_id": str(conn.id),
                "provider_message_id": inbound.provider_message_id,
                "attachment_count": len(pre_stored_attachments),
            },
        )
        # Never store sender/body PII inside outbox_events
        self._outbox.enqueue(
            db,
            organization_id=conn.organization_id,
            aggregate_type="inbound_email",
            aggregate_id=inbound.id,
            event_type="InboundEmailReceived",
            payload_json={
                "inbound_email_id": str(inbound.id),
                "email_connection_id": str(conn.id),
                "organization_id": str(conn.organization_id),
            },
        )
        return inbound

    def ingest_webhook_email(
        self,
        db: Session,
        *,
        connection_id: uuid.UUID,
        webhook_secret: Optional[str],
        payload: InboundEmailWebhookPayload,
        source_ip: Optional[str] = None,
        storage: Optional[ObjectStorageProvider] = None,
    ) -> InboundEmailWebhookAckResponse:
        conn = self._repo.get_email_connection_by_id(db, connection_id)
        if conn is None:
            self._audit.record_security_event(
                event_type="EMAIL_WEBHOOK_UNKNOWN_CONNECTION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.WARNING.value,
                source_ip=source_ip,
                target_resource=f"/api/v1/email-intake/webhooks/{connection_id}",
            )
            raise AuthenticationError("Invalid webhook credentials or connection.")

        if not verify_webhook_secret(webhook_secret, conn.inbound_secret_hash):
            self._audit.record_security_event(
                event_type="EMAIL_WEBHOOK_INVALID_SECRET",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=conn.organization_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/email-intake/webhooks/{connection_id}",
            )
            raise AuthenticationError("Invalid X-Webhook-Secret header.")

        org = db.get(Organization, conn.organization_id)
        if not conn.is_active or org is None or org.status != OrganizationStatus.ACTIVE.value:
            self._audit.record_security_event(
                event_type="EMAIL_WEBHOOK_INACTIVE_TARGET",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.WARNING.value,
                organization_id=conn.organization_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/email-intake/webhooks/{connection_id}",
            )
            raise PermissionDeniedError(
                "Email connection or organization is not active.",
                error_code="EMAIL_CONNECTION_INACTIVE",
            )

        existing = self._repo.get_inbound_email_by_provider_msg_id(
            db, conn.organization_id, payload.provider_message_id.strip()
        )
        if existing is not None:
            return InboundEmailWebhookAckResponse(
                id=existing.id,
                provider_message_id=existing.provider_message_id,
                triage_status=existing.triage_status,
                deduplicated=True,
                complaint_id=existing.complaint_id,
            )

        settings = get_settings()
        fetched = parse_dict_inbound_message(
            payload.model_dump(mode="python"),
            max_attachment_bytes=settings.UPLOAD_MAX_BYTES,
        )
        storage_provider = storage or self._get_storage()
        # Preserve attachments to ObjectStorageProvider BEFORE opening DB UnitOfWork
        pre_stored = self._pre_store_email_attachments(
            organization_id=conn.organization_id,
            attachments=fetched.attachments,
            storage=storage_provider,
        )

        try:
            with UnitOfWork(db):
                inbound = self._persist_inbound_email_in_tx(
                    db,
                    conn=conn,
                    fetched=fetched,
                    pre_stored_attachments=pre_stored,
                    storage=storage_provider,
                    actor_type=ActorType.WEBHOOK.value,
                    actor_user_id=None,
                    source_ip=source_ip,
                )
        except IntegrityError:
            for _, _, object_key, *_ in pre_stored:
                storage_provider.delete_object(object_key)
            db.rollback()
            existing = self._repo.get_inbound_email_by_provider_msg_id(
                db, conn.organization_id, fetched.provider_message_id
            )
            if existing is not None:
                return InboundEmailWebhookAckResponse(
                    id=existing.id,
                    provider_message_id=existing.provider_message_id,
                    triage_status=existing.triage_status,
                    deduplicated=True,
                    complaint_id=existing.complaint_id,
                )
            raise
        except Exception:
            for _, _, object_key, *_ in pre_stored:
                storage_provider.delete_object(object_key)
            raise

        db.refresh(inbound)
        return InboundEmailWebhookAckResponse(
            id=inbound.id,
            provider_message_id=inbound.provider_message_id,
            triage_status=inbound.triage_status,
            deduplicated=False,
            complaint_id=inbound.complaint_id,
        )

    def sync_email_connection(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        connection_id: uuid.UUID,
        email_provider: Optional[EmailProvider] = None,
        storage: Optional[ObjectStorageProvider] = None,
        limit: int = 50,
    ) -> EmailConnectionSyncResponse:
        conn = self._get_connection_for_tenant(db, tenant_ctx, connection_id)
        if not conn.is_active:
            raise ConflictError("Cannot sync an inactive email connection.")

        org_id = conn.organization_id
        provider_type = conn.provider_type
        cursor = conn.sync_cursor
        cfg = dict(conn.config_json or {})
        # Release any read transaction before calling external IMAP/provider I/O
        db.rollback()

        provider = email_provider or self._get_email_provider()
        batch = provider.fetch_messages(
            provider_type=provider_type,
            cursor=cursor,
            config=cfg,
            limit=limit,
        )

        storage_provider = storage or self._get_storage()
        pre_stored_by_msg_id: Dict[str, List[Tuple[uuid.UUID, uuid.UUID, str, str, str, str, bytes]]] = {}
        new_messages: List[FetchedInboundEmail] = []
        deduplicated_count = 0

        for msg in batch.messages:
            existing = self._repo.get_inbound_email_by_provider_msg_id(
                db, org_id, msg.provider_message_id
            )
            if existing is not None:
                deduplicated_count += 1
                continue
            if msg.provider_message_id in pre_stored_by_msg_id:
                deduplicated_count += 1
                continue
            pre_stored_by_msg_id[msg.provider_message_id] = self._pre_store_email_attachments(
                organization_id=org_id,
                attachments=msg.attachments,
                storage=storage_provider,
            )
            new_messages.append(msg)

        now = datetime.now(timezone.utc)
        created_count = 0
        try:
            with UnitOfWork(db):
                conn = self._get_connection_for_tenant(db, tenant_ctx, connection_id)
                for msg in new_messages:
                    if self._repo.get_inbound_email_by_provider_msg_id(
                        db, org_id, msg.provider_message_id
                    ) is not None:
                        deduplicated_count += 1
                        continue
                    self._persist_inbound_email_in_tx(
                        db,
                        conn=conn,
                        fetched=msg,
                        pre_stored_attachments=pre_stored_by_msg_id.get(msg.provider_message_id, []),
                        storage=storage_provider,
                        actor_type=ActorType.USER.value,
                        actor_user_id=tenant_ctx.user_id,
                    )
                    created_count += 1

                conn.last_synced_at = now
                if batch.next_cursor is not None:
                    conn.sync_cursor = batch.next_cursor
                conn.row_version += 1
                conn.updated_by_user_id = tenant_ctx.user_id

                self._audit.record(
                    db,
                    organization_id=org_id,
                    site_id=conn.default_site_id,
                    actor_type=ActorType.USER.value,
                    actor_id=tenant_ctx.user_id,
                    action="EMAIL_CONNECTION_SYNCED",
                    entity_type="email_connections",
                    entity_id=conn.id,
                    after_state={
                        "fetched_count": len(batch.messages),
                        "created_count": created_count,
                        "deduplicated_count": deduplicated_count,
                        "sync_cursor": conn.sync_cursor,
                    },
                )
        except Exception:
            for items in pre_stored_by_msg_id.values():
                for _, _, object_key, *_ in items:
                    storage_provider.delete_object(object_key)
            raise

        db.refresh(conn)
        return EmailConnectionSyncResponse(
            connection_id=conn.id,
            fetched_count=len(batch.messages),
            created_count=created_count,
            deduplicated_count=deduplicated_count,
            last_synced_at=now,
            sync_cursor=conn.sync_cursor,
        )

    def list_inbound_emails(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        triage_status: Optional[str] = None,
        email_connection_id: Optional[uuid.UUID] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[InboundEmail], int]:
        return self._repo.list_inbound_emails(
            db,
            tenant_ctx,
            triage_status=triage_status,
            email_connection_id=email_connection_id,
            page=page,
            page_size=page_size,
        )

    def get_inbound_email_detail(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        inbound_email_id: uuid.UUID,
    ) -> InboundEmail:
        return self._get_inbound_for_tenant(db, tenant_ctx, inbound_email_id)

    def _link_email_attachments_to_complaint_in_tx(
        self,
        db: Session,
        *,
        complaint: Complaint,
        email_attachments: List[EmailAttachment],
        actor_user_id: Optional[uuid.UUID],
    ) -> int:
        linked = 0
        for att in email_attachments:
            if att.document_id is None:
                continue
            existing = db.scalar(
                select(ComplaintAttachment).where(
                    ComplaintAttachment.complaint_id == complaint.id,
                    ComplaintAttachment.document_id == att.document_id,
                )
            )
            if existing is not None:
                continue
            db.add(
                ComplaintAttachment(
                    organization_id=complaint.organization_id,
                    complaint_id=complaint.id,
                    document_id=att.document_id,
                    attachment_type="EVIDENCE",
                    caption=f"Email attachment: {att.original_filename}"[:255],
                    is_external_visible=False,
                    uploaded_by_user_id=actor_user_id,
                    created_by_user_id=actor_user_id,
                )
            )
            linked += 1
        return linked

    def triage_inbound_email(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        inbound_email_id: uuid.UUID,
        payload: InboundEmailTriageRequest,
        source_ip: Optional[str] = None,
    ) -> InboundEmail:
        inbound = self._get_inbound_for_tenant(db, tenant_ctx, inbound_email_id)
        conn = self._repo.get_email_connection_for_org(
            db, tenant_ctx.organization_id, inbound.email_connection_id
        )
        org = db.get(Organization, tenant_ctx.organization_id)
        if org is None or org.status != OrganizationStatus.ACTIVE.value:
            raise NotFoundError("Active organization not found.")

        now = datetime.now(timezone.utc)
        email_atts = self._repo.list_email_attachments(db, tenant_ctx.organization_id, inbound.id)

        if payload.decision == "CREATE_COMPLAINT":
            if inbound.complaint_id is not None:
                raise ConflictError("Inbound email is already linked to a complaint.")
            target_site_id = payload.site_id if payload.site_id is not None else (conn.default_site_id if conn else None)
            if target_site_id is not None:
                site = db.scalar(
                    select(Site).where(
                        Site.organization_id == tenant_ctx.organization_id,
                        Site.id == target_site_id,
                    )
                )
                if site is None:
                    raise NotFoundError("Site not found in this organization.")
                self._assert_connection_site_access(tenant_ctx, target_site_id)

            description_text = (
                payload.description
                or inbound.body_text
                or inbound.subject
                or f"Complaint created from inbound email {inbound.provider_message_id}"
            ).strip()
            if len(description_text) < 5:
                description_text = f"Email intake: {description_text}"

            reg_req = ComplaintRegisterRequest(
                organization_id=tenant_ctx.organization_id,
                site_id=target_site_id,
                source_channel=ComplaintSource.EMAIL.value,
                source_record_id=inbound.id,
                complainant=ComplainantInput(
                    full_name=(inbound.from_name or inbound.from_email.split("@")[0] or "Email Reporter")[:150],
                    email=inbound.from_email,
                ),
                reported_product_name=payload.reported_product_name,
                reported_batch_number=payload.reported_batch_number,
                description=description_text,
            )
            principal = Principal(
                user_id=tenant_ctx.user_id,  # type: ignore[arg-type]
                email="",
                full_name="QA Triage User",
                account_status="ACTIVE",
            )
            with UnitOfWork(db):
                verify_and_increment_version(inbound, payload.row_version)
                complaint, _ = self._register_in_tx(
                    db,
                    reg_req,
                    target_org_id=tenant_ctx.organization_id,
                    target_org=org,
                    principal=principal if tenant_ctx.user_id else None,
                    effective_idempotency_key=None,
                    payload_hash=None,
                    source_ip=source_ip,
                )
                self._link_email_attachments_to_complaint_in_tx(
                    db,
                    complaint=complaint,
                    email_attachments=email_atts,
                    actor_user_id=tenant_ctx.user_id,
                )
                inbound.complaint_id = complaint.id
                inbound.triage_status = InboundEmailTriageStatus.TRIAGED_COMPLAINT.value
                inbound.triaged_by_membership_id = tenant_ctx.membership_id
                inbound.triaged_at = now
                inbound.triage_notes = payload.triage_notes
                inbound.updated_by_user_id = tenant_ctx.user_id

                self._audit.record(
                    db,
                    organization_id=tenant_ctx.organization_id,
                    site_id=target_site_id,
                    actor_type=ActorType.USER.value,
                    actor_id=tenant_ctx.user_id,
                    action="INBOUND_EMAIL_TRIAGED",
                    entity_type="inbound_emails",
                    entity_id=inbound.id,
                    source_ip=source_ip,
                    after_state={
                        "decision": payload.decision,
                        "triage_status": inbound.triage_status,
                        "complaint_id": str(complaint.id),
                    },
                )
                self._outbox.enqueue(
                    db,
                    organization_id=tenant_ctx.organization_id,
                    aggregate_type="inbound_email",
                    aggregate_id=inbound.id,
                    event_type="InboundEmailTriaged",
                    payload_json={
                        "inbound_email_id": str(inbound.id),
                        "decision": payload.decision,
                        "complaint_id": str(complaint.id),
                    },
                )

        elif payload.decision == "MARK_NON_COMPLAINT":
            with UnitOfWork(db):
                verify_and_increment_version(inbound, payload.row_version)
                inbound.triage_status = InboundEmailTriageStatus.TRIAGED_NON_COMPLAINT.value
                inbound.triaged_by_membership_id = tenant_ctx.membership_id
                inbound.triaged_at = now
                inbound.triage_notes = payload.triage_notes
                inbound.updated_by_user_id = tenant_ctx.user_id

                self._audit.record(
                    db,
                    organization_id=tenant_ctx.organization_id,
                    site_id=conn.default_site_id if conn else None,
                    actor_type=ActorType.USER.value,
                    actor_id=tenant_ctx.user_id,
                    action="INBOUND_EMAIL_TRIAGED",
                    entity_type="inbound_emails",
                    entity_id=inbound.id,
                    source_ip=source_ip,
                    after_state={
                        "decision": payload.decision,
                        "triage_status": inbound.triage_status,
                    },
                )
                self._outbox.enqueue(
                    db,
                    organization_id=tenant_ctx.organization_id,
                    aggregate_type="inbound_email",
                    aggregate_id=inbound.id,
                    event_type="InboundEmailTriaged",
                    payload_json={
                        "inbound_email_id": str(inbound.id),
                        "decision": payload.decision,
                    },
                )

        else:  # LINK_EXISTING_COMPLAINT
            if payload.complaint_id is None:
                raise ValidationError("complaint_id is required for LINK_EXISTING_COMPLAINT.")
            complaint = self._get_complaint_for_tenant_or_404(db, tenant_ctx, payload.complaint_id)
            with UnitOfWork(db):
                verify_and_increment_version(inbound, payload.row_version)
                inbound.complaint_id = complaint.id
                inbound.triage_status = InboundEmailTriageStatus.TRIAGED_COMPLAINT.value
                inbound.triaged_by_membership_id = tenant_ctx.membership_id
                inbound.triaged_at = now
                inbound.triage_notes = payload.triage_notes
                inbound.updated_by_user_id = tenant_ctx.user_id

                comm = ComplaintCommunication(
                    organization_id=complaint.organization_id,
                    complaint_id=complaint.id,
                    channel=CommunicationChannel.EMAIL.value,
                    direction=CommunicationDirection.INBOUND.value,
                    communication_type=CommunicationType.REPORTER_REPLY.value,
                    recipient_email=inbound.from_email,
                    recipient_name=inbound.from_name,
                    sender_user_id=tenant_ctx.user_id,
                    sender_membership_id=tenant_ctx.membership_id,
                    subject=(inbound.subject or "")[:200] or None,
                    body_text=(inbound.body_text or inbound.subject or "(Empty email body)").strip(),
                    is_internal_only=False,
                    is_information_request=False,
                    delivery_status=CommunicationDeliveryStatus.NOT_APPLICABLE.value,
                    provider_message_id=inbound.provider_message_id,
                    metadata_json={"inbound_email_id": str(inbound.id)},
                    sent_at=inbound.received_at,
                    created_by_user_id=tenant_ctx.user_id,
                )
                db.add(comm)
                db.flush()

                prev_status = complaint.status
                unpaused = 0
                if complaint.status == ComplaintStatus.WAITING_INFO.value:
                    complaint.status = ComplaintStatus.TRIAGED.value
                    complaint.updated_by_user_id = tenant_ctx.user_id
                    unpaused = self._unpause_sla_clocks_in_tx(db, complaint.id, now=now)

                self._link_email_attachments_to_complaint_in_tx(
                    db,
                    complaint=complaint,
                    email_attachments=email_atts,
                    actor_user_id=tenant_ctx.user_id,
                )
                db.add(
                    ComplaintEvent(
                        organization_id=complaint.organization_id,
                        complaint_id=complaint.id,
                        event_type="REPORTER_REPLY_RECEIVED",
                        from_status=prev_status,
                        to_status=complaint.status,
                        actor_user_id=tenant_ctx.user_id,
                        actor_membership_id=tenant_ctx.membership_id,
                        summary=f"Linked inbound email {inbound.provider_message_id} to complaint.",
                        details_json={"inbound_email_id": str(inbound.id), "unpaused_sla_clocks": unpaused},
                        occurred_at=now,
                    )
                )
                self._audit.record(
                    db,
                    organization_id=tenant_ctx.organization_id,
                    site_id=complaint.site_id,
                    actor_type=ActorType.USER.value,
                    actor_id=tenant_ctx.user_id,
                    action="INBOUND_EMAIL_TRIAGED",
                    entity_type="inbound_emails",
                    entity_id=inbound.id,
                    source_ip=source_ip,
                    after_state={
                        "decision": payload.decision,
                        "complaint_id": str(complaint.id),
                        "communication_id": str(comm.id),
                    },
                )
                self._outbox.enqueue(
                    db,
                    organization_id=tenant_ctx.organization_id,
                    aggregate_type="inbound_email",
                    aggregate_id=inbound.id,
                    event_type="InboundEmailTriaged",
                    payload_json={
                        "inbound_email_id": str(inbound.id),
                        "decision": payload.decision,
                        "complaint_id": str(complaint.id),
                    },
                )

        db.refresh(inbound)
        return inbound

    def handle_outbox_inbound_email(
        self,
        session_factory: sessionmaker[Session],
        event: Any,
        tenant_ctx: Optional[TenantContext] = None,
        *,
        llm_provider: Optional[LLMProvider] = None,
    ) -> None:
        """
        ExternalOutboxHandler (`worker.py:210`):
        1. Short read UoW snapshots InboundEmail & connection threshold.
        2. Classifies outside open DB transaction (PIIRedactor-masked LLMProvider or deterministic fallback;
           fail-safe to UNCERTAIN on LLM error/unavailability).
        3. Short write UoW writes immutable EmailProcessingAttempt and routes to:
           - ComplaintService._register_in_tx(source=EMAIL) when LIKELY_COMPLAINT & confidence >= threshold,
           - MANUAL_TRIAGE_PENDING when UNCERTAIN or below threshold,
           - CLASSIFIED when NON_COMPLAINT.
        """
        payload = getattr(event, "payload_json", None) or {}
        inbound_id_raw = payload.get("inbound_email_id") or event.aggregate_id
        inbound_id = uuid.UUID(str(inbound_id_raw))

        snapshot: Optional[Dict[str, Any]] = None
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            inbound = self._repo.get_inbound_email_by_id(uow.session, inbound_id)
            if inbound is None:
                return
            if tenant_ctx is not None and inbound.organization_id != tenant_ctx.organization_id:
                return
            if inbound.triage_status != InboundEmailTriageStatus.RECEIVED.value or inbound.complaint_id is not None:
                return

            conn = self._repo.get_email_connection_for_org(
                uow.session, inbound.organization_id, inbound.email_connection_id
            )
            atts = self._repo.list_email_attachments(uow.session, inbound.organization_id, inbound.id)
            attempt_num = self._repo.get_next_processing_attempt_number(
                uow.session, inbound.organization_id, inbound.id
            )
            snapshot = {
                "inbound_id": inbound.id,
                "organization_id": inbound.organization_id,
                "site_id": conn.default_site_id if conn else None,
                "auto_create_threshold": conn.auto_create_threshold if conn else Decimal("0.7500"),
                "from_email": inbound.from_email,
                "from_name": inbound.from_name,
                "subject": inbound.subject,
                "body_text": inbound.body_text,
                "attachment_names": [a.original_filename for a in atts],
                "attempt_number": attempt_num,
            }

        if snapshot is None:
            return

        started_at = datetime.now(timezone.utc)
        error_code: Optional[str] = None
        error_message: Optional[str] = None
        classifier_version = "deterministic-b7-v1"
        settings = get_settings()
        use_llm = llm_provider is not None or bool(settings.GROQ_API_KEY)

        if use_llm:
            provider = llm_provider or LLMProvider(settings=settings)
            classifier_version = f"llm:{provider._settings.LLM_MODEL_NAME}"
            email_narrative = (
                f"Subject: {snapshot['subject'] or '(no subject)'}\n"
                f"Attachments: {', '.join(snapshot['attachment_names']) or 'None'}\n"
                f"Body:\n{snapshot['body_text'] or ''}"
            )
            known_pii: Dict[str, List[str]] = {
                "email": [snapshot["from_email"]] if snapshot["from_email"] else [],
                "person": [snapshot["from_name"]] if snapshot["from_name"] else [],
            }
            try:
                cls_out = provider.generate_structured(
                    system_prompt=EMAIL_CLASSIFICATION_SYSTEM_PROMPT,
                    untrusted_text=email_narrative,
                    response_model=InboundEmailClassificationLLMOutput,
                    known_pii=known_pii,
                )
            except Exception as exc:
                # Fail-safe to UNCERTAIN so inbound emails are NEVER dropped on LLM failure
                error_code = "LLM_CLASSIFICATION_FALLBACK"
                error_message = str(exc)[:500]
                cls_out = InboundEmailClassificationLLMOutput(
                    classification=InboundEmailClassification.UNCERTAIN.value,
                    confidence=Decimal("0.5000"),
                    rationale="LLM classifier unavailable or returned invalid output; routed to manual triage.",
                )
        else:
            cls_out = _classify_inbound_email_deterministic(snapshot["subject"], snapshot["body_text"])

        completed_at = datetime.now(timezone.utc)

        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            db = uow.session
            inbound = self._repo.get_inbound_email_by_id(db, inbound_id)
            if inbound is None or inbound.triage_status != InboundEmailTriageStatus.RECEIVED.value:
                return
            org = db.get(Organization, inbound.organization_id)
            atts = self._repo.list_email_attachments(db, inbound.organization_id, inbound.id)

            inbound.classification = cls_out.classification
            inbound.classification_confidence = cls_out.confidence
            inbound.classification_rationale = cls_out.rationale
            inbound.row_version += 1

            threshold = Decimal(str(snapshot["auto_create_threshold"]))
            created_complaint_id: Optional[uuid.UUID] = None
            attempt_status = "COMPLETED"

            if (
                cls_out.classification == InboundEmailClassification.LIKELY_COMPLAINT.value
                and cls_out.confidence >= threshold
                and org is not None
                and org.status == OrganizationStatus.ACTIVE.value
            ):
                desc = (inbound.body_text or inbound.subject or "Inbound email complaint").strip()
                if len(desc) < 5:
                    desc = f"Email intake: {desc}"
                reg_req = ComplaintRegisterRequest(
                    organization_id=inbound.organization_id,
                    site_id=snapshot["site_id"],
                    source_channel=ComplaintSource.EMAIL.value,
                    source_record_id=inbound.id,
                    complainant=ComplainantInput(
                        full_name=(inbound.from_name or inbound.from_email.split("@")[0] or "Email Reporter")[:150],
                        email=inbound.from_email,
                    ),
                    reported_product_name=cls_out.extracted_product_name,
                    reported_batch_number=cls_out.extracted_batch_number,
                    defect_category=cls_out.defect_category,
                    description=desc,
                )
                complaint, _ = self._register_in_tx(
                    db,
                    reg_req,
                    target_org_id=inbound.organization_id,
                    target_org=org,
                    principal=None,
                    effective_idempotency_key=None,
                    payload_hash=None,
                    source_ip=None,
                    actor_type=ActorType.SYSTEM_WORKER.value,
                )
                self._link_email_attachments_to_complaint_in_tx(
                    db,
                    complaint=complaint,
                    email_attachments=atts,
                    actor_user_id=None,
                )
                inbound.complaint_id = complaint.id
                inbound.triage_status = InboundEmailTriageStatus.COMPLAINT_CREATED.value
                created_complaint_id = complaint.id
            elif cls_out.classification == InboundEmailClassification.NON_COMPLAINT.value and cls_out.confidence >= threshold:
                inbound.triage_status = InboundEmailTriageStatus.CLASSIFIED.value
            else:
                inbound.triage_status = InboundEmailTriageStatus.MANUAL_TRIAGE_PENDING.value
                if error_code:
                    attempt_status = "FALLBACK_MANUAL_TRIAGE"

            db.add(
                EmailProcessingAttempt(
                    organization_id=inbound.organization_id,
                    inbound_email_id=inbound.id,
                    attempt_number=snapshot["attempt_number"],
                    status=attempt_status,
                    classifier_version=classifier_version[:60],
                    error_code=error_code,
                    error_message=error_message,
                    started_at=started_at,
                    completed_at=completed_at,
                )
            )
            self._audit.record(
                db,
                organization_id=inbound.organization_id,
                site_id=snapshot["site_id"],
                actor_type=ActorType.SYSTEM_WORKER.value,
                actor_id=None,
                action="INBOUND_EMAIL_PROCESSED",
                entity_type="inbound_emails",
                entity_id=inbound.id,
                after_state={
                    "classification": inbound.classification,
                    "confidence": str(inbound.classification_confidence),
                    "triage_status": inbound.triage_status,
                    "complaint_id": str(created_complaint_id) if created_complaint_id else None,
                },
            )
