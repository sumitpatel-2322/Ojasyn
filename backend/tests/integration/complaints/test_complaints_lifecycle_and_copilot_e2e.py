import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.database import UnitOfWork
from app.core.deps import reset_rate_limiter
from app.core.security import create_access_token, get_password_hash
from app.domain.enums import SLAStatus, UserAccountStatus
from app.integrations.llm_provider import LLMProvider
from app.models.domain.complaints import Complaint, ComplaintAttachment, ComplaintClassification, SLAClock
from app.models.domain.documents import Document
from app.models.domain.governance import OutboxEvent
from app.models.domain.identity import User
from app.services.complaint_service import ComplaintService
from app.worker import OutboxWorker
from tests.conftest import create_seeded_tenant_actor


class _ScriptedLLMTransport:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.prompts = []

    def complete(self, *, system_prompt, user_prompt, model_name, temperature, max_tokens, timeout_seconds):
        self.prompts.append(user_prompt)
        return json.dumps(self.payloads.pop(0))


@pytest.fixture(autouse=True)
def _clean_limits():
    reset_rate_limiter()
    yield
    reset_rate_limiter()


def _create_super_admin(session_factory, email: str = "router.admin@ojasyn.platform") -> uuid.UUID:
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        u = User(
            email=email,
            password_hash=get_password_hash("SuperAdmin!123"),
            full_name="Platform Triage Admin",
            account_status=UserAccountStatus.ACTIVE.value,
            platform_role="PLATFORM_SUPER_ADMIN",
            email_verified_at=datetime.now(timezone.utc),
        )
        uow.session.add(u)
        uow.session.flush()
        return u.id


