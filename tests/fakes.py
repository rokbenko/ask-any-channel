"""Minimal in-memory VectorStore/LLMProvider stand-ins for chat-orchestration and job-lifecycle
tests. No DB, no network — duck-typed against the subset of each Protocol the callers under
test actually use."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from core.models import Channel, Chat, IngestJob, Message, UsageEvent, Video
from core.providers.base import ChatChunk, ChatResponse, ChatUsage
from core.store.base import ActiveJobExistsError, ChannelSummary, ChatSummary

_ACTIVE = ("queued", "running")
_CHAT_TITLE_MAX_CHARS = 60


class FakeVectorStore:
    """`channel` is the one channel this store knows about up front (tests seed more via
    `channels[c.id] = c` or `upsert_channel`). `messages`, `usage_events`, `channels`, `videos`,
    `chats`, and `jobs` are public so tests can assert on persistence directly. Chats are never
    synthesized on lookup (unlike the single-channel-chat era) — tests create them explicitly
    via `create_chat`, since a chat's scope/voice must actually mean something."""

    def __init__(
        self,
        *,
        channel: Channel | None = None,
        search_results=None,
        history=None,
        style_sample_chunk_texts: dict | None = None,
    ):
        self._search_results = search_results or []
        self.search_calls: list[dict] = []
        self.style_sample_chunk_texts: dict[UUID, list[str]] = style_sample_chunk_texts or {}
        self.messages: list[Message] = list(history or [])
        self.usage_events: list[UsageEvent] = []
        self.channels: dict[UUID, Channel] = {channel.id: channel} if channel else {}
        self.videos: dict[UUID, Video] = {}
        self.chats: dict[UUID, Chat] = {}
        self.jobs: dict[UUID, IngestJob] = {}
        self._job_order: list[UUID] = []
        self.embedding_dim: int | None = None

    # --- chat orchestration -------------------------------------------------

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    def get_channels(self, channel_ids) -> list[Channel]:
        return [self.channels[cid] for cid in channel_ids if cid in self.channels]

    def get_channel_by_handle_or_id(self, ref: str) -> Channel | None:
        bare = ref.lstrip("@")
        for c in self.channels.values():
            if c.yt_channel_id == ref or c.handle in (bare, f"@{bare}"):
                return c
        return None

    def get_chat(self, chat_id):
        return self.chats.get(chat_id)

    def search(self, *, channel_ids, query_embedding, top_k, query_text=None, mode=None):
        self.search_calls.append(
            {
                "channel_ids": list(channel_ids),
                "query_text": query_text,
                "mode": mode,
                "top_k": top_k,
            }
        )
        return [r for r in self._search_results if r.channel_id in channel_ids][:top_k]

    def list_channels(self):
        return [
            ChannelSummary(
                channel=c,
                video_count=sum(1 for v in self.videos.values() if v.channel_id == c.id),
                embedded_video_count=sum(
                    1
                    for v in self.videos.values()
                    if v.channel_id == c.id and v.status == "embedded"
                ),
                chunk_count=len(self.style_sample_chunk_texts.get(c.id, [])),
                last_updated_at=None,
            )
            for c in self.channels.values()
        ]

    def sample_embedding_dim(self) -> int | None:
        # Mirrors PgVectorStore: dimension of one stored embedding, None if nothing is stored.
        # Tests set `store.embedding_dim` directly to simulate a populated (or mismatched) store.
        return self.embedding_dim

    def list_messages(self, chat_id):
        return [m for m in self.messages if m.chat_id == chat_id]

    def add_message(self, *, chat_id, role, content, citations=None):
        message = Message(
            id=uuid4(), chat_id=chat_id, role=role, content=content, citations=citations or []
        )
        self.messages.append(message)
        return message

    def record_usage_event(
        self,
        *,
        channel_id,
        chat_id,
        model,
        tokens_in,
        tokens_out,
        est_cost_usd,
        source_channel_ids=None,
    ):
        event = UsageEvent(
            id=uuid4(),
            channel_id=channel_id,
            chat_id=chat_id,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            est_cost_usd=est_cost_usd,
            created_at=datetime.now(UTC),
            source_channel_ids=list(source_channel_ids or []),
        )
        self.usage_events.append(event)
        return event

    # --- channel/video lifecycle ---------------------------------------------

    def upsert_video(self, *, channel_id, yt_video_id, title, published_at, duration_s, view_count):
        existing = next(
            (
                v
                for v in self.videos.values()
                if v.channel_id == channel_id and v.yt_video_id == yt_video_id
            ),
            None,
        )
        if existing is not None:
            updated = replace(existing, title=title, view_count=view_count)
            self.videos[existing.id] = updated
            return updated
        video = Video(
            id=uuid4(),
            channel_id=channel_id,
            yt_video_id=yt_video_id,
            title=title,
            published_at=published_at,
            duration_s=duration_s,
            view_count=view_count,
            status="pending",
            error=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.videos[video.id] = video
        return video

    def get_video_status(self, video_id):
        return self.videos[video_id].status

    def set_video_status(self, video_id, status, error=None):
        self.videos[video_id] = replace(self.videos[video_id], status=status, error=error)

    def list_processed_video_ids(self, channel_id) -> set[str]:
        return {
            v.yt_video_id
            for v in self.videos.values()
            if v.channel_id == channel_id and v.status in ("embedded", "no_captions")
        }

    def upsert_channel(self, *, yt_channel_id, handle, title, thumbnail_url):
        existing = next(
            (c for c in self.channels.values() if c.yt_channel_id == yt_channel_id), None
        )
        if existing is not None:
            updated = replace(existing, handle=handle, title=title, thumbnail_url=thumbnail_url)
            self.channels[existing.id] = updated
            return updated
        channel = Channel(
            id=uuid4(),
            yt_channel_id=yt_channel_id,
            handle=handle,
            title=title,
            thumbnail_url=thumbnail_url,
            branding={},
            created_at=datetime.now(UTC),
        )
        self.channels[channel.id] = channel
        return channel

    def set_channel_branding(self, channel_id, patch: dict) -> Channel:
        channel = self.channels[channel_id]
        updated = replace(channel, branding={**channel.branding, **patch})
        self.channels[channel_id] = updated
        return updated

    def list_auto_update_channels(self) -> list[Channel]:
        return [c for c in self.channels.values() if c.auto_update]

    def set_channel_auto_update(self, channel_id, enabled: bool) -> Channel:
        updated = replace(self.channels[channel_id], auto_update=enabled)
        self.channels[channel_id] = updated
        return updated

    def mark_channel_checked(self, channel_id, at) -> None:
        self.channels[channel_id] = replace(self.channels[channel_id], last_checked_at=at)

    def list_style_sample_chunk_texts(
        self, channel_id, *, top_videos=10, chunks_per_video=5, random_chunks=30
    ) -> list[str]:
        return list(self.style_sample_chunk_texts.get(channel_id, []))

    def count_channel_chunks(self, channel_id) -> int:
        return len(self.style_sample_chunk_texts.get(channel_id, []))

    def create_chat(self, *, source_channel_ids, voice_channel_id):
        chat = Chat(
            id=uuid4(),
            voice_channel_id=voice_channel_id,
            created_at=datetime.now(UTC),
            source_channel_ids=list(source_channel_ids),
        )
        self.chats[chat.id] = chat
        return chat

    def set_chat_scope(self, chat_id, *, source_channel_ids, voice_channel_id):
        chat = self.chats[chat_id]
        updated = replace(
            chat,
            voice_channel_id=voice_channel_id,
            source_channel_ids=list(source_channel_ids),
        )
        self.chats[chat_id] = updated
        return updated

    def list_chats(self, *, channel_id=None, limit=50):
        # Mirrors the real store's inner-lateral-join semantics: a chat with no user message
        # yet is invisible. Title = first user message, truncated ~60 chars.
        summaries = []
        for chat in sorted(self.chats.values(), key=lambda c: c.created_at, reverse=True):
            if channel_id is not None and channel_id not in chat.source_channel_ids:
                continue
            first_user_message = next(
                (m for m in self.messages if m.chat_id == chat.id and m.role == "user"), None
            )
            if first_user_message is None:
                continue
            title = first_user_message.content
            if len(title) > _CHAT_TITLE_MAX_CHARS:
                title = title[: _CHAT_TITLE_MAX_CHARS - 1].rstrip() + "…"
            summaries.append(
                ChatSummary(
                    id=chat.id,
                    title=title,
                    created_at=chat.created_at,
                    source_channel_ids=list(chat.source_channel_ids),
                    voice_channel_id=chat.voice_channel_id,
                )
            )
            if len(summaries) >= limit:
                break
        return summaries

    def delete_channel(self, channel_id) -> None:
        """Mirrors the real schema's FK behavior (core/db/migrations/0001_init.sql +
        0007_chat_scope.sql): videos/jobs cascade away; a chat's membership in this channel's
        chat_sources is dropped and its voice falls back to Neutral if it pointed here; a chat
        left with zero sources is then deleted (its messages cascade); usage_events survive
        with channel_id/chat_id FKs nulled (source_channel_ids is plain jsonb, untouched)."""
        self.channels.pop(channel_id, None)
        self.videos = {vid: v for vid, v in self.videos.items() if v.channel_id != channel_id}

        orphaned_chat_ids: set[UUID] = set()
        for chat_id, chat in list(self.chats.items()):
            remaining_sources = [cid for cid in chat.source_channel_ids if cid != channel_id]
            voice = None if chat.voice_channel_id == channel_id else chat.voice_channel_id
            if not remaining_sources:
                orphaned_chat_ids.add(chat_id)
                del self.chats[chat_id]
            else:
                self.chats[chat_id] = replace(
                    chat, source_channel_ids=remaining_sources, voice_channel_id=voice
                )
        self.messages = [m for m in self.messages if m.chat_id not in orphaned_chat_ids]

        self.jobs = {jid: j for jid, j in self.jobs.items() if j.channel_id != channel_id}
        self._job_order = [jid for jid in self._job_order if jid in self.jobs]

        for i, event in enumerate(self.usage_events):
            channel_id_cleared = None if event.channel_id == channel_id else event.channel_id
            chat_id_cleared = None if event.chat_id in orphaned_chat_ids else event.chat_id
            if channel_id_cleared != event.channel_id or chat_id_cleared != event.chat_id:
                self.usage_events[i] = replace(
                    event, channel_id=channel_id_cleared, chat_id=chat_id_cleared
                )

    # --- job lifecycle ---------------------------------------------------

    def _assert_no_active_conflict(self, *, channel_id, channel_input, exclude_id=None) -> None:
        """Mirrors migration 0005's partial unique indexes: one active job per channel_id, and
        one active job per raw channel_input while channel_id is still NULL."""
        for other in self.jobs.values():
            if other.id == exclude_id or other.status not in _ACTIVE:
                continue
            if channel_id is not None and other.channel_id == channel_id:
                raise ActiveJobExistsError("active job exists for channel")
            if (
                channel_id is None
                and other.channel_id is None
                and other.payload.get("channel_input") == channel_input
            ):
                raise ActiveJobExistsError("active job exists for input")

    def create_job(self, *, channel_id, payload: dict, status: str = "queued") -> IngestJob:
        if status in _ACTIVE:
            self._assert_no_active_conflict(
                channel_id=channel_id, channel_input=payload.get("channel_input")
            )
        now = datetime.now(UTC)
        job = IngestJob(
            id=uuid4(),
            channel_id=channel_id,
            payload=payload,
            status=status,
            progress={},
            error=None,
            created_at=now,
            started_at=now if status == "running" else None,
            finished_at=None,
            heartbeat_at=now,
        )
        self.jobs[job.id] = job
        self._job_order.append(job.id)
        return job

    def seed_job(self, job: IngestJob) -> None:
        """Test helper: insert a job with an arbitrary status directly, bypassing create_job's
        always-'queued' default — used to set up a pre-existing queued/running/failed job."""
        self.jobs[job.id] = job
        self._job_order.append(job.id)

    def get_job(self, job_id) -> IngestJob:
        return self.jobs[job_id]

    def update_job(self, job_id, *, status=None, progress=None, error=None, channel_id=None):
        job = self.jobs[job_id]
        changes: dict = {"heartbeat_at": datetime.now(UTC)}
        if status is not None:
            changes["status"] = status
        if progress is not None:
            changes["progress"] = {**job.progress, **progress}  # merge, like the real store
        if error is not None:
            changes["error"] = error
        if channel_id is not None:
            self._assert_no_active_conflict(
                channel_id=channel_id, channel_input=None, exclude_id=job_id
            )
            changes["channel_id"] = channel_id
        self.jobs[job_id] = replace(job, **changes)

    def claim_next_queued_job(self) -> IngestJob | None:
        for job_id in self._job_order:
            job = self.jobs[job_id]
            if job.status == "queued":
                claimed = replace(job, status="running", started_at=datetime.now(UTC))
                self.jobs[job_id] = claimed
                return claimed
        return None

    def reclaim_stale_jobs(self, stale_after_s: float, *, max_attempts: int) -> list[UUID]:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_s)
        requeued: list[UUID] = []
        for job_id, job in list(self.jobs.items()):
            if job.status != "running" or job.heartbeat_at >= cutoff:
                continue
            attempts = int(job.progress.get("attempts", 0))
            if attempts >= max_attempts - 1:
                self.jobs[job_id] = replace(
                    job, status="failed", error=f"The worker died {attempts + 1} times"
                )
            else:
                self.jobs[job_id] = replace(
                    job,
                    status="queued",
                    started_at=None,
                    progress={**job.progress, "attempts": attempts + 1, "stage": "reclaimed"},
                )
                requeued.append(job_id)
        return requeued

    def get_latest_job_for_channel(self, channel_id) -> IngestJob | None:
        for job_id in reversed(self._job_order):
            job = self.jobs[job_id]
            if job.channel_id == channel_id:
                return job
        return None

    def list_latest_jobs_by_channel(self) -> dict[UUID, IngestJob]:
        latest: dict[UUID, IngestJob] = {}
        for job_id in self._job_order:
            job = self.jobs[job_id]
            if job.channel_id is not None:
                latest[job.channel_id] = job  # later entries overwrite → latest wins
        return latest

    def list_unattached_jobs(self) -> list[IngestJob]:
        return [
            self.jobs[job_id]
            for job_id in reversed(self._job_order)
            if self.jobs[job_id].channel_id is None
            and self.jobs[job_id].status in ("queued", "running", "failed")
        ]

    def count_stale_queued_jobs(self, older_than_s: float) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_s)
        return sum(
            1 for j in self.jobs.values() if j.status == "queued" and j.heartbeat_at < cutoff
        )

    def retry_job(self, job_id) -> IngestJob | None:
        job = self.jobs.get(job_id)
        if job is None or job.status != "failed":
            return None
        self._assert_no_active_conflict(
            channel_id=job.channel_id,
            channel_input=job.payload.get("channel_input"),
            exclude_id=job_id,
        )
        updated = replace(job, status="queued", error=None, started_at=None)
        self.jobs[job_id] = updated
        return updated

    def cancel_job(self, job_id) -> IngestJob | None:
        job = self.jobs.get(job_id)
        if job is None or job.status != "queued":
            return None
        updated = replace(job, status="cancelled", finished_at=datetime.now(UTC))
        self.jobs[job_id] = updated
        return updated


