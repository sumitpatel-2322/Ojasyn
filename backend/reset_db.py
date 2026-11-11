from app.database import engine
from app.models.domain.db_models import Base

def reset_database():
    print("Dropping all existing tables in the database...")
    Base.metadata.drop_all(bind=engine)
    print("All tables dropped successfully!")

if __name__ == "__main__":
    reset_database()