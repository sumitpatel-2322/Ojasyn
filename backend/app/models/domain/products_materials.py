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


class Product(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Regulated pharmaceutical product master record (`C1`)."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("organization_id", "product_code", name="uq_products_org_code"),
        UniqueConstraint("organization_id", "id", name="uq_products_org_id_id"),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_products_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "specification_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_products_org_spec_doc",
        ),
        CheckConstraint(
            "shelf_life_months IS NULL OR shelf_life_months > 0",
            name="ck_products_shelf_life_pos",
        ),
        CheckConstraint("version_number >= 1", name="ck_products_version_pos"),
    )

    site_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    product_code: Mapped[str] = mapped_column(String(60), nullable=False)
    brand_name: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    generic_name: Mapped[str] = mapped_column(String(180), nullable=False)
    product_category: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    dosage_form: Mapped[str] = mapped_column(String(60), nullable=False)
    strength_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True)
    strength_unit: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    route_of_administration: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    composition_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    specifications_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    manufacturer_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    pack_description: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    therapeutic_class: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    shelf_life_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    storage_conditions: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    product_status: Mapped[str] = mapped_column(String(30), nullable=False, default="DRAFT", index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    specification_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )

    authorizations: Mapped[List["ProductAuthorization"]] = relationship(
        "ProductAuthorization",
        back_populates="product",
        foreign_keys="ProductAuthorization.product_id",
    )
    material_bills: Mapped[List["ProductMaterialBill"]] = relationship(
        "ProductMaterialBill",
        back_populates="product",
        foreign_keys="ProductMaterialBill.product_id",
    )


