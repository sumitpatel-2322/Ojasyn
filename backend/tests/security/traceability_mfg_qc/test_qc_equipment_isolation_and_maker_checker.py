from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

try:
    import app.models.domain.sops  # noqa: F401
except ImportError:
    pass
try:
    import app.models.domain.manufacturing_execution  # noqa: F401
except ImportError:
    pass
import app.models.domain.qc_equipment  # noqa: F401

from app.api.v1.qc_equipment import router as qc_equipment_router
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
from app.models.domain.governance import SecurityEvent
from app.models.domain.identity import (
    MembershipRoleAssignment,
    OrganizationMembership,
    Role,
    SiteAssignment,
    User,
)
from app.models.domain.products_materials import Product
from app.models.domain.qc_equipment import (
    CalibrationRecord,
    Equipment,
    QCSample,
    QCTestDefinition,
    QCTestResult,
)
from app.models.domain.tenancy import Site
from app.models.domain.traceability import Batch
from tests.conftest import create_seeded_tenant_actor


def _ensure_qc_router_mounted(api_client) -> None:
    existing_paths = {getattr(r, "path", "") for r in api_client.app.routes}
    if "/api/v1/equipment" not in existing_paths:
        api_client.app.include_router(qc_equipment_router, prefix="/api/v1")


