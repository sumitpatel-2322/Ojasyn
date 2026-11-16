from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
# This is the Version 1 branch
# Import database engine and models
from app.database import engine
from app.models.domain.db_models import Base

# Import your API router
from app.api.complaints import router
from app.api.auth import router as auth_router
from app.api.batches import router as batches_router
from app.api.production import router as production_router
from app.api.investigations import router as investigations_router

# Automatically create all tables in PostgreSQL on startup
Base.metadata.create_all(bind=engine)

# Ensure newly added combined columns exist on existing PostgreSQL tables
def ensure_combined_columns():
    alter_statements = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS city VARCHAR;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS state VARCHAR;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS pincode VARCHAR;",
        "ALTER TABLE chemist_profiles ADD COLUMN IF NOT EXISTS gst_number VARCHAR;",
        "ALTER TABLE chemist_profiles ADD COLUMN IF NOT EXISTS pharmacy_address TEXT;",
        "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS gst_number VARCHAR;",
        "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS official_email VARCHAR;",
        "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS company_mobile VARCHAR;",
        "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS registered_address TEXT;",
        "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS state VARCHAR;",
        "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS city VARCHAR;",
        "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS postal_code VARCHAR;",
    ]
    try:
        with engine.begin() as conn:
            for stmt in alter_statements:
                conn.execute(text(stmt))
    except Exception as e:
        print(f"Column migration check warning: {e}")

ensure_combined_columns()

app = FastAPI(title="QMS Copilot API")

# Configure CORS so the React frontend can communicate with FastAPI
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the routes
app.include_router(router, prefix="/api")
app.include_router(auth_router, prefix="/api/auth", tags=["Authentication"])
app.include_router(batches_router, prefix="/api/batches", tags=["Batch Management"])
app.include_router(production_router, prefix="/api/production", tags=["Production & Documents"])
app.include_router(investigations_router, prefix="/api/investigations", tags=["QA Investigations"])

@app.get("/")
def read_root():
    return {"message": "QMS Copilot Backend is running."}
