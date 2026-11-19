"""Phase 6 complaint communications (B6), email intake (B7), and CAPA & effectiveness checks (E3)

Revision ID: 0007_phase6_comm_email_capa
Revises: 0006_phase5_investigations
Create Date: 2026-11-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_phase6_comm_email_capa"
down_revision: Union[str, None] = "0006_phase5_investigations"
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
    # 1. B6: Extend complaint_communications with delivery/template/threading fields
    # ------------------------------------------------------------------
    op.add_column(
        "complaint_communications",
        sa.Column(
            "communication_type",
            sa.String(length=40),
            nullable=False,
            server_default="GENERAL",
        ),
    )
    op.add_column(
        "complaint_communications",
        sa.Column("template_code", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "complaint_communications",
        sa.Column("recipient_email", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "complaint_communications",
        sa.Column("recipient_name", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "complaint_communications",
        sa.Column(
            "delivery_status",
            sa.String(length=30),
            nullable=False,
            server_default="NOT_APPLICABLE",
        ),
    )
    op.add_column(
        "complaint_communications",
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "complaint_communications",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "complaint_communications",
        sa.Column("failed_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "complaint_communications",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "complaint_communications",
        sa.Column("in_reply_to_communication_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.add_column(
        "complaint_communications",
        sa.Column("metadata_json", JSONType, nullable=True),
    )

    op.create_unique_constraint(
        "uq_complaint_comms_org_id_id",
        "complaint_communications",
        ["organization_id", "id"],
    )
    op.create_foreign_key(
        "fk_complaint_comms_in_reply_to",
        "complaint_communications",
        "complaint_communications",
        ["in_reply_to_communication_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_complaint_comms_org_reply",
        "complaint_communications",
        "complaint_communications",
        ["organization_id", "in_reply_to_communication_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_complaint_comms_attempt_non_neg",
        "complaint_communications",
        "attempt_count >= 0",
    )
    op.create_index(
        "ix_complaint_comms_org_comp_sent",
        "complaint_communications",
        ["organization_id", "complaint_id", "sent_at"],
    )
    op.create_index(
        "ix_complaint_comms_delivery_status",
        "complaint_communications",
        ["delivery_status"],
    )
    op.create_index(
        "ix_complaint_comms_in_reply_to",
        "complaint_communications",
        ["in_reply_to_communication_id"],
    )

    # ------------------------------------------------------------------
    # 2. B7: Email Connections
    # ------------------------------------------------------------------
    op.create_table(
        "email_connections",
        *_common_tenant_version_columns(),
        sa.Column(
            "default_site_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("provider_type", sa.String(length=30), nullable=False, server_default="MOCK"),
        sa.Column("mailbox_address", sa.String(length=255), nullable=False),
        sa.Column("inbound_secret_hash", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "auto_create_threshold",
            sa.Numeric(precision=5, scale=4),
            nullable=False,
            server_default="0.7500",
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_cursor", sa.String(length=255), nullable=True),
        sa.Column("config_json", JSONType, nullable=True),
        sa.UniqueConstraint("organization_id", "id", name="uq_email_conns_org_id_id"),
        sa.UniqueConstraint("organization_id", "mailbox_address", name="uq_email_conns_org_mailbox"),
        sa.ForeignKeyConstraint(
            ["organization_id", "default_site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_email_conns_org_site",
        ),
        sa.CheckConstraint(
            "auto_create_threshold >= 0 AND auto_create_threshold <= 1",
            name="ck_email_conns_threshold_range",
        ),
    )
    op.create_index("ix_email_connections_organization_id", "email_connections", ["organization_id"])
    op.create_index("ix_email_connections_default_site_id", "email_connections", ["default_site_id"])
    op.create_index("ix_email_connections_org_active", "email_connections", ["organization_id", "is_active"])

    # ------------------------------------------------------------------
    # 3. B7: Inbound Emails
    # ------------------------------------------------------------------
    op.create_table(
        "inbound_emails",
        *_common_tenant_version_columns(),
        sa.Column(
            "email_connection_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("email_connections.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider_message_id", sa.String(length=255), nullable=False),
        sa.Column("thread_id", sa.String(length=255), nullable=True),
        sa.Column("in_reply_to_header", sa.String(length=255), nullable=True),
        sa.Column("from_email", sa.String(length=255), nullable=False),
        sa.Column("from_name", sa.String(length=200), nullable=True),
        sa.Column("to_addresses_json", JSONType, nullable=True),
        sa.Column("cc_addresses_json", JSONType, nullable=True),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("body_html_sanitized", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("classification", sa.String(length=30), nullable=True),
        sa.Column("classification_confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("classification_rationale", sa.Text(), nullable=True),
        sa.Column("triage_status", sa.String(length=40), nullable=False, server_default="RECEIVED"),
        sa.Column(
            "complaint_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("complaints.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "triaged_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("triaged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triage_notes", sa.Text(), nullable=True),
        sa.Column("raw_headers_json", JSONType, nullable=True),
        sa.UniqueConstraint("organization_id", "id", name="uq_inbound_emails_org_id_id"),
        sa.UniqueConstraint("organization_id", "provider_message_id", name="uq_inbound_emails_org_msg_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "email_connection_id"],
            ["email_connections.organization_id", "email_connections.id"],
            ondelete="RESTRICT",
            name="fk_inbound_emails_org_conn",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_inbound_emails_org_complaint",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "triaged_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_inbound_emails_org_triager",
        ),
        sa.CheckConstraint(
            "classification_confidence IS NULL OR (classification_confidence >= 0 AND classification_confidence <= 1)",
            name="ck_inbound_emails_conf_range",
        ),
    )
    op.create_index("ix_inbound_emails_organization_id", "inbound_emails", ["organization_id"])
    op.create_index("ix_inbound_emails_email_connection_id", "inbound_emails", ["email_connection_id"])
    op.create_index("ix_inbound_emails_complaint_id", "inbound_emails", ["complaint_id"])
    op.create_index("ix_inbound_emails_triage_status", "inbound_emails", ["triage_status"])
    op.create_index(
        "ix_inbound_emails_org_triage_recv",
        "inbound_emails",
        ["organization_id", "triage_status", "received_at"],
    )
    op.create_index("ix_inbound_emails_org_thread", "inbound_emails", ["organization_id", "thread_id"])

    # ------------------------------------------------------------------
    # 4. B7: Email Attachments
    # ------------------------------------------------------------------
    op.create_table(
        "email_attachments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "inbound_email_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("inbound_emails.id", ondelete="RESTRICT"),
            nullable=False,
        ),
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
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("storage_status", sa.String(length=30), nullable=False, server_default="PENDING"),
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
        sa.UniqueConstraint("organization_id", "id", name="uq_email_att_org_id_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "inbound_email_id"],
            ["inbound_emails.organization_id", "inbound_emails.id"],
            ondelete="RESTRICT",
            name="fk_email_att_org_inbound",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_email_att_org_doc",
        ),
        sa.CheckConstraint("byte_size >= 0", name="ck_email_att_byte_size_non_neg"),
    )
    op.create_index("ix_email_attachments_organization_id", "email_attachments", ["organization_id"])
    op.create_index("ix_email_attachments_inbound_email_id", "email_attachments", ["inbound_email_id"])
    op.create_index("ix_email_attachments_document_id", "email_attachments", ["document_id"])

    # ------------------------------------------------------------------
    # 5. B7: Email Processing Attempts (append-only audit log)
    # ------------------------------------------------------------------
    op.create_table(
        "email_processing_attempts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "inbound_email_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("inbound_emails.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("classifier_version", sa.String(length=60), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "organization_id",
            "inbound_email_id",
            "attempt_number",
            name="uq_email_proc_org_email_att",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "inbound_email_id"],
            ["inbound_emails.organization_id", "inbound_emails.id"],
            ondelete="RESTRICT",
            name="fk_email_proc_org_inbound",
        ),
        sa.CheckConstraint("attempt_number >= 1", name="ck_email_proc_attempt_pos"),
    )
    op.create_index(
        "ix_email_proc_attempts_organization_id",
        "email_processing_attempts",
        ["organization_id"],
    )
    op.create_index(
        "ix_email_proc_attempts_inbound_email_id",
        "email_processing_attempts",
        ["inbound_email_id"],
    )

    # ------------------------------------------------------------------
    # 6. E3: CAPAs
    # ------------------------------------------------------------------
    op.create_table(
        "capas",
        *_common_tenant_version_columns(),
        sa.Column(
            "site_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("capa_number", sa.String(length=60), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("capa_type", sa.String(length=30), nullable=False, server_default="CORRECTIVE"),
        sa.Column("source_type", sa.String(length=30), nullable=False, server_default="MANUAL"),
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
            "deviation_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("deviations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "is_mandatory_for_complaint_closure",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="MEDIUM"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        sa.Column(
            "owner_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "approver_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "approved_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("overdue_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closure_reason", sa.Text(), nullable=True),
        sa.Column("root_cause_summary", sa.Text(), nullable=True),
        sa.UniqueConstraint("organization_id", "id", name="uq_capas_org_id_id"),
        sa.UniqueConstraint("organization_id", "capa_number", name="uq_capas_org_capa_num"),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_capas_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_capas_org_complaint",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "investigation_id"],
            ["investigations.organization_id", "investigations.id"],
            ondelete="RESTRICT",
            name="fk_capas_org_inv",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "deviation_id"],
            ["deviations.organization_id", "deviations.id"],
            ondelete="RESTRICT",
            name="fk_capas_org_dev",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "owner_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_capas_org_owner",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "approver_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_capas_org_approver",
        ),
        sa.CheckConstraint(
            "approver_membership_id IS NULL OR approver_membership_id != owner_membership_id",
            name="ck_capas_approver_neq_owner",
        ),
    )
    op.create_index("ix_capas_organization_id", "capas", ["organization_id"])
    op.create_index("ix_capas_site_id", "capas", ["site_id"])
    op.create_index("ix_capas_complaint_id", "capas", ["complaint_id"])
    op.create_index("ix_capas_investigation_id", "capas", ["investigation_id"])
    op.create_index("ix_capas_deviation_id", "capas", ["deviation_id"])
    op.create_index("ix_capas_owner_membership_id", "capas", ["owner_membership_id"])
    op.create_index("ix_capas_status", "capas", ["status"])
    op.create_index("ix_capas_org_status_due", "capas", ["organization_id", "status", "due_at"])

    # ------------------------------------------------------------------
    # 7. E3: CAPA Actions
    # ------------------------------------------------------------------
    op.create_table(
        "capa_actions",
        *_common_tenant_version_columns(),
        sa.Column(
            "capa_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("capas.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action_seq", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(length=30), nullable=False, server_default="CORRECTIVE"),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "owner_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="PENDING"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "completed_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("implementation_notes", sa.Text(), nullable=True),
        sa.Column(
            "evidence_document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "evidence_document_version_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_capa_actions_org_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "capa_id",
            "action_seq",
            name="uq_capa_actions_org_capa_seq",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "capa_id"],
            ["capas.organization_id", "capas.id"],
            ondelete="RESTRICT",
            name="fk_capa_actions_org_capa",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "owner_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_capa_actions_org_owner",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "evidence_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_capa_actions_org_ev_doc",
        ),
        sa.CheckConstraint("action_seq >= 1", name="ck_capa_actions_seq_pos"),
    )
    op.create_index("ix_capa_actions_organization_id", "capa_actions", ["organization_id"])
    op.create_index("ix_capa_actions_capa_id", "capa_actions", ["capa_id"])
    op.create_index("ix_capa_actions_owner_membership_id", "capa_actions", ["owner_membership_id"])
    op.create_index("ix_capa_actions_status", "capa_actions", ["status"])
    op.create_index(
        "ix_capa_actions_org_capa_status",
        "capa_actions",
        ["organization_id", "capa_id", "status"],
    )

    # ------------------------------------------------------------------
    # 8. E3: CAPA Effectiveness Checks
    # ------------------------------------------------------------------
    op.create_table(
        "capa_effectiveness_checks",
        *_common_tenant_version_columns(),
        sa.Column(
            "capa_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("capas.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("check_seq", sa.Integer(), nullable=False),
        sa.Column("criteria_description", sa.Text(), nullable=False),
        sa.Column("evaluation_method", sa.Text(), nullable=True),
        sa.Column("planned_check_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="PENDING"),
        sa.Column(
            "reviewer_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "reviewer_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("findings_summary", sa.Text(), nullable=True),
        sa.Column(
            "evidence_document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "evidence_document_version_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_capa_eff_org_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "capa_id",
            "check_seq",
            name="uq_capa_eff_org_capa_seq",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "capa_id"],
            ["capas.organization_id", "capas.id"],
            ondelete="RESTRICT",
            name="fk_capa_eff_org_capa",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reviewer_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_capa_eff_org_reviewer",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "evidence_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_capa_eff_org_ev_doc",
        ),
        sa.CheckConstraint("check_seq >= 1", name="ck_capa_eff_seq_pos"),
    )
    op.create_index("ix_capa_eff_checks_organization_id", "capa_effectiveness_checks", ["organization_id"])
    op.create_index("ix_capa_eff_checks_capa_id", "capa_effectiveness_checks", ["capa_id"])
    op.create_index("ix_capa_eff_checks_status", "capa_effectiveness_checks", ["status"])
    op.create_index("ix_capa_eff_checks_planned_date", "capa_effectiveness_checks", ["planned_check_date"])

    # ------------------------------------------------------------------
    # 9. PostgreSQL append-only immutability trigger on email_processing_attempts
    # ------------------------------------------------------------------
    if op.get_context().dialect.name == "postgresql":
        op.execute(
            """
            CREATE TRIGGER trg_prevent_mutation_email_processing_attempts
            BEFORE UPDATE OR DELETE ON email_processing_attempts
            FOR EACH ROW EXECUTE FUNCTION prevent_append_only_mutation();
            """
        )


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_prevent_mutation_email_processing_attempts "
            "ON email_processing_attempts;"
        )

    op.drop_table("capa_effectiveness_checks")
    op.drop_table("capa_actions")
    op.drop_table("capas")
    op.drop_table("email_processing_attempts")
    op.drop_table("email_attachments")
    op.drop_table("inbound_emails")
    op.drop_table("email_connections")

    op.drop_index("ix_complaint_comms_in_reply_to", table_name="complaint_communications")
    op.drop_index("ix_complaint_comms_delivery_status", table_name="complaint_communications")
    op.drop_index("ix_complaint_comms_org_comp_sent", table_name="complaint_communications")
    op.drop_constraint("ck_complaint_comms_attempt_non_neg", "complaint_communications", type_="check")
    op.drop_constraint("fk_complaint_comms_org_reply", "complaint_communications", type_="foreignkey")
    op.drop_constraint("fk_complaint_comms_in_reply_to", "complaint_communications", type_="foreignkey")
    op.drop_constraint("uq_complaint_comms_org_id_id", "complaint_communications", type_="unique")

    op.drop_column("complaint_communications", "metadata_json")
    op.drop_column("complaint_communications", "in_reply_to_communication_id")
    op.drop_column("complaint_communications", "attempt_count")
    op.drop_column("complaint_communications", "failed_reason")
    op.drop_column("complaint_communications", "delivered_at")
    op.drop_column("complaint_communications", "provider_message_id")
    op.drop_column("complaint_communications", "delivery_status")
    op.drop_column("complaint_communications", "recipient_name")
    op.drop_column("complaint_communications", "recipient_email")
    op.drop_column("complaint_communications", "template_code")
    op.drop_column("complaint_communications", "communication_type")
