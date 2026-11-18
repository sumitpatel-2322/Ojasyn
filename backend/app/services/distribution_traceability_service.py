import re
import uuid
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import UnitOfWork, next_tenant_sequence, verify_and_increment_version
from app.core.deps import TenantContext
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.domain.enums import (
    ActorType,
    BatchDocumentRole,
    BatchMovementEventType,
    BatchStatus,
    SecurityEventOutcome,
    SecurityEventSeverity,
    ShipmentStatus,
)
from app.domain.state_machines import assert_transition
from app.models.domain.actors import BusinessPartner, PartnerSite
from app.models.domain.products_materials import Material, Product, SupplierLot
from app.models.domain.tenancy import Organization
from app.models.domain.traceability import (
    Batch,
    BatchMovementEvent,
    BatchRelationship,
    Shipment,
    ShipmentLine,
)
from app.models.schemas.traceability import (
    BatchDispositionResponse,
    BatchDocumentLinkResponse,
    BatchGenealogyEdge,
    BatchMovementEventResponse,
    BatchTraceabilityResponse,
    ShipmentCancelRequest,
    ShipmentCreateRequest,
    ShipmentDispatchRequest,
    ShipmentLineCreateRequest,
    ShipmentReceiptRequest,
    TraceabilityBatchSummary,
    TraceabilityComplaintItem,
    TraceabilityMaterialItem,
    TraceabilityShipmentItem,
)
from app.repositories.governance_repo import OutboxRepository
from app.repositories.organization_repo import OrganizationRepository
from app.repositories.partner_repo import PartnerRepository
from app.repositories.product_batch_repo import ProductBatchRepository
from app.services.audit_service import AuditService

SHIPMENT_TRANSITIONS: Dict[str, Set[str]] = {
    ShipmentStatus.DRAFT.value: {
        ShipmentStatus.DISPATCHED.value,
        ShipmentStatus.CANCELLED.value,
    },
    ShipmentStatus.DISPATCHED.value: {
        ShipmentStatus.IN_TRANSIT.value,
        ShipmentStatus.PARTIALLY_RECEIVED.value,
        ShipmentStatus.RECEIVED.value,
    },
    ShipmentStatus.IN_TRANSIT.value: {
        ShipmentStatus.PARTIALLY_RECEIVED.value,
        ShipmentStatus.RECEIVED.value,
    },
    ShipmentStatus.PARTIALLY_RECEIVED.value: {
        ShipmentStatus.PARTIALLY_RECEIVED.value,
        ShipmentStatus.RECEIVED.value,
    },
    ShipmentStatus.RECEIVED.value: set(),
    ShipmentStatus.CANCELLED.value: set(),
}


def _org_shipment_prefix(org: Optional[Organization]) -> str:
    if org is None:
        return "ORG-SHP"
    raw = (org.trade_name or org.legal_name or "ORG").upper()
    code = re.sub(r"[^A-Za-z0-9]", "", raw)[:4] or "ORG"
    return f"{code}-SHP"


def _batch_capacity(batch: Batch) -> Decimal:
    return batch.actual_yield_value if batch.actual_yield_value is not None else batch.batch_size_value


def _available_quantity(batch: Batch) -> Decimal:
    return max(Decimal("0"), _batch_capacity(batch) - batch.distributed_quantity)


