"""Auto-ingest scheduling: keeps opted-in channels' incremental updates flowing without a
separate cron process — `core.worker.daemon.poll_and_run` calls `run_auto_ingest_tick` once
per scheduler tick, reusing the existing job queue (`enqueue_update_job`) and its one-active-
job-per-channel dedupe. External/guest videos don't exist in this product's model, so a
scheduled check only ever lists a channel's own videos, same as a manual "check for new
videos" click."""

import hashlib
from datetime import datetime
from uuid import UUID

from core.constants import AUTO_UPDATE_JITTER_S, AUTO_UPDATE_LIMIT
from core.ingest.jobs import ActiveJobExistsError, enqueue_update_job
from core.store.base import VectorStore


def jitter_for(channel_id: UUID, jitter_s: float) -> float:
    """A deterministic offset in [0, jitter_s) for this channel — hashed from its id, not
    random, so it's stable across worker restarts (a restart must not reset every channel's
    due time to the same instant). Spreads out channels that share an interval and would
    otherwise all become due in the same tick."""
    if jitter_s <= 0:
        return 0.0
    digest = hashlib.sha256(str(channel_id).encode()).digest()
    fraction = int.from_bytes(digest[:8], "big") / 2**64
    return fraction * jitter_s


def is_due(
    *,
    last_checked_at: datetime | None,
    now: datetime,
    interval_s: float,
    jitter_s: float,
    channel_id: UUID,
) -> bool:
    if last_checked_at is None:
        return True  # never checked -> due immediately
    elapsed_s = (now - last_checked_at).total_seconds()
    return elapsed_s >= interval_s + jitter_for(channel_id, jitter_s)


def run_auto_ingest_tick(store: VectorStore, *, now: datetime, interval_hours: float) -> list[UUID]:
    """Enqueues an incremental update for every due, auto_update-enabled channel; marks every
    due channel checked regardless of outcome, so a channel whose update is already active
    (ActiveJobExistsError) isn't retried on the very next tick. Returns the ids an update was
    actually enqueued for. interval_hours <= 0 means auto-ingest is off globally."""
    if interval_hours <= 0:
        return []
    interval_s = interval_hours * 3600.0

    enqueued: list[UUID] = []
    for channel in store.list_auto_update_channels():
        if not is_due(
            last_checked_at=channel.last_checked_at,
            now=now,
            interval_s=interval_s,
            jitter_s=AUTO_UPDATE_JITTER_S,
            channel_id=channel.id,
        ):
            continue
        try:
            enqueue_update_job(store, channel.id, limit=AUTO_UPDATE_LIMIT, sort="recent")
        except ActiveJobExistsError:
            pass  # a manual or already-scheduled check is running — don't queue a second one
        else:
            enqueued.append(channel.id)
        store.mark_channel_checked(channel.id, now)
    return enqueued
