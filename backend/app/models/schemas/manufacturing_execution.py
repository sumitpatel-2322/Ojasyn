import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# --- Master Batch Records (MBR / MPR) ---


class PlannedStepTemplate(BaseModel):
    step_code: str = Field(..., min_length=1, max_length=60)
    step_sequence: int = Field(..., ge=1)
    description: str = Field(..., min_length=1, max_length=2000)
    sop_code: Optional[str] = Field(default=None, max_length=80)
    sop_step_code: Optional[str] = Field(default=None, max_length=60)
    is_mandatory: bool = True
    requires_checker_signoff: bool = False
    planned_requirement: Optional[str] = Field(default=None, max_length=2000)


class MasterBatchRecordCreateRequest(BaseModel):
    site_id: uuid.UUID
    product_id: uuid.UUID
    mbr_code: str = Field(..., min_length=2, max_length=80)
    version_number: str = Field(default="1.0", min_length=1, max_length=40)
    product_version_context: Optional[str] = Field(default=None, max_length=80)
    record_type: Literal["BMR", "BPR", "COMBINED"] = "BMR"
    title: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = Field(default=None, max_length=4000)
    target_batch_size_value: Optional[Decimal] = Field(default=None, gt=Decimal("0"))
    target_batch_size_unit: Optional[str] = Field(default=None, max_length=30)
    document_id: Optional[uuid.UUID] = None
    document_version_id: Optional[uuid.UUID] = None
    applicable_sop_revision_ids: List[uuid.UUID] = Field(default_factory=list)
    planned_steps_json: Optional[List[Dict[str, Any]]] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    supersedes_mbr_id: Optional[uuid.UUID] = None

    @model_validator(mode="after")
    def _validate_effective_window(self) -> "MasterBatchRecordCreateRequest":
        if self.effective_to is not None:
            if self.effective_from is None or self.effective_to <= self.effective_from:
                raise ValueError(
                    "effective_to must be strictly greater than effective_from when specified."
                )
        return self


class MasterBatchRecordUpdateRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    title: Optional[str] = Field(default=None, min_length=2, max_length=255)
    description: Optional[str] = Field(default=None, max_length=4000)
    product_version_context: Optional[str] = Field(default=None, max_length=80)
    target_batch_size_value: Optional[Decimal] = Field(default=None, gt=Decimal("0"))
    target_batch_size_unit: Optional[str] = Field(default=None, max_length=30)
    document_id: Optional[uuid.UUID] = None
    document_version_id: Optional[uuid.UUID] = None
    applicable_sop_revision_ids: Optional[List[uuid.UUID]] = None
    planned_steps_json: Optional[List[Dict[str, Any]]] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    reason: Optional[str] = Field(default=None, max_length=1000)


class MasterBatchRecordApproveRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    decision: Literal["APPROVED", "REJECTED"] = "APPROVED"
    comments: Optional[str] = Field(default=None, max_length=2000)


class MasterBatchRecordActivateRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    reason: Optional[str] = Field(default=None, max_length=1000)


class MasterBatchRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: uuid.UUID
    product_id: uuid.UUID
    mbr_code: str
    version_number: str
    product_version_context: Optional[str]
    record_type: str
    title: str
    description: Optional[str]
    target_batch_size_value: Optional[Decimal]
    target_batch_size_unit: Optional[str]
    document_id: Optional[uuid.UUID]
    document_version_id: Optional[uuid.UUID]
    applicable_sop_revision_ids: List[str] = Field(default_factory=list)
    planned_steps_json: Optional[List[Dict[str, Any]]]
    effective_from: Optional[datetime]
    effective_to: Optional[datetime]
    approval_status: str
    approved_by_user_id: Optional[uuid.UUID]
    approved_by_membership_id: Optional[uuid.UUID]
    approved_at: Optional[datetime]
    approval_record_id: Optional[uuid.UUID]
    supersedes_mbr_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime
    created_by_user_id: Optional[uuid.UUID]
    row_version: int


