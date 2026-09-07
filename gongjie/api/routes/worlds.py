from __future__ import annotations

from fastapi import APIRouter

from gongjie.api.errors import api_error
from gongjie.api.schemas import CharacterInput, LocationInput, PulseInput, WorldInput, WorldResetInput
from gongjie.cognition.pulse import advance_world_pulse
from gongjie.scenes.settlements import recover_settlements
from gongjie.runtime import active_profile, provider_client, store


router = APIRouter(prefix="/api")


@router.get("/worlds/{world_id}")
async def world(world_id: str) -> dict:
    try:
        return store.get_world(world_id)
    except Exception as error:
        raise api_error(error) from error


@router.post("/worlds")
async def create_world(body: WorldInput) -> dict:
    try:
        return store.create_world(body.model_dump())
    except Exception as error:
        raise api_error(error) from error


@router.put("/worlds/{world_id}")
async def update_world(world_id: str, body: WorldInput) -> dict:
    try:
        with store.world_operation(world_id):
            return store.update_world(world_id, body.model_dump())
    except Exception as error:
        raise api_error(error) from error


@router.post("/worlds/{world_id}/reset")
async def reset_world(world_id: str, body: WorldResetInput) -> dict:
    try:
        with store.world_operation(world_id, recovery=True):
            return store.reset_world(world_id, body.confirmation)
    except Exception as error:
        raise api_error(error) from error


@router.post("/worlds/{world_id}/locations")
async def create_location(world_id: str, body: LocationInput) -> dict:
    try:
        with store.world_operation(world_id):
            return store.create_location(world_id, body.model_dump())
    except Exception as error:
        raise api_error(error) from error


@router.post("/worlds/{world_id}/characters")
async def create_character(world_id: str, body: CharacterInput) -> dict:
    try:
        with store.world_operation(world_id):
            return store.create_character(world_id, body.model_dump())
    except Exception as error:
        raise api_error(error) from error


@router.put("/characters/{character_id}")
async def update_character(character_id: str, body: CharacterInput) -> dict:
    try:
        with store.world_operation(store.get_character(character_id)["world_id"]):
            return store.update_character(character_id, body.model_dump())
    except Exception as error:
        raise api_error(error) from error


@router.post("/worlds/{world_id}/pulse")
async def pulse(world_id: str, body: PulseInput) -> dict:
    try:
        return await advance_world_pulse(
            store=store,
            client=provider_client,
            profile=active_profile(),
            world_id=world_id,
            target_time=body.target_time,
        )
    except Exception as error:
        raise api_error(error) from error


@router.post("/worlds/{world_id}/settlements/retry")
async def retry_settlements(world_id: str) -> dict:
    try:
        store.get_world(world_id)
        return await recover_settlements(store, provider_client, active_profile(), world_id)
    except Exception as error:
        raise api_error(error) from error
