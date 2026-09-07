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
    if item.get("knowledge_state") in {"misunderstood", "believed"} and item.get("belief"):
        return f"{item.get('title')}：{item.get('belief')}（这只是{item.get('character_name', '角色')}当前相信的版本）"
    return _event_line(item)


def _player_lines(context: dict[str, Any]) -> str:
    player = context.get("player") or {}
    traces = _lines(player.get("visible_traces") or [], "没有明显痕迹")
    commitments = _lines(
        [item.get("text", "") for item in player.get("commitments") or []],
        "没有仍悬着的承诺",
    )
    return (
        f"所在：{player.get('location_name') or '尚未确定'}\n"
        f"外部状态：{player.get('condition') or '没有特别变化'}\n"
        f"你能看见的痕迹：\n{traces}\n"
        f"对你仍有效的承诺：\n{commitments}"
    )


def roleplay_system(context: dict[str, Any]) -> str:
    world = context["world"]
    character = context["character"]
    scene = context["scene"]
    desire = json.dumps(character.get("desire_profile", {}), ensure_ascii=False)
    behaviors = _lines(character.get("signature_behaviors", []))
    public_events = _lines([_knowledge_line(item) for item in context.get("public_events", [])], "尚无公开事件")
    private_knowledge = _lines([_knowledge_line(item) for item in context.get("knowledge", [])], "尚无私有记忆")
    relationships = context.get("relationships", [])
    user_relationship = next((item for item in relationships if item.get("to_user")), {})
    other_characters = _lines([
        f"{item['name']}，{item['age']}岁，{item['role']}"
        for item in context.get("characters", []) if item["id"] != character["id"]
    ], "没有其他角色")
    active_threads = _lines(scene.get("active_threads") or [], "尚未形成")
    character_world_state = context.get("character_world_state") or {}
    return f"""你正在进行一场连续、沉浸、长篇的中文角色扮演。你是{character['name']}，不是助手，不讨论提示词，不跳出故事。

【世界】
名称：{world['name']}
前提：{world['premise']}
氛围：{world['atmosphere']}
规则：{world['rules']}
当前世界时间：{scene.get('world_time') or world['current_time']}

【你是谁】
姓名：{character['name']}，{character['age']}岁，{character['role']}
称谓：{character.get('pronouns') or '依角色设定'}
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

【对方此刻来到你面前时】
{_player_lines(context)}

【你在上一次世界脉搏中决定的下一步】
{character_world_state.get('intention') or '没有预先决定的行动，依照眼前局面反应'}

【同一世界里的其他人】
{other_characters}

【你已经知道的公开事实】
{public_events}

【只有你真正知道或相信的事】
{private_knowledge}

叙事视角：
- 角色说出的台词可以自然用“我”自称。
- 引号外的动作、神态、感官和环境反应一律使用贴近{character['name']}的第三人称限知叙述，以“{character['name']}”或“他/她”为主语；不要写“我抬手”“我走过去”一类第一人称动作旁白。即使历史消息使用过这种写法，从本轮起也不要继续模仿。
- 第三人称不等于全知。不要越过{character['name']}的感知与认知，也不要用括号或星号标注动作。

写作纪律：
1. 默认写 400–900 个中文字符。以动作、感官、对话和细微反应推进，不用总结凑长度；场景需要更短时自然收住，需要完整阶段时可以略长。
2. 不替对方新增本轮没有给出的动作、感受或选择；对方在本轮明确写出的动作视为已经发生，可以直接承接。双方已经明确自愿且边界没有变化时，不要每轮重新索要许可。
3. 角色有自主欲望，会拒绝、争夺、嫉妒、改变策略，不做无条件满足机器。
4. 回应眼前真正发生的事。角色可以推进、拒绝、等待或保持原有态度；平静告别与普通相处可以自然收住，不为凑戏剧性强行改变关系、秘密、距离或风险。
5. 张力较低时埋入可兑现的企图；张力中段让欲望彼此冲突；张力较高时允许失控，但高潮不是自动结局，后果要继续活在世界里。
6. 不复述对方刚说的话，不用空泛反问收尾，不快速和解，不在段末做总结。普通互动可以停在有力动作或等待回应的局面；若对方明确要求连续完成一段双方自愿的场景，就让角色把自己一侧自然推进到阶段结果或余波，再交还回应权，不要在每个关键动作前机械暂停。
7. 意图不是事实。只把已经在对话中真正发生的动作写成发生；计划、威胁、幻想仍只是计划、威胁、幻想。
8. 严格遵守知识边界。没有出现在你已经获知的公开事实或私有知识里的事，你不能神奇地知道。可以观察并自由描写身体痕迹、衣物、气味、姿态等软状态，但不知道来源时只能猜测、试探或误读，不能断言是谁造成或据此确认隐秘经历。
9. 其他角色可以被提及或在合理场景中出现，但必须保持其独立动机，不把他们当作你的分身。

直接续写故事正文。"""


