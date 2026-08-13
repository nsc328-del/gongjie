from __future__ import annotations

from fastapi import APIRouter, HTTPException

from gongjie.ai.providers import ContextMessage, ProviderError, RequestContext
from gongjie.api.errors import api_error
from gongjie.api.schemas import ProviderInput
from gongjie.runtime import active_profile, provider_client, secrets, store


router = APIRouter(prefix="/api/provider")


@router.put("")
async def save_provider(body: ProviderInput) -> dict:
    try:
        profile = store.save_provider(body.model_dump(exclude={"api_key"}))
        if body.api_key:
            secrets.set(profile["id"], body.api_key)
        return {**profile, "has_key": secrets.has(profile["id"])}
    except Exception as error:
        raise api_error(error) from error


@router.post("/test")
async def test_provider() -> dict:
    try:
        profile = active_profile()
        reply = await provider_client.response(
            RequestContext(
                system="只回复：连接成功",
                messages=[ContextMessage(role="user", content="测试")],
                max_tokens=512,
                temperature=0,
            ),
            profile,
        )
        content = reply.content.strip()
        if not content:
            raise ProviderError("empty_reply", "模型已连接，但没有返回正文", retryable=True)
        return {"ok": True, "reply": content, "model": reply.model or profile["model"]}
    except ProviderError as error:
        raise HTTPException(400, error.public_dict()) from error
