import uuid
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, Header, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.core.deps import TenantContext, get_db, get_tenant_context, require_permission
from app.core.exceptions import PermissionDeniedError
from app.domain.enums import SecurityEventOutcome, SecurityEventSeverity
from app.models.schemas.common import Page
from app.models.schemas.investigations import (
    EvidenceReferenceResponse,
    GeneratedReportResponse,
    HypothesisDecisionRequest,
    InvestigationCreateRequest,
    InvestigationDetailResponse,
    InvestigationReviewRequest,
    InvestigationRunResponse,
    InvestigationRunTriggerRequest,
    InvestigationRunTriggerResponse,
    InvestigationSummaryResponse,
    RootCauseHypothesisResponse,
    ReportGenerateRequest,
    ReportReviewRequest,
    SOPComplianceResultResponse,
    SOPComplianceRunRequest,
)
from app.repositories.investigation_repo import InvestigationRepository
from app.services.audit_service import AuditService
from app.services.investigation_service import InvestigationService
from app.services.report_service import DOCX_MIME_TYPE, ReportService
from app.services.sop_compliance_service import SOPComplianceService

router = APIRouter(tags=["investigations-reports"])

_inv_svc = InvestigationService()
_comp_svc = SOPComplianceService()
_report_svc = ReportService()
_repo = InvestigationRepository()
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


# --- D1: Durable Investigations & Runs ---


