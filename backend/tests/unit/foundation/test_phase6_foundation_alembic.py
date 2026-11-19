import ast
import contextlib
import io
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.database import UnitOfWork
from app.core.security import get_password_hash
from app.domain.enums import (
    CAPAActionStatus,
    CAPAActionType,
    CAPASourceType,
    CAPAType,
    CommunicationDeliveryStatus,
    CommunicationType,
    EffectivenessCheckStatus,
    EmailProviderType,
    InboundEmailClassification,
    InboundEmailTriageStatus,
    MembershipStatus,
    OrganizationStatus,
    RoleAssignmentScopeType,
    RoleAssignmentStatus,
    SiteStatus,
    SiteType,
    UserAccountStatus,
)
from app.domain.rbac_catalog import (
    PERMISSION_CODES,
    ROLE_PERMISSION_MAP,
    WORKER_PERMISSIONS,
    seed_rbac_catalog,
)
from app.models.domain.governance import Notification
from app.models.domain.identity import (
    MembershipRoleAssignment,
    OrganizationMembership,
    Role,
    User,
)
from app.models.domain.tenancy import Organization, OrganizationAddress, Site
from app.worker import (
    OutboxWorker,
    _handle_complaint_registered,
    build_worker_tenant_context,
    fan_out_complaint_registered_notifications,
)


BACKEND_DIR = Path(__file__).resolve().parents[3]


def test_alembic_0007_chain_pg_upgrade_and_downgrade_sql() -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option(
        "sqlalchemy.url",
        "postgresql+psycopg://user:pass@localhost:5432/ojasyn",
    )
    sd = ScriptDirectory.from_config(cfg)
    assert sd.get_heads() == ["0007_phase6_comm_email_capa"]

    revisions = list(sd.walk_revisions())
    assert [r.revision for r in revisions] == [
        "0007_phase6_comm_email_capa",
        "0006_phase5_investigations",
        "0005_phase4_mfg_evidence",
        "0004_phase3_traceability",
        "0003_phase2_docs_complaints",
        "0002_phase1_tenancy_actors",
        "0001_phase0_foundation",
    ]
    for rev in revisions:
        assert len(rev.revision) <= 32, f"Revision ID > 32 chars: {rev.revision}"

    up_buf = io.StringIO()
    with contextlib.redirect_stdout(up_buf):
        command.upgrade(cfg, "0006_phase5_investigations:0007_phase6_comm_email_capa", sql=True)
    up_sql = up_buf.getvalue()

    phase6_new_tables = (
        "email_connections",
        "inbound_emails",
        "email_attachments",
        "email_processing_attempts",
        "capas",
        "capa_actions",
        "capa_effectiveness_checks",
    )
    for tbl in phase6_new_tables:
        assert f"CREATE TABLE {tbl}" in up_sql

    b6_added_columns = (
        "communication_type",
        "template_code",
        "recipient_email",
        "recipient_name",
        "delivery_status",
        "provider_message_id",
        "delivered_at",
        "failed_reason",
        "attempt_count",
        "in_reply_to_communication_id",
        "metadata_json",
    )
    for col in b6_added_columns:
        assert f"ADD COLUMN {col}" in up_sql

    assert "uq_complaint_comms_org_id_id" in up_sql
    assert "ix_complaint_comms_org_comp_sent" in up_sql
    assert "uq_inbound_emails_org_msg_id" in up_sql
    assert "trg_prevent_mutation_email_processing_attempts" in up_sql
    assert "ON DELETE CASCADE" not in up_sql.upper()

    for ident in re.findall(r"(?:CONSTRAINT|INDEX|TRIGGER)\s+([a-zA-Z0-9_]+)", up_sql):
        assert len(ident) <= 63, f"Identifier > 63 chars: {ident}"

    down_buf = io.StringIO()
    with contextlib.redirect_stdout(down_buf):
        command.downgrade(cfg, "0007_phase6_comm_email_capa:0006_phase5_investigations", sql=True)
    down_sql = down_buf.getvalue()

    assert "DROP TRIGGER IF EXISTS trg_prevent_mutation_email_processing_attempts" in down_sql
    for tbl in phase6_new_tables:
        assert f"DROP TABLE {tbl}" in down_sql
    for col in b6_added_columns:
        assert f"DROP COLUMN {col}" in down_sql
    assert "DROP CONSTRAINT uq_complaint_comms_org_id_id" in down_sql


