"""apps/api — FastAPI HTTP surface. TestClient drives the real app with FakeVectorStore/
FakeLLMProvider injected via create_app()'s dependency_overrides seam, so no real Postgres or
vendor SDK is touched."""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from core.config import Settings
from core.constants import EMBEDDING_DIM
from core.models import Channel
from core.store.base import SearchResult
from tests.fakes import FakeLLMProvider, FakeVectorStore, make_chat_chunks


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
    }
    fields.update(overrides)
    return Settings(**fields)


def _make_channel(title="Alex", handle=None) -> Channel:
    return Channel(
        id=uuid4(),
        yt_channel_id="UC" + "x" * 22,
        handle=handle or f"@{title.lower()}",
        title=title,
        thumbnail_url=None,
        branding={},
        created_at=datetime.now(UTC),
    )


def _client(*, store=None, settings=None, chat_reply="ok", stream_chunks=None) -> TestClient:
    from apps.api.main import create_app

    embedding_provider = FakeLLMProvider(embedding_dim=EMBEDDING_DIM)
    chat_provider = FakeLLMProvider(
        embedding_dim=EMBEDDING_DIM,
        chat_reply=chat_reply,
        stream_chunks=stream_chunks or make_chat_chunks(["ok"]),
    )
    app = create_app(
        store=store or FakeVectorStore(),
        settings=settings or _settings(),
        providers=(embedding_provider, chat_provider, "gpt-4.1-mini"),
    )
    return TestClient(app)


# --- health -----------------------------------------------------------------------------


