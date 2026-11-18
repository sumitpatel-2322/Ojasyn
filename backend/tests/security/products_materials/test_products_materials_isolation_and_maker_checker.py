from datetime import date, datetime, timezone

from sqlalchemy import select

from app.core.database import UnitOfWork
from app.core.security import create_access_token, get_password_hash
from app.models.domain.actors import BusinessPartner
from app.models.domain.governance import SecurityEvent
from app.models.domain.identity import (
    MembershipRoleAssignment,
    OrganizationMembership,
    Role,
    SiteAssignment,
    User,
)
from app.models.domain.tenancy import OrganizationAddress, Site
from tests.conftest import create_seeded_tenant_actor


def test_cross_tenant_site_scope_and_supplier_lot_maker_checker(api_client, sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        org_a = create_seeded_tenant_actor(
            uow.session,
            org_name="Alpha Meds",
            site_code="AM-01",
            email="prod@alpha.in",
            role_code="PRODUCTION_HEAD",
            scope_type="SITE",
        )
        org_b = create_seeded_tenant_actor(
            uow.session,
            org_name="Beta Meds",
            site_code="BM-01",
            email="prod@beta.in",
            role_code="PRODUCTION_HEAD",
            scope_type="SITE",
        )
        org_a_id = org_a["organization"].id
        site_a1_id = org_a["site"].id

        # Unassigned second active site in Org A
        addr2 = OrganizationAddress(
            organization_id=org_a_id,
            line_1="Plot 88",
            city="Indore",
            postal_code="452001",
            country_code="IN",
            is_primary=False,
        )
        uow.session.add(addr2)
        uow.session.flush()
        site_a2 = Site(
            organization_id=org_a_id,
            site_code="AM-02",
            site_name="Indore Unit 2",
            site_type="API",
            address_id=addr2.id,
            status="ACTIVE",
        )
        uow.session.add(site_a2)
        uow.session.flush()
        site_a2_id = site_a2.id

        # Second ORG_ADMIN in Org A who owns site_a2
        org_a_admin = User(
            email="admin@alpha.in",
            password_hash=get_password_hash("AdminPass!123"),
            full_name="Alpha Admin",
            account_status="ACTIVE",
            email_verified_at=datetime.now(timezone.utc),
        )
        uow.session.add(org_a_admin)
        uow.session.flush()
        admin_m = OrganizationMembership(
            user_id=org_a_admin.id,
            organization_id=org_a_id,
            membership_status="ACTIVE",
            member_type="EMPLOYEE",
        )
        uow.session.add(admin_m)
        uow.session.flush()
        uow.session.add(
            SiteAssignment(
                organization_id=org_a_id,
                membership_id=admin_m.id,
                site_id=site_a2_id,
                status="ACTIVE",
                assigned_by_user_id=org_a["user"].id,
            )
        )
        admin_role = uow.session.scalar(select(Role).where(Role.role_code == "ORG_ADMIN"))
        prod_role = uow.session.scalar(select(Role).where(Role.role_code == "PRODUCTION_HEAD"))
        assert admin_role is not None and prod_role is not None
        uow.session.add_all(
            [
                MembershipRoleAssignment(
                    organization_id=org_a_id,
                    membership_id=admin_m.id,
                    role_id=admin_role.id,
                    scope_type="ORGANIZATION",
                    site_id=None,
                    status="ACTIVE",
                    granted_by_user_id=org_a["user"].id,
                ),
                MembershipRoleAssignment(
                    organization_id=org_a_id,
                    membership_id=admin_m.id,
                    role_id=prod_role.id,
                    scope_type="SITE",
                    site_id=site_a2_id,
                    status="ACTIVE",
                    granted_by_user_id=org_a["user"].id,
                ),
            ]
        )

        sup_a = BusinessPartner(
            organization_id=org_a_id,
            partner_code="SUP-A",
            legal_name="Alpha Supplier",
            partner_type="SUPPLIER",
            primary_email="sup@alpha.in",
        )
        sup_b = BusinessPartner(
            organization_id=org_b["organization"].id,
            partner_code="SUP-B",
            legal_name="Beta Supplier",
            partner_type="SUPPLIER",
            primary_email="sup@beta.in",
        )
        uow.session.add_all([sup_a, sup_b])
        uow.session.flush()

        a_uid = org_a["user"].id
        a_admin_uid = org_a_admin.id
        b_uid = org_b["user"].id
        org_b_id = org_b["organization"].id
        sup_a_id = sup_a.id
        sup_b_id = sup_b.id

    a_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(a_uid))}",
        "X-Organization-Id": str(org_a_id),
    }
    a_admin_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(a_admin_uid))}",
        "X-Organization-Id": str(org_a_id),
    }
    b_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(b_uid))}",
        "X-Organization-Id": str(org_b_id),
    }

    # 1. Cross-tenant supplier partner reference in Material rejected (404)
    assert (
        api_client.post(
            "/api/v1/materials",
            headers=a_headers,
            json={
                "material_code": "MAT-X",
                "material_name": "Cross Tenant Probe",
                "material_type": "EXCIPIENT",
                "default_unit_code": "KG",
                "default_supplier_partner_id": str(sup_b_id),
            },
        ).status_code
        == 404
    )

    # Create valid Material & Product in site_a1 and SupplierLot by a_uid
    mat = api_client.post(
        "/api/v1/materials",
        headers=a_headers,
        json={
            "material_code": "MAT-STARCH",
            "material_name": "Maize Starch",
            "material_type": "EXCIPIENT",
            "default_unit_code": "KG",
            "default_supplier_partner_id": str(sup_a_id),
        },
    ).json()
    prod_site2 = api_client.post(
        "/api/v1/products",
        headers=a_admin_headers,
        json={
            "site_id": str(site_a2_id),
            "product_code": "PRD-IND-01",
            "generic_name": "Metformin",
            "dosage_form": "TABLET",
        },
    ).json()
    lot = api_client.post(
        "/api/v1/supplier-lots",
        headers=a_headers,
        json={
            "site_id": str(site_a1_id),
            "material_id": mat["id"],
            "supplier_partner_id": str(sup_a_id),
            "supplier_lot_number": "LOT-A-01",
            "received_quantity": "100.0000",
            "unit_code": "KG",
            "received_date": str(date(2026, 2, 10)),
        },
    ).json()

    # 2. Same user (a_uid) CANNOT release or reject their own received supplier lot -> 403 SELF_APPROVAL_BLOCKED
    self_rel = api_client.post(
        f"/api/v1/supplier-lots/{lot['id']}/status",
        headers=a_headers,
        json={"row_version": 1, "target_status": "RELEASED", "reason": "Self release attempt"},
    )
    assert self_rel.status_code == 403

    # 3. Cross-tenant Org B reads return 404 (no existence leakage)
    assert api_client.get(f"/api/v1/products/{prod_site2['id']}", headers=b_headers).status_code == 404
    assert api_client.get(f"/api/v1/materials/{mat['id']}", headers=b_headers).status_code == 404
    assert api_client.get(f"/api/v1/supplier-lots/{lot['id']}", headers=b_headers).status_code == 404

    # 4. Same-org site-restricted user (a_uid on site_a1) CANNOT access site_a2 product -> 403
    assert api_client.get(f"/api/v1/products/{prod_site2['id']}", headers=a_headers).status_code == 403

    db = session_factory()
    try:
        assert (
            db.scalars(
                select(SecurityEvent).where(SecurityEvent.event_type == "SELF_APPROVAL_BLOCKED")
            ).first()
            is not None
        )
    finally:
        db.close()
