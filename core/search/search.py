"""Channel-scoped semantic search over ingested transcript chunks."""

from core.providers.base import LLMProvider
from core.store.base import SearchResult, VectorStore


class ChannelNotFoundError(RuntimeError):
    pass


def search_channel(
    store: VectorStore,
    provider: LLMProvider,
    *,
    channel_ref: str,
    query: str,
    top_k: int = 8,
) -> list[SearchResult]:
    channel = store.get_channel_by_handle_or_id(channel_ref)
    if channel is None:
        raise ChannelNotFoundError(f"No channel found matching {channel_ref!r}")

    query_embedding = provider.embed([query])[0]
    return store.search(channel_id=channel.id, query_embedding=query_embedding, top_k=top_k)


def build_timestamped_url(yt_video_id: str, t_start_s: float) -> str:
    return f"https://www.youtube.com/watch?v={yt_video_id}&t={int(round(t_start_s))}s"
