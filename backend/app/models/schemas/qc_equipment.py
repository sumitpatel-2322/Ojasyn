import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# --- QC Test Definitions (Module C6) ---


class QCTestDefinitionCreateRequest(BaseModel):
    site_id: Optional[uuid.UUID] = None
    product_id: Optional[uuid.UUID] = None
    product_category: Optional[str] = Field(default=None, max_length=80)
    test_code: str = Field(..., min_length=1, max_length=80)
    version_number: str = Field(default="1.0", min_length=1, max_length=40)
    test_name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    test_method_reference: str = Field(..., min_length=1, max_length=150)
    sop_revision_id: Optional[uuid.UUID] = None
    specification_document_id: Optional[uuid.UUID] = None
    specification_document_version_id: Optional[uuid.UUID] = None
    test_type: Literal[
        "NUMERIC_RANGE",
        "NUMERIC_MIN",
        "NUMERIC_MAX",
        "TEXT_MATCH",
        "PASS_FAIL",
        "QUALITATIVE",
    ] = "NUMERIC_RANGE"
    expected_unit_code: Optional[str] = Field(default=None, max_length=30)
    min_limit: Optional[Decimal] = None
    max_limit: Optional[Decimal] = None
    target_value: Optional[Decimal] = None
    expected_text_specification: Optional[str] = Field(default=None, max_length=255)
    acceptance_criteria: str = Field(..., min_length=1)
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    status: Literal["DRAFT", "ACTIVE", "SUPERSEDED", "OBSOLETE"] = "ACTIVE"

    @model_validator(mode="after")
    def _validate_limits_and_dates(self) -> "QCTestDefinitionCreateRequest":
        if (
            self.min_limit is not None
            and self.max_limit is not None
            and self.min_limit > self.max_limit
        ):
            raise ValueError("min_limit must be less than or equal to max_limit.")
        if self.effective_to is not None:
            if self.effective_from is None or self.effective_to <= self.effective_from:
                raise ValueError(
                    "effective_to must be strictly after effective_from when specified."
                )
        return self


class QCTestDefinitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: Optional[uuid.UUID]
    product_id: Optional[uuid.UUID]
    product_category: Optional[str]
    test_code: str
    version_number: str
    test_name: str
    description: Optional[str]
    test_method_reference: str
    sop_revision_id: Optional[uuid.UUID]
    specification_document_id: Optional[uuid.UUID]
    specification_document_version_id: Optional[uuid.UUID]
    test_type: str
    expected_unit_code: Optional[str]
    min_limit: Optional[Decimal]
    max_limit: Optional[Decimal]
    target_value: Optional[Decimal]
    expected_text_specification: Optional[str]
    acceptance_criteria: str
    effective_from: Optional[datetime]
    effective_to: Optional[datetime]
    status: str
    created_at: datetime
    updated_at: datetime
    row_version: int


# --- QC Samples & Results (Module C6) ---


class QCSampleCreateRequest(BaseModel):
    sample_identifier: str = Field(..., min_length=1, max_length=80)
    sample_type: Literal[
        "RAW_MATERIAL",
        "IN_PROCESS",
        "BULK",
        "FINISHED_PRODUCT",
        "RETENTION",
        "STABILITY",
    ] = "FINISHED_PRODUCT"
    sampled_at: Optional[datetime] = None
    sampled_by_membership_id: Optional[uuid.UUID] = None
    sample_quantity: Decimal = Field(..., gt=Decimal("0"))
    sample_unit_code: str = Field(..., min_length=1, max_length=30)
    storage_location: Optional[str] = Field(default=None, max_length=120)
    supporting_document_id: Optional[uuid.UUID] = None
    supporting_document_version_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class QCTestResultCreateRequest(BaseModel):
    test_definition_id: uuid.UUID
    specification_version_applied: Optional[str] = Field(default=None, max_length=40)
    result_type: Literal["NUMERIC", "TEXT", "BOOLEAN"] = "NUMERIC"
    numeric_result: Optional[Decimal] = None
    text_result: Optional[str] = None
    boolean_result: Optional[bool] = None
    unit_code: Optional[str] = Field(default=None, max_length=30)
    equipment_id: Optional[uuid.UUID] = None
    tested_at: Optional[datetime] = None
    analyst_membership_id: Optional[uuid.UUID] = None
    supporting_document_id: Optional[uuid.UUID] = None
    supporting_document_version_id: Optional[uuid.UUID] = None
    comments: Optional[str] = None

    @model_validator(mode="after")
    def _validate_typed_value(self) -> "QCTestResultCreateRequest":
        if self.result_type == "NUMERIC" and self.numeric_result is None:
            raise ValueError("numeric_result is required when result_type is 'NUMERIC'.")
        if self.result_type == "TEXT" and (
            self.text_result is None or not self.text_result.strip()
        ):
            raise ValueError("text_result is required when result_type is 'TEXT'.")
        if self.result_type == "BOOLEAN" and self.boolean_result is None:
            raise ValueError("boolean_result is required when result_type is 'BOOLEAN'.")
        return self


