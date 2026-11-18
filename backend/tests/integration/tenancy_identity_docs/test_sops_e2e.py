from datetime import datetime, timezone

from sqlalchemy import select

import app.models.domain.sops  # noqa: F401
from app.core.database import UnitOfWork
from app.core.security import create_access_token, get_password_hash
from app.models.domain.governance import ApprovalRecord, OutboxEvent
from app.models.domain.identity import (
    MembershipRoleAssignment,
    OrganizationMembership,
    Role,
    SiteAssignment,
    User,
)
from tests.conftest import create_seeded_tenant_actor


def test_sop_lifecycle_immutability_supersession_and_historical_resolution_e2e(
    api_client, sqlite_db
) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        doc_ctrl = create_seeded_tenant_actor(
            uow.session,
            org_name="Helios Pharma Ltd",
            site_code="HEL-S1",
            email="docctrl@helios.in",
            role_code="DOCUMENT_CONTROLLER",
            scope_type="SITE",
        )
        org_id = doc_ctrl["organization"].id
        site_id = doc_ctrl["site"].id
        doc_ctrl_user_id = doc_ctrl["user"].id

        # Second actor (QA_HEAD) in same org & site for maker-checker approval
        qa_user = User(
            email="qahead@helios.in",
            password_hash=get_password_hash("QaHeadPass!123"),
            full_name="QA Head Approver",
            account_status="ACTIVE",
            email_verified_at=datetime.now(timezone.utc),
        )
        uow.session.add(qa_user)
        uow.session.flush()
        qa_member = OrganizationMembership(
            user_id=qa_user.id,
            organization_id=org_id,
            membership_status="ACTIVE",
            member_type="EMPLOYEE",
        )
        uow.session.add(qa_member)
        uow.session.flush()
        uow.session.add(
            SiteAssignment(
                organization_id=org_id,
                membership_id=qa_member.id,
                site_id=site_id,
                status="ACTIVE",
                assigned_by_user_id=doc_ctrl_user_id,
            )
        )
        qa_role = uow.session.scalar(select(Role).where(Role.role_code == "QA_HEAD"))
        assert qa_role is not None
        uow.session.add(
            MembershipRoleAssignment(
                organization_id=org_id,
                membership_id=qa_member.id,
                role_id=qa_role.id,
                scope_type="SITE",
                site_id=site_id,
                status="ACTIVE",
                granted_by_user_id=doc_ctrl_user_id,
            )
        )
        qa_user_id = qa_user.id

    author_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(doc_ctrl_user_id))}",
        "X-Organization-Id": str(org_id),
    }
    approver_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(qa_user_id))}",
        "X-Organization-Id": str(org_id),
    }

    # 1. Create SOP master
    sop_resp = api_client.post(
        "/api/v1/sops",
        headers=author_headers,
        json={
            "site_id": str(site_id),
            "sop_code": "SOP-MIX-100",
            "title": "High-Shear Blending and Granulation",
            "description": "Controlled mixing and temperature procedure",
            "category": "MANUFACTURING",
        },
    )
    assert sop_resp.status_code == 201, sop_resp.text
    sop = sop_resp.json()
    sop_id = sop["id"]
    assert sop["sop_status"] == "DRAFT"

    # 1b. Update SOP title with explicit row_version and verify stale row_version 409
    stale_sop_patch = api_client.patch(
        f"/api/v1/sops/{sop_id}",
        headers=author_headers,
        json={"row_version": 99, "title": "Stale Title"},
    )
    assert stale_sop_patch.status_code == 409
    assert stale_sop_patch.json()["error_code"] == "OPTIMISTIC_LOCK_CONFLICT"

    ok_sop_patch = api_client.patch(
        f"/api/v1/sops/{sop_id}",
        headers=author_headers,
        json={
            "row_version": sop["row_version"],
            "title": "High-Shear Blending and Granulation",
        },
    )
    assert ok_sop_patch.status_code == 200, ok_sop_patch.text
    assert ok_sop_patch.json()["row_version"] == sop["row_version"] + 1

    # 2. Create Revision 1.0 with inline Step 1
    rev1_resp = api_client.post(
        f"/api/v1/sops/{sop_id}/revisions",
        headers=author_headers,
        json={
            "revision_number": "1.0",
            "revision_description": "Initial controlled release",
            "steps": [
                {
                    "step_code": "STEP-01",
                    "step_sequence": 1,
                    "process_stage": "BLENDING",
                    "title": "Blending Temperature Control",
                    "instruction": "Maintain blend temperature between 20.0 and 25.0 C",
                    "is_mandatory": True,
                    "parameter_name": "Blend Temperature",
                    "parameter_code": "BLEND_TEMP",
                    "expected_data_type": "NUMERIC",
                    "unit_code": "C",
                    "min_value": "20.000000",
                    "max_value": "25.000000",
                    "target_value": "22.500000",
                    "requires_operator_signoff": True,
                    "requires_checker_signoff": True,
                }
            ],
        },
    )
    assert rev1_resp.status_code == 201, rev1_resp.text
    rev1 = rev1_resp.json()
    rev1_id = rev1["id"]
    assert rev1["revision_status"] == "DRAFT"
    assert len(rev1["steps"]) == 1

    # 3. Add Step 2 and update it while revision is still in DRAFT
    step2_resp = api_client.post(
        f"/api/v1/sops/{sop_id}/revisions/{rev1_id}/steps",
        headers=author_headers,
        json={
            "step_code": "STEP-02",
            "step_sequence": 2,
            "process_stage": "BLENDING",
            "title": "Impeller Speed Check",
            "instruction": "Run impeller at 100 to 150 RPM",
            "parameter_name": "Impeller Speed",
            "parameter_code": "IMPELLER_RPM",
            "expected_data_type": "NUMERIC",
            "unit_code": "RPM",
            "min_value": "100.000000",
            "max_value": "150.000000",
        },
    )
    assert step2_resp.status_code == 201, step2_resp.text
    step2_id = step2_resp.json()["id"]

    patch_step_resp = api_client.patch(
        f"/api/v1/sops/{sop_id}/revisions/{rev1_id}/steps/{step2_id}",
        headers=author_headers,
        json={"row_version": step2_resp.json()["row_version"], "max_value": "140.000000"},
    )
    assert patch_step_resp.status_code == 200, patch_step_resp.text
    assert patch_step_resp.json()["max_value"] == "140.000000"
    assert patch_step_resp.json()["row_version"] == step2_resp.json()["row_version"] + 1

    # 4. Resolve while only DRAFT revision exists -> PENDING_APPROVAL_ONLY
    res_draft = api_client.get(
        "/api/v1/sops/resolve",
        headers=author_headers,
        params={
            "sop_code": "SOP-MIX-100",
            "site_id": str(site_id),
            "at": "2026-02-15T10:00:00Z",
        },
    )
    assert res_draft.status_code == 200, res_draft.text
    assert res_draft.json()["outcome"] == "PENDING_APPROVAL_ONLY"

    # 5. Stale row_version rejected, then submit Rev 1.0 with explicit row_version -> DRAFT -> REVIEW
    stale_submit = api_client.post(
        f"/api/v1/sops/{sop_id}/revisions/{rev1_id}/submit-review",
        headers=author_headers,
        json={"row_version": 99, "comments": "Stale"},
    )
    assert stale_submit.status_code == 409
    assert stale_submit.json()["error_code"] == "OPTIMISTIC_LOCK_CONFLICT"

    submit_resp = api_client.post(
        f"/api/v1/sops/{sop_id}/revisions/{rev1_id}/submit-review",
        headers=author_headers,
        json={"row_version": rev1["row_version"], "comments": "Ready for QA review"},
    )
    assert submit_resp.status_code == 200, submit_resp.text
    assert submit_resp.json()["revision_status"] == "REVIEW"
    assert submit_resp.json()["row_version"] == rev1["row_version"] + 1

    # 6. Immutability check: adding or modifying a step after leaving DRAFT returns 409
    imm_add = api_client.post(
        f"/api/v1/sops/{sop_id}/revisions/{rev1_id}/steps",
        headers=author_headers,
        json={
            "step_code": "STEP-03",
            "step_sequence": 3,
            "title": "Forbidden Step",
            "instruction": "Should fail",
        },
    )
    assert imm_add.status_code == 409
    assert imm_add.json()["error_code"] == "SOP_REVISION_IMMUTABLE"

    imm_patch = api_client.patch(
        f"/api/v1/sops/{sop_id}/revisions/{rev1_id}/steps/{step2_id}",
        headers=author_headers,
        json={"max_value": "160.000000"},
    )
    assert imm_patch.status_code == 409
    assert imm_patch.json()["error_code"] == "SOP_REVISION_IMMUTABLE"

    # 7. Approve Rev 1.0 as QA_HEAD with explicit row_version -> transitions REVIEW -> APPROVED
    approve_resp = api_client.post(
        f"/api/v1/sops/{sop_id}/revisions/{rev1_id}/approve",
        headers=approver_headers,
        json={
            "row_version": submit_resp.json()["row_version"],
            "decision": "APPROVED",
            "comments": "Validated against MBR limits",
        },
    )
    assert approve_resp.status_code == 200, approve_resp.text
    assert approve_resp.json()["revision_status"] == "APPROVED"
    assert approve_resp.json()["approval_record_id"] is not None

    # 8. Activate Rev 1.0 with explicit row_version effective 2026-01-01T00:00:00Z (open-ended)
    act1_resp = api_client.post(
        f"/api/v1/sops/{sop_id}/revisions/{rev1_id}/activate",
        headers=approver_headers,
        json={
            "row_version": approve_resp.json()["row_version"],
            "effective_from": "2026-01-01T00:00:00Z",
        },
    )
    assert act1_resp.status_code == 200, act1_resp.text
    assert act1_resp.json()["revision_status"] == "EFFECTIVE"

    sop_after_act1 = api_client.get(
        f"/api/v1/sops/{sop_id}", headers=author_headers
    ).json()
    assert sop_after_act1["sop_status"] == "ACTIVE"
    assert sop_after_act1["current_revision_id"] == rev1_id

    # 9. Create Rev 2.0 (tightened temperature max_value = 23.0 C), submit & approve
    rev2_resp = api_client.post(
        f"/api/v1/sops/{sop_id}/revisions",
        headers=author_headers,
        json={
            "revision_number": "2.0",
            "change_reason": "Tighten blend temperature upper limit to 23.0 C",
            "supersedes_revision_id": rev1_id,
            "steps": [
                {
                    "step_code": "STEP-01",
                    "step_sequence": 1,
                    "process_stage": "BLENDING",
                    "title": "Blending Temperature Control v2",
                    "instruction": "Maintain blend temperature between 20.0 and 23.0 C",
                    "is_mandatory": True,
                    "parameter_name": "Blend Temperature",
                    "parameter_code": "BLEND_TEMP",
                    "expected_data_type": "NUMERIC",
                    "unit_code": "C",
                    "min_value": "20.000000",
                    "max_value": "23.000000",
                    "target_value": "21.500000",
                    "requires_operator_signoff": True,
                    "requires_checker_signoff": True,
                }
            ],
        },
    )
    assert rev2_resp.status_code == 201, rev2_resp.text
    rev2_id = rev2_resp.json()["id"]

    assert (
        api_client.post(
            f"/api/v1/sops/{sop_id}/revisions/{rev2_id}/submit-review",
            headers=author_headers,
        ).status_code
        == 200
    )
    assert (
        api_client.post(
            f"/api/v1/sops/{sop_id}/revisions/{rev2_id}/approve",
            headers=approver_headers,
            json={"decision": "APPROVED"},
        ).status_code
        == 200
    )

    # 10. Overlap detection: attempting to activate Rev 2.0 with effective_from <= Rev 1.0 effective_from fails with 409
    overlap_resp = api_client.post(
        f"/api/v1/sops/{sop_id}/revisions/{rev2_id}/activate",
        headers=approver_headers,
        json={"effective_from": "2025-12-15T00:00:00Z"},
    )
    assert overlap_resp.status_code == 409
    assert overlap_resp.json()["error_code"] == "OVERLAPPING_SOP_REVISION_WINDOW"

    # 11. Valid supersession: activating Rev 2.0 at 2026-06-01T00:00:00Z closes Rev 1.0 effective_to and marks Rev 1.0 SUPERSEDED
    act2_resp = api_client.post(
        f"/api/v1/sops/{sop_id}/revisions/{rev2_id}/activate",
        headers=approver_headers,
        json={"effective_from": "2026-06-01T00:00:00Z"},
    )
    assert act2_resp.status_code == 200, act2_resp.text
    assert act2_resp.json()["revision_status"] == "EFFECTIVE"
    assert act2_resp.json()["supersedes_revision_id"] == rev1_id

    rev1_after = api_client.get(
        f"/api/v1/sops/{sop_id}/revisions/{rev1_id}",
        headers=author_headers,
    ).json()
    assert rev1_after["revision_status"] == "SUPERSEDED"
    assert rev1_after["superseded_by_revision_id"] == rev2_id
    assert rev1_after["effective_to"] is not None

    # 12. Historical resolution via GET /api/v1/sops/resolve:
    # At 2026-03-10 -> resolves historical Rev 1.0 (max_value 25.0), NEVER defaulting to latest Rev 2.0!
    hist_res_v1 = api_client.get(
        "/api/v1/sops/resolve",
        headers=author_headers,
        params={
            "sop_code": "SOP-MIX-100",
            "site_id": str(site_id),
            "at": "2026-03-10T14:30:00Z",
        },
    )
    assert hist_res_v1.status_code == 200, hist_res_v1.text
    body_v1 = hist_res_v1.json()
    assert body_v1["outcome"] == "RESOLVED"
    assert body_v1["revision_number"] == "1.0"
    assert body_v1["sop_revision_id"] == rev1_id
    assert body_v1["steps"][0]["max_value"] == "25.000000"

    # At 2026-07-10 -> resolves Rev 2.0 (max_value 23.0)
    hist_res_v2 = api_client.get(
        "/api/v1/sops/resolve",
        headers=author_headers,
        params={
            "sop_code": "SOP-MIX-100",
            "site_id": str(site_id),
            "at": "2026-07-10T09:00:00Z",
        },
    )
    assert hist_res_v2.status_code == 200, hist_res_v2.text
    body_v2 = hist_res_v2.json()
    assert body_v2["outcome"] == "RESOLVED"
    assert body_v2["revision_number"] == "2.0"
    assert body_v2["sop_revision_id"] == rev2_id
    assert body_v2["steps"][0]["max_value"] == "23.000000"

    # Verify ApprovalRecord and OutboxEvents persisted
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        approvals = list(
            uow.session.scalars(
                select(ApprovalRecord).where(
                    ApprovalRecord.organization_id == org_id,
                    ApprovalRecord.entity_type == "SOPRevision",
                )
            ).all()
        )
        assert len(approvals) == 2
        outbox_types = set(
            uow.session.scalars(
                select(OutboxEvent.event_type).where(
                    OutboxEvent.organization_id == org_id
                )
            ).all()
        )
        assert {
            "SOPCreated",
            "SOPRevisionApproved",
            "SOPRevisionActivated",
        }.issubset(outbox_types)
