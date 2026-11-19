import io
import json
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List

import pytest

from app.core.config import Settings
from app.core.database import UnitOfWork
from app.core.exceptions import ValidationError
from app.domain.enums import (
    BatchStatus,
    FindingCategory,
    HypothesisCategory,
    SOPComplianceResultStatus,
    UncertaintyLevel,
)
from app.integrations.llm_provider import LLMProvider
from app.models.domain.actors import Complainant
from app.models.domain.complaints import Complaint, ComplaintBatchLink
from app.models.domain.investigations import EvidenceReference, Investigation
from app.models.domain.manufacturing_execution import (
    BatchExecutionRecord,
    BatchExecutionStep,
    MasterBatchRecord,
    ProcessParameterObservation,
)
from app.models.domain.products_materials import Product
from app.models.domain.qms_loop import Deviation
from app.models.domain.sops import SOP, SOPRevision, SOPStep
from app.models.domain.traceability import Batch
from app.models.schemas.investigations import (
    RCASynthesisFindingItem,
    RCASynthesisHypothesisItem,
    RCASynthesisLLMOutput,
)
from app.services.investigation_service import BatchResolver, InvestigationService
from app.services.rca_analysis_service import RCAAnalysisService
from app.services.report_service import render_investigation_docx_bytes
from app.services.sop_compliance_service import SOPComplianceService
from tests.conftest import create_seeded_tenant_actor


class _CapturingTransport:
    def __init__(self, response_payload: Dict[str, Any]) -> None:
        self.response_payload = response_payload
        self.captured_prompts: List[Dict[str, str]] = []

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
        self.captured_prompts.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        return json.dumps(self.response_payload)


