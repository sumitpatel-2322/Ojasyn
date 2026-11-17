"""Phase 2 document vault, OCR extraction, canonical complaints, SLA, communications, and copilot

Revision ID: 0003_phase2_docs_complaints
Revises: 0002_phase1_tenancy_actors
Create Date: 2026-11-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_phase2_docs_complaints"
down_revision: Union[str, None] = "0002_phase1_tenancy_actors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # 1. Central Regulated Document Vault (created before complaint_attachments FK)
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "site_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("document_code", sa.String(length=80), nullable=True),
        sa.Column("document_type", sa.String(length=40), nullable=False, server_default="OTHER"),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("confidentiality", sa.String(length=30), nullable=False, server_default="INTERNAL"),
        sa.Column(
            "owner_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="ACTIVE"),
        sa.Column("metadata_json", JSONType, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("organization_id", "id", name="uq_documents_org_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "document_code",
            name="uq_documents_org_code",
            postgresql_nulls_not_distinct=True,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_documents_org_site",
        ),
    )
    op.create_index("ix_documents_organization_id", "documents", ["organization_id"])
    op.create_index("ix_documents_site_id", "documents", ["site_id"])
    op.create_index("ix_documents_document_type", "documents", ["document_type"])
    op.create_index("ix_documents_status", "documents", ["status"])

    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version_label", sa.String(length=40), nullable=False, server_default="1.0"),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("object_key", sa.String(length=512), nullable=False, unique=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("scan_status", sa.String(length=30), nullable=False, server_default="CLEAN"),
        sa.Column("approval_status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        sa.Column(
            "uploaded_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "supersedes_version_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("document_id", "version_number", name="uq_doc_versions_doc_num"),
        sa.CheckConstraint("version_number >= 1", name="ck_doc_versions_num_pos"),
        sa.CheckConstraint("byte_size > 0", name="ck_doc_versions_byte_size_pos"),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
    op.create_index("ix_document_versions_checksum_sha256", "document_versions", ["checksum_sha256"])
    op.create_index("ix_document_versions_scan_status", "document_versions", ["scan_status"])
    op.create_index("ix_document_versions_approval_status", "document_versions", ["approval_status"])

    op.create_table(
        "extraction_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "document_version_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("extractor", sa.String(length=60), nullable=False, server_default="pdfplumber"),
        sa.Column("extractor_version", sa.String(length=40), nullable=False, server_default="1.0"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="QUEUED"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_extraction_jobs_document_version_id", "extraction_jobs", ["document_version_id"])
    op.create_index("ix_extraction_jobs_status", "extraction_jobs", ["status"])

    op.create_table(
        "extracted_contents",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "document_version_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("section_ref", sa.String(length=120), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("structured_data_json", JSONType, nullable=True),
        sa.Column("extraction_confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("page_number >= 1", name="ck_extracted_contents_page_pos"),
    )
    op.create_index("ix_extracted_contents_document_version_id", "extracted_contents", ["document_version_id"])
    if op.get_context().dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_extracted_contents_fts ON extracted_contents "
            "USING gin (to_tsvector('english', coalesce(text, '')))"
        )

    # 2. Canonical Complaint Aggregate & Child Tables
    op.create_table(
        "complaints",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "site_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("complaint_number", sa.String(length=60), nullable=False, unique=True),
        sa.Column(
            "complainant_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("complainants.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("source_channel", sa.String(length=30), nullable=False, server_default="WEBSITE"),
        sa.Column("source_record_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.Column("idempotency_payload_hash", sa.String(length=64), nullable=True),
        sa.Column("tracking_token_hash", sa.String(length=64), nullable=True),
        sa.Column("product_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("reported_product_name", sa.String(length=200), nullable=True),
        sa.Column("reported_batch_number", sa.String(length=100), nullable=True),
        sa.Column("reported_manufacturer", sa.String(length=200), nullable=True),
        sa.Column("reported_strength", sa.String(length=100), nullable=True),
        sa.Column("reported_dosage_form", sa.String(length=100), nullable=True),
        sa.Column("reported_manufacturing_date", sa.Date(), nullable=True),
        sa.Column("reported_expiry_date", sa.Date(), nullable=True),
        sa.Column("originating_site_block", sa.String(length=120), nullable=True),
        sa.Column("impacted_non_product_material", sa.String(length=150), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("defect_category", sa.String(length=80), nullable=True),
        sa.Column("reported_onset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purchase_date", sa.Date(), nullable=True),
        sa.Column("purchase_location", sa.String(length=200), nullable=True),
        sa.Column("affected_quantity_value", sa.Numeric(12, 4), nullable=True),
        sa.Column("affected_quantity_unit", sa.String(length=30), nullable=True),
        sa.Column("ai_severity", sa.String(length=20), nullable=True),
        sa.Column("ai_priority", sa.String(length=20), nullable=True),
        sa.Column("final_severity", sa.String(length=20), nullable=True),
        sa.Column("final_priority", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="RECEIVED"),
        sa.Column(
            "owner_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "duplicate_of_complaint_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("complaints.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "status = 'UNROUTED_TRIAGE' OR organization_id IS NOT NULL",
            name="ck_complaints_routed_requires_org",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_complaints_org_id_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_complaints_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "owner_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_complaints_org_owner_membership",
        ),
    )
    op.create_index("ix_complaints_organization_id", "complaints", ["organization_id"])
    op.create_index("ix_complaints_site_id", "complaints", ["site_id"])
    op.create_index("ix_complaints_complainant_id", "complaints", ["complainant_id"])
    op.create_index("ix_complaints_product_id", "complaints", ["product_id"])
    op.create_index("ix_complaints_reported_batch_number", "complaints", ["reported_batch_number"])
    op.create_index("ix_complaints_status", "complaints", ["status"])
    op.create_index("ix_complaints_owner_membership_id", "complaints", ["owner_membership_id"])
    op.create_index("ix_complaints_org_status", "complaints", ["organization_id", "status"])
    op.create_index(
        "ix_complaints_org_idempotency",
        "complaints",
        ["organization_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL AND idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_complaints_global_idempotency",
        "complaints",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL AND idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_complaints_tracking_token_hash",
        "complaints",
        ["tracking_token_hash"],
        unique=True,
        postgresql_where=sa.text("tracking_token_hash IS NOT NULL"),
    )

    op.create_table(
        "complaint_classifications",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "complaint_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("complaints.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(length=30), nullable=False, server_default="AI_SUGGESTION"),
        sa.Column("suggested_severity", sa.String(length=20), nullable=True),
        sa.Column("suggested_priority", sa.String(length=20), nullable=True),
        sa.Column("defect_category", sa.String(length=80), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=40), nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("risk_indicators_json", JSONType, nullable=True),
        sa.Column("evidence_refs_json", JSONType, nullable=True),
        sa.Column(
            "decided_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_complaint_classifications_org_complaint",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "decided_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_complaint_classifications_org_decider",
        ),
    )
    op.create_index("ix_complaint_classifications_organization_id", "complaint_classifications", ["organization_id"])
    op.create_index("ix_complaint_classifications_complaint_id", "complaint_classifications", ["complaint_id"])

    op.create_table(
        "complaint_batch_links",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "complaint_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("complaints.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("batch_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("reported_batch_number", sa.String(length=100), nullable=True),
        sa.Column("resolution_outcome", sa.String(length=40), nullable=False),
        sa.Column("match_method", sa.String(length=40), nullable=True),
        sa.Column("match_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("match_status", sa.String(length=30), nullable=False, server_default="PROPOSED"),
        sa.Column(
            "verified_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("match_evidence_json", JSONType, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_complaint_batch_links_org_complaint",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "verified_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_complaint_batch_links_org_verifier",
        ),
    )
    op.create_index("ix_complaint_batch_links_organization_id", "complaint_batch_links", ["organization_id"])
    op.create_index("ix_complaint_batch_links_complaint_id", "complaint_batch_links", ["complaint_id"])
    op.create_index("ix_complaint_batch_links_batch_id", "complaint_batch_links", ["batch_id"])

    op.create_table(
        "complaint_attachments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "complaint_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("complaints.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("attachment_type", sa.String(length=40), nullable=False, server_default="EVIDENCE"),
        sa.Column("caption", sa.String(length=255), nullable=True),
        sa.Column("is_external_visible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "uploaded_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_complaint_attachments_org_complaint",
        ),
        sa.UniqueConstraint("complaint_id", "document_id", name="uq_complaint_attachments_comp_doc"),
    )
    op.create_index("ix_complaint_attachments_organization_id", "complaint_attachments", ["organization_id"])
    op.create_index("ix_complaint_attachments_complaint_id", "complaint_attachments", ["complaint_id"])
    op.create_index("ix_complaint_attachments_document_id", "complaint_attachments", ["document_id"])

    op.create_table(
        "complaint_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "complaint_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("complaints.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("from_status", sa.String(length=30), nullable=True),
        sa.Column("to_status", sa.String(length=30), nullable=True),
        sa.Column(
            "actor_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "actor_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("summary", sa.String(length=300), nullable=False),
        sa.Column("details_json", JSONType, nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_complaint_events_org_complaint",
        ),
    )
    op.create_index("ix_complaint_events_organization_id", "complaint_events", ["organization_id"])
    op.create_index("ix_complaint_events_complaint_id", "complaint_events", ["complaint_id"])
    op.create_index("ix_complaint_events_comp_time", "complaint_events", ["complaint_id", "occurred_at"])

    op.create_table(
        "complaint_assignments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "complaint_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("complaints.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assignee_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assigned_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("assignment_role", sa.String(length=30), nullable=False, server_default="OWNER"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unassigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_complaint_assignments_org_complaint",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "assignee_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_complaint_assignments_org_assignee",
        ),
    )
    op.create_index("ix_complaint_assignments_organization_id", "complaint_assignments", ["organization_id"])
    op.create_index("ix_complaint_assignments_complaint_id", "complaint_assignments", ["complaint_id"])
    op.create_index("ix_complaint_assignments_assignee_membership_id", "complaint_assignments", ["assignee_membership_id"])

    op.create_table(
        "sla_clocks",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "complaint_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("complaints.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sla_stage", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="ON_TRACK"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("breached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accumulated_pause_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_sla_clocks_org_complaint",
        ),
    )
    op.create_index("ix_sla_clocks_organization_id", "sla_clocks", ["organization_id"])
    op.create_index("ix_sla_clocks_complaint_id", "sla_clocks", ["complaint_id"])
    op.create_index("ix_sla_clocks_status", "sla_clocks", ["status"])

    op.create_table(
        "complaint_communications",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "complaint_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("complaints.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(length=30), nullable=False, server_default="PORTAL"),
        sa.Column("direction", sa.String(length=20), nullable=False),
        sa.Column(
            "sender_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "sender_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("is_internal_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_information_request", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_complaint_comms_org_complaint",
        ),
    )
    op.create_index("ix_complaint_communications_organization_id", "complaint_communications", ["organization_id"])
    op.create_index("ix_complaint_communications_complaint_id", "complaint_communications", ["complaint_id"])

    # 3. Conversational Complaint Copilot Tables
    op.create_table(
        "copilot_sessions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("session_token_hash", sa.String(length=64), nullable=True),
        sa.Column("draft_state_json", JSONType, nullable=False),
        sa.Column("session_status", sa.String(length=30), nullable=False, server_default="ACTIVE_DRAFT"),
        sa.Column(
            "converted_complaint_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("complaints.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_copilot_sessions_organization_id", "copilot_sessions", ["organization_id"])
    op.create_index("ix_copilot_sessions_user_id", "copilot_sessions", ["user_id"])
    op.create_index("ix_copilot_sessions_session_status", "copilot_sessions", ["session_status"])
    op.create_index(
        "ix_copilot_sessions_token_hash",
        "copilot_sessions",
        ["session_token_hash"],
        unique=True,
        postgresql_where=sa.text("session_token_hash IS NOT NULL"),
    )

    op.create_table(
        "copilot_messages",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "session_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("copilot_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("intent_type", sa.String(length=20), nullable=True),
        sa.Column("extracted_fields_json", JSONType, nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_copilot_messages_session_id", "copilot_messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_copilot_messages_session_id", table_name="copilot_messages")
    op.drop_table("copilot_messages")

    op.drop_index("ix_copilot_sessions_token_hash", table_name="copilot_sessions")
    op.drop_index("ix_copilot_sessions_session_status", table_name="copilot_sessions")
    op.drop_index("ix_copilot_sessions_user_id", table_name="copilot_sessions")
    op.drop_index("ix_copilot_sessions_organization_id", table_name="copilot_sessions")
    op.drop_table("copilot_sessions")

    op.drop_index("ix_complaint_communications_complaint_id", table_name="complaint_communications")
    op.drop_index("ix_complaint_communications_organization_id", table_name="complaint_communications")
    op.drop_table("complaint_communications")

    op.drop_index("ix_sla_clocks_status", table_name="sla_clocks")
    op.drop_index("ix_sla_clocks_complaint_id", table_name="sla_clocks")
    op.drop_index("ix_sla_clocks_organization_id", table_name="sla_clocks")
    op.drop_table("sla_clocks")

    op.drop_index("ix_complaint_assignments_assignee_membership_id", table_name="complaint_assignments")
    op.drop_index("ix_complaint_assignments_complaint_id", table_name="complaint_assignments")
    op.drop_index("ix_complaint_assignments_organization_id", table_name="complaint_assignments")
    op.drop_table("complaint_assignments")

    op.drop_index("ix_complaint_events_comp_time", table_name="complaint_events")
    op.drop_index("ix_complaint_events_complaint_id", table_name="complaint_events")
    op.drop_index("ix_complaint_events_organization_id", table_name="complaint_events")
    op.drop_table("complaint_events")

    op.drop_index("ix_complaint_attachments_document_id", table_name="complaint_attachments")
    op.drop_index("ix_complaint_attachments_complaint_id", table_name="complaint_attachments")
    op.drop_index("ix_complaint_attachments_organization_id", table_name="complaint_attachments")
    op.drop_table("complaint_attachments")

    op.drop_index("ix_complaint_batch_links_batch_id", table_name="complaint_batch_links")
    op.drop_index("ix_complaint_batch_links_complaint_id", table_name="complaint_batch_links")
    op.drop_index("ix_complaint_batch_links_organization_id", table_name="complaint_batch_links")
    op.drop_table("complaint_batch_links")

    op.drop_index("ix_complaint_classifications_complaint_id", table_name="complaint_classifications")
    op.drop_index("ix_complaint_classifications_organization_id", table_name="complaint_classifications")
    op.drop_table("complaint_classifications")

    op.drop_index("ix_complaints_tracking_token_hash", table_name="complaints")
    op.drop_index("ix_complaints_global_idempotency", table_name="complaints")
    op.drop_index("ix_complaints_org_idempotency", table_name="complaints")
    op.drop_index("ix_complaints_org_status", table_name="complaints")
    op.drop_index("ix_complaints_owner_membership_id", table_name="complaints")
    op.drop_index("ix_complaints_status", table_name="complaints")
    op.drop_index("ix_complaints_reported_batch_number", table_name="complaints")
    op.drop_index("ix_complaints_product_id", table_name="complaints")
    op.drop_index("ix_complaints_complainant_id", table_name="complaints")
    op.drop_index("ix_complaints_site_id", table_name="complaints")
    op.drop_index("ix_complaints_organization_id", table_name="complaints")
    op.drop_table("complaints")

    if op.get_context().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_extracted_contents_fts")
    op.drop_index("ix_extracted_contents_document_version_id", table_name="extracted_contents")
    op.drop_table("extracted_contents")

    op.drop_index("ix_extraction_jobs_status", table_name="extraction_jobs")
    op.drop_index("ix_extraction_jobs_document_version_id", table_name="extraction_jobs")
    op.drop_table("extraction_jobs")

    op.drop_index("ix_document_versions_approval_status", table_name="document_versions")
    op.drop_index("ix_document_versions_scan_status", table_name="document_versions")
    op.drop_index("ix_document_versions_checksum_sha256", table_name="document_versions")
    op.drop_index("ix_document_versions_document_id", table_name="document_versions")
    op.drop_table("document_versions")

    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_index("ix_documents_document_type", table_name="documents")
    op.drop_index("ix_documents_site_id", table_name="documents")
    op.drop_index("ix_documents_organization_id", table_name="documents")
    op.drop_table("documents")
