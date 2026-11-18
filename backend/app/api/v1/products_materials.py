import uuid
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.deps import TenantContext, get_db, get_tenant_context, require_permission
from app.core.exceptions import PermissionDeniedError
from app.domain.enums import SecurityEventOutcome, SecurityEventSeverity
from app.models.schemas.common import Page
from app.models.schemas.products_materials import (
    MaterialCreateRequest,
    MaterialResponse,
    MaterialStatusChangeRequest,
    MaterialUpdateRequest,
    ProductAuthorizationCreateRequest,
    ProductAuthorizationResponse,
    ProductCreateRequest,
    ProductMaterialBillCreateRequest,
    ProductMaterialBillResponse,
    ProductResponse,
    ProductStatusChangeRequest,
    ProductUpdateRequest,
    SupplierLotCreateRequest,
    SupplierLotResponse,
    SupplierLotStatusChangeRequest,
)
from app.services.audit_service import AuditService
from app.services.product_material_service import ProductMaterialService

router = APIRouter(tags=["products-materials"])

_svc = ProductMaterialService()
_audit_svc = AuditService()


def _require_any_permission(required: Tuple[str, ...]):
    def _dep(
        request: Request,
        tenant_ctx: TenantContext = Depends(get_tenant_context),
    ) -> TenantContext:
        if not (set(required) & set(tenant_ctx.permission_codes)):
            _audit_svc.record_security_event(
                event_type="RBAC_PERMISSION_DENIED",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.WARNING.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=request.client.host if request.client else None,
                target_resource=str(request.url.path),
                details_json={"required_any": list(required)},
            )
            raise PermissionDeniedError(
                f"Missing one of required permissions {required}.",
                error_code="INSUFFICIENT_PERMISSION",
            )
        return tenant_ctx

    return _dep


# --- Product Management (C1) ---


@router.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("product.manage")),
    db: Session = Depends(get_db),
) -> ProductResponse:
    product = _svc.create_product(db, tenant_ctx=tenant_ctx, payload=payload)
    return ProductResponse.model_validate(product)


@router.get("/products", response_model=Page[ProductResponse])
def list_products(
    product_status: Optional[str] = Query(default=None, alias="status"),
    site_id: Optional[uuid.UUID] = Query(default=None),
    search: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(require_permission("product.read")),
    db: Session = Depends(get_db),
) -> Page[ProductResponse]:
    items, total = _svc.list_products(
        db,
        tenant_ctx=tenant_ctx,
        status=product_status,
        site_id=site_id,
        search=search,
        page=page,
        page_size=page_size,
    )
    return Page(
        items=[ProductResponse.model_validate(p) for p in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/products/{product_id}", response_model=ProductResponse)
def get_product(
    product_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("product.read")),
    db: Session = Depends(get_db),
) -> ProductResponse:
    product = _svc.get_product(db, tenant_ctx=tenant_ctx, product_id=product_id)
    return ProductResponse.model_validate(product)


@router.patch("/products/{product_id}", response_model=ProductResponse)
def update_product(
    product_id: uuid.UUID,
    payload: ProductUpdateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("product.manage")),
    db: Session = Depends(get_db),
) -> ProductResponse:
    product = _svc.update_product(db, tenant_ctx=tenant_ctx, product_id=product_id, payload=payload)
    return ProductResponse.model_validate(product)


@router.post("/products/{product_id}/status", response_model=ProductResponse)
def change_product_status(
    product_id: uuid.UUID,
    payload: ProductStatusChangeRequest,
    tenant_ctx: TenantContext = Depends(require_permission("product.manage")),
    db: Session = Depends(get_db),
) -> ProductResponse:
    product = _svc.change_product_status(
        db, tenant_ctx=tenant_ctx, product_id=product_id, payload=payload
    )
    return ProductResponse.model_validate(product)


@router.post(
    "/products/{product_id}/authorizations",
    response_model=ProductAuthorizationResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_product_authorization(
    product_id: uuid.UUID,
    payload: ProductAuthorizationCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("product.manage")),
    db: Session = Depends(get_db),
) -> ProductAuthorizationResponse:
    auth = _svc.add_product_authorization(
        db, tenant_ctx=tenant_ctx, product_id=product_id, payload=payload
    )
    return ProductAuthorizationResponse.model_validate(auth)


@router.get("/products/{product_id}/authorizations", response_model=List[ProductAuthorizationResponse])
def list_product_authorizations(
    product_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("product.read")),
    db: Session = Depends(get_db),
) -> List[ProductAuthorizationResponse]:
    rows = _svc.list_product_authorizations(db, tenant_ctx=tenant_ctx, product_id=product_id)
    return [ProductAuthorizationResponse.model_validate(a) for a in rows]


