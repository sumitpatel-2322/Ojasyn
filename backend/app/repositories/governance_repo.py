import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.enums import NotificationDeliveryStatus, OutboxStatus
from app.models.domain.governance import AuditEvent, Notification, OutboxEvent, SecurityEvent
from app.repositories.base import paginate


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditRepository:
    def create(self, db: Session, event: AuditEvent) -> AuditEvent:
        db.add(event)
        db.flush()
        return event

    def list_by_organization(
        self,
        db: Session,
        organization_id: uuid.UUID,
        *,
        entity_type: Optional[str] = None,
        entity_id: Optional[uuid.UUID] = None,
        correlation_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[AuditEvent], int]:
        stmt = select(AuditEvent).where(AuditEvent.organization_id == organization_id)
        if entity_type:
            stmt = stmt.where(AuditEvent.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(AuditEvent.entity_id == entity_id)
        if correlation_id:
            stmt = stmt.where(AuditEvent.correlation_id == correlation_id)
        stmt = stmt.order_by(AuditEvent.occurred_at.desc())
        return paginate(db, stmt, page=page, page_size=page_size)


class SecurityEventRepository:
    def create(self, db: Session, event: SecurityEvent) -> SecurityEvent:
        db.add(event)
        db.flush()
        return event


class OutboxRepository:
    def enqueue(
        self,
        db: Session,
        *,
        organization_id: Optional[uuid.UUID],
        aggregate_type: str,
        aggregate_id: uuid.UUID,
        event_type: str,
        payload_json: Dict[str, Any],
        max_attempts: int = 5,
        next_attempt_at: Optional[datetime] = None,
    ) -> OutboxEvent:
        now = _utcnow()
        event = OutboxEvent(
            organization_id=organization_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload_json=payload_json,
            occurred_at=now,
            attempt_count=0,
            max_attempts=max_attempts,
            status=OutboxStatus.PENDING.value,
            next_attempt_at=next_attempt_at or now,
        )
        db.add(event)
        db.flush()
        return event

    def claim_pending_batch(
        self,
        db: Session,
        *,
        worker_id: str,
        batch_size: int = 20,
        lease_seconds: int = 60,
    ) -> List[OutboxEvent]:
        now = _utcnow()
        stmt = (
            select(OutboxEvent)
            .where(
                OutboxEvent.status.in_([OutboxStatus.PENDING.value, OutboxStatus.FAILED.value]),
                OutboxEvent.next_attempt_at <= now,
            )
            .order_by(OutboxEvent.occurred_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        events = list(db.scalars(stmt).all())
        lease_until = now + timedelta(seconds=lease_seconds)
        for ev in events:
            ev.status = OutboxStatus.PROCESSING.value
            ev.worker_id = worker_id
            ev.lease_expires_at = lease_until
        db.flush()
        return events

    def mark_published(self, db: Session, event_id: uuid.UUID) -> Optional[OutboxEvent]:
        ev = db.get(OutboxEvent, event_id)
        if ev is None:
            return None
        ev.status = OutboxStatus.PUBLISHED.value
        ev.published_at = _utcnow()
        ev.worker_id = None
        ev.lease_expires_at = None
        db.flush()
        return ev

    def mark_failed_or_dead_letter(
        self,
        db: Session,
        event_id: uuid.UUID,
        error_message: str,
        *,
        base_backoff_seconds: int = 5,
    ) -> Optional[OutboxEvent]:
        ev = db.get(OutboxEvent, event_id)
        if ev is None:
            return None
        now = _utcnow()
        ev.attempt_count += 1
        ev.last_error = error_message[:2000]
        ev.worker_id = None
        ev.lease_expires_at = None
        if ev.attempt_count >= ev.max_attempts:
            ev.status = OutboxStatus.DEAD_LETTER.value
        else:
            delay_seconds = base_backoff_seconds * (2 ** (ev.attempt_count - 1))
            ev.status = OutboxStatus.FAILED.value
            ev.next_attempt_at = now + timedelta(seconds=delay_seconds)
        db.flush()
        return ev

    def reclaim_expired_leases(self, db: Session) -> int:
        now = _utcnow()
        stmt = (
            update(OutboxEvent)
            .where(
                OutboxEvent.status == OutboxStatus.PROCESSING.value,
                OutboxEvent.lease_expires_at.is_not(None),
                OutboxEvent.lease_expires_at < now,
            )
            .values(
                status=OutboxStatus.FAILED.value,
                worker_id=None,
                lease_expires_at=None,
                next_attempt_at=now,
                last_error="Worker lease expired; reclaimed for retry.",
            )
        )
        result = db.execute(stmt)
        db.flush()
        return int(result.rowcount or 0)


class NotificationRepository:
    def create(self, db: Session, notification: Notification) -> Notification:
        db.add(notification)
        db.flush()
        return notification

    def list_for_recipient(
        self,
        db: Session,
        organization_id: uuid.UUID,
        recipient_membership_id: uuid.UUID,
        *,
        unread_only: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Notification], int]:
        stmt = select(Notification).where(
            Notification.organization_id == organization_id,
            Notification.recipient_membership_id == recipient_membership_id,
        )
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        stmt = stmt.order_by(Notification.delivery_seq.desc())
        return paginate(db, stmt, page=page, page_size=page_size)

    def count_unread(
        self,
        db: Session,
        organization_id: uuid.UUID,
        recipient_membership_id: uuid.UUID,
    ) -> int:
        from sqlalchemy import func

        stmt = select(func.count(Notification.id)).where(
            Notification.organization_id == organization_id,
            Notification.recipient_membership_id == recipient_membership_id,
            Notification.read_at.is_(None),
        )
        return int(db.scalar(stmt) or 0)

    def list_since_seq(
        self,
        db: Session,
        organization_id: uuid.UUID,
        recipient_membership_id: uuid.UUID,
        *,
        after_seq: int = 0,
        limit: int = 100,
    ) -> List[Notification]:
        stmt = (
            select(Notification)
            .where(
                Notification.organization_id == organization_id,
                Notification.recipient_membership_id == recipient_membership_id,
                Notification.delivery_seq > after_seq,
            )
            .order_by(Notification.delivery_seq.asc())
            .limit(limit)
        )
        return list(db.scalars(stmt).all())

    def mark_read(
        self,
        db: Session,
        organization_id: uuid.UUID,
        recipient_membership_id: uuid.UUID,
        notification_id: uuid.UUID,
    ) -> Optional[Notification]:
        notif = db.scalar(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.organization_id == organization_id,
                Notification.recipient_membership_id == recipient_membership_id,
            )
        )
        if notif is None:
            return None
        if notif.read_at is None:
            notif.read_at = _utcnow()
            notif.delivery_status = NotificationDeliveryStatus.DELIVERED.value
            db.flush()
        return notif
