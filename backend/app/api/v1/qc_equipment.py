import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.deps import TenantContext, get_db, get_tenant_context, require_permission
from app.core.exceptions import PermissionDeniedError
from app.domain.enums import SecurityEventOutcome, SecurityEventSeverity
from app.models.schemas.common import Page
from app.models.schemas.qc_equipment import (
    BatchQCHistoryResponse,
    CalibrationRecordCreateRequest,
    CalibrationRecordResponse,
    CalibrationRecordReviewRequest,
    CalibrationValidityResponse,
    EquipmentCreateRequest,
    EquipmentResponse,
    EquipmentUpdateRequest,
    EquipmentUsageCreateRequest,
    EquipmentUsageResponse,
    QCSampleCreateRequest,
    QCSampleResponse,
    QCTestDefinitionCreateRequest,
    QCTestDefinitionResponse,
    QCTestResultCreateRequest,
    QCTestResultResponse,
    QCTestResultReviewRequest,
)
from app.services.audit_service import AuditService
from app.services.qc_equipment_service import QCEquipmentService

router = APIRouter(tags=["qc-equipment"])

_qc_equip_svc = QCEquipmentService()
_audit_svc = AuditService()


def _client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def _require_any_permission(*permission_codes: str):
    def _dependency(
        request: Request,
        tenant_ctx: TenantContext = Depends(get_tenant_context),
        db: Session = Depends(get_db),
    ) -> TenantContext:
        if not any(code in tenant_ctx.permission_codes for code in permission_codes):
            db.rollback()
            _audit_svc.record_security_event(
                event_type="RBAC_PERMISSION_DENIED",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.WARNING.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=_client_ip(request),
                target_resource=request.url.path,
                details_json={"required_permissions": list(permission_codes)},
            )
            raise PermissionDeniedError(
                f"Missing required permission (one of {', '.join(permission_codes)}).",
                error_code="INSUFFICIENT_PERMISSION",
            )
        return tenant_ctx

    return _dependency


# --- QC Test Definitions (Module C6) ---


@router.post(
    "/qc/test-definitions",
    response_model=QCTestDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_qc_test_definition(
    request: Request,
    payload: QCTestDefinitionCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("qc_def.manage")),
    db: Session = Depends(get_db),
) -> QCTestDefinitionResponse:
    test_def = _qc_equip_svc.create_test_definition(
        db, tenant_ctx=tenant_ctx, payload=payload, source_ip=_client_ip(request)
    )
    return QCTestDefinitionResponse.model_validate(test_def)


@router.get("/qc/test-definitions", response_model=Page[QCTestDefinitionResponse])
def list_qc_test_definitions(
    site_id: Optional[uuid.UUID] = Query(default=None),
    product_id: Optional[uuid.UUID] = Query(default=None),
    product_category: Optional[str] = Query(default=None),
    test_code: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    q: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(
        _require_any_permission(
            "qc_def.manage", "qc_result.read", "qc_result.record", "qc_result.approve"
        )
    ),
    db: Session = Depends(get_db),
) -> Page[QCTestDefinitionResponse]:
    items, total = _qc_equip_svc.list_test_definitions(
        db,
        tenant_ctx=tenant_ctx,
        site_id=site_id,
        product_id=product_id,
        product_category=product_category,
        test_code=test_code,
        status=status_filter,
        q=q,
        page=page,
        page_size=page_size,
    )
    return Page(
        items=[QCTestDefinitionResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/qc/test-definitions/{test_definition_id}",
    response_model=QCTestDefinitionResponse,
)
def get_qc_test_definition(
    request: Request,
    test_definition_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission(
            "qc_def.manage", "qc_result.read", "qc_result.record", "qc_result.approve"
        )
    ),
    db: Session = Depends(get_db),
) -> QCTestDefinitionResponse:
    test_def = _qc_equip_svc.get_test_definition(
        db,
        tenant_ctx=tenant_ctx,
        test_definition_id=test_definition_id,
        source_ip=_client_ip(request),
    )
    return QCTestDefinitionResponse.model_validate(test_def)


# --- QC Samples & Results (Module C6) ---


