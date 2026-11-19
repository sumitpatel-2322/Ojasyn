import io
import json
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.database import UnitOfWork
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
from app.integrations.llm_provider import LLMProvider
from app.integrations.storage_provider import LocalPrivateVaultAdapter
from app.models.domain.complaints import ComplaintBatchLink
from app.models.domain.documents import Document, DocumentVersion
from app.models.domain.governance import ApprovalRecord, OutboxEvent
from app.models.domain.identity import (
    MembershipRoleAssignment,
    OrganizationMembership,
    Role,
    SiteAssignment,
    User,
)
from app.models.domain.investigations import (
    EvidenceReference,
    GeneratedReport,
    Investigation,
    InvestigationRun,
    SOPComplianceResult,
)
from app.models.domain.manufacturing_execution import (
    BatchExecutionRecord,
    BatchExecutionStep,
    MasterBatchRecord,
    ProcessParameterObservation,
)
from app.models.domain.products_materials import Product
from app.models.domain.qc_equipment import (
    CalibrationRecord,
    Equipment,
    QCSample,
    QCTestDefinition,
    QCTestResult,
)
from app.models.domain.qms_loop import Deviation
from app.models.domain.sops import SOP, SOPRevision, SOPStep
from app.models.domain.traceability import Batch
from app.services.document_control_service import DocumentControlService
from app.services.investigation_service import InvestigationService
from app.worker import OutboxWorker
from tests.conftest import create_seeded_tenant_actor


def _auth_headers(user: User, org_id: uuid.UUID) -> Dict[str, str]:
    token = create_access_token(subject=str(user.id))
    return {
        "Authorization": f"Bearer {token}",
        "X-Organization-Id": str(org_id),
    }


def _seed_second_user(db, *, org_id: uuid.UUID, site_id: uuid.UUID, email: str, role_code: str) -> User:
    user = User(
        email=email,
        full_name=f"User {email}",
        password_hash=get_password_hash("StrongPass!123"),
        account_status=UserAccountStatus.ACTIVE.value,
    )
    db.add(user)
    db.flush()
    mem = OrganizationMembership(
        user_id=user.id,
        organization_id=org_id,
        member_type=MemberType.EMPLOYEE.value,
        membership_status=MembershipStatus.ACTIVE.value,
        employee_code=f"EMP-{uuid.uuid4().hex[:6]}",
    )
    db.add(mem)
    db.flush()
    db.add(
        SiteAssignment(
            organization_id=org_id,
            membership_id=mem.id,
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
            membership_id=mem.id,
            role_id=role.id,
            scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
            site_id=None,
            granted_by_user_id=user.id,
            approved_by_user_id=user.id,
            status=RoleAssignmentStatus.ACTIVE.value,
        )
    )
    db.flush()
    return user


class _EvidenceAwareFakeTransport:
    """
    Fake LLMTransport that parses `available_evidence_ids` from the untrusted prompt
    and returns a valid `RCASynthesisLLMOutput` citing persisted EvidenceReference IDs.
    """

    def __init__(self, *, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model_name: str,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
    ) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("Simulated transient LLM gateway timeout")

        inner = user_prompt.split("<untrusted_input>", 1)[1].rsplit("</untrusted_input>", 1)[0].strip()
        data = json.loads(inner)
        ev_ids: List[str] = data.get("available_evidence_ids") or []
        cited = ev_ids[:2] if len(ev_ids) >= 2 else ev_ids
        return json.dumps(
            {
                "executive_summary": "Drying temperature excursion and expired balance calibration observed.",
                "findings": [
                    {
                        "category": "PROCESS_PARAMETER_EXCURSION",
                        "title": "AI-synthesized drying excursion pattern",
                        "statement": "Observation indicates drying exceeded 50 C while calibration was expired.",
                        "severity": "HIGH",
                        "evidence_ref_ids": cited,
                    }
                ],
                "hypotheses": [
                    {
                        "category": "MANUFACTURING_PROCESS",
                        "title": "Dryer setpoint drift during granulation drying",
                        "description": "Granulation drying reached 54 C (SOP limit 45-50 C).",
                        "supporting_evidence_ids": cited,
                        "contradicting_evidence_ids": [],
                        "uncertainty_level": "LOW",
                        "confidence_score": "0.8800",
                        "recommended_verification": "Inspect dryer PID controller logs and thermal sensor.",
                        "limitations": "Distribution shipment records were not attached for this lot.",
                    }
                ],
                "limitations": ["No shipment records linked to batch."],
            }
        )


