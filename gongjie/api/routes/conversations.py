from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from gongjie.ai.providers import ProviderError
from gongjie.api.errors import api_error
from gongjie.api.schemas import ConversationCreate, TurnInput
from gongjie.cognition.compiler import opening_request_context
from gongjie.runtime import active_profile, provider_client, store
from gongjie.scenes.turns import stream_turn


router = APIRouter(prefix="/api/conversations")


@router.post("")
async def create_conversation(body: ConversationCreate) -> dict:
    try:
        conversation_id = store.get_or_create_conversation(body.world_id, body.character_id)
        return store.get_conversation(conversation_id)
    except Exception as error:
        raise api_error(error) from error


@router.get("/{conversation_id}")
async def conversation(conversation_id: str) -> dict:
    try:
        return store.get_conversation(conversation_id)
    except Exception as error:
        raise api_error(error) from error


@router.post("/{conversation_id}/opening")
async def opening(conversation_id: str) -> dict:
    try:
        current = store.get_conversation(conversation_id)
        if current["messages"]:
            return {"created": False, "message": current["messages"][0]}
        profile = active_profile()
        context = store.context_for_turn(conversation_id)
        reply = await provider_client.response(
            opening_request_context(context, profile), profile
        )
        content = reply.content.strip()
        if not content:
            raise ProviderError("empty_reply", "角色没有成功走进这一刻")
        usage = reply.usage
        message, created = store.add_opening_message(
            conversation_id,
            content,
            reasoning=reply.reasoning,
            finish_reason=reply.finish_reason,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
        )
        return {"created": created, "message": message}
    except Exception as error:
        raise api_error(error) from error


@router.post("/{conversation_id}/turn")
async def turn(conversation_id: str, body: TurnInput, request: Request) -> StreamingResponse:
    events = stream_turn(
        store=store,
        provider_client=provider_client,
        profile_loader=active_profile,
        conversation_id=conversation_id,
        content=body.content,
        continuation=body.continuation,
        is_disconnected=request.is_disconnected,
    )
    return StreamingResponse(
        events,
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
