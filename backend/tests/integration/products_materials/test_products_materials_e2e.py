from datetime import date

from sqlalchemy import select

from app.core.database import UnitOfWork
from app.core.security import create_access_token
from app.models.domain.actors import BusinessPartner
from app.models.domain.governance import ApprovalRecord, OutboxEvent
from tests.conftest import create_seeded_tenant_actor


def test_product_material_bom_and_supplier_lot_lifecycle_e2e(api_client, sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        prod_head = create_seeded_tenant_actor(
            uow.session,
            org_name="Apex Pharma",
            site_code="APX-01",
            email="prodhead@apex.in",
            role_code="PRODUCTION_HEAD",
            scope_type="SITE",
        )
        org_id = prod_head["organization"].id
        site_id = prod_head["site"].id

        # Create a second actor (QC_HEAD) in the same org & site for lot release maker-checker
        from datetime import datetime, timezone
        from app.core.security import get_password_hash
        from app.models.domain.identity import (
            MembershipRoleAssignment,
            OrganizationMembership,
            Role,
            SiteAssignment,
            User,
        )

        qc_user = User(
            email="qchead@apex.in",
            password_hash=get_password_hash("QcHeadPass!123"),
            full_name="QC Head",
            account_status="ACTIVE",
            email_verified_at=datetime.now(timezone.utc),
        )
        uow.session.add(qc_user)
        uow.session.flush()
        qc_member = OrganizationMembership(
            user_id=qc_user.id,
            organization_id=org_id,
            membership_status="ACTIVE",
            member_type="EMPLOYEE",
        )
        uow.session.add(qc_member)
        uow.session.flush()
        uow.session.add(
            SiteAssignment(
                organization_id=org_id,
                membership_id=qc_member.id,
                site_id=site_id,
                status="ACTIVE",
                assigned_by_user_id=prod_head["user"].id,
            )
        )
        qc_role = uow.session.scalar(select(Role).where(Role.role_code == "QC_HEAD"))
        assert qc_role is not None
        uow.session.add(
            MembershipRoleAssignment(
                organization_id=org_id,
                membership_id=qc_member.id,
                role_id=qc_role.id,
                scope_type="SITE",
                site_id=site_id,
                status="ACTIVE",
                granted_by_user_id=prod_head["user"].id,
            )
        )

        supplier = BusinessPartner(
            organization_id=org_id,
            partner_code="SUP-API-01",
            legal_name="ChemPure API Ltd",
            partner_type="SUPPLIER",
            primary_email="qa@chempure.in",
        )
        distributor = BusinessPartner(
            organization_id=org_id,
            partner_code="DIST-01",
            legal_name="MedDist Logistics",
            partner_type="DISTRIBUTOR",
            primary_email="ops@meddist.in",
        )
        uow.session.add_all([supplier, distributor])
        uow.session.flush()

        prod_head_uid = prod_head["user"].id
        qc_head_uid = qc_user.id
        supplier_id = supplier.id
        distributor_id = distributor.id

    prod_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(prod_head_uid))}",
        "X-Organization-Id": str(org_id),
    }
    qc_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(qc_head_uid))}",
        "X-Organization-Id": str(org_id),
    }

    # 1. Create Product (DRAFT), update with version bump + stale row_version 409 check, activate
    p_resp = api_client.post(
        "/api/v1/products",
        headers=prod_headers,
        json={
            "site_id": str(site_id),
            "product_code": "PRD-PARA-500",
            "brand_name": "ApexDol 500",
            "generic_name": "Paracetamol",
            "dosage_form": "TABLET",
            "strength_value": "500.0000",
            "strength_unit": "MG",
            "shelf_life_months": 36,
        },
    )
    assert p_resp.status_code == 201
    product = p_resp.json()
    product_id = product["id"]
    assert product["product_status"] == "DRAFT"
    assert product["version_number"] == 1

    # Stale row_version -> 409
    assert (
        api_client.patch(
            f"/api/v1/products/{product_id}",
            headers=prod_headers,
            json={"row_version": 99, "shelf_life_months": 24, "reason": "Stale version check"},
        ).status_code
        == 409
    )

    p_upd = api_client.patch(
        f"/api/v1/products/{product_id}",
        headers=prod_headers,
        json={"row_version": 1, "shelf_life_months": 24, "reason": "Revised stability shelf life"},
    )
    assert p_upd.status_code == 200
    assert p_upd.json()["version_number"] == 2
    assert p_upd.json()["row_version"] == 2

    p_act = api_client.post(
        f"/api/v1/products/{product_id}/status",
        headers=prod_headers,
        json={"row_version": 2, "target_status": "ACTIVE", "reason": "QA/Production master approved"},
    )
    assert p_act.status_code == 200
    assert p_act.json()["product_status"] == "ACTIVE"

    # Product Regulatory Authorization
    auth_resp = api_client.post(
        f"/api/v1/products/{product_id}/authorizations",
        headers=prod_headers,
        json={
            "country_code": "IN",
            "authorization_number": "CDSCO-MA-2026-88",
            "issuing_authority": "CDSCO",
            "approved_on": "2026-01-10",
            "regulatory_status": "ACTIVE",
        },
    )
    assert auth_resp.status_code == 201
    assert len(api_client.get(f"/api/v1/products/{product_id}/authorizations", headers=qc_headers).json()) == 1

    # 2. Material creation rejects non-SUPPLIER BusinessPartner (422)
    bad_mat = api_client.post(
        "/api/v1/materials",
        headers=prod_headers,
        json={
            "material_code": "API-PARA-01",
            "material_name": "Paracetamol IP Grade",
            "material_type": "API",
            "default_unit_code": "KG",
            "default_supplier_partner_id": str(distributor_id),
        },
    )
    assert bad_mat.status_code == 422

    m_resp = api_client.post(
        "/api/v1/materials",
        headers=prod_headers,
        json={
            "material_code": "API-PARA-01",
            "material_name": "Paracetamol IP Grade",
            "material_type": "API",
            "pharmacopoeia_grade": "IP",
            "default_unit_code": "KG",
            "retest_period_days": 365,
            "default_supplier_partner_id": str(supplier_id),
        },
    )
    assert m_resp.status_code == 201
    mat_id = m_resp.json()["id"]

    m_act = api_client.post(
        f"/api/v1/materials/{mat_id}/status",
        headers=prod_headers,
        json={
            "row_version": 1,
            "material_status": "ACTIVE",
            "qualification_status": "APPROVED",
            "reason": "Vendor qualification audit passed",
        },
    )
    assert m_act.status_code == 200
    assert m_act.json()["material_status"] == "ACTIVE"
    assert m_act.json()["qualification_status"] == "APPROVED"

    # Link Material in Product BOM
    bom_resp = api_client.post(
        f"/api/v1/products/{product_id}/materials",
        headers=prod_headers,
        json={
            "material_id": mat_id,
            "quantity_per_unit": "0.5000",
            "unit_code": "G",
            "function_role": "ACTIVE_INGREDIENT",
            "is_critical_material": True,
        },
    )
    assert bom_resp.status_code == 201
    assert len(api_client.get(f"/api/v1/products/{product_id}/materials", headers=qc_headers).json()) == 1

    # 3. SupplierLot receipt (QUARANTINED) -> Released by distinct QC_HEAD (maker-checker!)
    lot_resp = api_client.post(
        "/api/v1/supplier-lots",
        headers=prod_headers,
        json={
            "site_id": str(site_id),
            "material_id": mat_id,
            "supplier_partner_id": str(supplier_id),
            "supplier_lot_number": "CP-2026-991",
            "received_quantity": "250.0000",
            "unit_code": "KG",
            "received_date": str(date(2026, 2, 1)),
            "manufacturing_date": str(date(2026, 1, 15)),
            "expiry_or_retest_date": str(date(2028, 1, 15)),
        },
    )
    assert lot_resp.status_code == 201
    lot = lot_resp.json()
    lot_id = lot["id"]
    assert lot["lot_status"] == "QUARANTINED"
    assert lot["remaining_quantity"] == "250.0000"
    assert lot["internal_lot_number"].startswith("SLOT-")

    # QC_HEAD releases lot cleanly
    rel = api_client.post(
        f"/api/v1/supplier-lots/{lot_id}/status",
        headers=qc_headers,
        json={"row_version": 1, "target_status": "RELEASED", "reason": "COA & ID test passed"},
    )
    assert rel.status_code == 200
    assert rel.json()["lot_status"] == "RELEASED"
    assert rel.json()["approved_by_membership_id"] is not None

    # Verify ApprovalRecord and OutboxEvents created
    db = session_factory()
    try:
        appr = db.scalars(select(ApprovalRecord).where(ApprovalRecord.entity_type == "supplier_lots")).first()
        assert appr is not None and appr.decision == "APPROVED"
        ev_types = {e.event_type for e in db.scalars(select(OutboxEvent)).all()}
        assert {"ProductCreated", "ProductStatusChanged", "MaterialCreated", "MaterialStatusChanged", "SupplierLotStatusChanged"}.issubset(ev_types)
    finally:
        db.close()
