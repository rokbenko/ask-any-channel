# AskAnyChannel

Turn any YouTube channel into a grounded AI chatbot. Paste a channel URL, it ingests the
channel's video transcripts, and you get answers cited to the exact video and timestamp
(`https://www.youtube.com/watch?v={id}&t={seconds}s`).

> **Status:** Phase 2 — ingestion pipeline, pgvector search, dataset bundles, and the `aac`
> CLI (Phase 1), plus a streaming Streamlit chat UI with cited answers (Phase 2).

## Prerequisites

Docker, Python 3.11 (`>=3.11,<3.12`), [`uv`](https://docs.astral.sh/uv/), and **Node.js**
(yt-dlp needs a JS runtime to fully extract YouTube pages, captions included — the `worker`
Docker image already bundles it, but running `aac ingest` directly on your host needs Node
on `PATH`). None of this pulls Node/TypeScript into the app itself — `core/` is pure Python.

## Quickstart

```bash
git clone <this-repo-url> && cd ask-any-channel
cp .env.example .env   # fill in OPENAI_API_KEY
docker compose up -d postgres
uv sync
uv run aac ingest @SomeChannel --limit 20
uv run aac search "what does this channel say about X?" --channel @SomeChannel
```

Database migrations apply automatically the first time you run any `aac` command — there's
no separate migration step.

## Architecture

```
               ┌────────────┐
  aac CLI   →  │            │ ──▶  OpenAI (embeddings)
  worker    →  │   core/    │
  Streamlit →  │            │ ──▶  OpenAI or Anthropic (chat, per CHAT_PROVIDER)
               └─────┬──────┘
                     │
                     ▼
           Postgres 16 + pgvector
  (channels, videos, chunks, jobs, chats, messages, usage)
```

- **`core/`** — all logic: ingestion, chunking, retrieval, chat orchestration + citation
  parsing, provider + credentials seams, dataset bundling. The only package with business
  logic; everything else is a thin client over it.
- **`cli/`** — the `aac` Typer CLI (`ingest`, `search`, `status`, `dataset`, `registry`).
- **`core/db/`** — connection pool plus plain numbered SQL migrations and a small
  dependency-light runner (applied lazily on the first database touch).
- **`apps/ui/`** — Streamlit chat client; imports `core` only, zero logic of its own.
- One database for everything: relational data, vectors (pgvector), and the ingestion job
  queue — no Pinecone, no Redis.

## Repo layout

```
├── cli/                    # aac Typer CLI — thin wrappers over core/
├── core/
│   ├── ingest/             # channel resolution, caption fetch, VTT parsing, chunking
│   ├── dataset/            # local bundle build/load/validate + registry entries
│   ├── db/                 # connection pool + numbered SQL migrations (applied lazily)
│   ├── providers/          # LLMProvider seam — OpenAI + Anthropic, chosen via CHAT_PROVIDER
│   ├── store/              # VectorStore seam, pgvector implementation
│   ├── search/             # retrieval
│   ├── chat/               # grounded prompt, streaming answer, [n] citation parsing
│   └── worker/             # polling daemon, shares pipeline code with the CLI
├── registry/channels.json  # community index of built dataset bundles (metadata only)
├── data/raw/               # cached .vtt captions (gitignored)
├── datasets/               # local dataset bundles (gitignored — see Dataset bundles)
├── apps/ui/                # Streamlit chat client
└── tests/                  # pytest — parsing/chunking/bundle/chat-orchestration logic
```

## CLI

| Command | Does |
| --- | --- |
| `aac ingest <channel> [--limit N] [--sort views\|recent]` | Lists channel videos, fetches captions, chunks transcripts, embeds, and stores them directly in Postgres. Idempotent — re-runs skip already-completed work. |
| `aac search "<question>" --channel <handle> [--top-k 8]` | Prints the top matching transcript chunks with a score, video title, and a timestamped YouTube link (`&t={seconds}s`) that lands where the words are spoken. |
| `aac status` | Channels, per-status video counts, recent ingest job states. |
| `aac dataset build <channel> [--limit N] [--sort views\|recent] [--out DIR] [--skip-embeddings]` | Builds a local, portable dataset bundle (videos, chunks, embeddings, manifest) without touching Postgres. Whole-bundle idempotent — rerunning a finished build is a no-op. |
| `aac dataset load <bundle_dir>` | Loads a previously built bundle into Postgres. Only calls an embedding API if the bundle's model doesn't match your configured one, or embeddings were skipped at build time. |
| `aac dataset validate <bundle_dir>` | Checks a bundle's manifest and files for integrity. |
| `aac registry entry <handle>` | Emits a metadata-only JSON entry (channel, counts, embedding model — never transcript content) for `registry/channels.json`, ready to paste into a PR. |

`channel` accepts a full channel URL, an `@handle`, or a bare `UC...` id.

## Dataset bundles

`aac dataset build` produces a self-contained, shareable bundle (`manifest.json`,
`videos.jsonl`, `chunks.parquet`, and — unless `--skip-embeddings` is passed —
`embeddings-{model}.parquet`) under `datasets/{channel-slug}/`. Bundles are **local-only**
and gitignored: nothing under `datasets/` is ever committed, so no transcript content leaves
your machine unless you choose to share the directory yourself.

`registry/channels.json` is a public, metadata-only index (channel, video/chunk counts,
embedding model — no transcript text) of channels the community has already built bundles
for. After a build, `aac registry entry <handle>` prints the entry to add there in a PR, so
others can find a channel worth re-ingesting themselves.

## Chat

Once a channel is ingested, chat with it in the Streamlit UI:

```bash
uv run streamlit run apps/ui/Home.py
# or, via Docker:
docker compose --profile ui up -d
```

Pick a channel from the sidebar, ask a question, and get a streamed answer with inline
`[n]` citations — each links to the exact video + timestamp and expands to an embedded
player that starts right there. Off-topic questions get an honest "the channel doesn't
cover this" instead of an invented answer.

`CHAT_PROVIDER` (`openai` or `anthropic`) picks which vendor answers chat turns;
`CHAT_MODEL` overrides the default model for that provider. Embedding the question always
goes through OpenAI regardless of `CHAT_PROVIDER`, so `OPENAI_API_KEY` is required either
way — set `ANTHROPIC_API_KEY` too if `CHAT_PROVIDER=anthropic`.

The UI has **no login** in self-host mode and every question is billed to *your* API keys, so
it listens on `localhost` only (`.streamlit/config.toml`; the compose service publishes on
`127.0.0.1:8501` for the same reason). Put it behind a reverse proxy with authentication if
you need it reachable from elsewhere. Streamlit's usage telemetry is switched off there too.

*(screenshot coming soon)*

## Configuration

All settings come from `.env` (copy `.env.example` to start) via `core/config.py` (and
`core/credentials.py` for API keys) — no other module reads environment variables directly.

| Variable | Purpose |
| --- | --- |
| `INSTANCE_MODE` | `selfhost` (default, no auth/quotas) or `cloud` (future, not built). |
| `POSTGRES_PASSWORD` | Password for the compose Postgres service (default `aac`). Change it for anything beyond a laptop, and keep `DATABASE_URL` in sync. |
| `DATABASE_URL` | Postgres connection string. The compose Postgres service is published on **`127.0.0.1:5432` only** — Docker port publishing bypasses host firewalls, so it's deliberately not reachable from the network. |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | Required for embeddings, always — including for chat, since the query is embedded regardless of `CHAT_PROVIDER`. `OPENAI_BASE_URL` lets you point at any OpenAI-compatible endpoint. |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` | Required only when `CHAT_PROVIDER=anthropic`. |
| `CHAT_PROVIDER` | `openai` (default) or `anthropic` — which vendor answers chat turns. |
| `CHAT_MODEL` | Overrides the default chat model for the configured provider. Leave blank to use the built-in default. |

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run ruff format .
```

## Contributing

- Type hints everywhere; code must pass `ruff check` and `ruff format`. Add `pytest`
  coverage for parsing/chunking logic under `tests/`.
- Dataset-registry PRs (`registry/channels.json`) should contain only the JSON entry from
  `aac registry entry` — no transcript content or bundle files.
- Commit **only after a logical, reviewable unit of work** — don't commit per file edit.

### Commit messages

- **Subject**: `<type>(<scope>): <summary>` — lowercase, imperative, ≤ 50 chars. Types:
  `feat`, `fix`, `refactor`, `chore`, `docs`, `style`, `test`, `perf`, `ci`, `build`,
  `revert`. Scopes: `core`, `cli`, `db`, `ingest`, `dataset`, `search`, `store`,
  `providers`, `worker`, `ui`, `tests`, `repo` (root tooling/workspace), `docs`, `deps`.
- **Body is mandatory for anything non-trivial**, hard-wrapped at ~72 columns, written like
  a reviewer's briefing:
  1. Open with 1–3 sentences of context: what state prompted this change.
  2. Then one cluster per concern — a short lead-in line (often ending with a colon)
     followed by bullets. Bullets name concrete files, functions, and behavior, and say
     *why* each change was needed or safe, not just what moved.
  3. Explicitly record what was **deliberately left untouched** whenever a reader might
     expect it to change ("X untouched — it describes Y, not the deliverable").
  4. Close with a `Verified:` line listing the exact checks run (`pytest`, `ruff check`,
     manual CLI invocation, etc.) and their outcomes.
- Skeleton:

  ```
  fix(ingest): dedupe rolling caption cues

  One short paragraph: the situation and why the change
  was needed.

  First cluster of changes:
  - concrete change, with file/function names and the
    reason it was needed or safe
  - what was deliberately left untouched and why

  Second cluster:
  - ...

  Verified: exact checks run and their results.
  ```

- **No attribution or generation footers** — never `Co-Authored-By: Claude ...`, never
  "Generated with ..." lines.
- Never push unless explicitly asked to.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
