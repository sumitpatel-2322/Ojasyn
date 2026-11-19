import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.domain.enums import (
    BatchMatchStatus,
    ClassificationSource,
    CommunicationChannel,
    CommunicationDeliveryStatus,
    CommunicationType,
    ComplaintSource,
    ComplaintStatus,
    CopilotSessionStatus,
    EmailProviderType,
    InboundEmailTriageStatus,
    SLAStatus,
)
from app.models.domain.base import Base, TenantScopedMixin, TimestampVersionMixin, UUIDPKMixin, utcnow

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Complaint(UUIDPKMixin, TimestampVersionMixin, Base):
    """
    Canonical pharmaceutical quality/customer complaint aggregate root.
    Supports both tenant-routed complaints (`RECEIVED`) and pre-routing public/guest intake (`UNROUTED_TRIAGE`).
    Strictly separates AI triage suggestions (`ai_*`) from human-verified triage decisions (`final_*`).
    """

    __tablename__ = "complaints"
    __table_args__ = (
        CheckConstraint(
            "status = 'UNROUTED_TRIAGE' OR organization_id IS NOT NULL",
            name="ck_complaints_routed_requires_org",
        ),
        UniqueConstraint("organization_id", "id", name="uq_complaints_org_id_id"),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_complaints_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "owner_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_complaints_org_owner_membership",
        ),
        ForeignKeyConstraint(
            ["organization_id", "product_id"],
            ["products.organization_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_complaints_org_product",
        ),
        Index(
            "ix_complaints_org_idempotency",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where="organization_id IS NOT NULL AND idempotency_key IS NOT NULL",
        ),
        Index(
            "ix_complaints_global_idempotency",
            "idempotency_key",
            unique=True,
            postgresql_where="organization_id IS NULL AND idempotency_key IS NOT NULL",
        ),
        Index(
            "ix_complaints_tracking_token_hash",
            "tracking_token_hash",
            unique=True,
            postgresql_where="tracking_token_hash IS NOT NULL",
        ),
        Index("ix_complaints_org_status", "organization_id", "status"),
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
        index=True,
    )
    complaint_number: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    complainant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("complainants.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    source_channel: Mapped[str] = mapped_column(
        String(30), nullable=False, default=ComplaintSource.WEBSITE.value
    )
    source_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)

    idempotency_key: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    idempotency_payload_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    tracking_token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    product_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    reported_product_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    reported_batch_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    reported_manufacturer: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    reported_strength: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reported_dosage_form: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reported_manufacturing_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    reported_expiry_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    originating_site_block: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    impacted_non_product_material: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)

    description: Mapped[str] = mapped_column(Text, nullable=False)
    defect_category: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    reported_onset_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    purchase_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    purchase_location: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    affected_quantity_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True)
    affected_quantity_unit: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    # AI vs Human triage separation
    ai_severity: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    ai_priority: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    final_severity: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    final_priority: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=ComplaintStatus.RECEIVED.value, index=True
    )
    owner_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duplicate_of_complaint_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("complaints.id", ondelete="RESTRICT"),
        nullable=True,
    )
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    classifications: Mapped[List["ComplaintClassification"]] = relationship(
        "ComplaintClassification",
        back_populates="complaint",
        foreign_keys="ComplaintClassification.complaint_id",
    )
    batch_links: Mapped[List["ComplaintBatchLink"]] = relationship(
        "ComplaintBatchLink",
        back_populates="complaint",
        foreign_keys="ComplaintBatchLink.complaint_id",
    )
    attachments: Mapped[List["ComplaintAttachment"]] = relationship(
        "ComplaintAttachment",
        back_populates="complaint",
        foreign_keys="ComplaintAttachment.complaint_id",
    )
    events: Mapped[List["ComplaintEvent"]] = relationship(
        "ComplaintEvent",
        back_populates="complaint",
        foreign_keys="ComplaintEvent.complaint_id",
    )
    assignments: Mapped[List["ComplaintAssignment"]] = relationship(
        "ComplaintAssignment",
        back_populates="complaint",
        foreign_keys="ComplaintAssignment.complaint_id",
    )
    sla_clocks: Mapped[List["SLAClock"]] = relationship(
        "SLAClock",
        back_populates="complaint",
        foreign_keys="SLAClock.complaint_id",
    )
    communications: Mapped[List["ComplaintCommunication"]] = relationship(
        "ComplaintCommunication",
        back_populates="complaint",
        foreign_keys="ComplaintCommunication.complaint_id",
    )


