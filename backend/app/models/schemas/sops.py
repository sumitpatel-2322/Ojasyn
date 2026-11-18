import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# --- SOP Master Schemas (C5) ---


class SOPCreateRequest(BaseModel):
    site_id: Optional[uuid.UUID] = None
    applicable_site_ids: Optional[List[uuid.UUID]] = None
    department_id: Optional[uuid.UUID] = None
    sop_code: str = Field(..., min_length=2, max_length=80)
    title: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = None
    category: Optional[str] = Field(default=None, max_length=80)
    document_id: Optional[uuid.UUID] = None
    owner_membership_id: Optional[uuid.UUID] = None


class SOPUpdateRequest(BaseModel):
    row_version: Optional[int] = Field(default=None, ge=1)
    site_id: Optional[uuid.UUID] = None
    applicable_site_ids: Optional[List[uuid.UUID]] = None
    department_id: Optional[uuid.UUID] = None
    title: Optional[str] = Field(default=None, min_length=2, max_length=255)
    description: Optional[str] = None
    category: Optional[str] = Field(default=None, max_length=80)
    document_id: Optional[uuid.UUID] = None
    owner_membership_id: Optional[uuid.UUID] = None
    sop_status: Optional[str] = Field(default=None, max_length=30)


class SOPResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: Optional[uuid.UUID] = None
    applicable_site_ids: Optional[List[str]] = None
    department_id: Optional[uuid.UUID] = None
    sop_code: str
    title: str
    description: Optional[str] = None
    category: Optional[str] = None
    document_id: Optional[uuid.UUID] = None
    owner_membership_id: Optional[uuid.UUID] = None
    current_revision_id: Optional[uuid.UUID] = None
    sop_status: str
    row_version: int
    created_at: datetime
    updated_at: datetime
    created_by_user_id: Optional[uuid.UUID] = None
    updated_by_user_id: Optional[uuid.UUID] = None


# --- SOP Step Schemas (C5) ---


class SOPStepCreateRequest(BaseModel):
    step_code: str = Field(..., min_length=1, max_length=60)
    step_sequence: int = Field(..., ge=1)
    process_stage: Optional[str] = Field(default=None, max_length=100)
    title: str = Field(..., min_length=1, max_length=200)
    instruction: str = Field(..., min_length=1)
    is_mandatory: bool = True
    parameter_name: Optional[str] = Field(default=None, max_length=120)
    parameter_code: Optional[str] = Field(default=None, max_length=80)
    expected_data_type: str = Field(default="NONE", max_length=30)
    unit_code: Optional[str] = Field(default=None, max_length=30)
    min_value: Optional[Decimal] = None
    max_value: Optional[Decimal] = None
    target_value: Optional[Decimal] = None
    expected_text_value: Optional[str] = Field(default=None, max_length=255)
    allowed_values_json: Optional[List[Any]] = None
    acceptance_criteria: Optional[str] = None
    requires_operator_signoff: bool = True
    requires_checker_signoff: bool = False
    requires_qa_signoff: bool = False
    required_equipment_category: Optional[str] = Field(default=None, max_length=80)
    required_equipment_code: Optional[str] = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def _validate_limits(self) -> "SOPStepCreateRequest":
        if (
            self.min_value is not None
            and self.max_value is not None
            and self.min_value > self.max_value
        ):
            raise ValueError("min_value must be less than or equal to max_value.")
        return self


class SOPStepUpdateRequest(BaseModel):
    row_version: Optional[int] = Field(default=None, ge=1)
    step_code: Optional[str] = Field(default=None, min_length=1, max_length=60)
    step_sequence: Optional[int] = Field(default=None, ge=1)
    process_stage: Optional[str] = Field(default=None, max_length=100)
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    instruction: Optional[str] = Field(default=None, min_length=1)
    is_mandatory: Optional[bool] = None
    parameter_name: Optional[str] = Field(default=None, max_length=120)
    parameter_code: Optional[str] = Field(default=None, max_length=80)
    expected_data_type: Optional[str] = Field(default=None, max_length=30)
    unit_code: Optional[str] = Field(default=None, max_length=30)
    min_value: Optional[Decimal] = None
    max_value: Optional[Decimal] = None
    target_value: Optional[Decimal] = None
    expected_text_value: Optional[str] = Field(default=None, max_length=255)
    allowed_values_json: Optional[List[Any]] = None
    acceptance_criteria: Optional[str] = None
    requires_operator_signoff: Optional[bool] = None
    requires_checker_signoff: Optional[bool] = None
    requires_qa_signoff: Optional[bool] = None
    required_equipment_category: Optional[str] = Field(default=None, max_length=80)
    required_equipment_code: Optional[str] = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def _validate_limits(self) -> "SOPStepUpdateRequest":
        if (
            self.min_value is not None
            and self.max_value is not None
            and self.min_value > self.max_value
        ):
            raise ValueError("min_value must be less than or equal to max_value.")
        return self


class SOPStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    sop_revision_id: uuid.UUID
    step_code: str
    step_sequence: int
    process_stage: Optional[str] = None
    title: str
    instruction: str
    is_mandatory: bool
    parameter_name: Optional[str] = None
    parameter_code: Optional[str] = None
    expected_data_type: str
    unit_code: Optional[str] = None
    min_value: Optional[Decimal] = None
    max_value: Optional[Decimal] = None
    target_value: Optional[Decimal] = None
    expected_text_value: Optional[str] = None
    allowed_values_json: Optional[List[Any]] = None
    acceptance_criteria: Optional[str] = None
    requires_operator_signoff: bool
    requires_checker_signoff: bool
    requires_qa_signoff: bool
    required_equipment_category: Optional[str] = None
    required_equipment_code: Optional[str] = None
    row_version: int
    created_at: datetime
    updated_at: datetime
    created_by_user_id: Optional[uuid.UUID] = None
    updated_by_user_id: Optional[uuid.UUID] = None


# --- SOP Revision Schemas (C5) ---


class SOPRevisionCreateRequest(BaseModel):
    site_id: Optional[uuid.UUID] = None
    revision_number: str = Field(..., min_length=1, max_length=40)
    revision_sequence: Optional[int] = Field(default=None, ge=1)
    revision_description: Optional[str] = None
    change_reason: Optional[str] = None
    document_id: Optional[uuid.UUID] = None
    document_version_id: Optional[uuid.UUID] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    supersedes_revision_id: Optional[uuid.UUID] = None
    steps: Optional[List[SOPStepCreateRequest]] = None

    @model_validator(mode="after")
    def _validate_window(self) -> "SOPRevisionCreateRequest":
        if self.effective_to is not None:
            if self.effective_from is None or self.effective_to <= self.effective_from:
                raise ValueError("effective_to must be strictly after effective_from.")
        return self


class SOPRevisionSubmitReviewRequest(BaseModel):
    row_version: Optional[int] = Field(default=None, ge=1)
    comments: Optional[str] = None


class SOPRevisionApproveRequest(BaseModel):
    row_version: Optional[int] = Field(default=None, ge=1)
    decision: str = Field(default="APPROVED", max_length=30)
    comments: Optional[str] = None


class SOPRevisionActivateRequest(BaseModel):
    row_version: Optional[int] = Field(default=None, ge=1)
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    supersedes_revision_id: Optional[uuid.UUID] = None
    comments: Optional[str] = None

    @model_validator(mode="after")
    def _validate_window(self) -> "SOPRevisionActivateRequest":
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to <= self.effective_from
        ):
            raise ValueError("effective_to must be strictly after effective_from.")
        return self


class SOPRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    sop_id: uuid.UUID
    site_id: Optional[uuid.UUID] = None
    revision_number: str
    revision_sequence: int
    revision_description: Optional[str] = None
    change_reason: Optional[str] = None
    document_id: Optional[uuid.UUID] = None
    document_version_id: Optional[uuid.UUID] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    revision_status: str
    approval_status: str
    submitted_by_membership_id: Optional[uuid.UUID] = None
    submitted_at: Optional[datetime] = None
    approved_by_user_id: Optional[uuid.UUID] = None
    approved_by_membership_id: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    approval_record_id: Optional[uuid.UUID] = None
    activated_by_membership_id: Optional[uuid.UUID] = None
    activated_at: Optional[datetime] = None
    supersedes_revision_id: Optional[uuid.UUID] = None
    superseded_by_revision_id: Optional[uuid.UUID] = None
    steps: List[SOPStepResponse] = Field(default_factory=list)
    row_version: int
    created_at: datetime
    updated_at: datetime
    created_by_user_id: Optional[uuid.UUID] = None
    updated_by_user_id: Optional[uuid.UUID] = None


# --- Historical SOP Resolution Schemas (C5) ---


class SOPResolutionResponse(BaseModel):
    outcome: str
    sop_id: Optional[uuid.UUID] = None
    sop_code: str
    site_id: Optional[uuid.UUID] = None
    evaluated_at: datetime
    revision: Optional[SOPRevisionResponse] = None
    sop_revision_id: Optional[uuid.UUID] = None
    revision_number: Optional[str] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    steps: List[SOPStepResponse] = Field(default_factory=list)
    candidate_revision_ids: List[uuid.UUID] = Field(default_factory=list)
    explanation: str
