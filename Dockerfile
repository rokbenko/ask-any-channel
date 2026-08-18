FROM python:3.11-slim

# yt-dlp needs a JS runtime for full YouTube extraction (captions included) — see
# core/constants.py YTDLP_BASE_OPTS. Debian bookworm's packaged nodejs is 18.x (EOL April 2025)
# and yt-dlp's remote challenge-solver components track current Node, so take the binary from
# the official Node image instead of apt. `node` is a self-contained binary (only libstdc++,
# which slim already has), so copying just it is enough for yt-dlp.
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node

# Pinned: an unpinned `pip install uv` makes the image's resolver a moving target.
# Bump deliberately, alongside the local `uv --version` recorded in DECISIONS.md.
RUN pip install --no-cache-dir "uv==0.9.30"

WORKDIR /app

# uv.lock is required, not optional, and --frozen has no fallback: a stale or missing lock
# must fail the build loudly rather than silently re-resolve without hashes. --no-dev keeps
# pytest/ruff out of the runtime image.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Same entry point compose uses (docker-compose.yml `worker.command`) — keep the two in sync.
CMD ["aac", "worker"]
