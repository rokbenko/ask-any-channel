"""Anthropic-backed LLMProvider. Anthropic has no embeddings endpoint — embed() exists only
to satisfy the Protocol and always raises; the embedding step always goes through
OpenAIProvider regardless of CHAT_PROVIDER, since a channel's stored vectors are always built
with EMBEDDING_MODEL. chat()/stream_chat() are the real implementation here."""

from collections.abc import Iterator

from core.constants import (
    DEFAULT_CHAT_MAX_TOKENS,
    DEFAULT_CHAT_MODEL_ANTHROPIC,
    LLM_REQUEST_TIMEOUT_S,
)
from core.credentials import CredentialError, CredentialsProvider
from core.providers.base import ChatChunk, ChatMessage, ChatResponse, ChatUsage, ProviderError


class EmbeddingNotSupportedError(RuntimeError):
    pass


def _split_system(messages: list[ChatMessage]) -> tuple[str, list[dict]]:
    """Anthropic takes `system` as a separate top-level string, not a message with
    role="system". Concatenates any system-role ChatMessages (there's always exactly one,
    from core.chat.prompt) and returns the rest as turns."""
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    turns = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
    return system, turns


class AnthropicProvider:
    def __init__(self, credentials: CredentialsProvider):
        # Same lazy-import rationale as OpenAIProvider.__init__ — see DECISIONS.md.
        from anthropic import Anthropic

        client_kwargs = {
            "api_key": credentials.anthropic_api_key(),
            "timeout": LLM_REQUEST_TIMEOUT_S,
        }
        base_url = credentials.anthropic_base_url()
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = Anthropic(**client_kwargs)

    def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        raise EmbeddingNotSupportedError(
            "AnthropicProvider has no embeddings API. Query and chunk embeddings always go "
            "through OpenAIProvider (EMBEDDING_MODEL) — construct that separately for the "
            "embedding step even when CHAT_PROVIDER=anthropic."
        )

    def chat(self, messages: list[ChatMessage], *, model: str | None = None) -> ChatResponse:
        from anthropic import APIError, AuthenticationError

        chat_model = model or DEFAULT_CHAT_MODEL_ANTHROPIC
        system, turns = _split_system(messages)
        try:
            response = self._client.messages.create(
                model=chat_model,
                max_tokens=DEFAULT_CHAT_MAX_TOKENS,
                system=system,
                messages=turns,
            )
        except AuthenticationError as exc:
            raise CredentialError(f"Anthropic rejected the API key: {exc}") from exc
        except APIError as exc:
            raise ProviderError(f"Anthropic chat request failed: {exc}") from exc

        text = "".join(block.text for block in response.content if block.type == "text")
        return ChatResponse(
            content=text,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
        )

    def stream_chat(
        self, messages: list[ChatMessage], *, model: str | None = None
    ) -> Iterator[ChatChunk]:
        from anthropic import APIError, AuthenticationError

        chat_model = model or DEFAULT_CHAT_MODEL_ANTHROPIC
        system, turns = _split_system(messages)
        try:
            with self._client.messages.stream(
                model=chat_model,
                max_tokens=DEFAULT_CHAT_MAX_TOKENS,
                system=system,
                messages=turns,
            ) as stream:
                for text in stream.text_stream:
                    yield ChatChunk(text_delta=text)
                final = stream.get_final_message()
                yield ChatChunk(
                    usage=ChatUsage(
                        tokens_in=final.usage.input_tokens,
                        tokens_out=final.usage.output_tokens,
                    )
                )
        except AuthenticationError as exc:
            raise CredentialError(f"Anthropic rejected the API key: {exc}") from exc
        except APIError as exc:
            raise ProviderError(f"Anthropic chat request failed: {exc}") from exc
