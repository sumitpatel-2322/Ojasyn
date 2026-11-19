import base64
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import UnitOfWork
from app.core.deps import reset_rate_limiter
from app.core.exceptions import ConflictError
from app.core.security import create_access_token
from app.domain.enums import (
    CommunicationDeliveryStatus,
    CommunicationType,
    ComplaintSource,
    ComplaintStatus,
    InboundEmailClassification,
    InboundEmailTriageStatus,
    SLAStatus,
)
from app.integrations.email_provider import EmailProvider, MockEmailAdapter
from app.models.domain.complaints import (
    Complaint,
    ComplaintAttachment,
    ComplaintCommunication,
    ComplaintEvent,
    EmailAttachment,
    EmailConnection,
    EmailProcessingAttempt,
    InboundEmail,
    SLAClock,
)
from app.models.domain.documents import Document
from app.models.domain.governance import OutboxEvent
from app.models.schemas.complaints import ComplaintCloseRequest
from app.services.complaint_service import ComplaintService
from app.worker import OutboxWorker
from tests.conftest import create_seeded_tenant_actor

_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\x0bIDATx\x9cc`\x00\x00\x00\x02\x00\x01"
    b"\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture(autouse=True)
def _clean_limits():
    reset_rate_limiter()
    yield
    reset_rate_limiter()


def test_b6_complaint_communication_and_waiting_info_loop_e2e(
    api_client, sqlite_db
) -> None:
    session_factory = sqlite_db["session_factory"]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        qa = create_seeded_tenant_actor(
            uow.session,
            org_name="Comms Pharma",
            site_code="COM-01",
            email="qa@commspharma.in",
            role_code="QA_HEAD",
            scope_type="ORGANIZATION",
        )
        org_id = qa["organization"].id
        site_id = qa["site"].id
        qa_user_id = qa["user"].id
        qa_membership_id = qa["membership"].id

    headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(qa_user_id))}",
        "X-Organization-Id": str(org_id),
    }

    # 1. Register canonical complaint with complainant email
    reg = api_client.post(
        "/api/v1/complaints",
        headers=headers,
        json={
            "organization_id": str(org_id),
            "site_id": str(site_id),
            "source_channel": "WEBSITE",
            "reported_product_name": "Paracetamol 650mg",
            "description": "Missing batch print on foil strip.",
            "complainant": {
                "full_name": "Ananya Rao",
                "email": "ananya.rao@example.com",
            },
        },
    )
    assert reg.status_code == 201
    complaint_id = reg.json()["id"]

    # 2. Send acknowledgement via EMAIL using template -> sets acknowledged_at & queues outbound email
    ack = api_client.post(
        f"/api/v1/complaints/{complaint_id}/acknowledgements",
        headers=headers,
        json={"channel": "EMAIL", "template_code": "ACK_STANDARD"},
    )
    assert ack.status_code == 201
    ack_body = ack.json()
    assert ack_body["communication_type"] == CommunicationType.ACKNOWLEDGEMENT.value
    assert ack_body["delivery_status"] == CommunicationDeliveryStatus.QUEUED.value
    assert ack_body["recipient_email"] == "ananya.rao@example.com"

    detail_after_ack = api_client.get(f"/api/v1/complaints/{complaint_id}", headers=headers).json()
    assert detail_after_ack["acknowledged_at"] is not None

    # 3. Add internal note + send status update
    internal_note = api_client.post(
        f"/api/v1/complaints/{complaint_id}/communications",
        headers=headers,
        json={
            "channel": "INTERNAL_NOTE",
            "direction": "INTERNAL",
            "body_text": "Internal QA note: check packaging line 2 logbook.",
            "is_internal_only": True,
        },
    )
    assert internal_note.status_code == 201
    assert internal_note.json()["delivery_status"] == CommunicationDeliveryStatus.NOT_APPLICABLE.value

    status_upd = api_client.post(
        f"/api/v1/complaints/{complaint_id}/status-updates",
        headers=headers,
        json={"channel": "PORTAL", "body_text": "Initial QA assessment is underway."},
    )
    assert status_upd.status_code == 201
    assert status_upd.json()["communication_type"] == CommunicationType.STATUS_UPDATE.value

    # 4. Request information -> transitions -> WAITING_INFO & pauses SLA clock + enqueues email
    info_req = api_client.post(
        f"/api/v1/complaints/{complaint_id}/information-requests",
        headers=headers,
        json={
            "channel": "EMAIL",
            "template_code": "INFO_REQUEST_BATCH",
            "transition_to_waiting_info": True,
        },
    )
    assert info_req.status_code == 201
    info_req_id = info_req.json()["id"]
    assert info_req.json()["communication_type"] == CommunicationType.INFO_REQUEST.value
    assert info_req.json()["delivery_status"] == CommunicationDeliveryStatus.QUEUED.value

    db = session_factory()
    try:
        comp_row = db.get(Complaint, uuid.UUID(complaint_id))
        assert comp_row is not None and comp_row.status == ComplaintStatus.WAITING_INFO.value
        clocks = db.scalars(select(SLAClock).where(SLAClock.complaint_id == comp_row.id)).all()
        assert len(clocks) >= 1
        assert all(c.status == SLAStatus.PAUSED.value and c.paused_at is not None for c in clocks)
        # Backdate paused_at by 120s to test pause accumulation on reply
        for c in clocks:
            c.paused_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        original_due = clocks[0].due_at
        db.commit()
    finally:
        db.close()

    # Run OutboxWorker with MockEmailAdapter -> delivers queued outbound EMAILs
    mock_adapter = MockEmailAdapter()
    email_provider = EmailProvider(default_mock=mock_adapter)
    worker = OutboxWorker(session_factory=session_factory)
    worker.register_external_handler(
        "OutboundCommunicationRequested",
        lambda sf, ev, ctx: ComplaintService().handle_outbox_outbound_communication(
            sf, ev, ctx, email_provider=email_provider
        ),
    )
    processed = worker.run_once()
    assert processed >= 2
    assert len(mock_adapter.sent_messages) == 2

    db = session_factory()
    try:
        ack_row = db.get(ComplaintCommunication, uuid.UUID(ack_body["id"]))
        info_row = db.get(ComplaintCommunication, uuid.UUID(info_req_id))
        assert ack_row is not None and ack_row.delivery_status == CommunicationDeliveryStatus.SENT.value
        assert ack_row.provider_message_id is not None and ack_row.delivered_at is not None
        assert ack_row.attempt_count == 1
        assert info_row is not None and info_row.delivery_status == CommunicationDeliveryStatus.SENT.value
    finally:
        db.close()

    # 5. Record reporter reply -> links in_reply_to_communication_id, transitions WAITING_INFO -> TRIAGED, unpauses SLA
    reply = api_client.post(
        f"/api/v1/complaints/{complaint_id}/communications/{info_req_id}/reply",
        headers=headers,
        json={
            "channel": "EMAIL",
            "body_text": "Batch number on the outer carton is PCM-2026-44.",
            "sender_email": "ananya.rao@example.com",
        },
    )
    assert reply.status_code == 201
    reply_body = reply.json()
    assert reply_body["direction"] == "INBOUND"
    assert reply_body["communication_type"] == CommunicationType.REPORTER_REPLY.value
    assert reply_body["in_reply_to_communication_id"] == info_req_id

    db = session_factory()
    try:
        comp_row = db.get(Complaint, uuid.UUID(complaint_id))
        assert comp_row is not None and comp_row.status == ComplaintStatus.TRIAGED.value
        clocks = db.scalars(select(SLAClock).where(SLAClock.complaint_id == comp_row.id)).all()
        assert all(c.status == SLAStatus.ON_TRACK.value and c.paused_at is None for c in clocks)
        assert clocks[0].accumulated_pause_seconds >= 115
        assert clocks[0].due_at > original_due
    finally:
        db.close()

    # 6. List communications with include_internal=false excludes internal notes
    ext_comms = api_client.get(
        f"/api/v1/complaints/{complaint_id}/communications?include_internal=false",
        headers=headers,
    )
    assert ext_comms.status_code == 200
    assert all(item["is_internal_only"] is False for item in ext_comms.json()["items"])
    all_comms = api_client.get(
        f"/api/v1/complaints/{complaint_id}/communications",
        headers=headers,
    )
    assert all_comms.status_code == 200
    assert all_comms.json()["total"] == 5

    # 7. Closure message blocked before CLOSED/UNDER_QA_REVIEW/ACTION_PENDING, succeeds after close
    early_closure_msg = api_client.post(
        f"/api/v1/complaints/{complaint_id}/closure-messages",
        headers=headers,
        json={"channel": "EMAIL", "template_code": "CLOSURE_STANDARD"},
    )
    assert early_closure_msg.status_code == 409

    closed = api_client.post(
        f"/api/v1/complaints/{complaint_id}/close",
        headers=headers,
        json={"row_version": comp_row.row_version, "closure_reason": "Batch confirmed within spec."},
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == ComplaintStatus.CLOSED.value

    closure_msg = api_client.post(
        f"/api/v1/complaints/{complaint_id}/closure-messages",
        headers=headers,
        json={"channel": "EMAIL", "template_code": "CLOSURE_STANDARD"},
    )
    assert closure_msg.status_code == 201
    assert closure_msg.json()["communication_type"] == CommunicationType.CLOSURE_NOTIFICATION.value


def test_b7_email_intake_webhook_dedupe_sync_worker_and_triage_e2e(
    api_client, sqlite_db, tmp_path, monkeypatch
) -> None:
    vault_dir = tmp_path / "email_vault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(vault_dir))
    monkeypatch.setenv("GROQ_API_KEY", "")
    get_settings.cache_clear()

    session_factory = sqlite_db["session_factory"]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        admin = create_seeded_tenant_actor(
            uow.session,
            org_name="Email Intake Pharma",
            site_code="EIP-01",
            email="admin@emailintake.in",
            role_code="ORG_ADMIN",
            scope_type="ORGANIZATION",
        )
        org_id = admin["organization"].id
        site_id = admin["site"].id
        admin_user_id = admin["user"].id

        qa = create_seeded_tenant_actor(
            uow.session,
            org_name="Dummy",
            site_code="DUM-01",
            email="qa.triage@emailintake.in",
            role_code="QA_OFFICER",
        )
        # Re-use Org for QA officer? Simpler: grant QA_HEAD to another user in same org
        from app.domain.enums import AssignmentStatus, AssignmentType, MembershipStatus, MemberType, RoleAssignmentScopeType, RoleAssignmentStatus, UserAccountStatus
        from app.models.domain.identity import MembershipRoleAssignment, OrganizationMembership, Role, SiteAssignment, User
        from app.core.security import get_password_hash

        qa_user = User(
            email="qa.real@emailintake.in",
            full_name="QA Triage Officer",
            password_hash=get_password_hash("StrongPass!123"),
            account_status=UserAccountStatus.ACTIVE.value,
        )
        uow.session.add(qa_user)
        uow.session.flush()
        qa_m = OrganizationMembership(
            organization_id=org_id,
            user_id=qa_user.id,
            member_type=MemberType.EMPLOYEE.value,
            membership_status=MembershipStatus.ACTIVE.value,
        )
        uow.session.add(qa_m)
        uow.session.flush()
        uow.session.add(
            SiteAssignment(
                organization_id=org_id,
                membership_id=qa_m.id,
                site_id=site_id,
                assignment_type=AssignmentType.PRIMARY.value,
                status=AssignmentStatus.ACTIVE.value,
                assigned_by_user_id=qa_user.id,
            )
        )
        qa_role = uow.session.query(Role).filter(Role.role_code == "QA_HEAD").one()
        uow.session.add(
            MembershipRoleAssignment(
                organization_id=org_id,
                membership_id=qa_m.id,
                role_id=qa_role.id,
                scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
                granted_by_user_id=qa_user.id,
                status=RoleAssignmentStatus.ACTIVE.value,
            )
        )
        qa_user_id = qa_user.id

    admin_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(admin_user_id))}",
        "X-Organization-Id": str(org_id),
    }
    qa_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(qa_user_id))}",
        "X-Organization-Id": str(org_id),
    }

    # 1. Create EmailConnection -> returns webhook_secret ONCE
    conn_resp = api_client.post(
        "/api/v1/email-intake/connections",
        headers=admin_headers,
        json={
            "name": "Quality Complaints Mailbox",
            "provider_type": "MOCK",
            "mailbox_address": "complaints@emailintake.in",
            "default_site_id": str(site_id),
            "auto_create_threshold": "0.7500",
            "config_json": {
                "mock_inbound_messages": [
                    {
                        "provider_message_id": "sync-msg-001",
                        "from_email": "clinic.buyer@example.com",
                        "from_name": "City Clinic",
                        "subject": "Order inquiry",
                        "body_text": "When will our clinic order arrive?",
                    },
                    {
                        "provider_message_id": "wh-msg-001",
                        "from_email": "patient@example.com",
                        "subject": "Duplicate from sync",
                        "body_text": "Should be deduplicated.",
                    },
                ]
            },
        },
    )
    assert conn_resp.status_code == 201
    conn_id = conn_resp.json()["id"]
    webhook_secret = conn_resp.json()["webhook_secret"]
    assert webhook_secret

    # GET connection & list NEVER echo webhook_secret
    get_conn = api_client.get(f"/api/v1/email-intake/connections/{conn_id}", headers=admin_headers)
    assert get_conn.status_code == 200
    assert get_conn.json()["webhook_secret"] is None
    list_conn = api_client.get("/api/v1/email-intake/connections", headers=admin_headers)
    assert list_conn.status_code == 200
    assert all(item["webhook_secret"] is None for item in list_conn.json()["items"])

    # 2. Webhook ingestion with PNG attachment & HTML XSS payload
    wh_payload = {
        "provider_message_id": "wh-msg-001",
        "from_email": "priya.nair@example.com",
        "from_name": "Priya Nair",
        "subject": "Quality complaint: broken tablet in batch LOT-2026-77",
        "body_text": "Found a broken tablet and discoloration inside the blister pack.",
        "body_html": "<p>Broken tablet<script>alert(1)</script></p>",
        "attachments": [
            {
                "original_filename": "tablet_photo.png",
                "mime_type": "image/png",
                "content_base64": base64.b64encode(_PNG_1X1).decode("ascii"),
            }
        ],
    }
    wh1 = api_client.post(
        f"/api/v1/email-intake/webhooks/{conn_id}",
        headers={"X-Webhook-Secret": webhook_secret},
        json=wh_payload,
    )
    assert wh1.status_code == 201
    inbound_likely_id = wh1.json()["id"]
    assert wh1.json()["deduplicated"] is False

    # Duplicate webhook -> 200 OK idempotent, zero duplicate InboundEmail
    wh_dup = api_client.post(
        f"/api/v1/email-intake/webhooks/{conn_id}",
        headers={"X-Webhook-Secret": webhook_secret},
        json=wh_payload,
    )
    assert wh_dup.status_code == 200
    assert wh_dup.json()["deduplicated"] is True
    assert wh_dup.json()["id"] == inbound_likely_id

    # Verify HTML sanitized & Document/EmailAttachment stored
    detail1 = api_client.get(f"/api/v1/email-intake/inbound/{inbound_likely_id}", headers=qa_headers)
    assert detail1.status_code == 200
    assert "<script" not in (detail1.json()["body_html_sanitized"] or "").lower()
    assert len(detail1.json()["attachments"]) == 1
    att_doc_id = detail1.json()["attachments"][0]["document_id"]
    assert att_doc_id is not None

    # 3. Controlled sync pulls sync-msg-001 (uncertain) and deduplicates wh-msg-001
    sync_resp = api_client.post(
        f"/api/v1/email-intake/connections/{conn_id}/sync",
        headers=admin_headers,
    )
    assert sync_resp.status_code == 200
    sync_data = sync_resp.json()
    assert sync_data["fetched_count"] == 2
    assert sync_data["created_count"] == 1
    assert sync_data["deduplicated_count"] == 1
    assert sync_data["sync_cursor"] == "2"

    # 4. Run OutboxWorker -> auto-creates canonical EMAIL Complaint for wh-msg-001 + queues sync-msg-001 to manual triage
    worker = OutboxWorker(session_factory=session_factory)
    assert worker.run_once() >= 2

    detail1_after = api_client.get(
        f"/api/v1/email-intake/inbound/{inbound_likely_id}", headers=qa_headers
    ).json()
    assert detail1_after["classification"] == InboundEmailClassification.LIKELY_COMPLAINT.value
    assert detail1_after["triage_status"] == InboundEmailTriageStatus.COMPLAINT_CREATED.value
    auto_complaint_id = detail1_after["complaint_id"]
    assert auto_complaint_id is not None
    assert len(detail1_after["processing_attempts"]) == 1

    # Auto-created complaint uses canonical Complaint contract (source_channel=EMAIL + attached Document)
    comp_detail = api_client.get(f"/api/v1/complaints/{auto_complaint_id}", headers=qa_headers).json()
    assert comp_detail["source_channel"] == ComplaintSource.EMAIL.value
    assert comp_detail["reported_batch_number"] == "LOT-2026-77"
    assert any(a["document_id"] == att_doc_id for a in comp_detail["attachments"])

    # Uncertain sync-msg-001 appears in manual triage queue
    queue_resp = api_client.get("/api/v1/email-intake/triage-queue", headers=qa_headers)
    assert queue_resp.status_code == 200
    assert queue_resp.json()["total"] == 1
    uncertain_id = queue_resp.json()["items"][0]["id"]

    # Manual triage: LINK_EXISTING_COMPLAINT attaches REPORTER_REPLY communication
    triage_link = api_client.post(
        f"/api/v1/email-intake/inbound/{uncertain_id}/triage",
        headers=qa_headers,
        json={
            "row_version": queue_resp.json()["items"][0]["row_version"],
            "decision": "LINK_EXISTING_COMPLAINT",
            "complaint_id": auto_complaint_id,
            "triage_notes": "Follow-up from same clinic.",
        },
    )
    assert triage_link.status_code == 200
    assert triage_link.json()["triage_status"] == InboundEmailTriageStatus.TRIAGED_COMPLAINT.value
    assert triage_link.json()["complaint_id"] == auto_complaint_id

    comms_after_link = api_client.get(
        f"/api/v1/complaints/{auto_complaint_id}/communications", headers=qa_headers
    ).json()
    assert any(
        c["communication_type"] == CommunicationType.REPORTER_REPLY.value
        and c["direction"] == "INBOUND"
        for c in comms_after_link["items"]
    )

    # 5. Rotate secret & stale row_version check on PATCH connection
    stale_patch = api_client.patch(
        f"/api/v1/email-intake/connections/{conn_id}",
        headers=admin_headers,
        json={"row_version": 99, "name": "Stale"},
    )
    assert stale_patch.status_code == 409

    ok_patch = api_client.patch(
        f"/api/v1/email-intake/connections/{conn_id}",
        headers=admin_headers,
        json={"row_version": 2, "rotate_webhook_secret": True},
    )
    assert ok_patch.status_code == 200
    new_secret = ok_patch.json()["webhook_secret"]
    assert new_secret and new_secret != webhook_secret

    # Old secret now rejected with 401
    old_secret_wh = api_client.post(
        f"/api/v1/email-intake/webhooks/{conn_id}",
        headers={"X-Webhook-Secret": webhook_secret},
        json={**wh_payload, "provider_message_id": "wh-msg-after-rotate"},
    )
    assert old_secret_wh.status_code == 401

    # 6. Manual triage CREATE_COMPLAINT & MARK_NON_COMPLAINT
    wh_manual = api_client.post(
        f"/api/v1/email-intake/webhooks/{conn_id}",
        headers={"X-Webhook-Secret": new_secret},
        json={
            "provider_message_id": "wh-msg-manual-create",
            "from_email": "nurse@hospital.in",
            "subject": "Follow-up",
            "body_text": "Packaging tore when opening.",
            "attachments": [
                {
                    "original_filename": "tear.png",
                    "mime_type": "image/png",
                    "content_base64": base64.b64encode(_PNG_1X1).decode("ascii"),
                }
            ],
        },
    )
    assert wh_manual.status_code == 201
    manual_id = wh_manual.json()["id"]
    manual_triage = api_client.post(
        f"/api/v1/email-intake/inbound/{manual_id}/triage",
        headers=qa_headers,
        json={
            "row_version": 1,
            "decision": "CREATE_COMPLAINT",
            "reported_product_name": "Ibuprofen 400mg",
            "triage_notes": "Confirmed packaging complaint.",
        },
    )
    assert manual_triage.status_code == 200
    assert manual_triage.json()["triage_status"] == InboundEmailTriageStatus.TRIAGED_COMPLAINT.value
    created_comp_id = manual_triage.json()["complaint_id"]
    assert created_comp_id is not None

    created_comp = api_client.get(f"/api/v1/complaints/{created_comp_id}", headers=qa_headers).json()
    assert created_comp["source_channel"] == ComplaintSource.EMAIL.value
    assert len(created_comp["attachments"]) == 1


def test_close_complaint_guarded_by_open_mandatory_capas_hook(sqlite_db, monkeypatch) -> None:
    session_factory = sqlite_db["session_factory"]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        qa = create_seeded_tenant_actor(
            uow.session,
            org_name="CAPA Guard Pharma",
            site_code="CG-01",
            email="qa@capaguard.in",
            role_code="QA_HEAD",
            scope_type="ORGANIZATION",
        )
        org_id = qa["organization"].id
        site_id = qa["site"].id
        user_id = qa["user"].id
        membership_id = qa["membership"].id
        comp = Complaint(
            organization_id=org_id,
            site_id=site_id,
            complaint_number="CG-CMP-2026-000001",
            description="Complaint with open mandatory CAPA.",
            status=ComplaintStatus.ACTION_PENDING.value,
        )
        uow.session.add(comp)
        uow.session.flush()
        comp_id = comp.id

    from app.core.deps import TenantContext
    from app.repositories.qms_repo import QMSRepository

    fake_capa = types.SimpleNamespace(id=uuid.uuid4(), capa_number="CG-CAPA-2026-000001")
    monkeypatch.setattr(
        QMSRepository,
        "list_open_mandatory_capas_for_complaint",
        lambda self, db, oid, cid: [fake_capa],
        raising=False,
    )

    tenant_ctx = TenantContext(
        user_id=user_id,
        organization_id=org_id,
        membership_id=membership_id,
        allowed_site_ids=frozenset({site_id}),
        is_org_wide=True,
        permission_codes=frozenset({"complaint.close", "complaint.read"}),
    )
    db = session_factory()
    try:
        with pytest.raises(ConflictError) as exc_info:
            ComplaintService().close_complaint(
                db,
                complaint_id=comp_id,
                tenant_ctx=tenant_ctx,
                payload=ComplaintCloseRequest(row_version=1, closure_reason="Trying to close early"),
            )
        assert exc_info.value.error_code == "OPEN_MANDATORY_CAPAS_EXIST"
        assert exc_info.value.details["open_capa_numbers"] == ["CG-CAPA-2026-000001"]
    finally:
        db.close()
