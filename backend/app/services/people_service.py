import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.database import UnitOfWork, next_tenant_sequence, verify_and_increment_version
from app.core.deps import Principal, TenantContext
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.core.security import hash_token
from app.domain.enums import (
    ActorType,
    ApprovalDecision,
    AssignmentStatus,
    AssignmentType,
    EmploymentStatus,
    InvitationStatus,
    MemberType,
    MembershipStatus,
    OrganizationStatus,
    RoleAssignmentScopeType,
    RoleAssignmentStatus,
    RoleScope,
    SecurityEventOutcome,
    SecurityEventSeverity,
    UserAccountStatus,
)
from app.domain.rbac_catalog import QUALITY_SENSITIVE_ROLES, ROLE_DEFINITIONS_BY_CODE
from app.models.domain.governance import ApprovalRecord
from app.models.domain.identity import (
    EmployeeProfile,
    EmployeeQualification,
    Invitation,
    MembershipRoleAssignment,
    OrganizationMembership,
    Role,
    SiteAssignment,
    TrainingRecord,
)
from app.models.domain.tenancy import Organization
from app.models.schemas.actors_partners import (
    EmployeeQualificationCreateRequest,
    InvitationAcceptRequest,
    InvitationCreateRequest,
    InvitationPreviewResponse,
    InvitationResponse,
    MembershipApproveRequest,
    MembershipResponse,
    RoleAssignmentCreateRequest,
    RoleAssignmentResponse,
    SiteAssignmentCreateRequest,
    SiteAssignmentResponse,
    TrainingRecordCreateRequest,
)
from app.repositories.governance_repo import OutboxRepository
from app.repositories.identity_repo import IdentityRepository
from app.repositories.organization_repo import OrganizationRepository
from app.services.audit_service import AuditService


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        masked_local = local[0] + "***" if local else "***"
    else:
        masked_local = f"{local[0]}***{local[-1]}"
    return f"{masked_local}@{domain}"