class QCTestResultReviewRequest(BaseModel):
    row_version: Optional[int] = Field(default=None, ge=1)
    decision: Optional[Literal["APPROVED", "REJECTED"]] = None
    review_decision: Optional[Literal["APPROVED", "REJECTED"]] = None
    review_status: Optional[Literal["APPROVED", "REJECTED"]] = None
    result_outcome_override: Optional[
        Literal["PASS", "FAIL", "INVALID", "PENDING", "NOT_APPLICABLE", "UNVERIFIED"]
    ] = None
    comments: Optional[str] = Field(default=None, max_length=2000)

    def resolved_decision(self) -> str:
        return (
            self.decision
            or self.review_decision
            or self.review_status
            or "APPROVED"
        )


class QCTestResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: uuid.UUID
    batch_id: uuid.UUID
    qc_sample_id: uuid.UUID
    test_definition_id: uuid.UUID
    specification_version_applied: str
    result_type: str
    numeric_result: Optional[Decimal]
    text_result: Optional[str]
    boolean_result: Optional[bool]
    unit_code: Optional[str]
    equipment_id: Optional[uuid.UUID]
    tested_at: datetime
    analyst_user_id: Optional[uuid.UUID]
    analyst_membership_id: uuid.UUID
    reviewer_user_id: Optional[uuid.UUID]
    reviewer_membership_id: Optional[uuid.UUID]
    reviewed_at: Optional[datetime]
    approval_record_id: Optional[uuid.UUID]
    result_outcome: str
    review_status: str
    is_out_of_specification_flagged: bool
    supporting_document_id: Optional[uuid.UUID]
    supporting_document_version_id: Optional[uuid.UUID]
    comments: Optional[str]
    created_at: datetime
    updated_at: datetime
    row_version: int


class QCSampleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: uuid.UUID
    batch_id: uuid.UUID
    product_id: uuid.UUID
    sample_identifier: str
    sample_type: str
    sampled_at: datetime
    sampled_by_user_id: Optional[uuid.UUID]
    sampled_by_membership_id: uuid.UUID
    sample_quantity: Decimal
    sample_unit_code: str
    storage_location: Optional[str]
    testing_status: str
    supporting_document_id: Optional[uuid.UUID]
    supporting_document_version_id: Optional[uuid.UUID]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
    row_version: int
    results: List[QCTestResultResponse] = Field(default_factory=list)


class BatchQCHistoryResponse(BaseModel):
    batch_id: uuid.UUID
    site_id: uuid.UUID
    product_id: uuid.UUID
    total_samples: int
    total_results: int
    passed_count: int
    failed_count: int
    unverified_count: int
    pending_review_count: int
    approved_count: int
    rejected_count: int
    oos_flagged_count: int
    has_out_of_specification: bool
    samples: List[QCSampleResponse] = Field(default_factory=list)
    results: List[QCTestResultResponse] = Field(default_factory=list)


