"""Phase 5 AI investigation, evidence provenance, deterministic SOP compliance, RCA, DOCX reports & core Deviation (E1)

Revision ID: 0006_phase5_investigations
Revises: 0005_phase4_mfg_evidence
Create Date: 2026-11-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_phase5_investigations"
down_revision: Union[str, None] = "0005_phase4_mfg_evidence"
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
    # 1. D1: Investigations
    # ------------------------------------------------------------------
    op.create_table(
        "investigations",
        *_common_tenant_version_columns(),
        sa.Column(
            "complaint_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("complaints.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        # Circular FK to deviations.id added after deviations table creation
        sa.Column("deviation_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "batch_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "product_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "site_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("investigation_number", sa.String(length=60), nullable=False),
        sa.Column("trigger_type", sa.String(length=30), nullable=False, server_default="COMPLAINT"),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("scope_summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="QUEUED"),
        sa.Column(
            "owner_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "qa_reviewed_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "qa_reviewed_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("qa_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("qa_decision", sa.String(length=30), nullable=True),
        sa.Column("qa_decision_comments", sa.Text(), nullable=True),
        sa.Column(
            "approval_record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("approval_records.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closure_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("organization_id", "id", name="uq_investigations_org_id_id"),
        sa.UniqueConstraint("organization_id", "investigation_number", name="uq_investigations_org_num"),
        sa.ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_investigations_org_complaint",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_investigations_org_batch",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "product_id"],
            ["products.organization_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_investigations_org_product",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_investigations_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "owner_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_investigations_org_owner",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "qa_reviewed_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_investigations_org_qa_reviewer",
        ),
    )
    op.create_index("ix_investigations_organization_id", "investigations", ["organization_id"])
    op.create_index("ix_investigations_complaint_id", "investigations", ["complaint_id"])
    op.create_index("ix_investigations_deviation_id", "investigations", ["deviation_id"])
    op.create_index("ix_investigations_batch_id", "investigations", ["batch_id"])
    op.create_index("ix_investigations_product_id", "investigations", ["product_id"])
    op.create_index("ix_investigations_site_id", "investigations", ["site_id"])
    op.create_index("ix_investigations_status", "investigations", ["status"])
    op.create_index("ix_investigations_owner_membership_id", "investigations", ["owner_membership_id"])
    op.create_index("ix_investigations_org_status", "investigations", ["organization_id", "status"])

    # ------------------------------------------------------------------
    # 2. D1/D2: Investigation Runs
    # ------------------------------------------------------------------
    op.create_table(
        "investigation_runs",
        *_common_tenant_version_columns(),
        sa.Column(
            "investigation_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("run_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("workflow_version", sa.String(length=40), nullable=False, server_default="phase5-v1"),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=40), nullable=True),
        sa.Column("rule_engine_version", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="QUEUED"),
        sa.Column("waiting_reason", sa.String(length=120), nullable=True),
        sa.Column("resume_from_step_seq", sa.Integer(), nullable=True),
        sa.Column("checkpoint_state_json", JSONType, nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.Column(
            "triggered_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("summary_json", JSONType, nullable=True),
        sa.UniqueConstraint("organization_id", "id", name="uq_inv_runs_org_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "investigation_id",
            "run_number",
            name="uq_inv_runs_org_inv_num",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "investigation_id"],
            ["investigations.organization_id", "investigations.id"],
            ondelete="RESTRICT",
            name="fk_inv_runs_org_inv",
        ),
        sa.CheckConstraint("run_number >= 1", name="ck_inv_runs_num_pos"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_inv_runs_attempt_non_neg"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_inv_runs_max_attempts_pos"),
        sa.CheckConstraint(
            "resume_from_step_seq IS NULL OR resume_from_step_seq >= 1",
            name="ck_inv_runs_resume_seq_pos",
        ),
    )
    op.create_index("ix_investigation_runs_organization_id", "investigation_runs", ["organization_id"])
    op.create_index("ix_investigation_runs_investigation_id", "investigation_runs", ["investigation_id"])
    op.create_index("ix_investigation_runs_status", "investigation_runs", ["status"])
    op.create_index(
        "ix_inv_runs_org_idemp",
        "investigation_runs",
        ["organization_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_inv_runs_org_inv_status",
        "investigation_runs",
        ["organization_id", "investigation_id", "status"],
    )

    # ------------------------------------------------------------------
    # 3. D1: Investigation Steps
    # ------------------------------------------------------------------
    op.create_table(
        "investigation_steps",
        *_common_tenant_version_columns(),
        sa.Column(
            "investigation_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "investigation_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("investigation_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("step_seq", sa.Integer(), nullable=False),
        sa.Column("node_name", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="STARTED"),
        sa.Column("input_refs_json", JSONType, nullable=True),
        sa.Column("output_refs_json", JSONType, nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("organization_id", "id", name="uq_inv_steps_org_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "investigation_run_id",
            "step_seq",
            name="uq_inv_steps_org_run_seq",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "investigation_id"],
            ["investigations.organization_id", "investigations.id"],
            ondelete="RESTRICT",
            name="fk_inv_steps_org_inv",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "investigation_run_id"],
            ["investigation_runs.organization_id", "investigation_runs.id"],
            ondelete="RESTRICT",
            name="fk_inv_steps_org_run",
        ),
        sa.CheckConstraint("step_seq >= 1", name="ck_inv_steps_seq_pos"),
    )
    op.create_index("ix_investigation_steps_organization_id", "investigation_steps", ["organization_id"])
    op.create_index("ix_investigation_steps_investigation_id", "investigation_steps", ["investigation_id"])
    op.create_index("ix_investigation_steps_investigation_run_id", "investigation_steps", ["investigation_run_id"])
    op.create_index("ix_investigation_steps_status", "investigation_steps", ["status"])

    # ------------------------------------------------------------------
    # 4. D4: Evidence References (immutable provenance pointers)
    # ------------------------------------------------------------------
    op.create_table(
        "evidence_references",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "investigation_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "investigation_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("investigation_runs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_table", sa.String(length=80), nullable=False),
        sa.Column("source_record_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_row_version", sa.Integer(), nullable=True),
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
        sa.Column("document_checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("section_ref", sa.String(length=120), nullable=True),
        sa.Column("field_path", sa.String(length=160), nullable=True),
        sa.Column("quoted_value", sa.Text(), nullable=True),
        sa.Column("provenance_metadata_json", JSONType, nullable=True),
        sa.Column("collected_by", sa.String(length=40), nullable=False, server_default="SYSTEM_WORKER"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_ev_refs_org_id_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "investigation_id"],
            ["investigations.organization_id", "investigations.id"],
            ondelete="RESTRICT",
            name="fk_ev_refs_org_inv",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "investigation_run_id"],
            ["investigation_runs.organization_id", "investigation_runs.id"],
            ondelete="RESTRICT",
            name="fk_ev_refs_org_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_ev_refs_org_doc",
        ),
        sa.CheckConstraint("page_number IS NULL OR page_number >= 1", name="ck_ev_refs_page_pos"),
    )
    op.create_index("ix_evidence_references_organization_id", "evidence_references", ["organization_id"])
    op.create_index("ix_evidence_references_investigation_id", "evidence_references", ["investigation_id"])
    op.create_index("ix_evidence_references_investigation_run_id", "evidence_references", ["investigation_run_id"])
    op.create_index("ix_evidence_references_source_type", "evidence_references", ["source_type"])
    op.create_index("ix_evidence_references_source_record_id", "evidence_references", ["source_record_id"])
    op.create_index(
        "ix_ev_refs_inv_source",
        "evidence_references",
        ["investigation_id", "source_table", "source_record_id"],
    )

    # ------------------------------------------------------------------
    # 5. D3: SOP Compliance Results (immutable deterministic checks)
    # ------------------------------------------------------------------
    op.create_table(
        "sop_compliance_results",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "investigation_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "investigation_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("investigation_runs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "batch_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "sop_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sops.id", ondelete="RESTRICT"),
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
            "deterministic_check_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("deterministic_evidence_checks.id", ondelete="RESTRICT"),
            nullable=True,
        ),
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
            "qc_test_result_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("qc_test_results.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "evidence_ref_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("evidence_references.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("check_type", sa.String(length=60), nullable=False),
        sa.Column("rule_identifier", sa.String(length=80), nullable=False),
        sa.Column("rule_version", sa.String(length=40), nullable=False, server_default="1.0.0"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="PASS"),
        sa.Column("actual_value_text", sa.Text(), nullable=True),
        sa.Column("actual_numeric_value", sa.Numeric(18, 6), nullable=True),
        sa.Column("expected_requirement_text", sa.Text(), nullable=True),
        sa.Column("expected_min_value", sa.Numeric(18, 6), nullable=True),
        sa.Column("expected_max_value", sa.Numeric(18, 6), nullable=True),
        sa.Column("unit_code", sa.String(length=30), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("deviation_suggested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("suggested_deviation_payload_json", JSONType, nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_sop_comp_org_id_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "investigation_id"],
            ["investigations.organization_id", "investigations.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_inv",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "investigation_run_id"],
            ["investigation_runs.organization_id", "investigation_runs.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_batch",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "sop_id"],
            ["sops.organization_id", "sops.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_sop",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "sop_revision_id"],
            ["sop_revisions.organization_id", "sop_revisions.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_sop_rev",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "sop_step_id"],
            ["sop_steps.organization_id", "sop_steps.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_sop_step",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "deterministic_check_id"],
            ["deterministic_evidence_checks.organization_id", "deterministic_evidence_checks.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_det_chk",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_execution_record_id"],
            ["batch_execution_records.organization_id", "batch_execution_records.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_exec_rec",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_execution_step_id"],
            ["batch_execution_steps.organization_id", "batch_execution_steps.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_exec_step",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "observation_id"],
            ["process_parameter_observations.organization_id", "process_parameter_observations.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_obs",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "equipment_id"],
            ["equipment.organization_id", "equipment.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_equip",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "calibration_record_id"],
            ["calibration_records.organization_id", "calibration_records.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_cal",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "qc_test_result_id"],
            ["qc_test_results.organization_id", "qc_test_results.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_qc_res",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "evidence_ref_id"],
            ["evidence_references.organization_id", "evidence_references.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_ev_ref",
        ),
    )
    op.create_index("ix_sop_compliance_results_organization_id", "sop_compliance_results", ["organization_id"])
    op.create_index("ix_sop_compliance_results_investigation_id", "sop_compliance_results", ["investigation_id"])
    op.create_index("ix_sop_compliance_results_investigation_run_id", "sop_compliance_results", ["investigation_run_id"])
    op.create_index("ix_sop_compliance_results_batch_id", "sop_compliance_results", ["batch_id"])
    op.create_index("ix_sop_compliance_results_evidence_ref_id", "sop_compliance_results", ["evidence_ref_id"])
    op.create_index("ix_sop_compliance_results_status", "sop_compliance_results", ["status"])
    op.create_index("ix_sop_comp_inv_status", "sop_compliance_results", ["investigation_id", "status"])

    # ------------------------------------------------------------------
    # 6. D5: Investigation Findings
    # ------------------------------------------------------------------
    op.create_table(
        "investigation_findings",
        *_common_tenant_version_columns(),
        sa.Column(
            "investigation_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "investigation_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("investigation_runs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("finding_code", sa.String(length=60), nullable=False),
        sa.Column("category", sa.String(length=60), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("is_verified_observation", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("severity", sa.String(length=20), nullable=True),
        sa.Column("evidence_ref_ids_json", JSONType, nullable=False, server_default="[]"),
        sa.Column(
            "sop_compliance_result_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sop_compliance_results.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("source_origin", sa.String(length=30), nullable=False, server_default="DETERMINISTIC"),
        sa.UniqueConstraint("organization_id", "id", name="uq_inv_findings_org_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "investigation_id",
            "finding_code",
            name="uq_inv_findings_org_inv_code",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "investigation_id"],
            ["investigations.organization_id", "investigations.id"],
            ondelete="RESTRICT",
            name="fk_inv_findings_org_inv",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "investigation_run_id"],
            ["investigation_runs.organization_id", "investigation_runs.id"],
            ondelete="RESTRICT",
            name="fk_inv_findings_org_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "sop_compliance_result_id"],
            ["sop_compliance_results.organization_id", "sop_compliance_results.id"],
            ondelete="RESTRICT",
            name="fk_inv_findings_org_comp",
        ),
    )
    op.create_index("ix_investigation_findings_organization_id", "investigation_findings", ["organization_id"])
    op.create_index("ix_investigation_findings_investigation_id", "investigation_findings", ["investigation_id"])
    op.create_index(
        "ix_investigation_findings_investigation_run_id",
        "investigation_findings",
        ["investigation_run_id"],
    )

    # ------------------------------------------------------------------
    # 7. D5/D7: Root Cause Hypotheses
    # ------------------------------------------------------------------
    op.create_table(
        "root_cause_hypotheses",
        *_common_tenant_version_columns(),
        sa.Column(
            "investigation_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "investigation_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("investigation_runs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("hypothesis_code", sa.String(length=60), nullable=False),
        sa.Column("category", sa.String(length=60), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("supporting_evidence_ids_json", JSONType, nullable=False, server_default="[]"),
        sa.Column("contradicting_evidence_ids_json", JSONType, nullable=False, server_default="[]"),
        sa.Column("uncertainty_level", sa.String(length=20), nullable=False, server_default="MEDIUM"),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("recommended_verification", sa.Text(), nullable=True),
        sa.Column("limitations", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="PROPOSED"),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=40), nullable=True),
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
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_comments", sa.Text(), nullable=True),
        sa.Column(
            "approval_record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("approval_records.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_rc_hyp_org_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "investigation_id",
            "hypothesis_code",
            name="uq_rc_hyp_org_inv_code",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "investigation_id"],
            ["investigations.organization_id", "investigations.id"],
            ondelete="RESTRICT",
            name="fk_rc_hyp_org_inv",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "investigation_run_id"],
            ["investigation_runs.organization_id", "investigation_runs.id"],
            ondelete="RESTRICT",
            name="fk_rc_hyp_org_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reviewed_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_rc_hyp_org_reviewer",
        ),
        sa.CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
            name="ck_rc_hyp_conf_range",
        ),
        sa.CheckConstraint(
            "reviewed_by_user_id IS NULL OR created_by_user_id IS NULL OR reviewed_by_user_id != created_by_user_id",
            name="ck_rc_hyp_maker_checker_user",
        ),
    )
    op.create_index("ix_root_cause_hypotheses_organization_id", "root_cause_hypotheses", ["organization_id"])
    op.create_index("ix_root_cause_hypotheses_investigation_id", "root_cause_hypotheses", ["investigation_id"])
    op.create_index(
        "ix_root_cause_hypotheses_investigation_run_id",
        "root_cause_hypotheses",
        ["investigation_run_id"],
    )
    op.create_index("ix_root_cause_hypotheses_status", "root_cause_hypotheses", ["status"])

    # ------------------------------------------------------------------
    # 8. D6/D7: Generated Reports
    # ------------------------------------------------------------------
    op.create_table(
        "generated_reports",
        *_common_tenant_version_columns(),
        sa.Column(
            "investigation_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "investigation_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("investigation_runs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("report_number", sa.String(length=60), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="GENERATED"),
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
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("report_snapshot_json", JSONType, nullable=True),
        sa.Column(
            "generated_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
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
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_comments", sa.Text(), nullable=True),
        sa.Column(
            "approval_record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("approval_records.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "supersedes_report_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("generated_reports.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_gen_reports_org_id_id"),
        sa.UniqueConstraint("organization_id", "report_number", name="uq_gen_reports_org_num"),
        sa.UniqueConstraint(
            "organization_id",
            "investigation_id",
            "version_number",
            name="uq_gen_reports_org_inv_ver",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "investigation_id"],
            ["investigations.organization_id", "investigations.id"],
            ondelete="RESTRICT",
            name="fk_gen_reports_org_inv",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "investigation_run_id"],
            ["investigation_runs.organization_id", "investigation_runs.id"],
            ondelete="RESTRICT",
            name="fk_gen_reports_org_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_gen_reports_org_doc",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reviewed_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_gen_reports_org_reviewer",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "supersedes_report_id"],
            ["generated_reports.organization_id", "generated_reports.id"],
            ondelete="RESTRICT",
            name="fk_gen_reports_org_supersedes",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_gen_reports_ver_pos"),
        sa.CheckConstraint(
            "reviewed_by_user_id IS NULL OR generated_by_user_id IS NULL OR reviewed_by_user_id != generated_by_user_id",
            name="ck_gen_reports_maker_checker_user",
        ),
    )
    op.create_index("ix_generated_reports_organization_id", "generated_reports", ["organization_id"])
    op.create_index("ix_generated_reports_investigation_id", "generated_reports", ["investigation_id"])
    op.create_index("ix_generated_reports_investigation_run_id", "generated_reports", ["investigation_run_id"])
    op.create_index("ix_generated_reports_status", "generated_reports", ["status"])

    # ------------------------------------------------------------------
    # 9. E1: Core Quality Deviations
    # ------------------------------------------------------------------
    op.create_table(
        "deviations",
        *_common_tenant_version_columns(),
        sa.Column(
            "site_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("deviation_number", sa.String(length=60), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False, server_default="MANUAL"),
        sa.Column("classification", sa.String(length=20), nullable=False, server_default="MAJOR"),
        sa.Column("is_mandatory_for_closure", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("immediate_containment", sa.Text(), nullable=True),
        sa.Column("impact_assessment", sa.Text(), nullable=True),
        sa.Column("root_cause_summary", sa.Text(), nullable=True),
        sa.Column(
            "complaint_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("complaints.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "investigation_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "batch_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "batch_execution_step_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batch_execution_steps.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "sop_compliance_result_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sop_compliance_results.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "qc_test_result_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("qc_test_results.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "owner_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="OPEN"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "submitted_for_review_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "submitted_for_review_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("submitted_for_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "closed_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "closed_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closure_comments", sa.Text(), nullable=True),
        sa.Column(
            "approval_record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("approval_records.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_deviations_org_id_id"),
        sa.UniqueConstraint("organization_id", "deviation_number", name="uq_deviations_org_num"),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_complaint",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "investigation_id"],
            ["investigations.organization_id", "investigations.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_inv",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_batch",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_execution_step_id"],
            ["batch_execution_steps.organization_id", "batch_execution_steps.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_exec_step",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "sop_compliance_result_id"],
            ["sop_compliance_results.organization_id", "sop_compliance_results.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_sop_comp",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "qc_test_result_id"],
            ["qc_test_results.organization_id", "qc_test_results.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_qc_res",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "owner_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_owner",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "submitted_for_review_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_submitter",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "closed_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_closer",
        ),
        sa.CheckConstraint(
            "closed_by_user_id IS NULL OR created_by_user_id IS NULL OR closed_by_user_id != created_by_user_id",
            name="ck_deviations_maker_checker_user",
        ),
        sa.CheckConstraint(
            "closed_by_user_id IS NULL OR submitted_for_review_by_user_id IS NULL OR closed_by_user_id != submitted_for_review_by_user_id",
            name="ck_deviations_submit_close_user",
        ),
        sa.CheckConstraint(
            "closed_by_membership_id IS NULL OR submitted_for_review_by_membership_id IS NULL OR closed_by_membership_id != submitted_for_review_by_membership_id",
            name="ck_deviations_maker_checker_member",
        ),
    )
    op.create_index("ix_deviations_organization_id", "deviations", ["organization_id"])
    op.create_index("ix_deviations_site_id", "deviations", ["site_id"])
    op.create_index("ix_deviations_source_type", "deviations", ["source_type"])
    op.create_index("ix_deviations_classification", "deviations", ["classification"])
    op.create_index("ix_deviations_complaint_id", "deviations", ["complaint_id"])
    op.create_index("ix_deviations_investigation_id", "deviations", ["investigation_id"])
    op.create_index("ix_deviations_batch_id", "deviations", ["batch_id"])
    op.create_index("ix_deviations_status", "deviations", ["status"])
    op.create_index("ix_deviations_org_status", "deviations", ["organization_id", "status"])

    # ------------------------------------------------------------------
    # 10. Deferred circular FKs: investigations.deviation_id -> deviations.id
    # ------------------------------------------------------------------
    op.create_foreign_key(
        "fk_investigations_deviation_id",
        "investigations",
        "deviations",
        ["deviation_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_investigations_org_dev",
        "investigations",
        "deviations",
        ["organization_id", "deviation_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )

    # ------------------------------------------------------------------
    # 11. PostgreSQL append-only immutability triggers on provenance & compliance logs
    # ------------------------------------------------------------------
    if op.get_context().dialect.name == "postgresql":
        for table_name in ("evidence_references", "sop_compliance_results"):
            op.execute(
                f"""
                CREATE TRIGGER trg_prevent_mutation_{table_name}
                BEFORE UPDATE OR DELETE ON {table_name}
                FOR EACH ROW EXECUTE FUNCTION prevent_append_only_mutation();
                """
            )


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        for table_name in ("sop_compliance_results", "evidence_references"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_prevent_mutation_{table_name} ON {table_name};")

    op.drop_constraint("fk_investigations_org_dev", "investigations", type_="foreignkey")
    op.drop_constraint("fk_investigations_deviation_id", "investigations", type_="foreignkey")

    op.drop_table("deviations")
    op.drop_table("generated_reports")
    op.drop_table("root_cause_hypotheses")
    op.drop_table("investigation_findings")
    op.drop_table("sop_compliance_results")
    op.drop_table("evidence_references")
    op.drop_table("investigation_steps")
    op.drop_table("investigation_runs")
    op.drop_table("investigations")
