import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import UnitOfWork
from app.core.security import create_access_token
from app.integrations.storage_provider import LocalPrivateVaultAdapter
from app.models.domain.documents import DocumentVersion
from app.models.domain.governance import SecurityEvent
from app.models.domain.tenancy import OrganizationAddress, Site
from app.services.document_control_service import DocumentControlService
from tests.conftest import create_seeded_tenant_actor

MINIMAL_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 100]>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n0\n%%EOF\n"
)


@pytest.fixture()
def vault_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sqlite_db):
    vault_dir = tmp_path / "sec_vault"
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(vault_dir))
    get_settings.cache_clear()
    storage = LocalPrivateVaultAdapter(root_dir=vault_dir)
    monkeypatch.setattr("app.api.v1.documents_sops._doc_svc", DocumentControlService(storage=storage))
    yield {"vault_dir": vault_dir, "storage": storage}
    get_settings.cache_clear()


def test_upload_validation_spoofing_and_size_limit(
    api_client, sqlite_db, vault_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_factory = sqlite_db["session_factory"]
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        actor = create_seeded_tenant_actor(
            uow.session,
            org_name="Upload Sec Pharma",
            site_code="US-01",
            email="sec.doc@pharma.in",
            role_code="DOCUMENT_CONTROLLER",
            scope_type="ORGANIZATION",
        )
        headers = {
            "Authorization": f"Bearer {create_access_token(subject=str(actor['user'].id))}",
            "X-Organization-Id": str(actor["organization"].id),
        }

    # 1. Disallowed SVG / active HTML / PE executable blocked (422) + SecurityEvent logged
    for fname, body, ctype in (
        ("evil.svg", b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>', "image/svg+xml"),
        ("payload.html", b"<!DOCTYPE html><script>alert(1)</script>", "text/html"),
        ("malware.pdf", b"MZ\x90\x00\x03\x00\x00\x00\x04\x00", "application/pdf"),
    ):
        resp = api_client.post(
            "/api/v1/documents",
            headers=headers,
            files={"file": (fname, body, ctype)},
            data={"document_type": "OTHER", "title": fname},
        )
        assert resp.status_code == 422

    # 2. MIME spoofing: PDF magic bytes uploaded with claimed image/png -> 422 MIME_SPOOFING_DETECTED
    spoof = api_client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("fake.png", MINIMAL_PDF, "image/png")},
        data={"document_type": "OTHER", "title": "Spoofed MIME"},
    )
    assert spoof.status_code == 422
    assert spoof.json()["error_code"] == "MIME_SPOOFING_DETECTED"

    # 3. Exceeding UPLOAD_MAX_BYTES -> 413 PAYLOAD_TOO_LARGE
    monkeypatch.setenv("UPLOAD_MAX_BYTES", "1024")
    get_settings.cache_clear()
    too_big = api_client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("big.pdf", MINIMAL_PDF + (b"A" * 2048), "application/pdf")},
        data={"document_type": "SOP", "title": "Too Big"},
    )
    assert too_big.status_code == 413

    db = session_factory()
    try:
        sec_types = {e.event_type for e in db.scalars(select(SecurityEvent)).all()}
        assert "UPLOAD_DISALLOWED_CONTENT" in sec_types
        assert "UPLOAD_MIME_SPOOFING_DETECTED" in sec_types
    finally:
        db.close()


