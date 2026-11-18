import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.deps import TenantContext, get_db, get_tenant_context, require_permission
from app.core.exceptions import PermissionDeniedError
from app.domain.enums import SecurityEventOutcome, SecurityEventSeverity
from app.models.schemas.common import Page
from app.models.schemas.traceability import (
    BatchCreateRequest,
    BatchDetailResponse,
    BatchDispositionRequest,
    BatchDispositionResponse,
    BatchDocumentLinkCreateRequest,
    BatchDocumentLinkResponse,
    BatchMaterialConsumptionCreateRequest,
    BatchMaterialConsumptionResponse,
    BatchMaterialConsumptionVerifyRequest,
    BatchOperationalStatusRequest,
    BatchRelationshipCreateRequest,
    BatchRelationshipResponse,
    BatchSummaryResponse,
    BatchTraceabilityResponse,
    BatchUpdateRequest,
)
from app.services.audit_service import AuditService
from app.services.distribution_traceability_service import DistributionTraceabilityService
from app.services.product_batch_service import ProductBatchService

router = APIRouter(tags=["batches"])

_batch_svc = ProductBatchService()
_traceability_svc = DistributionTraceabilityService()
_audit_svc = AuditService()


def _client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def _require_any_permission(*permission_codes: str):
    def _dependency(
        request: Request,
        tenant_ctx: TenantContext = Depends(get_tenant_context),
        db: Session = Depends(get_db),
    ) -> TenantContext:
        if not any(code in tenant_ctx.permission_codes for code in permission_codes):
            db.rollback()
            _audit_svc.record_security_event(
                event_type="RBAC_PERMISSION_DENIED",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.WARNING.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=_client_ip(request),
                target_resource=request.url.path,
                details_json={"required_permissions": list(permission_codes)},
            )
            raise PermissionDeniedError(
                f"Missing required permission (one of {', '.join(permission_codes)}).",
                error_code="INSUFFICIENT_PERMISSION",
            )
        return tenant_ctx

    return _dependency


@router.post("/batches", response_model=BatchSummaryResponse, status_code=status.HTTP_201_CREATED)
def create_batch(
    request: Request,
    payload: BatchCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("batch.create")),
    db: Session = Depends(get_db),
) -> BatchSummaryResponse:
    batch = _batch_svc.create_batch(
        db, tenant_ctx=tenant_ctx, payload=payload, source_ip=_client_ip(request)
    )
    return BatchSummaryResponse.model_validate(batch)


