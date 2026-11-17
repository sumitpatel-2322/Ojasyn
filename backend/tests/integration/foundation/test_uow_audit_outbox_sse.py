import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.database import UnitOfWork, next_tenant_sequence, verify_and_increment_version
from app.core.exceptions import ConflictError
from app.domain.enums import ActorType, OrganizationType, OutboxStatus
from app.integrations.sse_transport import SSETransport
from app.models.domain import AuditEvent, Organization, OutboxEvent, SecurityEvent
from app.repositories.governance_repo import OutboxRepository
from app.services.audit_service import AuditService
from app.services.notification_service import NotificationService
from app.worker import OutboxWorker
from tests.conftest import create_seeded_tenant_actor, run_in_memory_async


def test_unit_of_work_atomic_commit_and_nested_rollback(sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]
    audit_svc = AuditService()

    # 1. Nested UoW rolls back completely when inner/outer fails; SecurityEvent survives
    failing_org_id = uuid.uuid4()
    try:
        with UnitOfWork(session_factory) as outer:
            assert outer.session is not None
            org = Organization(
                id=failing_org_id,
                legal_name="Rollback Pharma",
                organization_type=OrganizationType.FDF_MANUFACTURER.value,
                country_of_registration="IN",
                business_email="rollback@example.com",
            )
            outer.session.add(org)
            outer.session.flush()

            with UnitOfWork(outer.session) as inner:
                assert inner.session is not None
                audit_svc.record(
                    inner.session,
                    organization_id=org.id,
                    actor_type=ActorType.USER.value,
                    action="ORG_CREATED",
                    entity_type="organizations",
                    entity_id=org.id,
                    after_state={"legal_name": org.legal_name},
                )
                raise RuntimeError("boom")
    except RuntimeError:
        # Out-of-band security event commits independently of the rolled-back business UoW
        audit_svc.record_security_event(
            event_type="TEST_OUT_OF_BAND_SECURITY_PROBE",
            details_json={"attempted_org": str(failing_org_id)},
            session_factory=session_factory,
        )

    verify_session = session_factory()
    try:
        assert verify_session.get(Organization, failing_org_id) is None
        assert (
            verify_session.scalar(
                select(AuditEvent).where(AuditEvent.entity_id == failing_org_id)
            )
            is None
        )
        sec_event = verify_session.scalar(
            select(SecurityEvent).where(SecurityEvent.event_type == "TEST_OUT_OF_BAND_SECURITY_PROBE")
        )
        assert sec_event is not None
    finally:
        verify_session.close()

    # 2. Successful nested UoW commits both Organization and AuditEvent with hashes
    committed_org_id = uuid.uuid4()
    with UnitOfWork(session_factory) as outer:
        assert outer.session is not None
        org = Organization(
            id=committed_org_id,
            legal_name="Committed Pharma",
            organization_type=OrganizationType.FDF_MANUFACTURER.value,
            country_of_registration="IN",
            business_email="commit@example.com",
        )
        outer.session.add(org)
        outer.session.flush()
        with UnitOfWork(outer.session) as inner:
            assert inner.session is not None
            audit_svc.record(
                inner.session,
                organization_id=org.id,
                actor_type=ActorType.USER.value,
                action="ORG_CREATED",
                entity_type="organizations",
                entity_id=org.id,
                after_state={"legal_name": org.legal_name},
            )

    check = session_factory()
    try:
        saved_org = check.get(Organization, committed_org_id)
        assert saved_org is not None
        saved_audit = check.scalar(select(AuditEvent).where(AuditEvent.entity_id == committed_org_id))
        assert saved_audit is not None
        assert saved_audit.before_hash is None
        assert saved_audit.after_hash is not None and len(saved_audit.after_hash) == 64

        # Optimistic concurrency version check
        assert verify_and_increment_version(saved_org, 1) == 2
        with pytest.raises(ConflictError):
            verify_and_increment_version(saved_org, 1)
    finally:
        check.close()


