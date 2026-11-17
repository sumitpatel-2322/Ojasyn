import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import MembershipStatus
from app.models.domain.base import Base, TenantScopedMixin, TimestampVersionMixin, UUIDPKMixin


class Complainant(UUIDPKMixin, TimestampVersionMixin, Base):
    """External or internal complainant contact profile (portal/email/phone intake)."""

    __tablename__ = "complainants"

    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    complainant_type: Mapped[str] = mapped_column(String(30), nullable=False, default="CUSTOMER")
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(254), nullable=True, index=True)
    phone_e164: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state_region: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="IN")


class CustomerProfile(UUIDPKMixin, TimestampVersionMixin, Base):
    """Registered public customer profile linked 1:1 to a platform User."""

    __tablename__ = "customer_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    phone_e164: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    address_line: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    pincode: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="IN")
    preferred_language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ACTIVE")


class ChemistProfile(UUIDPKMixin, TimestampVersionMixin, Base):
    """Registered retail/hospital chemist profile with regulatory KYC fields."""

    __tablename__ = "chemist_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    pharmacy_name: Mapped[str] = mapped_column(String(200), nullable=False)
    drug_license_number: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    gst_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    pharmacy_address: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    pincode: Mapped[str] = mapped_column(String(20), nullable=False)
    pharmacist_registration_number: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING", index=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    # Nullable UUID without DB FK until Phase 2 migration creates documents table
    licence_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)


class BusinessPartner(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """External supplier, distributor, CMO, or contract lab scoped to a tenant organization."""

    __tablename__ = "business_partners"
    __table_args__ = (
        UniqueConstraint("organization_id", "partner_code", name="uq_business_partners_org_code"),
        UniqueConstraint("organization_id", "id", name="uq_business_partners_org_id_id"),
    )

    partner_code: Mapped[str] = mapped_column(String(40), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    trade_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    partner_type: Mapped[str] = mapped_column(String(40), nullable=False)
    tax_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    license_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    primary_email: Mapped[str] = mapped_column(String(254), nullable=False)
    primary_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    qualification_status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ACTIVE", index=True)

    sites: Mapped[List["PartnerSite"]] = relationship(
        "PartnerSite", back_populates="partner", foreign_keys="PartnerSite.partner_id"
    )
    contacts: Mapped[List["PartnerContact"]] = relationship(
        "PartnerContact", back_populates="partner", foreign_keys="PartnerContact.partner_id"
    )
    memberships: Mapped[List["PartnerMembership"]] = relationship(
        "PartnerMembership", back_populates="partner", foreign_keys="PartnerMembership.partner_id"
    )


class PartnerSite(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Physical location/warehouse/facility belonging to a BusinessPartner."""

    __tablename__ = "partner_sites"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "partner_id",
            "site_code",
            name="uq_partner_sites_org_partner_code",
        ),
        UniqueConstraint("organization_id", "id", name="uq_partner_sites_org_id_id"),
        ForeignKeyConstraint(
            ["organization_id", "partner_id"],
            ["business_partners.organization_id", "business_partners.id"],
            ondelete="RESTRICT",
            name="fk_partner_sites_org_partner",
        ),
    )

    partner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("business_partners.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    site_code: Mapped[str] = mapped_column(String(40), nullable=False)
    site_name: Mapped[str] = mapped_column(String(200), nullable=False)
    address_line_1: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state_region: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str] = mapped_column(String(20), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="IN")
    drug_license_number: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ACTIVE")

    partner: Mapped["BusinessPartner"] = relationship(
        "BusinessPartner",
        back_populates="sites",
        foreign_keys=[partner_id],
    )


class PartnerContact(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Point of contact for a BusinessPartner or specific PartnerSite."""

    __tablename__ = "partner_contacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "partner_id"],
            ["business_partners.organization_id", "business_partners.id"],
            ondelete="RESTRICT",
            name="fk_partner_contacts_org_partner",
        ),
        ForeignKeyConstraint(
            ["organization_id", "partner_site_id"],
            ["partner_sites.organization_id", "partner_sites.id"],
            ondelete="RESTRICT",
            name="fk_partner_contacts_org_site",
        ),
    )

    partner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("business_partners.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    partner_site_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("partner_sites.id", ondelete="RESTRICT"),
        nullable=True,
    )
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    phone_e164: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    designation: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ACTIVE")

    partner: Mapped["BusinessPartner"] = relationship(
        "BusinessPartner",
        back_populates="contacts",
        foreign_keys=[partner_id],
    )


class PartnerMembership(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """External partner portal user membership and invitation state."""

    __tablename__ = "partner_memberships"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "partner_id",
            "user_id",
            name="uq_partner_memberships_org_partner_user",
        ),
        UniqueConstraint("organization_id", "id", name="uq_partner_memberships_org_id_id"),
        ForeignKeyConstraint(
            ["organization_id", "partner_id"],
            ["business_partners.organization_id", "business_partners.id"],
            ondelete="RESTRICT",
            name="fk_partner_memberships_org_partner",
        ),
        ForeignKeyConstraint(
            ["organization_id", "partner_site_id"],
            ["partner_sites.organization_id", "partner_sites.id"],
            ondelete="RESTRICT",
            name="fk_partner_memberships_org_site",
        ),
    )

    partner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("business_partners.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    partner_site_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("partner_sites.id", ondelete="RESTRICT"),
        nullable=True,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    invited_email: Mapped[str] = mapped_column(String(254), nullable=False)
    token_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    membership_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=MembershipStatus.INVITED.value, index=True
    )
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    partner: Mapped["BusinessPartner"] = relationship(
        "BusinessPartner",
        back_populates="memberships",
        foreign_keys=[partner_id],
    )
