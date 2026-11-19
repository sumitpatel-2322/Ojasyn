import uuid
from datetime import date
from typing import Dict

from sqlalchemy import select

from app.core.database import UnitOfWork
from app.core.security import create_access_token, get_password_hash
from app.domain.enums import (
    AssignmentStatus,
    AssignmentType,
    CAPAStatus,
    MembershipStatus,
    MemberType,
    RoleAssignmentScopeType,
    RoleAssignmentStatus,
    SiteStatus,
    SiteType,
    UserAccountStatus,
)
from app.models.domain.governance import SecurityEvent
from app.models.domain.identity import (
    MembershipRoleAssignment,
    OrganizationMembership,
    Role,
    SiteAssignment,
    User,
)
from app.models.domain.qms_loop import CAPA, CAPAAction, CAPAEffectivenessCheck
from app.models.domain.tenancy import OrganizationAddress, Site
from tests.conftest import create_seeded_tenant_actor


def _headers(user: User, org_id: uuid.UUID) -> Dict[str, str]:
    token = create_access_token(subject=str(user.id))
    return {"Authorization": f"Bearer {token}", "X-Organization-Id": str(org_id)}


def _add_user(
    db,
    *,
    org_id: uuid.UUID,
    site_id: uuid.UUID,
    email: str,
    role_code: str,
    scope_type: str = RoleAssignmentScopeType.ORGANIZATION.value,
) -> Dict[str, object]:
    user = User(
        email=email,
        full_name=f"User {email}",
        password_hash=get_password_hash("StrongPass!123"),
        account_status=UserAccountStatus.ACTIVE.value,
    )
    db.add(user)
    db.flush()
    mem = OrganizationMembership(
        user_id=user.id,
        organization_id=org_id,
        member_type=MemberType.EMPLOYEE.value,
        membership_status=MembershipStatus.ACTIVE.value,
        employee_code=f"EMP-{uuid.uuid4().hex[:6]}",
    )
    db.add(mem)
    db.flush()
    db.add(
        SiteAssignment(
            organization_id=org_id,
            membership_id=mem.id,
            site_id=site_id,
            assignment_type=AssignmentType.PRIMARY.value,
            status=AssignmentStatus.ACTIVE.value,
            assigned_by_user_id=user.id,
        )
    )
    role = db.query(Role).filter(Role.role_code == role_code).one()
    db.add(
        MembershipRoleAssignment(
            organization_id=org_id,
            membership_id=mem.id,
            role_id=role.id,
            scope_type=scope_type,
            site_id=site_id if scope_type == RoleAssignmentScopeType.SITE.value else None,
            granted_by_user_id=user.id,
            approved_by_user_id=user.id,
            status=RoleAssignmentStatus.ACTIVE.value,
        )
    )
    db.flush()
    return {"user": user, "membership": mem}