def test_healthz():
    client = _client()
    resp = client.get("/api/v1/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# --- channels ------------------------------------------------------------------------------


def test_list_channels_includes_persona_and_disclosure_fields():
    channel = _make_channel(title="Alex")
    store = FakeVectorStore(channel=channel)
    client = _client(store=store)

    resp = client.get("/api/v1/channels")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["title"] == "Alex"
    assert body[0]["persona"]["enabled"] is True
    assert body[0]["persona"]["disclosure"] == "AI trained on Alex's public videos — not Alex."


def test_get_channel_by_handle():
    channel = _make_channel(title="Alex")
    store = FakeVectorStore(channel=channel)
    client = _client(store=store)

    resp = client.get(f"/api/v1/channels/{channel.handle}")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(channel.id)


def test_get_channel_unknown_ref_is_404():
    client = _client()
    resp = client.get("/api/v1/channels/@nope")
    assert resp.status_code == 404


# --- chat lifecycle --------------------------------------------------------------------


def test_create_chat_with_sources_and_voice():
    channel = _make_channel(title="Alex")
    store = FakeVectorStore(channel=channel)
    client = _client(store=store)

    resp = client.post("/api/v1/chats", json={"sources": [channel.handle], "voice": channel.handle})

    assert resp.status_code == 201
    body = resp.json()
    assert body["sources"] == [str(channel.id)]
    assert body["voice"] == str(channel.id)
    assert body["disclosure"] == "AI trained on Alex's public videos — not Alex."


def test_create_chat_voice_not_in_sources_is_422():
    a, b = _make_channel("A"), _make_channel("B")
    store = FakeVectorStore()
    store.channels[a.id] = a
    store.channels[b.id] = b
    client = _client(store=store)

    resp = client.post("/api/v1/chats", json={"sources": [a.handle], "voice": b.handle})

    assert resp.status_code == 422


def test_create_chat_unknown_source_ref_is_404():
    client = _client()
    resp = client.post("/api/v1/chats", json={"sources": ["@nope"]})
    assert resp.status_code == 404


def test_create_chat_empty_sources_is_422():
    client = _client()
    resp = client.post("/api/v1/chats", json={"sources": []})
    assert resp.status_code == 422


def test_get_chat_and_list_messages():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    client = _client(store=store)

    created = client.post("/api/v1/chats", json={"sources": [channel.handle]}).json()
    chat_id = created["id"]

    got = client.get(f"/api/v1/chats/{chat_id}")
    assert got.status_code == 200
    assert got.json()["id"] == chat_id

    messages = client.get(f"/api/v1/chats/{chat_id}/messages")
    assert messages.status_code == 200
    assert messages.json() == []


def test_get_chat_unknown_id_is_404():
    client = _client()
    resp = client.get(f"/api/v1/chats/{uuid4()}")
    assert resp.status_code == 404


def test_post_message_streams_tokens_then_a_done_event_with_citations():
    channel = _make_channel(title="Alex")
    result = SearchResult(
        chunk_id=uuid4(),
        video_id=uuid4(),
        yt_video_id="vid1",
        video_title="A Talk",
        text="some transcript text",
        t_start_s=30.0,
        t_end_s=40.0,
        score=0.9,
        channel_id=channel.id,
        channel_title=channel.title,
        channel_handle=channel.handle,
    )
    store = FakeVectorStore(channel=channel, search_results=[result])
    client = _client(store=store, stream_chunks=make_chat_chunks(["Hello", " [1]"]))

    chat_id = client.post("/api/v1/chats", json={"sources": [channel.handle]}).json()["id"]
    resp = client.post(f"/api/v1/chats/{chat_id}/messages", json={"question": "hi"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert "event: token" in body
    assert '"text": "Hello"' in body
    assert "event: done" in body
    assert '"n": 1' in body
    assert channel.title in body


def test_post_message_unknown_chat_is_404():
    client = _client()
    resp = client.post(f"/api/v1/chats/{uuid4()}/messages", json={"question": "hi"})
    assert resp.status_code == 404


# --- /ask — stateless ------------------------------------------------------------------


def test_ask_is_stateless_and_streams_a_done_event():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    client = _client(store=store)

    resp = client.post(
        "/api/v1/ask", json={"sources": [channel.handle], "voice": None, "question": "hi"}
    )

    assert resp.status_code == 200
    assert "event: done" in resp.text
    assert store.messages == []
    assert len(store.usage_events) == 1
    assert store.usage_events[0].chat_id is None


def test_ask_voice_not_in_sources_is_422():
    a, b = _make_channel("A"), _make_channel("B")
    store = FakeVectorStore()
    store.channels[a.id] = a
    store.channels[b.id] = b
    client = _client(store=store)

    resp = client.post(
        "/api/v1/ask", json={"sources": [a.handle], "voice": b.handle, "question": "hi"}
    )
    assert resp.status_code == 422


# --- auth (API_TOKEN) -------------------------------------------------------------------


def test_chats_requires_bearer_token_when_api_token_is_set():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    client = _client(store=store, settings=_settings(api_token="secret"))

    unauthenticated = client.post("/api/v1/chats", json={"sources": [channel.handle]})
    assert unauthenticated.status_code == 401

    authenticated = client.post(
        "/api/v1/chats",
        json={"sources": [channel.handle]},
        headers={"Authorization": "Bearer secret"},
    )
    assert authenticated.status_code == 201


def test_chats_open_when_api_token_unset():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    client = _client(store=store, settings=_settings(api_token=None))

    resp = client.post("/api/v1/chats", json={"sources": [channel.handle]})
    assert resp.status_code == 201


# --- CORS -------------------------------------------------------------------------------


def test_cors_headers_present_for_an_allowed_origin():
    client = _client(settings=_settings(cors_origins=("https://example.com",)))

    resp = client.get(
        "/api/v1/healthz",
        headers={"Origin": "https://example.com"},
    )

    assert resp.headers.get("access-control-allow-origin") == "https://example.com"


def test_cors_headers_absent_for_a_disallowed_origin():
    client = _client(settings=_settings(cors_origins=("https://example.com",)))

    resp = client.get(
        "/api/v1/healthz",
        headers={"Origin": "https://evil.example"},
    )

    assert "access-control-allow-origin" not in resp.headers
