from datetime import date, datetime, timezone
from decimal import Decimal
import uuid

from sqlalchemy import select

import app.models.domain.manufacturing_execution  # noqa: F401
import app.models.domain.qc_equipment  # noqa: F401
import app.models.domain.sops  # noqa: F401
from app.api.v1.manufacturing_execution import router as manufacturing_execution_router
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
from app.models.domain.manufacturing_execution import MasterBatchRecord
from app.models.domain.products_materials import Product
from app.models.domain.sops import SOP, SOPRevision, SOPStep
from app.models.domain.tenancy import Site
from app.models.domain.traceability import Batch
from tests.conftest import create_seeded_tenant_actor


def test_manufacturing_execution_isolation_maker_checker_and_immutability(
    api_client, sqlite_db
) -> None:
    if not any(getattr(r, "path", "") == "/api/v1/mbrs" for r in api_client.app.routes):
        api_client.app.include_router(manufacturing_execution_router, prefix="/api/v1")
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        org_a_seed = create_seeded_tenant_actor(
            uow.session,
            org_name="Org Alpha Mfg",
            site_code="AMF-1",
            email="prod.amf1@alpha.in",
            role_code="PRODUCTION_HEAD",
            scope_type="SITE",
        )
        org_b_seed = create_seeded_tenant_actor(
            uow.session,
            org_name="Org Beta Mfg",
            site_code="BMF-1",
            email="prod.bmf1@beta.in",
            role_code="PRODUCTION_HEAD",
            scope_type="ORGANIZATION",
        )

        org_a = org_a_seed["organization"]
        site_a1 = org_a_seed["site"]
        prod_a1_user = org_a_seed["user"]

        site_a2 = Site(
            organization_id=org_a.id,
            site_code="AMF-2",
            site_name="Alpha Site 2",
            site_type=SiteType.FDF.value,
            address_id=org_a.registered_address_id,
            status=SiteStatus.ACTIVE.value,
            is_primary_site=False,
        )
        uow.session.add(site_a2)
        uow.session.flush()

        # Second actor in Org A (QA_HEAD)
        qa_a_user = User(
            email="qa.amf@alpha.in",
            full_name="QA Head Alpha",
            password_hash=get_password_hash("StrongPass!123"),
            account_status=UserAccountStatus.ACTIVE.value,
        )
        uow.session.add(qa_a_user)
        uow.session.flush()
        qa_a_member = OrganizationMembership(
            user_id=qa_a_user.id,
            organization_id=org_a.id,
            member_type=MemberType.EMPLOYEE.value,
            membership_status=MembershipStatus.ACTIVE.value,
            employee_code="EMP-QA-AMF",
        )
        uow.session.add(qa_a_member)
        uow.session.flush()
        uow.session.add(
            SiteAssignment(
                organization_id=org_a.id,
                membership_id=qa_a_member.id,
                site_id=site_a1.id,
                assignment_type=AssignmentType.PRIMARY.value,
                status=AssignmentStatus.ACTIVE.value,
                assigned_by_user_id=qa_a_user.id,
            )
        )
        qa_role = uow.session.query(Role).filter(Role.role_code == "QA_HEAD").one()
        uow.session.add(
            MembershipRoleAssignment(
                organization_id=org_a.id,
                membership_id=qa_a_member.id,
                role_id=qa_role.id,
                scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
                granted_by_user_id=qa_a_user.id,
                approved_by_user_id=qa_a_user.id,
                status=RoleAssignmentStatus.ACTIVE.value,
            )
        )

        prod_a = Product(
            organization_id=org_a.id,
            product_code="AMF-PRD-1",
            generic_name="Ibuprofen",
            dosage_form="TABLET",
            product_status="ACTIVE",
        )
        uow.session.add(prod_a)
        uow.session.flush()

        mbr_a2 = MasterBatchRecord(
            organization_id=org_a.id,
            site_id=site_a2.id,
            product_id=prod_a.id,
            mbr_code="MBR-A2-01",
            version_number="1.0",
            title="Site 2 MBR",
            approval_status="EFFECTIVE",
        )
        batch_a1 = Batch(
            organization_id=org_a.id,
            site_id=site_a1.id,
            product_id=prod_a.id,
            batch_number="AMF-B1",
            manufacturing_date=date(2026, 2, 1),
            expiry_date=date(2028, 2, 1),
            batch_size_value=Decimal("1000.0000"),
            batch_size_unit="TABLETS",
            batch_status="IN_PROCESS",
        )
        batch_a2 = Batch(
            organization_id=org_a.id,
            site_id=site_a2.id,
            product_id=prod_a.id,
            batch_number="AMF-B2",
            manufacturing_date=date(2026, 2, 1),
            expiry_date=date(2028, 2, 1),
            batch_size_value=Decimal("1000.0000"),
            batch_size_unit="TABLETS",
            batch_status="IN_PROCESS",
        )
        uow.session.add_all([mbr_a2, batch_a1, batch_a2])
        uow.session.flush()

        org_a_id = org_a.id
        org_b_id = org_b_seed["organization"].id
        site_a1_id = site_a1.id
        prod_a_id = prod_a.id
        mbr_a2_id = mbr_a2.id
        batch_a1_id = batch_a1.id
        batch_a2_id = batch_a2.id
        prod_a1_uid = prod_a1_user.id
        qa_a_uid = qa_a_user.id
        org_b_uid = org_b_seed["user"].id

    h_a_prod = {
        "Authorization": f"Bearer {create_access_token(subject=str(prod_a1_uid))}",
        "X-Organization-Id": str(org_a_id),
    }
    h_a_qa = {
        "Authorization": f"Bearer {create_access_token(subject=str(qa_a_uid))}",
        "X-Organization-Id": str(org_a_id),
    }
    h_b = {
        "Authorization": f"Bearer {create_access_token(subject=str(org_b_uid))}",
        "X-Organization-Id": str(org_b_id),
    }

    # 1. Create MBR on Site 1 by prod_a1 -> self-approval blocked (403 MAKER_CHECKER_VIOLATION)
    r_mbr = api_client.post(
        "/api/v1/mbrs",
        headers=h_a_prod,
        json={
            "site_id": str(site_a1_id),
            "product_id": str(prod_a_id),
            "mbr_code": "MBR-A1-01",
            "version_number": "1.0",
            "title": "Site 1 Ibuprofen MBR",
        },
    )
    assert r_mbr.status_code == 201
    mbr1 = r_mbr.json()
    mbr1_id = mbr1["id"]

    r_self_mbr = api_client.post(
        f"/api/v1/mbrs/{mbr1_id}/approve",
        headers=h_a_prod,
        json={"row_version": mbr1["row_version"], "decision": "APPROVED"},
    )
    assert r_self_mbr.status_code == 403
    assert r_self_mbr.json()["error_code"] == "MAKER_CHECKER_VIOLATION"

    # Approve by distinct QA user -> then attempt PATCH on APPROVED MBR -> 409 MBR_IMMUTABLE
    r_app_mbr = api_client.post(
        f"/api/v1/mbrs/{mbr1_id}/approve",
        headers=h_a_qa,
        json={"row_version": mbr1["row_version"], "decision": "APPROVED"},
    )
    assert r_app_mbr.status_code == 200
    mbr1 = r_app_mbr.json()

    r_mut_mbr = api_client.patch(
        f"/api/v1/mbrs/{mbr1_id}",
        headers=h_a_prod,
        json={"row_version": mbr1["row_version"], "title": "Tampered Title"},
    )
    assert r_mut_mbr.status_code == 409
    assert r_mut_mbr.json()["error_code"] == "MBR_IMMUTABLE"

    # 2. Cross-tenant 404 & Site-scope 403 checks
    assert api_client.get(f"/api/v1/mbrs/{mbr1_id}", headers=h_b).status_code == 404
    assert api_client.get(f"/api/v1/batches/{batch_a1_id}/manufacturing-evidence", headers=h_b).status_code == 404

    r_site_mbr = api_client.get(f"/api/v1/mbrs/{mbr_a2_id}", headers=h_a_prod)
    assert r_site_mbr.status_code == 403
    assert r_site_mbr.json()["error_code"] == "SITE_SCOPE_DENIED"

    r_site_ev = api_client.get(
        f"/api/v1/batches/{batch_a2_id}/manufacturing-evidence", headers=h_a_prod
    )
    assert r_site_ev.status_code == 403
    assert r_site_ev.json()["error_code"] == "SITE_SCOPE_DENIED"

    # 3. Execution Record, Step, and Observation Maker-Checker & Immutability
    r_rec = api_client.post(
        f"/api/v1/batches/{batch_a1_id}/execution-records",
        headers=h_a_prod,
        json={
            "master_batch_record_id": mbr1_id,
            "execution_code": "EXEC-A1-01",
            "manufacturing_stage": "COMPRESSION",
            "execution_sequence": 1,
            "started_at": "2026-02-02T08:00:00Z",
            "steps": [
                {
                    "step_code": "ST-COMP-1",
                    "step_sequence": 1,
                    "description": "Tablet compression force check",
                    "requires_checker_signoff": True,
                }
            ],
        },
    )
    assert r_rec.status_code == 201
    rec = r_rec.json()
    rec_id = rec["id"]
    st = rec["steps"][0]
    st_id = st["id"]

    r_exec_st = api_client.post(
        f"/api/v1/execution-records/{rec_id}/steps/{st_id}/execute",
        headers=h_a_prod,
        json={"row_version": st["row_version"], "outcome_status": "COMPLETED"},
    )
    assert r_exec_st.status_code == 200
    st = r_exec_st.json()

    r_obs = api_client.post(
        f"/api/v1/execution-records/{rec_id}/steps/{st_id}/observations",
        headers=h_a_prod,
        json={
            "parameter_code": "HARDNESS",
            "parameter_name": "Tablet Hardness",
            "value_type": "NUMERIC",
            "numeric_value": "12.500000",
            "unit_code": "KP",
        },
    )
    assert r_obs.status_code == 201
    obs = r_obs.json()
    obs_id = obs["id"]

    # Operator self-verifying observation or step -> 403 MAKER_CHECKER_VIOLATION
    r_self_obs = api_client.post(
        f"/api/v1/observations/{obs_id}/verify",
        headers=h_a_prod,
        json={"row_version": obs["row_version"], "verification_status": "VERIFIED"},
    )
    assert r_self_obs.status_code == 403
    assert r_self_obs.json()["error_code"] == "MAKER_CHECKER_VIOLATION"

    r_self_st = api_client.post(
        f"/api/v1/execution-records/{rec_id}/steps/{st_id}/verify",
        headers=h_a_prod,
        json={"row_version": st["row_version"]},
    )
    assert r_self_st.status_code == 403
    assert r_self_st.json()["error_code"] == "MAKER_CHECKER_VIOLATION"

    # Distinct QA user verifies observation & step -> subsequent mutation blocked (409)
    r_ver_obs = api_client.post(
        f"/api/v1/observations/{obs_id}/verify",
        headers=h_a_qa,
        json={"row_version": obs["row_version"], "verification_status": "VERIFIED"},
    )
    assert r_ver_obs.status_code == 200

    r_rever_obs = api_client.post(
        f"/api/v1/observations/{obs_id}/verify",
        headers=h_a_qa,
        json={"row_version": r_ver_obs.json()["row_version"], "verification_status": "REJECTED"},
    )
    assert r_rever_obs.status_code == 409
    assert r_rever_obs.json()["error_code"] == "OBSERVATION_IMMUTABLE"

    r_ver_st = api_client.post(
        f"/api/v1/execution-records/{rec_id}/steps/{st_id}/verify",
        headers=h_a_qa,
        json={"row_version": st["row_version"]},
    )
    assert r_ver_st.status_code == 200

    r_obs_on_verified_step = api_client.post(
        f"/api/v1/execution-records/{rec_id}/steps/{st_id}/observations",
        headers=h_a_prod,
        json={
            "parameter_code": "FRIABILITY",
            "parameter_name": "Friability",
            "value_type": "NUMERIC",
            "numeric_value": "0.200000",
            "unit_code": "PCT",
        },
    )
    assert r_obs_on_verified_step.status_code == 409
    assert r_obs_on_verified_step.json()["error_code"] == "EXECUTION_STEP_IMMUTABLE"

    # Complete execution record -> self-verify blocked (403) -> QA verify succeeds -> adding step blocked (409)
    rec_curr = api_client.get(f"/api/v1/execution-records/{rec_id}", headers=h_a_prod).json()
    r_comp = api_client.post(
        f"/api/v1/execution-records/{rec_id}/complete",
        headers=h_a_prod,
        json={"row_version": rec_curr["row_version"], "occurred_at": "2026-02-02T10:00:00Z"},
    )
    assert r_comp.status_code == 200
    rec_curr = r_comp.json()

    r_self_ver_rec = api_client.post(
        f"/api/v1/execution-records/{rec_id}/verify",
        headers=h_a_prod,
        json={"row_version": rec_curr["row_version"]},
    )
    assert r_self_ver_rec.status_code == 403
    assert r_self_ver_rec.json()["error_code"] == "MAKER_CHECKER_VIOLATION"

    r_ver_rec = api_client.post(
        f"/api/v1/execution-records/{rec_id}/verify",
        headers=h_a_qa,
        json={"row_version": rec_curr["row_version"]},
    )
    assert r_ver_rec.status_code == 200

    r_add_step_after_ver = api_client.post(
        f"/api/v1/execution-records/{rec_id}/steps",
        headers=h_a_prod,
        json={
            "step_code": "ST-LATE",
            "step_sequence": 2,
            "description": "Late step after verification",
        },
    )
    assert r_add_step_after_ver.status_code == 409
    assert r_add_step_after_ver.json()["error_code"] == "EXECUTION_RECORD_IMMUTABLE"

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


