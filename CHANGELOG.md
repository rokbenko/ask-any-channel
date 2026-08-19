# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions are tagged `vX.Y.Z` on
`main`.

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
