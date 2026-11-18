import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import (
    BatchDispositionDecision,
    BatchDocumentRole,
    BatchRelationType,
)


# --- Batch Management (C3) ---


class BatchCreateRequest(BaseModel):
    site_id: uuid.UUID
    product_id: uuid.UUID
    batch_number: str = Field(..., min_length=2, max_length=100)
    manufacturing_order_no: Optional[str] = Field(default=None, max_length=80)
    manufacturing_date: date
    expiry_date: date
    batch_size_value: Decimal = Field(..., gt=Decimal("0"))
    batch_size_unit: str = Field(..., min_length=1, max_length=30)
    packaging_configuration: Optional[str] = Field(default=None, max_length=200)
    primary_document_id: Optional[uuid.UUID] = None
    master_batch_record_id: Optional[uuid.UUID] = None
    initial_status: Literal["PLANNED", "IN_PROCESS"] = "PLANNED"
    reason: Optional[str] = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _validate_dates(self) -> "BatchCreateRequest":
        if self.expiry_date <= self.manufacturing_date:
            raise ValueError("expiry_date must be strictly after manufacturing_date.")
        return self


class BatchUpdateRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    manufacturing_order_no: Optional[str] = Field(default=None, max_length=80)
    expiry_date: Optional[date] = None
    batch_size_value: Optional[Decimal] = Field(default=None, gt=Decimal("0"))
    batch_size_unit: Optional[str] = Field(default=None, min_length=1, max_length=30)
    actual_yield_value: Optional[Decimal] = Field(default=None, ge=Decimal("0"))
    packaging_configuration: Optional[str] = Field(default=None, max_length=200)
    primary_document_id: Optional[uuid.UUID] = None
    master_batch_record_id: Optional[uuid.UUID] = None
    batch_status: Optional[str] = Field(default=None, max_length=40)
    reason: Optional[str] = Field(default=None, max_length=1000)


class BatchOperationalStatusRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    to_status: str = Field(..., min_length=2, max_length=40)
    reason: str = Field(..., min_length=3, max_length=1000)


class BatchDispositionChecklist(BaseModel):
    bmr_reviewed: bool
    qc_passed: bool
    open_critical_deviations: bool = False
    packaging_verified: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=2000)


class BatchDispositionRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    disposition_decision: BatchDispositionDecision
    proposed_by_membership_id: uuid.UUID
    checklist: BatchDispositionChecklist
    hold_or_rejection_reason: Optional[str] = Field(default=None, max_length=2000)
    certificate_document_id: Optional[uuid.UUID] = None
    comments: Optional[str] = Field(default=None, max_length=2000)


class BatchMaterialConsumptionCreateRequest(BaseModel):
    material_id: uuid.UUID
    supplier_lot_id: uuid.UUID
    quantity_consumed: Decimal = Field(..., gt=Decimal("0"))
    unit_code: str = Field(..., min_length=1, max_length=30)
    stage_name: Optional[str] = Field(default=None, max_length=120)
    issued_by_membership_id: Optional[uuid.UUID] = None
    verified_by_membership_id: Optional[uuid.UUID] = None
    evidence_document_id: Optional[uuid.UUID] = None


class BatchMaterialConsumptionVerifyRequest(BaseModel):
    row_version: Optional[int] = Field(default=None, ge=1)
    notes: Optional[str] = Field(default=None, max_length=1000)


class BatchRelationshipCreateRequest(BaseModel):
    related_batch_id: uuid.UUID
    direction: Literal["CHILD", "PARENT"] = "CHILD"
    relation_type: BatchRelationType
    quantity: Optional[Decimal] = Field(default=None, gt=Decimal("0"))
    unit_code: Optional[str] = Field(default=None, max_length=30)
    notes: Optional[str] = Field(default=None, max_length=1000)


class BatchDocumentLinkCreateRequest(BaseModel):
    document_id: uuid.UUID
    document_role: BatchDocumentRole


class BatchStatusHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    batch_id: uuid.UUID
    from_status: Optional[str]
    to_status: str
    changed_by_user_id: Optional[uuid.UUID]
    changed_by_membership_id: Optional[uuid.UUID]
    reason: Optional[str]
    occurred_at: datetime


class BatchDispositionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: uuid.UUID
    batch_id: uuid.UUID
    from_status: str
    disposition_decision: str
    quality_review_status: str
    checklist_json: Dict[str, Any]
    hold_or_rejection_reason: Optional[str]
    proposed_by_membership_id: uuid.UUID
    decided_by_membership_id: uuid.UUID
    approval_record_id: uuid.UUID
    certificate_document_id: Optional[uuid.UUID]
    decided_at: datetime
    comments: Optional[str]


class BatchMaterialConsumptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    batch_id: uuid.UUID
    material_id: uuid.UUID
    supplier_lot_id: uuid.UUID
    quantity_consumed: Decimal
    unit_code: str
    stage_name: Optional[str]
    issued_at: datetime
    issued_by_membership_id: uuid.UUID
    verified_by_membership_id: Optional[uuid.UUID]
    evidence_document_id: Optional[uuid.UUID]
    row_version: int


class BatchRelationshipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    parent_batch_id: uuid.UUID
    child_batch_id: uuid.UUID
    relation_type: str
    quantity: Optional[Decimal]
    unit_code: Optional[str]
    event_at: datetime
    notes: Optional[str]
    row_version: int


class BatchDocumentLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    batch_id: uuid.UUID
    document_id: uuid.UUID
    document_role: str
    linked_by_user_id: uuid.UUID
    linked_at: datetime


class BatchSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: uuid.UUID
    product_id: uuid.UUID
    batch_number: str
    manufacturing_order_no: Optional[str]
    manufacturing_date: date
    expiry_date: date
    batch_size_value: Decimal
    batch_size_unit: str
    actual_yield_value: Optional[Decimal]
    distributed_quantity: Decimal
    packaging_configuration: Optional[str]
    batch_status: str
    quality_review_status: str
    hold_or_rejection_reason: Optional[str]
    released_at: Optional[datetime]
    released_by_membership_id: Optional[uuid.UUID]
    primary_document_id: Optional[uuid.UUID]
    master_batch_record_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime
    row_version: int


class BatchDetailResponse(BatchSummaryResponse):
    status_history: List[BatchStatusHistoryResponse] = Field(default_factory=list)
    dispositions: List[BatchDispositionResponse] = Field(default_factory=list)
    material_consumptions: List[BatchMaterialConsumptionResponse] = Field(default_factory=list)
    document_links: List[BatchDocumentLinkResponse] = Field(default_factory=list)
    parent_relationships: List[BatchRelationshipResponse] = Field(default_factory=list)
    child_relationships: List[BatchRelationshipResponse] = Field(default_factory=list)


# --- Distribution & Traceability (C7) ---


class ShipmentLineCreateRequest(BaseModel):
    batch_id: uuid.UUID
    quantity: Decimal = Field(..., gt=Decimal("0"))
    unit_code: str = Field(..., min_length=1, max_length=30)
    notes: Optional[str] = Field(default=None, max_length=1000)


class ShipmentCreateRequest(BaseModel):
    site_id: uuid.UUID
    partner_id: uuid.UUID
    partner_site_id: uuid.UUID
    shipment_number: Optional[str] = Field(default=None, min_length=2, max_length=60)
    carrier_name: Optional[str] = Field(default=None, max_length=150)
    tracking_number: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=2000)
    lines: List[ShipmentLineCreateRequest] = Field(default_factory=list)


class ShipmentDispatchRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    carrier_name: Optional[str] = Field(default=None, max_length=150)
    tracking_number: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=1000)


class ShipmentLineReceiptInput(BaseModel):
    shipment_line_id: uuid.UUID
    received_quantity: Decimal = Field(..., ge=Decimal("0"))
    notes: Optional[str] = Field(default=None, max_length=1000)


class ShipmentReceiptRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    received_all: bool = False
    line_receipts: List[ShipmentLineReceiptInput] = Field(default_factory=list)
    notes: Optional[str] = Field(default=None, max_length=1000)


class ShipmentCancelRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    reason: str = Field(..., min_length=3, max_length=1000)


class ShipmentLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    shipment_id: uuid.UUID
    batch_id: uuid.UUID
    quantity: Decimal
    unit_code: str
    received_quantity: Optional[Decimal]
    notes: Optional[str]
    row_version: int


class ShipmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: uuid.UUID
    shipment_number: str
    partner_id: uuid.UUID
    partner_site_id: uuid.UUID
    shipment_status: str
    dispatched_at: Optional[datetime]
    received_at: Optional[datetime]
    carrier_name: Optional[str]
    tracking_number: Optional[str]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
    row_version: int


class ShipmentDetailResponse(ShipmentResponse):
    lines: List[ShipmentLineResponse] = Field(default_factory=list)


class BatchMovementEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    batch_id: uuid.UUID
    shipment_id: Optional[uuid.UUID]
    shipment_line_id: Optional[uuid.UUID]
    partner_id: Optional[uuid.UUID]
    partner_site_id: Optional[uuid.UUID]
    event_type: str
    quantity: Optional[Decimal]
    unit_code: Optional[str]
    actor_user_id: uuid.UUID
    occurred_at: datetime
    notes: Optional[str]


class TraceabilityMaterialItem(BaseModel):
    consumption_id: uuid.UUID
    material_id: uuid.UUID
    material_code: str
    material_name: str
    material_type: str
    supplier_lot_id: uuid.UUID
    internal_lot_number: str
    supplier_lot_number: str
    supplier_partner_id: uuid.UUID
    supplier_name: Optional[str] = None
    quantity_consumed: Decimal
    unit_code: str
    stage_name: Optional[str] = None
    lot_status: str
    issued_at: datetime
    verified_by_membership_id: Optional[uuid.UUID] = None
    coa_document_id: Optional[uuid.UUID] = None


class TraceabilityShipmentItem(BaseModel):
    shipment_id: uuid.UUID
    shipment_number: str
    shipment_status: str
    site_id: uuid.UUID
    partner_id: uuid.UUID
    partner_name: Optional[str] = None
    partner_site_id: uuid.UUID
    partner_site_name: Optional[str] = None
    shipment_line_id: uuid.UUID
    quantity: Decimal
    received_quantity: Optional[Decimal] = None
    unit_code: str
    dispatched_at: Optional[datetime] = None
    received_at: Optional[datetime] = None


class TraceabilityComplaintItem(BaseModel):
    complaint_id: uuid.UUID
    complaint_number: str
    status: str
    final_severity: Optional[str] = None
    reported_batch_number: Optional[str] = None
    link_resolution_outcome: str
    match_status: str
    created_at: datetime


class TraceabilityBatchSummary(BaseModel):
    batch_id: uuid.UUID
    batch_number: str
    product_id: uuid.UUID
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    site_id: uuid.UUID
    batch_status: str
    quality_review_status: str
    manufacturing_date: date
    expiry_date: date
    batch_size_value: Decimal
    batch_size_unit: str
    actual_yield_value: Optional[Decimal] = None
    distributed_quantity: Decimal


class BatchGenealogyEdge(BaseModel):
    relationship_id: uuid.UUID
    parent_batch_id: uuid.UUID
    parent_batch_number: Optional[str] = None
    child_batch_id: uuid.UUID
    child_batch_number: Optional[str] = None
    relation_type: str
    quantity: Optional[Decimal] = None
    unit_code: Optional[str] = None
    event_at: datetime


class BatchTraceabilityResponse(BaseModel):
    batch: TraceabilityBatchSummary
    product: Dict[str, Any]
    latest_disposition: Optional[BatchDispositionResponse] = None
    dispositions: List[BatchDispositionResponse] = Field(default_factory=list)
    backward_materials: List[TraceabilityMaterialItem] = Field(default_factory=list)
    parent_batches: List[TraceabilityBatchSummary] = Field(default_factory=list)
    child_batches: List[TraceabilityBatchSummary] = Field(default_factory=list)
    genealogy_edges: List[BatchGenealogyEdge] = Field(default_factory=list)
    forward_shipments: List[TraceabilityShipmentItem] = Field(default_factory=list)
    movement_events: List[BatchMovementEventResponse] = Field(default_factory=list)
    linked_documents: List[BatchDocumentLinkResponse] = Field(default_factory=list)
    linked_complaints: List[TraceabilityComplaintItem] = Field(default_factory=list)
    missing_links: List[str] = Field(default_factory=list)
