import json
from typing import Any, Dict, Iterable, Optional, Protocol, Type, TypeVar

from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.exceptions import ExternalProviderError
from app.core.pii_redactor import PIIRedactor

TModel = TypeVar("TModel", bound=BaseModel)


class LLMTransport(Protocol):
    """Protocol for swappable external LLM transports."""

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model_name: str,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
    ) -> str: ...


class GroqTransport:
    """Groq SDK implementation of LLMTransport."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ExternalProviderError("GROQ_API_KEY is not configured.")
        try:
            from groq import Groq
        except ImportError as exc:
            raise ExternalProviderError("groq package is not installed.") from exc
        self._client = Groq(api_key=api_key)

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
        try:
            response = self._client.chat.completions.create(
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout_seconds,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as exc:
            raise ExternalProviderError(f"Groq inference failed: {exc}") from exc


def _strip_json_fences(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


class LLMProvider:
    """
    Centralized LLM gateway enforcing:
    1. Reversible PII redaction before outbound LLM transmission.
    2. Untrusted-input delimiter isolation against prompt injection.
    3. Strict Pydantic v2 schema validation.
    4. Local unmasking of placeholder tokens into the returned structured DTO.
    """

    def __init__(
        self,
        *,
        transport: Optional[LLMTransport] = None,
        redactor: Optional[PIIRedactor] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._redactor = redactor or PIIRedactor()
        self._transport = transport

    def _get_transport(self) -> LLMTransport:
        if self._transport is None:
            self._transport = GroqTransport(api_key=self._settings.GROQ_API_KEY or "")
        return self._transport

    def generate_structured(
        self,
        *,
        system_prompt: str,
        untrusted_text: str,
        response_model: Type[TModel],
        known_pii: Optional[Dict[str, Iterable[str]]] = None,
        model_name: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> TModel:
        redaction = self._redactor.redact_text(untrusted_text, known_pii=known_pii)
        isolated_prompt = self._redactor.wrap_untrusted(redaction.masked_text)

        raw_response = self._get_transport().complete(
            system_prompt=system_prompt,
            user_prompt=isolated_prompt,
            model_name=model_name or self._settings.LLM_MODEL_NAME,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=self._settings.LLM_TIMEOUT_SECONDS,
        )

        try:
            parsed = json.loads(_strip_json_fences(raw_response))
            validated = response_model.model_validate(parsed)
        except Exception as exc:
            raise ExternalProviderError(f"LLM returned invalid structured output: {exc}") from exc

        unmasked_payload: Any = self._redactor.unmask_data(validated.model_dump(), redaction.mask_map)
        return response_model.model_validate(unmasked_payload)
