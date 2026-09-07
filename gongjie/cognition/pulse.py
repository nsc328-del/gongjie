from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from gongjie.ai.providers import ContextMessage, ProviderClient, RequestContext
from gongjie.storage.store import Store
from gongjie.domain.knowledge import player_observation


def _parse_object(text: str) -> dict[str, Any] | None:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean)
    try:
        value = json.loads(clean)
    except json.JSONDecodeError:
        start = clean.find("{")
        if start < 0:
            return None
        try:
            value, _ = json.JSONDecoder().raw_decode(clean[start:])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def character_pulse_context(
    item: dict[str, Any], context: dict[str, Any], profile: dict[str, Any], output_budget: int,
    target_time: str = "",
) -> RequestContext:
    character = item["character"]
    current_player = player_observation(context["player"], [entry["character"]["name"] for entry in context["characters"]])
    can_observe = bool(
        character.get("current_location_id")
        and character["current_location_id"] == current_player.get("location_id")
    )
    player = {
        **(current_player if can_observe else {}),
        "commitments": item.get("commitments") or [],
    }
    payload = {
        "世界": {
            "名称": context["world"]["name"],
            "前提": context["world"]["premise"],
            "规则": context["world"]["rules"],
            "当前时刻": context["world"]["current_time"],
            "用户希望推进到的时刻": target_time or "未指定，由世界自然决定",
        },
        "可选地点": [location["name"] for location in context["locations"]],
        "角色": {
            "姓名": character["name"],
            "身份": character["role"],
            "人格": character["persona"],
            "说话方式": character["speech_style"],
            "欲望指纹": character["desire_profile"],
            "失控方式": character["loss_of_control"],
            "当前地点": character.get("location_name") or "未定",
            "原有表层状态": character.get("state_summary") or character.get("tagline", ""),
            "当前可接触性": (item.get("world_state") or {}).get("availability") or "reachable",
            "上一行动意图": (item.get("world_state") or {}).get("intention", ""),
            "本时段是否已经与玩家互动": bool(
                (item.get("world_state") or {}).get("interacted_since_pulse")
            ),
        },
        "与玩家关系": item.get("relationship") or {},
        "玩家可被观察的状态": player,
        "本次新获得或亲历的事实": item.get("new_events") or [],
        "此前认知": item.get("knowledge") or [],
    }
    system = """你只负责共享世界中一名角色在时间向前一步时的独立行动决定。你不是全知导演，只能使用该角色获得的认知；不知道的事绝不能猜中。玩家当前外部状态只在实际同地时提供；缺失即不可观察，不能据此猜测玩家现在的位置或经历。对自己的承诺只是约定，不证明玩家已到场或已经兑现。
    这次决定是下一时段的意图和局面，不是已经发生的世界事实。角色可以等待、离开、调查、赴约、主动寻找玩家、拒绝见面或继续自己的生活，不要总是原地等待玩家。
    如果用户给出了希望到达的故事时间，优先以它安排下一时段；它只是故事时间锚点，不要求原样复述，也不是现实钟点。
只返回 JSON，不要 Markdown 或解释：
{
  "participation": "active|quiet",
  "active_reason": "new_fact|commitment|matured_intention|visible_change|空字符串",
  "active_source": "触发来源：事实ID、承诺ID、previous_intention、location 或 availability；quiet 时空字符串",
  "intention": "角色私下决定的下一步，供角色自己的后续对话使用，不超过80字",
  "surface_state": "玩家从外部能够感受到的姿态或余波，不泄露秘密，不超过55字",
  "location": "下一时段所在的一个现有地点名",
  "availability": "present|reachable|incoming|unavailable",
  "encounter_hook": "角色主动发出的信号或环境入口，不替玩家写动作，不新增物品、证据或事件，不超过90字"
}
默认必须返回 participation=quiet。只有以下一种来源真实存在并且足以让玩家察觉时才返回 active，并在 active_reason 与 active_source 中准确指出：new_fact=本次新获得或亲历的事实，active_source 必须填该事实的 id；commitment=对方仍待兑现且在目标时段已相关的承诺，active_source 必须填该承诺的 id；matured_intention=上一行动意图在本时段自然成熟，active_source 固定填 previous_intention；visible_change=角色地点或可接触性确实发生结构化变化，active_source 按实际变化填 location 或 availability。普通起床、喝咖啡、整理东西、继续工作不构成 active；不能为了让角色出场而新造调查、线索或巧遇。
participation=active 表示角色在新时段形成了有来源的可见行动或相遇牵引；quiet 表示角色没有受到相关事实影响，也没有值得显露的新变化。quiet 时 active_reason、active_source、intention、surface_state、encounter_hook 均留空，location 和 availability 沿用当前值。
availability 含义：present=已经在玩家眼前；incoming=正在主动靠近或联系玩家；reachable=玩家可以前去寻找；unavailable=暂时无法接触。没有参与上一时段不等于 unavailable，玩家仍可以主动去见一个 quiet 且 reachable 的角色。不要使用数值、系统字段或开发者术语。"""
    return RequestContext(
        system=system,
        messages=[ContextMessage(role="user", content=json.dumps(payload, ensure_ascii=False))],
        max_tokens=min(int(profile.get("max_tokens") or output_budget), output_budget),
        temperature=0.55, thinking=False,
    )


