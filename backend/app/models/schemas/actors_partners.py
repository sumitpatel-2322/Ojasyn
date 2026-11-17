import uuid
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class InvitationCreateRequest(BaseModel):
    email: EmailStr
    site_ids: List[uuid.UUID] = Field(default_factory=list)
    requested_role_codes: List[str] = Field(..., min_length=1)
    department_id: Optional[uuid.UUID] = None
    employee_code: Optional[str] = Field(default=None, max_length=50)
    designation: Optional[str] = Field(default=None, max_length=120)
    expires_in_hours: int = Field(default=72, ge=1, le=720)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class InvitationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_ids: List[str]
    email: str
    requested_role_codes: List[str]
    department_id: Optional[uuid.UUID]
    employee_code: Optional[str]
    designation: Optional[str]
    requires_quality_approval: bool
    expires_at: datetime
    accepted_at: Optional[datetime]
    status: str
    created_at: datetime
    # Returned once on creation so caller/dispatcher can hand it off; never persisted in plaintext
    invite_token: Optional[str] = None


class InvitationPreviewResponse(BaseModel):
    organization_legal_name: str
    masked_email: str
    expires_at: datetime
    status: str


class InvitationAcceptRequest(BaseModel):
    employee_code: Optional[str] = Field(default=None, max_length=50)
    job_title: Optional[str] = Field(default=None, max_length=120)


class RoleAssignmentCreateRequest(BaseModel):
    role_code: str = Field(..., min_length=2, max_length=60)
    scope_type: str = Field(default="SITE", max_length=20)
    site_id: Optional[uuid.UUID] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    reason: str = Field(..., min_length=3, max_length=1000)


class RoleAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    membership_id: uuid.UUID
    role_id: uuid.UUID
    role_code: Optional[str] = None
    scope_type: str
    site_id: Optional[uuid.UUID]
    status: str
    granted_by_user_id: uuid.UUID
    approved_by_user_id: Optional[uuid.UUID]
    created_at: datetime


class SiteAssignmentCreateRequest(BaseModel):
    site_id: uuid.UUID
    assignment_type: str = Field(default="PRIMARY", max_length=30)
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    reason: Optional[str] = Field(default=None, max_length=1000)


class SiteAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    membership_id: uuid.UUID
    site_id: uuid.UUID
    assignment_type: str
    status: str
    assigned_by_user_id: uuid.UUID
    created_at: datetime


class MembershipApproveRequest(BaseModel):
    row_version: Optional[int] = Field(default=None, ge=1)
    reason: str = Field(..., min_length=3, max_length=1000)
    approve_pending_roles: bool = True
    approve_pending_sites: bool = True


class MembershipRevokeRequest(BaseModel):
    row_version: Optional[int] = Field(default=None, ge=1)
    reason: str = Field(..., min_length=3, max_length=1000)


class MembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    organization_id: uuid.UUID
    member_type: str
    membership_status: str
    employee_code: Optional[str]
    department_id: Optional[uuid.UUID]
    designation: Optional[str]
    approved_at: Optional[datetime]
    approved_by_user_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime
    row_version: int
    employee_profile_id: Optional[uuid.UUID] = None
    role_assignments: List[RoleAssignmentResponse] = Field(default_factory=list)
    site_assignments: List[SiteAssignmentResponse] = Field(default_factory=list)


class RoleRevokeRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=1000)


class EmployeeQualificationCreateRequest(BaseModel):
    qualification_type: str = Field(..., min_length=2, max_length=60)
    qualification_name: str = Field(..., min_length=2, max_length=150)
    issuing_body: Optional[str] = Field(default=None, max_length=150)
    certificate_number: Optional[str] = Field(default=None, max_length=80)
    issued_on: Optional[date] = None
    expires_on: Optional[date] = None
    document_id: Optional[uuid.UUID] = None
    verification_status: str = Field(default="VERIFIED", max_length=30)


class EmployeeQualificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    membership_id: uuid.UUID
    employee_profile_id: Optional[uuid.UUID]
    qualification_type: str
    qualification_name: str
    issuing_body: Optional[str]
    certificate_number: Optional[str]
    issued_on: Optional[date]
    expires_on: Optional[date]
    document_id: Optional[uuid.UUID]
    verification_status: str
    verified_by_user_id: Optional[uuid.UUID]
    verified_at: Optional[datetime]
    created_at: datetime
    row_version: int