@router.post(
    "/batches/{batch_id}/qc-samples",
    response_model=QCSampleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_batch_qc_sample(
    request: Request,
    batch_id: uuid.UUID,
    payload: QCSampleCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("qc_result.record")),
    db: Session = Depends(get_db),
) -> QCSampleResponse:
    sample = _qc_equip_svc.create_qc_sample(
        db,
        tenant_ctx=tenant_ctx,
        batch_id=batch_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return QCSampleResponse.model_validate(sample)


@router.get("/batches/{batch_id}/qc-samples", response_model=List[QCSampleResponse])
def list_batch_qc_samples(
    request: Request,
    batch_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission(
            "qc_result.read", "qc_result.record", "qc_result.approve", "batch.read"
        )
    ),
    db: Session = Depends(get_db),
) -> List[QCSampleResponse]:
    samples = _qc_equip_svc.list_batch_qc_samples(
        db,
        tenant_ctx=tenant_ctx,
        batch_id=batch_id,
        source_ip=_client_ip(request),
    )
    return [QCSampleResponse.model_validate(s) for s in samples]


@router.get("/qc/samples/{sample_id}", response_model=QCSampleResponse)
def get_qc_sample(
    request: Request,
    sample_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission(
            "qc_result.read", "qc_result.record", "qc_result.approve"
        )
    ),
    db: Session = Depends(get_db),
) -> QCSampleResponse:
    sample = _qc_equip_svc.get_qc_sample(
        db,
        tenant_ctx=tenant_ctx,
        sample_id=sample_id,
        source_ip=_client_ip(request),
    )
    return QCSampleResponse.model_validate(sample)


