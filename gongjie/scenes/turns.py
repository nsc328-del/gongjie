from __future__ import annotations

import asyncio
import json
from contextlib import aclosing
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from gongjie.ai.providers import ProviderClient, ProviderError
from gongjie.cognition.compiler import request_context
from gongjie.scenes.adjudication import patch_skip_reason
from gongjie.scenes.settlements import start_settlements, settlement_result
from gongjie.scenes.places import prepare_scene, destination_reply
from gongjie.storage.store import Store


def packet(kind: str, **payload: Any) -> bytes:
    value = {"type": kind, **payload}
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


async def reply_snapshots(client, context, profile, continuation):
    request = request_context(context, profile, continuation=continuation)
    if context['scene_plan']['mode'] == 'resolve':
        yield await destination_reply(client, context, request, profile)
    else:
        async with aclosing(client.stream(request, profile)) as snapshots:
            async for snapshot in snapshots:
                yield snapshot


async def _stream_turn(
    *,
    store: Store,
    provider_client: ProviderClient,
    profile_loader: Callable[[], dict[str, Any]],
    conversation_id: str,
    content: str,
    continuation: bool,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[bytes]:
    assistant: dict[str, Any] | None = None
    reply_saved = False
    accumulated = ""
    reasoning = ""
    try:
        store.get_conversation(conversation_id)
        store.assert_conversation_entry(conversation_id)
        clean_content = content.strip()
        if not continuation and not clean_content:
            raise ValueError("message_empty")
        if continuation and clean_content:
            raise ValueError("continuation_must_be_empty")
        profile = profile_loader()
        if clean_content:
            user_message = store.add_message(conversation_id, "user", clean_content)
            yield packet("user", message=user_message)
        assistant = store.add_message(conversation_id, "assistant", "", "streaming")
        yield packet("assistant_start", message=assistant)
        context = store.context_for_turn(conversation_id)
        context["continuation"] = continuation
        context = await prepare_scene(provider_client, context, profile, store=store)
        previous_content = ""
        final_snapshot = None
        async with aclosing(reply_snapshots(provider_client, context, profile, continuation)) as snapshots:
            async for snapshot in snapshots:
                if await is_disconnected():
                    raise asyncio.CancelledError()
                final_snapshot = snapshot
                accumulated = snapshot.content
                reasoning = snapshot.reasoning
                delta = accumulated[len(previous_content):] if accumulated.startswith(previous_content) else accumulated
                previous_content = accumulated
                if delta:
                    yield packet("delta", content=delta, full=accumulated)
                if snapshot.usage or snapshot.finish_reason:
                    yield packet(
                        "meta",
                        finish_reason=snapshot.finish_reason,
                        usage={
                            "input_tokens": snapshot.usage.input_tokens,
                            "output_tokens": snapshot.usage.output_tokens,
                            "cached_input_tokens": snapshot.usage.cached_input_tokens,
                        } if snapshot.usage else None,
                    )
        if not accumulated.strip() or final_snapshot is None:
            raise ProviderError("stream_failed", "模型没有返回正文", retryable=True)
        store.update_message(
            assistant["id"],
            content=accumulated,
            reasoning=reasoning,
            status="complete",
            finish_reason=final_snapshot.finish_reason,
            input_tokens=final_snapshot.usage.input_tokens if final_snapshot.usage else None,
            output_tokens=final_snapshot.usage.output_tokens if final_snapshot.usage else None,
            settlement_context=context,
        )
        reply_saved = True
        settlement_task = start_settlements(store, provider_client, profile, context['world']['id'])
        yield packet("assistant_complete", message_id=assistant["id"], content=accumulated, finish_reason=final_snapshot.finish_reason)
        try:
            result = await settlement_result(settlement_task, store, assistant['id'])
            yield packet("world_patch", **result)
        except Exception as error:
            yield packet(
                "patch_skipped", message="这一段余波还未记下", reason=patch_skip_reason(error),
                source_message_id=assistant["id"],
            )
        yield packet("done", message_id=assistant["id"])
    except (asyncio.CancelledError, GeneratorExit):
        if assistant and not reply_saved:
            if accumulated.strip():
                store.update_message(
                    assistant["id"], content=accumulated, reasoning=reasoning, status="cancelled"
                )
            else:
                store.delete_message(assistant["id"])
        return
    except ProviderError as error:
        if assistant and not reply_saved:
            if accumulated.strip():
                store.update_message(
                    assistant["id"], content=accumulated, reasoning=reasoning, status="error"
                )
            else:
                store.delete_message(assistant["id"])
        yield packet("error", error=error.public_dict(), partial=accumulated)
    except Exception as error:
        if assistant and not reply_saved:
            if accumulated.strip():
                store.update_message(
                    assistant["id"], content=accumulated, reasoning=reasoning, status="error"
                )
            else:
                store.delete_message(assistant["id"])
        yield packet(
            "error",
            error={"code": "request", "message": str(error), "retryable": False},
            partial=accumulated,
        )


async def stream_turn(**kwargs: Any) -> AsyncIterator[bytes]:
    store = kwargs["store"]
    try:
        world_id = store.get_conversation(kwargs["conversation_id"])["conversation"]["world_id"]
        with store.world_operation(world_id):
            async with aclosing(_stream_turn(**kwargs)) as events:
                async for event in events:
                    yield event
    except Exception as error:
        yield packet("error", error={"code": "request", "message": str(error), "retryable": False}, partial="")
