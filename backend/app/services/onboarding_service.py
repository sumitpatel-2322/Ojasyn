import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.core.database import UnitOfWork, verify_and_increment_version
from app.core.deps import Principal, TenantContext
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.domain.enums import (
    ActorType,
    ApprovalDecision,
    MemberType,
    MembershipStatus,
    OrganizationStatus,
    RoleAssignmentScopeType,
    RoleAssignmentStatus,
    SiteStatus,
)
from app.domain.state_machines import assert_transition
from app.models.domain.governance import ApprovalRecord
from app.models.domain.identity import MembershipRoleAssignment, OrganizationMembership
from app.models.domain.tenancy import (
    Department,
    Organization,
    OrganizationAddress,
    OrganizationLicense,
    Site,
    SiteLicense,
)
from app.models.schemas.auth_onboarding import (
    AddressCreateInput,
    DepartmentCreateRequest,
    LicenseCreateRequest,
    OnboardingOrganizationCreateRequest,
    OrganizationUpdateRequest,
    ReviewDecisionRequest,
    SiteCreateRequest,
    SiteUpdateRequest,
)
from app.repositories.governance_repo import OutboxRepository
from app.repositories.identity_repo import IdentityRepository
from app.repositories.organization_repo import OrganizationRepository
from app.services.audit_service import AuditService

ORG_TRANSITIONS: Dict[str, Set[str]] = {
    OrganizationStatus.DRAFT.value: {OrganizationStatus.SUBMITTED.value},
    OrganizationStatus.SUBMITTED.value: {
        OrganizationStatus.UNDER_REVIEW.value,
        OrganizationStatus.ACTIVE.value,
        OrganizationStatus.NEEDS_INFORMATION.value,
        OrganizationStatus.REJECTED.value,
    },
    OrganizationStatus.UNDER_REVIEW.value: {
        OrganizationStatus.ACTIVE.value,
        OrganizationStatus.NEEDS_INFORMATION.value,
        OrganizationStatus.REJECTED.value,
    },
    OrganizationStatus.NEEDS_INFORMATION.value: {OrganizationStatus.SUBMITTED.value},
    OrganizationStatus.ACTIVE.value: {OrganizationStatus.SUSPENDED.value},
    OrganizationStatus.SUSPENDED.value: {OrganizationStatus.ACTIVE.value, OrganizationStatus.CLOSED.value},
    OrganizationStatus.REJECTED.value: set(),
    OrganizationStatus.CLOSED.value: set(),
}

SITE_TRANSITIONS: Dict[str, Set[str]] = {
    SiteStatus.DRAFT.value: {SiteStatus.SUBMITTED.value},
    SiteStatus.SUBMITTED.value: {
        SiteStatus.PENDING_VERIFICATION.value,
        SiteStatus.ACTIVE.value,
        SiteStatus.NEEDS_INFORMATION.value,
        SiteStatus.INACTIVE.value,
    },
    SiteStatus.PENDING_VERIFICATION.value: {
        SiteStatus.ACTIVE.value,
        SiteStatus.NEEDS_INFORMATION.value,
        SiteStatus.INACTIVE.value,
    },
    SiteStatus.NEEDS_INFORMATION.value: {SiteStatus.SUBMITTED.value},
    SiteStatus.ACTIVE.value: {SiteStatus.INACTIVE.value, SiteStatus.SUSPENDED.value},
    SiteStatus.SUSPENDED.value: {SiteStatus.ACTIVE.value, SiteStatus.INACTIVE.value},
    SiteStatus.INACTIVE.value: {SiteStatus.ACTIVE.value},
}


