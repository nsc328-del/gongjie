from __future__ import annotations

import asyncio
import json
from typing import Any

from gongjie.ai.providers import ContextMessage, ProviderClient, RequestContext
from gongjie.domain.knowledge import knowledge_projection
from gongjie.storage.store import Store


def summary_context(
    item: dict[str, Any],
    world: dict[str, Any],
    profile: dict[str, Any],
    output_budget: int = 2048,
) -> RequestContext:
    character = item["character"]
    relationship = item.get("relationship") or {}
    new_events = item.get("new_events") or []
    knowledge = item.get("knowledge") or []
    system = """你负责为共享世界中的一名角色更新状态卡片。你不是角色本人，不续写剧情，也不创造新事实。
你只能依据提供给该角色的认知生成一句中文状态摘要。摘要描述角色此刻可被玩家从外部感受到的姿态、余波或关系倾向；即使变化来自私密事实，也不能复述或暗示那件私密事实的具体内容。不要泄露秘密计划，不使用数值、系统字段或开发者术语。
每项认知保留其 knowledge_state、belief 和 source。heard 只证明听到这些话；believed、misunderstood 只是角色所信版本，不把它们改写为客观事实，也不补全来源没有包含的内容。
只返回一句话，20到55个中文字符，不要引号、标题、解释或 Markdown。"""
    payload = {
        "世界": {
            "名称": world["name"],
            "前提": world["premise"],
            "此刻": world["current_time"],
        },
        "角色": {
            "姓名": character["name"],
            "身份": character["role"],
            "人格": character["persona"],
            "原有状态": character.get("state_summary") or character.get("tagline", ""),
        },
        "与玩家的关系": {
            "称呼": relationship.get("label", ""),
            "痕迹": relationship.get("notes", ""),
            "信任": relationship.get("trust", 0),
            "欲望": relationship.get("desire", 0),
            "张力": relationship.get("tension", 0),
        },
        "本次获得的认知": [knowledge_projection(event) for event in new_events],
        "此前认知": [knowledge_projection(entry) for entry in knowledge[:20]],
    }
    return RequestContext(
        system=system,
        messages=[ContextMessage(role="user", content=json.dumps(payload, ensure_ascii=False))],
        max_tokens=min(int(profile.get("max_tokens") or output_budget), output_budget),
        temperature=0.35, thinking=False,
    )


async def sync_world_cognition(
    *, store: Store, client: ProviderClient, profile: dict[str, Any], world_id: str
) -> dict[str, Any]:
    context = store.cognition_sync_context(world_id)
    pending = context["pending_events"]
    if not pending:
        return {"synced_events": 0, "updated_characters": [], "summaries": {}}

    affected = [item for item in context["characters"] if item["new_events"]]

    async def generate(item: dict[str, Any]) -> tuple[str, str]:
        for output_budget in (2048, 4096):
            reply = await client.response(
                summary_context(item, context["world"], profile, output_budget), profile
            )
            summary = " ".join(reply.content.strip().strip('“”"').splitlines()).strip()
            if summary:
                return item["character"]["id"], summary[:120]
        return item["character"]["id"], ""

    generated = await asyncio.gather(*(generate(item) for item in affected))
    summaries = {character_id: summary for character_id, summary in generated if summary}
    result = store.commit_cognition_sync(
        world_id,
        [event["id"] for event in pending],
        summaries,
    )
    return {**result, "summaries": summaries}
