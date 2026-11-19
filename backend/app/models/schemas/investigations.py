import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import (
    FindingCategory,
    HypothesisCategory,
    InvestigationTriggerType,
    Severity,
    UncertaintyLevel,
)


class InvestigationCreateRequest(BaseModel):
    complaint_id: Optional[uuid.UUID] = None
    deviation_id: Optional[uuid.UUID] = None
    batch_id: Optional[uuid.UUID] = None
    product_id: Optional[uuid.UUID] = None
    site_id: Optional[uuid.UUID] = None
    trigger_type: Optional[InvestigationTriggerType] = None
    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    scope_summary: Optional[str] = Field(default=None, max_length=5000)
    owner_membership_id: Optional[uuid.UUID] = None
    due_at: Optional[datetime] = None


class InvestigationRunTriggerRequest(BaseModel):
    idempotency_key: Optional[str] = Field(default=None, max_length=120)
    workflow_version: str = Field(default="phase5-v1", max_length=40)
    max_attempts: int = Field(default=3, ge=1, le=10)


class InvestigationStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    investigation_id: uuid.UUID
    investigation_run_id: uuid.UUID
    step_seq: int
    node_name: str
    status: str
    input_refs_json: Optional[Dict[str, Any]]
    output_refs_json: Optional[Dict[str, Any]]
    error_message: Optional[str]
    started_at: datetime
    finished_at: Optional[datetime]


class InvestigationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    investigation_id: uuid.UUID
    run_number: int
    workflow_version: str
    model_name: Optional[str]
    prompt_version: Optional[str]
    rule_engine_version: Optional[str]
    status: str
    waiting_reason: Optional[str]
    resume_from_step_seq: Optional[int]
    checkpoint_state_json: Optional[Dict[str, Any]]
    attempt_count: int
    max_attempts: int
    idempotency_key: Optional[str]
    triggered_by_user_id: Optional[uuid.UUID]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    last_error: Optional[str]
    summary_json: Optional[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    row_version: int
    steps: List[InvestigationStepResponse] = Field(default_factory=list)


class InvestigationRunTriggerResponse(BaseModel):
    run_id: uuid.UUID
    investigation_id: uuid.UUID
    run_number: int
    status: str
    idempotent_replay: bool = False
    outbox_event_id: Optional[uuid.UUID] = None


class EvidenceReferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    investigation_id: uuid.UUID
    investigation_run_id: Optional[uuid.UUID]
    source_type: str
    source_table: str
    source_record_id: uuid.UUID
    source_row_version: Optional[int]
    document_id: Optional[uuid.UUID]
    document_version_id: Optional[uuid.UUID]
    document_checksum_sha256: Optional[str]
    page_number: Optional[int]
    section_ref: Optional[str]
    field_path: Optional[str]
    quoted_value: Optional[str]
    provenance_metadata_json: Optional[Dict[str, Any]]
    collected_by: str
    created_at: datetime


class SOPComplianceResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    investigation_id: uuid.UUID
    investigation_run_id: Optional[uuid.UUID]
    batch_id: Optional[uuid.UUID]
    sop_id: Optional[uuid.UUID]
    sop_revision_id: Optional[uuid.UUID]
    sop_step_id: Optional[uuid.UUID]
    deterministic_check_id: Optional[uuid.UUID]
    batch_execution_record_id: Optional[uuid.UUID]
    batch_execution_step_id: Optional[uuid.UUID]
    observation_id: Optional[uuid.UUID]
    equipment_id: Optional[uuid.UUID]
    calibration_record_id: Optional[uuid.UUID]
    qc_test_result_id: Optional[uuid.UUID]
    evidence_ref_id: Optional[uuid.UUID]
    check_type: str
    rule_identifier: str
    rule_version: str
    status: str
    actual_value_text: Optional[str]
    actual_numeric_value: Optional[Decimal]
    expected_requirement_text: Optional[str]
    expected_min_value: Optional[Decimal]
    expected_max_value: Optional[Decimal]
    unit_code: Optional[str]
    explanation: str
    deviation_suggested: bool
    suggested_deviation_payload_json: Optional[Dict[str, Any]]
    evaluated_at: datetime
    created_at: datetime


class SOPComplianceRunRequest(BaseModel):
    batch_id: Optional[uuid.UUID] = None
    run_id: Optional[uuid.UUID] = None


class InvestigationFindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    investigation_id: uuid.UUID
    investigation_run_id: Optional[uuid.UUID]
    finding_code: str
    category: str
    title: str
    statement: str
    is_verified_observation: bool
    severity: Optional[str]
    evidence_ref_ids_json: List[str] = Field(default_factory=list)
    sop_compliance_result_id: Optional[uuid.UUID]
    source_origin: str
    created_at: datetime
    updated_at: datetime
    row_version: int


class RootCauseHypothesisResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    investigation_id: uuid.UUID
    investigation_run_id: Optional[uuid.UUID]
    hypothesis_code: str
    category: str
    title: str
    description: str
    supporting_evidence_ids_json: List[str] = Field(default_factory=list)
    contradicting_evidence_ids_json: List[str] = Field(default_factory=list)
    uncertainty_level: str
    confidence_score: Optional[Decimal]
    recommended_verification: Optional[str]
    limitations: Optional[str]
    status: str
    model_name: Optional[str]
    prompt_version: Optional[str]
    reviewed_by_user_id: Optional[uuid.UUID]
    reviewed_by_membership_id: Optional[uuid.UUID]
    reviewed_at: Optional[datetime]
    review_comments: Optional[str]
    approval_record_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime
    row_version: int


class GeneratedReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    investigation_id: uuid.UUID
    investigation_run_id: Optional[uuid.UUID]
    report_number: str
    version_number: int
    title: str
    status: str
    document_id: Optional[uuid.UUID]
    document_version_id: Optional[uuid.UUID]
    checksum_sha256: Optional[str]
    report_snapshot_json: Optional[Dict[str, Any]]
    generated_by_user_id: Optional[uuid.UUID]
    generated_at: datetime
    reviewed_by_user_id: Optional[uuid.UUID]
    reviewed_by_membership_id: Optional[uuid.UUID]
    reviewed_at: Optional[datetime]
    review_comments: Optional[str]
    approval_record_id: Optional[uuid.UUID]
    supersedes_report_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime
    row_version: int


class InvestigationSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    complaint_id: Optional[uuid.UUID]
    deviation_id: Optional[uuid.UUID]
    batch_id: Optional[uuid.UUID]
    product_id: Optional[uuid.UUID]
    site_id: Optional[uuid.UUID]
    investigation_number: str
    trigger_type: str
    title: str
    scope_summary: Optional[str]
    status: str
    owner_membership_id: Optional[uuid.UUID]
    due_at: Optional[datetime]
    opened_at: datetime
    completed_at: Optional[datetime]
    qa_reviewed_by_user_id: Optional[uuid.UUID]
    qa_reviewed_by_membership_id: Optional[uuid.UUID]
    qa_reviewed_at: Optional[datetime]
    qa_decision: Optional[str]
    qa_decision_comments: Optional[str]
    approval_record_id: Optional[uuid.UUID]
    closed_at: Optional[datetime]
    closure_reason: Optional[str]
    created_at: datetime
    updated_at: datetime
    row_version: int


class InvestigationDetailResponse(InvestigationSummaryResponse):
    runs: List[InvestigationRunResponse] = Field(default_factory=list)
    evidence_references: List[EvidenceReferenceResponse] = Field(default_factory=list)
    compliance_results: List[SOPComplianceResultResponse] = Field(default_factory=list)
    findings: List[InvestigationFindingResponse] = Field(default_factory=list)
    hypotheses: List[RootCauseHypothesisResponse] = Field(default_factory=list)
    reports: List[GeneratedReportResponse] = Field(default_factory=list)


# --- QA Review & Report Generation DTOs ---


class HypothesisDecisionRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    decision: Literal[
        "UNDER_REVIEW",
        "NEEDS_MORE_EVIDENCE",
        "ACCEPTED",
        "REJECTED",
        "INCONCLUSIVE",
    ]
    comments: str = Field(..., min_length=3, max_length=2000)


class ReportGenerateRequest(BaseModel):
    run_id: Optional[uuid.UUID] = None
    title: Optional[str] = Field(default=None, min_length=3, max_length=255)


class ReportReviewRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    decision: Literal["APPROVED", "REJECTED", "REQUEST_MORE_WORK"]
    comments: str = Field(..., min_length=3, max_length=2000)


class InvestigationReviewRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    decision: Literal["APPROVED", "REJECTED", "REQUEST_MORE_WORK"]
    comments: str = Field(..., min_length=3, max_length=2000)
    closure_reason: Optional[str] = Field(default=None, max_length=2000)


# --- Structured LLM Output DTOs (strictly evidence-cited; never generates CAPA records) ---


class RCASynthesisFindingItem(BaseModel):
    category: FindingCategory = FindingCategory.OBSERVATION
    title: str = Field(..., min_length=3, max_length=255)
    statement: str = Field(..., min_length=5, max_length=4000)
    severity: Optional[Severity] = None
    evidence_ref_ids: List[uuid.UUID] = Field(default_factory=list)


class RCASynthesisHypothesisItem(BaseModel):
    category: HypothesisCategory = HypothesisCategory.UNDETERMINED
    title: str = Field(..., min_length=3, max_length=255)
    description: str = Field(..., min_length=5, max_length=5000)
    supporting_evidence_ids: List[uuid.UUID] = Field(default_factory=list)
    contradicting_evidence_ids: List[uuid.UUID] = Field(default_factory=list)
    uncertainty_level: UncertaintyLevel = UncertaintyLevel.MEDIUM
    confidence_score: Optional[Decimal] = Field(
        default=Decimal("0.6000"), ge=Decimal("0"), le=Decimal("1")
    )
    recommended_verification: Optional[str] = Field(default=None, max_length=2000)
    limitations: Optional[str] = Field(default=None, max_length=2000)


class RCASynthesisLLMOutput(BaseModel):
    executive_summary: str = Field(..., min_length=5, max_length=4000)
    findings: List[RCASynthesisFindingItem] = Field(default_factory=list)
    hypotheses: List[RCASynthesisHypothesisItem] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)