def test_capa_e3_cross_tenant_site_scope_rbac_and_maker_checker(api_client, sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        db = uow.session
        org_a = create_seeded_tenant_actor(
            db,
            org_name="Alpha CAPA Org",
            site_code="ALP-01",
            email="qms.alpha@example.com",
            role_code="QMS_HEAD",
            scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
        )
        org_b = create_seeded_tenant_actor(
            db,
            org_name="Beta CAPA Org",
            site_code="BET-01",
            email="qms.beta@example.com",
            role_code="QMS_HEAD",
            scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
        )

        site_a1 = org_a["site"]
        addr2 = OrganizationAddress(
            organization_id=org_a["organization"].id,
            line_1="Plot 2",
            city="Pune",
            postal_code="411002",
            country_code="IN",
            is_primary=False,
        )
        db.add(addr2)
        db.flush()
        site_a2 = Site(
            organization_id=org_a["organization"].id,
            site_code="ALP-02",
            site_name="Alpha Site 2",
            site_type=SiteType.FDF.value,
            address_id=addr2.id,
            status=SiteStatus.ACTIVE.value,
            is_primary_site=False,
        )
        db.add(site_a2)
        db.flush()

        officer_a1 = _add_user(
            db,
            org_id=org_a["organization"].id,
            site_id=site_a1.id,
            email="officer.a1@example.com",
            role_code="QA_OFFICER",
            scope_type=RoleAssignmentScopeType.SITE.value,
        )
        qa_head_a = _add_user(
            db,
            org_id=org_a["organization"].id,
            site_id=site_a1.id,
            email="qahead.a@example.com",
            role_code="QA_HEAD",
        )
        auditor_a = _add_user(
            db,
            org_id=org_a["organization"].id,
            site_id=site_a1.id,
            email="auditor.a@example.com",
            role_code="AUDITOR_READ_ONLY",
        )
        prod_op_a = _add_user(
            db,
            org_id=org_a["organization"].id,
            site_id=site_a1.id,
            email="operator.a@example.com",
            role_code="PRODUCTION_OPERATOR",
            scope_type=RoleAssignmentScopeType.SITE.value,
        )

        capa_a2 = CAPA(
            organization_id=org_a["organization"].id,
            site_id=site_a2.id,
            capa_number="ALPHA-CAPA-2026-00001",
            title="Site A2 CAPA",
            status=CAPAStatus.DRAFT.value,
            owner_membership_id=qa_head_a["membership"].id,
            created_by_user_id=org_a["user"].id,
        )
        db.add(capa_a2)
        db.flush()

        org_a_id = org_a["organization"].id
        org_b_id = org_b["organization"].id
        site_a1_id = site_a1.id
        site_a2_id = site_a2.id
        capa_a2_id = capa_a2.id

    qms_a_hdrs = _headers(org_a["user"], org_a_id)
    qms_b_hdrs = _headers(org_b["user"], org_b_id)
    officer_a1_hdrs = _headers(officer_a1["user"], org_a_id)
    qa_head_a_hdrs = _headers(qa_head_a["user"], org_a_id)
    auditor_a_hdrs = _headers(auditor_a["user"], org_a_id)
    prod_op_a_hdrs = _headers(prod_op_a["user"], org_a_id)

    # 1. Cross-tenant 404 isolation
    assert api_client.get(f"/api/v1/capas/{capa_a2_id}", headers=qms_b_hdrs).status_code == 404
    assert (
        api_client.patch(
            f"/api/v1/capas/{capa_a2_id}",
            headers=qms_b_hdrs,
            json={"row_version": 1, "title": "Hijack"},
        ).status_code
        == 404
    )
    assert (
        api_client.post(
            f"/api/v1/capas/{capa_a2_id}/open",
            headers=qms_b_hdrs,
            json={"row_version": 1},
        ).status_code
        == 404
    )

    # 2. Site-scope 403 isolation
    r_site_get = api_client.get(f"/api/v1/capas/{capa_a2_id}", headers=officer_a1_hdrs)
    assert r_site_get.status_code == 403
    assert r_site_get.json()["error_code"] == "SITE_SCOPE_DENIED"

    r_site_create = api_client.post(
        "/api/v1/capas",
        headers=officer_a1_hdrs,
        json={"site_id": str(site_a2_id), "title": "Out of scope CAPA"},
    )
    assert r_site_create.status_code == 403
    assert r_site_create.json()["error_code"] == "SITE_SCOPE_DENIED"

    # 3. RBAC enforcement
    assert api_client.get("/api/v1/capas", headers=prod_op_a_hdrs).status_code == 403
    assert api_client.get("/api/v1/capas", headers=auditor_a_hdrs).status_code == 200
    assert (
        api_client.post(
            "/api/v1/capas",
            headers=auditor_a_hdrs,
            json={"title": "Auditor cannot create"},
        ).status_code
        == 403
    )

    # Create a Site A1 CAPA owned & created by officer_a1
    capa_a1 = api_client.post(
        "/api/v1/capas",
        headers=officer_a1_hdrs,
        json={
            "site_id": str(site_a1_id),
            "title": "Maker-checker CAPA test",
            "owner_membership_id": str(officer_a1["membership"].id),
        },
    ).json()
    capa_a1_id = capa_a1["id"]

    # QA_OFFICER lacks capa.approve and capa.close
    assert (
        api_client.post(
            f"/api/v1/capas/{capa_a1_id}/open",
            headers=officer_a1_hdrs,
            json={"row_version": 1},
        ).status_code
        == 403
    )
    assert (
        api_client.post(
            f"/api/v1/capas/{capa_a1_id}/close",
            headers=officer_a1_hdrs,
            json={"row_version": 1, "closure_reason": "No perm"},
        ).status_code
        == 403
    )

    # 4. Maker-checker on open: creator/owner (even if QMS_HEAD created it) cannot self-approve!
    # Check using capa_a2 where qms_head_a (org_a["user"]) created it and qa_head_a is owner:
    # - org_a["user"] is creator -> 403 MAKER_CHECKER_VIOLATION
    mc_open_creator = api_client.post(
        f"/api/v1/capas/{capa_a2_id}/open",
        headers=qms_a_hdrs,
        json={"row_version": 1},
    )
    assert mc_open_creator.status_code == 403
    assert mc_open_creator.json()["error_code"] == "MAKER_CHECKER_VIOLATION"

    # - qa_head_a is owner -> 403 MAKER_CHECKER_VIOLATION
    mc_open_owner = api_client.post(
        f"/api/v1/capas/{capa_a2_id}/open",
        headers=qa_head_a_hdrs,
        json={"row_version": 1},
    )
    assert mc_open_owner.status_code == 403
    assert mc_open_owner.json()["error_code"] == "MAKER_CHECKER_VIOLATION"

    # Approve capa_a1 via qa_head_a (distinct from officer_a1)
    assert (
        api_client.post(
            f"/api/v1/capas/{capa_a1_id}/open",
            headers=qa_head_a_hdrs,
            json={"row_version": 1},
        ).status_code
        == 200
    )

    # Add & complete action, submit effectiveness
    act = api_client.post(
        f"/api/v1/capas/{capa_a1_id}/actions",
        headers=officer_a1_hdrs,
        json={"title": "Fix line sensor"},
    ).json()
    api_client.post(
        f"/api/v1/capas/{capa_a1_id}/actions/{act['id']}/complete",
        headers=officer_a1_hdrs,
        json={"row_version": 1, "implementation_notes": "Replaced sensor."},
    )
    sub_resp = api_client.post(
        f"/api/v1/capas/{capa_a1_id}/submit-effectiveness",
        headers=officer_a1_hdrs,
        json={"row_version": 3},
    )
    assert sub_resp.status_code == 200, sub_resp.text

    chk = api_client.post(
        f"/api/v1/capas/{capa_a1_id}/effectiveness-checks",
        headers=officer_a1_hdrs,
        json={"criteria_description": "Zero sensor errors", "planned_check_date": date.today().isoformat()},
    ).json()

    # 5. Maker-checker on effectiveness review: owner (officer_a1) cannot self-review!
    mc_eff = api_client.post(
        f"/api/v1/capas/{capa_a1_id}/effectiveness-checks/{chk['id']}/record",
        headers=officer_a1_hdrs,
        json={"row_version": 1, "status": "EFFECTIVE", "findings_summary": "Self review attempt"},
    )
    assert mc_eff.status_code == 403
    assert mc_eff.json()["error_code"] == "MAKER_CHECKER_VIOLATION"

    # Distinct reviewer (qa_head_a) records EFFECTIVE
    assert (
        api_client.post(
            f"/api/v1/capas/{capa_a1_id}/effectiveness-checks/{chk['id']}/record",
            headers=qa_head_a_hdrs,
            json={"row_version": 1, "status": "EFFECTIVE", "findings_summary": "Verified clean."},
        ).status_code
        == 200
    )

    # 6. Maker-checker on close: create CAPA owned by qa_head_a (created by officer_a1) and verify qa_head_a cannot close own CAPA
    capa_owned_by_qa = api_client.post(
        "/api/v1/capas",
        headers=officer_a1_hdrs,
        json={
            "site_id": str(site_a1_id),
            "title": "QA Head owned CAPA",
            "owner_membership_id": str(qa_head_a["membership"].id),
        },
    ).json()
    assert (
        api_client.post(
            f"/api/v1/capas/{capa_owned_by_qa['id']}/open",
            headers=qms_a_hdrs,
            json={"row_version": 1},
        ).status_code
        == 200
    )
    act_q = api_client.post(
        f"/api/v1/capas/{capa_owned_by_qa['id']}/actions",
        headers=qa_head_a_hdrs,
        json={"title": "Update logbook"},
    ).json()
    api_client.post(
        f"/api/v1/capas/{capa_owned_by_qa['id']}/actions/{act_q['id']}/complete",
        headers=qa_head_a_hdrs,
        json={"row_version": 1, "implementation_notes": "Logbook updated."},
    )
    sub_q_resp = api_client.post(
        f"/api/v1/capas/{capa_owned_by_qa['id']}/submit-effectiveness",
        headers=qa_head_a_hdrs,
        json={"row_version": 3},
    )
    assert sub_q_resp.status_code == 200, sub_q_resp.text
    sub_q = sub_q_resp.json()
    chk_q = api_client.post(
        f"/api/v1/capas/{capa_owned_by_qa['id']}/effectiveness-checks",
        headers=qa_head_a_hdrs,
        json={"criteria_description": "Logbook audit clean", "planned_check_date": date.today().isoformat()},
    ).json()
    assert (
        api_client.post(
            f"/api/v1/capas/{capa_owned_by_qa['id']}/effectiveness-checks/{chk_q['id']}/record",
            headers=qms_a_hdrs,
            json={"row_version": 1, "status": "EFFECTIVE", "findings_summary": "Verified by QMS Head."},
        ).status_code
        == 200
    )

    mc_close_owner = api_client.post(
        f"/api/v1/capas/{capa_owned_by_qa['id']}/close",
        headers=qa_head_a_hdrs,
        json={"row_version": sub_q["row_version"], "closure_reason": "Self-close attempt"},
    )
    assert mc_close_owner.status_code == 403
    assert mc_close_owner.json()["error_code"] == "MAKER_CHECKER_VIOLATION"

    # SecurityEvents persisted for maker-checker & site-scope violations
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        mc_events = uow.session.scalars(
            select(SecurityEvent).where(
                SecurityEvent.organization_id == org_a_id,
                SecurityEvent.event_type == "MAKER_CHECKER_VIOLATION",
            )
        ).all()
        assert len(mc_events) >= 4
