import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select

from app.core.database import UnitOfWork
from app.core.deps import reset_rate_limiter
from app.core.security import create_access_token, get_password_hash
from app.domain.enums import UserAccountStatus
from app.models.domain.governance import AuditEvent, OutboxEvent, SecurityEvent
from app.models.domain.identity import Invitation, User
from tests.conftest import create_seeded_tenant_actor


@pytest.fixture(autouse=True)
def _clean_rate_limits():
    reset_rate_limiter()
    yield
    reset_rate_limiter()


def _create_verified_user(session_factory, *, email: str, full_name: str, password: str = "Pass!123456"):
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        u = User(
            email=email.lower(),
            password_hash=get_password_hash(password),
            full_name=full_name,
            account_status=UserAccountStatus.ACTIVE.value,
            email_verified_at=datetime.now(timezone.utc),
        )
        uow.session.add(u)
        uow.session.flush()
        return u.id


def test_account_lockout_and_login_rate_limit(api_client, sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]
    uid = _create_verified_user(session_factory, email="lockme@example.com", full_name="Lock User")

    for _ in range(5):
        resp = api_client.post(
            "/api/v1/auth/login",
            json={"email": "lockme@example.com", "password": "WrongPassword!"},
        )
        assert resp.status_code == 401

    # 6th attempt even with RIGHT password is blocked!
    blocked = api_client.post(
        "/api/v1/auth/login",
        json={"email": "lockme@example.com", "password": "Pass!123456"},
    )
    assert blocked.status_code == 401

    db = session_factory()
    try:
        user = db.get(User, uid)
        assert user is not None
        assert user.account_status == "LOCKED"
        assert user.locked_until is not None
        assert db.scalars(select(SecurityEvent).where(SecurityEvent.event_type == "ACCOUNT_LOCKED")).first() is not None
    finally:
        db.close()

    # Rate limit triggers 429 cleanly after 20 requests in window
    for _ in range(25):
        r = api_client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "x"},
        )
        if r.status_code == 429:
            break
    else:
        pytest.fail("Expected 429 RATE_LIMIT_EXCEEDED after >20 login requests")


