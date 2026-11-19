from fastapi import APIRouter

from app.api.v1.auth_onboarding import router as auth_onboarding_router
from app.api.v1.complaints import router as complaints_router
from app.api.v1.distribution import router as distribution_router
from app.api.v1.documents_sops import router as documents_sops_router
from app.api.v1.investigations import router as investigations_router
from app.api.v1.manufacturing_execution import router as manufacturing_execution_router
from app.api.v1.people_access import router as people_access_router
from app.api.v1.platform_governance import router as governance_router
from app.api.v1.products_batches import router as products_batches_router
from app.api.v1.products_materials import router as products_materials_router
from app.api.v1.qc_equipment import router as qc_equipment_router
from app.api.v1.qms_actions import router as qms_actions_router
from app.api.v1.sops import router as sops_router

v1_router = APIRouter()
v1_router.include_router(governance_router)
v1_router.include_router(auth_onboarding_router)
v1_router.include_router(people_access_router)
v1_router.include_router(documents_sops_router)
v1_router.include_router(sops_router)
v1_router.include_router(complaints_router)
v1_router.include_router(products_materials_router)
v1_router.include_router(products_batches_router)
v1_router.include_router(distribution_router)
v1_router.include_router(manufacturing_execution_router)
v1_router.include_router(qc_equipment_router)
v1_router.include_router(investigations_router)
v1_router.include_router(qms_actions_router)