def transition_context(
    context: dict[str, Any], actions: list[dict[str, Any]], profile: dict[str, Any],
    output_budget: int, target_time: str = "",
) -> RequestContext:
    payload = {
        "世界": {
            "名称": context["world"]["name"],
            "前提": context["world"]["premise"],
            "氛围": context["world"]["atmosphere"],
            "规则": context["world"]["rules"],
            "当前时刻": context["world"]["current_time"],
        },
        "玩家当前状态": {key: value for key, value in player_observation(context["player"], [entry["character"]["name"] for entry in context["characters"]]).items() if key != "commitments"},
        "用户希望推进到的时刻": target_time or "未指定，由世界自然决定",
        "角色在下一时段可被玩家察觉的变化": [
            {
                "角色": action["character_name"],
                "表层状态": action["surface_state"],
                "地点": action["location"],
                "相遇线索": action["encounter_hook"],
            }
            for action in actions
        ],
        "刚刚等待结算的公开事实": [
            event for event in context["pending_events"] if event["visibility"] == "public"
        ],
    }
    system = """你负责把共享世界从当前时刻自然推到下一个可游玩的时段。你只看公开局面与角色愿意显露的外部变化，不补写秘密，不创造已经发生的新事件，也不替玩家行动。
只返回 JSON，不要 Markdown 或解释：
{
  "world_time": "比当前时刻自然向后推进的故事时间锚点，如次日清晨、一周后；禁止填写现实钟点",
  "narration": "80到180字的局面播报，把地点、等待、错过或正在靠近的变化组织成一段自然叙事，不逐项点名汇报",
  "player_condition": "时间过去后玩家仍可被外界观察到的整体状态；没有则空字符串",
  "visible_traces": ["时间过去后仍然可见的玩家痕迹，完整列表，最多8条"]
}
    如果用户给出了希望到达的故事时间，优先以它作为时间锚点，但不要求原样复述。时间必须向前，不能换一种说法停在原时刻。player_condition 只能原样保留输入或在已经自然消退时返回空字符串；visible_traces 只能保留输入中的原文条目或删除自然消退的条目，输入为空就返回空列表，不新增痕迹。不能补写玩家在这段时间站了一夜、走了某条路、去了某个地点，也不能用木屑、气味或灰尘新造去向证据。
narration 只能重组输入中已有的地点、外部变化和公开事实；不得新造信件、报纸、钥匙、伤痕、脚印、证据、来客或已完成的调查。不得写“你推开门”“你看见”“你走到”等玩家动作或感知，只描述无需玩家配合也成立的世界局面。"""
    return RequestContext(
        system=system,
        messages=[ContextMessage(role="user", content=json.dumps(payload, ensure_ascii=False))],
        max_tokens=min(int(profile.get("max_tokens") or output_budget), output_budget),
        temperature=0.4, thinking=False,
    )


