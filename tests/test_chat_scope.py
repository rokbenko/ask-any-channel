from dataclasses import replace
from uuid import uuid4

import pytest

from core.chat.errors import EmptyScopeError, InvalidVoiceError
from core.chat.scope import build_scope, coerce_voice, default_voice, resolve_channel_refs
from core.models import Channel
from core.persona import Persona, set_persona
from core.search.search import ChannelNotFoundError
from tests.fakes import FakeVectorStore


def _make_channel(title="Alex", handle=None) -> Channel:
    return Channel(
        id=uuid4(),
        yt_channel_id="UC" + "x" * 22,
        handle=handle or f"@{title.lower()}",
        title=title,
        thumbnail_url=None,
        branding={},
        created_at=None,
    )


def _disabled(channel: Channel) -> Channel:
    return replace(channel, branding={"persona": {"enabled": False}})


# --- default_voice --------------------------------------------------------------------------


def test_default_voice_is_the_single_source_when_persona_enabled():
    channel = _make_channel()
    assert default_voice([channel]) == channel.id


def test_default_voice_is_neutral_when_the_single_source_has_persona_disabled():
    channel = _disabled(_make_channel())
    assert default_voice([channel]) is None


def test_default_voice_is_neutral_with_multiple_sources():
    a, b = _make_channel("A"), _make_channel("B")
    assert default_voice([a, b]) is None


def test_default_voice_is_neutral_with_zero_sources():
    assert default_voice([]) is None


# --- coerce_voice ----------------------------------------------------------------------------


def test_coerce_voice_keeps_a_valid_enabled_voice():
    a, b = _make_channel("A"), _make_channel("B")
    resolved, changed = coerce_voice([a, b], a.id)
    assert resolved == a.id
    assert changed is False


def test_coerce_voice_falls_back_to_neutral_when_voice_not_in_sources():
    a, b = _make_channel("A"), _make_channel("B")
    resolved, changed = coerce_voice([a], b.id)
    assert resolved is None
    assert changed is True


def test_coerce_voice_falls_back_to_neutral_when_persona_disabled():
    a = _disabled(_make_channel("A"))
    resolved, changed = coerce_voice([a], a.id)
    assert resolved is None
    assert changed is True


def test_coerce_voice_neutral_stays_neutral_without_reporting_a_change():
    a = _make_channel("A")
    resolved, changed = coerce_voice([a], None)
    assert resolved is None
    assert changed is False


# --- build_scope -----------------------------------------------------------------------------


def test_build_scope_raises_empty_scope_error_with_no_sources():
    with pytest.raises(EmptyScopeError):
        build_scope([], None)


def test_build_scope_raises_invalid_voice_when_voice_not_in_sources():
    a, b = _make_channel("A"), _make_channel("B")
    with pytest.raises(InvalidVoiceError):
        build_scope([a], b.id)


def test_build_scope_raises_invalid_voice_when_persona_disabled():
    a = _disabled(_make_channel("A"))
    with pytest.raises(InvalidVoiceError):
        build_scope([a], a.id)


def test_build_scope_accepts_neutral_voice():
    a, b = _make_channel("A"), _make_channel("B")
    scope = build_scope([a, b], None)
    assert scope.voice_channel_id is None
    assert scope.source_channel_ids == (a.id, b.id)


def test_build_scope_accepts_a_valid_creator_voice():
    a = _make_channel("A")
    scope = build_scope([a], a.id)
    assert scope.voice_channel_id == a.id


def test_build_scope_dedupes_sources_preserving_first_occurrence_order():
    a, b = _make_channel("A"), _make_channel("B")
    scope = build_scope([a, b, a], None)
    assert scope.source_channel_ids == (a.id, b.id)


# --- resolve_channel_refs ----------------------------------------------------------------


def test_resolve_channel_refs_resolves_handles_and_ids_in_order():
    a, b = _make_channel("A"), _make_channel("B")
    store = FakeVectorStore()
    store.channels[a.id] = a
    store.channels[b.id] = b

    resolved = resolve_channel_refs(store, [b.handle, a.yt_channel_id])

    assert [c.id for c in resolved] == [b.id, a.id]


def test_resolve_channel_refs_raises_for_a_missing_ref():
    store = FakeVectorStore()
    with pytest.raises(ChannelNotFoundError):
        resolve_channel_refs(store, ["@nope"])


def test_resolve_channel_refs_dedupes_preserving_order():
    a = _make_channel("A")
    store = FakeVectorStore()
    store.channels[a.id] = a

    resolved = resolve_channel_refs(store, [a.handle, a.yt_channel_id])

    assert [c.id for c in resolved] == [a.id]


# --- set_persona round-trip sanity (used by _disabled helper above) ----------------------


def test_disabled_helper_actually_disables_persona_for_get_persona_callers():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    updated = set_persona(store, channel.id, Persona(enabled=False))
    assert coerce_voice([updated], channel.id) == (None, True)
