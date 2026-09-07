from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from gongjie.api.errors import api_error
from gongjie.api.schemas import RoomInput, TurnInput
from gongjie.runtime import active_profile, provider_client, store
from gongjie.scenes.rooms import stream_room_turn


router = APIRouter(prefix="/api")


@router.get("/worlds/{world_id}/rooms")
async def list_rooms(world_id: str) -> dict:
    try:
        return {"rooms": store.list_rooms(world_id)}
    except Exception as error:
        raise api_error(error) from error


@router.post("/worlds/{world_id}/rooms")
async def create_room(world_id: str, body: RoomInput) -> dict:
    try:
        with store.world_operation(world_id):
            room = store.create_room(world_id, body.name, body.location_id, body.character_ids)
            return room
    except Exception as error:
        raise api_error(error) from error


@router.get("/rooms/{room_id}")
async def room(room_id: str) -> dict:
    try:
        return store.get_room(room_id)
    except Exception as error:
        raise api_error(error) from error


@router.post("/rooms/{room_id}/enter")
async def enter_room(room_id: str) -> dict:
    try:
        return store.enter_room(room_id)
    except Exception as error:
        raise api_error(error) from error


@router.delete("/rooms/{room_id}")
async def delete_room(room_id: str) -> dict:
    try:
        with store.world_operation(store.get_room(room_id)["world"]["id"]):
            store.delete_room(room_id)
            return {"ok": True}
    except Exception as error:
        raise api_error(error) from error


@router.post("/rooms/{room_id}/turn")
async def room_turn(room_id: str, body: TurnInput, request: Request) -> StreamingResponse:
    events = stream_room_turn(
        store=store,
        provider_client=provider_client,
        profile_loader=active_profile,
        room_id=room_id,
        content=body.content,
        is_disconnected=request.is_disconnected,
    )
    return StreamingResponse(
        events,
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
