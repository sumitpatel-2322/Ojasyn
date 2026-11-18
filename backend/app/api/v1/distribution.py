import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.deps import TenantContext, get_db, require_permission
from app.models.schemas.common import Page
from app.models.schemas.traceability import (
    BatchMovementEventResponse,
    BatchTraceabilityResponse,
    ShipmentCancelRequest,
    ShipmentCreateRequest,
    ShipmentDetailResponse,
    ShipmentDispatchRequest,
    ShipmentLineCreateRequest,
    ShipmentLineResponse,
    ShipmentReceiptRequest,
    ShipmentResponse,
)
from app.services.distribution_traceability_service import DistributionTraceabilityService

router = APIRouter(tags=["distribution-traceability"])

_svc = DistributionTraceabilityService()


def _client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


@router.post(
    "/distribution/shipments",
    response_model=ShipmentDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_shipment(
    request: Request,
    payload: ShipmentCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("shipment.manage")),
    db: Session = Depends(get_db),
) -> ShipmentDetailResponse:
    shipment = _svc.create_shipment(
        db, tenant_ctx=tenant_ctx, payload=payload, source_ip=_client_ip(request)
    )
    return ShipmentDetailResponse.model_validate(shipment)


@router.get("/distribution/shipments", response_model=Page[ShipmentResponse])
def list_shipments(
    site_id: Optional[uuid.UUID] = Query(default=None),
    partner_id: Optional[uuid.UUID] = Query(default=None),
    shipment_status: Optional[str] = Query(default=None),
    shipment_number: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(require_permission("shipment.read")),
    db: Session = Depends(get_db),
) -> Page[ShipmentResponse]:
    items, total = _svc.list_shipments(
        db,
        tenant_ctx=tenant_ctx,
        site_id=site_id,
        partner_id=partner_id,
        shipment_status=shipment_status,
        shipment_number=shipment_number,
        page=page,
        page_size=page_size,
    )
    return Page(
        items=[ShipmentResponse.model_validate(s) for s in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/distribution/shipments/{shipment_id}", response_model=ShipmentDetailResponse)
def get_shipment(
    request: Request,
    shipment_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("shipment.read")),
    db: Session = Depends(get_db),
) -> ShipmentDetailResponse:
    shipment = _svc.get_shipment(
        db, tenant_ctx=tenant_ctx, shipment_id=shipment_id, source_ip=_client_ip(request)
    )
    return ShipmentDetailResponse.model_validate(shipment)


@router.post(
    "/distribution/shipments/{shipment_id}/lines",
    response_model=ShipmentLineResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_shipment_line(
    request: Request,
    shipment_id: uuid.UUID,
    payload: ShipmentLineCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("shipment.manage")),
    db: Session = Depends(get_db),
) -> ShipmentLineResponse:
    line = _svc.add_shipment_line(
        db,
        tenant_ctx=tenant_ctx,
        shipment_id=shipment_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return ShipmentLineResponse.model_validate(line)


@router.post("/distribution/shipments/{shipment_id}/dispatch", response_model=ShipmentDetailResponse)
def dispatch_shipment(
    request: Request,
    shipment_id: uuid.UUID,
    payload: ShipmentDispatchRequest,
    tenant_ctx: TenantContext = Depends(require_permission("shipment.manage")),
    db: Session = Depends(get_db),
) -> ShipmentDetailResponse:
    shipment = _svc.dispatch_shipment(
        db,
        tenant_ctx=tenant_ctx,
        shipment_id=shipment_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return ShipmentDetailResponse.model_validate(shipment)


@router.post("/distribution/shipments/{shipment_id}/receipt", response_model=ShipmentDetailResponse)
def confirm_shipment_receipt(
    request: Request,
    shipment_id: uuid.UUID,
    payload: ShipmentReceiptRequest,
    tenant_ctx: TenantContext = Depends(require_permission("shipment.receipt")),
    db: Session = Depends(get_db),
) -> ShipmentDetailResponse:
    shipment = _svc.confirm_receipt(
        db,
        tenant_ctx=tenant_ctx,
        shipment_id=shipment_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return ShipmentDetailResponse.model_validate(shipment)


@router.post("/distribution/shipments/{shipment_id}/cancel", response_model=ShipmentDetailResponse)
def cancel_shipment(
    request: Request,
    shipment_id: uuid.UUID,
    payload: ShipmentCancelRequest,
    tenant_ctx: TenantContext = Depends(require_permission("shipment.manage")),
    db: Session = Depends(get_db),
) -> ShipmentDetailResponse:
    shipment = _svc.cancel_shipment(
        db,
        tenant_ctx=tenant_ctx,
        shipment_id=shipment_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return ShipmentDetailResponse.model_validate(shipment)


@router.get(
    "/distribution/batches/{batch_id}/movements",
    response_model=List[BatchMovementEventResponse],
)
def get_batch_movements(
    request: Request,
    batch_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("traceability.read")),
    db: Session = Depends(get_db),
) -> List[BatchMovementEventResponse]:
    events = _svc.get_batch_movements(
        db, tenant_ctx=tenant_ctx, batch_id=batch_id, source_ip=_client_ip(request)
    )
    return [BatchMovementEventResponse.model_validate(e) for e in events]


@router.get("/batches/{batch_id}/traceability", response_model=BatchTraceabilityResponse)
def get_batch_traceability(
    request: Request,
    batch_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("traceability.read")),
    db: Session = Depends(get_db),
) -> BatchTraceabilityResponse:
    return _svc.get_batch_traceability(
        db, tenant_ctx=tenant_ctx, batch_id=batch_id, source_ip=_client_ip(request)
    )
