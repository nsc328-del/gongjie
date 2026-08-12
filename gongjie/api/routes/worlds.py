from __future__ import annotations

from fastapi import APIRouter

from gongjie.api.errors import api_error
from gongjie.api.schemas import CharacterInput, LocationInput, WorldInput
from gongjie.runtime import store


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
        return store.update_world(world_id, body.model_dump())
    except Exception as error:
        raise api_error(error) from error


@router.post("/worlds/{world_id}/locations")
async def create_location(world_id: str, body: LocationInput) -> dict:
    try:
        return store.create_location(world_id, body.model_dump())
    except Exception as error:
        raise api_error(error) from error


@router.post("/worlds/{world_id}/characters")
async def create_character(world_id: str, body: CharacterInput) -> dict:
    try:
        return store.create_character(world_id, body.model_dump())
    except Exception as error:
        raise api_error(error) from error


@router.put("/characters/{character_id}")
async def update_character(character_id: str, body: CharacterInput) -> dict:
    try:
        return store.update_character(character_id, body.model_dump())
    except Exception as error:
        raise api_error(error) from error
