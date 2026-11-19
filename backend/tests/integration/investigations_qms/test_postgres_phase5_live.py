from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, delete, update
from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import UnitOfWork, reset_engine_cache
from app.domain.rbac_catalog import seed_rbac_catalog
from app.models.domain.investigations import (
    EvidenceReference,
    GeneratedReport,
    Investigation,
    RootCauseHypothesis,
    SOPComplianceResult,
)
from app.models.domain.qms_loop import Deviation
from tests.conftest import BACKEND_DIR, create_seeded_tenant_actor, get_live_postgres_url


@pytest.fixture()
def live_pg_phase5(monkeypatch: pytest.MonkeyPatch):
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


def test_pg_phase5_alembic_roundtrip_and_composite_fks(live_pg_phase5) -> None:
    alembic_cfg = live_pg_phase5["alembic_cfg"]
    session_factory = live_pg_phase5["session_factory"]

    # Verify 0006 -> 0005 -> head roundtrip works cleanly on PostgreSQL
    command.downgrade(alembic_cfg, "0005_phase4_mfg_evidence")
    command.upgrade(alembic_cfg, "head")

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seed_rbac_catalog(uow.session)
        org_a = create_seeded_tenant_actor(
            uow.session,
            org_name="Phase5 PG Org A",
            site_code="P5A-01",
            email="qa.p5a@example.com",
            role_code="QMS_HEAD",
        )
        org_b = create_seeded_tenant_actor(
            uow.session,
            org_name="Phase5 PG Org B",
            site_code="P5B-01",
            email="qa.p5b@example.com",
            role_code="QMS_HEAD",
        )

        inv_a = Investigation(
            organization_id=org_a["organization"].id,
            site_id=org_a["site"].id,
            investigation_number="INV-PG5-A1",
            trigger_type="MANUAL",
            title="Live PG Inv A",
            status="QUEUED",
            created_by_user_id=org_a["user"].id,
        )
        dev_b = Deviation(
            organization_id=org_b["organization"].id,
            site_id=org_b["site"].id,
            deviation_number="DEV-PG5-B1",
            source_type="MANUAL",
            classification="MAJOR",
            title="Live PG Dev B",
            description="Org B deviation",
            status="OPEN",
            created_by_user_id=org_b["user"].id,
        )
        uow.session.add_all([inv_a, dev_b])
        uow.session.flush()

        org_a_id = org_a["organization"].id
        site_a_id = org_a["site"].id
        user_a_id = org_a["user"].id
        inv_a_id = inv_a.id
        dev_b_id = dev_b.id

    # Cross-tenant circular FK fk_investigations_org_dev must block Org A Investigation -> Org B Deviation
    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(
                Investigation(
                    organization_id=org_a_id,
                    site_id=site_a_id,
                    deviation_id=dev_b_id,
                    investigation_number="INV-PG5-CROSS",
                    trigger_type="DEVIATION",
                    title="Cross-tenant deviation link",
                    status="QUEUED",
                )
            )
            uow.session.flush()

    # Cross-tenant composite FK fk_deviations_org_inv must block Org B Deviation -> Org A Investigation
    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            dev = uow.session.get(Deviation, dev_b_id)
            assert dev is not None
            dev.investigation_id = inv_a_id
            uow.session.flush()

    # DB-level maker-checker CHECK constraint ck_gen_reports_maker_checker_user
    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(
                GeneratedReport(
                    organization_id=org_a_id,
                    investigation_id=inv_a_id,
                    report_number="RPT-PG5-BAD",
                    version_number=1,
                    title="Self reviewed report",
                    status="APPROVED",
                    generated_by_user_id=user_a_id,
                    reviewed_by_user_id=user_a_id,
                )
            )
            uow.session.flush()


def test_pg_phase5_append_only_triggers(live_pg_phase5) -> None:
    session_factory = live_pg_phase5["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seed_rbac_catalog(uow.session)
        org_a = create_seeded_tenant_actor(
            uow.session,
            org_name="Phase5 AppendOnly Org",
            site_code="P5AO-01",
            email="qa.p5ao@example.com",
            role_code="QA_HEAD",
        )
        inv = Investigation(
            organization_id=org_a["organization"].id,
            site_id=org_a["site"].id,
            investigation_number="INV-PG5-AO1",
            trigger_type="MANUAL",
            title="Append only check",
            status="PROCESSING",
        )
        uow.session.add(inv)
        uow.session.flush()

        ev = EvidenceReference(
            organization_id=org_a["organization"].id,
            investigation_id=inv.id,
            source_type="OTHER",
            source_table="investigations",
            source_record_id=inv.id,
            quoted_value="Immutable evidence",
        )
        uow.session.add(ev)
        uow.session.flush()

        comp = SOPComplianceResult(
            organization_id=org_a["organization"].id,
            investigation_id=inv.id,
            evidence_ref_id=ev.id,
            check_type="PROCESS_VALUE_VS_SOP_LIMIT",
            rule_identifier="RULE_PARAM_VS_SOP_LIMIT",
            rule_version="1.0.0",
            status="PASS",
            explanation="Immutable compliance result",
            evaluated_at=datetime.now(timezone.utc),
        )
        uow.session.add(comp)
        uow.session.flush()
        ev_id = ev.id
        comp_id = comp.id

    # PG append-only trigger blocks UPDATE/DELETE on evidence_references
    with pytest.raises((InternalError, ProgrammingError)):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.execute(
                update(EvidenceReference).where(EvidenceReference.id == ev_id).values(quoted_value="Tampered")
            )

    # PG append-only trigger blocks DELETE on sop_compliance_results
    with pytest.raises((InternalError, ProgrammingError)):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.execute(delete(SOPComplianceResult).where(SOPComplianceResult.id == comp_id))
