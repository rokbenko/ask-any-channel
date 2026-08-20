"""core.persona: corpus-derived voice profiles stored instance-only in channels.branding.
Style-profile generation calls tiktoken to cap the sample by token budget — stubbed here so
tests never touch the network (the real encoding download is a known live-environment gap,
see DECISIONS.md)."""

from uuid import uuid4

import pytest

from core.config import Settings
from core.credentials import CredentialsProvider
from core.dataset.manifest import ChannelMeta, ChunkingParams, Manifest
from core.dataset.registry import build_registry_entry
from core.models import Channel
from core.persona import (
    Persona,
    build_style_profile,
    disclosure_string,
    ensure_style_profile,
    get_persona,
    is_profile_stale,
    render_persona_section,
    set_persona,
)
from core.persona.profile import parse_style_profile
from tests.fakes import FakeLLMProvider, FakeVectorStore


class _FakeEncoding:
    """Token-count proxy for tests: one "token" per whitespace-split word. Real tiktoken would
    hang in this sandbox (its encoding file's download host is network-unreachable here)."""

    def encode(self, text: str) -> list[str]:
        return text.split()


@pytest.fixture(autouse=True)
def _stub_tiktoken(monkeypatch):
    monkeypatch.setattr("core.persona.profile.tiktoken.get_encoding", lambda name: _FakeEncoding())


def _make_channel(*, title="Alex", handle="@alex", branding=None) -> Channel:
    return Channel(
        id=uuid4(),
        yt_channel_id="UC" + "x" * 22,
        handle=handle,
        title=title,
        thumbnail_url=None,
        branding=branding or {},
        created_at=None,
    )


def _settings(**overrides) -> Settings:
    fields = {
        "instance_mode": "selfhost",
        "database_url": "postgresql://x",
        "openai_api_key": "sk-test",
        "openai_base_url": None,
        "anthropic_api_key": None,
        "anthropic_base_url": None,
        "chat_provider": "openai",
        "chat_model": None,
        "raw_captions_dir": "data/raw",
        "retrieval_mode": "hybrid",
        "api_token": None,
        "cors_origins": (),
        "auto_ingest_interval_hours": 24.0,
    }
    fields.update(overrides)
    return Settings(**fields)


# --- Persona / branding round-trip -----------------------------------------------------


def test_get_persona_defaults_when_branding_has_no_persona_key():
    channel = _make_channel(branding={})
    persona = get_persona(channel)

    assert persona == Persona()
    assert persona.enabled is True
    assert persona.style_profile is None


def test_get_persona_round_trips_through_set_persona():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    persona = Persona(
        enabled=False,
        style_profile="Tone & energy\nHigh energy.",
        custom_instructions="Talk fast.",
        family_friendly=True,
        profile_generated_at="2026-01-01T00:00:00+00:00",
        profile_chunk_count=42,
    )

    updated_channel = set_persona(store, channel.id, persona)

    assert get_persona(updated_channel) == persona


def test_get_persona_tolerates_malformed_branding_shapes():
    channel = _make_channel(
        branding={"persona": {"enabled": "yes", "style_profile": 5, "profile_chunk_count": "lots"}}
    )
    persona = get_persona(channel)

    # Malformed/wrong-typed fields fall back to their defaults rather than raising.
    assert persona.style_profile is None
    assert persona.profile_chunk_count is None
    assert persona.custom_instructions == ""


def test_get_persona_ignores_non_dict_persona_value():
    channel = _make_channel(branding={"persona": "not a dict"})
    assert get_persona(channel) == Persona()


# --- render_persona_section --------------------------------------------------------------


def test_render_persona_section_includes_honesty_and_disclosure_text():
    persona = Persona(style_profile="Tone & energy\nCalm.")
    section = render_persona_section(persona, "Alex")

    assert "You are an AI, not Alex" in section
    assert disclosure_string("Alex") in section
    assert "are you Alex?" in section


def test_render_persona_section_family_friendly_flips_the_profanity_clause():
    base = Persona(style_profile="x")

    clean = render_persona_section(base, "Alex")
    assert "characteristic language, including profanity" in clean

    family_friendly = render_persona_section(
        Persona(style_profile="x", family_friendly=True), "Alex"
    )
    assert "no profanity" in family_friendly
    assert "characteristic language, including profanity" not in family_friendly


def test_render_persona_section_includes_custom_instructions_when_present():
    persona = Persona(style_profile="x", custom_instructions="Always mention pricing.")
    section = render_persona_section(persona, "Alex")

    assert "Always mention pricing." in section


def test_render_persona_section_omits_custom_instructions_when_absent():
    persona = Persona(style_profile="x", custom_instructions="")
    section = render_persona_section(persona, "Alex")

    assert "operator" not in section


# --- build_style_profile ------------------------------------------------------------------


def test_build_style_profile_returns_none_with_no_sample_chunks():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel, style_sample_chunk_texts={})
    provider = FakeLLMProvider(embedding_dim=8)

    assert build_style_profile(store, provider, "gpt-4.1-mini", channel) is None
    assert provider.chat_calls == 0


