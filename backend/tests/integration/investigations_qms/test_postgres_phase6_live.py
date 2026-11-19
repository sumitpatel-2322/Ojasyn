import uuid
from datetime import date, datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import UnitOfWork, reset_engine_cache
from app.domain.enums import (
    CAPAStatus,
    ComplaintSource,
    ComplaintStatus,
    EffectivenessCheckStatus,
)
from app.domain.rbac_catalog import seed_rbac_catalog
from app.models.domain.complaints import Complaint
from app.models.domain.investigations import Investigation
from app.models.domain.qms_loop import CAPA, CAPAAction, CAPAEffectivenessCheck, Deviation
from app.repositories.qms_repo import QMSRepository
from tests.conftest import BACKEND_DIR, create_seeded_tenant_actor, get_live_postgres_url


@pytest.fixture()
def live_pg_phase6(monkeypatch: pytest.MonkeyPatch):
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


def test_pg_phase6_0007_roundtrip_composite_fks_and_capa_constraints(live_pg_phase6) -> None:
    alembic_cfg = live_pg_phase6["alembic_cfg"]
    session_factory = live_pg_phase6["session_factory"]

    # 0007 -> 0006 -> head roundtrip
    command.downgrade(alembic_cfg, "0006_phase5_investigations")
    command.upgrade(alembic_cfg, "head")

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seed_rbac_catalog(uow.session)
        org_a = create_seeded_tenant_actor(
            uow.session,
            org_name="Phase6 PG Org A",
            site_code="P6A-01",
            email="qa.p6a@example.com",
            role_code="QMS_HEAD",
        )
        org_b = create_seeded_tenant_actor(
            uow.session,
            org_name="Phase6 PG Org B",
            site_code="P6B-01",
            email="qa.p6b@example.com",
            role_code="QMS_HEAD",
        )

        comp_a = Complaint(
            organization_id=org_a["organization"].id,
            site_id=org_a["site"].id,
            complaint_number="CMP-PG6-A1",
            source_channel=ComplaintSource.STAFF.value,
            status=ComplaintStatus.INVESTIGATION_OPEN.value,
            reported_product_name="TestProd",
            description="Org A complaint",
        )
        comp_b = Complaint(
            organization_id=org_b["organization"].id,
            site_id=org_b["site"].id,
            complaint_number="CMP-PG6-B1",
            source_channel=ComplaintSource.STAFF.value,
            status=ComplaintStatus.RECEIVED.value,
            reported_product_name="TestProdB",
            description="Org B complaint",
        )
        uow.session.add_all([comp_a, comp_b])
        uow.session.flush()

        inv_a = Investigation(
            organization_id=org_a["organization"].id,
            site_id=org_a["site"].id,
            complaint_id=comp_a.id,
            investigation_number="INV-PG6-A1",
            trigger_type="COMPLAINT",
            title="Live PG Inv A",
            status="APPROVED",
        )
        uow.session.add(inv_a)
        uow.session.flush()

        dev_a = Deviation(
            organization_id=org_a["organization"].id,
            site_id=org_a["site"].id,
            investigation_id=inv_a.id,
            deviation_number="DEV-PG6-A1",
            source_type="INVESTIGATION",
            classification="MAJOR",
            title="Live PG Dev A",
            description="Deviation under Inv A",
            status="OPEN",
        )
        uow.session.add(dev_a)
        uow.session.flush()

        org_a_id = org_a["organization"].id
        org_b_id = org_b["organization"].id
        mem_a_id = org_a["membership"].id
        comp_a_id = comp_a.id
        comp_b_id = comp_b.id
        dev_a_id = dev_a.id

    # Cross-tenant composite FK fk_capas_org_complaint must reject Org A CAPA -> Org B Complaint
    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(
                CAPA(
                    organization_id=org_a_id,
                    capa_number="CAPA-PG6-CROSS",
                    title="Cross-tenant complaint link",
                    complaint_id=comp_b_id,
                    owner_membership_id=mem_a_id,
                )
            )
            uow.session.flush()

    # DB check constraint ck_capas_approver_neq_owner must reject self-approval
    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(
                CAPA(
                    organization_id=org_a_id,
                    capa_number="CAPA-PG6-SELF",
                    title="Self-approved CAPA",
                    owner_membership_id=mem_a_id,
                    approver_membership_id=mem_a_id,
                    status=CAPAStatus.OPEN.value,
                )
            )
            uow.session.flush()

    # Create open mandatory CAPA on dev_a -> list_open_mandatory_capas_for_complaint returns it; close -> clears!
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        capa = CAPA(
            organization_id=org_a_id,
            capa_number="CAPA-PG6-0001",
            title="Mandatory CAPA",
            source_type="DEVIATION",
            deviation_id=dev_a_id,
            is_mandatory_for_complaint_closure=True,
            status=CAPAStatus.EFFECTIVENESS_REVIEW.value,
            owner_membership_id=mem_a_id,
        )
        uow.session.add(capa)
        uow.session.flush()
        capa_id = capa.id

        assert [c.id for c in QMSRepository().list_open_mandatory_capas_for_complaint(uow.session, org_a_id, comp_a_id)] == [capa_id]

        capa.status = CAPAStatus.CLOSED.value
        capa.closed_at = datetime.now(timezone.utc)
        uow.session.flush()

        assert QMSRepository().list_open_mandatory_capas_for_complaint(uow.session, org_a_id, comp_a_id) == []


def test_pg_phase6_email_processing_attempts_append_only_trigger(live_pg_phase6) -> None:
    session_factory = live_pg_phase6["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seed_rbac_catalog(uow.session)
        org = create_seeded_tenant_actor(
            uow.session,
            org_name="Phase6 Trigger Org",
            site_code="P6T-01",
            email="qa.p6t@example.com",
            role_code="QA_HEAD",
        )
        org_id = org["organization"].id
        conn_id = uuid.uuid4()
        email_id = uuid.uuid4()
        att_id = uuid.uuid4()

        uow.session.execute(
            text(
                "INSERT INTO email_connections (id, organization_id, name, provider_type, mailbox_address, is_active, auto_create_threshold, row_version) "
                "VALUES (:id, :org_id, 'Support', 'MOCK', 'qa@p6t.example.com', true, 0.7500, 1)"
            ),
            {"id": conn_id, "org_id": org_id},
        )
        uow.session.execute(
            text(
                "INSERT INTO inbound_emails (id, organization_id, email_connection_id, provider_message_id, from_email, triage_status, row_version) "
                "VALUES (:id, :org_id, :conn_id, 'msg-001', 'reporter@example.com', 'RECEIVED', 1)"
            ),
            {"id": email_id, "org_id": org_id, "conn_id": conn_id},
        )
        uow.session.execute(
            text(
                "INSERT INTO email_processing_attempts (id, organization_id, inbound_email_id, attempt_number, status) "
                "VALUES (:id, :org_id, :email_id, 1, 'COMPLETED')"
            ),
            {"id": att_id, "org_id": org_id, "email_id": email_id},
        )

    with pytest.raises((InternalError, ProgrammingError)):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.execute(
                text("UPDATE email_processing_attempts SET status = 'FAILED' WHERE id = :id"),
                {"id": att_id},
            )

    with pytest.raises((InternalError, ProgrammingError)):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.execute(
                text("DELETE FROM email_processing_attempts WHERE id = :id"),
                {"id": att_id},
            )
