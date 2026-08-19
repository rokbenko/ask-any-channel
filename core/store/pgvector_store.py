"""Postgres + pgvector implementation of the VectorStore interface. All SQL for reads/writes
against channels/videos/chunks/ingest_jobs lives here — nothing outside this module issues
SQL against those tables."""

from datetime import datetime
from uuid import UUID

from pgvector import Vector
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from core.constants import DEFAULT_RETRIEVAL_MODE, RETRIEVAL_CANDIDATES
from core.db import get_connection
from core.models import Channel, Chat, IngestJob, Message, UsageEvent, Video
from core.search.hybrid import prepare_lexical_query, rrf_fuse
from core.store.base import (
    ActiveJobExistsError,
    ChannelStatusSummary,
    ChannelSummary,
    ChatSummary,
    ChunkInput,
    SearchResult,
    StatusSummary,
    VideoStatusCount,
)

_CHAT_TITLE_MAX_CHARS = 60
# ingest_jobs.error is rendered in the UI and comes from arbitrary exception text (yt-dlp
# dumps whole HTTP bodies) — cap it so one bad job can't bloat the row or the page.
_MAX_JOB_ERROR_CHARS = 2000
_ACTIVE_JOB_MESSAGE = "This channel already has a job queued or running — wait or cancel it."


