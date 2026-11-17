import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import UnitOfWork, reset_engine_cache
from app.domain.rbac_catalog import seed_rbac_catalog
from app.models.domain.master_data import ReferenceValue
from tests.conftest import BACKEND_DIR, get_live_postgres_url


@pytest.fixture()
def live_pg_phase1(monkeypatch: pytest.MonkeyPatch):
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


def test_pg_phase1_nulls_not_distinct_and_downgrade_roundtrip(live_pg_phase1) -> None:
    alembic_cfg = live_pg_phase1["alembic_cfg"]
    session_factory = live_pg_phase1["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(
            ReferenceValue(
                organization_id=None,
                category_code="TEST_CAT",
                code="DUP_CODE",
                display_name="First Global",
            )
        )

    # PostgreSQL 15+ NULLS NOT DISTINCT constraint rejects duplicate global ReferenceValue
    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(
                ReferenceValue(
                    organization_id=None,
                    category_code="TEST_CAT",
                    code="DUP_CODE",
                    display_name="Second Global",
                )
            )

    # Clean 0002 -> 0001 -> head roundtrip
    command.downgrade(alembic_cfg, "0001_phase0_foundation")
    command.upgrade(alembic_cfg, "head")