def test_tenant_sequence_generation(sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seeded = create_seeded_tenant_actor(
            uow.session,
            org_name="Seq Pharma",
            site_code="SEQ-01",
            email="seq@example.com",
            role_code="QA_OFFICER",
        )
        org_id = seeded["organization"].id
        s1 = next_tenant_sequence(uow.session, org_id, "COMPLAINT", period_year=2026, prefix="CMP")
        s2 = next_tenant_sequence(uow.session, org_id, "COMPLAINT", period_year=2026, prefix="CMP")
        assert s1 == "CMP-2026-000001"
        assert s2 == "CMP-2026-000002"


def test_outbox_worker_claim_backoff_dead_letter_and_lease_reclaim(sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]
    repo = OutboxRepository()

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seeded = create_seeded_tenant_actor(
            uow.session,
            org_name="Worker Pharma",
            site_code="WRK-01",
            email="qa.worker@example.com",
            role_code="QA_OFFICER",
        )
        org_id = seeded["organization"].id
        membership_id = seeded["membership"].id

        # 1. Valid notification outbox event
        notif_ev = repo.enqueue(
            uow.session,
            organization_id=org_id,
            aggregate_type="complaint",
            aggregate_id=uuid.uuid4(),
            event_type="NotificationRequested",
            payload_json={
                "recipient_membership_id": str(membership_id),
                "title": "New Complaint Received",
                "body": "Complaint CMP-2026-000001 assigned.",
            },
        )
        # 2. Failing outbox event to test backoff & dead-letter
        fail_ev = repo.enqueue(
            uow.session,
            organization_id=org_id,
            aggregate_type="complaint",
            aggregate_id=uuid.uuid4(),
            event_type="AlwaysFails",
            payload_json={},
            max_attempts=2,
        )
        # 3. Expired processing lease to test crash recovery
        stale_ev = OutboxEvent(
            organization_id=org_id,
            aggregate_type="investigation",
            aggregate_id=uuid.uuid4(),
            event_type="NoOpEvent",
            payload_json={},
            status=OutboxStatus.PROCESSING.value,
            worker_id="crashed-worker",
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=30),
            next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
        uow.session.add(stale_ev)
        # 4. Unregistered event_type must fail explicitly rather than vanish as PUBLISHED
        unknown_ev = repo.enqueue(
            uow.session,
            organization_id=org_id,
            aggregate_type="complaint",
            aggregate_id=uuid.uuid4(),
            event_type="UnregisteredEvent",
            payload_json={},
            max_attempts=1,
        )
        uow.session.flush()
        notif_id, fail_id, stale_id, unknown_id = notif_ev.id, fail_ev.id, stale_ev.id, unknown_ev.id

    def _always_fail(_db, _event, _ctx) -> None:
        raise RuntimeError("simulated provider outage")

    worker = OutboxWorker(session_factory=session_factory, base_backoff_seconds=1)
    worker.register_handler("AlwaysFails", _always_fail)
    worker.register_handler("NoOpEvent", lambda _db, _event, _ctx: None)

    processed = worker.run_once()
    assert processed == 4

    db = session_factory()
    try:
        assert db.get(OutboxEvent, notif_id).status == OutboxStatus.PUBLISHED.value
        assert db.get(OutboxEvent, stale_id).status == OutboxStatus.PUBLISHED.value
        unknown_row = db.get(OutboxEvent, unknown_id)
        assert unknown_row.status == OutboxStatus.DEAD_LETTER.value
        assert "No outbox handler registered" in (unknown_row.last_error or "")
        failed_row = db.get(OutboxEvent, fail_id)
        assert failed_row.status == OutboxStatus.FAILED.value
        assert failed_row.attempt_count == 1
        # Make next_attempt_at immediate so second run hits max_attempts -> DEAD_LETTER
        failed_row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()

    worker.run_once()
    db = session_factory()
    try:
        dead_row = db.get(OutboxEvent, fail_id)
        assert dead_row.status == OutboxStatus.DEAD_LETTER.value
        assert dead_row.attempt_count == 2
    finally:
        db.close()


def test_sse_replay_and_live_queue_delivery(sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seeded = create_seeded_tenant_actor(
            uow.session,
            org_name="SSE Pharma",
            site_code="SSE-01",
            email="sse.qa@example.com",
            role_code="QA_OFFICER",
        )
        org_id = seeded["organization"].id
        membership_id = seeded["membership"].id

        n1 = NotificationService().create_in_tx(
            uow.session,
            organization_id=org_id,
            recipient_membership_id=membership_id,
            event_type="ComplaintRegistered",
            aggregate_type="complaint",
            aggregate_id=uuid.uuid4(),
            title="Missed Notification 1",
            body="First alert",
        )
        n2 = NotificationService().create_in_tx(
            uow.session,
            organization_id=org_id,
            recipient_membership_id=membership_id,
            event_type="ComplaintRegistered",
            aggregate_type="complaint",
            aggregate_id=uuid.uuid4(),
            title="Missed Notification 2",
            body="Second alert",
        )
        seq1, seq2 = n1.delivery_seq, n2.delivery_seq

    transport = SSETransport()

    async def _collect_replay_and_live() -> list[str]:
        frames: list[str] = []
        gen = transport.stream_events(
            organization_id=org_id,
            recipient_membership_id=membership_id,
            last_event_id=seq1,  # Should replay only seq2, then receive live seq3
            session_factory=session_factory,
            max_events=2,
        )

        # Schedule a live notification dispatch after replay yields
        async def _emit_live():
            await asyncio.sleep(0.05)
            with UnitOfWork(session_factory) as uow:
                assert uow.session is not None
                n3 = NotificationService().create_in_tx(
                    uow.session,
                    organization_id=org_id,
                    recipient_membership_id=membership_id,
                    event_type="InvestigationCompleted",
                    aggregate_type="investigation",
                    aggregate_id=uuid.uuid4(),
                    title="Live Notification 3",
                    body="Live alert",
                )
                live_seq = n3.delivery_seq
            transport.dispatch_payload(
                {
                    "recipient_membership_id": str(membership_id),
                    "delivery_seq": live_seq,
                    "event_type": "InvestigationCompleted",
                }
            )

        emitter = asyncio.create_task(_emit_live())
        async for chunk in gen:
            frames.append(chunk)
        await emitter
        return frames

    frames = run_in_memory_async(_collect_replay_and_live())
    assert len(frames) == 2
    assert f"id: {seq2}" in frames[0]
    assert "Missed Notification 2" in frames[0]
    assert "Live Notification 3" in frames[1]
