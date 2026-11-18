import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import (
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
    ComplaintSource,
    ComplaintStatus,
    CopilotSessionStatus,
    SLAStatus,
)
from app.models.domain.base import Base, TimestampVersionMixin, UUIDPKMixin, utcnow

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
        ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_complaint_comms_org_complaint",
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

    complaint: Mapped["Complaint"] = relationship(
        "Complaint", back_populates="communications", foreign_keys=[complaint_id]
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
