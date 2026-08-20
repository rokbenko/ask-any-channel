# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions are tagged `vX.Y.Z` on
`main`.

## [0.2.0] — 2026-08-20

### Upgrading from 0.1.x — read this before `docker compose up`

Three migrations (`0006`–`0008`) apply **automatically** the first time any 0.2.0 process
touches the database — there is no prompt and no manual step. Back up first:

```bash
docker compose exec -T postgres pg_dump -U aac askanychannel > backup-0.1.sql
```

- **`0007` is not reversible.** It backfills each chat's channel into the new `chat_sources`
  table and then **drops `chats.channel_id`**. Downgrading to 0.1.x afterwards requires
  restoring the dump above — 0.1.x cannot read the 0.2.0 schema.
- **`0006` rewrites the whole `chunks` table.** Adding the generated `tsvector` column forces a
  full table rewrite under an exclusive lock, then builds a GIN index. On a large corpus expect
  the first start to block for minutes and to need roughly double the `chunks` table's disk
  while it runs. **Let it finish** — interrupting a migration is far worse than waiting.
- `0008` just adds two nullable/defaulted columns to `channels`; it is instant.

Nothing else about the upgrade is breaking: existing chats keep their history and citations,
dataset bundles built by 0.1.x still load unchanged (`schema_version` is still `1`), and every
0.1.x CLI command keeps its behaviour.

### Added

- Hybrid retrieval: vector search fused with full-text search (Reciprocal Rank Fusion) via a
  new generated `tsvector` column and GIN index on `chunks`, on by default (`RETRIEVAL_MODE`,
  `hybrid`/`dense`). `aac retrieval compare` shows dense vs. hybrid rankings side by side, and
  `VectorStore.search()` now accepts a list of channel ids, scoping a query across any subset
  of ingested channels.
- Corpus-derived voice profiles (`core/persona/`): `aac persona build <channel>` (or
  "Regenerate voice" in the UI) samples a channel's own transcripts and asks the configured
  chat model for an editable style profile (tone, catchphrases, how it names its own
  frameworks). Per-channel `enabled`/`family_friendly`/custom-instructions settings, and a
  non-negotiable honesty guardrail on every voice: never claims to be the real person, and
  discloses itself ("AI trained on {name}'s public videos — not {name}."). Instance-only —
  never written into dataset bundles or registry entries.
- Multi-channel chat: a chat now has an independent **Sources** set (any subset of ingested
  channels) and **Voice** (Neutral, or one selected creator answering first-person in their
  style), both editable on an already-open chat. Context is retrieved per source and grouped/
  labeled by creator in the prompt; a selected creator's own material is delivered first-
  person, every other selected creator's material is explicitly attributed by name, never
  absorbed. A question none of the selected sources cover is refused, with a suggestion to add
  another ingested-but-unselected channel when it looks relevant.
- FastAPI HTTP API (`apps/api/`, opt-in via `docker compose --profile api up -d`, port 8000
  loopback-only): `GET /channels`, `POST /chats` + `GET/POST /chats/{id}/messages` (SSE-
  streamed answers), and a stateless `POST /ask` for embedding the bot on another page. Optional
  bearer-token auth (`API_TOKEN`) and CORS allowlist (`CORS_ORIGINS`). See
  [docs/api.md](docs/api.md).
- Auto-update scheduler: a per-channel "Auto-update" toggle (off by default) makes the existing
  worker periodically check that channel for new videos on its own
  (`AUTO_INGEST_INTERVAL_HOURS`), with no new process. An update that adds new videos also
  refreshes suggested questions and, if the corpus grew enough, the voice profile.
- `aac status` and the Channels page now show each channel's auto-update state and last-checked
  time.

## [0.1.0] — 2026-08-18

### Added

- Channel ingestion pipeline: resolve a YouTube channel from a URL/`@handle`/`UC...` id, list
  its videos, fetch and cache captions, parse and dedupe rolling caption cues, chunk
  transcripts, and embed them (`aac ingest`, `aac dataset build`/`load`/`validate`).
- Shareable, reproducible dataset bundles (`datasets/{handle}/manifest.json` + `videos.jsonl` +
  `chunks.parquet` + `embeddings-{model}.parquet`) and a public, metadata-only community
  registry (`registry/channels.json`, `aac registry entry`) — transcript content never leaves
  a self-hoster's machine.
- pgvector-backed search (`aac search`) and streaming, retrieval-grounded chat with inline
  `[n]` citations linking to the exact video and timestamp, over OpenAI or Anthropic
  (`CHAT_PROVIDER`).
- Polling ingest worker (`aac worker`) with job dedupe, retry, cancel, and crash recovery
  (heartbeat-based stale-job reclaim with a poison-job attempt cap).
- Streamlit channel-management UI (`apps/ui/`): add/monitor/update/delete channels with live
  progress, plus the chat page with suggested starter questions and an embedded citation
  player.
- `docker compose up` self-host stack: Postgres 16 + pgvector, worker, and UI, with lazy
  idempotent migrations and loopback-only network exposure by default.
- `aac doctor` (env vars, database + migrations, data-directory permissions, embedding
  dimension, API keys, yt-dlp/Node.js; `--role worker|ui` subsets shared with the boot hooks
  and compose healthchecks; `aac --version`), non-root container images, and a worker/UI
  dependency split so the worker image doesn't carry Streamlit.
- Contribution infrastructure: CI (lint + tests + registry schema validation), PR/issue
  templates, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`.
