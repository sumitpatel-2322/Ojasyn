import time
import uuid

import pytest
from sqlalchemy import select

from app.core.database import UnitOfWork
from app.core.deps import reset_rate_limiter
from app.core.security import create_access_token, decode_access_token
from app.models.domain.identity import User, UserCredential
from app.models.domain.governance import OutboxEvent
from app.services.identity_access_service import _generate_totp


@pytest.fixture(autouse=True)
def _clean_rate_limits():
    reset_rate_limiter()
    yield
    reset_rate_limiter()


def _create_platform_super_admin(session_factory, email: str = "superadmin@ojasyn.platform") -> uuid.UUID:
    from app.core.security import get_password_hash
    from app.domain.enums import UserAccountStatus
    from datetime import datetime, timezone

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        u = User(
            email=email,
            password_hash=get_password_hash("SuperAdminPass!123"),
            full_name="Platform Super Admin",
            account_status=UserAccountStatus.ACTIVE.value,
            platform_role="PLATFORM_SUPER_ADMIN",
            email_verified_at=datetime.now(timezone.utc),
        )
        uow.session.add(u)
        uow.session.flush()
        return u.id


def test_full_auth_profile_mfa_and_refresh_flow(api_client, sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]

    # 1. Register customer & verify email (raw verification token NEVER persisted in outbox_events)
    reg_resp = api_client.post(
        "/api/v1/auth/register",
        json={
            "email": "Customer.User@Example.COM",
            "password": "StrongPassword!123",
            "full_name": "Anita Sharma",
            "profile_type": "CUSTOMER",
            "customer_profile": {
                "city": "Pune",
                "state": "Maharashtra",
                "pincode": "411001",
            },
        },
    )
    assert reg_resp.status_code == 201
    reg_data = reg_resp.json()
    assert reg_data["email"] == "customer.user@example.com"
    assert reg_data["account_status"] == "PENDING_VERIFICATION"
    verify_token = reg_data["verification_token"]
    assert verify_token

    # Outbox payload must never contain the raw verification token
    db = session_factory()
    try:
        ev = db.scalars(
            select(OutboxEvent).where(OutboxEvent.event_type == "EmailVerificationRequested")
        ).first()
        assert ev is not None
        assert verify_token not in str(ev.payload_json)
    finally:
        db.close()

    # Login blocked before email verification
    pre_login = api_client.post(
        "/api/v1/auth/login",
        json={"email": "customer.user@example.com", "password": "StrongPassword!123"},
    )
    assert pre_login.status_code == 401

    # Verify email -> ACTIVE
    v_resp = api_client.post("/api/v1/auth/verify-email", json={"token": verify_token})
    assert v_resp.status_code == 200
    assert v_resp.json()["account_status"] == "ACTIVE"

    # Login succeeds
    login_resp = api_client.post(
        "/api/v1/auth/login",
        json={"email": "customer.user@example.com", "password": "StrongPassword!123"},
    )
    assert login_resp.status_code == 200
    tokens = login_resp.json()
    access = tokens["access_token"]
    refresh = tokens["refresh_token"]
    headers = {"Authorization": f"Bearer {access}"}

    # Profile GET + PATCH
    me = api_client.get("/api/v1/users/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["customer_profile"]["city"] == "Pune"

    patched = api_client.patch(
        "/api/v1/users/me/profile",
        headers=headers,
        json={"full_name": "Anita S.", "customer_profile": {"city": "Mumbai"}},
    )
    assert patched.status_code == 200
    assert patched.json()["user"]["full_name"] == "Anita S."
    assert patched.json()["customer_profile"]["city"] == "Mumbai"

    # Roles catalog
    roles = api_client.get("/api/v1/roles", headers=headers)
    assert roles.status_code == 200
    assert any(r["role_code"] == "QMS_HEAD" for r in roles.json())

    # Enroll + confirm MFA
    enroll = api_client.post("/api/v1/auth/mfa/challenge", headers=headers, json={"action": "enroll"})
    assert enroll.status_code == 200
    secret = enroll.json()["secret"]
    totp = _generate_totp(secret, int(time.time() // 30))

    # TOTP secret MUST be encrypted at rest in user_credentials.credential_hash
    db = session_factory()
    try:
        mfa_cred = db.scalars(
            select(UserCredential).where(UserCredential.credential_type == "MFA")
        ).first()
        assert mfa_cred is not None
        assert mfa_cred.credential_hash != secret
        assert (mfa_cred.credential_hash or "").startswith("enc:v1:")
    finally:
        db.close()

    confirm = api_client.post(
        "/api/v1/auth/mfa/challenge",
        headers=headers,
        json={"action": "enroll_confirm", "code": totp},
    )
    assert confirm.status_code == 200
    assert confirm.json()["mfa_state"] == "ENROLLED"

    # Login now requires valid TOTP
    no_mfa = api_client.post(
        "/api/v1/auth/login",
        json={"email": "customer.user@example.com", "password": "StrongPassword!123"},
    )
    assert no_mfa.status_code == 401

    with_mfa = api_client.post(
        "/api/v1/auth/login",
        json={"email": "customer.user@example.com", "password": "StrongPassword!123", "totp_code": totp},
    )
    assert with_mfa.status_code == 200
    mfa_refresh = with_mfa.json()["refresh_token"]

    # Refresh MUST preserve ["pwd", "mfa"] amr across rotations
    refreshed = api_client.post("/api/v1/auth/refresh", json={"refresh_token": mfa_refresh})
    assert refreshed.status_code == 200
    new_access = refreshed.json()["access_token"]
    assert decode_access_token(new_access)["amr"] == ["pwd", "mfa"]

    # Partial chemist profile PATCH preserves omitted fields like gst_number
    chem_reg = api_client.post(
        "/api/v1/auth/register",
        json={
            "email": "chemist@rx.example.com",
            "password": "ChemistPassword!123",
            "full_name": "Rajesh Chemist",
            "profile_type": "CHEMIST",
            "chemist_profile": {
                "pharmacy_name": "HealthPlus Pharmacy",
                "gst_number": "27ABCDE1234F1Z5",
                "pharmacy_address": "12 MG Road",
                "city": "Pune",
                "state": "Maharashtra",
                "pincode": "411001",
            },
        },
    )
    assert chem_reg.status_code == 201
    api_client.post("/api/v1/auth/verify-email", json={"token": chem_reg.json()["verification_token"]})
    chem_login = api_client.post(
        "/api/v1/auth/login",
        json={"email": "chemist@rx.example.com", "password": "ChemistPassword!123"},
    )
    chem_headers = {"Authorization": f"Bearer {chem_login.json()['access_token']}"}
    chem_patch = api_client.patch(
        "/api/v1/users/me/profile",
        headers=chem_headers,
        json={"chemist_profile": {"pharmacy_name": "HealthPlus Rx"}},
    )
    assert chem_patch.status_code == 200
    assert chem_patch.json()["chemist_profile"]["pharmacy_name"] == "HealthPlus Rx"
    assert chem_patch.json()["chemist_profile"]["gst_number"] == "27ABCDE1234F1Z5"

    out = api_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {new_access}"},
        json={"revoke_all_sessions": True},
    )
    assert out.status_code == 204
    assert api_client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {new_access}"}).status_code == 401


def test_organization_site_partner_and_master_data_lifecycle(api_client, sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]
    super_admin_id = _create_platform_super_admin(session_factory)
    super_headers = {"Authorization": f"Bearer {create_access_token(subject=str(super_admin_id))}"}

    # Register & verify org founder
    reg = api_client.post(
        "/api/v1/auth/register",
        json={"email": "founder@acmelabs.in", "password": "FounderPassword!123", "full_name": "Founder"},
    )
    assert reg.status_code == 201
    api_client.post("/api/v1/auth/verify-email", json={"token": reg.json()["verification_token"]})
    login = api_client.post(
        "/api/v1/auth/login",
        json={"email": "founder@acmelabs.in", "password": "FounderPassword!123"},
    )
    owner_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # Create Organization Onboarding (DRAFT)
    org_resp = api_client.post(
        "/api/v1/onboarding/organizations",
        headers=owner_headers,
        json={
            "legal_name": "Acme Life Sciences Pvt Ltd",
            "organization_type": "FDF_MANUFACTURER",
            "business_email": "regulatory@acmelabs.in",
            "registered_address": {
                "line_1": "Plot 42, Pharma SEZ",
                "city": "Hyderabad",
                "state_region": "Telangana",
                "postal_code": "500032",
                "country_code": "IN",
            },
        },
    )
    assert org_resp.status_code == 201
    org = org_resp.json()
    org_id = org["id"]
    assert org["status"] == "DRAFT"

    # Operational route (/permissions/me) strictly rejects DRAFT organization!
    op_blocked = api_client.get(
        "/api/v1/permissions/me",
        headers={**owner_headers, "X-Organization-Id": org_id},
    )
    assert op_blocked.status_code == 403

    # Onboarding route works on DRAFT organization; stale row_version returns 409
    assert api_client.get(f"/api/v1/onboarding/organizations/{org_id}", headers=owner_headers).status_code == 200
    stale = api_client.patch(
        f"/api/v1/organizations/{org_id}",
        headers=owner_headers,
        json={"row_version": 99, "trade_name": "Acme Pharma"},
    )
    assert stale.status_code == 409

    upd = api_client.patch(
        f"/api/v1/organizations/{org_id}",
        headers=owner_headers,
        json={"row_version": 1, "trade_name": "Acme Pharma", "reason": "Add trade name"},
    )
    assert upd.status_code == 200
    assert upd.json()["row_version"] == 2

    # Organization License
    lic = api_client.post(
        f"/api/v1/organizations/{org_id}/licenses",
        headers=owner_headers,
        json={
            "license_type": "WHO_GMP",
            "license_number": "GMP-IN-2026-01",
            "issuing_authority": "CDSCO",
        },
    )
    assert lic.status_code == 201

    # Submit org -> non-platform admin review blocked -> super admin approves to ACTIVE
    sub = api_client.post(
        f"/api/v1/organizations/{org_id}/submit",
        headers=owner_headers,
        json={"row_version": 2, "reason": "Onboarding ready"},
    )
    assert sub.status_code == 200
    assert sub.json()["status"] == "SUBMITTED"

    denied_review = api_client.post(
        f"/api/v1/organizations/{org_id}/review",
        headers=owner_headers,
        json={"row_version": 3, "decision": "ACTIVE", "comments": "Self approve attempt"},
    )
    assert denied_review.status_code == 403

    approved_org = api_client.post(
        f"/api/v1/organizations/{org_id}/review",
        headers=super_headers,
        json={"row_version": 3, "decision": "ACTIVE", "comments": "Verified GMP license & registration"},
    )
    assert approved_org.status_code == 200
    assert approved_org.json()["status"] == "ACTIVE"

    # Site creation, license, submit & super-admin activation
    addr_id = org["registered_address_id"]
    site_resp = api_client.post(
        f"/api/v1/organizations/{org_id}/sites",
        headers=owner_headers,
        json={
            "site_code": "HYD-01",
            "site_name": "Hyderabad FDF Unit",
            "site_type": "FDF",
            "address_id": addr_id,
            "is_primary_site": True,
        },
    )
    assert site_resp.status_code == 201
    site_id = site_resp.json()["id"]

    assert (
        api_client.post(
            f"/api/v1/sites/{site_id}/licenses",
            headers={**owner_headers, "X-Organization-Id": org_id},
            json={"license_type": "FORM_25", "license_number": "TS-25-900", "issuing_authority": "DCA Telangana"},
        ).status_code
        == 201
    )
    assert (
        api_client.post(
            f"/api/v1/sites/{site_id}/submit",
            headers={**owner_headers, "X-Organization-Id": org_id},
            json={"row_version": 1},
        ).status_code
        == 200
    )
    assert (
        api_client.post(
            f"/api/v1/sites/{site_id}/review",
            headers=super_headers,
            json={"row_version": 2, "decision": "ACTIVE", "comments": "Site GMP approved"},
        ).status_code
        == 200
    )

    # Department & /permissions/me
    dept = api_client.post(
        f"/api/v1/organizations/{org_id}/departments",
        headers=owner_headers,
        json={"site_id": site_id, "department_code": "QA", "department_name": "Quality Assurance"},
    )
    assert dept.status_code == 201

    perms = api_client.get("/api/v1/permissions/me", headers={**owner_headers, "X-Organization-Id": org_id})
    assert perms.status_code == 200
    assert "ORG_OWNER" in perms.json()["role_codes"]

    # Partner lifecycle (create, site, invite without leaking token in outbox, approve)
    p_resp = api_client.post(
        "/api/v1/partners",
        headers={**owner_headers, "X-Organization-Id": org_id},
        json={
            "partner_code": "SUP-01",
            "legal_name": "PureExcipients India",
            "partner_type": "SUPPLIER",
            "primary_email": "qa@pureexcipients.in",
        },
    )
    assert p_resp.status_code == 201
    partner_id = p_resp.json()["id"]

    assert (
        api_client.post(
            f"/api/v1/partners/{partner_id}/sites",
            headers={**owner_headers, "X-Organization-Id": org_id},
            json={
                "site_code": "MUM-WH1",
                "site_name": "Mumbai Warehouse",
                "address_line_1": "MIDC Andheri",
                "city": "Mumbai",
                "postal_code": "400093",
            },
        ).status_code
        == 201
    )
    p_inv = api_client.post(
        f"/api/v1/partners/{partner_id}/invitations",
        headers={**owner_headers, "X-Organization-Id": org_id},
        json={"email": "rep@pureexcipients.in", "role_code": "PARTNER_ADMIN"},
    )
    assert p_inv.status_code == 201
    pm_id = p_inv.json()["id"]
    pm_tok = p_inv.json()["invite_token"]

    db = session_factory()
    try:
        out_ev = db.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "PartnerUserInvited")).first()
        assert out_ev is not None and pm_tok not in str(out_ev.payload_json)
    finally:
        db.close()

    assert (
        api_client.post(
            f"/api/v1/partner-memberships/{pm_id}/approve",
            headers={**owner_headers, "X-Organization-Id": org_id},
            json={"reason": "Supplier verified"},
        ).status_code
        == 200
    )

    # Master Data: global restricted to PLATFORM_SUPER_ADMIN; tenant override allowed for ORG_OWNER
    assert (
        api_client.post(
            "/api/v1/reference-values",
            headers={**owner_headers, "X-Organization-Id": org_id},
            json={"category_code": "DOSAGE_FORM", "code": "INJ", "display_name": "Injectable", "is_global": True},
        ).status_code
        == 403
    )
    assert (
        api_client.post(
            "/api/v1/reference-values",
            headers=super_headers,
            json={"category_code": "DOSAGE_FORM", "code": "INJ", "display_name": "Injectable", "is_global": True},
        ).status_code
        == 201
    )
    assert (
        api_client.post(
            "/api/v1/units-of-measure",
            headers=super_headers,
            json={"unit_code": "PPM", "unit_name": "Parts Per Million", "symbol": "ppm", "dimension": "CONCENTRATION"},
        ).status_code
        == 201
    )
    assert (
        api_client.post(
            "/api/v1/reference-values",
            headers={**owner_headers, "X-Organization-Id": org_id},
            json={
                "category_code": "COMPLAINT_CATEGORY",
                "code": "CUSTOM_ACME",
                "display_name": "Acme Packaging Seal Defect",
                "is_global": False,
            },
        ).status_code
        == 201
    )
    rv_list = api_client.get(
        "/api/v1/reference-values",
        headers={**owner_headers, "X-Organization-Id": org_id},
    )
    assert rv_list.status_code == 200
    codes = {r["code"] for r in rv_list.json()["items"]}
    assert {"INJ", "CUSTOM_ACME"}.issubset(codes)
