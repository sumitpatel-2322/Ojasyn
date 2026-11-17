import json
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    """Returns the current request/job correlation ID or generates a fallback."""
    cid = correlation_id_var.get()
    return cid or str(uuid.uuid4())


class CorrelationIdFilter(logging.Filter):
    """Injects correlation_id into all log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_var.get() or "-"
        return True


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as single-line structured JSON without leaking secrets."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Configures root structured logging."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredJsonFormatter())
    handler.addFilter(CorrelationIdFilter())
    root.handlers = [handler]


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Reads or assigns X-Correlation-ID on every HTTP request."""

    header_name = "X-Correlation-ID"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        incoming = request.headers.get(self.header_name)
        cid = incoming.strip() if incoming and len(incoming.strip()) <= 128 else str(uuid.uuid4())
        token = correlation_id_var.set(cid)
        try:
            response = await call_next(request)
            response.headers[self.header_name] = cid
            return response
        finally:
            correlation_id_var.reset(token)