async def review_transition(
    client: ProviderClient, context: dict[str, Any], actions: list[dict[str, Any]],
    transition: dict[str, Any], profile: dict[str, Any], target_time: str,
) -> str:
    request = transition_context(context, actions, profile, int(profile.get("max_tokens") or 4096), target_time)
    payload = json.loads(request.messages[0].content)
    payload["本次时刻"] = transition.get("world_time")
    payload["待校对的局面"] = transition.get("narration")
    reply = await client.response(RequestContext(
        system="""你是世界局面校对员，只校对草稿，不续写剧情。仅保留输入中成立的公开事实和角色愿意显露的外部变化，可以保留普通光线、海风等气氛。删除新造的到访、离开、调查结果、玩家动作与路线；“昨夜无人经过”“整夜没人来”等否定断言同样需要来源，不能把安静写成全知的历史证明。表层姿态不证明持续了一整夜；当前状态不证明玩家此前的经历。不要出现私人承诺、秘密内容或玩家未明确选择的行为。
只返回 {"narration":"校对后的完整局面正文"}，没有可靠内容则用本次时刻和普通环境气氛简洁呈现。""",
        messages=[ContextMessage(role="user", content=json.dumps(payload, ensure_ascii=False))],
        max_tokens=int(profile.get("max_tokens") or 4096), thinking=True, reasoning_effort="low",
    ), profile)
    value = _parse_object(reply.content)
    if not value or not isinstance(value.get("narration"), str) or not value["narration"].strip():
        raise ValueError("新的局面没有通过校对，请重新推进")
    return value["narration"].strip()


async def _response_object(
    client: ProviderClient,
    contexts: list[RequestContext],
    profile: dict[str, Any],
) -> dict[str, Any] | None:
    for context in contexts:
        reply = await client.response(context, profile)
        value = _parse_object(reply.content)
        if value is not None:
            return value
    return None


def _active_reason_is_grounded(
    reason: str,
    source: str,
    value: dict[str, Any],
    item: dict[str, Any],
    location_by_name: dict[str, str],
    player_location_id: str | None,
) -> bool:
    if reason == "new_fact":
        return source in {
            str(event.get("id")) for event in item.get("new_events") or []
            if event.get("id")
        }
    if reason == "commitment":
        return source in {
            str(commitment.get("id")) for commitment in item.get("commitments") or []
            if commitment.get("id")
        }
    world_state = item.get("world_state") or {}
    if reason == "matured_intention":
        return source == "previous_intention" and bool(
            str(world_state.get("intention") or "").strip()
        )
    if reason != "visible_change":
        return False
    character = item["character"]
    location_name = str(value.get("location") or "").strip()
    proposed_location_id = location_by_name.get(location_name)
    location_changed = bool(
        proposed_location_id
        and proposed_location_id != character.get("current_location_id")
    )
    current_availability = world_state.get("availability") or "reachable"
    proposed_availability = value.get("availability")
    availability_changed = (
        proposed_availability in {"present", "incoming", "unavailable"}
        and proposed_availability != current_availability
        and (
            proposed_availability != "present"
            or proposed_location_id == player_location_id
        )
    )
    return (
        source == "location" and location_changed
    ) or (
        source == "availability" and availability_changed
    )


