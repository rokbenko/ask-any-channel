"""The LLMProvider seam. Embedding and chat calls go through this interface so the backend
(model, vendor, or any OpenAI-compatible endpoint) can be swapped via config alone."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class ChatResponse:
    content: str
    tokens_in: int | None = None
    tokens_out: int | None = None


@dataclass
class ChatUsage:
    tokens_in: int | None
    tokens_out: int | None


@dataclass
class ChatChunk:
    """One piece of a streamed chat response. Exactly one field is set: `text_delta` for
    every text piece as it arrives, `usage` once on the final chunk once the provider
    reports total token counts. Never both."""

    text_delta: str | None = None
    usage: ChatUsage | None = None


class ProviderError(RuntimeError):
    """A chat/embedding provider SDK call failed for a reason that isn't a bad credential
    (rate limit, timeout, network, 5xx). Distinct from CredentialError, which is raised at
    provider *construction* time."""


class LLMProvider(Protocol):
    def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]: ...

    def chat(self, messages: list[ChatMessage], *, model: str | None = None) -> ChatResponse: ...

    def stream_chat(
        self, messages: list[ChatMessage], *, model: str | None = None
    ) -> Iterator[ChatChunk]: ...
