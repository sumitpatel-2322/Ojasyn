import uuid
from typing import List

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.deps import (
    Principal,
    TenantContext,
    get_current_principal,
    get_db,
    get_tenant_context,
    rate_limit,
    require_onboarding_permission,
    require_platform_permission,
)
from app.models.schemas.auth_onboarding import (
    AddressCreateInput,
    DepartmentCreateRequest,
    DepartmentResponse,
    LicenseCreateRequest,
    LoginRequest,
    MFAChallengeRequest,
    MFAChallengeResponse,
    MyPermissionsResponse,
    OnboardingOrganizationCreateRequest,
    OnboardingSubmitRequest,
    OrganizationAddressResponse,
    OrganizationLicenseResponse,
    OrganizationResponse,
    OrganizationUpdateRequest,
    RefreshTokenRequest,
    RegisterRequest,
    RegisterResponse,
    ReviewDecisionRequest,
    RoleCatalogResponse,
    SiteCreateRequest,
    SiteLicenseResponse,
    SiteResponse,
    SiteUpdateRequest,
    TokenResponse,
    UpdateUserProfileRequest,
    UserProfileResponse,
    VerifyEmailRequest,
    VerifyEmailResponse,
    LogoutRequest,
)
from app.models.schemas.common import Page
from app.repositories.identity_repo import IdentityRepository
from app.services.identity_access_service import IdentityAccessService
from app.services.onboarding_service import OnboardingService

router = APIRouter(tags=["auth-onboarding"])

_identity_svc = IdentityAccessService()
_onboarding_svc = OnboardingService()
_identity_repo = IdentityRepository()


# --- Auth, Sessions, MFA & User Profile ---


@router.post(
    "/auth/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("auth_register", max_requests=20, window_seconds=60))],
)
def register(
    request: Request,
    payload: RegisterRequest,
    db: Session = Depends(get_db),
) -> RegisterResponse:
    source_ip = request.client.host if request.client else None
    return _identity_svc.register(db, payload, source_ip=source_ip)


@router.post(
    "/auth/verify-email",
    response_model=VerifyEmailResponse,
    dependencies=[Depends(rate_limit("auth_verify_email", max_requests=30, window_seconds=60))],
)
def verify_email(
    request: Request,
    payload: VerifyEmailRequest,
    db: Session = Depends(get_db),
) -> VerifyEmailResponse:
    source_ip = request.client.host if request.client else None
    return _identity_svc.verify_email(db, payload.token, source_ip=source_ip)


