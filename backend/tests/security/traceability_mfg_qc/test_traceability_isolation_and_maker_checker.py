from datetime import date
from decimal import Decimal
import uuid

from sqlalchemy import select

from app.core.database import UnitOfWork
from app.core.security import create_access_token, get_password_hash
from app.domain.enums import (
    AssignmentStatus,
    AssignmentType,
    MembershipStatus,
    MemberType,
    RoleAssignmentScopeType,
    RoleAssignmentStatus,
    SiteStatus,
    SiteType,
    UserAccountStatus,
)
from app.models.domain.actors import BusinessPartner, PartnerSite
from app.models.domain.governance import SecurityEvent
from app.models.domain.identity import (
    MembershipRoleAssignment,
    OrganizationMembership,
    Role,
    SiteAssignment,
    User,
)
from app.models.domain.products_materials import Material, Product, SupplierLot
from app.models.domain.tenancy import Site
from app.models.domain.traceability import Batch, Shipment
from tests.conftest import create_seeded_tenant_actor


def test_cross_tenant_site_scope_rbac_and_maker_checker_security(api_client, sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        org_a_seed = create_seeded_tenant_actor(
            uow.session,
            org_name="Org Alpha Labs",
            site_code="ALP-1",
            email="prod.alp1@alpha.in",
            role_code="PRODUCTION_HEAD",
            scope_type="SITE",
        )
        org_b_seed = create_seeded_tenant_actor(
            uow.session,
            org_name="Org Beta Labs",
            site_code="BET-1",
            email="qa.bet1@beta.in",
            role_code="QA_OFFICER",
            scope_type="ORGANIZATION",
        )

        org_a = org_a_seed["organization"]
        site_a1 = org_a_seed["site"]
        prod_a1_user = org_a_seed["user"]
        prod_a1_member = org_a_seed["membership"]

        # Second site in Org A (unauthorized for prod_a1_user)
        site_a2 = Site(
            organization_id=org_a.id,
            site_code="ALP-2",
            site_name="Org Alpha Site 2",
            site_type=SiteType.FDF.value,
            address_id=org_a.registered_address_id,
            status=SiteStatus.ACTIVE.value,
            is_primary_site=False,
        )
        uow.session.add(site_a2)
        uow.session.flush()

        # Org-wide batch disposition approver in Org A (different user & same user with extra role test)
        approver_a_user = User(
            email="approver.alp@alpha.in",
            full_name="Approver Alpha",
            password_hash=get_password_hash("StrongPass!123"),
            account_status=UserAccountStatus.ACTIVE.value,
        )
        uow.session.add(approver_a_user)
        uow.session.flush()
        approver_a_member = OrganizationMembership(
            user_id=approver_a_user.id,
            organization_id=org_a.id,
            member_type=MemberType.EMPLOYEE.value,
            membership_status=MembershipStatus.ACTIVE.value,
            employee_code="EMP-APP-A",
        )
        uow.session.add(approver_a_member)
        uow.session.flush()
        uow.session.add(
            SiteAssignment(
                organization_id=org_a.id,
                membership_id=approver_a_member.id,
                site_id=site_a1.id,
                assignment_type=AssignmentType.PRIMARY.value,
                status=AssignmentStatus.ACTIVE.value,
                assigned_by_user_id=approver_a_user.id,
            )
        )
        role_approver = uow.session.query(Role).filter(Role.role_code == "BATCH_DISPOSITION_APPROVER").one()
        uow.session.add(
            MembershipRoleAssignment(
                organization_id=org_a.id,
                membership_id=approver_a_member.id,
                role_id=role_approver.id,
                scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
                granted_by_user_id=approver_a_user.id,
                approved_by_user_id=approver_a_user.id,
                status=RoleAssignmentStatus.ACTIVE.value,
            )
        )

        # Second actor in Org B with BATCH_DISPOSITION_APPROVER
        role_prod_b = uow.session.query(Role).filter(Role.role_code == "PRODUCTION_HEAD").one()
        uow.session.add(
            MembershipRoleAssignment(
                organization_id=org_b_seed["organization"].id,
                membership_id=org_b_seed["membership"].id,
                role_id=role_prod_b.id,
                scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
                granted_by_user_id=org_b_seed["user"].id,
                approved_by_user_id=org_b_seed["user"].id,
                status=RoleAssignmentStatus.ACTIVE.value,
            )
        )

        prod_a = Product(
            organization_id=org_a.id,
            product_code="PRD-A-01",
            generic_name="Paracetamol",
            dosage_form="TABLET",
            product_status="ACTIVE",
        )
        uow.session.add(prod_a)
        uow.session.flush()

        batch_a1 = Batch(
            organization_id=org_a.id,
            site_id=site_a1.id,
            product_id=prod_a.id,
            batch_number="ALP-B01",
            manufacturing_date=date(2026, 1, 1),
            expiry_date=date(2028, 1, 1),
            batch_size_value=Decimal("500.0000"),
            batch_size_unit="BOXES",
            batch_status="UNDER_QUALITY_REVIEW",
            quality_review_status="IN_REVIEW",
        )
        batch_a2 = Batch(
            organization_id=org_a.id,
            site_id=site_a2.id,
            product_id=prod_a.id,
            batch_number="ALP-B02",
            manufacturing_date=date(2026, 1, 2),
            expiry_date=date(2028, 1, 2),
            batch_size_value=Decimal("500.0000"),
            batch_size_unit="BOXES",
            batch_status="RELEASED",
            quality_review_status="APPROVED",
        )
        uow.session.add_all([batch_a1, batch_a2])

        partner_a = BusinessPartner(
            organization_id=org_a.id,
            partner_code="DIST-A",
            legal_name="Alpha Logistics",
            partner_type="DISTRIBUTOR",
            primary_email="d@alpha.in",
            status="ACTIVE",
        )
        uow.session.add(partner_a)
        uow.session.flush()
        psite_a = PartnerSite(
            organization_id=org_a.id,
            partner_id=partner_a.id,
            site_code="PS-A1",
            site_name="Alpha Hub",
            address_line_1="1 Main Rd",
            city="Pune",
            postal_code="411001",
            country_code="IN",
            status="ACTIVE",
        )
        uow.session.add(psite_a)
        uow.session.flush()

        ship_a2 = Shipment(
            organization_id=org_a.id,
            site_id=site_a2.id,
            shipment_number="ALP-SHP-2026-000001",
            partner_id=partner_a.id,
            partner_site_id=psite_a.id,
            shipment_status="DRAFT",
        )
        uow.session.add(ship_a2)
        uow.session.flush()

        org_a_id = org_a.id
        org_b_id = org_b_seed["organization"].id
        site_a2_id = site_a2.id
        prod_a_id = prod_a.id
        batch_a1_id = batch_a1.id
        batch_a2_id = batch_a2.id
        ship_a2_id = ship_a2.id
        prod_a1_uid = prod_a1_user.id
        approver_a_uid = approver_a_user.id
        approver_a_mid = approver_a_member.id
        org_b_uid = org_b_seed["user"].id
        org_b_site_id = org_b_seed["site"].id

    headers_a_site1 = {
        "Authorization": f"Bearer {create_access_token(subject=str(prod_a1_uid))}",
        "X-Organization-Id": str(org_a_id),
    }
    headers_a_approver = {
        "Authorization": f"Bearer {create_access_token(subject=str(approver_a_uid))}",
        "X-Organization-Id": str(org_a_id),
    }
    headers_b = {
        "Authorization": f"Bearer {create_access_token(subject=str(org_b_uid))}",
        "X-Organization-Id": str(org_b_id),
    }

    # 1. Cross-tenant isolation: Org B gets 404 on Org A Batch, Shipment, Traceability, Complaint Product/Batch
    assert api_client.get(f"/api/v1/batches/{batch_a1_id}", headers=headers_b).status_code == 404
    assert api_client.get(f"/api/v1/batches/{batch_a1_id}/traceability", headers=headers_b).status_code == 404
    assert api_client.get(f"/api/v1/distribution/shipments/{ship_a2_id}", headers=headers_b).status_code == 404

    r_cmp_xtenant_prod = api_client.post(
        "/api/v1/complaints",
        headers=headers_b,
        json={
            "site_id": str(org_b_site_id),
            "product_id": str(prod_a_id),
            "description": "Attempting to link another tenant product.",
        },
    )
    assert r_cmp_xtenant_prod.status_code == 404

    r_cmp_b = api_client.post(
        "/api/v1/complaints",
        headers=headers_b,
        json={
            "site_id": str(org_b_site_id),
            "description": "Valid Org B complaint.",
        },
    )
    assert r_cmp_b.status_code == 201
    cmp_b_id = r_cmp_b.json()["id"]
    r_link_xtenant_batch = api_client.post(
        f"/api/v1/complaints/{cmp_b_id}/batch-candidates/resolve",
        headers=headers_b,
        json={"action": "MANUAL_LINK", "batch_id": str(batch_a1_id)},
    )
    assert r_link_xtenant_batch.status_code == 404

    # 2. Site-scope enforcement (403 SITE_SCOPE_DENIED) + SecurityEvent audit
    r_site_read = api_client.get(f"/api/v1/batches/{batch_a2_id}", headers=headers_a_site1)
    assert r_site_read.status_code == 403
    assert r_site_read.json()["error_code"] == "SITE_SCOPE_DENIED"

    r_site_create = api_client.post(
        "/api/v1/batches",
        headers=headers_a_site1,
        json={
            "site_id": str(site_a2_id),
            "product_id": str(prod_a_id),
            "batch_number": "ALP-B-DENIED",
            "manufacturing_date": "2026-03-01",
            "expiry_date": "2028-03-01",
            "batch_size_value": "100.0000",
            "batch_size_unit": "BOXES",
        },
    )
    assert r_site_create.status_code == 403

    # 3. RBAC enforcement: PRODUCTION_HEAD lacks batch.release & shipment.manage
    r_rbac_disp = api_client.post(
        f"/api/v1/batches/{batch_a1_id}/disposition",
        headers=headers_a_site1,
        json={
            "row_version": 1,
            "disposition_decision": "RELEASED",
            "proposed_by_membership_id": str(approver_a_mid),
            "checklist": {"bmr_reviewed": True, "qc_passed": True, "open_critical_deviations": False},
        },
    )
    assert r_rbac_disp.status_code == 403

    # 4. Maker-checker violation on disposition -> 403 + MAKER_CHECKER_VIOLATION security event
    r_mc = api_client.post(
        f"/api/v1/batches/{batch_a1_id}/disposition",
        headers=headers_a_approver,
        json={
            "row_version": 1,
            "disposition_decision": "RELEASED",
            "proposed_by_membership_id": str(approver_a_mid),
            "checklist": {"bmr_reviewed": True, "qc_passed": True, "open_critical_deviations": False},
        },
    )
    assert r_mc.status_code == 403
    assert r_mc.json()["error_code"] == "MAKER_CHECKER_VIOLATION"

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        sec_types = {
            ev.event_type
            for ev in uow.session.scalars(
                select(SecurityEvent).where(SecurityEvent.organization_id == org_a_id)
            ).all()
        }
        assert "SITE_SCOPE_VIOLATION" in sec_types
        assert "MAKER_CHECKER_VIOLATION" in sec_types


def test_cross_site_shipment_consumption_relationship_and_verify_rbac_audit(
    api_client, sqlite_db
) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        site1_seed = create_seeded_tenant_actor(
            uow.session,
            org_name="Helios Pharma",
            site_code="HEL-S1",
            email="prod.s1@helios.in",
            role_code="PRODUCTION_HEAD",
            scope_type="SITE",
        )
        org = site1_seed["organization"]
        site1 = site1_seed["site"]
        prod_user = site1_seed["user"]
        prod_member = site1_seed["membership"]

        # Grant WAREHOUSE_MANAGER (SITE scope on site1) + ORG_ADMIN (separate user) for cross-site checks
        wh_role = uow.session.query(Role).filter(Role.role_code == "WAREHOUSE_MANAGER").one()
        uow.session.add(
            MembershipRoleAssignment(
                organization_id=org.id,
                membership_id=prod_member.id,
                role_id=wh_role.id,
                scope_type=RoleAssignmentScopeType.SITE.value,
                site_id=site1.id,
                granted_by_user_id=prod_user.id,
                approved_by_user_id=prod_user.id,
                status=RoleAssignmentStatus.ACTIVE.value,
            )
        )

        admin_user = User(
            email="org.admin@helios.in",
            full_name="Helios Org Admin",
            password_hash=get_password_hash("StrongPass!123"),
            account_status=UserAccountStatus.ACTIVE.value,
        )
        uow.session.add(admin_user)
        uow.session.flush()
        admin_member = OrganizationMembership(
            user_id=admin_user.id,
            organization_id=org.id,
            member_type=MemberType.EMPLOYEE.value,
            membership_status=MembershipStatus.ACTIVE.value,
            employee_code="EMP-HEL-ADMIN",
        )
        uow.session.add(admin_member)
        uow.session.flush()
        admin_role = uow.session.query(Role).filter(Role.role_code == "ORG_ADMIN").one()
        prod_head_role = uow.session.query(Role).filter(Role.role_code == "PRODUCTION_HEAD").one()
        uow.session.add_all(
            [
                MembershipRoleAssignment(
                    organization_id=org.id,
                    membership_id=admin_member.id,
                    role_id=admin_role.id,
                    scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
                    granted_by_user_id=admin_user.id,
                    approved_by_user_id=admin_user.id,
                    status=RoleAssignmentStatus.ACTIVE.value,
                ),
                MembershipRoleAssignment(
                    organization_id=org.id,
                    membership_id=admin_member.id,
                    role_id=wh_role.id,
                    scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
                    granted_by_user_id=admin_user.id,
                    approved_by_user_id=admin_user.id,
                    status=RoleAssignmentStatus.ACTIVE.value,
                ),
                MembershipRoleAssignment(
                    organization_id=org.id,
                    membership_id=admin_member.id,
                    role_id=prod_head_role.id,
                    scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
                    granted_by_user_id=admin_user.id,
                    approved_by_user_id=admin_user.id,
                    status=RoleAssignmentStatus.ACTIVE.value,
                ),
            ]
        )

        # User lacking batch.create & batch.release (QA_OFFICER) to test verify RBAC audit
        qa_user = User(
            email="qa.only@helios.in",
            full_name="Helios QA Officer",
            password_hash=get_password_hash("StrongPass!123"),
            account_status=UserAccountStatus.ACTIVE.value,
        )
        uow.session.add(qa_user)
        uow.session.flush()
        qa_member = OrganizationMembership(
            user_id=qa_user.id,
            organization_id=org.id,
            member_type=MemberType.EMPLOYEE.value,
            membership_status=MembershipStatus.ACTIVE.value,
            employee_code="EMP-HEL-QA",
        )
        uow.session.add(qa_member)
        uow.session.flush()
        qa_role = uow.session.query(Role).filter(Role.role_code == "QA_OFFICER").one()
        uow.session.add(
            MembershipRoleAssignment(
                organization_id=org.id,
                membership_id=qa_member.id,
                role_id=qa_role.id,
                scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
                granted_by_user_id=qa_user.id,
                approved_by_user_id=qa_user.id,
                status=RoleAssignmentStatus.ACTIVE.value,
            )
        )

        site2 = Site(
            organization_id=org.id,
            site_code="HEL-S2",
            site_name="Helios Site 2",
            site_type=SiteType.FDF.value,
            address_id=org.registered_address_id,
            status=SiteStatus.ACTIVE.value,
            is_primary_site=False,
        )
        uow.session.add(site2)
        uow.session.flush()

        product = Product(
            organization_id=org.id,
            product_code="HEL-PRD-1",
            generic_name="Cetirizine",
            dosage_form="TABLET",
            product_status="ACTIVE",
        )
        uow.session.add(product)
        uow.session.flush()

        batch_s1 = Batch(
            organization_id=org.id,
            site_id=site1.id,
            product_id=product.id,
            batch_number="HEL-B-S1",
            manufacturing_date=date(2026, 1, 1),
            expiry_date=date(2028, 1, 1),
            batch_size_value=Decimal("200.0000"),
            batch_size_unit="BOXES",
            batch_status="IN_PROCESS",
        )
        batch_s2 = Batch(
            organization_id=org.id,
            site_id=site2.id,
            product_id=product.id,
            batch_number="HEL-B-S2",
            manufacturing_date=date(2026, 1, 1),
            expiry_date=date(2028, 1, 1),
            batch_size_value=Decimal("200.0000"),
            batch_size_unit="BOXES",
            batch_status="RELEASED",
            quality_review_status="APPROVED",
        )
        uow.session.add_all([batch_s1, batch_s2])

        sup = BusinessPartner(
            organization_id=org.id,
            partner_code="HEL-SUP",
            legal_name="Helios Supplier",
            partner_type="SUPPLIER",
            primary_email="sup@helios.in",
            status="ACTIVE",
        )
        dist = BusinessPartner(
            organization_id=org.id,
            partner_code="HEL-DIST",
            legal_name="Helios Distributor",
            partner_type="DISTRIBUTOR",
            primary_email="dist@helios.in",
            status="ACTIVE",
        )
        uow.session.add_all([sup, dist])
        uow.session.flush()

        psite = PartnerSite(
            organization_id=org.id,
            partner_id=dist.id,
            site_code="HEL-PS1",
            site_name="Helios Hub",
            address_line_1="10 Road",
            city="Pune",
            postal_code="411001",
            country_code="IN",
            status="ACTIVE",
        )
        mat = Material(
            organization_id=org.id,
            material_code="HEL-MAT-1",
            material_name="Cetirizine API",
            material_type="API",
            default_unit_code="KG",
            qualification_status="APPROVED",
            material_status="ACTIVE",
            default_supplier_partner_id=sup.id,
        )
        uow.session.add_all([psite, mat])
        uow.session.flush()

        lot_s2 = SupplierLot(
            organization_id=org.id,
            site_id=site2.id,
            material_id=mat.id,
            supplier_partner_id=sup.id,
            internal_lot_number="HEL-LOT-S2",
            supplier_lot_number="V-HEL-S2",
            received_quantity=Decimal("50.0000"),
            remaining_quantity=Decimal("50.0000"),
            unit_code="KG",
            received_date=date(2026, 1, 5),
            lot_status="RELEASED",
        )
        uow.session.add(lot_s2)
        uow.session.flush()

        org_id = org.id
        site1_id, site2_id = site1.id, site2.id
        batch_s1_id, batch_s2_id = batch_s1.id, batch_s2.id
        dist_id, psite_id = dist.id, psite.id
        mat_id, lot_s2_id = mat.id, lot_s2.id
        prod_uid, admin_uid, qa_uid = prod_user.id, admin_user.id, qa_user.id

    h_site1 = {
        "Authorization": f"Bearer {create_access_token(subject=str(prod_uid))}",
        "X-Organization-Id": str(org_id),
    }
    h_admin = {
        "Authorization": f"Bearer {create_access_token(subject=str(admin_uid))}",
        "X-Organization-Id": str(org_id),
    }
    h_qa = {
        "Authorization": f"Bearer {create_access_token(subject=str(qa_uid))}",
        "X-Organization-Id": str(org_id),
    }

    # 1. Cross-site batch shipment: 403 SITE_SCOPE_DENIED for site1-scoped user, 422 CROSS_SITE_BATCH_ALLOCATION for org-wide admin
    r_ship_s1 = api_client.post(
        "/api/v1/distribution/shipments",
        headers=h_site1,
        json={
            "site_id": str(site1_id),
            "partner_id": str(dist_id),
            "partner_site_id": str(psite_id),
            "lines": [{"batch_id": str(batch_s2_id), "quantity": "50.0000", "unit_code": "BOXES"}],
        },
    )
    assert r_ship_s1.status_code == 403
    assert r_ship_s1.json()["error_code"] == "SITE_SCOPE_DENIED"

    r_ship_admin_xsite = api_client.post(
        "/api/v1/distribution/shipments",
        headers=h_admin,
        json={
            "site_id": str(site1_id),
            "partner_id": str(dist_id),
            "partner_site_id": str(psite_id),
            "lines": [{"batch_id": str(batch_s2_id), "quantity": "50.0000", "unit_code": "BOXES"}],
        },
    )
    assert r_ship_admin_xsite.status_code == 422
    assert r_ship_admin_xsite.json()["error_code"] == "CROSS_SITE_BATCH_ALLOCATION"

    # 2. consume_material supplier_lot site check: 403 for site1 user, 422 CROSS_SITE_SUPPLIER_LOT for org-wide admin
    r_cons_s1 = api_client.post(
        f"/api/v1/batches/{batch_s1_id}/materials",
        headers=h_site1,
        json={
            "material_id": str(mat_id),
            "supplier_lot_id": str(lot_s2_id),
            "quantity_consumed": "5.0000",
            "unit_code": "KG",
        },
    )
    assert r_cons_s1.status_code == 403
    assert r_cons_s1.json()["error_code"] == "SITE_SCOPE_DENIED"

    r_cons_admin = api_client.post(
        f"/api/v1/batches/{batch_s1_id}/materials",
        headers=h_admin,
        json={
            "material_id": str(mat_id),
            "supplier_lot_id": str(lot_s2_id),
            "quantity_consumed": "5.0000",
            "unit_code": "KG",
        },
    )
    assert r_cons_admin.status_code == 422
    assert r_cons_admin.json()["error_code"] == "CROSS_SITE_SUPPLIER_LOT"

    # 3. link_batch_relationship site isolation: site1 user cannot link site2 batch
    r_rel_xsite = api_client.post(
        f"/api/v1/batches/{batch_s1_id}/relationships",
        headers=h_site1,
        json={
            "related_batch_id": str(batch_s2_id),
            "direction": "CHILD",
            "relation_type": "SPLIT",
        },
    )
    assert r_rel_xsite.status_code == 403
    assert r_rel_xsite.json()["error_code"] == "SITE_SCOPE_DENIED"

    # 4. verify_material_consumption RBAC denial logs RBAC_PERMISSION_DENIED SecurityEvent
    r_verify_rbac = api_client.post(
        f"/api/v1/batches/{batch_s1_id}/materials/{uuid.uuid4()}/verify",
        headers=h_qa,
        json={"notes": "Unauthorized verify"},
    )
    assert r_verify_rbac.status_code == 403
    assert r_verify_rbac.json()["error_code"] == "INSUFFICIENT_PERMISSION"

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        sec_types = {
            ev.event_type
            for ev in uow.session.scalars(
                select(SecurityEvent).where(SecurityEvent.organization_id == org_id)
            ).all()
        }
        assert "SITE_SCOPE_VIOLATION" in sec_types
        assert "RBAC_PERMISSION_DENIED" in sec_types
