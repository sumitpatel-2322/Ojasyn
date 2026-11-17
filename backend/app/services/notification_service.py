import json
import uuid
from typing import List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.domain.enums import NotificationDeliveryStatus
from app.integrations.sse_transport import PG_NOTIFY_CHANNEL
from app.models.domain.governance import Notification
from app.repositories.governance_repo import NotificationRepository


class NotificationService:
    """Manages durable in-app notifications and PostgreSQL transactional NOTIFY alerts."""

    def __init__(self, repo: Optional[NotificationRepository] = None) -> None:
        self._repo = repo or NotificationRepository()

    def create_in_tx(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        recipient_membership_id: uuid.UUID,
        event_type: str,
        aggregate_type: str,
        aggregate_id: uuid.UUID,
        title: str,
        body: str,
    ) -> Notification:
        """
        Inserts a durable Notification inside the caller's UnitOfWork and emits
        pg_notify('ojasyn_notifications', ...) which PostgreSQL delivers only on commit.
        """
        notif = Notification(
            organization_id=organization_id,
            recipient_membership_id=recipient_membership_id,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            title=title,
            body=body,
            delivery_status=NotificationDeliveryStatus.PENDING.value,
        )
        self._repo.create(db, notif)

        if db.get_bind().dialect.name == "postgresql":
            payload = json.dumps(
                {
                    "notification_id": str(notif.id),
                    "organization_id": str(organization_id),
                    "recipient_membership_id": str(recipient_membership_id),
                    "delivery_seq": notif.delivery_seq,
                    "event_type": event_type,
                    "aggregate_type": aggregate_type,
                    "aggregate_id": str(aggregate_id),
                    "title": title,
                }
            )
            db.execute(
                text("SELECT pg_notify(:channel, :payload)"),
                {"channel": PG_NOTIFY_CHANNEL, "payload": payload},
            )
        return notif

    def list_notifications(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        recipient_membership_id: uuid.UUID,
        unread_only: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Notification], int]:
        return self._repo.list_for_recipient(
            db,
            organization_id,
            recipient_membership_id,
            unread_only=unread_only,
            page=page,
            page_size=page_size,
        )

    def unread_count(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        recipient_membership_id: uuid.UUID,
    ) -> int:
        return self._repo.count_unread(db, organization_id, recipient_membership_id)

    def mark_as_read(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        recipient_membership_id: uuid.UUID,
        notification_id: uuid.UUID,
    ) -> Notification:
        notif = self._repo.mark_read(
            db,
            organization_id,
            recipient_membership_id,
            notification_id,
        )
        if notif is None:
            raise NotFoundError("Notification not found.")
        return notif
