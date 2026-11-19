import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.deps import TenantContext, get_db, get_tenant_context, require_permission
from app.core.exceptions import PermissionDeniedError
from app.domain.enums import DeviationClassification, SecurityEventOutcome, SecurityEventSeverity
from app.models.schemas.common import Page
from app.models.schemas.qms_loop import (
    CAPAActionCompleteRequest,
    CAPAActionCreateRequest,
    CAPAActionResponse,
    CAPAActionUpdateRequest,
    CAPACloseRequest,
    CAPACreateRequest,
    CAPADetailResponse,
    CAPAEffectivenessCheckCreateRequest,
    CAPAEffectivenessCheckRecordRequest,
    CAPAEffectivenessCheckResponse,
    CAPAOpenRequest,
    CAPAOverdueCheckResponse,
    CAPAResponse,
    CAPASubmitEffectivenessRequest,
    CAPAUpdateRequest,
    DeviationCloseRequest,
    DeviationCreateRequest,
    DeviationResponse,
    DeviationSubmitReviewRequest,
    DeviationUpdateRequest,
)
from app.services.audit_service import AuditService
from app.services.qms_action_service import QMSActionService

router = APIRouter(tags=["qms-deviations"])

_svc = QMSActionService()
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


@router.post(
    "/deviations",
    response_model=DeviationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_deviation(
    request: Request,
    payload: DeviationCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("deviation.create")),
    db: Session = Depends(get_db),
) -> DeviationResponse:
    dev = _svc.create_deviation(
        db, tenant_ctx=tenant_ctx, payload=payload, source_ip=_client_ip(request)
    )
    return DeviationResponse.model_validate(dev)


