from uuid import uuid4

from core.chat.prompt import ContextGroup, build_context_blocks, build_messages, build_system_prompt
from core.models import Channel, Message
from core.store.base import SearchResult


def _make_channel(*, title="Alex", handle=None) -> Channel:
    return Channel(
        id=uuid4(),
        yt_channel_id="UC" + "x" * 22,
        handle=handle or f"@{title.lower()}",
        title=title,
        thumbnail_url=None,
        branding={},
        created_at=None,
    )


def _make_result(
    channel: Channel, *, video_title="Some Video", t_start_s=125.0, text="chunk text"
) -> SearchResult:
    return SearchResult(
        chunk_id=uuid4(),
        video_id=uuid4(),
        yt_video_id="abc123",
        video_title=video_title,
        text=text,
        t_start_s=t_start_s,
        t_end_s=t_start_s + 10,
        score=0.9,
        channel_id=channel.id,
        channel_title=channel.title,
        channel_handle=channel.handle,
    )


def _make_message(role: str, content: str) -> Message:
    return Message(id=uuid4(), chat_id=uuid4(), role=role, content=content)


# --- build_context_blocks: grouping/ranges/numbering --------------------------------------


def test_context_blocks_are_numbered_from_one_within_a_single_group():
    channel = _make_channel()
    groups = [
        ContextGroup(
            channel=channel,
            results=[
                _make_result(channel, video_title="First"),
                _make_result(channel, video_title="Second"),
            ],
        )
    ]
    blocks = build_context_blocks(groups)

    assert '[1] "First"' in blocks
    assert '[2] "Second"' in blocks


def test_context_blocks_include_video_title_and_mmss_timestamp():
    channel = _make_channel()
    groups = [
        ContextGroup(
            channel=channel,
            results=[_make_result(channel, video_title="My Video", t_start_s=125.0)],
        )
    ]
    blocks = build_context_blocks(groups)

    assert "My Video" in blocks
    assert "2:05" in blocks


def test_context_blocks_number_continuously_across_groups_and_label_each_source():
    alex, dan = (
        _make_channel(title="Alex", handle="@alex"),
        _make_channel(title="Dan", handle="@dan"),
    )
    groups = [
        ContextGroup(channel=alex, results=[_make_result(alex), _make_result(alex)]),
        ContextGroup(channel=dan, results=[_make_result(dan), _make_result(dan)]),
    ]
    blocks = build_context_blocks(groups)

    assert "=== SOURCE — Alex (@alex) — blocks [1]–[2] ===" in blocks
    assert "=== SOURCE — Dan (@dan) — blocks [3]–[4] ===" in blocks
    assert "[3]" in blocks and "[4]" in blocks


def test_context_blocks_omit_a_source_with_zero_results():
    alex, dan = _make_channel(title="Alex"), _make_channel(title="Dan")
    groups = [
        ContextGroup(channel=alex, results=[]),
        ContextGroup(channel=dan, results=[_make_result(dan)]),
    ]
    blocks = build_context_blocks(groups)

    assert "Alex" not in blocks  # no header rendered for the empty group
    assert "=== SOURCE — Dan" in blocks
    assert "[1]" in blocks  # numbering isn't shifted by the skipped empty group


def test_empty_context_produces_no_matching_context_message():
    channel = _make_channel()
    blocks = build_context_blocks([ContextGroup(channel=channel, results=[])])
    assert "No matching context" in blocks


# --- system prompt: sources list, voice, attribution, honesty, candidates -----------------


def test_sources_list_shows_block_ranges_per_source():
    alex, dan = (
        _make_channel(title="Alex", handle="@alex"),
        _make_channel(title="Dan", handle="@dan"),
    )
    groups = [
        ContextGroup(channel=alex, results=[_make_result(alex)] * 3),
        ContextGroup(channel=dan, results=[_make_result(dan)] * 2),
    ]
    prompt = build_system_prompt(groups=groups, voice=None, persona_section=None, candidates=[])

    assert "- Alex (@alex): context blocks [1]–[3]" in prompt
    assert "- Dan (@dan): context blocks [4]–[5]" in prompt


def test_sources_list_notes_a_source_with_no_matching_context():
    alex, dan = _make_channel(title="Alex"), _make_channel(title="Dan")
    groups = [
        ContextGroup(channel=alex, results=[]),
        ContextGroup(channel=dan, results=[_make_result(dan)]),
    ]
    prompt = build_system_prompt(groups=groups, voice=None, persona_section=None, candidates=[])

    assert "- Alex (@alex): no matching context for this question" in prompt


def test_neutral_voice_forbids_first_person_and_has_no_persona_section():
    channel = _make_channel(title="Alex")
    groups = [ContextGroup(channel=channel, results=[_make_result(channel)])]
    prompt = build_system_prompt(
        groups=groups, voice=None, persona_section="Tone & energy\nHigh energy.", candidates=[]
    )

    assert "Neutral" in prompt
    assert "never use the first person for any creator" in prompt
    assert "Tone & energy" not in prompt  # persona_section is ignored when voice is Neutral
    assert "Name the creator before each point" in prompt


def test_creator_voice_restricts_first_person_to_its_own_block_range_and_includes_persona():
    alex, dan = _make_channel(title="Alex"), _make_channel(title="Dan")
    groups = [
        ContextGroup(channel=alex, results=[_make_result(alex), _make_result(alex)]),
        ContextGroup(channel=dan, results=[_make_result(dan)]),
    ]
    prompt = build_system_prompt(
        groups=groups, voice=alex, persona_section="Tone & energy\nDirect.", candidates=[]
    )

    assert "AI stand-in for Alex" in prompt
    assert "ONLY blocks [1]–[2]" in prompt
    assert "Tone & energy\nDirect." in prompt
    assert "Voice changes delivery only" in prompt
    assert "never changes who an idea belongs to" in prompt
    # Attribution rule for the OTHER creator's material is present.
    assert "introduced with that creator's name" in prompt
    assert "misattributes their work" in prompt