# --- Equipment & Calibration (Module C8) ---


class EquipmentCreateRequest(BaseModel):
    site_id: uuid.UUID
    department_id: Optional[uuid.UUID] = None
    equipment_code: str = Field(..., min_length=1, max_length=80)
    equipment_name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    equipment_category: str = Field(..., min_length=1, max_length=80)
    manufacturer: Optional[str] = Field(default=None, max_length=150)
    model_number: Optional[str] = Field(default=None, max_length=120)
    serial_number: Optional[str] = Field(default=None, max_length=120)
    commissioned_on: Optional[date] = None
    operational_status: Literal[
        "DRAFT",
        "ACTIVE",
        "OUT_OF_SERVICE",
        "UNDER_MAINTENANCE",
        "CALIBRATION_DUE",
        "DECOMMISSIONED",
    ] = "ACTIVE"
    qualification_status: Literal[
        "NOT_REQUIRED",
        "PENDING",
        "IQ_COMPLETED",
        "OQ_COMPLETED",
        "PQ_COMPLETED",
        "QUALIFIED",
        "REQUALIFICATION_DUE",
        "DISQUALIFIED",
    ] = "QUALIFIED"
    calibration_required: bool = True
    calibration_frequency_days: Optional[int] = Field(default=None, gt=0)
    calibration_specification_json: Optional[Dict[str, Any]] = None
    maintenance_metadata_json: Optional[Dict[str, Any]] = None


class EquipmentUpdateRequest(BaseModel):
    row_version: Optional[int] = Field(default=None, ge=1)
    department_id: Optional[uuid.UUID] = None
    equipment_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    equipment_category: Optional[str] = Field(default=None, min_length=1, max_length=80)
    manufacturer: Optional[str] = Field(default=None, max_length=150)
    model_number: Optional[str] = Field(default=None, max_length=120)
    serial_number: Optional[str] = Field(default=None, max_length=120)
    commissioned_on: Optional[date] = None
    operational_status: Optional[
        Literal[
            "DRAFT",
            "ACTIVE",
            "OUT_OF_SERVICE",
            "UNDER_MAINTENANCE",
            "CALIBRATION_DUE",
            "DECOMMISSIONED",
        ]
    ] = None
    qualification_status: Optional[
        Literal[
            "NOT_REQUIRED",
            "PENDING",
            "IQ_COMPLETED",
            "OQ_COMPLETED",
            "PQ_COMPLETED",
            "QUALIFIED",
            "REQUALIFICATION_DUE",
            "DISQUALIFIED",
        ]
    ] = None
    calibration_required: Optional[bool] = None
    calibration_frequency_days: Optional[int] = Field(default=None, gt=0)
    calibration_specification_json: Optional[Dict[str, Any]] = None
    maintenance_metadata_json: Optional[Dict[str, Any]] = None
    reason: Optional[str] = Field(default=None, max_length=1000)


class EquipmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: uuid.UUID
    department_id: Optional[uuid.UUID]
    equipment_code: str
    equipment_name: str
    description: Optional[str]
    equipment_category: str
    manufacturer: Optional[str]
    model_number: Optional[str]
    serial_number: Optional[str]
    commissioned_on: Optional[date]
    operational_status: str
    qualification_status: str
    calibration_required: bool
    calibration_frequency_days: Optional[int]
    calibration_specification_json: Optional[Dict[str, Any]]
    maintenance_metadata_json: Optional[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    row_version: int


class CalibrationRecordCreateRequest(BaseModel):
    calibration_code: str = Field(..., min_length=1, max_length=80)
    calibrated_at: datetime
    due_at: datetime
    calibration_provider: str = Field(..., min_length=1, max_length=200)
    certificate_number: Optional[str] = Field(default=None, max_length=120)
    certificate_document_id: Optional[uuid.UUID] = None
    certificate_document_version_id: Optional[uuid.UUID] = None
    calibration_result: Literal[
        "PASS", "FAIL", "LIMITED_USE", "OUT_OF_TOLERANCE"
    ] = "PASS"
    record_status: Literal["DRAFT", "UNDER_REVIEW"] = "DRAFT"
    recorded_by_membership_id: Optional[uuid.UUID] = None
    deviation_reference: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _validate_dates(self) -> "CalibrationRecordCreateRequest":
        if self.due_at <= self.calibrated_at:
            raise ValueError("due_at must be strictly after calibrated_at.")
        return self


class CalibrationRecordReviewRequest(BaseModel):
    row_version: Optional[int] = Field(default=None, ge=1)
    decision: Optional[Literal["APPROVED", "REJECTED"]] = None
    review_decision: Optional[Literal["APPROVED", "REJECTED"]] = None
    record_status: Optional[Literal["APPROVED", "REJECTED"]] = None
    deviation_reference: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=2000)
    comments: Optional[str] = Field(default=None, max_length=2000)

    def resolved_decision(self) -> str:
        return (
            self.decision
            or self.review_decision
            or self.record_status
            or "APPROVED"
        )


class CalibrationRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: uuid.UUID
    equipment_id: uuid.UUID
    calibration_code: str
    calibrated_at: datetime
    due_at: datetime
    calibration_provider: str
    certificate_number: Optional[str]
    certificate_document_id: Optional[uuid.UUID]
    certificate_document_version_id: Optional[uuid.UUID]
    calibration_result: str
    record_status: str
    recorded_by_user_id: Optional[uuid.UUID]
    recorded_by_membership_id: uuid.UUID
    reviewed_by_user_id: Optional[uuid.UUID]
    reviewed_by_membership_id: Optional[uuid.UUID]
    approved_at: Optional[datetime]
    approval_record_id: Optional[uuid.UUID]
    deviation_reference: Optional[str]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
    row_version: int


class CalibrationValidityResponse(BaseModel):
    equipment_id: uuid.UUID
    equipment_code: str
    equipment_name: str
    site_id: uuid.UUID
    calibration_required: bool
    evaluated_at_timestamp: datetime
    validity_status: Literal[
        "VALID", "EXPIRED", "NOT_CALIBRATED", "MISSING_RECORD", "UNVERIFIED"
    ]
    applicable_calibration_record_id: Optional[uuid.UUID] = None
    applicable_calibration_code: Optional[str] = None
    calibrated_at: Optional[datetime] = None
    due_at: Optional[datetime] = None
    calibration_result: Optional[str] = None
    record_status: Optional[str] = None
    certificate_document_version_id: Optional[uuid.UUID] = None
    explanation: str


class EquipmentUsageCreateRequest(BaseModel):
    equipment_id: uuid.UUID
    batch_execution_step_id: Optional[uuid.UUID] = None
    usage_start_at: datetime
    usage_end_at: Optional[datetime] = None
    operator_membership_id: Optional[uuid.UUID] = None
    usage_purpose: str = Field(..., min_length=1, max_length=200)
    status_at_time_of_use: Optional[str] = Field(default=None, max_length=30)
    supporting_document_id: Optional[uuid.UUID] = None
    supporting_document_version_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _validate_usage_window(self) -> "EquipmentUsageCreateRequest":
        if self.usage_end_at is not None and self.usage_end_at < self.usage_start_at:
            raise ValueError("usage_end_at must be greater than or equal to usage_start_at.")
        return self


class EquipmentUsageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: uuid.UUID
    equipment_id: uuid.UUID
    batch_id: uuid.UUID
    batch_execution_record_id: uuid.UUID
    batch_execution_step_id: Optional[uuid.UUID]
    usage_start_at: datetime
    usage_end_at: Optional[datetime]
    operator_user_id: Optional[uuid.UUID]
    operator_membership_id: uuid.UUID
    usage_purpose: str
    status_at_time_of_use: str
    calibration_status_at_use: str
    applicable_calibration_record_id: Optional[uuid.UUID]
    supporting_document_id: Optional[uuid.UUID]
    supporting_document_version_id: Optional[uuid.UUID]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
    row_version: int
