from datetime import date, datetime, timezone
from decimal import Decimal
import uuid

import pytest

import app.models.domain.manufacturing_execution  # noqa: F401
import app.models.domain.qc_equipment  # noqa: F401
import app.models.domain.sops  # noqa: F401
from app.api.v1.manufacturing_execution import router as manufacturing_execution_router
from app.core.database import UnitOfWork
from app.core.deps import reset_rate_limiter
from app.core.security import create_access_token, get_password_hash
from app.domain.enums import (
    AssignmentStatus,
    AssignmentType,
    DeterministicCheckType,
    MembershipStatus,
    MemberType,
    RoleAssignmentScopeType,
    RoleAssignmentStatus,
    UserAccountStatus,
)
from app.models.domain.documents import Document, DocumentVersion
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
    EquipmentUsage,
    QCSample,
    QCTestDefinition,
    QCTestResult,
)
from app.models.domain.sops import SOP, SOPRevision, SOPStep
from app.models.domain.traceability import Batch
from tests.conftest import create_seeded_tenant_actor


@pytest.fixture(autouse=True)
def _clean_limits():
    reset_rate_limiter()
    yield
    reset_rate_limiter()


def _add_actor(db, *, org_id: uuid.UUID, site_id: uuid.UUID, email: str, role_code: str) -> dict:
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
            scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
            granted_by_user_id=user.id,
            approved_by_user_id=user.id,
            status=RoleAssignmentStatus.ACTIVE.value,
        )
    )
    db.flush()
    return {"user": user, "membership": membership}


