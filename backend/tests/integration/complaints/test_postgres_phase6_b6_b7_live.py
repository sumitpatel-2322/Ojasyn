import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import UnitOfWork, reset_engine_cache
from app.domain.enums import (
    CommunicationDirection,
    CommunicationType,
    ComplaintStatus,
    EmailProviderType,
    InboundEmailTriageStatus,
)
from app.domain.rbac_catalog import seed_rbac_catalog
from app.integrations.email_provider import hash_webhook_secret
from app.models.domain.complaints import (
    Complaint,
    ComplaintCommunication,
    EmailConnection,
    EmailProcessingAttempt,
    InboundEmail,
)
from tests.conftest import BACKEND_DIR, create_seeded_tenant_actor, get_live_postgres_url


@pytest.fixture()
def live_pg_phase6_b6_b7(monkeypatch: pytest.MonkeyPatch):
    pg_url = get_live_postgres_url()
    if not pg_url:
        pytest.skip("TEST_DATABASE_URL not set or PostgreSQL unreachable in current environment.")

    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("JWT_SIGNING_KEY", "test-only-signing-key-with-at-least-32-characters")
    get_settings.cache_clear()
    reset_engine_cache()

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", pg_url)
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(pg_url, pool_pre_ping=True)
    session_factory = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=engine)

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seed_rbac_catalog(uow.session)

    yield {"pg_url": pg_url, "alembic_cfg": alembic_cfg, "session_factory": session_factory}

    engine.dispose()
    reset_engine_cache()
    get_settings.cache_clear()


def test_pg_phase6_b6_b7_constraints_dedupe_and_append_only(live_pg_phase6_b6_b7) -> None:
    alembic_cfg = live_pg_phase6_b6_b7["alembic_cfg"]
    session_factory = live_pg_phase6_b6_b7["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        org_a = create_seeded_tenant_actor(
            uow.session,
            org_name="PG B6B7 Alpha",
            site_code="PGB6-A",
            email="pgb6a@example.com",
            role_code="QA_HEAD",
        )
        org_b = create_seeded_tenant_actor(
            uow.session,
            org_name="PG B6B7 Beta",
            site_code="PGB6-B",
            email="pgb6b@example.com",
            role_code="QA_HEAD",
        )
        org_a_id = org_a["organization"].id
        org_b_id = org_b["organization"].id

        comp_a = Complaint(
            organization_id=org_a_id,
            complaint_number="PGB6A-CMP-2026-000001",
            description="PG B6 complaint",
            status=ComplaintStatus.RECEIVED.value,
        )
        uow.session.add(comp_a)
        uow.session.flush()

        comm_a = ComplaintCommunication(
            organization_id=org_a_id,
            complaint_id=comp_a.id,
            channel="EMAIL",
            direction=CommunicationDirection.OUTBOUND.value,
            communication_type=CommunicationType.INFO_REQUEST.value,
            body_text="Need batch number",
        )
        uow.session.add(comm_a)

        conn_a = EmailConnection(
            organization_id=org_a_id,
            name="PG Alpha Mailbox",
            provider_type=EmailProviderType.WEBHOOK.value,
            mailbox_address="intake@pgalpha.test",
            inbound_secret_hash=hash_webhook_secret("pg-secret"),
        )
        uow.session.add(conn_a)
        uow.session.flush()

        inbound_a = InboundEmail(
            organization_id=org_a_id,
            email_connection_id=conn_a.id,
            provider_message_id="pg-msg-unique-1",
            from_email="reporter@pg.test",
            subject="Broken tablet",
            body_text="Broken tablet in blister",
            triage_status=InboundEmailTriageStatus.RECEIVED.value,
        )
        uow.session.add(inbound_a)
        uow.session.flush()

        attempt = EmailProcessingAttempt(
            organization_id=org_a_id,
            inbound_email_id=inbound_a.id,
            attempt_number=1,
            status="COMPLETED",
            classifier_version="pg-test-v1",
        )
        uow.session.add(attempt)
        uow.session.flush()

        comp_a_id = comp_a.id
        comm_a_id = comm_a.id
        conn_a_id = conn_a.id
        inbound_a_id = inbound_a.id
        attempt_id = attempt.id

    # 1. Duplicate (organization_id, provider_message_id) rejected by uq_inbound_emails_org_msg_id
    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(
                InboundEmail(
                    organization_id=org_a_id,
                    email_connection_id=conn_a_id,
                    provider_message_id="pg-msg-unique-1",
                    from_email="reporter@pg.test",
                    triage_status=InboundEmailTriageStatus.RECEIVED.value,
                )
            )

    # 2. Cross-tenant composite FK fk_complaint_comms_org_reply blocks replying across orgs
    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            comp_b = Complaint(
                organization_id=org_b_id,
                complaint_number="PGB6B-CMP-2026-000001",
                description="Org B complaint",
                status=ComplaintStatus.RECEIVED.value,
            )
            uow.session.add(comp_b)
            uow.session.flush()
            uow.session.add(
                ComplaintCommunication(
                    organization_id=org_b_id,
                    complaint_id=comp_b.id,
                    channel="EMAIL",
                    direction=CommunicationDirection.INBOUND.value,
                    communication_type=CommunicationType.REPORTER_REPLY.value,
                    body_text="Cross-tenant reply probe",
                    in_reply_to_communication_id=comm_a_id,
                )
            )

    # 3. Append-only trigger trg_prevent_mutation_email_processing_attempts blocks UPDATE and DELETE
    with pytest.raises(DBAPIError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.execute(
                text("UPDATE email_processing_attempts SET status = 'TAMPERED' WHERE id = :id"),
                {"id": attempt_id},
            )

    with pytest.raises(DBAPIError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.execute(
                text("DELETE FROM email_processing_attempts WHERE id = :id"),
                {"id": attempt_id},
            )

    # 4. Verify downgrade -> upgrade round-trip across 0007
    command.downgrade(alembic_cfg, "0006_phase5_investigations")
    command.upgrade(alembic_cfg, "head")
