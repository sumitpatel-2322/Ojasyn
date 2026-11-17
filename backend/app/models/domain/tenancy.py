import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.domain.enums import AddressType, OrganizationStatus, SiteStatus
from app.models.domain.base import Base, UUIDPKMixin, utcnow

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Organization(UUIDPKMixin, Base):
    __tablename__ = "organizations"

    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    trade_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    organization_type: Mapped[str] = mapped_column(String(50), nullable=False)
    registration_number: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    cin: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    pan: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    gstin: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    country_of_registration: Mapped[str] = mapped_column(String(2), nullable=False, default="IN")
    incorporation_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    website_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    business_email: Mapped[str] = mapped_column(String(254), nullable=False)
    business_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    registered_address_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "organization_addresses.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_organizations_registered_address",
        ),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=OrganizationStatus.DRAFT.value, index=True
    )
    onboarding_submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_organizations_verified_by",
        ),
        nullable=True,
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    addresses: Mapped[List["OrganizationAddress"]] = relationship(
        "OrganizationAddress",
        back_populates="organization",
        foreign_keys="OrganizationAddress.organization_id",
    )
    sites: Mapped[List["Site"]] = relationship("Site", back_populates="organization")


class OrganizationAddress(UUIDPKMixin, Base):
    __tablename__ = "organization_addresses"
    __table_args__ = (
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="ck_org_addresses_valid_dates"),
        UniqueConstraint("organization_id", "id", name="uq_org_addresses_org_id_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    address_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default=AddressType.REGISTERED.value
    )
    line_1: Mapped[str] = mapped_column(String(200), nullable=False)
    line_2: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    locality: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state_region: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str] = mapped_column(String(20), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="IN")
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    valid_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    valid_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="addresses",
        foreign_keys=[organization_id],
    )


class Site(UUIDPKMixin, Base):
    __tablename__ = "sites"
    __table_args__ = (
        UniqueConstraint("organization_id", "site_code", name="uq_sites_org_code"),
        UniqueConstraint("organization_id", "id", name="uq_sites_org_id_id"),
        ForeignKeyConstraint(
            ["organization_id", "address_id"],
            ["organization_addresses.organization_id", "organization_addresses.id"],
            ondelete="RESTRICT",
            name="fk_sites_org_address",
        ),
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="ck_sites_effective_dates"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    site_code: Mapped[str] = mapped_column(String(40), nullable=False)
    site_name: Mapped[str] = mapped_column(String(200), nullable=False)
    site_type: Mapped[str] = mapped_column(String(40), nullable=False)
    operations_scope: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    address_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_addresses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    primary_contact_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=SiteStatus.DRAFT.value, index=True
    )
    is_primary_site: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    effective_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    organization: Mapped["Organization"] = relationship("Organization", back_populates="sites")


class Department(UUIDPKMixin, Base):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "site_id",
            "department_code",
            name="uq_departments_org_site_code",
            postgresql_nulls_not_distinct=True,
        ),
        UniqueConstraint("organization_id", "id", name="uq_departments_org_id_id"),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_departments_org_site",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    site_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    department_code: Mapped[str] = mapped_column(String(40), nullable=False)
    department_name: Mapped[str] = mapped_column(String(120), nullable=False)
    department_head_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "organization_memberships.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_departments_head_membership",
        ),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class OrganizationSequence(UUIDPKMixin, Base):
    __tablename__ = "organization_sequences"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "entity_type",
            "period_year",
            name="uq_org_sequences_scope_type_year",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("last_value >= 0", name="ck_org_sequences_non_negative"),
    )

    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    period_year: Mapped[int] = mapped_column(Integer, nullable=False)
    last_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
