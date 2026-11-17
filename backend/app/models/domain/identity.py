import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.domain.enums import (
    AssignmentStatus,
    AssignmentType,
    EmploymentStatus,
    InvitationStatus,
    MFAState,
    MembershipStatus,
    MemberType,
    RoleAssignmentStatus,
    UserAccountStatus,
)
from app.models.domain.base import Base, TenantScopedMixin, TimestampVersionMixin, UUIDPKMixin, utcnow

JSONType = JSON().with_variant(JSONB(), "postgresql")


def _utcnow_seconds() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class User(UUIDPKMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_email_lower", text("lower(email)"), unique=True),
        CheckConstraint("failed_login_attempts >= 0", name="ck_users_failed_login_non_neg"),
    )

    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    mobile_e164: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    account_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=UserAccountStatus.PENDING_VERIFICATION.value
    )
    platform_role: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    mobile_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    mfa_state: Mapped[str] = mapped_column(String(30), nullable=False, default=MFAState.DISABLED.value)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    token_valid_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow_seconds
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    credentials: Mapped[List["UserCredential"]] = relationship("UserCredential", back_populates="user")
    memberships: Mapped[List["OrganizationMembership"]] = relationship(
        "OrganizationMembership",
        back_populates="user",
        foreign_keys="OrganizationMembership.user_id",
    )


class UserCredential(UUIDPKMixin, Base):
    __tablename__ = "user_credentials"
    __table_args__ = (
        CheckConstraint("replaced_by_id IS NULL OR replaced_by_id != id", name="ck_user_creds_no_self_replace"),
        Index(
            "ix_user_credentials_active_user_type",
            "user_id",
            "credential_type",
            postgresql_where="revoked_at IS NULL",
        ),
        Index("ix_user_credentials_token_family", "token_family_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    credential_type: Mapped[str] = mapped_column(String(30), nullable=False)
    credential_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    provider_subject: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    token_family_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    replaced_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user_credentials.id", ondelete="RESTRICT"),
        nullable=True,
    )
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    session_metadata: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="credentials")


class OrganizationMembership(UUIDPKMixin, Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_org_memberships_org_user"),
        UniqueConstraint("organization_id", "id", name="uq_org_memberships_org_id_id"),
        Index(
            "ix_org_memberships_org_emp_code",
            "organization_id",
            "employee_code",
            unique=True,
            postgresql_where="employee_code IS NOT NULL",
        ),
        ForeignKeyConstraint(
            ["organization_id", "department_id"],
            ["departments.organization_id", "departments.id"],
            ondelete="RESTRICT",
            name="fk_org_memberships_org_dept",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    member_type: Mapped[str] = mapped_column(String(30), nullable=False, default=MemberType.EMPLOYEE.value)
    membership_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=MembershipStatus.INVITED.value, index=True
    )
    employee_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    designation: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    user: Mapped["User"] = relationship("User", back_populates="memberships", foreign_keys=[user_id])
    employee_profile: Mapped[Optional["EmployeeProfile"]] = relationship(
        "EmployeeProfile",
        back_populates="membership",
        uselist=False,
        foreign_keys="EmployeeProfile.membership_id",
    )
    site_assignments: Mapped[List["SiteAssignment"]] = relationship(
        "SiteAssignment",
        back_populates="membership",
        foreign_keys="SiteAssignment.membership_id",
    )
    role_assignments: Mapped[List["MembershipRoleAssignment"]] = relationship(
        "MembershipRoleAssignment",
        back_populates="membership",
        foreign_keys="MembershipRoleAssignment.membership_id",
    )


class EmployeeProfile(UUIDPKMixin, Base):
    __tablename__ = "employee_profiles"
    __table_args__ = (
        UniqueConstraint("organization_id", "employee_code", name="uq_emp_profiles_org_code"),
        UniqueConstraint("organization_id", "user_id", name="uq_emp_profiles_org_user"),
        ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_emp_profiles_org_membership",
        ),
    )

    membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    employee_code: Mapped[str] = mapped_column(String(50), nullable=False)
    joining_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    employment_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    job_title: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    supervisor_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="SET NULL"),
        nullable=True,
    )
    employment_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=EmploymentStatus.ACTIVE.value
    )
    termination_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    emergency_contact_reference: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    membership: Mapped["OrganizationMembership"] = relationship(
        "OrganizationMembership",
        back_populates="employee_profile",
        foreign_keys=[membership_id],
    )


