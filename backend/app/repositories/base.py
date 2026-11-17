from typing import Any, List, Optional, Tuple, Type

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import PermissionDeniedError


def apply_tenant_filter(
    stmt: Select[Any],
    model: Type[Any],
    tenant_ctx: Any,
    *,
    allow_org_wide_site_null: bool = True,
) -> Select[Any]:
    """
    Enforces verified organization_id (and site_id where applicable) on SQLAlchemy 2.0 queries.
    """
    org_id = getattr(tenant_ctx, "organization_id", None)
    if org_id is None:
        raise PermissionDeniedError(
            "Tenant context with verified organization_id is required.",
            error_code="TENANT_CONTEXT_REQUIRED",
        )

    if hasattr(model, "organization_id"):
        stmt = stmt.where(model.organization_id == org_id)

    if hasattr(model, "site_id") and not getattr(tenant_ctx, "is_org_wide", False):
        allowed_sites = list(getattr(tenant_ctx, "allowed_site_ids", ()) or ())
        if not allowed_sites:
            if allow_org_wide_site_null:
                stmt = stmt.where(model.site_id.is_(None))
            else:
                stmt = stmt.where(False)
        elif allow_org_wide_site_null:
            stmt = stmt.where((model.site_id.is_(None)) | (model.site_id.in_(allowed_sites)))
        else:
            stmt = stmt.where(model.site_id.in_(allowed_sites))

    return stmt


def paginate(
    db: Session,
    stmt: Select[Any],
    *,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[Any], int]:
    """Executes paginated query returning (items, total_count)."""
    safe_page = max(1, page)
    safe_size = max(1, min(100, page_size))

    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int(db.scalar(count_stmt) or 0)

    items = list(
        db.scalars(stmt.offset((safe_page - 1) * safe_size).limit(safe_size)).all()
    )
    return items, total
