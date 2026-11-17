import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import UnitOfWork, get_session_factory
from app.core.deps import TenantContext, get_db, require_permission
from app.core.exceptions import PermissionDeniedError
from app.integrations.sse_transport import sse_transport
from app.models.schemas.common import Page
from app.models.schemas.governance import (
    AuditEventResponse,
    HealthResponse,
    NotificationResponse,
    UnreadCountResponse,
)
from app.repositories.governance_repo import AuditRepository
from app.services.audit_service import AuditService
from app.services.notification_service import NotificationService

health_router = APIRouter(tags=["Platform Health"])
router = APIRouter(tags=["Notifications & Governance"])


@health_router.get("/health/live", response_model=HealthResponse)
def health_live() -> HealthResponse:
    return HealthResponse(status="ok")


@health_router.get("/health/ready", response_model=HealthResponse)
def health_ready(db: Session = Depends(get_db)) -> JSONResponse:
    try:
        db.execute(text("SELECT 1"))
        return JSONResponse(status_code=200, content={"status": "ready"})
    except Exception:
        return JSONResponse(status_code=503, content={"status": "degraded"})


@router.get("/notifications", response_model=Page[NotificationResponse])
def list_notifications(
    unread_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(require_permission("notification.read")),
    db: Session = Depends(get_db),
) -> Page[NotificationResponse]:
    if tenant_ctx.membership_id is None:
        raise PermissionDeniedError("Notification inbox requires an active membership context.")
    items, total = NotificationService().list_notifications(
        db,
        organization_id=tenant_ctx.organization_id,
        recipient_membership_id=tenant_ctx.membership_id,
        unread_only=unread_only,
        page=page,
        page_size=page_size,
    )
    return Page(
        items=[NotificationResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/notifications/unread-count", response_model=UnreadCountResponse)
def get_unread_notification_count(
    tenant_ctx: TenantContext = Depends(require_permission("notification.read")),
    db: Session = Depends(get_db),
) -> UnreadCountResponse:
    if tenant_ctx.membership_id is None:
        raise PermissionDeniedError("Notification inbox requires an active membership context.")
    count = NotificationService().unread_count(
        db,
        organization_id=tenant_ctx.organization_id,
        recipient_membership_id=tenant_ctx.membership_id,
    )
    return UnreadCountResponse(unread_count=count)


@router.patch("/notifications/{notification_id}/read", response_model=NotificationResponse)
def mark_notification_read(
    notification_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("notification.read")),
    db: Session = Depends(get_db),
) -> NotificationResponse:
    if tenant_ctx.membership_id is None:
        raise PermissionDeniedError("Notification inbox requires an active membership context.")
    with UnitOfWork(db):
        notif = NotificationService().mark_as_read(
            db,
            organization_id=tenant_ctx.organization_id,
            recipient_membership_id=tenant_ctx.membership_id,
            notification_id=notification_id,
        )
    return NotificationResponse.model_validate(notif)


@router.get("/notifications/stream")
async def stream_notifications(
    last_event_id_header: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    after_seq: int = Query(default=0, ge=0),
    max_events: Optional[int] = Query(default=None, ge=1, le=500),
    tenant_ctx: TenantContext = Depends(require_permission("notification.read")),
) -> StreamingResponse:
    if tenant_ctx.membership_id is None:
        raise PermissionDeniedError("Notification stream requires an active membership context.")

    resolved_cursor = after_seq
    if last_event_id_header and last_event_id_header.strip().isdigit():
        resolved_cursor = max(resolved_cursor, int(last_event_id_header.strip()))

    generator = sse_transport.stream_events(
        organization_id=tenant_ctx.organization_id,
        recipient_membership_id=tenant_ctx.membership_id,
        last_event_id=resolved_cursor,
        session_factory=get_session_factory(),
        max_events=max_events,
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/audit-events", response_model=Page[AuditEventResponse])
def list_audit_events(
    request: Request,
    entity_type: Optional[str] = Query(default=None),
    entity_id: Optional[uuid.UUID] = Query(default=None),
    correlation_id: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(require_permission("audit.read")),
    db: Session = Depends(get_db),
) -> Page[AuditEventResponse]:
    with UnitOfWork(db):
        items, total = AuditRepository().list_by_organization(
            db,
            tenant_ctx.organization_id,
            entity_type=entity_type,
            entity_id=entity_id,
            correlation_id=correlation_id,
            page=page,
            page_size=page_size,
        )
        AuditService().record(
            db,
            organization_id=tenant_ctx.organization_id,
            actor_type=tenant_ctx.actor_type,
            actor_id=tenant_ctx.user_id,
            action="AUDIT_TRAIL_VIEWED",
            entity_type="audit_events",
            entity_id=tenant_ctx.organization_id,
            source_ip=request.client.host if request.client else None,
            metadata_json={
                "entity_type_filter": entity_type,
                "entity_id_filter": str(entity_id) if entity_id else None,
                "page": page,
            },
        )
    return Page(
        items=[AuditEventResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )
