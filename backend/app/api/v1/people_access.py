import uuid
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.deps import (
    Principal,
    TenantContext,
    get_current_principal,
    get_db,
    rate_limit,
    require_permission,
)
from app.core.exceptions import PermissionDeniedError
from app.domain.enums import SecurityEventOutcome, SecurityEventSeverity
from app.models.schemas.actors_partners import (
    BusinessPartnerCreateRequest,
    BusinessPartnerResponse,
    EmployeeQualificationCreateRequest,
    EmployeeQualificationResponse,
    InvitationAcceptRequest,
    InvitationCreateRequest,
    InvitationPreviewResponse,
    InvitationResponse,
    MembershipApproveRequest,
    MembershipResponse,
    MembershipRevokeRequest,
    PartnerInvitationCreateRequest,
    PartnerMembershipApproveRequest,
    PartnerMembershipResponse,
    PartnerSiteCreateRequest,
    PartnerSiteResponse,
    RoleAssignmentCreateRequest,
    RoleAssignmentResponse,
    RoleRevokeRequest,
    SiteAssignmentCreateRequest,
    SiteAssignmentResponse,
    TrainingRecordCreateRequest,
    TrainingRecordResponse,
)
from app.models.schemas.common import AuditReasonRequest, Page
from app.services.audit_service import AuditService
from app.services.partner_service import PartnerService
from app.services.people_service import PeopleService

router = APIRouter(tags=["people-access-partners"])

_people_svc = PeopleService()
_partner_svc = PartnerService()
_audit_svc = AuditService()


def _assert_matching_org_path(request: Request, tenant_ctx: TenantContext, organization_id: uuid.UUID) -> None:
    if tenant_ctx.organization_id != organization_id:
        _audit_svc.record_security_event(
            event_type="TENANT_PATH_MISMATCH",
            outcome=SecurityEventOutcome.DENIED.value,
            severity=SecurityEventSeverity.HIGH.value,
            organization_id=organization_id,
            user_id=tenant_ctx.user_id,
            source_ip=request.client.host if request.client else None,
            target_resource=str(request.url.path),
        )
        raise PermissionDeniedError("Cross-tenant access denied.", error_code="TENANT_MEMBERSHIP_DENIED")


# --- Invitations, Memberships & Role Governance ---


@router.post(
    "/organizations/{organization_id}/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_invitation(
    request: Request,
    organization_id: uuid.UUID,
    payload: InvitationCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("membership.invite")),
    db: Session = Depends(get_db),
) -> InvitationResponse:
    _assert_matching_org_path(request, tenant_ctx, organization_id)
    return _people_svc.create_invitation(
        db, organization_id=organization_id, tenant_ctx=tenant_ctx, payload=payload
    )


@router.get(
    "/invitations/{token}",
    response_model=InvitationPreviewResponse,
    dependencies=[Depends(rate_limit("invitation_preview", max_requests=30, window_seconds=60))],
)
def preview_invitation(
    token: str,
    db: Session = Depends(get_db),
) -> InvitationPreviewResponse:
    return _people_svc.preview_invitation(db, token)