class OnboardingService:
    """Organization, address, regulatory license, site, and department onboarding lifecycle service."""

    def __init__(
        self,
        org_repo: Optional[OrganizationRepository] = None,
        identity_repo: Optional[IdentityRepository] = None,
        audit_service: Optional[AuditService] = None,
        outbox_repo: Optional[OutboxRepository] = None,
    ) -> None:
        self._org_repo = org_repo or OrganizationRepository()
        self._identity_repo = identity_repo or IdentityRepository()
        self._audit = audit_service or AuditService()
        self._outbox = outbox_repo or OutboxRepository()

    def create_organization_onboarding(
        self,
        db: Session,
        *,
        principal: Principal,
        payload: OnboardingOrganizationCreateRequest,
    ) -> Organization:
        org_owner_role = self._identity_repo.get_role_by_code(db, "ORG_OWNER")
        if org_owner_role is None:
            raise ValidationError("RBAC catalog must be seeded before organization onboarding.")

        now = datetime.now(timezone.utc)
        addr_in = payload.registered_address

        with UnitOfWork(db):
            org = Organization(
                legal_name=payload.legal_name.strip(),
                trade_name=payload.trade_name,
                organization_type=payload.organization_type,
                registration_number=payload.registration_number,
                cin=payload.cin,
                pan=payload.pan,
                gstin=payload.gstin,
                country_of_registration=payload.country_of_registration.upper(),
                incorporation_date=payload.incorporation_date,
                website_url=payload.website_url,
                business_email=str(payload.business_email).strip().lower(),
                business_phone=payload.business_phone,
                timezone=payload.timezone,
                status=OrganizationStatus.DRAFT.value,
            )
            self._org_repo.create_organization(db, org)

            addr = OrganizationAddress(
                organization_id=org.id,
                address_type=addr_in.address_type,
                line_1=addr_in.line_1,
                line_2=addr_in.line_2,
                locality=addr_in.locality,
                city=addr_in.city,
                state_region=addr_in.state_region,
                postal_code=addr_in.postal_code,
                country_code=addr_in.country_code.upper(),
                is_primary=addr_in.is_primary,
                valid_from=addr_in.valid_from,
                valid_to=addr_in.valid_to,
            )
            self._org_repo.create_address(db, addr)
            org.registered_address_id = addr.id

            membership = OrganizationMembership(
                user_id=principal.user_id,
                organization_id=org.id,
                member_type=MemberType.EMPLOYEE.value,
                membership_status=MembershipStatus.ACTIVE.value,
                designation="Organization Owner",
                requested_at=now,
                approved_at=now,
                approved_by_user_id=principal.user_id,
            )
            db.add(membership)
            db.flush()

            db.add(
                MembershipRoleAssignment(
                    organization_id=org.id,
                    membership_id=membership.id,
                    role_id=org_owner_role.id,
                    scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
                    site_id=None,
                    granted_by_user_id=principal.user_id,
                    approved_by_user_id=principal.user_id,
                    status=RoleAssignmentStatus.ACTIVE.value,
                )
            )

            db.add(
                ApprovalRecord(
                    organization_id=org.id,
                    entity_type="organizations",
                    entity_id=org.id,
                    approval_stage="ONBOARDING_INITIATED",
                    decision=ApprovalDecision.APPROVED.value,
                    approver_membership_id=membership.id,
                    decided_at=now,
                    comments="Initial organization draft and ORG_OWNER assignment",
                )
            )
            self._audit.record(
                db,
                organization_id=org.id,
                actor_type=ActorType.USER.value,
                actor_id=principal.user_id,
                action="ORG_CREATED_DRAFT",
                entity_type="organizations",
                entity_id=org.id,
                after_state={"legal_name": org.legal_name, "status": org.status},
            )
            self._outbox.enqueue(
                db,
                organization_id=org.id,
                aggregate_type="organization",
                aggregate_id=org.id,
                event_type="OrganizationDraftCreated",
                payload_json={"organization_id": str(org.id), "legal_name": org.legal_name},
            )

        db.refresh(org)
        return org

    def get_organization(self, db: Session, organization_id: uuid.UUID) -> Organization:
        org = self._org_repo.get_by_id(db, organization_id)
        if org is None:
            raise NotFoundError("Organization not found.")
        return org

    def update_organization(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: OrganizationUpdateRequest,
    ) -> Organization:
        org = self._org_repo.get_by_id(db, organization_id)
        if org is None:
            raise NotFoundError("Organization not found.")
        if org.status not in {OrganizationStatus.DRAFT.value, OrganizationStatus.NEEDS_INFORMATION.value, OrganizationStatus.ACTIVE.value}:
            raise ConflictError("Organization cannot be edited in its current lifecycle status.")

        with UnitOfWork(db):
            verify_and_increment_version(org, payload.row_version)
            for field in (
                "legal_name",
                "trade_name",
                "registration_number",
                "cin",
                "pan",
                "gstin",
                "website_url",
                "business_phone",
                "timezone",
            ):
                val = getattr(payload, field)
                if val is not None:
                    setattr(org, field, val)
            self._audit.record(
                db,
                organization_id=org.id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="ORG_UPDATED",
                entity_type="organizations",
                entity_id=org.id,
                reason=payload.reason,
                after_state={"legal_name": org.legal_name, "row_version": org.row_version},
            )
        db.refresh(org)
        return org

    def add_organization_address(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: AddressCreateInput,
    ) -> OrganizationAddress:
        with UnitOfWork(db):
            addr = OrganizationAddress(
                organization_id=organization_id,
                address_type=payload.address_type,
                line_1=payload.line_1,
                line_2=payload.line_2,
                locality=payload.locality,
                city=payload.city,
                state_region=payload.state_region,
                postal_code=payload.postal_code,
                country_code=payload.country_code.upper(),
                is_primary=payload.is_primary,
                valid_from=payload.valid_from,
                valid_to=payload.valid_to,
            )
            self._org_repo.create_address(db, addr)
            self._audit.record(
                db,
                organization_id=organization_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="ORG_ADDRESS_ADDED",
                entity_type="organization_addresses",
                entity_id=addr.id,
            )
        db.refresh(addr)
        return addr

    def list_organization_addresses(self, db: Session, organization_id: uuid.UUID) -> List[OrganizationAddress]:
        return self._org_repo.list_addresses(db, organization_id)

    def add_organization_license(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: LicenseCreateRequest,
    ) -> OrganizationLicense:
        with UnitOfWork(db):
            lic = OrganizationLicense(
                organization_id=organization_id,
                license_type=payload.license_type,
                license_number=payload.license_number,
                issuing_authority=payload.issuing_authority,
                valid_from=payload.valid_from,
                valid_to=payload.valid_to,
                licence_document_id=payload.licence_document_id,
                metadata_json=payload.metadata_json,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            self._org_repo.create_org_license(db, lic)
            self._audit.record(
                db,
                organization_id=organization_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="ORG_LICENSE_ADDED",
                entity_type="organization_licenses",
                entity_id=lic.id,
            )
        db.refresh(lic)
        return lic

    def list_organization_licenses(self, db: Session, organization_id: uuid.UUID) -> List[OrganizationLicense]:
        return self._org_repo.list_org_licenses(db, organization_id)

    def submit_organization(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        tenant_ctx: TenantContext,
        row_version: int,
        reason: Optional[str] = None,
    ) -> Organization:
        org = self._org_repo.get_by_id(db, organization_id)
        if org is None:
            raise NotFoundError("Organization not found.")

        assert_transition(
            ORG_TRANSITIONS,
            org.status,
            OrganizationStatus.SUBMITTED.value,
            entity_name="Organization",
        )
        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            verify_and_increment_version(org, row_version)
            prev_status = org.status
            org.status = OrganizationStatus.SUBMITTED.value
            org.onboarding_submitted_at = now
            self._audit.record(
                db,
                organization_id=org.id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="ORG_ONBOARDING_SUBMITTED",
                entity_type="organizations",
                entity_id=org.id,
                reason=reason,
                before_state={"status": prev_status},
                after_state={"status": org.status},
            )
            self._outbox.enqueue(
                db,
                organization_id=org.id,
                aggregate_type="organization",
                aggregate_id=org.id,
                event_type="OrganizationSubmittedForReview",
                payload_json={"organization_id": str(org.id)},
            )
        db.refresh(org)
        return org

    def review_organization(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        reviewer: Principal,
        payload: ReviewDecisionRequest,
    ) -> Organization:
        org = self._org_repo.get_for_update(db, organization_id)
        if org is None:
            raise NotFoundError("Organization not found.")

        assert_transition(ORG_TRANSITIONS, org.status, payload.decision, entity_name="Organization")
        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            verify_and_increment_version(org, payload.row_version)
            prev_status = org.status
            org.status = payload.decision
            if payload.decision == OrganizationStatus.ACTIVE.value:
                org.verified_at = now
                org.verified_by_user_id = reviewer.user_id
            self._audit.record(
                db,
                organization_id=org.id,
                actor_type=ActorType.USER.value,
                actor_id=reviewer.user_id,
                action=f"ORG_REVIEW_{payload.decision}",
                entity_type="organizations",
                entity_id=org.id,
                reason=payload.comments,
                before_state={"status": prev_status},
                after_state={"status": org.status},
            )
            self._outbox.enqueue(
                db,
                organization_id=org.id,
                aggregate_type="organization",
                aggregate_id=org.id,
                event_type="OrganizationReviewed",
                payload_json={"organization_id": str(org.id), "status": org.status},
            )
        db.refresh(org)
        return org

    def create_site(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: SiteCreateRequest,
    ) -> Site:
        address = self._org_repo.get_address_for_org(db, organization_id, payload.address_id)
        if address is None:
            raise NotFoundError("Organization address not found for this organization.")
        if self._org_repo.get_site_by_code(db, organization_id, payload.site_code) is not None:
            raise ConflictError("Site code already exists in this organization.", error_code="SITE_CODE_EXISTS")

        with UnitOfWork(db):
            site = Site(
                organization_id=organization_id,
                site_code=payload.site_code.strip().upper(),
                site_name=payload.site_name.strip(),
                site_type=payload.site_type,
                operations_scope=payload.operations_scope,
                address_id=address.id,
                primary_contact_user_id=payload.primary_contact_user_id,
                timezone=payload.timezone,
                status=SiteStatus.DRAFT.value,
                is_primary_site=payload.is_primary_site,
                effective_from=payload.effective_from,
                effective_to=payload.effective_to,
            )
            self._org_repo.create_site(db, site)
            self._audit.record(
                db,
                organization_id=organization_id,
                site_id=site.id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="SITE_CREATED_DRAFT",
                entity_type="sites",
                entity_id=site.id,
                after_state={"site_code": site.site_code, "status": site.status},
            )
        db.refresh(site)
        return site

    def list_sites(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        tenant_ctx: TenantContext,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Site], int]:
        return self._org_repo.list_sites(
            db, organization_id, tenant_ctx=tenant_ctx, page=page, page_size=page_size
        )

    def update_site(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        site_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: SiteUpdateRequest,
    ) -> Site:
        site = self._org_repo.get_site_for_org(db, organization_id, site_id)
        if site is None:
            raise NotFoundError("Site not found.")
        if not tenant_ctx.is_org_wide and site.id not in tenant_ctx.allowed_site_ids:
            raise PermissionDeniedError("Not authorized to manage this site.")

        with UnitOfWork(db):
            verify_and_increment_version(site, payload.row_version)
            for field in ("site_name", "site_type", "operations_scope", "primary_contact_user_id", "timezone"):
                val = getattr(payload, field)
                if val is not None:
                    setattr(site, field, val)
            self._audit.record(
                db,
                organization_id=organization_id,
                site_id=site.id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="SITE_UPDATED",
                entity_type="sites",
                entity_id=site.id,
                reason=payload.reason,
            )
        db.refresh(site)
        return site

    def add_site_license(
        self,
        db: Session,
        *,
        site_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: LicenseCreateRequest,
    ) -> SiteLicense:
        site = self._org_repo.get_site_for_org(db, tenant_ctx.organization_id, site_id)
        if site is None:
            raise NotFoundError("Site not found.")
        if not tenant_ctx.is_org_wide and site.id not in tenant_ctx.allowed_site_ids:
            raise PermissionDeniedError("Not authorized to manage this site.")

        with UnitOfWork(db):
            lic = SiteLicense(
                organization_id=site.organization_id,
                site_id=site.id,
                license_type=payload.license_type,
                license_number=payload.license_number,
                issuing_authority=payload.issuing_authority,
                scope_description=payload.scope_description,
                valid_from=payload.valid_from,
                valid_to=payload.valid_to,
                licence_document_id=payload.licence_document_id,
                metadata_json=payload.metadata_json,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            self._org_repo.create_site_license(db, lic)
            self._audit.record(
                db,
                organization_id=site.organization_id,
                site_id=site.id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="SITE_LICENSE_ADDED",
                entity_type="site_licenses",
                entity_id=lic.id,
            )
        db.refresh(lic)
        return lic

    def submit_site(
        self,
        db: Session,
        *,
        site_id: uuid.UUID,
        tenant_ctx: TenantContext,
        row_version: int,
        reason: Optional[str] = None,
    ) -> Site:
        site = self._org_repo.get_site_for_org(db, tenant_ctx.organization_id, site_id)
        if site is None:
            raise NotFoundError("Site not found.")
        if not tenant_ctx.is_org_wide and site.id not in tenant_ctx.allowed_site_ids:
            raise PermissionDeniedError("Not authorized to manage this site.")
        assert_transition(SITE_TRANSITIONS, site.status, SiteStatus.SUBMITTED.value, entity_name="Site")
        with UnitOfWork(db):
            verify_and_increment_version(site, row_version)
            prev = site.status
            site.status = SiteStatus.SUBMITTED.value
            self._audit.record(
                db,
                organization_id=site.organization_id,
                site_id=site.id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="SITE_SUBMITTED",
                entity_type="sites",
                entity_id=site.id,
                reason=reason,
                before_state={"status": prev},
                after_state={"status": site.status},
            )
        db.refresh(site)
        return site

    def review_site(
        self,
        db: Session,
        *,
        site_id: uuid.UUID,
        reviewer: Principal,
        payload: ReviewDecisionRequest,
    ) -> Site:
        site = self._org_repo.get_site_by_id(db, site_id)
        if site is None:
            raise NotFoundError("Site not found.")
        assert_transition(SITE_TRANSITIONS, site.status, payload.decision, entity_name="Site")
        with UnitOfWork(db):
            verify_and_increment_version(site, payload.row_version)
            prev = site.status
            site.status = payload.decision
            self._audit.record(
                db,
                organization_id=site.organization_id,
                site_id=site.id,
                actor_type=ActorType.USER.value,
                actor_id=reviewer.user_id,
                action=f"SITE_REVIEW_{payload.decision}",
                entity_type="sites",
                entity_id=site.id,
                reason=payload.comments,
                before_state={"status": prev},
                after_state={"status": site.status},
            )
            self._outbox.enqueue(
                db,
                organization_id=site.organization_id,
                aggregate_type="site",
                aggregate_id=site.id,
                event_type="SiteReviewed",
                payload_json={"site_id": str(site.id), "status": site.status},
            )
        db.refresh(site)
        return site

    def create_department(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: DepartmentCreateRequest,
    ) -> Department:
        if payload.site_id is not None:
            site = self._org_repo.get_site_for_org(db, organization_id, payload.site_id)
            if site is None:
                raise NotFoundError("Site not found in this organization.")

        with UnitOfWork(db):
            dept = Department(
                organization_id=organization_id,
                site_id=payload.site_id,
                department_code=payload.department_code.strip().upper(),
                department_name=payload.department_name.strip(),
                department_head_membership_id=payload.department_head_membership_id,
                status="ACTIVE",
            )
            self._org_repo.create_department(db, dept)
            self._audit.record(
                db,
                organization_id=organization_id,
                site_id=payload.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="DEPARTMENT_CREATED",
                entity_type="departments",
                entity_id=dept.id,
            )
        db.refresh(dept)
        return dept

    def list_departments(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        tenant_ctx: TenantContext,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[Department], int]:
        return self._org_repo.list_departments(
            db, organization_id, tenant_ctx=tenant_ctx, page=page, page_size=page_size
        )