class TrainingRecordCreateRequest(BaseModel):
    site_id: Optional[uuid.UUID] = None
    training_topic: str = Field(..., min_length=2, max_length=200)
    training_method: str = Field(default="CLASSROOM", max_length=40)
    sop_revision_id: Optional[uuid.UUID] = None
    trainer_membership_id: Optional[uuid.UUID] = None
    completed_on: date
    due_on: Optional[date] = None
    valid_until: Optional[date] = None
    assessment_score: Optional[int] = Field(default=None, ge=0, le=100)
    completion_status: str = Field(default="COMPLETED", max_length=30)
    evidence_document_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class TrainingRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    membership_id: uuid.UUID
    employee_profile_id: Optional[uuid.UUID]
    site_id: Optional[uuid.UUID]
    training_topic: str
    training_method: str
    sop_revision_id: Optional[uuid.UUID]
    trainer_membership_id: Optional[uuid.UUID]
    completed_on: date
    due_on: Optional[date]
    valid_until: Optional[date]
    assessment_score: Optional[int]
    completion_status: str
    evidence_document_id: Optional[uuid.UUID]
    notes: Optional[str]
    created_at: datetime
    row_version: int


# --- Business Partner DTOs ---


class BusinessPartnerCreateRequest(BaseModel):
    partner_code: str = Field(..., min_length=2, max_length=40)
    legal_name: str = Field(..., min_length=2, max_length=200)
    trade_name: Optional[str] = Field(default=None, max_length=200)
    partner_type: str = Field(..., min_length=2, max_length=40)
    tax_id: Optional[str] = Field(default=None, max_length=40)
    license_number: Optional[str] = Field(default=None, max_length=100)
    primary_email: EmailStr
    primary_phone: Optional[str] = Field(default=None, max_length=32)


class BusinessPartnerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    partner_code: str
    legal_name: str
    trade_name: Optional[str]
    partner_type: str
    tax_id: Optional[str]
    license_number: Optional[str]
    primary_email: str
    primary_phone: Optional[str]
    qualification_status: str
    status: str
    created_at: datetime
    row_version: int


class PartnerSiteCreateRequest(BaseModel):
    site_code: str = Field(..., min_length=2, max_length=40)
    site_name: str = Field(..., min_length=2, max_length=200)
    address_line_1: str = Field(..., min_length=3, max_length=200)
    city: str = Field(..., min_length=2, max_length=100)
    state_region: Optional[str] = Field(default=None, max_length=100)
    postal_code: str = Field(..., min_length=3, max_length=20)
    country_code: str = Field(default="IN", min_length=2, max_length=2)
    drug_license_number: Optional[str] = Field(default=None, max_length=80)


class PartnerSiteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    partner_id: uuid.UUID
    site_code: str
    site_name: str
    address_line_1: str
    city: str
    state_region: Optional[str]
    postal_code: str
    country_code: str
    drug_license_number: Optional[str]
    status: str
    created_at: datetime
    row_version: int


class PartnerInvitationCreateRequest(BaseModel):
    email: EmailStr
    role_code: str = Field(..., min_length=2, max_length=60)
    partner_site_id: Optional[uuid.UUID] = None
    expires_in_hours: int = Field(default=72, ge=1, le=720)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class PartnerMembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    partner_id: uuid.UUID
    partner_site_id: Optional[uuid.UUID]
    user_id: Optional[uuid.UUID]
    role_id: uuid.UUID
    invited_email: str
    expires_at: Optional[datetime]
    membership_status: str
    approved_by_user_id: Optional[uuid.UUID]
    approved_at: Optional[datetime]
    created_at: datetime
    row_version: int
    invite_token: Optional[str] = None


class PartnerMembershipApproveRequest(BaseModel):
    row_version: Optional[int] = Field(default=None, ge=1)
    user_id: Optional[uuid.UUID] = None
    reason: str = Field(..., min_length=3, max_length=1000)
