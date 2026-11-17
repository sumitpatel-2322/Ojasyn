import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class CustomerProfileInput(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    phone_e164: Optional[str] = Field(default=None, max_length=20)
    address_line: Optional[str] = Field(default=None, max_length=255)
    city: Optional[str] = Field(default=None, max_length=100)
    state: Optional[str] = Field(default=None, max_length=100)
    pincode: Optional[str] = Field(default=None, max_length=20)
    country_code: str = Field(default="IN", min_length=2, max_length=2)
    preferred_language: str = Field(default="en", min_length=2, max_length=10)


class ChemistProfileInput(BaseModel):
    pharmacy_name: str = Field(..., min_length=2, max_length=200)
    drug_license_number: Optional[str] = Field(default=None, max_length=80)
    gst_number: Optional[str] = Field(default=None, max_length=20)
    pharmacy_address: str = Field(..., min_length=3, max_length=255)
    city: str = Field(..., min_length=2, max_length=100)
    state: str = Field(..., min_length=2, max_length=100)
    pincode: str = Field(..., min_length=3, max_length=20)
    pharmacist_registration_number: Optional[str] = Field(default=None, max_length=80)


class ChemistProfileUpdateInput(BaseModel):
    pharmacy_name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    drug_license_number: Optional[str] = Field(default=None, max_length=80)
    gst_number: Optional[str] = Field(default=None, max_length=20)
    pharmacy_address: Optional[str] = Field(default=None, min_length=3, max_length=255)
    city: Optional[str] = Field(default=None, min_length=2, max_length=100)
    state: Optional[str] = Field(default=None, min_length=2, max_length=100)
    pincode: Optional[str] = Field(default=None, min_length=3, max_length=20)
    pharmacist_registration_number: Optional[str] = Field(default=None, max_length=80)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., min_length=2, max_length=150)
    mobile_e164: Optional[str] = Field(default=None, max_length=20)
    profile_type: Optional[Literal["CUSTOMER", "CHEMIST"]] = None
    customer_profile: Optional[CustomerProfileInput] = None
    chemist_profile: Optional[ChemistProfileInput] = None

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class RegisterResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    account_status: str
    verification_required: bool
    # Raw verification token is returned directly ONLY on creation for non-SMTP dev/test verification flows
    # and is NEVER written to outbox_events or audit_events.
    verification_token: Optional[str] = None


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., min_length=10)


class VerifyEmailResponse(BaseModel):
    user_id: uuid.UUID
    account_status: str
    email_verified_at: datetime


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)
    totp_code: Optional[str] = Field(default=None, max_length=12)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., min_length=16)


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None
    revoke_all_sessions: bool = False


class MFAChallengeRequest(BaseModel):
    action: Literal["enroll", "enroll_confirm", "verify"] = "verify"
    code: Optional[str] = Field(default=None, min_length=6, max_length=12)


class MFAChallengeResponse(BaseModel):
    mfa_state: str
    verified: bool
    provisioning_uri: Optional[str] = None
    secret: Optional[str] = None


class MembershipBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    membership_id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    organization_status: str
    membership_status: str
    member_type: str
    role_codes: List[str] = Field(default_factory=list)


class PartnerMembershipBrief(BaseModel):
    partner_membership_id: uuid.UUID
    organization_id: uuid.UUID
    partner_id: uuid.UUID
    partner_name: str
    membership_status: str
    role_code: str


class UserSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    mobile_e164: Optional[str]
    account_status: str
    platform_role: Optional[str] = None
    mfa_state: str
    email_verified_at: Optional[datetime]
    last_login_at: Optional[datetime]


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserSummaryResponse
    memberships: List[MembershipBrief] = Field(default_factory=list)
    partner_memberships: List[PartnerMembershipBrief] = Field(default_factory=list)


class UserProfileResponse(BaseModel):
    user: UserSummaryResponse
    memberships: List[MembershipBrief] = Field(default_factory=list)
    partner_memberships: List[PartnerMembershipBrief] = Field(default_factory=list)
    customer_profile: Optional[Dict[str, Any]] = None
    chemist_profile: Optional[Dict[str, Any]] = None


