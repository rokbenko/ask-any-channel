"""POST /ask — the stateless, embed-friendly one-shot endpoint: no chat to create first, just
sources + voice + a question, streamed back over SSE."""

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from apps.api import deps
from apps.api.schemas import AskRequest
from apps.api.sse import stream_answer
from core.chat.answer import ask as ask_core
from core.chat.scope import build_scope, resolve_channel_refs, resolve_voice_ref
from core.providers.base import LLMProvider
from core.store.base import VectorStore

router = APIRouter()


@router.post("/ask", dependencies=[Depends(deps.require_token)])
def ask_route(
    body: AskRequest,
    store: VectorStore = Depends(deps.get_store),
    embedding_provider: LLMProvider = Depends(deps.get_embedding_provider),
    chat_provider_and_model: tuple = Depends(deps.get_chat_provider_and_model),
    settings=Depends(deps.get_settings_dep),
) -> StreamingResponse:
    sources = resolve_channel_refs(store, body.sources)
    voice_id = resolve_voice_ref(sources, body.voice)
    scope = build_scope(sources, voice_id)

    chat_provider, chat_model = chat_provider_and_model
    result = ask_core(
        store,
        embedding_provider,
        chat_provider,
        scope=scope,
        user_text=body.question,
        chat_model=chat_model,
        retrieval_mode=settings.retrieval_mode,
    )
    return StreamingResponse(stream_answer(result), media_type="text/event-stream")
