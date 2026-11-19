import uuid
from datetime import date
from decimal import Decimal

from app.core.database import UnitOfWork
from app.core.security import create_access_token
from app.domain.enums import (
    AssignmentStatus,
    AssignmentType,
    BatchStatus,
    MembershipStatus,
    MemberType,
    OrganizationStatus,
    OrganizationType,
    RoleAssignmentScopeType,
    RoleAssignmentStatus,
    SiteStatus,
    SiteType,
    UserAccountStatus,
)
from app.models.domain.complaints import Complaint
from app.models.domain.identity import (
    MembershipRoleAssignment,
    OrganizationMembership,
    Role,
    SiteAssignment,
    User,
)
from app.models.domain.investigations import (
    EvidenceReference,
    GeneratedReport,
    Investigation,
    RootCauseHypothesis,
)
from app.models.domain.products_materials import Product
from app.models.domain.qms_loop import Deviation
from app.models.domain.tenancy import Organization, OrganizationAddress, Site
from app.models.domain.traceability import Batch
from app.services.report_service import ReportService
from app.models.schemas.investigations import ReportGenerateRequest
from tests.conftest import create_seeded_tenant_actor


def _headers(user: User, org_id: uuid.UUID) -> dict:
    token = create_access_token(subject=str(user.id))
    return {
        "Authorization": f"Bearer {token}",
        "X-Organization-Id": str(org_id),
    }


def _add_org_user(
    db,
    *,
    org: Organization,
    site: Site,
    email: str,
    role_code: str,
    scope_type: str = RoleAssignmentScopeType.ORGANIZATION.value,
    assign_site: bool = True,
) -> dict:
    from app.core.security import get_password_hash

    user = User(
        email=email,
        full_name=f"User {email}",
        password_hash=get_password_hash("StrongPass!123"),
        account_status=UserAccountStatus.ACTIVE.value,
    )
    db.add(user)
    db.flush()

    membership = OrganizationMembership(
        user_id=user.id,
        organization_id=org.id,
        member_type=MemberType.EMPLOYEE.value,
        membership_status=MembershipStatus.ACTIVE.value,
        employee_code=f"EMP-{uuid.uuid4().hex[:6]}",
    )
    db.add(membership)
    db.flush()

    if assign_site:
        db.add(
            SiteAssignment(
                organization_id=org.id,
                membership_id=membership.id,
                site_id=site.id,
                assignment_type=AssignmentType.PRIMARY.value,
                status=AssignmentStatus.ACTIVE.value,
                assigned_by_user_id=user.id,
            )
        )

    role = db.query(Role).filter(Role.role_code == role_code).one()
    db.add(
        MembershipRoleAssignment(
            organization_id=org.id,
            membership_id=membership.id,
            role_id=role.id,
            scope_type=scope_type,
            site_id=site.id if scope_type == RoleAssignmentScopeType.SITE.value else None,
            granted_by_user_id=user.id,
            approved_by_user_id=user.id,
            status=RoleAssignmentStatus.ACTIVE.value,
        )
    )
    db.flush()
    return {"user": user, "membership": membership}


