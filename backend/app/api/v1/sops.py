import uuid
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.deps import TenantContext, get_db, get_tenant_context, require_permission
from app.core.exceptions import PermissionDeniedError
from app.domain.enums import SecurityEventOutcome, SecurityEventSeverity
from app.models.schemas.common import Page
from app.models.schemas.sops import (
    SOPCreateRequest,
    SOPResolutionResponse,
    SOPResponse,
    SOPRevisionActivateRequest,
    SOPRevisionApproveRequest,
    SOPRevisionCreateRequest,
    SOPRevisionResponse,
    SOPRevisionSubmitReviewRequest,
    SOPStepCreateRequest,
    SOPStepResponse,
    SOPStepUpdateRequest,
    SOPUpdateRequest,
)
from app.services.audit_service import AuditService
from app.services.sop_service import SOPService

router = APIRouter(tags=["sops"])
_service = SOPService()
_audit_svc = AuditService()


def _require_any_permission(
    permissions: Tuple[str, ...],
) -> Callable[..., TenantContext]:
    def _dep(
        tenant_ctx: TenantContext = Depends(get_tenant_context),
        db: Session = Depends(get_db),
    ) -> TenantContext:
        if not any(p in tenant_ctx.permission_codes for p in permissions):
            db.rollback()
            _audit_svc.record_security_event(
                event_type="RBAC_PERMISSION_DENIED",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.WARNING.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                target_resource="any_of:" + ",".join(permissions),
                details_json={"required_any": list(permissions)},
            )
            raise PermissionDeniedError(
                f"Missing required permission (one of: {', '.join(permissions)})."
            )
        return tenant_ctx

    return _dep


def _to_revision_response(revision, steps) -> SOPRevisionResponse:
    resp = SOPRevisionResponse.model_validate(revision)
    resp.steps = [SOPStepResponse.model_validate(s) for s in steps]
    return resp


# --- SOP Master Endpoints (C5) ---


@router.post(
    "/sops",
    response_model=SOPResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_sop(
    payload: SOPCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("sop.manage")),
    db: Session = Depends(get_db),
) -> SOPResponse:
    sop = _service.create_sop(db, tenant_ctx, payload)
    return SOPResponse.model_validate(sop)


@router.get(
    "/sops",
    response_model=Page[SOPResponse],
)
def list_sops(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    site_id: Optional[uuid.UUID] = Query(default=None),
    category: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(require_permission("sop.read")),
    db: Session = Depends(get_db),
) -> Page[SOPResponse]:
    items, total = _service.list_sops(
        db,
        tenant_ctx,
        status=status_filter,
        site_id=site_id,
        category=category,
        search=search,
        page=page,
        page_size=page_size,
    )
    return Page(
        items=[SOPResponse.model_validate(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/sops/resolve",
    response_model=SOPResolutionResponse,
)
def resolve_historical_sop(
    sop_code: str = Query(..., min_length=1),
    at: datetime = Query(...),
    site_id: Optional[uuid.UUID] = Query(default=None),
    tenant_ctx: TenantContext = Depends(require_permission("sop.read")),
    db: Session = Depends(get_db),
) -> SOPResolutionResponse:
    result = _service.resolve_sop(
        db,
        tenant_ctx,
        sop_code=sop_code,
        at=at,
        site_id=site_id,
    )
    return result.to_response()


@router.get(
    "/sops/{sop_id}",
    response_model=SOPResponse,
)
def get_sop(
    sop_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("sop.read")),
    db: Session = Depends(get_db),
) -> SOPResponse:
    sop = _service.get_sop(db, tenant_ctx, sop_id)
    return SOPResponse.model_validate(sop)


@router.patch(
    "/sops/{sop_id}",
    response_model=SOPResponse,
)
def update_sop(
    sop_id: uuid.UUID,
    payload: SOPUpdateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("sop.manage")),
    db: Session = Depends(get_db),
) -> SOPResponse:
    sop = _service.update_sop(db, tenant_ctx, sop_id, payload)
    return SOPResponse.model_validate(sop)


# --- SOP Revision & Step Endpoints (C5) ---


@router.post(
    "/sops/{sop_id}/revisions",
    response_model=SOPRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_sop_revision(
    sop_id: uuid.UUID,
    payload: SOPRevisionCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("sop.manage")),
    db: Session = Depends(get_db),
) -> SOPRevisionResponse:
    rev, steps = _service.create_revision(db, tenant_ctx, sop_id, payload)
    return _to_revision_response(rev, steps)