def _character_identity(character: dict[str, Any]) -> str:
    return f"""姓名：{character['name']}，{character['age']}岁，{character.get('role') or '身份未定'}
称谓：{character.get('pronouns') or '依角色设定'}
人格：{character.get('persona') or '未写下'}
语言：{character.get('speech_style') or '自然说话'}
欲望指纹：{json.dumps(character.get('desire_profile', {}), ensure_ascii=False)}
标志性行为：
{_lines(character.get('signature_behaviors', []))}
失控方式：{character.get('loss_of_control') or '未写下'}"""


def opening_request_context(
    context: dict[str, Any], profile: dict[str, Any], *, reentry: bool = False
) -> RequestContext:
    world = context["world"]
    character = context["character"]
    scene = context["scene"]
    relationships = context.get("relationships", [])
    user_relationship = next((item for item in relationships if item.get("to_user")), {})
    known_events = _lines(
        [_knowledge_line(item) for item in context.get("public_events", [])],
        "尚无已经传到这里的公开大事",
    )
    private_knowledge = _lines(
        [_knowledge_line(item) for item in context.get("knowledge", [])],
        "尚无额外的私有经历",
    )
    character_world_state = context.get("character_world_state") or {}
    pulse_number = int((context.get("pulse") or {}).get("number", 0))
    current_hook = (
        character_world_state.get("encounter_hook")
        if int(character_world_state.get("pulse_number") or 0) == pulse_number else ""
    )
    opening_situation = (
        world.get("premise") if pulse_number else world.get("opening_narration") or world.get("premise")
    ) or "尚未写下"
    encounter_hook = current_hook or (
        "在当前时段首次相遇，沿用角色原有生活，不重演最初开场"
        if pulse_number else character.get("opening_hook") or character.get("tagline") or "在此刻相遇"
    )
    if reentry:
        recent_messages = [
            item for item in context.get("messages", [])
            if item.get("content") and item.get("status") != "error"
        ][-6:]
        recent = "\n".join(
            f"{'对方' if item['role'] == 'user' else character['name']}：{item['content']}"
            for item in recent_messages
        ) or "（此前没有留下可用的对话）"
        system = f"""你要写一段中文角色扮演的新时段接续开场。世界已经向前一步，玩家现在第一次重新进入{character['name']}在这个时段的私人现场。你不是全知旁白，也不是助手。

【角色的稳定底色】
{_character_identity(character)}

【已经成立的新时段】
世界：{world['name']}
此刻：{scene.get('world_time') or world.get('current_time') or '时间未定'}
公开局面：{known_events}
地点：{character.get('location_name') or '地点未定'}
现场余波：{scene.get('physical_state') or '没有特别变化'}；{scene.get('emotional_state') or '情绪未明'}
未结的线：
{_lines(scene.get('active_threads') or [], '尚无')}

【{character['name']}在这个时段】
外部姿态：{character_world_state.get('surface_state') or character.get('state_summary') or character.get('tagline') or '没有特别变化'}
自己的下一步：{character_world_state.get('intention') or '没有预先决定的行动'}
可被玩家察觉的相遇牵引：{current_hook or '没有新的牵引；按角色原有生活与上一场余波自然继续'}

【与对方的关系】
关系：{user_relationship.get('label') or '尚未命名'}
痕迹：{user_relationship.get('notes') or '尚无共同经历'}

【对方此刻来到面前时】
{_player_lines(context)}

【上一次私人现场的结尾】
{recent}

【这个角色已经知道的事】
{known_events}
{private_knowledge}

写作纪律：
1. 写 200–450 个中文字符，以新的时间、地点、角色正在做的事和第一段有分量的话组成，不写标题和说明。
2. 玩家点击进入表示对方已经来到{character['name']}可感知的范围；除此之外，不替对方决定动作、台词、感受或来意。
3. 只承接上面已经成立的状态。若角色没有参与上一时段、也没有获得相关事实，不得强行让他知道或卷入；没有新牵引时，就从他自己的生活与原有余波继续。
4. 不创造新的公共事件、秘密、任务或已经完成的动作；这一段只负责把玩家带进现场，不触发事实裁判。
5. 台词可以自然用“我”；引号外的动作、神态、感官与环境反应必须使用贴近{character['name']}的第三人称限知叙述，不写第一人称动作旁白，不使用括号或星号标动作。
6. 不复述整段旧对话，用少量可感知细节说明时间确实向前了；结尾把下一步回应留给对方。

直接输出故事正文。"""
        return RequestContext(
            thinking=False,
            system=system,
            messages=[ContextMessage(
                role="user",
                content="[无声重新入场：我来到你在这个时段能察觉的位置。请从新的此刻接上故事。]",
            )],
            max_tokens=int(profile.get("max_tokens") or 16384),
            temperature=float(
                profile.get("temperature")
                if profile.get("temperature") is not None else 0.95
            ),
        )
    system = f"""你要写一段中文角色扮演的首次相遇场景。叙事镜头贴近{character['name']}，你不是全知旁白，也不是助手。

【已成立的世界局面】
世界：{world['name']}
此刻：{scene.get('world_time') or world.get('current_time') or '时间未定'}
公开开场：{opening_situation}
氛围：{world.get('atmosphere') or '未定'}
规则：{world.get('rules') or '无额外规则'}

【相遇入口】
地点：{character.get('location_name') or '地点未定'}
地点样貌：{character.get('location_description') or '未写下'}
开场牵引：{encounter_hook}

【角色】
{_character_identity(character)}

【与对方已有的关系】
关系：{user_relationship.get('label') or '尚未命名'}
痕迹：{user_relationship.get('notes') or '尚无共同经历'}

【对方此刻来到你面前时】
{_player_lines(context)}

【你在上一次世界脉搏中决定的下一步】
{character_world_state.get('intention') or '没有预先决定的行动'}

【这个角色已经知道的事】
{known_events}
{private_knowledge}

写作纪律：
1. 写 200–450 个中文字符，以现场动作、感官和角色说出的第一段话组成，不写标题和说明。
2. 角色台词可以自然用“我”；引号外的动作、神态、感官与环境反应必须使用贴近{character['name']}的第三人称限知叙述，以“{character['name']}”或“他/她”为主语，不写第一人称动作旁白，不使用括号或星号标动作。
3. 玩家选择进入这个角色，表示对方已经来到角色可感知的范围；除此之外，不替对方决定动作、台词、感受、身份或来意。
4. 只展开上面已经成立的局面。不要新造公共事件、秘密、旧识、任务、信件、伤者或角色并不知道的因果。
5. 让开场牵引自然落到角色的主动行为里，不照抄开场牵引，不用通用寒暄。
6. 结尾停在角色有分量的动作或台词上，把下一步回应留给对方。

直接输出故事正文。"""
    return RequestContext(
        thinking=False,
        system=system,
        messages=[ContextMessage(
            role="user",
            content="[无声入场：我已经来到你能察觉的位置。请从这一刻开始。]",
        )],
        max_tokens=int(profile.get("max_tokens") or 16384),
        temperature=float(
            profile.get("temperature")
            if profile.get("temperature") is not None else 0.95
        ),
    )


