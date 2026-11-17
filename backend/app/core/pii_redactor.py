import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple


_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,5}\)?[\s.-]?)?\d{3,5}[\s.-]?\d{4,6}(?!\w)"
)
_PINCODE_RE = re.compile(r"\b[1-9][0-9]{5}\b")
_LABELED_PERSON_RE = re.compile(
    r"(?im)^(\s*(?:customer\s*name|customer|reporter|complainant|patient\s*name|patient|contact\s*person|regards|signed\s*by)\s*[:\-]\s*)([^\r\n,;]+)"
)
_LABELED_ADDRESS_RE = re.compile(
    r"(?im)^(\s*(?:address|postal\s*address|pharmacy\s*address|delivery\s*address|location)\s*[:\-]\s*)([^\r\n]+)"
)


@dataclass
class RedactionResult:
    """Holds masked text and the reversible token-to-value lookup map."""

    masked_text: str
    mask_map: Dict[str, str] = field(default_factory=dict)
    extracted_entities: Dict[str, List[str]] = field(default_factory=dict)


class PIIRedactor:
    """
    Deterministic pre-LLM PII scrubber with reversible placeholder masking.
    Prevents raw emails, phone numbers, postal PIN codes, and labeled person/address
    spans from leaving the trust boundary while preserving local UI/field extraction.
    """

    def redact_text(
        self,
        text: str,
        *,
        known_pii: Optional[Dict[str, Iterable[str]]] = None,
    ) -> RedactionResult:
        if not text:
            return RedactionResult(masked_text="")

        mask_map: Dict[str, str] = {}
        reverse_map: Dict[str, str] = {}
        counters: Dict[str, int] = {}
        extracted: Dict[str, List[str]] = {
            "EMAIL": [],
            "PHONE": [],
            "PINCODE": [],
            "PERSON": [],
            "ADDRESS": [],
        }

        def register_token(category: str, raw_value: str) -> str:
            cleaned = raw_value.strip()
            if not cleaned:
                return raw_value
            if cleaned in reverse_map:
                return reverse_map[cleaned]
            idx = counters.get(category, 0) + 1
            counters[category] = idx
            token = f"[{category}_{idx}]"
            mask_map[token] = cleaned
            reverse_map[cleaned] = token
            extracted.setdefault(category, []).append(cleaned)
            return token

        working = text

        # 1. Explicit known PII hints (highest priority, e.g., known reporter/employee names & addresses)
        if known_pii:
            category_map = {
                "person": "PERSON",
                "persons": "PERSON",
                "name": "PERSON",
                "email": "EMAIL",
                "emails": "EMAIL",
                "phone": "PHONE",
                "phones": "PHONE",
                "address": "ADDRESS",
                "addresses": "ADDRESS",
                "pincode": "PINCODE",
            }
            for raw_cat, values in known_pii.items():
                cat = category_map.get(raw_cat.lower(), raw_cat.upper())
                for val in sorted((v for v in values if v and v.strip()), key=len, reverse=True):
                    token = register_token(cat, val)
                    working = re.sub(re.escape(val.strip()), token, working, flags=re.IGNORECASE)

        # 2. Emails first (so digits inside emails are never misclassified as phones/pincodes)
        def _replace_email(match: re.Match) -> str:
            return register_token("EMAIL", match.group(0))

        working = _EMAIL_RE.sub(_replace_email, working)

        # 3. Labeled person & address lines
        def _replace_labeled_person(match: re.Match) -> str:
            prefix, val = match.group(1), match.group(2)
            # Do not mask if already tokenized
            if val.strip().startswith("[") and val.strip().endswith("]"):
                return match.group(0)
            return f"{prefix}{register_token('PERSON', val)}"

        working = _LABELED_PERSON_RE.sub(_replace_labeled_person, working)

        def _replace_labeled_address(match: re.Match) -> str:
            prefix, val = match.group(1), match.group(2)
            if val.strip().startswith("[") and val.strip().endswith("]"):
                return match.group(0)
            return f"{prefix}{register_token('ADDRESS', val)}"

        working = _LABELED_ADDRESS_RE.sub(_replace_labeled_address, working)

        # 4. Phone numbers (require >= 8 digits total so batch/lot short codes are untouched)
        def _replace_phone(match: re.Match) -> str:
            raw = match.group(0)
            digits = re.sub(r"\D", "", raw)
            if len(digits) < 8 or len(digits) > 15:
                return raw
            return register_token("PHONE", raw)

        working = _PHONE_RE.sub(_replace_phone, working)

        # 5. 6-digit postal PIN codes (only when not attached to an alphanumeric token/batch)
        def _replace_pincode(match: re.Match) -> str:
            return register_token("PINCODE", match.group(0))

        working = _PINCODE_RE.sub(_replace_pincode, working)

        return RedactionResult(
            masked_text=working,
            mask_map=mask_map,
            extracted_entities={k: v for k, v in extracted.items() if v},
        )

    @staticmethod
    def wrap_untrusted(text: str) -> str:
        """Isolates untrusted complaint/OCR text against prompt injection."""
        safe = (text or "").replace("</untrusted_input>", "&lt;/untrusted_input&gt;")
        return f"<untrusted_input>\n{safe}\n</untrusted_input>"

    @staticmethod
    def unmask_text(text: str, mask_map: Dict[str, str]) -> str:
        if not text or not mask_map:
            return text
        result = text
        for token, original in mask_map.items():
            result = result.replace(token, original)
        return result

    @classmethod
    def unmask_data(cls, data: Any, mask_map: Dict[str, str]) -> Any:
        if not mask_map:
            return data
        if isinstance(data, str):
            return cls.unmask_text(data, mask_map)
        if isinstance(data, list):
            return [cls.unmask_data(item, mask_map) for item in data]
        if isinstance(data, tuple):
            return tuple(cls.unmask_data(item, mask_map) for item in data)
        if isinstance(data, dict):
            return {k: cls.unmask_data(v, mask_map) for k, v in data.items()}
        return data