def test_phase6_enums_rbac_and_worker_hooks() -> None:
    assert {"GENERAL", "INFO_REQUEST", "REPORTER_REPLY"}.issubset({e.value for e in CommunicationType})
    assert {"NOT_APPLICABLE", "QUEUED", "SENT", "DELIVERED", "FAILED"}.issubset(
        {e.value for e in CommunicationDeliveryStatus}
    )
    assert {"IMAP", "SMTP", "WEBHOOK", "MOCK"} == {e.value for e in EmailProviderType}
    assert {"LIKELY_COMPLAINT", "NON_COMPLAINT", "UNCERTAIN"} == {
        e.value for e in InboundEmailClassification
    }
    assert {
        "RECEIVED",
        "CLASSIFIED",
        "COMPLAINT_CREATED",
        "MANUAL_TRIAGE_PENDING",
        "TRIAGED_COMPLAINT",
        "TRIAGED_NON_COMPLAINT",
        "DUPLICATE",
        "FAILED",
    } == {e.value for e in InboundEmailTriageStatus}
    assert {"CORRECTIVE", "PREVENTIVE", "BOTH"} == {e.value for e in CAPAType}
    assert {"COMPLAINT", "INVESTIGATION", "DEVIATION", "MANUAL"} == {e.value for e in CAPASourceType}
    assert {"CORRECTIVE", "PREVENTIVE", "CONTAINMENT"} == {e.value for e in CAPAActionType}
    assert {"PENDING", "IN_PROGRESS", "COMPLETED", "VERIFIED", "CANCELLED"} == {
        e.value for e in CAPAActionStatus
    }
    assert {"PENDING", "EFFECTIVE", "INEFFECTIVE", "INCONCLUSIVE"} == {
        e.value for e in EffectivenessCheckStatus
    }

    expected_perms = {
        "complaint.communicate",
        "email_intake.triage",
        "email_intake.admin",
        "email_intake.process",
        "capa.read",
        "capa.manage",
        "capa.approve",
        "capa.effectiveness_review",
        "capa.close",
    }
    assert expected_perms.issubset(PERMISSION_CODES)
    assert {"capa.read", "capa.manage", "capa.approve", "capa.effectiveness_review", "capa.close"}.issubset(
        ROLE_PERMISSION_MAP["QMS_HEAD"]
    )
    assert {"capa.read", "capa.manage", "capa.approve", "capa.effectiveness_review", "capa.close"}.issubset(
        ROLE_PERMISSION_MAP["QA_HEAD"]
    )
    assert "capa.close" not in ROLE_PERMISSION_MAP["QA_OFFICER"]
    assert "capa.approve" not in ROLE_PERMISSION_MAP["QA_OFFICER"]
    assert WORKER_PERMISSIONS.isdisjoint({"capa.approve", "capa.close", "capa.effectiveness_review", "complaint.close"})

    worker = OutboxWorker(session_factory=lambda: None)  # type: ignore[arg-type]
    for ext_ev in (
        "ComplaintRegistered",
        "OutboundCommunicationRequested",
        "InboundEmailReceived",
        "InboundEmailProcessRequested",
    ):
        assert ext_ev in worker._external_handlers

    for ev in (
        "CAPAOverdueCheckRequested",
        "CAPAOverdue",
        "ComplaintInformationRequested",
        "OutboundCommunicationSent",
        "InboundEmailTriaged",
        "CAPACreated",
        "CAPAStatusChanged",
        "CAPAActionCompleted",
        "CAPAEffectivenessRecorded",
        "CAPAClosed",
        "CAPAOverdueNotified",
    ):
        assert ev in worker._handlers


