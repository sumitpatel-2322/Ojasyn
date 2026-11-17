import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.database import UnitOfWork, verify_and_increment_version
from app.core.deps import TenantContext
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.core.security import hash_token
from app.domain.enums import (
    ActorType,
    ApprovalDecision,
    MembershipStatus,
    RoleScope,
    SecurityEventOutcome,
    SecurityEventSeverity,
)
from app.models.domain.actors import BusinessPartner, PartnerMembership, PartnerSite
from app.models.domain.governance import ApprovalRecord
from app.models.schemas.actors_partners import (
    BusinessPartnerCreateRequest,
    PartnerInvitationCreateRequest,
    PartnerMembershipApproveRequest,
    PartnerMembershipResponse,
    PartnerSiteCreateRequest,
)
from app.repositories.governance_repo import OutboxRepository
from app.repositories.identity_repo import IdentityRepository
from app.repositories.partner_repo import PartnerRepository
from app.services.audit_service import AuditService


class PartnerService:
    """Tenant-scoped BusinessPartner, PartnerSite, invitation, and approval service."""

    def __init__(
        self,
        partner_repo: Optional[PartnerRepository] = None,
        identity_repo: Optional[IdentityRepository] = None,
        audit_service: Optional[AuditService] = None,
        outbox_repo: Optional[OutboxRepository] = None,
    ) -> None:
        self._partner_repo = partner_repo or PartnerRepository()
        self._identity_repo = identity_repo or IdentityRepository()
        self._audit = audit_service or AuditService()
        self._outbox = outbox_repo or OutboxRepository()

    def create_partner(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        payload: BusinessPartnerCreateRequest,
    ) -> BusinessPartner:
        if self._partner_repo.get_partner_by_code(db, tenant_ctx.organization_id, payload.partner_code) is not None:
            raise ConflictError("Partner code already exists in this organization.", error_code="PARTNER_CODE_EXISTS")

        with UnitOfWork(db):
            partner = BusinessPartner(
                organization_id=tenant_ctx.organization_id,
                partner_code=payload.partner_code.strip().upper(),
                legal_name=payload.legal_name.strip(),
                trade_name=payload.trade_name,
                partner_type=payload.partner_type.strip().upper(),
                tax_id=payload.tax_id,
                license_number=payload.license_number,
                primary_email=str(payload.primary_email).strip().lower(),
                primary_phone=payload.primary_phone,
                qualification_status="PENDING",
                status="ACTIVE",
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            self._partner_repo.create_partner(db, partner)
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="PARTNER_CREATED",
                entity_type="business_partners",
                entity_id=partner.id,
                after_state={"partner_code": partner.partner_code, "partner_type": partner.partner_type},
            )
        db.refresh(partner)
        return partner

    def list_partners(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        partner_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[BusinessPartner], int]:
        return self._partner_repo.list_partners_for_org(
            db,
            tenant_ctx.organization_id,
            partner_type=partner_type.strip().upper() if partner_type else None,
            page=page,
            page_size=page_size,
        )

    def create_partner_site(
        self,
        db: Session,
        *,
        partner_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: PartnerSiteCreateRequest,
    ) -> PartnerSite:
        partner = self._partner_repo.get_partner_for_org(db, tenant_ctx.organization_id, partner_id)
        if partner is None:
            raise NotFoundError("Business partner not found.")

        with UnitOfWork(db):
            site = PartnerSite(
                organization_id=tenant_ctx.organization_id,
                partner_id=partner.id,
                site_code=payload.site_code.strip().upper(),
                site_name=payload.site_name.strip(),
                address_line_1=payload.address_line_1,
                city=payload.city,
                state_region=payload.state_region,
                postal_code=payload.postal_code,
                country_code=payload.country_code.upper(),
                drug_license_number=payload.drug_license_number,
                status="ACTIVE",
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            self._partner_repo.create_partner_site(db, site)
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="PARTNER_SITE_CREATED",
                entity_type="partner_sites",
                entity_id=site.id,
            )
        db.refresh(site)
        return site

    def invite_partner_user(
        self,
        db: Session,
        *,
        partner_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: PartnerInvitationCreateRequest,
    ) -> PartnerMembershipResponse:
        partner = self._partner_repo.get_partner_for_org(db, tenant_ctx.organization_id, partner_id)
        if partner is None:
            raise NotFoundError("Business partner not found.")

        if payload.partner_site_id is not None:
            psite = self._partner_repo.get_partner_site_for_org(
                db, tenant_ctx.organization_id, partner.id, payload.partner_site_id
            )
            if psite is None:
                raise NotFoundError("Partner site not found for this partner.")

        role = self._identity_repo.get_role_by_code(db, payload.role_code)
        if role is None or role.status != "ACTIVE":
            raise NotFoundError(f"Role '{payload.role_code}' not found.")
        if role.role_scope != RoleScope.PARTNER.value:
            raise ValidationError("Partner invitations strictly require a role with PARTNER scope.")

        raw_token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=payload.expires_in_hours)

        with UnitOfWork(db):
            pm = PartnerMembership(
                organization_id=tenant_ctx.organization_id,
                partner_id=partner.id,
                partner_site_id=payload.partner_site_id,
                role_id=role.id,
                invited_email=payload.email,
                token_hash=hash_token(raw_token),
                expires_at=expires_at,
                membership_status=MembershipStatus.INVITED.value,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            self._partner_repo.create_partner_membership(db, pm)
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="PARTNER_USER_INVITED",
                entity_type="partner_memberships",
                entity_id=pm.id,
            )
            # Never persist raw invite token in outbox_events
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="partner_membership",
                aggregate_id=pm.id,
                event_type="PartnerUserInvited",
                payload_json={
                    "partner_membership_id": str(pm.id),
                    "partner_id": str(partner.id),
                    "organization_id": str(tenant_ctx.organization_id),
                },
            )

        resp = PartnerMembershipResponse.model_validate(pm)
        resp.invite_token = raw_token
        return resp

    def approve_partner_membership(
        self,
        db: Session,
        *,
        membership_id: uuid.UUID,
        tenant_ctx: TenantContext,
        payload: PartnerMembershipApproveRequest,
    ) -> PartnerMembership:
        pm = self._partner_repo.get_partner_membership_for_org(db, tenant_ctx.organization_id, membership_id)
        if pm is None:
            raise NotFoundError("Partner membership not found.")
        if pm.membership_status not in {MembershipStatus.INVITED.value, MembershipStatus.PENDING_APPROVAL.value}:
            raise ConflictError(f"Partner membership in status '{pm.membership_status}' cannot be approved.")

        target_user_id = payload.user_id or pm.user_id
        if target_user_id is not None and tenant_ctx.user_id == target_user_id:
            self._audit.record_security_event(
                event_type="SELF_APPROVAL_BLOCKED",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                target_resource="/api/v1/partner-memberships/{id}/approve",
            )
            raise PermissionDeniedError("Approver cannot approve their own partner membership.")

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            if payload.row_version is not None:
                verify_and_increment_version(pm, payload.row_version)
            if payload.user_id is not None:
                user = self._identity_repo.get_user_by_id(db, payload.user_id)
                if user is None:
                    raise NotFoundError("Target user not found.")
                pm.user_id = user.id
            pm.membership_status = MembershipStatus.ACTIVE.value
            pm.approved_by_user_id = tenant_ctx.user_id
            pm.approved_at = now
            pm.updated_by_user_id = tenant_ctx.user_id

            if tenant_ctx.membership_id is not None:
                db.add(
                    ApprovalRecord(
                        organization_id=tenant_ctx.organization_id,
                        entity_type="partner_memberships",
                        entity_id=pm.id,
                        approval_stage="PARTNER_MEMBERSHIP_APPROVAL",
                        decision=ApprovalDecision.APPROVED.value,
                        approver_membership_id=tenant_ctx.membership_id,
                        decided_at=now,
                        comments=payload.reason,
                    )
                )
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="PARTNER_MEMBERSHIP_APPROVED",
                entity_type="partner_memberships",
                entity_id=pm.id,
                reason=payload.reason,
            )
        db.refresh(pm)
        return pm
