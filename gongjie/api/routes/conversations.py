from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from gongjie.ai.providers import ProviderError
from gongjie.api.errors import api_error
from gongjie.api.schemas import ConversationCreate, TurnInput
from gongjie.cognition.compiler import (
    opening_request_context,
    parse_scene_entry_grounding,
    scene_entry_grounding_context,
)
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
        if not current["needs_scene_entry"]:
            existing = current.get("scene_entry")
            if not existing and current["messages"]:
                existing = current["messages"][0]
            return {
                "created": False,
                "message": existing,
                "pulse_number": current["pulse"]["number"],
            }
        with store.world_operation(current["conversation"]["world_id"]):
            store.assert_conversation_entry(conversation_id)
            profile = active_profile()
            context = store.context_for_turn(conversation_id)
            reentry = bool(current["messages"])
            reply = await provider_client.response(
                opening_request_context(context, profile, reentry=reentry), profile
            )
            content = reply.content.strip()
            if not content:
                raise ProviderError("empty_reply", "角色没有成功走进这一刻")
            audit_reply = None
            audit_replies = []
            if content:
                grounding_error = None
                for _ in range(2):
                    audit_reply = await provider_client.response(
                        scene_entry_grounding_context(context, content, profile),
                        profile,
                    )
                    audit_replies.append(audit_reply)
                    try:
                        content = parse_scene_entry_grounding(audit_reply.content)["text"]
                        grounding_error = None
                        break
                    except ValueError as error:
                        grounding_error = error
                if grounding_error is not None:
                    raise ProviderError(
                        "invalid_reply", "这一刻没有通过记忆校对，请再试一次"
                    ) from grounding_error
            input_tokens = sum(
                item.usage.input_tokens for item in (reply, *audit_replies) if item.usage
            ) or None
            output_tokens = sum(
                item.usage.output_tokens for item in (reply, *audit_replies) if item.usage
            ) or None
            message, created = store.add_opening_message(
                conversation_id,
                content,
                reasoning=reply.reasoning,
                finish_reason=audit_reply.finish_reason if audit_reply else reply.finish_reason,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            return {
                "created": created,
                "message": message,
                "kind": "reentry" if reentry else "initial",
                "pulse_number": context["pulse"]["number"],
            }
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
