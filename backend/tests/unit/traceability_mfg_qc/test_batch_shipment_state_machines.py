from datetime import date
from decimal import Decimal
import uuid

import pytest

from app.domain.enums import BatchDispositionDecision, BatchStatus, ShipmentStatus
from app.models.schemas.traceability import BatchCreateRequest
from app.services.distribution_traceability_service import SHIPMENT_TRANSITIONS
from app.services.product_batch_service import (
    DISPOSITION_ALLOWED_FROM,
    DISPOSITION_CONTROLLED_STATUSES,
    OPERATIONAL_BATCH_TRANSITIONS,
)


def test_operational_transitions_never_reach_disposition_controlled_states() -> None:
    for targets in OPERATIONAL_BATCH_TRANSITIONS.values():
        assert DISPOSITION_CONTROLLED_STATUSES.isdisjoint(targets)


def test_disposition_transitions_prevent_releasing_rejected_or_planned_batches() -> None:
    release_sources = DISPOSITION_ALLOWED_FROM[BatchDispositionDecision.RELEASED.value]
    assert BatchStatus.PLANNED.value not in release_sources
    assert BatchStatus.REJECTED.value not in release_sources
    assert BatchStatus.RECALLED.value not in release_sources
    assert BatchStatus.EXPIRED.value not in release_sources
    assert BatchStatus.UNDER_QUALITY_REVIEW.value in release_sources
    assert BatchStatus.QUARANTINED.value in release_sources


def test_shipment_transitions_cover_partial_receipt_and_terminal_states() -> None:
    assert ShipmentStatus.DISPATCHED.value in SHIPMENT_TRANSITIONS[ShipmentStatus.DRAFT.value]
    assert ShipmentStatus.CANCELLED.value in SHIPMENT_TRANSITIONS[ShipmentStatus.DRAFT.value]
    assert ShipmentStatus.PARTIALLY_RECEIVED.value in SHIPMENT_TRANSITIONS[ShipmentStatus.DISPATCHED.value]
    assert ShipmentStatus.RECEIVED.value in SHIPMENT_TRANSITIONS[ShipmentStatus.PARTIALLY_RECEIVED.value]
    assert SHIPMENT_TRANSITIONS[ShipmentStatus.RECEIVED.value] == set()
    assert SHIPMENT_TRANSITIONS[ShipmentStatus.CANCELLED.value] == set()


def test_batch_create_schema_rejects_expiry_before_or_equal_to_mfg_date() -> None:
    with pytest.raises(ValueError, match="expiry_date"):
        BatchCreateRequest(
            site_id=uuid.uuid4(),
            product_id=uuid.uuid4(),
            batch_number="B-INVALID-01",
            manufacturing_date=date(2026, 6, 1),
            expiry_date=date(2026, 6, 1),
            batch_size_value=Decimal("1000"),
            batch_size_unit="TABLETS",
        )
