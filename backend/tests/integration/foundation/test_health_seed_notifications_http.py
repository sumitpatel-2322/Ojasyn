import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.database import UnitOfWork
from app.core.security import create_access_token
from app.domain.enums import AssignmentStatus, AssignmentType, RoleAssignmentScopeType, RoleAssignmentStatus
from app.models.domain import MembershipRoleAssignment, Role, SiteAssignment
from app.services.notification_service import NotificationService
from init_db import seed_foundation
from reset_db import reset_database
from tests.conftest import create_seeded_tenant_actor


def test_health_correlation_id_and_notifications_http_lifecycle(api_client, sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]

    live = api_client.get("/health/live", headers={"X-Correlation-ID": "corr-live-1"})
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert live.headers["X-Correlation-ID"] == "corr-live-1"

    ready = api_client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}

    # ErrorEnvelope check on unauthenticated request
    unauth = api_client.get("/api/v1/notifications")
    assert unauth.status_code == 401
    err_body = unauth.json()
    assert err_body["error_code"] == "AUTHENTICATION_FAILED"
    assert "correlation_id" in err_body

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        actor = create_seeded_tenant_actor(
            uow.session,
            org_name="HTTP Notif Pharma",
            site_code="HN-01",
            email="hn.qa@example.com",
            role_code="QA_OFFICER",
        )
        n1 = NotificationService().create_in_tx(
            uow.session,
            organization_id=actor["organization"].id,
            recipient_membership_id=actor["membership"].id,
            event_type="ComplaintRegistered",
            aggregate_type="complaint",
            aggregate_id=uuid.uuid4(),
            title="HTTP Alert 1",
            body="First HTTP alert",
        )
        n2 = NotificationService().create_in_tx(
            uow.session,
            organization_id=actor["organization"].id,
            recipient_membership_id=actor["membership"].id,
            event_type="ComplaintRegistered",
            aggregate_type="complaint",
            aggregate_id=uuid.uuid4(),
            title="HTTP Alert 2",
            body="Second HTTP alert",
        )
        user_id = actor["user"].id
        n1_id, n1_seq, n2_seq = n1.id, n1.delivery_seq, n2.delivery_seq

    token = create_access_token(subject=str(user_id))
    headers = {"Authorization": f"Bearer {token}"}

    # Unread count == 2
    assert api_client.get("/api/v1/notifications/unread-count", headers=headers).json() == {"unread_count": 2}

    # List notifications
    listed = api_client.get("/api/v1/notifications", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 2

    # Mark n1 read -> unread_count == 1
    marked = api_client.patch(f"/api/v1/notifications/{n1_id}/read", headers=headers)
    assert marked.status_code == 200
    assert marked.json()["read_at"] is not None
    assert api_client.get("/api/v1/notifications/unread-count", headers=headers).json() == {"unread_count": 1}

    # HTTP SSE stream replay with Last-Event-ID = n1_seq -> receives n2 only
    stream_resp = api_client.get(
        "/api/v1/notifications/stream?max_events=1",
        headers={**headers, "Last-Event-ID": str(n1_seq)},
    )
    assert stream_resp.status_code == 200
    assert f"id: {n2_seq}" in stream_resp.text
    assert "HTTP Alert 2" in stream_resp.text


def test_init_db_idempotent_and_reset_db_production_guard(sqlite_db, monkeypatch: pytest.MonkeyPatch) -> None:
    # seed_foundation runs cleanly twice without duplicate errors
    seed_foundation()
    seed_foundation()

    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="production"):
        reset_database()


def test_composite_fk_blocks_cross_tenant_site_assignment_and_scope_check(sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        org_a = create_seeded_tenant_actor(
            uow.session,
            org_name="FK Org A",
            site_code="FKA-01",
            email="fka@example.com",
            role_code="QA_OFFICER",
        )
        org_b = create_seeded_tenant_actor(
            uow.session,
            org_name="FK Org B",
            site_code="FKB-01",
            email="fkb@example.com",
            role_code="QA_OFFICER",
        )
        org_a_id = org_a["organization"].id
        org_a_member_id = org_a["membership"].id
        org_a_user_id = org_a["user"].id
        org_b_site_id = org_b["site"].id
        role_id = uow.session.scalar(select(Role.id).where(Role.role_code == "QA_OFFICER"))

    # 1. Cross-tenant SiteAssignment (Org A membership linked to Org B site) MUST fail FK constraint
    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(
                SiteAssignment(
                    organization_id=org_a_id,
                    membership_id=org_a_member_id,
                    site_id=org_b_site_id,
                    assignment_type=AssignmentType.SECONDARY.value,
                    status=AssignmentStatus.ACTIVE.value,
                    assigned_by_user_id=org_a_user_id,
                )
            )

    # 2. MembershipRoleAssignment CHECK constraint blocks SITE scope without site_id
    with pytest.raises(IntegrityError):
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(
                MembershipRoleAssignment(
                    organization_id=org_a_id,
                    membership_id=org_a_member_id,
                    role_id=role_id,
                    scope_type=RoleAssignmentScopeType.SITE.value,
                    site_id=None,
                    granted_by_user_id=org_a_user_id,
                    status=RoleAssignmentStatus.ACTIVE.value,
                )
            )
