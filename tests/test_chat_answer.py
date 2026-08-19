from uuid import UUID, uuid4

import pytest

from core.chat.answer import answer
from core.chat.errors import ChatNotFoundError, EmbeddingModelMismatchError, QuestionTooLongError
from core.constants import EMBEDDING_DIM, MAX_QUESTION_CHARS
from core.models import Channel, Message
from core.search.search import ChannelNotFoundError
from core.store.base import SearchResult
from tests.fakes import FakeLLMProvider, FakeVectorStore, make_chat_chunks

CHAT_ID = UUID("00000000-0000-0000-0000-000000000c4a")


def _make_channel(title="Some Channel") -> Channel:
    return Channel(
        id=uuid4(),
        yt_channel_id="UC" + "x" * 22,
        handle="@some",
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


def _providers(*, stream_chunks=None, embedding_dim=EMBEDDING_DIM, raise_after=None):
    embedding_provider = FakeLLMProvider(embedding_dim=embedding_dim)
    chat_provider = FakeLLMProvider(
        embedding_dim=EMBEDDING_DIM, stream_chunks=stream_chunks, raise_after=raise_after
    )
    return embedding_provider, chat_provider


def _answer(store, embedding_provider, chat_provider, *, channel_id, user_text="hi", **kwargs):
    return answer(
        store,
        embedding_provider,
        chat_provider,
        channel_id=channel_id,
        chat_id=CHAT_ID,
        user_text=user_text,
        chat_model="gpt-4.1-mini",
        **kwargs,
    )


def test_answer_raises_channel_not_found_for_unknown_channel():
    store = FakeVectorStore(channel=None)
    embedding_provider, chat_provider = _providers()

    with pytest.raises(ChannelNotFoundError):
        _answer(store, embedding_provider, chat_provider, channel_id=uuid4())


def test_answer_raises_chat_not_found_when_chat_belongs_to_another_channel():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel, chat_channel_id=uuid4())
    embedding_provider, chat_provider = _providers()

    with pytest.raises(ChatNotFoundError):
        _answer(store, embedding_provider, chat_provider, channel_id=channel.id)

    assert store.messages == []


def test_answer_raises_question_too_long_before_touching_providers():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    embedding_provider, chat_provider = _providers()

    with pytest.raises(QuestionTooLongError):
        _answer(
            store,
            embedding_provider,
            chat_provider,
            channel_id=channel.id,
            user_text="x" * (MAX_QUESTION_CHARS + 1),
        )

    assert store.messages == []


def test_answer_raises_embedding_model_mismatch_when_dims_differ():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    embedding_provider, chat_provider = _providers(embedding_dim=EMBEDDING_DIM - 1)

    with pytest.raises(EmbeddingModelMismatchError):
        _answer(store, embedding_provider, chat_provider, channel_id=channel.id)


def test_answer_persists_user_message_before_stream_is_consumed():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    embedding_provider, chat_provider = _providers(stream_chunks=make_chat_chunks(["hello"]))

    _answer(store, embedding_provider, chat_provider, channel_id=channel.id)

    assert len(store.messages) == 1
    assert store.messages[0].role == "user"
    assert store.messages[0].content == "hi"


def test_answer_streams_text_chunks_in_order():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    embedding_provider, chat_provider = _providers(
        stream_chunks=make_chat_chunks(["Hello", " world"])
    )

    result = _answer(store, embedding_provider, chat_provider, channel_id=channel.id)

    assert list(result.text_stream) == ["Hello", " world"]


def test_answer_persists_assistant_message_and_citations_after_stream_exhausted():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel, search_results=_make_search_results(1, channel))
    embedding_provider, chat_provider = _providers(
        stream_chunks=make_chat_chunks(["The answer is [1]."])
    )

    result = _answer(store, embedding_provider, chat_provider, channel_id=channel.id)
    list(result.text_stream)  # exhaust

    assert len(result.citations) == 1
    assistant_messages = [m for m in store.messages if m.role == "assistant"]
    assert len(assistant_messages) == 1
    assert assistant_messages[0].citations[0]["n"] == 1


def test_answer_records_usage_event_with_est_cost_after_stream_exhausted():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    embedding_provider, chat_provider = _providers(
        stream_chunks=make_chat_chunks(["hi"], tokens_in=1000, tokens_out=1000)
    )

    result = _answer(store, embedding_provider, chat_provider, channel_id=channel.id)
    list(result.text_stream)

    assert len(store.usage_events) == 1
    assert store.usage_events[0].tokens_in == 1000
    assert result.usage.est_cost_usd == pytest.approx(0.0004 + 0.0016)


def test_refusal_response_persists_no_citations():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    embedding_provider, chat_provider = _providers(
        stream_chunks=make_chat_chunks(["This channel doesn't cover that."])
    )

    result = _answer(
        store, embedding_provider, chat_provider, channel_id=channel.id, user_text="off topic"
    )
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
    embedding_provider, chat_provider = _providers(stream_chunks=make_chat_chunks(["ok"]))

    result = _answer(
        store,
        embedding_provider,
        chat_provider,
        channel_id=channel.id,
        user_text="new",
        history_window=3,
    )
    list(result.text_stream)  # stream_chat() isn't called until the stream is consumed

    sent_history_texts = [m.content for m in chat_provider.last_messages if m.role == "user"]
    # the last 3 of the 10 seeded history rows, plus the new user turn itself
    assert sent_history_texts == ["q7", "q8", "q9", "new"]


def test_answer_does_not_persist_assistant_turn_when_provider_raises_mid_stream():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    embedding_provider, chat_provider = _providers(
        stream_chunks=make_chat_chunks(["partial"]), raise_after=0
    )

    result = _answer(store, embedding_provider, chat_provider, channel_id=channel.id)

    with pytest.raises(RuntimeError, match="simulated provider failure"):
        list(result.text_stream)

    assert all(m.role != "assistant" for m in store.messages)
    assert store.usage_events == []