@router.post(
    "/qc/samples/{sample_id}/results",
    response_model=QCTestResultResponse,
    status_code=status.HTTP_201_CREATED,
)
def record_qc_sample_result(
    request: Request,
    sample_id: uuid.UUID,
    payload: QCTestResultCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("qc_result.record")),
    db: Session = Depends(get_db),
) -> QCTestResultResponse:
    result = _qc_equip_svc.record_qc_result(
        db,
        tenant_ctx=tenant_ctx,
        sample_id=sample_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return QCTestResultResponse.model_validate(result)


@router.post(
    "/qc/results/{result_id}/review",
    response_model=QCTestResultResponse,
)
def review_qc_test_result(
    request: Request,
    result_id: uuid.UUID,
    payload: QCTestResultReviewRequest,
    tenant_ctx: TenantContext = Depends(require_permission("qc_result.approve")),
    db: Session = Depends(get_db),
) -> QCTestResultResponse:
    result = _qc_equip_svc.review_qc_result(
        db,
        tenant_ctx=tenant_ctx,
        result_id=result_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return QCTestResultResponse.model_validate(result)


@router.get(
    "/batches/{batch_id}/qc-history",
    response_model=BatchQCHistoryResponse,
)
def get_batch_qc_history(
    request: Request,
    batch_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission(
            "qc_result.read", "qc_result.record", "qc_result.approve"
        )
    ),
    db: Session = Depends(get_db),
) -> BatchQCHistoryResponse:
    return _qc_equip_svc.get_batch_qc_history(
        db,
        tenant_ctx=tenant_ctx,
        batch_id=batch_id,
        source_ip=_client_ip(request),
    )


# --- Equipment & Calibration (Module C8) ---


@router.post(
    "/equipment",
    response_model=EquipmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_equipment(
    request: Request,
    payload: EquipmentCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("equipment.manage")),
    db: Session = Depends(get_db),
) -> EquipmentResponse:
    equipment = _qc_equip_svc.create_equipment(
        db, tenant_ctx=tenant_ctx, payload=payload, source_ip=_client_ip(request)
    )
    return EquipmentResponse.model_validate(equipment)


@router.get("/equipment", response_model=Page[EquipmentResponse])
def list_equipment(
    site_id: Optional[uuid.UUID] = Query(default=None),
    department_id: Optional[uuid.UUID] = Query(default=None),
    equipment_category: Optional[str] = Query(default=None),
    operational_status: Optional[str] = Query(default=None),
    qualification_status: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(
        _require_any_permission(
            "equipment.manage",
            "calibration.record",
            "execution.read",
            "qc_result.read",
        )
    ),
    db: Session = Depends(get_db),
) -> Page[EquipmentResponse]:
    items, total = _qc_equip_svc.list_equipment(
        db,
        tenant_ctx=tenant_ctx,
        site_id=site_id,
        department_id=department_id,
        equipment_category=equipment_category,
        operational_status=operational_status,
        qualification_status=qualification_status,
        q=q,
        page=page,
        page_size=page_size,
    )
    return Page(
        items=[EquipmentResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/equipment/{equipment_id}", response_model=EquipmentResponse)
def get_equipment(
    request: Request,
    equipment_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission(
            "equipment.manage",
            "calibration.record",
            "execution.read",
            "qc_result.read",
        )
    ),
    db: Session = Depends(get_db),
) -> EquipmentResponse:
    equipment = _qc_equip_svc.get_equipment(
        db,
        tenant_ctx=tenant_ctx,
        equipment_id=equipment_id,
        source_ip=_client_ip(request),
    )
    return EquipmentResponse.model_validate(equipment)


@router.patch("/equipment/{equipment_id}", response_model=EquipmentResponse)
def update_equipment(
    request: Request,
    equipment_id: uuid.UUID,
    payload: EquipmentUpdateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("equipment.manage")),
    db: Session = Depends(get_db),
) -> EquipmentResponse:
    equipment = _qc_equip_svc.update_equipment(
        db,
        tenant_ctx=tenant_ctx,
        equipment_id=equipment_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return EquipmentResponse.model_validate(equipment)


@router.post(
    "/equipment/{equipment_id}/calibrations",
    response_model=CalibrationRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_calibration_record(
    request: Request,
    equipment_id: uuid.UUID,
    payload: CalibrationRecordCreateRequest,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission("calibration.record", "equipment.manage")
    ),
    db: Session = Depends(get_db),
) -> CalibrationRecordResponse:
    cal = _qc_equip_svc.create_calibration_record(
        db,
        tenant_ctx=tenant_ctx,
        equipment_id=equipment_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return CalibrationRecordResponse.model_validate(cal)


@router.get(
    "/equipment/{equipment_id}/calibrations",
    response_model=List[CalibrationRecordResponse],
)
def list_calibration_records(
    request: Request,
    equipment_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission(
            "calibration.record",
            "equipment.manage",
            "execution.read",
            "qc_result.read",
        )
    ),
    db: Session = Depends(get_db),
) -> List[CalibrationRecordResponse]:
    items = _qc_equip_svc.list_calibration_records(
        db,
        tenant_ctx=tenant_ctx,
        equipment_id=equipment_id,
        source_ip=_client_ip(request),
    )
    return [CalibrationRecordResponse.model_validate(c) for c in items]


@router.post(
    "/equipment/{equipment_id}/calibrations/{calibration_id}/review",
    response_model=CalibrationRecordResponse,
)
def review_calibration_record(
    request: Request,
    equipment_id: uuid.UUID,
    calibration_id: uuid.UUID,
    payload: CalibrationRecordReviewRequest,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission("equipment.manage", "calibration.record")
    ),
    db: Session = Depends(get_db),
) -> CalibrationRecordResponse:
    cal = _qc_equip_svc.review_calibration_record(
        db,
        tenant_ctx=tenant_ctx,
        equipment_id=equipment_id,
        calibration_id=calibration_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return CalibrationRecordResponse.model_validate(cal)


@router.get(
    "/equipment/{equipment_id}/calibration-validity",
    response_model=CalibrationValidityResponse,
)
def get_equipment_calibration_validity(
    request: Request,
    equipment_id: uuid.UUID,
    at: Optional[datetime] = Query(default=None),
    tenant_ctx: TenantContext = Depends(
        _require_any_permission(
            "calibration.record",
            "equipment.manage",
            "execution.read",
            "qc_result.read",
        )
    ),
    db: Session = Depends(get_db),
) -> CalibrationValidityResponse:
    return _qc_equip_svc.get_equipment_calibration_validity(
        db,
        tenant_ctx=tenant_ctx,
        equipment_id=equipment_id,
        at=at,
        source_ip=_client_ip(request),
    )


# --- Equipment Usages (Module C8) ---


@router.post(
    "/execution-records/{execution_record_id}/equipment-usage",
    response_model=EquipmentUsageResponse,
    status_code=status.HTTP_201_CREATED,
)
def record_equipment_usage(
    request: Request,
    execution_record_id: uuid.UUID,
    payload: EquipmentUsageCreateRequest,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission("execution.record", "equipment.manage")
    ),
    db: Session = Depends(get_db),
) -> EquipmentUsageResponse:
    usage = _qc_equip_svc.record_equipment_usage(
        db,
        tenant_ctx=tenant_ctx,
        execution_record_id=execution_record_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return EquipmentUsageResponse.model_validate(usage)


@router.get(
    "/execution-records/{execution_record_id}/equipment-usage",
    response_model=List[EquipmentUsageResponse],
)
def list_equipment_usages(
    request: Request,
    execution_record_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission(
            "execution.read",
            "execution.record",
            "equipment.manage",
            "qc_result.read",
        )
    ),
    db: Session = Depends(get_db),
) -> List[EquipmentUsageResponse]:
    items = _qc_equip_svc.list_equipment_usages_for_execution_record(
        db,
        tenant_ctx=tenant_ctx,
        execution_record_id=execution_record_id,
        source_ip=_client_ip(request),
    )
    return [EquipmentUsageResponse.model_validate(u) for u in items]
