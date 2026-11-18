import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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

from app.models.domain.base import Base, TenantScopedMixin, TimestampVersionMixin, UUIDPKMixin

JSONType = JSON().with_variant(JSONB(), "postgresql")


class SOP(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Standard Operating Procedure master record (`C5`)."""

    __tablename__ = "sops"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_sops_org_id_id"),
        UniqueConstraint("organization_id", "sop_code", name="uq_sops_org_code"),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_sops_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "department_id"],
            ["departments.organization_id", "departments.id"],
            ondelete="RESTRICT",
            name="fk_sops_org_dept",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_sops_org_doc",
        ),
        ForeignKeyConstraint(
            ["organization_id", "owner_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_sops_org_owner",
        ),
    )

    site_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    applicable_site_ids: Mapped[Optional[List[str]]] = mapped_column(
        JSONType,
        nullable=True,
    )
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    sop_code: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    owner_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    current_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "sop_revisions.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_sops_current_revision_id",
        ),
        nullable=True,
    )
    sop_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="DRAFT",
        index=True,
    )

    revisions: Mapped[List["SOPRevision"]] = relationship(
        "SOPRevision",
        back_populates="sop",
        foreign_keys="SOPRevision.sop_id",
        order_by="SOPRevision.revision_sequence.asc()",
    )


class SOPRevision(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Versioned, effective-dated SOP revision with dual maker-checker approval (`C5`)."""

    __tablename__ = "sop_revisions"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_sop_revisions_org_id_id"),
        UniqueConstraint(
            "organization_id",
            "sop_id",
            "revision_number",
            name="uq_sop_revisions_org_sop_revnum",
        ),
        ForeignKeyConstraint(
            ["organization_id", "sop_id"],
            ["sops.organization_id", "sops.id"],
            ondelete="RESTRICT",
            name="fk_sop_revisions_org_sop",
        ),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_sop_revisions_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_sop_revisions_org_doc",
        ),
        ForeignKeyConstraint(
            ["organization_id", "submitted_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_sop_revisions_org_submitted_by",
        ),
        ForeignKeyConstraint(
            ["organization_id", "approved_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_sop_revisions_org_approved_by",
        ),
        ForeignKeyConstraint(
            ["organization_id", "activated_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_sop_revisions_org_activated_by",
        ),
        ForeignKeyConstraint(
            ["organization_id", "supersedes_revision_id"],
            ["sop_revisions.organization_id", "sop_revisions.id"],
            ondelete="RESTRICT",
            name="fk_sop_revisions_org_supersedes",
        ),
        ForeignKeyConstraint(
            ["organization_id", "superseded_by_revision_id"],
            ["sop_revisions.organization_id", "sop_revisions.id"],
            ondelete="RESTRICT",
            name="fk_sop_revisions_org_superseded_by",
        ),
        CheckConstraint("revision_sequence >= 1", name="ck_sop_revisions_seq_pos"),
        CheckConstraint(
            "effective_to IS NULL OR (effective_from IS NOT NULL AND effective_to > effective_from)",
            name="ck_sop_revisions_effective_window",
        ),
        CheckConstraint(
            "approved_by_user_id IS NULL OR created_by_user_id IS NULL OR approved_by_user_id != created_by_user_id",
            name="ck_sop_revisions_maker_checker_user",
        ),
        CheckConstraint(
            "approved_by_membership_id IS NULL OR submitted_by_membership_id IS NULL OR approved_by_membership_id != submitted_by_membership_id",
            name="ck_sop_revisions_maker_checker_member",
        ),
    )

    sop_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sops.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    site_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    revision_number: Mapped[str] = mapped_column(String(40), nullable=False)
    revision_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    revision_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    change_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    document_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    effective_from: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    effective_to: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    revision_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="DRAFT",
        index=True,
    )
    approval_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="DRAFT",
    )
    submitted_by_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    approved_by_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    approval_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("approval_records.id", ondelete="RESTRICT"),
        nullable=True,
    )
    activated_by_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    activated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    supersedes_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sop_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    superseded_by_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sop_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )

    sop: Mapped["SOP"] = relationship(
        "SOP",
        back_populates="revisions",
        foreign_keys=[sop_id],
    )
    steps: Mapped[List["SOPStep"]] = relationship(
        "SOPStep",
        back_populates="revision",
        foreign_keys="SOPStep.sop_revision_id",
        order_by="SOPStep.step_sequence.asc()",
    )


class SOPStep(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Structured step requirement and process parameter acceptance limits on an SOP revision (`C5`)."""

    __tablename__ = "sop_steps"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_sop_steps_org_id_id"),
        UniqueConstraint(
            "organization_id",
            "sop_revision_id",
            "step_code",
            name="uq_sop_steps_org_rev_code",
        ),
        UniqueConstraint(
            "organization_id",
            "sop_revision_id",
            "step_sequence",
            name="uq_sop_steps_org_rev_seq",
        ),
        ForeignKeyConstraint(
            ["organization_id", "sop_revision_id"],
            ["sop_revisions.organization_id", "sop_revisions.id"],
            ondelete="RESTRICT",
            name="fk_sop_steps_org_revision",
        ),
        CheckConstraint("step_sequence >= 1", name="ck_sop_steps_seq_pos"),
        CheckConstraint(
            "min_value IS NULL OR max_value IS NULL OR min_value <= max_value",
            name="ck_sop_steps_min_lte_max",
        ),
    )

    sop_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sop_revisions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    step_code: Mapped[str] = mapped_column(String(60), nullable=False)
    step_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    process_stage: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    parameter_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    parameter_code: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    expected_data_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="NONE",
    )
    unit_code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    min_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    max_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    target_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    expected_text_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    allowed_values_json: Mapped[Optional[List[Any]]] = mapped_column(JSONType, nullable=True)
    acceptance_criteria: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requires_operator_signoff: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    requires_checker_signoff: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_qa_signoff: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    required_equipment_category: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    required_equipment_code: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)

    revision: Mapped["SOPRevision"] = relationship(
        "SOPRevision",
        back_populates="steps",
        foreign_keys=[sop_revision_id],
    )
