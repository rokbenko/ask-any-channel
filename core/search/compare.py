"""Dense-vs-hybrid retrieval comparison — the evidence behind RETRIEVAL_MODE=hybrid, driven by
`aac retrieval compare`. Runs the same query through both modes over the same channel set."""

from dataclasses import dataclass

from core.providers.base import LLMProvider
from core.search.search import resolve_channel_refs
from core.store.base import SearchResult, VectorStore


@dataclass
class RetrievalComparison:
    dense: list[SearchResult]
    hybrid: list[SearchResult]


def compare_retrieval(
    store: VectorStore,
    provider: LLMProvider,
    *,
    channel_refs: list[str],
    query: str,
    top_k: int = 8,
) -> RetrievalComparison:
    channels = resolve_channel_refs(store, channel_refs)
    channel_ids = [c.id for c in channels]
    query_embedding = provider.embed([query])[0]

    dense = store.search(
        channel_ids=channel_ids, query_embedding=query_embedding, top_k=top_k, mode="dense"
    )
    hybrid = store.search(
        channel_ids=channel_ids,
        query_embedding=query_embedding,
        top_k=top_k,
        query_text=query,
        mode="hybrid",
    )
    return RetrievalComparison(dense=dense, hybrid=hybrid)