class UpdateUserProfileRequest(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    mobile_e164: Optional[str] = Field(default=None, max_length=20)
    customer_profile: Optional[CustomerProfileInput] = None
    chemist_profile: Optional[ChemistProfileUpdateInput] = None


class RoleCatalogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role_code: str
    role_name: str
    role_scope: str
    is_system_role: bool
    description: Optional[str]
    status: str


class MyPermissionsResponse(BaseModel):
    user_id: uuid.UUID
    organization_id: uuid.UUID
    membership_id: Optional[uuid.UUID]
    role_codes: List[str]
    permission_codes: List[str]
    allowed_site_ids: List[uuid.UUID]
    is_org_wide: bool


# --- Onboarding & Organization / Site / Department DTOs ---


class AddressCreateInput(BaseModel):
    address_type: str = Field(default="REGISTERED", max_length=30)
    line_1: str = Field(..., min_length=3, max_length=200)
    line_2: Optional[str] = Field(default=None, max_length=200)
    locality: Optional[str] = Field(default=None, max_length=120)
    city: str = Field(..., min_length=2, max_length=100)
    state_region: Optional[str] = Field(default=None, max_length=100)
    postal_code: str = Field(..., min_length=3, max_length=20)
    country_code: str = Field(default="IN", min_length=2, max_length=2)
    is_primary: bool = True
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None


class OrganizationAddressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    address_type: str
    line_1: str
    line_2: Optional[str]
    locality: Optional[str]
    city: str
    state_region: Optional[str]
    postal_code: str
    country_code: str
    is_primary: bool
    valid_from: Optional[date]
    valid_to: Optional[date]
    created_at: datetime
    updated_at: datetime


class OnboardingOrganizationCreateRequest(BaseModel):
    legal_name: str = Field(..., min_length=2, max_length=200)
    trade_name: Optional[str] = Field(default=None, max_length=200)
    organization_type: str = Field(..., min_length=2, max_length=50)
    registration_number: Optional[str] = Field(default=None, max_length=80)
    cin: Optional[str] = Field(default=None, max_length=30)
    pan: Optional[str] = Field(default=None, max_length=20)
    gstin: Optional[str] = Field(default=None, max_length=20)
    country_of_registration: str = Field(default="IN", min_length=2, max_length=2)
    incorporation_date: Optional[date] = None
    website_url: Optional[str] = Field(default=None, max_length=255)
    business_email: EmailStr
    business_phone: Optional[str] = Field(default=None, max_length=32)
    timezone: str = Field(default="Asia/Kolkata", max_length=64)
    registered_address: AddressCreateInput


class OrganizationUpdateRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    legal_name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    trade_name: Optional[str] = Field(default=None, max_length=200)
    registration_number: Optional[str] = Field(default=None, max_length=80)
    cin: Optional[str] = Field(default=None, max_length=30)
    pan: Optional[str] = Field(default=None, max_length=20)
    gstin: Optional[str] = Field(default=None, max_length=20)
    website_url: Optional[str] = Field(default=None, max_length=255)
    business_phone: Optional[str] = Field(default=None, max_length=32)
    timezone: Optional[str] = Field(default=None, max_length=64)
    reason: Optional[str] = Field(default=None, max_length=1000)


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    legal_name: str
    trade_name: Optional[str]
    organization_type: str
    registration_number: Optional[str]
    cin: Optional[str]
    pan: Optional[str]
    gstin: Optional[str]
    country_of_registration: str
    incorporation_date: Optional[date]
    website_url: Optional[str]
    business_email: str
    business_phone: Optional[str]
    registered_address_id: Optional[uuid.UUID]
    status: str
    onboarding_submitted_at: Optional[datetime]
    verified_at: Optional[datetime]
    verified_by_user_id: Optional[uuid.UUID]
    timezone: str
    created_at: datetime
    updated_at: datetime
    row_version: int


class LicenseCreateRequest(BaseModel):
    license_type: str = Field(..., min_length=2, max_length=60)
    license_number: str = Field(..., min_length=2, max_length=120)
    issuing_authority: str = Field(..., min_length=2, max_length=150)
    scope_description: Optional[str] = Field(default=None, max_length=500)
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    licence_document_id: Optional[uuid.UUID] = None
    metadata_json: Optional[Dict[str, Any]] = None


class OrganizationLicenseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    license_type: str
    license_number: str
    issuing_authority: str
    valid_from: Optional[date]
    valid_to: Optional[date]
    status: str
    licence_document_id: Optional[uuid.UUID]
    metadata_json: Optional[Dict[str, Any]]
    created_at: datetime
    row_version: int


class SiteLicenseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: uuid.UUID
    license_type: str
    license_number: str
    issuing_authority: str
    scope_description: Optional[str]
    valid_from: Optional[date]
    valid_to: Optional[date]
    status: str
    licence_document_id: Optional[uuid.UUID]
    metadata_json: Optional[Dict[str, Any]]
    created_at: datetime
    row_version: int


class OnboardingSubmitRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    reason: Optional[str] = Field(default=None, max_length=1000)


class ReviewDecisionRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    decision: Literal["UNDER_REVIEW", "ACTIVE", "NEEDS_INFORMATION", "REJECTED", "SUSPENDED", "INACTIVE"]
    comments: str = Field(..., min_length=3, max_length=1000)


class SiteCreateRequest(BaseModel):
    site_code: str = Field(..., min_length=2, max_length=40)
    site_name: str = Field(..., min_length=2, max_length=200)
    site_type: str = Field(..., min_length=2, max_length=40)
    address_id: uuid.UUID
    operations_scope: Optional[Dict[str, Any]] = None
    primary_contact_user_id: Optional[uuid.UUID] = None
    timezone: str = Field(default="Asia/Kolkata", max_length=64)
    is_primary_site: bool = False
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None


class SiteUpdateRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    site_name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    site_type: Optional[str] = Field(default=None, min_length=2, max_length=40)
    operations_scope: Optional[Dict[str, Any]] = None
    primary_contact_user_id: Optional[uuid.UUID] = None
    timezone: Optional[str] = Field(default=None, max_length=64)
    reason: Optional[str] = Field(default=None, max_length=1000)


class SiteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_code: str
    site_name: str
    site_type: str
    operations_scope: Optional[Dict[str, Any]]
    address_id: uuid.UUID
    primary_contact_user_id: Optional[uuid.UUID]
    timezone: str
    status: str
    is_primary_site: bool
    effective_from: Optional[date]
    effective_to: Optional[date]
    created_at: datetime
    updated_at: datetime
    row_version: int


class DepartmentCreateRequest(BaseModel):
    site_id: Optional[uuid.UUID] = None
    department_code: str = Field(..., min_length=2, max_length=40)
    department_name: str = Field(..., min_length=2, max_length=120)
    department_head_membership_id: Optional[uuid.UUID] = None


class DepartmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: Optional[uuid.UUID]
    department_code: str
    department_name: str
    department_head_membership_id: Optional[uuid.UUID]
    status: str
    created_at: datetime
    updated_at: datetime
