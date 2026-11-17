import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ReferenceValueCreateRequest(BaseModel):
    category_code: str = Field(..., min_length=2, max_length=60)
    code: str = Field(..., min_length=1, max_length=60)
    display_name: str = Field(..., min_length=1, max_length=150)
    description: Optional[str] = None
    sort_order: int = Field(default=0, ge=0)
    is_active: bool = True
    is_global: bool = False
    metadata_json: Optional[Dict[str, Any]] = None


class ReferenceValueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: Optional[uuid.UUID]
    category_code: str
    code: str
    display_name: str
    description: Optional[str]
    sort_order: int
    is_active: bool
    metadata_json: Optional[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    row_version: int


class UnitOfMeasureCreateRequest(BaseModel):
    unit_code: str = Field(..., min_length=1, max_length=30)
    unit_name: str = Field(..., min_length=1, max_length=100)
    symbol: str = Field(..., min_length=1, max_length=20)
    dimension: str = Field(..., min_length=2, max_length=40)
    base_unit_code: Optional[str] = Field(default=None, max_length=30)
    conversion_factor: Decimal = Field(default=Decimal("1.000000"), gt=Decimal("0"))
    is_active: bool = True


class UnitOfMeasureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    unit_code: str
    unit_name: str
    symbol: str
    dimension: str
    base_unit_code: Optional[str]
    conversion_factor: Decimal
    is_active: bool
    created_at: datetime
    updated_at: datetime
    row_version: int


# --- Document Vault & Extraction DTOs (Module A7) ---


class DocumentVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    version_label: str
    version_number: int
    original_filename: str
    mime_type: str
    byte_size: int
    checksum_sha256: str
    scan_status: str
    approval_status: str
    uploaded_by_user_id: Optional[uuid.UUID]
    supersedes_version_id: Optional[uuid.UUID]
    created_at: datetime
    row_version: int


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: Optional[uuid.UUID]
    site_id: Optional[uuid.UUID]
    document_code: Optional[str]
    document_type: str
    title: str
    confidentiality: str
    owner_membership_id: Optional[uuid.UUID]
    status: str
    metadata_json: Optional[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    row_version: int


class DocumentDetailResponse(DocumentResponse):
    versions: List[DocumentVersionResponse] = Field(default_factory=list)


class ExtractedContentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_version_id: uuid.UUID
    page_number: int
    section_ref: Optional[str]
    text: Optional[str]
    structured_data_json: Optional[Dict[str, Any]]
    extraction_confidence: Optional[Decimal]
    created_at: datetime


class ExtractionJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_version_id: uuid.UUID
    extractor: str
    extractor_version: str
    status: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    error_code: Optional[str]
    created_at: datetime
    extracted_contents: List[ExtractedContentResponse] = Field(default_factory=list)
