import json
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import (
    Principal,
    TenantContext,
    get_current_principal,
    get_db,
    get_tenant_context,
    require_permission,
    require_platform_permission,
)
from app.core.exceptions import AppError, PermissionDeniedError, ValidationError
from app.domain.enums import ConfidentialityLevel
from app.models.schemas.common import Page
from app.models.schemas.master_documents import (
    DocumentDetailResponse,
    DocumentResponse,
    DocumentVersionResponse,
    ExtractedContentResponse,
    ExtractionJobResponse,
    ReferenceValueCreateRequest,
    ReferenceValueResponse,
    UnitOfMeasureCreateRequest,
    UnitOfMeasureResponse,
)
from app.services.document_control_service import DocumentControlService
from app.services.master_data_service import MasterDataService

router = APIRouter(tags=["master-data-documents"])

_master_data_svc = MasterDataService()
_doc_svc = DocumentControlService()


def _read_upload_bounded(upload: UploadFile, max_bytes: int) -> bytes:
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = upload.file.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise AppError(
                f"Uploaded file exceeds maximum allowed size of {max_bytes} bytes.",
                status_code=413,
                error_code="PAYLOAD_TOO_LARGE",
            )
        chunks.append(chunk)
    return b"".join(chunks)


# --- Phase 1 Master Data Routes ---


