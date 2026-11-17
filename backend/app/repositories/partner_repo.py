import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import MembershipStatus
from app.models.domain.actors import BusinessPartner, PartnerMembership, PartnerSite
from app.repositories.base import paginate


class PartnerRepository:
    """Data access for BusinessPartners, PartnerSites, and PartnerMemberships."""

    def create_partner(self, db: Session, partner: BusinessPartner) -> BusinessPartner:
        db.add(partner)
        db.flush()
        return partner

    def get_partner_for_org(
        self, db: Session, organization_id: uuid.UUID, partner_id: uuid.UUID
    ) -> Optional[BusinessPartner]:
        return db.scalar(
            select(BusinessPartner).where(
                BusinessPartner.organization_id == organization_id,
                BusinessPartner.id == partner_id,
            )
        )

    def get_partner_by_code(
        self, db: Session, organization_id: uuid.UUID, partner_code: str
    ) -> Optional[BusinessPartner]:
        return db.scalar(
            select(BusinessPartner).where(
                BusinessPartner.organization_id == organization_id,
                func.lower(BusinessPartner.partner_code) == partner_code.strip().lower(),
            )
        )

    def list_partners_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        *,
        partner_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[BusinessPartner], int]:
        stmt = select(BusinessPartner).where(BusinessPartner.organization_id == organization_id)
        if partner_type:
            stmt = stmt.where(BusinessPartner.partner_type == partner_type)
        stmt = stmt.order_by(BusinessPartner.legal_name.asc())
        return paginate(db, stmt, page=page, page_size=page_size)

    def create_partner_site(self, db: Session, site: PartnerSite) -> PartnerSite:
        db.add(site)
        db.flush()
        return site

    def get_partner_site_for_org(
        self, db: Session, organization_id: uuid.UUID, partner_id: uuid.UUID, site_id: uuid.UUID
    ) -> Optional[PartnerSite]:
        return db.scalar(
            select(PartnerSite).where(
                PartnerSite.organization_id == organization_id,
                PartnerSite.partner_id == partner_id,
                PartnerSite.id == site_id,
            )
        )

    def create_partner_membership(
        self, db: Session, membership: PartnerMembership
    ) -> PartnerMembership:
        db.add(membership)
        db.flush()
        return membership

    def get_partner_membership_for_org(
        self, db: Session, organization_id: uuid.UUID, membership_id: uuid.UUID
    ) -> Optional[PartnerMembership]:
        return db.scalar(
            select(PartnerMembership).where(
                PartnerMembership.organization_id == organization_id,
                PartnerMembership.id == membership_id,
            )
        )

    def list_user_partner_memberships(
        self, db: Session, user_id: uuid.UUID, *, active_only: bool = True
    ) -> List[PartnerMembership]:
        stmt = select(PartnerMembership).where(PartnerMembership.user_id == user_id)
        if active_only:
            stmt = stmt.where(PartnerMembership.membership_status == MembershipStatus.ACTIVE.value)
        return list(db.scalars(stmt).all())
