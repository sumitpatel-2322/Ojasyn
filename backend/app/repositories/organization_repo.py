import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.domain.identity import MembershipRoleAssignment, OrganizationMembership, Role
from app.models.domain.tenancy import (
    Department,
    Organization,
    OrganizationAddress,
    OrganizationLicense,
    Site,
    SiteLicense,
)
from app.repositories.base import apply_tenant_filter, paginate


class OrganizationRepository:
    """Data access for Organizations, Addresses, Sites, Licenses, and Departments."""

    def get_by_id(self, db: Session, organization_id: uuid.UUID) -> Optional[Organization]:
        return db.get(Organization, organization_id)

    def get_for_update(self, db: Session, organization_id: uuid.UUID) -> Optional[Organization]:
        stmt = select(Organization).where(Organization.id == organization_id).with_for_update()
        return db.scalar(stmt)

    def create_organization(self, db: Session, org: Organization) -> Organization:
        db.add(org)
        db.flush()
        return org

    def create_address(self, db: Session, address: OrganizationAddress) -> OrganizationAddress:
        db.add(address)
        db.flush()
        return address

    def list_addresses(self, db: Session, organization_id: uuid.UUID) -> List[OrganizationAddress]:
        stmt = (
            select(OrganizationAddress)
            .where(OrganizationAddress.organization_id == organization_id)
            .order_by(OrganizationAddress.is_primary.desc(), OrganizationAddress.created_at.asc())
        )
        return list(db.scalars(stmt).all())

    def get_address_for_org(
        self, db: Session, organization_id: uuid.UUID, address_id: uuid.UUID
    ) -> Optional[OrganizationAddress]:
        stmt = select(OrganizationAddress).where(
            OrganizationAddress.organization_id == organization_id,
            OrganizationAddress.id == address_id,
        )
        return db.scalar(stmt)

    def create_org_license(self, db: Session, lic: OrganizationLicense) -> OrganizationLicense:
        db.add(lic)
        db.flush()
        return lic

    def list_org_licenses(self, db: Session, organization_id: uuid.UUID) -> List[OrganizationLicense]:
        stmt = (
            select(OrganizationLicense)
            .where(OrganizationLicense.organization_id == organization_id)
            .order_by(OrganizationLicense.created_at.desc())
        )
        return list(db.scalars(stmt).all())

    def create_site(self, db: Session, site: Site) -> Site:
        db.add(site)
        db.flush()
        return site

    def get_site_by_id(self, db: Session, site_id: uuid.UUID) -> Optional[Site]:
        return db.get(Site, site_id)

    def get_site_for_org(
        self, db: Session, organization_id: uuid.UUID, site_id: uuid.UUID
    ) -> Optional[Site]:
        stmt = select(Site).where(Site.organization_id == organization_id, Site.id == site_id)
        return db.scalar(stmt)

    def get_site_by_code(
        self, db: Session, organization_id: uuid.UUID, site_code: str
    ) -> Optional[Site]:
        stmt = select(Site).where(
            Site.organization_id == organization_id,
            func.lower(Site.site_code) == site_code.strip().lower(),
        )
        return db.scalar(stmt)

    def list_sites(
        self,
        db: Session,
        organization_id: uuid.UUID,
        *,
        tenant_ctx: Optional[object] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Site], int]:
        stmt = select(Site).where(Site.organization_id == organization_id)
        if tenant_ctx is not None and not getattr(tenant_ctx, "is_org_wide", False):
            allowed = list(getattr(tenant_ctx, "allowed_site_ids", ()) or ())
            stmt = stmt.where(Site.id.in_(allowed)) if allowed else stmt.where(False)
        stmt = stmt.order_by(Site.is_primary_site.desc(), Site.created_at.asc())
        return paginate(db, stmt, page=page, page_size=page_size)

    def create_site_license(self, db: Session, lic: SiteLicense) -> SiteLicense:
        db.add(lic)
        db.flush()
        return lic

    def create_department(self, db: Session, dept: Department) -> Department:
        db.add(dept)
        db.flush()
        return dept

    def get_department_for_org(
        self, db: Session, organization_id: uuid.UUID, department_id: uuid.UUID
    ) -> Optional[Department]:
        stmt = select(Department).where(
            Department.organization_id == organization_id,
            Department.id == department_id,
        )
        return db.scalar(stmt)

    def list_departments(
        self,
        db: Session,
        organization_id: uuid.UUID,
        *,
        tenant_ctx: Optional[object] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[Department], int]:
        stmt = select(Department).where(Department.organization_id == organization_id)
        if tenant_ctx is not None:
            stmt = apply_tenant_filter(stmt, Department, tenant_ctx, allow_org_wide_site_null=True)
        stmt = stmt.order_by(Department.department_code.asc())
        return paginate(db, stmt, page=page, page_size=page_size)

    def count_active_role_holders(
        self, db: Session, organization_id: uuid.UUID, role_code: str
    ) -> int:
        stmt = (
            select(func.count(func.distinct(MembershipRoleAssignment.membership_id)))
            .join(Role, Role.id == MembershipRoleAssignment.role_id)
            .join(
                OrganizationMembership,
                OrganizationMembership.id == MembershipRoleAssignment.membership_id,
            )
            .where(
                MembershipRoleAssignment.organization_id == organization_id,
                MembershipRoleAssignment.status == "ACTIVE",
                OrganizationMembership.membership_status == "ACTIVE",
                Role.role_code == role_code,
            )
        )
        return int(db.scalar(stmt) or 0)