@router.get("/batches", response_model=Page[BatchSummaryResponse])
def list_batches(
    site_id: Optional[uuid.UUID] = Query(default=None),
    product_id: Optional[uuid.UUID] = Query(default=None),
    batch_status: Optional[str] = Query(default=None),
    quality_review_status: Optional[str] = Query(default=None),
    batch_number: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(require_permission("batch.read")),
    db: Session = Depends(get_db),
) -> Page[BatchSummaryResponse]:
    items, total = _batch_svc.list_batches(
        db,
        tenant_ctx=tenant_ctx,
        site_id=site_id,
        product_id=product_id,
        batch_status=batch_status,
        quality_review_status=quality_review_status,
        batch_number=batch_number,
        q=q,
        page=page,
        page_size=page_size,
    )
    return Page(
        items=[BatchSummaryResponse.model_validate(b) for b in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/batches/{batch_id}", response_model=BatchDetailResponse)
def get_batch_detail(
    request: Request,
    batch_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("batch.read")),
    db: Session = Depends(get_db),
) -> BatchDetailResponse:
    return _batch_svc.get_batch_detail(
        db, tenant_ctx=tenant_ctx, batch_id=batch_id, source_ip=_client_ip(request)
    )


@router.patch("/batches/{batch_id}", response_model=BatchSummaryResponse)
def update_batch(
    request: Request,
    batch_id: uuid.UUID,
    payload: BatchUpdateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("batch.create")),
    db: Session = Depends(get_db),
) -> BatchSummaryResponse:
    batch = _batch_svc.update_batch(
        db,
        tenant_ctx=tenant_ctx,
        batch_id=batch_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return BatchSummaryResponse.model_validate(batch)


@router.post("/batches/{batch_id}/status", response_model=BatchSummaryResponse)
def transition_batch_operational_status(
    request: Request,
    batch_id: uuid.UUID,
    payload: BatchOperationalStatusRequest,
    tenant_ctx: TenantContext = Depends(require_permission("batch.create")),
    db: Session = Depends(get_db),
) -> BatchSummaryResponse:
    batch = _batch_svc.transition_operational_status(
        db,
        tenant_ctx=tenant_ctx,
        batch_id=batch_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return BatchSummaryResponse.model_validate(batch)


@router.post(
    "/batches/{batch_id}/disposition",
    response_model=BatchDispositionResponse,
    status_code=status.HTTP_201_CREATED,
)
def decide_batch_disposition(
    request: Request,
    batch_id: uuid.UUID,
    payload: BatchDispositionRequest,
    tenant_ctx: TenantContext = Depends(require_permission("batch.release")),
    db: Session = Depends(get_db),
) -> BatchDispositionResponse:
    disposition = _batch_svc.record_disposition(
        db,
        tenant_ctx=tenant_ctx,
        batch_id=batch_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return BatchDispositionResponse.model_validate(disposition)


@router.post(
    "/batches/{batch_id}/materials",
    response_model=BatchMaterialConsumptionResponse,
    status_code=status.HTTP_201_CREATED,
)
def record_material_consumption(
    request: Request,
    batch_id: uuid.UUID,
    payload: BatchMaterialConsumptionCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("batch.create")),
    db: Session = Depends(get_db),
) -> BatchMaterialConsumptionResponse:
    consumption = _batch_svc.consume_material(
        db,
        tenant_ctx=tenant_ctx,
        batch_id=batch_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return BatchMaterialConsumptionResponse.model_validate(consumption)


@router.post(
    "/batches/{batch_id}/materials/{consumption_id}/verify",
    response_model=BatchMaterialConsumptionResponse,
)
def verify_material_consumption(
    request: Request,
    batch_id: uuid.UUID,
    consumption_id: uuid.UUID,
    payload: BatchMaterialConsumptionVerifyRequest,
    tenant_ctx: TenantContext = Depends(_require_any_permission("batch.create", "batch.release")),
    db: Session = Depends(get_db),
) -> BatchMaterialConsumptionResponse:
    consumption = _batch_svc.verify_material_consumption(
        db,
        tenant_ctx=tenant_ctx,
        batch_id=batch_id,
        consumption_id=consumption_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return BatchMaterialConsumptionResponse.model_validate(consumption)


@router.get("/batches/{batch_id}/materials", response_model=List[BatchMaterialConsumptionResponse])
def list_batch_material_consumptions(
    request: Request,
    batch_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("batch.read")),
    db: Session = Depends(get_db),
) -> List[BatchMaterialConsumptionResponse]:
    items = _batch_svc.list_material_consumptions(
        db, tenant_ctx=tenant_ctx, batch_id=batch_id, source_ip=_client_ip(request)
    )
    return [BatchMaterialConsumptionResponse.model_validate(c) for c in items]


@router.post(
    "/batches/{batch_id}/relationships",
    response_model=BatchRelationshipResponse,
    status_code=status.HTTP_201_CREATED,
)
def link_batch_relationship(
    request: Request,
    batch_id: uuid.UUID,
    payload: BatchRelationshipCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("batch.create")),
    db: Session = Depends(get_db),
) -> BatchRelationshipResponse:
    rel = _batch_svc.link_batch_relationship(
        db,
        tenant_ctx=tenant_ctx,
        batch_id=batch_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return BatchRelationshipResponse.model_validate(rel)


@router.post(
    "/batches/{batch_id}/documents",
    response_model=BatchDocumentLinkResponse,
    status_code=status.HTTP_201_CREATED,
)
def link_batch_document(
    request: Request,
    batch_id: uuid.UUID,
    payload: BatchDocumentLinkCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("batch.create")),
    db: Session = Depends(get_db),
) -> BatchDocumentLinkResponse:
    link = _batch_svc.link_batch_document(
        db,
        tenant_ctx=tenant_ctx,
        batch_id=batch_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return BatchDocumentLinkResponse.model_validate(link)


@router.get("/batches/{batch_id}/genealogy", response_model=BatchTraceabilityResponse)
def get_batch_genealogy(
    request: Request,
    batch_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("batch.genealogy.read")),
    db: Session = Depends(get_db),
) -> BatchTraceabilityResponse:
    return _traceability_svc.get_batch_traceability(
        db, tenant_ctx=tenant_ctx, batch_id=batch_id, source_ip=_client_ip(request)
    )
