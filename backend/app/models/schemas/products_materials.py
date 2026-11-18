import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


# --- Product Schemas (C1) ---


class ProductCreateRequest(BaseModel):
    site_id: Optional[uuid.UUID] = None
    product_code: str = Field(..., min_length=2, max_length=60)
    brand_name: Optional[str] = Field(default=None, max_length=180)
    generic_name: str = Field(..., min_length=2, max_length=180)
    product_category: Optional[str] = Field(default=None, max_length=80)
    dosage_form: str = Field(..., min_length=2, max_length=60)
    strength_value: Optional[Decimal] = Field(default=None, gt=Decimal("0"))
    strength_unit: Optional[str] = Field(default=None, max_length=30)
    route_of_administration: Optional[str] = Field(default=None, max_length=60)
    composition_json: Optional[Dict[str, Any]] = None
    specifications_json: Optional[Dict[str, Any]] = None
    manufacturer_name: Optional[str] = Field(default=None, max_length=200)
    pack_description: Optional[str] = Field(default=None, max_length=180)
    therapeutic_class: Optional[str] = Field(default=None, max_length=120)
    shelf_life_months: Optional[int] = Field(default=None, ge=1)
    storage_conditions: Optional[str] = Field(default=None, max_length=200)
    specification_document_id: Optional[uuid.UUID] = None


class ProductUpdateRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    site_id: Optional[uuid.UUID] = None
    brand_name: Optional[str] = Field(default=None, max_length=180)
    generic_name: Optional[str] = Field(default=None, min_length=2, max_length=180)
    product_category: Optional[str] = Field(default=None, max_length=80)
    dosage_form: Optional[str] = Field(default=None, min_length=2, max_length=60)
    strength_value: Optional[Decimal] = Field(default=None, gt=Decimal("0"))
    strength_unit: Optional[str] = Field(default=None, max_length=30)
    route_of_administration: Optional[str] = Field(default=None, max_length=60)
    composition_json: Optional[Dict[str, Any]] = None
    specifications_json: Optional[Dict[str, Any]] = None
    manufacturer_name: Optional[str] = Field(default=None, max_length=200)
    pack_description: Optional[str] = Field(default=None, max_length=180)
    therapeutic_class: Optional[str] = Field(default=None, max_length=120)
    shelf_life_months: Optional[int] = Field(default=None, ge=1)
    storage_conditions: Optional[str] = Field(default=None, max_length=200)
    specification_document_id: Optional[uuid.UUID] = None
    reason: str = Field(..., min_length=3, max_length=1000)


class ProductStatusChangeRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    target_status: str = Field(..., min_length=2, max_length=30)
    reason: str = Field(..., min_length=3, max_length=1000)


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: Optional[uuid.UUID]
    product_code: str
    brand_name: Optional[str]
    generic_name: str
    product_category: Optional[str]
    dosage_form: str
    strength_value: Optional[Decimal]
    strength_unit: Optional[str]
    route_of_administration: Optional[str]
    composition_json: Optional[Dict[str, Any]]
    specifications_json: Optional[Dict[str, Any]]
    manufacturer_name: Optional[str]
    pack_description: Optional[str]
    therapeutic_class: Optional[str]
    shelf_life_months: Optional[int]
    storage_conditions: Optional[str]
    product_status: str
    version_number: int
    specification_document_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime
    row_version: int


class ProductAuthorizationCreateRequest(BaseModel):
    country_code: str = Field(..., min_length=2, max_length=2)
    authorization_number: str = Field(..., min_length=2, max_length=100)
    authorization_holder: Optional[str] = Field(default=None, max_length=200)
    issuing_authority: Optional[str] = Field(default=None, max_length=150)
    approved_on: Optional[date] = None
    valid_to: Optional[date] = None
    regulatory_status: str = Field(default="PENDING", max_length=30)
    evidence_document_id: Optional[uuid.UUID] = None


class ProductAuthorizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    product_id: uuid.UUID
    country_code: str
    authorization_number: str
    authorization_holder: Optional[str]
    issuing_authority: Optional[str]
    approved_on: Optional[date]
    valid_to: Optional[date]
    regulatory_status: str
    evidence_document_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime
    row_version: int


# --- Material & BOM Schemas (C1/C2) ---


