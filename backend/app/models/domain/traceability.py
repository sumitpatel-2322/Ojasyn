import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.domain.enums import (
    BatchStatus,
    QualityReviewStatus,
    ShipmentStatus,
)
from app.models.domain.base import Base, TenantScopedMixin, TimestampVersionMixin, UUIDPKMixin, utcnow

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Batch(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Canonical manufacturing batch aggregate root scoped to organization, site, and product."""

    __tablename__ = "batches"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_batches_org_id_id"),
        UniqueConstraint(
            "organization_id",
            "site_id",
            "product_id",
            "batch_number",
            name="uq_batches_org_site_product_num",
        ),
        CheckConstraint("expiry_date > manufacturing_date", name="ck_batches_expiry_after_mfg"),
        CheckConstraint("batch_size_value > 0", name="ck_batches_size_pos"),
        CheckConstraint(
            "actual_yield_value IS NULL OR actual_yield_value >= 0",
            name="ck_batches_yield_non_neg",
        ),
        CheckConstraint("distributed_quantity >= 0", name="ck_batches_distributed_non_neg"),
        CheckConstraint(
            "distributed_quantity <= COALESCE(actual_yield_value, batch_size_value)",
            name="ck_batches_distributed_lte_available",
        ),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_batches_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "product_id"],
            ["products.organization_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_batches_org_product",
        ),
        ForeignKeyConstraint(
            ["organization_id", "released_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batches_org_released_by",
        ),
        ForeignKeyConstraint(
            ["organization_id", "primary_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_batches_org_primary_doc",
        ),
        Index("ix_batches_org_status", "organization_id", "batch_status"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    batch_number: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    manufacturing_order_no: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    manufacturing_date: Mapped[date] = mapped_column(Date, nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    batch_size_value: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    batch_size_unit: Mapped[str] = mapped_column(String(30), nullable=False)
    actual_yield_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4), nullable=True)
    distributed_quantity: Mapped[Decimal] = mapped_column(
        Numeric(14, 4), nullable=False, default=Decimal("0.0000"), server_default="0"
    )
    packaging_configuration: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    batch_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default=BatchStatus.PLANNED.value, index=True
    )
    quality_review_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=QualityReviewStatus.PENDING.value, index=True
    )
    hold_or_rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    released_by_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    primary_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    # Nullable UUID without DB FK until Phase 4 creates master_batch_records table
    master_batch_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )

    status_history: Mapped[List["BatchStatusHistory"]] = relationship(
        "BatchStatusHistory",
        back_populates="batch",
        foreign_keys="BatchStatusHistory.batch_id",
        order_by="BatchStatusHistory.occurred_at.asc()",
    )
    dispositions: Mapped[List["BatchDisposition"]] = relationship(
        "BatchDisposition",
        back_populates="batch",
        foreign_keys="BatchDisposition.batch_id",
        order_by="BatchDisposition.decided_at.desc()",
    )
    material_consumptions: Mapped[List["BatchMaterialConsumption"]] = relationship(
        "BatchMaterialConsumption",
        back_populates="batch",
        foreign_keys="BatchMaterialConsumption.batch_id",
        order_by="BatchMaterialConsumption.issued_at.asc()",
    )
    document_links: Mapped[List["BatchDocumentLink"]] = relationship(
        "BatchDocumentLink",
        back_populates="batch",
        foreign_keys="BatchDocumentLink.batch_id",
        order_by="BatchDocumentLink.linked_at.asc()",
    )


class BatchStatusHistory(UUIDPKMixin, TenantScopedMixin, Base):
    """Append-only lifecycle state transition log for a Batch."""

    __tablename__ = "batch_status_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_batch_status_hist_org_batch",
        ),
        ForeignKeyConstraint(
            ["organization_id", "changed_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_status_hist_org_member",
        ),
        Index("ix_batch_status_hist_batch_time", "batch_id", "occurred_at"),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    to_status: Mapped[str] = mapped_column(String(40), nullable=False)
    changed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    changed_by_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    batch: Mapped["Batch"] = relationship(
        "Batch", back_populates="status_history", foreign_keys=[batch_id]
    )


class BatchDisposition(UUIDPKMixin, TenantScopedMixin, Base):
    """Append-only QA batch release/hold/rejection/rework decision ledger with maker-checker enforcement."""

    __tablename__ = "batch_dispositions"
    __table_args__ = (
        CheckConstraint(
            "proposed_by_membership_id != decided_by_membership_id",
            name="ck_batch_dispositions_maker_checker",
        ),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_batch_dispositions_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_batch_dispositions_org_batch",
        ),
        ForeignKeyConstraint(
            ["organization_id", "proposed_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_dispositions_org_proposer",
        ),
        ForeignKeyConstraint(
            ["organization_id", "decided_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_dispositions_org_decider",
        ),
        ForeignKeyConstraint(
            ["organization_id", "certificate_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_batch_dispositions_org_cert_doc",
        ),
        Index("ix_batch_dispositions_batch_time", "batch_id", "decided_at"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[str] = mapped_column(String(40), nullable=False)
    disposition_decision: Mapped[str] = mapped_column(String(30), nullable=False)
    quality_review_status: Mapped[str] = mapped_column(String(30), nullable=False)
    checklist_json: Mapped[Dict[str, Any]] = mapped_column(JSONType, nullable=False)
    hold_or_rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    proposed_by_membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
    )
    decided_by_membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
    )
    approval_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("approval_records.id", ondelete="RESTRICT"),
        nullable=False,
    )
    certificate_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    batch: Mapped["Batch"] = relationship(
        "Batch", back_populates="dispositions", foreign_keys=[batch_id]
    )


class BatchMaterialConsumption(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Links a Batch to an approved Material and released SupplierLot with quantity deduction."""

    __tablename__ = "batch_material_consumptions"
    __table_args__ = (
        CheckConstraint("quantity_consumed > 0", name="ck_batch_mat_cons_qty_pos"),
        CheckConstraint(
            "verified_by_membership_id IS NULL OR verified_by_membership_id != issued_by_membership_id",
            name="ck_batch_mat_cons_maker_checker",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_batch_mat_cons_org_batch",
        ),
        ForeignKeyConstraint(
            ["organization_id", "material_id"],
            ["materials.organization_id", "materials.id"],
            ondelete="RESTRICT",
            name="fk_batch_mat_cons_org_material",
        ),
        ForeignKeyConstraint(
            ["organization_id", "supplier_lot_id"],
            ["supplier_lots.organization_id", "supplier_lots.id"],
            ondelete="RESTRICT",
            name="fk_batch_mat_cons_org_supplier_lot",
        ),
        ForeignKeyConstraint(
            ["organization_id", "issued_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_mat_cons_org_issuer",
        ),
        ForeignKeyConstraint(
            ["organization_id", "verified_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_batch_mat_cons_org_verifier",
        ),
        ForeignKeyConstraint(
            ["organization_id", "evidence_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_batch_mat_cons_org_doc",
        ),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("materials.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    supplier_lot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("supplier_lots.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quantity_consumed: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(30), nullable=False)
    stage_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    issued_by_membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
    )
    verified_by_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    evidence_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )

    batch: Mapped["Batch"] = relationship(
        "Batch", back_populates="material_consumptions", foreign_keys=[batch_id]
    )


class BatchRelationship(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Parent-child genealogy edge between manufacturing batches (split, merge, rework, repack, conversion)."""

    __tablename__ = "batch_relationships"
    __table_args__ = (
        CheckConstraint("parent_batch_id != child_batch_id", name="ck_batch_rel_no_self_loop"),
        CheckConstraint("quantity IS NULL OR quantity > 0", name="ck_batch_rel_qty_pos"),
        UniqueConstraint(
            "organization_id",
            "parent_batch_id",
            "child_batch_id",
            "relation_type",
            name="uq_batch_rel_org_parent_child_type",
        ),
        ForeignKeyConstraint(
            ["organization_id", "parent_batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_batch_rel_org_parent",
        ),
        ForeignKeyConstraint(
            ["organization_id", "child_batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_batch_rel_org_child",
        ),
    )

    parent_batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    child_batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    relation_type: Mapped[str] = mapped_column(String(30), nullable=False)
    quantity: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4), nullable=True)
    unit_code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    parent_batch: Mapped["Batch"] = relationship("Batch", foreign_keys=[parent_batch_id])
    child_batch: Mapped["Batch"] = relationship("Batch", foreign_keys=[child_batch_id])


class BatchDocumentLink(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Links a vault Document (BMR, BPR, COA, QC raw data, disposition cert) to a Batch."""

    __tablename__ = "batch_document_links"
    __table_args__ = (
        UniqueConstraint("batch_id", "document_id", name="uq_batch_doc_links_batch_doc"),
        ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_batch_doc_links_org_batch",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_batch_doc_links_org_doc",
        ),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    document_role: Mapped[str] = mapped_column(String(40), nullable=False)
    linked_by_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    batch: Mapped["Batch"] = relationship(
        "Batch", back_populates="document_links", foreign_keys=[batch_id]
    )


class Shipment(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Forward distribution dispatch record from a manufacturing site to a BusinessPartner site."""

    __tablename__ = "shipments"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_shipments_org_id_id"),
        UniqueConstraint("organization_id", "shipment_number", name="uq_shipments_org_number"),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_shipments_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "partner_id"],
            ["business_partners.organization_id", "business_partners.id"],
            ondelete="RESTRICT",
            name="fk_shipments_org_partner",
        ),
        ForeignKeyConstraint(
            ["organization_id", "partner_site_id"],
            ["partner_sites.organization_id", "partner_sites.id"],
            ondelete="RESTRICT",
            name="fk_shipments_org_partner_site",
        ),
        Index("ix_shipments_org_status", "organization_id", "shipment_status"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    shipment_number: Mapped[str] = mapped_column(String(60), nullable=False)
    partner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("business_partners.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    partner_site_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("partner_sites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    shipment_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=ShipmentStatus.DRAFT.value, index=True
    )
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    carrier_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    tracking_number: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    lines: Mapped[List["ShipmentLine"]] = relationship(
        "ShipmentLine",
        back_populates="shipment",
        foreign_keys="ShipmentLine.shipment_id",
    )


class ShipmentLine(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Line item specifying a released Batch and dispatched/received quantity within a Shipment."""

    __tablename__ = "shipment_lines"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_shipment_lines_org_id_id"),
        CheckConstraint("quantity > 0", name="ck_shipment_lines_qty_pos"),
        CheckConstraint(
            "received_quantity IS NULL OR (received_quantity >= 0 AND received_quantity <= quantity)",
            name="ck_shipment_lines_recv_range",
        ),
        ForeignKeyConstraint(
            ["organization_id", "shipment_id"],
            ["shipments.organization_id", "shipments.id"],
            ondelete="RESTRICT",
            name="fk_shipment_lines_org_shipment",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_shipment_lines_org_batch",
        ),
    )

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("shipments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(30), nullable=False)
    received_quantity: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    shipment: Mapped["Shipment"] = relationship(
        "Shipment", back_populates="lines", foreign_keys=[shipment_id]
    )


class BatchMovementEvent(UUIDPKMixin, TenantScopedMixin, Base):
    """Append-only custody and physical movement ledger for a Batch."""

    __tablename__ = "batch_movement_events"
    __table_args__ = (
        CheckConstraint("quantity IS NULL OR quantity >= 0", name="ck_batch_movement_events_qty_non_neg"),
        ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_batch_movements_org_batch",
        ),
        ForeignKeyConstraint(
            ["organization_id", "shipment_id"],
            ["shipments.organization_id", "shipments.id"],
            ondelete="RESTRICT",
            name="fk_batch_movements_org_shipment",
        ),
        ForeignKeyConstraint(
            ["organization_id", "shipment_line_id"],
            ["shipment_lines.organization_id", "shipment_lines.id"],
            ondelete="RESTRICT",
            name="fk_batch_movements_org_shipment_line",
        ),
        ForeignKeyConstraint(
            ["organization_id", "partner_id"],
            ["business_partners.organization_id", "business_partners.id"],
            ondelete="RESTRICT",
            name="fk_batch_movements_org_partner",
        ),
        ForeignKeyConstraint(
            ["organization_id", "partner_site_id"],
            ["partner_sites.organization_id", "partner_sites.id"],
            ondelete="RESTRICT",
            name="fk_batch_movements_org_partner_site",
        ),
        Index("ix_batch_movement_events_batch_time", "batch_id", "occurred_at"),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    shipment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("shipments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    shipment_line_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("shipment_lines.id", ondelete="RESTRICT"),
        nullable=True,
    )
    partner_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("business_partners.id", ondelete="RESTRICT"),
        nullable=True,
    )
    partner_site_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("partner_sites.id", ondelete="RESTRICT"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    quantity: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4), nullable=True)
    unit_code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
