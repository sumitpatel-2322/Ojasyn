"""Phase 0 foundation: tenancy, identity, RBAC, audit, security events, notifications, outbox

Revision ID: 0001_phase0_foundation
Revises:
Create Date: 2026-11-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_phase0_foundation"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Tier 0: Global identity & RBAC catalogs
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False, unique=True),
        sa.Column("mobile_e164", sa.String(length=20), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("full_name", sa.String(length=150), nullable=False),
        sa.Column("account_status", sa.String(length=30), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mobile_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mfa_state", sa.String(length=30), nullable=False),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_valid_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("failed_login_attempts >= 0", name="ck_users_failed_login_non_neg"),
    )
    op.create_index("ix_users_email_lower", "users", [sa.text("lower(email)")], unique=True)

    op.create_table(
        "user_credentials",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("credential_type", sa.String(length=30), nullable=False),
        sa.Column("credential_hash", sa.String(length=255), nullable=True),
        sa.Column("provider_subject", sa.String(length=255), nullable=True),
        sa.Column("token_family_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "replaced_by_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("user_credentials.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("session_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("replaced_by_id IS NULL OR replaced_by_id != id", name="ck_user_creds_no_self_replace"),
    )
    op.create_index("ix_user_credentials_user_id", "user_credentials", ["user_id"])
    op.create_index("ix_user_credentials_token_family", "user_credentials", ["token_family_id"])
    op.create_index(
        "ix_user_credentials_active_user_type",
        "user_credentials",
        ["user_id", "credential_type"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("role_code", sa.String(length=60), nullable=False, unique=True),
        sa.Column("role_name", sa.String(length=100), nullable=False),
        sa.Column("role_scope", sa.String(length=20), nullable=False),
        sa.Column("is_system_role", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
    )

    op.create_table(
        "permissions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("permission_code", sa.String(length=100), nullable=False, unique=True),
        sa.Column("resource", sa.String(length=60), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Uuid(as_uuid=True), sa.ForeignKey("roles.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column(
            "permission_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("permissions.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("constraint_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # 2. Tier 1: Organizations, Addresses (with deferred registered_address_id FK), Sites, Departments
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("legal_name", sa.String(length=200), nullable=False),
        sa.Column("trade_name", sa.String(length=200), nullable=True),
        sa.Column("organization_type", sa.String(length=50), nullable=False),
        sa.Column("registration_number", sa.String(length=80), nullable=True),
        sa.Column("cin", sa.String(length=30), nullable=True),
        sa.Column("pan", sa.String(length=20), nullable=True),
        sa.Column("gstin", sa.String(length=20), nullable=True),
        sa.Column("country_of_registration", sa.String(length=2), nullable=False),
        sa.Column("incorporation_date", sa.Date(), nullable=True),
        sa.Column("website_url", sa.String(length=255), nullable=True),
        sa.Column("business_email", sa.String(length=254), nullable=False),
        sa.Column("business_phone", sa.String(length=32), nullable=True),
        sa.Column("registered_address_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("onboarding_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_organizations_status", "organizations", ["status"])

    op.create_table(
        "organization_addresses",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("address_type", sa.String(length=30), nullable=False),
        sa.Column("line_1", sa.String(length=200), nullable=False),
        sa.Column("line_2", sa.String(length=200), nullable=True),
        sa.Column("locality", sa.String(length=120), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=False),
        sa.Column("state_region", sa.String(length=100), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="ck_org_addresses_valid_dates"),
        sa.UniqueConstraint("organization_id", "id", name="uq_org_addresses_org_id_id"),
    )
    op.create_index("ix_organization_addresses_organization_id", "organization_addresses", ["organization_id"])

    op.create_foreign_key(
        "fk_organizations_registered_address",
        "organizations",
        "organization_addresses",
        ["registered_address_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "sites",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("site_code", sa.String(length=40), nullable=False),
        sa.Column("site_name", sa.String(length=200), nullable=False),
        sa.Column("site_type", sa.String(length=40), nullable=False),
        sa.Column("operations_scope", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "address_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_addresses.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "primary_contact_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("is_primary_site", sa.Boolean(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("organization_id", "site_code", name="uq_sites_org_code"),
        sa.UniqueConstraint("organization_id", "id", name="uq_sites_org_id_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "address_id"],
            ["organization_addresses.organization_id", "organization_addresses.id"],
            ondelete="RESTRICT",
            name="fk_sites_org_address",
        ),
        sa.CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="ck_sites_effective_dates"),
    )
    op.create_index("ix_sites_organization_id", "sites", ["organization_id"])
    op.create_index("ix_sites_status", "sites", ["status"])

    op.create_table(
        "departments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("site_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("department_code", sa.String(length=40), nullable=False),
        sa.Column("department_name", sa.String(length=120), nullable=False),
        sa.Column("department_head_membership_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id",
            "site_id",
            "department_code",
            name="uq_departments_org_site_code",
            postgresql_nulls_not_distinct=True,
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_departments_org_id_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_departments_org_site",
        ),
    )
    op.create_index("ix_departments_organization_id", "departments", ["organization_id"])
    op.create_index("ix_departments_site_id", "departments", ["site_id"])

    op.create_table(
        "organization_sequences",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("period_year", sa.Integer(), nullable=False),
        sa.Column("last_value", sa.BigInteger(), nullable=False, server_default="0"),
        sa.UniqueConstraint(
            "organization_id",
            "entity_type",
            "period_year",
            name="uq_org_sequences_scope_type_year",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint("last_value >= 0", name="ck_org_sequences_non_negative"),
    )
    op.create_index("ix_organization_sequences_organization_id", "organization_sequences", ["organization_id"])

    # 3. Tier 1: Memberships, Profiles, Site Assignments, Role Assignments, Invitations
    op.create_table(
        "organization_memberships",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("member_type", sa.String(length=30), nullable=False),
        sa.Column("membership_status", sa.String(length=30), nullable=False),
        sa.Column("employee_code", sa.String(length=50), nullable=True),
        sa.Column(
            "department_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("departments.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("designation", sa.String(length=120), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "approved_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_org_memberships_org_user"),
        sa.UniqueConstraint("organization_id", "id", name="uq_org_memberships_org_id_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "department_id"],
            ["departments.organization_id", "departments.id"],
            ondelete="RESTRICT",
            name="fk_org_memberships_org_dept",
        ),
    )
    op.create_index("ix_organization_memberships_user_id", "organization_memberships", ["user_id"])
    op.create_index("ix_organization_memberships_organization_id", "organization_memberships", ["organization_id"])
    op.create_index("ix_organization_memberships_membership_status", "organization_memberships", ["membership_status"])
    op.create_index(
        "ix_org_memberships_org_emp_code",
        "organization_memberships",
        ["organization_id", "employee_code"],
        unique=True,
        postgresql_where=sa.text("employee_code IS NOT NULL"),
    )

    op.create_foreign_key(
        "fk_departments_head_membership",
        "departments",
        "organization_memberships",
        ["department_head_membership_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "employee_profiles",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("employee_code", sa.String(length=50), nullable=False),
        sa.Column("joining_date", sa.Date(), nullable=True),
        sa.Column("employment_type", sa.String(length=40), nullable=True),
        sa.Column("job_title", sa.String(length=120), nullable=True),
        sa.Column(
            "supervisor_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("employment_status", sa.String(length=30), nullable=False),
        sa.Column("termination_date", sa.Date(), nullable=True),
        sa.Column("emergency_contact_reference", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("organization_id", "employee_code", name="uq_emp_profiles_org_code"),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_emp_profiles_org_user"),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_emp_profiles_org_membership",
        ),
    )
    op.create_index("ix_employee_profiles_organization_id", "employee_profiles", ["organization_id"])

    op.create_table(
        "site_assignments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("site_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("assignment_type", sa.String(length=30), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column(
            "assigned_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("membership_id", "site_id", name="uq_site_assignments_member_site"),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_site_assignments_org_membership",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_site_assignments_org_site",
        ),
    )
    op.create_index("ix_site_assignments_organization_id", "site_assignments", ["organization_id"])
    op.create_index("ix_site_assignments_membership_id", "site_assignments", ["membership_id"])
    op.create_index("ix_site_assignments_site_id", "site_assignments", ["site_id"])

    op.create_table(
        "membership_role_assignments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("role_id", sa.Uuid(as_uuid=True), sa.ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column("site_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "granted_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "approved_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=25), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(scope_type = 'ORGANIZATION' AND site_id IS NULL) OR (scope_type = 'SITE' AND site_id IS NOT NULL)",
            name="ck_member_role_scope_consistency",
        ),
        sa.UniqueConstraint(
            "membership_id",
            "role_id",
            "scope_type",
            "site_id",
            name="uq_member_role_scope",
            postgresql_nulls_not_distinct=True,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_member_role_org_membership",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_member_role_org_site",
        ),
    )
    op.create_index("ix_membership_role_assignments_organization_id", "membership_role_assignments", ["organization_id"])
    op.create_index("ix_membership_role_assignments_membership_id", "membership_role_assignments", ["membership_id"])
    op.create_index("ix_membership_role_assignments_role_id", "membership_role_assignments", ["role_id"])
    op.create_index("ix_membership_role_assignments_site_id", "membership_role_assignments", ["site_id"])
    op.create_index("ix_membership_role_assignments_status", "membership_role_assignments", ["status"])

    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("site_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("requested_role_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "department_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("departments.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("employee_code", sa.String(length=50), nullable=True),
        sa.Column("designation", sa.String(length=120), nullable=True),
        sa.Column("requires_quality_approval", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("token_hash", sa.String(length=255), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "invited_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=25), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "department_id"],
            ["departments.organization_id", "departments.id"],
            ondelete="RESTRICT",
            name="fk_invitations_org_dept",
        ),
    )
    op.create_index("ix_invitations_organization_id", "invitations", ["organization_id"])
    op.create_index("ix_invitations_status", "invitations", ["status"])
    op.create_index(
        "ix_invitations_pending_org_email",
        "invitations",
        ["organization_id", "email"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    # 4. Tier 1 Governance: Notifications, Outbox, Audit, Security Events, Approvals, E-Signatures
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("delivery_seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "recipient_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("aggregate_type", sa.String(length=60), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_status", sa.String(length=30), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "recipient_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_notifications_org_recipient",
        ),
    )
    op.create_index("ix_notifications_organization_id", "notifications", ["organization_id"])
    op.create_index("ix_notifications_recipient_membership_id", "notifications", ["recipient_membership_id"])
    op.create_index(
        "ix_notifications_recipient_seq",
        "notifications",
        ["recipient_membership_id", "delivery_seq"],
        unique=True,
    )
    op.create_index(
        "ix_notifications_recipient_unread",
        "notifications",
        ["recipient_membership_id", "created_at"],
        postgresql_where=sa.text("read_at IS NULL"),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("aggregate_type", sa.String(length=60), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("status", sa.String(length=25), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("worker_id", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint("attempt_count >= 0", name="ck_outbox_attempt_non_neg"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_outbox_max_attempts_pos"),
    )
    op.create_index("ix_outbox_events_organization_id", "outbox_events", ["organization_id"])
    op.create_index("ix_outbox_events_status", "outbox_events", ["status"])
    op.create_index(
        "ix_outbox_pending_schedule",
        "outbox_events",
        ["next_attempt_at", "occurred_at"],
        postgresql_where=sa.text("status IN ('PENDING', 'FAILED')"),
    )
    op.create_index(
        "ix_outbox_processing_lease",
        "outbox_events",
        ["lease_expires_at"],
        postgresql_where=sa.text("status = 'PROCESSING'"),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("site_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("actor_type", sa.String(length=30), nullable=False),
        sa.Column("actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("before_hash", sa.String(length=64), nullable=True),
        sa.Column("after_hash", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("source_ip", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_audit_events_org_site",
        ),
    )
    op.create_index("ix_audit_events_organization_id", "audit_events", ["organization_id"])
    op.create_index(
        "ix_audit_events_org_entity_time",
        "audit_events",
        ["organization_id", "entity_type", "entity_id", "occurred_at"],
    )
    op.create_index("ix_audit_events_correlation", "audit_events", ["correlation_id"])

    op.create_table(
        "security_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("source_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("target_resource", sa.String(length=255), nullable=True),
        sa.Column("details_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_security_events_organization_id", "security_events", ["organization_id"])
    op.create_index("ix_security_events_user_id", "security_events", ["user_id"])
    op.create_index("ix_security_events_org_time", "security_events", ["organization_id", "occurred_at"])
    op.create_index("ix_security_events_type_time", "security_events", ["event_type", "occurred_at"])
    op.create_index("ix_security_events_correlation", "security_events", ["correlation_id"])

    op.create_table(
        "approval_records",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("approval_stage", sa.String(length=60), nullable=False),
        sa.Column("decision", sa.String(length=30), nullable=False),
        sa.Column(
            "approver_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("record_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "approver_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_approval_records_org_approver",
        ),
    )
    op.create_index("ix_approval_records_organization_id", "approval_records", ["organization_id"])
    op.create_index(
        "ix_approval_records_entity",
        "approval_records",
        ["organization_id", "entity_type", "entity_id"],
    )

    op.create_table(
        "electronic_signatures",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "approval_record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("approval_records.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "signer_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("meaning", sa.String(length=120), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("auth_context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signature_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
    )
    op.create_index("ix_electronic_signatures_organization_id", "electronic_signatures", ["organization_id"])

    # 5. Append-only immutability triggers on audit_events & security_events
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_append_only_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only; % operations are prohibited.', TG_TABLE_NAME, TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION prevent_append_only_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_security_events_append_only
        BEFORE UPDATE OR DELETE ON security_events
        FOR EACH ROW EXECUTE FUNCTION prevent_append_only_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_security_events_append_only ON security_events;")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_append_only ON audit_events;")
    op.execute("DROP FUNCTION IF EXISTS prevent_append_only_mutation();")

    op.drop_table("electronic_signatures")
    op.drop_table("approval_records")
    op.drop_table("security_events")
    op.drop_table("audit_events")
    op.drop_table("outbox_events")
    op.drop_table("notifications")

    op.drop_table("invitations")
    op.drop_table("membership_role_assignments")
    op.drop_table("site_assignments")
    op.drop_table("employee_profiles")

    op.drop_constraint("fk_departments_head_membership", "departments", type_="foreignkey")
    op.drop_table("organization_memberships")
    op.drop_table("organization_sequences")
    op.drop_table("departments")
    op.drop_table("sites")

    op.drop_constraint("fk_organizations_registered_address", "organizations", type_="foreignkey")
    op.drop_table("organization_addresses")
    op.drop_table("organizations")

    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")
    op.drop_table("user_credentials")
    op.drop_table("users")