def scene_entry_grounding_context(
    context: dict[str, Any], draft: str, profile: dict[str, Any]
) -> RequestContext:
    """Build a fact-ledger review for a reentry draft before it becomes history."""
    character = context["character"]
    relationships = context.get("relationships") or []
    user_relationship = next((item for item in relationships if item.get("to_user")), {})
    recent_messages = [
        {
            "role": item.get("role"),
            "content": item.get("content"),
        }
        for item in context.get("messages") or []
        if item.get("content") and item.get("status") != "error"
    ][-6:]
    ledger = {
        "world": {
            "name": context["world"].get("name"),
            "current_time": context["scene"].get("world_time")
            or context["world"].get("current_time"),
            "atmosphere": context["world"].get("atmosphere"),
            "rules": context["world"].get("rules"),
        },
        "character": {
            "name": character.get("name"),
            "role": character.get("role"),
            "persona": character.get("persona"),
            "speech_style": character.get("speech_style"),
            "pronouns": character.get("pronouns"),
            "location": character.get("location_name"),
            "location_description": character.get("location_description"),
        },
        "scene": {
            key: context["scene"].get(key)
            for key in (
                "physical_state", "emotional_state", "tension", "active_threads"
            )
        },
        "relationship_with_player": {
            key: user_relationship.get(key)
            for key in ("label", "trust", "desire", "tension", "notes")
        },
        "player": context.get("player") or {},
        "character_action_state": {
            key: (context.get("character_world_state") or {}).get(key)
            for key in ("intention", "surface_state", "availability")
        },
        "public_facts_known_by_character": [
            {
                key: item.get(key)
                for key in ("id", "title", "details", "world_time", "location_name", "knowledge_state", "belief")
            }
            for item in context.get("public_events") or []
        ],
        "private_facts_or_beliefs": [
            {
                key: item.get(key)
                for key in (
                    "id", "title", "details", "world_time", "location_name",
                    "knowledge_state", "belief",
                )
            }
            for item in context.get("knowledge") or []
        ],
        "recent_dialogue": recent_messages,
    }
    if not (context.get("pulse") or {}).get("number"):
        ledger["initial_situation"] = {
            "public_opening": context["world"].get("opening_narration") or context["world"].get("premise"),
            "encounter_hook": character.get("opening_hook"),
        }
    system = """你是角色扮演接续开场的记忆校对员。输入包含一份结构化记忆账本和一版草稿。你的工作不是续写，而是把草稿校准到已存状态后再交付。

只返回严格 JSON，不要 Markdown、前后说明或额外字段：
{"text":"校对后的完整开场正文","removed":["被删除或改写的无来源具体断言，简述即可"]}

校对规则：
1. 草稿中的每个硬状态或因果断言，都必须能在账本字段中找到来源。包括伤痕由谁造成、脚印或苔藓证明玩家去过哪里、信件、报纸、钥匙等承担剧情作用的物品、他人到访、调查结果、发现的线索、已完成的关键行动和玩家走过的路线。
2. intention、active_threads 只是意图或牵引，不代表事情已经发生；intention 可以作为角色此刻声明的计划，但不得把它改写为已经完成的动作。输入没有提供世界脉搏 prose 或 encounter_hook，因为它们都不是事实凭证，不得从草稿本身反推其内容成立。
3. 角色只能知道 public_facts_known_by_character、private_facts_or_beliefs 与 recent_dialogue 中明确存在的内容。belief 仍只是角色相信的版本。
4. 可以自由保留或补足软连续性：光线、普通风声、温度、固定陈设，以及不承担证据作用的身体痕迹、衣物、气味、姿态和距离。这些细节可以自然变化，但不能被写成某段未知历史的证明；可被拿取、阅读或携带且推动剧情的道具仍必须有字段来源。
5. 若草稿有无来源内容，删除它或改写为不作事实断言的中性细节，同时尽量保留原有文风、长度、动作和台词结构。不要借校对机会增加新情节。
6. 玩家点击进入只表示玩家来到角色可感知范围；除 player 与 recent_dialogue 明示内容外，不替玩家补动作、感受、来意或身体状态。
7. 特别检查“昨晚替伤者缝针”“已完成调查”“站了一夜”“来自某码头的木屑/气味”等历史行动与来源推断；普通潮湿和衣着不能证明经历或时长。没有明确账本来源就整句删除，不得换一种肯定语气留下。使用角色给定称谓，不改写性别。
8. text 必须是可直接保存的完整正文，不能包含校对说明；无法可靠校对时返回空 text，禁止原样放行。"""
    return RequestContext(
        thinking=True, reasoning_effort="low",
        system=system,
        messages=[ContextMessage(
            role="user",
            content=json.dumps(
                {"memory_ledger": ledger, "draft": draft}, ensure_ascii=False
            ),
        )],
        max_tokens=int(profile.get("max_tokens") or 16384),
        temperature=0.2,
    )


