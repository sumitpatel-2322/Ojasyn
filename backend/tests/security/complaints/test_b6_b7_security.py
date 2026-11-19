import base64
import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import UnitOfWork
from app.core.deps import reset_rate_limiter
from app.core.security import create_access_token, get_password_hash
from app.domain.enums import (
    AssignmentStatus,
    AssignmentType,
    MembershipStatus,
    MemberType,
    RoleAssignmentScopeType,
    RoleAssignmentStatus,
    SiteStatus,
    SiteType,
    UserAccountStatus,
)
from app.integrations.email_provider import hash_webhook_secret
from app.models.domain.complaints import Complaint, EmailConnection, InboundEmail
from app.models.domain.governance import SecurityEvent
from app.models.domain.identity import (
    MembershipRoleAssignment,
    OrganizationMembership,
    Role,
    SiteAssignment,
    User,
)
from app.models.domain.tenancy import OrganizationAddress, Site
from tests.conftest import create_seeded_tenant_actor


@pytest.fixture(autouse=True)
def _clean_limits():
    reset_rate_limiter()
    yield
    reset_rate_limiter()


def test_b6_b7_cross_tenant_site_scope_rbac_and_webhook_security(
    api_client, sqlite_db, tmp_path, monkeypatch
) -> None:
    vault_dir = tmp_path / "sec_vault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(vault_dir))
    get_settings.cache_clear()

    session_factory = sqlite_db["session_factory"]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        org_a_admin = create_seeded_tenant_actor(
            uow.session,
            org_name="Alpha Sec Pharma",
            site_code="ASP-01",
            email="admin.a@alphasec.in",
            role_code="ORG_ADMIN",
            scope_type="ORGANIZATION",
        )
        org_b_admin = create_seeded_tenant_actor(
            uow.session,
            org_name="Beta Sec Pharma",
            site_code="BSP-01",
            email="admin.b@betasec.in",
            role_code="ORG_ADMIN",
            scope_type="ORGANIZATION",
        )
        org_a_id = org_a_admin["organization"].id
        site_a1_id = org_a_admin["site"].id
        org_b_id = org_b_admin["organization"].id

        addr_a = OrganizationAddress(
            organization_id=org_a_id,
            line_1="Plot 2",
            city="Pune",
            postal_code="411001",
            country_code="IN",
            is_primary=False,
        )
        uow.session.add(addr_a)
        uow.session.flush()
        site_a2 = Site(
            organization_id=org_a_id,
            site_code="ASP-02",
            site_name="Alpha Site 2",
            site_type=SiteType.FDF.value,
            address_id=addr_a.id,
            status=SiteStatus.ACTIVE.value,
        )
        uow.session.add(site_a2)
        uow.session.flush()
        site_a2_id = site_a2.id

        # Site-scoped QA Officer restricted to Site A1 (NOT Site A2)
        qa_a1_user = User(
            email="qa.a1@alphasec.in",
            full_name="QA Site A1",
            password_hash=get_password_hash("StrongPass!123"),
            account_status=UserAccountStatus.ACTIVE.value,
        )
        uow.session.add(qa_a1_user)
        uow.session.flush()
        qa_a1_m = OrganizationMembership(
            organization_id=org_a_id,
            user_id=qa_a1_user.id,
            member_type=MemberType.EMPLOYEE.value,
            membership_status=MembershipStatus.ACTIVE.value,
        )
        uow.session.add(qa_a1_m)
        uow.session.flush()
        uow.session.add(
            SiteAssignment(
                organization_id=org_a_id,
                membership_id=qa_a1_m.id,
                site_id=site_a1_id,
                assignment_type=AssignmentType.PRIMARY.value,
                status=AssignmentStatus.ACTIVE.value,
                assigned_by_user_id=qa_a1_user.id,
            )
        )
        qa_role = uow.session.query(Role).filter(Role.role_code == "QA_OFFICER").one()
        uow.session.add(
            MembershipRoleAssignment(
                organization_id=org_a_id,
                membership_id=qa_a1_m.id,
                role_id=qa_role.id,
                scope_type=RoleAssignmentScopeType.SITE.value,
                site_id=site_a1_id,
                granted_by_user_id=qa_a1_user.id,
                status=RoleAssignmentStatus.ACTIVE.value,
            )
        )

        # Operator without email_intake / complaint.communicate permissions
        op_user = User(
            email="op.a1@alphasec.in",
            full_name="Operator A1",
            password_hash=get_password_hash("StrongPass!123"),
            account_status=UserAccountStatus.ACTIVE.value,
        )
        uow.session.add(op_user)
        uow.session.flush()
        op_m = OrganizationMembership(
            organization_id=org_a_id,
            user_id=op_user.id,
            member_type=MemberType.EMPLOYEE.value,
            membership_status=MembershipStatus.ACTIVE.value,
        )
        uow.session.add(op_m)
        uow.session.flush()
        uow.session.add(
            SiteAssignment(
                organization_id=org_a_id,
                membership_id=op_m.id,
                site_id=site_a1_id,
                assignment_type=AssignmentType.PRIMARY.value,
                status=AssignmentStatus.ACTIVE.value,
                assigned_by_user_id=op_user.id,
            )
        )
        op_role = uow.session.query(Role).filter(Role.role_code == "PRODUCTION_OPERATOR").one()
        uow.session.add(
            MembershipRoleAssignment(
                organization_id=org_a_id,
                membership_id=op_m.id,
                role_id=op_role.id,
                scope_type=RoleAssignmentScopeType.SITE.value,
                site_id=site_a1_id,
                granted_by_user_id=op_user.id,
                status=RoleAssignmentStatus.ACTIVE.value,
            )
        )

        # Org A Site A2 complaint + EmailConnection + InboundEmail
        comp_a2 = Complaint(
            organization_id=org_a_id,
            site_id=site_a2_id,
            complaint_number="ASP-CMP-2026-000001",
            description="Site A2 complaint.",
            status="RECEIVED",
        )
        uow.session.add(comp_a2)
        conn_a2 = EmailConnection(
            organization_id=org_a_id,
            default_site_id=site_a2_id,
            name="Site A2 Mailbox",
            provider_type="WEBHOOK",
            mailbox_address="site2@alphasec.in",
            inbound_secret_hash=hash_webhook_secret("valid-secret-a2"),
            is_active=True,
        )
        uow.session.add(conn_a2)
        uow.session.flush()
        inbound_a2 = InboundEmail(
            organization_id=org_a_id,
            email_connection_id=conn_a2.id,
            provider_message_id="sec-msg-a2",
            from_email="caller@example.com",
            subject="Site A2 issue",
            body_text="Issue at site A2",
            triage_status="MANUAL_TRIAGE_PENDING",
        )
        uow.session.add(inbound_a2)
        uow.session.flush()

        admin_a_id = org_a_admin["user"].id
        admin_b_id = org_b_admin["user"].id
        qa_a1_id = qa_a1_user.id
        op_id = op_user.id
        comp_a2_id = comp_a2.id
        conn_a2_id = conn_a2.id
        inbound_a2_id = inbound_a2.id

    admin_a_hdrs = {
        "Authorization": f"Bearer {create_access_token(subject=str(admin_a_id))}",
        "X-Organization-Id": str(org_a_id),
    }
    admin_b_hdrs = {
        "Authorization": f"Bearer {create_access_token(subject=str(admin_b_id))}",
        "X-Organization-Id": str(org_b_id),
    }
    qa_a1_hdrs = {
        "Authorization": f"Bearer {create_access_token(subject=str(qa_a1_id))}",
        "X-Organization-Id": str(org_a_id),
    }
    op_hdrs = {
        "Authorization": f"Bearer {create_access_token(subject=str(op_id))}",
        "X-Organization-Id": str(org_a_id),
    }

    # 1. Cross-tenant 404 on B6 & B7 resources
    assert api_client.get(f"/api/v1/complaints/{comp_a2_id}/communications", headers=admin_b_hdrs).status_code == 403  # ORG_ADMIN lacks complaint.read
    # Grant Org B QA actor to check cross-tenant 404
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        org_b_qa = create_seeded_tenant_actor(
            uow.session,
            org_name="Gamma Sec Pharma",
            site_code="GSP-01",
            email="qa@gammasec.in",
            role_code="QA_HEAD",
            scope_type="ORGANIZATION",
        )
        org_b_qa_hdrs = {
            "Authorization": f"Bearer {create_access_token(subject=str(org_b_qa['user'].id))}",
            "X-Organization-Id": str(org_b_qa["organization"].id),
        }
    assert api_client.get(f"/api/v1/complaints/{comp_a2_id}/communications", headers=org_b_qa_hdrs).status_code == 404
    assert api_client.post(
        f"/api/v1/complaints/{comp_a2_id}/acknowledgements",
        headers=org_b_qa_hdrs,
        json={"channel": "PORTAL", "body_text": "Probe"},
    ).status_code == 404
    assert api_client.get(f"/api/v1/email-intake/connections/{conn_a2_id}", headers=admin_b_hdrs).status_code == 404
    assert api_client.get(f"/api/v1/email-intake/inbound/{inbound_a2_id}", headers=org_b_qa_hdrs).status_code == 404
    assert api_client.post(
        f"/api/v1/email-intake/inbound/{inbound_a2_id}/triage",
        headers=org_b_qa_hdrs,
        json={"row_version": 1, "decision": "MARK_NON_COMPLAINT"},
    ).status_code == 404

    # 2. Same-tenant out-of-scope site -> 403 SITE_ACCESS_DENIED on B7 inbound/triage
    assert api_client.get(f"/api/v1/email-intake/inbound/{inbound_a2_id}", headers=qa_a1_hdrs).status_code == 403
    assert api_client.post(
        f"/api/v1/email-intake/inbound/{inbound_a2_id}/triage",
        headers=qa_a1_hdrs,
        json={"row_version": 1, "decision": "MARK_NON_COMPLAINT"},
    ).status_code == 403

    # 3. RBAC enforcement -> 403
    assert api_client.post(
        "/api/v1/email-intake/connections",
        headers=op_hdrs,
        json={"name": "X", "mailbox_address": "x@alphasec.in"},
    ).status_code == 403
    assert api_client.get("/api/v1/email-intake/triage-queue", headers=op_hdrs).status_code == 403
    assert api_client.post(
        f"/api/v1/complaints/{comp_a2_id}/acknowledgements",
        headers=op_hdrs,
        json={"channel": "PORTAL", "body_text": "No perm"},
    ).status_code == 403

    # 4. Invalid/missing X-Webhook-Secret -> 401 + SecurityEvent logged out-of-band
    bad_wh = api_client.post(
        f"/api/v1/email-intake/webhooks/{conn_a2_id}",
        headers={"X-Webhook-Secret": "forged-secret"},
        json={
            "provider_message_id": "bad-wh-1",
            "from_email": "attacker@example.com",
            "body_text": "Spoof attempt",
        },
    )
    assert bad_wh.status_code == 401

    unknown_wh = api_client.post(
        f"/api/v1/email-intake/webhooks/{uuid.uuid4()}",
        headers={"X-Webhook-Secret": "anything"},
        json={
            "provider_message_id": "bad-wh-2",
            "from_email": "attacker@example.com",
            "body_text": "Unknown conn",
        },
    )
    assert unknown_wh.status_code == 401

    # 5. Disallowed executable attachment rejected (422) and leaves zero orphan objects/emails
    mz_b64 = base64.b64encode(b"MZ\x90\x00\x03\x00\x00\x00executable-payload").decode("ascii")
    bad_att_wh = api_client.post(
        f"/api/v1/email-intake/webhooks/{conn_a2_id}",
        headers={"X-Webhook-Secret": "valid-secret-a2"},
        json={
            "provider_message_id": "bad-att-msg-1",
            "from_email": "sender@example.com",
            "body_text": "Malicious attachment",
            "attachments": [
                {
                    "original_filename": "payload.exe",
                    "mime_type": "application/octet-stream",
                    "content_base64": mz_b64,
                }
            ],
        },
    )
    assert bad_att_wh.status_code == 422
    assert list(vault_dir.rglob("*")) == [] or all(p.is_dir() for p in vault_dir.rglob("*"))

    # 6. Inactive EmailConnection -> 403 EMAIL_CONNECTION_INACTIVE
    assert api_client.patch(
        f"/api/v1/email-intake/connections/{conn_a2_id}",
        headers=admin_a_hdrs,
        json={"row_version": 1, "is_active": False},
    ).status_code == 200
    inactive_wh = api_client.post(
        f"/api/v1/email-intake/webhooks/{conn_a2_id}",
        headers={"X-Webhook-Secret": "valid-secret-a2"},
        json={
            "provider_message_id": "inactive-msg-1",
            "from_email": "sender@example.com",
            "body_text": "Connection is paused",
        },
    )
    assert inactive_wh.status_code == 403

    db = session_factory()
    try:
        sec_events = db.scalars(
            select(SecurityEvent.event_type).where(
                SecurityEvent.event_type.in_(
                    [
                        "EMAIL_WEBHOOK_INVALID_SECRET",
                        "EMAIL_WEBHOOK_UNKNOWN_CONNECTION",
                        "EMAIL_WEBHOOK_INACTIVE_TARGET",
                    ]
                )
            )
        ).all()
        assert set(sec_events) == {
            "EMAIL_WEBHOOK_INVALID_SECRET",
            "EMAIL_WEBHOOK_UNKNOWN_CONNECTION",
            "EMAIL_WEBHOOK_INACTIVE_TARGET",
        }
    finally:
        db.close()


def test_email_webhook_rate_limit_enforced(api_client, sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        actor = create_seeded_tenant_actor(
            uow.session,
            org_name="Rate Limit Pharma",
            site_code="RLP-01",
            email="admin@ratelimit.in",
            role_code="ORG_ADMIN",
        )
        conn = EmailConnection(
            organization_id=actor["organization"].id,
            name="Rate Limit Box",
            provider_type="WEBHOOK",
            mailbox_address="rl@ratelimit.in",
            inbound_secret_hash=hash_webhook_secret("rl-secret"),
            is_active=True,
        )
        uow.session.add(conn)
        uow.session.flush()
        conn_id = conn.id

    statuses = []
    for idx in range(62):
        resp = api_client.post(
            f"/api/v1/email-intake/webhooks/{conn_id}",
            headers={"X-Webhook-Secret": "rl-secret"},
            json={
                "provider_message_id": f"rl-msg-{idx}",
                "from_email": "user@example.com",
                "body_text": "Rate limit check",
            },
        )
        statuses.append(resp.status_code)
    assert 429 in statuses