@router.post(
    "/investigations",
    response_model=InvestigationSummaryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_investigation(
    request: Request,
    payload: InvestigationCreateRequest,
    tenant_ctx: TenantContext = Depends(require_permission("investigation.open")),
    db: Session = Depends(get_db),
) -> InvestigationSummaryResponse:
    inv = _inv_svc.create_investigation(
        db,
        tenant_ctx=tenant_ctx,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return InvestigationSummaryResponse.model_validate(inv)


@router.get("/investigations", response_model=Page[InvestigationSummaryResponse])
def list_investigations(
    complaint_id: Optional[uuid.UUID] = Query(default=None),
    deviation_id: Optional[uuid.UUID] = Query(default=None),
    batch_id: Optional[uuid.UUID] = Query(default=None),
    investigation_status: Optional[str] = Query(default=None, alias="status"),
    site_id: Optional[uuid.UUID] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(require_permission("investigation.read")),
    db: Session = Depends(get_db),
) -> Page[InvestigationSummaryResponse]:
    items, total = _inv_svc.list_investigations(
        db,
        tenant_ctx=tenant_ctx,
        complaint_id=complaint_id,
        deviation_id=deviation_id,
        batch_id=batch_id,
        status=investigation_status,
        site_id=site_id,
        page=page,
        page_size=page_size,
    )
    return Page(
        items=[InvestigationSummaryResponse.model_validate(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# Static /investigations/runs/{run_id} MUST come before /investigations/{investigation_id}
@router.get("/investigations/runs/{run_id}", response_model=InvestigationRunResponse)
def get_investigation_run_by_id(
    request: Request,
    run_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("investigation.read")),
    db: Session = Depends(get_db),
) -> InvestigationRunResponse:
    run = _inv_svc.get_run(
        db,
        tenant_ctx=tenant_ctx,
        run_id=run_id,
        source_ip=_client_ip(request),
    )
    return InvestigationRunResponse.model_validate(run)


@router.get("/investigations/{investigation_id}", response_model=InvestigationDetailResponse)
def get_investigation(
    request: Request,
    investigation_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("investigation.read")),
    db: Session = Depends(get_db),
) -> InvestigationDetailResponse:
    inv = _inv_svc.get_investigation(
        db,
        tenant_ctx=tenant_ctx,
        investigation_id=investigation_id,
        with_children=True,
        source_ip=_client_ip(request),
    )
    return InvestigationDetailResponse.model_validate(inv)


@router.post(
    "/investigations/{investigation_id}/runs",
    response_model=InvestigationRunTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_investigation_run(
    request: Request,
    response: Response,
    investigation_id: uuid.UUID,
    payload: Optional[InvestigationRunTriggerRequest] = Body(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    tenant_ctx: TenantContext = Depends(require_permission("investigation.execute")),
    db: Session = Depends(get_db),
) -> InvestigationRunTriggerResponse:
    result = _inv_svc.trigger_run(
        db,
        tenant_ctx=tenant_ctx,
        investigation_id=investigation_id,
        payload=payload or InvestigationRunTriggerRequest(),
        idempotency_key=idempotency_key,
        source_ip=_client_ip(request),
    )
    if result.idempotent_replay:
        response.status_code = status.HTTP_200_OK
    return result


@router.get(
    "/investigations/{investigation_id}/runs/{run_id}",
    response_model=InvestigationRunResponse,
)
def get_investigation_run(
    request: Request,
    investigation_id: uuid.UUID,
    run_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("investigation.read")),
    db: Session = Depends(get_db),
) -> InvestigationRunResponse:
    run = _inv_svc.get_run(
        db,
        tenant_ctx=tenant_ctx,
        run_id=run_id,
        investigation_id=investigation_id,
        source_ip=_client_ip(request),
    )
    return InvestigationRunResponse.model_validate(run)


@router.post(
    "/investigations/{investigation_id}/runs/{run_id}/retry",
    response_model=InvestigationRunTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_investigation_run(
    request: Request,
    investigation_id: uuid.UUID,
    run_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("investigation.execute")),
    db: Session = Depends(get_db),
) -> InvestigationRunTriggerResponse:
    return _inv_svc.retry_run(
        db,
        tenant_ctx=tenant_ctx,
        investigation_id=investigation_id,
        run_id=run_id,
        source_ip=_client_ip(request),
    )


# --- D3 & D4: Deterministic SOP Compliance & Evidence ---


@router.post(
    "/investigations/{investigation_id}/compliance-checks",
    response_model=List[SOPComplianceResultResponse],
)
def run_sop_compliance_checks(
    investigation_id: uuid.UUID,
    payload: Optional[SOPComplianceRunRequest] = Body(default=None),
    tenant_ctx: TenantContext = Depends(require_permission("investigation.execute")),
    db: Session = Depends(get_db),
) -> List[SOPComplianceResultResponse]:
    req = payload or SOPComplianceRunRequest()
    items = _comp_svc.run_compliance_check(
        db,
        tenant_ctx=tenant_ctx,
        investigation_id=investigation_id,
        batch_id=req.batch_id,
        run_id=req.run_id,
    )
    return [SOPComplianceResultResponse.model_validate(c) for c in items]


@router.get(
    "/investigations/{investigation_id}/compliance-checks",
    response_model=List[SOPComplianceResultResponse],
)
def list_sop_compliance_checks(
    request: Request,
    investigation_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("investigation.read")),
    db: Session = Depends(get_db),
) -> List[SOPComplianceResultResponse]:
    inv = _inv_svc.get_investigation(
        db,
        tenant_ctx=tenant_ctx,
        investigation_id=investigation_id,
        with_children=False,
        source_ip=_client_ip(request),
    )
    items = _repo.list_compliance_results_for_investigation(
        db, tenant_ctx.organization_id, inv.id
    )
    return [SOPComplianceResultResponse.model_validate(c) for c in items]


@router.get(
    "/investigations/{investigation_id}/evidence",
    response_model=List[EvidenceReferenceResponse],
)
def list_investigation_evidence(
    request: Request,
    investigation_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("investigation.read")),
    db: Session = Depends(get_db),
) -> List[EvidenceReferenceResponse]:
    inv = _inv_svc.get_investigation(
        db,
        tenant_ctx=tenant_ctx,
        investigation_id=investigation_id,
        with_children=False,
        source_ip=_client_ip(request),
    )
    items = _repo.list_evidence_for_investigation(db, tenant_ctx.organization_id, inv.id)
    return [EvidenceReferenceResponse.model_validate(e) for e in items]


# --- D5 & D7: RCA Hypotheses & QA Decisions ---


@router.get(
    "/investigations/{investigation_id}/hypotheses",
    response_model=List[RootCauseHypothesisResponse],
)
def list_investigation_hypotheses(
    request: Request,
    investigation_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("investigation.read")),
    db: Session = Depends(get_db),
) -> List[RootCauseHypothesisResponse]:
    inv = _inv_svc.get_investigation(
        db,
        tenant_ctx=tenant_ctx,
        investigation_id=investigation_id,
        with_children=False,
        source_ip=_client_ip(request),
    )
    items = _repo.list_hypotheses_for_investigation(db, tenant_ctx.organization_id, inv.id)
    return [RootCauseHypothesisResponse.model_validate(h) for h in items]


@router.post("/hypotheses/{hypothesis_id}/decision", response_model=RootCauseHypothesisResponse)
def decide_hypothesis(
    request: Request,
    hypothesis_id: uuid.UUID,
    payload: HypothesisDecisionRequest,
    tenant_ctx: TenantContext = Depends(require_permission("investigation.review")),
    db: Session = Depends(get_db),
) -> RootCauseHypothesisResponse:
    hyp = _inv_svc.decide_hypothesis(
        db,
        tenant_ctx=tenant_ctx,
        hypothesis_id=hypothesis_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return RootCauseHypothesisResponse.model_validate(hyp)


# --- D6 & D7: DOCX Reports & QA Review ---


@router.post(
    "/investigations/{investigation_id}/reports",
    response_model=GeneratedReportResponse,
    status_code=status.HTTP_201_CREATED,
)
def generate_investigation_report(
    request: Request,
    investigation_id: uuid.UUID,
    payload: Optional[ReportGenerateRequest] = Body(default=None),
    tenant_ctx: TenantContext = Depends(require_permission("report.generate")),
    db: Session = Depends(get_db),
) -> GeneratedReportResponse:
    report = _report_svc.generate_report(
        db,
        tenant_ctx=tenant_ctx,
        investigation_id=investigation_id,
        payload=payload or ReportGenerateRequest(),
        source_ip=_client_ip(request),
    )
    return GeneratedReportResponse.model_validate(report)


@router.get(
    "/investigations/{investigation_id}/reports",
    response_model=List[GeneratedReportResponse],
)
def list_investigation_reports(
    investigation_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("investigation.read")),
    db: Session = Depends(get_db),
) -> List[GeneratedReportResponse]:
    items = _report_svc.list_reports(
        db, tenant_ctx=tenant_ctx, investigation_id=investigation_id
    )
    return [GeneratedReportResponse.model_validate(r) for r in items]


@router.get("/investigations/{investigation_id}/reports/{report_id}/download")
def download_investigation_report(
    request: Request,
    investigation_id: uuid.UUID,
    report_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("report.download")),
    db: Session = Depends(get_db),
) -> Response:
    report, ver, data = _report_svc.download_report(
        db,
        tenant_ctx=tenant_ctx,
        report_id=report_id,
        investigation_id=investigation_id,
        source_ip=_client_ip(request),
    )
    return Response(
        content=data,
        media_type=DOCX_MIME_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{ver.original_filename}"',
            "X-Document-Version-Id": str(ver.id),
            "X-Checksum-SHA256": ver.checksum_sha256,
        },
    )


