import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.models.domain.base import Base, TimestampVersionMixin, UUIDPKMixin, utcnow

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Document(UUIDPKMixin, TimestampVersionMixin, Base):
    """
    Central regulated document vault metadata record.
    `organization_id` is nullable ONLY for pre-routing UNROUTED_TRIAGE public complaint attachments
    and is backfilled atomically when routed to a tenant organization.
    """

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_documents_org_id_id"),
        UniqueConstraint(
            "organization_id",
            "document_code",
            name="uq_documents_org_code",
            postgresql_nulls_not_distinct=True,
        ),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_documents_org_site",
        ),
    )

    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    site_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    document_code: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    document_type: Mapped[str] = mapped_column(String(40), nullable=False, default="OTHER", index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    confidentiality: Mapped[str] = mapped_column(String(30), nullable=False, default="INTERNAL")
    owner_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ACTIVE", index=True)
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)

    versions: Mapped[List["DocumentVersion"]] = relationship(
        "DocumentVersion",
        back_populates="document",
        order_by="DocumentVersion.version_number.asc()",
    )


class DocumentVersion(UUIDPKMixin, TimestampVersionMixin, Base):
    """Immutable binary object reference, SHA-256 integrity hash, scan state, and approval status."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_doc_versions_doc_num"),
        CheckConstraint("version_number >= 1", name="ck_doc_versions_num_pos"),
        CheckConstraint("byte_size > 0", name="ck_doc_versions_byte_size_pos"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version_label: Mapped[str] = mapped_column(String(40), nullable=False, default="1.0")
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scan_status: Mapped[str] = mapped_column(String(30), nullable=False, default="CLEAN", index=True)
    approval_status: Mapped[str] = mapped_column(String(30), nullable=False, default="DRAFT", index=True)
    uploaded_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    supersedes_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )

    document: Mapped["Document"] = relationship("Document", back_populates="versions")
    extraction_jobs: Mapped[List["ExtractionJob"]] = relationship(
        "ExtractionJob",
        back_populates="document_version",
    )
    extracted_contents: Mapped[List["ExtractedContent"]] = relationship(
        "ExtractedContent",
        back_populates="document_version",
        order_by="ExtractedContent.page_number.asc()",
    )


class ExtractionJob(UUIDPKMixin, Base):
    """Asynchronous OCR / text extraction job tracking for a DocumentVersion."""

    __tablename__ = "extraction_jobs"

    document_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    extractor: Mapped[str] = mapped_column(String(60), nullable=False, default="pdfplumber")
    extractor_version: Mapped[str] = mapped_column(String(40), nullable=False, default="1.0")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="QUEUED", index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    document_version: Mapped["DocumentVersion"] = relationship(
        "DocumentVersion", back_populates="extraction_jobs"
    )


class ExtractedContent(UUIDPKMixin, Base):
    """Page-numbered OCR/text extraction output (PG GIN FTS index added in Alembic 0003)."""

    __tablename__ = "extracted_contents"
    __table_args__ = (
        CheckConstraint("page_number >= 1", name="ck_extracted_contents_page_pos"),
    )

    document_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    section_ref: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    structured_data_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    extraction_confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    document_version: Mapped["DocumentVersion"] = relationship(
        "DocumentVersion", back_populates="extracted_contents"
    )
