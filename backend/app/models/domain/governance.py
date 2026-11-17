import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.domain.enums import (
    NotificationDeliveryStatus,
    OutboxStatus,
    SecurityEventOutcome,
    SecurityEventSeverity,
)
from app.models.domain.base import Base, UUIDPKMixin, utcnow

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Notification(UUIDPKMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "recipient_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_notifications_org_recipient",
        ),
        Index("ix_notifications_recipient_seq", "recipient_membership_id", "delivery_seq", unique=True),
        Index(
            "ix_notifications_recipient_unread",
            "recipient_membership_id",
            "created_at",
            postgresql_where="read_at IS NULL",
        ),
    )

    delivery_seq: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    recipient_membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(60), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=NotificationDeliveryStatus.PENDING.value
    )


class OutboxEvent(UUIDPKMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_outbox_attempt_non_neg"),
        CheckConstraint("max_attempts >= 1", name="ck_outbox_max_attempts_pos"),
        Index(
            "ix_outbox_pending_schedule",
            "next_attempt_at",
            "occurred_at",
            postgresql_where="status IN ('PENDING', 'FAILED')",
        ),
        Index(
            "ix_outbox_processing_lease",
            "lease_expires_at",
            postgresql_where="status = 'PROCESSING'",
        ),
    )

    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    aggregate_type: Mapped[str] = mapped_column(String(60), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_json: Mapped[Dict[str, Any]] = mapped_column(JSONType, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")
    status: Mapped[str] = mapped_column(
        String(25), nullable=False, default=OutboxStatus.PENDING.value, index=True
    )
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    worker_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class AuditEvent(UUIDPKMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_org_entity_time", "organization_id", "entity_type", "entity_id", "occurred_at"),
        Index("ix_audit_events_correlation", "correlation_id"),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_audit_events_org_site",
        ),
    )

    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    site_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=True,
    )
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    before_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    after_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)


class SecurityEvent(UUIDPKMixin, Base):
    __tablename__ = "security_events"
    __table_args__ = (
        Index("ix_security_events_org_time", "organization_id", "occurred_at"),
        Index("ix_security_events_type_time", "event_type", "occurred_at"),
        Index("ix_security_events_correlation", "correlation_id"),
    )

    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    outcome: Mapped[str] = mapped_column(
        String(30), nullable=False, default=SecurityEventOutcome.DENIED.value
    )
    severity: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SecurityEventSeverity.WARNING.value
    )
    source_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    target_resource: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    details_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ApprovalRecord(UUIDPKMixin, Base):
    __tablename__ = "approval_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "approver_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_approval_records_org_approver",
        ),
        Index("ix_approval_records_entity", "organization_id", "entity_type", "entity_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    approval_stage: Mapped[str] = mapped_column(String(60), nullable=False)
    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    approver_membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
    )
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ElectronicSignature(UUIDPKMixin, Base):
    __tablename__ = "electronic_signatures"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    approval_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("approval_records.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    signer_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    meaning: Mapped[str] = mapped_column(String(120), nullable=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    auth_context: Mapped[Dict[str, Any]] = mapped_column(JSONType, nullable=False)
    signature_manifest: Mapped[Dict[str, Any]] = mapped_column(JSONType, nullable=False)
    record_hash: Mapped[str] = mapped_column(String(64), nullable=False)