@router.get(
    "/sops/{sop_id}/revisions",
    response_model=List[SOPRevisionResponse],
)
def list_sop_revisions(
    sop_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("sop.read")),
    db: Session = Depends(get_db),
) -> List[SOPRevisionResponse]:
    rev_pairs = _service.list_revisions(db, tenant_ctx, sop_id)
    return [_to_revision_response(rev, steps) for rev, steps in rev_pairs]


@router.get(
    "/sops/{sop_id}/revisions/{revision_id}",
    response_model=SOPRevisionResponse,
)
def get_sop_revision(
    sop_id: uuid.UUID,
    revision_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("sop.read")),
    db: Session = Depends(get_db),
) -> SOPRevisionResponse:
    rev, steps = _service.get_revision(db, tenant_ctx, sop_id, revision_id)
    return _to_revision_response(rev, steps)


@router.post(
    "/sops/{sop_id}/revisions/{revision_id}/steps",
    response_model=SOPStepResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_sop_step(
    sop_id: uuid.UUID,
    revision_id: uuid.UUID,
    payload: SOPStepCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("sop.manage")),
    db: Session = Depends(get_db),
) -> SOPStepResponse:
    step = _service.add_step(db, tenant_ctx, sop_id, revision_id, payload)
    return SOPStepResponse.model_validate(step)


@router.get(
    "/sops/{sop_id}/revisions/{revision_id}/steps",
    response_model=List[SOPStepResponse],
)
def list_sop_steps(
    sop_id: uuid.UUID,
    revision_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("sop.read")),
    db: Session = Depends(get_db),
) -> List[SOPStepResponse]:
    steps = _service.list_steps(db, tenant_ctx, sop_id, revision_id)
    return [SOPStepResponse.model_validate(s) for s in steps]


@router.patch(
    "/sops/{sop_id}/revisions/{revision_id}/steps/{step_id}",
    response_model=SOPStepResponse,
)
def update_sop_step(
    sop_id: uuid.UUID,
    revision_id: uuid.UUID,
    step_id: uuid.UUID,
    payload: SOPStepUpdateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("sop.manage")),
    db: Session = Depends(get_db),
) -> SOPStepResponse:
    step = _service.update_step(
        db, tenant_ctx, sop_id, revision_id, step_id, payload
    )
    return SOPStepResponse.model_validate(step)


@router.post(
    "/sops/{sop_id}/revisions/{revision_id}/submit-review",
    response_model=SOPRevisionResponse,
)
def submit_sop_revision_for_review(
    sop_id: uuid.UUID,
    revision_id: uuid.UUID,
    payload: Optional[SOPRevisionSubmitReviewRequest] = Body(default=None),
    tenant_ctx: TenantContext = Depends(require_permission("sop.manage")),
    db: Session = Depends(get_db),
) -> SOPRevisionResponse:
    rev, steps = _service.submit_revision_for_review(
        db, tenant_ctx, sop_id, revision_id, payload
    )
    return _to_revision_response(rev, steps)


@router.post(
    "/sops/{sop_id}/revisions/{revision_id}/approve",
    response_model=SOPRevisionResponse,
)
def approve_sop_revision(
    sop_id: uuid.UUID,
    revision_id: uuid.UUID,
    payload: Optional[SOPRevisionApproveRequest] = Body(default=None),
    tenant_ctx: TenantContext = Depends(require_permission("sop.approve")),
    db: Session = Depends(get_db),
) -> SOPRevisionResponse:
    rev, steps = _service.approve_revision(
        db, tenant_ctx, sop_id, revision_id, payload
    )
    return _to_revision_response(rev, steps)


@router.post(
    "/sops/{sop_id}/revisions/{revision_id}/activate",
    response_model=SOPRevisionResponse,
)
def activate_sop_revision(
    sop_id: uuid.UUID,
    revision_id: uuid.UUID,
    payload: Optional[SOPRevisionActivateRequest] = Body(default=None),
    tenant_ctx: TenantContext = Depends(
        _require_any_permission(("sop.approve", "sop.manage"))
    ),
    db: Session = Depends(get_db),
) -> SOPRevisionResponse:
    rev, steps = _service.activate_revision(
        db, tenant_ctx, sop_id, revision_id, payload
    )
    return _to_revision_response(rev, steps)