async def _advance_world_pulse(
    *, store: Store, client: ProviderClient, profile: dict[str, Any], world_id: str,
    target_time: str = "",
) -> dict[str, Any]:
    target_time = str(target_time or "").strip()[:120]
    context = store.cognition_sync_context(world_id)
    location_by_name = {item["name"]: item["id"] for item in context["locations"]}
    output_budget = int(profile.get("max_tokens") or 16384)

    if not context["characters"]:
        raise ValueError("这个世界还没有可以行动的角色")

    async def generate(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        value = await _response_object(
            client,
            [
                character_pulse_context(item, context, profile, output_budget, target_time),
                character_pulse_context(item, context, profile, output_budget, target_time),
            ],
            profile,
        )
        character = item["character"]
        if value is None:
            value = {"participation": "quiet"}
        world_state = item.get("world_state") or {}
        participation = value.get("participation")
        if participation not in {"active", "quiet"}:
            participation = "quiet"
        active_reason = str(value.get("active_reason") or "").strip()
        active_source = str(value.get("active_source") or "").strip()
        if participation == "active" and not _active_reason_is_grounded(
            active_reason,
            active_source,
            value,
            item,
            location_by_name,
            context["player"].get("location_id"),
        ):
            participation = "quiet"
        if participation == "quiet":
            availability = world_state.get("availability") or "reachable"
            if availability not in {
                "present", "reachable", "incoming", "unavailable"
            }:
                availability = "reachable"
            if availability == "present" and (not character.get("current_location_id") or character["current_location_id"] != context["player"].get("location_id")):
                availability = "reachable"
            action = {
                "character_name": character["name"],
                "participation": "quiet",
                "active_reason": "",
                "active_source": "",
                "intention": str(world_state.get("intention") or "").strip(),
                "surface_state": str(
                    world_state.get("surface_state") or character.get("state_summary")
                    or character.get("tagline") or ""
                ).strip(),
                "location": character.get("location_name") or "",
                "location_id": character.get("current_location_id"),
                "availability": availability,
                "encounter_hook": "",
            }
            return character["id"], action
        location_name = str(value.get("location") or character.get("location_name") or "").strip()
        availability = value.get("availability")
        if availability not in {"present", "reachable", "incoming", "unavailable"}:
            availability = "reachable"
        if availability == "present" and (not location_by_name.get(location_name) or location_by_name[location_name] != context["player"].get("location_id")):
            availability = "reachable"
        action = {
            "character_name": character["name"],
            "participation": "active",
            "active_reason": active_reason,
            "active_source": active_source,
            "intention": str(value.get("intention") or "").strip(),
            "surface_state": str(
                value.get("surface_state") or character.get("state_summary")
                or character.get("tagline") or ""
            ).strip(),
            "location": location_name,
            "location_id": location_by_name.get(location_name) or character.get("current_location_id"),
            "availability": availability,
            "encounter_hook": str(value.get("encounter_hook") or "").strip(),
        }
        return character["id"], action

    results = await asyncio.gather(
        *(generate(item) for item in context["characters"]),
        return_exceptions=True,
    )
    failures = [item for item in results if isinstance(item, BaseException)]
    if len(failures) == len(results):
        raise failures[0]
    actions: dict[str, dict[str, Any]] = {}
    for item in results:
        if isinstance(item, BaseException):
            continue
        character_id, action = item
        actions[character_id] = action
    visible_actions = [
        action for action in actions.values()
        if action.get("participation") == "active"
    ]
    transition = await _response_object(
        client,
        [
            transition_context(context, visible_actions, profile, output_budget, target_time),
            transition_context(context, visible_actions, profile, output_budget, target_time),
        ],
        profile,
    )
    if transition is None:
        raise ValueError("世界这一刻没有成功向前，请再试一次")
    observations = player_observation(context["player"], [entry["character"]["name"] for entry in context["characters"]])
    if transition.get("player_condition") not in {None, "", observations.get("condition", "")}:
        raise ValueError("这一刻夹带了未发生的玩家经历，请重新推进")
    if any(trace not in observations.get("visible_traces", []) for trace in transition.get("visible_traces", [])):
        raise ValueError("这一刻新增了没有来源的玩家痕迹，请重新推进")
    transition["narration"] = await review_transition(client, context, visible_actions, transition, profile, target_time)
    result = store.commit_world_pulse(
        world_id,
        [event["id"] for event in context["pending_events"]],
        actions,
        transition,
    )
    return {
        **result,
        "world_time": transition.get("world_time", ""),
        "narration": transition.get("narration", ""),
    }


async def advance_world_pulse(*, store: Store, client: ProviderClient,
                              profile: dict[str, Any], world_id: str,
                              target_time: str = "") -> dict[str, Any]:
    with store.world_operation(world_id):
        return await _advance_world_pulse(store=store, client=client, profile=profile,
                                          world_id=world_id, target_time=target_time)