class DistributionTraceabilityService:
    """Tenant-scoped distribution shipments (C7), movement ledger & end-to-end batch traceability."""

    def __init__(
        self,
        repo: Optional[ProductBatchRepository] = None,
        org_repo: Optional[OrganizationRepository] = None,
        partner_repo: Optional[PartnerRepository] = None,
        audit_service: Optional[AuditService] = None,
        outbox_repo: Optional[OutboxRepository] = None,
    ) -> None:
        self._repo = repo or ProductBatchRepository()
        self._org_repo = org_repo or OrganizationRepository()
        self._partner_repo = partner_repo or PartnerRepository()
        self._audit = audit_service or AuditService()
        self._outbox = outbox_repo or OutboxRepository()

    def _assert_site_access(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        site_id: uuid.UUID,
        *,
        source_ip: Optional[str] = None,
        target_resource: str = "/api/v1/distribution/shipments",
    ) -> None:
        site = self._org_repo.get_site_for_org(db, tenant_ctx.organization_id, site_id)
        if site is None:
            raise NotFoundError("Site not found in this organization.")
        if not tenant_ctx.is_org_wide and site.id not in tenant_ctx.allowed_site_ids:
            self._audit.record_security_event(
                event_type="SITE_SCOPE_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.WARNING.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=source_ip,
                target_resource=target_resource,
                details_json={"site_id": str(site_id)},
            )
            raise PermissionDeniedError(
                "Not authorized to access or manage records for this site.",
                error_code="SITE_SCOPE_DENIED",
            )

    def _validate_allocatable_lines(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        shipment_site_id: uuid.UUID,
        lines_qty_unit: List[Tuple[uuid.UUID, Decimal, str]],
        for_update: bool = False,
        source_ip: Optional[str] = None,
        target_resource: str = "/api/v1/distribution/shipments",
    ) -> Dict[uuid.UUID, Batch]:
        batch_cache: Dict[uuid.UUID, Batch] = {}
        cumulative_by_batch: Dict[uuid.UUID, Decimal] = {}

        # Lock distinct batch rows in deterministic UUID order when for_update=True to prevent deadlocks
        if for_update:
            for bid in sorted({b for b, _, _ in lines_qty_unit}):
                locked = self._repo.get_batch_for_org(
                    db, tenant_ctx.organization_id, bid, for_update=True
                )
                if locked is None:
                    raise NotFoundError(f"Batch {bid} not found in this organization.")
                batch_cache[bid] = locked

        for batch_id, qty, unit_code in lines_qty_unit:
            batch = batch_cache.get(batch_id)
            if batch is None:
                batch = self._repo.get_batch_for_org(
                    db, tenant_ctx.organization_id, batch_id, for_update=for_update
                )
                if batch is None:
                    raise NotFoundError(f"Batch {batch_id} not found in this organization.")
                batch_cache[batch_id] = batch

            if not tenant_ctx.is_org_wide and batch.site_id not in tenant_ctx.allowed_site_ids:
                self._audit.record_security_event(
                    event_type="SITE_SCOPE_VIOLATION",
                    outcome=SecurityEventOutcome.DENIED.value,
                    severity=SecurityEventSeverity.WARNING.value,
                    organization_id=tenant_ctx.organization_id,
                    user_id=tenant_ctx.user_id,
                    source_ip=source_ip,
                    target_resource=target_resource,
                    details_json={"batch_id": str(batch.id), "batch_site_id": str(batch.site_id)},
                )
                raise PermissionDeniedError(
                    "Not authorized to allocate or dispatch batches from this site.",
                    error_code="SITE_SCOPE_DENIED",
                )

            if batch.site_id != shipment_site_id:
                raise ValidationError(
                    f"Batch {batch.batch_number} belongs to a different site and cannot be allocated on a shipment for site {shipment_site_id}.",
                    error_code="CROSS_SITE_BATCH_ALLOCATION",
                )

            if batch.batch_status != BatchStatus.RELEASED.value:
                raise ConflictError(
                    f"Only RELEASED batches can be allocated or dispatched (batch {batch.batch_number} is '{batch.batch_status}').",
                    error_code="BATCH_NOT_RELEASED",
                )
            if unit_code.strip() != batch.batch_size_unit:
                raise ValidationError(
                    f"Unit mismatch for batch {batch.batch_number}: expected '{batch.batch_size_unit}', got '{unit_code}'."
                )

            new_total = cumulative_by_batch.get(batch_id, Decimal("0")) + qty
            available = _available_quantity(batch)
            if new_total > available:
                raise ValidationError(
                    f"Requested quantity ({new_total}) for batch {batch.batch_number} exceeds available quantity ({available}).",
                    error_code="INSUFFICIENT_BATCH_QUANTITY",
                )
            cumulative_by_batch[batch_id] = new_total

        return batch_cache

    # --- Shipments CRUD & Lifecycle ---

    def create_shipment(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        payload: ShipmentCreateRequest,
        source_ip: Optional[str] = None,
    ) -> Shipment:
        self._assert_site_access(
            db,
            tenant_ctx,
            payload.site_id,
            source_ip=source_ip,
            target_resource="/api/v1/distribution/shipments",
        )

        partner = self._partner_repo.get_partner_for_org(
            db, tenant_ctx.organization_id, payload.partner_id
        )
        if partner is None:
            raise NotFoundError("Business partner not found in this organization.")
        if partner.status != "ACTIVE":
            raise ConflictError("Business partner is not ACTIVE.")

        partner_site = self._partner_repo.get_partner_site_for_org(
            db, tenant_ctx.organization_id, partner.id, payload.partner_site_id
        )
        if partner_site is None:
            raise NotFoundError("Partner site not found for the specified partner in this organization.")
        if partner_site.status != "ACTIVE":
            raise ConflictError("Partner site is not ACTIVE.")

        if payload.lines:
            self._validate_allocatable_lines(
                db,
                tenant_ctx=tenant_ctx,
                shipment_site_id=payload.site_id,
                lines_qty_unit=[(ln.batch_id, ln.quantity, ln.unit_code) for ln in payload.lines],
                source_ip=source_ip,
                target_resource="/api/v1/distribution/shipments",
            )

        now = datetime.now(timezone.utc)
        org = db.get(Organization, tenant_ctx.organization_id)
        if payload.shipment_number and payload.shipment_number.strip():
            ship_num = payload.shipment_number.strip()
            if self._repo.get_shipment_by_number(db, tenant_ctx.organization_id, ship_num) is not None:
                raise ConflictError(
                    f"Shipment number '{ship_num}' already exists.",
                    error_code="DUPLICATE_SHIPMENT_NUMBER",
                )
        else:
            ship_num = str(
                next_tenant_sequence(
                    db,
                    tenant_ctx.organization_id,
                    "SHIPMENT",
                    period_year=now.year,
                    prefix=_org_shipment_prefix(org),
                )
            )

        with UnitOfWork(db):
            shipment = Shipment(
                organization_id=tenant_ctx.organization_id,
                site_id=payload.site_id,
                shipment_number=ship_num,
                partner_id=partner.id,
                partner_site_id=partner_site.id,
                shipment_status=ShipmentStatus.DRAFT.value,
                carrier_name=payload.carrier_name,
                tracking_number=payload.tracking_number,
                notes=payload.notes,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(shipment)
            db.flush()

            for ln in payload.lines:
                db.add(
                    ShipmentLine(
                        organization_id=tenant_ctx.organization_id,
                        shipment_id=shipment.id,
                        batch_id=ln.batch_id,
                        quantity=ln.quantity,
                        unit_code=ln.unit_code.strip(),
                        notes=ln.notes,
                        created_by_user_id=tenant_ctx.user_id,
                        updated_by_user_id=tenant_ctx.user_id,
                    )
                )

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=shipment.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="SHIPMENT_CREATED",
                entity_type="shipments",
                entity_id=shipment.id,
                source_ip=source_ip,
                after_state={
                    "shipment_number": shipment.shipment_number,
                    "partner_id": str(shipment.partner_id),
                    "line_count": len(payload.lines),
                },
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="shipment",
                aggregate_id=shipment.id,
                event_type="ShipmentCreated",
                payload_json={
                    "shipment_id": str(shipment.id),
                    "shipment_number": shipment.shipment_number,
                    "partner_id": str(shipment.partner_id),
                },
            )
        db.refresh(shipment)
        return self._repo.get_shipment_for_org(
            db, tenant_ctx.organization_id, shipment.id, with_lines=True
        ) or shipment

    def list_shipments(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        site_id: Optional[uuid.UUID] = None,
        partner_id: Optional[uuid.UUID] = None,
        shipment_status: Optional[str] = None,
        shipment_number: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Shipment], int]:
        return self._repo.list_shipments_for_tenant(
            db,
            tenant_ctx,
            site_id=site_id,
            partner_id=partner_id,
            shipment_status=shipment_status,
            shipment_number=shipment_number,
            page=page,
            page_size=page_size,
        )

    def get_shipment(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        shipment_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> Shipment:
        shipment = self._repo.get_shipment_for_org(
            db, tenant_ctx.organization_id, shipment_id, with_lines=True
        )
        if shipment is None:
            raise NotFoundError("Shipment not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            shipment.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/distribution/shipments/{shipment_id}",
        )
        return shipment

    def add_shipment_line(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        shipment_id: uuid.UUID,
        payload: ShipmentLineCreateRequest,
        source_ip: Optional[str] = None,
    ) -> ShipmentLine:
        shipment = self.get_shipment(
            db, tenant_ctx=tenant_ctx, shipment_id=shipment_id, source_ip=source_ip
        )
        if shipment.shipment_status != ShipmentStatus.DRAFT.value:
            raise ConflictError("Lines can only be added while shipment is in DRAFT status.")

        combined_lines = [(ln.batch_id, ln.quantity, ln.unit_code) for ln in shipment.lines]
        combined_lines.append((payload.batch_id, payload.quantity, payload.unit_code))
        self._validate_allocatable_lines(
            db,
            tenant_ctx=tenant_ctx,
            shipment_site_id=shipment.site_id,
            lines_qty_unit=combined_lines,
            source_ip=source_ip,
            target_resource=f"/api/v1/distribution/shipments/{shipment_id}/lines",
        )

        with UnitOfWork(db):
            line = ShipmentLine(
                organization_id=tenant_ctx.organization_id,
                shipment_id=shipment.id,
                batch_id=payload.batch_id,
                quantity=payload.quantity,
                unit_code=payload.unit_code.strip(),
                notes=payload.notes,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(line)
            db.flush()
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=shipment.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="SHIPMENT_LINE_ADDED",
                entity_type="shipment_lines",
                entity_id=line.id,
                source_ip=source_ip,
                after_state={
                    "shipment_id": str(shipment.id),
                    "batch_id": str(line.batch_id),
                    "quantity": str(line.quantity),
                },
            )
        db.refresh(line)
        return line

    def dispatch_shipment(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        shipment_id: uuid.UUID,
        payload: ShipmentDispatchRequest,
        source_ip: Optional[str] = None,
    ) -> Shipment:
        shipment = self.get_shipment(
            db, tenant_ctx=tenant_ctx, shipment_id=shipment_id, source_ip=source_ip
        )
        assert_transition(
            SHIPMENT_TRANSITIONS,
            shipment.shipment_status,
            ShipmentStatus.DISPATCHED.value,
            entity_name="Shipment",
        )
        if not shipment.lines:
            raise ValidationError("Shipment must contain at least one line item before dispatch.")

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            verify_and_increment_version(shipment, payload.row_version)
            batch_map = self._validate_allocatable_lines(
                db,
                tenant_ctx=tenant_ctx,
                shipment_site_id=shipment.site_id,
                lines_qty_unit=[(ln.batch_id, ln.quantity, ln.unit_code) for ln in shipment.lines],
                for_update=True,
                source_ip=source_ip,
                target_resource=f"/api/v1/distribution/shipments/{shipment_id}/dispatch",
            )
            shipment.shipment_status = ShipmentStatus.DISPATCHED.value
            shipment.dispatched_at = now
            if payload.carrier_name is not None:
                shipment.carrier_name = payload.carrier_name
            if payload.tracking_number is not None:
                shipment.tracking_number = payload.tracking_number
            if payload.notes is not None:
                shipment.notes = payload.notes
            shipment.updated_by_user_id = tenant_ctx.user_id

            for line in shipment.lines:
                batch = batch_map[line.batch_id]
                batch.distributed_quantity = batch.distributed_quantity + line.quantity
                batch.updated_by_user_id = tenant_ctx.user_id
                db.add(
                    BatchMovementEvent(
                        organization_id=tenant_ctx.organization_id,
                        batch_id=batch.id,
                        shipment_id=shipment.id,
                        shipment_line_id=line.id,
                        partner_id=shipment.partner_id,
                        partner_site_id=shipment.partner_site_id,
                        event_type=BatchMovementEventType.DISPATCH.value,
                        quantity=line.quantity,
                        unit_code=line.unit_code,
                        actor_user_id=tenant_ctx.user_id,
                        occurred_at=now,
                        notes=payload.notes or line.notes or f"Dispatched on {shipment.shipment_number}.",
                    )
                )

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=shipment.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="SHIPMENT_DISPATCHED",
                entity_type="shipments",
                entity_id=shipment.id,
                reason=payload.notes,
                source_ip=source_ip,
                before_state={"shipment_status": ShipmentStatus.DRAFT.value},
                after_state={
                    "shipment_status": shipment.shipment_status,
                    "dispatched_at": now.isoformat(),
                    "line_count": len(shipment.lines),
                },
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="shipment",
                aggregate_id=shipment.id,
                event_type="ShipmentDispatched",
                payload_json={
                    "shipment_id": str(shipment.id),
                    "shipment_number": shipment.shipment_number,
                    "partner_id": str(shipment.partner_id),
                    "batch_ids": [str(ln.batch_id) for ln in shipment.lines],
                },
            )
        return self.get_shipment(
            db, tenant_ctx=tenant_ctx, shipment_id=shipment.id, source_ip=source_ip
        )

    def confirm_receipt(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        shipment_id: uuid.UUID,
        payload: ShipmentReceiptRequest,
        source_ip: Optional[str] = None,
    ) -> Shipment:
        shipment = self.get_shipment(
            db, tenant_ctx=tenant_ctx, shipment_id=shipment_id, source_ip=source_ip
        )
        assert_transition(
            SHIPMENT_TRANSITIONS,
            shipment.shipment_status,
            ShipmentStatus.RECEIVED.value,
            entity_name="Shipment",
        )

        line_by_id: Dict[uuid.UUID, ShipmentLine] = {ln.id: ln for ln in shipment.lines}
        if not line_by_id:
            raise ConflictError("Shipment has no lines to confirm receipt against.")

        updates_by_line: Dict[uuid.UUID, Tuple[Decimal, Optional[str]]] = {}
        if payload.received_all and not payload.line_receipts:
            for ln in shipment.lines:
                updates_by_line[ln.id] = (ln.quantity, payload.notes)
        else:
            if not payload.line_receipts:
                raise ValidationError(
                    "Provide line_receipts or set received_all=true to confirm shipment receipt."
                )
            for item in payload.line_receipts:
                ln = line_by_id.get(item.shipment_line_id)
                if ln is None:
                    raise NotFoundError(
                        f"Shipment line {item.shipment_line_id} not found on this shipment."
                    )
                if item.received_quantity > ln.quantity:
                    raise ValidationError(
                        f"received_quantity ({item.received_quantity}) cannot exceed dispatched quantity ({ln.quantity})."
                    )
                prev_recv = ln.received_quantity or Decimal("0")
                if item.received_quantity < prev_recv:
                    raise ValidationError(
                        f"received_quantity ({item.received_quantity}) cannot be less than previously recorded receipt ({prev_recv})."
                    )
                updates_by_line[ln.id] = (item.received_quantity, item.notes)

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            verify_and_increment_version(shipment, payload.row_version)
            prev_status = shipment.shipment_status

            for ln_id, (new_total_qty, ln_notes) in updates_by_line.items():
                ln = line_by_id[ln_id]
                prev_qty = ln.received_quantity or Decimal("0")
                delta = new_total_qty - prev_qty
                ln.received_quantity = new_total_qty
                if ln_notes is not None:
                    ln.notes = ln_notes
                ln.updated_by_user_id = tenant_ctx.user_id

                if delta > Decimal("0"):
                    db.add(
                        BatchMovementEvent(
                            organization_id=tenant_ctx.organization_id,
                            batch_id=ln.batch_id,
                            shipment_id=shipment.id,
                            shipment_line_id=ln.id,
                            partner_id=shipment.partner_id,
                            partner_site_id=shipment.partner_site_id,
                            event_type=BatchMovementEventType.RECEIPT.value,
                            quantity=delta,
                            unit_code=ln.unit_code,
                            actor_user_id=tenant_ctx.user_id,
                            occurred_at=now,
                            notes=ln_notes or payload.notes or f"Receipt recorded on {shipment.shipment_number}.",
                        )
                    )

            all_full = all(
                ln.received_quantity is not None and ln.received_quantity == ln.quantity
                for ln in shipment.lines
            )
            shipment.shipment_status = (
                ShipmentStatus.RECEIVED.value
                if all_full
                else ShipmentStatus.PARTIALLY_RECEIVED.value
            )
            shipment.received_at = now
            shipment.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=shipment.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="SHIPMENT_RECEIPT_CONFIRMED",
                entity_type="shipments",
                entity_id=shipment.id,
                reason=payload.notes,
                source_ip=source_ip,
                before_state={"shipment_status": prev_status},
                after_state={"shipment_status": shipment.shipment_status},
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="shipment",
                aggregate_id=shipment.id,
                event_type="ShipmentReceived",
                payload_json={
                    "shipment_id": str(shipment.id),
                    "shipment_number": shipment.shipment_number,
                    "shipment_status": shipment.shipment_status,
                },
            )
        return self.get_shipment(
            db, tenant_ctx=tenant_ctx, shipment_id=shipment.id, source_ip=source_ip
        )

    def cancel_shipment(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        shipment_id: uuid.UUID,
        payload: ShipmentCancelRequest,
        source_ip: Optional[str] = None,
    ) -> Shipment:
        shipment = self.get_shipment(
            db, tenant_ctx=tenant_ctx, shipment_id=shipment_id, source_ip=source_ip
        )
        assert_transition(
            SHIPMENT_TRANSITIONS,
            shipment.shipment_status,
            ShipmentStatus.CANCELLED.value,
            entity_name="Shipment",
        )
        with UnitOfWork(db):
            verify_and_increment_version(shipment, payload.row_version)
            shipment.shipment_status = ShipmentStatus.CANCELLED.value
            shipment.updated_by_user_id = tenant_ctx.user_id
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=shipment.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="SHIPMENT_CANCELLED",
                entity_type="shipments",
                entity_id=shipment.id,
                reason=payload.reason,
                source_ip=source_ip,
            )
        return self.get_shipment(
            db, tenant_ctx=tenant_ctx, shipment_id=shipment.id, source_ip=source_ip
        )

    # --- Movement Ledger & End-to-End Traceability ---

    def get_batch_movements(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> List[BatchMovementEvent]:
        batch = self._repo.get_batch_for_org(db, tenant_ctx.organization_id, batch_id)
        if batch is None:
            raise NotFoundError("Batch not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            batch.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/distribution/batches/{batch_id}/movements",
        )
        return self._repo.list_movement_events_for_batch(
            db, tenant_ctx.organization_id, batch.id
        )

    def _to_traceability_batch_summary(
        self, batch: Batch, product: Optional[Product]
    ) -> TraceabilityBatchSummary:
        return TraceabilityBatchSummary(
            batch_id=batch.id,
            batch_number=batch.batch_number,
            product_id=batch.product_id,
            product_code=product.product_code if product else None,
            product_name=(product.brand_name or product.generic_name) if product else None,
            site_id=batch.site_id,
            batch_status=batch.batch_status,
            quality_review_status=batch.quality_review_status,
            manufacturing_date=batch.manufacturing_date,
            expiry_date=batch.expiry_date,
            batch_size_value=batch.batch_size_value,
            batch_size_unit=batch.batch_size_unit,
            actual_yield_value=batch.actual_yield_value,
            distributed_quantity=batch.distributed_quantity,
        )

    def get_batch_traceability(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> BatchTraceabilityResponse:
        batch = self._repo.get_batch_for_org(
            db, tenant_ctx.organization_id, batch_id, with_children=True
        )
        if batch is None:
            raise NotFoundError("Batch not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            batch.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/batches/{batch_id}/traceability",
        )

        org_id = tenant_ctx.organization_id
        product = db.get(Product, batch.product_id)

        # Backward material consumptions -> Materials -> SupplierLots -> BusinessPartners
        consumptions = list(batch.material_consumptions or [])
        mat_ids = {c.material_id for c in consumptions}
        lot_ids = {c.supplier_lot_id for c in consumptions}

        materials_map: Dict[uuid.UUID, Material] = {}
        if mat_ids:
            for m in db.scalars(select(Material).where(Material.id.in_(mat_ids))).all():
                materials_map[m.id] = m

        lots_map: Dict[uuid.UUID, SupplierLot] = {}
        supplier_ids: Set[uuid.UUID] = set()
        if lot_ids:
            for lot in db.scalars(select(SupplierLot).where(SupplierLot.id.in_(lot_ids))).all():
                lots_map[lot.id] = lot
                supplier_ids.add(lot.supplier_partner_id)

        # Forward shipments & lines
        shipment_line_pairs = self._repo.list_shipment_lines_for_batch(db, org_id, batch.id)
        for _ln, sh in shipment_line_pairs:
            supplier_ids.add(sh.partner_id)

        partners_map: Dict[uuid.UUID, BusinessPartner] = {}
        if supplier_ids:
            for p in db.scalars(
                select(BusinessPartner).where(BusinessPartner.id.in_(supplier_ids))
            ).all():
                partners_map[p.id] = p

        partner_site_ids = {sh.partner_site_id for _ln, sh in shipment_line_pairs}
        partner_sites_map: Dict[uuid.UUID, PartnerSite] = {}
        if partner_site_ids:
            for ps in db.scalars(select(PartnerSite).where(PartnerSite.id.in_(partner_site_ids))).all():
                partner_sites_map[ps.id] = ps

        backward_materials: List[TraceabilityMaterialItem] = []
        for c in consumptions:
            mat = materials_map.get(c.material_id)
            lot = lots_map.get(c.supplier_lot_id)
            partner = partners_map.get(lot.supplier_partner_id) if lot else None
            backward_materials.append(
                TraceabilityMaterialItem(
                    consumption_id=c.id,
                    material_id=c.material_id,
                    material_code=mat.material_code if mat else "",
                    material_name=mat.material_name if mat else "",
                    material_type=mat.material_type if mat else "",
                    supplier_lot_id=c.supplier_lot_id,
                    internal_lot_number=lot.internal_lot_number if lot else "",
                    supplier_lot_number=lot.supplier_lot_number if lot else "",
                    supplier_partner_id=lot.supplier_partner_id if lot else uuid.UUID(int=0),
                    supplier_name=partner.legal_name if partner else None,
                    quantity_consumed=c.quantity_consumed,
                    unit_code=c.unit_code,
                    stage_name=c.stage_name,
                    lot_status=lot.lot_status if lot else "",
                    issued_at=c.issued_at,
                    verified_by_membership_id=c.verified_by_membership_id,
                    coa_document_id=lot.coa_document_id if lot else None,
                )
            )

        # Multi-hop backward/forward batch genealogy graph via BFS
        all_rels: List[BatchRelationship] = self._repo.list_all_relationships_for_org(db, org_id)
        parents_by_child: Dict[uuid.UUID, List[BatchRelationship]] = {}
        children_by_parent: Dict[uuid.UUID, List[BatchRelationship]] = {}
        for rel in all_rels:
            parents_by_child.setdefault(rel.child_batch_id, []).append(rel)
            children_by_parent.setdefault(rel.parent_batch_id, []).append(rel)

        ancestor_ids: List[uuid.UUID] = []
        seen_anc: Set[uuid.UUID] = {batch.id}
        q_anc: deque[uuid.UUID] = deque([batch.id])
        relevant_edges: Dict[uuid.UUID, BatchRelationship] = {}
        while q_anc:
            cur = q_anc.popleft()
            for rel in parents_by_child.get(cur, ()):
                relevant_edges[rel.id] = rel
                if rel.parent_batch_id not in seen_anc:
                    seen_anc.add(rel.parent_batch_id)
                    ancestor_ids.append(rel.parent_batch_id)
                    q_anc.append(rel.parent_batch_id)

        descendant_ids: List[uuid.UUID] = []
        seen_desc: Set[uuid.UUID] = {batch.id}
        q_desc: deque[uuid.UUID] = deque([batch.id])
        while q_desc:
            cur = q_desc.popleft()
            for rel in children_by_parent.get(cur, ()):
                relevant_edges[rel.id] = rel
                if rel.child_batch_id not in seen_desc:
                    seen_desc.add(rel.child_batch_id)
                    descendant_ids.append(rel.child_batch_id)
                    q_desc.append(rel.child_batch_id)

        related_batch_ids = set(ancestor_ids) | set(descendant_ids)
        related_batches: Dict[uuid.UUID, Batch] = {batch.id: batch}
        if related_batch_ids:
            for rb in db.scalars(select(Batch).where(Batch.id.in_(related_batch_ids))).all():
                related_batches[rb.id] = rb

        extra_product_ids = {
            rb.product_id for rb in related_batches.values() if rb.product_id != batch.product_id
        }
        products_by_id: Dict[uuid.UUID, Product] = {}
        if product is not None:
            products_by_id[product.id] = product
        if extra_product_ids:
            for pr in db.scalars(select(Product).where(Product.id.in_(extra_product_ids))).all():
                products_by_id[pr.id] = pr

        parent_summaries = [
            self._to_traceability_batch_summary(
                related_batches[bid], products_by_id.get(related_batches[bid].product_id)
            )
            for bid in ancestor_ids
            if bid in related_batches
        ]
        child_summaries = [
            self._to_traceability_batch_summary(
                related_batches[bid], products_by_id.get(related_batches[bid].product_id)
            )
            for bid in descendant_ids
            if bid in related_batches
        ]
        genealogy_edges = [
            BatchGenealogyEdge(
                relationship_id=rel.id,
                parent_batch_id=rel.parent_batch_id,
                parent_batch_number=(
                    related_batches[rel.parent_batch_id].batch_number
                    if rel.parent_batch_id in related_batches
                    else None
                ),
                child_batch_id=rel.child_batch_id,
                child_batch_number=(
                    related_batches[rel.child_batch_id].batch_number
                    if rel.child_batch_id in related_batches
                    else None
                ),
                relation_type=rel.relation_type,
                quantity=rel.quantity,
                unit_code=rel.unit_code,
                event_at=rel.event_at,
            )
            for rel in relevant_edges.values()
        ]

        forward_shipments: List[TraceabilityShipmentItem] = []
        for ln, sh in shipment_line_pairs:
            partner = partners_map.get(sh.partner_id)
            psite = partner_sites_map.get(sh.partner_site_id)
            forward_shipments.append(
                TraceabilityShipmentItem(
                    shipment_id=sh.id,
                    shipment_number=sh.shipment_number,
                    shipment_status=sh.shipment_status,
                    site_id=sh.site_id,
                    partner_id=sh.partner_id,
                    partner_name=partner.legal_name if partner else None,
                    partner_site_id=sh.partner_site_id,
                    partner_site_name=psite.site_name if psite else None,
                    shipment_line_id=ln.id,
                    quantity=ln.quantity,
                    received_quantity=ln.received_quantity,
                    unit_code=ln.unit_code,
                    dispatched_at=sh.dispatched_at,
                    received_at=sh.received_at,
                )
            )

        movements = self._repo.list_movement_events_for_batch(db, org_id, batch.id)
        dispositions = list(batch.dispositions or [])
        doc_links = list(batch.document_links or [])
        complaint_pairs = self._repo.list_complaint_links_for_batch(db, org_id, batch.id)

        linked_complaints = [
            TraceabilityComplaintItem(
                complaint_id=cmp.id,
                complaint_number=cmp.complaint_number,
                status=cmp.status,
                final_severity=cmp.final_severity,
                reported_batch_number=cmp.reported_batch_number,
                link_resolution_outcome=lnk.resolution_outcome,
                match_status=lnk.match_status,
                created_at=cmp.created_at,
            )
            for lnk, cmp in complaint_pairs
        ]

        # Traceability gap detection
        missing_links: List[str] = []
        if not backward_materials:
            missing_links.append("NO_MATERIAL_CONSUMPTIONS_RECORDED")
        for m_item in backward_materials:
            if m_item.verified_by_membership_id is None:
                missing_links.append(f"UNVERIFIED_MATERIAL_CONSUMPTION:{m_item.internal_lot_number}")
            if m_item.coa_document_id is None:
                missing_links.append(f"MISSING_SUPPLIER_COA:{m_item.internal_lot_number}")

        has_bmr_doc = batch.primary_document_id is not None or any(
            dl.document_role in {BatchDocumentRole.BMR.value, BatchDocumentRole.BPR.value}
            for dl in doc_links
        )
        if not has_bmr_doc:
            missing_links.append("NO_BMR_OR_PRIMARY_DOCUMENT_LINKED")

        if batch.batch_status != BatchStatus.RELEASED.value:
            missing_links.append("BATCH_NOT_RELEASED")
        if batch.batch_status in {
            BatchStatus.RELEASED.value,
            BatchStatus.REJECTED.value,
            BatchStatus.REWORKED.value,
        } and not dispositions:
            missing_links.append("MISSING_DISPOSITION_RECORD")

        dispatched_shipment_ids = {
            sh.id
            for _ln, sh in shipment_line_pairs
            if sh.shipment_status
            in {
                ShipmentStatus.DISPATCHED.value,
                ShipmentStatus.IN_TRANSIT.value,
                ShipmentStatus.PARTIALLY_RECEIVED.value,
                ShipmentStatus.RECEIVED.value,
            }
        }
        movement_dispatch_shipments = {
            ev.shipment_id
            for ev in movements
            if ev.event_type == BatchMovementEventType.DISPATCH.value and ev.shipment_id is not None
        }
        if dispatched_shipment_ids - movement_dispatch_shipments:
            missing_links.append("DISPATCHED_SHIPMENT_MISSING_MOVEMENT_EVENT")
        if batch.distributed_quantity > Decimal("0") and not any(
            sh.shipment_status != ShipmentStatus.CANCELLED.value for _ln, sh in shipment_line_pairs
        ):
            missing_links.append("DISTRIBUTED_QUANTITY_WITHOUT_SHIPMENTS")

        product_info: Dict[str, Any] = {}
        if product is not None:
            product_info = {
                "id": str(product.id),
                "product_code": product.product_code,
                "brand_name": product.brand_name,
                "generic_name": product.generic_name,
                "dosage_form": product.dosage_form,
                "product_status": product.product_status,
            }

        disp_responses = [BatchDispositionResponse.model_validate(d) for d in dispositions]
        return BatchTraceabilityResponse(
            batch=self._to_traceability_batch_summary(batch, product),
            product=product_info,
            latest_disposition=disp_responses[0] if disp_responses else None,
            dispositions=disp_responses,
            backward_materials=backward_materials,
            parent_batches=parent_summaries,
            child_batches=child_summaries,
            genealogy_edges=genealogy_edges,
            forward_shipments=forward_shipments,
            movement_events=[BatchMovementEventResponse.model_validate(ev) for ev in movements],
            linked_documents=[BatchDocumentLinkResponse.model_validate(dl) for dl in doc_links],
            linked_complaints=linked_complaints,
            missing_links=missing_links,
        )
