import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.database import UnitOfWork
from app.core.security import (
    create_access_token,
    issue_refresh_token,
    rotate_refresh_token,
)
from app.domain.enums import RoleAssignmentScopeType
from app.models.domain import AuditEvent, SecurityEvent, UserCredential
from app.repositories.base import apply_tenant_filter
from app.services.notification_service import NotificationService
from tests.conftest import create_seeded_tenant_actor


def test_refresh_token_rotation_and_reuse_revokes_family_and_logs_security_event(sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seeded = create_seeded_tenant_actor(
            uow.session,
            org_name="Refresh Pharma",
            site_code="REF-01",
            email="refresh@example.com",
            role_code="QA_OFFICER",
        )
        user = seeded["user"]
        raw_t1, cred1 = issue_refresh_token(uow.session, user_id=user.id)
        family_id = cred1.token_family_id

    # Normal rotation issues t2 and revokes t1
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        raw_t2, cred2 = rotate_refresh_token(
            uow.session,
            raw_t1,
            session_factory=session_factory,
        )
        assert raw_t2 != raw_t1
        assert cred2.token_family_id == family_id

    # Replaying t1 triggers reuse detection, revokes entire family (including t2), and logs CRITICAL SecurityEvent
    with pytest.raises(Exception, match="reuse"):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            rotate_refresh_token(uow.session, raw_t1, session_factory=session_factory)

    check = session_factory()
    try:
        creds = check.scalars(
            select(UserCredential).where(UserCredential.token_family_id == family_id)
        ).all()
        assert len(creds) == 2
        assert all(c.revoked_at is not None for c in creds)

        sec_ev = check.scalar(
            select(SecurityEvent).where(SecurityEvent.event_type == "REFRESH_TOKEN_REUSE_DETECTED")
        )
        assert sec_ev is not None
        assert sec_ev.severity == "CRITICAL"
    finally:
        check.close()


def test_jwt_expiration_and_token_valid_after_invalidation(api_client, sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seeded = create_seeded_tenant_actor(
            uow.session,
            org_name="Auth Pharma",
            site_code="AUT-01",
            email="auth.user@example.com",
            role_code="QA_OFFICER",
        )
        user = seeded["user"]
        user_id = user.id

    # 1. Expired JWT -> 401
    expired_token = create_access_token(
        subject=str(user_id),
        expires_delta=timedelta(seconds=-10),
    )
    resp_exp = api_client.get(
        "/api/v1/notifications/unread-count",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert resp_exp.status_code == 401

    # 2. Valid JWT succeeds
    valid_token = create_access_token(subject=str(user_id))
    resp_ok = api_client.get(
        "/api/v1/notifications/unread-count",
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert resp_ok.status_code == 200

    # 3. Advancing token_valid_after invalidates existing tokens immediately -> 401
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        db_user = uow.session.get(type(user), user_id)
        assert db_user is not None
        db_user.token_valid_after = datetime.now(timezone.utc) + timedelta(seconds=5)

    resp_invalidated = api_client.get(
        "/api/v1/notifications/unread-count",
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert resp_invalidated.status_code == 401


def test_rbac_permission_and_cross_tenant_isolation(api_client, sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        org_a_qa = create_seeded_tenant_actor(
            uow.session,
            org_name="Alpha Pharma",
            site_code="ALP-01",
            email="qa@alpha.example.com",
            role_code="QA_OFFICER",
            scope_type=RoleAssignmentScopeType.SITE.value,
        )
        org_b_auditor = create_seeded_tenant_actor(
            uow.session,
            org_name="Beta Pharma",
            site_code="BET-01",
            email="auditor@beta.example.com",
            role_code="AUDITOR_READ_ONLY",
            scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
        )
        org_c_qa = create_seeded_tenant_actor(
            uow.session,
            org_name="Gamma Pharma",
            site_code="GAM-01",
            email="qa@gamma.example.com",
            role_code="QA_OFFICER",
            scope_type=RoleAssignmentScopeType.SITE.value,
        )

        # Create a notification in Org A for org_a_qa
        notif_a = NotificationService().create_in_tx(
            uow.session,
            organization_id=org_a_qa["organization"].id,
            recipient_membership_id=org_a_qa["membership"].id,
            event_type="ComplaintRegistered",
            aggregate_type="complaint",
            aggregate_id=uuid.uuid4(),
            title="Alpha Alert",
            body="Alpha only",
        )
        org_a_id = org_a_qa["organization"].id
        org_b_id = org_b_auditor["organization"].id
        org_c_id = org_c_qa["organization"].id
        qa_user_id = org_a_qa["user"].id
        auditor_user_id = org_b_auditor["user"].id
        gamma_qa_user_id = org_c_qa["user"].id
        notif_a_id = notif_a.id

    qa_token = create_access_token(subject=str(qa_user_id))
    auditor_token = create_access_token(subject=str(auditor_user_id))
    gamma_qa_token = create_access_token(subject=str(gamma_qa_user_id))

    # 1. QA_OFFICER does not hold `audit.read` -> 403 + SecurityEvent recorded out-of-band
    r_forbidden = api_client.get(
        "/api/v1/audit-events",
        headers={"Authorization": f"Bearer {qa_token}"},
    )
    assert r_forbidden.status_code == 403

    # 2. Beta Auditor trying to switch X-Organization-Id to Alpha -> 403 + SecurityEvent
    r_cross_tenant = api_client.get(
        "/api/v1/audit-events",
        headers={
            "Authorization": f"Bearer {auditor_token}",
            "X-Organization-Id": str(org_a_id),
        },
    )
    assert r_cross_tenant.status_code == 403

    # 3. Gamma QA_OFFICER (holds notification.read in Gamma) trying to mark Alpha's notification as read -> 404 (no tenant leakage)
    r_cross_notif = api_client.patch(
        f"/api/v1/notifications/{notif_a_id}/read",
        headers={
            "Authorization": f"Bearer {gamma_qa_token}",
            "X-Organization-Id": str(org_c_id),
        },
    )
    assert r_cross_notif.status_code == 404

    # 4. Beta Auditor reading Beta's own audit-events -> 200 and sees zero Alpha events
    r_beta_ok = api_client.get(
        "/api/v1/audit-events",
        headers={
            "Authorization": f"Bearer {auditor_token}",
            "X-Organization-Id": str(org_b_id),
        },
    )
    assert r_beta_ok.status_code == 200
    assert all(item["organization_id"] == str(org_b_id) for item in r_beta_ok.json()["items"])

    # 5. Verify SecurityEvents were persisted out-of-band for both 403s
    db = session_factory()
    try:
        sec_events = db.scalars(select(SecurityEvent)).all()
        event_types = {e.event_type for e in sec_events}
        assert "RBAC_PERMISSION_DENIED" in event_types
        assert "TENANT_MEMBERSHIP_DENIED" in event_types
    finally:
        db.close()


def test_apply_tenant_filter_enforces_site_isolation(sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]
    from app.core.deps import TenantContext
    from app.models.domain.tenancy import Department

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        org = create_seeded_tenant_actor(
            uow.session,
            org_name="Site Scope Pharma",
            site_code="SITE-1",
            email="site1@example.com",
            role_code="QA_OFFICER",
        )
        org_obj = org["organization"]
        site1 = org["site"]
        site2 = create_seeded_tenant_actor(
            uow.session,
            org_name="Other Org",
            site_code="OTH-1",
            email="other@example.com",
            role_code="QA_OFFICER",
        )
        # Create second site inside org_obj
        from app.domain.enums import SiteStatus, SiteType
        from app.models.domain import OrganizationAddress, Site

        addr = uow.session.scalar(
            select(OrganizationAddress).where(OrganizationAddress.organization_id == org_obj.id)
        )
        site_b = Site(
            organization_id=org_obj.id,
            site_code="SITE-2",
            site_name="Site 2",
            site_type=SiteType.QC_LAB.value,
            address_id=addr.id,
            status=SiteStatus.ACTIVE.value,
        )
        uow.session.add(site_b)
        uow.session.flush()

        d1 = Department(
            organization_id=org_obj.id,
            site_id=site1.id,
            department_code="QA",
            department_name="Site 1 QA",
        )
        d2 = Department(
            organization_id=org_obj.id,
            site_id=site_b.id,
            department_code="QC",
            department_name="Site 2 QC",
        )
        uow.session.add_all([d1, d2])

    db = session_factory()
    try:
        ctx_site1_only = TenantContext(
            user_id=org["user"].id,
            organization_id=org_obj.id,
            membership_id=org["membership"].id,
            allowed_site_ids=frozenset({site1.id}),
            is_org_wide=False,
            permission_codes=frozenset({"site.read"}),
        )
        stmt = apply_tenant_filter(select(Department), Department, ctx_site1_only, allow_org_wide_site_null=False)
        rows = db.scalars(stmt).all()
        assert [r.department_code for r in rows] == ["QA"]
    finally:
        db.close()
