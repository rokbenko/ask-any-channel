"""Per-channel voice persona: a corpus-derived style profile plus honesty/disclosure text that
core.chat.prompt renders into the VOICE section of the system prompt. Stored instance-only in
channels.branding["persona"] — never in bundles or registry entries, since anyone can
regenerate a channel's profile locally in one command (aac persona build)."""

from core.persona.model import (
    PERSONA_KEY,
    Persona,
    disclosure_string,
    get_persona,
    persona_to_dict,
    set_persona,
)
from core.persona.profile import build_style_profile, ensure_style_profile, is_profile_stale
from core.persona.prompt import render_persona_section

__all__ = [
    "PERSONA_KEY",
    "Persona",
    "build_style_profile",
    "disclosure_string",
    "ensure_style_profile",
    "get_persona",
    "is_profile_stale",
    "persona_to_dict",
    "render_persona_section",
    "set_persona",
]
