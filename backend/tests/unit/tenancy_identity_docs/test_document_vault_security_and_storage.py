import sys
import types
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.exceptions import ExternalProviderError, PermissionDeniedError, ValidationError
from app.integrations.ocr_provider import PdfPlumberTextOCRAdapter
from app.integrations.storage_provider import LocalPrivateVaultAdapter, build_vault_object_key
from app.services.document_control_service import _detect_verified_mime, sanitize_display_filename

MINIMAL_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 100]>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n0\n%%EOF\n"
)
MINIMAL_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"


def test_local_vault_prevents_path_traversal_and_absolute_keys(tmp_path: Path) -> None:
    vault = LocalPrivateVaultAdapter(root_dir=tmp_path)
    import uuid

    key = build_vault_object_key(None, uuid.uuid4(), uuid.uuid4())
    assert key.startswith("tenants/unrouted/documents/")
    vault.put_object(key, b"payload", content_type="application/pdf")
    assert vault.exists(key) is True
    assert vault.get_object(key) == b"payload"

    for bad_key in (
        "../outside.txt",
        "tenants/../../escape",
        "/abs/path/file",
        "tenants\\win\\backslash",
        "bad\x00key",
    ):
        with pytest.raises((PermissionDeniedError, ValidationError)):
            vault.put_object(bad_key, b"x", content_type="application/pdf")


def test_filename_sanitization_and_magic_mime_detection() -> None:
    assert sanitize_display_filename("../../evil\\sub/..hidden_SOP#1.pdf") == "hidden_SOP_1.pdf"
    with pytest.raises(ValidationError):
        sanitize_display_filename("bad\x00name.pdf")

    assert _detect_verified_mime(MINIMAL_PDF) == "application/pdf"
    assert _detect_verified_mime(MINIMAL_PNG) == "image/png"
    assert _detect_verified_mime(b"<svg onload=alert(1)></svg>") == "image/svg+xml"
    assert _detect_verified_mime(b"<!DOCTYPE html><script>alert(1)</script>") == "text/html"
    assert _detect_verified_mime(b"MZ\x90\x00\x03\x00") == "application/x-executable"


def test_pdfplumber_ocr_adapter_with_mocked_library(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = PdfPlumberTextOCRAdapter()

    # Clean ExternalProviderError when pdfplumber is missing
    monkeypatch.setitem(sys.modules, "pdfplumber", None)
    with pytest.raises(ExternalProviderError):
        adapter.extract(MINIMAL_PDF, mime_type="application/pdf", filename="sop.pdf")

    class _FakePage:
        def __init__(self, txt: str, tables):
            self._txt = txt
            self._tables = tables

        def extract_text(self):
            return self._txt

        def extract_tables(self):
            return self._tables

    class _FakePDF:
        pages = [_FakePage("SOP-001 Mixing Instructions", [[["Step", "Temp"], ["1", "25C"]]])]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    fake_mod = types.SimpleNamespace(open=lambda _bio: _FakePDF())
    monkeypatch.setitem(sys.modules, "pdfplumber", fake_mod)

    pages = adapter.extract(MINIMAL_PDF, mime_type="application/pdf", filename="sop.pdf")
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert "SOP-001" in pages[0].text
    assert pages[0].structured_data_json == {"tables": [[["Step", "Temp"], ["1", "25C"]]]}
    assert pages[0].extraction_confidence == Decimal("0.9800")
