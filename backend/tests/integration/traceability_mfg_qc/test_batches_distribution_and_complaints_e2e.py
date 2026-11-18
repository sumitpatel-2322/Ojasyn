import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.database import UnitOfWork
from app.core.deps import reset_rate_limiter
from app.core.security import create_access_token, get_password_hash
from app.domain.enums import (
    AssignmentStatus,
    AssignmentType,
    BatchStatus,
    MembershipStatus,
    MemberType,
    RoleAssignmentScopeType,
    RoleAssignmentStatus,
    UserAccountStatus,
)
from app.models.domain.actors import BusinessPartner, PartnerSite
from app.models.domain.documents import Document
from app.models.domain.governance import ApprovalRecord, OutboxEvent
from app.models.domain.identity import (
    MembershipRoleAssignment,
    OrganizationMembership,
    Role,
    SiteAssignment,
    User,
)
from app.models.domain.products_materials import Material, Product, SupplierLot
from app.models.domain.traceability import Batch, BatchMovementEvent, BatchStatusHistory
from tests.conftest import create_seeded_tenant_actor


@pytest.fixture(autouse=True)
def _clean_limits():
    reset_rate_limiter()
    yield
    reset_rate_limiter()


def _add_org_actor(
    db,
    *,
    org_id: uuid.UUID,
    site_id: uuid.UUID,
    email: str,
    role_code: str,
    scope_type: str = RoleAssignmentScopeType.ORGANIZATION.value,
) -> dict:
    user = User(
        email=email,
        full_name=f"User {email}",
        password_hash=get_password_hash("StrongPass!123"),
        account_status=UserAccountStatus.ACTIVE.value,
    )
    db.add(user)
    db.flush()

    membership = OrganizationMembership(
        user_id=user.id,
        organization_id=org_id,
        member_type=MemberType.EMPLOYEE.value,
        membership_status=MembershipStatus.ACTIVE.value,
        employee_code=f"EMP-{uuid.uuid4().hex[:6]}",
    )
    db.add(membership)
    db.flush()

    db.add(
        SiteAssignment(
            organization_id=org_id,
            membership_id=membership.id,
            site_id=site_id,
            assignment_type=AssignmentType.PRIMARY.value,
            status=AssignmentStatus.ACTIVE.value,
            assigned_by_user_id=user.id,
        )
    )
    role = db.query(Role).filter(Role.role_code == role_code).one()
    db.add(
        MembershipRoleAssignment(
            organization_id=org_id,
            membership_id=membership.id,
            role_id=role.id,
            scope_type=scope_type,
            site_id=site_id if scope_type == RoleAssignmentScopeType.SITE.value else None,
            granted_by_user_id=user.id,
            approved_by_user_id=user.id,
            status=RoleAssignmentStatus.ACTIVE.value,
        )
    )
    db.flush()
    return {"user": user, "membership": membership}


