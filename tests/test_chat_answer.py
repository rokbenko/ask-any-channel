from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from core.chat.answer import answer, ask
from core.chat.errors import ChatNotFoundError, EmbeddingModelMismatchError, QuestionTooLongError
from core.chat.scope import ChatScope
from core.constants import EMBEDDING_DIM, MAX_QUESTION_CHARS, MIN_BLOCKS_PER_SOURCE
from core.models import Channel, Chat, Message
from core.persona import Persona, set_persona
from core.search.search import ChannelNotFoundError
from core.store.base import SearchResult
from tests.fakes import FakeLLMProvider, FakeVectorStore, make_chat_chunks

CHAT_ID = UUID("00000000-0000-0000-0000-000000000c4a")


def _make_channel(title="Some Channel", handle="@some") -> Channel:
    return Channel(
        id=uuid4(),
        yt_channel_id="UC" + "x" * 22,
        handle=handle,
        title=title,
        thumbnail_url=None,
        branding={},
        created_at=None,
    )


def _make_search_results(n: int, channel: Channel | None = None) -> list[SearchResult]:
    channel = channel or _make_channel()
    return [
        SearchResult(
            chunk_id=uuid4(),
            video_id=uuid4(),
            yt_video_id=f"vid{i:08d}",
            video_title=f"Video {i}",
            text=f"transcript text {i}",
            t_start_s=float(i * 30),
            t_end_s=float(i * 30 + 10),
            score=0.9,
            channel_id=channel.id,
            channel_title=channel.title,
            channel_handle=channel.handle,
        )
        for i in range(1, n + 1)
    ]


def _seed_channel(store: FakeVectorStore, channel: Channel) -> None:
    store.channels[channel.id] = channel


def _seed_chat(
    store: FakeVectorStore, *, source_channel_ids, voice_channel_id=None, chat_id=CHAT_ID
) -> Chat:
    chat = Chat(
        id=chat_id,
        voice_channel_id=voice_channel_id,
        created_at=datetime.now(UTC),
        source_channel_ids=list(source_channel_ids),
    )
    store.chats[chat.id] = chat
    return chat


def _providers(*, stream_chunks=None, embedding_dim=EMBEDDING_DIM, raise_after=None):
    embedding_provider = FakeLLMProvider(embedding_dim=embedding_dim)
    chat_provider = FakeLLMProvider(
        embedding_dim=EMBEDDING_DIM, stream_chunks=stream_chunks, raise_after=raise_after
    )
    return embedding_provider, chat_provider


def _answer(store, embedding_provider, chat_provider, *, user_text="hi", chat_id=CHAT_ID, **kwargs):
    return answer(
        store,
        embedding_provider,
        chat_provider,
        chat_id=chat_id,
        user_text=user_text,
        chat_model="gpt-4.1-mini",
        **kwargs,
    )


# --- basic errors ---------------------------------------------------------------------------


def test_answer_raises_chat_not_found_for_unknown_chat_id():
    store = FakeVectorStore()
    embedding_provider, chat_provider = _providers()

    with pytest.raises(ChatNotFoundError):
        _answer(store, embedding_provider, chat_provider, chat_id=uuid4())

    assert store.messages == []


def test_answer_raises_question_too_long_before_touching_providers():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    _seed_chat(store, source_channel_ids=[channel.id])
    embedding_provider, chat_provider = _providers()

    with pytest.raises(QuestionTooLongError):
        _answer(store, embedding_provider, chat_provider, user_text="x" * (MAX_QUESTION_CHARS + 1))

    assert store.messages == []


def test_answer_raises_embedding_model_mismatch_when_dims_differ():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    _seed_chat(store, source_channel_ids=[channel.id])
    embedding_provider, chat_provider = _providers(embedding_dim=EMBEDDING_DIM - 1)

    with pytest.raises(EmbeddingModelMismatchError):
        _answer(store, embedding_provider, chat_provider)


def test_answer_raises_channel_not_found_when_a_source_channel_no_longer_exists():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    _seed_chat(store, source_channel_ids=[channel.id, uuid4()])  # second id resolves to nothing
    embedding_provider, chat_provider = _providers()

    with pytest.raises(ChannelNotFoundError):
        _answer(store, embedding_provider, chat_provider)


# --- persistence ordering / streaming (single source, unchanged behavior) -----------------