def test_cross_tenant_site_scope_quarantine_and_sha256_tamper_protection(
    api_client, sqlite_db, vault_env
) -> None:
    session_factory = sqlite_db["session_factory"]
    storage: LocalPrivateVaultAdapter = vault_env["storage"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        org_a_dc = create_seeded_tenant_actor(
            uow.session,
            org_name="Alpha Vault Org",
            site_code="AV-01",
            email="dc@alphavault.in",
            role_code="DOCUMENT_CONTROLLER",
            scope_type="ORGANIZATION",
        )
        org_b_dc = create_seeded_tenant_actor(
            uow.session,
            org_name="Beta Vault Org",
            site_code="BV-01",
            email="dc@betavault.in",
            role_code="DOCUMENT_CONTROLLER",
            scope_type="ORGANIZATION",
        )
        org_a_id = org_a_dc["organization"].id
        site_a1_id = org_a_dc["site"].id

        # Second site in Org A + site-restricted QA_OFFICER assigned only to site_a1
        addr2 = OrganizationAddress(
            organization_id=org_a_id,
            line_1="Plot 99",
            city="Baddi",
            postal_code="173205",
            country_code="IN",
            is_primary=False,
        )
        uow.session.add(addr2)
        uow.session.flush()
        site_a2 = Site(
            organization_id=org_a_id,
            site_code="AV-02",
            site_name="Baddi Unit 2",
            site_type="QC_LAB",
            address_id=addr2.id,
            status="ACTIVE",
        )
        uow.session.add(site_a2)
        uow.session.flush()
        site_a2_id = site_a2.id

        # Assign Org A Document Controller to site_a2 so they can upload for site_a2
        from app.models.domain.identity import (
            MembershipRoleAssignment,
            OrganizationMembership,
            Role,
            SiteAssignment,
            User,
        )

        uow.session.add(
            SiteAssignment(
                organization_id=org_a_id,
                membership_id=org_a_dc["membership"].id,
                site_id=site_a2_id,
                status="ACTIVE",
                assigned_by_user_id=org_a_dc["user"].id,
            )
        )
        uow.session.flush()
        from app.core.security import get_password_hash
        from datetime import datetime, timezone

        qa_user = User(
            email="qa.site1@alphavault.in",
            password_hash=get_password_hash("QaSitePass!123"),
            full_name="Site 1 QA",
            account_status="ACTIVE",
            email_verified_at=datetime.now(timezone.utc),
        )
        uow.session.add(qa_user)
        uow.session.flush()
        qa_member = OrganizationMembership(
            user_id=qa_user.id,
            organization_id=org_a_id,
            membership_status="ACTIVE",
            member_type="EMPLOYEE",
        )
        uow.session.add(qa_member)
        uow.session.flush()
        uow.session.add(
            SiteAssignment(
                organization_id=org_a_id,
                membership_id=qa_member.id,
                site_id=site_a1_id,
                status="ACTIVE",
                assigned_by_user_id=org_a_dc["user"].id,
            )
        )
        qa_role = uow.session.scalar(select(Role).where(Role.role_code == "QA_OFFICER"))
        assert qa_role is not None
        uow.session.add(
            MembershipRoleAssignment(
                organization_id=org_a_id,
                membership_id=qa_member.id,
                role_id=qa_role.id,
                scope_type="SITE",
                site_id=site_a1_id,
                status="ACTIVE",
                granted_by_user_id=org_a_dc["user"].id,
            )
        )
        uow.session.flush()
        qa_site1_user_id = qa_user.id
        org_b_id = org_b_dc["organization"].id
        org_a_dc_uid = org_a_dc["user"].id
        org_b_dc_uid = org_b_dc["user"].id

    a_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(org_a_dc_uid))}",
        "X-Organization-Id": str(org_a_id),
    }
    b_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(org_b_dc_uid))}",
        "X-Organization-Id": str(org_b_id),
    }
    qa1_headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(qa_site1_user_id))}",
        "X-Organization-Id": str(org_a_id),
    }

    # Upload document scoped to site_a2
    up = api_client.post(
        "/api/v1/documents",
        headers=a_headers,
        files={"file": ("baddi-qc.pdf", MINIMAL_PDF, "application/pdf")},
        data={"document_type": "QC_RAW_DATA", "title": "Baddi QC Spec", "site_id": str(site_a2_id)},
    )
    assert up.status_code == 201
    doc_id = up.json()["id"]
    ver_id = up.json()["versions"][0]["id"]

    # Cross-tenant Org B access returns 404 (no existence leakage)
    assert api_client.get(f"/api/v1/documents/{doc_id}", headers=b_headers).status_code == 404
    assert api_client.get(f"/api/v1/document-versions/{ver_id}/download", headers=b_headers).status_code == 404
    assert api_client.post(f"/api/v1/document-versions/{ver_id}/extract", headers=b_headers).status_code == 404

    # Same-org user restricted to site_a1 cannot download/view site_a2 doc -> 403
    assert api_client.get(f"/api/v1/documents/{doc_id}", headers=qa1_headers).status_code == 403
    assert api_client.get(f"/api/v1/document-versions/{ver_id}/download", headers=qa1_headers).status_code == 403

    # Quarantined version blocks download (403 DOCUMENT_NOT_CLEAN)
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        ver_row = uow.session.get(DocumentVersion, uuid.UUID(ver_id))
        assert ver_row is not None
        ver_row.scan_status = "QUARANTINED"
        obj_key = ver_row.object_key

    assert api_client.get(f"/api/v1/document-versions/{ver_id}/download", headers=a_headers).status_code == 403

    # Restore CLEAN, then tamper bytes on disk -> SHA-256 check fails cleanly (500 + CRITICAL SecurityEvent)
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        ver_row = uow.session.get(DocumentVersion, uuid.UUID(ver_id))
        assert ver_row is not None
        ver_row.scan_status = "CLEAN"

    storage.put_object(obj_key, MINIMAL_PDF + b"TAMPERED", content_type="application/pdf")
    tamper_resp = api_client.get(f"/api/v1/document-versions/{ver_id}/download", headers=a_headers)
    assert tamper_resp.status_code == 500
    assert tamper_resp.json()["error_code"] == "DOCUMENT_INTEGRITY_ERROR"

    db = session_factory()
    try:
        sec_types = {e.event_type for e in db.scalars(select(SecurityEvent)).all()}
        assert "QUARANTINED_DOCUMENT_DOWNLOAD_BLOCKED" in sec_types
        assert "DOCUMENT_CHECKSUM_TAMPER_DETECTED" in sec_types
        assert "DOCUMENT_SITE_ACCESS_DENIED" in sec_types
    finally:
        db.close()