@router.post(
    "/products/{product_id}/materials",
    response_model=ProductMaterialBillResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_product_material_bill(
    product_id: uuid.UUID,
    payload: ProductMaterialBillCreateRequest,
    tenant_ctx: TenantContext = Depends(_require_any_permission(("product.manage", "mbr.manage"))),
    db: Session = Depends(get_db),
) -> ProductMaterialBillResponse:
    item = _svc.add_product_material(db, tenant_ctx=tenant_ctx, product_id=product_id, payload=payload)
    return ProductMaterialBillResponse.model_validate(item)


@router.get("/products/{product_id}/materials", response_model=List[ProductMaterialBillResponse])
def list_product_materials(
    product_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("product.read")),
    db: Session = Depends(get_db),
) -> List[ProductMaterialBillResponse]:
    rows = _svc.list_product_materials(db, tenant_ctx=tenant_ctx, product_id=product_id)
    return [ProductMaterialBillResponse.model_validate(item) for item in rows]


# --- Material & Supplier Lot Management (C2) ---


@router.post("/materials", response_model=MaterialResponse, status_code=status.HTTP_201_CREATED)
def create_material(
    payload: MaterialCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("material.manage")),
    db: Session = Depends(get_db),
) -> MaterialResponse:
    mat = _svc.create_material(db, tenant_ctx=tenant_ctx, payload=payload)
    return MaterialResponse.model_validate(mat)


@router.get("/materials", response_model=Page[MaterialResponse])
def list_materials(
    material_type: Optional[str] = Query(default=None),
    material_status: Optional[str] = Query(default=None, alias="status"),
    search: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(require_permission("material.read")),
    db: Session = Depends(get_db),
) -> Page[MaterialResponse]:
    items, total = _svc.list_materials(
        db,
        tenant_ctx=tenant_ctx,
        material_type=material_type,
        status=material_status,
        search=search,
        page=page,
        page_size=page_size,
    )
    return Page(
        items=[MaterialResponse.model_validate(m) for m in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/materials/{material_id}", response_model=MaterialResponse)
def get_material(
    material_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("material.read")),
    db: Session = Depends(get_db),
) -> MaterialResponse:
    mat = _svc.get_material(db, tenant_ctx=tenant_ctx, material_id=material_id)
    return MaterialResponse.model_validate(mat)


@router.patch("/materials/{material_id}", response_model=MaterialResponse)
def update_material(
    material_id: uuid.UUID,
    payload: MaterialUpdateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("material.manage")),
    db: Session = Depends(get_db),
) -> MaterialResponse:
    mat = _svc.update_material(db, tenant_ctx=tenant_ctx, material_id=material_id, payload=payload)
    return MaterialResponse.model_validate(mat)


@router.post("/materials/{material_id}/status", response_model=MaterialResponse)
def change_material_status(
    material_id: uuid.UUID,
    payload: MaterialStatusChangeRequest,
    tenant_ctx: TenantContext = Depends(require_permission("material.manage")),
    db: Session = Depends(get_db),
) -> MaterialResponse:
    mat = _svc.change_material_status(db, tenant_ctx=tenant_ctx, material_id=material_id, payload=payload)
    return MaterialResponse.model_validate(mat)


@router.post("/supplier-lots", response_model=SupplierLotResponse, status_code=status.HTTP_201_CREATED)
def create_supplier_lot(
    payload: SupplierLotCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("material.manage")),
    db: Session = Depends(get_db),
) -> SupplierLotResponse:
    lot = _svc.create_supplier_lot(db, tenant_ctx=tenant_ctx, payload=payload)
    return SupplierLotResponse.model_validate(lot)


@router.get("/supplier-lots", response_model=Page[SupplierLotResponse])
def list_supplier_lots(
    material_id: Optional[uuid.UUID] = Query(default=None),
    lot_status: Optional[str] = Query(default=None),
    site_id: Optional[uuid.UUID] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(require_permission("material.read")),
    db: Session = Depends(get_db),
) -> Page[SupplierLotResponse]:
    items, total = _svc.list_supplier_lots(
        db,
        tenant_ctx=tenant_ctx,
        material_id=material_id,
        lot_status=lot_status,
        site_id=site_id,
        page=page,
        page_size=page_size,
    )
    return Page(
        items=[SupplierLotResponse.model_validate(l) for l in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/supplier-lots/{lot_id}", response_model=SupplierLotResponse)
def get_supplier_lot(
    lot_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("material.read")),
    db: Session = Depends(get_db),
) -> SupplierLotResponse:
    lot = _svc.get_supplier_lot(db, tenant_ctx=tenant_ctx, lot_id=lot_id)
    return SupplierLotResponse.model_validate(lot)


@router.post("/supplier-lots/{lot_id}/status", response_model=SupplierLotResponse)
def change_supplier_lot_status(
    lot_id: uuid.UUID,
    payload: SupplierLotStatusChangeRequest,
    tenant_ctx: TenantContext = Depends(_require_any_permission(("material.manage", "qc_result.approve"))),
    db: Session = Depends(get_db),
) -> SupplierLotResponse:
    lot = _svc.change_supplier_lot_status(db, tenant_ctx=tenant_ctx, lot_id=lot_id, payload=payload)
    return SupplierLotResponse.model_validate(lot)
