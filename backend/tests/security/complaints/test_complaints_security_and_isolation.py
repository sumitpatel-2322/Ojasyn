import pytest
from sqlalchemy import select

from app.core.database import UnitOfWork
from app.core.deps import reset_rate_limiter
from app.core.security import create_access_token
from app.models.domain.governance import SecurityEvent
from app.models.domain.identity import OrganizationMembership
from app.models.domain.tenancy import OrganizationAddress, Site
from tests.conftest import create_seeded_tenant_actor


@pytest.fixture(autouse=True)
def _clean_limits():
    reset_rate_limiter()
    yield
    reset_rate_limiter()


def test_tracking_token_sanitization_and_tenant_site_isolation(api_client, sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        org_a_qa = create_seeded_tenant_actor(
            uow.session,
            org_name="Alpha Therapeutics",
            site_code="ALP-A",
            email="qa.a@alpha.in",
            role_code="QA_OFFICER",
            scope_type="SITE",
        )
        org_b_qa = create_seeded_tenant_actor(
            uow.session,
            org_name="Beta Therapeutics",
            site_code="BET-A",
            email="qa.b@beta.in",
            role_code="QA_OFFICER",
            scope_type="SITE",
        )
        org_a_id = org_a_qa["organization"].id
        site_a1_id = org_a_qa["site"].id
        addr_a = OrganizationAddress(
            organization_id=org_a_id,
            line_1="Plot 99",
            city="Indore",
            postal_code="452001",
            country_code="IN",
            is_primary=False,
        )
        uow.session.add(addr_a)
        uow.session.flush()
        site_a2 = Site(
            organization_id=org_a_id,
            site_code="ALP-B",
            site_name="Alpha Site B",
            site_type="FDF",
            address_id=addr_a.id,
            status="ACTIVE",
        )
        uow.session.add(site_a2)
        uow.session.flush()
        site_a2_id = site_a2.id
        qa_a_user_id = org_a_qa["user"].id
        qa_b_user_id = org_b_qa["user"].id

    qa_a_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(qa_a_user_id))}",
        "X-Organization-Id": str(org_a_id),
    }
    qa_b_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(qa_b_user_id))}",
        "X-Organization-Id": str(org_b_qa["organization"].id),
    }

    # Create un-sited Org A complaint (site_id IS NULL) + Site A2 complaint + Site A1 complaint
    c_unsited = api_client.post(
        "/api/v1/complaints",
        json={
            "organization_id": str(org_a_id),
            "reported_product_name": "Product Unsited",
            "description": "Packaging tear on delivery.",
            "complainant": {"full_name": "Guest Reporter", "email": "guest.secret@example.com"},
        },
    )
    assert c_unsited.status_code == 201
    unsited_id = c_unsited.json()["id"]
    unsited_token = c_unsited.json()["tracking_token"]

    c_site_b = api_client.post(
        "/api/v1/complaints",
        json={
            "organization_id": str(org_a_id),
            "site_id": str(site_a2_id),
            "reported_product_name": "Product Site A2",
            "description": "Label smudge on bottle.",
        },
    )
    assert c_site_b.status_code == 201
    site_a2_comp_id = c_site_b.json()["id"]

    # Add INTERNAL QA note and OUTBOUND customer communication to unsited complaint
    assert (
        api_client.post(
            f"/api/v1/complaints/{unsited_id}/communications",
            headers=qa_a_headers,
            json={
                "direction": "INTERNAL",
                "body_text": "SECRET INTERNAL ROOT CAUSE NOTE - DO NOT EXPOSE TO GUEST",
                "is_internal_only": True,
            },
        ).status_code
        == 201
    )
    assert (
        api_client.post(
            f"/api/v1/complaints/{unsited_id}/communications",
            headers=qa_a_headers,
            json={
                "direction": "OUTBOUND",
                "body_text": "We have received your complaint.",
                "is_internal_only": False,
            },
        ).status_code
        == 201
    )

    # 1. Guest tracking with invalid token -> 401 + SecurityEvent
    bad_track = api_client.get(
        f"/api/v1/complaints/{unsited_id}",
        headers={"X-Tracking-Token": "wrong-tracking-token"},
    )
    assert bad_track.status_code == 401
    db = session_factory()
    try:
        assert (
            db.scalars(
                select(SecurityEvent).where(SecurityEvent.event_type == "COMPLAINT_TRACKING_TOKEN_INVALID")
            ).first()
            is not None
        )
    finally:
        db.close()

    # 2. Guest tracking with valid token -> sanitized DTO, ZERO internal notes, ZERO internal PII
    good_track = api_client.get(
        f"/api/v1/complaints/{unsited_id}",
        headers={"X-Tracking-Token": unsited_token},
    )
    assert good_track.status_code == 200
    body_text = good_track.text
    assert "We have received your complaint." in body_text
    assert "SECRET INTERNAL ROOT CAUSE NOTE" not in body_text
    assert "guest.secret@example.com" not in body_text

    # 3. Site-scoped QA Officer in Org A sees un-sited (site_id IS NULL) complaint, NEVER sees Site A2 complaint!
    listed = api_client.get("/api/v1/complaints", headers=qa_a_headers)
    assert listed.status_code == 200
    visible_ids = {item["id"] for item in listed.json()["items"]}
    assert unsited_id in visible_ids
    assert site_a2_comp_id not in visible_ids
    assert api_client.get(f"/api/v1/complaints/{site_a2_comp_id}", headers=qa_a_headers).status_code == 404

    # 4. Cross-tenant Org B QA Officer gets 404 on Org A complaint & 403 on super-admin route endpoint
    assert api_client.get(f"/api/v1/complaints/{unsited_id}", headers=qa_b_headers).status_code == 404
    assert api_client.get("/api/v1/complaints/unrouted", headers=qa_b_headers).status_code == 403
    assert (
        api_client.post(
            f"/api/v1/complaints/{unsited_id}/route",
            headers=qa_b_headers,
            json={"row_version": 1, "organization_id": str(org_a_id), "reason": "Unauthorized route"},
        ).status_code
        == 403
    )