def test_batch_material_disposition_distribution_traceability_and_complaint_e2e(
    api_client, sqlite_db
) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        prod_seed = create_seeded_tenant_actor(
            uow.session,
            org_name="Aether Remedies",
            site_code="ATH-S1",
            email="prod.head@aether.in",
            role_code="PRODUCTION_HEAD",
            scope_type="SITE",
        )
        org = prod_seed["organization"]
        site = prod_seed["site"]
        prod_user = prod_seed["user"]
        prod_member = prod_seed["membership"]

        approver_seed = _add_org_actor(
            uow.session,
            org_id=org.id,
            site_id=site.id,
            email="qa.approver@aether.in",
            role_code="BATCH_DISPOSITION_APPROVER",
            scope_type="ORGANIZATION",
        )
        warehouse_seed = _add_org_actor(
            uow.session,
            org_id=org.id,
            site_id=site.id,
            email="wh.mgr@aether.in",
            role_code="WAREHOUSE_MANAGER",
            scope_type="ORGANIZATION",
        )
        qa_officer_seed = _add_org_actor(
            uow.session,
            org_id=org.id,
            site_id=site.id,
            email="qa.officer@aether.in",
            role_code="QA_OFFICER",
            scope_type="ORGANIZATION",
        )

        product = Product(
            organization_id=org.id,
            site_id=site.id,
            product_code="PRD-AZI-500",
            brand_name="AziPure 500",
            generic_name="Azithromycin",
            dosage_form="TABLET",
            strength_value=Decimal("500.0000"),
            strength_unit="MG",
            product_status="ACTIVE",
            created_by_user_id=prod_user.id,
            updated_by_user_id=prod_user.id,
        )
        uow.session.add(product)
        uow.session.flush()

        supplier = BusinessPartner(
            organization_id=org.id,
            partner_code="SUP-CHEM-01",
            legal_name="Indus API Suppliers",
            partner_type="SUPPLIER",
            primary_email="supply@indusapi.in",
            status="ACTIVE",
        )
        distributor = BusinessPartner(
            organization_id=org.id,
            partner_code="DIST-WEST-01",
            legal_name="Western Med Logistics",
            partner_type="DISTRIBUTOR",
            primary_email="ops@westernmed.in",
            status="ACTIVE",
        )
        uow.session.add_all([supplier, distributor])
        uow.session.flush()

        dist_site = PartnerSite(
            organization_id=org.id,
            partner_id=distributor.id,
            site_code="WML-DEPOT-1",
            site_name="Mumbai Cold Depot",
            address_line_1="Plot 42, Bhiwandi",
            city="Thane",
            postal_code="421302",
            country_code="IN",
            status="ACTIVE",
        )
        uow.session.add(dist_site)

        material = Material(
            organization_id=org.id,
            material_code="MAT-AZI-API",
            material_name="Azithromycin Dihydrate API",
            material_type="API",
            default_unit_code="KG",
            qualification_status="APPROVED",
            material_status="ACTIVE",
            default_supplier_partner_id=supplier.id,
        )
        uow.session.add(material)
        uow.session.flush()

        coa_doc = Document(
            organization_id=org.id,
            site_id=site.id,
            document_code="COA-MAT-001",
            document_type="COA",
            title="Supplier Lot COA",
            status="ACTIVE",
        )
        bmr_doc = Document(
            organization_id=org.id,
            site_id=site.id,
            document_code="BMR-B26-001",
            document_type="BMR",
            title="Batch Manufacturing Record B26-001",
            status="ACTIVE",
        )
        uow.session.add_all([coa_doc, bmr_doc])
        uow.session.flush()

        lot_quarantined = SupplierLot(
            organization_id=org.id,
            site_id=site.id,
            material_id=material.id,
            supplier_partner_id=supplier.id,
            internal_lot_number="ILOT-Q-01",
            supplier_lot_number="VLOT-Q-01",
            received_quantity=Decimal("50.0000"),
            remaining_quantity=Decimal("50.0000"),
            unit_code="KG",
            received_date=date(2026, 1, 10),
            lot_status="QUARANTINED",
        )
        lot_released = SupplierLot(
            organization_id=org.id,
            site_id=site.id,
            material_id=material.id,
            supplier_partner_id=supplier.id,
            internal_lot_number="ILOT-REL-01",
            supplier_lot_number="VLOT-REL-01",
            received_quantity=Decimal("100.0000"),
            remaining_quantity=Decimal("100.0000"),
            unit_code="KG",
            received_date=date(2026, 1, 12),
            expiry_or_retest_date=date(2028, 1, 12),
            lot_status="RELEASED",
            coa_document_id=coa_doc.id,
        )
        uow.session.add_all([lot_quarantined, lot_released])
        uow.session.flush()

        org_id = org.id
        site_id = site.id
        product_id = product.id
        material_id = material.id
        lot_q_id = lot_quarantined.id
        lot_r_id = lot_released.id
        dist_id = distributor.id
        dist_site_id = dist_site.id
        bmr_doc_id = bmr_doc.id
        prod_user_id = prod_user.id
        prod_member_id = prod_member.id
        approver_user_id = approver_seed["user"].id
        approver_member_id = approver_seed["membership"].id
        wh_user_id = warehouse_seed["user"].id
        qa_officer_user_id = qa_officer_seed["user"].id

    def _headers(uid: uuid.UUID) -> dict:
        return {
            "Authorization": f"Bearer {create_access_token(subject=str(uid))}",
            "X-Organization-Id": str(org_id),
        }

    prod_headers = _headers(prod_user_id)
    approver_headers = _headers(approver_user_id)
    wh_headers = _headers(wh_user_id)
    qa_headers = _headers(qa_officer_user_id)

    # 1. Create parent & child Batch
    create_resp = api_client.post(
        "/api/v1/batches",
        headers=prod_headers,
        json={
            "site_id": str(site_id),
            "product_id": str(product_id),
            "batch_number": "B26-AZI-001",
            "manufacturing_order_no": "MO-2026-88",
            "manufacturing_date": "2026-02-01",
            "expiry_date": "2028-01-31",
            "batch_size_value": "1000.0000",
            "batch_size_unit": "BOXES",
            "packaging_configuration": "10x10 Blister",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    batch = create_resp.json()
    batch_id = batch["id"]
    assert batch["batch_status"] == BatchStatus.PLANNED.value

    # 2. Operational transition via /status and attempt bypass of disposition via PATCH -> 409
    r_inproc = api_client.post(
        f"/api/v1/batches/{batch_id}/status",
        headers=prod_headers,
        json={
            "row_version": batch["row_version"],
            "to_status": "IN_PROCESS",
            "reason": "Dispensing and granulation initiated.",
        },
    )
    assert r_inproc.status_code == 200
    batch = r_inproc.json()

    r_bypass_patch = api_client.patch(
        f"/api/v1/batches/{batch_id}",
        headers=prod_headers,
        json={"row_version": batch["row_version"], "batch_status": "RELEASED"},
    )
    assert r_bypass_patch.status_code == 409
    assert r_bypass_patch.json()["error_code"] == "DISPOSITION_ENDPOINT_REQUIRED"

    r_bypass_status = api_client.post(
        f"/api/v1/batches/{batch_id}/status",
        headers=prod_headers,
        json={"row_version": batch["row_version"], "to_status": "REJECTED", "reason": "Try bypass"},
    )
    assert r_bypass_status.status_code == 409
    assert r_bypass_status.json()["error_code"] == "DISPOSITION_ENDPOINT_REQUIRED"

    # 3. Material consumption rules: QUARANTINED lot rejected (409), over-consumption rejected (422), valid deducted
    r_q_lot = api_client.post(
        f"/api/v1/batches/{batch_id}/materials",
        headers=prod_headers,
        json={
            "material_id": str(material_id),
            "supplier_lot_id": str(lot_q_id),
            "quantity_consumed": "10.0000",
            "unit_code": "KG",
        },
    )
    assert r_q_lot.status_code == 409
    assert r_q_lot.json()["error_code"] == "SUPPLIER_LOT_NOT_RELEASED"

    r_over = api_client.post(
        f"/api/v1/batches/{batch_id}/materials",
        headers=prod_headers,
        json={
            "material_id": str(material_id),
            "supplier_lot_id": str(lot_r_id),
            "quantity_consumed": "120.0000",
            "unit_code": "KG",
        },
    )
    assert r_over.status_code == 422
    assert r_over.json()["error_code"] == "INSUFFICIENT_SUPPLIER_LOT_QUANTITY"

    r_cons = api_client.post(
        f"/api/v1/batches/{batch_id}/materials",
        headers=prod_headers,
        json={
            "material_id": str(material_id),
            "supplier_lot_id": str(lot_r_id),
            "quantity_consumed": "60.0000",
            "unit_code": "KG",
            "stage_name": "DISPENSING",
        },
    )
    assert r_cons.status_code == 201, r_cons.text
    consumption_id = r_cons.json()["id"]

    # Same-user verification blocked, distinct verifier succeeds
    r_self_verify = api_client.post(
        f"/api/v1/batches/{batch_id}/materials/{consumption_id}/verify",
        headers=prod_headers,
        json={"notes": "Self verify"},
    )
    assert r_self_verify.status_code == 403
    assert r_self_verify.json()["error_code"] == "MAKER_CHECKER_VIOLATION"

    r_verify = api_client.post(
        f"/api/v1/batches/{batch_id}/materials/{consumption_id}/verify",
        headers=approver_headers,
        json={"notes": "Witnessed weighing slip"},
    )
    assert r_verify.status_code == 200
    assert r_verify.json()["verified_by_membership_id"] == str(approver_member_id)

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        lot_row = uow.session.get(SupplierLot, lot_r_id)
        assert lot_row is not None
        assert lot_row.remaining_quantity == Decimal("40.0000")

    # Move batch to UNDER_QUALITY_REVIEW + link BMR document + update yield
    r_yield = api_client.patch(
        f"/api/v1/batches/{batch_id}",
        headers=prod_headers,
        json={
            "row_version": batch["row_version"],
            "actual_yield_value": "980.0000",
            "batch_status": "UNDER_QUALITY_REVIEW",
            "reason": "Ready for QA disposition review.",
        },
    )
    assert r_yield.status_code == 200, r_yield.text
    batch = r_yield.json()
    assert batch["batch_status"] == BatchStatus.UNDER_QUALITY_REVIEW.value

    r_doc = api_client.post(
        f"/api/v1/batches/{batch_id}/documents",
        headers=prod_headers,
        json={"document_id": str(bmr_doc_id), "document_role": "BMR"},
    )
    assert r_doc.status_code == 201

    # 4. Shipment cannot be created before RELEASED disposition -> 409
    r_ship_unrel = api_client.post(
        "/api/v1/distribution/shipments",
        headers=wh_headers,
        json={
            "site_id": str(site_id),
            "partner_id": str(dist_id),
            "partner_site_id": str(dist_site_id),
            "lines": [{"batch_id": batch_id, "quantity": "100.0000", "unit_code": "BOXES"}],
        },
    )
    assert r_ship_unrel.status_code == 409
    assert r_ship_unrel.json()["error_code"] == "BATCH_NOT_RELEASED"

    # 5. QA Disposition checks: incomplete checklist (422), valid RELEASED (201)
    r_bad_check = api_client.post(
        f"/api/v1/batches/{batch_id}/disposition",
        headers=approver_headers,
        json={
            "row_version": batch["row_version"],
            "disposition_decision": "RELEASED",
            "proposed_by_membership_id": str(prod_member_id),
            "checklist": {"bmr_reviewed": True, "qc_passed": False, "open_critical_deviations": False},
        },
    )
    assert r_bad_check.status_code == 422
    assert r_bad_check.json()["error_code"] == "INCOMPLETE_DISPOSITION_CHECKLIST"

    r_disp = api_client.post(
        f"/api/v1/batches/{batch_id}/disposition",
        headers=approver_headers,
        json={
            "row_version": batch["row_version"],
            "disposition_decision": "RELEASED",
            "proposed_by_membership_id": str(prod_member_id),
            "checklist": {"bmr_reviewed": True, "qc_passed": True, "open_critical_deviations": False},
            "comments": "All IPC & COA specifications met.",
        },
    )
    assert r_disp.status_code == 201, r_disp.text
    disp_data = r_disp.json()
    assert disp_data["disposition_decision"] == "RELEASED"

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        b_db = uow.session.get(Batch, uuid.UUID(batch_id))
        assert b_db is not None
        assert b_db.batch_status == BatchStatus.RELEASED.value
        assert b_db.released_by_membership_id == approver_member_id
        assert uow.session.get(ApprovalRecord, uuid.UUID(disp_data["approval_record_id"])) is not None
        hist_count = len(
            uow.session.scalars(
                select(BatchStatusHistory).where(BatchStatusHistory.batch_id == uuid.UUID(batch_id))
            ).all()
        )
        assert hist_count == 4
        outbox_ev = uow.session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == uuid.UUID(batch_id),
                OutboxEvent.event_type == "BatchDispositionDecided",
            )
        )
        assert outbox_ev is not None

    # 6. Create child batch & genealogy relationship
    r_child = api_client.post(
        "/api/v1/batches",
        headers=prod_headers,
        json={
            "site_id": str(site_id),
            "product_id": str(product_id),
            "batch_number": "B26-AZI-001-RP",
            "manufacturing_date": "2026-02-10",
            "expiry_date": "2028-01-31",
            "batch_size_value": "200.0000",
            "batch_size_unit": "BOXES",
        },
    )
    assert r_child.status_code == 201
    child_id = r_child.json()["id"]

    r_rel = api_client.post(
        f"/api/v1/batches/{batch_id}/relationships",
        headers=prod_headers,
        json={
            "related_batch_id": child_id,
            "direction": "CHILD",
            "relation_type": "REPACK",
            "quantity": "200.0000",
            "unit_code": "BOXES",
        },
    )
    assert r_rel.status_code == 201

    r_cycle = api_client.post(
        f"/api/v1/batches/{child_id}/relationships",
        headers=prod_headers,
        json={
            "related_batch_id": batch_id,
            "direction": "CHILD",
            "relation_type": "SPLIT",
        },
    )
    assert r_cycle.status_code == 422

    # 7. Shipment over-dispatch blocked (422), valid shipment created, dispatched, partially + fully received
    r_over_ship = api_client.post(
        "/api/v1/distribution/shipments",
        headers=wh_headers,
        json={
            "site_id": str(site_id),
            "partner_id": str(dist_id),
            "partner_site_id": str(dist_site_id),
            "lines": [{"batch_id": batch_id, "quantity": "990.0000", "unit_code": "BOXES"}],
        },
    )
    assert r_over_ship.status_code == 422
    assert r_over_ship.json()["error_code"] == "INSUFFICIENT_BATCH_QUANTITY"

    r_ship = api_client.post(
        "/api/v1/distribution/shipments",
        headers=wh_headers,
        json={
            "site_id": str(site_id),
            "partner_id": str(dist_id),
            "partner_site_id": str(dist_site_id),
            "carrier_name": "MedExpress",
            "lines": [{"batch_id": batch_id, "quantity": "600.0000", "unit_code": "BOXES"}],
        },
    )
    assert r_ship.status_code == 201, r_ship.text
    shipment = r_ship.json()
    shipment_id = shipment["id"]
    line_id = shipment["lines"][0]["id"]

    r_disp_ship = api_client.post(
        f"/api/v1/distribution/shipments/{shipment_id}/dispatch",
        headers=wh_headers,
        json={"row_version": shipment["row_version"], "tracking_number": "AWB-99100"},
    )
    assert r_disp_ship.status_code == 200, r_disp_ship.text
    shipment = r_disp_ship.json()
    assert shipment["shipment_status"] == "DISPATCHED"

    # Attempt second shipment exceeding remaining 380 boxes -> 422
    r_over_ship2 = api_client.post(
        "/api/v1/distribution/shipments",
        headers=wh_headers,
        json={
            "site_id": str(site_id),
            "partner_id": str(dist_id),
            "partner_site_id": str(dist_site_id),
            "lines": [{"batch_id": batch_id, "quantity": "400.0000", "unit_code": "BOXES"}],
        },
    )
    assert r_over_ship2.status_code == 422

    # Partial then full receipt
    r_recv_part = api_client.post(
        f"/api/v1/distribution/shipments/{shipment_id}/receipt",
        headers=wh_headers,
        json={
            "row_version": shipment["row_version"],
            "line_receipts": [{"shipment_line_id": line_id, "received_quantity": "500.0000"}],
        },
    )
    assert r_recv_part.status_code == 200
    shipment = r_recv_part.json()
    assert shipment["shipment_status"] == "PARTIALLY_RECEIVED"

    r_recv_full = api_client.post(
        f"/api/v1/distribution/shipments/{shipment_id}/receipt",
        headers=wh_headers,
        json={
            "row_version": shipment["row_version"],
            "received_all": True,
        },
    )
    assert r_recv_full.status_code == 200
    assert r_recv_full.json()["shipment_status"] == "RECEIVED"

    # 8. Complaint integration with canonical product_id & batch_id
    r_unrouted_prod = api_client.post(
        "/api/v1/complaints",
        json={
            "product_id": str(product_id),
            "description": "Public caller should not pass internal product_id without org.",
        },
    )
    assert r_unrouted_prod.status_code == 422

    r_cmp = api_client.post(
        "/api/v1/complaints",
        headers=qa_headers,
        json={
            "site_id": str(site_id),
            "product_id": str(product_id),
            "reported_product_name": "AziPure 500",
            "reported_batch_number": "B26-AZI-001",
            "description": "Blister seal open inside carton from Mumbai depot.",
        },
    )
    assert r_cmp.status_code == 201, r_cmp.text
    cmp_id = r_cmp.json()["id"]

    r_link_batch = api_client.post(
        f"/api/v1/complaints/{cmp_id}/batch-candidates/resolve",
        headers=qa_headers,
        json={
            "action": "MANUAL_LINK",
            "batch_id": batch_id,
            "notes": "Matched against Mumbai dispatch batch.",
        },
    )
    assert r_link_batch.status_code == 200, r_link_batch.text
    assert r_link_batch.json()["batch_id"] == batch_id

    # 9. End-to-end Traceability + Genealogy + Movements
    r_trace = api_client.get(f"/api/v1/batches/{batch_id}/traceability", headers=qa_headers)
    assert r_trace.status_code == 200, r_trace.text
    trace = r_trace.json()
    assert trace["batch"]["batch_number"] == "B26-AZI-001"
    assert trace["batch"]["distributed_quantity"] == "600.0000"
    assert len(trace["backward_materials"]) == 1
    assert trace["backward_materials"][0]["internal_lot_number"] == "ILOT-REL-01"
    assert len(trace["child_batches"]) == 1
    assert trace["child_batches"][0]["batch_number"] == "B26-AZI-001-RP"
    assert len(trace["forward_shipments"]) == 1
    assert trace["forward_shipments"][0]["shipment_status"] == "RECEIVED"
    assert len(trace["linked_documents"]) == 1
    assert len(trace["linked_complaints"]) == 1
    assert trace["linked_complaints"][0]["complaint_id"] == cmp_id
    assert trace["missing_links"] == []

    r_moves = api_client.get(
        f"/api/v1/distribution/batches/{batch_id}/movements", headers=wh_headers
    )
    assert r_moves.status_code == 200
    ev_types = [m["event_type"] for m in r_moves.json()]
    assert "PRODUCED" in ev_types
    assert "RELEASED" in ev_types
    assert "DISPATCH" in ev_types
    assert "RECEIPT" in ev_types

    # 10. Regression checks: dispatch-time TOCTOU over-allocation & zero-delta RECEIPT suppression
    r_draft_2 = api_client.post(
        "/api/v1/distribution/shipments",
        headers=wh_headers,
        json={
            "site_id": str(site_id),
            "partner_id": str(dist_id),
            "partner_site_id": str(dist_site_id),
            "lines": [{"batch_id": batch_id, "quantity": "300.0000", "unit_code": "BOXES"}],
        },
    )
    assert r_draft_2.status_code == 201
    draft_2 = r_draft_2.json()

    r_draft_3 = api_client.post(
        "/api/v1/distribution/shipments",
        headers=wh_headers,
        json={
            "site_id": str(site_id),
            "partner_id": str(dist_id),
            "partner_site_id": str(dist_site_id),
            "lines": [{"batch_id": batch_id, "quantity": "300.0000", "unit_code": "BOXES"}],
        },
    )
    assert r_draft_3.status_code == 201
    draft_3 = r_draft_3.json()

    r_disp_2 = api_client.post(
        f"/api/v1/distribution/shipments/{draft_2['id']}/dispatch",
        headers=wh_headers,
        json={"row_version": draft_2["row_version"]},
    )
    assert r_disp_2.status_code == 200
    draft_2 = r_disp_2.json()

    # Dispatching draft_3 would total 1200 > actual_yield_value (980) -> blocked at dispatch time (422)
    r_disp_3 = api_client.post(
        f"/api/v1/distribution/shipments/{draft_3['id']}/dispatch",
        headers=wh_headers,
        json={"row_version": draft_3["row_version"]},
    )
    assert r_disp_3.status_code == 422
    assert r_disp_3.json()["error_code"] == "INSUFFICIENT_BATCH_QUANTITY"

    # Submitting 0 received_quantity emits no duplicate zero-quantity RECEIPT movement event
    r_zero_recv = api_client.post(
        f"/api/v1/distribution/shipments/{draft_2['id']}/receipt",
        headers=wh_headers,
        json={
            "row_version": draft_2["row_version"],
            "line_receipts": [
                {"shipment_line_id": draft_2["lines"][0]["id"], "received_quantity": "0.0000"}
            ],
        },
    )
    assert r_zero_recv.status_code == 200
    r_moves_after = api_client.get(
        f"/api/v1/distribution/batches/{batch_id}/movements", headers=wh_headers
    )
    receipt_events = [m for m in r_moves_after.json() if m["event_type"] == "RECEIPT"]
    assert all(Decimal(m["quantity"]) > Decimal("0") for m in receipt_events)
