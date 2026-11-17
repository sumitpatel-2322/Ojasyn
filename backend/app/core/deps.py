import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Deque, Dict, FrozenSet, Generator, Iterable, Optional, Set, Tuple

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import UnitOfWork, get_session_factory
from app.core.exceptions import (
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
    ValidationError,
)
from app.core.security import decode_access_token
from app.domain.enums import (
    ActorType,
    AssignmentStatus,
    MembershipStatus,
    OrganizationStatus,
    RoleAssignmentScopeType,
    RoleAssignmentStatus,
    SecurityEventOutcome,
    SecurityEventSeverity,
    SiteStatus,
    UserAccountStatus,
)
from app.domain.rbac_catalog import ORG_WIDE_ROLES
from app.models.domain.identity import (
    MembershipRoleAssignment,
    OrganizationMembership,
    Permission,
    Role,
    RolePermission,
    SiteAssignment,
    User,
)
from app.models.domain.tenancy import Organization, Site
from app.services.audit_service import AuditService

_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    email: str
    full_name: str
    account_status: str
    token_jti: Optional[str] = None
    token_iat: Optional[datetime] = None
    amr: tuple[str, ...] = ("pwd",)
    actor_type: str = ActorType.USER.value
    platform_role: Optional[str] = None


@dataclass(frozen=True)
class TenantContext:
    user_id: Optional[uuid.UUID]
    organization_id: uuid.UUID
    membership_id: Optional[uuid.UUID]
    allowed_site_ids: FrozenSet[uuid.UUID]
    is_org_wide: bool
    permission_codes: FrozenSet[str]
    role_codes: FrozenSet[str] = field(default_factory=frozenset)
    actor_type: str = ActorType.USER.value


def get_db() -> Generator[Session, None, None]:
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


def get_uow(db: Session = Depends(get_db)) -> UnitOfWork:
    return UnitOfWork(db)


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _record_auth_failure(
    request: Optional[Request],
    *,
    event_type: str,
    user_id: Optional[uuid.UUID] = None,
    organization_id: Optional[uuid.UUID] = None,
    severity: str = SecurityEventSeverity.WARNING.value,
    details: Optional[dict] = None,
) -> None:
    try:
        AuditService().record_security_event(
            event_type=event_type,
            outcome=SecurityEventOutcome.DENIED.value,
            severity=severity,
            organization_id=organization_id,
            user_id=user_id,
            source_ip=request.client.host if request and request.client else None,
            user_agent=request.headers.get("User-Agent") if request else None,
            target_resource=str(request.url.path) if request else None,
            details_json=details,
        )
    except Exception:
        # Telemetry failure must never turn an auth denial into a 500
        pass


