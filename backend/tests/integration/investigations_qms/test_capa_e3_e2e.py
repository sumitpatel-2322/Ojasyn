import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Dict

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import UnitOfWork
from app.core.security import create_access_token, get_password_hash
from app.domain.enums import (
    AssignmentStatus,
    AssignmentType,
    ComplaintSource,
    ComplaintStatus,
    MembershipStatus,
    MemberType,
    RoleAssignmentScopeType,
    RoleAssignmentStatus,
    UserAccountStatus,
)
from app.integrations.storage_provider import LocalPrivateVaultAdapter
from app.models.domain.actors import Complainant
from app.models.domain.complaints import Complaint
from app.models.domain.documents import DocumentVersion
from app.models.domain.governance import ApprovalRecord, AuditEvent, Notification
from app.models.domain.identity import (
    MembershipRoleAssignment,
    OrganizationMembership,
    Role,
    SiteAssignment,
    User,
)
from app.models.domain.investigations import Investigation
from app.models.domain.qms_loop import CAPA, CAPAAction, CAPAEffectivenessCheck, Deviation
from app.repositories.qms_repo import QMSRepository
from app.services.document_control_service import DocumentControlService
from app.worker import OutboxWorker
from tests.conftest import create_seeded_tenant_actor


def _headers(user: User, org_id: uuid.UUID) -> Dict[str, str]:
    token = create_access_token(subject=str(user.id))
    return {"Authorization": f"Bearer {token}", "X-Organization-Id": str(org_id)}


def _add_user(db, *, org_id: uuid.UUID, site_id: uuid.UUID, email: str, role_code: str) -> Dict[str, object]:
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
            scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
            site_id=None,
            granted_by_user_id=user.id,
            approved_by_user_id=user.id,
            status=RoleAssignmentStatus.ACTIVE.value,
        )
    )
    db.flush()
    return {"user": user, "membership": mem}


