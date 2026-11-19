import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.domain.enums import ComplaintSource, Priority, Severity


class ComplainantInput(BaseModel):
    complainant_type: str = Field(default="CUSTOMER", max_length=30)
    full_name: str = Field(..., min_length=2, max_length=150)
    email: Optional[EmailStr] = None
    phone_e164: Optional[str] = Field(default=None, max_length=20)
    city: Optional[str] = Field(default=None, max_length=100)
    state_region: Optional[str] = Field(default=None, max_length=100)
    postal_code: Optional[str] = Field(default=None, max_length=20)
    country_code: str = Field(default="IN", min_length=2, max_length=2)


class ComplaintRegisterRequest(BaseModel):
    organization_id: Optional[uuid.UUID] = None
    site_id: Optional[uuid.UUID] = None
    source_channel: str = Field(default=ComplaintSource.WEBSITE.value, max_length=30)
    source_record_id: Optional[uuid.UUID] = None
    idempotency_key: Optional[str] = Field(default=None, max_length=120)

    complainant: Optional[ComplainantInput] = None
    complainant_id: Optional[uuid.UUID] = None

    product_id: Optional[uuid.UUID] = None
    reported_product_name: Optional[str] = Field(default=None, max_length=200)
    reported_batch_number: Optional[str] = Field(default=None, max_length=100)
    reported_manufacturer: Optional[str] = Field(default=None, max_length=200)
    reported_strength: Optional[str] = Field(default=None, max_length=100)
    reported_dosage_form: Optional[str] = Field(default=None, max_length=100)
    reported_manufacturing_date: Optional[date] = None
    reported_expiry_date: Optional[date] = None
    originating_site_block: Optional[str] = Field(default=None, max_length=120)
    impacted_non_product_material: Optional[str] = Field(default=None, max_length=150)

    description: str = Field(..., min_length=5, max_length=10000)
    defect_category: Optional[str] = Field(default=None, max_length=80)
    reported_onset_at: Optional[datetime] = None
    purchase_date: Optional[date] = None
    purchase_location: Optional[str] = Field(default=None, max_length=200)
    affected_quantity_value: Optional[Decimal] = None
    affected_quantity_unit: Optional[str] = Field(default=None, max_length=30)


class ComplaintRegisterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    complaint_number: str
    organization_id: Optional[uuid.UUID]
    site_id: Optional[uuid.UUID]
    status: str
    source_channel: str
    reported_product_name: Optional[str]
    reported_batch_number: Optional[str]
    due_at: Optional[datetime]
    created_at: datetime
    row_version: int
    idempotent_replay: bool = False
    # Returned once on initial creation for guest/external callers; never returned on replay
    tracking_token: Optional[str] = None


class ComplaintClassificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_type: str
    suggested_severity: Optional[str]
    suggested_priority: Optional[str]
    defect_category: Optional[str]
    model_name: Optional[str]
    prompt_version: Optional[str]
    confidence_score: Optional[Decimal]
    rationale: Optional[str]
    risk_indicators_json: Optional[Dict[str, Any]]
    decided_by_membership_id: Optional[uuid.UUID]
    override_reason: Optional[str]
    created_at: datetime


class ComplaintBatchLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    complaint_id: uuid.UUID
    batch_id: Optional[uuid.UUID]
    reported_batch_number: Optional[str]
    resolution_outcome: str
    match_method: Optional[str]
    match_score: Optional[Decimal]
    match_status: str
    verified_by_membership_id: Optional[uuid.UUID]
    verified_at: Optional[datetime]
    match_evidence_json: Optional[Dict[str, Any]]
    created_at: datetime


class ComplaintAttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: Optional[uuid.UUID]
    complaint_id: uuid.UUID
    document_id: uuid.UUID
    attachment_type: str
    caption: Optional[str]
    is_external_visible: bool
    created_at: datetime


class ComplaintEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    from_status: Optional[str]
    to_status: Optional[str]
    summary: str
    occurred_at: datetime


class ComplaintAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: Optional[uuid.UUID]
    complaint_id: uuid.UUID
    assignee_membership_id: uuid.UUID
    assigned_by_user_id: uuid.UUID
    assignment_role: str
    reason: Optional[str]
    is_active: bool
    assigned_at: datetime


class SLAClockResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sla_stage: str
    status: str
    started_at: datetime
    due_at: datetime
    paused_at: Optional[datetime]
    completed_at: Optional[datetime]
    breached_at: Optional[datetime]
    accumulated_pause_seconds: int


class ComplaintCommunicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel: str
    direction: str
    subject: Optional[str]
    body_text: str
    is_internal_only: bool
    is_information_request: bool
    sent_at: datetime


class ComplaintSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    complaint_number: str
    organization_id: Optional[uuid.UUID]
    site_id: Optional[uuid.UUID]
    source_channel: str
    reported_product_name: Optional[str]
    reported_batch_number: Optional[str]
    defect_category: Optional[str]
    ai_severity: Optional[str]
    ai_priority: Optional[str]
    final_severity: Optional[str]
    final_priority: Optional[str]
    status: str
    owner_membership_id: Optional[uuid.UUID]
    due_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    row_version: int


class ComplaintDetailResponse(ComplaintSummaryResponse):
    complainant_id: Optional[uuid.UUID]
    product_id: Optional[uuid.UUID]
    reported_manufacturer: Optional[str]
    reported_strength: Optional[str]
    reported_dosage_form: Optional[str]
    reported_manufacturing_date: Optional[date]
    reported_expiry_date: Optional[date]
    originating_site_block: Optional[str]
    impacted_non_product_material: Optional[str]
    description: str
    reported_onset_at: Optional[datetime]
    purchase_date: Optional[date]
    purchase_location: Optional[str]
    affected_quantity_value: Optional[Decimal]
    affected_quantity_unit: Optional[str]
    acknowledged_at: Optional[datetime]
    closed_at: Optional[datetime]
    closure_reason: Optional[str]
    classifications: List[ComplaintClassificationResponse] = Field(default_factory=list)
    batch_links: List[ComplaintBatchLinkResponse] = Field(default_factory=list)
    attachments: List[ComplaintAttachmentResponse] = Field(default_factory=list)
    events: List[ComplaintEventResponse] = Field(default_factory=list)
    assignments: List[ComplaintAssignmentResponse] = Field(default_factory=list)
    sla_clocks: List[SLAClockResponse] = Field(default_factory=list)
    communications: List[ComplaintCommunicationResponse] = Field(default_factory=list)


class ComplaintPublicStatusResponse(BaseModel):
    """Sanitized status response for guest tracking-token lookups (zero internal PII or QA notes)."""

    id: uuid.UUID
    complaint_number: str
    status: str
    source_channel: str
    reported_product_name: Optional[str]
    reported_batch_number: Optional[str]
    defect_category: Optional[str]
    acknowledged_at: Optional[datetime]
    due_at: Optional[datetime]
    closed_at: Optional[datetime]
    created_at: datetime
    events: List[ComplaintEventResponse] = Field(default_factory=list)
    communications: List[ComplaintCommunicationResponse] = Field(default_factory=list)
    attachments: List[ComplaintAttachmentResponse] = Field(default_factory=list)


class ComplaintUpdateRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    product_id: Optional[uuid.UUID] = None
    reported_product_name: Optional[str] = Field(default=None, max_length=200)
    reported_batch_number: Optional[str] = Field(default=None, max_length=100)
    reported_manufacturer: Optional[str] = Field(default=None, max_length=200)
    reported_strength: Optional[str] = Field(default=None, max_length=100)
    reported_dosage_form: Optional[str] = Field(default=None, max_length=100)
    reported_manufacturing_date: Optional[date] = None
    reported_expiry_date: Optional[date] = None
    defect_category: Optional[str] = Field(default=None, max_length=80)
    description: Optional[str] = Field(default=None, min_length=5, max_length=10000)
    site_id: Optional[uuid.UUID] = None
    reason: Optional[str] = Field(default=None, max_length=1000)


class ComplaintAssignRequest(BaseModel):
    assignee_membership_id: uuid.UUID
    assignment_role: str = Field(default="OWNER", max_length=30)
    due_at: Optional[datetime] = None
    reason: str = Field(..., min_length=3, max_length=1000)
    row_version: Optional[int] = Field(default=None, ge=1)


class ComplaintTriageRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    severity: Severity
    priority: Priority
    defect_category: Optional[str] = Field(default=None, max_length=80)
    target_status: Literal["TRIAGED", "WAITING_INFO"] = "TRIAGED"
    override_reason: Optional[str] = Field(default=None, max_length=1000)
    rationale: Optional[str] = Field(default=None, max_length=2000)


class ComplaintRouteRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    organization_id: uuid.UUID
    site_id: Optional[uuid.UUID] = None
    reason: str = Field(..., min_length=3, max_length=1000)


class BatchResolutionRequest(BaseModel):
    action: Literal["VERIFY_CANDIDATE", "MANUAL_LINK", "MARK_NOT_FOUND_OR_INSUFFICIENT"]
    candidate_link_id: Optional[uuid.UUID] = None
    batch_id: Optional[uuid.UUID] = None
    reported_batch_number: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = Field(default=None, max_length=1000)
    row_version: Optional[int] = Field(default=None, ge=1)


