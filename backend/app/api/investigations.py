from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.database import SessionLocal

from app.models.domain.db_models import DBComplaint, DBBatch, DBProductionStage, DBBatchDocument
from app.models.schemas.investigations import LinkBatchRequest, InvestigationResponse
from app.graph.investigation_workflow import run_qa_investigation

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/{complaint_id}/link", response_model=InvestigationResponse)
def link_complaint_to_batch(complaint_id: str, request: LinkBatchRequest, db: Session = Depends(get_db)):
    """Links a customer complaint to a specific manufacturing batch."""
    complaint = db.query(DBComplaint).filter(DBComplaint.id == complaint_id).first()
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")

    batch = db.query(DBBatch).filter(DBBatch.id == request.batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    complaint.linked_batch_id = batch.id
    db.commit()
    db.refresh(complaint)

    return InvestigationResponse(
        complaint_id=complaint.id,
        linked_batch_id=batch.id,
        investigation_status="Batch Linked - Ready for AI Analysis"
    )

@router.post("/{complaint_id}/analyze", response_model=InvestigationResponse)
def run_ai_investigation(complaint_id: str, db: Session = Depends(get_db)):
    """
    Gathers complaint data and full batch production history, 
    then delegates to the LangGraph workflow to perform RCA.
    """
    complaint = db.query(DBComplaint).filter(DBComplaint.id == complaint_id).first()
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")
        
    if not complaint.linked_batch_id:
        raise HTTPException(status_code=400, detail="Complaint must be linked to a batch before investigation.")

    # 1. Gather all context for the AI
    batch = db.query(DBBatch).filter(DBBatch.id == complaint.linked_batch_id).first()
    stages = db.query(DBProductionStage).filter(DBProductionStage.batch_id == batch.id).all()
    documents = db.query(DBBatchDocument).filter(DBBatchDocument.batch_id == batch.id).all()

    # 2. Format the context string for the LLM prompt
    context_payload = f"""
    COMPLAINT DETAILS:
    Issue: {complaint.description}
    Category: {complaint.complaint_category}
    Severity: {complaint.severity}
    
    BATCH DETAILS:
    Batch Number: {batch.batch_number}
    Product: {batch.product_name}
    
    PRODUCTION STAGES:
    {[f"Stage: {s.stage_name} | Status: {s.status} | Notes: {s.notes}" for s in stages]}
    
    ATTACHED DOCUMENTS:
    {[d.document_type for d in documents]}
    """

    # 3. Delegate to the separate graph workflow
    ai_result = run_qa_investigation(context_payload)

    return InvestigationResponse(
        complaint_id=complaint.id,
        linked_batch_id=batch.id,
        investigation_status="AI Analysis Complete",
        ai_analysis=ai_result
    )