def test_answer_persists_user_message_before_stream_is_consumed():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    _seed_chat(store, source_channel_ids=[channel.id])
    embedding_provider, chat_provider = _providers(stream_chunks=make_chat_chunks(["hello"]))

    _answer(store, embedding_provider, chat_provider)

    assert len(store.messages) == 1
    assert store.messages[0].role == "user"
    assert store.messages[0].content == "hi"


def test_answer_streams_text_chunks_in_order():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    _seed_chat(store, source_channel_ids=[channel.id])
    embedding_provider, chat_provider = _providers(
        stream_chunks=make_chat_chunks(["Hello", " world"])
    )

    result = _answer(store, embedding_provider, chat_provider)

    assert list(result.text_stream) == ["Hello", " world"]


def test_answer_persists_assistant_message_and_citations_after_stream_exhausted():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel, search_results=_make_search_results(1, channel))
    _seed_chat(store, source_channel_ids=[channel.id])
    embedding_provider, chat_provider = _providers(
        stream_chunks=make_chat_chunks(["The answer is [1]."])
    )

    result = _answer(store, embedding_provider, chat_provider)
    list(result.text_stream)  # exhaust

    assert len(result.citations) == 1
    assistant_messages = [m for m in store.messages if m.role == "assistant"]
    assert len(assistant_messages) == 1
    assert assistant_messages[0].citations[0]["n"] == 1
    assert assistant_messages[0].citations[0]["channel_id"] == str(channel.id)
    assert assistant_messages[0].citations[0]["channel_title"] == channel.title


def test_answer_records_usage_event_with_est_cost_after_stream_exhausted():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    _seed_chat(store, source_channel_ids=[channel.id])
    embedding_provider, chat_provider = _providers(
        stream_chunks=make_chat_chunks(["hi"], tokens_in=1000, tokens_out=1000)
    )

    result = _answer(store, embedding_provider, chat_provider)
    list(result.text_stream)

    assert len(store.usage_events) == 1
    assert store.usage_events[0].tokens_in == 1000
    assert store.usage_events[0].source_channel_ids == [channel.id]
    # No voice selected, one source: channel_id falls back to that first (only) source.
    assert store.usage_events[0].channel_id == channel.id
    assert result.usage.est_cost_usd == pytest.approx(0.0004 + 0.0016)


def test_refusal_response_persists_no_citations():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    _seed_chat(store, source_channel_ids=[channel.id])
    embedding_provider, chat_provider = _providers(
        stream_chunks=make_chat_chunks(["This channel doesn't cover that."])
    )

    result = _answer(store, embedding_provider, chat_provider, user_text="off topic")
    list(result.text_stream)

    assert result.citations == []
    assistant_messages = [m for m in store.messages if m.role == "assistant"]
    assert assistant_messages[0].citations == []


def test_history_window_limits_to_last_n_messages():
    channel = _make_channel()
    history = [
        Message(id=uuid4(), chat_id=CHAT_ID, role="user", content=f"q{i}") for i in range(10)
    ]
    store = FakeVectorStore(channel=channel, history=history)
    _seed_chat(store, source_channel_ids=[channel.id])
    embedding_provider, chat_provider = _providers(stream_chunks=make_chat_chunks(["ok"]))

    result = _answer(store, embedding_provider, chat_provider, user_text="new", history_window=3)
    list(result.text_stream)  # stream_chat() isn't called until the stream is consumed

    sent_history_texts = [m.content for m in chat_provider.last_messages if m.role == "user"]
    # the last 3 of the 10 seeded history rows, plus the new user turn itself
    assert sent_history_texts == ["q7", "q8", "q9", "new"]


def test_answer_does_not_persist_assistant_turn_when_provider_raises_mid_stream():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    _seed_chat(store, source_channel_ids=[channel.id])
    embedding_provider, chat_provider = _providers(
        stream_chunks=make_chat_chunks(["partial"]), raise_after=0
    )

    result = _answer(store, embedding_provider, chat_provider)

    with pytest.raises(RuntimeError, match="simulated provider failure"):
        list(result.text_stream)

    assert all(m.role != "assistant" for m in store.messages)
    assert store.usage_events == []


def test_answer_records_usage_and_partial_text_when_the_consumer_disconnects_mid_stream():
    # An SSE client hanging up (closed tab, proxy timeout) closes the generator. The provider
    # call is already billed by then, so abandoning the write would make the spend invisible and
    # leave the persisted user turn with no reply at all.
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    _seed_chat(store, source_channel_ids=[channel.id])
    embedding_provider, chat_provider = _providers(
        stream_chunks=make_chat_chunks(["first ", "second ", "third"])
    )

    result = _answer(store, embedding_provider, chat_provider)
    stream = result.text_stream
    next(stream)  # consume one token, then walk away
    stream.close()

    assistant = [m for m in store.messages if m.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].content == "first "  # only what actually made it to the consumer
    assert len(store.usage_events) == 1


