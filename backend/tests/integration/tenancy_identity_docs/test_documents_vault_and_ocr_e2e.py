from decimal import Decimal
from pathlib import Path
from typing import List

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import UnitOfWork
from app.core.security import create_access_token
from app.integrations.ocr_provider import ExtractedPageResult, OCRProvider
from app.integrations.storage_provider import LocalPrivateVaultAdapter
from app.models.domain.documents import Document, DocumentVersion, ExtractionJob
from app.models.domain.governance import AuditEvent, OutboxEvent
from app.services.document_control_service import DocumentControlService
from app.worker import OutboxWorker
from tests.conftest import create_seeded_tenant_actor

MINIMAL_PDF_V1 = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 100]>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n0\n%%EOF\n"
)
MINIMAL_PDF_V2 = MINIMAL_PDF_V1 + b"% revision 2.0\n"


class _StubOCR(OCRProvider):
    extractor_name = "stub_ocr"
    extractor_version = "2.1"

    def extract(self, file_bytes: bytes, *, mime_type: str, filename: str) -> List[ExtractedPageResult]:
        return [
            ExtractedPageResult(
                page_number=1,
                text=f"Extracted text for {filename} ({len(file_bytes)} bytes)",
                section_ref="page_1",
                structured_data_json={"detected_mime": mime_type},
                extraction_confidence=Decimal("0.9500"),
            )
        ]


@pytest.fixture()
def isolated_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sqlite_db):
    vault_dir = tmp_path / "private_vault"
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(vault_dir))
    get_settings.cache_clear()
    storage = LocalPrivateVaultAdapter(root_dir=vault_dir)
    monkeypatch.setattr("app.api.v1.documents_sops._doc_svc", DocumentControlService(storage=storage, ocr=_StubOCR()))
    yield {"vault_dir": vault_dir, "storage": storage}
    get_settings.cache_clear()


