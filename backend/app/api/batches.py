from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from typing import List

from app.database import SessionLocal
from app.models.domain.db_models import DBBatch, DBManufacturingUnit
from app.models.schemas.batches import BatchCreate, BatchResponse, BatchUpdate

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/", response_model=BatchResponse)
def create_batch(batch_data: BatchCreate, db: Session = Depends(get_db)):
    """
    Creates a new production batch. 
    The organization_id is automatically inherited from the selected manufacturing unit.
    """
    # 1. Verify the manufacturing unit exists
    unit = db.query(DBManufacturingUnit).filter(DBManufacturingUnit.id == batch_data.manufacturing_unit_id).first()
    if not unit:
        raise HTTPException(status_code=404, detail="Manufacturing unit not found.")

    # 2. Check for duplicate batch numbers within the same organization
    existing_batch = db.query(DBBatch).filter(
        DBBatch.organization_id == unit.organization_id,
        DBBatch.batch_number == batch_data.batch_number
    ).first()
    
    if existing_batch:
        raise HTTPException(status_code=400, detail=f"Batch number {batch_data.batch_number} already exists for this organization.")

    # 3. Create the Batch
    new_batch = DBBatch(
        organization_id=unit.organization_id,
        manufacturing_unit_id=batch_data.manufacturing_unit_id,
        batch_number=batch_data.batch_number,
        product_name=batch_data.product_name,
        manufacturing_date=batch_data.manufacturing_date,
        expiry_date=batch_data.expiry_date
    )
    
    db.add(new_batch)
    db.commit()
    db.refresh(new_batch)
    
    return new_batch


@router.get("/", response_model=List[BatchResponse])
def list_batches(organization_id: int = None, db: Session = Depends(get_db)):
    """
    Retrieves all batches. Can optionally filter by organization_id.
    """
    query = db.query(DBBatch)
    if organization_id:
        query = query.filter(DBBatch.organization_id == organization_id)
        
    return query.all()


@router.get("/{batch_id}", response_model=BatchResponse)
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    """
    Retrieves a specific batch by its ID.
    """
    batch = db.query(DBBatch).filter(DBBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found.")
    return batch


@router.patch("/{batch_id}", response_model=BatchResponse)
def update_batch_status(batch_id: int, updates: BatchUpdate, db: Session = Depends(get_db)):
    """
    Updates a batch (e.g., changing status to 'Quarantined' or 'Released').
    """
    batch = db.query(DBBatch).filter(DBBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found.")
        
    update_data = updates.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(batch, key, value)
        
    db.commit()
    db.refresh(batch)
    return batch