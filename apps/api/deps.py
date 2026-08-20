"""FastAPI dependency providers: store/credentials/provider construction, and the bearer-
token guard. Configuration flows through core.config/core.credentials only — no os.environ
here. Tests override these via app.dependency_overrides (see apps/api/main.py::create_app)."""

from fastapi import Depends, Header, HTTPException

from core.config import Settings, get_settings
from core.credentials import CredentialsProvider
from core.providers.base import LLMProvider
from core.providers.factory import build_chat_provider
from core.providers.openai_provider import OpenAIProvider
from core.store.base import VectorStore
from core.store.pgvector_store import PgVectorStore


def get_settings_dep() -> Settings:
    return get_settings()


def get_store() -> VectorStore:
    return PgVectorStore()


def get_credentials(settings: Settings = Depends(get_settings_dep)) -> CredentialsProvider:
    return CredentialsProvider(settings)


def get_embedding_provider(
    credentials: CredentialsProvider = Depends(get_credentials),
) -> LLMProvider:
    return OpenAIProvider(credentials)


def get_chat_provider_and_model(
    settings: Settings = Depends(get_settings_dep),
    credentials: CredentialsProvider = Depends(get_credentials),
) -> tuple[LLMProvider, str]:
    return build_chat_provider(settings, credentials)


def require_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings_dep),
) -> None:
    """API_TOKEN unset = the API is open (selfhost default, matching the rest of this
    product's no-auth posture). Set it and every request needs `Authorization: Bearer <token>`."""
    if not settings.api_token:
        return
    if authorization != f"Bearer {settings.api_token}":
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")
