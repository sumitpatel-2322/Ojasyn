"""Phase 4 manufacturing execution, SOPs, QC laboratory, equipment & deterministic evidence checks

Revision ID: 0005_phase4_mfg_evidence
Revises: 0004_phase3_traceability
Create Date: 2026-11-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_phase4_mfg_evidence"
down_revision: Union[str, None] = "0004_phase3_traceability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _common_tenant_version_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "updated_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
    ]


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. C5: SOPs, SOP Revisions & SOP Steps
    # ------------------------------------------------------------------
    op.create_table(
        "sops",
        *_common_tenant_version_columns(),
        sa.Column("site_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("applicable_site_ids", JSONType, nullable=True),
        sa.Column(
            "department_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("departments.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("sop_code", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column(
            "document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "owner_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("current_revision_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("sop_status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        sa.UniqueConstraint("organization_id", "id", name="uq_sops_org_id_id"),
        sa.UniqueConstraint("organization_id", "sop_code", name="uq_sops_org_code"),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_sops_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "department_id"],
            ["departments.organization_id", "departments.id"],
            ondelete="RESTRICT",
            name="fk_sops_org_dept",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_sops_org_doc",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "owner_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_sops_org_owner",
        ),
    )
    op.create_index("ix_sops_organization_id", "sops", ["organization_id"])
    op.create_index("ix_sops_site_id", "sops", ["site_id"])
    op.create_index("ix_sops_department_id", "sops", ["department_id"])
    op.create_index("ix_sops_sop_code", "sops", ["sop_code"])
    op.create_index("ix_sops_category", "sops", ["category"])
    op.create_index("ix_sops_sop_status", "sops", ["sop_status"])

    op.create_table(
        "sop_revisions",
        *_common_tenant_version_columns(),
        sa.Column("sop_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sops.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("site_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("revision_number", sa.String(length=40), nullable=False),
        sa.Column("revision_sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("revision_description", sa.Text(), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column(
            "document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "document_version_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision_status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        sa.Column("approval_status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        sa.Column(
            "submitted_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "approved_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "approved_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "approval_record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("approval_records.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "activated_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "supersedes_revision_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sop_revisions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "superseded_by_revision_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sop_revisions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_sop_revisions_org_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "sop_id",
            "revision_number",
            name="uq_sop_revisions_org_sop_revnum",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "sop_id"],
            ["sops.organization_id", "sops.id"],
            ondelete="RESTRICT",
            name="fk_sop_revisions_org_sop",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_sop_revisions_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_sop_revisions_org_doc",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "submitted_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_sop_revisions_org_submitted_by",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "approved_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_sop_revisions_org_approved_by",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "activated_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_sop_revisions_org_activated_by",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "supersedes_revision_id"],
            ["sop_revisions.organization_id", "sop_revisions.id"],
            ondelete="RESTRICT",
            name="fk_sop_revisions_org_supersedes",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "superseded_by_revision_id"],
            ["sop_revisions.organization_id", "sop_revisions.id"],
            ondelete="RESTRICT",
            name="fk_sop_revisions_org_superseded_by",
        ),
        sa.CheckConstraint("revision_sequence >= 1", name="ck_sop_revisions_seq_pos"),
        sa.CheckConstraint(
            "effective_to IS NULL OR (effective_from IS NOT NULL AND effective_to > effective_from)",
            name="ck_sop_revisions_effective_window",
        ),
        sa.CheckConstraint(
            "approved_by_user_id IS NULL OR created_by_user_id IS NULL OR approved_by_user_id != created_by_user_id",
            name="ck_sop_revisions_maker_checker_user",
        ),
        sa.CheckConstraint(
            "approved_by_membership_id IS NULL OR submitted_by_membership_id IS NULL OR approved_by_membership_id != submitted_by_membership_id",
            name="ck_sop_revisions_maker_checker_member",
        ),
    )
    op.create_index("ix_sop_revisions_organization_id", "sop_revisions", ["organization_id"])
    op.create_index("ix_sop_revisions_sop_id", "sop_revisions", ["sop_id"])
    op.create_index("ix_sop_revisions_site_id", "sop_revisions", ["site_id"])
    op.create_index("ix_sop_revisions_effective_from", "sop_revisions", ["effective_from"])
    op.create_index("ix_sop_revisions_effective_to", "sop_revisions", ["effective_to"])
    op.create_index("ix_sop_revisions_revision_status", "sop_revisions", ["revision_status"])

    # Deferred circular FK between sops.current_revision_id and sop_revisions.id
    op.create_foreign_key(
        "fk_sops_current_revision_id",
        "sops",
        "sop_revisions",
        ["current_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "sop_steps",
        *_common_tenant_version_columns(),
        sa.Column(
            "sop_revision_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sop_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("step_code", sa.String(length=60), nullable=False),
        sa.Column("step_sequence", sa.Integer(), nullable=False),
        sa.Column("process_stage", sa.String(length=100), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("is_mandatory", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("parameter_name", sa.String(length=120), nullable=True),
        sa.Column("parameter_code", sa.String(length=80), nullable=True),
        sa.Column("expected_data_type", sa.String(length=30), nullable=False, server_default="NONE"),
        sa.Column("unit_code", sa.String(length=30), nullable=True),
        sa.Column("min_value", sa.Numeric(18, 6), nullable=True),
        sa.Column("max_value", sa.Numeric(18, 6), nullable=True),
        sa.Column("target_value", sa.Numeric(18, 6), nullable=True),
        sa.Column("expected_text_value", sa.String(length=255), nullable=True),
        sa.Column("allowed_values_json", JSONType, nullable=True),
        sa.Column("acceptance_criteria", sa.Text(), nullable=True),
        sa.Column("requires_operator_signoff", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("requires_checker_signoff", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("requires_qa_signoff", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("required_equipment_category", sa.String(length=80), nullable=True),
        sa.Column("required_equipment_code", sa.String(length=80), nullable=True),
        sa.UniqueConstraint("organization_id", "id", name="uq_sop_steps_org_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "sop_revision_id",
            "step_code",
            name="uq_sop_steps_org_rev_code",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "sop_revision_id",
            "step_sequence",
            name="uq_sop_steps_org_rev_seq",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "sop_revision_id"],
            ["sop_revisions.organization_id", "sop_revisions.id"],
            ondelete="RESTRICT",
            name="fk_sop_steps_org_revision",
        ),
        sa.CheckConstraint("step_sequence >= 1", name="ck_sop_steps_seq_pos"),
        sa.CheckConstraint(
            "min_value IS NULL OR max_value IS NULL OR min_value <= max_value",
            name="ck_sop_steps_min_lte_max",
        ),
    )
    op.create_index("ix_sop_steps_organization_id", "sop_steps", ["organization_id"])
    op.create_index("ix_sop_steps_sop_revision_id", "sop_steps", ["sop_revision_id"])
    op.create_index("ix_sop_steps_parameter_code", "sop_steps", ["parameter_code"])

    # ------------------------------------------------------------------
    # 2. C8: Equipment & Calibration Records
    # ------------------------------------------------------------------
    op.create_table(
        "equipment",
        *_common_tenant_version_columns(),
        sa.Column("site_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "department_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("departments.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("equipment_code", sa.String(length=80), nullable=False),
        sa.Column("equipment_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("equipment_category", sa.String(length=80), nullable=False),
        sa.Column("manufacturer", sa.String(length=150), nullable=True),
        sa.Column("model_number", sa.String(length=120), nullable=True),
        sa.Column("serial_number", sa.String(length=120), nullable=True),
        sa.Column("commissioned_on", sa.Date(), nullable=True),
        sa.Column("operational_status", sa.String(length=30), nullable=False, server_default="ACTIVE"),
        sa.Column("qualification_status", sa.String(length=30), nullable=False, server_default="QUALIFIED"),
        sa.Column("calibration_required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("calibration_frequency_days", sa.Integer(), nullable=True),
        sa.Column("calibration_specification_json", JSONType, nullable=True),
        sa.Column("maintenance_metadata_json", JSONType, nullable=True),
        sa.UniqueConstraint("organization_id", "id", name="uq_equipment_org_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "site_id",
            "equipment_code",
            name="uq_equipment_org_site_code",
        ),
        sa.CheckConstraint(
            "calibration_frequency_days IS NULL OR calibration_frequency_days > 0",
            name="ck_equipment_cal_freq_pos",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_equipment_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "department_id"],
            ["departments.organization_id", "departments.id"],
            ondelete="RESTRICT",
            name="fk_equipment_org_dept",
        ),
    )
    op.create_index("ix_equipment_organization_id", "equipment", ["organization_id"])
    op.create_index("ix_equipment_site_id", "equipment", ["site_id"])
    op.create_index("ix_equipment_department_id", "equipment", ["department_id"])
    op.create_index("ix_equipment_equipment_code", "equipment", ["equipment_code"])
    op.create_index("ix_equipment_equipment_category", "equipment", ["equipment_category"])
    op.create_index("ix_equipment_operational_status", "equipment", ["operational_status"])
    op.create_index("ix_equipment_qualification_status", "equipment", ["qualification_status"])
    op.create_index("ix_equipment_org_status", "equipment", ["organization_id", "operational_status"])

    op.create_table(
        "calibration_records",
        *_common_tenant_version_columns(),
        sa.Column("site_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "equipment_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("equipment.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("calibration_code", sa.String(length=80), nullable=False),
        sa.Column("calibrated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calibration_provider", sa.String(length=200), nullable=False),
        sa.Column("certificate_number", sa.String(length=120), nullable=True),
        sa.Column(
            "certificate_document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "certificate_document_version_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("calibration_result", sa.String(length=30), nullable=False, server_default="PASS"),
        sa.Column("record_status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        sa.Column(
            "recorded_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "recorded_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "reviewed_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "reviewed_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "approval_record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("approval_records.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("deviation_reference", sa.String(length=120), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("organization_id", "id", name="uq_calibration_records_org_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "equipment_id",
            "calibration_code",
            name="uq_cal_records_org_equip_code",
        ),
        sa.CheckConstraint("due_at > calibrated_at", name="ck_cal_records_due_after_cal"),
        sa.CheckConstraint(
            "reviewed_by_membership_id IS NULL OR reviewed_by_membership_id != recorded_by_membership_id",
            name="ck_cal_records_maker_checker_member",
        ),
        sa.CheckConstraint(
            "reviewed_by_user_id IS NULL OR reviewed_by_user_id != recorded_by_user_id",
            name="ck_cal_records_maker_checker_user",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_cal_records_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "equipment_id"],
            ["equipment.organization_id", "equipment.id"],
            ondelete="RESTRICT",
            name="fk_cal_records_org_equipment",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "certificate_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_cal_records_org_cert_doc",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "recorded_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_cal_records_org_recorder",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reviewed_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_cal_records_org_reviewer",
        ),
    )
    op.create_index("ix_calibration_records_organization_id", "calibration_records", ["organization_id"])
    op.create_index("ix_calibration_records_site_id", "calibration_records", ["site_id"])
    op.create_index("ix_calibration_records_equipment_id", "calibration_records", ["equipment_id"])
    op.create_index("ix_calibration_records_record_status", "calibration_records", ["record_status"])
    op.create_index(
        "ix_cal_records_equip_time",
        "calibration_records",
        ["organization_id", "equipment_id", "calibrated_at", "due_at"],
    )

    # ------------------------------------------------------------------
    # 3. C6: QC Test Definitions, QC Samples & QC Test Results
    # ------------------------------------------------------------------
    op.create_table(
        "qc_test_definitions",
        *_common_tenant_version_columns(),
        sa.Column("site_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=True),
        sa.Column(
            "product_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("product_category", sa.String(length=80), nullable=True),
        sa.Column("test_code", sa.String(length=80), nullable=False),
        sa.Column("version_number", sa.String(length=40), nullable=False, server_default="1.0"),
        sa.Column("test_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("test_method_reference", sa.String(length=150), nullable=False),
        sa.Column(
            "sop_revision_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sop_revisions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "specification_document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "specification_document_version_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("test_type", sa.String(length=30), nullable=False, server_default="NUMERIC_RANGE"),
        sa.Column("expected_unit_code", sa.String(length=30), nullable=True),
        sa.Column("min_limit", sa.Numeric(18, 6), nullable=True),
        sa.Column("max_limit", sa.Numeric(18, 6), nullable=True),
        sa.Column("target_value", sa.Numeric(18, 6), nullable=True),
        sa.Column("expected_text_specification", sa.String(length=255), nullable=True),
        sa.Column("acceptance_criteria", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="ACTIVE"),
        sa.UniqueConstraint("organization_id", "id", name="uq_qc_test_defs_org_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "test_code",
            "version_number",
            name="uq_qc_test_defs_org_code_ver",
        ),
        sa.CheckConstraint(
            "min_limit IS NULL OR max_limit IS NULL OR min_limit <= max_limit",
            name="ck_qc_test_defs_min_lte_max",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR (effective_from IS NOT NULL AND effective_to > effective_from)",
            name="ck_qc_test_defs_effective_window",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_qc_test_defs_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "product_id"],
            ["products.organization_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_qc_test_defs_org_product",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "sop_revision_id"],
            ["sop_revisions.organization_id", "sop_revisions.id"],
            ondelete="RESTRICT",
            name="fk_qc_test_defs_org_sop_rev",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "specification_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_qc_test_defs_org_spec_doc",
        ),
    )
    op.create_index("ix_qc_test_definitions_organization_id", "qc_test_definitions", ["organization_id"])
    op.create_index("ix_qc_test_definitions_site_id", "qc_test_definitions", ["site_id"])
    op.create_index("ix_qc_test_definitions_product_id", "qc_test_definitions", ["product_id"])
    op.create_index("ix_qc_test_definitions_test_code", "qc_test_definitions", ["test_code"])
    op.create_index("ix_qc_test_definitions_status", "qc_test_definitions", ["status"])
    op.create_index("ix_qc_test_defs_org_status", "qc_test_definitions", ["organization_id", "status"])

    op.create_table(
        "qc_samples",
        *_common_tenant_version_columns(),
        sa.Column("site_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("batch_id", sa.Uuid(as_uuid=True), sa.ForeignKey("batches.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "product_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sample_identifier", sa.String(length=80), nullable=False),
        sa.Column("sample_type", sa.String(length=40), nullable=False, server_default="FINISHED_PRODUCT"),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "sampled_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "sampled_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sample_quantity", sa.Numeric(14, 4), nullable=False),
        sa.Column("sample_unit_code", sa.String(length=30), nullable=False),
        sa.Column("storage_location", sa.String(length=120), nullable=True),
        sa.Column("testing_status", sa.String(length=30), nullable=False, server_default="REGISTERED"),
        sa.Column(
            "supporting_document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "supporting_document_version_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("organization_id", "id", name="uq_qc_samples_org_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "sample_identifier",
            name="uq_qc_samples_org_identifier",
        ),
        sa.CheckConstraint(
            "sample_quantity IS NULL OR sample_quantity > 0",
            name="ck_qc_samples_qty_pos",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_qc_samples_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_qc_samples_org_batch",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "product_id"],
            ["products.organization_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_qc_samples_org_product",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "sampled_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_qc_samples_org_sampler",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "supporting_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_qc_samples_org_doc",
        ),
    )
    op.create_index("ix_qc_samples_organization_id", "qc_samples", ["organization_id"])
    op.create_index("ix_qc_samples_site_id", "qc_samples", ["site_id"])
    op.create_index("ix_qc_samples_batch_id", "qc_samples", ["batch_id"])
    op.create_index("ix_qc_samples_product_id", "qc_samples", ["product_id"])
    op.create_index("ix_qc_samples_sample_identifier", "qc_samples", ["sample_identifier"])
    op.create_index("ix_qc_samples_testing_status", "qc_samples", ["testing_status"])
    op.create_index("ix_qc_samples_batch_time", "qc_samples", ["organization_id", "batch_id", "sampled_at"])

    op.create_table(
        "qc_test_results",
        *_common_tenant_version_columns(),
        sa.Column("site_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("batch_id", sa.Uuid(as_uuid=True), sa.ForeignKey("batches.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "qc_sample_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("qc_samples.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "test_definition_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("qc_test_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("specification_version_applied", sa.String(length=40), nullable=False),
        sa.Column("result_type", sa.String(length=30), nullable=False, server_default="NUMERIC"),
        sa.Column("numeric_result", sa.Numeric(18, 6), nullable=True),
        sa.Column("text_result", sa.Text(), nullable=True),
        sa.Column("boolean_result", sa.Boolean(), nullable=True),
        sa.Column("unit_code", sa.String(length=30), nullable=True),
        sa.Column(
            "equipment_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("equipment.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("tested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "analyst_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "analyst_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "reviewer_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "reviewer_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "approval_record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("approval_records.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("result_outcome", sa.String(length=30), nullable=False, server_default="UNVERIFIED"),
        sa.Column("review_status", sa.String(length=30), nullable=False, server_default="PENDING_REVIEW"),
        sa.Column("is_out_of_specification_flagged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "supporting_document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "supporting_document_version_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.UniqueConstraint("organization_id", "id", name="uq_qc_results_org_id_id"),
        sa.CheckConstraint(
            "(result_type = 'NUMERIC' AND numeric_result IS NOT NULL) OR "
            "(result_type = 'TEXT' AND text_result IS NOT NULL) OR "
            "(result_type = 'BOOLEAN' AND boolean_result IS NOT NULL)",
            name="ck_qc_results_typed_value_present",
        ),
        sa.CheckConstraint(
            "reviewer_membership_id IS NULL OR reviewer_membership_id != analyst_membership_id",
            name="ck_qc_results_maker_checker_member",
        ),
        sa.CheckConstraint(
            "reviewer_user_id IS NULL OR reviewer_user_id != analyst_user_id",
            name="ck_qc_results_maker_checker_user",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_qc_results_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_qc_results_org_batch",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "qc_sample_id"],
            ["qc_samples.organization_id", "qc_samples.id"],
            ondelete="RESTRICT",
            name="fk_qc_results_org_sample",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "test_definition_id"],
            ["qc_test_definitions.organization_id", "qc_test_definitions.id"],
            ondelete="RESTRICT",
            name="fk_qc_results_org_test_def",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "equipment_id"],
            ["equipment.organization_id", "equipment.id"],
            ondelete="RESTRICT",
            name="fk_qc_results_org_equipment",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "analyst_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_qc_results_org_analyst",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reviewer_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_qc_results_org_reviewer",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "supporting_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_qc_results_org_doc",
        ),
    )
    op.create_index("ix_qc_test_results_organization_id", "qc_test_results", ["organization_id"])
    op.create_index("ix_qc_test_results_site_id", "qc_test_results", ["site_id"])
    op.create_index("ix_qc_test_results_batch_id", "qc_test_results", ["batch_id"])
    op.create_index("ix_qc_test_results_qc_sample_id", "qc_test_results", ["qc_sample_id"])
    op.create_index("ix_qc_test_results_test_definition_id", "qc_test_results", ["test_definition_id"])
    op.create_index("ix_qc_test_results_equipment_id", "qc_test_results", ["equipment_id"])
    op.create_index("ix_qc_test_results_result_outcome", "qc_test_results", ["result_outcome"])
    op.create_index("ix_qc_test_results_review_status", "qc_test_results", ["review_status"])
    op.create_index("ix_qc_results_batch_time", "qc_test_results", ["organization_id", "batch_id", "tested_at"])

    # ------------------------------------------------------------------
    # 4. C4: Master Batch Records, Batch Execution Records, Steps & Observations
    # ------------------------------------------------------------------
    op.create_table(
        "master_batch_records",
        *_common_tenant_version_columns(),
        sa.Column("site_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "product_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("mbr_code", sa.String(length=80), nullable=False),
        sa.Column("version_number", sa.String(length=40), nullable=False),
        sa.Column("product_version_context", sa.String(length=80), nullable=True),
        sa.Column("record_type", sa.String(length=20), nullable=False, server_default="BMR"),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target_batch_size_value", sa.Numeric(14, 4), nullable=True),
        sa.Column("target_batch_size_unit", sa.String(length=30), nullable=True),
        sa.Column(
            "document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "document_version_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("applicable_sop_revision_ids", JSONType, nullable=False),
        sa.Column("planned_steps_json", JSONType, nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        sa.Column(
            "approved_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "approved_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "approval_record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("approval_records.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "supersedes_mbr_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("master_batch_records.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_mbrs_org_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "site_id",
            "product_id",
            "mbr_code",
            "version_number",
            name="uq_mbrs_org_site_prod_code_ver",
        ),
        sa.CheckConstraint(
            "target_batch_size_value IS NULL OR target_batch_size_value > 0",
            name="ck_mbrs_target_size_pos",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR (effective_from IS NOT NULL AND effective_to > effective_from)",
            name="ck_mbrs_effective_dates",
        ),
        sa.CheckConstraint(
            "approved_by_user_id IS NULL OR created_by_user_id IS NULL OR approved_by_user_id != created_by_user_id",
            name="ck_mbrs_maker_checker_user",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_mbrs_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "product_id"],
            ["products.organization_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_mbrs_org_product",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_mbrs_org_document",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "approved_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_mbrs_org_approver",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "supersedes_mbr_id"],
            ["master_batch_records.organization_id", "master_batch_records.id"],
            ondelete="RESTRICT",
            name="fk_mbrs_org_supersedes",
        ),
    )
    op.create_index("ix_master_batch_records_organization_id", "master_batch_records", ["organization_id"])
    op.create_index("ix_master_batch_records_site_id", "master_batch_records", ["site_id"])
    op.create_index("ix_master_batch_records_product_id", "master_batch_records", ["product_id"])
    op.create_index("ix_master_batch_records_mbr_code", "master_batch_records", ["mbr_code"])
    op.create_index("ix_master_batch_records_approval_status", "master_batch_records", ["approval_status"])
    op.create_index(
        "ix_mbrs_org_prod_status",
        "master_batch_records",
        ["organization_id", "product_id", "approval_status"],
    )

    op.create_table(
        "batch_execution_records",
        *_common_tenant_version_columns(),
        sa.Column("site_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("batch_id", sa.Uuid(as_uuid=True), sa.ForeignKey("batches.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "master_batch_record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("master_batch_records.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("execution_code", sa.String(length=80), nullable=False),
        sa.Column("execution_type", sa.String(length=20), nullable=False, server_default="BMR"),
        sa.Column("manufacturing_stage", sa.String(length=100), nullable=False),
        sa.Column("execution_sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        sa.Column(
            "operator_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "operator_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "checker_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "checker_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "approved_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "approval_record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("approval_records.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "primary_document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "primary_document_version_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("supporting_document_version_ids", JSONType, nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("organization_id", "id", name="uq_batch_exec_org_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "batch_id",
            "execution_code",
            name="uq_batch_exec_org_batch_code",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "batch_id",
            "execution_sequence",
            name="uq_batch_exec_org_batch_seq",
        ),
        sa.CheckConstraint("execution_sequence >= 1", name="ck_batch_exec_seq_pos"),
        sa.CheckConstraint(
            "completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at)",
            name="ck_batch_exec_dates",
        ),
        sa.CheckConstraint(
            "checker_membership_id IS NULL OR checker_membership_id != operator_membership_id",
            name="ck_batch_exec_maker_checker_member",
        ),
        sa.CheckConstraint(
            "checker_user_id IS NULL OR operator_user_id IS NULL OR checker_user_id != operator_user_id",
            name="ck_batch_exec_maker_checker_user",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_org_batch",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "master_batch_record_id"],
            ["master_batch_records.organization_id", "master_batch_records.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_org_mbr",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "operator_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_org_operator",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "checker_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_org_checker",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "approved_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_org_approver",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "primary_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_org_primary_doc",
        ),
    )
    op.create_index("ix_batch_execution_records_organization_id", "batch_execution_records", ["organization_id"])
    op.create_index("ix_batch_execution_records_site_id", "batch_execution_records", ["site_id"])
    op.create_index("ix_batch_execution_records_batch_id", "batch_execution_records", ["batch_id"])
    op.create_index(
        "ix_batch_execution_records_master_batch_record_id",
        "batch_execution_records",
        ["master_batch_record_id"],
    )
    op.create_index("ix_batch_execution_records_execution_status", "batch_execution_records", ["execution_status"])
    op.create_index(
        "ix_batch_exec_org_batch_status",
        "batch_execution_records",
        ["organization_id", "batch_id", "execution_status"],
    )

    op.create_table(
        "batch_execution_steps",
        *_common_tenant_version_columns(),
        sa.Column("site_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "batch_execution_record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batch_execution_records.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("step_code", sa.String(length=60), nullable=False),
        sa.Column("step_sequence", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("sop_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sops.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("sop_code", sa.String(length=80), nullable=True),
        sa.Column(
            "sop_revision_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sop_revisions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "sop_step_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sop_steps.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("planned_requirement", sa.Text(), nullable=True),
        sa.Column("requires_checker_signoff", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("step_status", sa.String(length=30), nullable=False, server_default="PENDING"),
        sa.Column(
            "operator_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "operator_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "checker_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "checker_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "equipment_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("equipment.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("exception_reason", sa.Text(), nullable=True),
        sa.Column(
            "supporting_document_version_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("supporting_evidence_json", JSONType, nullable=True),
        sa.UniqueConstraint("organization_id", "id", name="uq_batch_exec_steps_org_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "batch_execution_record_id",
            "step_code",
            name="uq_batch_exec_steps_org_rec_code",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "batch_execution_record_id",
            "step_sequence",
            name="uq_batch_exec_steps_org_rec_seq",
        ),
        sa.CheckConstraint("step_sequence >= 1", name="ck_batch_exec_steps_seq_pos"),
        sa.CheckConstraint(
            "checker_membership_id IS NULL OR operator_membership_id IS NULL OR checker_membership_id != operator_membership_id",
            name="ck_batch_exec_steps_maker_checker_member",
        ),
        sa.CheckConstraint(
            "checker_user_id IS NULL OR operator_user_id IS NULL OR checker_user_id != operator_user_id",
            name="ck_batch_exec_steps_maker_checker_user",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_steps_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_execution_record_id"],
            ["batch_execution_records.organization_id", "batch_execution_records.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_steps_org_rec",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "sop_id"],
            ["sops.organization_id", "sops.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_steps_org_sop",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "sop_revision_id"],
            ["sop_revisions.organization_id", "sop_revisions.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_steps_org_sop_rev",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "sop_step_id"],
            ["sop_steps.organization_id", "sop_steps.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_steps_org_sop_step",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "operator_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_steps_org_operator",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "checker_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_steps_org_checker",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "equipment_id"],
            ["equipment.organization_id", "equipment.id"],
            ondelete="RESTRICT",
            name="fk_batch_exec_steps_org_equip",
        ),
    )
    op.create_index("ix_batch_execution_steps_organization_id", "batch_execution_steps", ["organization_id"])
    op.create_index("ix_batch_execution_steps_site_id", "batch_execution_steps", ["site_id"])
    op.create_index(
        "ix_batch_execution_steps_batch_execution_record_id",
        "batch_execution_steps",
        ["batch_execution_record_id"],
    )
    op.create_index("ix_batch_execution_steps_step_status", "batch_execution_steps", ["step_status"])

    op.create_table(
        "process_parameter_observations",
        *_common_tenant_version_columns(),
        sa.Column("site_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "batch_execution_record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batch_execution_records.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "batch_execution_step_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batch_execution_steps.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "sop_step_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sop_steps.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("parameter_code", sa.String(length=80), nullable=False),
        sa.Column("parameter_name", sa.String(length=150), nullable=False),
        sa.Column("value_type", sa.String(length=30), nullable=False, server_default="NUMERIC"),
        sa.Column("numeric_value", sa.Numeric(18, 6), nullable=True),
        sa.Column("text_value", sa.Text(), nullable=True),
        sa.Column("boolean_value", sa.Boolean(), nullable=True),
        sa.Column("unit_code", sa.String(length=30), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "equipment_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("equipment.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "operator_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "operator_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "verified_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "verified_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_status", sa.String(length=30), nullable=False, server_default="UNVERIFIED"),
        sa.Column(
            "source_document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "source_document_version_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("source_page_number", sa.Integer(), nullable=True),
        sa.Column("source_section_ref", sa.String(length=120), nullable=True),
        sa.Column("extraction_method", sa.String(length=40), nullable=False, server_default="MANUAL_ENTRY"),
        sa.Column("extraction_confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("organization_id", "id", name="uq_param_obs_org_id_id"),
        sa.CheckConstraint(
            "(value_type = 'NUMERIC' AND numeric_value IS NOT NULL) OR "
            "(value_type = 'TEXT' AND text_value IS NOT NULL) OR "
            "(value_type = 'BOOLEAN' AND boolean_value IS NOT NULL)",
            name="ck_param_obs_typed_val",
        ),
        sa.CheckConstraint(
            "source_page_number IS NULL OR source_page_number >= 1",
            name="ck_param_obs_page_pos",
        ),
        sa.CheckConstraint(
            "extraction_confidence IS NULL OR (extraction_confidence >= 0 AND extraction_confidence <= 1)",
            name="ck_param_obs_conf_range",
        ),
        sa.CheckConstraint(
            "verified_by_membership_id IS NULL OR verified_by_membership_id != operator_membership_id",
            name="ck_param_obs_maker_checker_member",
        ),
        sa.CheckConstraint(
            "verified_by_user_id IS NULL OR operator_user_id IS NULL OR verified_by_user_id != operator_user_id",
            name="ck_param_obs_maker_checker_user",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_param_obs_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_execution_record_id"],
            ["batch_execution_records.organization_id", "batch_execution_records.id"],
            ondelete="RESTRICT",
            name="fk_param_obs_org_exec_rec",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_execution_step_id"],
            ["batch_execution_steps.organization_id", "batch_execution_steps.id"],
            ondelete="RESTRICT",
            name="fk_param_obs_org_exec_step",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "sop_step_id"],
            ["sop_steps.organization_id", "sop_steps.id"],
            ondelete="RESTRICT",
            name="fk_param_obs_org_sop_step",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "equipment_id"],
            ["equipment.organization_id", "equipment.id"],
            ondelete="RESTRICT",
            name="fk_param_obs_org_equip",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "operator_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_param_obs_org_operator",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "verified_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_param_obs_org_verifier",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_param_obs_org_source_doc",
        ),
    )
    op.create_index(
        "ix_process_parameter_observations_organization_id",
        "process_parameter_observations",
        ["organization_id"],
    )
    op.create_index("ix_process_parameter_observations_site_id", "process_parameter_observations", ["site_id"])
    op.create_index(
        "ix_process_parameter_observations_batch_execution_record_id",
        "process_parameter_observations",
        ["batch_execution_record_id"],
    )
    op.create_index(
        "ix_process_parameter_observations_batch_execution_step_id",
        "process_parameter_observations",
        ["batch_execution_step_id"],
    )
    op.create_index(
        "ix_process_parameter_observations_parameter_code",
        "process_parameter_observations",
        ["parameter_code"],
    )
    op.create_index(
        "ix_process_parameter_observations_verification_status",
        "process_parameter_observations",
        ["verification_status"],
    )

    # ------------------------------------------------------------------
    # 5. C8: Equipment Usages (depends on equipment, calibration_records, batch_execution_*)
    # ------------------------------------------------------------------
    op.create_table(
        "equipment_usages",
        *_common_tenant_version_columns(),
        sa.Column("site_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "equipment_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("equipment.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("batch_id", sa.Uuid(as_uuid=True), sa.ForeignKey("batches.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "batch_execution_record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batch_execution_records.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "batch_execution_step_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batch_execution_steps.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("usage_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usage_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "operator_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "operator_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("usage_purpose", sa.String(length=200), nullable=False),
        sa.Column("status_at_time_of_use", sa.String(length=30), nullable=False),
        sa.Column("calibration_status_at_use", sa.String(length=30), nullable=False, server_default="UNVERIFIED"),
        sa.Column(
            "applicable_calibration_record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("calibration_records.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "supporting_document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "supporting_document_version_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("organization_id", "id", name="uq_equipment_usages_org_id_id"),
        sa.CheckConstraint(
            "usage_end_at IS NULL OR usage_end_at >= usage_start_at",
            name="ck_equip_usage_end_after_start",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_equip_usages_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "equipment_id"],
            ["equipment.organization_id", "equipment.id"],
            ondelete="RESTRICT",
            name="fk_equip_usages_org_equipment",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_equip_usages_org_batch",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_execution_record_id"],
            ["batch_execution_records.organization_id", "batch_execution_records.id"],
            ondelete="RESTRICT",
            name="fk_equip_usages_org_exec_rec",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_execution_step_id"],
            ["batch_execution_steps.organization_id", "batch_execution_steps.id"],
            ondelete="RESTRICT",
            name="fk_equip_usages_org_exec_step",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "operator_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_equip_usages_org_operator",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "applicable_calibration_record_id"],
            ["calibration_records.organization_id", "calibration_records.id"],
            ondelete="RESTRICT",
            name="fk_equip_usages_org_cal_rec",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "supporting_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_equip_usages_org_doc",
        ),
    )
    op.create_index("ix_equipment_usages_organization_id", "equipment_usages", ["organization_id"])
    op.create_index("ix_equipment_usages_site_id", "equipment_usages", ["site_id"])
    op.create_index("ix_equipment_usages_equipment_id", "equipment_usages", ["equipment_id"])
    op.create_index("ix_equipment_usages_batch_id", "equipment_usages", ["batch_id"])
    op.create_index(
        "ix_equipment_usages_batch_execution_record_id",
        "equipment_usages",
        ["batch_execution_record_id"],
    )
    op.create_index(
        "ix_equipment_usages_batch_execution_step_id",
        "equipment_usages",
        ["batch_execution_step_id"],
    )
    op.create_index(
        "ix_equip_usages_batch_time",
        "equipment_usages",
        ["organization_id", "batch_id", "usage_start_at"],
    )

    # ------------------------------------------------------------------
    # 6. Deterministic Evidence Checks (immutable evaluation records)
    # ------------------------------------------------------------------
    op.create_table(
        "deterministic_evidence_checks",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("site_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("batch_id", sa.Uuid(as_uuid=True), sa.ForeignKey("batches.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("check_type", sa.String(length=60), nullable=False),
        sa.Column("rule_identifier", sa.String(length=80), nullable=False),
        sa.Column("rule_version", sa.String(length=40), nullable=False, server_default="1.0.0"),
        sa.Column("source_entity_type", sa.String(length=80), nullable=False),
        sa.Column("source_entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "batch_execution_record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batch_execution_records.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "batch_execution_step_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batch_execution_steps.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "observation_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("process_parameter_observations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "sop_revision_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sop_revisions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "sop_step_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sop_steps.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "equipment_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("equipment.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "calibration_record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("calibration_records.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "qc_test_definition_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("qc_test_definitions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "qc_test_result_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("qc_test_results.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("actual_value_text", sa.Text(), nullable=True),
        sa.Column("actual_numeric_value", sa.Numeric(18, 6), nullable=True),
        sa.Column("expected_requirement_text", sa.Text(), nullable=True),
        sa.Column("expected_min_value", sa.Numeric(18, 6), nullable=True),
        sa.Column("expected_max_value", sa.Numeric(18, 6), nullable=True),
        sa.Column("unit_code", sa.String(length=30), nullable=True),
        sa.Column("execution_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("metadata_json", JSONType, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "id", name="uq_det_checks_org_id_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_batch",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_execution_record_id"],
            ["batch_execution_records.organization_id", "batch_execution_records.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_exec_rec",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_execution_step_id"],
            ["batch_execution_steps.organization_id", "batch_execution_steps.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_exec_step",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "observation_id"],
            ["process_parameter_observations.organization_id", "process_parameter_observations.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_obs",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "sop_revision_id"],
            ["sop_revisions.organization_id", "sop_revisions.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_sop_rev",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "sop_step_id"],
            ["sop_steps.organization_id", "sop_steps.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_sop_step",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "equipment_id"],
            ["equipment.organization_id", "equipment.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_equip",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "calibration_record_id"],
            ["calibration_records.organization_id", "calibration_records.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_cal_rec",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "qc_test_definition_id"],
            ["qc_test_definitions.organization_id", "qc_test_definitions.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_qc_def",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "qc_test_result_id"],
            ["qc_test_results.organization_id", "qc_test_results.id"],
            ondelete="RESTRICT",
            name="fk_det_checks_org_qc_res",
        ),
    )
    op.create_index(
        "ix_deterministic_evidence_checks_organization_id",
        "deterministic_evidence_checks",
        ["organization_id"],
    )
    op.create_index("ix_deterministic_evidence_checks_site_id", "deterministic_evidence_checks", ["site_id"])
    op.create_index("ix_deterministic_evidence_checks_batch_id", "deterministic_evidence_checks", ["batch_id"])
    op.create_index("ix_deterministic_evidence_checks_check_type", "deterministic_evidence_checks", ["check_type"])
    op.create_index("ix_deterministic_evidence_checks_outcome", "deterministic_evidence_checks", ["outcome"])
    op.create_index(
        "ix_det_checks_org_batch_type",
        "deterministic_evidence_checks",
        ["organization_id", "batch_id", "check_type"],
    )

    # ------------------------------------------------------------------
    # 7. Deferred FKs from Phase 1 (training_records.sop_revision_id) & Phase 3 (batches.master_batch_record_id)
    # ------------------------------------------------------------------
    op.create_foreign_key(
        "fk_batches_master_batch_record_id",
        "batches",
        "master_batch_records",
        ["master_batch_record_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_batches_org_mbr",
        "batches",
        "master_batch_records",
        ["organization_id", "master_batch_record_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_training_records_sop_revision_id",
        "training_records",
        "sop_revisions",
        ["sop_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_training_org_sop_rev",
        "training_records",
        "sop_revisions",
        ["organization_id", "sop_revision_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_training_org_sop_rev", "training_records", type_="foreignkey")
    op.drop_constraint("fk_training_records_sop_revision_id", "training_records", type_="foreignkey")
    op.drop_constraint("fk_batches_org_mbr", "batches", type_="foreignkey")
    op.drop_constraint("fk_batches_master_batch_record_id", "batches", type_="foreignkey")

    op.drop_table("deterministic_evidence_checks")
    op.drop_table("equipment_usages")
    op.drop_table("process_parameter_observations")
    op.drop_table("batch_execution_steps")
    op.drop_table("batch_execution_records")
    op.drop_table("master_batch_records")
    op.drop_table("qc_test_results")
    op.drop_table("qc_samples")
    op.drop_table("qc_test_definitions")
    op.drop_table("calibration_records")
    op.drop_table("equipment")
    op.drop_table("sop_steps")

    op.drop_constraint("fk_sops_current_revision_id", "sops", type_="foreignkey")
    op.drop_table("sop_revisions")
    op.drop_table("sops")
