from datetime import datetime, timezone

from sqlalchemy import select

import app.models.domain.sops  # noqa: F401
from app.core.database import UnitOfWork
from app.core.security import create_access_token, get_password_hash
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


def test_sops_cross_tenant_site_scope_rbac_and_maker_checker_security(
    api_client, sqlite_db
) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        # Org A Site 1 QA_HEAD (has sop.read, sop.manage, sop.approve)
        qa_site1 = create_seeded_tenant_actor(
            uow.session,
            org_name="Alpha Bio",
            site_code="ALP-S1",
            email="qa.s1@alphabio.in",
            role_code="QA_HEAD",
            scope_type="SITE",
        )
        org_a_id = qa_site1["organization"].id
        site_1_id = qa_site1["site"].id
        qa_site1_user_id = qa_site1["user"].id

        # Org A Site 2
        addr_2 = OrganizationAddress(
            organization_id=org_a_id,
            address_type="MANUFACTURING",
            line_1="Plot 22 Bio Park",
            city="Pune",
            state_region="MH",
            postal_code="411001",
            country_code="IN",
            is_primary=False,
        )
        uow.session.add(addr_2)
        uow.session.flush()
        site_2 = Site(
            organization_id=org_a_id,
            site_code="ALP-S2",
            site_name="Alpha Bio Site 2",
            site_type="MANUFACTURING_PLANT",
            address_id=addr_2.id,
            status="ACTIVE",
        )
        uow.session.add(site_2)
        uow.session.flush()
        site_2_id = site_2.id

        # Org A Site 2 QA_HEAD
        qa_s2_user = User(
            email="qa.s2@alphabio.in",
            password_hash=get_password_hash("QaS2Pass!123"),
            full_name="QA Site 2",
            account_status="ACTIVE",
            email_verified_at=datetime.now(timezone.utc),
        )
        uow.session.add(qa_s2_user)
        uow.session.flush()
        qa_s2_member = OrganizationMembership(
            user_id=qa_s2_user.id,
            organization_id=org_a_id,
            membership_status="ACTIVE",
            member_type="EMPLOYEE",
        )
        uow.session.add(qa_s2_member)
        uow.session.flush()
        uow.session.add(
            SiteAssignment(
                organization_id=org_a_id,
                membership_id=qa_s2_member.id,
                site_id=site_2_id,
                status="ACTIVE",
                assigned_by_user_id=qa_site1_user_id,
            )
        )
        qa_role = uow.session.scalar(select(Role).where(Role.role_code == "QA_HEAD"))
        assert qa_role is not None
        uow.session.add(
            MembershipRoleAssignment(
                organization_id=org_a_id,
                membership_id=qa_s2_member.id,
                role_id=qa_role.id,
                scope_type="SITE",
                site_id=site_2_id,
                status="ACTIVE",
                granted_by_user_id=qa_site1_user_id,
            )
        )
        qa_site2_user_id = qa_s2_user.id

        # Org A Site 1 PRODUCTION_OPERATOR (only sop.read, no sop.manage / sop.approve)
        op_user = User(
            email="operator.s1@alphabio.in",
            password_hash=get_password_hash("OpS1Pass!123"),
            full_name="Operator Site 1",
            account_status="ACTIVE",
            email_verified_at=datetime.now(timezone.utc),
        )
        uow.session.add(op_user)
        uow.session.flush()
        op_member = OrganizationMembership(
            user_id=op_user.id,
            organization_id=org_a_id,
            membership_status="ACTIVE",
            member_type="EMPLOYEE",
        )
        uow.session.add(op_member)
        uow.session.flush()
        uow.session.add(
            SiteAssignment(
                organization_id=org_a_id,
                membership_id=op_member.id,
                site_id=site_1_id,
                status="ACTIVE",
                assigned_by_user_id=qa_site1_user_id,
            )
        )
        op_role = uow.session.scalar(
            select(Role).where(Role.role_code == "PRODUCTION_OPERATOR")
        )
        assert op_role is not None
        uow.session.add(
            MembershipRoleAssignment(
                organization_id=org_a_id,
                membership_id=op_member.id,
                role_id=op_role.id,
                scope_type="SITE",
                site_id=site_1_id,
                status="ACTIVE",
                granted_by_user_id=qa_site1_user_id,
            )
        )
        op_user_id = op_user.id

        # Org B QA_HEAD
        org_b_actor = create_seeded_tenant_actor(
            uow.session,
            org_name="Beta Remedies",
            site_code="BET-S1",
            email="qa@betaremedies.in",
            role_code="QA_HEAD",
            scope_type="SITE",
        )
        org_b_id = org_b_actor["organization"].id
        org_b_user_id = org_b_actor["user"].id

    s1_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(qa_site1_user_id))}",
        "X-Organization-Id": str(org_a_id),
    }
    s2_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(qa_site2_user_id))}",
        "X-Organization-Id": str(org_a_id),
    }
    op_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(op_user_id))}",
        "X-Organization-Id": str(org_a_id),
    }
    org_b_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(org_b_user_id))}",
        "X-Organization-Id": str(org_b_id),
    }

    # 1. Org A Site 1 QA_HEAD creates SOP and Revision 1.0 on Site 1
    sop_resp = api_client.post(
        "/api/v1/sops",
        headers=s1_headers,
        json={
            "site_id": str(site_1_id),
            "sop_code": "SOP-SEC-01",
            "title": "Sterile Filtration Control",
        },
    )
    assert sop_resp.status_code == 201, sop_resp.text
    sop_id = sop_resp.json()["id"]

    rev_resp = api_client.post(
        f"/api/v1/sops/{sop_id}/revisions",
        headers=s1_headers,
        json={"revision_number": "1.0"},
    )
    assert rev_resp.status_code == 201, rev_resp.text
    rev_id = rev_resp.json()["id"]

    submit_resp = api_client.post(
        f"/api/v1/sops/{sop_id}/revisions/{rev_id}/submit-review",
        headers=s1_headers,
    )
    assert submit_resp.status_code == 200

    # 2. Maker-checker violation: creator/submitter (QA Site 1) attempts to self-approve -> 403 SELF_APPROVAL_BLOCKED
    self_app = api_client.post(
        f"/api/v1/sops/{sop_id}/revisions/{rev_id}/approve",
        headers=s1_headers,
        json={"decision": "APPROVED"},
    )
    assert self_app.status_code == 403
    assert self_app.json()["error_code"] == "SELF_APPROVAL_BLOCKED"

    # 3. Cross-tenant isolation: Org B gets 404 on Org A's SOP & revisions
    assert (
        api_client.get(f"/api/v1/sops/{sop_id}", headers=org_b_headers).status_code
        == 404
    )
    assert (
        api_client.get(
            f"/api/v1/sops/{sop_id}/revisions/{rev_id}", headers=org_b_headers
        ).status_code
        == 404
    )

    # 4. Site-scope isolation: Org A Site 2 user cannot access Site 1 SOP (403 SITE_SCOPE_DENIED)
    site_Read = api_client.get(f"/api/v1/sops/{sop_id}", headers=s2_headers)
    assert site_Read.status_code == 403
    assert site_Read.json()["error_code"] == "SITE_SCOPE_DENIED"

    # Org A Site 1 user cannot create an SOP for Site 2 (403 SITE_SCOPE_DENIED)
    cross_site_create = api_client.post(
        "/api/v1/sops",
        headers=s1_headers,
        json={
            "site_id": str(site_2_id),
            "sop_code": "SOP-SEC-02",
            "title": "Unauthorized Cross-Site SOP",
        },
    )
    assert cross_site_create.status_code == 403
    assert cross_site_create.json()["error_code"] == "SITE_SCOPE_DENIED"

    # Org A Site 1 user cannot create an org-wide SOP (site_id=None, applicable_site_ids=None)
    org_wide_create_denied = api_client.post(
        "/api/v1/sops",
        headers=s1_headers,
        json={
            "sop_code": "SOP-SEC-ORGWIDE-FAIL",
            "title": "Unauthorized Org-Wide SOP by Site User",
        },
    )
    assert org_wide_create_denied.status_code == 403
    assert org_wide_create_denied.json()["error_code"] == "SITE_SCOPE_DENIED"

    # 5. RBAC enforcement: PRODUCTION_OPERATOR can read SOPs on Site 1 but cannot create, approve, or activate
    assert (
        api_client.get(f"/api/v1/sops/{sop_id}", headers=op_headers).status_code
        == 200
    )
    op_create = api_client.post(
        "/api/v1/sops",
        headers=op_headers,
        json={
            "site_id": str(site_1_id),
            "sop_code": "SOP-OP-FAIL",
            "title": "Operator Unauthorized SOP",
        },
    )
    assert op_create.status_code == 403
    op_approve = api_client.post(
        f"/api/v1/sops/{sop_id}/revisions/{rev_id}/approve",
        headers=op_headers,
        json={"decision": "APPROVED"},
    )
    assert op_approve.status_code == 403
    op_activate = api_client.post(
        f"/api/v1/sops/{sop_id}/revisions/{rev_id}/activate",
        headers=op_headers,
        json={},
    )
    assert op_activate.status_code == 403

    # Verify SecurityEvents persisted out-of-band for SELF_APPROVAL_BLOCKED, SITE_SCOPE_VIOLATION, RBAC_PERMISSION_DENIED
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        event_types = set(
            uow.session.scalars(
                select(SecurityEvent.event_type).where(
                    SecurityEvent.organization_id == org_a_id
                )
            ).all()
        )
        assert "SELF_APPROVAL_BLOCKED" in event_types
        assert "SITE_SCOPE_VIOLATION" in event_types
        assert "RBAC_PERMISSION_DENIED" in event_types
