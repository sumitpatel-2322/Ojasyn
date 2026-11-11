from app.database import engine, SessionLocal
from app.models.domain.db_models import Base, DBOrganization, DBManufacturingUnit

def init_and_seed():
    # 1. Create all tables in PostgreSQL
    print("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    
    # 2. Seed a test Organization and Manufacturing Unit for testing
    db = SessionLocal()
    try:
        org = db.query(DBOrganization).first()
        if not org:
            print("Seeding test Organization and Manufacturing Unit...")
            test_org = DBOrganization(
                legal_name="Ojasyn Test Pharma",
                organization_type="Private Limited",
                status="Active"
            )
            db.add(test_org)
            db.commit()
            db.refresh(test_org)
            
            test_unit = DBManufacturingUnit(
                organization_id=test_org.id,
                unit_name="Pune Main Plant",
                unit_type="FDF",
                address_line_1="123 Pharma Zone",
                city="Pune",
                state="Maharashtra",
                pincode="411001",
                is_primary=True
            )
            db.add(test_unit)
            db.commit()
            print(f"Success! Test Organization ID: {test_org.id}, Test Unit ID: {test_unit.id}")
        else:
            print("Database already contains data. Skipping seed.")
    finally:
        db.close()

if __name__ == "__main__":
    init_and_seed()