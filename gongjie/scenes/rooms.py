from __future__ import annotations

import asyncio
import json
from contextlib import aclosing
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from gongjie.ai.providers import ProviderClient, ProviderError
from gongjie.cognition.compiler import parse_room_reply, room_turn_request_context
from gongjie.scenes.adjudication import patch_skip_reason
from gongjie.scenes.settlements import settle_message
from gongjie.storage.store import Store


def packet(kind: str, **payload: Any) -> bytes:
    value = {"type": kind, **payload}
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


async def _stream_room_turn(
    *,
    store: Store,
    provider_client: ProviderClient,
    profile_loader: Callable[[], dict[str, Any]],
    room_id: str,
    content: str,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[bytes]:
    clean_content = content.strip()
    if not clean_content:
        raise ValueError("message_empty")
    try:
        user_message = store.add_room_message(room_id, "user", None, clean_content)
        yield packet("user", message=user_message)
        profile = profile_loader()
        room = store.get_room(room_id)
        replied: list[str] = []
        for member in room["characters"]:
            if await is_disconnected():
                raise asyncio.CancelledError()
            character_id = member["id"]
            context = store.room_character_context(room_id, character_id)
            try:
                snapshot = await provider_client.response(
                    room_turn_request_context(context, profile), profile
                )
                decision = parse_room_reply(snapshot.content)
            except ProviderError as error:
                yield packet("error", error=error.public_dict(), character_id=character_id)
                continue
            except Exception as error:
                yield packet(
                    "error",
                    error={"code": "request", "message": str(error), "retryable": False},
                    character_id=character_id,
                )
                continue
            if decision.get("silent"):
                yield packet("room_silent", character_id=character_id, character_name=member["name"])
                continue
            reply = decision["reply"]
            message = store.add_room_message(room_id, "assistant", character_id, reply, settlement_context=context)
            replied.append(character_id)
            yield packet("room_reply", message=message)
            pending = store.pending_settlements(room["world"]["id"])
            if pending and pending[0]["source_id"] != message["id"]:
                yield packet("patch_skipped", character_id=character_id,
                             source_message_id=message["id"], message="这段余波正在等待记录", reason="queued")
                continue
            try:
                result = await settle_message(store, provider_client, profile, message["id"])
                yield packet("world_patch", character_id=character_id,
                             source_message_id=message["id"], **result)
            except Exception as error:
                yield packet("patch_skipped", character_id=character_id,
                             source_message_id=message["id"], message="这一段余波还未记下",
                             reason=patch_skip_reason(error))
        yield packet("done", replied=replied)
    except asyncio.CancelledError:
        return
    except ProviderError as error:
        yield packet("error", error=error.public_dict())
    except Exception as error:
        yield packet(
            "error",
            error={"code": "request", "message": str(error), "retryable": False},
        )


async def stream_room_turn(**kwargs: Any) -> AsyncIterator[bytes]:
    store = kwargs["store"]
    try:
        world_id = store.get_room(kwargs["room_id"])["world"]["id"]
        with store.world_operation(world_id):
            async with aclosing(_stream_room_turn(**kwargs)) as events:
                async for event in events:
                    yield event
    except Exception as error:
        yield packet("error", error={"code": "request", "message": str(error), "retryable": False})
