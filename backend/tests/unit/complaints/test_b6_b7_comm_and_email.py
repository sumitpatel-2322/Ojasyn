import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List

import pytest

from app.core.config import Settings
from app.core.database import UnitOfWork
from app.core.exceptions import ConflictError, ExternalProviderError, ValidationError
from app.domain.enums import (
    CommunicationChannel,
    CommunicationDeliveryStatus,
    CommunicationType,
    ComplaintStatus,
    EmailProviderType,
    InboundEmailClassification,
    InboundEmailTriageStatus,
)
from app.integrations.email_provider import (
    EmailProvider,
    FetchedInboundEmail,
    MockEmailAdapter,
    OutboundEmailMessage,
    generate_webhook_secret,
    hash_webhook_secret,
    sanitize_email_html,
    verify_webhook_secret,
)
from app.integrations.llm_provider import LLMProvider
from app.models.domain.complaints import (
    Complaint,
    ComplaintCommunication,
    EmailConnection,
    EmailProcessingAttempt,
    InboundEmail,
)
from app.models.domain.governance import OutboxEvent
from app.models.schemas.complaints import (
    ClosureMessageCreateRequest,
    ComplaintCommunicationCreateRequest,
)
from app.services.complaint_service import (
    ComplaintService,
    _classify_inbound_email_deterministic,
    _normalize_communication_type,
    _render_communication_content,
)
from app.worker import OutboxWorker
from tests.conftest import create_seeded_tenant_actor


def test_webhook_secret_hashing_and_constant_time_verification() -> None:
    secret = generate_webhook_secret()
    assert len(secret) >= 32
    digest = hash_webhook_secret(secret)
    assert len(digest) == 64
    assert secret not in digest
    assert verify_webhook_secret(secret, digest) is True
    assert verify_webhook_secret(f" {secret} ", digest) is True
    assert verify_webhook_secret("wrong-secret", digest) is False
    assert verify_webhook_secret(None, digest) is False
    assert verify_webhook_secret(secret, None) is False


def test_sanitize_email_html_strips_active_scripts_and_handlers() -> None:
    raw = (
        '<div onclick="alert(1)">Hello<script>evil()</script>'
        '<iframe src="https://evil.test"></iframe>'
        '<a href="javascript:steal()">Click</a></div>'
    )
    sanitized = sanitize_email_html(raw)
    assert sanitized is not None
    assert "<script" not in sanitized.lower()
    assert "<iframe" not in sanitized.lower()
    assert "onclick" not in sanitized.lower()
    assert "javascript:" not in sanitized.lower()
    assert "Hello" in sanitized


def test_email_provider_adapters_and_error_guards() -> None:
    mock_adapter = MockEmailAdapter(
        inbound_queue=[
            FetchedInboundEmail(
                provider_message_id="msg-q-1",
                from_email="patient@example.com",
                subject="Broken tablet",
                body_text="Tablet cracked in blister strip",
            )
        ]
    )
    provider = EmailProvider(default_mock=mock_adapter)

    res = provider.send_email(
        provider_type=EmailProviderType.MOCK.value,
        message=OutboundEmailMessage(
            to_email="patient@example.com",
            subject="Ack",
            body_text="Received",
        ),
    )
    assert res.provider_message_id.startswith("<mock-")
    assert len(mock_adapter.sent_messages) == 1

    batch = provider.fetch_messages(provider_type=EmailProviderType.MOCK.value, limit=10)
    assert len(batch.messages) == 1
    assert batch.messages[0].provider_message_id == "msg-q-1"

    with pytest.raises(ExternalProviderError):
        provider.send_email(
            provider_type=EmailProviderType.SMTP.value,
            message=OutboundEmailMessage(to_email="a@b.com", subject="S", body_text="B"),
            config={},
        )

    with pytest.raises(ExternalProviderError):
        provider.fetch_messages(provider_type=EmailProviderType.IMAP.value, config={})

    with pytest.raises(ValidationError):
        provider.get_adapter("UNKNOWN_ADAPTER")