@router.get("/reference-values", response_model=Page[ReferenceValueResponse])
def list_reference_values(
    request: Request,
    category_code: Optional[str] = Query(default=None),
    active_only: bool = Query(default=True),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    x_organization_id: Optional[str] = Header(default=None, alias="X-Organization-Id"),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Page[ReferenceValueResponse]:
    org_id = None
    if principal.platform_role == "PLATFORM_SUPER_ADMIN" and not x_organization_id:
        org_id = None
    else:
        tenant_ctx = get_tenant_context(
            request=request, principal=principal, x_organization_id=x_organization_id, db=db
        )
        if "master_data.read" not in tenant_ctx.permission_codes:
            raise PermissionDeniedError(
                "Missing required permission 'master_data.read'.",
                error_code="INSUFFICIENT_PERMISSION",
            )
        org_id = tenant_ctx.organization_id

    items, total = _master_data_svc.list_reference_values(
        db,
        organization_id=org_id,
        category_code=category_code,
        active_only=active_only,
        page=page,
        page_size=page_size,
    )
    return Page(
        items=[ReferenceValueResponse.model_validate(r) for r in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/reference-values", response_model=ReferenceValueResponse, status_code=status.HTTP_201_CREATED)
def create_reference_value(
    request: Request,
    payload: ReferenceValueCreateRequest,
    x_organization_id: Optional[str] = Header(default=None, alias="X-Organization-Id"),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ReferenceValueResponse:
    tenant_ctx: Optional[TenantContext] = None
    if payload.is_global:
        require_platform_permission("master_data.manage_global")(
            request=request, principal=principal, db=db
        )
    else:
        tenant_ctx = get_tenant_context(
            request=request, principal=principal, x_organization_id=x_organization_id, db=db
        )
        if "master_data.manage_tenant" not in tenant_ctx.permission_codes:
            raise PermissionDeniedError(
                "Missing required permission 'master_data.manage_tenant'.",
                error_code="INSUFFICIENT_PERMISSION",
            )

    ref = _master_data_svc.create_reference_value(
        db, principal=principal, tenant_ctx=tenant_ctx, payload=payload
    )
    return ReferenceValueResponse.model_validate(ref)


@router.get("/units-of-measure", response_model=Page[UnitOfMeasureResponse])
def list_units_of_measure(
    request: Request,
    dimension: Optional[str] = Query(default=None),
    active_only: bool = Query(default=True),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    x_organization_id: Optional[str] = Header(default=None, alias="X-Organization-Id"),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Page[UnitOfMeasureResponse]:
    if principal.platform_role != "PLATFORM_SUPER_ADMIN":
        tenant_ctx = get_tenant_context(
            request=request, principal=principal, x_organization_id=x_organization_id, db=db
        )
        if "master_data.read" not in tenant_ctx.permission_codes:
            raise PermissionDeniedError(
                "Missing required permission 'master_data.read'.",
                error_code="INSUFFICIENT_PERMISSION",
            )

    items, total = _master_data_svc.list_units_of_measure(
        db, dimension=dimension, active_only=active_only, page=page, page_size=page_size
    )
    return Page(
        items=[UnitOfMeasureResponse.model_validate(u) for u in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/units-of-measure", response_model=UnitOfMeasureResponse, status_code=status.HTTP_201_CREATED)
def create_unit_of_measure(
    payload: UnitOfMeasureCreateRequest,
    principal: Principal = Depends(require_platform_permission("master_data.manage_global")),
    db: Session = Depends(get_db),
) -> UnitOfMeasureResponse:
    uom = _master_data_svc.create_unit_of_measure(db, principal=principal, payload=payload)
    return UnitOfMeasureResponse.model_validate(uom)


# --- Phase 2 Document Vault & OCR Extraction Routes (Module A7) ---


@router.post("/documents", response_model=DocumentDetailResponse, status_code=status.HTTP_201_CREATED)
def upload_document(
    file: UploadFile = File(...),
    document_type: str = Form(...),
    title: str = Form(...),
    document_code: Optional[str] = Form(default=None),
    site_id: Optional[uuid.UUID] = Form(default=None),
    confidentiality: str = Form(default=ConfidentialityLevel.INTERNAL.value),
    metadata_json: Optional[str] = Form(default=None),
    tenant_ctx: TenantContext = Depends(require_permission("document.upload")),
    db: Session = Depends(get_db),
) -> DocumentDetailResponse:
    parsed_meta: Optional[Dict[str, Any]] = None
    if metadata_json:
        try:
            loaded = json.loads(metadata_json)
            if not isinstance(loaded, dict):
                raise ValueError("metadata_json must be a JSON object.")
            parsed_meta = loaded
        except ValueError as exc:
            raise ValidationError("Invalid metadata_json payload.") from exc

    raw_bytes = _read_upload_bounded(file, get_settings().UPLOAD_MAX_BYTES)
    doc = _doc_svc.upload_document(
        db,
        tenant_ctx=tenant_ctx,
        file_bytes=raw_bytes,
        original_filename=file.filename or "upload.bin",
        claimed_mime_type=file.content_type,
        document_type=document_type,
        title=title,
        document_code=document_code,
        site_id=site_id,
        confidentiality=confidentiality,
        metadata_json=parsed_meta,
    )
    doc_loaded, versions = _doc_svc.get_document_detail(db, tenant_ctx=tenant_ctx, document_id=doc.id)
    resp = DocumentDetailResponse.model_validate(doc_loaded)
    resp.versions = [DocumentVersionResponse.model_validate(v) for v in versions]
    return resp


@router.get("/documents", response_model=Page[DocumentResponse])
def list_documents(
    document_type: Optional[str] = Query(default=None),
    doc_status: Optional[str] = Query(default=None, alias="status"),
    site_id: Optional[uuid.UUID] = Query(default=None),
    search: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(require_permission("document.read")),
    db: Session = Depends(get_db),
) -> Page[DocumentResponse]:
    items, total = _doc_svc.list_documents(
        db,
        tenant_ctx=tenant_ctx,
        document_type=document_type,
        status=doc_status,
        site_id=site_id,
        search=search,
        page=page,
        page_size=page_size,
    )
    return Page(
        items=[DocumentResponse.model_validate(d) for d in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/documents/{document_id}", response_model=DocumentDetailResponse)
def get_document(
    document_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("document.read")),
    db: Session = Depends(get_db),
) -> DocumentDetailResponse:
    doc, versions = _doc_svc.get_document_detail(db, tenant_ctx=tenant_ctx, document_id=document_id)
    resp = DocumentDetailResponse.model_validate(doc)
    resp.versions = [DocumentVersionResponse.model_validate(v) for v in versions]
    return resp


@router.post(
    "/documents/{document_id}/versions",
    response_model=DocumentVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_document_version(
    document_id: uuid.UUID,
    file: UploadFile = File(...),
    version_label: Optional[str] = Form(default=None),
    tenant_ctx: TenantContext = Depends(require_permission("document.upload")),
    db: Session = Depends(get_db),
) -> DocumentVersionResponse:
    raw_bytes = _read_upload_bounded(file, get_settings().UPLOAD_MAX_BYTES)
    ver = _doc_svc.add_document_version(
        db,
        document_id=document_id,
        tenant_ctx=tenant_ctx,
        file_bytes=raw_bytes,
        original_filename=file.filename or "upload.bin",
        claimed_mime_type=file.content_type,
        version_label=version_label,
    )
    return DocumentVersionResponse.model_validate(ver)


@router.get("/documents/{document_id}/versions", response_model=List[DocumentVersionResponse])
def list_document_versions(
    document_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("document.read")),
    db: Session = Depends(get_db),
) -> List[DocumentVersionResponse]:
    _doc, versions = _doc_svc.get_document_detail(db, tenant_ctx=tenant_ctx, document_id=document_id)
    return [DocumentVersionResponse.model_validate(v) for v in versions]


@router.get("/document-versions/{version_id}/download")
def download_document_version(
    version_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("document.download")),
    db: Session = Depends(get_db),
) -> Response:
    ver, _doc, data = _doc_svc.download_version(db, version_id=version_id, tenant_ctx=tenant_ctx)
    return Response(
        content=data,
        media_type=ver.mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{ver.original_filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "ETag": f'"{ver.checksum_sha256}"',
        },
    )


@router.post(
    "/document-versions/{version_id}/extract",
    response_model=ExtractionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_document_extraction(
    version_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("document.upload")),
    db: Session = Depends(get_db),
) -> ExtractionJobResponse:
    job = _doc_svc.request_extraction(db, version_id=version_id, tenant_ctx=tenant_ctx)
    return ExtractionJobResponse.model_validate(job)


@router.get("/extraction-jobs/{job_id}", response_model=ExtractionJobResponse)
def get_extraction_job(
    job_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(require_permission("document.read")),
    db: Session = Depends(get_db),
) -> ExtractionJobResponse:
    job, contents = _doc_svc.get_extraction_job(db, job_id=job_id, tenant_ctx=tenant_ctx)
    resp = ExtractionJobResponse.model_validate(job)
    resp.extracted_contents = [ExtractedContentResponse.model_validate(c) for c in contents]
    return resp
