"""Postgres + pgvector implementation of the VectorStore interface. All SQL for reads/writes
against channels/videos/chunks/ingest_jobs lives here — nothing outside this module issues
SQL against those tables."""

from datetime import datetime
from uuid import UUID

from pgvector import Vector
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from core.db import get_connection
from core.models import Channel, IngestJob, Video
from core.store.base import (
    ChannelStatusSummary,
    ChunkInput,
    SearchResult,
    StatusSummary,
    VideoStatusCount,
)


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
        self, *, channel_id: UUID, query_embedding: list[float], top_k: int
    ) -> list[SearchResult]:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT c.id AS chunk_id, c.video_id, v.yt_video_id, v.title AS video_title,
                       c.text, c.t_start_s, c.t_end_s,
                       1 - (c.embedding <=> %(qvec)s) AS score
                FROM chunks c
                JOIN videos v ON v.id = c.video_id
                WHERE c.channel_id = %(channel_id)s
                ORDER BY c.embedding <=> %(qvec)s
                LIMIT %(top_k)s
                """,
                {"qvec": Vector(query_embedding), "channel_id": channel_id, "top_k": top_k},
            )
            rows = cur.fetchall()
        return [SearchResult(**row) for row in rows]

    def create_job(self, *, channel_id: UUID | None, payload: dict) -> IngestJob:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "INSERT INTO ingest_jobs (channel_id, payload) VALUES (%s, %s) RETURNING *",
                (channel_id, Jsonb(payload)),
            )
            row = cur.fetchone()
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
                UPDATE ingest_jobs SET status = 'running', started_at = now()
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
    ) -> None:
        fields = []
        params: dict = {"job_id": job_id}

        if status is not None:
            fields.append("status = %(status)s")
            params["status"] = status
            if status == "running":
                fields.append("started_at = COALESCE(started_at, now())")
            elif status in ("done", "failed"):
                fields.append("finished_at = now()")
        if progress is not None:
            fields.append("progress = %(progress)s")
            params["progress"] = Jsonb(progress)
        if error is not None:
            fields.append("error = %(error)s")
            params["error"] = error

        if not fields:
            return

        sql = f"UPDATE ingest_jobs SET {', '.join(fields)} WHERE id = %(job_id)s"
        with get_connection() as conn:
            conn.execute(sql, params)

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
