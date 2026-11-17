import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, FrozenSet, Generator, Optional, Set

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import UnitOfWork, get_session_factory
from app.core.exceptions import AuthenticationError, PermissionDeniedError, ValidationError
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
    )


def get_tenant_context(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    x_organization_id: Optional[str] = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
) -> TenantContext:
    now = datetime.now(timezone.utc)

    requested_org_id: Optional[uuid.UUID] = None
    if x_organization_id:
        try:
            requested_org_id = uuid.UUID(x_organization_id.strip())
        except ValueError as exc:
            raise ValidationError("Invalid X-Organization-Id header; expected UUID.") from exc

    membership_stmt = (
        select(OrganizationMembership)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(
            OrganizationMembership.user_id == principal.user_id,
            OrganizationMembership.membership_status == MembershipStatus.ACTIVE.value,
            Organization.status == OrganizationStatus.ACTIVE.value,
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
    permission_codes: Set[str] = set()
    if role_ids:
        roles = db.scalars(
            select(Role).where(Role.id.in_(role_ids), Role.status == "ACTIVE")
        ).all()
        active_role_ids = {r.id for r in roles}
        role_codes = {r.role_code for r in roles}
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

    role_by_id = {}
    if role_ids:
        for r in db.scalars(select(Role).where(Role.id.in_(role_ids), Role.status == "ACTIVE")).all():
            role_by_id[r.id] = r.role_code

    is_org_wide = any(
        ra.scope_type == RoleAssignmentScopeType.ORGANIZATION.value
        and role_by_id.get(ra.role_id) in ORG_WIDE_ROLES
        for ra in role_assignments
    )

    if is_org_wide:
        active_org_sites = db.scalars(
            select(Site.id).where(
                Site.organization_id == org_id,
                Site.status == SiteStatus.ACTIVE.value,
            )
        ).all()
        allowed_site_ids = frozenset(active_org_sites)
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


def require_permission(permission_code: str) -> Callable[..., TenantContext]:
    """Dependency factory enforcing an atomic permission inside the verified TenantContext."""

    def _dependency(
        request: Request,
        tenant_ctx: TenantContext = Depends(get_tenant_context),
        db: Session = Depends(get_db),
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

    return _dependency
