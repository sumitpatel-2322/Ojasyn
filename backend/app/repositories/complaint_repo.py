import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import SLAStatus
from app.models.domain.complaints import (
    Complaint,
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
from app.repositories.base import apply_tenant_filter, paginate


class ComplaintRepository:
    """Data access for Complaints, Classifications, BatchLinks, SLA Clocks, Communications & Copilot Sessions."""

    def get_by_id(self, db: Session, complaint_id: uuid.UUID) -> Optional[Complaint]:
        return db.get(Complaint, complaint_id)

    def get_for_org(
        self, db: Session, organization_id: uuid.UUID, complaint_id: uuid.UUID
    ) -> Optional[Complaint]:
        return db.scalar(
            select(Complaint).where(
                Complaint.organization_id == organization_id,
                Complaint.id == complaint_id,
            )
        )

    def get_by_tracking_token_hash(self, db: Session, token_hash: str) -> Optional[Complaint]:
        return db.scalar(select(Complaint).where(Complaint.tracking_token_hash == token_hash))

    def get_by_idempotency(
        self, db: Session, organization_id: Optional[uuid.UUID], idempotency_key: str
    ) -> Optional[Complaint]:
        stmt = select(Complaint).where(Complaint.idempotency_key == idempotency_key)
        if organization_id is None:
            stmt = stmt.where(Complaint.organization_id.is_(None))
        else:
            stmt = stmt.where(Complaint.organization_id == organization_id)
        return db.scalar(stmt)

    def list_for_tenant(
        self,
        db: Session,
        tenant_ctx: object,
        *,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        source_channel: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Complaint], int]:
        stmt = select(Complaint)
        stmt = apply_tenant_filter(stmt, Complaint, tenant_ctx, allow_org_wide_site_null=True)
        if status:
            stmt = stmt.where(Complaint.status == status)
        if severity:
            stmt = stmt.where(Complaint.final_severity == severity)
        if source_channel:
            stmt = stmt.where(Complaint.source_channel == source_channel)
        stmt = stmt.order_by(Complaint.created_at.desc())
        return paginate(db, stmt, page=page, page_size=page_size)

    def list_unrouted(
        self,
        db: Session,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Complaint], int]:
        stmt = (
            select(Complaint)
            .where(Complaint.organization_id.is_(None), Complaint.status == "UNROUTED_TRIAGE")
            .order_by(Complaint.created_at.asc())
        )
        return paginate(db, stmt, page=page, page_size=page_size)

    def get_active_sla_clocks(self, db: Session, complaint_id: uuid.UUID) -> List[SLAClock]:
        return list(
            db.scalars(
                select(SLAClock).where(
                    SLAClock.complaint_id == complaint_id,
                    SLAClock.status.in_(
                        [SLAStatus.ON_TRACK.value, SLAStatus.AT_RISK.value, SLAStatus.PAUSED.value]
                    ),
                )
            ).all()
        )

    def get_batch_link_for_org(
        self, db: Session, organization_id: uuid.UUID, complaint_id: uuid.UUID, link_id: uuid.UUID
    ) -> Optional[ComplaintBatchLink]:
        return db.scalar(
            select(ComplaintBatchLink).where(
                ComplaintBatchLink.organization_id == organization_id,
                ComplaintBatchLink.complaint_id == complaint_id,
                ComplaintBatchLink.id == link_id,
            )
        )

    def list_attachments(self, db: Session, complaint_id: uuid.UUID) -> List[ComplaintAttachment]:
        return list(
            db.scalars(
                select(ComplaintAttachment)
                .where(ComplaintAttachment.complaint_id == complaint_id)
                .order_by(ComplaintAttachment.created_at.asc())
            ).all()
        )

    def list_public_events(self, db: Session, complaint_id: uuid.UUID) -> List[ComplaintEvent]:
        return list(
            db.scalars(
                select(ComplaintEvent)
                .where(ComplaintEvent.complaint_id == complaint_id)
                .order_by(ComplaintEvent.occurred_at.asc())
            ).all()
        )

    def list_public_communications(
        self, db: Session, complaint_id: uuid.UUID
    ) -> List[ComplaintCommunication]:
        return list(
            db.scalars(
                select(ComplaintCommunication)
                .where(
                    ComplaintCommunication.complaint_id == complaint_id,
                    ComplaintCommunication.is_internal_only.is_(False),
                )
                .order_by(ComplaintCommunication.sent_at.asc())
            ).all()
        )

    def get_copilot_session(self, db: Session, session_id: uuid.UUID) -> Optional[CopilotSession]:
        return db.get(CopilotSession, session_id)

    def list_copilot_messages(self, db: Session, session_id: uuid.UUID) -> List[CopilotMessage]:
        return list(
            db.scalars(
                select(CopilotMessage)
                .where(CopilotMessage.session_id == session_id)
                .order_by(CopilotMessage.created_at.asc())
            ).all()
        )

    # --- B6 Complaint Communications ---

    def list_communications(
        self,
        db: Session,
        organization_id: uuid.UUID,
        complaint_id: uuid.UUID,
        *,
        include_internal: bool = True,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[ComplaintCommunication], int]:
        stmt = select(ComplaintCommunication).where(
            ComplaintCommunication.organization_id == organization_id,
            ComplaintCommunication.complaint_id == complaint_id,
        )
        if not include_internal:
            stmt = stmt.where(ComplaintCommunication.is_internal_only.is_(False))
        stmt = stmt.order_by(ComplaintCommunication.sent_at.asc(), ComplaintCommunication.created_at.asc())
        return paginate(db, stmt, page=page, page_size=page_size)

    def get_communication_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        complaint_id: uuid.UUID,
        communication_id: uuid.UUID,
    ) -> Optional[ComplaintCommunication]:
        return db.scalar(
            select(ComplaintCommunication).where(
                ComplaintCommunication.organization_id == organization_id,
                ComplaintCommunication.complaint_id == complaint_id,
                ComplaintCommunication.id == communication_id,
            )
        )

    def get_communication_by_id(
        self, db: Session, communication_id: uuid.UUID
    ) -> Optional[ComplaintCommunication]:
        return db.get(ComplaintCommunication, communication_id)

    # --- B7 Email Connections, Inbound Emails, Attachments & Processing Attempts ---

    def get_email_connection_by_id(
        self, db: Session, connection_id: uuid.UUID
    ) -> Optional[EmailConnection]:
        return db.get(EmailConnection, connection_id)

    def get_email_connection_for_org(
        self, db: Session, organization_id: uuid.UUID, connection_id: uuid.UUID
    ) -> Optional[EmailConnection]:
        return db.scalar(
            select(EmailConnection).where(
                EmailConnection.organization_id == organization_id,
                EmailConnection.id == connection_id,
            )
        )

    def get_email_connection_by_mailbox(
        self, db: Session, organization_id: uuid.UUID, mailbox_address: str
    ) -> Optional[EmailConnection]:
        return db.scalar(
            select(EmailConnection).where(
                EmailConnection.organization_id == organization_id,
                EmailConnection.mailbox_address == mailbox_address,
            )
        )

    def get_active_email_connection_for_org(
        self, db: Session, organization_id: uuid.UUID, site_id: Optional[uuid.UUID] = None
    ) -> Optional[EmailConnection]:
        stmt = select(EmailConnection).where(
            EmailConnection.organization_id == organization_id,
            EmailConnection.is_active.is_(True),
        )
        if site_id is not None:
            stmt = stmt.where(
                (EmailConnection.default_site_id == site_id) | (EmailConnection.default_site_id.is_(None))
            ).order_by(EmailConnection.default_site_id.is_(None).asc(), EmailConnection.created_at.asc())
        else:
            stmt = stmt.order_by(EmailConnection.created_at.asc())
        return db.scalars(stmt).first()

    def list_email_connections(
        self,
        db: Session,
        tenant_ctx: object,
        *,
        is_active: Optional[bool] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[EmailConnection], int]:
        org_id = getattr(tenant_ctx, "organization_id")
        stmt = select(EmailConnection).where(EmailConnection.organization_id == org_id)
        if not getattr(tenant_ctx, "is_org_wide", False):
            allowed_sites = list(getattr(tenant_ctx, "allowed_site_ids", ()) or ())
            if allowed_sites:
                stmt = stmt.where(
                    (EmailConnection.default_site_id.is_(None))
                    | (EmailConnection.default_site_id.in_(allowed_sites))
                )
            else:
                stmt = stmt.where(EmailConnection.default_site_id.is_(None))
        if is_active is not None:
            stmt = stmt.where(EmailConnection.is_active.is_(is_active))
        stmt = stmt.order_by(EmailConnection.created_at.desc())
        return paginate(db, stmt, page=page, page_size=page_size)

    def get_inbound_email_by_id(
        self, db: Session, inbound_email_id: uuid.UUID
    ) -> Optional[InboundEmail]:
        return db.get(InboundEmail, inbound_email_id)

    def get_inbound_email_for_org(
        self, db: Session, organization_id: uuid.UUID, inbound_email_id: uuid.UUID
    ) -> Optional[InboundEmail]:
        return db.scalar(
            select(InboundEmail).where(
                InboundEmail.organization_id == organization_id,
                InboundEmail.id == inbound_email_id,
            )
        )

    def get_inbound_email_by_provider_msg_id(
        self, db: Session, organization_id: uuid.UUID, provider_message_id: str
    ) -> Optional[InboundEmail]:
        return db.scalar(
            select(InboundEmail).where(
                InboundEmail.organization_id == organization_id,
                InboundEmail.provider_message_id == provider_message_id,
            )
        )

    def list_inbound_emails(
        self,
        db: Session,
        tenant_ctx: object,
        *,
        triage_status: Optional[str] = None,
        email_connection_id: Optional[uuid.UUID] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[InboundEmail], int]:
        org_id = getattr(tenant_ctx, "organization_id")
        stmt = (
            select(InboundEmail)
            .join(EmailConnection, EmailConnection.id == InboundEmail.email_connection_id)
            .where(InboundEmail.organization_id == org_id)
        )
        if not getattr(tenant_ctx, "is_org_wide", False):
            allowed_sites = list(getattr(tenant_ctx, "allowed_site_ids", ()) or ())
            if allowed_sites:
                stmt = stmt.where(
                    (EmailConnection.default_site_id.is_(None))
                    | (EmailConnection.default_site_id.in_(allowed_sites))
                )
            else:
                stmt = stmt.where(EmailConnection.default_site_id.is_(None))
        if triage_status:
            stmt = stmt.where(InboundEmail.triage_status == triage_status)
        if email_connection_id is not None:
            stmt = stmt.where(InboundEmail.email_connection_id == email_connection_id)
        stmt = stmt.order_by(InboundEmail.received_at.desc(), InboundEmail.created_at.desc())
        return paginate(db, stmt, page=page, page_size=page_size)

    def list_email_attachments(
        self, db: Session, organization_id: uuid.UUID, inbound_email_id: uuid.UUID
    ) -> List[EmailAttachment]:
        return list(
            db.scalars(
                select(EmailAttachment)
                .where(
                    EmailAttachment.organization_id == organization_id,
                    EmailAttachment.inbound_email_id == inbound_email_id,
                )
                .order_by(EmailAttachment.created_at.asc())
            ).all()
        )

    def get_next_processing_attempt_number(
        self, db: Session, organization_id: uuid.UUID, inbound_email_id: uuid.UUID
    ) -> int:
        max_attempt = db.scalar(
            select(func.coalesce(func.max(EmailProcessingAttempt.attempt_number), 0)).where(
                EmailProcessingAttempt.organization_id == organization_id,
                EmailProcessingAttempt.inbound_email_id == inbound_email_id,
            )
        )
        return int(max_attempt or 0) + 1
