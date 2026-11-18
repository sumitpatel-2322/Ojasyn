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

from app.models.domain.base import Base, TenantScopedMixin, TimestampVersionMixin, UUIDPKMixin

JSONType = JSON().with_variant(JSONB(), "postgresql")


class QCTestDefinition(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Versioned QC test specification and acceptance criteria definition (Module C6)."""

    __tablename__ = "qc_test_definitions"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_qc_test_defs_org_id_id"),
        UniqueConstraint(
            "organization_id",
            "test_code",
            "version_number",
            name="uq_qc_test_defs_org_code_ver",
        ),
        CheckConstraint(
            "min_limit IS NULL OR max_limit IS NULL OR min_limit <= max_limit",
            name="ck_qc_test_defs_min_lte_max",
        ),
        CheckConstraint(
            "effective_to IS NULL OR (effective_from IS NOT NULL AND effective_to > effective_from)",
            name="ck_qc_test_defs_effective_window",
        ),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_qc_test_defs_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "product_id"],
            ["products.organization_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_qc_test_defs_org_product",
        ),
        ForeignKeyConstraint(
            ["organization_id", "sop_revision_id"],
            ["sop_revisions.organization_id", "sop_revisions.id"],
            ondelete="RESTRICT",
            name="fk_qc_test_defs_org_sop_rev",
        ),
        ForeignKeyConstraint(
            ["organization_id", "specification_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_qc_test_defs_org_spec_doc",
        ),
        Index("ix_qc_test_defs_org_status", "organization_id", "status"),
    )

    site_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    product_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    product_category: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    test_code: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    version_number: Mapped[str] = mapped_column(String(40), nullable=False, default="1.0")
    test_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    test_method_reference: Mapped[str] = mapped_column(String(150), nullable=False)
    sop_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sop_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    specification_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    specification_document_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    test_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="NUMERIC_RANGE"
    )
    expected_unit_code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    min_limit: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    max_limit: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    target_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    expected_text_specification: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    acceptance_criteria: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    effective_to: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="ACTIVE", index=True
    )


class QCSample(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Physical quality control sample drawn from a manufacturing batch (Module C6)."""

    __tablename__ = "qc_samples"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_qc_samples_org_id_id"),
        UniqueConstraint(
            "organization_id",
            "sample_identifier",
            name="uq_qc_samples_org_identifier",
        ),
        CheckConstraint("sample_quantity > 0", name="ck_qc_samples_qty_pos"),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_qc_samples_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_qc_samples_org_batch",
        ),
        ForeignKeyConstraint(
            ["organization_id", "product_id"],
            ["products.organization_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_qc_samples_org_product",
        ),
        ForeignKeyConstraint(
            ["organization_id", "sampled_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_qc_samples_org_sampler",
        ),
        ForeignKeyConstraint(
            ["organization_id", "supporting_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_qc_samples_org_doc",
        ),
        Index("ix_qc_samples_batch_time", "batch_id", "sampled_at"),
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
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    sample_identifier: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    sample_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="FINISHED_PRODUCT"
    )
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sampled_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    sampled_by_membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sample_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    sample_unit_code: Mapped[str] = mapped_column(String(30), nullable=False)
    storage_location: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    testing_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="REGISTERED", index=True
    )
    supporting_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    supporting_document_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    results: Mapped[List["QCTestResult"]] = relationship(
        "QCTestResult",
        back_populates="sample",
        foreign_keys="QCTestResult.qc_sample_id",
        order_by="QCTestResult.tested_at.asc()",
    )


class QCTestResult(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """QC analytical test result with dual maker-checker review and OOS flagging (Module C6)."""

    __tablename__ = "qc_test_results"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_qc_results_org_id_id"),
        CheckConstraint(
            "(result_type = 'NUMERIC' AND numeric_result IS NOT NULL) OR "
            "(result_type = 'TEXT' AND text_result IS NOT NULL) OR "
            "(result_type = 'BOOLEAN' AND boolean_result IS NOT NULL)",
            name="ck_qc_results_typed_value_present",
        ),
        CheckConstraint(
            "reviewer_membership_id IS NULL OR reviewer_membership_id != analyst_membership_id",
            name="ck_qc_results_maker_checker_member",
        ),
        CheckConstraint(
            "reviewer_user_id IS NULL OR analyst_user_id IS NULL OR reviewer_user_id != analyst_user_id",
            name="ck_qc_results_maker_checker_user",
        ),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_qc_results_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_qc_results_org_batch",
        ),
        ForeignKeyConstraint(
            ["organization_id", "qc_sample_id"],
            ["qc_samples.organization_id", "qc_samples.id"],
            ondelete="RESTRICT",
            name="fk_qc_results_org_sample",
        ),
        ForeignKeyConstraint(
            ["organization_id", "test_definition_id"],
            ["qc_test_definitions.organization_id", "qc_test_definitions.id"],
            ondelete="RESTRICT",
            name="fk_qc_results_org_test_def",
        ),
        ForeignKeyConstraint(
            ["organization_id", "equipment_id"],
            ["equipment.organization_id", "equipment.id"],
            ondelete="RESTRICT",
            name="fk_qc_results_org_equipment",
        ),
        ForeignKeyConstraint(
            ["organization_id", "analyst_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_qc_results_org_analyst",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reviewer_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_qc_results_org_reviewer",
        ),
        ForeignKeyConstraint(
            ["organization_id", "supporting_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_qc_results_org_doc",
        ),
        Index("ix_qc_results_batch_time", "batch_id", "tested_at"),
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
    qc_sample_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("qc_samples.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    test_definition_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("qc_test_definitions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    specification_version_applied: Mapped[str] = mapped_column(String(40), nullable=False)
    result_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="NUMERIC"
    )
    numeric_result: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    text_result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    boolean_result: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    unit_code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    equipment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("equipment.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    tested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    analyst_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    analyst_membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reviewer_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reviewer_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approval_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("approval_records.id", ondelete="RESTRICT"),
        nullable=True,
    )
    result_outcome: Mapped[str] = mapped_column(
        String(30), nullable=False, default="UNVERIFIED", index=True
    )
    review_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="PENDING_REVIEW", index=True
    )
    is_out_of_specification_flagged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    supporting_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    supporting_document_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    sample: Mapped["QCSample"] = relationship(
        "QCSample", back_populates="results", foreign_keys=[qc_sample_id]
    )
    test_definition: Mapped["QCTestDefinition"] = relationship(
        "QCTestDefinition", foreign_keys=[test_definition_id]
    )


class Equipment(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Manufacturing or QC analytical instrument/equipment asset registry (Module C8)."""

    __tablename__ = "equipment"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_equipment_org_id_id"),
        UniqueConstraint(
            "organization_id",
            "site_id",
            "equipment_code",
            name="uq_equipment_org_site_code",
        ),
        CheckConstraint(
            "calibration_frequency_days IS NULL OR calibration_frequency_days > 0",
            name="ck_equipment_cal_freq_pos",
        ),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_equipment_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "department_id"],
            ["departments.organization_id", "departments.id"],
            ondelete="RESTRICT",
            name="fk_equipment_org_dept",
        ),
        Index("ix_equipment_org_status", "organization_id", "operational_status"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    equipment_code: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    equipment_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    equipment_category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    model_number: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    serial_number: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    commissioned_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    operational_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="ACTIVE", index=True
    )
    qualification_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="QUALIFIED", index=True
    )
    calibration_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    calibration_frequency_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    calibration_specification_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONType, nullable=True
    )
    maintenance_metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONType, nullable=True
    )

    calibrations: Mapped[List["CalibrationRecord"]] = relationship(
        "CalibrationRecord",
        back_populates="equipment",
        foreign_keys="CalibrationRecord.equipment_id",
        order_by="CalibrationRecord.calibrated_at.desc()",
    )


class CalibrationRecord(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Historical calibration certificate and review ledger for an Equipment asset (Module C8)."""

    __tablename__ = "calibration_records"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_calibration_records_org_id_id"),
        UniqueConstraint(
            "organization_id",
            "equipment_id",
            "calibration_code",
            name="uq_cal_records_org_equip_code",
        ),
        CheckConstraint("due_at > calibrated_at", name="ck_cal_records_due_after_cal"),
        CheckConstraint(
            "reviewed_by_membership_id IS NULL OR reviewed_by_membership_id != recorded_by_membership_id",
            name="ck_cal_records_maker_checker_member",
        ),
        CheckConstraint(
            "reviewed_by_user_id IS NULL OR recorded_by_user_id IS NULL OR reviewed_by_user_id != recorded_by_user_id",
            name="ck_cal_records_maker_checker_user",
        ),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_cal_records_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "equipment_id"],
            ["equipment.organization_id", "equipment.id"],
            ondelete="RESTRICT",
            name="fk_cal_records_org_equipment",
        ),
        ForeignKeyConstraint(
            ["organization_id", "certificate_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_cal_records_org_cert_doc",
        ),
        ForeignKeyConstraint(
            ["organization_id", "recorded_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_cal_records_org_recorder",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reviewed_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_cal_records_org_reviewer",
        ),
        Index("ix_cal_records_equip_time", "equipment_id", "calibrated_at"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    equipment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("equipment.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    calibration_code: Mapped[str] = mapped_column(String(80), nullable=False)
    calibrated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    calibration_provider: Mapped[str] = mapped_column(String(200), nullable=False)
    certificate_number: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    certificate_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    certificate_document_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    calibration_result: Mapped[str] = mapped_column(
        String(30), nullable=False, default="PASS"
    )
    record_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="DRAFT", index=True
    )
    recorded_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    recorded_by_membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reviewed_by_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approval_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("approval_records.id", ondelete="RESTRICT"),
        nullable=True,
    )
    deviation_reference: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    equipment: Mapped["Equipment"] = relationship(
        "Equipment", back_populates="calibrations", foreign_keys=[equipment_id]
    )


class EquipmentUsage(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Links an Equipment asset to a BatchExecutionRecord/Step with historical calibration validity snapshot (Module C8)."""

    __tablename__ = "equipment_usages"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_equipment_usages_org_id_id"),
        CheckConstraint(
            "usage_end_at IS NULL OR usage_end_at >= usage_start_at",
            name="ck_equip_usage_end_after_start",
        ),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_equip_usages_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "equipment_id"],
            ["equipment.organization_id", "equipment.id"],
            ondelete="RESTRICT",
            name="fk_equip_usages_org_equipment",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_equip_usages_org_batch",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_execution_record_id"],
            ["batch_execution_records.organization_id", "batch_execution_records.id"],
            ondelete="RESTRICT",
            name="fk_equip_usages_org_exec_rec",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_execution_step_id"],
            ["batch_execution_steps.organization_id", "batch_execution_steps.id"],
            ondelete="RESTRICT",
            name="fk_equip_usages_org_exec_step",
        ),
        ForeignKeyConstraint(
            ["organization_id", "operator_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_equip_usages_org_operator",
        ),
        ForeignKeyConstraint(
            ["organization_id", "applicable_calibration_record_id"],
            ["calibration_records.organization_id", "calibration_records.id"],
            ondelete="RESTRICT",
            name="fk_equip_usages_org_cal_rec",
        ),
        ForeignKeyConstraint(
            ["organization_id", "supporting_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_equip_usages_org_doc",
        ),
        Index("ix_equip_usages_batch_time", "batch_id", "usage_start_at"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    equipment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("equipment.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    batch_execution_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batch_execution_records.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    batch_execution_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batch_execution_steps.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    usage_start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    usage_end_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    usage_purpose: Mapped[str] = mapped_column(String(200), nullable=False)
    status_at_time_of_use: Mapped[str] = mapped_column(String(30), nullable=False)
    calibration_status_at_use: Mapped[str] = mapped_column(
        String(30), nullable=False, default="UNVERIFIED"
    )
    applicable_calibration_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("calibration_records.id", ondelete="RESTRICT"),
        nullable=True,
    )
    supporting_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    supporting_document_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