def test_intake_without_batch_idempotency_routing_triage_and_outbox_ai(
    api_client, sqlite_db, tmp_path, monkeypatch
) -> None:
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(vault_dir))
    get_settings.cache_clear()

    session_factory = sqlite_db["session_factory"]
    super_id = _create_super_admin(session_factory)
    super_headers = {"Authorization": f"Bearer {create_access_token(subject=str(super_id))}"}

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        qa_actor = create_seeded_tenant_actor(
            uow.session,
            org_name="Zenith Formulations",
            site_code="ZEN-01",
            email="qa.lead@zenith.in",
            role_code="QA_OFFICER",
            scope_type="SITE",
        )
        org_id = qa_actor["organization"].id
        site_id = qa_actor["site"].id
        qa_user_id = qa_actor["user"].id
        qa_membership_id = qa_actor["membership"].id

    qa_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(qa_user_id))}",
        "X-Organization-Id": str(org_id),
    }

    # 1. Guest intake WITHOUT organization_id and WITHOUT batch_number -> 201 UNROUTED_TRIAGE
    guest_payload = {
        "source_channel": "WEBSITE",
        "reported_product_name": "Azithromycin 500mg",
        "description": "Tablet discoloration noticed by patient Priya Nair (priya@patient.in).",
        "complainant": {
            "full_name": "Priya Nair",
            "email": "priya@patient.in",
            "phone_e164": "+919876543210",
        },
    }
    r1 = api_client.post(
        "/api/v1/complaints",
        headers={"Idempotency-Key": "guest-intake-1"},
        json=guest_payload,
    )
    assert r1.status_code == 201
    c_data = r1.json()
    complaint_id = uuid.UUID(c_data["id"])
    assert c_data["complaint_number"].startswith("PLT-")
    assert c_data["status"] == "UNROUTED_TRIAGE"
    assert c_data["reported_batch_number"] is None
    tracking_token = c_data["tracking_token"]
    assert tracking_token

    # 2. Idempotent retry -> 200 OK, NO tracking_token re-exposed!
    r2 = api_client.post(
        "/api/v1/complaints",
        headers={"Idempotency-Key": "guest-intake-1"},
        json=guest_payload,
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == str(complaint_id)
    assert r2.json()["idempotent_replay"] is True
    assert r2.json()["tracking_token"] is None

    # Same Idempotency-Key with modified payload -> 409 Conflict
    r_conflict = api_client.post(
        "/api/v1/complaints",
        headers={"Idempotency-Key": "guest-intake-1"},
        json={**guest_payload, "description": "Completely different complaint payload."},
    )
    assert r_conflict.status_code == 409

    # Outbox event contains neither raw tracking token nor complainant PII
    db = session_factory()
    try:
        ev = db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == complaint_id)).first()
        assert ev is not None and ev.event_type == "ComplaintRegistered"
        assert tracking_token not in str(ev.payload_json)
        assert "priya@patient.in" not in str(ev.payload_json)
    finally:
        db.close()

    # Upload guest evidence attachment on UNROUTED_TRIAGE complaint using X-Tracking-Token
    png_1x1 = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\x0bIDATx\x9cc`\x00\x00\x00\x02\x00\x01"
        b"\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    att_resp = api_client.post(
        f"/api/v1/complaints/{complaint_id}/attachments",
        headers={"X-Tracking-Token": tracking_token},
        files={"file": ("strip_photo.png", png_1x1, "image/png")},
        data={"caption": "Tablet blister front", "is_external_visible": "true"},
    )
    assert att_resp.status_code == 201
    att_doc_id = uuid.UUID(att_resp.json()["document_id"])
    assert att_resp.json()["organization_id"] is None

    # 3. Async Outbox AI Triage (mocked LLM outside DB tx) respects PIIRedactor
    transport = _ScriptedLLMTransport(
        [
            {
                "suggested_severity": "HIGH",
                "suggested_priority": "URGENT",
                "defect_category": "APPEARANCE",
                "confidence_score": "0.9100",
                "rationale": "Discoloration may indicate chemical degradation.",
                "risk_indicators": {"stability_risk": True},
            }
        ]
    )
    settings = Settings(
        DATABASE_URL=sqlite_db["db_url"],
        JWT_SIGNING_KEY="test-only-signing-key-with-at-least-32-characters",
    )
    provider = LLMProvider(transport=transport, settings=settings)

    worker = OutboxWorker(session_factory=session_factory)
    worker.register_external_handler(
        "ComplaintRegistered",
        lambda sf, ev, _ctx: ComplaintService().run_async_triage(
            ev.aggregate_id, session_factory=sf, llm_provider=provider
        ),
    )
    assert worker.run_once() == 1
    assert "priya@patient.in" not in transport.prompts[0]
    assert "Priya Nair" not in transport.prompts[0]

    # 4. Super Admin discovers unrouted complaint and routes to Zenith Formulations
    unrouted_list = api_client.get("/api/v1/complaints/unrouted", headers=super_headers)
    assert unrouted_list.status_code == 200
    assert any(item["id"] == str(complaint_id) for item in unrouted_list.json()["items"])

    routed = api_client.post(
        f"/api/v1/complaints/{complaint_id}/route",
        headers=super_headers,
        json={
            "row_version": 1,
            "organization_id": str(org_id),
            "site_id": str(site_id),
            "reason": "Product matches Zenith Azithromycin 500mg portfolio",
        },
    )
    assert routed.status_code == 200
    assert routed.json()["status"] == "RECEIVED"
    assert routed.json()["organization_id"] == str(org_id)
    assert routed.json()["ai_severity"] == "HIGH"
    assert routed.json()["final_severity"] is None

    # Verify route_unrouted_complaint atomically backfilled organization_id on Document & ComplaintAttachment
    db = session_factory()
    try:
        doc_row = db.get(Document, att_doc_id)
        att_row = db.scalars(select(ComplaintAttachment).where(ComplaintAttachment.complaint_id == complaint_id)).first()
        assert doc_row is not None and doc_row.organization_id == org_id
        assert att_row is not None and att_row.organization_id == org_id
    finally:
        db.close()

    # 5. QA Officer assigns, resolves batch as NO_BATCH_INFO/INSUFFICIENT, and performs Human Triage
    assigned = api_client.post(
        f"/api/v1/complaints/{complaint_id}/assign",
        headers=qa_headers,
        json={"assignee_membership_id": str(qa_membership_id), "reason": "Assign to QA Lead", "row_version": 2},
    )
    assert assigned.status_code == 200

    batch_res = api_client.post(
        f"/api/v1/complaints/{complaint_id}/batch-candidates/resolve",
        headers=qa_headers,
        json={"action": "MARK_NOT_FOUND_OR_INSUFFICIENT", "notes": "Patient discarded outer carton"},
    )
    assert batch_res.status_code == 200
    assert batch_res.json()["resolution_outcome"] == "INSUFFICIENT_INFO"

    triaged = api_client.post(
        f"/api/v1/complaints/{complaint_id}/triage",
        headers=qa_headers,
        json={
            "row_version": 3,
            "severity": "CRITICAL",
            "priority": "URGENT",
            "defect_category": "APPEARANCE",
            "override_reason": "Escalated due to potential potency loss",
        },
    )
    assert triaged.status_code == 200
    t_body = triaged.json()
    assert t_body["status"] == "TRIAGED"
    assert t_body["ai_severity"] == "HIGH"
    assert t_body["final_severity"] == "CRITICAL"

    # Re-running AI triage NEVER overwrites human final_severity!
    transport2 = _ScriptedLLMTransport(
        [
            {
                "suggested_severity": "LOW",
                "suggested_priority": "LOW",
                "defect_category": "PACKAGING",
                "confidence_score": "0.5000",
                "rationale": "Secondary pass",
                "risk_indicators": {},
            }
        ]
    )
    ComplaintService().run_async_triage(
        complaint_id,
        session_factory=session_factory,
        llm_provider=LLMProvider(transport=transport2, settings=settings),
    )
    db = session_factory()
    try:
        comp_row = db.get(Complaint, complaint_id)
        assert comp_row is not None
        assert comp_row.final_severity == "CRITICAL"
        clocks = db.scalars(select(SLAClock).where(SLAClock.complaint_id == complaint_id)).all()
        assert any(c.sla_stage == "TRIAGE" and c.status == SLAStatus.MET.value for c in clocks)
        classifications = db.scalars(
            select(ComplaintClassification).where(ComplaintClassification.complaint_id == complaint_id)
        ).all()
        assert any(c.source_type == "AI_SUGGESTION" for c in classifications)
        assert any(c.source_type == "HUMAN_REVIEW" for c in classifications)
    finally:
        db.close()

    # 6. Optimistic concurrency check on PATCH & Manual batch link + Information Request pauses SLA
    stale_patch = api_client.patch(
        f"/api/v1/complaints/{complaint_id}",
        headers=qa_headers,
        json={"row_version": 99, "reported_manufacturer": "Wrong version"},
    )
    assert stale_patch.status_code == 409

    ok_patch = api_client.patch(
        f"/api/v1/complaints/{complaint_id}",
        headers=qa_headers,
        json={"row_version": 4, "reported_manufacturer": "Zenith Mfg", "reason": "Confirmed label"},
    )
    assert ok_patch.status_code == 200
    assert ok_patch.json()["row_version"] == 5

    manual_link = api_client.post(
        f"/api/v1/complaints/{complaint_id}/batch-candidates/resolve",
        headers=qa_headers,
        json={"action": "MANUAL_LINK", "reported_batch_number": "AZM-2026-88", "notes": "Verified strip photo"},
    )
    assert manual_link.status_code == 200
    assert manual_link.json()["resolution_outcome"] == "MANUAL_LINKED"

    info_req = api_client.post(
        f"/api/v1/complaints/{complaint_id}/information-requests",
        headers=qa_headers,
        json={"row_version": 5, "body_text": "Please upload photo of tablet strip.", "transition_to_waiting_info": True},
    )
    assert info_req.status_code == 201
    detail = api_client.get(f"/api/v1/complaints/{complaint_id}", headers=qa_headers).json()
    assert detail["status"] == "WAITING_INFO"


def test_copilot_http_session_and_atomic_submit(api_client, sqlite_db, monkeypatch) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        actor = create_seeded_tenant_actor(
            uow.session,
            org_name="Copilot Pharma",
            site_code="CP-01",
            email="staff@copilot.in",
            role_code="QA_OFFICER",
        )
        org_id = actor["organization"].id

    transport = _ScriptedLLMTransport(
        [
            {"intent": "CREATE"},
            {
                "extracted_fields": {
                    "reported_product_name": "Metformin 500",
                    "description": "Cracked blister pack on purchase.",
                    "reporter_name": "Meera Joshi",
                    "reporter_email": "meera@example.com",
                },
                "confidence_score": "0.9000",
                "assistant_reply": "Recorded your Metformin 500 complaint.",
            },
        ]
    )
    settings = Settings(
        DATABASE_URL=sqlite_db["db_url"],
        JWT_SIGNING_KEY="test-only-signing-key-with-at-least-32-characters",
    )
    provider = LLMProvider(transport=transport, settings=settings)

    import app.api.v1.complaints as complaints_api

    orig_send = complaints_api._svc.send_copilot_message

    def _patched_send(db, **kwargs):
        kwargs["llm_provider"] = provider
        return orig_send(db, **kwargs)

    monkeypatch.setattr(complaints_api._svc, "send_copilot_message", _patched_send)

    # Create guest copilot session -> returns session_token once
    sess = api_client.post("/api/v1/complaints/copilot/sessions", json={})
    assert sess.status_code == 201
    sess_id = sess.json()["id"]
    sess_token = sess.json()["session_token"]
    assert sess_token

    # Guest turn extracts mandatory fields without requiring batch number
    turn = api_client.post(
        f"/api/v1/complaints/copilot/sessions/{sess_id}/messages",
        headers={"X-Copilot-Session-Token": sess_token},
        json={"message": "Meera Joshi (meera@example.com): Metformin 500 blister pack cracked."},
    )
    assert turn.status_code == 200
    assert turn.json()["is_complete"] is True
    assert "reported_batch_number" in turn.json()["missing_advisory_fields"]

    # Submit with bogus site_id returns clean 404 (never unhandled 500 IntegrityError)
    bad_site_submit = api_client.post(
        f"/api/v1/complaints/copilot/sessions/{sess_id}/submit",
        headers={"X-Copilot-Session-Token": sess_token},
        json={"organization_id": str(org_id), "site_id": str(uuid.uuid4())},
    )
    assert bad_site_submit.status_code == 404

    # Submit converts copilot session atomically into canonical Complaint
    submitted = api_client.post(
        f"/api/v1/complaints/copilot/sessions/{sess_id}/submit",
        headers={"X-Copilot-Session-Token": sess_token},
        json={"organization_id": str(org_id)},
    )
    assert submitted.status_code == 201
    assert submitted.json()["status"] == "RECEIVED"

    # Re-reading session shows SUBMITTED + converted_complaint_id; second submit blocked (409)
    sess_after = api_client.get(
        f"/api/v1/complaints/copilot/sessions/{sess_id}",
        headers={"X-Copilot-Session-Token": sess_token},
    )
    assert sess_after.status_code == 200
    assert sess_after.json()["session_status"] == "SUBMITTED"
    assert sess_after.json()["converted_complaint_id"] == submitted.json()["id"]

    resubmit = api_client.post(
        f"/api/v1/complaints/copilot/sessions/{sess_id}/submit",
        headers={"X-Copilot-Session-Token": sess_token},
        json={},
    )
    assert resubmit.status_code == 409


def test_run_async_triage_uses_fresh_organization_after_concurrent_routing(sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seeded = create_seeded_tenant_actor(
            uow.session,
            org_name="Concurrent Route Pharma",
            site_code="CR-01",
            email="qa@concurrentroute.in",
            role_code="QA_OFFICER",
        )
        org_id = seeded["organization"].id
        complaint = Complaint(
            organization_id=None,
            complaint_number="PLT-2026-990001",
            description="Unrouted complaint routed mid-LLM-inference.",
            status="UNROUTED_TRIAGE",
        )
        uow.session.add(complaint)
        uow.session.flush()
        comp_id = complaint.id

    class _ConcurrentRouteTransport:
        def complete(self, **_kwargs) -> str:
            # Simulate super-admin routing the complaint WHILE LLM call is in flight
            with UnitOfWork(session_factory) as route_uow:
                assert route_uow.session is not None
                row = route_uow.session.get(Complaint, comp_id)
                assert row is not None
                row.organization_id = org_id
                row.status = "RECEIVED"
            return json.dumps(
                {
                    "suggested_severity": "MEDIUM",
                    "suggested_priority": "HIGH",
                    "defect_category": "PACKAGING",
                    "confidence_score": "0.8700",
                    "rationale": "Routed concurrently during triage.",
                    "risk_indicators": {},
                }
            )

    settings = Settings(
        DATABASE_URL=sqlite_db["db_url"],
        JWT_SIGNING_KEY="test-only-signing-key-with-at-least-32-characters",
    )
    classification = ComplaintService().run_async_triage(
        comp_id,
        session_factory=session_factory,
        llm_provider=LLMProvider(transport=_ConcurrentRouteTransport(), settings=settings),
    )
    assert classification is not None
    assert classification.organization_id == org_id
