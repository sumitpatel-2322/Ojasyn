import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import DeviationClassification, DeviationSourceType


class DeviationCreateRequest(BaseModel):
    site_id: Optional[uuid.UUID] = None
    source_type: DeviationSourceType = DeviationSourceType.MANUAL
    classification: DeviationClassification = DeviationClassification.MAJOR
    is_mandatory_for_closure: bool = True
    title: str = Field(..., min_length=3, max_length=255)
    description: str = Field(..., min_length=5, max_length=10000)
    immediate_containment: Optional[str] = Field(default=None, max_length=5000)
    impact_assessment: Optional[str] = Field(default=None, max_length=5000)
    root_cause_summary: Optional[str] = Field(default=None, max_length=5000)
    complaint_id: Optional[uuid.UUID] = None
    investigation_id: Optional[uuid.UUID] = None
    batch_id: Optional[uuid.UUID] = None
    batch_execution_step_id: Optional[uuid.UUID] = None
    sop_compliance_result_id: Optional[uuid.UUID] = None
    qc_test_result_id: Optional[uuid.UUID] = None
    owner_membership_id: Optional[uuid.UUID] = None
    occurred_at: Optional[datetime] = None
    due_at: Optional[datetime] = None


class DeviationUpdateRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    classification: Optional[DeviationClassification] = None
    status: Optional[Literal["OPEN", "UNDER_INVESTIGATION", "CAPA_PENDING"]] = None
    is_mandatory_for_closure: Optional[bool] = None
    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, min_length=5, max_length=10000)
    immediate_containment: Optional[str] = Field(default=None, max_length=5000)
    impact_assessment: Optional[str] = Field(default=None, max_length=5000)
    root_cause_summary: Optional[str] = Field(default=None, max_length=5000)
    owner_membership_id: Optional[uuid.UUID] = None
    due_at: Optional[datetime] = None
    reason: Optional[str] = Field(default=None, max_length=1000)


class DeviationSubmitReviewRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    comments: Optional[str] = Field(default=None, max_length=2000)


class DeviationCloseRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    decision: Literal["APPROVED", "REJECTED", "REQUEST_MORE_WORK"] = "APPROVED"
    closure_comments: str = Field(..., min_length=3, max_length=2000)


class DeviationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: Optional[uuid.UUID]
    deviation_number: str
    source_type: str
    classification: str
    is_mandatory_for_closure: bool
    title: str
    description: str
    immediate_containment: Optional[str]
    impact_assessment: Optional[str]
    root_cause_summary: Optional[str]
    complaint_id: Optional[uuid.UUID]
    investigation_id: Optional[uuid.UUID]
    batch_id: Optional[uuid.UUID]
    batch_execution_step_id: Optional[uuid.UUID]
    sop_compliance_result_id: Optional[uuid.UUID]
    qc_test_result_id: Optional[uuid.UUID]
    owner_membership_id: Optional[uuid.UUID]
    status: str
    occurred_at: datetime
    due_at: Optional[datetime]
    submitted_for_review_by_user_id: Optional[uuid.UUID]
    submitted_for_review_by_membership_id: Optional[uuid.UUID]
    submitted_for_review_at: Optional[datetime]
    closed_by_user_id: Optional[uuid.UUID]
    closed_by_membership_id: Optional[uuid.UUID]
    closed_at: Optional[datetime]
    closure_comments: Optional[str]
    approval_record_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime
    created_by_user_id: Optional[uuid.UUID]
    updated_by_user_id: Optional[uuid.UUID]
    row_version: int
