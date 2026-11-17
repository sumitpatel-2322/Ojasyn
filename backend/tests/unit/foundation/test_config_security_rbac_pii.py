import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.core.exceptions import ConflictError, ValidationError
from app.core.pii_redactor import PIIRedactor
from app.core.security import create_access_token, decode_access_token, get_password_hash, verify_password
from app.domain.rbac_catalog import (
    CANONICAL_ROLES,
    QUALITY_SENSITIVE_ROLES,
    ROLE_DEFINITIONS_BY_CODE,
    WORKER_PERMISSIONS,
)
from app.domain.state_machines import assert_transition
from app.integrations.llm_provider import LLMProvider


class _DummyExtraction(BaseModel):
    product_name: str
    customer_name: str
    customer_contact: str
    summary: str


class _RecordingTransport:
    def __init__(self, response_json: str) -> None:
        self.calls = []
        self._response_json = response_json

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model_name: str,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
    ) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "model_name": model_name,
            }
        )
        return self._response_json


def test_settings_rejects_missing_or_default_jwt_secret() -> None:
    with pytest.raises(ValueError):
        Settings(
            DATABASE_URL="postgresql://user:pass@localhost:5432/db",
            JWT_SIGNING_KEY="",
            SECRET_KEY="",
        )

    with pytest.raises(ValueError):
        Settings(
            DATABASE_URL="postgresql://user:pass@localhost:5432/db",
            JWT_SIGNING_KEY="ojasyn-super-secret-development-key-change-in-production",
        )

    with pytest.raises(ValueError):
        Settings(
            DATABASE_URL="postgresql://user:pass@localhost:5432/db",
            JWT_SIGNING_KEY="short-key",
        )


def test_settings_normalizes_postgresql_scheme() -> None:
    s = Settings(
        DATABASE_URL="postgresql://user:pass@localhost:5432/qms_db",
        JWT_SIGNING_KEY="a_valid_random_secret_key_at_least_32_characters",
    )
    assert s.DATABASE_URL == "postgresql+psycopg://user:pass@localhost:5432/qms_db"
    assert s.DB_ECHO is False


def test_bcrypt_password_hashing_and_72_byte_guard() -> None:
    hashed = get_password_hash("CorrectHorseBatteryStaple!")
    assert verify_password("CorrectHorseBatteryStaple!", hashed) is True
    assert verify_password("WrongPassword", hashed) is False

    too_long = "A" * 73
    with pytest.raises(ValidationError):
        get_password_hash(too_long)
    assert verify_password(too_long, hashed) is False


def test_jwt_claims_and_validation() -> None:
    s = Settings(
        DATABASE_URL="postgresql://user:pass@localhost:5432/qms_db",
        JWT_SIGNING_KEY="a_valid_random_secret_key_at_least_32_characters",
    )
    token = create_access_token(subject="user-uuid-123", settings=s)
    payload = decode_access_token(token, settings=s)
    assert payload["sub"] == "user-uuid-123"
    assert payload["iss"] == "ojasyn-api"
    assert payload["aud"] == "ojasyn-clients"
    assert payload["typ"] == "access"
    assert "jti" in payload

    wrong_key = Settings(
        DATABASE_URL="postgresql://user:pass@localhost:5432/qms_db",
        JWT_SIGNING_KEY="another_valid_secret_key_with_32_plus_chars",
    )
    with pytest.raises(Exception):
        decode_access_token(token, settings=wrong_key)


def test_rbac_catalog_completeness_and_worker_restrictions() -> None:
    assert len(CANONICAL_ROLES) == 24  # 19 internal + 4 partner + CUSTOMER
    assert {"QMS_HEAD", "QA_HEAD", "QC_HEAD", "QC_ANALYST", "BATCH_DISPOSITION_APPROVER"} == QUALITY_SENSITIVE_ROLES
    assert "PLATFORM_SUPER_ADMIN" in ROLE_DEFINITIONS_BY_CODE
    # Workers must never hold human quality approval permissions
    forbidden_worker_perms = {
        "batch.release",
        "sop.approve",
        "qc_result.approve",
        "investigation.review",
        "capa.close",
        "complaint.close",
        "recall.initiate",
    }
    assert WORKER_PERMISSIONS.isdisjoint(forbidden_worker_perms)


def test_state_machine_assert_transition() -> None:
    transitions = {"DRAFT": {"SUBMITTED"}, "SUBMITTED": {"ACTIVE"}}
    assert_transition(transitions, "DRAFT", "SUBMITTED", entity_name="Organization")
    with pytest.raises(ConflictError):
        assert_transition(transitions, "DRAFT", "ACTIVE", entity_name="Organization")


def test_pii_redactor_and_llm_provider_never_send_raw_pii() -> None:
    raw_complaint = (
        "Customer Name: Ramesh Kulkarni\n"
        "Address: 42 MG Road, Pune 411001\n"
        "Email: ramesh.kulkarni@example.com\n"
        "Phone: +91-9820012345\n"
        "Issue: Amoxicillin 500mg tablets in batch BMX240602 are discolored."
    )
    transport = _RecordingTransport(
        response_json="""```json
        {
          "product_name": "Amoxicillin 500mg",
          "customer_name": "[PERSON_1]",
          "customer_contact": "[EMAIL_1] / [PHONE_1]",
          "summary": "Discolored tablets reported by [PERSON_1] at [ADDRESS_1]."
        }
        ```"""
    )
    settings = Settings(
        DATABASE_URL="postgresql://user:pass@localhost:5432/qms_db",
        JWT_SIGNING_KEY="a_valid_random_secret_key_at_least_32_characters",
    )
    provider = LLMProvider(transport=transport, redactor=PIIRedactor(), settings=settings)

    result = provider.generate_structured(
        system_prompt="Extract complaint fields as JSON.",
        untrusted_text=raw_complaint,
        response_model=_DummyExtraction,
    )

    assert len(transport.calls) == 1
    sent_prompt = transport.calls[0]["user_prompt"]
    assert "<untrusted_input>" in sent_prompt
    assert "</untrusted_input>" in sent_prompt
    assert "ramesh.kulkarni@example.com" not in sent_prompt
    assert "9820012345" not in sent_prompt
    assert "411001" not in sent_prompt
    assert "Ramesh Kulkarni" not in sent_prompt
    assert "42 MG Road, Pune" not in sent_prompt

    # Locally unmasked structured fields preserve original customer details
    assert result.customer_name == "Ramesh Kulkarni"
    assert result.customer_contact == "ramesh.kulkarni@example.com / +91-9820012345"
    assert "Ramesh Kulkarni" in result.summary
    assert "411001" in result.summary
