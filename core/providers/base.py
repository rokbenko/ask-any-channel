"""The LLMProvider seam. Embedding and chat calls go through this interface so the backend
(model, vendor, or any OpenAI-compatible endpoint) can be swapped via config alone."""

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


class LLMProvider(Protocol):
    def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]: ...

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        stream: bool = False,
    ) -> ChatResponse: ...
