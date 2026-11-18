import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.domain.complaints import Complaint, ComplaintBatchLink
from app.models.domain.products_materials import SupplierLot
from app.models.domain.traceability import (
    Batch,
    BatchDisposition,
    BatchDocumentLink,
    BatchMaterialConsumption,
    BatchMovementEvent,
    BatchRelationship,
    BatchStatusHistory,
    Shipment,
    ShipmentLine,
)
from app.repositories.base import apply_tenant_filter, paginate


class ProductBatchRepository:
    """Data access for Batches, Dispositions, Material Consumptions, Genealogy, Shipments & Movements."""

    # --- Batch Queries ---

    def get_batch_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        batch_id: uuid.UUID,
        *,
        with_children: bool = False,
        for_update: bool = False,
    ) -> Optional[Batch]:
        stmt = select(Batch).where(
            Batch.organization_id == organization_id,
            Batch.id == batch_id,
        )
        if with_children:
            stmt = stmt.options(
                selectinload(Batch.status_history),
                selectinload(Batch.dispositions),
                selectinload(Batch.material_consumptions),
                selectinload(Batch.document_links),
            )
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def get_supplier_lot_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        supplier_lot_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> Optional[SupplierLot]:
        stmt = select(SupplierLot).where(
            SupplierLot.organization_id == organization_id,
            SupplierLot.id == supplier_lot_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def get_batch_by_composite_key(
        self,
        db: Session,
        organization_id: uuid.UUID,
        site_id: uuid.UUID,
        product_id: uuid.UUID,
        batch_number: str,
    ) -> Optional[Batch]:
        return db.scalar(
            select(Batch).where(
                Batch.organization_id == organization_id,
                Batch.site_id == site_id,
                Batch.product_id == product_id,
                func.lower(Batch.batch_number) == batch_number.strip().lower(),
            )
        )

    def list_batches_for_tenant(
        self,
        db: Session,
        tenant_ctx: object,
        *,
        site_id: Optional[uuid.UUID] = None,
        product_id: Optional[uuid.UUID] = None,
        batch_status: Optional[str] = None,
        quality_review_status: Optional[str] = None,
        batch_number: Optional[str] = None,
        q: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Batch], int]:
        stmt = select(Batch)
        stmt = apply_tenant_filter(stmt, Batch, tenant_ctx, allow_org_wide_site_null=False)
        if site_id is not None:
            stmt = stmt.where(Batch.site_id == site_id)
        if product_id is not None:
            stmt = stmt.where(Batch.product_id == product_id)
        if batch_status:
            stmt = stmt.where(Batch.batch_status == batch_status)
        if quality_review_status:
            stmt = stmt.where(Batch.quality_review_status == quality_review_status)
        if batch_number:
            stmt = stmt.where(func.lower(Batch.batch_number) == batch_number.strip().lower())
        if q:
            like_pattern = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(
                    Batch.batch_number.ilike(like_pattern),
                    Batch.manufacturing_order_no.ilike(like_pattern),
                )
            )
        stmt = stmt.order_by(Batch.created_at.desc())
        return paginate(db, stmt, page=page, page_size=page_size)

    def get_consumption_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        batch_id: uuid.UUID,
        consumption_id: uuid.UUID,
    ) -> Optional[BatchMaterialConsumption]:
        return db.scalar(
            select(BatchMaterialConsumption).where(
                BatchMaterialConsumption.organization_id == organization_id,
                BatchMaterialConsumption.batch_id == batch_id,
                BatchMaterialConsumption.id == consumption_id,
            )
        )

    def list_consumptions_for_batch(
        self, db: Session, organization_id: uuid.UUID, batch_id: uuid.UUID
    ) -> List[BatchMaterialConsumption]:
        return list(
            db.scalars(
                select(BatchMaterialConsumption)
                .where(
                    BatchMaterialConsumption.organization_id == organization_id,
                    BatchMaterialConsumption.batch_id == batch_id,
                )
                .order_by(BatchMaterialConsumption.issued_at.asc())
            ).all()
        )

    def list_relationships_for_batch(
        self, db: Session, organization_id: uuid.UUID, batch_id: uuid.UUID
    ) -> Tuple[List[BatchRelationship], List[BatchRelationship]]:
        parent_rels = list(
            db.scalars(
                select(BatchRelationship)
                .where(
                    BatchRelationship.organization_id == organization_id,
                    BatchRelationship.child_batch_id == batch_id,
                )
                .order_by(BatchRelationship.event_at.asc())
            ).all()
        )
        child_rels = list(
            db.scalars(
                select(BatchRelationship)
                .where(
                    BatchRelationship.organization_id == organization_id,
                    BatchRelationship.parent_batch_id == batch_id,
                )
                .order_by(BatchRelationship.event_at.asc())
            ).all()
        )
        return parent_rels, child_rels

    def list_all_relationships_for_org(
        self, db: Session, organization_id: uuid.UUID
    ) -> List[BatchRelationship]:
        return list(
            db.scalars(
                select(BatchRelationship)
                .where(BatchRelationship.organization_id == organization_id)
                .order_by(BatchRelationship.event_at.asc())
            ).all()
        )

    def get_relationship_duplicate(
        self,
        db: Session,
        organization_id: uuid.UUID,
        parent_batch_id: uuid.UUID,
        child_batch_id: uuid.UUID,
        relation_type: str,
    ) -> Optional[BatchRelationship]:
        return db.scalar(
            select(BatchRelationship).where(
                BatchRelationship.organization_id == organization_id,
                BatchRelationship.parent_batch_id == parent_batch_id,
                BatchRelationship.child_batch_id == child_batch_id,
                BatchRelationship.relation_type == relation_type,
            )
        )

    def get_batch_document_link(
        self,
        db: Session,
        organization_id: uuid.UUID,
        batch_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> Optional[BatchDocumentLink]:
        return db.scalar(
            select(BatchDocumentLink).where(
                BatchDocumentLink.organization_id == organization_id,
                BatchDocumentLink.batch_id == batch_id,
                BatchDocumentLink.document_id == document_id,
            )
        )

    def list_status_history(
        self, db: Session, organization_id: uuid.UUID, batch_id: uuid.UUID
    ) -> List[BatchStatusHistory]:
        return list(
            db.scalars(
                select(BatchStatusHistory)
                .where(
                    BatchStatusHistory.organization_id == organization_id,
                    BatchStatusHistory.batch_id == batch_id,
                )
                .order_by(BatchStatusHistory.occurred_at.asc())
            ).all()
        )

    def list_dispositions(
        self, db: Session, organization_id: uuid.UUID, batch_id: uuid.UUID
    ) -> List[BatchDisposition]:
        return list(
            db.scalars(
                select(BatchDisposition)
                .where(
                    BatchDisposition.organization_id == organization_id,
                    BatchDisposition.batch_id == batch_id,
                )
                .order_by(BatchDisposition.decided_at.desc())
            ).all()
        )

    # --- Shipment & Movement Queries ---

    def get_shipment_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        shipment_id: uuid.UUID,
        *,
        with_lines: bool = True,
    ) -> Optional[Shipment]:
        stmt = select(Shipment).where(
            Shipment.organization_id == organization_id,
            Shipment.id == shipment_id,
        )
        if with_lines:
            stmt = stmt.options(selectinload(Shipment.lines))
        return db.scalar(stmt)

    def get_shipment_by_number(
        self, db: Session, organization_id: uuid.UUID, shipment_number: str
    ) -> Optional[Shipment]:
        return db.scalar(
            select(Shipment).where(
                Shipment.organization_id == organization_id,
                func.lower(Shipment.shipment_number) == shipment_number.strip().lower(),
            )
        )

    def list_shipments_for_tenant(
        self,
        db: Session,
        tenant_ctx: object,
        *,
        site_id: Optional[uuid.UUID] = None,
        partner_id: Optional[uuid.UUID] = None,
        shipment_status: Optional[str] = None,
        shipment_number: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Shipment], int]:
        stmt = select(Shipment)
        stmt = apply_tenant_filter(stmt, Shipment, tenant_ctx, allow_org_wide_site_null=False)
        if site_id is not None:
            stmt = stmt.where(Shipment.site_id == site_id)
        if partner_id is not None:
            stmt = stmt.where(Shipment.partner_id == partner_id)
        if shipment_status:
            stmt = stmt.where(Shipment.shipment_status == shipment_status)
        if shipment_number:
            stmt = stmt.where(func.lower(Shipment.shipment_number) == shipment_number.strip().lower())
        stmt = stmt.order_by(Shipment.created_at.desc())
        return paginate(db, stmt, page=page, page_size=page_size)

    def list_movement_events_for_batch(
        self, db: Session, organization_id: uuid.UUID, batch_id: uuid.UUID
    ) -> List[BatchMovementEvent]:
        return list(
            db.scalars(
                select(BatchMovementEvent)
                .where(
                    BatchMovementEvent.organization_id == organization_id,
                    BatchMovementEvent.batch_id == batch_id,
                )
                .order_by(BatchMovementEvent.occurred_at.asc())
            ).all()
        )

    def list_shipment_lines_for_batch(
        self, db: Session, organization_id: uuid.UUID, batch_id: uuid.UUID
    ) -> List[Tuple[ShipmentLine, Shipment]]:
        rows = db.execute(
            select(ShipmentLine, Shipment)
            .join(Shipment, Shipment.id == ShipmentLine.shipment_id)
            .where(
                ShipmentLine.organization_id == organization_id,
                ShipmentLine.batch_id == batch_id,
            )
            .order_by(Shipment.created_at.asc())
        ).all()
        return [(line, shipment) for line, shipment in rows]

    def list_complaint_links_for_batch(
        self, db: Session, organization_id: uuid.UUID, batch_id: uuid.UUID
    ) -> List[Tuple[ComplaintBatchLink, Complaint]]:
        rows = db.execute(
            select(ComplaintBatchLink, Complaint)
            .join(Complaint, Complaint.id == ComplaintBatchLink.complaint_id)
            .where(
                ComplaintBatchLink.organization_id == organization_id,
                ComplaintBatchLink.batch_id == batch_id,
            )
            .order_by(Complaint.created_at.desc())
        ).all()
        return [(link, complaint) for link, complaint in rows]
