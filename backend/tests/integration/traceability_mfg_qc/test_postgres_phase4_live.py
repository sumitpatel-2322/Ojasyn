from datetime import date, datetime, timedelta, timezone
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
from app.models.domain.identity import TrainingRecord
from app.models.domain.manufacturing_execution import MasterBatchRecord
from app.models.domain.products_materials import Product
from app.models.domain.qc_equipment import CalibrationRecord, Equipment
from app.models.domain.sops import SOP, SOPRevision
from app.models.domain.traceability import Batch
from tests.conftest import BACKEND_DIR, create_seeded_tenant_actor, get_live_postgres_url


@pytest.fixture()
def live_pg_phase4(monkeypatch: pytest.MonkeyPatch):
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


def test_pg_phase4_alembic_roundtrip_and_deferred_fks(live_pg_phase4) -> None:
    alembic_cfg = live_pg_phase4["alembic_cfg"]
    session_factory = live_pg_phase4["session_factory"]

    # Verify 0005 -> 0004 -> head roundtrip works cleanly on PostgreSQL
    command.downgrade(alembic_cfg, "0004_phase3_traceability")
    command.upgrade(alembic_cfg, "head")

    now = datetime.now(timezone.utc)
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seed_rbac_catalog(uow.session)
        org_a = create_seeded_tenant_actor(
            uow.session,
            org_name="Phase4 PG Org A",
            site_code="P4A-01",
            email="qa.p4a@example.com",
            role_code="QMS_HEAD",
        )
        org_b = create_seeded_tenant_actor(
            uow.session,
            org_name="Phase4 PG Org B",
            site_code="P4B-01",
            email="qa.p4b@example.com",
            role_code="QMS_HEAD",
        )

        sop_a = SOP(
            organization_id=org_a["organization"].id,
            site_id=org_a["site"].id,
            sop_code="SOP-PG-001",
            title="Live PG SOP",
            sop_status="ACTIVE",
        )
        uow.session.add(sop_a)
        uow.session.flush()

        rev_a = SOPRevision(
            organization_id=org_a["organization"].id,
            sop_id=sop_a.id,
            site_id=org_a["site"].id,
            revision_number="1.0",
            revision_sequence=1,
            effective_from=now - timedelta(days=5),
            revision_status="EFFECTIVE",
            approval_status="APPROVED",
            created_by_user_id=org_a["user"].id,
            submitted_by_membership_id=org_a["membership"].id,
        )
        uow.session.add(rev_a)
        uow.session.flush()

        prod_a = Product(
            organization_id=org_a["organization"].id,
            site_id=org_a["site"].id,
            product_code="PROD-PG4-A",
            generic_name="Paracetamol",
            dosage_form="TABLET",
            product_status="ACTIVE",
        )
        prod_b = Product(
            organization_id=org_b["organization"].id,
            site_id=org_b["site"].id,
            product_code="PROD-PG4-B",
            generic_name="Amoxicillin",
            dosage_form="CAPSULE",
            product_status="ACTIVE",
        )
        uow.session.add_all([prod_a, prod_b])
        uow.session.flush()

        mbr_b = MasterBatchRecord(
            organization_id=org_b["organization"].id,
            site_id=org_b["site"].id,
            product_id=prod_b.id,
            mbr_code="MBR-PG-B1",
            version_number="1.0",
            record_type="BMR",
            title="Org B MBR",
            applicable_sop_revision_ids=[],
            approval_status="APPROVED",
        )
        uow.session.add(mbr_b)
        uow.session.flush()

        org_a_id = org_a["organization"].id
        site_a_id = org_a["site"].id
        prod_a_id = prod_a.id
        user_a_id = org_a["user"].id
        rev_a_id = rev_a.id

        org_b_id = org_b["organization"].id
        site_b_id = org_b["site"].id
        member_b_id = org_b["membership"].id
        mbr_b_id = mbr_b.id

    # Verify deferred composite FK fk_batches_org_mbr blocks cross-tenant MBR on Batch
    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(
                Batch(
                    organization_id=org_a_id,
                    site_id=site_a_id,
                    product_id=prod_a_id,
                    batch_number="PG4-CROSS-MBR-01",
                    manufacturing_date=date(2026, 1, 1),
                    expiry_date=date(2028, 1, 1),
                    batch_size_value=Decimal("100.0000"),
                    batch_size_unit="BOXES",
                    master_batch_record_id=mbr_b_id,
                )
            )
            uow.session.flush()

    # Verify deferred composite FK fk_training_org_sop_rev blocks cross-tenant SOPRevision on TrainingRecord
    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(
                TrainingRecord(
                    organization_id=org_b_id,
                    membership_id=member_b_id,
                    site_id=site_b_id,
                    training_topic="Cross-tenant SOP training",
                    sop_revision_id=rev_a_id,
                    completed_on=date(2026, 1, 10),
                )
            )
            uow.session.flush()

    # Verify DB-level maker-checker CHECK constraint ck_sop_revisions_maker_checker_user on sop_revisions
    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            rev = uow.session.get(SOPRevision, rev_a_id)
            assert rev is not None
            rev.approved_by_user_id = user_a_id
            uow.session.flush()


def test_pg_phase4_check_constraints_and_composite_fks(live_pg_phase4) -> None:
    session_factory = live_pg_phase4["session_factory"]
    now = datetime.now(timezone.utc)

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seed_rbac_catalog(uow.session)
        org_a = create_seeded_tenant_actor(
            uow.session,
            org_name="Phase4 Constraint Org",
            site_code="P4C-01",
            email="qa.p4c@example.com",
            role_code="QMS_HEAD",
        )
        equip = Equipment(
            organization_id=org_a["organization"].id,
            site_id=org_a["site"].id,
            equipment_code="EQ-PG-01",
            equipment_name="HPLC System",
            equipment_category="ANALYTICAL",
            operational_status="ACTIVE",
            qualification_status="QUALIFIED",
            calibration_required=True,
            calibration_frequency_days=180,
        )
        uow.session.add(equip)
        uow.session.flush()
        org_id = org_a["organization"].id
        site_id = org_a["site"].id
        equip_id = equip.id
        user_id = org_a["user"].id
        member_id = org_a["membership"].id

    # Verify ck_cal_records_due_after_cal CHECK constraint (due_at > calibrated_at)
    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            bad_cal = CalibrationRecord(
                organization_id=org_id,
                site_id=site_id,
                equipment_id=equip_id,
                calibration_code="CAL-BAD-01",
                calibrated_at=now,
                due_at=now - timedelta(days=1),
                calibration_provider="CalLab Inc",
                calibration_result="PASS",
                record_status="DRAFT",
                recorded_by_user_id=user_id,
                recorded_by_membership_id=member_id,
            )
            uow.session.add(bad_cal)
            uow.session.flush()
