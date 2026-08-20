"""FastAPI app assembly: CORS, exception handlers mapping core domain errors to HTTP status
codes, route registration, and a startup health check. Routes parse/validate/serialize and
call core — nothing else lives here.

create_app() takes optional store/settings/credentials/providers so tests can inject fakes via
FastAPI's own dependency_overrides mechanism, without touching real Postgres or vendor SDKs."""

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from apps.api import deps
from apps.api.routes import ask, channels, chats, health
from core.chat.errors import (
    ChatNotFoundError,
    EmbeddingModelMismatchError,
    EmptyScopeError,
    InvalidVoiceError,
    QuestionTooLongError,
)
from core.config import Settings, get_settings
from core.credentials import CredentialError
from core.db import DatabaseUnavailableError
from core.doctor import run_checks
from core.providers.base import ProviderError
from core.search.search import ChannelNotFoundError

logger = logging.getLogger(__name__)

# Domain error -> HTTP status. Anything not listed here is a bug: FastAPI's default handler
# turns it into a 500 with a traceback in the server log, which is what we want for a real bug.
_ERROR_STATUS: dict[type[Exception], int] = {
    ChannelNotFoundError: 404,
    ChatNotFoundError: 404,
    InvalidVoiceError: 422,
    EmptyScopeError: 422,
    QuestionTooLongError: 422,
    EmbeddingModelMismatchError: 422,
    CredentialError: 503,
    ProviderError: 502,
    DatabaseUnavailableError: 503,
}


def _make_handler(status_code: int):
    def _handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    return _handler


def create_app(
    *,
    store=None,
    settings: Settings | None = None,
    credentials=None,
    providers: tuple | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    app = FastAPI(title="AskAnyChannel API", version="0.2.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for exc_type, status_code in _ERROR_STATUS.items():
        app.add_exception_handler(exc_type, _make_handler(status_code))

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(channels.router, prefix="/api/v1")
    app.include_router(chats.router, prefix="/api/v1")
    app.include_router(ask.router, prefix="/api/v1")

    # Test/injection seam: FastAPI's own dependency_overrides, not constructor plumbing through
    # every route — each override replaces exactly the Depends() callable routes already use.
    if store is not None:
        app.dependency_overrides[deps.get_store] = lambda: store
    if settings is not None:
        app.dependency_overrides[deps.get_settings_dep] = lambda: settings
    if credentials is not None:
        app.dependency_overrides[deps.get_credentials] = lambda: credentials
    if providers is not None:
        embedding_provider, chat_provider, chat_model = providers
        app.dependency_overrides[deps.get_embedding_provider] = lambda: embedding_provider
        app.dependency_overrides[deps.get_chat_provider_and_model] = lambda: (
            chat_provider,
            chat_model,
        )

    # core.doctor's checks read the REAL get_settings()/PgVectorStore directly — they aren't
    # wired through FastAPI's DI, so they can't be redirected by the overrides above. Only run
    # the boot check for the real, uninjected app (what uvicorn actually serves); a test/tool
    # that supplied any override is by definition not that app, and must never be blocked on
    # (or accidentally hit) real Postgres just from constructing a TestClient.
    if store is None and settings is None and credentials is None and providers is None:

        @app.on_event("startup")
        def _boot_check() -> None:
            failed = [r for r in run_checks("api") if not r.ok]
            for r in failed:
                logger.error("%s: %s", r.name, r.detail)
            if failed:
                logger.error("api starting unhealthy — fix the above (aac doctor --role api)")

    return app


# No module-level `app = create_app()`: that would call the real get_settings() at import
# time (requiring DATABASE_URL to already be set), which breaks importing this module from a
# test process before it's had a chance to inject a fake Settings/store. uvicorn is pointed at
# the factory itself instead: `uvicorn apps.api.main:create_app --factory`.