def test_phase5_pause_resume_compliance_docx_qa_review_deviation_and_guarded_close(
    api_client, sqlite_db, tmp_path, monkeypatch
) -> None:
    session_factory = sqlite_db["session_factory"]
    now = datetime.now(timezone.utc)
    vault_dir = tmp_path / "vault"
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(vault_dir))
    get_settings.cache_clear()

    # Seed Phase 1-4 fixtures + two ambiguous batches (LOT-2026-01A, LOT-2026-01B)
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        db = uow.session
        seeded = create_seeded_tenant_actor(
            db,
            org_name="Zenith Formulations",
            site_code="ZF-01",
            email="officer@zenith.in",
            role_code="QA_OFFICER",
            scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
        )
        org = seeded["organization"]
        site = seeded["site"]
        qa_officer = seeded["user"]
        qa_officer_mem = seeded["membership"]

        qa_head = _seed_second_user(
            db, org_id=org.id, site_id=site.id, email="qahead@zenith.in", role_code="QA_HEAD"
        )
        qms_head = _seed_second_user(
            db, org_id=org.id, site_id=site.id, email="qmshead@zenith.in", role_code="QMS_HEAD"
        )

        # Store a real BMR source document in vault
        doc_svc = DocumentControlService(storage=LocalPrivateVaultAdapter(root_dir=vault_dir))
        bmr_doc = doc_svc.upload_in_tx(
            db,
            organization_id=org.id,
            uploaded_by_user_id=qa_officer.id,
            file_bytes=b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n",
            original_filename="bmr_lot01a.pdf",
            claimed_mime_type="application/pdf",
            category="BMR",
            site_id=site.id,
        )
        bmr_ver = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == bmr_doc.id))
        assert bmr_ver is not None

        prod = Product(
            organization_id=org.id,
            site_id=site.id,
            product_code="CIPRO-500",
            generic_name="Ciprofloxacin",
            brand_name="CiproZen",
            dosage_form="TABLET",
            strength_value=Decimal("500.0"),
            strength_unit="mg",
            product_status="ACTIVE",
        )
        db.add(prod)
        db.flush()

        sop = SOP(
            organization_id=org.id,
            site_id=site.id,
            sop_code="SOP-DRY-01",
            title="Fluid Bed Drying",
            sop_status="ACTIVE",
        )
        db.add(sop)
        db.flush()

        rev = SOPRevision(
            organization_id=org.id,
            sop_id=sop.id,
            site_id=site.id,
            revision_number="2.0",
            revision_sequence=1,
            effective_from=now - timedelta(days=60),
            revision_status="EFFECTIVE",
            approval_status="APPROVED",
            created_by_user_id=qa_head.id,
        )
        db.add(rev)
        db.flush()

        s_step = SOPStep(
            organization_id=org.id,
            sop_revision_id=rev.id,
            step_code="DRY-01",
            step_sequence=1,
            title="Drying Temperature",
            instruction="Keep between 45 and 50 C",
            is_mandatory=True,
            parameter_name="Drying Temperature",
            parameter_code="DRY_TEMP",
            expected_data_type="NUMERIC",
            unit_code="C",
            min_value=Decimal("45.0"),
            max_value=Decimal("50.0"),
        )
        db.add(s_step)
        db.flush()

        mbr = MasterBatchRecord(
            organization_id=org.id,
            site_id=site.id,
            product_id=prod.id,
            mbr_code="MBR-CIP-01",
            version_number="1.0",
            title="Cipro Tablet MBR",
            applicable_sop_revision_ids=[str(rev.id)],
            approval_status="EFFECTIVE",
        )
        db.add(mbr)
        db.flush()

        b1 = Batch(
            organization_id=org.id,
            site_id=site.id,
            product_id=prod.id,
            batch_number="LOT-2026-01A",
            manufacturing_date=date(2026, 3, 1),
            expiry_date=date(2028, 3, 1),
            batch_size_value=Decimal("10000.0"),
            batch_size_unit="TABLET",
            batch_status=BatchStatus.RELEASED.value,
            master_batch_record_id=mbr.id,
            primary_document_id=bmr_doc.id,
        )
        b2 = Batch(
            organization_id=org.id,
            site_id=site.id,
            product_id=prod.id,
            batch_number="LOT-2026-01B",
            manufacturing_date=date(2026, 3, 2),
            expiry_date=date(2028, 3, 2),
            batch_size_value=Decimal("10000.0"),
            batch_size_unit="TABLET",
            batch_status=BatchStatus.RELEASED.value,
            master_batch_record_id=mbr.id,
        )
        db.add_all([b1, b2])
        db.flush()

        equip = Equipment(
            organization_id=org.id,
            site_id=site.id,
            equipment_code="FBD-01",
            equipment_name="Fluid Bed Dryer 01",
            equipment_category="DRYER",
            operational_status="ACTIVE",
            qualification_status="QUALIFIED",
            calibration_required=True,
            calibration_frequency_days=180,
        )
        db.add(equip)
        db.flush()

        # Calibration expired BEFORE execution time!
        cal = CalibrationRecord(
            organization_id=org.id,
            site_id=site.id,
            equipment_id=equip.id,
            calibration_code="CAL-FBD-OLD",
            calibrated_at=now - timedelta(days=90),
            due_at=now - timedelta(days=20),
            calibration_provider="MetroCal",
            calibration_result="PASS",
            record_status="APPROVED",
            recorded_by_user_id=qa_officer.id,
            recorded_by_membership_id=qa_officer_mem.id,
        )
        db.add(cal)
        db.flush()

        exec_rec = BatchExecutionRecord(
            organization_id=org.id,
            site_id=site.id,
            batch_id=b1.id,
            master_batch_record_id=mbr.id,
            execution_code="EX-CIP-01",
            manufacturing_stage="DRYING",
            execution_sequence=1,
            operator_user_id=qa_officer.id,
            operator_membership_id=qa_officer_mem.id,
            execution_status="COMPLETED",
            started_at=now - timedelta(days=5),
            completed_at=now - timedelta(days=4),
            primary_document_id=bmr_doc.id,
            primary_document_version_id=bmr_ver.id,
        )
        db.add(exec_rec)
        db.flush()

        exec_step = BatchExecutionStep(
            organization_id=org.id,
            site_id=site.id,
            batch_execution_record_id=exec_rec.id,
            step_code="DRY-01",
            step_sequence=1,
            description="Granule drying",
            sop_id=sop.id,
            sop_code=sop.sop_code,
            sop_revision_id=rev.id,
            sop_step_id=s_step.id,
            step_status="COMPLETED",
            operator_user_id=qa_officer.id,
            operator_membership_id=qa_officer_mem.id,
            executed_at=now - timedelta(days=5),
            equipment_id=equip.id,
        )
        db.add(exec_step)
        db.flush()

        obs = ProcessParameterObservation(
            organization_id=org.id,
            site_id=site.id,
            batch_execution_record_id=exec_rec.id,
            batch_execution_step_id=exec_step.id,
            sop_step_id=s_step.id,
            parameter_code="DRY_TEMP",
            parameter_name="Drying Temperature",
            value_type="NUMERIC",
            numeric_value=Decimal("54.0"),
            unit_code="C",
            observed_at=now - timedelta(days=5),
            operator_user_id=qa_officer.id,
            operator_membership_id=qa_officer_mem.id,
            source_document_id=bmr_doc.id,
            source_document_version_id=bmr_ver.id,
            source_page_number=2,
            source_section_ref="Section 4.1",
        )
        db.add(obs)
        db.flush()

        qc_def = QCTestDefinition(
            organization_id=org.id,
            site_id=site.id,
            product_id=prod.id,
            test_code="ASSAY-CIP",
            version_number="1.0",
            test_name="Assay %",
            test_method_reference="USP <621>",
            test_type="NUMERIC_RANGE",
            expected_unit_code="%",
            min_limit=Decimal("90.0"),
            max_limit=Decimal("110.0"),
            acceptance_criteria="90.0% to 110.0%",
            status="ACTIVE",
        )
        db.add(qc_def)
        db.flush()

        qc_sample = QCSample(
            organization_id=org.id,
            site_id=site.id,
            batch_id=b1.id,
            product_id=prod.id,
            sample_identifier="SMP-CIP-01",
            sample_type="FINISHED_PRODUCT",
            sampled_at=now - timedelta(days=4),
            sampled_by_user_id=qa_officer.id,
            sampled_by_membership_id=qa_officer_mem.id,
            sample_quantity=Decimal("20.0"),
            sample_unit_code="TABLET",
        )
        db.add(qc_sample)
        db.flush()

        qc_res = QCTestResult(
            organization_id=org.id,
            site_id=site.id,
            batch_id=b1.id,
            qc_sample_id=qc_sample.id,
            test_definition_id=qc_def.id,
            specification_version_applied="1.0",
            result_type="NUMERIC",
            numeric_result=Decimal("98.5"),
            unit_code="%",
            tested_at=now - timedelta(days=3),
            analyst_user_id=qa_officer.id,
            analyst_membership_id=qa_officer_mem.id,
            result_outcome="PASS",
            review_status="PENDING_REVIEW",
        )
        db.add(qc_res)
        db.flush()

        org_id = org.id
        site_id = site.id
        prod_id = prod.id
        b1_id = b1.id
        bmr_checksum = bmr_ver.checksum_sha256

        officer_hdrs = _auth_headers(qa_officer, org_id)
        qa_head_hdrs = _auth_headers(qa_head, org_id)
        qms_head_hdrs = _auth_headers(qms_head, org_id)

    # 1. Register complaint with ambiguous reported_batch_number "LOT-2026-01"
    comp_resp = api_client.post(
        "/api/v1/complaints",
        headers=officer_hdrs,
        json={
            "organization_id": str(org_id),
            "site_id": str(site_id),
            "product_id": str(prod_id),
            "reported_product_name": "CiproZen 500",
            "reported_batch_number": "LOT-2026-01",
            "description": "Tablets appear discolored and brittle.",
            "source_channel": "STAFF",
        },
    )
    assert comp_resp.status_code == 201
    complaint_id = comp_resp.json()["id"]

    # 2. Open Investigation (D1) & trigger Run with Idempotency-Key -> 202
    inv_resp = api_client.post(
        "/api/v1/investigations",
        headers=officer_hdrs,
        json={
            "complaint_id": complaint_id,
            "scope_summary": "Investigate granulation drying & QC records.",
        },
    )
    assert inv_resp.status_code == 201
    inv_id = inv_resp.json()["id"]
    assert inv_resp.json()["investigation_number"].endswith("-000001")

    run_resp = api_client.post(
        f"/api/v1/investigations/{inv_id}/runs",
        headers={**officer_hdrs, "Idempotency-Key": "run-key-001"},
        json={},
    )
    assert run_resp.status_code == 202
    run_id = run_resp.json()["run_id"]

    # Idempotent replay returns 200 with same run_id
    run_replay = api_client.post(
        f"/api/v1/investigations/{inv_id}/runs",
        headers={**officer_hdrs, "Idempotency-Key": "run-key-001"},
        json={},
    )
    assert run_replay.status_code == 200
    assert run_replay.json()["run_id"] == run_id
    assert run_replay.json()["idempotent_replay"] is True

    # 3. Configure OutboxWorker with fake LLM transport and run_once -> pauses at WAITING_FOR_HUMAN!
    transport = _EvidenceAwareFakeTransport(fail_times=0)
    settings = Settings(
        DATABASE_URL=sqlite_db["db_url"],
        JWT_SIGNING_KEY="test-only-signing-key-with-at-least-32-characters",
        LOCAL_STORAGE_ROOT=str(vault_dir),
    )
    fake_provider = LLMProvider(transport=transport, settings=settings)

    def _run_handler(sf, event, _ctx):
        payload = event.payload_json or {}
        r_id = uuid.UUID(str(payload.get("run_id") or event.aggregate_id))
        InvestigationService().execute_run(r_id, session_factory=sf, llm_provider=fake_provider)

    worker = OutboxWorker(session_factory=session_factory)
    worker.register_external_handler("InvestigationRunRequested", _run_handler)
    worker.register_external_handler("InvestigationRunResumeRequested", _run_handler)

    worker.run_once()

    run_after_pause = api_client.get(f"/api/v1/investigations/runs/{run_id}", headers=officer_hdrs)
    assert run_after_pause.status_code == 200
    assert run_after_pause.json()["status"] == "WAITING_FOR_HUMAN"
    assert run_after_pause.json()["waiting_reason"] == "AMBIGUOUS_BATCH_CANDIDATES"
    assert run_after_pause.json()["resume_from_step_seq"] == 1
    assert transport.calls == 0  # LLM was never called while paused!

    # Check candidate ComplaintBatchLink rows created
    comp_detail = api_client.get(f"/api/v1/complaints/{complaint_id}", headers=officer_hdrs).json()
    candidate_links = [
        lnk for lnk in comp_detail["batch_links"] if lnk["resolution_outcome"] == "MULTIPLE_CANDIDATES"
    ]
    assert len(candidate_links) == 2
    b1_link = next(lnk for lnk in candidate_links if lnk["batch_id"] == str(b1_id))

    # 4. Human resolves batch candidate -> automatically queues InvestigationRunResumeRequested!
    res_resp = api_client.post(
        f"/api/v1/complaints/{complaint_id}/batch-candidates/resolve",
        headers=officer_hdrs,
        json={
            "action": "VERIFY_CANDIDATE",
            "candidate_link_id": b1_link["id"],
            "notes": "Confirmed LOT-2026-01A from strip photo.",
        },
    )
    assert res_resp.status_code == 200
    assert res_resp.json()["match_status"] == "VERIFIED"

    run_resumed_queued = api_client.get(f"/api/v1/investigations/runs/{run_id}", headers=officer_hdrs).json()
    assert run_resumed_queued["status"] == "QUEUED"
    assert run_resumed_queued["resume_from_step_seq"] == 2

    # 5. Run worker again -> resumes from step 2 and completes full pipeline!
    worker.run_once()

    run_done = api_client.get(f"/api/v1/investigations/{inv_id}/runs/{run_id}", headers=officer_hdrs).json()
    assert run_done["status"] == "COMPLETED"
    assert len(run_done["steps"]) == 5
    assert [s["node_name"] for s in run_done["steps"]] == [
        "batch_resolution",
        "evidence_collection",
        "deterministic_sop_compliance",
        "rca_synthesis",
        "finalize_run",
    ]
    assert all(s["status"] == "COMPLETED" for s in run_done["steps"])
    assert transport.calls == 1

    # Verify D4 EvidenceReference provenance + SHA-256
    ev_list = api_client.get(f"/api/v1/investigations/{inv_id}/evidence", headers=officer_hdrs).json()
    assert any(e["document_checksum_sha256"] == bmr_checksum for e in ev_list)

    # Verify D3 Deterministic SOPComplianceResult (FAILs + suggested deviation, ZERO auto-created Deviations!)
    comp_results = api_client.get(
        f"/api/v1/investigations/{inv_id}/compliance-checks", headers=officer_hdrs
    ).json()
    fail_results = [c for c in comp_results if c["status"] == "FAIL"]
    assert len(fail_results) >= 2  # 54 C > 50 C AND expired calibration!
    assert all(c["deviation_suggested"] is True and c["suggested_deviation_payload_json"] for c in fail_results)
    assert all(c["evidence_ref_id"] is not None for c in comp_results)

    devs_before = api_client.get("/api/v1/deviations", headers=officer_hdrs).json()
    assert devs_before["total"] == 0

    # Regression check 1 & 2: Repeat compliance-checks + GET manufacturing-evidence must NOT crash with FK DELETE RESTRICT,
    # and invalid run_id must cleanly return 404 NotFoundError.
    repeat_chk = api_client.post(
        f"/api/v1/investigations/{inv_id}/compliance-checks",
        headers=officer_hdrs,
        json={"batch_id": str(b1_id), "run_id": run_id},
    )
    assert repeat_chk.status_code == 200
    assert all(row["deterministic_check_id"] is None for row in repeat_chk.json())

    mfg_ev_after = api_client.get(
        f"/api/v1/batches/{b1_id}/manufacturing-evidence",
        headers=officer_hdrs,
    )
    assert mfg_ev_after.status_code == 200

    bad_run_chk = api_client.post(
        f"/api/v1/investigations/{inv_id}/compliance-checks",
        headers=officer_hdrs,
        json={"batch_id": str(b1_id), "run_id": str(uuid.uuid4())},
    )
    assert bad_run_chk.status_code == 404

    # Regression check 3: Idempotent execute_run redelivery on already-COMPLETED run must not re-run or bump attempt_count.
    redelivered_run = InvestigationService().execute_run(
        uuid.UUID(run_id), session_factory=session_factory, llm_provider=fake_provider
    )
    assert redelivered_run.status == "COMPLETED"
    assert redelivered_run.attempt_count == run_done["attempt_count"]
    assert transport.calls == 1

    # Verify D5 findings (verified=True vs False) and hypotheses
    inv_detail = api_client.get(f"/api/v1/investigations/{inv_id}", headers=officer_hdrs).json()
    assert inv_detail["status"] == "COMPLETED"
    assert any(f["is_verified_observation"] is True for f in inv_detail["findings"])
    assert any(f["is_verified_observation"] is False for f in inv_detail["findings"])
    assert len(inv_detail["hypotheses"]) == 1
    hyp_id = inv_detail["hypotheses"][0]["id"]

    # 6. D6 Generate DOCX report & download
    rep_resp = api_client.post(
        f"/api/v1/investigations/{inv_id}/reports",
        headers=officer_hdrs,
        json={"run_id": run_id},
    )
    assert rep_resp.status_code == 201
    rep_data = rep_resp.json()
    rep_id = rep_data["id"]
    assert rep_data["status"] == "GENERATED"
    assert rep_data["checksum_sha256"]
    assert rep_data["document_version_id"]

    dl_resp = api_client.get(
        f"/api/v1/investigations/{inv_id}/reports/{rep_id}/download",
        headers=officer_hdrs,
    )
    assert dl_resp.status_code == 200
    assert dl_resp.content[:4] == b"PK\x03\x04"
    assert dl_resp.headers["X-Checksum-SHA256"] == rep_data["checksum_sha256"]
    with zipfile.ZipFile(io.BytesIO(dl_resp.content), "r") as zf:
        xml_content = zf.read("word/document.xml").decode("utf-8")
        assert inv_detail["investigation_number"] in xml_content

    # 7. E1 Human-confirmed Deviation creation from FAIL compliance suggestion
    dev_resp = api_client.post(
        f"/api/v1/deviations/from-compliance-result/{fail_results[0]['id']}",
        headers=officer_hdrs,
    )
    assert dev_resp.status_code == 201
    dev_id = dev_resp.json()["id"]
    assert dev_resp.json()["status"] == "OPEN"

    # 8. D7 Guarded complaint close tests (blocked at each guard!)
    comp_rv = api_client.get(f"/api/v1/complaints/{complaint_id}", headers=officer_hdrs).json()["row_version"]

    # Guard 1: Investigation not approved -> 409
    blk1 = api_client.post(
        f"/api/v1/complaints/{complaint_id}/close",
        headers=qa_head_hdrs,
        json={"row_version": comp_rv, "closure_reason": "Trying early close"},
    )
    assert blk1.status_code == 409
    assert blk1.json()["error_code"] == "INVESTIGATION_NOT_APPROVED"

    # Approve hypothesis & investigation, leave report unapproved -> Guard 2: Report not approved -> 409
    hyp_dec = api_client.post(
        f"/api/v1/hypotheses/{hyp_id}/decision",
        headers=qa_head_hdrs,
        json={"row_version": 1, "decision": "ACCEPTED", "comments": "Evidence confirms temp excursion."},
    )
    assert hyp_dec.status_code == 200
    assert hyp_dec.json()["status"] == "ACCEPTED"

    inv_rev = api_client.post(
        f"/api/v1/investigations/{inv_id}/review",
        headers=qa_head_hdrs,
        json={"row_version": inv_detail["row_version"], "decision": "APPROVED", "comments": "QA investigation approved."},
    )
    assert inv_rev.status_code == 200
    assert inv_rev.json()["status"] == "APPROVED"

    blk2 = api_client.post(
        f"/api/v1/complaints/{complaint_id}/close",
        headers=qa_head_hdrs,
        json={"row_version": comp_rv, "closure_reason": "Report not yet approved"},
    )
    assert blk2.status_code == 409
    assert blk2.json()["error_code"] == "REPORT_NOT_APPROVED"

    # Regression check 4: Repeated REQUEST_MORE_WORK on GeneratedReport (`GENERATED -> UNDER_REVIEW -> UNDER_REVIEW -> APPROVED`)
    req_work_1 = api_client.post(
        f"/api/v1/investigations/{inv_id}/reports/{rep_id}/review",
        headers=qa_head_hdrs,
        json={"row_version": 1, "decision": "REQUEST_MORE_WORK", "comments": "First review note."},
    )
    assert req_work_1.status_code == 200
    assert req_work_1.json()["status"] == "UNDER_REVIEW"

    req_work_2 = api_client.post(
        f"/api/v1/investigations/{inv_id}/reports/{rep_id}/review",
        headers=qa_head_hdrs,
        json={"row_version": 2, "decision": "REQUEST_MORE_WORK", "comments": "Second review note."},
    )
    assert req_work_2.status_code == 200
    assert req_work_2.json()["status"] == "UNDER_REVIEW"

    # Approve report -> Guard 3: Open mandatory deviation -> 409
    rep_rev = api_client.post(
        f"/api/v1/investigations/{inv_id}/reports/{rep_id}/review",
        headers=qa_head_hdrs,
        json={"row_version": 3, "decision": "APPROVED", "comments": "Report verified and approved."},
    )
    assert rep_rev.status_code == 200
    assert rep_rev.json()["status"] == "APPROVED"

    blk3 = api_client.post(
        f"/api/v1/complaints/{complaint_id}/close",
        headers=qa_head_hdrs,
        json={"row_version": comp_rv, "closure_reason": "Deviation still open"},
    )
    assert blk3.status_code == 409
    assert blk3.json()["error_code"] == "OPEN_MANDATORY_DEVIATIONS_EXIST"

    # Progress & close Deviation via QA Head (creator=officer, submitter=officer, closer=qms_head -> distinct!)
    dev_upd = api_client.patch(
        f"/api/v1/deviations/{dev_id}",
        headers=officer_hdrs,
        json={
            "row_version": 1,
            "status": "UNDER_INVESTIGATION",
            "immediate_containment": "Batch quarantined and dryer recalibrated.",
            "impact_assessment": "Restricted to LOT-2026-01A.",
        },
    )
    assert dev_upd.status_code == 200

    dev_sub = api_client.post(
        f"/api/v1/deviations/{dev_id}/submit-review",
        headers=officer_hdrs,
        json={"row_version": 2, "comments": "Ready for QA closure."},
    )
    assert dev_sub.status_code == 200
    assert dev_sub.json()["status"] == "QA_REVIEW"

    dev_close = api_client.post(
        f"/api/v1/deviations/{dev_id}/close",
        headers=qms_head_hdrs,
        json={"row_version": 3, "decision": "APPROVED", "closure_comments": "Containment & calibration verified."},
    )
    assert dev_close.status_code == 200
    assert dev_close.json()["status"] == "CLOSED"
    assert dev_close.json()["approval_record_id"] is not None

    # 9. Now guarded POST /complaints/{id}/close succeeds!
    close_ok = api_client.post(
        f"/api/v1/complaints/{complaint_id}/close",
        headers=qa_head_hdrs,
        json={"row_version": comp_rv, "closure_reason": "Investigation, report, and deviation closed."},
    )
    assert close_ok.status_code == 200
    assert close_ok.json()["status"] == "CLOSED"

    inv_final = api_client.get(f"/api/v1/investigations/{inv_id}", headers=qa_head_hdrs).json()
    assert inv_final["status"] == "CLOSED"


