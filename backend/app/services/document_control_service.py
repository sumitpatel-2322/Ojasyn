import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import puremagic
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.database import UnitOfWork, get_session_factory, next_tenant_sequence
from app.core.deps import TenantContext
from app.core.exceptions import AppError, ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.domain.enums import (
    ActorType,
    ConfidentialityLevel,
    DocumentApprovalStatus,
    DocumentStatus,
    DocumentType,
    ExtractionJobStatus,
    ScanStatus,
    SecurityEventOutcome,
    SecurityEventSeverity,
)
from app.integrations.ocr_provider import OCRProvider, PdfPlumberTextOCRAdapter
from app.integrations.storage_provider import (
    LocalPrivateVaultAdapter,
    ObjectStorageProvider,
    build_vault_object_key,
)
from app.models.domain.documents import Document, DocumentVersion, ExtractedContent, ExtractionJob
from app.models.domain.governance import OutboxEvent
from app.repositories.document_sop_repo import DocumentSOPRepository
from app.repositories.governance_repo import OutboxRepository
from app.repositories.organization_repo import OrganizationRepository
from app.services.audit_service import AuditService

_DISALLOWED_ACTIVE_MIMES = {
    "image/svg+xml",
    "text/html",
    "application/javascript",
    "text/javascript",
    "application/x-msdownload",
    "application/x-dosexec",
    "application/x-executable",
    "application/x-sh",
}


def sanitize_display_filename(filename: str) -> str:
    """Strips path components, null bytes, and unsafe control characters from display filenames."""
    if not filename or "\x00" in filename:
        raise ValidationError("Invalid filename.")
    base = re.split(r"[\\/]", filename)[-1].strip()
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", base).lstrip(".")
    if not cleaned:
        raise ValidationError("Filename must contain valid non-separator characters.")
    return cleaned[:200]


def _sniff_dangerous_signature(file_bytes: bytes) -> Optional[str]:
    head = file_bytes[:512].lstrip()
    lower_head = head.lower()
    if lower_head.startswith(b"<svg") or (b"<svg" in lower_head and lower_head.startswith(b"<?xml")):
        return "image/svg+xml"
    if lower_head.startswith((b"<!doctype html", b"<html", b"<script")):
        return "text/html"
    if file_bytes.startswith(b"MZ") or file_bytes.startswith(b"\x7fELF") or file_bytes.startswith(b"#!"):
        return "application/x-executable"
    return None


def _detect_verified_mime(file_bytes: bytes) -> str:
    dangerous = _sniff_dangerous_signature(file_bytes)
    if dangerous is not None:
        return dangerous
    try:
        matches = puremagic.magic_string(file_bytes)
    except Exception:
        matches = []

    for match in matches:
        mime = (getattr(match, "mime_type", None) or "").strip().lower()
        if mime:
            # Handle canonical PDF/image MIME normalization
            if mime == "image/jpg":
                return "image/jpeg"
            return mime
    return ""


