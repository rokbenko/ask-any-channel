FROM python:3.11-slim

# yt-dlp needs a JS runtime for full YouTube extraction (captions included) —
# see core/constants.py YTDLP_BASE_OPTS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

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

CMD ["python", "-m", "core.worker.daemon"]