def test_mbr_execution_historical_sop_side_by_side_and_consolidated_evidence_e2e(
    api_client, sqlite_db
) -> None:
    if not any(getattr(r, "path", "") == "/api/v1/mbrs" for r in api_client.app.routes):
        api_client.app.include_router(manufacturing_execution_router, prefix="/api/v1")
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        prod_seed = create_seeded_tenant_actor(
            uow.session,
            org_name="Vanguard Biopharma",
            site_code="VAN-S1",
            email="prod.head@vanguard.in",
            role_code="PRODUCTION_HEAD",
            scope_type="SITE",
        )
        org = prod_seed["organization"]
        site = prod_seed["site"]
        prod_user = prod_seed["user"]
        prod_member = prod_seed["membership"]

        qa_head = _add_actor(
            uow.session,
            org_id=org.id,
            site_id=site.id,
            email="qa.head@vanguard.in",
            role_code="QA_HEAD",
        )
        sup_actor = _add_actor(
            uow.session,
            org_id=org.id,
            site_id=site.id,
            email="prod.sup@vanguard.in",
            role_code="PRODUCTION_SUPERVISOR",
        )

        product = Product(
            organization_id=org.id,
            site_id=site.id,
            product_code="PRD-MET-500",
            brand_name="MetGluco 500",
            generic_name="Metformin HCl",
            dosage_form="TABLET",
            strength_value=Decimal("500.0000"),
            strength_unit="MG",
            product_status="ACTIVE",
        )
        uow.session.add(product)
        uow.session.flush()

        bmr_doc = Document(
            organization_id=org.id,
            site_id=site.id,
            document_code="DOC-BMR-MET-01",
            document_type="BMR",
            title="Master BMR MetGluco 500",
            status="ACTIVE",
        )
        uow.session.add(bmr_doc)
        uow.session.flush()

        bmr_doc_ver = DocumentVersion(
            document_id=bmr_doc.id,
            version_label="1.0",
            version_number=1,
            object_key=f"org/{org.id}/bmr_met_v1.pdf",
            original_filename="bmr_met_v1.pdf",
            mime_type="application/pdf",
            byte_size=4096,
            checksum_sha256="a" * 64,
            scan_status="CLEAN",
            approval_status="EFFECTIVE",
            uploaded_by_user_id=prod_user.id,
        )
        uow.session.add(bmr_doc_ver)
        uow.session.flush()

        # Historical SOP with Rev 1.0 (2026-01-01 .. 2026-06-01) and Rev 2.0 (2026-06-01 .. open)
        sop = SOP(
            organization_id=org.id,
            site_id=site.id,
            sop_code="SOP-GRAN-101",
            title="Fluid Bed Granulation & Drying",
            category="MANUFACTURING",
            sop_status="ACTIVE",
        )
        uow.session.add(sop)
        uow.session.flush()

        rev1 = SOPRevision(
            organization_id=org.id,
            sop_id=sop.id,
            site_id=site.id,
            revision_number="1.0",
            revision_sequence=1,
            effective_from=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
            effective_to=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
            revision_status="SUPERSEDED",
            approval_status="APPROVED",
            created_by_user_id=prod_user.id,
            approved_by_user_id=qa_head["user"].id,
            approved_by_membership_id=qa_head["membership"].id,
        )
        rev2 = SOPRevision(
            organization_id=org.id,
            sop_id=sop.id,
            site_id=site.id,
            revision_number="2.0",
            revision_sequence=2,
            effective_from=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
            effective_to=None,
            revision_status="EFFECTIVE",
            approval_status="APPROVED",
            created_by_user_id=prod_user.id,
            approved_by_user_id=qa_head["user"].id,
            approved_by_membership_id=qa_head["membership"].id,
        )
        uow.session.add_all([rev1, rev2])
        uow.session.flush()
        sop.current_revision_id = rev2.id

        # Rev 1.0 allows 60..70 C; Rev 2.0 tightens to 50..58 C
        rev1_step1 = SOPStep(
            organization_id=org.id,
            sop_revision_id=rev1.id,
            step_code="STEP-DRY-01",
            step_sequence=1,
            process_stage="DRYING",
            title="Inlet Air Temperature Control",
            instruction="Maintain inlet air temperature between 60 C and 70 C.",
            is_mandatory=True,
            parameter_name="Inlet Air Temperature",
            parameter_code="INLET_TEMP",
            expected_data_type="NUMERIC",
            unit_code="C",
            min_value=Decimal("60.000000"),
            max_value=Decimal("70.000000"),
            target_value=Decimal("65.000000"),
            acceptance_criteria="60.0 C to 70.0 C",
            requires_operator_signoff=True,
            requires_checker_signoff=True,
        )
        rev1_step2 = SOPStep(
            organization_id=org.id,
            sop_revision_id=rev1.id,
            step_code="STEP-BLEND-02",
            step_sequence=2,
            process_stage="BLENDING",
            title="Final Lubrication Blend",
            instruction="Blend for 15 minutes.",
            is_mandatory=True,
            expected_data_type="NONE",
            requires_operator_signoff=True,
            requires_checker_signoff=False,
        )
        rev2_step1 = SOPStep(
            organization_id=org.id,
            sop_revision_id=rev2.id,
            step_code="STEP-DRY-01",
            step_sequence=1,
            process_stage="DRYING",
            title="Inlet Air Temperature Control (Tightened)",
            instruction="Maintain inlet air temperature between 50 C and 58 C.",
            is_mandatory=True,
            parameter_name="Inlet Air Temperature",
            parameter_code="INLET_TEMP",
            expected_data_type="NUMERIC",
            unit_code="C",
            min_value=Decimal("50.000000"),
            max_value=Decimal("58.000000"),
            target_value=Decimal("54.000000"),
            acceptance_criteria="50.0 C to 58.0 C",
            requires_operator_signoff=True,
            requires_checker_signoff=True,
        )
        uow.session.add_all([rev1_step1, rev1_step2, rev2_step1])
        uow.session.flush()

        batch = Batch(
            organization_id=org.id,
            site_id=site.id,
            product_id=product.id,
            batch_number="VAN-MET-2601",
            manufacturing_date=date(2026, 3, 15),
            expiry_date=date(2028, 3, 14),
            batch_size_value=Decimal("50000.0000"),
            batch_size_unit="TABLETS",
            batch_status="IN_PROCESS",
            primary_document_id=bmr_doc.id,
        )
        uow.session.add(batch)
        uow.session.flush()

        equip = Equipment(
            organization_id=org.id,
            site_id=site.id,
            equipment_code="EQ-FBD-01",
            equipment_name="Fluid Bed Dryer #1",
            equipment_category="DRYER",
            operational_status="OUT_OF_SERVICE",  # Current status is OUT_OF_SERVICE, but historically VALID in March 2026!
            qualification_status="QUALIFIED",
            calibration_required=True,
        )
        uow.session.add(equip)
        uow.session.flush()

        cal_rec = CalibrationRecord(
            organization_id=org.id,
            site_id=site.id,
            equipment_id=equip.id,
            calibration_code="CAL-FBD-2026-Q1",
            calibrated_at=datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc),
            due_at=datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc),
            calibration_provider="Metrology India Lab",
            calibration_result="PASS",
            record_status="APPROVED",
            recorded_by_user_id=prod_user.id,
            recorded_by_membership_id=prod_member.id,
            reviewed_by_user_id=qa_head["user"].id,
            reviewed_by_membership_id=qa_head["membership"].id,
            approved_at=datetime(2026, 1, 16, 10, 0, tzinfo=timezone.utc),
        )
        uow.session.add(cal_rec)

        qc_def = QCTestDefinition(
            organization_id=org.id,
            site_id=site.id,
            product_id=product.id,
            test_code="QC-ASSAY-MET",
            version_number="1.0",
            test_name="Metformin Assay (%)",
            test_method_reference="IP-2026-MET",
            test_type="NUMERIC_RANGE",
            expected_unit_code="PCT",
            min_limit=Decimal("95.000000"),
            max_limit=Decimal("105.000000"),
            acceptance_criteria="95.0% to 105.0%",
            status="ACTIVE",
        )
        uow.session.add(qc_def)
        uow.session.flush()

        qc_sample = QCSample(
            organization_id=org.id,
            site_id=site.id,
            batch_id=batch.id,
            product_id=product.id,
            sample_identifier="SMP-VAN-2601-01",
            sample_type="FINISHED_PRODUCT",
            sampled_at=datetime(2026, 3, 15, 16, 0, tzinfo=timezone.utc),
            sampled_by_user_id=prod_user.id,
            sampled_by_membership_id=prod_member.id,
            sample_quantity=Decimal("20.0000"),
            sample_unit_code="TABLETS",
            testing_status="APPROVED",
        )
        uow.session.add(qc_sample)
        uow.session.flush()

        qc_result = QCTestResult(
            organization_id=org.id,
            site_id=site.id,
            batch_id=batch.id,
            qc_sample_id=qc_sample.id,
            test_definition_id=qc_def.id,
            specification_version_applied="1.0",
            result_type="NUMERIC",
            numeric_result=Decimal("99.400000"),
            unit_code="%",
            tested_at=datetime(2026, 3, 15, 17, 0, tzinfo=timezone.utc),
            analyst_user_id=prod_user.id,
            analyst_membership_id=prod_member.id,
            reviewer_user_id=qa_head["user"].id,
            reviewer_membership_id=qa_head["membership"].id,
            reviewed_at=datetime(2026, 3, 15, 18, 0, tzinfo=timezone.utc),
            result_outcome="PASS",
            review_status="APPROVED",
            is_out_of_specification_flagged=False,
        )
        uow.session.add(qc_result)
        uow.session.flush()

        org_id = org.id
        site_id = site.id
        product_id = product.id
        batch_id = batch.id
        bmr_doc_id = bmr_doc.id
        bmr_doc_ver_id = bmr_doc_ver.id
        rev1_id = rev1.id
        equip_id = equip.id
        cal_rec_id = cal_rec.id
        prod_uid = prod_user.id
        qa_uid = qa_head["user"].id
        sup_uid = sup_actor["user"].id
        sup_mid = sup_actor["membership"].id

    def _headers(uid: uuid.UUID) -> dict:
        return {
            "Authorization": f"Bearer {create_access_token(subject=str(uid))}",
            "X-Organization-Id": str(org_id),
        }

    h_prod = _headers(prod_uid)
    h_qa = _headers(qa_uid)
    h_sup = _headers(sup_uid)

    # 1. Create MasterBatchRecord (DRAFT) -> cannot execute before approval -> approve by QA_HEAD -> activate
    r_mbr = api_client.post(
        "/api/v1/mbrs",
        headers=h_prod,
        json={
            "site_id": str(site_id),
            "product_id": str(product_id),
            "mbr_code": "MBR-MET-500",
            "version_number": "1.0",
            "record_type": "BMR",
            "title": "Master Batch Record - MetGluco 500",
            "target_batch_size_value": "50000.0000",
            "target_batch_size_unit": "TABLETS",
            "document_id": str(bmr_doc_id),
            "document_version_id": str(bmr_doc_ver_id),
            "applicable_sop_revision_ids": [str(rev1_id)],
        },
    )
    assert r_mbr.status_code == 201, r_mbr.text
    mbr = r_mbr.json()
    mbr_id = mbr["id"]

    r_exec_unapproved = api_client.post(
        f"/api/v1/batches/{batch_id}/execution-records",
        headers=h_prod,
        json={
            "master_batch_record_id": mbr_id,
            "execution_code": "BMR-EXEC-01",
            "execution_type": "BMR",
            "manufacturing_stage": "GRANULATION_AND_DRYING",
            "execution_sequence": 1,
        },
    )
    assert r_exec_unapproved.status_code == 409
    assert r_exec_unapproved.json()["error_code"] == "MBR_NOT_APPROVED"

    r_sub = api_client.post(
        f"/api/v1/mbrs/{mbr_id}/submit-review",
        headers=h_prod,
        json={"row_version": mbr["row_version"]},
    )
    assert r_sub.status_code == 200
    mbr = r_sub.json()

    r_app_mbr = api_client.post(
        f"/api/v1/mbrs/{mbr_id}/approve",
        headers=h_qa,
        json={"row_version": mbr["row_version"], "decision": "APPROVED", "comments": "QA approved MBR"},
    )
    assert r_app_mbr.status_code == 200
    mbr = r_app_mbr.json()

    r_act_mbr = api_client.post(
        f"/api/v1/mbrs/{mbr_id}/activate",
        headers=h_qa,
        json={
            "row_version": mbr["row_version"],
            "effective_from": "2026-01-01T00:00:00Z",
        },
    )
    assert r_act_mbr.status_code == 200

    # 2. Create BatchExecutionRecord in March 2026 (when SOP Rev 1.0 was effective, NOT Rev 2.0!)
    r_exec = api_client.post(
        f"/api/v1/batches/{batch_id}/execution-records",
        headers=h_prod,
        json={
            "master_batch_record_id": mbr_id,
            "execution_code": "BMR-EXEC-01",
            "execution_type": "BMR",
            "manufacturing_stage": "GRANULATION_AND_DRYING",
            "execution_sequence": 1,
            "started_at": "2026-03-15T10:00:00Z",
            "primary_document_id": str(bmr_doc_id),
            "primary_document_version_id": str(bmr_doc_ver_id),
            "steps": [
                {
                    "step_code": "STEP-DRY-01",
                    "step_sequence": 1,
                    "description": "Fluid bed drying temperature control",
                    "sop_code": "SOP-GRAN-101",
                    "requires_checker_signoff": True,
                    "equipment_id": str(equip_id),
                }
            ],
        },
    )
    assert r_exec.status_code == 201, r_exec.text
    exec_rec = r_exec.json()
    exec_rec_id = exec_rec["id"]
    step1 = exec_rec["steps"][0]
    step1_id = step1["id"]

    # Execute STEP-DRY-01 at 2026-03-15T10:30:00Z
    r_exec_st1 = api_client.post(
        f"/api/v1/execution-records/{exec_rec_id}/steps/{step1_id}/execute",
        headers=h_prod,
        json={
            "row_version": step1["row_version"],
            "outcome_status": "COMPLETED",
            "started_at": "2026-03-15T10:05:00Z",
            "executed_at": "2026-03-15T10:30:00Z",
            "equipment_id": str(equip_id),
        },
    )
    assert r_exec_st1.status_code == 200
    step1 = r_exec_st1.json()

    # Record observation in Fahrenheit (149.0 F = 65.0 C -> within Rev 1.0 [60, 70] C, outside Rev 2.0 [50, 58] C!)
    r_obs = api_client.post(
        f"/api/v1/execution-records/{exec_rec_id}/steps/{step1_id}/observations",
        headers=h_prod,
        json={
            "parameter_code": "INLET_TEMP",
            "parameter_name": "Inlet Air Temperature",
            "value_type": "NUMERIC",
            "numeric_value": "149.000000",
            "unit_code": "F",
            "observed_at": "2026-03-15T10:20:00Z",
            "equipment_id": str(equip_id),
            "source_document_id": str(bmr_doc_id),
            "source_document_version_id": str(bmr_doc_ver_id),
            "source_page_number": 4,
            "source_section_ref": "Section 3.2",
        },
    )
    assert r_obs.status_code == 201, r_obs.text
    obs = r_obs.json()
    obs_id = obs["id"]

    # 3. Inspect Consolidated Evidence BEFORE step2 & checker signoffs:
    # Should resolve Rev 1.0 (not 2.0!), flag missing STEP-BLEND-02, missing checker signoff, and unverified observation
    r_ev1 = api_client.get(
        f"/api/v1/batches/{batch_id}/manufacturing-evidence",
        headers=h_prod,
    )
    assert r_ev1.status_code == 200, r_ev1.text
    ev1 = r_ev1.json()
    assert len(ev1["sop_resolutions"]) == 1
    assert ev1["sop_resolutions"][0]["resolved_revision_number"] == "1.0"
    assert ev1["sop_resolutions"][0]["resolution_outcome"] == "RESOLVED"

    assert len(ev1["side_by_side_parameter_comparisons"]) == 1
    cmp1 = ev1["side_by_side_parameter_comparisons"][0]
    assert cmp1["sop_revision_number"] == "1.0"
    assert Decimal(cmp1["actual_numeric_value"]) == Decimal("149.000000")
    assert cmp1["actual_unit_code"] == "F"
    assert cmp1["expected_unit_code"] == "C"
    assert Decimal(cmp1["converted_numeric_value"]) == Decimal("65.000000")
    assert Decimal(cmp1["min_value"]) == Decimal("60.000000")
    assert Decimal(cmp1["max_value"]) == Decimal("70.000000")
    assert cmp1["compliance_outcome"] == "UNVERIFIED"

    assert ev1["completeness_summary"]["missing_count"] >= 2  # Missing STEP-BLEND-02 & missing checker signoff
    assert ev1["completeness_summary"]["unverified_count"] >= 1
    assert ev1["completeness_summary"]["is_fully_compliant"] is False

    # 4. Complete missing step STEP-BLEND-02, verify observation, verify step1, complete & verify execution record
    r_ver_obs = api_client.post(
        f"/api/v1/observations/{obs_id}/verify",
        headers=h_sup,
        json={"row_version": obs["row_version"], "verification_status": "VERIFIED"},
    )
    assert r_ver_obs.status_code == 200

    r_ver_st1 = api_client.post(
        f"/api/v1/execution-records/{exec_rec_id}/steps/{step1_id}/verify",
        headers=h_sup,
        json={"row_version": step1["row_version"], "comments": "Checked inlet temp chart"},
    )
    assert r_ver_st1.status_code == 200

    r_add_st2 = api_client.post(
        f"/api/v1/execution-records/{exec_rec_id}/steps",
        headers=h_prod,
        json={
            "step_code": "STEP-BLEND-02",
            "step_sequence": 2,
            "description": "Final Lubrication Blend",
            "sop_code": "SOP-GRAN-101",
            "requires_checker_signoff": False,
        },
    )
    assert r_add_st2.status_code == 201
    st2 = r_add_st2.json()

    r_exec_st2 = api_client.post(
        f"/api/v1/execution-records/{exec_rec_id}/steps/{st2['id']}/execute",
        headers=h_prod,
        json={
            "row_version": st2["row_version"],
            "outcome_status": "COMPLETED",
            "executed_at": "2026-03-15T11:15:00Z",
        },
    )
    assert r_exec_st2.status_code == 200

    # Refresh execution record row_version, complete -> verify -> approve
    rec_curr = api_client.get(f"/api/v1/execution-records/{exec_rec_id}", headers=h_prod).json()
    r_comp_rec = api_client.post(
        f"/api/v1/execution-records/{exec_rec_id}/complete",
        headers=h_prod,
        json={"row_version": rec_curr["row_version"], "occurred_at": "2026-03-15T12:00:00Z"},
    )
    assert r_comp_rec.status_code == 200
    rec_curr = r_comp_rec.json()

    r_ver_rec = api_client.post(
        f"/api/v1/execution-records/{exec_rec_id}/verify",
        headers=h_sup,
        json={"row_version": rec_curr["row_version"], "occurred_at": "2026-03-15T12:30:00Z"},
    )
    assert r_ver_rec.status_code == 200
    rec_curr = r_ver_rec.json()

    r_app_rec = api_client.post(
        f"/api/v1/execution-records/{exec_rec_id}/approve",
        headers=h_qa,
        json={"row_version": rec_curr["row_version"], "occurred_at": "2026-03-15T13:00:00Z"},
    )
    assert r_app_rec.status_code == 200

    # 5. Re-evaluate Consolidated Batch Manufacturing & Quality Evidence -> 100% PASS across all 7 check types!
    r_ev2 = api_client.get(
        f"/api/v1/batches/{batch_id}/manufacturing-evidence",
        headers=h_qa,
    )
    assert r_ev2.status_code == 200, r_ev2.text
    ev2 = r_ev2.json()

    check_types_present = {c["check_type"] for c in ev2["deterministic_checks"]}
    assert DeterministicCheckType.PROCESS_VALUE_VS_SOP_LIMIT.value in check_types_present
    assert DeterministicCheckType.UNIT_COMPATIBILITY_AND_CONVERSION.value in check_types_present
    assert DeterministicCheckType.REQUIRED_EXECUTION_STEP_PRESENCE.value in check_types_present
    assert DeterministicCheckType.REQUIRED_SIGNOFF_PRESENCE.value in check_types_present
    assert DeterministicCheckType.SOP_REVISION_APPLICABILITY.value in check_types_present
    assert DeterministicCheckType.EQUIPMENT_CALIBRATION_VALIDITY.value in check_types_present
    assert DeterministicCheckType.QC_RESULT_VS_SPECIFICATION.value in check_types_present

    # Historical calibration at 2026-03-15 is VALID despite current equipment.operational_status == OUT_OF_SERVICE
    assert len(ev2["equipment_calibration_evidence"]) == 1
    assert ev2["equipment_calibration_evidence"][0]["calibration_validity_status"] == "VALID"
    assert ev2["equipment_calibration_evidence"][0]["compliance_outcome"] == "PASS"

    assert len(ev2["qc_results_evidence"]) == 1
    assert ev2["qc_results_evidence"][0]["deterministic_outcome"] == "PASS"

    assert len(ev2["referenced_document_versions"]) == 1
    assert ev2["referenced_document_versions"][0]["checksum_sha256"] == "a" * 64

    assert ev2["completeness_summary"]["fail_count"] == 0
    assert ev2["completeness_summary"]["missing_count"] == 0
    assert ev2["completeness_summary"]["unverified_count"] == 0
    assert ev2["completeness_summary"]["is_fully_compliant"] is True
