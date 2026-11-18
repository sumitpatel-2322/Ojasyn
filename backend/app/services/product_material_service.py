import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.core.database import UnitOfWork, next_tenant_sequence, verify_and_increment_version
from app.core.deps import TenantContext
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.domain.enums import (
    ActorType,
    ApprovalDecision,
    SecurityEventOutcome,
    SecurityEventSeverity,
)
from app.domain.state_machines import assert_transition
from app.models.domain.governance import ApprovalRecord
from app.models.domain.products_materials import (
    Material,
    Product,
    ProductAuthorization,
    ProductMaterialBill,
    SupplierLot,
)
from app.models.schemas.products_materials import (
    MaterialCreateRequest,
    MaterialStatusChangeRequest,
    MaterialUpdateRequest,
    ProductAuthorizationCreateRequest,
    ProductCreateRequest,
    ProductMaterialBillCreateRequest,
    ProductStatusChangeRequest,
    ProductUpdateRequest,
    SupplierLotCreateRequest,
    SupplierLotStatusChangeRequest,
)
from app.repositories.document_sop_repo import DocumentSOPRepository
from app.repositories.governance_repo import OutboxRepository
from app.repositories.organization_repo import OrganizationRepository
from app.repositories.partner_repo import PartnerRepository
from app.repositories.product_material_repo import ProductMaterialRepository
from app.services.audit_service import AuditService

VALID_MATERIAL_TYPES: Set[str] = {
    "API",
    "EXCIPIENT",
    "PACKAGING_PRIMARY",
    "PACKAGING_SECONDARY",
    "SOLVENT",
    "REAGENT",
    "OTHER",
}
VALID_REG_AUTH_STATUSES: Set[str] = {"PENDING", "ACTIVE", "SUSPENDED", "WITHDRAWN"}

PRODUCT_STATUS_TRANSITIONS: Dict[str, Set[str]] = {
    "DRAFT": {"ACTIVE", "BLOCKED"},
    "ACTIVE": {"BLOCKED", "DISCONTINUED"},
    "BLOCKED": {"ACTIVE", "DISCONTINUED"},
    "DISCONTINUED": set(),
}

MATERIAL_STATUS_TRANSITIONS: Dict[str, Set[str]] = {
    "DRAFT": {"ACTIVE", "BLOCKED"},
    "ACTIVE": {"BLOCKED", "DISCONTINUED"},
    "BLOCKED": {"ACTIVE", "DISCONTINUED"},
    "DISCONTINUED": set(),
}

MATERIAL_QUALIFICATION_TRANSITIONS: Dict[str, Set[str]] = {
    "DRAFT": {"UNDER_QUALIFICATION", "APPROVED"},
    "UNDER_QUALIFICATION": {"APPROVED", "DISQUALIFIED"},
    "APPROVED": {"DISQUALIFIED", "OBSOLETE"},
    "DISQUALIFIED": {"UNDER_QUALIFICATION", "OBSOLETE"},
    "OBSOLETE": set(),
}

SUPPLIER_LOT_STATUS_TRANSITIONS: Dict[str, Set[str]] = {
    "QUARANTINED": {"RELEASED", "REJECTED", "BLOCKED"},
    "RELEASED": {"QUARANTINED", "BLOCKED", "EXHAUSTED", "REJECTED"},
    "BLOCKED": {"QUARANTINED", "RELEASED", "REJECTED"},
    "REJECTED": set(),
    "EXHAUSTED": set(),
}

_PRODUCT_VERSIONED_FIELDS = {
    "generic_name",
    "dosage_form",
    "strength_value",
    "strength_unit",
    "route_of_administration",
    "composition_json",
    "specifications_json",
    "shelf_life_months",
    "storage_conditions",
    "specification_document_id",
}