@router.post("/invitations/{token}/accept", response_model=MembershipResponse)
def accept_invitation(
    request: Request,
    token: str,
    payload: Optional[InvitationAcceptRequest] = Body(default=None),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> MembershipResponse:
    source_ip = request.client.host if request.client else None
    return _people_svc.accept_invitation(
        db,
        token,
        principal=principal,
        payload=payload or InvitationAcceptRequest(),
        source_ip=source_ip,
    )


@router.post("/invitations/{invitation_id}/revoke", response_model=InvitationResponse)
def revoke_invitation(
    invitation_id: uuid.UUID,
    payload: AuditReasonRequest,
    tenant_ctx: TenantContext = Depends(require_permission("membership.invite")),
    db: Session = Depends(get_db),
) -> InvitationResponse:
    return _people_svc.revoke_invitation(
        db,
        organization_id=tenant_ctx.organization_id,
        invitation_id=invitation_id,
        tenant_ctx=tenant_ctx,
        reason=payload.reason,
    )


@router.get("/organizations/{organization_id}/memberships", response_model=Page[MembershipResponse])
def list_memberships(
    request: Request,
    organization_id: uuid.UUID,
    membership_status: Optional[str] = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(require_permission("membership.read")),
    db: Session = Depends(get_db),
) -> Page[MembershipResponse]:
    _assert_matching_org_path(request, tenant_ctx, organization_id)
    items, total = _people_svc.list_memberships(
        db,
        organization_id=organization_id,
        status=membership_status,
        page=page,
        page_size=page_size,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/memberships/{membership_id}/approve", response_model=MembershipResponse)
def approve_membership(
    membership_id: uuid.UUID,
    payload: MembershipApproveRequest,
    tenant_ctx: TenantContext = Depends(require_permission("membership.approve")),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> MembershipResponse:
    return _people_svc.approve_membership(
        db,
        organization_id=tenant_ctx.organization_id,
        membership_id=membership_id,
        tenant_ctx=tenant_ctx,
        principal=principal,
        payload=payload,
    )


@router.post("/memberships/{membership_id}/revoke", response_model=MembershipResponse)
def revoke_membership(
    membership_id: uuid.UUID,
    payload: MembershipRevokeRequest,
    tenant_ctx: TenantContext = Depends(require_permission("membership.revoke")),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> MembershipResponse:
    return _people_svc.revoke_membership(
        db,
        organization_id=tenant_ctx.organization_id,
        membership_id=membership_id,
        principal=principal,
        reason=payload.reason,
        row_version=payload.row_version,
    )


@router.post(
    "/memberships/{membership_id}/sites",
    response_model=SiteAssignmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def assign_member_site(
    membership_id: uuid.UUID,
    payload: SiteAssignmentCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("role.assign")),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> SiteAssignmentResponse:
    return _people_svc.assign_site(
        db,
        organization_id=tenant_ctx.organization_id,
        membership_id=membership_id,
        principal=principal,
        payload=payload,
    )


@router.post(
    "/memberships/{membership_id}/roles",
    response_model=RoleAssignmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def assign_member_role(
    membership_id: uuid.UUID,
    payload: RoleAssignmentCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("role.assign")),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> RoleAssignmentResponse:
    return _people_svc.assign_role(
        db,
        organization_id=tenant_ctx.organization_id,
        membership_id=membership_id,
        tenant_ctx=tenant_ctx,
        principal=principal,
        payload=payload,
    )


@router.delete("/memberships/{membership_id}/roles/{assignment_id}", response_model=RoleAssignmentResponse)
def revoke_member_role(
    membership_id: uuid.UUID,
    assignment_id: uuid.UUID,
    payload: Optional[RoleRevokeRequest] = Body(default=None),
    reason: Optional[str] = Query(default=None, min_length=3, max_length=1000),
    tenant_ctx: TenantContext = Depends(require_permission("role.revoke")),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> RoleAssignmentResponse:
    effective_reason = (payload.reason if payload is not None else reason) or "Role revoked by administrator"
    return _people_svc.revoke_role_assignment(
        db,
        organization_id=tenant_ctx.organization_id,
        membership_id=membership_id,
        assignment_id=assignment_id,
        principal=principal,
        reason=effective_reason,
    )


# --- Employee Qualifications & Training Records ---


@router.post(
    "/employee-profiles/{employee_profile_id}/qualifications",
    response_model=EmployeeQualificationResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_employee_qualification(
    employee_profile_id: uuid.UUID,
    payload: EmployeeQualificationCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("people.qualification.manage")),
    db: Session = Depends(get_db),
) -> EmployeeQualificationResponse:
    qual = _people_svc.add_qualification(
        db,
        organization_id=tenant_ctx.organization_id,
        employee_profile_id=employee_profile_id,
        tenant_ctx=tenant_ctx,
        payload=payload,
    )
    return EmployeeQualificationResponse.model_validate(qual)


@router.post(
    "/employee-profiles/{employee_profile_id}/training-records",
    response_model=TrainingRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_training_record(
    employee_profile_id: uuid.UUID,
    payload: TrainingRecordCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("people.training.manage")),
    db: Session = Depends(get_db),
) -> TrainingRecordResponse:
    rec = _people_svc.add_training_record(
        db,
        organization_id=tenant_ctx.organization_id,
        employee_profile_id=employee_profile_id,
        tenant_ctx=tenant_ctx,
        payload=payload,
    )
    return TrainingRecordResponse.model_validate(rec)


# --- Business Partners ---


@router.post("/partners", response_model=BusinessPartnerResponse, status_code=status.HTTP_201_CREATED)
def create_partner(
    payload: BusinessPartnerCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("partner.manage")),
    db: Session = Depends(get_db),
) -> BusinessPartnerResponse:
    partner = _partner_svc.create_partner(db, tenant_ctx=tenant_ctx, payload=payload)
    return BusinessPartnerResponse.model_validate(partner)


@router.get("/partners", response_model=Page[BusinessPartnerResponse])
def list_partners(
    partner_type: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(require_permission("partner.read")),
    db: Session = Depends(get_db),
) -> Page[BusinessPartnerResponse]:
    items, total = _partner_svc.list_partners(
        db, tenant_ctx=tenant_ctx, partner_type=partner_type, page=page, page_size=page_size
    )
    return Page(
        items=[BusinessPartnerResponse.model_validate(p) for p in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/partners/{partner_id}/sites", response_model=PartnerSiteResponse, status_code=status.HTTP_201_CREATED)
def create_partner_site(
    partner_id: uuid.UUID,
    payload: PartnerSiteCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("partner.manage")),
    db: Session = Depends(get_db),
) -> PartnerSiteResponse:
    site = _partner_svc.create_partner_site(db, partner_id=partner_id, tenant_ctx=tenant_ctx, payload=payload)
    return PartnerSiteResponse.model_validate(site)


@router.post(
    "/partners/{partner_id}/invitations",
    response_model=PartnerMembershipResponse,
    status_code=status.HTTP_201_CREATED,
)
def invite_partner_user(
    partner_id: uuid.UUID,
    payload: PartnerInvitationCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("partner.manage")),
    db: Session = Depends(get_db),
) -> PartnerMembershipResponse:
    return _partner_svc.invite_partner_user(
        db, partner_id=partner_id, tenant_ctx=tenant_ctx, payload=payload
    )


@router.post("/partner-memberships/{membership_id}/approve", response_model=PartnerMembershipResponse)
def approve_partner_membership(
    membership_id: uuid.UUID,
    payload: PartnerMembershipApproveRequest,
    tenant_ctx: TenantContext = Depends(require_permission("partner.manage")),
    db: Session = Depends(get_db),
) -> PartnerMembershipResponse:
    pm = _partner_svc.approve_partner_membership(
        db, membership_id=membership_id, tenant_ctx=tenant_ctx, payload=payload
    )
    return PartnerMembershipResponse.model_validate(pm)
