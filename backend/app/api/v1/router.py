from fastapi import APIRouter

from app.api.v1.platform_governance import router as governance_router

v1_router = APIRouter()
v1_router.include_router(governance_router)
