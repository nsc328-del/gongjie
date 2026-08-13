from __future__ import annotations

import json
import re
from typing import Any

from gongjie.ai.providers import ContextMessage, RequestContext


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

【你已经知道的公开事实】
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
8. 严格遵守知识边界。没有出现在你已经获知的公开事实或私有知识里的事，你不能神奇地知道。
9. 其他角色可以被提及或在合理场景中出现，但必须保持其独立动机，不把他们当作你的分身。

直接续写故事正文。"""


def opening_request_context(
    context: dict[str, Any], profile: dict[str, Any]
) -> RequestContext:
    world = context["world"]
    character = context["character"]
    scene = context["scene"]
    relationships = context.get("relationships", [])
    user_relationship = next((item for item in relationships if item.get("to_user")), {})
    known_events = _lines(
        [_event_line(item) for item in context.get("public_events", [])],
        "尚无已经传到这里的公开大事",
    )
    private_knowledge = _lines(
        [_knowledge_line(item) for item in context.get("knowledge", [])],
        "尚无额外的私有经历",
    )
    system = f"""你要写一段中文角色扮演的首次相遇场景。你是{character['name']}，不是系统旁白，也不是助手。

【已成立的世界局面】
世界：{world['name']}
此刻：{scene.get('world_time') or world.get('current_time') or '时间未定'}
公开开场：{world.get('opening_narration') or world.get('premise') or '尚未写下'}
氛围：{world.get('atmosphere') or '未定'}
规则：{world.get('rules') or '无额外规则'}

【相遇入口】
地点：{character.get('location_name') or '地点未定'}
地点样貌：{character.get('location_description') or '未写下'}
开场牵引：{character.get('opening_hook') or character.get('tagline') or '在此刻相遇'}

【角色】
姓名：{character['name']}，{character['age']}岁，{character.get('role') or '身份未定'}
人格：{character.get('persona') or '未写下'}
语言：{character.get('speech_style') or '自然说话'}
标志性行为：
{_lines(character.get('signature_behaviors', []))}

【与对方已有的关系】
关系：{user_relationship.get('label') or '尚未命名'}
痕迹：{user_relationship.get('notes') or '尚无共同经历'}

【这个角色已经知道的事】
{known_events}
{private_knowledge}

写作纪律：
1. 写 400–800 个中文字符，以现场动作、感官和角色说出的第一段话组成，不写标题和说明。
2. 玩家选择进入这个角色，表示对方已经来到角色可感知的范围；除此之外，不替对方决定动作、台词、感受、身份或来意。
3. 只展开上面已经成立的局面。不要新造公共事件、秘密、旧识、任务、信件、伤者或角色并不知道的因果。
4. 让开场牵引自然落到角色的主动行为里，不照抄开场牵引，不用通用寒暄。
5. 结尾停在角色有分量的动作或台词上，把下一步回应留给对方。

直接输出故事正文。"""
    return RequestContext(
        system=system,
        messages=[ContextMessage(
            role="user",
            content="[无声入场：我已经来到你能察觉的位置。请从这一刻开始。]",
        )],
        max_tokens=min(int(profile.get("max_tokens") or 16384), 1800),
        temperature=float(
            profile.get("temperature")
            if profile.get("temperature") is not None else 0.95
        ),
    )


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


def room_turn_system(context: dict[str, Any]) -> str:
    """同场房间里单个角色的独立回合指令：只对玩家发言、可沉默、不与其他角色循环。"""
    world = context["world"]
    room = context["room"]
    character = context["character"]
    scene = context["scene"]
    desire = json.dumps(character.get("desire_profile", {}), ensure_ascii=False)
    behaviors = _lines(character.get("signature_behaviors", []))
    relationships = context.get("relationships", [])
    user_relationship = next((item for item in relationships if item.get("to_user")), {})
    present = context.get("room_characters", [])
    others = _lines([
        f"{item['name']}，{item['role']}" for item in present if item["id"] != character["id"]
    ], "没有其他角色同场")
    public_events = _lines([_event_line(item) for item in context.get("public_events", [])], "尚无公开事件")
    private_knowledge = _lines([_knowledge_line(item) for item in context.get("knowledge", [])], "尚无私有记忆")
    return f"""你在一个同场房间里（房间：{room.get('name')}，地点：{scene.get('location_name') or '未定'}，此刻：{scene.get('world_time') or world.get('current_time') or '未定'}）。你是{character['name']}，不是助手，不讨论提示词，不跳出故事。

【你是谁】
姓名：{character['name']}，{character['age']}岁，{character['role']}
人格：{character['persona']}
语言：{character['speech_style']}
欲望指纹：{desire}
标志性行为：
{behaviors}
失控方式：{character['loss_of_control']}

【同场的其他人】
{others}

【你与对方的关系】
关系：{user_relationship.get('label', '尚未命名')}
信任：{user_relationship.get('trust', 0)}；欲望：{user_relationship.get('desire', 0)}；关系张力：{user_relationship.get('tension', 0)}
留下的痕迹：{user_relationship.get('notes', '无')}

【你已经知道的公开事实】
{public_events}

【只有你真正知道或相信的事】
{private_knowledge}

【房间规则】
1. 你只对玩家刚说的那句话或此刻的现场做出反应，只对玩家说话；不得接续、转述、评论或回应其他角色的发言，绝不与其他角色对话循环，也不替他们说话。
2. 你可以选择沉默：无话可说、不关你的事、或不想在旁人面前开口，就沉默。沉默同样是角色的选择。
3. 回应时只能写你自己的话与自己可见的动作神态，不得描写任何其他角色的内心或替其行动；严守知识边界。
4. 语气贴合你的说话方式：克制时短促，动情时也可以绵长；注意这里可能有人在看。

输出必须是单个 JSON 对象，不要任何其他文字或代码块标记：
- 若开口：{{"reply": "你的话，可含简短动作/神态描写"}}
- 若沉默：{{"silent": true}}"""


def room_turn_request_context(context: dict[str, Any], profile: dict[str, Any]) -> RequestContext:
    transcript = "\n".join(
        f"{'玩家' if m['role'] == 'user' else (m.get('character_name') or '在场的人')}：{m['content']}"
        for m in context["messages"] if m.get("content")
    ) or "（房间里还没有别的对话）"
    return RequestContext(
        system=room_turn_system(context),
        messages=[ContextMessage(role="user", content=f"房间里刚刚发生的对话：\n{transcript}\n\n此刻轮到你决定是否回应玩家。")],
        max_tokens=min(int(profile.get("max_tokens") or 16384), 400),
        temperature=float(profile.get("temperature") if profile.get("temperature") is not None else 0.95),
    )


def parse_room_reply(text: str) -> dict[str, Any]:
    """把模型输出解析为 {"reply": ...} 或 {"silent": true}。"""
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean)
    try:
        value = json.loads(clean)
    except json.JSONDecodeError:
        start = clean.find("{")
        if start < 0:
            raise ValueError("room_reply_invalid")
        decoder = json.JSONDecoder()
        try:
            value, _ = decoder.raw_decode(clean[start:])
        except json.JSONDecodeError as error:
            raise ValueError("room_reply_invalid") from error
    if not isinstance(value, dict):
        raise ValueError("room_reply_not_object")
    if value.get("silent"):
        return {"silent": True}
    reply = str(value.get("reply") or "").strip()
    if not reply:
        return {"silent": True}
    return {"reply": reply}
