import uuid
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, Header, Query, Request, Response, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.deps import (
    Principal,
    TenantContext,
    get_current_principal,
    get_db,
    get_tenant_context,
    rate_limit,
    require_permission,
    require_platform_permission,
)
from app.core.exceptions import AuthenticationError, PermissionDeniedError, ValidationError
from app.models.schemas.common import Page
from app.models.schemas.complaints import (
    BatchResolutionRequest,
    ComplaintAssignRequest,
    ComplaintAssignmentResponse,
    ComplaintAttachmentResponse,
    ComplaintBatchLinkResponse,
    ComplaintCloseRequest,
    ComplaintCommunicationCreateRequest,
    ComplaintCommunicationResponse,
    ComplaintDetailResponse,
    ComplaintPublicStatusResponse,
    ComplaintRegisterRequest,
    ComplaintRegisterResponse,
    ComplaintRouteRequest,
    ComplaintSummaryResponse,
    ComplaintTriageRequest,
    ComplaintUpdateRequest,
    CopilotCreateSessionRequest,
    CopilotMessageCreateRequest,
    CopilotSessionResponse,
    CopilotSubmitRequest,
    InformationRequestCreateRequest,
)
from app.services.complaint_service import ComplaintService

router = APIRouter(tags=["complaints-copilot"])

_svc = ComplaintService()
_optional_bearer = HTTPBearer(auto_error=False)


def _get_optional_principal(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer),
    db: Session = Depends(get_db),
) -> Optional[Principal]:
    if credentials is None or not credentials.credentials:
        return None
    return get_current_principal(request=request, credentials=credentials, db=db)


def _get_optional_tenant_context(
    request: Request,
    principal: Optional[Principal] = Depends(_get_optional_principal),
    x_organization_id: Optional[str] = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
) -> Optional[TenantContext]:
    if principal is None:
        return None
    if not x_organization_id:
        # Public authenticated customers/chemists (zero orgs) or multi-org users without header fall back cleanly
        try:
            return get_tenant_context(
                request=request, principal=principal, x_organization_id=None, db=db
            )
        except PermissionDeniedError:
            return None
        except ValidationError as exc:
            if exc.error_code == "TENANT_HEADER_REQUIRED":
                return None
            raise
    return get_tenant_context(
        request=request, principal=principal, x_organization_id=x_organization_id, db=db
    )


# --- Canonical Complaint Intake & Management ---


@router.post(
    "/complaints",
    response_model=ComplaintRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("complaint_register", max_requests=30, window_seconds=60))],
)
def register_complaint(
    request: Request,
    response: Response,
    payload: ComplaintRegisterRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    principal: Optional[Principal] = Depends(_get_optional_principal),
    tenant_ctx: Optional[TenantContext] = Depends(_get_optional_tenant_context),
    db: Session = Depends(get_db),
) -> ComplaintRegisterResponse:
    source_ip = request.client.host if request.client else None
    result = _svc.register(
        db,
        payload,
        principal=principal,
        tenant_ctx=tenant_ctx,
        idempotency_key=idempotency_key,
        source_ip=source_ip,
    )
    if result.idempotent_replay:
        response.status_code = status.HTTP_200_OK
    return result


