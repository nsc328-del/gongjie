from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from gongjie.api.errors import api_error
from gongjie.api.schemas import BranchCreate, ConversationCreate, TurnInput
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
async def conversation(conversation_id: str, branch_id: str | None = None) -> dict:
    try:
        return store.get_conversation(conversation_id, branch_id)
    except Exception as error:
        raise api_error(error) from error


@router.post("/{conversation_id}/branches")
async def fork_branch(conversation_id: str, body: BranchCreate) -> dict:
    try:
        branch_id = store.fork_branch(
            conversation_id, body.parent_branch_id, body.from_message_id, body.title
        )
        return store.get_conversation(conversation_id, branch_id)
    except Exception as error:
        raise api_error(error) from error


@router.post("/{conversation_id}/branches/{branch_id}/activate")
async def activate_branch(conversation_id: str, branch_id: str) -> dict:
    try:
        store.activate_branch(conversation_id, branch_id)
        return store.get_conversation(conversation_id, branch_id)
    except Exception as error:
        raise api_error(error) from error


@router.post("/{conversation_id}/branches/{branch_id}/canon")
async def promote_branch(conversation_id: str, branch_id: str) -> dict:
    try:
        store.promote_branch(conversation_id, branch_id)
        return store.get_conversation(conversation_id, branch_id)
    except Exception as error:
        raise api_error(error) from error


@router.post("/{conversation_id}/turn")
async def turn(conversation_id: str, body: TurnInput, request: Request) -> StreamingResponse:
    events = stream_turn(
        store=store,
        provider_client=provider_client,
        profile_loader=active_profile,
        conversation_id=conversation_id,
        branch_id=body.branch_id,
        content=body.content,
        continuation=body.continuation,
        is_disconnected=request.is_disconnected,
    )
    return StreamingResponse(
        events,
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