class ComplaintClassification(UUIDPKMixin, TimestampVersionMixin, Base):
    """Stores both AI triage suggestions (`AI_SUGGESTION`) and human triage decisions (`HUMAN_REVIEW`)."""

    __tablename__ = "complaint_classifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_complaint_classifications_org_complaint",
        ),
        ForeignKeyConstraint(
            ["organization_id", "decided_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_complaint_classifications_org_decider",
        ),
    )

    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    complaint_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("complaints.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default=ClassificationSource.AI_SUGGESTION.value
    )
    suggested_severity: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    suggested_priority: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    defect_category: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    confidence_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    risk_indicators_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    evidence_refs_json: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(JSONType, nullable=True)
    decided_by_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    override_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    complaint: Mapped["Complaint"] = relationship(
        "Complaint", back_populates="classifications", foreign_keys=[complaint_id]
    )


class ComplaintBatchLink(UUIDPKMixin, TimestampVersionMixin, Base):
    """Tenant-only link between a routed Complaint and resolved/candidate manufacturing Batch."""

    __tablename__ = "complaint_batch_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_complaint_batch_links_org_complaint",
        ),
        ForeignKeyConstraint(
            ["organization_id", "verified_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_complaint_batch_links_org_verifier",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_complaint_batch_links_org_batch",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    complaint_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("complaints.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    reported_batch_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    resolution_outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    match_method: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    match_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    match_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=BatchMatchStatus.PROPOSED.value
    )
    verified_by_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    match_evidence_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)

    complaint: Mapped["Complaint"] = relationship(
        "Complaint", back_populates="batch_links", foreign_keys=[complaint_id]
    )


class ComplaintAttachment(UUIDPKMixin, TimestampVersionMixin, Base):
    """Links an evidence Document in the central vault to a Complaint."""

    __tablename__ = "complaint_attachments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_complaint_attachments_org_complaint",
        ),
        UniqueConstraint("complaint_id", "document_id", name="uq_complaint_attachments_comp_doc"),
    )

    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    complaint_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("complaints.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    attachment_type: Mapped[str] = mapped_column(String(40), nullable=False, default="EVIDENCE")
    caption: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_external_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    uploaded_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    complaint: Mapped["Complaint"] = relationship(
        "Complaint", back_populates="attachments", foreign_keys=[complaint_id]
    )


class ComplaintEvent(UUIDPKMixin, Base):
    """Append-only domain timeline events for a complaint lifecycle."""

    __tablename__ = "complaint_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_complaint_events_org_complaint",
        ),
        Index("ix_complaint_events_comp_time", "complaint_id", "occurred_at"),
    )

    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    complaint_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("complaints.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    from_status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    to_status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    actor_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    details_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    complaint: Mapped["Complaint"] = relationship(
        "Complaint", back_populates="events", foreign_keys=[complaint_id]
    )


