from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorEnvelope(BaseModel):
    """Standardized API error payload."""

    error_code: str
    message: str
    details: Optional[Any] = None
    correlation_id: str


class Page(BaseModel, Generic[T]):
    """Standardized paginated list response."""

    items: List[T]
    total: int
    page: int
    page_size: int


class AuditReasonRequest(BaseModel):
    """Mixin/payload for audited mutations requiring an explicit human rationale."""

    reason: str = Field(..., min_length=3, max_length=1000)
