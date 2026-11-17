import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain.enums import CredentialType, MembershipStatus, RoleAssignmentStatus
from app.models.domain.actors import ChemistProfile, CustomerProfile
from app.models.domain.identity import (
    EmployeeProfile,
    EmployeeQualification,
    Invitation,
    MembershipRoleAssignment,
    OrganizationMembership,
    Permission,
    Role,
    RolePermission,
    SiteAssignment,
    TrainingRecord,
    User,
    UserCredential,
)
from app.repositories.base import paginate


class IdentityRepository:
    """Data access for Users, Credentials, Profiles, Memberships, Invitations, Roles, Qualifications & Training."""

    def get_user_by_id(self, db: Session, user_id: uuid.UUID) -> Optional[User]:
        return db.get(User, user_id)

    def get_user_by_email(self, db: Session, email: str) -> Optional[User]:
        stmt = select(User).where(func.lower(User.email) == email.strip().lower())
        return db.scalar(stmt)

    def create_user(self, db: Session, user: User) -> User:
        db.add(user)
        db.flush()
        return user

    def create_credential(self, db: Session, cred: UserCredential) -> UserCredential:
        db.add(cred)
        db.flush()
        return cred

    def get_active_credential_by_hash(
        self, db: Session, credential_type: str, credential_hash: str
    ) -> Optional[UserCredential]:
        stmt = select(UserCredential).where(
            UserCredential.credential_type == credential_type,
            UserCredential.credential_hash == credential_hash,
            UserCredential.revoked_at.is_(None),
        )
        return db.scalar(stmt)

    def get_latest_mfa_credential(self, db: Session, user_id: uuid.UUID) -> Optional[UserCredential]:
        stmt = (
            select(UserCredential)
            .where(
                UserCredential.user_id == user_id,
                UserCredential.credential_type == CredentialType.MFA.value,
                UserCredential.revoked_at.is_(None),
            )
            .order_by(UserCredential.issued_at.desc())
        )
        return db.scalar(stmt)

    def revoke_user_refresh_tokens(self, db: Session, user_id: uuid.UUID) -> int:
        now = datetime.now(timezone.utc)
        res = db.execute(
            update(UserCredential)
            .where(
                UserCredential.user_id == user_id,
                UserCredential.credential_type == CredentialType.REFRESH_TOKEN.value,
                UserCredential.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        db.flush()
        return int(res.rowcount or 0)

    def get_customer_profile(self, db: Session, user_id: uuid.UUID) -> Optional[CustomerProfile]:
        return db.scalar(select(CustomerProfile).where(CustomerProfile.user_id == user_id))

    def get_chemist_profile(self, db: Session, user_id: uuid.UUID) -> Optional[ChemistProfile]:
        return db.scalar(select(ChemistProfile).where(ChemistProfile.user_id == user_id))

    def list_roles(self, db: Session) -> List[Role]:
        return list(db.scalars(select(Role).where(Role.status == "ACTIVE").order_by(Role.role_code.asc())).all())

    def get_role_by_code(self, db: Session, role_code: str) -> Optional[Role]:
        return db.scalar(select(Role).where(Role.role_code == role_code.strip()))

    def list_user_memberships(
        self, db: Session, user_id: uuid.UUID, *, active_only: bool = False
    ) -> List[OrganizationMembership]:
        stmt = select(OrganizationMembership).where(OrganizationMembership.user_id == user_id)
        if active_only:
            stmt = stmt.where(OrganizationMembership.membership_status == MembershipStatus.ACTIVE.value)
        return list(db.scalars(stmt.order_by(OrganizationMembership.created_at.asc())).all())

    def get_membership_by_id(self, db: Session, membership_id: uuid.UUID) -> Optional[OrganizationMembership]:
        return db.get(OrganizationMembership, membership_id)

    def get_membership_for_org(
        self, db: Session, organization_id: uuid.UUID, membership_id: uuid.UUID
    ) -> Optional[OrganizationMembership]:
        return db.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.id == membership_id,
            )
        )

    def get_membership_for_org_user(
        self, db: Session, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> Optional[OrganizationMembership]:
        return db.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id == user_id,
            )
        )

    def list_org_memberships(
        self,
        db: Session,
        organization_id: uuid.UUID,
        *,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[OrganizationMembership], int]:
        stmt = select(OrganizationMembership).where(OrganizationMembership.organization_id == organization_id)
        if status:
            stmt = stmt.where(OrganizationMembership.membership_status == status)
        stmt = stmt.order_by(OrganizationMembership.created_at.desc())
        return paginate(db, stmt, page=page, page_size=page_size)

    def list_membership_role_codes(
        self, db: Session, organization_id: uuid.UUID, membership_id: uuid.UUID, *, active_only: bool = True
    ) -> List[str]:
        stmt = (
            select(Role.role_code)
            .join(MembershipRoleAssignment, MembershipRoleAssignment.role_id == Role.id)
            .where(
                MembershipRoleAssignment.organization_id == organization_id,
                MembershipRoleAssignment.membership_id == membership_id,
            )
        )
        if active_only:
            stmt = stmt.where(MembershipRoleAssignment.status == RoleAssignmentStatus.ACTIVE.value)
        return list(db.scalars(stmt).all())

    def list_role_assignments_for_membership(
        self, db: Session, organization_id: uuid.UUID, membership_id: uuid.UUID
    ) -> List[MembershipRoleAssignment]:
        stmt = select(MembershipRoleAssignment).where(
            MembershipRoleAssignment.organization_id == organization_id,
            MembershipRoleAssignment.membership_id == membership_id,
        )
        return list(db.scalars(stmt).all())

    def get_role_assignment_for_org(
        self, db: Session, organization_id: uuid.UUID, membership_id: uuid.UUID, assignment_id: uuid.UUID
    ) -> Optional[MembershipRoleAssignment]:
        return db.scalar(
            select(MembershipRoleAssignment).where(
                MembershipRoleAssignment.organization_id == organization_id,
                MembershipRoleAssignment.membership_id == membership_id,
                MembershipRoleAssignment.id == assignment_id,
            )
        )

    def list_site_assignments_for_membership(
        self, db: Session, organization_id: uuid.UUID, membership_id: uuid.UUID
    ) -> List[SiteAssignment]:
        stmt = select(SiteAssignment).where(
            SiteAssignment.organization_id == organization_id,
            SiteAssignment.membership_id == membership_id,
        )
        return list(db.scalars(stmt).all())

    def get_invitation_by_token_hash(self, db: Session, token_hash: str) -> Optional[Invitation]:
        return db.scalar(select(Invitation).where(Invitation.token_hash == token_hash))

    def get_invitation_for_org(
        self, db: Session, organization_id: uuid.UUID, invitation_id: uuid.UUID
    ) -> Optional[Invitation]:
        return db.scalar(
            select(Invitation).where(
                Invitation.organization_id == organization_id,
                Invitation.id == invitation_id,
            )
        )

    def get_pending_invitation_for_email(
        self, db: Session, organization_id: uuid.UUID, email: str
    ) -> Optional[Invitation]:
        return db.scalar(
            select(Invitation).where(
                Invitation.organization_id == organization_id,
                func.lower(Invitation.email) == email.strip().lower(),
                Invitation.status == "PENDING",
            )
        )

    def get_employee_profile_for_org(
        self, db: Session, organization_id: uuid.UUID, profile_id: uuid.UUID
    ) -> Optional[EmployeeProfile]:
        return db.scalar(
            select(EmployeeProfile).where(
                EmployeeProfile.organization_id == organization_id,
                EmployeeProfile.id == profile_id,
            )
        )

    def get_employee_profile_by_membership(
        self, db: Session, organization_id: uuid.UUID, membership_id: uuid.UUID
    ) -> Optional[EmployeeProfile]:
        return db.scalar(
            select(EmployeeProfile).where(
                EmployeeProfile.organization_id == organization_id,
                EmployeeProfile.membership_id == membership_id,
            )
        )

    def create_qualification(
        self, db: Session, qualification: EmployeeQualification
    ) -> EmployeeQualification:
        db.add(qualification)
        db.flush()
        return qualification

    def create_training_record(self, db: Session, record: TrainingRecord) -> TrainingRecord:
        db.add(record)
        db.flush()
        return record

    def get_permissions_for_role_code(self, db: Session, role_code: str) -> List[str]:
        stmt = (
            select(Permission.permission_code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .where(Role.role_code == role_code)
        )
        return list(db.scalars(stmt).all())