def get_current_principal(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        _record_auth_failure(request, event_type="AUTH_MISSING_TOKEN")
        raise AuthenticationError("Authentication credentials were not provided.")

    try:
        payload = decode_access_token(credentials.credentials, expected_type="access")
        user_uuid = uuid.UUID(str(payload["sub"]))
    except (AuthenticationError, ValueError) as exc:
        db.rollback()
        _record_auth_failure(request, event_type="AUTH_INVALID_TOKEN")
        raise AuthenticationError("Invalid or expired authentication token.") from exc

    user = db.get(User, user_uuid)
    if user is None:
        db.rollback()
        _record_auth_failure(request, event_type="AUTH_UNKNOWN_USER", user_id=user_uuid)
        raise AuthenticationError("Invalid authentication principal.")

    if user.account_status != UserAccountStatus.ACTIVE.value:
        db.rollback()
        _record_auth_failure(
            request,
            event_type="AUTH_INACTIVE_ACCOUNT",
            user_id=user.id,
            details={"account_status": user.account_status},
        )
        raise AuthenticationError("User account is not active.")

    iat_ts = int(payload["iat"])
    token_iat = datetime.fromtimestamp(iat_ts, tz=timezone.utc)
    if token_iat < _as_utc(user.token_valid_after):
        db.rollback()
        _record_auth_failure(request, event_type="AUTH_TOKEN_INVALIDATED", user_id=user.id)
        raise AuthenticationError("Authentication token has been invalidated.")

    return Principal(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        account_status=user.account_status,
        token_jti=str(payload.get("jti")),
        token_iat=token_iat,
        amr=tuple(payload.get("amr") or ("pwd",)),
        actor_type=ActorType.USER.value,
        platform_role=getattr(user, "platform_role", None),
    )


_ONBOARDING_ORG_STATUSES: Tuple[str, ...] = (
    OrganizationStatus.DRAFT.value,
    OrganizationStatus.SUBMITTED.value,
    OrganizationStatus.UNDER_REVIEW.value,
    OrganizationStatus.NEEDS_INFORMATION.value,
    OrganizationStatus.ACTIVE.value,
)
_ONBOARDING_ORG_WIDE_ROLES: FrozenSet[str] = ORG_WIDE_ROLES | frozenset({"REGULATORY_AFFAIRS"})


def _parse_org_uuid(raw_value: Optional[str], field_name: str) -> Optional[uuid.UUID]:
    if not raw_value:
        return None
    try:
        return uuid.UUID(str(raw_value).strip())
    except ValueError as exc:
        raise ValidationError(f"Invalid {field_name}; expected UUID.") from exc


def _resolve_tenant_context(
    request: Request,
    principal: Principal,
    requested_org_id: Optional[uuid.UUID],
    db: Session,
    *,
    allowed_org_statuses: Iterable[str],
    include_non_suspended_sites: bool = False,
    org_wide_roles: FrozenSet[str] = ORG_WIDE_ROLES,
) -> TenantContext:
    now = datetime.now(timezone.utc)
    allowed_statuses_tuple = tuple(allowed_org_statuses)

    membership_stmt = (
        select(OrganizationMembership)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(
            OrganizationMembership.user_id == principal.user_id,
            OrganizationMembership.membership_status == MembershipStatus.ACTIVE.value,
            Organization.status.in_(allowed_statuses_tuple),
        )
    )
    if requested_org_id is not None:
        membership_stmt = membership_stmt.where(OrganizationMembership.organization_id == requested_org_id)

    memberships = [
        m
        for m in db.scalars(membership_stmt).all()
        if (m.valid_from is None or _as_utc(m.valid_from) <= now)
        and (m.valid_to is None or _as_utc(m.valid_to) >= now)
    ]

    if not memberships:
        db.rollback()
        _record_auth_failure(
            request,
            event_type="TENANT_MEMBERSHIP_DENIED",
            user_id=principal.user_id,
            organization_id=requested_org_id,
            severity=SecurityEventSeverity.HIGH.value,
        )
        raise PermissionDeniedError("No active membership in the requested organization.")

    if len(memberships) > 1 and requested_org_id is None:
        raise ValidationError(
            "Multiple active organizations found; specify X-Organization-Id header.",
            error_code="TENANT_HEADER_REQUIRED",
        )

    membership = memberships[0]
    org_id = membership.organization_id

    # Load active role assignments and permissions for this membership
    role_assignments = [
        ra
        for ra in db.scalars(
            select(MembershipRoleAssignment).where(
                MembershipRoleAssignment.organization_id == org_id,
                MembershipRoleAssignment.membership_id == membership.id,
                MembershipRoleAssignment.status == RoleAssignmentStatus.ACTIVE.value,
            )
        ).all()
        if (ra.valid_from is None or _as_utc(ra.valid_from) <= now)
        and (ra.valid_to is None or _as_utc(ra.valid_to) >= now)
    ]

    role_ids = {ra.role_id for ra in role_assignments}
    role_codes: Set[str] = set()
    role_by_id: Dict[uuid.UUID, str] = {}
    permission_codes: Set[str] = set()
    if role_ids:
        roles = db.scalars(
            select(Role).where(Role.id.in_(role_ids), Role.status == "ACTIVE")
        ).all()
        active_role_ids = {r.id for r in roles}
        role_codes = {r.role_code for r in roles}
        role_by_id = {r.id: r.role_code for r in roles}
        if active_role_ids:
            perms = db.scalars(
                select(Permission.permission_code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id.in_(active_role_ids))
            ).all()
            permission_codes.update(perms)

    # Load active site assignments
    site_assignments = [
        sa
        for sa in db.scalars(
            select(SiteAssignment).where(
                SiteAssignment.organization_id == org_id,
                SiteAssignment.membership_id == membership.id,
                SiteAssignment.status == AssignmentStatus.ACTIVE.value,
            )
        ).all()
        if (sa.valid_from is None or _as_utc(sa.valid_from) <= now)
        and (sa.valid_to is None or _as_utc(sa.valid_to) >= now)
    ]
    assigned_site_ids = {sa.site_id for sa in site_assignments}

    is_org_wide = any(
        ra.scope_type == RoleAssignmentScopeType.ORGANIZATION.value
        and role_by_id.get(ra.role_id) in org_wide_roles
        for ra in role_assignments
    )

    if is_org_wide:
        site_stmt = select(Site.id).where(Site.organization_id == org_id)
        if include_non_suspended_sites:
            site_stmt = site_stmt.where(Site.status != SiteStatus.SUSPENDED.value)
        else:
            site_stmt = site_stmt.where(Site.status == SiteStatus.ACTIVE.value)
        allowed_site_ids = frozenset(db.scalars(site_stmt).all())
    else:
        role_site_ids = {
            ra.site_id
            for ra in role_assignments
            if ra.scope_type == RoleAssignmentScopeType.SITE.value and ra.site_id is not None
        }
        if role_site_ids:
            allowed_site_ids = frozenset(assigned_site_ids & role_site_ids)
        else:
            allowed_site_ids = frozenset(assigned_site_ids)

    return TenantContext(
        user_id=principal.user_id,
        organization_id=org_id,
        membership_id=membership.id,
        allowed_site_ids=allowed_site_ids,
        is_org_wide=is_org_wide,
        permission_codes=frozenset(permission_codes),
        role_codes=frozenset(role_codes),
        actor_type=principal.actor_type,
    )


def get_tenant_context(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    x_organization_id: Optional[str] = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
) -> TenantContext:
    requested_org_id = _parse_org_uuid(x_organization_id, "X-Organization-Id header")
    return _resolve_tenant_context(
        request,
        principal,
        requested_org_id,
        db,
        allowed_org_statuses=(OrganizationStatus.ACTIVE.value,),
        include_non_suspended_sites=False,
        org_wide_roles=ORG_WIDE_ROLES,
    )


def get_onboarding_tenant_context(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    x_organization_id: Optional[str] = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
) -> TenantContext:
    """
    Resolves tenant context for organization/site onboarding routes where the org may
    still be in DRAFT/SUBMITTED/UNDER_REVIEW/NEEDS_INFORMATION or ACTIVE status.
    """
    header_org_id = _parse_org_uuid(x_organization_id, "X-Organization-Id header")
    path_raw = request.path_params.get("organization_id")
    path_org_id = _parse_org_uuid(str(path_raw), "organization_id path parameter") if path_raw is not None else None

    if header_org_id is not None and path_org_id is not None and header_org_id != path_org_id:
        db.rollback()
        _record_auth_failure(
            request,
            event_type="TENANT_HEADER_MISMATCH",
            user_id=principal.user_id,
            organization_id=path_org_id,
            severity=SecurityEventSeverity.HIGH.value,
            details={"header_org_id": str(header_org_id)},
        )
        raise PermissionDeniedError(
            "X-Organization-Id header does not match target organization.",
            error_code="TENANT_HEADER_MISMATCH",
        )

    requested_org_id = path_org_id or header_org_id
    return _resolve_tenant_context(
        request,
        principal,
        requested_org_id,
        db,
        allowed_org_statuses=_ONBOARDING_ORG_STATUSES,
        include_non_suspended_sites=True,
        org_wide_roles=_ONBOARDING_ORG_WIDE_ROLES,
    )


def _enforce_permission(
    request: Request,
    tenant_ctx: TenantContext,
    permission_code: str,
    db: Session,
) -> TenantContext:
    if permission_code not in tenant_ctx.permission_codes:
        db.rollback()
        _record_auth_failure(
            request,
            event_type="RBAC_PERMISSION_DENIED",
            user_id=tenant_ctx.user_id,
            organization_id=tenant_ctx.organization_id,
            severity=SecurityEventSeverity.WARNING.value,
            details={"required_permission": permission_code},
        )
        raise PermissionDeniedError(
            f"Missing required permission '{permission_code}'.",
            error_code="INSUFFICIENT_PERMISSION",
        )
    return tenant_ctx


def require_permission(permission_code: str) -> Callable[..., TenantContext]:
    """Dependency factory enforcing an atomic permission inside the verified operational TenantContext."""

    def _dependency(
        request: Request,
        tenant_ctx: TenantContext = Depends(get_tenant_context),
        db: Session = Depends(get_db),
    ) -> TenantContext:
        return _enforce_permission(request, tenant_ctx, permission_code, db)

    return _dependency


def require_onboarding_permission(permission_code: str) -> Callable[..., TenantContext]:
    """Dependency factory enforcing an atomic permission inside the pre-activation onboarding TenantContext."""

    def _dependency(
        request: Request,
        tenant_ctx: TenantContext = Depends(get_onboarding_tenant_context),
        db: Session = Depends(get_db),
    ) -> TenantContext:
        return _enforce_permission(request, tenant_ctx, permission_code, db)

    return _dependency


def get_platform_principal(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Principal:
    """Authenticates and enforces platform-level PLATFORM_SUPER_ADMIN access."""
    if principal.platform_role != "PLATFORM_SUPER_ADMIN":
        db.rollback()
        _record_auth_failure(
            request,
            event_type="PLATFORM_PERMISSION_DENIED",
            user_id=principal.user_id,
            severity=SecurityEventSeverity.HIGH.value,
            details={"platform_role": principal.platform_role},
        )
        raise PermissionDeniedError(
            "Platform super-admin role required.",
            error_code="PLATFORM_PERMISSION_DENIED",
        )
    return principal


def require_platform_permission(permission_code: str) -> Callable[..., Principal]:
    """Dependency factory enforcing a PLATFORM_SUPER_ADMIN role permission via RBAC tables."""

    def _dependency(
        request: Request,
        principal: Principal = Depends(get_platform_principal),
        db: Session = Depends(get_db),
    ) -> Principal:
        perms = set(
            db.scalars(
                select(Permission.permission_code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .join(Role, Role.id == RolePermission.role_id)
                .where(Role.role_code == "PLATFORM_SUPER_ADMIN", Role.status == "ACTIVE")
            ).all()
        )
        if permission_code not in perms:
            db.rollback()
            _record_auth_failure(
                request,
                event_type="PLATFORM_PERMISSION_DENIED",
                user_id=principal.user_id,
                severity=SecurityEventSeverity.HIGH.value,
                details={"required_permission": permission_code},
            )
            raise PermissionDeniedError(
                f"Missing required platform permission '{permission_code}'.",
                error_code="INSUFFICIENT_PERMISSION",
            )
        return principal

    return _dependency


class SlidingWindowRateLimiter:
    """Thread-safe in-memory sliding-window rate limiter keyed by (scope, client_ip)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: Dict[Tuple[str, str], Deque[float]] = {}

    def check(self, scope: str, client_key: str, *, max_requests: int, window_seconds: int) -> None:
        now = time.monotonic()
        cutoff = now - float(window_seconds)
        key = (scope, client_key)
        with self._lock:
            dq = self._buckets.setdefault(key, deque())
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= max_requests:
                raise RateLimitError(
                    f"Rate limit exceeded for '{scope}'; please retry later.",
                    details={"scope": scope, "retry_after_seconds": window_seconds},
                )
            dq.append(now)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


_rate_limiter = SlidingWindowRateLimiter()


def reset_rate_limiter() -> None:
    """Clears sliding-window buckets (primarily for isolated tests)."""
    _rate_limiter.reset()


def rate_limit(scope: str, max_requests: int = 20, window_seconds: int = 60) -> Callable[..., None]:
    """FastAPI dependency enforcing a thread-safe in-memory sliding-window rate limit."""

    def _dependency(request: Request) -> None:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip() or "unknown"
        else:
            client_ip = request.client.host if request.client else "unknown"
        _rate_limiter.check(scope, client_ip, max_requests=max_requests, window_seconds=window_seconds)

    return _dependency
