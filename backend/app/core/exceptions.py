from typing import Any, Optional


class AppError(Exception):
    """Base domain/application exception mapped to standardized HTTP responses."""

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
        details: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if error_code is not None:
            self.error_code = error_code
        self.details = details


class ValidationError(AppError):
    status_code = 422
    error_code = "VALIDATION_ERROR"


class AuthenticationError(AppError):
    status_code = 401
    error_code = "AUTHENTICATION_FAILED"


class PermissionDeniedError(AppError):
    status_code = 403
    error_code = "PERMISSION_DENIED"


class NotFoundError(AppError):
    status_code = 404
    error_code = "NOT_FOUND"


class ConflictError(AppError):
    status_code = 409
    error_code = "CONFLICT"


class RateLimitError(AppError):
    status_code = 429
    error_code = "RATE_LIMIT_EXCEEDED"


class ExternalProviderError(AppError):
    status_code = 502
    error_code = "EXTERNAL_PROVIDER_ERROR"
