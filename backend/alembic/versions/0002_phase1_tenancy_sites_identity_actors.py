"""Phase 1 tenancy licenses, user platform role, qualifications, training, actors, and master data

Revision ID: 0002_phase1_tenancy_actors
Revises: 0001_phase0_foundation
Create Date: 2026-11-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_phase1_tenancy_actors"
down_revision: Union[str, None] = "0001_phase0_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # 1. Add platform_role column to users
    op.add_column("users", sa.Column("platform_role", sa.String(length=40), nullable=True))

    # 2. Organization & Site Regulatory Licenses
    op.create_table(
        "organization_licenses",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("license_type", sa.String(length=60), nullable=False),
        sa.Column("license_number", sa.String(length=120), nullable=False),
        sa.Column("issuing_authority", sa.String(length=150), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="ACTIVE"),
        sa.Column("licence_document_id", sa.Uuid(as_uuid=True), nullable=True),
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
        sa.UniqueConstraint("organization_id", "license_type", "license_number", name="uq_org_licenses_type_num"),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_org_licenses_valid_dates",
        ),
    )
    op.create_index("ix_organization_licenses_organization_id", "organization_licenses", ["organization_id"])
    op.create_index("ix_organization_licenses_status", "organization_licenses", ["status"])

    op.create_table(
        "site_licenses",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "site_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("license_type", sa.String(length=60), nullable=False),
        sa.Column("license_number", sa.String(length=120), nullable=False),
        sa.Column("issuing_authority", sa.String(length=150), nullable=False),
        sa.Column("scope_description", sa.String(length=500), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="ACTIVE"),
        sa.Column("licence_document_id", sa.Uuid(as_uuid=True), nullable=True),
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
        sa.UniqueConstraint(
            "organization_id",
            "site_id",
            "license_type",
            "license_number",
            name="uq_site_licenses_site_type_num",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_site_licenses_org_site",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_site_licenses_valid_dates",
        ),
    )
    op.create_index("ix_site_licenses_organization_id", "site_licenses", ["organization_id"])
    op.create_index("ix_site_licenses_site_id", "site_licenses", ["site_id"])
    op.create_index("ix_site_licenses_status", "site_licenses", ["status"])

    # 3. Employee Qualifications & Training Records
    op.create_table(
        "employee_qualifications",
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
        sa.Column(
            "employee_profile_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("employee_profiles.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("qualification_type", sa.String(length=60), nullable=False),
        sa.Column("qualification_name", sa.String(length=150), nullable=False),
        sa.Column("issuing_body", sa.String(length=150), nullable=True),
        sa.Column("certificate_number", sa.String(length=80), nullable=True),
        sa.Column("issued_on", sa.Date(), nullable=True),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("verification_status", sa.String(length=30), nullable=False, server_default="PENDING"),
        sa.Column(
            "verified_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
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
            ["organization_id", "membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_emp_qual_org_membership",
        ),
        sa.CheckConstraint(
            "expires_on IS NULL OR issued_on IS NULL OR expires_on >= issued_on",
            name="ck_emp_qual_valid_dates",
        ),
    )
    op.create_index("ix_employee_qualifications_organization_id", "employee_qualifications", ["organization_id"])
    op.create_index("ix_employee_qualifications_membership_id", "employee_qualifications", ["membership_id"])
    op.create_index("ix_employee_qualifications_employee_profile_id", "employee_qualifications", ["employee_profile_id"])
    op.create_index("ix_employee_qualifications_verification_status", "employee_qualifications", ["verification_status"])

    op.create_table(
        "training_records",
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
        sa.Column(
            "employee_profile_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("employee_profiles.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "site_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("training_topic", sa.String(length=200), nullable=False),
        sa.Column("training_method", sa.String(length=40), nullable=False, server_default="CLASSROOM"),
        sa.Column("sop_revision_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "trainer_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("completed_on", sa.Date(), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("assessment_score", sa.Integer(), nullable=True),
        sa.Column("completion_status", sa.String(length=30), nullable=False, server_default="COMPLETED"),
        sa.Column("evidence_document_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
            ["organization_id", "membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_training_org_membership",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_training_org_site",
        ),
        sa.CheckConstraint(
            "assessment_score IS NULL OR (assessment_score >= 0 AND assessment_score <= 100)",
            name="ck_training_score_range",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until >= completed_on",
            name="ck_training_valid_until",
        ),
    )
    op.create_index("ix_training_records_organization_id", "training_records", ["organization_id"])
    op.create_index("ix_training_records_membership_id", "training_records", ["membership_id"])
    op.create_index("ix_training_records_employee_profile_id", "training_records", ["employee_profile_id"])
    op.create_index("ix_training_records_site_id", "training_records", ["site_id"])
    op.create_index("ix_training_records_completion_status", "training_records", ["completion_status"])

    # 4. External Actors & Business Partners
    op.create_table(
        "complainants",
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
        sa.Column("complainant_type", sa.String(length=30), nullable=False, server_default="CUSTOMER"),
        sa.Column("full_name", sa.String(length=150), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("phone_e164", sa.String(length=20), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("state_region", sa.String(length=100), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=False, server_default="IN"),
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
    op.create_index("ix_complainants_organization_id", "complainants", ["organization_id"])
    op.create_index("ix_complainants_user_id", "complainants", ["user_id"])
    op.create_index("ix_complainants_email", "complainants", ["email"])

    op.create_table(
        "customer_profiles",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("full_name", sa.String(length=150), nullable=False),
        sa.Column("phone_e164", sa.String(length=20), nullable=True),
        sa.Column("address_line", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=100), nullable=True),
        sa.Column("pincode", sa.String(length=20), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=False, server_default="IN"),
        sa.Column("preferred_language", sa.String(length=10), nullable=False, server_default="en"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="ACTIVE"),
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

    op.create_table(
        "chemist_profiles",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("pharmacy_name", sa.String(length=200), nullable=False),
        sa.Column("drug_license_number", sa.String(length=80), nullable=True),
        sa.Column("gst_number", sa.String(length=20), nullable=True),
        sa.Column("pharmacy_address", sa.String(length=255), nullable=False),
        sa.Column("city", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=100), nullable=False),
        sa.Column("pincode", sa.String(length=20), nullable=False),
        sa.Column("pharmacist_registration_number", sa.String(length=80), nullable=True),
        sa.Column("verification_status", sa.String(length=30), nullable=False, server_default="PENDING"),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "verified_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("licence_document_id", sa.Uuid(as_uuid=True), nullable=True),
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
    op.create_index("ix_chemist_profiles_verification_status", "chemist_profiles", ["verification_status"])

    op.create_table(
        "business_partners",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("partner_code", sa.String(length=40), nullable=False),
        sa.Column("legal_name", sa.String(length=200), nullable=False),
        sa.Column("trade_name", sa.String(length=200), nullable=True),
        sa.Column("partner_type", sa.String(length=40), nullable=False),
        sa.Column("tax_id", sa.String(length=40), nullable=True),
        sa.Column("license_number", sa.String(length=100), nullable=True),
        sa.Column("primary_email", sa.String(length=254), nullable=False),
        sa.Column("primary_phone", sa.String(length=32), nullable=True),
        sa.Column("qualification_status", sa.String(length=30), nullable=False, server_default="PENDING"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="ACTIVE"),
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
        sa.UniqueConstraint("organization_id", "partner_code", name="uq_business_partners_org_code"),
        sa.UniqueConstraint("organization_id", "id", name="uq_business_partners_org_id_id"),
    )
    op.create_index("ix_business_partners_organization_id", "business_partners", ["organization_id"])
    op.create_index("ix_business_partners_status", "business_partners", ["status"])

    op.create_table(
        "partner_sites",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "partner_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("business_partners.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("site_code", sa.String(length=40), nullable=False),
        sa.Column("site_name", sa.String(length=200), nullable=False),
        sa.Column("address_line_1", sa.String(length=200), nullable=False),
        sa.Column("city", sa.String(length=100), nullable=False),
        sa.Column("state_region", sa.String(length=100), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False, server_default="IN"),
        sa.Column("drug_license_number", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="ACTIVE"),
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
        sa.UniqueConstraint(
            "organization_id",
            "partner_id",
            "site_code",
            name="uq_partner_sites_org_partner_code",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_partner_sites_org_id_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "partner_id"],
            ["business_partners.organization_id", "business_partners.id"],
            ondelete="RESTRICT",
            name="fk_partner_sites_org_partner",
        ),
    )
    op.create_index("ix_partner_sites_organization_id", "partner_sites", ["organization_id"])
    op.create_index("ix_partner_sites_partner_id", "partner_sites", ["partner_id"])

    op.create_table(
        "partner_contacts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "partner_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("business_partners.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "partner_site_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("partner_sites.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("full_name", sa.String(length=150), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("phone_e164", sa.String(length=20), nullable=True),
        sa.Column("designation", sa.String(length=100), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="ACTIVE"),
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
            ["organization_id", "partner_id"],
            ["business_partners.organization_id", "business_partners.id"],
            ondelete="RESTRICT",
            name="fk_partner_contacts_org_partner",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "partner_site_id"],
            ["partner_sites.organization_id", "partner_sites.id"],
            ondelete="RESTRICT",
            name="fk_partner_contacts_org_site",
        ),
    )
    op.create_index("ix_partner_contacts_organization_id", "partner_contacts", ["organization_id"])
    op.create_index("ix_partner_contacts_partner_id", "partner_contacts", ["partner_id"])

    op.create_table(
        "partner_memberships",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "partner_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("business_partners.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "partner_site_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("partner_sites.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "role_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("invited_email", sa.String(length=254), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=True, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("membership_status", sa.String(length=30), nullable=False, server_default="INVITED"),
        sa.Column(
            "approved_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "organization_id",
            "partner_id",
            "user_id",
            name="uq_partner_memberships_org_partner_user",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_partner_memberships_org_id_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "partner_id"],
            ["business_partners.organization_id", "business_partners.id"],
            ondelete="RESTRICT",
            name="fk_partner_memberships_org_partner",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "partner_site_id"],
            ["partner_sites.organization_id", "partner_sites.id"],
            ondelete="RESTRICT",
            name="fk_partner_memberships_org_site",
        ),
    )
    op.create_index("ix_partner_memberships_organization_id", "partner_memberships", ["organization_id"])
    op.create_index("ix_partner_memberships_partner_id", "partner_memberships", ["partner_id"])
    op.create_index("ix_partner_memberships_user_id", "partner_memberships", ["user_id"])
    op.create_index("ix_partner_memberships_membership_status", "partner_memberships", ["membership_status"])

    # 5. Master Data: Controlled Reference Values & Units of Measure
    op.create_table(
        "reference_values",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("category_code", sa.String(length=60), nullable=False),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("display_name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
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
        sa.UniqueConstraint(
            "organization_id",
            "category_code",
            "code",
            name="uq_reference_values_org_cat_code",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_reference_values_organization_id", "reference_values", ["organization_id"])
    op.create_index("ix_reference_values_org_cat", "reference_values", ["organization_id", "category_code"])

    op.create_table(
        "units_of_measure",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("unit_code", sa.String(length=30), nullable=False),
        sa.Column("unit_name", sa.String(length=100), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("dimension", sa.String(length=40), nullable=False),
        sa.Column("base_unit_code", sa.String(length=30), nullable=True),
        sa.Column("conversion_factor", sa.Numeric(18, 6), nullable=False, server_default="1.000000"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
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
    op.create_index("ix_units_of_measure_unit_code", "units_of_measure", ["unit_code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_units_of_measure_unit_code", table_name="units_of_measure")
    op.drop_table("units_of_measure")

    op.drop_index("ix_reference_values_org_cat", table_name="reference_values")
    op.drop_index("ix_reference_values_organization_id", table_name="reference_values")
    op.drop_table("reference_values")

    op.drop_index("ix_partner_memberships_membership_status", table_name="partner_memberships")
    op.drop_index("ix_partner_memberships_user_id", table_name="partner_memberships")
    op.drop_index("ix_partner_memberships_partner_id", table_name="partner_memberships")
    op.drop_index("ix_partner_memberships_organization_id", table_name="partner_memberships")
    op.drop_table("partner_memberships")

    op.drop_index("ix_partner_contacts_partner_id", table_name="partner_contacts")
    op.drop_index("ix_partner_contacts_organization_id", table_name="partner_contacts")
    op.drop_table("partner_contacts")

    op.drop_index("ix_partner_sites_partner_id", table_name="partner_sites")
    op.drop_index("ix_partner_sites_organization_id", table_name="partner_sites")
    op.drop_table("partner_sites")

    op.drop_index("ix_business_partners_status", table_name="business_partners")
    op.drop_index("ix_business_partners_organization_id", table_name="business_partners")
    op.drop_table("business_partners")

    op.drop_index("ix_chemist_profiles_verification_status", table_name="chemist_profiles")
    op.drop_table("chemist_profiles")
    op.drop_table("customer_profiles")

    op.drop_index("ix_complainants_email", table_name="complainants")
    op.drop_index("ix_complainants_user_id", table_name="complainants")
    op.drop_index("ix_complainants_organization_id", table_name="complainants")
    op.drop_table("complainants")

    op.drop_index("ix_training_records_completion_status", table_name="training_records")
    op.drop_index("ix_training_records_site_id", table_name="training_records")
    op.drop_index("ix_training_records_employee_profile_id", table_name="training_records")
    op.drop_index("ix_training_records_membership_id", table_name="training_records")
    op.drop_index("ix_training_records_organization_id", table_name="training_records")
    op.drop_table("training_records")

    op.drop_index("ix_employee_qualifications_verification_status", table_name="employee_qualifications")
    op.drop_index("ix_employee_qualifications_employee_profile_id", table_name="employee_qualifications")
    op.drop_index("ix_employee_qualifications_membership_id", table_name="employee_qualifications")
    op.drop_index("ix_employee_qualifications_organization_id", table_name="employee_qualifications")
    op.drop_table("employee_qualifications")

    op.drop_index("ix_site_licenses_status", table_name="site_licenses")
    op.drop_index("ix_site_licenses_site_id", table_name="site_licenses")
    op.drop_index("ix_site_licenses_organization_id", table_name="site_licenses")
    op.drop_table("site_licenses")

    op.drop_index("ix_organization_licenses_status", table_name="organization_licenses")
    op.drop_index("ix_organization_licenses_organization_id", table_name="organization_licenses")
    op.drop_table("organization_licenses")

    op.drop_column("users", "platform_role")