@router.post(
    "/deviations/from-compliance-result/{compliance_result_id}",
    response_model=DeviationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_deviation_from_compliance_result(
    request: Request,
    compliance_result_id: uuid.UUID,
    classification: Optional[DeviationClassification] = Query(default=None),
    tenant_ctx: TenantContext = Depends(require_permission("deviation.create")),
    db: Session = Depends(get_db),
) -> DeviationResponse:
    dev = _svc.create_from_compliance_result(
        db,
        tenant_ctx=tenant_ctx,
        compliance_result_id=compliance_result_id,
        classification=classification,
        source_ip=_client_ip(request),
    )
    return DeviationResponse.model_validate(dev)


@router.get("/deviations", response_model=Page[DeviationResponse])
def list_deviations(
    deviation_status: Optional[str] = Query(default=None, alias="status"),
    classification: Optional[str] = Query(default=None),
    source_type: Optional[str] = Query(default=None),
    complaint_id: Optional[uuid.UUID] = Query(default=None),
    investigation_id: Optional[uuid.UUID] = Query(default=None),
    batch_id: Optional[uuid.UUID] = Query(default=None),
    site_id: Optional[uuid.UUID] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(require_permission("deviation.read")),
    db: Session = Depends(get_db),
) -> Page[DeviationResponse]:
    items, total = _svc.list_deviations(
        db,
        tenant_ctx=tenant_ctx,
        status=deviation_status,
        classification=classification,
        source_type=source_type,
        complaint_id=complaint_id,
        investigation_id=investigation_id,
        batch_id=batch_id,
        site_id=site_id,
        page=page,
        page_size=page_size,
    )
    return Page(
        items=[DeviationResponse.model_validate(d) for d in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/deviations/{deviation_id}", response_model=DeviationResponse)
def get_deviation(
    request: Request,
    deviation_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("deviation.read")),
    db: Session = Depends(get_db),
) -> DeviationResponse:
    dev = _svc.get_deviation(
        db,
        tenant_ctx=tenant_ctx,
        deviation_id=deviation_id,
        source_ip=_client_ip(request),
    )
    return DeviationResponse.model_validate(dev)


@router.patch("/deviations/{deviation_id}", response_model=DeviationResponse)
def update_deviation(
    request: Request,
    deviation_id: uuid.UUID,
    payload: DeviationUpdateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("deviation.manage")),
    db: Session = Depends(get_db),
) -> DeviationResponse:
    dev = _svc.update_deviation(
        db,
        tenant_ctx=tenant_ctx,
        deviation_id=deviation_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return DeviationResponse.model_validate(dev)


@router.post("/deviations/{deviation_id}/submit-review", response_model=DeviationResponse)
def submit_deviation_for_review(
    request: Request,
    deviation_id: uuid.UUID,
    payload: DeviationSubmitReviewRequest,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission("deviation.manage", "deviation.create")
    ),
    db: Session = Depends(get_db),
) -> DeviationResponse:
    dev = _svc.submit_for_review(
        db,
        tenant_ctx=tenant_ctx,
        deviation_id=deviation_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return DeviationResponse.model_validate(dev)


@router.post("/deviations/{deviation_id}/close", response_model=DeviationResponse)
def close_deviation(
    request: Request,
    deviation_id: uuid.UUID,
    payload: DeviationCloseRequest,
    tenant_ctx: TenantContext = Depends(require_permission("deviation.close")),
    db: Session = Depends(get_db),
) -> DeviationResponse:
    dev = _svc.close_deviation(
        db,
        tenant_ctx=tenant_ctx,
        deviation_id=deviation_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return DeviationResponse.model_validate(dev)


# ---------------------------------------------------------------------------
# Phase 6 (E3): CAPA, CAPA Actions & Effectiveness Checks Routes
# ---------------------------------------------------------------------------


@router.post(
    "/capas",
    response_model=CAPAResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_capa(
    request: Request,
    payload: CAPACreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("capa.manage")),
    db: Session = Depends(get_db),
) -> CAPAResponse:
    capa = _svc.create_capa(
        db, tenant_ctx=tenant_ctx, payload=payload, source_ip=_client_ip(request)
    )
    return CAPAResponse.model_validate(capa)


@router.get("/capas", response_model=Page[CAPAResponse])
def list_capas(
    capa_status: Optional[str] = Query(default=None, alias="status"),
    capa_type: Optional[str] = Query(default=None),
    source_type: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    complaint_id: Optional[uuid.UUID] = Query(default=None),
    investigation_id: Optional[uuid.UUID] = Query(default=None),
    deviation_id: Optional[uuid.UUID] = Query(default=None),
    site_id: Optional[uuid.UUID] = Query(default=None),
    owner_membership_id: Optional[uuid.UUID] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(require_permission("capa.read")),
    db: Session = Depends(get_db),
) -> Page[CAPAResponse]:
    items, total = _svc.list_capas(
        db,
        tenant_ctx=tenant_ctx,
        status=capa_status,
        capa_type=capa_type,
        source_type=source_type,
        severity=severity,
        complaint_id=complaint_id,
        investigation_id=investigation_id,
        deviation_id=deviation_id,
        site_id=site_id,
        owner_membership_id=owner_membership_id,
        page=page,
        page_size=page_size,
    )
    return Page(
        items=[CAPAResponse.model_validate(c) for c in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/capas/check-overdue",
    response_model=CAPAOverdueCheckResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def check_overdue_capas(
    request: Request,
    tenant_ctx: TenantContext = Depends(require_permission("capa.manage")),
    db: Session = Depends(get_db),
) -> CAPAOverdueCheckResponse:
    capa_ids, event_ids = _svc.check_overdue_capas(
        db, tenant_ctx=tenant_ctx, source_ip=_client_ip(request)
    )
    return CAPAOverdueCheckResponse(
        enqueued_count=len(event_ids),
        overdue_capa_ids=capa_ids,
        outbox_event_ids=event_ids,
    )


@router.get("/capas/{capa_id}", response_model=CAPADetailResponse)
def get_capa(
    request: Request,
    capa_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("capa.read")),
    db: Session = Depends(get_db),
) -> CAPADetailResponse:
    capa, actions, checks = _svc.get_capa_detail(
        db,
        tenant_ctx=tenant_ctx,
        capa_id=capa_id,
        source_ip=_client_ip(request),
    )
    base = CAPAResponse.model_validate(capa).model_dump()
    return CAPADetailResponse(
        **base,
        actions=[CAPAActionResponse.model_validate(a) for a in actions],
        effectiveness_checks=[CAPAEffectivenessCheckResponse.model_validate(c) for c in checks],
    )


@router.patch("/capas/{capa_id}", response_model=CAPAResponse)
def update_capa(
    request: Request,
    capa_id: uuid.UUID,
    payload: CAPAUpdateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("capa.manage")),
    db: Session = Depends(get_db),
) -> CAPAResponse:
    capa = _svc.update_capa(
        db,
        tenant_ctx=tenant_ctx,
        capa_id=capa_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return CAPAResponse.model_validate(capa)


@router.post("/capas/{capa_id}/open", response_model=CAPAResponse)
def open_capa(
    request: Request,
    capa_id: uuid.UUID,
    payload: CAPAOpenRequest,
    tenant_ctx: TenantContext = Depends(require_permission("capa.approve")),
    db: Session = Depends(get_db),
) -> CAPAResponse:
    capa = _svc.open_capa(
        db,
        tenant_ctx=tenant_ctx,
        capa_id=capa_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return CAPAResponse.model_validate(capa)


@router.post(
    "/capas/{capa_id}/actions",
    response_model=CAPAActionResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_capa_action(
    request: Request,
    capa_id: uuid.UUID,
    payload: CAPAActionCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("capa.manage")),
    db: Session = Depends(get_db),
) -> CAPAActionResponse:
    action = _svc.add_capa_action(
        db,
        tenant_ctx=tenant_ctx,
        capa_id=capa_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return CAPAActionResponse.model_validate(action)


@router.patch("/capas/{capa_id}/actions/{action_id}", response_model=CAPAActionResponse)
def update_capa_action(
    request: Request,
    capa_id: uuid.UUID,
    action_id: uuid.UUID,
    payload: CAPAActionUpdateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("capa.manage")),
    db: Session = Depends(get_db),
) -> CAPAActionResponse:
    action = _svc.update_capa_action(
        db,
        tenant_ctx=tenant_ctx,
        capa_id=capa_id,
        action_id=action_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return CAPAActionResponse.model_validate(action)


@router.post(
    "/capas/{capa_id}/actions/{action_id}/complete",
    response_model=CAPAActionResponse,
)
def complete_capa_action(
    request: Request,
    capa_id: uuid.UUID,
    action_id: uuid.UUID,
    payload: CAPAActionCompleteRequest,
    tenant_ctx: TenantContext = Depends(require_permission("capa.manage")),
    db: Session = Depends(get_db),
) -> CAPAActionResponse:
    action = _svc.complete_capa_action(
        db,
        tenant_ctx=tenant_ctx,
        capa_id=capa_id,
        action_id=action_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return CAPAActionResponse.model_validate(action)


@router.post("/capas/{capa_id}/submit-effectiveness", response_model=CAPAResponse)
def submit_capa_for_effectiveness(
    request: Request,
    capa_id: uuid.UUID,
    payload: CAPASubmitEffectivenessRequest,
    tenant_ctx: TenantContext = Depends(require_permission("capa.manage")),
    db: Session = Depends(get_db),
) -> CAPAResponse:
    capa = _svc.submit_for_effectiveness(
        db,
        tenant_ctx=tenant_ctx,
        capa_id=capa_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return CAPAResponse.model_validate(capa)


@router.post(
    "/capas/{capa_id}/effectiveness-checks",
    response_model=CAPAEffectivenessCheckResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_capa_effectiveness_check(
    request: Request,
    capa_id: uuid.UUID,
    payload: CAPAEffectivenessCheckCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("capa.manage")),
    db: Session = Depends(get_db),
) -> CAPAEffectivenessCheckResponse:
    check = _svc.create_effectiveness_check(
        db,
        tenant_ctx=tenant_ctx,
        capa_id=capa_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return CAPAEffectivenessCheckResponse.model_validate(check)


@router.post(
    "/capas/{capa_id}/effectiveness-checks/{check_id}/record",
    response_model=CAPAEffectivenessCheckResponse,
)
def record_capa_effectiveness_check(
    request: Request,
    capa_id: uuid.UUID,
    check_id: uuid.UUID,
    payload: CAPAEffectivenessCheckRecordRequest,
    tenant_ctx: TenantContext = Depends(require_permission("capa.effectiveness_review")),
    db: Session = Depends(get_db),
) -> CAPAEffectivenessCheckResponse:
    check = _svc.record_effectiveness_check(
        db,
        tenant_ctx=tenant_ctx,
        capa_id=capa_id,
        check_id=check_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return CAPAEffectivenessCheckResponse.model_validate(check)


@router.post("/capas/{capa_id}/close", response_model=CAPAResponse)
def close_capa(
    request: Request,
    capa_id: uuid.UUID,
    payload: CAPACloseRequest,
    tenant_ctx: TenantContext = Depends(require_permission("capa.close")),
    db: Session = Depends(get_db),
) -> CAPAResponse:
    capa = _svc.close_capa(
        db,
        tenant_ctx=tenant_ctx,
        capa_id=capa_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return CAPAResponse.model_validate(capa)