@router.get("/reports/{report_id}/download")
def download_report_by_id(
    request: Request,
    report_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("report.download")),
    db: Session = Depends(get_db),
) -> Response:
    report, ver, data = _report_svc.download_report(
        db,
        tenant_ctx=tenant_ctx,
        report_id=report_id,
        source_ip=_client_ip(request),
    )
    return Response(
        content=data,
        media_type=DOCX_MIME_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{ver.original_filename}"',
            "X-Document-Version-Id": str(ver.id),
            "X-Checksum-SHA256": ver.checksum_sha256,
        },
    )


@router.post(
    "/investigations/{investigation_id}/reports/{report_id}/review",
    response_model=GeneratedReportResponse,
)
def review_investigation_report(
    request: Request,
    investigation_id: uuid.UUID,
    report_id: uuid.UUID,
    payload: ReportReviewRequest,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission("report.review", "investigation.review")
    ),
    db: Session = Depends(get_db),
) -> GeneratedReportResponse:
    report = _report_svc.review_report(
        db,
        tenant_ctx=tenant_ctx,
        report_id=report_id,
        payload=payload,
        investigation_id=investigation_id,
        source_ip=_client_ip(request),
    )
    return GeneratedReportResponse.model_validate(report)


@router.post("/reports/{report_id}/review", response_model=GeneratedReportResponse)
def review_report_by_id(
    request: Request,
    report_id: uuid.UUID,
    payload: ReportReviewRequest,
    tenant_ctx: TenantContext = Depends(
        _require_any_permission("report.review", "investigation.review")
    ),
    db: Session = Depends(get_db),
) -> GeneratedReportResponse:
    report = _report_svc.review_report(
        db,
        tenant_ctx=tenant_ctx,
        report_id=report_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return GeneratedReportResponse.model_validate(report)


@router.post("/investigations/{investigation_id}/review", response_model=InvestigationSummaryResponse)
def review_investigation(
    request: Request,
    investigation_id: uuid.UUID,
    payload: InvestigationReviewRequest,
    tenant_ctx: TenantContext = Depends(require_permission("investigation.review")),
    db: Session = Depends(get_db),
) -> InvestigationSummaryResponse:
    inv = _inv_svc.review_investigation(
        db,
        tenant_ctx=tenant_ctx,
        investigation_id=investigation_id,
        payload=payload,
        source_ip=_client_ip(request),
    )
    return InvestigationSummaryResponse.model_validate(inv)
