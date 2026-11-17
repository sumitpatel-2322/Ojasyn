from fastapi import APIRouter

from app.api.v1.auth_onboarding import router as auth_onboarding_router
from app.api.v1.complaints import router as complaints_router
from app.api.v1.documents_sops import router as documents_sops_router
from app.api.v1.people_access import router as people_access_router
from app.api.v1.platform_governance import router as governance_router

v1_router = APIRouter()
v1_router.include_router(governance_router)
v1_router.include_router(auth_onboarding_router)
v1_router.include_router(people_access_router)
v1_router.include_router(documents_sops_router)
v1_router.include_router(complaints_router)