def test_communication_templates_and_type_aliases() -> None:
    assert _normalize_communication_type("CLOSURE", default="GENERAL") == CommunicationType.CLOSURE_NOTIFICATION.value
    assert _normalize_communication_type("INBOUND_REPLY", default="GENERAL") == CommunicationType.REPORTER_REPLY.value
    with pytest.raises(ValidationError):
        _normalize_communication_type("INVALID_TYPE", default="GENERAL")

    dummy_complaint = Complaint(
        id=uuid.uuid4(),
        complaint_number="ZEN-CMP-2026-000001",
        reported_product_name="Amoxicillin 500mg",
        reported_batch_number="AMX-01",
        status=ComplaintStatus.RECEIVED.value,
        description="Broken capsule",
    )
    subj, body, tpl = _render_communication_content(
        template_code="ACK_STANDARD",
        template_variables=None,
        subject=None,
        body_text=None,
        complaint=dummy_complaint,
        recipient_name="Priya",
    )
    assert tpl == "ACK_STANDARD"
    assert "ZEN-CMP-2026-000001" in (subj or "")
    assert "Priya" in body
    assert "Amoxicillin 500mg" in body

    with pytest.raises(ValidationError) as exc_info:
        _render_communication_content(
            template_code="NO_SUCH_TEMPLATE",
            template_variables=None,
            subject=None,
            body_text=None,
            complaint=dummy_complaint,
            recipient_name="Priya",
        )
    assert exc_info.value.error_code == "UNKNOWN_TEMPLATE_CODE"


def test_deterministic_email_classifier_branches() -> None:
    likely = _classify_inbound_email_deterministic(
        "Quality complaint: broken tablet in batch LOT-2026-99",
        "The blister strip had a cracked tablet and discoloration.",
    )
    assert likely.classification == InboundEmailClassification.LIKELY_COMPLAINT.value
    assert likely.confidence >= Decimal("0.75")
    assert likely.extracted_batch_number == "LOT-2026-99"

    non_comp = _classify_inbound_email_deterministic(
        "Monthly Pharma Newsletter",
        "Click here to unsubscribe from marketing updates.",
    )
    assert non_comp.classification == InboundEmailClassification.NON_COMPLAINT.value

    uncertain = _classify_inbound_email_deterministic(
        "Question about order #4412",
        "Can you share the delivery tracking link for our clinic?",
    )
    assert uncertain.classification == InboundEmailClassification.UNCERTAIN.value


