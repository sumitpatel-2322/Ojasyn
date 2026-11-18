import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import TenantContext
from app.models.domain.products_materials import (
    Material,
    Product,
    ProductAuthorization,
    ProductMaterialBill,
    SupplierLot,
)
from app.repositories.base import apply_tenant_filter, paginate


class ProductMaterialRepository:
    """Tenant-scoped data access for Products, Authorizations, Materials, BOMs, and SupplierLots."""

    # --- Products ---

    def create_product(self, db: Session, product: Product) -> Product:
        db.add(product)
        db.flush()
        return product

    def get_product_for_org(
        self, db: Session, organization_id: uuid.UUID, product_id: uuid.UUID
    ) -> Optional[Product]:
        return db.scalar(
            select(Product).where(Product.organization_id == organization_id, Product.id == product_id)
        )

    def get_product_by_code(
        self, db: Session, organization_id: uuid.UUID, product_code: str
    ) -> Optional[Product]:
        return db.scalar(
            select(Product).where(
                Product.organization_id == organization_id,
                func.lower(Product.product_code) == product_code.strip().lower(),
            )
        )

    def list_products_for_tenant(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        *,
        status: Optional[str] = None,
        site_id: Optional[uuid.UUID] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Product], int]:
        stmt = select(Product)
        stmt = apply_tenant_filter(stmt, Product, tenant_ctx, allow_org_wide_site_null=True)
        if status:
            stmt = stmt.where(Product.product_status == status.strip().upper())
        if site_id is not None:
            stmt = stmt.where(Product.site_id == site_id)
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    Product.product_code.ilike(like),
                    Product.generic_name.ilike(like),
                    Product.brand_name.ilike(like),
                )
            )
        stmt = stmt.order_by(Product.created_at.desc())
        return paginate(db, stmt, page=page, page_size=page_size)

    # --- ProductAuthorizations ---

    def create_authorization(self, db: Session, auth: ProductAuthorization) -> ProductAuthorization:
        db.add(auth)
        db.flush()
        return auth

    def get_authorization_duplicate(
        self,
        db: Session,
        organization_id: uuid.UUID,
        product_id: uuid.UUID,
        country_code: str,
        authorization_number: str,
    ) -> Optional[ProductAuthorization]:
        return db.scalar(
            select(ProductAuthorization).where(
                ProductAuthorization.organization_id == organization_id,
                ProductAuthorization.product_id == product_id,
                ProductAuthorization.country_code == country_code.strip().upper(),
                ProductAuthorization.authorization_number == authorization_number.strip(),
            )
        )

    def list_authorizations_for_product(
        self, db: Session, organization_id: uuid.UUID, product_id: uuid.UUID
    ) -> List[ProductAuthorization]:
        stmt = (
            select(ProductAuthorization)
            .where(
                ProductAuthorization.organization_id == organization_id,
                ProductAuthorization.product_id == product_id,
            )
            .order_by(ProductAuthorization.country_code.asc(), ProductAuthorization.created_at.desc())
        )
        return list(db.scalars(stmt).all())

    # --- Materials ---

    def create_material(self, db: Session, material: Material) -> Material:
        db.add(material)
        db.flush()
        return material

    def get_material_for_org(
        self, db: Session, organization_id: uuid.UUID, material_id: uuid.UUID
    ) -> Optional[Material]:
        return db.scalar(
            select(Material).where(Material.organization_id == organization_id, Material.id == material_id)
        )

    def get_material_by_code(
        self, db: Session, organization_id: uuid.UUID, material_code: str
    ) -> Optional[Material]:
        return db.scalar(
            select(Material).where(
                Material.organization_id == organization_id,
                func.lower(Material.material_code) == material_code.strip().lower(),
            )
        )

    def list_materials_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        *,
        material_type: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Material], int]:
        stmt = select(Material).where(Material.organization_id == organization_id)
        if material_type:
            stmt = stmt.where(Material.material_type == material_type.strip().upper())
        if status:
            stmt = stmt.where(Material.material_status == status.strip().upper())
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    Material.material_code.ilike(like),
                    Material.material_name.ilike(like),
                )
            )
        stmt = stmt.order_by(Material.created_at.desc())
        return paginate(db, stmt, page=page, page_size=page_size)

    # --- Product Material BOM ---

    def create_bill_item(self, db: Session, item: ProductMaterialBill) -> ProductMaterialBill:
        db.add(item)
        db.flush()
        return item

    def get_bill_item(
        self, db: Session, organization_id: uuid.UUID, product_id: uuid.UUID, material_id: uuid.UUID
    ) -> Optional[ProductMaterialBill]:
        return db.scalar(
            select(ProductMaterialBill).where(
                ProductMaterialBill.organization_id == organization_id,
                ProductMaterialBill.product_id == product_id,
                ProductMaterialBill.material_id == material_id,
            )
        )

    def list_bill_items_for_product(
        self, db: Session, organization_id: uuid.UUID, product_id: uuid.UUID
    ) -> List[ProductMaterialBill]:
        stmt = (
            select(ProductMaterialBill)
            .where(
                ProductMaterialBill.organization_id == organization_id,
                ProductMaterialBill.product_id == product_id,
            )
            .order_by(ProductMaterialBill.is_critical_material.desc(), ProductMaterialBill.created_at.asc())
        )
        return list(db.scalars(stmt).all())

    # --- SupplierLots ---

    def create_supplier_lot(self, db: Session, lot: SupplierLot) -> SupplierLot:
        db.add(lot)
        db.flush()
        return lot

    def get_supplier_lot_for_org(
        self, db: Session, organization_id: uuid.UUID, lot_id: uuid.UUID
    ) -> Optional[SupplierLot]:
        return db.scalar(
            select(SupplierLot).where(SupplierLot.organization_id == organization_id, SupplierLot.id == lot_id)
        )

    def get_supplier_lot_for_update(
        self, db: Session, organization_id: uuid.UUID, lot_id: uuid.UUID
    ) -> Optional[SupplierLot]:
        return db.scalar(
            select(SupplierLot)
            .where(SupplierLot.organization_id == organization_id, SupplierLot.id == lot_id)
            .with_for_update()
        )

    def get_supplier_lot_by_internal_number(
        self, db: Session, organization_id: uuid.UUID, internal_lot_number: str
    ) -> Optional[SupplierLot]:
        return db.scalar(
            select(SupplierLot).where(
                SupplierLot.organization_id == organization_id,
                func.lower(SupplierLot.internal_lot_number) == internal_lot_number.strip().lower(),
            )
        )

    def get_supplier_lot_by_vendor_tuple(
        self,
        db: Session,
        organization_id: uuid.UUID,
        material_id: uuid.UUID,
        supplier_partner_id: uuid.UUID,
        supplier_lot_number: str,
    ) -> Optional[SupplierLot]:
        return db.scalar(
            select(SupplierLot).where(
                SupplierLot.organization_id == organization_id,
                SupplierLot.material_id == material_id,
                SupplierLot.supplier_partner_id == supplier_partner_id,
                func.lower(SupplierLot.supplier_lot_number) == supplier_lot_number.strip().lower(),
            )
        )

    def list_supplier_lots_for_tenant(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        *,
        material_id: Optional[uuid.UUID] = None,
        lot_status: Optional[str] = None,
        site_id: Optional[uuid.UUID] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[SupplierLot], int]:
        stmt = select(SupplierLot)
        stmt = apply_tenant_filter(stmt, SupplierLot, tenant_ctx, allow_org_wide_site_null=True)
        if material_id is not None:
            stmt = stmt.where(SupplierLot.material_id == material_id)
        if lot_status:
            stmt = stmt.where(SupplierLot.lot_status == lot_status.strip().upper())
        if site_id is not None:
            stmt = stmt.where(SupplierLot.site_id == site_id)
        stmt = stmt.order_by(SupplierLot.received_date.desc(), SupplierLot.created_at.desc())
        return paginate(db, stmt, page=page, page_size=page_size)