def test_complaint_registered_qa_notification_fanout_scoped_and_idempotent(
    sqlite_db: dict,
) -> None:
    session_factory = sqlite_db["session_factory"]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        db_session = uow.session
        seed_rbac_catalog(db_session)

        org_a = Organization(
            legal_name="Org A Pharma",
            organization_type="MANUFACTURER",
            business_email="orga@example.com",
            status=OrganizationStatus.ACTIVE.value,
        )
        org_b = Organization(
            legal_name="Org B Pharma",
            organization_type="MANUFACTURER",
            business_email="orgb@example.com",
            status=OrganizationStatus.ACTIVE.value,
        )
        db_session.add_all([org_a, org_b])
        db_session.flush()

        addr_a = OrganizationAddress(
            organization_id=org_a.id,
            address_type="REGISTERED",
            line_1="1 Pharma Way",
            city="Hyderabad",
            state_region="TS",
            postal_code="500001",
            country_code="IN",
        )
        db_session.add(addr_a)
        db_session.flush()

        site_a1 = Site(
            organization_id=org_a.id,
            site_code="SITE-A1",
            site_name="Plant A1",
            site_type=SiteType.MANUFACTURING.value,
            address_id=addr_a.id,
            status=SiteStatus.ACTIVE.value,
        )
        site_a2 = Site(
            organization_id=org_a.id,
            site_code="SITE-A2",
            site_name="Plant A2",
            site_type=SiteType.MANUFACTURING.value,
            address_id=addr_a.id,
            status=SiteStatus.ACTIVE.value,
        )
        db_session.add_all([site_a1, site_a2])
        db_session.flush()

        roles = {
            r.role_code: r
            for r in db_session.query(Role).all()
        }

        def _make_member(email: str, org_id: uuid.UUID, role_code: str, scope: str, site_id=None):
            u = User(
                email=email,
                full_name=email,
                account_status=UserAccountStatus.ACTIVE.value,
            )
            db_session.add(u)
            db_session.flush()
            m = OrganizationMembership(
                organization_id=org_id,
                user_id=u.id,
                membership_status=MembershipStatus.ACTIVE.value,
            )
            db_session.add(m)
            db_session.flush()
            db_session.add(
                MembershipRoleAssignment(
                    organization_id=org_id,
                    membership_id=m.id,
                    role_id=roles[role_code].id,
                    scope_type=scope,
                    site_id=site_id,
                    granted_by_user_id=u.id,
                    status=RoleAssignmentStatus.ACTIVE.value,
                )
            )
            db_session.flush()
            return m

        m_qms_org = _make_member("qms@a.test", org_a.id, "QMS_HEAD", RoleAssignmentScopeType.ORGANIZATION.value)
        m_qa_a1 = _make_member("qa1@a.test", org_a.id, "QA_OFFICER", RoleAssignmentScopeType.SITE.value, site_a1.id)
        m_qa_a2 = _make_member("qa2@a.test", org_a.id, "QA_OFFICER", RoleAssignmentScopeType.SITE.value, site_a2.id)
        m_prod_a1 = _make_member(
            "prod@a.test", org_a.id, "PRODUCTION_OPERATOR", RoleAssignmentScopeType.SITE.value, site_a1.id
        )
        m_qa_other_org = _make_member("qa@b.test", org_b.id, "QA_HEAD", RoleAssignmentScopeType.ORGANIZATION.value)

        complaint_id = uuid.uuid4()
        created_first = fan_out_complaint_registered_notifications(
            db_session,
            organization_id=org_a.id,
            complaint_id=complaint_id,
            site_id=site_a1.id,
            complaint_number="CMP-2026-0001",
            title="Blister seal defect",
        )
        assert created_first == 2

        # Idempotent on retry
        created_second = fan_out_complaint_registered_notifications(
            db_session,
            organization_id=org_a.id,
            complaint_id=complaint_id,
            site_id=site_a1.id,
            complaint_number="CMP-2026-0001",
            title="Blister seal defect",
        )
        assert created_second == 0

        notifs = db_session.query(Notification).filter(Notification.aggregate_id == complaint_id).all()
        recipient_ids = {n.recipient_membership_id for n in notifs}
        assert recipient_ids == {m_qms_org.id, m_qa_a1.id}
        assert m_qa_a2.id not in recipient_ids
        assert m_prod_a1.id not in recipient_ids
        assert m_qa_other_org.id not in recipient_ids


def test_identity_access_service_has_no_direct_db_commit() -> None:
    src = (BACKEND_DIR / "app" / "services" / "identity_access_service.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    direct_db_commits = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "commit"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "db"
        ):
            direct_db_commits.append(node.lineno)
    assert direct_db_commits == [], f"Found direct db.commit() at lines {direct_db_commits}"
    assert "def _unlock_expired_lockout_isolated" in src


