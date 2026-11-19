import logging
import time
import uuid
from typing import Callable, Dict, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.database import UnitOfWork, get_session_factory
from app.core.deps import TenantContext
from app.core.logging import configure_logging
from app.domain.enums import (
    ActorType,
    MembershipStatus,
    RoleAssignmentScopeType,
    RoleAssignmentStatus,
)
from app.domain.rbac_catalog import WORKER_PERMISSIONS
from app.models.domain.governance import Notification, OutboxEvent
from app.repositories.governance_repo import OutboxRepository
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

QA_FANOUT_ROLE_CODES = ("QA_HEAD", "QA_OFFICER", "QMS_HEAD")

OutboxHandler = Callable[[Session, OutboxEvent, Optional[TenantContext]], None]
ExternalOutboxHandler = Callable[[sessionmaker[Session], OutboxEvent, Optional[TenantContext]], None]


def build_worker_tenant_context(event: OutboxEvent) -> Optional[TenantContext]:
    """
    Constructs a strictly scoped SYSTEM_WORKER TenantContext for an outbox event.
    Excludes all human quality-approval permissions (batch.release, sop.approve, etc.).
    """
    if event.organization_id is None:
        return None
    return TenantContext(
        user_id=None,
        organization_id=event.organization_id,
        membership_id=None,
        allowed_site_ids=frozenset(),
        is_org_wide=True,
        permission_codes=WORKER_PERMISSIONS,
        role_codes=frozenset(),
        actor_type=ActorType.SYSTEM_WORKER.value,
    )


def _handle_notification_requested(
    db: Session,
    event: OutboxEvent,
    tenant_ctx: Optional[TenantContext],
) -> None:
    if tenant_ctx is None:
        raise ValueError("NotificationRequested requires organization_id.")
    payload = event.payload_json or {}
    recipient_id = uuid.UUID(str(payload["recipient_membership_id"]))
    NotificationService().create_in_tx(
        db,
        organization_id=tenant_ctx.organization_id,
        recipient_membership_id=recipient_id,
        event_type=str(payload.get("notification_event_type", event.event_type)),
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        title=str(payload.get("title", "Notification")),
        body=str(payload.get("body", "")),
    )


def fan_out_complaint_registered_notifications(
    db: Session,
    *,
    organization_id: uuid.UUID,
    complaint_id: uuid.UUID,
    site_id: Optional[uuid.UUID] = None,
    complaint_number: Optional[str] = None,
    title: Optional[str] = None,
) -> int:
    """
    Idempotently creates durable Notification records for active QA memberships
    (QA_HEAD, QA_OFFICER, QMS_HEAD) scoped to the complaint's site or org-wide
    within the tenant organization. Participates in the caller's UnitOfWork.
    """
    from app.models.domain.identity import MembershipRoleAssignment, OrganizationMembership, Role

    stmt = (
        select(OrganizationMembership.id)
        .join(
            MembershipRoleAssignment,
            MembershipRoleAssignment.membership_id == OrganizationMembership.id,
        )
        .join(Role, Role.id == MembershipRoleAssignment.role_id)
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.membership_status == MembershipStatus.ACTIVE.value,
            MembershipRoleAssignment.organization_id == organization_id,
            MembershipRoleAssignment.status == RoleAssignmentStatus.ACTIVE.value,
            Role.role_code.in_(QA_FANOUT_ROLE_CODES),
        )
    )
    if site_id is not None:
        stmt = stmt.where(
            or_(
                MembershipRoleAssignment.scope_type == RoleAssignmentScopeType.ORGANIZATION.value,
                MembershipRoleAssignment.site_id == site_id,
            )
        )

    recipient_ids = list(dict.fromkeys(db.scalars(stmt).all()))
    if not recipient_ids:
        return 0

    existing_recipients = set(
        db.scalars(
            select(Notification.recipient_membership_id).where(
                Notification.organization_id == organization_id,
                Notification.event_type == "ComplaintRegistered",
                Notification.aggregate_id == complaint_id,
                Notification.recipient_membership_id.in_(recipient_ids),
            )
        ).all()
    )

    notif_service = NotificationService()
    notif_title = f"New Complaint Registered: {complaint_number}" if complaint_number else "New Complaint Registered"
    notif_body = title or "A new quality complaint has been registered and requires QA triage."

    created = 0
    for recipient_id in recipient_ids:
        if recipient_id in existing_recipients:
            continue
        notif_service.create_in_tx(
            db,
            organization_id=organization_id,
            recipient_membership_id=recipient_id,
            event_type="ComplaintRegistered",
            aggregate_type="complaints",
            aggregate_id=complaint_id,
            title=notif_title,
            body=notif_body,
        )
        created += 1
    return created