def test_ask_records_usage_when_the_consumer_disconnects_mid_stream():
    # /ask persists nothing else, so the usage row is the ONLY record that the spend happened.
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    embedding_provider, chat_provider = _providers(
        stream_chunks=make_chat_chunks(["first ", "second"])
    )

    result = ask(
        store,
        embedding_provider,
        chat_provider,
        scope=ChatScope(source_channel_ids=(channel.id,), voice_channel_id=None),
        user_text="hi",
        chat_model="gpt-4.1-mini",
    )
    stream = result.text_stream
    next(stream)
    stream.close()

    assert len(store.usage_events) == 1
    assert store.usage_events[0].chat_id is None
    assert store.messages == []


# --- multi-source scope: voice ordering, filtering, per-source quota ----------------------


def test_voice_channels_context_comes_first_regardless_of_source_order():
    alex, dan = (
        _make_channel(title="Alex", handle="@alex"),
        _make_channel(title="Dan", handle="@dan"),
    )
    store = FakeVectorStore(
        search_results=_make_search_results(1, alex) + _make_search_results(1, dan)
    )
    _seed_channel(store, alex)
    _seed_channel(store, dan)
    # Sources listed Dan-then-Alex, but voice=Alex — Alex's blocks must render first.
    _seed_chat(store, source_channel_ids=[dan.id, alex.id], voice_channel_id=alex.id)
    embedding_provider, chat_provider = _providers(stream_chunks=make_chat_chunks(["ok"]))

    result = _answer(store, embedding_provider, chat_provider)
    list(result.text_stream)

    system_prompt = chat_provider.last_messages[0].content
    assert system_prompt.index("Alex") < system_prompt.index("Dan")


def test_scope_filtering_excludes_unselected_channel_from_search_and_context():
    alex, dan = _make_channel(title="Alex"), _make_channel(title="Dan")
    store = FakeVectorStore(
        search_results=_make_search_results(1, alex) + _make_search_results(1, dan)
    )
    _seed_channel(store, alex)
    _seed_channel(store, dan)
    _seed_chat(store, source_channel_ids=[alex.id])  # Dan not selected
    embedding_provider, chat_provider = _providers(stream_chunks=make_chat_chunks(["ok"]))

    result = _answer(store, embedding_provider, chat_provider)
    list(result.text_stream)

    # Retrieval (context-building) calls always carry the question as query_text; the probe
    # call for unselected channels does not — a clean, mode-independent way to tell them apart.
    retrieval_calls = [c for c in store.search_calls if c["query_text"] is not None]
    assert all(dan.id not in call["channel_ids"] for call in retrieval_calls)
    system_prompt = chat_provider.last_messages[0].content
    assert "transcript text 1" in system_prompt  # Alex's chunk is present
    assert "=== SOURCE — Dan" not in system_prompt


def test_per_source_retrieval_quota_splits_top_k_across_sources():
    alex, dan = _make_channel(title="Alex"), _make_channel(title="Dan")
    store = FakeVectorStore()
    _seed_channel(store, alex)
    _seed_channel(store, dan)
    _seed_chat(store, source_channel_ids=[alex.id, dan.id])
    embedding_provider, chat_provider = _providers(stream_chunks=make_chat_chunks(["ok"]))

    _answer(store, embedding_provider, chat_provider, top_k=8)

    retrieval_calls = [c for c in store.search_calls if c["query_text"] is not None]
    assert len(retrieval_calls) == 2
    assert all(c["top_k"] == 4 for c in retrieval_calls)  # 8 // 2 sources


def test_per_source_retrieval_quota_is_floored_at_minimum():
    channels = [_make_channel(title=f"C{i}") for i in range(4)]
    store = FakeVectorStore()
    for c in channels:
        _seed_channel(store, c)
    _seed_chat(store, source_channel_ids=[c.id for c in channels])
    embedding_provider, chat_provider = _providers(stream_chunks=make_chat_chunks(["ok"]))

    _answer(store, embedding_provider, chat_provider, top_k=8)  # 8 // 4 == 2, floored to 3

    retrieval_calls = [c for c in store.search_calls if c["query_text"] is not None]
    assert len(retrieval_calls) == 4
    assert all(c["top_k"] == MIN_BLOCKS_PER_SOURCE for c in retrieval_calls)


