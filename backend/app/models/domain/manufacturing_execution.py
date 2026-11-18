import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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

from app.models.domain.base import Base, TenantScopedMixin, TimestampVersionMixin, UUIDPKMixin, utcnow

JSONType = JSON().with_variant(JSONB(), "postgresql")


class MasterBatchRecord(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Master Batch Record (MBR) / Master Packaging Record template for a Product at a Site (`C4`)."""

    __tablename__ = "master_batch_records"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_mbrs_org_id_id"),
        UniqueConstraint(
            "organization_id",
            "site_id",
            "product_id",
            "mbr_code",
            "version_number",
            name="uq_mbrs_org_site_prod_code_ver",
        ),
        CheckConstraint(
            "target_batch_size_value IS NULL OR target_batch_size_value > 0",
            name="ck_mbrs_target_size_pos",
        ),
        CheckConstraint(
            "effective_to IS NULL OR (effective_from IS NOT NULL AND effective_to > effective_from)",
            name="ck_mbrs_effective_dates",
        ),
        CheckConstraint(
            "approved_by_user_id IS NULL OR created_by_user_id IS NULL OR approved_by_user_id != created_by_user_id",
            name="ck_mbrs_maker_checker_user",
        ),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_mbrs_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "product_id"],
            ["products.organization_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_mbrs_org_product",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_mbrs_org_document",
        ),
        ForeignKeyConstraint(
            ["organization_id", "approved_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_mbrs_org_approver",
        ),
        ForeignKeyConstraint(
            ["organization_id", "supersedes_mbr_id"],
            ["master_batch_records.organization_id", "master_batch_records.id"],
            ondelete="RESTRICT",
            name="fk_mbrs_org_supersedes",
        ),
        Index("ix_mbrs_org_prod_status", "organization_id", "product_id", "approval_status"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    mbr_code: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    version_number: Mapped[str] = mapped_column(String(40), nullable=False)
    product_version_context: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    record_type: Mapped[str] = mapped_column(String(20), nullable=False, default="BMR")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_batch_size_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4), nullable=True)
    target_batch_size_unit: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
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
    applicable_sop_revision_ids: Mapped[List[str]] = mapped_column(
        JSONType, nullable=False, default=list
    )
    planned_steps_json: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(JSONType, nullable=True)
    effective_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="DRAFT", index=True
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
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("approval_records.id", ondelete="RESTRICT"),
        nullable=True,
    )
    supersedes_mbr_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("master_batch_records.id", ondelete="RESTRICT"),
        nullable=True,
    )


class BatchExecutionRecord(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Executed BMR / BPR stage record for a manufacturing Batch (`C4`)."""

    __tablename__ = "batch_execution_records"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_batch_exec_org_id_id"),
        UniqueConstraint(
            "organization_id",
            "batch_id",
            "execution_code",
            name="uq_batch_exec_org_batch_code",
        ),
        UniqueConstraint(
            "organization_id",
            "batch_id",
            "execution_sequence",
            name="uq_batch_exec_org_batch_seq",
        ),
        CheckConstraint("execution_sequence >= 1", name="ck_batch_exec_seq_pos"),
        CheckConstraint(
            "completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at)",
            name="ck_batch_exec_dates",
        ),
        CheckConstraint(
            "checker_membership_id IS NULL OR checker_membership_id != operator_membership_id",
            name="ck_batch_exec_maker_checker_member",
        ),
        CheckConstraint(
            "checker_user_id IS NULL OR operator_user_id IS NULL OR checker_user_id != operator_user_id",
            name="ck_batch_exec_maker_checker_user",
        ),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_org_batch",
        ),
        ForeignKeyConstraint(
            ["organization_id", "master_batch_record_id"],
            ["master_batch_records.organization_id", "master_batch_records.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_org_mbr",
        ),
        ForeignKeyConstraint(
            ["organization_id", "operator_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_org_operator",
        ),
        ForeignKeyConstraint(
            ["organization_id", "checker_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_org_checker",
        ),
        ForeignKeyConstraint(
            ["organization_id", "approved_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_org_approver",
        ),
        ForeignKeyConstraint(
            ["organization_id", "primary_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_org_primary_doc",
        ),
        Index("ix_batch_exec_org_batch_status", "organization_id", "batch_id", "execution_status"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    master_batch_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("master_batch_records.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    execution_code: Mapped[str] = mapped_column(String(80), nullable=False)
    execution_type: Mapped[str] = mapped_column(String(20), nullable=False, default="BMR")
    manufacturing_stage: Mapped[str] = mapped_column(String(100), nullable=False)
    execution_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="DRAFT", index=True
    )
    operator_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    operator_membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
    )
    checker_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    checker_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("approval_records.id", ondelete="RESTRICT"),
        nullable=True,
    )
    primary_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    primary_document_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    supporting_document_version_ids: Mapped[List[str]] = mapped_column(
        JSONType, nullable=False, default=list
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    steps: Mapped[List["BatchExecutionStep"]] = relationship(
        "BatchExecutionStep",
        back_populates="execution_record",
        foreign_keys="BatchExecutionStep.batch_execution_record_id",
        order_by="BatchExecutionStep.step_sequence.asc()",
    )
    observations: Mapped[List["ProcessParameterObservation"]] = relationship(
        "ProcessParameterObservation",
        back_populates="execution_record",
        foreign_keys="ProcessParameterObservation.batch_execution_record_id",
        order_by="ProcessParameterObservation.observed_at.asc()",
    )


class BatchExecutionStep(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Granular operational step within a BatchExecutionRecord linked to SOP step & equipment (`C4`)."""

    __tablename__ = "batch_execution_steps"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_batch_exec_steps_org_id_id"),
        UniqueConstraint(
            "organization_id",
            "batch_execution_record_id",
            "step_code",
            name="uq_batch_exec_steps_org_rec_code",
        ),
        UniqueConstraint(
            "organization_id",
            "batch_execution_record_id",
            "step_sequence",
            name="uq_batch_exec_steps_org_rec_seq",
        ),
        CheckConstraint("step_sequence >= 1", name="ck_batch_exec_steps_seq_pos"),
        CheckConstraint(
            "checker_membership_id IS NULL OR operator_membership_id IS NULL OR checker_membership_id != operator_membership_id",
            name="ck_batch_exec_steps_maker_checker_member",
        ),
        CheckConstraint(
            "checker_user_id IS NULL OR operator_user_id IS NULL OR checker_user_id != operator_user_id",
            name="ck_batch_exec_steps_maker_checker_user",
        ),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_steps_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_execution_record_id"],
            ["batch_execution_records.organization_id", "batch_execution_records.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_steps_org_rec",
        ),
        ForeignKeyConstraint(
            ["organization_id", "sop_id"],
            ["sops.organization_id", "sops.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_steps_org_sop",
        ),
        ForeignKeyConstraint(
            ["organization_id", "sop_revision_id"],
            ["sop_revisions.organization_id", "sop_revisions.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_steps_org_sop_rev",
        ),
        ForeignKeyConstraint(
            ["organization_id", "sop_step_id"],
            ["sop_steps.organization_id", "sop_steps.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_steps_org_sop_step",
        ),
        ForeignKeyConstraint(
            ["organization_id", "operator_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_steps_org_operator",
        ),
        ForeignKeyConstraint(
            ["organization_id", "checker_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_steps_org_checker",
        ),
        ForeignKeyConstraint(
            ["organization_id", "equipment_id"],
            ["equipment.organization_id", "equipment.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_steps_org_equip",
        ),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    batch_execution_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batch_execution_records.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    step_code: Mapped[str] = mapped_column(String(60), nullable=False)
    step_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    sop_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sops.id", ondelete="RESTRICT"),
        nullable=True,
    )
    sop_code: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    sop_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sop_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    sop_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sop_steps.id", ondelete="RESTRICT"),
        nullable=True,
    )
    planned_requirement: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requires_checker_signoff: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    step_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="PENDING", index=True
    )
    operator_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    operator_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    checker_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    checker_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    equipment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("equipment.id", ondelete="RESTRICT"),
        nullable=True,
    )
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    exception_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    supporting_document_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    supporting_evidence_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)

    execution_record: Mapped["BatchExecutionRecord"] = relationship(
        "BatchExecutionRecord",
        back_populates="steps",
        foreign_keys=[batch_execution_record_id],
    )
    observations: Mapped[List["ProcessParameterObservation"]] = relationship(
        "ProcessParameterObservation",
        back_populates="execution_step",
        foreign_keys="ProcessParameterObservation.batch_execution_step_id",
        order_by="ProcessParameterObservation.observed_at.asc()",
    )


class ProcessParameterObservation(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Typed in-process parameter observation recorded during a BatchExecutionStep (`C4`)."""

    __tablename__ = "process_parameter_observations"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_param_obs_org_id_id"),
        CheckConstraint(
            "(value_type = 'NUMERIC' AND numeric_value IS NOT NULL) OR "
            "(value_type = 'TEXT' AND text_value IS NOT NULL) OR "
            "(value_type = 'BOOLEAN' AND boolean_value IS NOT NULL)",
            name="ck_param_obs_typed_val",
        ),
        CheckConstraint(
            "source_page_number IS NULL OR source_page_number >= 1",
            name="ck_param_obs_page_pos",
        ),
        CheckConstraint(
            "extraction_confidence IS NULL OR (extraction_confidence >= 0 AND extraction_confidence <= 1)",
            name="ck_param_obs_conf_range",
        ),
        CheckConstraint(
            "verified_by_membership_id IS NULL OR verified_by_membership_id != operator_membership_id",
            name="ck_param_obs_maker_checker_member",
        ),
        CheckConstraint(
            "verified_by_user_id IS NULL OR operator_user_id IS NULL OR verified_by_user_id != operator_user_id",
            name="ck_param_obs_maker_checker_user",
        ),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_param_obs_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_execution_record_id"],
            ["batch_execution_records.organization_id", "batch_execution_records.id"],
            ondelete="RESTRICT",
            name="fk_param_obs_org_exec_rec",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_execution_step_id"],
            ["batch_execution_steps.organization_id", "batch_execution_steps.id"],
            ondelete="RESTRICT",
            name="fk_param_obs_org_exec_step",
        ),
        ForeignKeyConstraint(
            ["organization_id", "sop_step_id"],
            ["sop_steps.organization_id", "sop_steps.id"],
            ondelete="RESTRICT",
            name="fk_param_obs_org_sop_step",
        ),
        ForeignKeyConstraint(
            ["organization_id", "equipment_id"],
            ["equipment.organization_id", "equipment.id"],
            ondelete="RESTRICT",
            name="fk_param_obs_org_equip",
        ),
        ForeignKeyConstraint(
            ["organization_id", "operator_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_param_obs_org_operator",
        ),
        ForeignKeyConstraint(
            ["organization_id", "verified_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_param_obs_org_verifier",
        ),
        ForeignKeyConstraint(
            ["organization_id", "source_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_param_obs_org_source_doc",
        ),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    batch_execution_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batch_execution_records.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    batch_execution_step_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batch_execution_steps.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    sop_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sop_steps.id", ondelete="RESTRICT"),
        nullable=True,
    )
    parameter_code: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    parameter_name: Mapped[str] = mapped_column(String(150), nullable=False)
    value_type: Mapped[str] = mapped_column(String(30), nullable=False, default="NUMERIC")
    numeric_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    text_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    boolean_value: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    unit_code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    equipment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("equipment.id", ondelete="RESTRICT"),
        nullable=True,
    )
    operator_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    operator_membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
    )
    verified_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    verified_by_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="UNVERIFIED", index=True
    )
    source_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_document_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_section_ref: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    extraction_method: Mapped[str] = mapped_column(
        String(40), nullable=False, default="MANUAL_ENTRY"
    )
    extraction_confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    execution_record: Mapped["BatchExecutionRecord"] = relationship(
        "BatchExecutionRecord",
        back_populates="observations",
        foreign_keys=[batch_execution_record_id],
    )
    execution_step: Mapped["BatchExecutionStep"] = relationship(
        "BatchExecutionStep",
        back_populates="observations",
        foreign_keys=[batch_execution_step_id],
    )


class DeterministicEvidenceCheck(UUIDPKMixin, TenantScopedMixin, Base):
    """Persisted deterministic evidence compliance check evaluation record for a Batch (`C4`)."""

    __tablename__ = "deterministic_evidence_checks"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_det_checks_org_id_id"),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_batch",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_execution_record_id"],
            ["batch_execution_records.organization_id", "batch_execution_records.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_exec_rec",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_execution_step_id"],
            ["batch_execution_steps.organization_id", "batch_execution_steps.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_exec_step",
        ),
        ForeignKeyConstraint(
            ["organization_id", "observation_id"],
            ["process_parameter_observations.organization_id", "process_parameter_observations.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_obs",
        ),
        ForeignKeyConstraint(
            ["organization_id", "sop_revision_id"],
            ["sop_revisions.organization_id", "sop_revisions.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_sop_rev",
        ),
        ForeignKeyConstraint(
            ["organization_id", "sop_step_id"],
            ["sop_steps.organization_id", "sop_steps.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_sop_step",
        ),
        ForeignKeyConstraint(
            ["organization_id", "equipment_id"],
            ["equipment.organization_id", "equipment.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_equip",
        ),
        ForeignKeyConstraint(
            ["organization_id", "calibration_record_id"],
            ["calibration_records.organization_id", "calibration_records.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_cal_rec",
        ),
        ForeignKeyConstraint(
            ["organization_id", "qc_test_definition_id"],
            ["qc_test_definitions.organization_id", "qc_test_definitions.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_qc_def",
        ),
        ForeignKeyConstraint(
            ["organization_id", "qc_test_result_id"],
            ["qc_test_results.organization_id", "qc_test_results.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_qc_res",
        ),
        Index("ix_det_checks_org_batch_type", "organization_id", "batch_id", "check_type"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    check_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    rule_identifier: Mapped[str] = mapped_column(String(80), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(40), nullable=False, default="1.0.0")
    source_entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    batch_execution_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batch_execution_records.id", ondelete="RESTRICT"),
        nullable=True,
    )
    batch_execution_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batch_execution_steps.id", ondelete="RESTRICT"),
        nullable=True,
    )
    observation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("process_parameter_observations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    sop_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sop_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    sop_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sop_steps.id", ondelete="RESTRICT"),
        nullable=True,
    )
    equipment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("equipment.id", ondelete="RESTRICT"),
        nullable=True,
    )
    calibration_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("calibration_records.id", ondelete="RESTRICT"),
        nullable=True,
    )
    qc_test_definition_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("qc_test_definitions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    qc_test_result_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("qc_test_results.id", ondelete="RESTRICT"),
        nullable=True,
    )
    actual_value_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actual_numeric_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    expected_requirement_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expected_min_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    expected_max_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    unit_code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    execution_timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    outcome: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
