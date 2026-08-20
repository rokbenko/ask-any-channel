"""GET /channels, GET /channels/{ref} — read-only channel listing for API clients deciding
what to offer as sources/voice. Route parses/serializes only; all logic is core calls."""

from fastapi import APIRouter, Depends, HTTPException

from apps.api import deps
from apps.api.schemas import ChannelOut, PersonaOut
from core.chat.suggestions import BRANDING_KEY, sanitize_questions
from core.persona import disclosure_string, get_persona
from core.store.base import ChannelSummary, VectorStore

router = APIRouter()


def _to_out(cs: ChannelSummary) -> ChannelOut:
    channel = cs.channel
    persona = get_persona(channel)
    name = channel.title or channel.handle or channel.yt_channel_id
    return ChannelOut(
        id=channel.id,
        handle=channel.handle,
        yt_channel_id=channel.yt_channel_id,
        title=channel.title,
        thumbnail_url=channel.thumbnail_url,
        video_count=cs.video_count,
        embedded_video_count=cs.embedded_video_count,
        chunk_count=cs.chunk_count,
        suggested_questions=sanitize_questions(channel.branding.get(BRANDING_KEY, [])),
        persona=PersonaOut(
            enabled=persona.enabled,
            family_friendly=persona.family_friendly,
            has_profile=persona.style_profile is not None,
            disclosure=disclosure_string(name) if persona.enabled else None,
        ),
    )


@router.get("/channels", response_model=list[ChannelOut])
def list_channels(store: VectorStore = Depends(deps.get_store)) -> list[ChannelOut]:
    return [_to_out(cs) for cs in store.list_channels()]


@router.get("/channels/{ref}", response_model=ChannelOut)
def get_channel(ref: str, store: VectorStore = Depends(deps.get_store)) -> ChannelOut:
    channel = store.get_channel_by_handle_or_id(ref)
    if channel is None:
        raise HTTPException(status_code=404, detail=f"No channel found matching {ref!r}")
    summary = next((cs for cs in store.list_channels() if cs.channel.id == channel.id), None)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"No channel found matching {ref!r}")
    return _to_out(summary)