def test_document_upload_versioning_download_and_ocr_extraction_e2e(
    api_client, sqlite_db, isolated_vault
) -> None:
    session_factory = sqlite_db["session_factory"]
    storage: LocalPrivateVaultAdapter = isolated_vault["storage"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        actor = create_seeded_tenant_actor(
            uow.session,
            org_name="Vault Pharma",
            site_code="VP-01",
            email="docctrl@vaultpharma.in",
            role_code="DOCUMENT_CONTROLLER",
            scope_type="ORGANIZATION",
        )
        org_id = actor["organization"].id
        site_id = actor["site"].id
        user_id = actor["user"].id

    headers = {
        "Authorization": f"Bearer {create_access_token(subject=str(user_id))}",
        "X-Organization-Id": str(org_id),
    }

    # 1. Upload SOP v1 via multipart/form-data
    up = api_client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("SOP-QA-101.pdf", MINIMAL_PDF_V1, "application/pdf")},
        data={
            "document_type": "SOP",
            "title": "Controlled Complaint Intake SOP",
            "document_code": "SOP-QA-101",
            "site_id": str(site_id),
            "confidentiality": "INTERNAL",
            "metadata_json": '{"sop_category": "QA", "effective_cycle": 1}',
        },
    )
    assert up.status_code == 201
    doc = up.json()
    doc_id = doc["id"]
    v1_id = doc["versions"][0]["id"]
    assert doc["document_code"] == "SOP-QA-101"
    assert doc["metadata_json"]["sop_category"] == "QA"
    assert doc["versions"][0]["version_number"] == 1
    assert doc["versions"][0]["scan_status"] == "CLEAN"

    # Binary stored inside private vault using UUID object key
    db = session_factory()
    try:
        import uuid as _uuid

        v1_row = db.get(DocumentVersion, _uuid.UUID(v1_id))
        assert v1_row is not None
        assert storage.exists(v1_row.object_key) is True
    finally:
        db.close()

    # 2. List & Detail
    listed = api_client.get("/api/v1/documents?document_type=SOP&search=Complaint", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    detail = api_client.get(f"/api/v1/documents/{doc_id}", headers=headers)
    assert detail.status_code == 200
    assert len(detail.json()["versions"]) == 1

    # 3. Upload v2 -> supersedes v1
    v2_resp = api_client.post(
        f"/api/v1/documents/{doc_id}/versions",
        headers=headers,
        files={"file": ("SOP-QA-101-r2.pdf", MINIMAL_PDF_V2, "application/pdf")},
        data={"version_label": "2.0"},
    )
    assert v2_resp.status_code == 201
    v2 = v2_resp.json()
    v2_id = v2["id"]
    assert v2["version_number"] == 2
    assert v2["supersedes_version_id"] == v1_id

    vers = api_client.get(f"/api/v1/documents/{doc_id}/versions", headers=headers)
    assert vers.status_code == 200
    assert [v["version_number"] for v in vers.json()] == [1, 2]

    # 4. Download v2 -> returns exact bytes + security headers + download audit log
    dl = api_client.get(f"/api/v1/document-versions/{v2_id}/download", headers=headers)
    assert dl.status_code == 200
    assert dl.content == MINIMAL_PDF_V2
    assert dl.headers["X-Content-Type-Options"] == "nosniff"
    assert dl.headers["Cache-Control"] == "no-store"

    # 5. Trigger Extraction Job -> OutboxWorker runs OCR outside DB tx -> COMPLETED
    ext = api_client.post(f"/api/v1/document-versions/{v2_id}/extract", headers=headers)
    assert ext.status_code == 202
    job_id = ext.json()["id"]
    assert ext.json()["status"] == "QUEUED"

    worker = OutboxWorker(session_factory=session_factory)
    doc_svc_worker = DocumentControlService(storage=storage, ocr=_StubOCR())
    worker.register_external_handler("DocumentExtractionRequested", doc_svc_worker.run_extraction_job)
    processed = worker.run_once()
    assert processed == 1

    job_resp = api_client.get(f"/api/v1/extraction-jobs/{job_id}", headers=headers)
    assert job_resp.status_code == 200
    job_data = job_resp.json()
    assert job_data["status"] == "COMPLETED"
    assert len(job_data["extracted_contents"]) == 1
    assert "Extracted text for SOP-QA-101-r2.pdf" in job_data["extracted_contents"][0]["text"]

    db = session_factory()
    try:
        dl_audits = db.scalars(
            select(AuditEvent).where(AuditEvent.action == "DOCUMENT_VERSION_DOWNLOADED")
        ).all()
        assert len(dl_audits) == 1
    finally:
        db.close()


def test_upload_in_tx_and_assign_organization_in_tx_rollback_cleanup(sqlite_db, isolated_vault) -> None:
    session_factory = sqlite_db["session_factory"]
    storage: LocalPrivateVaultAdapter = isolated_vault["storage"]
    svc = DocumentControlService(storage=storage, ocr=_StubOCR())

    # 1. Create unrouted attachment inside UnitOfWork, then assign_organization_in_tx later
    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        actor = create_seeded_tenant_actor(
            uow.session,
            org_name="Routed Org",
            site_code="RO-01",
            email="route@vault.in",
            role_code="ORG_ADMIN",
        )
        target_org_id = actor["organization"].id
        user_id = actor["user"].id

        doc = svc.upload_in_tx(
            uow.session,
            organization_id=None,
            uploaded_by_user_id=user_id,
            file_bytes=MINIMAL_PDF_V1,
            original_filename="unrouted.pdf",
            claimed_mime_type="application/pdf",
            category="COMPLAINT_EVIDENCE",
        )
        doc_id = doc.id
        obj_key = doc.versions[0].object_key
        assert doc.organization_id is None
        assert storage.exists(obj_key) is True

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        updated = svc.assign_organization_in_tx(uow.session, doc_id, target_org_id)
        assert updated.organization_id == target_org_id
        # Idempotent same-org call succeeds
        assert svc.assign_organization_in_tx(uow.session, doc_id, target_org_id).organization_id == target_org_id
