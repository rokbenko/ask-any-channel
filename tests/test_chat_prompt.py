from uuid import uuid4

from core.chat.prompt import build_context_blocks, build_messages
from core.models import Message
from core.store.base import SearchResult


def _make_result(*, video_title="Some Video", t_start_s=125.0, text="chunk text") -> SearchResult:
    return SearchResult(
        chunk_id=uuid4(),
        video_id=uuid4(),
        yt_video_id="abc123",
        video_title=video_title,
        text=text,
        t_start_s=t_start_s,
        t_end_s=t_start_s + 10,
        score=0.9,
        channel_id=uuid4(),
        channel_title="Some Channel",
        channel_handle="@some",
    )


def _make_message(role: str, content: str) -> Message:
    return Message(id=uuid4(), chat_id=uuid4(), role=role, content=content)


def test_context_blocks_are_numbered_from_one():
    context = [_make_result(video_title="First"), _make_result(video_title="Second")]
    blocks = build_context_blocks(context)

    assert blocks.startswith("[1]")
    assert "[2]" in blocks


def test_context_blocks_include_video_title_and_mmss_timestamp():
    context = [_make_result(video_title="My Video", t_start_s=125.0)]
    blocks = build_context_blocks(context)

    assert "My Video" in blocks
    assert "2:05" in blocks


def test_empty_context_produces_no_matching_context_message():
    blocks = build_context_blocks([])
    assert "No matching context" in blocks


def test_build_messages_orders_system_then_history_then_new_user_turn():
    history = [
        _make_message("user", "earlier question"),
        _make_message("assistant", "earlier answer"),
    ]
    messages = build_messages(
        channel_title="Some Channel", history=history, context=[], user_text="new question"
    )

    assert messages[0].role == "system"
    assert messages[1].role == "user"
    assert messages[1].content == "earlier question"
    assert messages[2].role == "assistant"
    assert messages[2].content == "earlier answer"
    assert messages[-1].role == "user"
    assert messages[-1].content == "new question"


def test_build_messages_drops_stray_system_role_history_rows():
    history = [_make_message("system", "should never appear twice"), _make_message("user", "q")]
    messages = build_messages(channel_title="Ch", history=history, context=[], user_text="new")

    system_messages = [m for m in messages if m.role == "system"]
    assert len(system_messages) == 1
    assert "should never appear twice" not in system_messages[0].content


def test_history_window_never_starts_with_an_assistant_turn():
    # A failed earlier turn leaves an unpaired user message, so a fixed-size window over the
    # history can land on an assistant turn first — which Anthropic's API has rejected.
    history = [
        _make_message("assistant", "orphaned answer from the window's cut"),
        _make_message("user", "q2"),
        _make_message("assistant", "a2"),
    ]
    messages = build_messages(channel_title="Ch", history=history, context=[], user_text="new")

    non_system = [m for m in messages if m.role != "system"]
    assert non_system[0].role == "user"
    assert non_system[0].content == "q2"
    assert "orphaned answer" not in " ".join(m.content for m in messages)


def test_stale_citation_markers_are_stripped_from_prior_assistant_turns():
    history = [
        _make_message("user", "q1"),
        _make_message("assistant", "Play boosts creativity [1][3], per Bajaj [2]."),
    ]
    messages = build_messages(channel_title="Ch", history=history, context=[], user_text="new")

    prior_answer = [m for m in messages if m.role == "assistant"][0].content
    assert "[" not in prior_answer
    assert "Play boosts creativity" in prior_answer


def test_stale_citation_stripping_leaves_user_turns_untouched():
    history = [_make_message("user", "what does [1] mean in your last answer?")]
    messages = build_messages(channel_title="Ch", history=history, context=[], user_text="new")

    assert messages[1].content == "what does [1] mean in your last answer?"