def test_run_retry_dead_letter_and_deviation_only_investigation(api_client, sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seeded = create_seeded_tenant_actor(
            uow.session,
            org_name="Retry Pharma",
            site_code="RT-01",
            email="qa@retry.in",
            role_code="QA_HEAD",
            scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
        )
        org_id = seeded["organization"].id
        site_id = seeded["site"].id
        hdrs = _auth_headers(seeded["user"], org_id)

    # Create deviation-only investigation (complaint_id is None)
    dev_resp = api_client.post(
        "/api/v1/deviations",
        headers=hdrs,
        json={
            "site_id": str(site_id),
            "title": "Standalone Deviation",
            "description": "HVAC excursion in packaging room.",
        },
    )
    assert dev_resp.status_code == 201
    dev_id = dev_resp.json()["id"]

    inv_resp = api_client.post(
        "/api/v1/investigations",
        headers=hdrs,
        json={"deviation_id": dev_id},
    )
    assert inv_resp.status_code == 201
    assert inv_resp.json()["complaint_id"] is None
    assert inv_resp.json()["deviation_id"] == dev_id
    assert inv_resp.json()["trigger_type"] == "DEVIATION"
    inv_id = inv_resp.json()["id"]

    # Trigger run with max_attempts=1 so first failure transitions to DEAD_LETTER
    run_resp = api_client.post(
        f"/api/v1/investigations/{inv_id}/runs",
        headers=hdrs,
        json={"max_attempts": 1},
    )
    assert run_resp.status_code == 202
    run_id = run_resp.json()["run_id"]

    failing_transport = _EvidenceAwareFakeTransport(fail_times=1)
    settings = Settings(
        DATABASE_URL=sqlite_db["db_url"],
        JWT_SIGNING_KEY="test-only-signing-key-with-at-least-32-characters",
    )
    provider = LLMProvider(transport=failing_transport, settings=settings)

    def _run_handler(sf, event, _ctx):
        payload = event.payload_json or {}
        r_id = uuid.UUID(str(payload.get("run_id") or event.aggregate_id))
        InvestigationService().execute_run(r_id, session_factory=sf, llm_provider=provider)

    worker = OutboxWorker(session_factory=session_factory)
    worker.register_external_handler("InvestigationRunRequested", _run_handler)

    worker.run_once()
    dead_run = api_client.get(f"/api/v1/investigations/runs/{run_id}", headers=hdrs).json()
    assert dead_run["status"] == "DEAD_LETTER"
    assert dead_run["attempt_count"] == 1

    # Manual retry recovers and completes on attempt 2!
    retry_resp = api_client.post(
        f"/api/v1/investigations/{inv_id}/runs/{run_id}/retry",
        headers=hdrs,
    )
    assert retry_resp.status_code == 202
    assert retry_resp.json()["status"] == "QUEUED"

    worker.run_once()
    ok_run = api_client.get(f"/api/v1/investigations/runs/{run_id}", headers=hdrs).json()
    assert ok_run["status"] == "COMPLETED"
    assert ok_run["attempt_count"] == 2
