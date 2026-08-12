from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from db import Store
from engine import extract_patch, request_context
from providers import ContextMessage, ProviderClient, ProviderError, RequestContext, SecretStore


ROOT = Path(__file__).resolve().parent
store = Store(ROOT / "data" / "gongjie.db")
secrets = SecretStore(ROOT / "data" / "secrets.json")
provider_client = ProviderClient()
store.initialize()

app = FastAPI(title="共界", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class ConversationCreate(BaseModel):
    world_id: str
    character_id: str


class TurnInput(BaseModel):
    content: str = ""
    branch_id: str | None = None
    continuation: bool = False


class BranchCreate(BaseModel):
    parent_branch_id: str
    from_message_id: str
    title: str | None = None


class ProviderInput(BaseModel):
    id: str = "provider_default"
    name: str = "我的模型"
    protocol: str
    base_url: str
    path: str
    model: str
    api_key: str = ""
    max_tokens: int = Field(default=16384, ge=256, le=200000)
    temperature: float = Field(default=0.95, ge=0, le=2)


class WorldInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    premise: str = ""
    atmosphere: str = ""
    rules: str = ""
    current_time: str = ""


class LocationInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = ""
    mood: str = ""


class CharacterInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    age: int = Field(ge=18, le=999)
    pronouns: str = ""
    role: str = ""
    tagline: str = ""
    persona: str = ""
    speech_style: str = ""
    desire_profile: dict[str, int] = Field(default_factory=dict)
    signature_behaviors: list[str] = Field(default_factory=list)
    loss_of_control: str = ""
    current_location_id: str | None = None
    accent: str = "#bc5d45"


def api_error(error: Exception) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(404, str(error).strip("'"))
    if isinstance(error, ValueError):
        return HTTPException(400, str(error))
    return HTTPException(500, "internal_error")


def active_profile() -> dict[str, Any]:
    profile = store.active_provider()
    profile["api_key"] = secrets.get(profile["id"])
    if not profile["api_key"]:
        raise ProviderError("auth", "先连接你的模型", retryable=False)
    if not profile["model"].strip():
        raise ProviderError("model_unavailable", "先填写模型名称", retryable=False)
    return profile


def packet(kind: str, **payload: Any) -> bytes:
    return (json.dumps({"type": kind, **payload}, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/bootstrap")
async def bootstrap() -> dict[str, Any]:
    try:
        profile = store.active_provider()
    except KeyError:
        profile = None
    public_profile = None
    if profile:
        public_profile = {**profile, "has_key": secrets.has(profile["id"])}
    return {"worlds": store.list_worlds(), "provider": public_profile}


@app.get("/api/worlds/{world_id}")
async def world(world_id: str) -> dict[str, Any]:
    try:
        return store.get_world(world_id)
    except Exception as error:
        raise api_error(error) from error


@app.post("/api/worlds")
async def create_world(body: WorldInput) -> dict[str, Any]:
    try:
        return store.create_world(body.model_dump())
    except Exception as error:
        raise api_error(error) from error


@app.put("/api/worlds/{world_id}")
async def update_world(world_id: str, body: WorldInput) -> dict[str, Any]:
    try:
        return store.update_world(world_id, body.model_dump())
    except Exception as error:
        raise api_error(error) from error


@app.post("/api/worlds/{world_id}/locations")
async def create_location(world_id: str, body: LocationInput) -> dict[str, Any]:
    try:
        return store.create_location(world_id, body.model_dump())
    except Exception as error:
        raise api_error(error) from error


@app.post("/api/worlds/{world_id}/characters")
async def create_character(world_id: str, body: CharacterInput) -> dict[str, Any]:
    try:
        return store.create_character(world_id, body.model_dump())
    except Exception as error:
        raise api_error(error) from error


@app.put("/api/characters/{character_id}")
async def update_character(character_id: str, body: CharacterInput) -> dict[str, Any]:
    try:
        return store.update_character(character_id, body.model_dump())
    except Exception as error:
        raise api_error(error) from error


@app.post("/api/conversations")
async def create_conversation(body: ConversationCreate) -> dict[str, Any]:
    try:
        conversation_id = store.get_or_create_conversation(body.world_id, body.character_id)
        return store.get_conversation(conversation_id)
    except Exception as error:
        raise api_error(error) from error


@app.get("/api/conversations/{conversation_id}")
async def conversation(conversation_id: str, branch_id: str | None = None) -> dict[str, Any]:
    try:
        return store.get_conversation(conversation_id, branch_id)
    except Exception as error:
        raise api_error(error) from error


@app.post("/api/conversations/{conversation_id}/branches")
async def fork_branch(conversation_id: str, body: BranchCreate) -> dict[str, Any]:
    try:
        branch_id = store.fork_branch(conversation_id, body.parent_branch_id, body.from_message_id, body.title)
        return store.get_conversation(conversation_id, branch_id)
    except Exception as error:
        raise api_error(error) from error


@app.post("/api/conversations/{conversation_id}/branches/{branch_id}/activate")
async def activate_branch(conversation_id: str, branch_id: str) -> dict[str, Any]:
    try:
        store.activate_branch(conversation_id, branch_id)
        return store.get_conversation(conversation_id, branch_id)
    except Exception as error:
        raise api_error(error) from error


@app.post("/api/conversations/{conversation_id}/branches/{branch_id}/canon")
async def promote_branch(conversation_id: str, branch_id: str) -> dict[str, Any]:
    try:
        store.promote_branch(conversation_id, branch_id)
        return store.get_conversation(conversation_id, branch_id)
    except Exception as error:
        raise api_error(error) from error


@app.post("/api/conversations/{conversation_id}/turn")
async def turn(conversation_id: str, body: TurnInput, request: Request) -> StreamingResponse:
    async def generate() -> AsyncIterator[bytes]:
        assistant: dict[str, Any] | None = None
        accumulated = ""
        reasoning = ""
        try:
            current = store.get_conversation(conversation_id, body.branch_id)
            branch_id = current["branch_id"]
            content = body.content.strip()
            if not body.continuation and not content:
                raise ValueError("message_empty")
            if body.continuation and content:
                raise ValueError("continuation_must_be_empty")
            if content:
                user_message = store.add_message(conversation_id, branch_id, "user", content)
                yield packet("user", message=user_message)
            assistant = store.add_message(conversation_id, branch_id, "assistant", "", "streaming")
            yield packet("assistant_start", message=assistant)
            context = store.context_for_turn(conversation_id, branch_id)
            profile = active_profile()
            previous_content = ""
            async for snapshot in provider_client.stream(request_context(context, profile, continuation=body.continuation), profile):
                if await request.is_disconnected():
                    raise asyncio.CancelledError()
                accumulated = snapshot.content
                reasoning = snapshot.reasoning
                delta = accumulated[len(previous_content):] if accumulated.startswith(previous_content) else accumulated
                previous_content = accumulated
                if delta:
                    yield packet("delta", content=delta, full=accumulated)
                if snapshot.usage or snapshot.finish_reason:
                    yield packet(
                        "meta", finish_reason=snapshot.finish_reason,
                        usage={"input_tokens": snapshot.usage.input_tokens, "output_tokens": snapshot.usage.output_tokens, "cached_input_tokens": snapshot.usage.cached_input_tokens} if snapshot.usage else None,
                    )
            if not accumulated.strip():
                raise ProviderError("stream_failed", "模型没有返回正文", retryable=True)
            final_snapshot = snapshot
            store.update_message(
                assistant["id"], content=accumulated, reasoning=reasoning, status="complete",
                finish_reason=final_snapshot.finish_reason,
                input_tokens=final_snapshot.usage.input_tokens if final_snapshot.usage else None,
                output_tokens=final_snapshot.usage.output_tokens if final_snapshot.usage else None,
            )
            try:
                world_patch = await extract_patch(provider_client, context, accumulated, profile)
                patch_result = store.apply_world_patch(conversation_id, branch_id, assistant["id"], world_patch)
                yield packet("world_patch", patch=world_patch, result=patch_result)
            except Exception as patch_error:
                yield packet("patch_skipped", message="本轮未写入世界状态", detail=str(patch_error))
            yield packet("done", message_id=assistant["id"])
        except asyncio.CancelledError:
            if assistant:
                store.update_message(assistant["id"], content=accumulated or "已取消", reasoning=reasoning, status="cancelled")
            return
        except ProviderError as error:
            if assistant:
                visible = accumulated or error.message
                store.update_message(assistant["id"], content=visible, reasoning=reasoning, status="error")
            yield packet("error", error=error.public_dict(), partial=accumulated)
        except Exception as error:
            if assistant:
                store.update_message(assistant["id"], content=accumulated or "这一刻没有继续下去。", reasoning=reasoning, status="error")
            yield packet("error", error={"code": "request", "message": str(error), "retryable": False}, partial=accumulated)

    return StreamingResponse(generate(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.put("/api/provider")
async def save_provider(body: ProviderInput) -> dict[str, Any]:
    try:
        profile = store.save_provider(body.model_dump(exclude={"api_key"}))
        if body.api_key:
            secrets.set(profile["id"], body.api_key)
        return {**profile, "has_key": secrets.has(profile["id"])}
    except Exception as error:
        raise api_error(error) from error


@app.post("/api/provider/test")
async def test_provider() -> dict[str, Any]:
    try:
        profile = active_profile()
        reply = await provider_client.response(
            RequestContext(
                system="只回复：连接成功",
                messages=[ContextMessage(role="user", content="测试")],
                max_tokens=32,
                temperature=0,
            ),
            profile,
        )
        return {"ok": True, "reply": reply.content.strip(), "model": reply.model or profile["model"]}
    except ProviderError as error:
        raise HTTPException(400, error.public_dict()) from error