def test_single_source_voice_omits_the_multi_source_disagreement_rules():
    alex = _make_channel(title="Alex")
    groups = [ContextGroup(channel=alex, results=[_make_result(alex)])]
    prompt = build_system_prompt(groups=groups, voice=alex, persona_section=None, candidates=[])

    assert "creators disagree" not in prompt
    assert "doesn't cover it" not in prompt


def test_multi_source_prompt_includes_disagreement_and_no_coverage_rules():
    alex, dan = _make_channel(title="Alex"), _make_channel(title="Dan")
    groups = [
        ContextGroup(channel=alex, results=[_make_result(alex)]),
        ContextGroup(channel=dan, results=[_make_result(dan)]),
    ]
    prompt = build_system_prompt(groups=groups, voice=alex, persona_section=None, candidates=[])

    assert "creators disagree" in prompt
    assert "Do not blend them into one consensus view" in prompt


def test_honesty_section_names_the_active_voice_and_includes_disclosure():
    alex = _make_channel(title="Alex")
    groups = [ContextGroup(channel=alex, results=[_make_result(alex)])]
    prompt = build_system_prompt(groups=groups, voice=alex, persona_section=None, candidates=[])

    assert "You are an AI, not Alex" in prompt
    assert "AI trained on Alex's public videos — not Alex." in prompt
    assert "you may say it in voice" in prompt


def test_honesty_section_is_generic_for_neutral_voice():
    channel = _make_channel(title="Alex")
    groups = [ContextGroup(channel=channel, results=[_make_result(channel)])]
    prompt = build_system_prompt(groups=groups, voice=None, persona_section=None, candidates=[])

    assert "You are an AI, not any of these creators" in prompt


def test_candidates_hint_present_only_when_candidates_given():
    channel = _make_channel(title="Alex")
    groups = [ContextGroup(channel=channel, results=[_make_result(channel)])]

    without = build_system_prompt(groups=groups, voice=None, persona_section=None, candidates=[])
    assert "not currently selected" not in without.lower()

    dan = _make_channel(title="Dan")
    with_candidates = build_system_prompt(
        groups=groups, voice=None, persona_section=None, candidates=[dan]
    )
    assert "not currently selected" in with_candidates.lower()
    assert "Dan" in with_candidates
    assert "never describe or cite their content" in with_candidates.lower()


# --- build_messages: history handling (unchanged behavior) --------------------------------


def _one_group(channel):
    return [ContextGroup(channel=channel, results=[])]


def test_build_messages_orders_system_then_history_then_new_user_turn():
    channel = _make_channel()
    history = [
        _make_message("user", "earlier question"),
        _make_message("assistant", "earlier answer"),
    ]
    messages = build_messages(
        groups=_one_group(channel),
        voice=None,
        persona_section=None,
        candidates=[],
        history=history,
        user_text="new question",
    )

    assert messages[0].role == "system"
    assert messages[1].role == "user"
    assert messages[1].content == "earlier question"
    assert messages[2].role == "assistant"
    assert messages[2].content == "earlier answer"
    assert messages[-1].role == "user"
    assert messages[-1].content == "new question"


def test_build_messages_drops_stray_system_role_history_rows():
    channel = _make_channel()
    history = [_make_message("system", "should never appear twice"), _make_message("user", "q")]
    messages = build_messages(
        groups=_one_group(channel),
        voice=None,
        persona_section=None,
        candidates=[],
        history=history,
        user_text="new",
    )

    system_messages = [m for m in messages if m.role == "system"]
    assert len(system_messages) == 1
    assert "should never appear twice" not in system_messages[0].content


def test_history_window_never_starts_with_an_assistant_turn():
    channel = _make_channel()
    # A failed earlier turn leaves an unpaired user message, so a fixed-size window over the
    # history can land on an assistant turn first — which Anthropic's API has rejected.
    history = [
        _make_message("assistant", "orphaned answer from the window's cut"),
        _make_message("user", "q2"),
        _make_message("assistant", "a2"),
    ]
    messages = build_messages(
        groups=_one_group(channel),
        voice=None,
        persona_section=None,
        candidates=[],
        history=history,
        user_text="new",
    )

    non_system = [m for m in messages if m.role != "system"]
    assert non_system[0].role == "user"
    assert non_system[0].content == "q2"
    assert "orphaned answer" not in " ".join(m.content for m in messages)


def test_stale_citation_markers_are_stripped_from_prior_assistant_turns():
    channel = _make_channel()
    history = [
        _make_message("user", "q1"),
        _make_message("assistant", "Play boosts creativity [1][3], per Bajaj [2]."),
    ]
    messages = build_messages(
        groups=_one_group(channel),
        voice=None,
        persona_section=None,
        candidates=[],
        history=history,
        user_text="new",
    )

    prior_answer = [m for m in messages if m.role == "assistant"][0].content
    assert "[" not in prior_answer
    assert "Play boosts creativity" in prior_answer


def test_stale_citation_stripping_leaves_user_turns_untouched():
    channel = _make_channel()
    history = [_make_message("user", "what does [1] mean in your last answer?")]
    messages = build_messages(
        groups=_one_group(channel),
        voice=None,
        persona_section=None,
        candidates=[],
        history=history,
        user_text="new",
    )

    assert messages[1].content == "what does [1] mean in your last answer?"
