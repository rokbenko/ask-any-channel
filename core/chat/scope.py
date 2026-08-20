"""A chat's knowledge scope (which channels feed retrieval) and voice (Neutral, or one
selected creator's persona) — independent of each other, both editable on an open chat.
Pure resolution/validation plus two thin store-backed helpers; this is the exact surface
Part D's HTTP API calls (resolve refs -> build a scope -> create/update a chat)."""

from dataclasses import dataclass
from uuid import UUID

from core.chat.errors import EmptyScopeError, InvalidVoiceError
from core.models import Channel, Chat
from core.persona import get_persona
from core.search.search import resolve_channel_refs
from core.store.base import VectorStore

__all__ = [
    "ChatScope",
    "build_scope",
    "coerce_voice",
    "create_chat",
    "default_voice",
    "resolve_channel_refs",
    "update_chat_scope",
]


@dataclass(frozen=True)
class ChatScope:
    source_channel_ids: tuple[UUID, ...]
    voice_channel_id: UUID | None  # None = Neutral


def default_voice(sources: list[Channel]) -> UUID | None:
    """Exactly one source with persona enabled -> that creator, matching the single-channel
    feel this product had before multi-source chat existed. Anything else (multiple sources,
    or the one source's persona disabled) -> Neutral; the user opts into a voice."""
    if len(sources) == 1 and get_persona(sources[0]).enabled:
        return sources[0].id
    return None


def coerce_voice(sources: list[Channel], voice_channel_id: UUID | None) -> tuple[UUID | None, bool]:
    """Keeps voice_channel_id iff it's one of `sources` with persona enabled, else falls back
    to Neutral. Returns (resolved_voice, changed) so a caller (the UI) can show a fallback
    notice only when something actually changed — e.g. the voice's channel was just removed
    from sources, or its persona was disabled since the chat was opened."""
    if voice_channel_id is None:
        return None, False
    match = next((c for c in sources if c.id == voice_channel_id), None)
    if match is not None and get_persona(match).enabled:
        return voice_channel_id, False
    return None, True


def build_scope(sources: list[Channel], voice_channel_id: UUID | None) -> ChatScope:
    """Strict validation for creating a scope (a new chat, or an explicit edit) — unlike
    coerce_voice, an invalid voice here is the caller's bug (UI/API), not a state that drifted
    since the chat was opened, so it raises rather than silently falling back."""
    if not sources:
        raise EmptyScopeError("Select at least one source channel.")
    if voice_channel_id is not None:
        match = next((c for c in sources if c.id == voice_channel_id), None)
        if match is None:
            raise InvalidVoiceError("Voice must be Neutral or one of the selected sources.")
        if not get_persona(match).enabled:
            name = match.title or match.handle or match.yt_channel_id
            raise InvalidVoiceError(f"{name}'s voice is disabled — enable it or choose Neutral.")

    seen: set[UUID] = set()
    deduped_ids = []
    for c in sources:
        if c.id not in seen:
            seen.add(c.id)
            deduped_ids.append(c.id)
    return ChatScope(source_channel_ids=tuple(deduped_ids), voice_channel_id=voice_channel_id)


def create_chat(store: VectorStore, scope: ChatScope) -> Chat:
    return store.create_chat(
        source_channel_ids=list(scope.source_channel_ids), voice_channel_id=scope.voice_channel_id
    )


def update_chat_scope(store: VectorStore, chat_id: UUID, scope: ChatScope) -> Chat:
    return store.set_chat_scope(
        chat_id,
        source_channel_ids=list(scope.source_channel_ids),
        voice_channel_id=scope.voice_channel_id,
    )
