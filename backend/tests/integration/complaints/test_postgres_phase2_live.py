import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import UnitOfWork, reset_engine_cache
from app.domain.enums import ComplaintStatus
from app.domain.rbac_catalog import seed_rbac_catalog
from app.models.domain.complaints import Complaint, ComplaintBatchLink
from tests.conftest import BACKEND_DIR, create_seeded_tenant_actor, get_live_postgres_url


@pytest.fixture()
def live_pg_phase2(monkeypatch: pytest.MonkeyPatch):
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


def test_pg_phase2_check_and_composite_fk_constraints(live_pg_phase2) -> None:
    alembic_cfg = live_pg_phase2["alembic_cfg"]
    session_factory = live_pg_phase2["session_factory"]

    # 1. CHECK (status = 'UNROUTED_TRIAGE' OR organization_id IS NOT NULL)
    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(
                Complaint(
                    organization_id=None,
                    complaint_number="BAD-2026-000001",
                    description="Invalid routed complaint without organization",
                    status=ComplaintStatus.RECEIVED.value,
                )
            )

    # 2. Composite FK prevents cross-tenant ComplaintBatchLink pointing at another org's complaint
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        org_a = create_seeded_tenant_actor(
            uow.session,
            org_name="PG Comp A",
            site_code="PGCA-1",
            email="pgca@example.com",
            role_code="QA_OFFICER",
        )
        org_b = create_seeded_tenant_actor(
            uow.session,
            org_name="PG Comp B",
            site_code="PGCB-1",
            email="pgcb@example.com",
            role_code="QA_OFFICER",
        )
        comp_a = Complaint(
            organization_id=org_a["organization"].id,
            complaint_number="PGCA-CMP-2026-000001",
            description="Alpha PG complaint",
            status=ComplaintStatus.RECEIVED.value,
        )
        uow.session.add(comp_a)
        uow.session.flush()
        comp_a_id = comp_a.id
        org_b_id = org_b["organization"].id

    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(
                ComplaintBatchLink(
                    organization_id=org_b_id,
                    complaint_id=comp_a_id,
                    batch_id=uuid.uuid4(),
                    resolution_outcome="MANUAL_LINKED",
                    match_status="MANUAL_LINK",
                )
            )

    command.downgrade(alembic_cfg, "0002_phase1_tenancy_actors")
    command.upgrade(alembic_cfg, "head")