class SiteAssignment(UUIDPKMixin, Base):
    __tablename__ = "site_assignments"
    __table_args__ = (
        UniqueConstraint("membership_id", "site_id", name="uq_site_assignments_member_site"),
        ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_site_assignments_org_membership",
        ),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_site_assignments_org_site",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    assignment_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default=AssignmentType.PRIMARY.value
    )
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=AssignmentStatus.PENDING.value)
    assigned_by_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    membership: Mapped["OrganizationMembership"] = relationship(
        "OrganizationMembership",
        back_populates="site_assignments",
        foreign_keys=[membership_id],
    )


class Role(UUIDPKMixin, Base):
    __tablename__ = "roles"

    role_code: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    role_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role_scope: Mapped[str] = mapped_column(String(20), nullable=False)
    is_system_role: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")

    permissions: Mapped[List["RolePermission"]] = relationship("RolePermission", back_populates="role")


class Permission(UUIDPKMixin, Base):
    __tablename__ = "permissions"

    permission_code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    resource: Mapped[str] = mapped_column(String(60), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("permissions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    constraint_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)

    role: Mapped["Role"] = relationship("Role", back_populates="permissions")
    permission: Mapped["Permission"] = relationship("Permission")


class MembershipRoleAssignment(UUIDPKMixin, Base):
    __tablename__ = "membership_role_assignments"
    __table_args__ = (
        CheckConstraint(
            "(scope_type = 'ORGANIZATION' AND site_id IS NULL) OR (scope_type = 'SITE' AND site_id IS NOT NULL)",
            name="ck_member_role_scope_consistency",
        ),
        UniqueConstraint(
            "membership_id",
            "role_id",
            "scope_type",
            "site_id",
            name="uq_member_role_scope",
            postgresql_nulls_not_distinct=True,
        ),
        ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_member_role_org_membership",
        ),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_member_role_org_site",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    site_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    granted_by_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(25), nullable=False, default=RoleAssignmentStatus.PENDING.value, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    membership: Mapped["OrganizationMembership"] = relationship(
        "OrganizationMembership",
        back_populates="role_assignments",
        foreign_keys=[membership_id],
    )
    role: Mapped["Role"] = relationship("Role")


class Invitation(UUIDPKMixin, Base):
    __tablename__ = "invitations"
    __table_args__ = (
        Index(
            "ix_invitations_pending_org_email",
            "organization_id",
            "email",
            postgresql_where="status = 'PENDING'",
        ),
        ForeignKeyConstraint(
            ["organization_id", "department_id"],
            ["departments.organization_id", "departments.id"],
            ondelete="RESTRICT",
            name="fk_invitations_org_dept",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    site_ids: Mapped[List[str]] = mapped_column(JSONType, nullable=False, default=list)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    requested_role_codes: Mapped[List[str]] = mapped_column(JSONType, nullable=False)
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    employee_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    designation: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    requires_quality_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(25), nullable=False, default=InvitationStatus.PENDING.value, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class EmployeeQualification(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Formal GMP/QMS qualifications, certifications, and analyst credentials for an employee."""

    __tablename__ = "employee_qualifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_emp_qual_org_membership",
        ),
        CheckConstraint(
            "expires_on IS NULL OR issued_on IS NULL OR expires_on >= issued_on",
            name="ck_emp_qual_valid_dates",
        ),
    )

    membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    employee_profile_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("employee_profiles.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    qualification_type: Mapped[str] = mapped_column(String(60), nullable=False)
    qualification_name: Mapped[str] = mapped_column(String(150), nullable=False)
    issuing_body: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    certificate_number: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    issued_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expires_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # Nullable UUID without DB FK until Phase 2 migration creates documents table
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING", index=True)
    verified_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class TrainingRecord(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """SOP/GXP training completion and competency assessment records."""

    __tablename__ = "training_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_training_org_membership",
        ),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_training_org_site",
        ),
        CheckConstraint(
            "assessment_score IS NULL OR (assessment_score >= 0 AND assessment_score <= 100)",
            name="ck_training_score_range",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_until >= completed_on",
            name="ck_training_valid_until",
        ),
    )

    membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    employee_profile_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("employee_profiles.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    site_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    training_topic: Mapped[str] = mapped_column(String(200), nullable=False)
    training_method: Mapped[str] = mapped_column(String(40), nullable=False, default="CLASSROOM")
    # Nullable UUIDs without DB FK until Phases 2/4 create documents and sop_revisions
    sop_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    trainer_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    completed_on: Mapped[date] = mapped_column(Date, nullable=False)
    due_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    valid_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    assessment_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_status: Mapped[str] = mapped_column(String(30), nullable=False, default="COMPLETED", index=True)
    evidence_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
