from uuid import UUID, uuid4

import pytest

from core.chat.answer import answer
from core.config import ConfigError, get_settings
from core.constants import DEFAULT_RETRIEVAL_MODE, EMBEDDING_DIM
from core.search.hybrid import prepare_lexical_query, rrf_fuse
from core.store.base import SearchResult
from tests.fakes import FakeLLMProvider, FakeVectorStore, make_chat_chunks
from tests.test_chat_answer import CHAT_ID, _make_channel, _seed_chat

_ENV_VARS = (
    "DATABASE_URL",
    "INSTANCE_MODE",
    "CHAT_PROVIDER",
    "CHAT_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "RAW_CAPTIONS_DIR",
    "RETRIEVAL_MODE",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    # get_settings() is lru_cached and load_dotenv() would read the developer's real .env —
    # neutralise both so each test sees exactly the environment it sets up (same pattern as
    # tests/test_doctor.py's fixture).
    monkeypatch.setattr("core.config.load_dotenv", lambda *a, **k: False)
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _id(n: int) -> UUID:
    return UUID(int=n)


def test_rrf_fuse_ranks_items_appearing_in_both_lists_highest():
    dense = [_id(1), _id(2), _id(3)]
    lexical = [_id(2), _id(1), _id(3)]

    fused = rrf_fuse([dense, lexical])
    ids = [item_id for item_id, _score in fused]

    # 1 and 2 each appear near the top of both lists; 3 is last in both.
    assert set(ids[:2]) == {_id(1), _id(2)}
    assert ids[-1] == _id(3)


def test_rrf_fuse_rare_exact_term_wins_when_lexical_ranks_it_first():
    # Dense (cosine) buries the exact-term chunk at rank 30; lexical (full-text) ranks it 1st.
    dense = [_id(i) for i in range(100) if i != 30]
    dense.insert(30, _id(30))
    lexical = [_id(30), _id(1), _id(2)]

    fused = rrf_fuse([dense, lexical])
    ids = [item_id for item_id, _score in fused]

    assert ids.index(_id(30)) < 3


def test_rrf_fuse_scores_use_the_k_damping_constant():
    fused = rrf_fuse([[_id(1)]], k=10)
    assert fused == [(_id(1), pytest.approx(1 / 11))]


def test_rrf_fuse_ties_break_by_first_appearance_across_lists():
    fused = rrf_fuse([[_id(1)], [_id(2)]])
    # Both rank 0 in their own list — same score — so id(1) (seen first) sorts first.
    assert [item_id for item_id, _ in fused] == [_id(1), _id(2)]


def test_rrf_fuse_handles_empty_lists():
    assert rrf_fuse([]) == []
    assert rrf_fuse([[], []]) == []


def test_prepare_lexical_query_collapses_whitespace():
    assert prepare_lexical_query("  how   does   play   work  ") == "how does play work"


@pytest.mark.parametrize("text", ['"exact phrase" -excluded term', "$100M offer", "CLOSER"])
def test_prepare_lexical_query_preserves_quotes_and_special_chars(text):
    assert prepare_lexical_query(text) == text


@pytest.mark.parametrize("text", ["", "   ", "...", "---", "!!!"])
def test_prepare_lexical_query_returns_none_for_content_free_text(text):
    assert prepare_lexical_query(text) is None


def test_prepare_lexical_query_caps_length():
    huge = "word " * 1000
    prepared = prepare_lexical_query(huge)
    assert prepared is not None
    assert len(prepared) <= 2000


def test_settings_retrieval_mode_defaults_to_hybrid(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")

    assert get_settings().retrieval_mode == DEFAULT_RETRIEVAL_MODE == "hybrid"


def test_settings_retrieval_mode_rejects_unknown_value(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    monkeypatch.setenv("RETRIEVAL_MODE", "bm25")

    with pytest.raises(ConfigError):
        get_settings()


def test_settings_retrieval_mode_accepts_dense(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    monkeypatch.setenv("RETRIEVAL_MODE", "dense")

    assert get_settings().retrieval_mode == "dense"


def test_fake_store_search_scopes_results_to_requested_channel_ids():
    channel_a, channel_b = uuid4(), uuid4()
    results = [
        SearchResult(
            chunk_id=uuid4(),
            video_id=uuid4(),
            yt_video_id="a",
            video_title="A",
            text="from A",
            t_start_s=0.0,
            t_end_s=1.0,
            score=0.9,
            channel_id=channel_a,
            channel_title="A",
            channel_handle="@a",
        ),
        SearchResult(
            chunk_id=uuid4(),
            video_id=uuid4(),
            yt_video_id="b",
            video_title="B",
            text="from B",
            t_start_s=0.0,
            t_end_s=1.0,
            score=0.9,
            channel_id=channel_b,
            channel_title="B",
            channel_handle="@b",
        ),
    ]
    store = FakeVectorStore(search_results=results)

    scoped = store.search(channel_ids=[channel_a], query_embedding=[0.0], top_k=8)

    assert [r.text for r in scoped] == ["from A"]


def test_answer_passes_query_text_and_mode_through_to_search():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    _seed_chat(store, source_channel_ids=[channel.id])
    embedding_provider = FakeLLMProvider(embedding_dim=EMBEDDING_DIM)
    chat_provider = FakeLLMProvider(
        embedding_dim=EMBEDDING_DIM, stream_chunks=make_chat_chunks(["ok"])
    )

    result = answer(
        store,
        embedding_provider,
        chat_provider,
        chat_id=CHAT_ID,
        user_text="what is the CLOSER framework?",
        chat_model="gpt-4.1-mini",
        retrieval_mode="dense",
    )
    list(result.text_stream)

    assert len(store.search_calls) == 1
    call = store.search_calls[0]
    assert call["channel_ids"] == [channel.id]
    assert call["query_text"] == "what is the CLOSER framework?"
    assert call["mode"] == "dense"
