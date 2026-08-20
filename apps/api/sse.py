"""Server-Sent Events formatting for a streamed chat turn: one `token` event per text delta,
then one `done` event carrying the full message, labeled citations, usage, voice, and any
"try adding X" suggestions — everything AnswerResult exposes once the stream is exhausted."""

import json
from collections.abc import Iterator

from core.chat.answer import AnswerResult
from core.chat.citations import citation_to_payload


def _event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def stream_answer(result: AnswerResult) -> Iterator[str]:
    parts: list[str] = []
    try:
        for delta in result.text_stream:
            parts.append(delta)
            yield _event("token", {"text": delta})
    except Exception as exc:
        yield _event("error", {"detail": str(exc)})
        return

    usage = None
    if result.usage is not None:
        usage = {
            "model": result.usage.model,
            "tokens_in": result.usage.tokens_in,
            "tokens_out": result.usage.tokens_out,
            "est_cost_usd": result.usage.est_cost_usd,
        }

    yield _event(
        "done",
        {
            "message": "".join(parts),
            "citations": [citation_to_payload(c) for c in result.citations],
            "usage": usage,
            "voice": str(result.voice_channel.id) if result.voice_channel else None,
            "disclosure": result.disclosure,
            "suggested_sources": [
                {
                    "id": str(c.id),
                    "handle": c.handle,
                    "title": c.title,
                }
                for c in result.suggested_source_channels
            ],
        },
    )