# --- "try adding X" probe -------------------------------------------------------------------


def test_probe_suggests_unselected_channel_when_no_citations_and_score_above_floor():
    alex, dan = _make_channel(title="Alex"), _make_channel(title="Dan")
    dan_hit = _make_search_results(1, dan)[0]
    dan_hit.score = 0.5
    store = FakeVectorStore(search_results=[dan_hit])
    _seed_channel(store, alex)
    _seed_channel(store, dan)
    _seed_chat(store, source_channel_ids=[alex.id])
    embedding_provider, chat_provider = _providers(
        stream_chunks=make_chat_chunks(["The selected sources don't cover this."])
    )

    result = _answer(store, embedding_provider, chat_provider)
    list(result.text_stream)

    assert [c.id for c in result.suggested_source_channels] == [dan.id]
    system_prompt = chat_provider.last_messages[0].content
    assert "Dan" in system_prompt
    assert "not currently selected" in system_prompt.lower()


def test_probe_not_run_when_all_channels_are_already_selected():
    alex = _make_channel(title="Alex")
    store = FakeVectorStore()
    _seed_channel(store, alex)
    _seed_chat(store, source_channel_ids=[alex.id])
    embedding_provider, chat_provider = _providers(stream_chunks=make_chat_chunks(["ok"]))

    _answer(store, embedding_provider, chat_provider)

    probe_calls = [c for c in store.search_calls if c["query_text"] is None]
    assert probe_calls == []


def test_probe_hit_below_the_score_floor_is_ignored():
    alex, dan = _make_channel(title="Alex"), _make_channel(title="Dan")
    dan_hit = _make_search_results(1, dan)[0]
    dan_hit.score = 0.1  # below PROBE_MIN_SCORE
    store = FakeVectorStore(search_results=[dan_hit])
    _seed_channel(store, alex)
    _seed_channel(store, dan)
    _seed_chat(store, source_channel_ids=[alex.id])
    embedding_provider, chat_provider = _providers(stream_chunks=make_chat_chunks(["no coverage"]))

    result = _answer(store, embedding_provider, chat_provider)
    list(result.text_stream)

    assert result.suggested_source_channels == []


def test_probe_suggestion_absent_when_the_reply_has_citations():
    alex, dan = _make_channel(title="Alex"), _make_channel(title="Dan")
    dan_hit = _make_search_results(1, dan)[0]
    dan_hit.score = 0.9
    alex_hit = _make_search_results(1, alex)[0]
    store = FakeVectorStore(search_results=[alex_hit, dan_hit])
    _seed_channel(store, alex)
    _seed_channel(store, dan)
    _seed_chat(store, source_channel_ids=[alex.id])
    embedding_provider, chat_provider = _providers(
        stream_chunks=make_chat_chunks(["Answered from Alex [1]."])
    )

    result = _answer(store, embedding_provider, chat_provider)
    list(result.text_stream)

    assert result.suggested_source_channels == []


# --- ask() — stateless one-shot -------------------------------------------------------------


def test_ask_persists_no_messages_but_records_one_usage_event_with_null_chat_id():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    embedding_provider, chat_provider = _providers(stream_chunks=make_chat_chunks(["ok"]))
    scope = ChatScope(source_channel_ids=(channel.id,), voice_channel_id=None)

    result = ask(
        store,
        embedding_provider,
        chat_provider,
        scope=scope,
        user_text="hi",
        chat_model="gpt-4.1-mini",
    )
    list(result.text_stream)

    assert store.messages == []
    assert len(store.usage_events) == 1
    assert store.usage_events[0].chat_id is None
    assert store.usage_events[0].source_channel_ids == [channel.id]


# --- voice degrades to Neutral when persona is since-disabled ------------------------------


def test_answer_degrades_to_neutral_when_voice_persona_disabled_since_chat_was_created():
    channel = _make_channel(title="Alex")
    store = FakeVectorStore(channel=channel)
    _seed_chat(store, source_channel_ids=[channel.id], voice_channel_id=channel.id)
    # Persona gets disabled AFTER the chat was created with Alex as voice.
    set_persona(store, channel.id, Persona(enabled=False))
    embedding_provider, chat_provider = _providers(stream_chunks=make_chat_chunks(["ok"]))

    result = _answer(store, embedding_provider, chat_provider)
    list(result.text_stream)

    assert result.voice_channel is None
    assert result.disclosure is None
    system_prompt = chat_provider.last_messages[0].content
    assert "Neutral" in system_prompt
