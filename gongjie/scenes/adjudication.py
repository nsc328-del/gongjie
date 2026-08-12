from __future__ import annotations

import json
import re
from typing import Any

from gongjie.ai.providers import ContextMessage, ProviderClient, RequestContext


def patch_context(context: dict[str, Any], assistant_content: str, profile: dict[str, Any]) -> RequestContext:
    scene = context["scene"]
    character_names = [item["name"] for item in context["characters"]]
    locations = [item["name"] for item in context["locations"]]
    recent = [item for item in context["messages"] if item["content"]][-4:]
    transcript = "\n".join(f"{item['role']}: {item['content']}" for item in recent)
    system = """你是共享世界的状态裁判，不参与表演。你的任务是判断一轮对话后，什么真正发生了。
严格区分：说出口的计划、威胁、幻想、试探不是已发生事实。只有正文明确完成的动作和明确产生的关系变化才可写入。
只返回一个 JSON 对象，不要 Markdown，不要解释。字段必须符合：
{
  "event": null 或 {
    "title": "不超过18字的已发生事件",
    "details": "只写客观发生的事实，不写推测",
    "visibility": "public|private|secret",
    "participants": ["角色名"],
    "witnesses": ["亲眼知情的角色名"],
    "beliefs": {"角色名": "仅当该角色误解时填写其相信的版本"},
    "world_time": "可留空"
  },
  "scene": {
    "location": "现有地点名或空字符串",
    "world_time": "变化后的时间或空字符串",
    "tension": 0到100,
    "active_threads": ["仍未解决且会继续推动场景的线，最多4条"],
    "physical_state": "当前可观察的身体/距离状态",
    "emotional_state": "当前角色的情绪状态"
  },
  "relationship": {
    "trust_delta": -5到5,
    "desire_delta": -5到5,
    "tension_delta": -8到8,
    "label": "必要时更新，否则空字符串",
    "note": "这一轮留下的关系痕迹，必要时更新，否则空字符串"
  }
}
若没有值得跨对话保留的事实，event 必须为 null；scene 和 relationship 仍按真实变化填写。"""
    user = f"""角色：{context['character']['name']}
世界角色名：{json.dumps(character_names, ensure_ascii=False)}
现有地点：{json.dumps(locations, ensure_ascii=False)}
当前场景：{json.dumps(scene, ensure_ascii=False)}

最近上下文：
{transcript}

本轮角色回复：
{assistant_content}
"""
    return RequestContext(
        system=system,
        messages=[ContextMessage(role="user", content=user)],
        max_tokens=min(int(profile.get("max_tokens") or 4096), 4096),
        temperature=0.1,
    )


def parse_patch(text: str) -> dict[str, Any]:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean)
    try:
        value = json.loads(clean)
    except json.JSONDecodeError:
        start = clean.find("{")
        if start < 0:
            raise ValueError("world_patch_missing")
        decoder = json.JSONDecoder()
        try:
            value, _ = decoder.raw_decode(clean[start:])
        except json.JSONDecodeError as error:
            raise ValueError("world_patch_invalid") from error
    if not isinstance(value, dict):
        raise ValueError("world_patch_not_object")
    return value


async def extract_patch(
    client: ProviderClient,
    context: dict[str, Any],
    assistant_content: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    reply = await client.response(patch_context(context, assistant_content, profile), profile)
    return parse_patch(reply.content)