class PeopleService:
    """Invitations, memberships, two-phase QMS_HEAD bootstrap, role assignments, qualifications & training."""

    def __init__(
        self,
        identity_repo: Optional[IdentityRepository] = None,
        org_repo: Optional[OrganizationRepository] = None,
        audit_service: Optional[AuditService] = None,
        outbox_repo: Optional[OutboxRepository] = None,
    ) -> None:
        self._identity_repo = identity_repo or IdentityRepository()
        self._org_repo = org_repo or OrganizationRepository()
        self._audit = audit_service or AuditService()
        self._outbox = outbox_repo or OutboxRepository()

    def _validate_tenant_role(self, db: Session, role_code: str, *, scope_type: Optional[str] = None) -> Role:
        role = self._identity_repo.get_role_by_code(db, role_code)
        if role is None or role.status != "ACTIVE":
            raise NotFoundError(f"Role '{role_code}' not found.")
        if role.role_scope in {RoleScope.PLATFORM.value, RoleScope.PARTNER.value}:
            raise PermissionDeniedError(
                f"Role '{role_code}' with scope '{role.role_scope}' cannot be assigned to internal tenant memberships.",
                error_code="INVALID_ROLE_SCOPE",
            )
        if scope_type is not None:
            role_def = ROLE_DEFINITIONS_BY_CODE.get(role.role_code)
            if role_def and scope_type not in role_def.allowed_assignment_scopes:
                raise ValidationError(
                    f"Role '{role.role_code}' does not support scope_type '{scope_type}'.",
                    error_code="INVALID_ROLE_ASSIGNMENT_SCOPE",
                )
        return role

    def create_invitation(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: InvitationCreateRequest,
    ) -> InvitationResponse:
        if self._identity_repo.get_pending_invitation_for_email(db, organization_id, payload.email) is not None:
            raise ConflictError("A pending invitation already exists for this email in the organization.")

        for site_id in payload.site_ids:
            if self._org_repo.get_site_for_org(db, organization_id, site_id) is None:
                raise NotFoundError(f"Site {site_id} not found in this organization.")

        if payload.department_id is not None:
            if self._org_repo.get_department_for_org(db, organization_id, payload.department_id) is None:
                raise NotFoundError("Department not found in this organization.")

        role_codes: List[str] = []
        for code in payload.requested_role_codes:
            role = self._validate_tenant_role(db, code)
            role_def = ROLE_DEFINITIONS_BY_CODE.get(role.role_code)
            allowed_scopes = role_def.allowed_assignment_scopes if role_def else frozenset({"SITE"})
            if not payload.site_ids and "ORGANIZATION" not in allowed_scopes:
                raise ValidationError(
                    f"Role '{role.role_code}' is restricted to SITE scope and requires at least one site_id.",
                    error_code="SITE_ID_REQUIRED_FOR_ROLE",
                )
            if payload.site_ids and "SITE" not in allowed_scopes and "ORGANIZATION" not in allowed_scopes:
                raise ValidationError(
                    f"Role '{role.role_code}' does not allow tenant site assignments.",
                    error_code="INVALID_ROLE_ASSIGNMENT_SCOPE",
                )
            role_codes.append(role.role_code)

        raw_token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=payload.expires_in_hours)
        requires_quality = any(code in QUALITY_SENSITIVE_ROLES for code in role_codes)

        with UnitOfWork(db):
            inv = Invitation(
                organization_id=organization_id,
                site_ids=[str(sid) for sid in payload.site_ids],
                email=payload.email,
                requested_role_codes=role_codes,
                department_id=payload.department_id,
                employee_code=payload.employee_code,
                designation=payload.designation,
                requires_quality_approval=requires_quality,
                token_hash=hash_token(raw_token),
                expires_at=expires_at,
                invited_by_user_id=tenant_ctx.user_id,  # type: ignore[arg-type]
                status=InvitationStatus.PENDING.value,
            )
            db.add(inv)
            db.flush()

            self._audit.record(
                db,
                organization_id=organization_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="INVITATION_CREATED",
                entity_type="invitations",
                entity_id=inv.id,
                after_state={"email": inv.email, "requested_role_codes": role_codes},
            )
            # NEVER store raw invite token in outbox_events.payload_json
            self._outbox.enqueue(
                db,
                organization_id=organization_id,
                aggregate_type="invitation",
                aggregate_id=inv.id,
                event_type="InvitationCreated",
                payload_json={
                    "invitation_id": str(inv.id),
                    "organization_id": str(organization_id),
                    "expires_at": expires_at.isoformat(),
                },
            )

        resp = InvitationResponse.model_validate(inv)
        resp.invite_token = raw_token
        return resp

    def preview_invitation(self, db: Session, raw_token: str) -> InvitationPreviewResponse:
        inv = self._identity_repo.get_invitation_by_token_hash(db, hash_token(raw_token))
        if inv is None:
            raise NotFoundError("Invitation not found or invalid.")
        org = self._org_repo.get_by_id(db, inv.organization_id)
        if org is None:
            raise NotFoundError("Invitation organization not found.")

        now = datetime.now(timezone.utc)
        effective_status = inv.status
        if effective_status == InvitationStatus.PENDING.value and _as_utc(inv.expires_at) <= now:
            effective_status = InvitationStatus.EXPIRED.value

        return InvitationPreviewResponse(
            organization_legal_name=org.legal_name,
            masked_email=_mask_email(inv.email),
            expires_at=inv.expires_at,
            status=effective_status,
        )

    def accept_invitation(
        self,
        db: Session,
        raw_token: str,
        *,
        principal: Principal,
        payload: InvitationAcceptRequest,
        source_ip: Optional[str] = None,
    ) -> MembershipResponse:
        inv = self._identity_repo.get_invitation_by_token_hash(db, hash_token(raw_token))
        if inv is None:
            raise NotFoundError("Invitation not found or invalid.")

        user = self._identity_repo.get_user_by_id(db, principal.user_id)
        if user is None or user.account_status != UserAccountStatus.ACTIVE.value:
            raise PermissionDeniedError("Authenticated user account must be active.")
        if user.email_verified_at is None:
            raise PermissionDeniedError("Email verification is required before accepting an invitation.")

        if user.email.strip().lower() != inv.email.strip().lower():
            self._audit.record_security_event(
                event_type="INVITATION_EMAIL_MISMATCH",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=inv.organization_id,
                user_id=user.id,
                source_ip=source_ip,
                target_resource="/api/v1/invitations/{token}/accept",
            )
            raise PermissionDeniedError("Authenticated user email does not match this invitation.")

        now = datetime.now(timezone.utc)
        if inv.status != InvitationStatus.PENDING.value:
            raise ConflictError(f"Invitation is no longer pending (status={inv.status}).")
        if _as_utc(inv.expires_at) <= now:
            with UnitOfWork(db):
                inv.status = InvitationStatus.EXPIRED.value
            raise ConflictError("Invitation has expired.")

        if self._identity_repo.get_membership_for_org_user(db, inv.organization_id, user.id) is not None:
            raise ConflictError("User already holds a membership in this organization.")

        with UnitOfWork(db):
            inv.status = InvitationStatus.ACCEPTED.value
            inv.accepted_at = now

            emp_code = payload.employee_code or inv.employee_code
            if not emp_code:
                seq = next_tenant_sequence(db, inv.organization_id, "EMP", period_year=now.year)
                emp_code = f"EMP-{now.year}-{seq:06d}"

            membership = OrganizationMembership(
                user_id=user.id,
                organization_id=inv.organization_id,
                member_type=MemberType.EMPLOYEE.value,
                membership_status=MembershipStatus.PENDING_APPROVAL.value,
                employee_code=emp_code,
                department_id=inv.department_id,
                designation=inv.designation,
                requested_at=now,
            )
            db.add(membership)
            db.flush()

            emp_profile = EmployeeProfile(
                membership_id=membership.id,
                user_id=user.id,
                organization_id=inv.organization_id,
                employee_code=emp_code,
                job_title=payload.job_title or inv.designation,
                employment_status=EmploymentStatus.ACTIVE.value,
            )
            db.add(emp_profile)
            db.flush()

            site_uuids = [uuid.UUID(str(s)) for s in (inv.site_ids or [])]
            for idx, site_id in enumerate(site_uuids):
                db.add(
                    SiteAssignment(
                        organization_id=inv.organization_id,
                        membership_id=membership.id,
                        site_id=site_id,
                        assignment_type=AssignmentType.PRIMARY.value if idx == 0 else AssignmentType.SECONDARY.value,
                        status=AssignmentStatus.PENDING.value,
                        assigned_by_user_id=inv.invited_by_user_id,
                    )
                )

            for role_code in inv.requested_role_codes:
                role = self._validate_tenant_role(db, role_code)
                role_def = ROLE_DEFINITIONS_BY_CODE.get(role.role_code)
                scopes = role_def.allowed_assignment_scopes if role_def else frozenset({"SITE"})
                if site_uuids and "SITE" in scopes:
                    for sid in site_uuids:
                        db.add(
                            MembershipRoleAssignment(
                                organization_id=inv.organization_id,
                                membership_id=membership.id,
                                role_id=role.id,
                                scope_type=RoleAssignmentScopeType.SITE.value,
                                site_id=sid,
                                granted_by_user_id=inv.invited_by_user_id,
                                status=RoleAssignmentStatus.PENDING.value,
                            )
                        )
                elif "ORGANIZATION" in scopes:
                    db.add(
                        MembershipRoleAssignment(
                            organization_id=inv.organization_id,
                            membership_id=membership.id,
                            role_id=role.id,
                            scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
                            site_id=None,
                            granted_by_user_id=inv.invited_by_user_id,
                            status=RoleAssignmentStatus.PENDING.value,
                        )
                    )
                else:
                    raise ValidationError(
                        f"Role '{role.role_code}' requires valid site_ids for SITE scope assignment.",
                        error_code="SITE_ID_REQUIRED_FOR_ROLE",
                    )

            self._audit.record(
                db,
                organization_id=inv.organization_id,
                actor_type=ActorType.USER.value,
                actor_id=user.id,
                action="INVITATION_ACCEPTED",
                entity_type="organization_memberships",
                entity_id=membership.id,
                after_state={"invitation_id": str(inv.id), "membership_status": membership.membership_status},
            )

        return self.get_membership_response(db, inv.organization_id, membership.id)

    def revoke_invitation(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        invitation_id: uuid.UUID,
        tenant_ctx: TenantContext,
        reason: str,
    ) -> InvitationResponse:
        inv = self._identity_repo.get_invitation_for_org(db, organization_id, invitation_id)
        if inv is None:
            raise NotFoundError("Invitation not found.")
        if inv.status != InvitationStatus.PENDING.value:
            raise ConflictError("Only PENDING invitations can be revoked.")
        with UnitOfWork(db):
            inv.status = InvitationStatus.REVOKED.value
            self._audit.record(
                db,
                organization_id=organization_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="INVITATION_REVOKED",
                entity_type="invitations",
                entity_id=inv.id,
                reason=reason,
            )
        return InvitationResponse.model_validate(inv)

    def _enforce_quality_role_governance(
        self,
        db: Session,
        *,
        organization: Organization,
        approver_user_id: uuid.UUID,
        approver_role_codes: set[str],
        approver_platform_role: Optional[str],
        target_user_id: uuid.UUID,
        role_codes_being_approved: List[str],
        target_resource: str,
    ) -> bool:
        """
        Enforces Maker-Checker separation AND Two-Phase QMS_HEAD Bootstrap Rule.
        Returns True when this operation performs a QMS_HEAD bootstrap.
        """
        if approver_user_id == target_user_id:
            self._audit.record_security_event(
                event_type="SELF_APPROVAL_BLOCKED",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=organization.id,
                user_id=approver_user_id,
                target_resource=target_resource,
            )
            raise PermissionDeniedError("Approver cannot approve their own membership or role assignment.")

        quality_roles = [code for code in role_codes_being_approved if code in QUALITY_SENSITIVE_ROLES]
        if not quality_roles:
            return False

        active_qms_head_count = self._org_repo.count_active_role_holders(db, organization.id, "QMS_HEAD")
        if active_qms_head_count == 0:
            if organization.status != OrganizationStatus.ACTIVE.value:
                raise ConflictError("Organization must be ACTIVE to bootstrap the initial QMS_HEAD.")
            if quality_roles != ["QMS_HEAD"] and "QMS_HEAD" not in quality_roles:
                raise PermissionDeniedError(
                    "An active QMS_HEAD must be bootstrapped before approving other quality-sensitive roles."
                )
            if "QMS_HEAD" in quality_roles and len(set(quality_roles)) > 1:
                raise PermissionDeniedError(
                    "Bootstrap may approve the initial QMS_HEAD first; additional quality roles require that QMS_HEAD's approval."
                )
            is_org_owner = "ORG_OWNER" in approver_role_codes
            is_platform_admin = approver_platform_role == "PLATFORM_SUPER_ADMIN"
            if not (is_org_owner or is_platform_admin):
                self._audit.record_security_event(
                    event_type="QMS_HEAD_BOOTSTRAP_UNAUTHORIZED",
                    outcome=SecurityEventOutcome.DENIED.value,
                    severity=SecurityEventSeverity.HIGH.value,
                    organization_id=organization.id,
                    user_id=approver_user_id,
                    target_resource=target_resource,
                )
                raise PermissionDeniedError("Only ORG_OWNER or PLATFORM_SUPER_ADMIN may bootstrap the initial QMS_HEAD.")
            return True

        # Active QMS_HEAD already exists: strictly require an active QMS_HEAD approver
        if "QMS_HEAD" not in approver_role_codes:
            self._audit.record_security_event(
                event_type="QUALITY_ROLE_APPROVAL_REQUIRES_QMS_HEAD",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=organization.id,
                user_id=approver_user_id,
                target_resource=target_resource,
                details_json={"quality_roles": quality_roles},
            )
            raise PermissionDeniedError(
                "Approving quality-sensitive roles requires an active QMS_HEAD approver once bootstrapped."
            )
        return False

    def approve_membership(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        tenant_ctx: TenantContext,
        principal: Principal,
        payload: MembershipApproveRequest,
    ) -> MembershipResponse:
        org = self._org_repo.get_for_update(db, organization_id)
        if org is None:
            raise NotFoundError("Organization not found.")

        membership = self._identity_repo.get_membership_for_org(db, organization_id, membership_id)
        if membership is None:
            raise NotFoundError("Organization membership not found.")
        if membership.membership_status not in {
            MembershipStatus.PENDING_APPROVAL.value,
            MembershipStatus.ACTIVE.value,
        }:
            raise ConflictError(f"Membership in status '{membership.membership_status}' cannot be approved.")

        pending_roles = [
            ra
            for ra in self._identity_repo.list_role_assignments_for_membership(db, organization_id, membership.id)
            if ra.status == RoleAssignmentStatus.PENDING.value
        ]
        role_codes: List[str] = []
        for ra in pending_roles:
            role_obj = db.get(Role, ra.role_id)
            if role_obj is not None:
                role_codes.append(role_obj.role_code)

        bootstrapped = self._enforce_quality_role_governance(
            db,
            organization=org,
            approver_user_id=principal.user_id,
            approver_role_codes=set(tenant_ctx.role_codes),
            approver_platform_role=principal.platform_role,
            target_user_id=membership.user_id,
            role_codes_being_approved=role_codes if payload.approve_pending_roles else [],
            target_resource="/api/v1/memberships/{id}/approve",
        )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            if payload.row_version is not None:
                verify_and_increment_version(membership, payload.row_version)
            membership.membership_status = MembershipStatus.ACTIVE.value
            membership.approved_at = now
            membership.approved_by_user_id = principal.user_id

            if payload.approve_pending_sites:
                for sa in self._identity_repo.list_site_assignments_for_membership(db, organization_id, membership.id):
                    if sa.status == AssignmentStatus.PENDING.value:
                        sa.status = AssignmentStatus.ACTIVE.value

            if payload.approve_pending_roles:
                for ra in pending_roles:
                    ra.status = RoleAssignmentStatus.ACTIVE.value
                    ra.approved_by_user_id = principal.user_id

            if tenant_ctx.membership_id is not None:
                db.add(
                    ApprovalRecord(
                        organization_id=organization_id,
                        entity_type="organization_memberships",
                        entity_id=membership.id,
                        approval_stage="MEMBERSHIP_APPROVAL",
                        decision=ApprovalDecision.APPROVED.value,
                        approver_membership_id=tenant_ctx.membership_id,
                        decided_at=now,
                        comments=payload.reason,
                    )
                )

            self._audit.record(
                db,
                organization_id=organization_id,
                actor_type=ActorType.USER.value,
                actor_id=principal.user_id,
                action="QMS_HEAD_BOOTSTRAPPED" if bootstrapped else "MEMBERSHIP_APPROVED",
                entity_type="organization_memberships",
                entity_id=membership.id,
                reason=payload.reason,
                after_state={"role_codes": role_codes, "bootstrapped": bootstrapped},
            )

        return self.get_membership_response(db, organization_id, membership.id)

    def revoke_membership(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        principal: Principal,
        reason: str,
        row_version: Optional[int] = None,
    ) -> MembershipResponse:
        membership = self._identity_repo.get_membership_for_org(db, organization_id, membership_id)
        if membership is None:
            raise NotFoundError("Membership not found.")
        if membership.membership_status == MembershipStatus.REVOKED.value:
            raise ConflictError("Membership is already revoked.")

        with UnitOfWork(db):
            if row_version is not None:
                verify_and_increment_version(membership, row_version)
            membership.membership_status = MembershipStatus.REVOKED.value
            for ra in self._identity_repo.list_role_assignments_for_membership(db, organization_id, membership.id):
                if ra.status == RoleAssignmentStatus.ACTIVE.value:
                    ra.status = RoleAssignmentStatus.REVOKED.value
            self._audit.record(
                db,
                organization_id=organization_id,
                actor_type=ActorType.USER.value,
                actor_id=principal.user_id,
                action="MEMBERSHIP_REVOKED",
                entity_type="organization_memberships",
                entity_id=membership.id,
                reason=reason,
            )
        return self.get_membership_response(db, organization_id, membership.id)

    def assign_site(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        principal: Principal,
        payload: SiteAssignmentCreateRequest,
    ) -> SiteAssignmentResponse:
        membership = self._identity_repo.get_membership_for_org(db, organization_id, membership_id)
        if membership is None:
            raise NotFoundError("Membership not found.")
        if self._org_repo.get_site_for_org(db, organization_id, payload.site_id) is None:
            raise NotFoundError("Site not found in this organization.")

        with UnitOfWork(db):
            sa = SiteAssignment(
                organization_id=organization_id,
                membership_id=membership.id,
                site_id=payload.site_id,
                assignment_type=payload.assignment_type,
                valid_from=payload.valid_from,
                valid_to=payload.valid_to,
                status=AssignmentStatus.ACTIVE.value,
                assigned_by_user_id=principal.user_id,
            )
            db.add(sa)
            db.flush()
            self._audit.record(
                db,
                organization_id=organization_id,
                site_id=payload.site_id,
                actor_type=ActorType.USER.value,
                actor_id=principal.user_id,
                action="SITE_ASSIGNMENT_GRANTED",
                entity_type="site_assignments",
                entity_id=sa.id,
                reason=payload.reason,
            )
        return SiteAssignmentResponse.model_validate(sa)

    def assign_role(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        tenant_ctx: TenantContext,
        principal: Principal,
        payload: RoleAssignmentCreateRequest,
    ) -> RoleAssignmentResponse:
        org = self._org_repo.get_for_update(db, organization_id)
        if org is None:
            raise NotFoundError("Organization not found.")
        membership = self._identity_repo.get_membership_for_org(db, organization_id, membership_id)
        if membership is None:
            raise NotFoundError("Membership not found.")

        if payload.scope_type == RoleAssignmentScopeType.SITE.value:
            if payload.site_id is None:
                raise ValidationError("site_id is required when scope_type is SITE.")
            if self._org_repo.get_site_for_org(db, organization_id, payload.site_id) is None:
                raise NotFoundError("Site not found in this organization.")
        elif payload.scope_type == RoleAssignmentScopeType.ORGANIZATION.value:
            if payload.site_id is not None:
                raise ValidationError("site_id must be omitted when scope_type is ORGANIZATION.")
        else:
            raise ValidationError("scope_type must be ORGANIZATION or SITE.")

        role = self._validate_tenant_role(db, payload.role_code, scope_type=payload.scope_type)
        bootstrapped = self._enforce_quality_role_governance(
            db,
            organization=org,
            approver_user_id=principal.user_id,
            approver_role_codes=set(tenant_ctx.role_codes),
            approver_platform_role=principal.platform_role,
            target_user_id=membership.user_id,
            role_codes_being_approved=[role.role_code],
            target_resource="/api/v1/memberships/{id}/roles",
        )

        with UnitOfWork(db):
            assignment = MembershipRoleAssignment(
                organization_id=organization_id,
                membership_id=membership.id,
                role_id=role.id,
                scope_type=payload.scope_type,
                site_id=payload.site_id,
                valid_from=payload.valid_from,
                valid_to=payload.valid_to,
                granted_by_user_id=principal.user_id,
                approved_by_user_id=principal.user_id,
                status=RoleAssignmentStatus.ACTIVE.value,
            )
            db.add(assignment)
            db.flush()
            self._audit.record(
                db,
                organization_id=organization_id,
                site_id=payload.site_id,
                actor_type=ActorType.USER.value,
                actor_id=principal.user_id,
                action="QMS_HEAD_BOOTSTRAPPED" if bootstrapped else "ROLE_ASSIGNED",
                entity_type="membership_role_assignments",
                entity_id=assignment.id,
                reason=payload.reason,
                after_state={"role_code": role.role_code, "scope_type": payload.scope_type},
            )

        resp = RoleAssignmentResponse.model_validate(assignment)
        resp.role_code = role.role_code
        return resp

    def revoke_role_assignment(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        assignment_id: uuid.UUID,
        principal: Principal,
        reason: str,
    ) -> RoleAssignmentResponse:
        assignment = self._identity_repo.get_role_assignment_for_org(
            db, organization_id, membership_id, assignment_id
        )
        if assignment is None:
            raise NotFoundError("Role assignment not found.")
        with UnitOfWork(db):
            assignment.status = RoleAssignmentStatus.REVOKED.value
            self._audit.record(
                db,
                organization_id=organization_id,
                site_id=assignment.site_id,
                actor_type=ActorType.USER.value,
                actor_id=principal.user_id,
                action="ROLE_REVOKED",
                entity_type="membership_role_assignments",
                entity_id=assignment.id,
                reason=reason,
            )
        role_obj = db.get(Role, assignment.role_id)
        resp = RoleAssignmentResponse.model_validate(assignment)
        resp.role_code = role_obj.role_code if role_obj else None
        return resp

    def list_memberships(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[MembershipResponse], int]:
        rows, total = self._identity_repo.list_org_memberships(
            db, organization_id, status=status, page=page, page_size=page_size
        )
        return [self.get_membership_response(db, organization_id, row.id) for row in rows], total

    def get_membership_response(
        self, db: Session, organization_id: uuid.UUID, membership_id: uuid.UUID
    ) -> MembershipResponse:
        m = self._identity_repo.get_membership_for_org(db, organization_id, membership_id)
        if m is None:
            raise NotFoundError("Membership not found.")
        emp = self._identity_repo.get_employee_profile_by_membership(db, organization_id, m.id)
        ras = self._identity_repo.list_role_assignments_for_membership(db, organization_id, m.id)
        sas = self._identity_repo.list_site_assignments_for_membership(db, organization_id, m.id)

        ra_dtos: List[RoleAssignmentResponse] = []
        for ra in ras:
            role_obj = db.get(Role, ra.role_id)
            dto = RoleAssignmentResponse.model_validate(ra)
            dto.role_code = role_obj.role_code if role_obj else None
            ra_dtos.append(dto)

        resp = MembershipResponse.model_validate(m)
        resp.employee_profile_id = emp.id if emp else None
        resp.role_assignments = ra_dtos
        resp.site_assignments = [SiteAssignmentResponse.model_validate(sa) for sa in sas]
        return resp

    def add_qualification(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        employee_profile_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: EmployeeQualificationCreateRequest,
    ) -> EmployeeQualification:
        emp = self._identity_repo.get_employee_profile_for_org(db, organization_id, employee_profile_id)
        if emp is None:
            raise NotFoundError("Employee profile not found.")
        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            qual = EmployeeQualification(
                organization_id=organization_id,
                membership_id=emp.membership_id,
                employee_profile_id=emp.id,
                qualification_type=payload.qualification_type,
                qualification_name=payload.qualification_name,
                issuing_body=payload.issuing_body,
                certificate_number=payload.certificate_number,
                issued_on=payload.issued_on,
                expires_on=payload.expires_on,
                document_id=payload.document_id,
                verification_status=payload.verification_status,
                verified_by_user_id=tenant_ctx.user_id if payload.verification_status == "VERIFIED" else None,
                verified_at=now if payload.verification_status == "VERIFIED" else None,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            self._identity_repo.create_qualification(db, qual)
            self._audit.record(
                db,
                organization_id=organization_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="EMPLOYEE_QUALIFICATION_ADDED",
                entity_type="employee_qualifications",
                entity_id=qual.id,
            )
        db.refresh(qual)
        return qual

    def add_training_record(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        employee_profile_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: TrainingRecordCreateRequest,
    ) -> TrainingRecord:
        emp = self._identity_repo.get_employee_profile_for_org(db, organization_id, employee_profile_id)
        if emp is None:
            raise NotFoundError("Employee profile not found.")
        if payload.site_id is not None:
            if self._org_repo.get_site_for_org(db, organization_id, payload.site_id) is None:
                raise NotFoundError("Site not found in this organization.")
        with UnitOfWork(db):
            rec = TrainingRecord(
                organization_id=organization_id,
                membership_id=emp.membership_id,
                employee_profile_id=emp.id,
                site_id=payload.site_id,
                training_topic=payload.training_topic,
                training_method=payload.training_method,
                sop_revision_id=payload.sop_revision_id,
                trainer_membership_id=payload.trainer_membership_id,
                completed_on=payload.completed_on,
                due_on=payload.due_on,
                valid_until=payload.valid_until,
                assessment_score=payload.assessment_score,
                completion_status=payload.completion_status,
                evidence_document_id=payload.evidence_document_id,
                notes=payload.notes,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            self._identity_repo.create_training_record(db, rec)
            self._audit.record(
                db,
                organization_id=organization_id,
                site_id=payload.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="TRAINING_RECORD_CREATED",
                entity_type="training_records",
                entity_id=rec.id,
            )
        db.refresh(rec)
        return rec
