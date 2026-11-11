from pydantic import BaseModel, Field
from typing import Optional, List

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