def test_cross_tenant_sop_links_rejected_and_parent_record_blocks_child_verify(
    api_client, sqlite_db
) -> None:
    if not any(getattr(r, "path", "") == "/api/v1/mbrs" for r in api_client.app.routes):
        api_client.app.include_router(manufacturing_execution_router, prefix="/api/v1")
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        org_a_seed = create_seeded_tenant_actor(
            uow.session,
            org_name="Reg Alpha Bio",
            site_code="RAB-1",
            email="prod.rab@alpha.in",
            role_code="PRODUCTION_HEAD",
            scope_type="ORGANIZATION",
        )
        org_b_seed = create_seeded_tenant_actor(
            uow.session,
            org_name="Reg Beta Bio",
            site_code="RBB-1",
            email="prod.rbb@beta.in",
            role_code="PRODUCTION_HEAD",
            scope_type="ORGANIZATION",
        )
        org_a, site_a, prod_a_user = (
            org_a_seed["organization"],
            org_a_seed["site"],
            org_a_seed["user"],
        )
        org_b, site_b = org_b_seed["organization"], org_b_seed["site"]

        qa_a_user = User(
            email="qa.rab@alpha.in",
            full_name="QA Head RAB",
            password_hash=get_password_hash("StrongPass!123"),
            account_status=UserAccountStatus.ACTIVE.value,
        )
        uow.session.add(qa_a_user)
        uow.session.flush()
        qa_a_member = OrganizationMembership(
            user_id=qa_a_user.id,
            organization_id=org_a.id,
            member_type=MemberType.EMPLOYEE.value,
            membership_status=MembershipStatus.ACTIVE.value,
            employee_code="EMP-QA-RAB",
        )
        uow.session.add(qa_a_member)
        uow.session.flush()
        qa_role = uow.session.query(Role).filter(Role.role_code == "QA_HEAD").one()
        uow.session.add(
            MembershipRoleAssignment(
                organization_id=org_a.id,
                membership_id=qa_a_member.id,
                role_id=qa_role.id,
                scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
                granted_by_user_id=qa_a_user.id,
                approved_by_user_id=qa_a_user.id,
                status=RoleAssignmentStatus.ACTIVE.value,
            )
        )

        # Org B SOP/Revision/Step that Org A must NEVER link
        sop_b = SOP(
            organization_id=org_b.id,
            site_id=site_b.id,
            sop_code="SOP-B-SECRET",
            title="Org B SOP",
            sop_status="ACTIVE",
        )
        uow.session.add(sop_b)
        uow.session.flush()
        rev_b = SOPRevision(
            organization_id=org_b.id,
            sop_id=sop_b.id,
            site_id=site_b.id,
            revision_number="1.0",
            revision_sequence=1,
            effective_from=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
            revision_status="EFFECTIVE",
            approval_status="APPROVED",
        )
        uow.session.add(rev_b)
        uow.session.flush()
        sop_step_b = SOPStep(
            organization_id=org_b.id,
            sop_revision_id=rev_b.id,
            step_code="STEP-B1",
            step_sequence=1,
            title="Org B Parameter",
            instruction="Org B only",
            expected_data_type="NUMERIC",
            unit_code="C",
            min_value=Decimal("10"),
            max_value=Decimal("20"),
        )
        uow.session.add(sop_step_b)

        product_a = Product(
            organization_id=org_a.id,
            site_id=site_a.id,
            product_code="RAB-PRD-1",
            generic_name="Atenolol",
            dosage_form="TABLET",
            product_status="ACTIVE",
        )
        uow.session.add(product_a)
        uow.session.flush()

        mbr_a = MasterBatchRecord(
            organization_id=org_a.id,
            site_id=site_a.id,
            product_id=product_a.id,
            mbr_code="MBR-RAB-1",
            version_number="1.0",
            title="RAB Approved MBR",
            approval_status="EFFECTIVE",
        )
        batch_a = Batch(
            organization_id=org_a.id,
            site_id=site_a.id,
            product_id=product_a.id,
            batch_number="RAB-B1",
            manufacturing_date=date(2026, 2, 1),
            expiry_date=date(2028, 2, 1),
            batch_size_value=Decimal("500.0000"),
            batch_size_unit="TABLETS",
            batch_status="IN_PROCESS",
        )
        uow.session.add_all([mbr_a, batch_a])
        uow.session.flush()

        org_a_id, site_a_id, product_a_id = org_a.id, site_a.id, product_a.id
        mbr_a_id, batch_a_id = mbr_a.id, batch_a.id
        sop_b_id, rev_b_id, sop_step_b_id = sop_b.id, rev_b.id, sop_step_b.id
        prod_a_uid, qa_a_uid = prod_a_user.id, qa_a_user.id

    h_prod = {
        "Authorization": f"Bearer {create_access_token(subject=str(prod_a_uid))}",
        "X-Organization-Id": str(org_a_id),
    }
    h_qa = {
        "Authorization": f"Bearer {create_access_token(subject=str(qa_a_uid))}",
        "X-Organization-Id": str(org_a_id),
    }

    # 1. Cross-tenant SOP links must return 404 on MBR create, execution create, add_step, record_observation
    r_mbr_x = api_client.post(
        "/api/v1/mbrs",
        headers=h_prod,
        json={
            "site_id": str(site_a_id),
            "product_id": str(product_a_id),
            "mbr_code": "MBR-RAB-X",
            "version_number": "1.0",
            "title": "Cross-tenant SOP Ref MBR",
            "applicable_sop_revision_ids": [str(rev_b_id)],
        },
    )
    assert r_mbr_x.status_code == 404

    r_exec_x = api_client.post(
        f"/api/v1/batches/{batch_a_id}/execution-records",
        headers=h_prod,
        json={
            "master_batch_record_id": str(mbr_a_id),
            "execution_code": "EXEC-RAB-X",
            "manufacturing_stage": "MIXING",
            "execution_sequence": 1,
            "steps": [
                {
                    "step_code": "ST-X",
                    "step_sequence": 1,
                    "description": "Cross-tenant SOP link",
                    "sop_id": str(sop_b_id),
                    "sop_revision_id": str(rev_b_id),
                    "sop_step_id": str(sop_step_b_id),
                }
            ],
        },
    )
    assert r_exec_x.status_code == 404

    # Create valid execution record + step + observation for immutability checks
    r_exec = api_client.post(
        f"/api/v1/batches/{batch_a_id}/execution-records",
        headers=h_prod,
        json={
            "master_batch_record_id": str(mbr_a_id),
            "execution_code": "EXEC-RAB-1",
            "manufacturing_stage": "MIXING",
            "execution_sequence": 1,
            "started_at": "2026-02-02T08:00:00Z",
            "steps": [
                {
                    "step_code": "ST-1",
                    "step_sequence": 1,
                    "description": "Mixing check",
                    "requires_checker_signoff": True,
                }
            ],
        },
    )
    assert r_exec.status_code == 201
    rec = r_exec.json()
    rec_id = rec["id"]
    st1 = rec["steps"][0]
    st1_id = st1["id"]

    r_add_x = api_client.post(
        f"/api/v1/execution-records/{rec_id}/steps",
        headers=h_prod,
        json={
            "step_code": "ST-X2",
            "step_sequence": 2,
            "description": "Cross-tenant step link",
            "sop_step_id": str(sop_step_b_id),
        },
    )
    assert r_add_x.status_code == 404

    r_obs_x = api_client.post(
        f"/api/v1/execution-records/{rec_id}/steps/{st1_id}/observations",
        headers=h_prod,
        json={
            "sop_step_id": str(sop_step_b_id),
            "parameter_code": "TEMP",
            "parameter_name": "Temp",
            "value_type": "NUMERIC",
            "numeric_value": "15.000000",
            "unit_code": "C",
        },
    )
    assert r_obs_x.status_code == 404

    # Complete step & record observation (leave both unverified), then approve parent BatchExecutionRecord
    r_exec_st1 = api_client.post(
        f"/api/v1/execution-records/{rec_id}/steps/{st1_id}/execute",
        headers=h_prod,
        json={"row_version": st1["row_version"], "outcome_status": "COMPLETED"},
    )
    assert r_exec_st1.status_code == 200
    st1 = r_exec_st1.json()

    r_obs = api_client.post(
        f"/api/v1/execution-records/{rec_id}/steps/{st1_id}/observations",
        headers=h_prod,
        json={
            "parameter_code": "MIX_RPM",
            "parameter_name": "Mixer Speed",
            "value_type": "NUMERIC",
            "numeric_value": "250.000000",
            "unit_code": "RPM",
        },
    )
    assert r_obs.status_code == 201
    obs = r_obs.json()

    rec_curr = api_client.get(f"/api/v1/execution-records/{rec_id}", headers=h_prod).json()
    r_comp = api_client.post(
        f"/api/v1/execution-records/{rec_id}/complete",
        headers=h_prod,
        json={"row_version": rec_curr["row_version"], "occurred_at": "2026-02-02T09:00:00Z"},
    )
    assert r_comp.status_code == 200
    rec_curr = r_comp.json()

    r_app = api_client.post(
        f"/api/v1/execution-records/{rec_id}/approve",
        headers=h_qa,
        json={"row_version": rec_curr["row_version"], "occurred_at": "2026-02-02T10:00:00Z"},
    )
    assert r_app.status_code == 200

    # 2. Parent APPROVED BatchExecutionRecord blocks verify_step and verify_observation with 409 EXECUTION_RECORD_IMMUTABLE
    r_ver_st_locked = api_client.post(
        f"/api/v1/execution-records/{rec_id}/steps/{st1_id}/verify",
        headers=h_qa,
        json={"row_version": st1["row_version"]},
    )
    assert r_ver_st_locked.status_code == 409
    assert r_ver_st_locked.json()["error_code"] == "EXECUTION_RECORD_IMMUTABLE"

    r_ver_obs_locked = api_client.post(
        f"/api/v1/observations/{obs['id']}/verify",
        headers=h_qa,
        json={"row_version": obs["row_version"], "verification_status": "VERIFIED"},
    )
    assert r_ver_obs_locked.status_code == 409
    assert r_ver_obs_locked.json()["error_code"] == "EXECUTION_RECORD_IMMUTABLE"

    # Reads remain accessible after approval
    r_read_obs = api_client.get(
        f"/api/v1/execution-records/{rec_id}/steps/{st1_id}/observations",
        headers=h_qa,
    )
    assert r_read_obs.status_code == 200
    assert len(r_read_obs.json()) == 1
