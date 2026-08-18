"""Single source of truth for naming and tuning constants shared across core/, cli/, and apps/."""

from dataclasses import dataclass

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

DEFAULT_CHAT_MODEL_OPENAI = "gpt-4.1-mini"
DEFAULT_CHAT_MODEL_ANTHROPIC = "claude-sonnet-5"
DEFAULT_CHAT_MAX_TOKENS = 1024

# Both vendor SDKs default to a 600s request timeout; a stalled stream would freeze a chat tab
# for ten minutes. One value for both providers so behaviour doesn't drift between them.
LLM_REQUEST_TIMEOUT_S = 90.0

# Hard cap on a single chat question. The UI enforces it at the input box (max_chars) and
# core.chat.answer re-checks server-side — the UI has no login in selfhost mode, so nothing
# user-controlled should be able to make an unbounded embedding/completion call.
MAX_QUESTION_CHARS = 2000

# A 'running' ingest_jobs row whose heartbeat_at is older than this is assumed orphaned (the
# worker that claimed it died) and gets reclaimed to 'queued' by the next poll loop iteration.
# The heartbeat is bumped per video during build AND load, but a 300-video channel listing or a
# single embed batch (LLM_REQUEST_TIMEOUT_S each) can go quiet for a while — keep this well
# above those so a second worker never "reclaims" a job that is merely slow.
JOB_STALE_AFTER_S = 600.0
# A job reclaimed this many times without finishing is marked failed instead of requeued: a
# poison job (OOM, native crash) must not re-embed a channel forever on the owner's key.
MAX_JOB_ATTEMPTS = 3
# The Channels page warns that no worker seems to be running once a queued job has sat
# unclaimed this long — several worker poll intervals, so a healthy worker never trips it.
WORKER_STALL_WARNING_S = 30.0

# Server-side cap on videos per ingest/update job — the UI has no login and the CLI has no
# confirmation prompt, so a typo must not become a 30 000-video embedding bill.
MAX_INGEST_LIMIT = 1000
VALID_SORTS = ("recent", "views")

SUGGESTED_QUESTIONS_COUNT = 5
SUGGESTED_QUESTIONS_MAX_VIDEOS = 5
SUGGESTED_QUESTIONS_CHUNKS_PER_VIDEO = 3
# Bundles are untrusted (registry model) and these strings render as Streamlit button labels,
# which support Markdown links and images — cap and sanitize them at validate/generate time.
SUGGESTED_QUESTION_MAX_CHARS = 200
SUGGESTED_QUESTIONS_MAX_COUNT = 10


@dataclass(frozen=True)
class ChatModelPricing:
    input_per_1k_usd: float
    output_per_1k_usd: float


# Approximate list prices for chat completion, keyed by model string; for the chat UI's
# post-turn cost estimate only, not billing-accurate — spot-check against each vendor's
# pricing page before shipping. A model missing from this table yields no cost estimate
# rather than a guess.
CHAT_MODEL_PRICING_USD: dict[str, ChatModelPricing] = {
    "gpt-4.1-mini": ChatModelPricing(input_per_1k_usd=0.0004, output_per_1k_usd=0.0016),
    "gpt-4o-mini": ChatModelPricing(input_per_1k_usd=0.00015, output_per_1k_usd=0.0006),
    "claude-sonnet-5": ChatModelPricing(input_per_1k_usd=0.003, output_per_1k_usd=0.015),
    "claude-haiku-4-5-20251001": ChatModelPricing(input_per_1k_usd=0.001, output_per_1k_usd=0.005),
}

# yt-dlp defaults to requiring the `deno` JS runtime for full YouTube extraction (captions
# included); most machines don't have it preinstalled. Node.js is far more commonly already
# present (and is what the Docker image installs), so every yt_dlp.YoutubeDL(...) call in
# core/ingest/ passes this. Without it, subtitle/caption listings silently come back empty
# instead of erroring — see DECISIONS.md.
YTDLP_BASE_OPTS = {
    "js_runtimes": {"node": {}},
    "remote_components": ["ejs:github"],
}