class DocumentControlService:
    """
    Central Document Vault, MIME/magic validation, SHA-256 integrity, versioning,
    download authorization, and OCR extraction orchestration service (Module A7).
    """

    def __init__(
        self,
        repo: Optional[DocumentSOPRepository] = None,
        org_repo: Optional[OrganizationRepository] = None,
        storage: Optional[ObjectStorageProvider] = None,
        ocr: Optional[OCRProvider] = None,
        audit_service: Optional[AuditService] = None,
        outbox_repo: Optional[OutboxRepository] = None,
    ) -> None:
        self._repo = repo or DocumentSOPRepository()
        self._org_repo = org_repo or OrganizationRepository()
        self._storage = storage or LocalPrivateVaultAdapter()
        self._ocr = ocr or PdfPlumberTextOCRAdapter()
        self._audit = audit_service or AuditService()
        self._outbox = outbox_repo or OutboxRepository()

    def _validate_upload_bytes(
        self,
        file_bytes: bytes,
        *,
        original_filename: str,
        claimed_mime_type: Optional[str],
        organization_id: Optional[uuid.UUID],
        user_id: Optional[uuid.UUID],
    ) -> Tuple[str, str, str]:
        settings = get_settings()
        if not file_bytes:
            raise ValidationError("Uploaded file must not be empty.")
        if len(file_bytes) > settings.UPLOAD_MAX_BYTES:
            raise AppError(
                f"Uploaded file exceeds maximum allowed size of {settings.UPLOAD_MAX_BYTES} bytes.",
                status_code=413,
                error_code="PAYLOAD_TOO_LARGE",
            )

        safe_name = sanitize_display_filename(original_filename)
        detected_mime = _detect_verified_mime(file_bytes)

        if not detected_mime or detected_mime in _DISALLOWED_ACTIVE_MIMES:
            self._audit.record_security_event(
                event_type="UPLOAD_DISALLOWED_CONTENT",
                outcome=SecurityEventOutcome.BLOCKED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=organization_id,
                user_id=user_id,
                target_resource="/api/v1/documents",
                details_json={"detected_mime": detected_mime or "unknown", "filename": safe_name},
            )
            raise ValidationError(
                f"File type '{detected_mime or 'unknown'}' is not permitted in the document vault.",
                error_code="UNSUPPORTED_MEDIA_TYPE",
            )

        allowlist = {m.strip().lower() for m in settings.MIME_ALLOWLIST}
        if detected_mime not in allowlist:
            raise ValidationError(
                f"Detected file type '{detected_mime}' is not in the permitted allowlist.",
                error_code="UNSUPPORTED_MEDIA_TYPE",
            )

        if claimed_mime_type:
            claimed = claimed_mime_type.split(";")[0].strip().lower()
            if claimed == "image/jpg":
                claimed = "image/jpeg"
            if claimed and claimed != "application/octet-stream" and claimed != detected_mime:
                self._audit.record_security_event(
                    event_type="UPLOAD_MIME_SPOOFING_DETECTED",
                    outcome=SecurityEventOutcome.BLOCKED.value,
                    severity=SecurityEventSeverity.HIGH.value,
                    organization_id=organization_id,
                    user_id=user_id,
                    target_resource="/api/v1/documents",
                    details_json={"claimed_mime": claimed, "detected_mime": detected_mime},
                )
                raise ValidationError(
                    "Uploaded file magic bytes do not match the declared Content-Type.",
                    error_code="MIME_SPOOFING_DETECTED",
                )

        checksum = hashlib.sha256(file_bytes).hexdigest()
        return safe_name, detected_mime, checksum

    @staticmethod
    def _validate_document_type(raw_type: str) -> str:
        normalized = (raw_type or "").strip().upper()
        try:
            return DocumentType(normalized).value
        except ValueError as exc:
            raise ValidationError(f"Invalid document_type '{raw_type}'.") from exc

    @staticmethod
    def _validate_confidentiality(raw_conf: str) -> str:
        normalized = (raw_conf or "").strip().upper()
        try:
            return ConfidentialityLevel(normalized).value
        except ValueError as exc:
            raise ValidationError(f"Invalid confidentiality level '{raw_conf}'.") from exc

    def _assert_site_access(self, db: Session, tenant_ctx: TenantContext, doc: Document, *, resource: str) -> None:
        if doc.site_id is not None and not tenant_ctx.is_org_wide and doc.site_id not in tenant_ctx.allowed_site_ids:
            self._audit.record_security_event(
                event_type="DOCUMENT_SITE_ACCESS_DENIED",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                target_resource=resource,
                details_json={"document_id": str(doc.id), "site_id": str(doc.site_id)},
            )
            raise PermissionDeniedError("Not authorized to access documents for this site.")

    def upload_in_tx(
        self,
        db: Session,
        *,
        organization_id: Optional[uuid.UUID],
        uploaded_by_user_id: Optional[uuid.UUID],
        file_bytes: bytes,
        original_filename: str,
        claimed_mime_type: Optional[str] = None,
        category: str = DocumentType.COMPLAINT_EVIDENCE.value,
        site_id: Optional[uuid.UUID] = None,
        title: Optional[str] = None,
        document_code: Optional[str] = None,
        confidentiality: Optional[str] = None,
        owner_membership_id: Optional[uuid.UUID] = None,
        metadata_json: Optional[Dict[str, Any]] = None,
        pre_stored: Optional[Tuple[uuid.UUID, uuid.UUID, str, str, str, str]] = None,
    ) -> Document:
        """
        Creates `Document` + `DocumentVersion` (v1) inside the caller's active transaction (`db.flush()` only;
        never calls `db.commit()`). Safe for `ComplaintService.attach_document()` atomic commits.
        """
        doc_type = self._validate_document_type(category)
        if confidentiality is None:
            conf_level = (
                ConfidentialityLevel.PUBLIC_INTAKE.value
                if organization_id is None
                else ConfidentialityLevel.INTERNAL.value
            )
        else:
            conf_level = self._validate_confidentiality(confidentiality)

        if site_id is not None:
            if organization_id is None:
                raise ValidationError("site_id cannot be set when organization_id is NULL.")
            if self._org_repo.get_site_for_org(db, organization_id, site_id) is None:
                raise NotFoundError("Site not found in this organization.")

        if pre_stored is not None:
            doc_id, ver_id, object_key, safe_name, detected_mime, checksum = pre_stored
        else:
            safe_name, detected_mime, checksum = self._validate_upload_bytes(
                file_bytes,
                original_filename=original_filename,
                claimed_mime_type=claimed_mime_type,
                organization_id=organization_id,
                user_id=uploaded_by_user_id,
            )
            doc_id = uuid.uuid4()
            ver_id = uuid.uuid4()
            object_key = build_vault_object_key(organization_id, doc_id, ver_id)
            self._storage.put_object(object_key, file_bytes, content_type=detected_mime)

        try:
            now = datetime.now(timezone.utc)
            if document_code:
                resolved_code = document_code.strip().upper()
                if self._repo.get_document_by_code(db, organization_id, resolved_code) is not None:
                    raise ConflictError(f"Document code '{resolved_code}' already exists.")
            elif organization_id is not None:
                seq = next_tenant_sequence(db, organization_id, "DOC", period_year=now.year)
                resolved_code = f"DOC-{now.year}-{seq:06d}"
            else:
                resolved_code = f"DOC-UNROUTED-{doc_id.hex[:12].upper()}"

            doc = Document(
                id=doc_id,
                organization_id=organization_id,
                site_id=site_id,
                document_code=resolved_code,
                document_type=doc_type,
                title=(title or safe_name)[:255],
                confidentiality=conf_level,
                owner_membership_id=owner_membership_id,
                status=DocumentStatus.ACTIVE.value,
                metadata_json=metadata_json,
                created_by_user_id=uploaded_by_user_id,
                updated_by_user_id=uploaded_by_user_id,
            )
            self._repo.create_document(db, doc)

            version = DocumentVersion(
                id=ver_id,
                document_id=doc.id,
                version_label="1.0",
                version_number=1,
                object_key=object_key,
                original_filename=safe_name,
                mime_type=detected_mime,
                byte_size=len(file_bytes),
                checksum_sha256=checksum,
                scan_status=ScanStatus.CLEAN.value,
                approval_status=DocumentApprovalStatus.DRAFT.value,
                uploaded_by_user_id=uploaded_by_user_id,
                created_by_user_id=uploaded_by_user_id,
                updated_by_user_id=uploaded_by_user_id,
            )
            self._repo.create_version(db, version)

            self._audit.record(
                db,
                organization_id=organization_id,
                site_id=site_id,
                actor_type=ActorType.USER.value if uploaded_by_user_id else ActorType.ANONYMOUS.value,
                actor_id=uploaded_by_user_id,
                action="DOCUMENT_UPLOADED",
                entity_type="documents",
                entity_id=doc.id,
                after_state={
                    "document_code": doc.document_code,
                    "document_type": doc.document_type,
                    "version_id": str(version.id),
                    "checksum_sha256": checksum,
                },
            )
            db.flush()
            return doc
        except Exception:
            if pre_stored is None:
                self._storage.delete_object(object_key)
            raise

    def upload_document(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        file_bytes: bytes,
        original_filename: str,
        claimed_mime_type: Optional[str] = None,
        document_type: str,
        title: str,
        document_code: Optional[str] = None,
        site_id: Optional[uuid.UUID] = None,
        confidentiality: str = ConfidentialityLevel.INTERNAL.value,
        metadata_json: Optional[Dict[str, Any]] = None,
    ) -> Document:
        if site_id is not None and not tenant_ctx.is_org_wide and site_id not in tenant_ctx.allowed_site_ids:
            raise PermissionDeniedError("Not authorized to upload documents for this site.")

        safe_name, detected_mime, checksum = self._validate_upload_bytes(
            file_bytes,
            original_filename=original_filename,
            claimed_mime_type=claimed_mime_type,
            organization_id=tenant_ctx.organization_id,
            user_id=tenant_ctx.user_id,
        )
        doc_id = uuid.uuid4()
        ver_id = uuid.uuid4()
        object_key = build_vault_object_key(tenant_ctx.organization_id, doc_id, ver_id)

        # Write binary to private vault BEFORE opening the DB transaction
        self._storage.put_object(object_key, file_bytes, content_type=detected_mime)
        try:
            with UnitOfWork(db):
                doc = self.upload_in_tx(
                    db,
                    organization_id=tenant_ctx.organization_id,
                    uploaded_by_user_id=tenant_ctx.user_id,
                    file_bytes=file_bytes,
                    original_filename=safe_name,
                    claimed_mime_type=detected_mime,
                    category=document_type,
                    site_id=site_id,
                    title=title,
                    document_code=document_code,
                    confidentiality=confidentiality,
                    owner_membership_id=tenant_ctx.membership_id,
                    metadata_json=metadata_json,
                    pre_stored=(doc_id, ver_id, object_key, safe_name, detected_mime, checksum),
                )
            db.refresh(doc)
            return doc
        except Exception:
            self._storage.delete_object(object_key)
            raise

    def add_document_version(
        self,
        db: Session,
        *,
        document_id: uuid.UUID,
        tenant_ctx: TenantContext,
        file_bytes: bytes,
        original_filename: str,
        claimed_mime_type: Optional[str] = None,
        version_label: Optional[str] = None,
    ) -> DocumentVersion:
        doc = self._repo.get_document_for_org(db, tenant_ctx.organization_id, document_id)
        if doc is None:
            raise NotFoundError("Document not found.")
        self._assert_site_access(db, tenant_ctx, doc, resource=f"/api/v1/documents/{document_id}/versions")

        latest = self._repo.get_latest_version(db, doc.id)
        next_num = (latest.version_number + 1) if latest else 1
        label = (version_label or f"{next_num}.0").strip()

        safe_name, detected_mime, checksum = self._validate_upload_bytes(
            file_bytes,
            original_filename=original_filename,
            claimed_mime_type=claimed_mime_type,
            organization_id=tenant_ctx.organization_id,
            user_id=tenant_ctx.user_id,
        )
        ver_id = uuid.uuid4()
        object_key = build_vault_object_key(tenant_ctx.organization_id, doc.id, ver_id)

        self._storage.put_object(object_key, file_bytes, content_type=detected_mime)
        try:
            with UnitOfWork(db):
                version = DocumentVersion(
                    id=ver_id,
                    document_id=doc.id,
                    version_label=label,
                    version_number=next_num,
                    object_key=object_key,
                    original_filename=safe_name,
                    mime_type=detected_mime,
                    byte_size=len(file_bytes),
                    checksum_sha256=checksum,
                    scan_status=ScanStatus.CLEAN.value,
                    approval_status=DocumentApprovalStatus.DRAFT.value,
                    uploaded_by_user_id=tenant_ctx.user_id,
                    supersedes_version_id=latest.id if latest else None,
                    created_by_user_id=tenant_ctx.user_id,
                    updated_by_user_id=tenant_ctx.user_id,
                )
                self._repo.create_version(db, version)
                self._audit.record(
                    db,
                    organization_id=tenant_ctx.organization_id,
                    site_id=doc.site_id,
                    actor_type=ActorType.USER.value,
                    actor_id=tenant_ctx.user_id,
                    action="DOCUMENT_VERSION_ADDED",
                    entity_type="document_versions",
                    entity_id=version.id,
                    after_state={"version_number": next_num, "version_label": label, "checksum_sha256": checksum},
                )
            db.refresh(version)
            return version
        except Exception:
            self._storage.delete_object(object_key)
            raise

    def assign_organization_in_tx(
        self,
        db: Session,
        document_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> Document:
        """Atomically backfills `organization_id` onto an unrouted attachment inside caller's UnitOfWork."""
        doc = self._repo.get_document_by_id(db, document_id)
        if doc is None:
            raise NotFoundError("Document not found.")
        if doc.organization_id == organization_id:
            return doc
        if doc.organization_id is not None:
            raise ConflictError("Document is already assigned to another organization.")
        doc.organization_id = organization_id
        db.flush()
        return doc

    def list_documents(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        document_type: Optional[str] = None,
        status: Optional[str] = None,
        site_id: Optional[uuid.UUID] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Document], int]:
        return self._repo.list_documents_for_tenant(
            db,
            tenant_ctx,
            document_type=document_type,
            status=status,
            site_id=site_id,
            search=search,
            page=page,
            page_size=page_size,
        )

    def get_document_detail(
        self, db: Session, *, tenant_ctx: TenantContext, document_id: uuid.UUID
    ) -> Tuple[Document, List[DocumentVersion]]:
        doc = self._repo.get_document_for_org(db, tenant_ctx.organization_id, document_id)
        if doc is None:
            raise NotFoundError("Document not found.")
        self._assert_site_access(db, tenant_ctx, doc, resource=f"/api/v1/documents/{document_id}")
        versions = self._repo.list_versions_for_document(db, doc.id)
        return doc, versions

    def download_version(
        self,
        db: Session,
        *,
        version_id: uuid.UUID,
        tenant_ctx: TenantContext,
    ) -> Tuple[DocumentVersion, Document, bytes]:
        pair = self._repo.get_version_with_document(db, version_id)
        if pair is None:
            raise NotFoundError("Document version not found.")
        ver, doc = pair
        if doc.organization_id != tenant_ctx.organization_id:
            raise NotFoundError("Document version not found.")
        self._assert_site_access(
            db, tenant_ctx, doc, resource=f"/api/v1/document-versions/{version_id}/download"
        )

        if ver.scan_status != ScanStatus.CLEAN.value:
            self._audit.record_security_event(
                event_type="QUARANTINED_DOCUMENT_DOWNLOAD_BLOCKED",
                outcome=SecurityEventOutcome.BLOCKED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                target_resource=f"/api/v1/document-versions/{version_id}/download",
                details_json={"scan_status": ver.scan_status},
            )
            raise PermissionDeniedError(
                "Document version is not cleared (CLEAN) for download.",
                error_code="DOCUMENT_NOT_CLEAN",
            )

        data = self._storage.get_object(ver.object_key)
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if actual_sha256 != ver.checksum_sha256:
            self._audit.record_security_event(
                event_type="DOCUMENT_CHECKSUM_TAMPER_DETECTED",
                outcome=SecurityEventOutcome.BLOCKED.value,
                severity=SecurityEventSeverity.CRITICAL.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                target_resource=f"/api/v1/document-versions/{version_id}/download",
                details_json={"expected": ver.checksum_sha256, "actual": actual_sha256},
            )
            raise AppError(
                "Stored document integrity verification failed.",
                status_code=500,
                error_code="DOCUMENT_INTEGRITY_ERROR",
            )

        with UnitOfWork(db):
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=doc.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="DOCUMENT_VERSION_DOWNLOADED",
                entity_type="document_versions",
                entity_id=ver.id,
            )

        return ver, doc, data

    def request_extraction(
        self,
        db: Session,
        *,
        version_id: uuid.UUID,
        tenant_ctx: TenantContext,
    ) -> ExtractionJob:
        pair = self._repo.get_version_with_document(db, version_id)
        if pair is None:
            raise NotFoundError("Document version not found.")
        ver, doc = pair
        if doc.organization_id != tenant_ctx.organization_id:
            raise NotFoundError("Document version not found.")
        self._assert_site_access(
            db, tenant_ctx, doc, resource=f"/api/v1/document-versions/{version_id}/extract"
        )
        if ver.scan_status != ScanStatus.CLEAN.value:
            raise PermissionDeniedError("Cannot extract non-clean document version.", error_code="DOCUMENT_NOT_CLEAN")

        with UnitOfWork(db):
            job = ExtractionJob(
                document_version_id=ver.id,
                extractor=self._ocr.extractor_name,
                extractor_version=self._ocr.extractor_version,
                status=ExtractionJobStatus.QUEUED.value,
            )
            self._repo.create_extraction_job(db, job)
            self._audit.record(
                db,
                organization_id=doc.organization_id,
                site_id=doc.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="DOCUMENT_EXTRACTION_REQUESTED",
                entity_type="extraction_jobs",
                entity_id=job.id,
                after_state={"document_version_id": str(ver.id)},
            )
            self._outbox.enqueue(
                db,
                organization_id=doc.organization_id,
                aggregate_type="extraction_job",
                aggregate_id=job.id,
                event_type="DocumentExtractionRequested",
                payload_json={
                    "extraction_job_id": str(job.id),
                    "document_version_id": str(ver.id),
                },
            )
        db.refresh(job)
        return job

    def get_extraction_job(
        self,
        db: Session,
        *,
        job_id: uuid.UUID,
        tenant_ctx: TenantContext,
    ) -> Tuple[ExtractionJob, List[ExtractedContent]]:
        row = self._repo.get_extraction_job_for_org(db, tenant_ctx.organization_id, job_id)
        if row is None:
            raise NotFoundError("Extraction job not found.")
        job, ver, doc = row
        self._assert_site_access(db, tenant_ctx, doc, resource=f"/api/v1/extraction-jobs/{job_id}")
        contents = self._repo.list_extracted_contents(db, ver.id)
        return job, contents

    def run_extraction_job(
        self,
        session_factory: sessionmaker[Session],
        event: OutboxEvent,
        _tenant_ctx: Optional[TenantContext],
    ) -> None:
        """
        Outbox external handler (`register_external_handler("DocumentExtractionRequested", ...)`):
        Runs storage read + OCR extraction OUTSIDE any open DB transaction, then commits ExtractedContent
        in a short UnitOfWork.
        """
        payload = event.payload_json or {}
        job_id = uuid.UUID(str(payload["extraction_job_id"]))

        # 1. Short transaction: transition ExtractionJob -> RUNNING and capture snapshot
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            job = uow.session.get(ExtractionJob, job_id)
            if job is None:
                raise NotFoundError(f"ExtractionJob {job_id} not found.")
            ver = uow.session.get(DocumentVersion, job.document_version_id)
            if ver is None:
                raise NotFoundError(f"DocumentVersion {job.document_version_id} not found.")
            doc = uow.session.get(Document, ver.document_id)
            if doc is None:
                raise NotFoundError(f"Document {ver.document_id} not found.")

            job.status = ExtractionJobStatus.RUNNING.value
            job.started_at = datetime.now(timezone.utc)
            object_key = ver.object_key
            mime_type = ver.mime_type
            original_filename = ver.original_filename
            version_id = ver.id
            org_id = doc.organization_id
            site_id = doc.site_id

        # 2. External / CPU OCR I/O strictly OUTSIDE open DB transaction
        try:
            raw_bytes = self._storage.get_object(object_key)
            pages = self._ocr.extract(raw_bytes, mime_type=mime_type, filename=original_filename)
        except Exception as exc:
            with UnitOfWork(session_factory) as uow:
                assert uow.session is not None
                failed_job = uow.session.get(ExtractionJob, job_id)
                if failed_job is not None:
                    failed_job.status = ExtractionJobStatus.FAILED.value
                    failed_job.finished_at = datetime.now(timezone.utc)
                    failed_job.error_code = type(exc).__name__[:80]
            raise

        # 3. Short transaction: persist ExtractedContent and mark COMPLETED
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.execute(
                delete(ExtractedContent).where(ExtractedContent.document_version_id == version_id)
            )
            for page in pages:
                uow.session.add(
                    ExtractedContent(
                        document_version_id=version_id,
                        page_number=page.page_number,
                        section_ref=page.section_ref,
                        text=page.text,
                        structured_data_json=page.structured_data_json,
                        extraction_confidence=page.extraction_confidence,
                    )
                )
            completed_job = uow.session.get(ExtractionJob, job_id)
            if completed_job is not None:
                completed_job.status = ExtractionJobStatus.COMPLETED.value
                completed_job.finished_at = datetime.now(timezone.utc)
                completed_job.error_code = None

            self._audit.record(
                uow.session,
                organization_id=org_id,
                site_id=site_id,
                actor_type=ActorType.SYSTEM_WORKER.value,
                actor_id=None,
                action="DOCUMENT_EXTRACTION_COMPLETED",
                entity_type="extraction_jobs",
                entity_id=job_id,
                after_state={"page_count": len(pages)},
            )
