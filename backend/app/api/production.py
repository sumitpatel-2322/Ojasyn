import os
import shutil
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import List

from app.database import SessionLocal
from app.models.domain.db_models import DBProductionStage, DBBatchDocument, DBBatch
from app.models.schemas.production import ProductionStageCreate, ProductionStageUpdate, ProductionStageResponse, BatchDocumentResponse

router = APIRouter()

# Ensure upload directory exists
UPLOAD_DIR = "uploads/batch_documents"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/stages", response_model=ProductionStageResponse)
def create_production_stage(stage_data: ProductionStageCreate, db: Session = Depends(get_db)):
    """Creates a new production stage for a specific batch."""
    batch = db.query(DBBatch).filter(DBBatch.id == stage_data.batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found.")

    new_stage = DBProductionStage(
        batch_id=stage_data.batch_id,
        stage_name=stage_data.stage_name,
        notes=stage_data.notes
    )
    db.add(new_stage)
    db.commit()
    db.refresh(new_stage)
    return new_stage

@router.get("/batches/{batch_id}/stages", response_model=List[ProductionStageResponse])
def get_batch_stages(batch_id: int, db: Session = Depends(get_db)):
    """Retrieves all production stages for a specific batch."""
    stages = db.query(DBProductionStage).filter(DBProductionStage.batch_id == batch_id).all()
    return stages

@router.patch("/stages/{stage_id}", response_model=ProductionStageResponse)
def update_production_stage(stage_id: int, updates: ProductionStageUpdate, db: Session = Depends(get_db)):
    """Updates the status, timing, or notes of a production stage."""
    stage = db.query(DBProductionStage).filter(DBProductionStage.id == stage_id).first()
    if not stage:
        raise HTTPException(status_code=404, detail="Production stage not found.")

    update_data = updates.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(stage, key, value)

    db.commit()
    db.refresh(stage)
    return stage

@router.post("/batches/{batch_id}/documents", response_model=BatchDocumentResponse)
async def upload_batch_document(
    batch_id: int,
    document_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """Uploads a manufacturing document (e.g., BMR, Analytical Report) and links it to a batch."""
    batch = db.query(DBBatch).filter(DBBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found.")

    # Securely save the file locally
    file_location = os.path.join(UPLOAD_DIR, f"batch_{batch_id}_{file.filename}")
    with open(file_location, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    new_document = DBBatchDocument(
        batch_id=batch_id,
        document_type=document_type,
        file_name=file.filename,
        file_path=file_location
        # uploaded_by_id would be set here using the authenticated user's ID in a fully wired app
    )
    
    db.add(new_document)
    db.commit()
    db.refresh(new_document)
    
    return new_document

@router.get("/batches/{batch_id}/documents", response_model=List[BatchDocumentResponse])
def get_batch_documents(batch_id: int, db: Session = Depends(get_db)):
    """Retrieves all documents associated with a specific batch."""
    documents = db.query(DBBatchDocument).filter(DBBatchDocument.batch_id == batch_id).all()
    return documents