def _handle_complaint_registered(
    session_factory: sessionmaker[Session],
    event: OutboxEvent,
    tenant_ctx: Optional[TenantContext],
) -> None:
    if tenant_ctx is not None:
        from app.models.domain.complaints import Complaint

        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            complaint = uow.session.get(Complaint, event.aggregate_id)
            if complaint is not None and complaint.organization_id == tenant_ctx.organization_id:
                summary_parts = [
                    part.strip()
                    for part in (
                        complaint.reported_product_name,
                        (complaint.description or "")[:200],
                    )
                    if part and part.strip()
                ]
                fan_out_complaint_registered_notifications(
                    uow.session,
                    organization_id=tenant_ctx.organization_id,
                    complaint_id=complaint.id,
                    site_id=complaint.site_id,
                    complaint_number=complaint.complaint_number,
                    title=" — ".join(summary_parts) if summary_parts else None,
                )

    _handle_complaint_async_triage(session_factory, event, tenant_ctx)


def _handle_complaint_async_triage(
    session_factory: sessionmaker[Session],
    event: OutboxEvent,
    tenant_ctx: Optional[TenantContext],
) -> None:
    from app.services.complaint_service import ComplaintService

    ComplaintService().handle_outbox_async_triage(session_factory, event, tenant_ctx)


def _handle_outbound_communication(
    session_factory: sessionmaker[Session],
    event: OutboxEvent,
    tenant_ctx: Optional[TenantContext],
) -> None:
    from app.services.complaint_service import ComplaintService

    svc = ComplaintService()
    handler = getattr(svc, "handle_outbox_outbound_communication", None)
    if callable(handler):
        handler(session_factory, event, tenant_ctx)
        return
    logger.warning(
        "Outbound communication handler not yet available for event %s (%s:%s); skipping.",
        event.event_type,
        event.aggregate_type,
        event.aggregate_id,
    )


def _handle_inbound_email(
    session_factory: sessionmaker[Session],
    event: OutboxEvent,
    tenant_ctx: Optional[TenantContext],
) -> None:
    from app.services.complaint_service import ComplaintService

    svc = ComplaintService()
    handler = getattr(svc, "handle_outbox_inbound_email", None)
    if callable(handler):
        handler(session_factory, event, tenant_ctx)
        return
    logger.warning(
        "Inbound email handler not yet available for event %s (%s:%s); skipping.",
        event.event_type,
        event.aggregate_type,
        event.aggregate_id,
    )


def _handle_capa_overdue(
    db: Session,
    event: OutboxEvent,
    tenant_ctx: Optional[TenantContext],
) -> None:
    from app.services.qms_action_service import QMSActionService

    svc = QMSActionService()
    handler = getattr(svc, "handle_outbox_capa_overdue", None)
    if callable(handler):
        handler(db, event, tenant_ctx)
        return
    _handle_domain_event_relay(db, event, tenant_ctx)


def _handle_document_extraction(
    session_factory: sessionmaker[Session],
    event: OutboxEvent,
    tenant_ctx: Optional[TenantContext],
) -> None:
    from app.services.document_control_service import DocumentControlService

    DocumentControlService().run_extraction_job(session_factory, event, tenant_ctx)


