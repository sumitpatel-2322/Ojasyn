from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

# ==========================================
# PRODUCTION STAGE SCHEMAS
# ==========================================

class ProductionStageBase(BaseModel):
    batch_id: int
    stage_name: str = Field(..., description="E.g., Dispensing, Granulation, Compression")
    notes: Optional[str] = None

class ProductionStageCreate(ProductionStageBase):
    pass

class ProductionStageUpdate(BaseModel):
    status: Optional[str] = Field(None, description="Pending, In Progress, Completed, Deviated")
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    notes: Optional[str] = None
    performed_by_id: Optional[int] = None

class ProductionStageResponse(ProductionStageBase):
    id: int
    status: str
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    performed_by_id: Optional[int]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# ==========================================
# DOCUMENT SCHEMAS
# ==========================================

class BatchDocumentResponse(BaseModel):
    id: int
    batch_id: int
    document_type: str
    file_name: str
    uploaded_by_id: Optional[int]
    uploaded_at: datetime

    class Config:
        from_attributes = True