def test_expired_lockout_unlocks_via_isolated_uow(sqlite_db: dict) -> None:
    from app.models.schemas.auth_onboarding import LoginRequest
    from app.services.identity_access_service import IdentityAccessService

    session_factory = sqlite_db["session_factory"]
    now = datetime.now(timezone.utc)
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        u = User(
            email="locked_expired@example.com",
            full_name="Locked Expired",
            password_hash=get_password_hash("StrongPass!123"),
            account_status=UserAccountStatus.LOCKED.value,
            email_verified_at=now - timedelta(days=1),
            failed_login_attempts=5,
            locked_until=now - timedelta(minutes=5),
        )
        uow.session.add(u)

    db = session_factory()
    try:
        resp = IdentityAccessService().login(
            db,
            LoginRequest(email="locked_expired@example.com", password="StrongPass!123"),
            session_factory=session_factory,
        )
        assert resp.access_token
        assert resp.user.account_status == UserAccountStatus.ACTIVE.value
    finally:
        db.close()


def test_handle_complaint_registered_uses_real_complaint_orm_row(
    sqlite_db: dict,
    monkeypatch,
) -> None:
    from app.models.domain.complaints import Complaint
    from app.models.domain.governance import OutboxEvent
    from app.services.complaint_service import ComplaintService

    session_factory = sqlite_db["session_factory"]
    triage_called: list[uuid.UUID] = []

    def _fake_triage(self, sf, event, ctx):
        triage_called.append(event.aggregate_id)

    monkeypatch.setattr(ComplaintService, "handle_outbox_async_triage", _fake_triage)

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        db_session = uow.session
        seed_rbac_catalog(db_session)

        org = Organization(
            legal_name="Real Complaint Org",
            organization_type="MANUFACTURER",
            business_email="realcomp@example.com",
            status=OrganizationStatus.ACTIVE.value,
        )
        db_session.add(org)
        db_session.flush()

        addr = OrganizationAddress(
            organization_id=org.id,
            address_type="REGISTERED",
            line_1="10 Quality Rd",
            city="Pune",
            state_region="MH",
            postal_code="411001",
            country_code="IN",
        )
        db_session.add(addr)
        db_session.flush()

        site = Site(
            organization_id=org.id,
            site_code="PUNE-1",
            site_name="Pune Plant",
            site_type=SiteType.MANUFACTURING.value,
            address_id=addr.id,
            status=SiteStatus.ACTIVE.value,
        )
        db_session.add(site)
        db_session.flush()

        qa_role = db_session.query(Role).filter(Role.role_code == "QA_HEAD").one()
        qa_user = User(
            email="qahead@example.com",
            full_name="QA Head",
            account_status=UserAccountStatus.ACTIVE.value,
        )
        db_session.add(qa_user)
        db_session.flush()
        qa_member = OrganizationMembership(
            organization_id=org.id,
            user_id=qa_user.id,
            membership_status=MembershipStatus.ACTIVE.value,
        )
        db_session.add(qa_member)
        db_session.flush()
        db_session.add(
            MembershipRoleAssignment(
                organization_id=org.id,
                membership_id=qa_member.id,
                role_id=qa_role.id,
                scope_type=RoleAssignmentScopeType.SITE.value,
                site_id=site.id,
                granted_by_user_id=qa_user.id,
                status=RoleAssignmentStatus.ACTIVE.value,
            )
        )

        complaint = Complaint(
            organization_id=org.id,
            site_id=site.id,
            complaint_number="CMP-REAL-0001",
            source_channel="WEBSITE",
            reported_product_name="Paracetamol 500mg",
            description="Blister foil torn on two strips in carton.",
        )
        db_session.add(complaint)
        db_session.flush()
        complaint_id = complaint.id
        org_id = org.id
        qa_member_id = qa_member.id

    ev = OutboxEvent(
        id=uuid.uuid4(),
        organization_id=org_id,
        event_type="ComplaintRegistered",
        aggregate_type="complaints",
        aggregate_id=complaint_id,
        payload_json={},
    )
    ctx = build_worker_tenant_context(ev)
    _handle_complaint_registered(session_factory, ev, ctx)

    assert triage_called == [complaint_id]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        notifs = uow.session.query(Notification).filter(Notification.aggregate_id == complaint_id).all()
        assert len(notifs) == 1
        assert notifs[0].recipient_membership_id == qa_member_id
        assert "Paracetamol 500mg" in notifs[0].body
        assert "Blister foil torn" in notifs[0].body