def test_invitation_security_two_phase_qms_bootstrap_maker_checker_and_isolation(api_client, sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        org_a_owner = create_seeded_tenant_actor(
            uow.session,
            org_name="CleanQMS Pharma",
            site_code="CQ-01",
            email="owner@cleanqms.in",
            role_code="ORG_OWNER",
        )
        org_b_owner = create_seeded_tenant_actor(
            uow.session,
            org_name="OtherTenant Pharma",
            site_code="OT-01",
            email="owner@othertenant.in",
            role_code="ORG_OWNER",
        )
        org_a_id = org_a_owner["organization"].id
        site_a_id = org_a_owner["site"].id
        owner_a_id = org_a_owner["user"].id
        owner_b_id = org_b_owner["user"].id
        owner_a_membership_id = org_a_owner["membership"].id

    owner_a_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(owner_a_id))}",
        "X-Organization-Id": str(org_a_id),
    }
    owner_b_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(owner_b_id))}",
        "X-Organization-Id": str(org_b_owner["organization"].id),
    }

    # PLATFORM_SUPER_ADMIN cannot be invited as a tenant membership
    bad_role_inv = api_client.post(
        f"/api/v1/organizations/{org_a_id}/invitations",
        headers=owner_a_headers,
        json={
            "email": "evil@cleanqms.in",
            "site_ids": [str(site_a_id)],
            "requested_role_codes": ["PLATFORM_SUPER_ADMIN"],
        },
    )
    assert bad_role_inv.status_code == 403

    # SITE-only role (QA_OFFICER) MUST reject empty site_ids on invitation creation (422)
    site_only_no_site = api_client.post(
        f"/api/v1/organizations/{org_a_id}/invitations",
        headers=owner_a_headers,
        json={
            "email": "qa.nosite@cleanqms.in",
            "site_ids": [],
            "requested_role_codes": ["QA_OFFICER"],
        },
    )
    assert site_only_no_site.status_code == 422

    # Invite QA_HEAD BEFORE QMS_HEAD exists -> allowed to invite/accept, but ORG_OWNER CANNOT approve QA_HEAD!
    qa_uid = _create_verified_user(session_factory, email="qahead@cleanqms.in", full_name="QA Candidate")
    wrong_uid = _create_verified_user(session_factory, email="wrong@cleanqms.in", full_name="Wrong User")

    inv_qa = api_client.post(
        f"/api/v1/organizations/{org_a_id}/invitations",
        headers=owner_a_headers,
        json={
            "email": "qahead@cleanqms.in",
            "site_ids": [str(site_a_id)],
            "requested_role_codes": ["QA_HEAD"],
        },
    )
    assert inv_qa.status_code == 201
    qa_raw_token = inv_qa.json()["invite_token"]

    # Raw token is NOT in Invitation DB row or OutboxEvent
    db = session_factory()
    try:
        inv_row = db.get(Invitation, uuid.UUID(inv_qa.json()["id"]))
        assert inv_row is not None and inv_row.token_hash != qa_raw_token
        ev = db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == inv_row.id)).first()
        assert ev is not None and qa_raw_token not in str(ev.payload_json)
    finally:
        db.close()

    # Public preview returns minimal info & does NOT consume token
    preview = api_client.get(f"/api/v1/invitations/{qa_raw_token}")
    assert preview.status_code == 200
    assert preview.json()["organization_legal_name"] == "CleanQMS Pharma"
    assert "qahead" not in preview.json()["masked_email"]

    # Wrong authenticated user email rejected (403 + INVITATION_EMAIL_MISMATCH)
    mismatch = api_client.post(
        f"/api/v1/invitations/{qa_raw_token}/accept",
        headers={"Authorization": f"Bearer {create_access_token(subject=str(wrong_uid))}"},
        json={},
    )
    assert mismatch.status_code == 403

    # Matching verified user accepts -> PENDING_APPROVAL + EmployeeProfile
    acc_qa = api_client.post(
        f"/api/v1/invitations/{qa_raw_token}/accept",
        headers={"Authorization": f"Bearer {create_access_token(subject=str(qa_uid))}"},
        json={"job_title": "Head of QA"},
    )
    assert acc_qa.status_code == 200
    qa_membership_id = acc_qa.json()["id"]
    qa_emp_profile_id = acc_qa.json()["employee_profile_id"]
    assert acc_qa.json()["membership_status"] == "PENDING_APPROVAL"
    assert qa_emp_profile_id is not None

    # Phase 1 Gate: active_qms_head_count == 0 -> ORG_OWNER CANNOT approve QA_HEAD!
    qa_denied = api_client.post(
        f"/api/v1/memberships/{qa_membership_id}/approve",
        headers=owner_a_headers,
        json={"reason": "Attempt without QMS_HEAD"},
    )
    assert qa_denied.status_code == 403

    # Now invite first QMS_HEAD
    qms_uid = _create_verified_user(session_factory, email="qmshead@cleanqms.in", full_name="First QMS Head")
    inv_qms = api_client.post(
        f"/api/v1/organizations/{org_a_id}/invitations",
        headers=owner_a_headers,
        json={
            "email": "qmshead@cleanqms.in",
            "site_ids": [str(site_a_id)],
            "requested_role_codes": ["QMS_HEAD"],
        },
    )
    assert inv_qms.status_code == 201
    qms_tok = inv_qms.json()["invite_token"]
    acc_qms = api_client.post(
        f"/api/v1/invitations/{qms_tok}/accept",
        headers={"Authorization": f"Bearer {create_access_token(subject=str(qms_uid))}"},
        json={},
    )
    assert acc_qms.status_code == 200
    qms_membership_id = acc_qms.json()["id"]

    # ORG_OWNER bootstraps first QMS_HEAD -> succeeds with QMS_HEAD_BOOTSTRAPPED AuditEvent!
    boot = api_client.post(
        f"/api/v1/memberships/{qms_membership_id}/approve",
        headers=owner_a_headers,
        json={"reason": "Bootstrap initial QMS_HEAD"},
    )
    assert boot.status_code == 200
    assert boot.json()["membership_status"] == "ACTIVE"

    db = session_factory()
    try:
        assert (
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.organization_id == org_a_id,
                    AuditEvent.action == "QMS_HEAD_BOOTSTRAPPED",
                )
            ).first()
            is not None
        )
    finally:
        db.close()

    # Phase 2 Gate: active_qms_head_count == 1 -> ORG_OWNER CANNOT approve another quality-sensitive role or 2nd QMS_HEAD!
    assert (
        api_client.post(
            f"/api/v1/memberships/{qa_membership_id}/approve",
            headers=owner_a_headers,
            json={"reason": "ORG_OWNER bypass attempt"},
        ).status_code
        == 403
    )

    # Active QMS_HEAD approves QA_HEAD cleanly
    qms_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(qms_uid))}",
        "X-Organization-Id": str(org_a_id),
    }
    qa_ok = api_client.post(
        f"/api/v1/memberships/{qa_membership_id}/approve",
        headers=qms_headers,
        json={"reason": "QMS_HEAD reviewed QA credentials"},
    )
    assert qa_ok.status_code == 200
    assert qa_ok.json()["membership_status"] == "ACTIVE"

    # Maker-checker: QMS_HEAD cannot self-assign/self-approve QC_HEAD
    self_assign = api_client.post(
        f"/api/v1/memberships/{qms_membership_id}/roles",
        headers=qms_headers,
        json={"role_code": "QC_HEAD", "scope_type": "SITE", "site_id": str(site_a_id), "reason": "Self grant"},
    )
    assert self_assign.status_code == 403

    # Qualifications & Training records work for QMS_HEAD and reject cross-tenant Org B access (404)
    qual = api_client.post(
        f"/api/v1/employee-profiles/{qa_emp_profile_id}/qualifications",
        headers=qms_headers,
        json={"qualification_type": "GMP_AUDITOR", "qualification_name": "Lead Pharma Auditor"},
    )
    assert qual.status_code == 201

    tr = api_client.post(
        f"/api/v1/employee-profiles/{qa_emp_profile_id}/training-records",
        headers=qms_headers,
        json={
            "site_id": str(site_a_id),
            "training_topic": "SOP-QA-001 Deviation Handling",
            "completed_on": str(date.today()),
            "assessment_score": 96,
        },
    )
    assert tr.status_code == 201

    # Cross-tenant Org B owner attempting to read Org A memberships or add training fails with 403/404
    assert api_client.get(f"/api/v1/organizations/{org_a_id}/memberships", headers=owner_b_headers).status_code == 403
    assert (
        api_client.post(
            f"/api/v1/employee-profiles/{qa_emp_profile_id}/qualifications",
            headers=owner_b_headers,
            json={"qualification_type": "GMP_AUDITOR", "qualification_name": "Cross Tenant Probe"},
        ).status_code
        == 403
    )

    # Org A owner attempting to revoke own membership or read Org A membership list
    assert api_client.get(f"/api/v1/organizations/{org_a_id}/memberships", headers=owner_a_headers).status_code == 200
    assert (
        api_client.post(
            f"/api/v1/memberships/{owner_a_membership_id}/approve",
            headers=owner_a_headers,
            json={"reason": "Self approve"},
        ).status_code
        == 403
    )

    # Site-scoped user cannot add license or submit a site outside allowed_site_ids (403)
    from app.models.domain.tenancy import OrganizationAddress, Site

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        site_mgr = create_seeded_tenant_actor(
            uow.session,
            org_name="MultiSite Pharma",
            site_code="MS-01",
            email="mgr.ms@cleanqms.in",
            role_code="REGULATORY_AFFAIRS",
            scope_type="SITE",
        )
        ms_org_id = site_mgr["organization"].id
        ms_addr = OrganizationAddress(
            organization_id=ms_org_id,
            line_1="Plot 2",
            city="Goa",
            postal_code="403001",
            country_code="IN",
            is_primary=False,
        )
        uow.session.add(ms_addr)
        uow.session.flush()
        other_site = Site(
            organization_id=ms_org_id,
            site_code="MS-02",
            site_name="Unassigned Goa Site",
            site_type="FDF",
            address_id=ms_addr.id,
        )
        uow.session.add(other_site)
        uow.session.flush()
        ms_user_id = site_mgr["user"].id
        other_site_id = other_site.id

    ms_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(ms_user_id))}",
        "X-Organization-Id": str(ms_org_id),
    }
    assert (
        api_client.post(
            f"/api/v1/sites/{other_site_id}/licenses",
            headers=ms_headers,
            json={"license_type": "WHO_GMP", "license_number": "GOA-01", "issuing_authority": "FDA"},
        ).status_code
        == 403
    )
    assert (
        api_client.post(
            f"/api/v1/sites/{other_site_id}/submit",
            headers=ms_headers,
            json={"row_version": 1},
        ).status_code
        == 403
    )