def test_cross_tenant_and_site_scope_isolation_and_rbac_and_maker_checker(api_client, sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        db = uow.session
        org_a_seed = create_seeded_tenant_actor(
            db,
            org_name="Alpha Pharma",
            site_code="AL-01",
            email="qms.alpha@example.com",
            role_code="QMS_HEAD",
            scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
        )
        org_b_seed = create_seeded_tenant_actor(
            db,
            org_name="Beta Pharma",
            site_code="BT-01",
            email="qms.beta@example.com",
            role_code="QMS_HEAD",
            scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
        )
        org_a = org_a_seed["organization"]
        site_a1 = org_a_seed["site"]

        # Create Site A2 in Org A for site-scope tests
        addr2 = OrganizationAddress(
            organization_id=org_a.id,
            line_1="Plot 2",
            city="Hyderabad",
            postal_code="500001",
            country_code="IN",
            is_primary=False,
        )
        db.add(addr2)
        db.flush()
        site_a2 = Site(
            organization_id=org_a.id,
            site_code="AL-02",
            site_name="Alpha Site 2",
            site_type=SiteType.FDF.value,
            address_id=addr2.id,
            status=SiteStatus.ACTIVE.value,
            is_primary_site=False,
        )
        db.add(site_a2)
        db.flush()

        qa_officer_a1 = _add_org_user(
            db,
            org=org_a,
            site=site_a1,
            email="officer.a1@example.com",
            role_code="QA_OFFICER",
            scope_type=RoleAssignmentScopeType.SITE.value,
        )
        qa_head_a = _add_org_user(
            db,
            org=org_a,
            site=site_a1,
            email="qahead.a@example.com",
            role_code="QA_HEAD",
            scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
        )
        auditor_a = _add_org_user(
            db,
            org=org_a,
            site=site_a1,
            email="auditor.a@example.com",
            role_code="AUDITOR_READ_ONLY",
            scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
        )

        # Create investigation & deviation in Site A2
        inv_a2 = Investigation(
            organization_id=org_a.id,
            site_id=site_a2.id,
            investigation_number="INV-A2-001",
            trigger_type="MANUAL",
            title="Site A2 Investigation",
            status="COMPLETED",
            created_by_user_id=org_a_seed["user"].id,
        )
        db.add(inv_a2)
        db.flush()

        ev_a2 = EvidenceReference(
            organization_id=org_a.id,
            investigation_id=inv_a2.id,
            source_type="OTHER",
            source_table="investigations",
            source_record_id=inv_a2.id,
            quoted_value="Site A2 evidence",
        )
        db.add(ev_a2)
        db.flush()

        hyp_a2 = RootCauseHypothesis(
            organization_id=org_a.id,
            investigation_id=inv_a2.id,
            hypothesis_code="HYP-A2-1",
            category="SOP_OR_PROCEDURE_GAP",
            title="Hyp A2",
            description="Created by qa_head_a for maker-checker test.",
            supporting_evidence_ids_json=[str(ev_a2.id)],
            uncertainty_level="LOW",
            status="PROPOSED",
            created_by_user_id=qa_head_a["user"].id,
        )
        db.add(hyp_a2)
        db.flush()

        dev_a2 = Deviation(
            organization_id=org_a.id,
            site_id=site_a2.id,
            deviation_number="DEV-A2-001",
            source_type="MANUAL",
            classification="MAJOR",
            title="Site A2 Deviation",
            description="Deviation on Site A2",
            status="OPEN",
            created_by_user_id=qa_officer_a1["user"].id,
        )
        db.add(dev_a2)
        db.flush()

        # Create report on inv_a2 generated by qa_head_a (to test maker-checker when qa_head_a tries to review it!)
        from app.core.deps import TenantContext

        qa_head_ctx = TenantContext(
            user_id=qa_head_a["user"].id,
            organization_id=org_a.id,
            membership_id=qa_head_a["membership"].id,
            allowed_site_ids=frozenset({site_a1.id, site_a2.id}),
            is_org_wide=True,
            permission_codes=frozenset({"report.generate", "report.review"}),
        )
        org_a_id = org_a.id
        org_b_id = org_b_seed["organization"].id
        site_a1_id = site_a1.id
        site_a2_id = site_a2.id
        inv_a2_id = inv_a2.id
        hyp_a2_id = hyp_a2.id
        dev_a2_id = dev_a2.id

        officer_hdrs = _headers(qa_officer_a1["user"], org_a_id)
        qa_head_hdrs = _headers(qa_head_a["user"], org_a_id)
        qms_a_hdrs = _headers(org_a_seed["user"], org_a_id)
        auditor_hdrs = _headers(auditor_a["user"], org_a_id)
        beta_hdrs = _headers(org_b_seed["user"], org_b_id)

    # Generate report via API as qa_head_a
    rep_resp = api_client.post(
        f"/api/v1/investigations/{inv_a2_id}/reports",
        headers=qa_head_hdrs,
        json={"title": "Site A2 Report"},
    )
    assert rep_resp.status_code == 201
    rep_id = rep_resp.json()["id"]

    # 1. Cross-tenant isolation -> 404 everywhere from Org B
    assert api_client.get(f"/api/v1/investigations/{inv_a2_id}", headers=beta_hdrs).status_code == 404
    assert api_client.get(f"/api/v1/investigations/{inv_a2_id}/evidence", headers=beta_hdrs).status_code == 404
    assert api_client.get(f"/api/v1/investigations/{inv_a2_id}/compliance-checks", headers=beta_hdrs).status_code == 404
    assert api_client.post(f"/api/v1/investigations/{inv_a2_id}/runs", headers=beta_hdrs, json={}).status_code == 404
    assert api_client.get(f"/api/v1/reports/{rep_id}/download", headers=beta_hdrs).status_code == 404
    assert api_client.post(
        f"/api/v1/reports/{rep_id}/review",
        headers=beta_hdrs,
        json={"row_version": 1, "decision": "APPROVED", "comments": "Cross tenant"},
    ).status_code == 404
    assert api_client.post(
        f"/api/v1/hypotheses/{hyp_a2_id}/decision",
        headers=beta_hdrs,
        json={"row_version": 1, "decision": "ACCEPTED", "comments": "Cross tenant"},
    ).status_code == 404
    assert api_client.get(f"/api/v1/deviations/{dev_a2_id}", headers=beta_hdrs).status_code == 404

    # 2. Site-scope isolation -> 403 for site-A1-scoped QA_OFFICER accessing Site A2
    assert api_client.get(f"/api/v1/investigations/{inv_a2_id}", headers=officer_hdrs).status_code == 403
    assert api_client.get(f"/api/v1/reports/{rep_id}/download", headers=officer_hdrs).status_code == 403
    assert api_client.get(f"/api/v1/deviations/{dev_a2_id}", headers=officer_hdrs).status_code == 403
    assert api_client.post(
        "/api/v1/investigations",
        headers=officer_hdrs,
        json={"site_id": str(site_a2_id), "title": "Unauthorized site inv"},
    ).status_code == 403

    # 3. RBAC enforcement -> 403 when QA_OFFICER lacks review/close permissions or AUDITOR tries mutations
    assert api_client.post(
        f"/api/v1/investigations/{inv_a2_id}/review",
        headers=officer_hdrs,
        json={"row_version": 1, "decision": "APPROVED", "comments": "Officer cannot approve"},
    ).status_code == 403
    assert api_client.post(
        f"/api/v1/deviations/{dev_a2_id}/close",
        headers=officer_hdrs,
        json={"row_version": 1, "decision": "APPROVED", "closure_comments": "Officer cannot close"},
    ).status_code == 403
    assert api_client.post(
        "/api/v1/investigations",
        headers=auditor_hdrs,
        json={"site_id": str(site_a1_id), "title": "Auditor cannot open"},
    ).status_code == 403
    assert api_client.post(
        "/api/v1/deviations",
        headers=auditor_hdrs,
        json={"site_id": str(site_a1_id), "title": "Auditor Dev", "description": "Cannot create"},
    ).status_code == 403

    # 4. Dual user_id maker-checker -> 403 MAKER_CHECKER_VIOLATION
    # Report generated by qa_head_a cannot be reviewed by qa_head_a
    mc_rep = api_client.post(
        f"/api/v1/reports/{rep_id}/review",
        headers=qa_head_hdrs,
        json={"row_version": 1, "decision": "APPROVED", "comments": "Self approval blocked"},
    )
    assert mc_rep.status_code == 403
    assert mc_rep.json()["error_code"] == "MAKER_CHECKER_VIOLATION"

    # Hypothesis created by qa_head_a cannot be decided by qa_head_a
    mc_hyp = api_client.post(
        f"/api/v1/hypotheses/{hyp_a2_id}/decision",
        headers=qa_head_hdrs,
        json={"row_version": 1, "decision": "ACCEPTED", "comments": "Self approval blocked"},
    )
    assert mc_hyp.status_code == 403
    assert mc_hyp.json()["error_code"] == "MAKER_CHECKER_VIOLATION"

    # Investigation created by qms_a cannot be reviewed by qms_a
    mc_inv = api_client.post(
        f"/api/v1/investigations/{inv_a2_id}/review",
        headers=qms_a_hdrs,
        json={"row_version": 1, "decision": "APPROVED", "comments": "Self approval blocked"},
    )
    assert mc_inv.status_code == 403
    assert mc_inv.json()["error_code"] == "MAKER_CHECKER_VIOLATION"

    # Deviation created on Site A1 by qa_head_a, submitted by qms_a, cannot be closed by qa_head_a OR qms_a (both maker/submitter!)
    dev_a1_resp = api_client.post(
        "/api/v1/deviations",
        headers=qa_head_hdrs,
        json={
            "site_id": str(site_a1_id),
            "title": "Maker Checker Dev",
            "description": "Created by QA Head.",
        },
    )
    assert dev_a1_resp.status_code == 201
    dev_a1_id = dev_a1_resp.json()["id"]
    sub_resp = api_client.post(
        f"/api/v1/deviations/{dev_a1_id}/submit-review",
        headers=qms_a_hdrs,
        json={"row_version": 1, "comments": "Submitted by QMS Head"},
    )
    assert sub_resp.status_code == 200
    # Creator (qa_head_a) blocked from closing
    mc_dev_creator = api_client.post(
        f"/api/v1/deviations/{dev_a1_id}/close",
        headers=qa_head_hdrs,
        json={"row_version": 2, "decision": "APPROVED", "closure_comments": "Creator close blocked"},
    )
    assert mc_dev_creator.status_code == 403
    assert mc_dev_creator.json()["error_code"] == "MAKER_CHECKER_VIOLATION"
    # Submitter (qms_a) also blocked from closing
    mc_dev_submitter = api_client.post(
        f"/api/v1/deviations/{dev_a1_id}/close",
        headers=qms_a_hdrs,
        json={"row_version": 2, "decision": "APPROVED", "closure_comments": "Submitter close blocked"},
    )
    assert mc_dev_submitter.status_code == 403
    assert mc_dev_submitter.json()["error_code"] == "MAKER_CHECKER_VIOLATION"