class RCASynthesisWireFindingItem(BaseModel):
    category: FindingCategory = FindingCategory.OBSERVATION
    title: str = Field(..., min_length=3, max_length=255)
    statement: str = Field(..., min_length=5, max_length=4000)
    severity: Optional[Severity] = None
    evidence_ref_ids: List[str] = Field(default_factory=list)


class RCASynthesisWireHypothesisItem(BaseModel):
    category: HypothesisCategory = HypothesisCategory.UNDETERMINED
    title: str = Field(..., min_length=3, max_length=255)
    description: str = Field(..., min_length=5, max_length=5000)
    supporting_evidence_ids: List[str] = Field(default_factory=list)
    contradicting_evidence_ids: List[str] = Field(default_factory=list)
    uncertainty_level: UncertaintyLevel = UncertaintyLevel.MEDIUM
    confidence_score: Optional[Decimal] = Field(
        default=Decimal("0.6000"), ge=Decimal("0"), le=Decimal("1")
    )
    recommended_verification: Optional[str] = Field(default=None, max_length=2000)
    limitations: Optional[str] = Field(default=None, max_length=2000)


class RCASynthesisLLMDispatchOutput(BaseModel):
    """LLM wire DTO accepting opaque evidence aliases (e.g., EV-001) or UUID strings."""

    executive_summary: str = Field(..., min_length=5, max_length=4000)
    findings: List[RCASynthesisWireFindingItem] = Field(default_factory=list)
    hypotheses: List[RCASynthesisWireHypothesisItem] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Legacy compatibility schemas (used by app/api/investigations.py and
# app/graph/investigation_workflow.py while MOUNT_LEGACY_ROUTERS=true).
# ---------------------------------------------------------------------------


class LinkBatchRequest(BaseModel):
    batch_id: int = Field(..., description="The ID of the batch to link to the complaint")


class AIInvestigationResult(BaseModel):
    root_cause_analysis: str = Field(..., description="AI-generated root cause hypothesis")
    capa_recommendation: str = Field(..., description="AI-generated Corrective and Preventive Actions")
    risk_level: str = Field(..., description="High, Medium, or Low based on production history")
    confidence_score: int = Field(..., description="AI confidence score 1-100")


class InvestigationResponse(BaseModel):
    complaint_id: str
    linked_batch_id: int
    investigation_status: str
    ai_analysis: Optional[AIInvestigationResult] = None
