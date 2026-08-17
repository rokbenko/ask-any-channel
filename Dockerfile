FROM python:3.11-slim

# yt-dlp needs a JS runtime for full YouTube extraction (captions included) —
# see core/constants.py YTDLP_BASE_OPTS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-install-project || uv sync --no-install-project

COPY . .
RUN uv sync --frozen || uv sync

ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "-m", "core.worker.daemon"]
