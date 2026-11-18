from datetime import date, datetime, timezone
from decimal import Decimal
import uuid

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
    UserAccountStatus,
)
from app.models.domain.documents import Document, DocumentVersion
from app.models.domain.governance import ApprovalRecord, AuditEvent
from app.models.domain.identity import (
    MembershipRoleAssignment,
    OrganizationMembership,
    Role,
    SiteAssignment,
    User,
)
from app.models.domain.products_materials import Product
from app.models.domain.traceability import Batch
from tests.conftest import create_seeded_tenant_actor


def _ensure_qc_router_mounted(api_client) -> None:
    existing_paths = {getattr(r, "path", "") for r in api_client.app.routes}
    if "/api/v1/equipment" not in existing_paths:
        api_client.app.include_router(qc_equipment_router, prefix="/api/v1")


def test_qc_lab_and_equipment_calibration_e2e_workflow(api_client, sqlite_db) -> None:
    _ensure_qc_router_mounted(api_client)
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        qc_head_seed = create_seeded_tenant_actor(
            uow.session,
            org_name="Aurobindo Quality Labs",
            site_code="AUR-QC1",
            email="qc.head@auro.in",
            role_code="QC_HEAD",
            scope_type="SITE",
        )
        org = qc_head_seed["organization"]
        site = qc_head_seed["site"]
        qc_head_user = qc_head_seed["user"]
        qc_head_member = qc_head_seed["membership"]

        # Create a separate QC_ANALYST + PRODUCTION_OPERATOR user for maker-checker compliance
        analyst_user = User(
            email="qc.analyst@auro.in",
            full_name="QC Analyst Auro",
            password_hash=get_password_hash("StrongPass!123"),
            account_status=UserAccountStatus.ACTIVE.value,
        )
        uow.session.add(analyst_user)
        uow.session.flush()

        analyst_member = OrganizationMembership(
            user_id=analyst_user.id,
            organization_id=org.id,
            member_type=MemberType.EMPLOYEE.value,
            membership_status=MembershipStatus.ACTIVE.value,
            employee_code="EMP-QC-ANA-01",
        )
        uow.session.add(analyst_member)
        uow.session.flush()

        uow.session.add(
            SiteAssignment(
                organization_id=org.id,
                membership_id=analyst_member.id,
                site_id=site.id,
                assignment_type=AssignmentType.PRIMARY.value,
                status=AssignmentStatus.ACTIVE.value,
                assigned_by_user_id=qc_head_user.id,
            )
        )
        role_analyst = uow.session.query(Role).filter(Role.role_code == "QC_ANALYST").one()
        role_prod_sup = (
            uow.session.query(Role).filter(Role.role_code == "PRODUCTION_SUPERVISOR").one()
        )
        uow.session.add_all(
            [
                MembershipRoleAssignment(
                    organization_id=org.id,
                    membership_id=analyst_member.id,
                    role_id=role_analyst.id,
                    scope_type=RoleAssignmentScopeType.SITE.value,
                    site_id=site.id,
                    granted_by_user_id=qc_head_user.id,
                    approved_by_user_id=qc_head_user.id,
                    status=RoleAssignmentStatus.ACTIVE.value,
                ),
                MembershipRoleAssignment(
                    organization_id=org.id,
                    membership_id=analyst_member.id,
                    role_id=role_prod_sup.id,
                    scope_type=RoleAssignmentScopeType.SITE.value,
                    site_id=site.id,
                    granted_by_user_id=qc_head_user.id,
                    approved_by_user_id=qc_head_user.id,
                    status=RoleAssignmentStatus.ACTIVE.value,
                ),
            ]
        )

        product = Product(
            organization_id=org.id,
            site_id=site.id,
            product_code="AUR-AMOX-500",
            generic_name="Amoxicillin Capsules 500mg",
            dosage_form="CAPSULE",
            product_status="ACTIVE",
        )
        uow.session.add(product)
        uow.session.flush()

        batch = Batch(
            organization_id=org.id,
            site_id=site.id,
            product_id=product.id,
            batch_number="AUR-B-2026-001",
            manufacturing_date=date(2026, 2, 1),
            expiry_date=date(2028, 2, 1),
            batch_size_value=Decimal("100000.0000"),
            batch_size_unit="CAPSULES",
            batch_status="IN_PROCESS",
            quality_review_status="PENDING",
        )
        uow.session.add(batch)
        uow.session.flush()

        # Controlled certificate & raw data documents in vault
        cal_doc = Document(
            organization_id=org.id,
            site_id=site.id,
            document_code="DOC-CAL-CERT-01",
            document_type="COA",
            title="HPLC Calibration Certificate 2026",
            status="ACTIVE",
        )
        qc_doc = Document(
            organization_id=org.id,
            site_id=site.id,
            document_code="DOC-QC-RAW-01",
            document_type="QC_RAW_DATA",
            title="HPLC Chromatogram Run 01",
            status="ACTIVE",
        )
        uow.session.add_all([cal_doc, qc_doc])
        uow.session.flush()

        cal_doc_ver = DocumentVersion(
            document_id=cal_doc.id,
            version_label="1.0",
            version_number=1,
            object_key=f"tenants/{org.id}/cal_cert_v1.pdf",
            original_filename="cal_cert_v1.pdf",
            mime_type="application/pdf",
            byte_size=2048,
            checksum_sha256="a" * 64,
            scan_status="CLEAN",
            approval_status="APPROVED",
            uploaded_by_user_id=qc_head_user.id,
        )
        qc_doc_ver = DocumentVersion(
            document_id=qc_doc.id,
            version_label="1.0",
            version_number=1,
            object_key=f"tenants/{org.id}/qc_raw_v1.pdf",
            original_filename="qc_raw_v1.pdf",
            mime_type="application/pdf",
            byte_size=4096,
            checksum_sha256="b" * 64,
            scan_status="CLEAN",
            approval_status="APPROVED",
            uploaded_by_user_id=analyst_user.id,
        )
        uow.session.add_all([cal_doc_ver, qc_doc_ver])
        uow.session.flush()

        org_id = org.id
        site_id = site.id
        product_id = product.id
        batch_id = batch.id
        qc_head_uid = qc_head_user.id
        analyst_uid = analyst_user.id
        analyst_mid = analyst_member.id
        cal_doc_id, cal_doc_ver_id = cal_doc.id, cal_doc_ver.id
        qc_doc_id, qc_doc_ver_id = qc_doc.id, qc_doc_ver.id

    h_qc_head = {
        "Authorization": f"Bearer {create_access_token(subject=str(qc_head_uid))}",
        "X-Organization-Id": str(org_id),
    }
    h_analyst = {
        "Authorization": f"Bearer {create_access_token(subject=str(analyst_uid))}",
        "X-Organization-Id": str(org_id),
    }

    # 1. Create Equipment & Calibration Record
    r_eq = api_client.post(
        "/api/v1/equipment",
        headers=h_qc_head,
        json={
            "site_id": str(site_id),
            "equipment_code": "HPLC-AUR-01",
            "equipment_name": "Waters Alliance HPLC 2695",
            "equipment_category": "CHROMATOGRAPHY",
            "manufacturer": "Waters Corp",
            "model_number": "e2695",
            "serial_number": "SN-WAT-99812",
            "commissioned_on": "2025-06-01",
            "operational_status": "ACTIVE",
            "qualification_status": "QUALIFIED",
            "calibration_required": True,
            "calibration_frequency_days": 180,
        },
    )
    assert r_eq.status_code == 201, r_eq.text
    equipment_id = r_eq.json()["id"]

    # List & Get & Patch Equipment
    r_eq_list = api_client.get("/api/v1/equipment", headers=h_qc_head)
    assert r_eq_list.status_code == 200
    assert r_eq_list.json()["total"] == 1

    r_eq_patch = api_client.patch(
        f"/api/v1/equipment/{equipment_id}",
        headers=h_qc_head,
        json={
            "row_version": 1,
            "description": "Primary QC Lab HPLC system for Amoxicillin assay",
        },
    )
    assert r_eq_patch.status_code == 200
    assert r_eq_patch.json()["row_version"] == 2

    # Check calibration validity before any calibration record exists -> MISSING_RECORD
    r_val_missing = api_client.get(
        f"/api/v1/equipment/{equipment_id}/calibration-validity",
        headers=h_qc_head,
        params={"at": "2026-02-15T10:00:00Z"},
    )
    assert r_val_missing.status_code == 200
    assert r_val_missing.json()["validity_status"] == "MISSING_RECORD"

    # Analyst/Supervisor records calibration record (DRAFT)
    r_cal = api_client.post(
        f"/api/v1/equipment/{equipment_id}/calibrations",
        headers=h_analyst,
        json={
            "calibration_code": "CAL-HPLC-2026-01",
            "calibrated_at": "2026-01-15T08:00:00Z",
            "due_at": "2026-07-15T08:00:00Z",
            "calibration_provider": "Waters Metrology India",
            "certificate_number": "CERT-2026-881",
            "certificate_document_id": str(cal_doc_id),
            "certificate_document_version_id": str(cal_doc_ver_id),
            "calibration_result": "PASS",
            "record_status": "UNDER_REVIEW",
        },
    )
    assert r_cal.status_code == 201, r_cal.text
    cal_id = r_cal.json()["id"]

    # Before approval, historical validity at 2026-02-15 is UNVERIFIED
    r_val_unverified = api_client.get(
        f"/api/v1/equipment/{equipment_id}/calibration-validity",
        headers=h_qc_head,
        params={"at": "2026-02-15T10:00:00Z"},
    )
    assert r_val_unverified.status_code == 200
    assert r_val_unverified.json()["validity_status"] == "UNVERIFIED"

    # QC Head (distinct actor) approves calibration record
    r_cal_rev = api_client.post(
        f"/api/v1/equipment/{equipment_id}/calibrations/{cal_id}/review",
        headers=h_qc_head,
        json={
            "row_version": 1,
            "decision": "APPROVED",
            "comments": "NABL traceable calibration certificate verified.",
        },
    )
    assert r_cal_rev.status_code == 200, r_cal_rev.text
    assert r_cal_rev.json()["record_status"] == "APPROVED"
    assert r_cal_rev.json()["approval_record_id"] is not None

    # Verify historical validity across 3 timestamps: before (NOT_CALIBRATED), during (VALID), after (EXPIRED)
    r_val_before = api_client.get(
        f"/api/v1/equipment/{equipment_id}/calibration-validity",
        headers=h_qc_head,
        params={"at": "2026-01-01T10:00:00Z"},
    )
    assert r_val_before.json()["validity_status"] == "NOT_CALIBRATED"

    r_val_valid = api_client.get(
        f"/api/v1/equipment/{equipment_id}/calibration-validity",
        headers=h_qc_head,
        params={"at": "2026-02-15T10:00:00Z"},
    )
    assert r_val_valid.json()["validity_status"] == "VALID"
    assert r_val_valid.json()["applicable_calibration_record_id"] == cal_id

    r_val_expired = api_client.get(
        f"/api/v1/equipment/{equipment_id}/calibration-validity",
        headers=h_qc_head,
        params={"at": "2026-08-01T10:00:00Z"},
    )
    assert r_val_expired.json()["validity_status"] == "EXPIRED"

    # 2. Create QC Test Definitions (Assay & Dissolution)
    r_td_assay = api_client.post(
        "/api/v1/qc/test-definitions",
        headers=h_qc_head,
        json={
            "site_id": str(site_id),
            "product_id": str(product_id),
            "test_code": "AMOX-ASSAY-HPLC",
            "version_number": "1.0",
            "test_name": "Amoxicillin Assay (% Label Claim)",
            "test_method_reference": "IP 2022 / SOP-QC-014",
            "test_type": "NUMERIC_RANGE",
            "expected_unit_code": "%W/W",
            "min_limit": "92.500000",
            "max_limit": "107.500000",
            "target_value": "100.000000",
            "acceptance_criteria": "92.5% to 107.5% of labeled amount",
            "status": "ACTIVE",
        },
    )
    assert r_td_assay.status_code == 201, r_td_assay.text
    td_assay_id = r_td_assay.json()["id"]

    r_td_diss = api_client.post(
        "/api/v1/qc/test-definitions",
        headers=h_qc_head,
        json={
            "site_id": str(site_id),
            "product_id": str(product_id),
            "test_code": "AMOX-DISSOLUTION",
            "version_number": "1.0",
            "test_name": "Dissolution in 60 min (Q=80%)",
            "test_method_reference": "USP <711>",
            "test_type": "NUMERIC_MIN",
            "expected_unit_code": "%",
            "min_limit": "80.000000",
            "acceptance_criteria": "Not less than 80.0% (Q) in 60 minutes",
            "status": "ACTIVE",
        },
    )
    assert r_td_diss.status_code == 201, r_td_diss.text
    td_diss_id = r_td_diss.json()["id"]

    # List & Get Test Definitions
    r_td_list = api_client.get("/api/v1/qc/test-definitions", headers=h_analyst)
    assert r_td_list.status_code == 200
    assert r_td_list.json()["total"] == 2
    assert (
        api_client.get(
            f"/api/v1/qc/test-definitions/{td_assay_id}", headers=h_analyst
        ).status_code
        == 200
    )

    # 3. Register QC Sample for Batch
    r_sample = api_client.post(
        f"/api/v1/batches/{batch_id}/qc-samples",
        headers=h_analyst,
        json={
            "sample_identifier": "SMP-AUR-2026-001",
            "sample_type": "FINISHED_PRODUCT",
            "sampled_at": "2026-02-15T09:00:00Z",
            "sample_quantity": "60.0000",
            "sample_unit_code": "CAPSULES",
            "storage_location": "QC-RETENTION-RACK-A2",
            "supporting_document_id": str(qc_doc_id),
            "supporting_document_version_id": str(qc_doc_ver_id),
        },
    )
    assert r_sample.status_code == 201, r_sample.text
    sample_id = r_sample.json()["id"]

    assert (
        len(
            api_client.get(
                f"/api/v1/batches/{batch_id}/qc-samples", headers=h_analyst
            ).json()
        )
        == 1
    )
    assert (
        api_client.get(
            f"/api/v1/qc/samples/{sample_id}", headers=h_analyst
        ).status_code
        == 200
    )

    # 4. Record Passing Assay Result & Out-of-Specification (OOS) Dissolution Result
    r_res_pass = api_client.post(
        f"/api/v1/qc/samples/{sample_id}/results",
        headers=h_analyst,
        json={
            "test_definition_id": td_assay_id,
            "result_type": "NUMERIC",
            "numeric_result": "99.400000",
            "unit_code": "%W/W",
            "equipment_id": equipment_id,
            "tested_at": "2026-02-15T11:30:00Z",
            "supporting_document_id": str(qc_doc_id),
            "supporting_document_version_id": str(qc_doc_ver_id),
            "comments": "Assay peak area within specification.",
        },
    )
    assert r_res_pass.status_code == 201, r_res_pass.text
    res_pass_data = r_res_pass.json()
    assert res_pass_data["result_outcome"] == "PASS"
    assert res_pass_data["is_out_of_specification_flagged"] is False
    assert res_pass_data["review_status"] == "PENDING_REVIEW"
    res_pass_id = res_pass_data["id"]

    r_res_oos = api_client.post(
        f"/api/v1/qc/samples/{sample_id}/results",
        headers=h_analyst,
        json={
            "test_definition_id": td_diss_id,
            "result_type": "NUMERIC",
            "numeric_result": "71.200000",
            "unit_code": "%",
            "equipment_id": equipment_id,
            "tested_at": "2026-02-15T12:15:00Z",
            "comments": "Dissolution below Q=80% threshold.",
        },
    )
    assert r_res_oos.status_code == 201, r_res_oos.text
    res_oos_data = r_res_oos.json()
    assert res_oos_data["result_outcome"] == "FAIL"
    assert res_oos_data["is_out_of_specification_flagged"] is True
    assert res_oos_data["review_status"] == "PENDING_REVIEW"
    res_oos_id = res_oos_data["id"]

    # 5. QC Head reviews both results (distinct actor from analyst)
    r_rev_pass = api_client.post(
        f"/api/v1/qc/results/{res_pass_id}/review",
        headers=h_qc_head,
        json={"row_version": 1, "decision": "APPROVED", "comments": "Chromatogram verified."},
    )
    assert r_rev_pass.status_code == 200, r_rev_pass.text
    assert r_rev_pass.json()["review_status"] == "APPROVED"
    assert r_rev_pass.json()["approval_record_id"] is not None

    r_rev_oos = api_client.post(
        f"/api/v1/qc/results/{res_oos_id}/review",
        headers=h_qc_head,
        json={
            "row_version": 1,
            "decision": "APPROVED",
            "comments": "Confirmed OOS result; flagged for investigation.",
        },
    )
    assert r_rev_oos.status_code == 200, r_rev_oos.text
    assert r_rev_oos.json()["review_status"] == "APPROVED"
    assert r_rev_oos.json()["is_out_of_specification_flagged"] is True

    # 6. Check Batch QC History endpoint
    r_hist = api_client.get(
        f"/api/v1/batches/{batch_id}/qc-history",
        headers=h_qc_head,
    )
    assert r_hist.status_code == 200, r_hist.text
    hist = r_hist.json()
    assert hist["total_samples"] == 1
    assert hist["total_results"] == 2
    assert hist["passed_count"] == 1
    assert hist["failed_count"] == 1
    assert hist["approved_count"] == 2
    assert hist["oos_flagged_count"] == 1
    assert hist["has_out_of_specification"] is True

    # 7. Equipment Usage on a BatchExecutionRecord (if manufacturing_execution models are present)
    try:
        from app.models.domain.manufacturing_execution import (
            BatchExecutionRecord,
            MasterBatchRecord,
        )

        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            mbr = MasterBatchRecord(
                organization_id=org_id,
                site_id=site_id,
                product_id=product_id,
                mbr_code="MBR-AMOX-500",
                version_number="1.0",
                record_type="BMR",
                title="Amoxicillin 500mg Capsule Master Batch Record",
                approval_status="EFFECTIVE",
            )
            uow.session.add(mbr)
            uow.session.flush()

            exec_rec = BatchExecutionRecord(
                organization_id=org_id,
                site_id=site_id,
                batch_id=batch_id,
                master_batch_record_id=mbr.id,
                execution_code="BMR-EXEC-001",
                execution_type="BMR",
                manufacturing_stage="ENCAPSULATION",
                execution_sequence=1,
                started_at=datetime(2026, 2, 15, 8, 0, tzinfo=timezone.utc),
                execution_status="IN_PROGRESS",
                operator_user_id=analyst_uid,
                operator_membership_id=analyst_mid,
            )
            uow.session.add(exec_rec)
            uow.session.flush()
            exec_rec_id = exec_rec.id

        r_usage = api_client.post(
            f"/api/v1/execution-records/{exec_rec_id}/equipment-usage",
            headers=h_analyst,
            json={
                "equipment_id": equipment_id,
                "usage_start_at": "2026-02-15T10:00:00Z",
                "usage_end_at": "2026-02-15T12:30:00Z",
                "usage_purpose": "In-process & finished product assay verification",
            },
        )
        assert r_usage.status_code == 201, r_usage.text
        usage_data = r_usage.json()
        assert usage_data["calibration_status_at_use"] == "VALID"
        assert usage_data["applicable_calibration_record_id"] == cal_id

        r_usage_list = api_client.get(
            f"/api/v1/execution-records/{exec_rec_id}/equipment-usage",
            headers=h_qc_head,
        )
        assert r_usage_list.status_code == 200
        assert len(r_usage_list.json()) == 1
    except ImportError:
        pass

    # Verify AuditEvents and ApprovalRecords persisted
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        approvals = list(
            uow.session.scalars(
                select(ApprovalRecord).where(ApprovalRecord.organization_id == org_id)
            ).all()
        )
        assert len(approvals) == 3  # 1 calibration review + 2 QC result reviews

        audit_actions = {
            ev.action
            for ev in uow.session.scalars(
                select(AuditEvent).where(AuditEvent.organization_id == org_id)
            ).all()
        }
        assert "EQUIPMENT_CREATED" in audit_actions
        assert "CALIBRATION_RECORD_CREATED" in audit_actions
        assert "CALIBRATION_RECORD_REVIEWED" in audit_actions
        assert "QC_TEST_DEFINITION_CREATED" in audit_actions
        assert "QC_SAMPLE_REGISTERED" in audit_actions
        assert "QC_TEST_RESULT_RECORDED" in audit_actions
        assert "QC_TEST_RESULT_REVIEWED" in audit_actions
