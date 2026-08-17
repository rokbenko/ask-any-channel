"""OpenAI-backed LLMProvider. embed() batches via EMBED_BATCH_SIZE. chat()/stream_chat()
implement Phase 2 chat completion — stream_chat() is the path core.chat.answer uses."""

from collections.abc import Iterator
from contextlib import contextmanager

from core.constants import (
    DEFAULT_CHAT_MAX_TOKENS,
    DEFAULT_CHAT_MODEL_OPENAI,
    EMBEDDING_MODEL,
    LLM_REQUEST_TIMEOUT_S,
)
from core.credentials import CredentialError, CredentialsProvider
from core.providers.base import ChatChunk, ChatMessage, ChatResponse, ChatUsage, ProviderError

EMBED_BATCH_SIZE = 100


@contextmanager
def _mapped_sdk_errors(what: str):
    """Translate the SDK's exceptions into this codebase's domain errors so callers (CLI, UI)
    never have to import `openai` to catch them. An invalid key is a CredentialError just like
    a missing one — it's the same actionable fix for the self-hoster."""
    from openai import APIError, AuthenticationError

    try:
        yield
    except AuthenticationError as exc:
        raise CredentialError(f"OpenAI rejected the API key: {exc}") from exc
    except APIError as exc:
        raise ProviderError(f"OpenAI {what} request failed: {exc}") from exc


class OpenAIProvider:
    def __init__(self, credentials: CredentialsProvider):
        # Imported here, not at module top: `import openai` (3.x) eagerly loads thousands of
        # pydantic types and measured ~18s of a ~24s CLI cold start. Only commands that
        # actually construct a client should pay for it — `dataset validate`, `registry
        # entry`, `status`, and a key-free `dataset load` never do.
        from openai import OpenAI

        client_kwargs = {
            "api_key": credentials.openai_api_key(),
            "timeout": LLM_REQUEST_TIMEOUT_S,
        }
        base_url = credentials.openai_base_url()
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = OpenAI(**client_kwargs)

    def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        if not texts:
            return []

        embed_model = model or EMBEDDING_MODEL
        embeddings: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[i : i + EMBED_BATCH_SIZE]
            with _mapped_sdk_errors("embeddings"):
                response = self._client.embeddings.create(model=embed_model, input=batch)
            embeddings.extend(item.embedding for item in response.data)
        return embeddings

    def chat(self, messages: list[ChatMessage], *, model: str | None = None) -> ChatResponse:
        chat_model = model or DEFAULT_CHAT_MODEL_OPENAI
        with _mapped_sdk_errors("chat"):
            response = self._client.chat.completions.create(
                model=chat_model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                max_tokens=DEFAULT_CHAT_MAX_TOKENS,
            )

        choice = response.choices[0].message
        return ChatResponse(
            content=choice.content or "",
            tokens_in=response.usage.prompt_tokens if response.usage else None,
            tokens_out=response.usage.completion_tokens if response.usage else None,
        )

    def stream_chat(
        self, messages: list[ChatMessage], *, model: str | None = None
    ) -> Iterator[ChatChunk]:
        chat_model = model or DEFAULT_CHAT_MODEL_OPENAI
        with _mapped_sdk_errors("chat"):
            stream = self._client.chat.completions.create(
                model=chat_model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                max_tokens=DEFAULT_CHAT_MAX_TOKENS,
                stream=True,
                stream_options={"include_usage": True},
            )
            for event in stream:
                if event.choices and event.choices[0].delta.content:
                    yield ChatChunk(text_delta=event.choices[0].delta.content)
                if event.usage is not None:
                    yield ChatChunk(
                        usage=ChatUsage(
                            tokens_in=event.usage.prompt_tokens,
                            tokens_out=event.usage.completion_tokens,
                        )
                    )
