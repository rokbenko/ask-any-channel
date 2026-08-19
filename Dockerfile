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
#
# INSTALL_EXTRAS controls whether the `ui` extra (Streamlit) is installed. Left empty
# (worker's build) it's a plain `uv sync`; docker-compose.yml sets it to "ui" for the `ui`
# service only, via --build-arg. One Dockerfile, not two, to avoid duplicating the node-copy/
# uv-pin/non-root setup above and below across a second file.
COPY pyproject.toml uv.lock ./
ARG INSTALL_EXTRAS=""
# pyarrow/streamlit wheels are 100MB+; uv's 30s default HTTP timeout can be tight on a slow
# or congested connection and fails the whole build rather than just retrying that download.
# ARG, not ENV: it's a build-time knob and shouldn't leak into the runtime environment.
ARG UV_HTTP_TIMEOUT=120
# The cache mount is shared across the worker and ui builds (and across rebuilds), so the
# dependency set downloads once, not once per image — first `docker compose up` gets faster
# and a code-only change rebuilds in seconds.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project ${INSTALL_EXTRAS:+--extra ${INSTALL_EXTRAS}}

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev ${INSTALL_EXTRAS:+--extra ${INSTALL_EXTRAS}}

ENV PATH="/app/.venv/bin:$PATH"

# Fixed uid/gid (not a Debian-assigned floating one) so bind-mounted ./data and ./datasets
# keep predictable host ownership across rebuilds. 1000:1000 matches the typical Linux "first
# user"; a host whose user has another uid gets a clear `aac doctor` data_dirs failure with
# the chown to run, not a PermissionError mid-ingest. Only the two writable dirs are chowned —
# a `chown -R /app` would rewrite the whole .venv into a fresh layer and double the image.
RUN groupadd --gid 1000 aac \
    && useradd --uid 1000 --gid aac --create-home --shell /bin/bash aac \
    && mkdir -p /app/data /app/datasets \
    && chown aac:aac /app/data /app/datasets
USER aac

# Same entry point compose uses (docker-compose.yml `worker.command`) — keep the two in sync.
CMD ["aac", "worker"]
