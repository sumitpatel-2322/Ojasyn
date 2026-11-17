import io
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.core.exceptions import ExternalProviderError


@dataclass(frozen=True)
class ExtractedPageResult:
    page_number: int
    text: str
    section_ref: Optional[str] = None
    structured_data_json: Optional[Dict[str, Any]] = None
    extraction_confidence: Optional[Decimal] = None


class OCRProvider(ABC):
    """Abstract document text & table extraction provider executed outside DB transactions."""

    extractor_name: str = "base"
    extractor_version: str = "1.0"

    @abstractmethod
    def extract(self, file_bytes: bytes, *, mime_type: str, filename: str) -> List[ExtractedPageResult]:
        """Extract ordered page-level text/metadata from raw document bytes."""


class PdfPlumberTextOCRAdapter(OCRProvider):
    """PDF text/table extractor powered by `pdfplumber` (with pytesseract/image support hook)."""

    extractor_name = "pdfplumber"
    extractor_version = "1.0"

    def extract(self, file_bytes: bytes, *, mime_type: str, filename: str) -> List[ExtractedPageResult]:
        if mime_type == "application/pdf":
            try:
                import pdfplumber
            except ImportError as exc:
                raise ExternalProviderError(
                    "pdfplumber is required for PDF extraction in this environment."
                ) from exc

            pages: List[ExtractedPageResult] = []
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                for idx, page in enumerate(pdf.pages, start=1):
                    text = (page.extract_text() or "").strip()
                    tables = page.extract_tables() or []
                    pages.append(
                        ExtractedPageResult(
                            page_number=idx,
                            text=text,
                            section_ref=f"page_{idx}",
                            structured_data_json={"tables": tables} if tables else None,
                            extraction_confidence=Decimal("0.9800") if text else Decimal("0.5000"),
                        )
                    )
            return pages or [ExtractedPageResult(page_number=1, text="", section_ref="page_1")]

        if mime_type.startswith("image/"):
            try:
                import pytesseract
                from PIL import Image
            except ImportError as exc:
                raise ExternalProviderError(
                    "pytesseract and pillow are required for image OCR extraction."
                ) from exc
            img = Image.open(io.BytesIO(file_bytes))
            text = (pytesseract.image_to_string(img) or "").strip()
            return [
                ExtractedPageResult(
                    page_number=1,
                    text=text,
                    section_ref="page_1",
                    extraction_confidence=Decimal("0.8500") if text else Decimal("0.4000"),
                )
            ]

        raise ExternalProviderError(f"Unsupported MIME type for OCR extraction: '{mime_type}'.")