def _handle_investigation_run(
    session_factory: sessionmaker[Session],
    event: OutboxEvent,
    tenant_ctx: Optional[TenantContext],
) -> None:
    from app.services.investigation_service import InvestigationService

    InvestigationService().handle_outbox_investigation_run(session_factory, event, tenant_ctx)


def _handle_domain_event_relay(
    db: Session,
    event: OutboxEvent,
    tenant_ctx: Optional[TenantContext],
) -> None:
    payload = event.payload_json or {}
    recipient_raw = payload.get("recipient_membership_id")
    if recipient_raw and tenant_ctx is not None:
        NotificationService().create_in_tx(
            db,
            organization_id=tenant_ctx.organization_id,
            recipient_membership_id=uuid.UUID(str(recipient_raw)),
            event_type=event.event_type,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            title=str(payload.get("title", event.event_type)),
            body=str(payload.get("body", "")),
        )
        return
    logger.info(
        "Relayed domain outbox event %s for %s:%s",
        event.event_type,
        event.aggregate_type,
        event.aggregate_id,
    )


_DEFAULT_DOMAIN_RELAY_EVENTS = (
    "ComplaintAssigned",
    "ComplaintTriaged",
    "ComplaintRouted",
    "EmailVerificationRequested",
    "OrganizationDraftCreated",
    "OrganizationSubmittedForReview",
    "OrganizationReviewed",
    "SiteReviewed",
    "PartnerUserInvited",
    "InvitationCreated",
    "ProductCreated",
    "ProductStatusChanged",
    "MaterialCreated",
    "MaterialStatusChanged",
    "SupplierLotStatusChanged",
    "BatchCreated",
    "BatchStatusChanged",
    "BatchDispositionDecided",
    "ShipmentCreated",
    "ShipmentDispatched",
    "ShipmentReceived",
    "SOPCreated",
    "SOPRevisionApproved",
    "SOPRevisionRejected",
    "SOPRevisionActivated",
    "InvestigationOpened",
    "InvestigationRunCompleted",
    "InvestigationRunWaitingHuman",
    "InvestigationRunFailed",
    "InvestigationReviewed",
    "ComplaintBatchResolved",
    "ComplaintClosed",
    "ReportGenerated",
    "ReportReviewed",
    "DeviationCreated",
    "DeviationSubmittedForReview",
    "DeviationClosed",
    "ComplaintInformationRequested",
    "OutboundCommunicationSent",
    "InboundEmailTriaged",
    "CAPACreated",
    "CAPAStatusChanged",
    "CAPAActionCompleted",
    "CAPAEffectivenessRecorded",
    "CAPAClosed",
    "CAPAOverdueNotified",
)