def test_build_style_profile_sends_sampled_excerpts_and_parses_the_reply():
    channel = _make_channel()
    store = FakeVectorStore(
        channel=channel, style_sample_chunk_texts={channel.id: ["excerpt one", "excerpt two"]}
    )
    reply = "## Tone & energy\nHigh energy, direct."
    provider = FakeLLMProvider(embedding_dim=8, chat_reply=reply)

    profile = build_style_profile(store, provider, "gpt-4.1-mini", channel)

    assert profile == reply
    user_message = provider.last_messages[-1]
    assert "excerpt one" in user_message.content
    assert "excerpt two" in user_message.content
    assert channel.title in user_message.content


def test_build_style_profile_respects_the_sample_token_cap(monkeypatch):
    monkeypatch.setattr("core.persona.profile.STYLE_SAMPLE_MAX_TOKENS", 3)
    channel = _make_channel()
    # Each excerpt is 2 "tokens" (whitespace-split words) under the stubbed encoding.
    store = FakeVectorStore(
        channel=channel,
        style_sample_chunk_texts={channel.id: ["aa bb", "cc dd", "ee ff", "gg hh"]},
    )
    provider = FakeLLMProvider(embedding_dim=8, chat_reply="## Tone & energy\nx")

    build_style_profile(store, provider, "gpt-4.1-mini", channel)

    user_message = provider.last_messages[-1]
    # Only the first excerpt (2 tokens) fits under a 3-token budget; the second would push to 4.
    assert "aa bb" in user_message.content
    assert "cc dd" not in user_message.content


def test_parse_style_profile_strips_code_fences_and_caps_length():
    fenced = "```markdown\n## Tone & energy\nCalm.\n```"
    assert parse_style_profile(fenced) == "## Tone & energy\nCalm."

    huge = "## Tone & energy\n" + "x" * 10_000
    assert len(parse_style_profile(huge)) <= 6000


# --- ensure_style_profile -----------------------------------------------------------------


def test_ensure_style_profile_skips_without_a_configured_chat_key():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel, style_sample_chunk_texts={channel.id: ["excerpt"]})
    settings = _settings(openai_api_key=None, chat_provider="openai")
    credentials = CredentialsProvider(settings)

    assert ensure_style_profile(store, credentials, settings, channel) is None
    assert get_persona(store.channels[channel.id]).style_profile is None


def test_ensure_style_profile_skips_gracefully_with_no_embedded_chunks():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel, style_sample_chunk_texts={})
    settings = _settings()
    credentials = CredentialsProvider(settings)

    assert ensure_style_profile(store, credentials, settings, channel) is None


def test_ensure_style_profile_returns_existing_profile_without_regenerating(monkeypatch):
    channel = _make_channel(branding={"persona": {"style_profile": "existing profile"}})
    store = FakeVectorStore(channel=channel, style_sample_chunk_texts={channel.id: ["excerpt"]})
    settings = _settings()
    credentials = CredentialsProvider(settings)

    def _explode(*a, **k):
        raise AssertionError("should not call the chat provider when a profile already exists")

    monkeypatch.setattr("core.persona.profile.build_chat_provider_if_configured", _explode)

    assert ensure_style_profile(store, credentials, settings, channel) == "existing profile"


def test_ensure_style_profile_force_regenerates_and_persists(monkeypatch):
    channel = _make_channel(branding={"persona": {"style_profile": "old profile"}})
    store = FakeVectorStore(
        channel=channel, style_sample_chunk_texts={channel.id: ["excerpt one", "excerpt two"]}
    )
    settings = _settings()
    credentials = CredentialsProvider(settings)
    fake_provider = FakeLLMProvider(embedding_dim=8, chat_reply="## Tone & energy\nNew.")
    monkeypatch.setattr(
        "core.persona.profile.build_chat_provider_if_configured",
        lambda settings, credentials: (fake_provider, "gpt-4.1-mini"),
    )

    result = ensure_style_profile(store, credentials, settings, channel, force=True)

    assert result == "## Tone & energy\nNew."
    updated = get_persona(store.channels[channel.id])
    assert updated.style_profile == "## Tone & energy\nNew."
    assert updated.profile_generated_at is not None
    assert updated.profile_chunk_count == 2


# --- is_profile_stale ----------------------------------------------------------------------


def test_is_profile_stale_false_with_no_baseline():
    assert is_profile_stale(Persona(), current_chunk_count=100) is False
    assert is_profile_stale(Persona(style_profile="x"), current_chunk_count=100) is False


def test_is_profile_stale_true_after_growth_past_threshold():
    persona = Persona(style_profile="x", profile_chunk_count=100)

    assert is_profile_stale(persona, current_chunk_count=124) is False
    assert is_profile_stale(persona, current_chunk_count=125) is True


# --- persona stays out of bundles/registry --------------------------------------------------


def test_manifest_dataclass_has_no_persona_field():
    assert "persona" not in Manifest.__dataclass_fields__


def test_registry_entry_has_no_persona_field():
    manifest = Manifest(
        schema_version=1,
        channel=ChannelMeta(
            yt_channel_id="UC" + "x" * 22, handle="@alex", title="Alex", thumbnail_url=None
        ),
        snapshot_date="2026-01-01",
        chunking=ChunkingParams(target_tokens=400, overlap_ratio=0.15, encoding="cl100k_base"),
        embedding=None,
        tool_version="0.2.0",
        contributor="anonymous",
        video_count=1,
        chunk_count=1,
        limit=None,
        sort="recent",
    )

    entry = build_registry_entry(manifest)

    assert "persona" not in entry