def test_copilot_session_token_and_user_isolation(api_client, sqlite_db) -> None:
    sess = api_client.post("/api/v1/complaints/copilot/sessions", json={})
    assert sess.status_code == 201
    sess_id = sess.json()["id"]

    # Missing/wrong X-Copilot-Session-Token -> 401
    assert api_client.get(f"/api/v1/complaints/copilot/sessions/{sess_id}").status_code == 401
    assert (
        api_client.post(
            f"/api/v1/complaints/copilot/sessions/{sess_id}/messages",
            headers={"X-Copilot-Session-Token": "forged-token"},
            json={"message": "hello"},
        ).status_code
        == 401
    )


def test_multi_org_user_public_intake_without_header_and_copilot_org_mismatch(api_client, sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        org1 = create_seeded_tenant_actor(
            uow.session,
            org_name="Multi Org 1",
            site_code="MO-01",
            email="multi.user@example.com",
            role_code="QA_OFFICER",
        )
        org2 = create_seeded_tenant_actor(
            uow.session,
            org_name="Multi Org 2",
            site_code="MO-02",
            email="other.admin@example.com",
            role_code="QA_OFFICER",
        )
        multi_uid = org1["user"].id
        org1_id = org1["organization"].id
        org2_id = org2["organization"].id
        uow.session.add(
            OrganizationMembership(
                user_id=multi_uid,
                organization_id=org2_id,
                member_type="EMPLOYEE",
                membership_status="ACTIVE",
            )
        )
        uow.session.flush()

    auth_headers = {"Authorization": f"Bearer {create_access_token(subject=str(multi_uid))}"}

    # Multi-org authenticated user omitting X-Organization-Id falls back cleanly (no 422 TENANT_HEADER_REQUIRED)
    pub = api_client.post(
        "/api/v1/complaints",
        headers=auth_headers,
        json={
            "reported_product_name": "Multivitamin Syrup",
            "description": "Bottle cap loose on multi-org public intake.",
        },
    )
    assert pub.status_code == 201
    assert pub.json()["status"] == "UNROUTED_TRIAGE"

    # Malformed X-Organization-Id still fails cleanly with 422
    bad_hdr = api_client.post(
        "/api/v1/complaints",
        headers={**auth_headers, "X-Organization-Id": "not-a-uuid"},
        json={
            "reported_product_name": "Multivitamin Syrup",
            "description": "Should fail header validation.",
        },
    )
    assert bad_hdr.status_code == 422

    # Authenticated copilot session in org1 rejects submit targeting org2 (403)
    sess = api_client.post(
        "/api/v1/complaints/copilot/sessions",
        headers={**auth_headers, "X-Organization-Id": str(org1_id)},
        json={},
    )
    assert sess.status_code == 201
    sess_id = sess.json()["id"]

    # Seed draft directly to focus on submit guard
    from app.models.domain.complaints import CopilotSession
    import uuid as _uuid

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        cs = uow.session.get(CopilotSession, _uuid.UUID(sess_id))
        assert cs is not None
        cs.draft_state_json = {
            "reported_product_name": "Tablet X",
            "description": "Blister foil punctured.",
        }

    mismatch = api_client.post(
        f"/api/v1/complaints/copilot/sessions/{sess_id}/submit",
        headers={**auth_headers, "X-Organization-Id": str(org1_id)},
        json={"organization_id": str(org2_id)},
    )
    assert mismatch.status_code == 403