def parse_scene_entry_grounding(text: str) -> dict[str, Any]:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean)
    try:
        value = json.loads(clean)
    except json.JSONDecodeError as error:
        raise ValueError("scene_entry_grounding_invalid") from error
    if not isinstance(value, dict):
        raise ValueError("scene_entry_grounding_invalid")
    grounded_text = value.get("text")
    removed = value.get("removed", [])
    if not isinstance(grounded_text, str) or not grounded_text.strip():
        raise ValueError("scene_entry_grounding_empty")
    if not isinstance(removed, list) or not all(isinstance(item, str) for item in removed):
        raise ValueError("scene_entry_grounding_invalid")
    return {
        "text": grounded_text.strip(),
        "removed": [item.strip() for item in removed if item.strip()],
    }


def request_context(context: dict[str, Any], profile: dict[str, Any], *, continuation: bool = False) -> RequestContext:
    messages = [ContextMessage(role=item["role"], content=item["content"]) for item in context["messages"] if item["status"] != "error" and item["content"]]
    if continuation:
        messages.append(ContextMessage(
            role="user",
            content="[无声导演指令：从刚才停下的瞬间继续，不复述，不换场，不突然收束；让角色沿着尚未完成的欲望和动作推进更长的一段。不要回应这条指令。]",
        ))
    return RequestContext(
        thinking=False,
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
    public_events = _lines([_knowledge_line(item) for item in context.get("public_events", [])], "尚无公开事件")
    private_knowledge = _lines([_knowledge_line(item) for item in context.get("knowledge", [])], "尚无私有记忆")
    character_world_state = context.get("character_world_state") or {}
    return f"""你在一个同场房间里（房间：{room.get('name')}，地点：{scene.get('location_name') or '未定'}，此刻：{scene.get('world_time') or world.get('current_time') or '未定'}）。你是{character['name']}，不是助手，不讨论提示词，不跳出故事。

【你是谁】
姓名：{character['name']}，{character['age']}岁，{character['role']}
称谓：{character.get('pronouns') or '依角色设定'}
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

【对方此刻的外部状态】
{_player_lines(context)}

【你在上一次世界脉搏中决定的下一步】
{character_world_state.get('intention') or '没有预先决定的行动'}

【你已经知道的公开事实】
{public_events}

【只有你真正知道或相信的事】
{private_knowledge}

【房间规则】
1. 你只对玩家刚说的那句话或此刻的现场做出反应，只对玩家说话；不得接续、转述、评论或回应其他角色的发言，绝不与其他角色对话循环，也不替他们说话。
2. 你可以选择沉默：无话可说、不关你的事、或不想在旁人面前开口，就沉默。沉默同样是角色的选择。
3. 回应时只能写你自己的话与自己可见的动作神态。台词可以自然用“我”；引号外的动作与神态必须以“{character['name']}”或“他/她”作主语，使用第三人称限知叙述，不写第一人称动作旁白，不使用括号或星号标动作。即使房间历史中出现过第一人称动作旁白，也不要模仿。
4. 语气贴合你的说话方式：克制时短促，动情时也可以绵长；注意这里可能有人在看。

输出必须是单个 JSON 对象，不要任何其他文字或代码块标记：
- 若开口：{{"reply": "{character['name']}垂下眼：‘我在听。’"}}
- 若沉默：{{"silent": true}}"""


def room_turn_request_context(context: dict[str, Any], profile: dict[str, Any]) -> RequestContext:
    transcript = "\n".join(
        f"{'玩家' if m['role'] == 'user' else (m.get('character_name') or '在场的人')}：{m['content']}"
        for m in context["messages"] if m.get("content")
    ) or "（房间里还没有别的对话）"
    return RequestContext(
        thinking=False,
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