def test_batch_resolver_exact_normalized_fuzzy_and_not_found(sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        db = uow.session
        seeded = create_seeded_tenant_actor(
            db,
            org_name="Resolver Pharma",
            site_code="RP-01",
            email="qa@resolver.in",
            role_code="QA_HEAD",
        )
        org = seeded["organization"]
        site = seeded["site"]

        prod = Product(
            organization_id=org.id,
            site_id=site.id,
            product_code="PARA-500",
            generic_name="Paracetamol",
            dosage_form="TABLET",
            product_status="ACTIVE",
        )
        db.add(prod)
        db.flush()

        b1 = Batch(
            organization_id=org.id,
            site_id=site.id,
            product_id=prod.id,
            batch_number="PCM-2026-101",
            manufacturing_date=date(2026, 1, 1),
            expiry_date=date(2028, 1, 1),
            batch_size_value=Decimal("1000.0000"),
            batch_size_unit="TABLET",
            batch_status=BatchStatus.RELEASED.value,
        )
        b2 = Batch(
            organization_id=org.id,
            site_id=site.id,
            product_id=prod.id,
            batch_number="PCM-2026-102",
            manufacturing_date=date(2026, 1, 2),
            expiry_date=date(2028, 1, 2),
            batch_size_value=Decimal("1000.0000"),
            batch_size_unit="TABLET",
            batch_status=BatchStatus.RELEASED.value,
        )
        db.add_all([b1, b2])
        db.flush()

        resolver = BatchResolver()

        # 1. Exact match
        c_exact = Complaint(
            organization_id=org.id,
            site_id=site.id,
            complaint_number="RES-2026-0001",
            product_id=prod.id,
            reported_batch_number="PCM-2026-101",
            description="Exact batch match test.",
            status="RECEIVED",
        )
        db.add(c_exact)
        db.flush()
        ok, bid, outcome, wait_reason, _ = resolver.resolve_for_complaint_in_tx(
            db, organization_id=org.id, complaint=c_exact
        )
        assert ok is True
        assert bid == b1.id
        assert outcome == "MATCHED"
        assert wait_reason is None

        # 2. Normalized match (spacing/hyphen difference)
        c_norm = Complaint(
            organization_id=org.id,
            site_id=site.id,
            complaint_number="RES-2026-0002",
            product_id=prod.id,
            reported_batch_number="pcm 2026102",
            description="Normalized batch match test.",
            status="RECEIVED",
        )
        db.add(c_norm)
        db.flush()
        ok2, bid2, outcome2, wait_reason2, _ = resolver.resolve_for_complaint_in_tx(
            db, organization_id=org.id, complaint=c_norm
        )
        assert ok2 is True
        assert bid2 == b2.id
        assert outcome2 == "MATCHED"
        assert wait_reason2 is None

        # 3. Fuzzy multiple candidates -> pauses
        c_ambig = Complaint(
            organization_id=org.id,
            site_id=site.id,
            complaint_number="RES-2026-0003",
            product_id=prod.id,
            reported_batch_number="PCM-2026-10",
            description="Ambiguous fuzzy batch match test.",
            status="RECEIVED",
        )
        db.add(c_ambig)
        db.flush()
        ok3, bid3, outcome3, wait_reason3, link_ids3 = resolver.resolve_for_complaint_in_tx(
            db, organization_id=org.id, complaint=c_ambig
        )
        assert ok3 is False
        assert bid3 is None
        assert outcome3 == "MULTIPLE_CANDIDATES"
        assert wait_reason3 == "AMBIGUOUS_BATCH_CANDIDATES"
        assert len(link_ids3) == 2

        # 4. Not found
        c_none = Complaint(
            organization_id=org.id,
            site_id=site.id,
            complaint_number="RES-2026-0004",
            product_id=prod.id,
            reported_batch_number="UNKNOWN-XYZ-9999",
            description="No batch match test.",
            status="RECEIVED",
        )
        db.add(c_none)
        db.flush()
        ok4, bid4, outcome4, wait_reason4, _ = resolver.resolve_for_complaint_in_tx(
            db, organization_id=org.id, complaint=c_none
        )
        assert ok4 is True
        assert bid4 is None
        assert outcome4 == "NOT_FOUND"
        assert wait_reason4 is None


def test_rca_evidence_citation_gate_rejects_invalid_and_cross_investigation_ids(sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        db = uow.session
        seeded = create_seeded_tenant_actor(
            db,
            org_name="RCA Gate Pharma",
            site_code="RG-01",
            email="qa@rcagate.in",
            role_code="QA_HEAD",
        )
        org = seeded["organization"]
        site = seeded["site"]

        inv1 = Investigation(
            organization_id=org.id,
            site_id=site.id,
            investigation_number="INV-GATE-001",
            trigger_type="MANUAL",
            title="Inv 1",
            status="PROCESSING",
        )
        inv2 = Investigation(
            organization_id=org.id,
            site_id=site.id,
            investigation_number="INV-GATE-002",
            trigger_type="MANUAL",
            title="Inv 2",
            status="PROCESSING",
        )
        db.add_all([inv1, inv2])
        db.flush()

        ev1 = EvidenceReference(
            organization_id=org.id,
            investigation_id=inv1.id,
            source_type="COMPLAINT",
            source_table="complaints",
            source_record_id=uuid.uuid4(),
            quoted_value="Inv 1 evidence",
        )
        ev2 = EvidenceReference(
            organization_id=org.id,
            investigation_id=inv2.id,
            source_type="COMPLAINT",
            source_table="complaints",
            source_record_id=uuid.uuid4(),
            quoted_value="Inv 2 evidence",
        )
        db.add_all([ev1, ev2])
        db.flush()

        rca = RCAAnalysisService()

        # Non-existent UUID rejected
        bad_nonexistent = RCASynthesisLLMOutput(
            executive_summary="Summary text.",
            findings=[],
            hypotheses=[
                RCASynthesisHypothesisItem(
                    category=HypothesisCategory.MANUFACTURING_PROCESS,
                    title="Fabricated evidence",
                    description="Cites non-existent UUID.",
                    supporting_evidence_ids=[uuid.uuid4()],
                    uncertainty_level=UncertaintyLevel.HIGH,
                )
            ],
        )
        with pytest.raises(ValidationError) as exc1:
            rca.persist_ai_synthesis_in_tx(
                db,
                investigation=inv1,
                run=None,
                synthesis=bad_nonexistent,
                model_name="test-model",
                prompt_version="v1",
                created_by_user_id=None,
            )
        assert exc1.value.error_code == "INVALID_EVIDENCE_CITATION"

        # Cross-investigation EvidenceReference ID rejected!
        bad_cross_inv = RCASynthesisLLMOutput(
            executive_summary="Summary text.",
            findings=[],
            hypotheses=[
                RCASynthesisHypothesisItem(
                    category=HypothesisCategory.MANUFACTURING_PROCESS,
                    title="Cross-investigation citation",
                    description="Cites ev2 from inv2 inside inv1.",
                    supporting_evidence_ids=[ev2.id],
                    uncertainty_level=UncertaintyLevel.MEDIUM,
                )
            ],
        )
        with pytest.raises(ValidationError) as exc2:
            rca.persist_ai_synthesis_in_tx(
                db,
                investigation=inv1,
                run=None,
                synthesis=bad_cross_inv,
                model_name="test-model",
                prompt_version="v1",
                created_by_user_id=None,
            )
        assert exc2.value.error_code == "INVALID_EVIDENCE_CITATION"

        # Valid inv1 citation succeeds and sets is_verified_observation=False on AI finding
        good_out = RCASynthesisLLMOutput(
            executive_summary="Valid synthesis.",
            findings=[
                RCASynthesisFindingItem(
                    category=FindingCategory.OBSERVATION,
                    title="AI synthesis observation",
                    statement="Synthesized from ev1.",
                    evidence_ref_ids=[ev1.id],
                )
            ],
            hypotheses=[
                RCASynthesisHypothesisItem(
                    category=HypothesisCategory.SOP_OR_PROCEDURE_GAP,
                    title="Valid hypothesis",
                    description="Supported by ev1.",
                    supporting_evidence_ids=[ev1.id],
                    uncertainty_level=UncertaintyLevel.LOW,
                )
            ],
        )
        ai_findings, ai_hyps = rca.persist_ai_synthesis_in_tx(
            db,
            investigation=inv1,
            run=None,
            synthesis=good_out,
            model_name="test-model",
            prompt_version="v1",
            created_by_user_id=None,
        )
        assert len(ai_findings) == 1
        assert ai_findings[0].is_verified_observation is False
        assert len(ai_hyps) == 1
        assert ai_hyps[0].supporting_evidence_ids_json == [str(ev1.id)]


def test_pii_redactor_applied_before_investigation_llm_call(sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        db = uow.session
        seeded = create_seeded_tenant_actor(
            db,
            org_name="PII Mask Pharma",
            site_code="PM-01",
            email="qa@piimask.in",
            role_code="QA_HEAD",
        )
        org = seeded["organization"]
        site = seeded["site"]

        complainant = Complainant(
            organization_id=org.id,
            full_name="Aarav Sharma",
            email="aarav.sharma@secret-patient.in",
            phone_e164="+919876543210",
        )
        db.add(complainant)
        db.flush()

        comp = Complaint(
            organization_id=org.id,
            site_id=site.id,
            complaint_number="PII-2026-0001",
            complainant_id=complainant.id,
            description="Patient Aarav Sharma (aarav.sharma@secret-patient.in, +919876543210) reported discoloration.",
            status="RECEIVED",
        )
        db.add(comp)
        db.flush()

        inv = Investigation(
            organization_id=org.id,
            site_id=site.id,
            complaint_id=comp.id,
            investigation_number="INV-PII-001",
            trigger_type="COMPLAINT",
            title="PII Mask Test",
            status="QUEUED",
        )
        db.add(inv)
        db.flush()
        inv_id = inv.id

    svc = InvestigationService()
    # Create a run via DB and execute with capturing transport
    from app.models.domain.investigations import InvestigationRun

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        inv_obj = uow.session.get(Investigation, inv_id)
        assert inv_obj is not None
        run = InvestigationRun(
            organization_id=inv_obj.organization_id,
            investigation_id=inv_obj.id,
            run_number=1,
            status="QUEUED",
            resume_from_step_seq=2,
        )
        uow.session.add(run)
        uow.session.flush()
        run_id = run.id

    transport = _CapturingTransport(
        {
            "executive_summary": "Checked complaint evidence without PII exposure.",
            "findings": [],
            "hypotheses": [],
            "limitations": ["No batch linked."],
        }
    )
    settings = Settings(
        DATABASE_URL=sqlite_db["db_url"],
        JWT_SIGNING_KEY="test-only-signing-key-with-at-least-32-characters",
    )
    provider = LLMProvider(transport=transport, settings=settings)

    final_run = svc.execute_run(run_id, session_factory=session_factory, llm_provider=provider)
    assert final_run.status == "COMPLETED"
    assert len(transport.captured_prompts) == 1
    sent_prompt = transport.captured_prompts[0]["user_prompt"]
    assert "aarav.sharma@secret-patient.in" not in sent_prompt
    assert "+919876543210" not in sent_prompt
    assert "Aarav Sharma" not in sent_prompt
    assert "[EMAIL_1]" in sent_prompt
    assert "[PERSON_1]" in sent_prompt


def test_sop_compliance_fail_suggests_but_never_auto_creates_deviation(sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]
    now = datetime.now(timezone.utc)
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        db = uow.session
        seeded = create_seeded_tenant_actor(
            db,
            org_name="Compliance Pharma",
            site_code="CM-01",
            email="qa@comp.in",
            role_code="QA_HEAD",
        )
        org = seeded["organization"]
        site = seeded["site"]
        user = seeded["user"]
        member = seeded["membership"]

        prod = Product(
            organization_id=org.id,
            site_id=site.id,
            product_code="AZI-500",
            generic_name="Azithromycin",
            dosage_form="TABLET",
            product_status="ACTIVE",
        )
        db.add(prod)
        db.flush()

        sop = SOP(
            organization_id=org.id,
            site_id=site.id,
            sop_code="SOP-TEMP-01",
            title="Granulation Drying Temp",
            sop_status="ACTIVE",
        )
        db.add(sop)
        db.flush()

        rev = SOPRevision(
            organization_id=org.id,
            sop_id=sop.id,
            site_id=site.id,
            revision_number="1.0",
            revision_sequence=1,
            effective_from=now - timedelta(days=30),
            revision_status="EFFECTIVE",
            approval_status="APPROVED",
            created_by_user_id=user.id,
        )
        db.add(rev)
        db.flush()

        s_step = SOPStep(
            organization_id=org.id,
            sop_revision_id=rev.id,
            step_code="DRY-01",
            step_sequence=1,
            title="Drying Temperature",
            instruction="Maintain 45-50 C",
            is_mandatory=True,
            parameter_name="Drying Temp",
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
            mbr_code="MBR-AZI",
            version_number="1.0",
            title="Azi MBR",
            applicable_sop_revision_ids=[str(rev.id)],
            approval_status="EFFECTIVE",
        )
        db.add(mbr)
        db.flush()

        batch = Batch(
            organization_id=org.id,
            site_id=site.id,
            product_id=prod.id,
            batch_number="AZI-001",
            manufacturing_date=date(2026, 2, 1),
            expiry_date=date(2028, 2, 1),
            batch_size_value=Decimal("500.0"),
            batch_size_unit="TABLET",
            batch_status=BatchStatus.RELEASED.value,
            master_batch_record_id=mbr.id,
        )
        db.add(batch)
        db.flush()

        exec_rec = BatchExecutionRecord(
            organization_id=org.id,
            site_id=site.id,
            batch_id=batch.id,
            master_batch_record_id=mbr.id,
            execution_code="EX-001",
            manufacturing_stage="DRYING",
            execution_sequence=1,
            operator_user_id=user.id,
            operator_membership_id=member.id,
            execution_status="COMPLETED",
            started_at=now - timedelta(days=5),
            completed_at=now - timedelta(days=4),
        )
        db.add(exec_rec)
        db.flush()

        exec_step = BatchExecutionStep(
            organization_id=org.id,
            site_id=site.id,
            batch_execution_record_id=exec_rec.id,
            step_code="DRY-01",
            step_sequence=1,
            description="Drying",
            sop_id=sop.id,
            sop_code=sop.sop_code,
            sop_revision_id=rev.id,
            sop_step_id=s_step.id,
            step_status="COMPLETED",
            operator_user_id=user.id,
            operator_membership_id=member.id,
            executed_at=now - timedelta(days=5),
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
            parameter_name="Drying Temp",
            value_type="NUMERIC",
            numeric_value=Decimal("54.0"),
            unit_code="C",
            observed_at=now - timedelta(days=5),
            operator_user_id=user.id,
            operator_membership_id=member.id,
        )
        db.add(obs)
        db.flush()

        inv = Investigation(
            organization_id=org.id,
            site_id=site.id,
            batch_id=batch.id,
            product_id=prod.id,
            investigation_number="INV-COMP-001",
            trigger_type="MANUAL",
            title="Drying excursion check",
            status="PROCESSING",
        )
        db.add(inv)
        db.flush()

        from app.services.investigation_service import _build_worker_ctx

        results = SOPComplianceService().evaluate_in_tx(
            db, tenant_ctx=_build_worker_ctx(org.id), investigation=inv, batch_id=batch.id
        )
        fail_results = [r for r in results if r.status == SOPComplianceResultStatus.FAIL.value]
        assert len(fail_results) >= 1
        assert all(r.deviation_suggested is True and r.suggested_deviation_payload_json for r in fail_results)
        assert all(r.evidence_ref_id is not None for r in results)
        # Never auto-creates Deviation rows
        assert db.query(Deviation).count() == 0


def test_docx_renderer_produces_valid_ooxml_archive() -> None:
    snapshot = {
        "title": "Investigation Report TEST-INV-001",
        "control_page": {"report_number": "RPT-2026-0001", "version_number": 1},
        "executive_summary": {"narrative": "Drying excursion detected.", "metrics": {"compliance_fail": 1}},
        "complaint": {"complaint_number": "CMP-001", "description": "Discolored tablets"},
        "traceability": {"batch": {"batch_number": "B-1"}, "missing_links": ["MISSING_SHIPMENT_RECORDS"]},
        "compliance_results": [
            {
                "rule_identifier": "RULE_PARAM_VS_SOP_LIMIT",
                "rule_version": "1.0.0",
                "status": "FAIL",
                "actual_value_text": "54 C",
                "expected_requirement_text": "[45, 50] C",
                "explanation": "Exceeded limit",
                "evidence_ref_id": "ev-1",
            }
        ],
        "runs": [{"run_number": 1, "status": "COMPLETED", "steps": [{"step_seq": 1, "node_name": "batch_resolution", "status": "COMPLETED"}]}],
        "evidence_references": [{"id": "ev-1", "source_type": "PROCESS_PARAMETER_OBSERVATION", "source_table": "process_parameter_observations", "source_record_id": "rec-1", "document_checksum_sha256": "abc"}],
        "findings": [{"finding_code": "FND-001", "is_verified_observation": True, "title": "54 C excursion", "statement": "Exceeded 50 C", "evidence_ref_ids": ["ev-1"]}],
        "hypotheses": [{"hypothesis_code": "HYP-001", "status": "PROPOSED", "uncertainty_level": "LOW", "title": "Dryer overheat", "description": "Temp exceeded.", "supporting_evidence_ids": ["ev-1"], "contradicting_evidence_ids": []}],
        "limitations": ["Missing shipment records"],
        "qa_decision": {"investigation_qa_decision": "APPROVED"},
        "deviations": [],
    }
    raw = render_investigation_docx_bytes(snapshot)
    assert raw[:4] == b"PK\x03\x04"
    with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
        names = set(zf.namelist())
        assert "[Content_Types].xml" in names
        assert "word/document.xml" in names
        xml_body = zf.read("word/document.xml").decode("utf-8")
        assert "RPT-2026-0001" in xml_body
        assert "RULE_PARAM_VS_SOP_LIMIT" in xml_body
        assert "MISSING_SHIPMENT_RECORDS" in xml_body
