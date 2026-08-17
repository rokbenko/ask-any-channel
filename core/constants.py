"""Single source of truth for naming and tuning constants shared across core/, cli/, and apps/."""

APP_NAME = "AskAnyChannel"
APP_SLUG = "ask-any-channel"
CLI_NAME = "aac"

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536  # must match the `vector(N)` column in db/migrations/0001_init.sql

CHUNK_TARGET_TOKENS = 400
CHUNK_OVERLAP_RATIO = 0.15
TOKENIZER_ENCODING = "cl100k_base"

RAW_CAPTIONS_DIR = "data/raw"

DATASETS_DIR = "datasets"
DATASET_SCHEMA_VERSION = 1
REGISTRY_PATH = "registry/channels.json"
TOOL_VERSION = "0.1.0"  # mirrors pyproject.toml [project].version; bump both together

# Approximate text-embedding-3-small list price; for the CLI's post-build cost estimate
# only, not billing-accurate. Check platform.openai.com/pricing for current rates.
EMBEDDING_COST_PER_1K_TOKENS_USD = 0.00002

# yt-dlp defaults to requiring the `deno` JS runtime for full YouTube extraction (captions
# included); most machines don't have it preinstalled. Node.js is far more commonly already
# present (and is what the Docker image installs), so every yt_dlp.YoutubeDL(...) call in
# core/ingest/ passes this. Without it, subtitle/caption listings silently come back empty
# instead of erroring — see DECISIONS.md.
YTDLP_BASE_OPTS = {
    "js_runtimes": {"node": {}},
    "remote_components": ["ejs:github"],
}
