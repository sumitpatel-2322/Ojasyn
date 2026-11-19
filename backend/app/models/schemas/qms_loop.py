import uuid
from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import (
    CAPAActionStatus,
    CAPAActionType,
    CAPASourceType,
    CAPAType,
    DeviationClassification,
    DeviationSourceType,
    EffectivenessCheckStatus,
    Severity,
)


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


# ---------------------------------------------------------------------------
# Phase 6 (E3): CAPA, CAPA Actions & Effectiveness Checks DTOs
# ---------------------------------------------------------------------------


class CAPACreateRequest(BaseModel):
    site_id: Optional[uuid.UUID] = None
    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, max_length=10000)
    capa_type: CAPAType = CAPAType.CORRECTIVE
    source_type: CAPASourceType = CAPASourceType.MANUAL
    complaint_id: Optional[uuid.UUID] = None
    investigation_id: Optional[uuid.UUID] = None
    deviation_id: Optional[uuid.UUID] = None
    is_mandatory_for_complaint_closure: bool = True
    severity: Severity = Severity.MEDIUM
    owner_membership_id: Optional[uuid.UUID] = None
    due_at: Optional[datetime] = None
    root_cause_summary: Optional[str] = Field(default=None, max_length=5000)


class CAPAUpdateRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    site_id: Optional[uuid.UUID] = None
    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, max_length=10000)
    capa_type: Optional[CAPAType] = None
    is_mandatory_for_complaint_closure: Optional[bool] = None
    severity: Optional[Severity] = None
    owner_membership_id: Optional[uuid.UUID] = None
    due_at: Optional[datetime] = None
    root_cause_summary: Optional[str] = Field(default=None, max_length=5000)
    reason: Optional[str] = Field(default=None, max_length=1000)


class CAPAOpenRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    comments: Optional[str] = Field(default=None, max_length=2000)


class CAPASubmitEffectivenessRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    comments: Optional[str] = Field(default=None, max_length=2000)


class CAPACloseRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    closure_reason: str = Field(..., min_length=3, max_length=2000)


class CAPAActionCreateRequest(BaseModel):
    action_type: CAPAActionType = CAPAActionType.CORRECTIVE
    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, max_length=10000)
    owner_membership_id: Optional[uuid.UUID] = None
    due_at: Optional[datetime] = None


class CAPAActionUpdateRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    action_type: Optional[CAPAActionType] = None
    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, max_length=10000)
    owner_membership_id: Optional[uuid.UUID] = None
    status: Optional[CAPAActionStatus] = None
    due_at: Optional[datetime] = None
    implementation_notes: Optional[str] = Field(default=None, max_length=5000)
    evidence_document_id: Optional[uuid.UUID] = None
    evidence_document_version_id: Optional[uuid.UUID] = None
    reason: Optional[str] = Field(default=None, max_length=1000)


class CAPAActionCompleteRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    implementation_notes: str = Field(..., min_length=3, max_length=5000)
    evidence_document_id: Optional[uuid.UUID] = None
    evidence_document_version_id: Optional[uuid.UUID] = None


class CAPAEffectivenessCheckCreateRequest(BaseModel):
    criteria_description: str = Field(..., min_length=3, max_length=5000)
    evaluation_method: Optional[str] = Field(default=None, max_length=5000)
    planned_check_date: date


class CAPAEffectivenessCheckRecordRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    status: Literal["EFFECTIVE", "INEFFECTIVE", "INCONCLUSIVE"]
    findings_summary: str = Field(..., min_length=3, max_length=5000)
    evidence_document_id: Optional[uuid.UUID] = None
    evidence_document_version_id: Optional[uuid.UUID] = None


class CAPAActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    capa_id: uuid.UUID
    action_seq: int
    action_type: str
    title: str
    description: Optional[str]
    owner_membership_id: uuid.UUID
    status: str
    due_at: Optional[datetime]
    completed_at: Optional[datetime]
    completed_by_user_id: Optional[uuid.UUID]
    implementation_notes: Optional[str]
    evidence_document_id: Optional[uuid.UUID]
    evidence_document_version_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime
    created_by_user_id: Optional[uuid.UUID]
    updated_by_user_id: Optional[uuid.UUID]
    row_version: int


class CAPAEffectivenessCheckResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    capa_id: uuid.UUID
    check_seq: int
    criteria_description: str
    evaluation_method: Optional[str]
    planned_check_date: date
    status: str
    reviewer_membership_id: Optional[uuid.UUID]
    reviewer_user_id: Optional[uuid.UUID]
    reviewed_at: Optional[datetime]
    findings_summary: Optional[str]
    evidence_document_id: Optional[uuid.UUID]
    evidence_document_version_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime
    created_by_user_id: Optional[uuid.UUID]
    updated_by_user_id: Optional[uuid.UUID]
    row_version: int


class CAPAResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: Optional[uuid.UUID]
    capa_number: str
    title: str
    description: Optional[str]
    capa_type: str
    source_type: str
    complaint_id: Optional[uuid.UUID]
    investigation_id: Optional[uuid.UUID]
    deviation_id: Optional[uuid.UUID]
    is_mandatory_for_complaint_closure: bool
    severity: str
    status: str
    owner_membership_id: uuid.UUID
    approver_membership_id: Optional[uuid.UUID]
    approved_by_user_id: Optional[uuid.UUID]
    approved_at: Optional[datetime]
    due_at: Optional[datetime]
    overdue_notified_at: Optional[datetime]
    closed_at: Optional[datetime]
    closure_reason: Optional[str]
    root_cause_summary: Optional[str]
    created_at: datetime
    updated_at: datetime
    created_by_user_id: Optional[uuid.UUID]
    updated_by_user_id: Optional[uuid.UUID]
    row_version: int


class CAPADetailResponse(CAPAResponse):
    actions: List[CAPAActionResponse] = Field(default_factory=list)
    effectiveness_checks: List[CAPAEffectivenessCheckResponse] = Field(default_factory=list)


class CAPAOverdueCheckResponse(BaseModel):
    enqueued_count: int
    overdue_capa_ids: List[uuid.UUID] = Field(default_factory=list)
    outbox_event_ids: List[uuid.UUID] = Field(default_factory=list)
