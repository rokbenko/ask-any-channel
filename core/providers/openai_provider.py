"""OpenAI-backed LLMProvider. Only embed() is implemented in Phase 1 — chat orchestration
is Phase 2 scope, but the method exists now so the interface doesn't need to change later."""

from core.constants import EMBEDDING_MODEL
from core.credentials import CredentialsProvider
from core.providers.base import ChatMessage, ChatResponse

EMBED_BATCH_SIZE = 100


class OpenAIProvider:
    def __init__(self, credentials: CredentialsProvider):
        # Imported here, not at module top: `import openai` (3.x) eagerly loads thousands of
        # pydantic types and measured ~18s of a ~24s CLI cold start. Only commands that
        # actually construct a client should pay for it — `dataset validate`, `registry
        # entry`, `status`, and a key-free `dataset load` never do.
        from openai import OpenAI

        client_kwargs = {"api_key": credentials.openai_api_key()}
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
            response = self._client.embeddings.create(model=embed_model, input=batch)
            embeddings.extend(item.embedding for item in response.data)
        return embeddings

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        stream: bool = False,
    ) -> ChatResponse:
        raise NotImplementedError("Chat orchestration lands in Phase 2")
