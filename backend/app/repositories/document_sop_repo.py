import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import TenantContext
from app.models.domain.documents import Document, DocumentVersion, ExtractedContent, ExtractionJob
from app.repositories.base import apply_tenant_filter, paginate


class DocumentSOPRepository:
    """Data access for Document Vault records, Versions, ExtractionJobs, and ExtractedContent."""

    def create_document(self, db: Session, doc: Document) -> Document:
        db.add(doc)
        db.flush()
        return doc

    def create_version(self, db: Session, version: DocumentVersion) -> DocumentVersion:
        db.add(version)
        db.flush()
        return version

    def get_document_by_id(self, db: Session, document_id: uuid.UUID) -> Optional[Document]:
        return db.get(Document, document_id)

    def get_document_for_org(
        self, db: Session, organization_id: uuid.UUID, document_id: uuid.UUID
    ) -> Optional[Document]:
        return db.scalar(
            select(Document).where(
                Document.organization_id == organization_id,
                Document.id == document_id,
            )
        )

    def get_document_by_code(
        self, db: Session, organization_id: Optional[uuid.UUID], document_code: str
    ) -> Optional[Document]:
        stmt = select(Document).where(
            func.lower(Document.document_code) == document_code.strip().lower()
        )
        if organization_id is None:
            stmt = stmt.where(Document.organization_id.is_(None))
        else:
            stmt = stmt.where(Document.organization_id == organization_id)
        return db.scalar(stmt)

    def list_documents_for_tenant(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        *,
        document_type: Optional[str] = None,
        status: Optional[str] = None,
        site_id: Optional[uuid.UUID] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Document], int]:
        stmt = select(Document)
        stmt = apply_tenant_filter(stmt, Document, tenant_ctx, allow_org_wide_site_null=True)
        if document_type:
            stmt = stmt.where(Document.document_type == document_type.strip().upper())
        if status:
            stmt = stmt.where(Document.status == status.strip().upper())
        if site_id is not None:
            stmt = stmt.where(Document.site_id == site_id)
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    Document.title.ilike(like),
                    Document.document_code.ilike(like),
                )
            )
        stmt = stmt.order_by(Document.created_at.desc())
        return paginate(db, stmt, page=page, page_size=page_size)

    def list_versions_for_document(self, db: Session, document_id: uuid.UUID) -> List[DocumentVersion]:
        stmt = (
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number.asc())
        )
        return list(db.scalars(stmt).all())

    def get_latest_version(self, db: Session, document_id: uuid.UUID) -> Optional[DocumentVersion]:
        stmt = (
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number.desc())
            .limit(1)
        )
        return db.scalar(stmt)

    def get_version_with_document(
        self, db: Session, version_id: uuid.UUID
    ) -> Optional[Tuple[DocumentVersion, Document]]:
        ver = db.get(DocumentVersion, version_id)
        if ver is None:
            return None
        doc = db.get(Document, ver.document_id)
        if doc is None:
            return None
        return ver, doc

    def create_extraction_job(self, db: Session, job: ExtractionJob) -> ExtractionJob:
        db.add(job)
        db.flush()
        return job

    def get_extraction_job_for_org(
        self, db: Session, organization_id: uuid.UUID, job_id: uuid.UUID
    ) -> Optional[Tuple[ExtractionJob, DocumentVersion, Document]]:
        row = db.execute(
            select(ExtractionJob, DocumentVersion, Document)
            .join(DocumentVersion, DocumentVersion.id == ExtractionJob.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(ExtractionJob.id == job_id, Document.organization_id == organization_id)
        ).first()
        if row is None:
            return None
        return row[0], row[1], row[2]

    def list_extracted_contents(
        self, db: Session, document_version_id: uuid.UUID
    ) -> List[ExtractedContent]:
        stmt = (
            select(ExtractedContent)
            .where(ExtractedContent.document_version_id == document_version_id)
            .order_by(ExtractedContent.page_number.asc())
        )
        return list(db.scalars(stmt).all())
