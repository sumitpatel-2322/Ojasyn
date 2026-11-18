import uuid
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.deps import TenantContext, get_db, get_tenant_context, require_permission
from app.core.exceptions import PermissionDeniedError
from app.domain.enums import SecurityEventOutcome, SecurityEventSeverity
from app.models.schemas.common import Page
from app.models.schemas.manufacturing_execution import (
    BatchExecutionLifecycleActionRequest,
    BatchExecutionRecordCreateRequest,
    BatchExecutionRecordDetailResponse,
    BatchExecutionRecordUpdateRequest,
    BatchExecutionStepCreateRequest,
    BatchExecutionStepExecuteRequest,
    BatchExecutionStepResponse,
    BatchExecutionStepUpdateRequest,
    BatchExecutionStepVerifyRequest,
    BatchManufacturingEvidenceResponse,
    DeterministicEvidenceCheckResponse,
    MasterBatchRecordActivateRequest,
    MasterBatchRecordApproveRequest,
    MasterBatchRecordCreateRequest,
    MasterBatchRecordResponse,
    MasterBatchRecordUpdateRequest,
    ProcessParameterObservationCreateRequest,
    ProcessParameterObservationResponse,
    ProcessParameterObservationVerifyRequest,
)
from app.services.audit_service import AuditService
from app.services.manufacturing_execution_service import ManufacturingExecutionService

router = APIRouter(tags=["manufacturing-execution"])

_svc = ManufacturingExecutionService()
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


# --- Master Batch Records (MBR / MPR) ---


@router.post(
    "/mbrs",
    response_model=MasterBatchRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_mbr(
    request: Request,
    payload: MasterBatchRecordCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("mbr.manage")),
    db: Session = Depends(get_db),
) -> MasterBatchRecordResponse:
    mbr = _svc.create_mbr(
        db, tenant_ctx=tenant_ctx, payload=payload, source_ip=_client_ip(request)
    )
    return MasterBatchRecordResponse.model_validate(mbr)