class ComplaintAssignment(UUIDPKMixin, TimestampVersionMixin, Base):
    """Ownership/investigator assignment history for a complaint."""

    __tablename__ = "complaint_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_complaint_assignments_org_complaint",
        ),
        ForeignKeyConstraint(
            ["organization_id", "assignee_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_complaint_assignments_org_assignee",
        ),
    )

    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    complaint_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("complaints.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    assignee_membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    assigned_by_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    assignment_role: Mapped[str] = mapped_column(String(30), nullable=False, default="OWNER")
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    unassigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    complaint: Mapped["Complaint"] = relationship(
        "Complaint", back_populates="assignments", foreign_keys=[complaint_id]
    )


class SLAClock(UUIDPKMixin, TimestampVersionMixin, Base):
    """Tracks acknowledgement, triage, and closure SLA deadlines and pauses (e.g., WAITING_INFO)."""

    __tablename__ = "sla_clocks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_sla_clocks_org_complaint",
        ),
    )

    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    complaint_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("complaints.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    sla_stage: Mapped[str] = mapped_column(String(40), nullable=False)  # TRIAGE | ACKNOWLEDGEMENT | RESOLUTION
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=SLAStatus.ON_TRACK.value, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paused_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    breached_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    accumulated_pause_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    complaint: Mapped["Complaint"] = relationship(
        "Complaint", back_populates="sla_clocks", foreign_keys=[complaint_id]
    )


class ComplaintCommunication(UUIDPKMixin, TimestampVersionMixin, Base):
    """External customer/reporter communications, information requests, and internal QA notes."""

    __tablename__ = "complaint_communications"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_complaint_comms_org_id_id"),
        ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_complaint_comms_org_complaint",
        ),
        ForeignKeyConstraint(
            ["organization_id", "in_reply_to_communication_id"],
            ["complaint_communications.organization_id", "complaint_communications.id"],
            ondelete="RESTRICT",
            name="fk_complaint_comms_org_reply",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_complaint_comms_attempt_non_neg"),
        Index("ix_complaint_comms_org_comp_sent", "organization_id", "complaint_id", "sent_at"),
        Index("ix_complaint_comms_delivery_status", "delivery_status"),
        Index("ix_complaint_comms_in_reply_to", "in_reply_to_communication_id"),
    )

    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    complaint_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("complaints.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    channel: Mapped[str] = mapped_column(
        String(30), nullable=False, default=CommunicationChannel.PORTAL.value
    )
    direction: Mapped[str] = mapped_column(String(20), nullable=False)  # INBOUND | OUTBOUND | INTERNAL
    sender_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    sender_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    subject: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_information_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    # B6 delivery, template, and reply-threading fields (0007_phase6_comm_email_capa)
    communication_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default=CommunicationType.GENERAL.value,
        server_default="GENERAL",
    )
    template_code: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    recipient_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    recipient_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    delivery_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=CommunicationDeliveryStatus.NOT_APPLICABLE.value,
        server_default="NOT_APPLICABLE",
    )
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    in_reply_to_communication_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "complaint_communications.id",
            ondelete="RESTRICT",
            name="fk_complaint_comms_in_reply_to",
        ),
        nullable=True,
    )
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)

    complaint: Mapped["Complaint"] = relationship(
        "Complaint", back_populates="communications", foreign_keys=[complaint_id]
    )
    in_reply_to: Mapped[Optional["ComplaintCommunication"]] = relationship(
        "ComplaintCommunication",
        remote_side="ComplaintCommunication.id",
        foreign_keys=[in_reply_to_communication_id],
    )


class CopilotSession(UUIDPKMixin, TimestampVersionMixin, Base):
    """Interactive conversational intake copilot session (guest token or authenticated user)."""

    __tablename__ = "copilot_sessions"
    __table_args__ = (
        Index(
            "ix_copilot_sessions_token_hash",
            "session_token_hash",
            unique=True,
            postgresql_where="session_token_hash IS NOT NULL",
        ),
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
    session_token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    draft_state_json: Mapped[Dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    session_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=CopilotSessionStatus.ACTIVE_DRAFT.value, index=True
    )
    converted_complaint_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("complaints.id", ondelete="RESTRICT"),
        nullable=True,
    )

    messages: Mapped[List["CopilotMessage"]] = relationship(
        "CopilotMessage",
        back_populates="session",
        order_by="CopilotMessage.created_at.asc()",
        foreign_keys="CopilotMessage.session_id",
    )