class MaterialCreateRequest(BaseModel):
    material_code: str = Field(..., min_length=2, max_length=60)
    material_name: str = Field(..., min_length=2, max_length=180)
    material_type: str = Field(..., min_length=2, max_length=40)
    material_category: Optional[str] = Field(default=None, max_length=80)
    cas_number: Optional[str] = Field(default=None, max_length=40)
    pharmacopoeia_grade: Optional[str] = Field(default=None, max_length=40)
    default_unit_code: str = Field(..., min_length=1, max_length=30)
    manufacturer_name: Optional[str] = Field(default=None, max_length=200)
    storage_conditions: Optional[str] = Field(default=None, max_length=200)
    retest_period_days: Optional[int] = Field(default=None, ge=1)
    specifications_json: Optional[Dict[str, Any]] = None
    default_supplier_partner_id: Optional[uuid.UUID] = None
    specification_document_id: Optional[uuid.UUID] = None


class MaterialUpdateRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    material_name: Optional[str] = Field(default=None, min_length=2, max_length=180)
    material_category: Optional[str] = Field(default=None, max_length=80)
    cas_number: Optional[str] = Field(default=None, max_length=40)
    pharmacopoeia_grade: Optional[str] = Field(default=None, max_length=40)
    default_unit_code: Optional[str] = Field(default=None, min_length=1, max_length=30)
    manufacturer_name: Optional[str] = Field(default=None, max_length=200)
    storage_conditions: Optional[str] = Field(default=None, max_length=200)
    retest_period_days: Optional[int] = Field(default=None, ge=1)
    specifications_json: Optional[Dict[str, Any]] = None
    default_supplier_partner_id: Optional[uuid.UUID] = None
    specification_document_id: Optional[uuid.UUID] = None
    reason: str = Field(..., min_length=3, max_length=1000)


class MaterialStatusChangeRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    material_status: Optional[str] = Field(default=None, max_length=30)
    qualification_status: Optional[str] = Field(default=None, max_length=30)
    reason: str = Field(..., min_length=3, max_length=1000)


class MaterialResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    material_code: str
    material_name: str
    material_type: str
    material_category: Optional[str]
    cas_number: Optional[str]
    pharmacopoeia_grade: Optional[str]
    default_unit_code: str
    manufacturer_name: Optional[str]
    storage_conditions: Optional[str]
    retest_period_days: Optional[int]
    specifications_json: Optional[Dict[str, Any]]
    qualification_status: str
    material_status: str
    default_supplier_partner_id: Optional[uuid.UUID]
    specification_document_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime
    row_version: int


class ProductMaterialBillCreateRequest(BaseModel):
    material_id: uuid.UUID
    quantity_per_unit: Decimal = Field(..., gt=Decimal("0"))
    unit_code: str = Field(..., min_length=1, max_length=30)
    function_role: Optional[str] = Field(default=None, max_length=80)
    is_critical_material: bool = False
    notes: Optional[str] = None


class ProductMaterialBillResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    product_id: uuid.UUID
    material_id: uuid.UUID
    quantity_per_unit: Decimal
    unit_code: str
    function_role: Optional[str]
    is_critical_material: bool
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
    row_version: int


# --- Supplier Lot Schemas (C2) ---


class SupplierLotCreateRequest(BaseModel):
    site_id: Optional[uuid.UUID] = None
    material_id: uuid.UUID
    supplier_partner_id: uuid.UUID
    internal_lot_number: Optional[str] = Field(default=None, max_length=80)
    supplier_lot_number: str = Field(..., min_length=1, max_length=80)
    manufacturer_name: Optional[str] = Field(default=None, max_length=200)
    received_quantity: Decimal = Field(..., gt=Decimal("0"))
    unit_code: str = Field(..., min_length=1, max_length=30)
    received_date: date
    manufacturing_date: Optional[date] = None
    expiry_or_retest_date: Optional[date] = None
    coa_document_id: Optional[uuid.UUID] = None


class SupplierLotStatusChangeRequest(BaseModel):
    row_version: int = Field(..., ge=1)
    target_status: str = Field(..., min_length=2, max_length=30)
    reason: str = Field(..., min_length=3, max_length=1000)


class SupplierLotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    site_id: Optional[uuid.UUID]
    material_id: uuid.UUID
    supplier_partner_id: uuid.UUID
    internal_lot_number: str
    supplier_lot_number: str
    manufacturer_name: Optional[str]
    received_quantity: Decimal
    remaining_quantity: Decimal
    unit_code: str
    received_date: date
    manufacturing_date: Optional[date]
    expiry_or_retest_date: Optional[date]
    lot_status: str
    coa_document_id: Optional[uuid.UUID]
    approved_by_membership_id: Optional[uuid.UUID]
    approved_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    row_version: int
