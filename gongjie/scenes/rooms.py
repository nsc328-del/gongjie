from __future__ import annotations

import asyncio
import json
from contextlib import aclosing
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from gongjie.ai.providers import ProviderClient, ProviderError
from gongjie.cognition.compiler import parse_room_reply, room_turn_request_context
from gongjie.scenes.adjudication import patch_skip_reason
from gongjie.scenes.settlements import start_settlements, settlement_result
from gongjie.scenes.places import prepare_scene, destination_reply
from gongjie.scenes.room_memory import ensure_observations, observe_reply
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
        store.assert_room_entry(room_id)
        profile = profile_loader()
        user_message = store.add_room_message(room_id, "user", None, clean_content)
        yield packet("user", message=user_message)
        await ensure_observations(store,provider_client,profile,room_id)
        room = store.get_room(room_id)
        replied: list[str] = []
        for member in room["characters"]:
            if not store.room_is_current(room_id):
                break
            if member["id"] not in {item["id"] for item in store.get_room(room_id)["characters"]}:
                continue
            if await is_disconnected():
                raise asyncio.CancelledError()
            character_id = member["id"]
            context = store.room_character_context(room_id, character_id)
            try:
                context = await prepare_scene(provider_client, context, profile, store=store)
                request = room_turn_request_context(context, profile)
                if context['scene_plan']['mode'] == 'resolve':
                    snapshot = await destination_reply(provider_client, context, request, profile, parse_reply=parse_room_reply)
                else:
                    snapshot = await provider_client.response(request, profile)
                if snapshot.finish_reason not in {'stop','end_turn'}:
                    raise ProviderError('incomplete_reply','这段回应尚未完整生成，请再试一次',retryable=True)
                decision = parse_room_reply(snapshot.content)
                if not decision.get('silent'):
                    cached = context.get('reply_observations') or {}
                    if cached.get('reply') == decision['reply']:
                        views = cached['views']
                    else:
                        views = await observe_reply(provider_client,profile,context,decision['reply'])
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
            message = store.add_room_message(room_id, "assistant", character_id, reply, settlement_context=context, observations=views)
            replied.append(character_id)
            pending = store.pending_settlements(room["world"]["id"])
            if pending and pending[0]["source_id"] != message["id"]:
                yield packet("room_reply", message=message)
                yield packet("patch_skipped", character_id=character_id,
                             source_message_id=message["id"], message="这段余波正在等待记录", reason="queued")
                if context.get('scene_plan',{}).get('resolution',{}).get('arrived'):
                    break
                continue
            settlement_task = start_settlements(store, provider_client, profile, room['world']['id'])
            yield packet("room_reply", message=message)
            try:
                result = await settlement_result(settlement_task, store, message['id'])
                yield packet("world_patch", character_id=character_id,
                             source_message_id=message["id"], **result)
            except Exception as error:
                yield packet("patch_skipped", character_id=character_id,
                             source_message_id=message["id"], message="这一段余波还未记下",
                             reason=patch_skip_reason(error))
            if context.get('scene_plan',{}).get('resolution',{}).get('arrived'):
                break
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
