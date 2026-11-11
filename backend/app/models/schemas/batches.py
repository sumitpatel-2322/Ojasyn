from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

# ==========================================
# BATCH SCHEMAS
# ==========================================

class BatchBase(BaseModel):
    batch_number: str = Field(..., description="Unique alphanumeric batch identifier")
    product_name: str = Field(..., description="Name of the pharmaceutical product")
    manufacturing_unit_id: int = Field(..., description="ID of the manufacturing unit where the batch was produced")
    manufacturing_date: Optional[str] = Field(None, description="Date of manufacturing (e.g., 'Feb 2026')")
    expiry_date: Optional[str] = Field(None, description="Date of expiry (e.g., 'Jan 2028')")

class BatchCreate(BatchBase):
    """Payload required to create a new batch."""
    pass

class BatchUpdate(BaseModel):
    """Payload for updating an existing batch."""
    status: Optional[str] = Field(None, description="Active, Quarantined, Released")
    manufacturing_date: Optional[str] = None
    expiry_date: Optional[str] = None

class BatchResponse(BatchBase):
    """Response model returning batch details from the database."""
    id: int
    organization_id: int
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True