@router.get("/complaints", response_model=Page[ComplaintSummaryResponse])
def list_complaints(
    complaint_status: Optional[str] = Query(default=None, alias="status"),
    severity: Optional[str] = Query(default=None),
    source_channel: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(require_permission("complaint.read")),
    db: Session = Depends(get_db),
) -> Page[ComplaintSummaryResponse]:
    items, total = _svc.list_complaints(
        db,
        tenant_ctx,
        status=complaint_status,
        severity=severity,
        source_channel=source_channel,
        page=page,
        page_size=page_size,
    )
    return Page(
        items=[ComplaintSummaryResponse.model_validate(c) for c in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/complaints/unrouted", response_model=Page[ComplaintSummaryResponse])
def list_unrouted_complaints(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _platform: Principal = Depends(require_platform_permission("complaint.read")),
    db: Session = Depends(get_db),
) -> Page[ComplaintSummaryResponse]:
    items, total = _svc.list_unrouted_complaints(db, page=page, page_size=page_size)
    return Page(
        items=[ComplaintSummaryResponse.model_validate(c) for c in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# --- LangGraph Copilot Routes (defined BEFORE /complaints/{complaint_id}) ---


@router.post(
    "/complaints/copilot/sessions",
    response_model=CopilotSessionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("copilot_create", max_requests=20, window_seconds=60))],
)
def create_copilot_session(
    request: Request,
    payload: Optional[CopilotCreateSessionRequest] = Body(default=None),
    principal: Optional[Principal] = Depends(_get_optional_principal),
    tenant_ctx: Optional[TenantContext] = Depends(_get_optional_tenant_context),
    db: Session = Depends(get_db),
) -> CopilotSessionResponse:
    source_ip = request.client.host if request.client else None
    return _svc.create_copilot_session(
        db,
        payload or CopilotCreateSessionRequest(),
        principal=principal,
        tenant_ctx=tenant_ctx,
        source_ip=source_ip,
    )


@router.get("/complaints/copilot/sessions/{session_id}", response_model=CopilotSessionResponse)
def get_copilot_session(
    request: Request,
    session_id: uuid.UUID,
    x_copilot_session_token: Optional[str] = Header(default=None, alias="X-Copilot-Session-Token"),
    principal: Optional[Principal] = Depends(_get_optional_principal),
    db: Session = Depends(get_db),
) -> CopilotSessionResponse:
    source_ip = request.client.host if request.client else None
    return _svc.get_copilot_session(
        db,
        session_id=session_id,
        principal=principal,
        session_token=x_copilot_session_token,
        source_ip=source_ip,
    )


@router.post(
    "/complaints/copilot/sessions/{session_id}/messages",
    response_model=CopilotSessionResponse,
    dependencies=[Depends(rate_limit("copilot_message", max_requests=40, window_seconds=60))],
)
def post_copilot_message(
    request: Request,
    session_id: uuid.UUID,
    payload: CopilotMessageCreateRequest,
    x_copilot_session_token: Optional[str] = Header(default=None, alias="X-Copilot-Session-Token"),
    principal: Optional[Principal] = Depends(_get_optional_principal),
    db: Session = Depends(get_db),
) -> CopilotSessionResponse:
    source_ip = request.client.host if request.client else None
    return _svc.send_copilot_message(
        db,
        session_id=session_id,
        user_message=payload.message,
        principal=principal,
        session_token=x_copilot_session_token,
        source_ip=source_ip,
    )


@router.post(
    "/complaints/copilot/sessions/{session_id}/submit",
    response_model=ComplaintRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_copilot_session(
    request: Request,
    response: Response,
    session_id: uuid.UUID,
    payload: Optional[CopilotSubmitRequest] = Body(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_copilot_session_token: Optional[str] = Header(default=None, alias="X-Copilot-Session-Token"),
    principal: Optional[Principal] = Depends(_get_optional_principal),
    tenant_ctx: Optional[TenantContext] = Depends(_get_optional_tenant_context),
    db: Session = Depends(get_db),
) -> ComplaintRegisterResponse:
    source_ip = request.client.host if request.client else None
    result = _svc.submit_copilot_session(
        db,
        session_id=session_id,
        payload=payload or CopilotSubmitRequest(),
        principal=principal,
        tenant_ctx=tenant_ctx,
        session_token=x_copilot_session_token,
        idempotency_key=idempotency_key,
        source_ip=source_ip,
    )
    if result.idempotent_replay:
        response.status_code = status.HTTP_200_OK
    return result


# --- Complaint Detail & Lifecycle Endpoints ---


@router.get("/complaints/{complaint_id}", response_model=ComplaintDetailResponse | ComplaintPublicStatusResponse)
def get_complaint(
    request: Request,
    complaint_id: uuid.UUID,
    x_tracking_token: Optional[str] = Header(default=None, alias="X-Tracking-Token"),
    x_organization_id: Optional[str] = Header(default=None, alias="X-Organization-Id"),
    principal: Optional[Principal] = Depends(_get_optional_principal),
    db: Session = Depends(get_db),
):
    source_ip = request.client.host if request.client else None
    if x_tracking_token:
        return _svc.get_complaint_public_by_tracking_token(
            db, complaint_id, x_tracking_token, source_ip=source_ip
        )
    if principal is None:
        raise AuthenticationError("Authentication credentials or X-Tracking-Token required.")

    try:
        tenant_ctx = get_tenant_context(
            request=request, principal=principal, x_organization_id=x_organization_id, db=db
        )
        if "complaint.read" not in tenant_ctx.permission_codes:
            raise PermissionDeniedError(
                "Missing required permission 'complaint.read'.",
                error_code="INSUFFICIENT_PERMISSION",
            )
        return _svc.get_complaint_detail(db, tenant_ctx, complaint_id)
    except PermissionDeniedError:
        # External authenticated creator (e.g., CUSTOMER/CHEMIST) may read sanitized status of their own complaint
        return _svc.get_complaint_public_for_creator(db, complaint_id, principal)


@router.patch("/complaints/{complaint_id}", response_model=ComplaintDetailResponse)
def update_complaint(
    complaint_id: uuid.UUID,
    payload: ComplaintUpdateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("complaint.update")),
    db: Session = Depends(get_db),
) -> ComplaintDetailResponse:
    complaint = _svc.update_complaint(
        db, complaint_id=complaint_id, tenant_ctx=tenant_ctx, payload=payload
    )
    return ComplaintDetailResponse.model_validate(complaint)


@router.post(
    "/complaints/{complaint_id}/attachments",
    response_model=ComplaintAttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_complaint_attachment(
    request: Request,
    complaint_id: uuid.UUID,
    file: UploadFile = File(...),
    caption: Optional[str] = Form(default=None),
    is_external_visible: bool = Form(default=True),
    x_tracking_token: Optional[str] = Header(default=None, alias="X-Tracking-Token"),
    x_organization_id: Optional[str] = Header(default=None, alias="X-Organization-Id"),
    principal: Optional[Principal] = Depends(_get_optional_principal),
    db: Session = Depends(get_db),
) -> ComplaintAttachmentResponse:
    tenant_ctx: Optional[TenantContext] = None
    if not x_tracking_token and principal is not None:
        try:
            ctx = get_tenant_context(
                request=request, principal=principal, x_organization_id=x_organization_id, db=db
            )
            if "complaint.update" in ctx.permission_codes or "document.upload" in ctx.permission_codes:
                tenant_ctx = ctx
        except PermissionDeniedError:
            tenant_ctx = None

    raw_bytes = await file.read()
    attachment = _svc.attach_document(
        db,
        complaint_id=complaint_id,
        file_bytes=raw_bytes,
        original_filename=file.filename or "evidence.bin",
        claimed_mime_type=file.content_type,
        caption=caption,
        is_external_visible=is_external_visible,
        tenant_ctx=tenant_ctx,
        principal=principal,
        tracking_token=x_tracking_token,
    )
    return ComplaintAttachmentResponse.model_validate(attachment)


@router.post("/complaints/{complaint_id}/assign", response_model=ComplaintAssignmentResponse)
def assign_complaint(
    complaint_id: uuid.UUID,
    payload: ComplaintAssignRequest,
    tenant_ctx: TenantContext = Depends(require_permission("complaint.assign")),
    db: Session = Depends(get_db),
) -> ComplaintAssignmentResponse:
    assignment = _svc.assign_complaint(
        db, complaint_id=complaint_id, tenant_ctx=tenant_ctx, payload=payload
    )
    return ComplaintAssignmentResponse.model_validate(assignment)


@router.post("/complaints/{complaint_id}/triage", response_model=ComplaintDetailResponse)
def triage_complaint(
    complaint_id: uuid.UUID,
    payload: ComplaintTriageRequest,
    tenant_ctx: TenantContext = Depends(require_permission("complaint.triage")),
    db: Session = Depends(get_db),
) -> ComplaintDetailResponse:
    complaint = _svc.triage_complaint(
        db, complaint_id=complaint_id, tenant_ctx=tenant_ctx, payload=payload
    )
    return ComplaintDetailResponse.model_validate(complaint)


@router.post("/complaints/{complaint_id}/route", response_model=ComplaintDetailResponse)
def route_unrouted_complaint(
    complaint_id: uuid.UUID,
    payload: ComplaintRouteRequest,
    reviewer: Principal = Depends(require_platform_permission("complaint.triage")),
    db: Session = Depends(get_db),
) -> ComplaintDetailResponse:
    complaint = _svc.route_unrouted_complaint(
        db, complaint_id=complaint_id, reviewer=reviewer, payload=payload
    )
    return ComplaintDetailResponse.model_validate(complaint)


@router.post(
    "/complaints/{complaint_id}/batch-candidates/resolve",
    response_model=ComplaintBatchLinkResponse,
)
def resolve_batch_candidates(
    complaint_id: uuid.UUID,
    payload: BatchResolutionRequest,
    tenant_ctx: TenantContext = Depends(require_permission("complaint.batch_resolve")),
    db: Session = Depends(get_db),
) -> ComplaintBatchLinkResponse:
    link = _svc.resolve_batch_candidates(
        db, complaint_id=complaint_id, tenant_ctx=tenant_ctx, payload=payload
    )
    return ComplaintBatchLinkResponse.model_validate(link)


@router.post(
    "/complaints/{complaint_id}/communications",
    response_model=ComplaintCommunicationResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_communication(
    complaint_id: uuid.UUID,
    payload: ComplaintCommunicationCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("complaint.communicate")),
    db: Session = Depends(get_db),
) -> ComplaintCommunicationResponse:
    comm = _svc.add_communication(
        db, complaint_id=complaint_id, tenant_ctx=tenant_ctx, payload=payload
    )
    return ComplaintCommunicationResponse.model_validate(comm)


@router.post(
    "/complaints/{complaint_id}/information-requests",
    response_model=ComplaintCommunicationResponse,
    status_code=status.HTTP_201_CREATED,
)
def request_information(
    complaint_id: uuid.UUID,
    payload: InformationRequestCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("complaint.communicate")),
    db: Session = Depends(get_db),
) -> ComplaintCommunicationResponse:
    comm = _svc.request_information(
        db, complaint_id=complaint_id, tenant_ctx=tenant_ctx, payload=payload
    )
    return ComplaintCommunicationResponse.model_validate(comm)


@router.post("/complaints/{complaint_id}/close", response_model=ComplaintDetailResponse)
def close_complaint(
    request: Request,
    complaint_id: uuid.UUID,
    payload: ComplaintCloseRequest,
    tenant_ctx: TenantContext = Depends(require_permission("complaint.close")),
    db: Session = Depends(get_db),
) -> ComplaintDetailResponse:
    source_ip = request.client.host if request.client else None
    complaint = _svc.close_complaint(
        db,
        complaint_id=complaint_id,
        tenant_ctx=tenant_ctx,
        payload=payload,
        source_ip=source_ip,
    )
    return ComplaintDetailResponse.model_validate(complaint)

