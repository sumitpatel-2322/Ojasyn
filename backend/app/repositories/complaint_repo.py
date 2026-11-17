import uuid
from typing import List, Optional, Tuple

from sqlalchemy import select
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