class ProductAuthorization(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Jurisdictional marketing / regulatory authorization per product (`C1`)."""

    __tablename__ = "product_authorizations"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "product_id",
            "country_code",
            "authorization_number",
            name="uq_product_auth_org_prod_country_num",
        ),
        ForeignKeyConstraint(
            ["organization_id", "product_id"],
            ["products.organization_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_product_auth_org_product",
        ),
        ForeignKeyConstraint(
            ["organization_id", "evidence_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_product_auth_org_doc",
        ),
        CheckConstraint(
            "valid_to IS NULL OR approved_on IS NULL OR valid_to >= approved_on",
            name="ck_product_auth_valid_dates",
        ),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    authorization_number: Mapped[str] = mapped_column(String(100), nullable=False)
    authorization_holder: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    issuing_authority: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    approved_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    valid_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    regulatory_status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING", index=True)
    evidence_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )

    product: Mapped["Product"] = relationship(
        "Product",
        back_populates="authorizations",
        foreign_keys=[product_id],
    )


class Material(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Raw material, API, excipient, solvent, reagent, or packaging component master (`C2`)."""

    __tablename__ = "materials"
    __table_args__ = (
        UniqueConstraint("organization_id", "material_code", name="uq_materials_org_code"),
        UniqueConstraint("organization_id", "id", name="uq_materials_org_id_id"),
        ForeignKeyConstraint(
            ["organization_id", "default_supplier_partner_id"],
            ["business_partners.organization_id", "business_partners.id"],
            ondelete="RESTRICT",
            name="fk_materials_org_default_supplier",
        ),
        ForeignKeyConstraint(
            ["organization_id", "specification_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_materials_org_spec_doc",
        ),
        CheckConstraint(
            "retest_period_days IS NULL OR retest_period_days > 0",
            name="ck_materials_retest_pos",
        ),
    )

    material_code: Mapped[str] = mapped_column(String(60), nullable=False)
    material_name: Mapped[str] = mapped_column(String(180), nullable=False)
    material_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    material_category: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    cas_number: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    pharmacopoeia_grade: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    default_unit_code: Mapped[str] = mapped_column(String(30), nullable=False)
    manufacturer_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    storage_conditions: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    retest_period_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    specifications_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    qualification_status: Mapped[str] = mapped_column(String(30), nullable=False, default="DRAFT", index=True)
    material_status: Mapped[str] = mapped_column(String(30), nullable=False, default="DRAFT", index=True)
    default_supplier_partner_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("business_partners.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    specification_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )

    supplier_lots: Mapped[List["SupplierLot"]] = relationship(
        "SupplierLot",
        back_populates="material",
        foreign_keys="SupplierLot.material_id",
    )
    product_bills: Mapped[List["ProductMaterialBill"]] = relationship(
        "ProductMaterialBill",
        back_populates="material",
        foreign_keys="ProductMaterialBill.material_id",
    )


class ProductMaterialBill(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Bill-of-materials association linking a Product to its required Materials (`C1`/`C2`)."""

    __tablename__ = "product_material_bills"
    __table_args__ = (
        UniqueConstraint("organization_id", "product_id", "material_id", name="uq_pmb_org_product_material"),
        ForeignKeyConstraint(
            ["organization_id", "product_id"],
            ["products.organization_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_pmb_org_product",
        ),
        ForeignKeyConstraint(
            ["organization_id", "material_id"],
            ["materials.organization_id", "materials.id"],
            ondelete="RESTRICT",
            name="fk_pmb_org_material",
        ),
        CheckConstraint("quantity_per_unit > 0", name="ck_pmb_qty_pos"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("materials.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quantity_per_unit: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(30), nullable=False)
    function_role: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    is_critical_material: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    product: Mapped["Product"] = relationship(
        "Product",
        back_populates="material_bills",
        foreign_keys=[product_id],
    )
    material: Mapped["Material"] = relationship(
        "Material",
        back_populates="product_bills",
        foreign_keys=[material_id],
    )


class SupplierLot(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Received supplier material lot inventory with quarantine/release governance (`C2`)."""

    __tablename__ = "supplier_lots"
    __table_args__ = (
        UniqueConstraint("organization_id", "internal_lot_number", name="uq_supplier_lots_org_internal_no"),
        UniqueConstraint(
            "organization_id",
            "material_id",
            "supplier_partner_id",
            "supplier_lot_number",
            name="uq_supplier_lots_org_mat_sup_lot",
        ),
        UniqueConstraint("organization_id", "id", name="uq_supplier_lots_org_id_id"),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_supplier_lots_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "material_id"],
            ["materials.organization_id", "materials.id"],
            ondelete="RESTRICT",
            name="fk_supplier_lots_org_material",
        ),
        ForeignKeyConstraint(
            ["organization_id", "supplier_partner_id"],
            ["business_partners.organization_id", "business_partners.id"],
            ondelete="RESTRICT",
            name="fk_supplier_lots_org_supplier",
        ),
        ForeignKeyConstraint(
            ["organization_id", "coa_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_supplier_lots_org_coa_doc",
        ),
        ForeignKeyConstraint(
            ["organization_id", "approved_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_supplier_lots_org_approver",
        ),
        CheckConstraint("received_quantity > 0", name="ck_supplier_lots_recv_qty_pos"),
        CheckConstraint(
            "remaining_quantity >= 0 AND remaining_quantity <= received_quantity",
            name="ck_supplier_lots_rem_qty_bounds",
        ),
        CheckConstraint(
            "expiry_or_retest_date IS NULL OR manufacturing_date IS NULL OR expiry_or_retest_date >= manufacturing_date",
            name="ck_supplier_lots_dates",
        ),
    )

    site_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("materials.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    supplier_partner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("business_partners.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    internal_lot_number: Mapped[str] = mapped_column(String(80), nullable=False)
    supplier_lot_number: Mapped[str] = mapped_column(String(80), nullable=False)
    manufacturer_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    received_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    remaining_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(30), nullable=False)
    received_date: Mapped[date] = mapped_column(Date, nullable=False)
    manufacturing_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expiry_or_retest_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    lot_status: Mapped[str] = mapped_column(String(30), nullable=False, default="QUARANTINED", index=True)
    coa_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    approved_by_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    material: Mapped["Material"] = relationship(
        "Material",
        back_populates="supplier_lots",
        foreign_keys=[material_id],
    )