class CopilotMessage(UUIDPKMixin, Base):
    """Turn-by-turn message history inside a CopilotSession."""

    __tablename__ = "copilot_messages"

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("copilot_sessions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # USER | ASSISTANT | SYSTEM
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    intent_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # CREATE | EDIT | QUERY
    extracted_fields_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    confidence_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    session: Mapped["CopilotSession"] = relationship(
        "CopilotSession", back_populates="messages", foreign_keys=[session_id]
    )


class EmailConnection(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Tenant mailbox / webhook configuration for B7 inbound & outbound email intake."""

    __tablename__ = "email_connections"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_email_conns_org_id_id"),
        UniqueConstraint("organization_id", "mailbox_address", name="uq_email_conns_org_mailbox"),
        ForeignKeyConstraint(
            ["organization_id", "default_site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_email_conns_org_site",
        ),
        CheckConstraint(
            "auto_create_threshold >= 0 AND auto_create_threshold <= 1",
            name="ck_email_conns_threshold_range",
        ),
        Index("ix_email_connections_org_active", "organization_id", "is_active"),
    )

    default_site_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=EmailProviderType.MOCK.value,
        server_default="MOCK",
    )
    mailbox_address: Mapped[str] = mapped_column(String(255), nullable=False)
    inbound_secret_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=sa.true(),
    )
    auto_create_threshold: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        nullable=False,
        default=Decimal("0.7500"),
        server_default="0.7500",
    )
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_cursor: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    config_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)

    inbound_emails: Mapped[List["InboundEmail"]] = relationship(
        "InboundEmail",
        back_populates="connection",
        foreign_keys="InboundEmail.email_connection_id",
    )


class InboundEmail(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Deduplicated inbound email persisted for AI classification and human/auto triage."""

    __tablename__ = "inbound_emails"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_inbound_emails_org_id_id"),
        UniqueConstraint("organization_id", "provider_message_id", name="uq_inbound_emails_org_msg_id"),
        ForeignKeyConstraint(
            ["organization_id", "email_connection_id"],
            ["email_connections.organization_id", "email_connections.id"],
            ondelete="RESTRICT",
            name="fk_inbound_emails_org_conn",
        ),
        ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_inbound_emails_org_complaint",
        ),
        ForeignKeyConstraint(
            ["organization_id", "triaged_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_inbound_emails_org_triager",
        ),
        CheckConstraint(
            "classification_confidence IS NULL OR (classification_confidence >= 0 AND classification_confidence <= 1)",
            name="ck_inbound_emails_conf_range",
        ),
        Index(
            "ix_inbound_emails_org_triage_recv",
            "organization_id",
            "triage_status",
            "received_at",
        ),
        Index("ix_inbound_emails_org_thread", "organization_id", "thread_id"),
    )

    email_connection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("email_connections.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    provider_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    thread_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    in_reply_to_header: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    from_email: Mapped[str] = mapped_column(String(255), nullable=False)
    from_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    to_addresses_json: Mapped[Optional[Any]] = mapped_column(JSONType, nullable=True)
    cc_addresses_json: Mapped[Optional[Any]] = mapped_column(JSONType, nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    body_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body_html_sanitized: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=sa.func.now(),
    )
    classification: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    classification_confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    classification_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    triage_status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default=InboundEmailTriageStatus.RECEIVED.value,
        server_default="RECEIVED",
        index=True,
    )
    complaint_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("complaints.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    triaged_by_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    triaged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    triage_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_headers_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)

    connection: Mapped["EmailConnection"] = relationship(
        "EmailConnection",
        back_populates="inbound_emails",
        foreign_keys=[email_connection_id],
    )
    attachments: Mapped[List["EmailAttachment"]] = relationship(
        "EmailAttachment",
        back_populates="inbound_email",
        order_by="EmailAttachment.created_at.asc()",
        foreign_keys="EmailAttachment.inbound_email_id",
    )
    processing_attempts: Mapped[List["EmailProcessingAttempt"]] = relationship(
        "EmailProcessingAttempt",
        back_populates="inbound_email",
        order_by="EmailProcessingAttempt.attempt_number.asc()",
        foreign_keys="EmailProcessingAttempt.inbound_email_id",
    )


class EmailAttachment(UUIDPKMixin, TenantScopedMixin, Base):
    """Email attachment metadata linked to central Document Vault artifacts."""

    __tablename__ = "email_attachments"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_email_att_org_id_id"),
        ForeignKeyConstraint(
            ["organization_id", "inbound_email_id"],
            ["inbound_emails.organization_id", "inbound_emails.id"],
            ondelete="RESTRICT",
            name="fk_email_att_org_inbound",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_email_att_org_doc",
        ),
        CheckConstraint("byte_size >= 0", name="ck_email_att_byte_size_non_neg"),
    )

    inbound_email_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("inbound_emails.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    document_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    checksum_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    storage_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="PENDING", server_default="PENDING"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow, server_default=sa.func.now()
    )
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    inbound_email: Mapped["InboundEmail"] = relationship(
        "InboundEmail",
        back_populates="attachments",
        foreign_keys=[inbound_email_id],
    )


class EmailProcessingAttempt(UUIDPKMixin, Base):
    """Append-only audit log of inbound email classification/processing attempts."""

    __tablename__ = "email_processing_attempts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "inbound_email_id",
            "attempt_number",
            name="uq_email_proc_org_email_att",
        ),
        ForeignKeyConstraint(
            ["organization_id", "inbound_email_id"],
            ["inbound_emails.organization_id", "inbound_emails.id"],
            ondelete="RESTRICT",
            name="fk_email_proc_org_inbound",
        ),
        CheckConstraint("attempt_number >= 1", name="ck_email_proc_attempt_pos"),
        Index("ix_email_proc_attempts_organization_id", "organization_id"),
        Index("ix_email_proc_attempts_inbound_email_id", "inbound_email_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    inbound_email_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("inbound_emails.id", ondelete="RESTRICT"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    classifier_version: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=sa.func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=sa.func.now()
    )

    inbound_email: Mapped["InboundEmail"] = relationship(
        "InboundEmail",
        back_populates="processing_attempts",
        foreign_keys=[inbound_email_id],
    )
