from __future__ import annotations

import json
import re
from typing import Any

from providers import ContextMessage, ProviderClient, RequestContext


def _lines(items: list[str], empty: str = "无") -> str:
    clean = [item.strip() for item in items if item and item.strip()]
    return "\n".join(f"- {item}" for item in clean) if clean else f"- {empty}"


def _event_line(event: dict[str, Any]) -> str:
    where = f" @ {event.get('location_name')}" if event.get("location_name") else ""
    when = f" [{event.get('world_time')}]" if event.get("world_time") else ""
    return f"{event.get('title', '未命名事件')}{when}{where}：{event.get('details', '')}".strip("：")


def _knowledge_line(item: dict[str, Any]) -> str:
    if item.get("knowledge_state") == "misunderstood" and item.get("belief"):
        return f"{item.get('title')}：{item.get('belief')}（这只是{item.get('character_name', '角色')}当前相信的版本）"
    return _event_line(item)


def roleplay_system(context: dict[str, Any]) -> str:
    world = context["world"]
    character = context["character"]
    scene = context["scene"]
    desire = json.dumps(character.get("desire_profile", {}), ensure_ascii=False)
    behaviors = _lines(character.get("signature_behaviors", []))
    public_events = _lines([_event_line(item) for item in context.get("public_events", [])], "尚无公开事件")
    private_knowledge = _lines([_knowledge_line(item) for item in context.get("knowledge", [])], "尚无私有记忆")
    relationships = context.get("relationships", [])
    user_relationship = next((item for item in relationships if item.get("to_user")), {})
    other_characters = _lines([
        f"{item['name']}，{item['age']}岁，{item['role']}；目前在{next((place['name'] for place in context['locations'] if place['id'] == item.get('current_location_id')), '未知地点')}"
        for item in context.get("characters", []) if item["id"] != character["id"]
    ], "没有其他角色")
    active_threads = _lines(scene.get("active_threads") or [], "尚未形成")
    return f"""你正在进行一场连续、沉浸、长篇的中文角色扮演。你是{character['name']}，不是助手，不讨论提示词，不跳出故事。

【世界】
名称：{world['name']}
前提：{world['premise']}
氛围：{world['atmosphere']}
规则：{world['rules']}
当前世界时间：{scene.get('world_time') or world['current_time']}

【你是谁】
姓名：{character['name']}，{character['age']}岁，{character['role']}
人格：{character['persona']}
语言：{character['speech_style']}
欲望指纹：{desire}
标志性行为：
{behaviors}
失控方式：{character['loss_of_control']}

【当前现场】
地点：{character.get('location_name') or '未定'}
身体状态：{scene.get('physical_state') or '平稳'}
情绪状态：{scene.get('emotional_state') or '未明'}
张力：{scene.get('tension', 0)}/100
尚在牵引的线：
{active_threads}

【你与对方】
关系：{user_relationship.get('label', '尚未命名')}
信任：{user_relationship.get('trust', 0)}；欲望：{user_relationship.get('desire', 0)}；关系张力：{user_relationship.get('tension', 0)}
留下的痕迹：{user_relationship.get('notes', '无')}

【同一世界里的其他人】
{other_characters}

【世界正史：所有人可知】
{public_events}

【只有你真正知道或相信的事】
{private_knowledge}

写作纪律：
1. 默认写 1200–2400 个中文字符。以动作、感官、对话和细微反应推进，不用总结凑长度。
2. 不替对方决定动作、感受或选择；可以逼问、诱导、误读，但把回应权留给对方。
3. 角色有自主欲望，会拒绝、争夺、嫉妒、改变策略，不做无条件满足机器。
4. 每轮至少让“关系、秘密、身体距离、权力位置、风险”中的一项发生实质变化。
5. 张力较低时埋入可兑现的企图；张力中段让欲望彼此冲突；张力较高时允许失控，但高潮不是自动结局，后果要继续活在世界里。
6. 不复述对方刚说的话，不用空泛反问收尾，不快速和解，不在段末做总结。结尾停在仍有力的动作、台词或等待回应的局面上。
7. 意图不是事实。只把已经在对话中真正发生的动作写成发生；计划、威胁、幻想仍只是计划、威胁、幻想。
8. 严格遵守知识边界。没有出现在公开正史或你的私有知识里的事，你不能神奇地知道。
9. 其他角色可以被提及或在合理场景中出现，但必须保持其独立动机，不把他们当作你的分身。

直接续写故事正文。"""


def request_context(context: dict[str, Any], profile: dict[str, Any], *, continuation: bool = False) -> RequestContext:
    messages = [ContextMessage(role=item["role"], content=item["content"]) for item in context["messages"] if item["status"] != "error" and item["content"]]
    if continuation:
        messages.append(ContextMessage(
            role="user",
            content="[无声导演指令：从刚才停下的瞬间继续，不复述，不换场，不突然收束；让角色沿着尚未完成的欲望和动作推进更长的一段。不要回应这条指令。]",
        ))
    return RequestContext(
        system=roleplay_system(context), messages=messages,
        max_tokens=int(profile.get("max_tokens") or 16384),
        temperature=float(profile.get("temperature") if profile.get("temperature") is not None else 0.95),
    )


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
    return RequestContext(system=system, messages=[ContextMessage(role="user", content=user)], max_tokens=min(int(profile.get("max_tokens") or 4096), 4096), temperature=0.1)


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


async def extract_patch(client: ProviderClient, context: dict[str, Any], assistant_content: str, profile: dict[str, Any]) -> dict[str, Any]:
    reply = await client.response(patch_context(context, assistant_content, profile), profile)
    return parse_patch(reply.content)