def test_closure_message_requires_closed_or_qa_review_status(sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        actor = create_seeded_tenant_actor(
            uow.session,
            org_name="Closure Guard Pharma",
            site_code="CGP-01",
            email="qa@closureguard.in",
            role_code="QA_OFFICER",
        )
        org_id = actor["organization"].id
        site_id = actor["site"].id
        membership_id = actor["membership"].id
        user_id = actor["user"].id
        comp = Complaint(
            organization_id=org_id,
            site_id=site_id,
            complaint_number="CGP-CMP-2026-000001",
            description="Open complaint in RECEIVED status",
            status=ComplaintStatus.RECEIVED.value,
        )
        uow.session.add(comp)
        uow.session.flush()
        comp_id = comp.id

    from app.core.deps import TenantContext

    tenant_ctx = TenantContext(
        user_id=user_id,
        organization_id=org_id,
        membership_id=membership_id,
        allowed_site_ids=frozenset({site_id}),
        is_org_wide=False,
        permission_codes=frozenset({"complaint.communicate", "complaint.read"}),
    )
    db = session_factory()
    try:
        with pytest.raises(ConflictError):
            ComplaintService().send_closure_message(
                db,
                complaint_id=comp_id,
                tenant_ctx=tenant_ctx,
                payload=ClosureMessageCreateRequest(
                    channel="PORTAL",
                    body_text="Closing early should fail.",
                ),
            )
    finally:
        db.close()


def test_inbound_email_llm_failure_fails_safe_to_manual_triage_outside_tx(sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        actor = create_seeded_tenant_actor(
            uow.session,
            org_name="Failsafe Email Pharma",
            site_code="FEP-01",
            email="admin@failsafe.in",
            role_code="ORG_ADMIN",
        )
        org_id = actor["organization"].id
        conn = EmailConnection(
            organization_id=org_id,
            name="Intake Box",
            provider_type=EmailProviderType.WEBHOOK.value,
            mailbox_address="intake@failsafe.in",
            inbound_secret_hash=hash_webhook_secret("secret-123"),
            auto_create_threshold=Decimal("0.7500"),
        )
        uow.session.add(conn)
        uow.session.flush()
        inbound = InboundEmail(
            organization_id=org_id,
            email_connection_id=conn.id,
            provider_message_id="msg-llm-fail-1",
            from_email="reporter@clinic.in",
            from_name="Dr. Ramesh",
            subject="Tablet issue",
            body_text="Discolored capsule found by Dr. Ramesh (reporter@clinic.in).",
            triage_status=InboundEmailTriageStatus.RECEIVED.value,
        )
        uow.session.add(inbound)
        uow.session.flush()
        inbound_id = inbound.id
        ev = OutboxEvent(
            organization_id=org_id,
            aggregate_type="inbound_email",
            aggregate_id=inbound_id,
            event_type="InboundEmailReceived",
            payload_json={"inbound_email_id": str(inbound_id), "organization_id": str(org_id)},
        )
        uow.session.add(ev)

    class _FailingTransport:
        def __init__(self) -> None:
            self.saw_active_tx = False
            self.prompts: List[str] = []

        def complete(self, *, system_prompt, user_prompt, model_name, temperature, max_tokens, timeout_seconds):
            self.prompts.append(user_prompt)
            raise RuntimeError("Simulated LLM outage")

    transport = _FailingTransport()
    settings = Settings(
        DATABASE_URL=sqlite_db["db_url"],
        JWT_SIGNING_KEY="test-only-signing-key-with-at-least-32-characters",
    )
    provider = LLMProvider(transport=transport, settings=settings)

    svc = ComplaintService()
    worker = OutboxWorker(session_factory=session_factory)
    worker.register_external_handler(
        "InboundEmailReceived",
        lambda sf, event, ctx: svc.handle_outbox_inbound_email(
            sf, event, ctx, llm_provider=provider
        ),
    )
    assert worker.run_once() == 1
    assert len(transport.prompts) == 1
    # PIIRedactor masked sender name & email before LLM call
    assert "reporter@clinic.in" not in transport.prompts[0]
    assert "Dr. Ramesh" not in transport.prompts[0]

    db = session_factory()
    try:
        row = db.get(InboundEmail, inbound_id)
        assert row is not None
        assert row.classification == InboundEmailClassification.UNCERTAIN.value
        assert row.triage_status == InboundEmailTriageStatus.MANUAL_TRIAGE_PENDING.value
        assert row.complaint_id is None
        attempts = db.query(EmailProcessingAttempt).filter_by(inbound_email_id=inbound_id).all()
        assert len(attempts) == 1
        assert attempts[0].status == "FALLBACK_MANUAL_TRIAGE"
        assert attempts[0].error_code == "LLM_CLASSIFICATION_FALLBACK"
    finally:
        db.close()


def test_outbound_email_failure_records_failed_status_and_retries(sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        actor = create_seeded_tenant_actor(
            uow.session,
            org_name="Retry Mail Pharma",
            site_code="RMP-01",
            email="qa@retrymail.in",
            role_code="QA_OFFICER",
        )
        org_id = actor["organization"].id
        site_id = actor["site"].id
        user_id = actor["user"].id
        membership_id = actor["membership"].id
        comp = Complaint(
            organization_id=org_id,
            site_id=site_id,
            complaint_number="RMP-CMP-2026-000001",
            description="Test retryable outbound failure",
            status=ComplaintStatus.RECEIVED.value,
        )
        uow.session.add(comp)
        uow.session.flush()
        comp_id = comp.id

    from app.core.deps import TenantContext

    tenant_ctx = TenantContext(
        user_id=user_id,
        organization_id=org_id,
        membership_id=membership_id,
        allowed_site_ids=frozenset({site_id}),
        is_org_wide=False,
        permission_codes=frozenset({"complaint.communicate", "complaint.read"}),
    )
    db = session_factory()
    try:
        comm = ComplaintService().add_communication(
            db,
            complaint_id=comp_id,
            tenant_ctx=tenant_ctx,
            payload=ComplaintCommunicationCreateRequest(
                channel=CommunicationChannel.EMAIL.value,
                direction="OUTBOUND",
                recipient_email="customer@example.com",
                subject="Update",
                body_text="Investigating your complaint.",
            ),
        )
        comm_id = comm.id
        assert comm.delivery_status == CommunicationDeliveryStatus.QUEUED.value
    finally:
        db.close()

    failing_provider = EmailProvider(default_mock=MockEmailAdapter(fail_send=True))
    worker = OutboxWorker(session_factory=session_factory)
    worker.register_external_handler(
        "OutboundCommunicationRequested",
        lambda sf, event, ctx: ComplaintService().handle_outbox_outbound_communication(
            sf, event, ctx, email_provider=failing_provider
        ),
    )
    assert worker.run_once() == 1

    db = session_factory()
    try:
        row = db.get(ComplaintCommunication, comm_id)
        assert row is not None
        assert row.delivery_status == CommunicationDeliveryStatus.FAILED.value
        assert row.attempt_count == 1
        assert "Simulated outbound email transport failure" in (row.failed_reason or "")
    finally:
        db.close()