def test_qc_equipment_cross_tenant_site_scope_rbac_maker_checker_and_immutability(
    api_client, sqlite_db
) -> None:
    _ensure_qc_router_mounted(api_client)
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        org_a_seed = create_seeded_tenant_actor(
            uow.session,
            org_name="Org Alpha QC",
            site_code="ALP-QC1",
            email="qc.head.s1@alpha.in",
            role_code="QC_HEAD",
            scope_type="SITE",
        )
        org_b_seed = create_seeded_tenant_actor(
            uow.session,
            org_name="Org Beta QC",
            site_code="BET-QC1",
            email="qc.head@beta.in",
            role_code="QC_HEAD",
            scope_type="SITE",
        )

        org_a = org_a_seed["organization"]
        site_a1 = org_a_seed["site"]
        qc_head_a1_user = org_a_seed["user"]
        qc_head_a1_member = org_a_seed["membership"]

        # Unassigned Site 2 in Org A
        site_a2 = Site(
            organization_id=org_a.id,
            site_code="ALP-QC2",
            site_name="Org Alpha Site 2",
            site_type=SiteType.QC_LAB.value,
            address_id=org_a.registered_address_id,
            status=SiteStatus.ACTIVE.value,
            is_primary_site=False,
        )
        uow.session.add(site_a2)
        uow.session.flush()

        # Second distinct reviewer in Org A (QA_HEAD org-wide)
        qa_head_user = User(
            email="qa.head@alpha.in",
            full_name="QA Head Alpha",
            password_hash=get_password_hash("StrongPass!123"),
            account_status=UserAccountStatus.ACTIVE.value,
        )
        # QC Analyst in Org A Site 1 (lacks qc_def.manage, qc_result.approve, equipment.manage)
        analyst_user = User(
            email="qc.analyst@alpha.in",
            full_name="QC Analyst Alpha",
            password_hash=get_password_hash("StrongPass!123"),
            account_status=UserAccountStatus.ACTIVE.value,
        )
        uow.session.add_all([qa_head_user, analyst_user])
        uow.session.flush()

        qa_head_member = OrganizationMembership(
            user_id=qa_head_user.id,
            organization_id=org_a.id,
            member_type=MemberType.EMPLOYEE.value,
            membership_status=MembershipStatus.ACTIVE.value,
            employee_code="EMP-QA-HEAD-A",
        )
        analyst_member = OrganizationMembership(
            user_id=analyst_user.id,
            organization_id=org_a.id,
            member_type=MemberType.EMPLOYEE.value,
            membership_status=MembershipStatus.ACTIVE.value,
            employee_code="EMP-QC-ANA-A",
        )
        uow.session.add_all([qa_head_member, analyst_member])
        uow.session.flush()

        uow.session.add(
            SiteAssignment(
                organization_id=org_a.id,
                membership_id=analyst_member.id,
                site_id=site_a1.id,
                assignment_type=AssignmentType.PRIMARY.value,
                status=AssignmentStatus.ACTIVE.value,
                assigned_by_user_id=qc_head_a1_user.id,
            )
        )
        role_qa_head = uow.session.query(Role).filter(Role.role_code == "QA_HEAD").one()
        role_analyst = uow.session.query(Role).filter(Role.role_code == "QC_ANALYST").one()
        uow.session.add_all(
            [
                MembershipRoleAssignment(
                    organization_id=org_a.id,
                    membership_id=qa_head_member.id,
                    role_id=role_qa_head.id,
                    scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
                    granted_by_user_id=qa_head_user.id,
                    approved_by_user_id=qa_head_user.id,
                    status=RoleAssignmentStatus.ACTIVE.value,
                ),
                MembershipRoleAssignment(
                    organization_id=org_a.id,
                    membership_id=analyst_member.id,
                    role_id=role_analyst.id,
                    scope_type=RoleAssignmentScopeType.SITE.value,
                    site_id=site_a1.id,
                    granted_by_user_id=qc_head_a1_user.id,
                    approved_by_user_id=qc_head_a1_user.id,
                    status=RoleAssignmentStatus.ACTIVE.value,
                ),
            ]
        )

        prod_a = Product(
            organization_id=org_a.id,
            product_code="ALP-MET-500",
            generic_name="Metformin HCl 500mg",
            dosage_form="TABLET",
            product_status="ACTIVE",
        )
        uow.session.add(prod_a)
        uow.session.flush()

        batch_a1 = Batch(
            organization_id=org_a.id,
            site_id=site_a1.id,
            product_id=prod_a.id,
            batch_number="ALP-B-S1",
            manufacturing_date=date(2026, 1, 1),
            expiry_date=date(2028, 1, 1),
            batch_size_value=Decimal("1000.0000"),
            batch_size_unit="TABLETS",
            batch_status="IN_PROCESS",
        )
        batch_a2 = Batch(
            organization_id=org_a.id,
            site_id=site_a2.id,
            product_id=prod_a.id,
            batch_number="ALP-B-S2",
            manufacturing_date=date(2026, 1, 1),
            expiry_date=date(2028, 1, 1),
            batch_size_value=Decimal("1000.0000"),
            batch_size_unit="TABLETS",
            batch_status="IN_PROCESS",
        )
        uow.session.add_all([batch_a1, batch_a2])
        uow.session.flush()

        td_a1 = QCTestDefinition(
            organization_id=org_a.id,
            site_id=site_a1.id,
            product_id=prod_a.id,
            test_code="MET-ASSAY",
            version_number="1.0",
            test_name="Metformin Assay",
            test_method_reference="USP",
            test_type="NUMERIC_RANGE",
            expected_unit_code="%W/W",
            min_limit=Decimal("95.0"),
            max_limit=Decimal("105.0"),
            acceptance_criteria="95.0 - 105.0%",
            status="ACTIVE",
        )
        eq_a1 = Equipment(
            organization_id=org_a.id,
            site_id=site_a1.id,
            equipment_code="EQ-A1-01",
            equipment_name="Site 1 Balance",
            equipment_category="BALANCE",
            operational_status="ACTIVE",
            qualification_status="QUALIFIED",
            calibration_required=True,
        )
        eq_a2 = Equipment(
            organization_id=org_a.id,
            site_id=site_a2.id,
            equipment_code="EQ-A2-01",
            equipment_name="Site 2 Balance",
            equipment_category="BALANCE",
            operational_status="ACTIVE",
            qualification_status="QUALIFIED",
            calibration_required=True,
        )
        uow.session.add_all([td_a1, eq_a1, eq_a2])
        uow.session.flush()

        sample_a1 = QCSample(
            organization_id=org_a.id,
            site_id=site_a1.id,
            batch_id=batch_a1.id,
            product_id=prod_a.id,
            sample_identifier="SMP-A1-01",
            sample_type="FINISHED_PRODUCT",
            sampled_at=datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc),
            sampled_by_user_id=qc_head_a1_user.id,
            sampled_by_membership_id=qc_head_a1_member.id,
            sample_quantity=Decimal("20.0000"),
            sample_unit_code="TABLETS",
            testing_status="TESTED",
        )
        uow.session.add(sample_a1)
        uow.session.flush()

        # Result recorded by qc_head_a1_user (to test self-review maker-checker denial!)
        result_by_qchead = QCTestResult(
            organization_id=org_a.id,
            site_id=site_a1.id,
            batch_id=batch_a1.id,
            qc_sample_id=sample_a1.id,
            test_definition_id=td_a1.id,
            specification_version_applied="1.0",
            result_type="NUMERIC",
            numeric_result=Decimal("99.500000"),
            unit_code="%W/W",
            equipment_id=eq_a1.id,
            tested_at=datetime(2026, 2, 1, 11, 0, tzinfo=timezone.utc),
            analyst_user_id=qc_head_a1_user.id,
            analyst_membership_id=qc_head_a1_member.id,
            result_outcome="PASS",
            review_status="PENDING_REVIEW",
            is_out_of_specification_flagged=False,
        )
        # Calibration recorded by qc_head_a1_user (to test self-review maker-checker denial!)
        cal_by_qchead = CalibrationRecord(
            organization_id=org_a.id,
            site_id=site_a1.id,
            equipment_id=eq_a1.id,
            calibration_code="CAL-A1-01",
            calibrated_at=datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc),
            due_at=datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc),
            calibration_provider="NABL Lab",
            calibration_result="PASS",
            record_status="DRAFT",
            recorded_by_user_id=qc_head_a1_user.id,
            recorded_by_membership_id=qc_head_a1_member.id,
        )
        uow.session.add_all([result_by_qchead, cal_by_qchead])
        uow.session.flush()

        org_a_id = org_a.id
        org_b_id = org_b_seed["organization"].id
        site_a1_id, site_a2_id = site_a1.id, site_a2.id
        batch_a1_id, batch_a2_id = batch_a1.id, batch_a2.id
        td_a1_id = td_a1.id
        eq_a1_id, eq_a2_id = eq_a1.id, eq_a2.id
        sample_a1_id = sample_a1.id
        res_id = result_by_qchead.id
        cal_id = cal_by_qchead.id
        qc_head_a1_uid = qc_head_a1_user.id
        qa_head_uid = qa_head_user.id
        analyst_uid = analyst_user.id
        org_b_uid = org_b_seed["user"].id

    h_qchead_a1 = {
        "Authorization": f"Bearer {create_access_token(subject=str(qc_head_a1_uid))}",
        "X-Organization-Id": str(org_a_id),
    }
    h_qahead_a = {
        "Authorization": f"Bearer {create_access_token(subject=str(qa_head_uid))}",
        "X-Organization-Id": str(org_a_id),
    }
    h_analyst_a1 = {
        "Authorization": f"Bearer {create_access_token(subject=str(analyst_uid))}",
        "X-Organization-Id": str(org_a_id),
    }
    h_org_b = {
        "Authorization": f"Bearer {create_access_token(subject=str(org_b_uid))}",
        "X-Organization-Id": str(org_b_id),
    }

    # 1. Cross-tenant isolation (404 Not Found)
    assert api_client.get(f"/api/v1/qc/test-definitions/{td_a1_id}", headers=h_org_b).status_code == 404
    assert api_client.get(f"/api/v1/qc/samples/{sample_a1_id}", headers=h_org_b).status_code == 404
    assert api_client.get(f"/api/v1/batches/{batch_a1_id}/qc-history", headers=h_org_b).status_code == 404
    assert api_client.get(f"/api/v1/equipment/{eq_a1_id}", headers=h_org_b).status_code == 404
    assert (
        api_client.post(
            f"/api/v1/qc/results/{res_id}/review",
            headers=h_org_b,
            json={"decision": "APPROVED"},
        ).status_code
        == 404
    )
    assert (
        api_client.post(
            f"/api/v1/equipment/{eq_a1_id}/calibrations/{cal_id}/review",
            headers=h_org_b,
            json={"decision": "APPROVED"},
        ).status_code
        == 404
    )

    # 2. Site-scope isolation (403 SITE_SCOPE_DENIED)
    r_site_eq = api_client.get(f"/api/v1/equipment/{eq_a2_id}", headers=h_qchead_a1)
    assert r_site_eq.status_code == 403
    assert r_site_eq.json()["error_code"] == "SITE_SCOPE_DENIED"

    r_site_smp = api_client.post(
        f"/api/v1/batches/{batch_a2_id}/qc-samples",
        headers=h_qchead_a1,
        json={
            "sample_identifier": "SMP-DENIED-S2",
            "sample_quantity": "10.0000",
            "sample_unit_code": "TABLETS",
        },
    )
    assert r_site_smp.status_code == 403
    assert r_site_smp.json()["error_code"] == "SITE_SCOPE_DENIED"

    # 3. RBAC enforcement (403 INSUFFICIENT_PERMISSION)
    # QC_ANALYST cannot create test definitions, approve QC results, or create equipment
    r_rbac_td = api_client.post(
        "/api/v1/qc/test-definitions",
        headers=h_analyst_a1,
        json={
            "site_id": str(site_a1_id),
            "test_code": "UNAUTH-TD",
            "test_name": "Unauthorized TD",
            "test_method_reference": "USP",
            "acceptance_criteria": "Pass",
        },
    )
    assert r_rbac_td.status_code == 403
    assert r_rbac_td.json()["error_code"] == "INSUFFICIENT_PERMISSION"

    r_rbac_rev = api_client.post(
        f"/api/v1/qc/results/{res_id}/review",
        headers=h_analyst_a1,
        json={"decision": "APPROVED"},
    )
    assert r_rbac_rev.status_code == 403
    assert r_rbac_rev.json()["error_code"] == "INSUFFICIENT_PERMISSION"

    r_rbac_eq = api_client.post(
        "/api/v1/equipment",
        headers=h_analyst_a1,
        json={
            "site_id": str(site_a1_id),
            "equipment_code": "EQ-UNAUTH",
            "equipment_name": "Unauthorized Eq",
            "equipment_category": "BALANCE",
        },
    )
    assert r_rbac_eq.status_code == 403

    # 4. Dual Maker-Checker enforcement (403 MAKER_CHECKER_VIOLATION)
    # qc_head_a1_user recorded result_by_qchead and cal_by_qchead -> cannot self-approve!
    r_mc_qc = api_client.post(
        f"/api/v1/qc/results/{res_id}/review",
        headers=h_qchead_a1,
        json={"decision": "APPROVED", "comments": "Attempting self-approval"},
    )
    assert r_mc_qc.status_code == 403
    assert r_mc_qc.json()["error_code"] == "MAKER_CHECKER_VIOLATION"

    r_mc_cal = api_client.post(
        f"/api/v1/equipment/{eq_a1_id}/calibrations/{cal_id}/review",
        headers=h_qchead_a1,
        json={"decision": "APPROVED", "comments": "Attempting self-approval"},
    )
    assert r_mc_cal.status_code == 403
    assert r_mc_cal.json()["error_code"] == "MAKER_CHECKER_VIOLATION"

    # 5. Independent reviewer (qa_head_user) succeeds, then immutability (409) blocks re-review
    r_ok_qc = api_client.post(
        f"/api/v1/qc/results/{res_id}/review",
        headers=h_qahead_a,
        json={"decision": "APPROVED", "comments": "Independent QA review."},
    )
    assert r_ok_qc.status_code == 200
    assert r_ok_qc.json()["review_status"] == "APPROVED"

    r_imm_qc = api_client.post(
        f"/api/v1/qc/results/{res_id}/review",
        headers=h_qahead_a,
        json={"decision": "REJECTED", "comments": "Attempting to mutate approved result"},
    )
    assert r_imm_qc.status_code == 409
    assert r_imm_qc.json()["error_code"] == "QC_RESULT_IMMUTABLE"

    r_ok_cal = api_client.post(
        f"/api/v1/equipment/{eq_a1_id}/calibrations/{cal_id}/review",
        headers=h_qahead_a,
        json={"decision": "APPROVED", "comments": "Independent QA calibration review."},
    )
    assert r_ok_cal.status_code == 200
    assert r_ok_cal.json()["record_status"] == "APPROVED"

    r_imm_cal = api_client.post(
        f"/api/v1/equipment/{eq_a1_id}/calibrations/{cal_id}/review",
        headers=h_qahead_a,
        json={"decision": "REJECTED", "comments": "Attempting to mutate approved calibration"},
    )
    assert r_imm_cal.status_code == 409
    assert r_imm_cal.json()["error_code"] == "CALIBRATION_RECORD_IMMUTABLE"

    # Verify SecurityEvents persisted out-of-band
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        sec_types = {
            ev.event_type
            for ev in uow.session.scalars(
                select(SecurityEvent).where(SecurityEvent.organization_id == org_a_id)
            ).all()
        }
        assert "SITE_SCOPE_VIOLATION" in sec_types
        assert "RBAC_PERMISSION_DENIED" in sec_types
        assert "MAKER_CHECKER_VIOLATION" in sec_types