class PgVectorStore:
    def upsert_channel(
        self,
        *,
        yt_channel_id: str,
        handle: str | None,
        title: str | None,
        thumbnail_url: str | None,
    ) -> Channel:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO channels (yt_channel_id, handle, title, thumbnail_url)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (yt_channel_id) DO UPDATE SET
                    handle = EXCLUDED.handle,
                    title = EXCLUDED.title,
                    thumbnail_url = EXCLUDED.thumbnail_url
                RETURNING *
                """,
                (yt_channel_id, handle, title, thumbnail_url),
            )
            row = cur.fetchone()
        return Channel(**row)

    def upsert_video(
        self,
        *,
        channel_id: UUID,
        yt_video_id: str,
        title: str | None,
        published_at: datetime | None,
        duration_s: int | None,
        view_count: int | None,
    ) -> Video:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO videos
                    (channel_id, yt_video_id, title, published_at, duration_s, view_count)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (channel_id, yt_video_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    published_at = EXCLUDED.published_at,
                    duration_s = EXCLUDED.duration_s,
                    view_count = EXCLUDED.view_count,
                    updated_at = now()
                RETURNING *
                """,
                (channel_id, yt_video_id, title, published_at, duration_s, view_count),
            )
            row = cur.fetchone()
        return Video(**row)

    def get_video_status(self, video_id: UUID) -> str:
        with get_connection() as conn:
            row = conn.execute("SELECT status FROM videos WHERE id = %s", (video_id,)).fetchone()
        return row[0]

    def set_video_status(self, video_id: UUID, status: str, error: str | None = None) -> None:
        with get_connection() as conn:
            conn.execute(
                "UPDATE videos SET status = %s, error = %s, updated_at = now() WHERE id = %s",
                (status, error, video_id),
            )

    def replace_chunks(
        self, video_id: UUID, channel_id: UUID, chunks: list[ChunkInput]
    ) -> list[UUID]:
        ids: list[UUID] = []
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE video_id = %s", (video_id,))
            for c in chunks:
                cur.execute(
                    """
                    INSERT INTO chunks
                        (video_id, channel_id, idx, text, t_start_s, t_end_s, token_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (video_id, channel_id, c.idx, c.text, c.t_start_s, c.t_end_s, c.token_count),
                )
                ids.append(cur.fetchone()[0])
        return ids

    def set_chunk_embeddings(self, chunk_ids: list[UUID], embeddings: list[list[float]]) -> None:
        with get_connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "UPDATE chunks SET embedding = %s WHERE id = %s",
                [(Vector(e), chunk_id) for e, chunk_id in zip(embeddings, chunk_ids, strict=True)],
            )

    def search(
        self,
        *,
        channel_ids: list[UUID],
        query_embedding: list[float],
        top_k: int,
        query_text: str | None = None,
        mode: str = DEFAULT_RETRIEVAL_MODE,
    ) -> list[SearchResult]:
        if not channel_ids:
            return []

        lexical_query = prepare_lexical_query(query_text) if query_text else None
        run_hybrid = mode == "hybrid" and lexical_query is not None
        # Widen the dense arm's candidate pool when it's about to be fused with the lexical
        # arm — RRF needs both rankings' full breadth, not just the caller's final top_k.
        dense_limit = RETRIEVAL_CANDIDATES if run_hybrid else top_k

        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT c.id AS chunk_id, c.video_id, v.yt_video_id, v.title AS video_title,
                       c.text, c.t_start_s, c.t_end_s,
                       1 - (c.embedding <=> %(qvec)s) AS score,
                       c.channel_id, ch.title AS channel_title, ch.handle AS channel_handle
                FROM chunks c
                JOIN videos v ON v.id = c.video_id
                JOIN channels ch ON ch.id = c.channel_id
                WHERE c.channel_id = ANY(%(channel_ids)s) AND c.embedding IS NOT NULL
                ORDER BY c.embedding <=> %(qvec)s
                LIMIT %(limit)s
                """,
                {
                    "qvec": Vector(query_embedding),
                    "channel_ids": channel_ids,
                    "limit": dense_limit,
                },
            )
            dense_rows = cur.fetchall()

            if not run_hybrid:
                return [SearchResult(**row) for row in dense_rows[:top_k]]

            cur.execute(
                """
                SELECT c.id AS chunk_id, c.video_id, v.yt_video_id, v.title AS video_title,
                       c.text, c.t_start_s, c.t_end_s,
                       ts_rank_cd(c.tsv, websearch_to_tsquery('english', %(q)s)) AS score,
                       c.channel_id, ch.title AS channel_title, ch.handle AS channel_handle
                FROM chunks c
                JOIN videos v ON v.id = c.video_id
                JOIN channels ch ON ch.id = c.channel_id
                WHERE c.channel_id = ANY(%(channel_ids)s)
                  AND c.tsv @@ websearch_to_tsquery('english', %(q)s)
                ORDER BY score DESC
                LIMIT %(limit)s
                """,
                {"q": lexical_query, "channel_ids": channel_ids, "limit": RETRIEVAL_CANDIDATES},
            )
            lexical_rows = cur.fetchall()

        # Either arm has every non-score column; whichever row is found first supplies them.
        rows_by_id = {row["chunk_id"]: row for row in dense_rows}
        for row in lexical_rows:
            rows_by_id.setdefault(row["chunk_id"], row)

        fused = rrf_fuse(
            [[r["chunk_id"] for r in dense_rows], [r["chunk_id"] for r in lexical_rows]]
        )
        results = []
        for chunk_id, rrf_score in fused[:top_k]:
            row = dict(rows_by_id[chunk_id])
            row["score"] = rrf_score
            results.append(SearchResult(**row))
        return results

    def sample_embedding_dim(self) -> int | None:
        # Mid-ingest, chunk rows exist before their embedding is written — skip those, or the
        # sample could land on a NULL and report "nothing ingested yet" while an ingest is live.
        with get_connection() as conn:
            row = conn.execute(
                "SELECT vector_dims(embedding) FROM chunks WHERE embedding IS NOT NULL LIMIT 1"
            ).fetchone()
        return row[0] if row else None

    def create_job(
        self, *, channel_id: UUID | None, payload: dict, status: str = "queued"
    ) -> IngestJob:
        try:
            with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    INSERT INTO ingest_jobs (channel_id, payload, status, started_at)
                    VALUES (%(channel_id)s, %(payload)s, %(status)s,
                            CASE WHEN %(status)s = 'running' THEN now() END)
                    RETURNING *
                    """,
                    {"channel_id": channel_id, "payload": Jsonb(payload), "status": status},
                )
                row = cur.fetchone()
        except UniqueViolation as exc:
            raise ActiveJobExistsError(_ACTIVE_JOB_MESSAGE) from exc
        return IngestJob(**row)

    def get_job(self, job_id: UUID) -> IngestJob:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM ingest_jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
        return IngestJob(**row)

    def claim_next_queued_job(self) -> IngestJob | None:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id FROM ingest_jobs
                WHERE status = 'queued'
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if row is None:
                return None

            cur.execute(
                """
                UPDATE ingest_jobs SET status = 'running', started_at = now(), heartbeat_at = now()
                WHERE id = %s
                RETURNING *
                """,
                (row["id"],),
            )
            updated = cur.fetchone()
        return IngestJob(**updated)

    def update_job(
        self,
        job_id: UUID,
        *,
        status: str | None = None,
        progress: dict | None = None,
        error: str | None = None,
        channel_id: UUID | None = None,
    ) -> None:
        fields = ["heartbeat_at = now()"]
        params: dict = {"job_id": job_id}

        if status is not None:
            fields.append("status = %(status)s")
            params["status"] = status
            if status == "running":
                fields.append("started_at = COALESCE(started_at, now())")
            elif status in ("done", "failed"):
                fields.append("finished_at = now()")
        if progress is not None:
            fields.append("progress = progress || %(progress)s")
            params["progress"] = Jsonb(progress)
        if error is not None:
            fields.append("error = %(error)s")
            params["error"] = error[:_MAX_JOB_ERROR_CHARS]
        if channel_id is not None:
            fields.append("channel_id = %(channel_id)s")
            params["channel_id"] = channel_id

        sql = f"UPDATE ingest_jobs SET {', '.join(fields)} WHERE id = %(job_id)s"
        try:
            with get_connection() as conn:
                conn.execute(sql, params)
        except UniqueViolation as exc:
            raise ActiveJobExistsError(_ACTIVE_JOB_MESSAGE) from exc

    def get_latest_job_for_channel(self, channel_id: UUID) -> IngestJob | None:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM ingest_jobs WHERE channel_id = %s ORDER BY created_at DESC LIMIT 1",
                (channel_id,),
            )
            row = cur.fetchone()
        return IngestJob(**row) if row else None

    def list_latest_jobs_by_channel(self) -> dict[UUID, IngestJob]:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (channel_id) *
                FROM ingest_jobs
                WHERE channel_id IS NOT NULL
                ORDER BY channel_id, created_at DESC
                """
            )
            rows = cur.fetchall()
        return {row["channel_id"]: IngestJob(**row) for row in rows}

    def list_unattached_jobs(self) -> list[IngestJob]:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT * FROM ingest_jobs
                WHERE channel_id IS NULL AND status IN ('queued', 'running', 'failed')
                ORDER BY created_at DESC
                """
            )
            rows = cur.fetchall()
        return [IngestJob(**row) for row in rows]

    def count_stale_queued_jobs(self, older_than_s: float) -> int:
        # heartbeat_at, not created_at: retry_job/update_job re-stamp it, so it's "last touched".
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM ingest_jobs
                WHERE status = 'queued'
                  AND heartbeat_at < now() - make_interval(secs => %(older_than_s)s)
                """,
                {"older_than_s": older_than_s},
            ).fetchone()
        return int(row[0])

    def retry_job(self, job_id: UUID) -> IngestJob | None:
        try:
            with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    UPDATE ingest_jobs
                    SET status = 'queued', error = NULL, started_at = NULL, heartbeat_at = now()
                    WHERE id = %s AND status = 'failed'
                    RETURNING *
                    """,
                    (job_id,),
                )
                row = cur.fetchone()
        except UniqueViolation as exc:
            raise ActiveJobExistsError(_ACTIVE_JOB_MESSAGE) from exc
        return IngestJob(**row) if row else None

    def cancel_job(self, job_id: UUID) -> IngestJob | None:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE ingest_jobs SET status = 'cancelled', finished_at = now()
                WHERE id = %s AND status = 'queued'
                RETURNING *
                """,
                (job_id,),
            )
            row = cur.fetchone()
        return IngestJob(**row) if row else None

    def reclaim_stale_jobs(self, stale_after_s: float, *, max_attempts: int) -> list[UUID]:
        # `attempts` counts reclaims (the first run is attempt 0), kept in progress jsonb so no
        # extra column is needed and update_job's merge semantics preserve it across runs.
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ingest_jobs
                SET status = 'failed', finished_at = now(), heartbeat_at = now(),
                    error = format(
                        'The worker died %%s times while running this job (last stage: %%s). '
                        'Check `docker compose logs worker` and retry when fixed.',
                        COALESCE((progress->>'attempts')::int, 0) + 1,
                        COALESCE(progress->>'stage', 'unknown'))
                WHERE status = 'running'
                  AND heartbeat_at < now() - make_interval(secs => %(stale_after_s)s)
                  AND COALESCE((progress->>'attempts')::int, 0) >= %(max_attempts)s - 1
                """,
                {"stale_after_s": stale_after_s, "max_attempts": max_attempts},
            )
            cur.execute(
                """
                UPDATE ingest_jobs
                SET status = 'queued', started_at = NULL, heartbeat_at = now(),
                    progress = progress || jsonb_build_object(
                        'attempts', COALESCE((progress->>'attempts')::int, 0) + 1,
                        'stage', 'reclaimed')
                WHERE status = 'running'
                  AND heartbeat_at < now() - make_interval(secs => %(stale_after_s)s)
                RETURNING id
                """,
                {"stale_after_s": stale_after_s},
            )
            return [row[0] for row in cur.fetchall()]

    def get_channel_by_handle_or_id(self, ref: str) -> Channel | None:
        bare = ref.lstrip("@")
        with_at = f"@{bare}"
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT * FROM channels
                WHERE yt_channel_id = %(ref)s OR handle = %(bare)s OR handle = %(with_at)s
                LIMIT 1
                """,
                {"ref": ref, "bare": bare, "with_at": with_at},
            )
            row = cur.fetchone()
        return Channel(**row) if row else None

    def status_summary(self) -> StatusSummary:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM channels ORDER BY created_at")
            channels = [Channel(**row) for row in cur.fetchall()]

            cur.execute(
                "SELECT channel_id, status, COUNT(*) AS count "
                "FROM videos GROUP BY channel_id, status"
            )
            counts_by_channel: dict[UUID, list[VideoStatusCount]] = {}
            for row in cur.fetchall():
                counts_by_channel.setdefault(row["channel_id"], []).append(
                    VideoStatusCount(status=row["status"], count=row["count"])
                )

            cur.execute("SELECT * FROM ingest_jobs ORDER BY created_at DESC LIMIT 10")
            recent_jobs = [IngestJob(**row) for row in cur.fetchall()]

        channel_summaries = [
            ChannelStatusSummary(channel=ch, video_status_counts=counts_by_channel.get(ch.id, []))
            for ch in channels
        ]
        return StatusSummary(channels=channel_summaries, recent_jobs=recent_jobs)

    def get_channel(self, channel_id: UUID) -> Channel | None:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM channels WHERE id = %s", (channel_id,))
            row = cur.fetchone()
        return Channel(**row) if row else None

    def list_channels(self) -> list[ChannelSummary]:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT c.*,
                       COUNT(v.id) AS video_count,
                       COUNT(v.id) FILTER (WHERE v.status = 'embedded') AS embedded_video_count,
                       MAX(v.updated_at) AS last_updated_at,
                       (SELECT COUNT(*) FROM chunks ch WHERE ch.channel_id = c.id) AS chunk_count
                FROM channels c
                LEFT JOIN videos v ON v.channel_id = c.id
                GROUP BY c.id
                ORDER BY c.created_at
                """
            )
            rows = cur.fetchall()

        channel_fields = Channel.__dataclass_fields__
        return [
            ChannelSummary(
                channel=Channel(**{f: row[f] for f in channel_fields}),
                video_count=row["video_count"],
                embedded_video_count=row["embedded_video_count"],
                chunk_count=row["chunk_count"],
                last_updated_at=row["last_updated_at"],
            )
            for row in rows
        ]

    def list_processed_video_ids(self, channel_id: UUID) -> set[str]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT yt_video_id FROM videos
                WHERE channel_id = %s AND status IN ('embedded', 'no_captions')
                """,
                (channel_id,),
            ).fetchall()
        return {row[0] for row in rows}

    def set_channel_branding(self, channel_id: UUID, patch: dict) -> Channel:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE channels SET branding = branding || %s::jsonb
                WHERE id = %s
                RETURNING *
                """,
                (Jsonb(patch), channel_id),
            )
            row = cur.fetchone()
        return Channel(**row)

    def list_sample_chunk_texts(
        self, channel_id: UUID, *, max_videos: int = 5, max_chunks_per_video: int = 3
    ) -> list[str]:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id FROM videos
                WHERE channel_id = %(channel_id)s AND status = 'embedded'
                ORDER BY view_count DESC NULLS LAST
                LIMIT %(max_videos)s
                """,
                {"channel_id": channel_id, "max_videos": max_videos},
            )
            video_ids = [row["id"] for row in cur.fetchall()]
            if not video_ids:
                return []

            cur.execute(
                """
                SELECT text FROM (
                    SELECT text, video_id,
                           row_number() OVER (PARTITION BY video_id ORDER BY idx) AS rn
                    FROM chunks
                    WHERE video_id = ANY(%(video_ids)s)
                ) ranked
                WHERE rn <= %(max_chunks_per_video)s
                """,
                {"video_ids": video_ids, "max_chunks_per_video": max_chunks_per_video},
            )
            return [row["text"] for row in cur.fetchall()]

    def delete_channel(self, channel_id: UUID) -> None:
        with get_connection() as conn:
            conn.execute("DELETE FROM channels WHERE id = %s", (channel_id,))

    def create_chat(self, *, channel_id: UUID) -> Chat:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("INSERT INTO chats (channel_id) VALUES (%s) RETURNING *", (channel_id,))
            row = cur.fetchone()
        return Chat(**row)

    def get_chat(self, chat_id: UUID) -> Chat | None:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM chats WHERE id = %s", (chat_id,))
            row = cur.fetchone()
        return Chat(**row) if row else None

    def list_chats(self, *, channel_id: UUID, limit: int = 50) -> list[ChatSummary]:
        # Inner (not left) lateral join: a chat with no user message yet — one whose first turn
        # failed before anything was persisted — is not listed. Rather than guaranteeing such
        # rows never exist, they're simply invisible, and cascade away with the channel.
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT c.id, c.channel_id, c.created_at, fm.content AS title
                FROM chats c
                JOIN LATERAL (
                    SELECT content FROM messages m
                    WHERE m.chat_id = c.id AND m.role = 'user'
                    ORDER BY m.created_at ASC
                    LIMIT 1
                ) fm ON true
                WHERE c.channel_id = %(channel_id)s
                ORDER BY c.created_at DESC
                LIMIT %(limit)s
                """,
                {"channel_id": channel_id, "limit": limit},
            )
            rows = cur.fetchall()

        summaries = []
        for row in rows:
            title = row["title"]
            if title and len(title) > _CHAT_TITLE_MAX_CHARS:
                title = title[: _CHAT_TITLE_MAX_CHARS - 1].rstrip() + "…"
            summaries.append(
                ChatSummary(
                    id=row["id"],
                    channel_id=row["channel_id"],
                    title=title,
                    created_at=row["created_at"],
                )
            )
        return summaries

    def list_messages(self, chat_id: UUID) -> list[Message]:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM messages WHERE chat_id = %s ORDER BY created_at ASC", (chat_id,)
            )
            rows = cur.fetchall()
        return [Message(**row) for row in rows]

    def add_message(
        self,
        *,
        chat_id: UUID,
        role: str,
        content: str,
        citations: list[dict] | None = None,
    ) -> Message:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO messages (chat_id, role, content, citations)
                VALUES (%s, %s, %s, %s)
                RETURNING *
                """,
                (chat_id, role, content, Jsonb(citations or [])),
            )
            row = cur.fetchone()
        return Message(**row)

    def record_usage_event(
        self,
        *,
        channel_id: UUID | None,
        chat_id: UUID | None,
        model: str | None,
        tokens_in: int | None,
        tokens_out: int | None,
        est_cost_usd: float | None,
    ) -> UsageEvent:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO usage_events
                    (channel_id, chat_id, model, tokens_in, tokens_out, est_cost_usd)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (channel_id, chat_id, model, tokens_in, tokens_out, est_cost_usd),
            )
            row = cur.fetchone()
        return UsageEvent(**row)
