import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.platform_governance import health_router
from app.api.v1.router import v1_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import CorrelationIdMiddleware, configure_logging, get_correlation_id
from app.integrations.sse_transport import sse_transport
from app.models.schemas.common import ErrorEnvelope


def create_app(
    *,
    include_legacy_routers: Optional[bool] = None,
    start_sse_listener: bool = True,
) -> FastAPI:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    if include_legacy_routers is None:
        include_legacy_routers = os.getenv("MOUNT_LEGACY_ROUTERS", "true").lower() == "true"

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if start_sse_listener:
            sse_transport.start_listener(settings.DATABASE_URL)
        try:
            yield
        finally:
            if start_sse_listener:
                sse_transport.stop_listener()

    application = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

    application.add_middleware(CorrelationIdMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        envelope = ErrorEnvelope(
            error_code=exc.error_code,
            message=exc.message,
            details=exc.details,
            correlation_id=get_correlation_id(),
        )
        return JSONResponse(status_code=exc.status_code, content=envelope.model_dump())

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        envelope = ErrorEnvelope(
            error_code="REQUEST_VALIDATION_ERROR",
            message="Request validation failed.",
            details=exc.errors(),
            correlation_id=get_correlation_id(),
        )
        return JSONResponse(status_code=422, content=envelope.model_dump())

    application.include_router(health_router)
    application.include_router(v1_router, prefix="/api/v1")

    if include_legacy_routers:
        # Legacy routers remain mounted during Phase 0 until Phases 1-5 cutover
        from app.api.auth import router as auth_router
        from app.api.batches import router as batches_router
        from app.api.complaints import router as complaints_router
        from app.api.investigations import router as investigations_router
        from app.api.production import router as production_router

        application.include_router(complaints_router, prefix="/api")
        application.include_router(auth_router, prefix="/api/auth", tags=["Authentication (Legacy)"])
        application.include_router(batches_router, prefix="/api/batches", tags=["Batch Management (Legacy)"])
        application.include_router(
            production_router, prefix="/api/production", tags=["Production & Documents (Legacy)"]
        )
        application.include_router(
            investigations_router, prefix="/api/investigations", tags=["QA Investigations (Legacy)"]
        )

    @application.get("/")
    def read_root():
        return {"message": "QMS Copilot Backend is running."}

    return application


app = create_app()