# --- Batch Execution Steps & Observations ---


class BatchExecutionStepCreateRequest(BaseModel):
    step_code: str = Field(..., min_length=1, max_length=60)
    step_sequence: int = Field(..., ge=1)
    description: str = Field(..., min_length=2, max_length=2000)
    sop_id: Optional[uuid.UUID] = None
    sop_code: Optional[str] = Field(default=None, max_length=80)
    sop_revision_id: Optional[uuid.UUID] = None
    sop_step_id: Optional[uuid.UUID] = None
    planned_requirement: Optional[str] = Field(default=None, max_length=2000)
    requires_checker_signoff: bool = False
    equipment_id: Optional[uuid.UUID] = None
    supporting_document_version_id: Optional[uuid.UUID] = None
    supporting_evidence_json: Optional[Dict[str, Any]] = None


class BatchExecutionStepUpdateRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    step_status: Optional[
        Literal["PENDING", "IN_PROGRESS", "COMPLETED", "EXCEPTION", "SKIPPED"]
    ] = None
    started_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    equipment_id: Optional[uuid.UUID] = None
    sop_id: Optional[uuid.UUID] = None
    sop_code: Optional[str] = Field(default=None, max_length=80)
    sop_revision_id: Optional[uuid.UUID] = None
    sop_step_id: Optional[uuid.UUID] = None
    comments: Optional[str] = Field(default=None, max_length=2000)
    exception_reason: Optional[str] = Field(default=None, max_length=2000)
    supporting_document_version_id: Optional[uuid.UUID] = None
    supporting_evidence_json: Optional[Dict[str, Any]] = None


class BatchExecutionStepExecuteRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    outcome_status: Literal["COMPLETED", "EXCEPTION", "SKIPPED"] = "COMPLETED"
    started_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    equipment_id: Optional[uuid.UUID] = None
    comments: Optional[str] = Field(default=None, max_length=2000)
    exception_reason: Optional[str] = Field(default=None, max_length=2000)
    supporting_document_version_id: Optional[uuid.UUID] = None
    supporting_evidence_json: Optional[Dict[str, Any]] = None


class BatchExecutionStepVerifyRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    comments: Optional[str] = Field(default=None, max_length=2000)


class ProcessParameterObservationCreateRequest(BaseModel):
    sop_step_id: Optional[uuid.UUID] = None
    parameter_code: str = Field(..., min_length=1, max_length=80)
    parameter_name: str = Field(..., min_length=1, max_length=150)
    value_type: Literal["NUMERIC", "TEXT", "BOOLEAN"] = "NUMERIC"
    numeric_value: Optional[Decimal] = None
    text_value: Optional[str] = None
    boolean_value: Optional[bool] = None
    unit_code: Optional[str] = Field(default=None, max_length=30)
    observed_at: Optional[datetime] = None
    equipment_id: Optional[uuid.UUID] = None
    source_document_id: Optional[uuid.UUID] = None
    source_document_version_id: Optional[uuid.UUID] = None
    source_page_number: Optional[int] = Field(default=None, ge=1)
    source_section_ref: Optional[str] = Field(default=None, max_length=120)
    extraction_method: Literal[
        "MANUAL_ENTRY", "INSTRUMENT_IMPORT", "OCR_EXTRACTED", "SYSTEM_CALCULATED"
    ] = "MANUAL_ENTRY"
    extraction_confidence: Optional[Decimal] = Field(
        default=None, ge=Decimal("0"), le=Decimal("1")
    )
    notes: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _validate_typed_value(self) -> "ProcessParameterObservationCreateRequest":
        if self.value_type == "NUMERIC" and self.numeric_value is None:
            raise ValueError("numeric_value is required when value_type is 'NUMERIC'.")
        if self.value_type == "TEXT" and (self.text_value is None or not self.text_value.strip()):
            raise ValueError("text_value is required when value_type is 'TEXT'.")
        if self.value_type == "BOOLEAN" and self.boolean_value is None:
            raise ValueError("boolean_value is required when value_type is 'BOOLEAN'.")
        return self


class ProcessParameterObservationVerifyRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    verification_status: Literal["VERIFIED", "REJECTED", "FLAGGED"] = "VERIFIED"
    notes: Optional[str] = Field(default=None, max_length=2000)


class ProcessParameterObservationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: uuid.UUID
    batch_execution_record_id: uuid.UUID
    batch_execution_step_id: uuid.UUID
    sop_step_id: Optional[uuid.UUID]
    parameter_code: str
    parameter_name: str
    value_type: str
    numeric_value: Optional[Decimal]
    text_value: Optional[str]
    boolean_value: Optional[bool]
    unit_code: Optional[str]
    observed_at: datetime
    equipment_id: Optional[uuid.UUID]
    operator_user_id: Optional[uuid.UUID]
    operator_membership_id: uuid.UUID
    verified_by_user_id: Optional[uuid.UUID]
    verified_by_membership_id: Optional[uuid.UUID]
    verified_at: Optional[datetime]
    verification_status: str
    source_document_id: Optional[uuid.UUID]
    source_document_version_id: Optional[uuid.UUID]
    source_page_number: Optional[int]
    source_section_ref: Optional[str]
    extraction_method: str
    extraction_confidence: Optional[Decimal]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
    row_version: int


class BatchExecutionStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: uuid.UUID
    batch_execution_record_id: uuid.UUID
    step_code: str
    step_sequence: int
    description: str
    sop_id: Optional[uuid.UUID]
    sop_code: Optional[str]
    sop_revision_id: Optional[uuid.UUID]
    sop_step_id: Optional[uuid.UUID]
    planned_requirement: Optional[str]
    requires_checker_signoff: bool
    step_status: str
    operator_user_id: Optional[uuid.UUID]
    operator_membership_id: Optional[uuid.UUID]
    checker_user_id: Optional[uuid.UUID]
    checker_membership_id: Optional[uuid.UUID]
    started_at: Optional[datetime]
    executed_at: Optional[datetime]
    checked_at: Optional[datetime]
    equipment_id: Optional[uuid.UUID]
    comments: Optional[str]
    exception_reason: Optional[str]
    supporting_document_version_id: Optional[uuid.UUID]
    supporting_evidence_json: Optional[Dict[str, Any]]
    observations: List[ProcessParameterObservationResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    row_version: int


# --- Batch Execution Record ---


class BatchExecutionRecordCreateRequest(BaseModel):
    master_batch_record_id: uuid.UUID
    execution_code: str = Field(..., min_length=2, max_length=80)
    execution_type: Literal["BMR", "BPR", "COMBINED"] = "BMR"
    manufacturing_stage: str = Field(..., min_length=2, max_length=100)
    execution_sequence: int = Field(default=1, ge=1)
    started_at: Optional[datetime] = None
    operator_membership_id: Optional[uuid.UUID] = None
    primary_document_id: Optional[uuid.UUID] = None
    primary_document_version_id: Optional[uuid.UUID] = None
    supporting_document_version_ids: List[uuid.UUID] = Field(default_factory=list)
    notes: Optional[str] = Field(default=None, max_length=2000)
    steps: List[BatchExecutionStepCreateRequest] = Field(default_factory=list)


class BatchExecutionRecordUpdateRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    manufacturing_stage: Optional[str] = Field(default=None, min_length=2, max_length=100)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    primary_document_id: Optional[uuid.UUID] = None
    primary_document_version_id: Optional[uuid.UUID] = None
    supporting_document_version_ids: Optional[List[uuid.UUID]] = None
    notes: Optional[str] = Field(default=None, max_length=2000)


class BatchExecutionLifecycleActionRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    occurred_at: Optional[datetime] = None
    comments: Optional[str] = Field(default=None, max_length=2000)


class BatchExecutionRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: uuid.UUID
    batch_id: uuid.UUID
    master_batch_record_id: uuid.UUID
    execution_code: str
    execution_type: str
    manufacturing_stage: str
    execution_sequence: int
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    execution_status: str
    operator_user_id: Optional[uuid.UUID]
    operator_membership_id: uuid.UUID
    checker_user_id: Optional[uuid.UUID]
    checker_membership_id: Optional[uuid.UUID]
    checked_at: Optional[datetime]
    approved_by_membership_id: Optional[uuid.UUID]
    approved_at: Optional[datetime]
    approval_record_id: Optional[uuid.UUID]
    primary_document_id: Optional[uuid.UUID]
    primary_document_version_id: Optional[uuid.UUID]
    supporting_document_version_ids: List[str] = Field(default_factory=list)
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
    row_version: int


class BatchExecutionRecordDetailResponse(BatchExecutionRecordResponse):
    steps: List[BatchExecutionStepResponse] = Field(default_factory=list)
    observations: List[ProcessParameterObservationResponse] = Field(default_factory=list)


# --- Deterministic Checks & Consolidated Batch Manufacturing Evidence ---


class DeterministicEvidenceCheckResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: uuid.UUID
    batch_id: uuid.UUID
    check_type: str
    rule_identifier: str
    rule_version: str
    source_entity_type: str
    source_entity_id: uuid.UUID
    batch_execution_record_id: Optional[uuid.UUID]
    batch_execution_step_id: Optional[uuid.UUID]
    observation_id: Optional[uuid.UUID]
    sop_revision_id: Optional[uuid.UUID]
    sop_step_id: Optional[uuid.UUID]
    equipment_id: Optional[uuid.UUID]
    calibration_record_id: Optional[uuid.UUID]
    qc_test_definition_id: Optional[uuid.UUID]
    qc_test_result_id: Optional[uuid.UUID]
    actual_value_text: Optional[str]
    actual_numeric_value: Optional[Decimal]
    expected_requirement_text: Optional[str]
    expected_min_value: Optional[Decimal]
    expected_max_value: Optional[Decimal]
    unit_code: Optional[str]
    execution_timestamp: Optional[datetime]
    evaluated_at: datetime
    outcome: str
    explanation: str
    metadata_json: Optional[Dict[str, Any]]
    created_at: datetime


class SideBySideObservationSOPComparison(BaseModel):
    observation_id: uuid.UUID
    batch_execution_record_id: uuid.UUID
    batch_execution_step_id: uuid.UUID
    step_code: str
    parameter_code: str
    parameter_name: str
    value_type: str
    actual_numeric_value: Optional[Decimal] = None
    actual_text_value: Optional[str] = None
    actual_boolean_value: Optional[bool] = None
    actual_unit_code: Optional[str] = None
    observed_at: datetime
    verification_status: str
    sop_code: Optional[str] = None
    sop_resolution_outcome: str
    sop_revision_id: Optional[uuid.UUID] = None
    sop_revision_number: Optional[str] = None
    sop_step_id: Optional[uuid.UUID] = None
    sop_step_code: Optional[str] = None
    min_value: Optional[Decimal] = None
    max_value: Optional[Decimal] = None
    target_value: Optional[Decimal] = None
    expected_text_value: Optional[str] = None
    expected_unit_code: Optional[str] = None
    converted_numeric_value: Optional[Decimal] = None
    acceptance_criteria: Optional[str] = None
    compliance_outcome: str
    explanation: str
    source_document_version_id: Optional[uuid.UUID] = None
    source_page_number: Optional[int] = None
    source_section_ref: Optional[str] = None


class EvidenceSOPResolutionItem(BaseModel):
    batch_execution_record_id: uuid.UUID
    batch_execution_step_id: Optional[uuid.UUID] = None
    step_code: Optional[str] = None
    sop_code: str
    evaluated_at_timestamp: Optional[datetime] = None
    resolution_outcome: str
    resolved_sop_id: Optional[uuid.UUID] = None
    resolved_sop_revision_id: Optional[uuid.UUID] = None
    resolved_revision_number: Optional[str] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    explanation: str


class EvidenceEquipmentCalibrationItem(BaseModel):
    usage_or_source_id: uuid.UUID
    source_type: str
    equipment_id: uuid.UUID
    equipment_code: Optional[str] = None
    equipment_name: Optional[str] = None
    batch_execution_record_id: Optional[uuid.UUID] = None
    batch_execution_step_id: Optional[uuid.UUID] = None
    used_at: datetime
    calibration_validity_status: str
    applicable_calibration_record_id: Optional[uuid.UUID] = None
    calibration_code: Optional[str] = None
    calibrated_at: Optional[datetime] = None
    due_at: Optional[datetime] = None
    compliance_outcome: str
    explanation: str


class EvidenceQCTestResultItem(BaseModel):
    qc_test_result_id: uuid.UUID
    qc_sample_id: uuid.UUID
    sample_identifier: Optional[str] = None
    test_definition_id: uuid.UUID
    test_code: Optional[str] = None
    test_name: Optional[str] = None
    specification_version_applied: str
    result_type: str
    numeric_result: Optional[Decimal] = None
    text_result: Optional[str] = None
    boolean_result: Optional[bool] = None
    unit_code: Optional[str] = None
    expected_unit_code: Optional[str] = None
    min_limit: Optional[Decimal] = None
    max_limit: Optional[Decimal] = None
    expected_text_specification: Optional[str] = None
    tested_at: datetime
    review_status: str
    result_outcome: str
    is_out_of_specification_flagged: bool
    equipment_id: Optional[uuid.UUID] = None
    equipment_calibration_validity: Optional[str] = None
    deterministic_outcome: str
    explanation: str


class EvidenceDocumentVersionItem(BaseModel):
    document_id: uuid.UUID
    document_code: Optional[str] = None
    document_type: str
    title: str
    document_version_id: uuid.UUID
    version_number: int
    version_label: str
    original_filename: str
    mime_type: str
    byte_size: int
    checksum_sha256: str
    approval_status: str


class EvidenceCompletenessSummary(BaseModel):
    total_checks: int
    pass_count: int
    fail_count: int
    missing_count: int
    not_applicable_count: int
    unverified_count: int
    is_fully_compliant: bool
    has_blocking_failures: bool
    has_missing_or_unverified_evidence: bool
    missing_or_failed_items: List[str] = Field(default_factory=list)


class BatchManufacturingEvidenceResponse(BaseModel):
    batch_id: uuid.UUID
    organization_id: uuid.UUID
    site_id: uuid.UUID
    product_id: uuid.UUID
    batch_number: str
    batch_status: str
    quality_review_status: str
    manufacturing_date: date
    expiry_date: date
    master_batch_records: List[MasterBatchRecordResponse] = Field(default_factory=list)
    execution_records: List[BatchExecutionRecordDetailResponse] = Field(default_factory=list)
    sop_resolutions: List[EvidenceSOPResolutionItem] = Field(default_factory=list)
    side_by_side_parameter_comparisons: List[SideBySideObservationSOPComparison] = Field(
        default_factory=list
    )
    equipment_calibration_evidence: List[EvidenceEquipmentCalibrationItem] = Field(
        default_factory=list
    )
    qc_results_evidence: List[EvidenceQCTestResultItem] = Field(default_factory=list)
    referenced_document_versions: List[EvidenceDocumentVersionItem] = Field(default_factory=list)
    deterministic_checks: List[DeterministicEvidenceCheckResponse] = Field(default_factory=list)
    completeness_summary: EvidenceCompletenessSummary
