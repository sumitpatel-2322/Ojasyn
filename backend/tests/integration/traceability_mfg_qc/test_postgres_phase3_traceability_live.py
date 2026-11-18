from datetime import date
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import UnitOfWork, reset_engine_cache
from app.domain.rbac_catalog import seed_rbac_catalog
from app.models.domain.complaints import Complaint, ComplaintBatchLink
from app.models.domain.governance import ApprovalRecord
from app.models.domain.products_materials import Product
from app.models.domain.traceability import (
    Batch,
    BatchDisposition,
    BatchMovementEvent,
    BatchStatusHistory,
)
from tests.conftest import BACKEND_DIR, create_seeded_tenant_actor, get_live_postgres_url


@pytest.fixture()
def live_pg_phase3_trace(monkeypatch: pytest.MonkeyPatch):
    pg_url = get_live_postgres_url()
    if not pg_url:
        pytest.skip("TEST_DATABASE_URL not set or PostgreSQL unreachable in current environment.")

    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("JWT_SIGNING_KEY", "test-only-signing-key-with-at-least-32-characters")
    get_settings.cache_clear()
    reset_engine_cache()

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", pg_url)
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(pg_url, pool_pre_ping=True)
    session_factory = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=engine)

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seed_rbac_catalog(uow.session)

    yield {"session_factory": session_factory}

    engine.dispose()
    reset_engine_cache()
    get_settings.cache_clear()


def test_pg_batch_constraints_append_only_triggers_and_cross_tenant_complaint_fks(
    live_pg_phase3_trace,
) -> None:
    session_factory = live_pg_phase3_trace["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        actor_a = create_seeded_tenant_actor(
            uow.session,
            org_name="PG Trace Org A",
            site_code="PGTA-1",
            email="pgta@alpha.in",
            role_code="PRODUCTION_HEAD",
        )
        actor_b = create_seeded_tenant_actor(
            uow.session,
            org_name="PG Trace Org B",
            site_code="PGTB-1",
            email="pgtb@beta.in",
            role_code="QA_OFFICER",
        )
        org_a_id = actor_a["organization"].id
        site_a_id = actor_a["site"].id
        user_a_id = actor_a["user"].id
        member_a_id = actor_a["membership"].id
        org_b_id = actor_b["organization"].id

        prod_a = Product(
            organization_id=org_a_id,
            site_id=site_a_id,
            product_code="PG-PRD-A",
            generic_name="Amoxicillin",
            dosage_form="CAPSULE",
            product_status="ACTIVE",
        )
        uow.session.add(prod_a)
        uow.session.flush()

        batch_a = Batch(
            organization_id=org_a_id,
            site_id=site_a_id,
            product_id=prod_a.id,
            batch_number="PG-B-001",
            manufacturing_date=date(2026, 1, 1),
            expiry_date=date(2028, 1, 1),
            batch_size_value=Decimal("100.0000"),
            batch_size_unit="BOXES",
            batch_status="PLANNED",
        )
        uow.session.add(batch_a)
        uow.session.flush()

        hist = BatchStatusHistory(
            organization_id=org_a_id,
            batch_id=batch_a.id,
            from_status=None,
            to_status="PLANNED",
            changed_by_user_id=user_a_id,
            changed_by_membership_id=member_a_id,
            reason="Initial PG test batch",
        )
        mov = BatchMovementEvent(
            organization_id=org_a_id,
            batch_id=batch_a.id,
            event_type="PRODUCED",
            quantity=Decimal("100.0000"),
            unit_code="BOXES",
            actor_user_id=user_a_id,
        )
        uow.session.add_all([hist, mov])
        uow.session.flush()
        prod_a_id, batch_a_id, hist_id, mov_id = prod_a.id, batch_a.id, hist.id, mov.id

    # 1. Batch expiry > mfg CHECK constraint
    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(
                Batch(
                    organization_id=org_a_id,
                    site_id=site_a_id,
                    product_id=prod_a_id,
                    batch_number="PG-B-BAD-DATE",
                    manufacturing_date=date(2026, 5, 1),
                    expiry_date=date(2026, 4, 1),
                    batch_size_value=Decimal("10.0000"),
                    batch_size_unit="BOXES",
                )
            )

    # 2. Composite cross-tenant FK: Org B complaint cannot reference Org A Product
    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(
                Complaint(
                    organization_id=org_b_id,
                    complaint_number="PG-CMP-XTEN-1",
                    product_id=prod_a_id,
                    description="Cross-tenant product FK check",
                    status="RECEIVED",
                )
            )

    # 3. Distributed quantity <= COALESCE(actual_yield_value, batch_size_value) CHECK constraint
    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.execute(
                text("UPDATE batches SET distributed_quantity = 150.0000 WHERE id = :id"),
                {"id": batch_a_id},
            )

    # 4. Append-only PG trigger enforcement on batch_status_history and batch_movement_events
    with pytest.raises(DBAPIError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.execute(
                text("UPDATE batch_status_history SET reason = 'tamper' WHERE id = :id"),
                {"id": hist_id},
            )

    with pytest.raises(DBAPIError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.execute(
                text("DELETE FROM batch_movement_events WHERE id = :id"),
                {"id": mov_id},
            )