@router.post(
    "/auth/login",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit("auth_login", max_requests=20, window_seconds=60))],
)
def login(
    request: Request,
    payload: LoginRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    source_ip = request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")
    return _identity_svc.login(db, payload, source_ip=source_ip, user_agent=user_agent)


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh_token(
    request: Request,
    payload: RefreshTokenRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    source_ip = request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")
    return _identity_svc.refresh(
        db, payload.refresh_token, source_ip=source_ip, user_agent=user_agent
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    payload: LogoutRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> None:
    source_ip = request.client.host if request.client else None
    _identity_svc.logout(
        db,
        user_id=principal.user_id,
        raw_refresh_token=payload.refresh_token,
        revoke_all=payload.revoke_all_sessions,
        source_ip=source_ip,
    )


@router.post("/auth/mfa/challenge", response_model=MFAChallengeResponse)
def mfa_challenge(
    request: Request,
    payload: MFAChallengeRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> MFAChallengeResponse:
    source_ip = request.client.host if request.client else None
    return _identity_svc.mfa_challenge(
        db,
        user_id=principal.user_id,
        action=payload.action,
        code=payload.code,
        source_ip=source_ip,
    )


@router.get("/users/me", response_model=UserProfileResponse)
def get_my_profile(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> UserProfileResponse:
    return _identity_svc.get_my_profile(db, principal.user_id)


@router.patch("/users/me/profile", response_model=UserProfileResponse)
def update_my_profile(
    payload: UpdateUserProfileRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> UserProfileResponse:
    return _identity_svc.update_my_profile(db, principal.user_id, payload)


@router.get("/roles", response_model=List[RoleCatalogResponse])
def list_roles(
    _principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> List[RoleCatalogResponse]:
    roles = _identity_repo.list_roles(db)
    return [RoleCatalogResponse.model_validate(r) for r in roles]


@router.get("/permissions/me", response_model=MyPermissionsResponse)
def get_my_permissions(
    tenant_ctx: TenantContext = Depends(get_tenant_context),
) -> MyPermissionsResponse:
    return MyPermissionsResponse(
        user_id=tenant_ctx.user_id,  # type: ignore[arg-type]
        organization_id=tenant_ctx.organization_id,
        membership_id=tenant_ctx.membership_id,
        role_codes=sorted(tenant_ctx.role_codes),
        permission_codes=sorted(tenant_ctx.permission_codes),
        allowed_site_ids=sorted(tenant_ctx.allowed_site_ids, key=str),
        is_org_wide=tenant_ctx.is_org_wide,
    )


# --- Organization Onboarding, Sites, Licenses & Departments ---


@router.post("/onboarding/organizations", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
def create_onboarding_organization(
    payload: OnboardingOrganizationCreateRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> OrganizationResponse:
    org = _onboarding_svc.create_organization_onboarding(db, principal=principal, payload=payload)
    return OrganizationResponse.model_validate(org)


@router.get("/onboarding/organizations/{organization_id}", response_model=OrganizationResponse)
def get_onboarding_organization(
    organization_id: uuid.UUID,
    _tenant_ctx: TenantContext = Depends(require_onboarding_permission("org.read")),
    db: Session = Depends(get_db),
) -> OrganizationResponse:
    org = _onboarding_svc.get_organization(db, organization_id)
    return OrganizationResponse.model_validate(org)


@router.patch("/organizations/{organization_id}", response_model=OrganizationResponse)
def update_organization(
    organization_id: uuid.UUID,
    payload: OrganizationUpdateRequest,
    tenant_ctx: TenantContext = Depends(require_onboarding_permission("org.manage")),
    db: Session = Depends(get_db),
) -> OrganizationResponse:
    org = _onboarding_svc.update_organization(
        db, organization_id=organization_id, tenant_ctx=tenant_ctx, payload=payload
    )
    return OrganizationResponse.model_validate(org)


@router.post(
    "/organizations/{organization_id}/addresses",
    response_model=OrganizationAddressResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_organization_address(
    organization_id: uuid.UUID,
    payload: AddressCreateInput,
    tenant_ctx: TenantContext = Depends(require_onboarding_permission("org.manage")),
    db: Session = Depends(get_db),
) -> OrganizationAddressResponse:
    addr = _onboarding_svc.add_organization_address(
        db, organization_id=organization_id, tenant_ctx=tenant_ctx, payload=payload
    )
    return OrganizationAddressResponse.model_validate(addr)


@router.get("/organizations/{organization_id}/addresses", response_model=List[OrganizationAddressResponse])
def list_organization_addresses(
    organization_id: uuid.UUID,
    _tenant_ctx: TenantContext = Depends(require_onboarding_permission("org.read")),
    db: Session = Depends(get_db),
) -> List[OrganizationAddressResponse]:
    rows = _onboarding_svc.list_organization_addresses(db, organization_id)
    return [OrganizationAddressResponse.model_validate(r) for r in rows]


@router.post(
    "/organizations/{organization_id}/licenses",
    response_model=OrganizationLicenseResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_organization_license(
    organization_id: uuid.UUID,
    payload: LicenseCreateRequest,
    tenant_ctx: TenantContext = Depends(require_onboarding_permission("org.manage")),
    db: Session = Depends(get_db),
) -> OrganizationLicenseResponse:
    lic = _onboarding_svc.add_organization_license(
        db, organization_id=organization_id, tenant_ctx=tenant_ctx, payload=payload
    )
    return OrganizationLicenseResponse.model_validate(lic)


@router.get("/organizations/{organization_id}/licenses", response_model=List[OrganizationLicenseResponse])
def list_organization_licenses(
    organization_id: uuid.UUID,
    _tenant_ctx: TenantContext = Depends(require_onboarding_permission("org.read")),
    db: Session = Depends(get_db),
) -> List[OrganizationLicenseResponse]:
    rows = _onboarding_svc.list_organization_licenses(db, organization_id)
    return [OrganizationLicenseResponse.model_validate(r) for r in rows]


@router.post("/organizations/{organization_id}/submit", response_model=OrganizationResponse)
def submit_organization(
    organization_id: uuid.UUID,
    payload: OnboardingSubmitRequest,
    tenant_ctx: TenantContext = Depends(require_onboarding_permission("org.manage")),
    db: Session = Depends(get_db),
) -> OrganizationResponse:
    org = _onboarding_svc.submit_organization(
        db,
        organization_id=organization_id,
        tenant_ctx=tenant_ctx,
        row_version=payload.row_version,
        reason=payload.reason,
    )
    return OrganizationResponse.model_validate(org)


@router.post("/organizations/{organization_id}/review", response_model=OrganizationResponse)
def review_organization(
    organization_id: uuid.UUID,
    payload: ReviewDecisionRequest,
    reviewer: Principal = Depends(require_platform_permission("org.review")),
    db: Session = Depends(get_db),
) -> OrganizationResponse:
    org = _onboarding_svc.review_organization(
        db, organization_id=organization_id, reviewer=reviewer, payload=payload
    )
    return OrganizationResponse.model_validate(org)


@router.post(
    "/organizations/{organization_id}/sites",
    response_model=SiteResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_site(
    organization_id: uuid.UUID,
    payload: SiteCreateRequest,
    tenant_ctx: TenantContext = Depends(require_onboarding_permission("site.manage")),
    db: Session = Depends(get_db),
) -> SiteResponse:
    site = _onboarding_svc.create_site(
        db, organization_id=organization_id, tenant_ctx=tenant_ctx, payload=payload
    )
    return SiteResponse.model_validate(site)


@router.get("/organizations/{organization_id}/sites", response_model=Page[SiteResponse])
def list_sites(
    organization_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(require_onboarding_permission("site.read")),
    db: Session = Depends(get_db),
) -> Page[SiteResponse]:
    items, total = _onboarding_svc.list_sites(
        db, organization_id=organization_id, tenant_ctx=tenant_ctx, page=page, page_size=page_size
    )
    return Page(
        items=[SiteResponse.model_validate(s) for s in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.patch("/organizations/{organization_id}/sites/{site_id}", response_model=SiteResponse)
def update_site(
    organization_id: uuid.UUID,
    site_id: uuid.UUID,
    payload: SiteUpdateRequest,
    tenant_ctx: TenantContext = Depends(require_onboarding_permission("site.manage")),
    db: Session = Depends(get_db),
) -> SiteResponse:
    site = _onboarding_svc.update_site(
        db,
        organization_id=organization_id,
        site_id=site_id,
        tenant_ctx=tenant_ctx,
        payload=payload,
    )
    return SiteResponse.model_validate(site)


@router.post(
    "/sites/{site_id}/licenses",
    response_model=SiteLicenseResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_site_license(
    site_id: uuid.UUID,
    payload: LicenseCreateRequest,
    tenant_ctx: TenantContext = Depends(require_onboarding_permission("site.manage")),
    db: Session = Depends(get_db),
) -> SiteLicenseResponse:
    lic = _onboarding_svc.add_site_license(
        db, site_id=site_id, tenant_ctx=tenant_ctx, payload=payload
    )
    return SiteLicenseResponse.model_validate(lic)


@router.post("/sites/{site_id}/submit", response_model=SiteResponse)
def submit_site(
    site_id: uuid.UUID,
    payload: OnboardingSubmitRequest,
    tenant_ctx: TenantContext = Depends(require_onboarding_permission("site.manage")),
    db: Session = Depends(get_db),
) -> SiteResponse:
    site = _onboarding_svc.submit_site(
        db,
        site_id=site_id,
        tenant_ctx=tenant_ctx,
        row_version=payload.row_version,
        reason=payload.reason,
    )
    return SiteResponse.model_validate(site)


@router.post("/sites/{site_id}/review", response_model=SiteResponse)
def review_site(
    site_id: uuid.UUID,
    payload: ReviewDecisionRequest,
    reviewer: Principal = Depends(require_platform_permission("site.review")),
    db: Session = Depends(get_db),
) -> SiteResponse:
    site = _onboarding_svc.review_site(db, site_id=site_id, reviewer=reviewer, payload=payload)
    return SiteResponse.model_validate(site)


@router.post(
    "/organizations/{organization_id}/departments",
    response_model=DepartmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_department(
    organization_id: uuid.UUID,
    payload: DepartmentCreateRequest,
    tenant_ctx: TenantContext = Depends(require_onboarding_permission("department.manage")),
    db: Session = Depends(get_db),
) -> DepartmentResponse:
    dept = _onboarding_svc.create_department(
        db, organization_id=organization_id, tenant_ctx=tenant_ctx, payload=payload
    )
    return DepartmentResponse.model_validate(dept)


@router.get("/organizations/{organization_id}/departments", response_model=Page[DepartmentResponse])
def list_departments(
    organization_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(require_onboarding_permission("org.read")),
    db: Session = Depends(get_db),
) -> Page[DepartmentResponse]:
    items, total = _onboarding_svc.list_departments(
        db, organization_id=organization_id, tenant_ctx=tenant_ctx, page=page, page_size=page_size
    )
    return Page(
        items=[DepartmentResponse.model_validate(d) for d in items],
        total=total,
        page=page,
        page_size=page_size,
    )
