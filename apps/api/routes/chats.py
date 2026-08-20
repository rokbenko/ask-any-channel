"""POST /chats, GET /chats/{id}, GET/POST /chats/{id}/messages — the stateful chat lifecycle.
Route parses/validates/serializes only; scope building and the turn itself are core calls."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from apps.api import deps
from apps.api.schemas import ChatMessageRequest, ChatOut, CreateChatRequest, MessageOut
from apps.api.sse import stream_answer
from core.chat.answer import answer
from core.chat.scope import build_scope, create_chat, resolve_channel_refs, resolve_voice_ref
from core.persona import disclosure_string
from core.providers.base import LLMProvider
from core.store.base import VectorStore

router = APIRouter()


def _chat_out(chat, store: VectorStore) -> ChatOut:
    disclosure = None
    if chat.voice_channel_id is not None:
        voice_channel = store.get_channel(chat.voice_channel_id)
        if voice_channel is not None:
            name = voice_channel.title or voice_channel.handle or voice_channel.yt_channel_id
            disclosure = disclosure_string(name)
    return ChatOut(
        id=chat.id,
        sources=chat.source_channel_ids,
        voice=chat.voice_channel_id,
        disclosure=disclosure,
    )


@router.post(
    "/chats",
    response_model=ChatOut,
    status_code=201,
    dependencies=[Depends(deps.require_token)],
)
def create_chat_route(
    body: CreateChatRequest, store: VectorStore = Depends(deps.get_store)
) -> ChatOut:
    sources = resolve_channel_refs(store, body.sources)
    voice_id = resolve_voice_ref(sources, body.voice)
    scope = build_scope(sources, voice_id)
    chat = create_chat(store, scope)
    return _chat_out(chat, store)


@router.get("/chats/{chat_id}", response_model=ChatOut, dependencies=[Depends(deps.require_token)])
def get_chat_route(chat_id: UUID, store: VectorStore = Depends(deps.get_store)) -> ChatOut:
    chat = store.get_chat(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail=f"No chat {chat_id}")
    return _chat_out(chat, store)


@router.get(
    "/chats/{chat_id}/messages",
    response_model=list[MessageOut],
    dependencies=[Depends(deps.require_token)],
)
def list_messages(chat_id: UUID, store: VectorStore = Depends(deps.get_store)) -> list[MessageOut]:
    chat = store.get_chat(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail=f"No chat {chat_id}")
    return [
        MessageOut(
            id=m.id, role=m.role, content=m.content, citations=m.citations, created_at=m.created_at
        )
        for m in store.list_messages(chat_id)
    ]


@router.post("/chats/{chat_id}/messages", dependencies=[Depends(deps.require_token)])
def post_message(
    chat_id: UUID,
    body: ChatMessageRequest,
    store: VectorStore = Depends(deps.get_store),
    embedding_provider: LLMProvider = Depends(deps.get_embedding_provider),
    chat_provider_and_model: tuple = Depends(deps.get_chat_provider_and_model),
    settings=Depends(deps.get_settings_dep),
) -> StreamingResponse:
    chat = store.get_chat(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail=f"No chat {chat_id}")

    chat_provider, chat_model = chat_provider_and_model
    result = answer(
        store,
        embedding_provider,
        chat_provider,
        chat_id=chat_id,
        user_text=body.question,
        chat_model=chat_model,
        retrieval_mode=settings.retrieval_mode,
    )
    return StreamingResponse(stream_answer(result), media_type="text/event-stream")
