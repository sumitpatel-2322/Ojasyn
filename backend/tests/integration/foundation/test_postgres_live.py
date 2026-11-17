import json
import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import UnitOfWork, reset_engine_cache
from app.core.security import create_access_token
from app.domain.enums import ActorType
from app.domain.rbac_catalog import seed_rbac_catalog
from app.integrations.sse_transport import SSETransport
from app.models.domain import AuditEvent
from app.repositories.governance_repo import OutboxRepository
from app.services.audit_service import AuditService
from app.services.notification_service import NotificationService
from tests.conftest import BACKEND_DIR, create_seeded_tenant_actor, get_live_postgres_url


@pytest.fixture()
def live_pg(monkeypatch: pytest.MonkeyPatch):
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

    yield {"pg_url": pg_url, "alembic_cfg": alembic_cfg, "engine": engine, "session_factory": session_factory}

    engine.dispose()
    reset_engine_cache()
    get_settings.cache_clear()


def test_pg_alembic_upgrade_downgrade_upgrade_and_triggers(live_pg) -> None:
    alembic_cfg = live_pg["alembic_cfg"]
    session_factory = live_pg["session_factory"]

    # Downgrade -> base -> upgrade -> head roundtrip
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seed_rbac_catalog(uow.session)
        seeded = create_seeded_tenant_actor(
            uow.session,
            org_name="PG Trigger Pharma",
            site_code="PGT-01",
            email="pg.trigger@example.com",
            role_code="QA_OFFICER",
        )
        ev = AuditService().record(
            uow.session,
            organization_id=seeded["organization"].id,
            actor_type=ActorType.USER.value,
            actor_id=seeded["user"].id,
            action="TEST_RECORD",
            entity_type="organizations",
            entity_id=seeded["organization"].id,
        )
        ev_id = ev.id

    # Verify PostgreSQL BEFORE UPDATE OR DELETE trigger blocks mutations on audit_events
    with pytest.raises(Exception, match="append-only"):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.execute(
                text("UPDATE audit_events SET action = 'TAMPERED' WHERE id = :id"),
                {"id": ev_id},
            )


def test_pg_cross_process_notify_and_sse_replay(live_pg) -> None:
    pg_url = live_pg["pg_url"]
    session_factory = live_pg["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seeded = create_seeded_tenant_actor(
            uow.session,
            org_name="CrossProc Pharma",
            site_code="XPROC-01",
            email="xproc@example.com",
            role_code="QA_OFFICER",
        )
        org_id = seeded["organization"].id
        membership_id = seeded["membership"].id

    transport = SSETransport()
    transport.start_listener(pg_url)
    try:
        assert transport.wait_until_listening(timeout=5.0) is True
        import asyncio

        async def _run_test():
            transport.bind_loop(asyncio.get_running_loop())
            queue = transport.subscribe(membership_id)
            try:
                # Spawn a separate OS subprocess (Process B) to commit Notification + pg_notify
                script = textwrap.dedent(
                    f"""
                    import uuid
                    from app.core.database import UnitOfWork, get_session_factory
                    from app.services.notification_service import NotificationService

                    org_id = uuid.UUID({str(org_id)!r})
                    m_id = uuid.UUID({str(membership_id)!r})
                    with UnitOfWork(get_session_factory()) as uow:
                        NotificationService().create_in_tx(
                            uow.session,
                            organization_id=org_id,
                            recipient_membership_id=m_id,
                            event_type="ComplaintRegistered",
                            aggregate_type="complaint",
                            aggregate_id=uuid.uuid4(),
                            title="Subprocess Alert",
                            body="From Process B",
                        )
                    """
                ).strip()
                env = os.environ.copy()
                env["APP_ENV"] = "test"
                env["DATABASE_URL"] = pg_url
                env["JWT_SIGNING_KEY"] = "test-only-signing-key-with-at-least-32-characters"
                proc = subprocess.run(
                    [sys.executable, "-c", script],
                    cwd=str(BACKEND_DIR),
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert proc.returncode == 0, (
                    f"Subprocess failed (rc={proc.returncode}):\n"
                    f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
                )
                received = await asyncio.wait_for(queue.get(), timeout=5.0)
                assert received["title"] == "Subprocess Alert"
                assert received["recipient_membership_id"] == str(membership_id)

                frames = []
                async for chunk in transport.stream_events(
                    organization_id=org_id,
                    recipient_membership_id=membership_id,
                    last_event_id=0,
                    session_factory=session_factory,
                    max_events=1,
                ):
                    frames.append(chunk)
                assert len(frames) == 1
                assert "Subprocess Alert" in frames[0]
                assert f"id: {received['delivery_seq']}" in frames[0]
            finally:
                transport.unsubscribe(membership_id, queue)

        asyncio.run(_run_test())
    finally:
        transport.stop_listener()
