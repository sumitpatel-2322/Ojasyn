import pytest

from app.core.exceptions import ConflictError
from app.domain.state_machines import assert_transition
from app.services.product_material_service import (
    MATERIAL_QUALIFICATION_TRANSITIONS,
    MATERIAL_STATUS_TRANSITIONS,
    PRODUCT_STATUS_TRANSITIONS,
    SUPPLIER_LOT_STATUS_TRANSITIONS,
)


def test_product_material_and_supplier_lot_state_machines() -> None:
    assert_transition(PRODUCT_STATUS_TRANSITIONS, "DRAFT", "ACTIVE", entity_name="Product")
    assert_transition(PRODUCT_STATUS_TRANSITIONS, "ACTIVE", "BLOCKED", entity_name="Product")
    assert_transition(PRODUCT_STATUS_TRANSITIONS, "BLOCKED", "ACTIVE", entity_name="Product")
    with pytest.raises(ConflictError):
        assert_transition(PRODUCT_STATUS_TRANSITIONS, "DRAFT", "DISCONTINUED", entity_name="Product")
    with pytest.raises(ConflictError):
        assert_transition(PRODUCT_STATUS_TRANSITIONS, "DISCONTINUED", "ACTIVE", entity_name="Product")

    assert_transition(MATERIAL_STATUS_TRANSITIONS, "DRAFT", "ACTIVE", entity_name="Material")
    with pytest.raises(ConflictError):
        assert_transition(MATERIAL_STATUS_TRANSITIONS, "DISCONTINUED", "ACTIVE", entity_name="Material")

    assert_transition(MATERIAL_QUALIFICATION_TRANSITIONS, "DRAFT", "UNDER_QUALIFICATION")
    assert_transition(MATERIAL_QUALIFICATION_TRANSITIONS, "UNDER_QUALIFICATION", "APPROVED")
    assert_transition(MATERIAL_QUALIFICATION_TRANSITIONS, "APPROVED", "DISQUALIFIED")
    with pytest.raises(ConflictError):
        assert_transition(MATERIAL_QUALIFICATION_TRANSITIONS, "OBSOLETE", "APPROVED")

    assert_transition(SUPPLIER_LOT_STATUS_TRANSITIONS, "QUARANTINED", "RELEASED", entity_name="SupplierLot")
    assert_transition(SUPPLIER_LOT_STATUS_TRANSITIONS, "QUARANTINED", "REJECTED", entity_name="SupplierLot")
    assert_transition(SUPPLIER_LOT_STATUS_TRANSITIONS, "RELEASED", "EXHAUSTED", entity_name="SupplierLot")
    with pytest.raises(ConflictError):
        assert_transition(SUPPLIER_LOT_STATUS_TRANSITIONS, "REJECTED", "RELEASED", entity_name="SupplierLot")
    with pytest.raises(ConflictError):
        assert_transition(SUPPLIER_LOT_STATUS_TRANSITIONS, "EXHAUSTED", "RELEASED", entity_name="SupplierLot")