class ComplaintCommunicationCreateRequest(BaseModel):
    channel: str = Field(default="PORTAL", max_length=30)
    direction: Literal["INBOUND", "OUTBOUND", "INTERNAL"] = "OUTBOUND"
    subject: Optional[str] = Field(default=None, max_length=200)
    body_text: str = Field(..., min_length=1, max_length=10000)
    is_internal_only: bool = False


class InformationRequestCreateRequest(BaseModel):
    row_version: Optional[int] = Field(default=None, ge=1)
    channel: str = Field(default="PORTAL", max_length=30)
    subject: Optional[str] = Field(default=None, max_length=200)
    body_text: str = Field(..., min_length=3, max_length=10000)
    transition_to_waiting_info: bool = True


# --- AI Triage Structured DTO ---


class AITriageOutput(BaseModel):
    suggested_severity: Severity
    suggested_priority: Priority
    defect_category: str = Field(..., min_length=2, max_length=80)
    confidence_score: Decimal = Field(default=Decimal("0.8000"), ge=Decimal("0"), le=Decimal("1"))
    rationale: str = Field(..., min_length=3, max_length=2000)
    risk_indicators: Dict[str, Any] = Field(default_factory=dict)


# --- Copilot DTOs ---


class CopilotExtractedFields(BaseModel):
    reported_product_name: Optional[str] = None
    reported_batch_number: Optional[str] = None
    reported_manufacturer: Optional[str] = None
    reported_strength: Optional[str] = None
    reported_dosage_form: Optional[str] = None
    reported_manufacturing_date: Optional[str] = None
    reported_expiry_date: Optional[str] = None
    description: Optional[str] = None
    defect_category: Optional[str] = None
    reporter_name: Optional[str] = None
    reporter_email: Optional[str] = None
    reporter_phone: Optional[str] = None
    purchase_location: Optional[str] = None


class CopilotExtractionLLMOutput(BaseModel):
    extracted_fields: CopilotExtractedFields = Field(default_factory=CopilotExtractedFields)
    confidence_score: Decimal = Field(default=Decimal("0.8500"), ge=Decimal("0"), le=Decimal("1"))
    assistant_reply: str = Field(..., min_length=1, max_length=4000)


class CopilotIntentLLMOutput(BaseModel):
    intent: Literal["CREATE", "EDIT", "QUERY"]


class CopilotQueryLLMOutput(BaseModel):
    assistant_reply: str = Field(..., min_length=1, max_length=4000)


class CopilotCreateSessionRequest(BaseModel):
    organization_id: Optional[uuid.UUID] = None
    initial_message: Optional[str] = Field(default=None, max_length=4000)


class CopilotMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    message_text: str
    intent_type: Optional[str]
    extracted_fields_json: Optional[Dict[str, Any]]
    confidence_score: Optional[Decimal]
    created_at: datetime


class CopilotSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: Optional[uuid.UUID]
    user_id: Optional[uuid.UUID]
    session_status: str
    draft_state_json: Dict[str, Any]
    converted_complaint_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime
    row_version: int
    is_complete: bool = False
    missing_mandatory_fields: List[str] = Field(default_factory=list)
    missing_advisory_fields: List[str] = Field(default_factory=list)
    messages: List[CopilotMessageResponse] = Field(default_factory=list)
    # Returned once on guest session creation; never echoed again
    session_token: Optional[str] = None


class CopilotMessageCreateRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class CopilotSubmitRequest(BaseModel):
    organization_id: Optional[uuid.UUID] = None
    site_id: Optional[uuid.UUID] = None
    source_channel: str = Field(default=ComplaintSource.WEBSITE.value, max_length=30)
    idempotency_key: Optional[str] = Field(default=None, max_length=120)


class ComplaintCloseRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    closure_reason: str = Field(..., min_length=3, max_length=2000)
    resolution_notes: Optional[str] = Field(default=None, max_length=5000)


# ---------------------------------------------------------------------------
# Legacy compatibility schemas (used by app/api/complaints.py while
# MOUNT_LEGACY_ROUTERS=true).
# ---------------------------------------------------------------------------
from app.models.complaint import ComplaintState  # noqa: E402


class ChatRequest(BaseModel):
    """Payload received from the legacy frontend."""

    user_input: str
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    """Response sent back to the legacy frontend."""

    conversation_id: str
    message: str
    state: ComplaintState


class ErrorResponse(BaseModel):
    """Standardized legacy error response."""

    detail: str