@router.get("/mbrs", response_model=Page[MasterBatchRecordResponse])
def list_mbrs(
    site_id: Optional[uuid.UUID] = Query(default=None),
    product_id: Optional[uuid.UUID] = Query(default=None),
    approval_status: Optional[str] = Query(default=None),
    mbr_code: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(
        _require_any_permission("product.read", "execution.read", "mbr.manage")
    ),
    db: Session = Depends(get_db),
) -> Page[MasterBatchRecordResponse]:
    items, total = _svc.list_mbrs(
        db,
        tenant_ctx=tenant_ctx,
        site_id=site_id,
        product_id=product_id,
        approval_status=approval_status,
        mbr_code=mbr_code,
        page=page,
        page_size=page_size,
    )
    return Page(
        items=[MasterBatchRecordResponse.model_validate(m) for m in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/mbrs/{mbr_id}", response_model=MasterBatchRecordResponse)
def get_mbr(
    request: Request,
    mbr_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission("product.read", "execution.read", "mbr.manage")
    ),
    db: Session = Depends(get_db),
) -> MasterBatchRecordResponse:
    mbr = _svc.get_mbr(
        db, tenant_ctx=tenant_ctx, mbr_id=mbr_id, source_ip=_client_ip(request)
    )
    return MasterBatchRecordResponse.model_validate(mbr)


@router.patch("/mbrs/{mbr_id}", response_model=MasterBatchRecordResponse)
def update_mbr(
    request: Request,
    mbr_id: uuid.UUID,
    payload: MasterBatchRecordUpdateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("mbr.manage")),
    db: Session = Depends(get_db),
) -> MasterBatchRecordResponse:
    mbr = _svc.update_mbr(
        db,
        tenant_ctx=tenant_ctx,
        mbr_id=mbr_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return MasterBatchRecordResponse.model_validate(mbr)


@router.post("/mbrs/{mbr_id}/submit-review", response_model=MasterBatchRecordResponse)
def submit_mbr_review(
    request: Request,
    mbr_id: uuid.UUID,
    row_version: int = Body(default=1, embed=True, ge=1),
    tenant_ctx: TenantContext = Depends(require_permission("mbr.manage")),
    db: Session = Depends(get_db),
) -> MasterBatchRecordResponse:
    mbr = _svc.submit_mbr_review(
        db,
        tenant_ctx=tenant_ctx,
        mbr_id=mbr_id,
        row_version=row_version,
        source_ip=_client_ip(request),
    )
    return MasterBatchRecordResponse.model_validate(mbr)


@router.post("/mbrs/{mbr_id}/approve", response_model=MasterBatchRecordResponse)
def approve_mbr(
    request: Request,
    mbr_id: uuid.UUID,
    payload: MasterBatchRecordApproveRequest,
    tenant_ctx: TenantContext = Depends(require_permission("mbr.manage")),
    db: Session = Depends(get_db),
) -> MasterBatchRecordResponse:
    mbr = _svc.approve_mbr(
        db,
        tenant_ctx=tenant_ctx,
        mbr_id=mbr_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return MasterBatchRecordResponse.model_validate(mbr)


@router.post("/mbrs/{mbr_id}/activate", response_model=MasterBatchRecordResponse)
def activate_mbr(
    request: Request,
    mbr_id: uuid.UUID,
    payload: MasterBatchRecordActivateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("mbr.manage")),
    db: Session = Depends(get_db),
) -> MasterBatchRecordResponse:
    mbr = _svc.activate_mbr(
        db,
        tenant_ctx=tenant_ctx,
        mbr_id=mbr_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return MasterBatchRecordResponse.model_validate(mbr)


# --- Batch Execution Records ---


@router.post(
    "/batches/{batch_id}/execution-records",
    response_model=BatchExecutionRecordDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_batch_execution_record(
    request: Request,
    batch_id: uuid.UUID,
    payload: BatchExecutionRecordCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("execution.record")),
    db: Session = Depends(get_db),
) -> BatchExecutionRecordDetailResponse:
    rec = _svc.create_execution_record(
        db,
        tenant_ctx=tenant_ctx,
        batch_id=batch_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return BatchExecutionRecordDetailResponse.model_validate(rec)


@router.get(
    "/batches/{batch_id}/execution-records",
    response_model=List[BatchExecutionRecordDetailResponse],
)
def list_batch_execution_records(
    request: Request,
    batch_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("execution.read")),
    db: Session = Depends(get_db),
) -> List[BatchExecutionRecordDetailResponse]:
    items = _svc.list_execution_records_for_batch(
        db, tenant_ctx=tenant_ctx, batch_id=batch_id, source_ip=_client_ip(request)
    )
    return [BatchExecutionRecordDetailResponse.model_validate(r) for r in items]


@router.get(
    "/execution-records/{execution_record_id}",
    response_model=BatchExecutionRecordDetailResponse,
)
def get_batch_execution_record(
    request: Request,
    execution_record_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("execution.read")),
    db: Session = Depends(get_db),
) -> BatchExecutionRecordDetailResponse:
    rec = _svc.get_execution_record(
        db,
        tenant_ctx=tenant_ctx,
        execution_record_id=execution_record_id,
        source_ip=_client_ip(request),
    )
    return BatchExecutionRecordDetailResponse.model_validate(rec)


@router.patch(
    "/execution-records/{execution_record_id}",
    response_model=BatchExecutionRecordDetailResponse,
)
def update_batch_execution_record(
    request: Request,
    execution_record_id: uuid.UUID,
    payload: BatchExecutionRecordUpdateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("execution.record")),
    db: Session = Depends(get_db),
) -> BatchExecutionRecordDetailResponse:
    rec = _svc.update_execution_record(
        db,
        tenant_ctx=tenant_ctx,
        execution_record_id=execution_record_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return BatchExecutionRecordDetailResponse.model_validate(rec)


@router.post(
    "/execution-records/{execution_record_id}/start",
    response_model=BatchExecutionRecordDetailResponse,
)
def start_batch_execution_record(
    request: Request,
    execution_record_id: uuid.UUID,
    payload: BatchExecutionLifecycleActionRequest,
    tenant_ctx: TenantContext = Depends(require_permission("execution.record")),
    db: Session = Depends(get_db),
) -> BatchExecutionRecordDetailResponse:
    rec = _svc.start_execution_record(
        db,
        tenant_ctx=tenant_ctx,
        execution_record_id=execution_record_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return BatchExecutionRecordDetailResponse.model_validate(rec)


@router.post(
    "/execution-records/{execution_record_id}/complete",
    response_model=BatchExecutionRecordDetailResponse,
)
def complete_batch_execution_record(
    request: Request,
    execution_record_id: uuid.UUID,
    payload: BatchExecutionLifecycleActionRequest,
    tenant_ctx: TenantContext = Depends(require_permission("execution.record")),
    db: Session = Depends(get_db),
) -> BatchExecutionRecordDetailResponse:
    rec = _svc.complete_execution_record(
        db,
        tenant_ctx=tenant_ctx,
        execution_record_id=execution_record_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return BatchExecutionRecordDetailResponse.model_validate(rec)


@router.post(
    "/execution-records/{execution_record_id}/verify",
    response_model=BatchExecutionRecordDetailResponse,
)
def verify_batch_execution_record(
    request: Request,
    execution_record_id: uuid.UUID,
    payload: BatchExecutionLifecycleActionRequest,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission("execution.record", "batch.create", "batch.release", "mbr.manage")
    ),
    db: Session = Depends(get_db),
) -> BatchExecutionRecordDetailResponse:
    rec = _svc.verify_execution_record(
        db,
        tenant_ctx=tenant_ctx,
        execution_record_id=execution_record_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return BatchExecutionRecordDetailResponse.model_validate(rec)


@router.post(
    "/execution-records/{execution_record_id}/approve",
    response_model=BatchExecutionRecordDetailResponse,
)
def approve_batch_execution_record(
    request: Request,
    execution_record_id: uuid.UUID,
    payload: BatchExecutionLifecycleActionRequest,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission("mbr.manage", "batch.release", "batch.create")
    ),
    db: Session = Depends(get_db),
) -> BatchExecutionRecordDetailResponse:
    rec = _svc.approve_execution_record(
        db,
        tenant_ctx=tenant_ctx,
        execution_record_id=execution_record_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return BatchExecutionRecordDetailResponse.model_validate(rec)


# --- Batch Execution Steps ---


@router.post(
    "/execution-records/{execution_record_id}/steps",
    response_model=BatchExecutionStepResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_execution_step(
    request: Request,
    execution_record_id: uuid.UUID,
    payload: BatchExecutionStepCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("execution.record")),
    db: Session = Depends(get_db),
) -> BatchExecutionStepResponse:
    step = _svc.add_execution_step(
        db,
        tenant_ctx=tenant_ctx,
        execution_record_id=execution_record_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return BatchExecutionStepResponse.model_validate(step)


@router.get(
    "/execution-records/{execution_record_id}/steps",
    response_model=List[BatchExecutionStepResponse],
)
def list_execution_steps(
    request: Request,
    execution_record_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("execution.read")),
    db: Session = Depends(get_db),
) -> List[BatchExecutionStepResponse]:
    steps = _svc.list_execution_steps(
        db,
        tenant_ctx=tenant_ctx,
        execution_record_id=execution_record_id,
        source_ip=_client_ip(request),
    )
    return [BatchExecutionStepResponse.model_validate(s) for s in steps]


@router.patch(
    "/execution-records/{execution_record_id}/steps/{step_id}",
    response_model=BatchExecutionStepResponse,
)
def update_execution_step(
    request: Request,
    execution_record_id: uuid.UUID,
    step_id: uuid.UUID,
    payload: BatchExecutionStepUpdateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("execution.record")),
    db: Session = Depends(get_db),
) -> BatchExecutionStepResponse:
    step = _svc.update_execution_step(
        db,
        tenant_ctx=tenant_ctx,
        execution_record_id=execution_record_id,
        step_id=step_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return BatchExecutionStepResponse.model_validate(step)


@router.post(
    "/execution-records/{execution_record_id}/steps/{step_id}/execute",
    response_model=BatchExecutionStepResponse,
)
def execute_step(
    request: Request,
    execution_record_id: uuid.UUID,
    step_id: uuid.UUID,
    payload: BatchExecutionStepExecuteRequest,
    tenant_ctx: TenantContext = Depends(require_permission("execution.record")),
    db: Session = Depends(get_db),
) -> BatchExecutionStepResponse:
    step = _svc.execute_step(
        db,
        tenant_ctx=tenant_ctx,
        execution_record_id=execution_record_id,
        step_id=step_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return BatchExecutionStepResponse.model_validate(step)


@router.post(
    "/execution-records/{execution_record_id}/steps/{step_id}/verify",
    response_model=BatchExecutionStepResponse,
)
def verify_step(
    request: Request,
    execution_record_id: uuid.UUID,
    step_id: uuid.UUID,
    payload: BatchExecutionStepVerifyRequest,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission("execution.record", "batch.create", "batch.release", "mbr.manage")
    ),
    db: Session = Depends(get_db),
) -> BatchExecutionStepResponse:
    step = _svc.verify_step(
        db,
        tenant_ctx=tenant_ctx,
        execution_record_id=execution_record_id,
        step_id=step_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return BatchExecutionStepResponse.model_validate(step)


# --- Process Parameter Observations ---


@router.post(
    "/execution-records/{execution_record_id}/steps/{step_id}/observations",
    response_model=ProcessParameterObservationResponse,
    status_code=status.HTTP_201_CREATED,
)
def record_step_observation(
    request: Request,
    execution_record_id: uuid.UUID,
    step_id: uuid.UUID,
    payload: ProcessParameterObservationCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("execution.record")),
    db: Session = Depends(get_db),
) -> ProcessParameterObservationResponse:
    obs = _svc.record_observation(
        db,
        tenant_ctx=tenant_ctx,
        execution_record_id=execution_record_id,
        step_id=step_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return ProcessParameterObservationResponse.model_validate(obs)


@router.get(
    "/execution-records/{execution_record_id}/steps/{step_id}/observations",
    response_model=List[ProcessParameterObservationResponse],
)
def list_step_observations(
    request: Request,
    execution_record_id: uuid.UUID,
    step_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("execution.read")),
    db: Session = Depends(get_db),
) -> List[ProcessParameterObservationResponse]:
    items = _svc.list_observations_for_step(
        db,
        tenant_ctx=tenant_ctx,
        execution_record_id=execution_record_id,
        step_id=step_id,
        source_ip=_client_ip(request),
    )
    return [ProcessParameterObservationResponse.model_validate(o) for o in items]


@router.get(
    "/execution-records/{execution_record_id}/observations",
    response_model=List[ProcessParameterObservationResponse],
)
def list_execution_record_observations(
    request: Request,
    execution_record_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("execution.read")),
    db: Session = Depends(get_db),
) -> List[ProcessParameterObservationResponse]:
    items = _svc.list_observations_for_record(
        db,
        tenant_ctx=tenant_ctx,
        execution_record_id=execution_record_id,
        source_ip=_client_ip(request),
    )
    return [ProcessParameterObservationResponse.model_validate(o) for o in items]


@router.post(
    "/execution-records/{execution_record_id}/observations/{observation_id}/verify",
    response_model=ProcessParameterObservationResponse,
)
def verify_record_observation(
    request: Request,
    execution_record_id: uuid.UUID,
    observation_id: uuid.UUID,
    payload: ProcessParameterObservationVerifyRequest,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission("execution.record", "batch.create", "batch.release", "mbr.manage")
    ),
    db: Session = Depends(get_db),
) -> ProcessParameterObservationResponse:
    obs = _svc.verify_observation(
        db,
        tenant_ctx=tenant_ctx,
        observation_id=observation_id,
        payload=payload,
        execution_record_id=execution_record_id,
        source_ip=_client_ip(request),
    )
    return ProcessParameterObservationResponse.model_validate(obs)


@router.post(
    "/observations/{observation_id}/verify",
    response_model=ProcessParameterObservationResponse,
)
def verify_observation_direct(
    request: Request,
    observation_id: uuid.UUID,
    payload: ProcessParameterObservationVerifyRequest,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission("execution.record", "batch.create", "batch.release", "mbr.manage")
    ),
    db: Session = Depends(get_db),
) -> ProcessParameterObservationResponse:
    obs = _svc.verify_observation(
        db,
        tenant_ctx=tenant_ctx,
        observation_id=observation_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return ProcessParameterObservationResponse.model_validate(obs)


# --- Consolidated Batch Manufacturing & Quality Evidence ---


@router.get(
    "/batches/{batch_id}/manufacturing-evidence",
    response_model=BatchManufacturingEvidenceResponse,
)
def get_batch_manufacturing_evidence(
    request: Request,
    batch_id: uuid.UUID,
    persist_checks: bool = Query(default=True),
    tenant_ctx: TenantContext = Depends(
        _require_any_permission(
            "execution.read", "batch.read", "traceability.read", "batch.release"
        )
    ),
    db: Session = Depends(get_db),
) -> BatchManufacturingEvidenceResponse:
    return _svc.get_batch_manufacturing_evidence(
        db,
        tenant_ctx=tenant_ctx,
        batch_id=batch_id,
        persist_checks=persist_checks,
        source_ip=_client_ip(request),
    )


@router.post(
    "/batches/{batch_id}/evaluate-evidence",
    response_model=BatchManufacturingEvidenceResponse,
)
def evaluate_batch_manufacturing_evidence(
    request: Request,
    batch_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission(
            "execution.read", "batch.read", "traceability.read", "batch.release"
        )
    ),
    db: Session = Depends(get_db),
) -> BatchManufacturingEvidenceResponse:
    return _svc.get_batch_manufacturing_evidence(
        db,
        tenant_ctx=tenant_ctx,
        batch_id=batch_id,
        persist_checks=True,
        source_ip=_client_ip(request),
    )


@router.get(
    "/batches/{batch_id}/deterministic-checks",
    response_model=List[DeterministicEvidenceCheckResponse],
)
def list_batch_deterministic_checks(
    request: Request,
    batch_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission(
            "execution.read", "batch.read", "traceability.read", "batch.release"
        )
    ),
    db: Session = Depends(get_db),
) -> List[DeterministicEvidenceCheckResponse]:
    checks = _svc.list_deterministic_checks_for_batch(
        db, tenant_ctx=tenant_ctx, batch_id=batch_id, source_ip=_client_ip(request)
    )
    return [DeterministicEvidenceCheckResponse.model_validate(c) for c in checks]
