"""The Persona data shape and its channels.branding round-trip. Tolerant of a missing or
partially-shaped branding["persona"] dict — an un-generated channel and an old branding blob
from before this feature both parse to sensible defaults rather than raising."""

from dataclasses import asdict, dataclass
from uuid import UUID

from core.models import Channel
from core.store.base import VectorStore

PERSONA_KEY = "persona"


@dataclass(frozen=True)
class Persona:
    enabled: bool = True
    style_profile: str | None = None
    custom_instructions: str = ""
    family_friendly: bool = False
    profile_generated_at: str | None = None  # ISO 8601, set when style_profile is (re)generated
    profile_chunk_count: int | None = None  # channel's chunk count at generation time


def _str_or_default(value: object, default: str) -> str:
    return value if isinstance(value, str) else default


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def get_persona(channel: Channel) -> Persona:
    raw = channel.branding.get(PERSONA_KEY)
    if not isinstance(raw, dict):
        return Persona()
    return Persona(
        enabled=bool(raw.get("enabled", True)),
        style_profile=_str_or_none(raw.get("style_profile")),
        custom_instructions=_str_or_default(raw.get("custom_instructions"), ""),
        family_friendly=bool(raw.get("family_friendly", False)),
        profile_generated_at=_str_or_none(raw.get("profile_generated_at")),
        profile_chunk_count=_int_or_none(raw.get("profile_chunk_count")),
    )


def persona_to_dict(persona: Persona) -> dict:
    return asdict(persona)


def set_persona(store: VectorStore, channel_id: UUID, persona: Persona) -> Channel:
    # set_channel_branding does a top-level jsonb merge, so the whole "persona" sub-dict is
    # replaced as one unit — always write it in full, never a partial patch.
    return store.set_channel_branding(channel_id, {PERSONA_KEY: persona_to_dict(persona)})


def disclosure_string(name: str) -> str:
    return f"AI trained on {name}'s public videos — not {name}."