class FakeLLMProvider:
    """embed() returns a fixed-length vector; stream_chat() replays a scripted list of
    ChatChunk events (or raises, for mid-stream-failure tests) and records the `messages` it
    was called with, for assertions."""

    def __init__(self, *, embedding_dim, stream_chunks=None, raise_after=None, chat_reply=""):
        self._embedding_dim = embedding_dim
        self._stream_chunks = stream_chunks or []
        self._raise_after = raise_after
        self._chat_reply = chat_reply
        self.last_messages = None
        self.chat_calls = 0

    def embed(self, texts, *, model=None):
        return [[0.0] * self._embedding_dim for _ in texts]

    def chat(self, messages, *, model=None):
        self.last_messages = messages
        self.chat_calls += 1
        return ChatResponse(content=self._chat_reply, tokens_in=10, tokens_out=20)

    def stream_chat(self, messages, *, model=None):
        self.last_messages = messages
        for i, chunk in enumerate(self._stream_chunks):
            yield chunk
            if self._raise_after is not None and i == self._raise_after:
                raise RuntimeError("simulated provider failure mid-stream")


def make_chat_chunks(text_parts, *, tokens_in=10, tokens_out=20):
    chunks = [ChatChunk(text_delta=part) for part in text_parts]
    chunks.append(ChatChunk(usage=ChatUsage(tokens_in=tokens_in, tokens_out=tokens_out)))
    return chunks
