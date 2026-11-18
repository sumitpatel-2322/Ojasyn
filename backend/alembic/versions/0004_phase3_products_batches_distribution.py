"""Phase 3 products, materials, supplier lots, manufacturing batches, disposition, and distribution traceability

Revision ID: 0004_phase3_traceability
Revises: 0003_phase2_docs_complaints
Create Date: 2026-11-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_phase3_traceability"
down_revision: Union[str, None] = "0003_phase2_docs_complaints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _timestamp_version_columns() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    # 1. Products & Marketing/Regulatory Authorizations
    op.create_table(
        "products",
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
            nullable=True,
        ),
        sa.Column("product_code", sa.String(length=60), nullable=False),
        sa.Column("brand_name", sa.String(length=180), nullable=True),
        sa.Column("generic_name", sa.String(length=180), nullable=False),
        sa.Column("product_category", sa.String(length=80), nullable=True),
        sa.Column("dosage_form", sa.String(length=60), nullable=False),
        sa.Column("strength_value", sa.Numeric(12, 4), nullable=True),
        sa.Column("strength_unit", sa.String(length=30), nullable=True),
        sa.Column("route_of_administration", sa.String(length=60), nullable=True),
        sa.Column("composition_json", JSONType, nullable=True),
        sa.Column("specifications_json", JSONType, nullable=True),
        sa.Column("manufacturer_name", sa.String(length=200), nullable=True),
        sa.Column("pack_description", sa.String(length=180), nullable=True),
        sa.Column("therapeutic_class", sa.String(length=120), nullable=True),
        sa.Column("shelf_life_months", sa.Integer(), nullable=True),
        sa.Column("storage_conditions", sa.String(length=200), nullable=True),
        sa.Column("product_status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "specification_document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        *_timestamp_version_columns(),
        sa.UniqueConstraint("organization_id", "product_code", name="uq_products_org_code"),
        sa.UniqueConstraint("organization_id", "id", name="uq_products_org_id_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_products_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "specification_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_products_org_spec_doc",
        ),
        sa.CheckConstraint(
            "shelf_life_months IS NULL OR shelf_life_months > 0",
            name="ck_products_shelf_life_pos",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_products_version_pos"),
    )
    op.create_index("ix_products_organization_id", "products", ["organization_id"])
    op.create_index("ix_products_site_id", "products", ["site_id"])
    op.create_index("ix_products_product_status", "products", ["product_status"])

    op.create_table(
        "product_authorizations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("authorization_number", sa.String(length=100), nullable=False),
        sa.Column("authorization_holder", sa.String(length=200), nullable=True),
        sa.Column("issuing_authority", sa.String(length=150), nullable=True),
        sa.Column("approved_on", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("regulatory_status", sa.String(length=30), nullable=False, server_default="PENDING"),
        sa.Column(
            "evidence_document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        *_timestamp_version_columns(),
        sa.UniqueConstraint(
            "organization_id",
            "product_id",
            "country_code",
            "authorization_number",
            name="uq_product_auth_org_prod_country_num",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "product_id"],
            ["products.organization_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_product_auth_org_product",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "evidence_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_product_auth_org_doc",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR approved_on IS NULL OR valid_to >= approved_on",
            name="ck_product_auth_valid_dates",
        ),
    )
    op.create_index("ix_product_authorizations_organization_id", "product_authorizations", ["organization_id"])
    op.create_index("ix_product_authorizations_product_id", "product_authorizations", ["product_id"])
    op.create_index("ix_product_authorizations_regulatory_status", "product_authorizations", ["regulatory_status"])

    # 2. Materials, BOM & Supplier Lots
    op.create_table(
        "materials",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("material_code", sa.String(length=60), nullable=False),
        sa.Column("material_name", sa.String(length=180), nullable=False),
        sa.Column("material_type", sa.String(length=40), nullable=False),
        sa.Column("material_category", sa.String(length=80), nullable=True),
        sa.Column("cas_number", sa.String(length=40), nullable=True),
        sa.Column("pharmacopoeia_grade", sa.String(length=40), nullable=True),
        sa.Column("default_unit_code", sa.String(length=30), nullable=False),
        sa.Column("manufacturer_name", sa.String(length=200), nullable=True),
        sa.Column("storage_conditions", sa.String(length=200), nullable=True),
        sa.Column("retest_period_days", sa.Integer(), nullable=True),
        sa.Column("specifications_json", JSONType, nullable=True),
        sa.Column("qualification_status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        sa.Column("material_status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        sa.Column(
            "default_supplier_partner_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("business_partners.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "specification_document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        *_timestamp_version_columns(),
        sa.UniqueConstraint("organization_id", "material_code", name="uq_materials_org_code"),
        sa.UniqueConstraint("organization_id", "id", name="uq_materials_org_id_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "default_supplier_partner_id"],
            ["business_partners.organization_id", "business_partners.id"],
            ondelete="RESTRICT",
            name="fk_materials_org_default_supplier",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "specification_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_materials_org_spec_doc",
        ),
        sa.CheckConstraint(
            "retest_period_days IS NULL OR retest_period_days > 0",
            name="ck_materials_retest_pos",
        ),
    )
    op.create_index("ix_materials_organization_id", "materials", ["organization_id"])
    op.create_index("ix_materials_material_type", "materials", ["material_type"])
    op.create_index("ix_materials_qualification_status", "materials", ["qualification_status"])
    op.create_index("ix_materials_material_status", "materials", ["material_status"])
    op.create_index("ix_materials_default_supplier_partner_id", "materials", ["default_supplier_partner_id"])

    op.create_table(
        "product_material_bills",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "material_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("materials.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity_per_unit", sa.Numeric(14, 4), nullable=False),
        sa.Column("unit_code", sa.String(length=30), nullable=False),
        sa.Column("function_role", sa.String(length=80), nullable=True),
        sa.Column("is_critical_material", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamp_version_columns(),
        sa.UniqueConstraint("organization_id", "product_id", "material_id", name="uq_pmb_org_product_material"),
        sa.ForeignKeyConstraint(
            ["organization_id", "product_id"],
            ["products.organization_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_pmb_org_product",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "material_id"],
            ["materials.organization_id", "materials.id"],
            ondelete="RESTRICT",
            name="fk_pmb_org_material",
        ),
        sa.CheckConstraint("quantity_per_unit > 0", name="ck_pmb_qty_pos"),
    )
    op.create_index("ix_product_material_bills_organization_id", "product_material_bills", ["organization_id"])
    op.create_index("ix_product_material_bills_product_id", "product_material_bills", ["product_id"])
    op.create_index("ix_product_material_bills_material_id", "product_material_bills", ["material_id"])

    op.create_table(
        "supplier_lots",
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
            nullable=True,
        ),
        sa.Column(
            "material_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("materials.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "supplier_partner_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("business_partners.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("internal_lot_number", sa.String(length=80), nullable=False),
        sa.Column("supplier_lot_number", sa.String(length=80), nullable=False),
        sa.Column("manufacturer_name", sa.String(length=200), nullable=True),
        sa.Column("received_quantity", sa.Numeric(14, 4), nullable=False),
        sa.Column("remaining_quantity", sa.Numeric(14, 4), nullable=False),
        sa.Column("unit_code", sa.String(length=30), nullable=False),
        sa.Column("received_date", sa.Date(), nullable=False),
        sa.Column("manufacturing_date", sa.Date(), nullable=True),
        sa.Column("expiry_or_retest_date", sa.Date(), nullable=True),
        sa.Column("lot_status", sa.String(length=30), nullable=False, server_default="QUARANTINED"),
        sa.Column(
            "coa_document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "approved_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamp_version_columns(),
        sa.UniqueConstraint("organization_id", "internal_lot_number", name="uq_supplier_lots_org_internal_no"),
        sa.UniqueConstraint(
            "organization_id",
            "material_id",
            "supplier_partner_id",
            "supplier_lot_number",
            name="uq_supplier_lots_org_mat_sup_lot",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_supplier_lots_org_id_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_supplier_lots_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "material_id"],
            ["materials.organization_id", "materials.id"],
            ondelete="RESTRICT",
            name="fk_supplier_lots_org_material",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "supplier_partner_id"],
            ["business_partners.organization_id", "business_partners.id"],
            ondelete="RESTRICT",
            name="fk_supplier_lots_org_supplier",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "coa_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_supplier_lots_org_coa_doc",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "approved_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_supplier_lots_org_approver",
        ),
        sa.CheckConstraint("received_quantity > 0", name="ck_supplier_lots_recv_qty_pos"),
        sa.CheckConstraint(
            "remaining_quantity >= 0 AND remaining_quantity <= received_quantity",
            name="ck_supplier_lots_rem_qty_bounds",
        ),
        sa.CheckConstraint(
            "expiry_or_retest_date IS NULL OR manufacturing_date IS NULL OR expiry_or_retest_date >= manufacturing_date",
            name="ck_supplier_lots_dates",
        ),
    )
    op.create_index("ix_supplier_lots_organization_id", "supplier_lots", ["organization_id"])
    op.create_index("ix_supplier_lots_site_id", "supplier_lots", ["site_id"])
    op.create_index("ix_supplier_lots_material_id", "supplier_lots", ["material_id"])
    op.create_index("ix_supplier_lots_supplier_partner_id", "supplier_lots", ["supplier_partner_id"])
    op.create_index("ix_supplier_lots_lot_status", "supplier_lots", ["lot_status"])

    # 3. Manufacturing Batches, Dispositions, Consumption, Genealogy & Documents
    op.create_table(
        "batches",
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
        sa.Column(
            "product_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("batch_number", sa.String(length=100), nullable=False),
        sa.Column("manufacturing_order_no", sa.String(length=80), nullable=True),
        sa.Column("manufacturing_date", sa.Date(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("batch_size_value", sa.Numeric(14, 4), nullable=False),
        sa.Column("batch_size_unit", sa.String(length=30), nullable=False),
        sa.Column("actual_yield_value", sa.Numeric(14, 4), nullable=True),
        sa.Column("distributed_quantity", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("packaging_configuration", sa.String(length=200), nullable=True),
        sa.Column("batch_status", sa.String(length=40), nullable=False, server_default="PLANNED"),
        sa.Column("quality_review_status", sa.String(length=30), nullable=False, server_default="PENDING"),
        sa.Column("hold_or_rejection_reason", sa.Text(), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "released_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "primary_document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("master_batch_record_id", sa.Uuid(as_uuid=True), nullable=True),
        *_timestamp_version_columns(),
        sa.UniqueConstraint("organization_id", "id", name="uq_batches_org_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "site_id",
            "product_id",
            "batch_number",
            name="uq_batches_org_site_product_num",
        ),
        sa.CheckConstraint("expiry_date > manufacturing_date", name="ck_batches_expiry_after_mfg"),
        sa.CheckConstraint("batch_size_value > 0", name="ck_batches_size_pos"),
        sa.CheckConstraint(
            "actual_yield_value IS NULL OR actual_yield_value >= 0",
            name="ck_batches_yield_non_neg",
        ),
        sa.CheckConstraint("distributed_quantity >= 0", name="ck_batches_distributed_non_neg"),
        sa.CheckConstraint(
            "distributed_quantity <= COALESCE(actual_yield_value, batch_size_value)",
            name="ck_batches_distributed_lte_available",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_batches_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "product_id"],
            ["products.organization_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_batches_org_product",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "released_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batches_org_released_by",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "primary_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_batches_org_primary_doc",
        ),
    )
    op.create_index("ix_batches_organization_id", "batches", ["organization_id"])
    op.create_index("ix_batches_site_id", "batches", ["site_id"])
    op.create_index("ix_batches_product_id", "batches", ["product_id"])
    op.create_index("ix_batches_batch_number", "batches", ["batch_number"])
    op.create_index("ix_batches_batch_status", "batches", ["batch_status"])
    op.create_index("ix_batches_quality_review_status", "batches", ["quality_review_status"])
    op.create_index("ix_batches_org_status", "batches", ["organization_id", "batch_status"])

    op.create_table(
        "batch_status_history",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "batch_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(length=40), nullable=True),
        sa.Column("to_status", sa.String(length=40), nullable=False),
        sa.Column(
            "changed_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "changed_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_batch_status_hist_org_batch",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "changed_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_status_hist_org_member",
        ),
    )
    op.create_index("ix_batch_status_history_organization_id", "batch_status_history", ["organization_id"])
    op.create_index("ix_batch_status_history_batch_id", "batch_status_history", ["batch_id"])
    op.create_index("ix_batch_status_hist_batch_time", "batch_status_history", ["batch_id", "occurred_at"])

    op.create_table(
        "batch_dispositions",
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
        sa.Column(
            "batch_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(length=40), nullable=False),
        sa.Column("disposition_decision", sa.String(length=30), nullable=False),
        sa.Column("quality_review_status", sa.String(length=30), nullable=False),
        sa.Column("checklist_json", JSONType, nullable=False),
        sa.Column("hold_or_rejection_reason", sa.Text(), nullable=True),
        sa.Column(
            "proposed_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "decided_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "approval_record_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("approval_records.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "certificate_document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "proposed_by_membership_id != decided_by_membership_id",
            name="ck_batch_dispositions_maker_checker",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_batch_dispositions_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_batch_dispositions_org_batch",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "proposed_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_dispositions_org_proposer",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "decided_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_dispositions_org_decider",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "certificate_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_batch_dispositions_org_cert_doc",
        ),
    )
    op.create_index("ix_batch_dispositions_organization_id", "batch_dispositions", ["organization_id"])
    op.create_index("ix_batch_dispositions_site_id", "batch_dispositions", ["site_id"])
    op.create_index("ix_batch_dispositions_batch_id", "batch_dispositions", ["batch_id"])
    op.create_index("ix_batch_dispositions_batch_time", "batch_dispositions", ["batch_id", "decided_at"])

    op.create_table(
        "batch_material_consumptions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "batch_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "material_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("materials.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "supplier_lot_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("supplier_lots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity_consumed", sa.Numeric(14, 4), nullable=False),
        sa.Column("unit_code", sa.String(length=30), nullable=False),
        sa.Column("stage_name", sa.String(length=120), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "issued_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "verified_by_membership_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "evidence_document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        *_timestamp_version_columns(),
        sa.CheckConstraint("quantity_consumed > 0", name="ck_batch_mat_cons_qty_pos"),
        sa.CheckConstraint(
            "verified_by_membership_id IS NULL OR verified_by_membership_id != issued_by_membership_id",
            name="ck_batch_mat_cons_maker_checker",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_batch_mat_cons_org_batch",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "material_id"],
            ["materials.organization_id", "materials.id"],
            ondelete="RESTRICT",
            name="fk_batch_mat_cons_org_material",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "supplier_lot_id"],
            ["supplier_lots.organization_id", "supplier_lots.id"],
            ondelete="RESTRICT",
            name="fk_batch_mat_cons_org_supplier_lot",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "issued_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_mat_cons_org_issuer",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "verified_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_mat_cons_org_verifier",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "evidence_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_batch_mat_cons_org_doc",
        ),
    )
    op.create_index("ix_batch_material_consumptions_organization_id", "batch_material_consumptions", ["organization_id"])
    op.create_index("ix_batch_material_consumptions_batch_id", "batch_material_consumptions", ["batch_id"])
    op.create_index("ix_batch_material_consumptions_material_id", "batch_material_consumptions", ["material_id"])
    op.create_index("ix_batch_material_consumptions_supplier_lot_id", "batch_material_consumptions", ["supplier_lot_id"])

    op.create_table(
        "batch_relationships",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "parent_batch_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "child_batch_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(length=30), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 4), nullable=True),
        sa.Column("unit_code", sa.String(length=30), nullable=True),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamp_version_columns(),
        sa.CheckConstraint("parent_batch_id != child_batch_id", name="ck_batch_rel_no_self_loop"),
        sa.CheckConstraint("quantity IS NULL OR quantity > 0", name="ck_batch_rel_qty_pos"),
        sa.UniqueConstraint(
            "organization_id",
            "parent_batch_id",
            "child_batch_id",
            "relation_type",
            name="uq_batch_rel_org_parent_child_type",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "parent_batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_batch_rel_org_parent",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "child_batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_batch_rel_org_child",
        ),
    )
    op.create_index("ix_batch_relationships_organization_id", "batch_relationships", ["organization_id"])
    op.create_index("ix_batch_relationships_parent_batch_id", "batch_relationships", ["parent_batch_id"])
    op.create_index("ix_batch_relationships_child_batch_id", "batch_relationships", ["child_batch_id"])

    op.create_table(
        "batch_document_links",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "batch_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("document_role", sa.String(length=40), nullable=False),
        sa.Column(
            "linked_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamp_version_columns(),
        sa.UniqueConstraint("batch_id", "document_id", name="uq_batch_doc_links_batch_doc"),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_batch_doc_links_org_batch",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_batch_doc_links_org_doc",
        ),
    )
    op.create_index("ix_batch_document_links_organization_id", "batch_document_links", ["organization_id"])
    op.create_index("ix_batch_document_links_batch_id", "batch_document_links", ["batch_id"])
    op.create_index("ix_batch_document_links_document_id", "batch_document_links", ["document_id"])

    # 4. Distribution Shipments, Lines & Append-Only Batch Movements
    op.create_table(
        "shipments",
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
        sa.Column("shipment_number", sa.String(length=60), nullable=False),
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
            nullable=False,
        ),
        sa.Column("shipment_status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("carrier_name", sa.String(length=150), nullable=True),
        sa.Column("tracking_number", sa.String(length=120), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamp_version_columns(),
        sa.UniqueConstraint("organization_id", "id", name="uq_shipments_org_id_id"),
        sa.UniqueConstraint("organization_id", "shipment_number", name="uq_shipments_org_number"),
        sa.ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_shipments_org_site",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "partner_id"],
            ["business_partners.organization_id", "business_partners.id"],
            ondelete="RESTRICT",
            name="fk_shipments_org_partner",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "partner_site_id"],
            ["partner_sites.organization_id", "partner_sites.id"],
            ondelete="RESTRICT",
            name="fk_shipments_org_partner_site",
        ),
    )
    op.create_index("ix_shipments_organization_id", "shipments", ["organization_id"])
    op.create_index("ix_shipments_site_id", "shipments", ["site_id"])
    op.create_index("ix_shipments_partner_id", "shipments", ["partner_id"])
    op.create_index("ix_shipments_partner_site_id", "shipments", ["partner_site_id"])
    op.create_index("ix_shipments_shipment_status", "shipments", ["shipment_status"])
    op.create_index("ix_shipments_org_status", "shipments", ["organization_id", "shipment_status"])

    op.create_table(
        "shipment_lines",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "shipment_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("shipments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "batch_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(14, 4), nullable=False),
        sa.Column("unit_code", sa.String(length=30), nullable=False),
        sa.Column("received_quantity", sa.Numeric(14, 4), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamp_version_columns(),
        sa.UniqueConstraint("organization_id", "id", name="uq_shipment_lines_org_id_id"),
        sa.CheckConstraint("quantity > 0", name="ck_shipment_lines_qty_pos"),
        sa.CheckConstraint(
            "received_quantity IS NULL OR (received_quantity >= 0 AND received_quantity <= quantity)",
            name="ck_shipment_lines_recv_range",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shipment_id"],
            ["shipments.organization_id", "shipments.id"],
            ondelete="RESTRICT",
            name="fk_shipment_lines_org_shipment",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_shipment_lines_org_batch",
        ),
    )
    op.create_index("ix_shipment_lines_organization_id", "shipment_lines", ["organization_id"])
    op.create_index("ix_shipment_lines_shipment_id", "shipment_lines", ["shipment_id"])
    op.create_index("ix_shipment_lines_batch_id", "shipment_lines", ["batch_id"])

    op.create_table(
        "batch_movement_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "batch_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "shipment_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("shipments.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "shipment_line_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("shipment_lines.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "partner_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("business_partners.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "partner_site_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("partner_sites.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 4), nullable=True),
        sa.Column("unit_code", sa.String(length=30), nullable=True),
        sa.Column(
            "actor_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint("quantity IS NULL OR quantity >= 0", name="ck_batch_movement_events_qty_non_neg"),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_batch_movements_org_batch",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shipment_id"],
            ["shipments.organization_id", "shipments.id"],
            ondelete="RESTRICT",
            name="fk_batch_movements_org_shipment",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shipment_line_id"],
            ["shipment_lines.organization_id", "shipment_lines.id"],
            ondelete="RESTRICT",
            name="fk_batch_movements_org_shipment_line",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "partner_id"],
            ["business_partners.organization_id", "business_partners.id"],
            ondelete="RESTRICT",
            name="fk_batch_movements_org_partner",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "partner_site_id"],
            ["partner_sites.organization_id", "partner_sites.id"],
            ondelete="RESTRICT",
            name="fk_batch_movements_org_partner_site",
        ),
    )
    op.create_index("ix_batch_movement_events_organization_id", "batch_movement_events", ["organization_id"])
    op.create_index("ix_batch_movement_events_batch_id", "batch_movement_events", ["batch_id"])
    op.create_index("ix_batch_movement_events_shipment_id", "batch_movement_events", ["shipment_id"])
    op.create_index("ix_batch_movement_events_batch_time", "batch_movement_events", ["batch_id", "occurred_at"])

    # 5. Wire deferred Phase 2 Complaint FKs to Phase 3 products and batches
    op.create_foreign_key(
        "fk_complaints_product_id",
        "complaints",
        "products",
        ["product_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_complaints_org_product",
        "complaints",
        "products",
        ["organization_id", "product_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_complaint_batch_links_batch_id",
        "complaint_batch_links",
        "batches",
        ["batch_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_complaint_batch_links_org_batch",
        "complaint_batch_links",
        "batches",
        ["organization_id", "batch_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )

    # 6. PostgreSQL append-only immutability triggers on audit/ledger Phase 3 tables
    if op.get_context().dialect.name == "postgresql":
        for table_name in ("batch_status_history", "batch_dispositions", "batch_movement_events"):
            op.execute(
                f"""
                CREATE TRIGGER trg_prevent_mutation_{table_name}
                BEFORE UPDATE OR DELETE ON {table_name}
                FOR EACH ROW EXECUTE FUNCTION prevent_append_only_mutation();
                """
            )


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        for table_name in ("batch_movement_events", "batch_dispositions", "batch_status_history"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_prevent_mutation_{table_name} ON {table_name};")

    op.drop_constraint("fk_complaint_batch_links_org_batch", "complaint_batch_links", type_="foreignkey")
    op.drop_constraint("fk_complaint_batch_links_batch_id", "complaint_batch_links", type_="foreignkey")
    op.drop_constraint("fk_complaints_org_product", "complaints", type_="foreignkey")
    op.drop_constraint("fk_complaints_product_id", "complaints", type_="foreignkey")

    op.drop_index("ix_batch_movement_events_batch_time", table_name="batch_movement_events")
    op.drop_index("ix_batch_movement_events_shipment_id", table_name="batch_movement_events")
    op.drop_index("ix_batch_movement_events_batch_id", table_name="batch_movement_events")
    op.drop_index("ix_batch_movement_events_organization_id", table_name="batch_movement_events")
    op.drop_table("batch_movement_events")

    op.drop_index("ix_shipment_lines_batch_id", table_name="shipment_lines")
    op.drop_index("ix_shipment_lines_shipment_id", table_name="shipment_lines")
    op.drop_index("ix_shipment_lines_organization_id", table_name="shipment_lines")
    op.drop_table("shipment_lines")

    op.drop_index("ix_shipments_org_status", table_name="shipments")
    op.drop_index("ix_shipments_shipment_status", table_name="shipments")
    op.drop_index("ix_shipments_partner_site_id", table_name="shipments")
    op.drop_index("ix_shipments_partner_id", table_name="shipments")
    op.drop_index("ix_shipments_site_id", table_name="shipments")
    op.drop_index("ix_shipments_organization_id", table_name="shipments")
    op.drop_table("shipments")

    op.drop_index("ix_batch_document_links_document_id", table_name="batch_document_links")
    op.drop_index("ix_batch_document_links_batch_id", table_name="batch_document_links")
    op.drop_index("ix_batch_document_links_organization_id", table_name="batch_document_links")
    op.drop_table("batch_document_links")

    op.drop_index("ix_batch_relationships_child_batch_id", table_name="batch_relationships")
    op.drop_index("ix_batch_relationships_parent_batch_id", table_name="batch_relationships")
    op.drop_index("ix_batch_relationships_organization_id", table_name="batch_relationships")
    op.drop_table("batch_relationships")

    op.drop_index("ix_batch_material_consumptions_supplier_lot_id", table_name="batch_material_consumptions")
    op.drop_index("ix_batch_material_consumptions_material_id", table_name="batch_material_consumptions")
    op.drop_index("ix_batch_material_consumptions_batch_id", table_name="batch_material_consumptions")
    op.drop_index("ix_batch_material_consumptions_organization_id", table_name="batch_material_consumptions")
    op.drop_table("batch_material_consumptions")

    op.drop_index("ix_batch_dispositions_batch_time", table_name="batch_dispositions")
    op.drop_index("ix_batch_dispositions_batch_id", table_name="batch_dispositions")
    op.drop_index("ix_batch_dispositions_site_id", table_name="batch_dispositions")
    op.drop_index("ix_batch_dispositions_organization_id", table_name="batch_dispositions")
    op.drop_table("batch_dispositions")

    op.drop_index("ix_batch_status_hist_batch_time", table_name="batch_status_history")
    op.drop_index("ix_batch_status_history_batch_id", table_name="batch_status_history")
    op.drop_index("ix_batch_status_history_organization_id", table_name="batch_status_history")
    op.drop_table("batch_status_history")

    op.drop_index("ix_batches_org_status", table_name="batches")
    op.drop_index("ix_batches_quality_review_status", table_name="batches")
    op.drop_index("ix_batches_batch_status", table_name="batches")
    op.drop_index("ix_batches_batch_number", table_name="batches")
    op.drop_index("ix_batches_product_id", table_name="batches")
    op.drop_index("ix_batches_site_id", table_name="batches")
    op.drop_index("ix_batches_organization_id", table_name="batches")
    op.drop_table("batches")

    op.drop_index("ix_supplier_lots_lot_status", table_name="supplier_lots")
    op.drop_index("ix_supplier_lots_supplier_partner_id", table_name="supplier_lots")
    op.drop_index("ix_supplier_lots_material_id", table_name="supplier_lots")
    op.drop_index("ix_supplier_lots_site_id", table_name="supplier_lots")
    op.drop_index("ix_supplier_lots_organization_id", table_name="supplier_lots")
    op.drop_table("supplier_lots")

    op.drop_index("ix_product_material_bills_material_id", table_name="product_material_bills")
    op.drop_index("ix_product_material_bills_product_id", table_name="product_material_bills")
    op.drop_index("ix_product_material_bills_organization_id", table_name="product_material_bills")
    op.drop_table("product_material_bills")

    op.drop_index("ix_materials_default_supplier_partner_id", table_name="materials")
    op.drop_index("ix_materials_material_status", table_name="materials")
    op.drop_index("ix_materials_qualification_status", table_name="materials")
    op.drop_index("ix_materials_material_type", table_name="materials")
    op.drop_index("ix_materials_organization_id", table_name="materials")
    op.drop_table("materials")

    op.drop_index("ix_product_authorizations_regulatory_status", table_name="product_authorizations")
    op.drop_index("ix_product_authorizations_product_id", table_name="product_authorizations")
    op.drop_index("ix_product_authorizations_organization_id", table_name="product_authorizations")
    op.drop_table("product_authorizations")

    op.drop_index("ix_products_product_status", table_name="products")
    op.drop_index("ix_products_site_id", table_name="products")
    op.drop_index("ix_products_organization_id", table_name="products")
    op.drop_table("products")
