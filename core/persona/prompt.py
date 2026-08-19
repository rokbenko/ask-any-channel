"""Renders the VOICE section of the chat system prompt for a persona-enabled creator. Pure —
no store/provider access — so it's testable without a channel or a chat model."""

from core.persona.model import Persona, disclosure_string


def render_persona_section(persona: Persona, channel_title: str) -> str:
    parts: list[str] = []
    if persona.style_profile:
        parts.append(
            f"Style profile for {channel_title}, derived from their own videos:\n"
            f"{persona.style_profile}"
        )
    if persona.custom_instructions:
        parts.append(f"Additional voice notes from the operator: {persona.custom_instructions}")

    if persona.family_friendly:
        parts.append(
            "Keep it clean: no profanity, even where the style profile shows the creator uses "
            "it — keep the energy and phrasing, just without the swearing."
        )
    else:
        parts.append(
            f"You may use {channel_title}'s characteristic language, including profanity, "
            "wherever the style profile shows it's part of their authentic voice."
        )

    # Non-negotiable regardless of style profile content — see core/chat/prompt.py's HONESTY
    # section, which repeats this for every voice. Kept here too so a persona section pasted
    # or read in isolation (e.g. the UI popover) never loses the guardrail.
    parts.append(
        f"You are an AI, not {channel_title}. Never claim to BE {channel_title} or imply you "
        f'are a real person. If asked directly ("are you {channel_title}?", "are you human?", '
        '"are you real?"), answer honestly that you are an AI — you may say it in this voice, '
        f"but the answer is always no. {disclosure_string(channel_title)}"
    )
    return "\n\n".join(parts)