class ProductMaterialService:
    """Tenant-scoped Product (`C1`), Material, BOM, and SupplierLot (`C2`) management service."""

    def __init__(
        self,
        repo: Optional[ProductMaterialRepository] = None,
        org_repo: Optional[OrganizationRepository] = None,
        partner_repo: Optional[PartnerRepository] = None,
        doc_repo: Optional[DocumentSOPRepository] = None,
        audit_service: Optional[AuditService] = None,
        outbox_repo: Optional[OutboxRepository] = None,
    ) -> None:
        self._repo = repo or ProductMaterialRepository()
        self._org_repo = org_repo or OrganizationRepository()
        self._partner_repo = partner_repo or PartnerRepository()
        self._doc_repo = doc_repo or DocumentSOPRepository()
        self._audit = audit_service or AuditService()
        self._outbox = outbox_repo or OutboxRepository()

    def _assert_site_access(self, db: Session, tenant_ctx: TenantContext, site_id: Optional[uuid.UUID]) -> None:
        if site_id is None:
            return
        site = self._org_repo.get_site_for_org(db, tenant_ctx.organization_id, site_id)
        if site is None:
            raise NotFoundError("Site not found in this organization.")
        if not tenant_ctx.is_org_wide and site.id not in tenant_ctx.allowed_site_ids:
            raise PermissionDeniedError("Not authorized to access or manage records for this site.")

    def _assert_document_in_org(
        self, db: Session, organization_id: uuid.UUID, document_id: Optional[uuid.UUID]
    ) -> None:
        if document_id is None:
            return
        if self._doc_repo.get_document_for_org(db, organization_id, document_id) is None:
            raise NotFoundError("Referenced document not found in this organization.")

    def _assert_supplier_partner_in_org(
        self, db: Session, organization_id: uuid.UUID, partner_id: Optional[uuid.UUID]
    ) -> None:
        if partner_id is None:
            return
        partner = self._partner_repo.get_partner_for_org(db, organization_id, partner_id)
        if partner is None:
            raise NotFoundError("Business partner not found in this organization.")
        if partner.partner_type != "SUPPLIER":
            raise ValidationError(
                f"BusinessPartner '{partner.partner_code}' must have partner_type 'SUPPLIER' (got '{partner.partner_type}').",
                error_code="INVALID_PARTNER_TYPE",
            )

    # --- Products (C1) ---

    def create_product(
        self, db: Session, *, tenant_ctx: TenantContext, payload: ProductCreateRequest
    ) -> Product:
        self._assert_site_access(db, tenant_ctx, payload.site_id)
        self._assert_document_in_org(db, tenant_ctx.organization_id, payload.specification_document_id)

        code = payload.product_code.strip().upper()
        if self._repo.get_product_by_code(db, tenant_ctx.organization_id, code) is not None:
            raise ConflictError(f"Product code '{code}' already exists in this organization.")

        with UnitOfWork(db):
            product = Product(
                organization_id=tenant_ctx.organization_id,
                site_id=payload.site_id,
                product_code=code,
                brand_name=payload.brand_name.strip() if payload.brand_name else None,
                generic_name=payload.generic_name.strip(),
                product_category=payload.product_category,
                dosage_form=payload.dosage_form.strip().upper(),
                strength_value=payload.strength_value,
                strength_unit=payload.strength_unit.strip().upper() if payload.strength_unit else None,
                route_of_administration=payload.route_of_administration,
                composition_json=payload.composition_json,
                specifications_json=payload.specifications_json,
                manufacturer_name=payload.manufacturer_name,
                pack_description=payload.pack_description,
                therapeutic_class=payload.therapeutic_class,
                shelf_life_months=payload.shelf_life_months,
                storage_conditions=payload.storage_conditions,
                product_status="DRAFT",
                version_number=1,
                specification_document_id=payload.specification_document_id,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            self._repo.create_product(db, product)
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=product.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="PRODUCT_CREATED",
                entity_type="products",
                entity_id=product.id,
                after_state={"product_code": product.product_code, "product_status": product.product_status},
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="product",
                aggregate_id=product.id,
                event_type="ProductCreated",
                payload_json={"product_id": str(product.id), "product_code": product.product_code},
            )
        db.refresh(product)
        return product

    def list_products(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        status: Optional[str] = None,
        site_id: Optional[uuid.UUID] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Product], int]:
        return self._repo.list_products_for_tenant(
            db,
            tenant_ctx,
            status=status,
            site_id=site_id,
            search=search,
            page=page,
            page_size=page_size,
        )

    def get_product(self, db: Session, *, tenant_ctx: TenantContext, product_id: uuid.UUID) -> Product:
        product = self._repo.get_product_for_org(db, tenant_ctx.organization_id, product_id)
        if product is None:
            raise NotFoundError("Product not found.")
        self._assert_site_access(db, tenant_ctx, product.site_id)
        return product

    def update_product(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        product_id: uuid.UUID,
        payload: ProductUpdateRequest,
    ) -> Product:
        product = self.get_product(db, tenant_ctx=tenant_ctx, product_id=product_id)
        updates = payload.model_dump(exclude_unset=True, exclude={"row_version", "reason"})
        if "site_id" in updates:
            self._assert_site_access(db, tenant_ctx, updates["site_id"])
        if "specification_document_id" in updates:
            self._assert_document_in_org(db, tenant_ctx.organization_id, updates["specification_document_id"])

        with UnitOfWork(db):
            verify_and_increment_version(product, payload.row_version)
            bump_version = any(field in updates for field in _PRODUCT_VERSIONED_FIELDS)
            for field, value in updates.items():
                if field in {"dosage_form", "strength_unit"} and isinstance(value, str):
                    value = value.strip().upper()
                setattr(product, field, value)
            if bump_version:
                product.version_number += 1
            product.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=product.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="PRODUCT_UPDATED",
                entity_type="products",
                entity_id=product.id,
                reason=payload.reason,
                after_state={"version_number": product.version_number, "row_version": product.row_version},
            )
        db.refresh(product)
        return product

    def change_product_status(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        product_id: uuid.UUID,
        payload: ProductStatusChangeRequest,
    ) -> Product:
        product = self.get_product(db, tenant_ctx=tenant_ctx, product_id=product_id)
        target = payload.target_status.strip().upper()
        assert_transition(PRODUCT_STATUS_TRANSITIONS, product.product_status, target, entity_name="Product")

        with UnitOfWork(db):
            verify_and_increment_version(product, payload.row_version)
            prev = product.product_status
            product.product_status = target
            product.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=product.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="PRODUCT_STATUS_CHANGED",
                entity_type="products",
                entity_id=product.id,
                reason=payload.reason,
                before_state={"product_status": prev},
                after_state={"product_status": target},
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="product",
                aggregate_id=product.id,
                event_type="ProductStatusChanged",
                payload_json={"product_id": str(product.id), "from_status": prev, "to_status": target},
            )
        db.refresh(product)
        return product

    # --- Product Authorizations (C1) ---

    def add_product_authorization(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        product_id: uuid.UUID,
        payload: ProductAuthorizationCreateRequest,
    ) -> ProductAuthorization:
        product = self.get_product(db, tenant_ctx=tenant_ctx, product_id=product_id)
        self._assert_document_in_org(db, tenant_ctx.organization_id, payload.evidence_document_id)

        reg_status = payload.regulatory_status.strip().upper()
        if reg_status not in VALID_REG_AUTH_STATUSES:
            raise ValidationError(f"Invalid regulatory_status '{payload.regulatory_status}'.")
        if payload.approved_on and payload.valid_to and payload.valid_to < payload.approved_on:
            raise ValidationError("valid_to cannot precede approved_on.")

        country = payload.country_code.strip().upper()
        auth_num = payload.authorization_number.strip()
        if self._repo.get_authorization_duplicate(
            db, tenant_ctx.organization_id, product.id, country, auth_num
        ) is not None:
            raise ConflictError("Regulatory authorization already exists for this product and country.")

        with UnitOfWork(db):
            auth = ProductAuthorization(
                organization_id=tenant_ctx.organization_id,
                product_id=product.id,
                country_code=country,
                authorization_number=auth_num,
                authorization_holder=payload.authorization_holder,
                issuing_authority=payload.issuing_authority,
                approved_on=payload.approved_on,
                valid_to=payload.valid_to,
                regulatory_status=reg_status,
                evidence_document_id=payload.evidence_document_id,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            self._repo.create_authorization(db, auth)
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=product.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="PRODUCT_AUTHORIZATION_ADDED",
                entity_type="product_authorizations",
                entity_id=auth.id,
                after_state={"country_code": country, "authorization_number": auth_num},
            )
        db.refresh(auth)
        return auth

    def list_product_authorizations(
        self, db: Session, *, tenant_ctx: TenantContext, product_id: uuid.UUID
    ) -> List[ProductAuthorization]:
        product = self.get_product(db, tenant_ctx=tenant_ctx, product_id=product_id)
        return self._repo.list_authorizations_for_product(db, tenant_ctx.organization_id, product.id)

    # --- Product Material BOM (C1/C2) ---

    def add_product_material(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        product_id: uuid.UUID,
        payload: ProductMaterialBillCreateRequest,
    ) -> ProductMaterialBill:
        product = self.get_product(db, tenant_ctx=tenant_ctx, product_id=product_id)
        material = self._repo.get_material_for_org(db, tenant_ctx.organization_id, payload.material_id)
        if material is None:
            raise NotFoundError("Material not found in this organization.")
        if self._repo.get_bill_item(db, tenant_ctx.organization_id, product.id, material.id) is not None:
            raise ConflictError("Material is already linked to this product BOM.")

        with UnitOfWork(db):
            item = ProductMaterialBill(
                organization_id=tenant_ctx.organization_id,
                product_id=product.id,
                material_id=material.id,
                quantity_per_unit=payload.quantity_per_unit,
                unit_code=payload.unit_code.strip().upper(),
                function_role=payload.function_role,
                is_critical_material=payload.is_critical_material,
                notes=payload.notes,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            self._repo.create_bill_item(db, item)
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=product.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="PRODUCT_MATERIAL_LINKED",
                entity_type="product_material_bills",
                entity_id=item.id,
                after_state={"product_id": str(product.id), "material_id": str(material.id)},
            )
        db.refresh(item)
        return item

    def list_product_materials(
        self, db: Session, *, tenant_ctx: TenantContext, product_id: uuid.UUID
    ) -> List[ProductMaterialBill]:
        product = self.get_product(db, tenant_ctx=tenant_ctx, product_id=product_id)
        return self._repo.list_bill_items_for_product(db, tenant_ctx.organization_id, product.id)

    # --- Materials (C2) ---

    def create_material(
        self, db: Session, *, tenant_ctx: TenantContext, payload: MaterialCreateRequest
    ) -> Material:
        mtype = payload.material_type.strip().upper()
        if mtype not in VALID_MATERIAL_TYPES:
            raise ValidationError(f"Invalid material_type '{payload.material_type}'.")

        self._assert_supplier_partner_in_org(db, tenant_ctx.organization_id, payload.default_supplier_partner_id)
        self._assert_document_in_org(db, tenant_ctx.organization_id, payload.specification_document_id)

        code = payload.material_code.strip().upper()
        if self._repo.get_material_by_code(db, tenant_ctx.organization_id, code) is not None:
            raise ConflictError(f"Material code '{code}' already exists in this organization.")

        with UnitOfWork(db):
            material = Material(
                organization_id=tenant_ctx.organization_id,
                material_code=code,
                material_name=payload.material_name.strip(),
                material_type=mtype,
                material_category=payload.material_category,
                cas_number=payload.cas_number,
                pharmacopoeia_grade=payload.pharmacopoeia_grade,
                default_unit_code=payload.default_unit_code.strip().upper(),
                manufacturer_name=payload.manufacturer_name,
                storage_conditions=payload.storage_conditions,
                retest_period_days=payload.retest_period_days,
                specifications_json=payload.specifications_json,
                qualification_status="DRAFT",
                material_status="DRAFT",
                default_supplier_partner_id=payload.default_supplier_partner_id,
                specification_document_id=payload.specification_document_id,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            self._repo.create_material(db, material)
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="MATERIAL_CREATED",
                entity_type="materials",
                entity_id=material.id,
                after_state={"material_code": material.material_code, "material_type": mtype},
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="material",
                aggregate_id=material.id,
                event_type="MaterialCreated",
                payload_json={"material_id": str(material.id), "material_code": material.material_code},
            )
        db.refresh(material)
        return material

    def list_materials(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        material_type: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Material], int]:
        return self._repo.list_materials_for_org(
            db,
            tenant_ctx.organization_id,
            material_type=material_type,
            status=status,
            search=search,
            page=page,
            page_size=page_size,
        )

    def get_material(self, db: Session, *, tenant_ctx: TenantContext, material_id: uuid.UUID) -> Material:
        material = self._repo.get_material_for_org(db, tenant_ctx.organization_id, material_id)
        if material is None:
            raise NotFoundError("Material not found.")
        return material

    def update_material(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        material_id: uuid.UUID,
        payload: MaterialUpdateRequest,
    ) -> Material:
        material = self.get_material(db, tenant_ctx=tenant_ctx, material_id=material_id)
        updates = payload.model_dump(exclude_unset=True, exclude={"row_version", "reason"})
        if "default_supplier_partner_id" in updates:
            self._assert_supplier_partner_in_org(
                db, tenant_ctx.organization_id, updates["default_supplier_partner_id"]
            )
        if "specification_document_id" in updates:
            self._assert_document_in_org(db, tenant_ctx.organization_id, updates["specification_document_id"])

        with UnitOfWork(db):
            verify_and_increment_version(material, payload.row_version)
            for field, value in updates.items():
                if field == "default_unit_code" and isinstance(value, str):
                    value = value.strip().upper()
                setattr(material, field, value)
            material.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="MATERIAL_UPDATED",
                entity_type="materials",
                entity_id=material.id,
                reason=payload.reason,
                after_state={"row_version": material.row_version},
            )
        db.refresh(material)
        return material

    def change_material_status(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        material_id: uuid.UUID,
        payload: MaterialStatusChangeRequest,
    ) -> Material:
        if payload.material_status is None and payload.qualification_status is None:
            raise ValidationError("At least one of material_status or qualification_status must be supplied.")

        material = self.get_material(db, tenant_ctx=tenant_ctx, material_id=material_id)
        prev_mat_status = material.material_status
        prev_qual_status = material.qualification_status

        target_mat = payload.material_status.strip().upper() if payload.material_status else None
        target_qual = payload.qualification_status.strip().upper() if payload.qualification_status else None

        if target_mat is not None:
            assert_transition(MATERIAL_STATUS_TRANSITIONS, prev_mat_status, target_mat, entity_name="Material")
        if target_qual is not None:
            assert_transition(
                MATERIAL_QUALIFICATION_TRANSITIONS,
                prev_qual_status,
                target_qual,
                entity_name="MaterialQualification",
            )

        with UnitOfWork(db):
            verify_and_increment_version(material, payload.row_version)
            if target_mat is not None:
                material.material_status = target_mat
            if target_qual is not None:
                material.qualification_status = target_qual
            material.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="MATERIAL_STATUS_CHANGED",
                entity_type="materials",
                entity_id=material.id,
                reason=payload.reason,
                before_state={"material_status": prev_mat_status, "qualification_status": prev_qual_status},
                after_state={
                    "material_status": material.material_status,
                    "qualification_status": material.qualification_status,
                },
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="material",
                aggregate_id=material.id,
                event_type="MaterialStatusChanged",
                payload_json={
                    "material_id": str(material.id),
                    "material_status": material.material_status,
                    "qualification_status": material.qualification_status,
                },
            )
        db.refresh(material)
        return material

    # --- Supplier Lots (C2) ---

    def create_supplier_lot(
        self, db: Session, *, tenant_ctx: TenantContext, payload: SupplierLotCreateRequest
    ) -> SupplierLot:
        self._assert_site_access(db, tenant_ctx, payload.site_id)
        material = self._repo.get_material_for_org(db, tenant_ctx.organization_id, payload.material_id)
        if material is None:
            raise NotFoundError("Material not found in this organization.")
        self._assert_supplier_partner_in_org(db, tenant_ctx.organization_id, payload.supplier_partner_id)
        self._assert_document_in_org(db, tenant_ctx.organization_id, payload.coa_document_id)

        if (
            payload.manufacturing_date is not None
            and payload.expiry_or_retest_date is not None
            and payload.expiry_or_retest_date < payload.manufacturing_date
        ):
            raise ValidationError("expiry_or_retest_date cannot precede manufacturing_date.")

        if (
            self._repo.get_supplier_lot_by_vendor_tuple(
                db,
                tenant_ctx.organization_id,
                material.id,
                payload.supplier_partner_id,
                payload.supplier_lot_number,
            )
            is not None
        ):
            raise ConflictError("Supplier lot number already exists for this material and supplier.")

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            if payload.internal_lot_number:
                internal_no = payload.internal_lot_number.strip().upper()
                if (
                    self._repo.get_supplier_lot_by_internal_number(db, tenant_ctx.organization_id, internal_no)
                    is not None
                ):
                    raise ConflictError(f"Internal lot number '{internal_no}' already exists.")
            else:
                seq = next_tenant_sequence(db, tenant_ctx.organization_id, "SLOT", period_year=now.year)
                internal_no = f"SLOT-{now.year}-{seq:06d}"

            lot = SupplierLot(
                organization_id=tenant_ctx.organization_id,
                site_id=payload.site_id,
                material_id=material.id,
                supplier_partner_id=payload.supplier_partner_id,
                internal_lot_number=internal_no,
                supplier_lot_number=payload.supplier_lot_number.strip(),
                manufacturer_name=payload.manufacturer_name,
                received_quantity=payload.received_quantity,
                remaining_quantity=payload.received_quantity,
                unit_code=payload.unit_code.strip().upper(),
                received_date=payload.received_date,
                manufacturing_date=payload.manufacturing_date,
                expiry_or_retest_date=payload.expiry_or_retest_date,
                lot_status="QUARANTINED",
                coa_document_id=payload.coa_document_id,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            self._repo.create_supplier_lot(db, lot)
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=lot.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="SUPPLIER_LOT_RECEIVED",
                entity_type="supplier_lots",
                entity_id=lot.id,
                after_state={"internal_lot_number": lot.internal_lot_number, "lot_status": lot.lot_status},
            )
        db.refresh(lot)
        return lot

    def list_supplier_lots(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        material_id: Optional[uuid.UUID] = None,
        lot_status: Optional[str] = None,
        site_id: Optional[uuid.UUID] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[SupplierLot], int]:
        return self._repo.list_supplier_lots_for_tenant(
            db,
            tenant_ctx,
            material_id=material_id,
            lot_status=lot_status,
            site_id=site_id,
            page=page,
            page_size=page_size,
        )

    def get_supplier_lot(self, db: Session, *, tenant_ctx: TenantContext, lot_id: uuid.UUID) -> SupplierLot:
        lot = self._repo.get_supplier_lot_for_org(db, tenant_ctx.organization_id, lot_id)
        if lot is None:
            raise NotFoundError("Supplier lot not found.")
        self._assert_site_access(db, tenant_ctx, lot.site_id)
        return lot

    def change_supplier_lot_status(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        lot_id: uuid.UUID,
        payload: SupplierLotStatusChangeRequest,
    ) -> SupplierLot:
        lot = self._repo.get_supplier_lot_for_update(db, tenant_ctx.organization_id, lot_id)
        if lot is None:
            raise NotFoundError("Supplier lot not found.")
        self._assert_site_access(db, tenant_ctx, lot.site_id)

        target = payload.target_status.strip().upper()
        assert_transition(SUPPLIER_LOT_STATUS_TRANSITIONS, lot.lot_status, target, entity_name="SupplierLot")

        # Maker-checker on quality disposition of received supplier lots
        if target in {"RELEASED", "REJECTED"} and lot.created_by_user_id == tenant_ctx.user_id:
            self._audit.record_security_event(
                event_type="SELF_APPROVAL_BLOCKED",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                target_resource=f"/api/v1/supplier-lots/{lot_id}/status",
                details_json={"lot_id": str(lot_id), "target_status": target},
            )
            raise PermissionDeniedError(
                "Maker-checker violation: receiving user cannot release or reject their own supplier lot."
            )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            verify_and_increment_version(lot, payload.row_version)
            prev = lot.lot_status
            lot.lot_status = target
            lot.updated_by_user_id = tenant_ctx.user_id

            if target in {"RELEASED", "REJECTED"}:
                lot.approved_by_membership_id = tenant_ctx.membership_id
                lot.approved_at = now
                if tenant_ctx.membership_id is not None:
                    db.add(
                        ApprovalRecord(
                            organization_id=tenant_ctx.organization_id,
                            entity_type="supplier_lots",
                            entity_id=lot.id,
                            approval_stage="SUPPLIER_LOT_DISPOSITION",
                            decision=(
                                ApprovalDecision.APPROVED.value
                                if target == "RELEASED"
                                else ApprovalDecision.REJECTED.value
                            ),
                            approver_membership_id=tenant_ctx.membership_id,
                            decided_at=now,
                            comments=payload.reason,
                        )
                    )

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=lot.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="SUPPLIER_LOT_STATUS_CHANGED",
                entity_type="supplier_lots",
                entity_id=lot.id,
                reason=payload.reason,
                before_state={"lot_status": prev},
                after_state={"lot_status": target},
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="supplier_lot",
                aggregate_id=lot.id,
                event_type="SupplierLotStatusChanged",
                payload_json={"supplier_lot_id": str(lot.id), "from_status": prev, "to_status": target},
            )
        db.refresh(lot)
        return lot