def test_capa_e3_full_lifecycle_ineffective_rework_evidence_and_closure_guard(
    api_client, sqlite_db, tmp_path, monkeypatch
) -> None:
    session_factory = sqlite_db["session_factory"]
    vault_dir = tmp_path / "vault"
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(vault_dir))
    get_settings.cache_clear()

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        db = uow.session
        seed = create_seeded_tenant_actor(
            db,
            org_name="Apex Lifesciences",
            site_code="APX-01",
            email="officer@apex.in",
            role_code="QA_OFFICER",
            scope_type=RoleAssignmentScopeType.ORGANIZATION.value,
        )
        org = seed["organization"]
        site = seed["site"]
        officer = seed["user"]
        officer_mem = seed["membership"]

        qa_head_info = _add_user(
            db, org_id=org.id, site_id=site.id, email="qahead@apex.in", role_code="QA_HEAD"
        )
        qms_head_info = _add_user(
            db, org_id=org.id, site_id=site.id, email="qmshead@apex.in", role_code="QMS_HEAD"
        )
        qa_head = qa_head_info["user"]
        qms_head = qms_head_info["user"]

        complainant = Complainant(
            organization_id=org.id,
            full_name="Dr. Meera Nair",
            email="meera@clinic.in",
            complainant_type="CUSTOMER",
        )
        db.add(complainant)
        db.flush()

        complaint = Complaint(
            organization_id=org.id,
            site_id=site.id,
            complaint_number="CMP-2026-0010",
            complainant_id=complainant.id,
            source_channel=ComplaintSource.STAFF.value,
            status=ComplaintStatus.INVESTIGATION_OPEN.value,
            reported_product_name="ApexCef 500",
            description="Blister foil delamination reported by pharmacy.",
            created_by_user_id=officer.id,
        )
        db.add(complaint)
        db.flush()

        inv = Investigation(
            organization_id=org.id,
            site_id=site.id,
            complaint_id=complaint.id,
            investigation_number="INV-2026-0010",
            trigger_type="COMPLAINT",
            title="Blister sealing temperature investigation",
            status="APPROVED",
            created_by_user_id=officer.id,
        )
        db.add(inv)
        db.flush()

        dev = Deviation(
            organization_id=org.id,
            site_id=site.id,
            complaint_id=None,
            investigation_id=inv.id,
            deviation_number="DEV-2026-0010",
            source_type="INVESTIGATION",
            classification="MAJOR",
            title="Sealing jaw heater drift",
            description="Sealing roller drift below minimum temperature.",
            status="OPEN",
            created_by_user_id=officer.id,
        )
        db.add(dev)
        db.flush()

        doc_svc = DocumentControlService(storage=LocalPrivateVaultAdapter(root_dir=vault_dir))
        ev_doc = doc_svc.upload_in_tx(
            db,
            organization_id=org.id,
            uploaded_by_user_id=officer.id,
            file_bytes=b"%PDF-1.4\n%evidence\n",
            original_filename="calibration_cert.pdf",
            claimed_mime_type="application/pdf",
            category="OTHER",
            site_id=site.id,
        )
        ev_ver = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == ev_doc.id))
        assert ev_ver is not None

        org_id = org.id
        site_id = site.id
        complaint_id = complaint.id
        inv_id = inv.id
        dev_id = dev.id
        ev_doc_id = ev_doc.id
        ev_ver_id = ev_ver.id
        officer_mem_id = officer_mem.id

    officer_hdrs = _headers(officer, org_id)
    qa_head_hdrs = _headers(qa_head, org_id)
    qms_head_hdrs = _headers(qms_head, org_id)

    # 1. Create CAPA linked to Deviation (which is linked to Investigation -> Complaint)
    create_resp = api_client.post(
        "/api/v1/capas",
        headers=officer_hdrs,
        json={
            "site_id": str(site_id),
            "title": "Recalibrate blister sealing PID controller and update PM schedule",
            "description": "Address heater drift root cause.",
            "capa_type": "BOTH",
            "source_type": "DEVIATION",
            "deviation_id": str(dev_id),
            "is_mandatory_for_complaint_closure": True,
            "severity": "HIGH",
            "owner_membership_id": str(officer_mem_id),
            "root_cause_summary": "Thermocouple drift caused intermittent under-heating.",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    capa = create_resp.json()
    capa_id = capa["id"]
    assert capa["status"] == "DRAFT"
    assert "-CAPA-" in capa["capa_number"]

    # Also create a non-mandatory CAPA directly on complaint (should not block closure guard)
    non_mand_resp = api_client.post(
        "/api/v1/capas",
        headers=officer_hdrs,
        json={
            "title": "Optional operator refresher",
            "capa_type": "PREVENTIVE",
            "source_type": "COMPLAINT",
            "complaint_id": str(complaint_id),
            "is_mandatory_for_complaint_closure": False,
            "severity": "LOW",
        },
    )
    assert non_mand_resp.status_code == 201

    # Verify list_open_mandatory_capas_for_complaint sees mandatory CAPA via deviation -> inv -> complaint
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        open_mand = QMSRepository().list_open_mandatory_capas_for_complaint(
            uow.session, org_id, complaint_id
        )
        assert [str(c.id) for c in open_mand] == [capa_id]

    # 2. Approve / open CAPA by QA Head (distinct from creator & owner)
    open_resp = api_client.post(
        f"/api/v1/capas/{capa_id}/open",
        headers=qa_head_hdrs,
        json={"row_version": 1, "comments": "CAPA plan approved."},
    )
    assert open_resp.status_code == 200, open_resp.text
    assert open_resp.json()["status"] == "OPEN"
    assert open_resp.json()["approved_by_user_id"] == str(qa_head.id)

    # 3. Add 2 actions -> CAPA auto-advances OPEN -> ACTIONS_IN_PROGRESS
    act1_resp = api_client.post(
        f"/api/v1/capas/{capa_id}/actions",
        headers=officer_hdrs,
        json={"action_type": "CORRECTIVE", "title": "Replace thermocouple and recalibrate PID"},
    )
    assert act1_resp.status_code == 201
    act1 = act1_resp.json()
    assert act1["action_seq"] == 1

    act2_resp = api_client.post(
        f"/api/v1/capas/{capa_id}/actions",
        headers=officer_hdrs,
        json={"action_type": "PREVENTIVE", "title": "Add weekly calibration verification"},
    )
    assert act2_resp.status_code == 201
    act2 = act2_resp.json()
    assert act2["action_seq"] == 2

    detail = api_client.get(f"/api/v1/capas/{capa_id}", headers=officer_hdrs).json()
    assert detail["status"] == "ACTIONS_IN_PROGRESS"
    capa_rv = detail["row_version"]

    # 4. Complete action 1 with evidence doc; try submitting effectiveness while action 2 still PENDING -> 409
    comp1 = api_client.post(
        f"/api/v1/capas/{capa_id}/actions/{act1['id']}/complete",
        headers=officer_hdrs,
        json={
            "row_version": 1,
            "implementation_notes": "Thermocouple replaced and calibrated within +/- 0.5 C.",
            "evidence_document_id": str(ev_doc_id),
            "evidence_document_version_id": str(ev_ver_id),
        },
    )
    assert comp1.status_code == 200
    assert comp1.json()["status"] == "COMPLETED"
    assert comp1.json()["evidence_document_id"] == str(ev_doc_id)

    early_sub = api_client.post(
        f"/api/v1/capas/{capa_id}/submit-effectiveness",
        headers=officer_hdrs,
        json={"row_version": capa_rv, "comments": "Trying early submit"},
    )
    assert early_sub.status_code == 409
    assert early_sub.json()["error_code"] == "CAPA_ACTIONS_INCOMPLETE"

    # Complete action 2
    comp2 = api_client.post(
        f"/api/v1/capas/{capa_id}/actions/{act2['id']}/complete",
        headers=officer_hdrs,
        json={"row_version": 1, "implementation_notes": "Weekly check added to preventive maintenance SOP."},
    )
    assert comp2.status_code == 200

    # Stale row_version on submit-effectiveness -> 409
    stale_sub = api_client.post(
        f"/api/v1/capas/{capa_id}/submit-effectiveness",
        headers=officer_hdrs,
        json={"row_version": 99, "comments": "Stale"},
    )
    assert stale_sub.status_code == 409

    # Submit for effectiveness review
    sub_resp = api_client.post(
        f"/api/v1/capas/{capa_id}/submit-effectiveness",
        headers=officer_hdrs,
        json={"row_version": capa_rv, "comments": "All actions complete."},
    )
    assert sub_resp.status_code == 200
    assert sub_resp.json()["status"] == "EFFECTIVENESS_REVIEW"
    capa_rv = sub_resp.json()["row_version"]

    # 5. Create effectiveness check #1 and record INEFFECTIVE -> bounces CAPA back to ACTIONS_IN_PROGRESS
    chk1_resp = api_client.post(
        f"/api/v1/capas/{capa_id}/effectiveness-checks",
        headers=officer_hdrs,
        json={
            "criteria_description": "Zero seal failures across 3 consecutive packaging runs.",
            "evaluation_method": "Line audit & peel-strength sampling",
            "planned_check_date": date.today().isoformat(),
        },
    )
    assert chk1_resp.status_code == 201
    chk1 = chk1_resp.json()
    assert chk1["check_seq"] == 1

    rec1 = api_client.post(
        f"/api/v1/capas/{capa_id}/effectiveness-checks/{chk1['id']}/record",
        headers=qa_head_hdrs,
        json={
            "row_version": 1,
            "status": "INEFFECTIVE",
            "findings_summary": "Batch run #2 showed minor edge lift; additional jaw alignment needed.",
        },
    )
    assert rec1.status_code == 200
    assert rec1.json()["status"] == "INEFFECTIVE"

    after_ineff = api_client.get(f"/api/v1/capas/{capa_id}", headers=officer_hdrs).json()
    assert after_ineff["status"] == "ACTIONS_IN_PROGRESS"
    capa_rv = after_ineff["row_version"]

    # Cannot close while in ACTIONS_IN_PROGRESS
    close_blk1 = api_client.post(
        f"/api/v1/capas/{capa_id}/close",
        headers=qms_head_hdrs,
        json={"row_version": capa_rv, "closure_reason": "Trying to close after ineffective"},
    )
    assert close_blk1.status_code == 409

    # 6. Add & complete remediation action #3, re-submit, record EFFECTIVE check #2, then close CAPA!
    act3 = api_client.post(
        f"/api/v1/capas/{capa_id}/actions",
        headers=officer_hdrs,
        json={"action_type": "CORRECTIVE", "title": "Realign sealing jaw pressure"},
    ).json()
    assert act3["action_seq"] == 3
    api_client.post(
        f"/api/v1/capas/{capa_id}/actions/{act3['id']}/complete",
        headers=officer_hdrs,
        json={"row_version": 1, "implementation_notes": "Jaw pressure balanced across full web width."},
    )
    sub2 = api_client.post(
        f"/api/v1/capas/{capa_id}/submit-effectiveness",
        headers=officer_hdrs,
        json={"row_version": capa_rv, "comments": "Remediation complete."},
    )
    assert sub2.status_code == 200
    capa_rv = sub2.json()["row_version"]

    # Cannot close without a new EFFECTIVE latest check (latest check is still INEFFECTIVE)
    close_blk2 = api_client.post(
        f"/api/v1/capas/{capa_id}/close",
        headers=qms_head_hdrs,
        json={"row_version": capa_rv, "closure_reason": "No second check yet"},
    )
    assert close_blk2.status_code == 409
    assert close_blk2.json()["error_code"] == "EFFECTIVENESS_NOT_VERIFIED"

    chk2 = api_client.post(
        f"/api/v1/capas/{capa_id}/effectiveness-checks",
        headers=officer_hdrs,
        json={
            "criteria_description": "Verify 3 consecutive runs pass peel strength.",
            "planned_check_date": date.today().isoformat(),
        },
    ).json()
    assert chk2["check_seq"] == 2

    rec2 = api_client.post(
        f"/api/v1/capas/{capa_id}/effectiveness-checks/{chk2['id']}/record",
        headers=qa_head_hdrs,
        json={
            "row_version": 1,
            "status": "EFFECTIVE",
            "findings_summary": "All 3 consecutive runs passed peel-strength and visual inspection.",
            "evidence_document_id": str(ev_doc_id),
        },
    )
    assert rec2.status_code == 200
    assert rec2.json()["status"] == "EFFECTIVE"

    close_ok = api_client.post(
        f"/api/v1/capas/{capa_id}/close",
        headers=qms_head_hdrs,
        json={"row_version": capa_rv, "closure_reason": "CAPA verified effective and closed by QMS Head."},
    )
    assert close_ok.status_code == 200, close_ok.text
    assert close_ok.json()["status"] == "CLOSED"
    assert close_ok.json()["closed_at"] is not None

    # Verify ApprovalRecords and closure guard now clears!
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        open_after = QMSRepository().list_open_mandatory_capas_for_complaint(
            uow.session, org_id, complaint_id
        )
        assert open_after == []

        approvals = uow.session.scalars(
            select(ApprovalRecord).where(
                ApprovalRecord.organization_id == org_id,
                ApprovalRecord.entity_id.in_([uuid.UUID(capa_id), uuid.UUID(chk1["id"]), uuid.UUID(chk2["id"])]),
            )
        ).all()
        assert len(approvals) == 4  # open + check1 + check2 + close


def test_capa_overdue_detection_and_worker_notification_idempotent(api_client, sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        db = uow.session
        seed = create_seeded_tenant_actor(
            db,
            org_name="Overdue Pharma",
            site_code="OVD-01",
            email="officer@overdue.in",
            role_code="QA_OFFICER",
            scope_type=RoleAssignmentScopeType.SITE.value,
        )
        org = seed["organization"]
        site = seed["site"]
        officer = seed["user"]
        officer_mem = seed["membership"]

        qa_head_info = _add_user(
            db, org_id=org.id, site_id=site.id, email="qahead@overdue.in", role_code="QA_HEAD"
        )
        qa_head = qa_head_info["user"]
        qa_head_mem = qa_head_info["membership"]

        org_id = org.id
        site_id = site.id
        officer_mem_id = officer_mem.id
        qa_head_mem_id = qa_head_mem.id

    officer_hdrs = _headers(officer, org_id)
    past_due = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    future_due = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()

    overdue_capa = api_client.post(
        "/api/v1/capas",
        headers=officer_hdrs,
        json={
            "site_id": str(site_id),
            "title": "Overdue CAPA test",
            "owner_membership_id": str(officer_mem_id),
            "due_at": past_due,
        },
    ).json()
    api_client.post(
        "/api/v1/capas",
        headers=officer_hdrs,
        json={
            "site_id": str(site_id),
            "title": "On-track CAPA test",
            "owner_membership_id": str(officer_mem_id),
            "due_at": future_due,
        },
    )

    chk = api_client.post("/api/v1/capas/check-overdue", headers=officer_hdrs)
    assert chk.status_code == 202, chk.text
    body = chk.json()
    assert body["enqueued_count"] == 1
    assert body["overdue_capa_ids"] == [overdue_capa["id"]]

    worker = OutboxWorker(session_factory=session_factory)
    processed = worker.run_once()
    assert processed >= 1

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        capa_row = uow.session.get(CAPA, uuid.UUID(overdue_capa["id"]))
        assert capa_row is not None
        assert capa_row.overdue_notified_at is not None

        notifs = uow.session.scalars(
            select(Notification).where(
                Notification.organization_id == org_id,
                Notification.event_type == "CAPAOverdueNotified",
                Notification.aggregate_id == capa_row.id,
            )
        ).all()
        assert {n.recipient_membership_id for n in notifs} == {officer_mem_id, qa_head_mem_id}

        audits = uow.session.scalars(
            select(AuditEvent).where(
                AuditEvent.organization_id == org_id,
                AuditEvent.action == "CAPA_OVERDUE_NOTIFIED",
                AuditEvent.entity_id == capa_row.id,
            )
        ).all()
        assert len(audits) == 1

    # Second scan returns 0 because overdue_notified_at is already set
    chk2 = api_client.post("/api/v1/capas/check-overdue", headers=officer_hdrs)
    assert chk2.status_code == 202
    assert chk2.json()["enqueued_count"] == 0