class OutboxWorker:
    """
    Durable outbox relay and background job processor.
    Uses PostgreSQL FOR UPDATE SKIP LOCKED claiming, lease reclamation,
    exponential backoff retries, and DEAD_LETTER quarantine.
    """

    def __init__(
        self,
        *,
        worker_id: Optional[str] = None,
        session_factory: Optional[sessionmaker[Session]] = None,
        handlers: Optional[Dict[str, OutboxHandler]] = None,
        external_handlers: Optional[Dict[str, ExternalOutboxHandler]] = None,
        lease_seconds: int = 60,
        base_backoff_seconds: int = 5,
    ) -> None:
        self.worker_id = worker_id or f"worker-{uuid.uuid4()}"
        self._session_factory = session_factory or get_session_factory()
        self._outbox_repo = OutboxRepository()
        self._lease_seconds = lease_seconds
        self._base_backoff_seconds = base_backoff_seconds
        if handlers is not None:
            self._handlers: Dict[str, OutboxHandler] = dict(handlers)
        else:
            self._handlers = {
                "NotificationRequested": _handle_notification_requested,
                "CAPAOverdueCheckRequested": _handle_capa_overdue,
                "CAPAOverdue": _handle_capa_overdue,
            }
            for event_name in _DEFAULT_DOMAIN_RELAY_EVENTS:
                self._handlers[event_name] = _handle_domain_event_relay

        self._external_handlers: Dict[str, ExternalOutboxHandler] = (
            dict(external_handlers)
            if external_handlers is not None
            else {
                "ComplaintRegistered": _handle_complaint_registered,
                "ComplaintTriageRequested": _handle_complaint_async_triage,
                "DocumentExtractionRequested": _handle_document_extraction,
                "InvestigationRunRequested": _handle_investigation_run,
                "InvestigationRunResumeRequested": _handle_investigation_run,
                "OutboundCommunicationRequested": _handle_outbound_communication,
                "InboundEmailReceived": _handle_inbound_email,
                "InboundEmailProcessRequested": _handle_inbound_email,
            }
        )

    def register_handler(self, event_type: str, handler: OutboxHandler) -> None:
        self._handlers[event_type] = handler

    def register_external_handler(self, event_type: str, handler: ExternalOutboxHandler) -> None:
        """
        Registers a handler that performs external network/LLM/OCR I/O strictly outside
        any open DB transaction (receiving `session_factory` instead of an open `Session`).
        """
        self._external_handlers[event_type] = handler

    def run_once(self, *, batch_size: int = 20) -> int:
        # 1. Reclaim any expired worker leases from crashed runs
        with UnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            reclaimed = self._outbox_repo.reclaim_expired_leases(uow.session)
            if reclaimed:
                logger.warning("Reclaimed %s expired outbox lease(s).", reclaimed)

        # 2. Claim a batch of pending/retryable events in one short transaction
        with UnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            events = self._outbox_repo.claim_pending_batch(
                uow.session,
                worker_id=self.worker_id,
                batch_size=batch_size,
                lease_seconds=self._lease_seconds,
            )
            claimed_ids = [ev.id for ev in events]

        # 3. Process each claimed event in its own UnitOfWork
        processed = 0
        for event_id in claimed_ids:
            self._process_single_event(event_id)
            processed += 1
        return processed

    def _process_single_event(self, event_id: uuid.UUID) -> None:
        try:
            ext_handler: Optional[ExternalOutboxHandler] = None
            snapshot_event: Optional[OutboxEvent] = None
            snapshot_ctx: Optional[TenantContext] = None

            with UnitOfWork(self._session_factory) as uow:
                assert uow.session is not None
                event = uow.session.get(OutboxEvent, event_id)
                if event is None:
                    return
                ext_handler = self._external_handlers.get(event.event_type)
                if ext_handler is not None:
                    snapshot_ctx = build_worker_tenant_context(event)
                    uow.session.expunge(event)
                    snapshot_event = event
                else:
                    handler = self._handlers.get(event.event_type)
                    if handler is None:
                        raise ValueError(f"No outbox handler registered for event_type={event.event_type}")
                    tenant_ctx = build_worker_tenant_context(event)
                    handler(uow.session, event, tenant_ctx)
                    self._outbox_repo.mark_published(uow.session, event.id)

            if ext_handler is not None and snapshot_event is not None:
                # External network / LLM / OCR I/O executes strictly outside any open DB transaction.
                ext_handler(self._session_factory, snapshot_event, snapshot_ctx)
                with UnitOfWork(self._session_factory) as uow:
                    assert uow.session is not None
                    self._outbox_repo.mark_published(uow.session, event_id)
        except Exception as exc:
            logger.exception("Outbox event %s failed: %s", event_id, exc)
            with UnitOfWork(self._session_factory) as uow:
                assert uow.session is not None
                self._outbox_repo.mark_failed_or_dead_letter(
                    uow.session,
                    event_id,
                    str(exc),
                    base_backoff_seconds=self._base_backoff_seconds,
                )

    def run_forever(self, *, poll_interval_seconds: float = 2.0) -> None:
        logger.info("Starting Ojasyn OutboxWorker (%s)", self.worker_id)
        while True:
            count = self.run_once()
            if count == 0:
                time.sleep(poll_interval_seconds)


if __name__ == "__main__":
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    OutboxWorker().run_forever()
