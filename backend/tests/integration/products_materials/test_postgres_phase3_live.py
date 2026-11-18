from datetime import date
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import UnitOfWork, reset_engine_cache
from app.domain.rbac_catalog import seed_rbac_catalog
from app.models.domain.actors import BusinessPartner
from app.models.domain.products_materials import Material, SupplierLot
from tests.conftest import BACKEND_DIR, create_seeded_tenant_actor, get_live_postgres_url


@pytest.fixture()
def live_pg_phase3_pm(monkeypatch: pytest.MonkeyPatch):
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


def test_pg_supplier_lot_quantity_check_constraint(live_pg_phase3_pm) -> None:
    session_factory = live_pg_phase3_pm["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        actor = create_seeded_tenant_actor(
            uow.session,
            org_name="PG Lot Org",
            site_code="PGL-01",
            email="lot@pgl.in",
            role_code="WAREHOUSE_MANAGER",
        )
        org_id = actor["organization"].id
        sup = BusinessPartner(
            organization_id=org_id,
            partner_code="SUP-PG",
            legal_name="PG Supplier",
            partner_type="SUPPLIER",
            primary_email="sup@pgl.in",
        )
        uow.session.add(sup)
        uow.session.flush()
        mat = Material(
            organization_id=org_id,
            material_code="PG-MAT-01",
            material_name="Lactose",
            material_type="EXCIPIENT",
            default_unit_code="KG",
        )
        uow.session.add(mat)
        uow.session.flush()
        sup_id, mat_id = sup.id, mat.id

    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(
                SupplierLot(
                    organization_id=org_id,
                    material_id=mat_id,
                    supplier_partner_id=sup_id,
                    internal_lot_number="SLOT-BAD-01",
                    supplier_lot_number="V-BAD-01",
                    received_quantity=Decimal("10.0000"),
                    remaining_quantity=Decimal("20.0000"),  # violates remaining <= received
                    unit_code="KG",
                    received_date=date(2026, 2, 1),
                )
            )
