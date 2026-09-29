from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from gongjie.ai.providers import ContextMessage, ProviderClient, RequestContext
from gongjie.storage.store import Store
from gongjie.domain.knowledge import player_observation
from gongjie.domain.locations import known_locations, location_view, project_location_fields


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


def _pulse_locations(item: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    character = item["character"]
    locations = item.get("locations")
    if locations is None:
        locations = known_locations(context["locations"], character["id"])
    return [place for place in locations if (
        place["id"] == character.get("current_location_id")
        or location_view(place)["route_known"]
    )]


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
        **(project_location_fields(current_player, item.get('locations') or []) if can_observe else {}),
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
        "可选地点": [location["name"] for location in _pulse_locations(item, context)],
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
    给出的目标时刻已经确定，必须按那个时刻安排状态；若自己的约定已经到点，应呈现可开始赴约的准备或等待，不再当成明日之事。不得因此推定玩家已经到场。
    认知中只是听到名字、尚未明确去向的地方不在可选地点中，不能直接移动过去；可以保留打听方位的意图。location 只能选择输入目录或保持当前地点，不用听闻补出路线。
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
  "player_condition_action": "keep 或 clear；原有整体状态仍在则 keep，已自然消退或原来为空则 clear",
  "retained_trace_indices": [0]
}
    给出的目标时刻已经确定，正文与 world_time 必须使用这个时刻，不提前也不延后。时间必须向前，不能换一种说法停在原时刻。player_condition_action 只能选 keep 或 clear，不输出状态文本。retained_trace_indices 只能填写“玩家当前状态.visible_traces”中仍可见的条目编号（从0开始），已自然消退的条目不选；没有痕迹则必须返回 []，示例中的 [0] 仅在第0项确实存在且应保留时使用，不得填写文字或新增编号。不能补写玩家在这段时间站了一夜、走了某条路、去了某个地点，也不能用木屑、气味或灰尘新造去向证据。
narration 只能重组输入中已有的地点、外部变化和公开事实；不得新造信件、报纸、钥匙、伤痕、脚印、证据、来客或已完成的调查。不得写“你推开门”“你看见”“你走到”等玩家动作或感知，只描述无需玩家配合也成立的世界局面。"""
    return RequestContext(
        system=system,
        messages=[ContextMessage(role="user", content=json.dumps(payload, ensure_ascii=False))],
        max_tokens=min(int(profile.get("max_tokens") or output_budget), output_budget),
        temperature=0.4, thinking=False,
    )


def resolve_transition_state(context: dict[str, Any], transition: dict[str, Any]) -> dict[str, Any]:
    observations = player_observation(
        context["player"], [entry["character"]["name"] for entry in context["characters"]]
    )
    action = transition.get("player_condition_action")
    if action not in ("keep", "clear"):
        raise ValueError("这一刻的状态选择无效，请重新推进")
    traces = observations.get("visible_traces", [])
    indices = transition.get("retained_trace_indices")
    if not isinstance(indices, list) or any(
        type(index) is not int or not 0 <= index < len(traces) for index in indices
    ) or len(set(indices)) != len(indices):
        raise ValueError("这一刻的玩家痕迹选择无效，请重新推进")
    # 模型只能选择已有状态；存储值始终来自本次认知投影，不使用生成文本。
    return {
        "world_time": transition.get("world_time"),
        "narration": transition.get("narration"),
        "player_condition": observations.get("condition", "") if action == "keep" else "",
        "visible_traces": [trace for index, trace in enumerate(traces) if index in indices],
    }


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
        proposed_availability in {"present", "reachable", "incoming", "unavailable"}
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


async def appointment_time(store: Store, client: ProviderClient, context: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    promises = context["player"].get("commitments") or []
    histories = []
    # Only the clock planner sees these private excerpts. They never enter
    # another character's request or the public narration.
    with store.read() as connection:
        for character_id in dict.fromkeys(p.get("character_id") for p in promises):
            if not character_id:
                continue
            rows = connection.execute("""SELECT m.role,m.content,j.context,t.decision
                FROM messages m JOIN conversations c ON c.id=m.conversation_id
                LEFT JOIN settlement_jobs j ON j.source_id=m.id
                LEFT JOIN turn_settlements t ON t.source_message_id=m.id
                WHERE c.world_id=? AND c.character_id=? AND m.status='complete'
                ORDER BY m.sequence DESC LIMIT 24""", (context["world"]["id"], character_id)).fetchall()
            histories.append({"character_id": character_id, "history": [
                {"role": r["role"], "content": r["content"],
                 "recorded_time": (json.loads(r["context"] or "{}").get("world") or {}).get("current_time"),
                 "decision": json.loads(r["decision"] or "{}")} for r in reversed(rows)]})
    request = RequestContext(system="""你是世界时间规划员，只决定下一个故事时刻，不写剧情、不替玩家行动。
commitments 是已确认且仍有效的承诺，优先于历史中其他状态提案的拒绝标记；不能因同段的其他变化被拒绝而否定已有承诺。若当前是前一日夜间，承诺是明早第七声钟，就推进到次日早晨第七声钟，绝不能输出夜钟初响后这种微小推进。
已确认参与某项工作的承诺，其对应对话中角色给出的具体集合时间可以补足这项工作的时间安排；不要求玩家再次逐字重复日期。不要把“已答应参与任务后安排明早集合”误判成无关的单方面邀请。只补足现有承诺的时间，不新增承诺。
已有承诺优先：从明确约定中选距离当前最近、尚未错过的开始时刻，直接推进到能开始赴约的时刻，而不是只过一会儿再次提醒准时。用相关历史补足承诺摘要遗漏的日期和钟次；历史的明天相对 recorded_time 成立，不相对当前时间重新顺延。rejected 的状态不能作为约定已成立的依据，角色单方面邀请也不是承诺。
若约定已经到点或过期，不自动改约到明天，不宣布完成；自然推进到当前相邻的可行动时刻。长期目标或时间不明确的承诺不强行安排具体期限。没有明确赴约时间则选择下一段有意义的自然时段。时间必须向前，不能跳过更近的明确约定。遵守世界钟历。
只返回 {"world_time":"明确且完整的故事时间锚点","ready_commitment_ids":["在该时刻已经可以开始赴约的现有承诺id"],"appointment_times":{"承诺id":"该承诺自身的明确约定时间，未知则空字符串"}}。尚未到时、已经过期或没有明确时间的承诺不选；可根据历史识别参与同一约定的承诺。编号必须来自输入。不得返回地点、秘密、承诺正文或解释。""",
        messages=[ContextMessage(role="user", content=json.dumps({"world": {k:context["world"][k] for k in ("current_time", "rules")}, "commitments": promises, "history": histories}, ensure_ascii=False))],
        max_tokens=8192, temperature=0, thinking=True, reasoning_effort="low")
    result = await _response_object(client, [request, request], profile)
    value = result.get("world_time") if result else None
    if not isinstance(value, str) or not value.strip() or value.strip() == context["world"]["current_time"]:
        raise ValueError("未能确定下一段世界时间，请再试一次")
    ready = result.get("ready_commitment_ids", [])
    valid = {p["id"] for p in promises}
    if not isinstance(ready, list) or any(not isinstance(i, str) or i not in valid for i in ready):
        raise ValueError("赴约时间规划无效，请再试一次")
    times = result.get("appointment_times", {})
    if not isinstance(times, dict) or any(k not in valid or not isinstance(v, str) or len(v)>120 for k,v in times.items()):
        raise ValueError("约定时间预告无效")
    return {"world_time": value.strip()[:120], "ready_commitment_ids": list(dict.fromkeys(ready)), "appointment_times": times}


async def _advance_world_pulse(
    *, store: Store, client: ProviderClient, profile: dict[str, Any], world_id: str,
    target_time: str = "", clock: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target_time = str(target_time or "").strip()[:120]
    context = store.cognition_sync_context(world_id)
    clock = clock or (await appointment_time(store, client, context, profile) if not target_time and context["player"].get("commitments") else {"world_time": target_time, "ready_commitment_ids": []})
    target_time = clock["world_time"]
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
        location_by_name = {p['name']: p['id'] for p in _pulse_locations(item, context)}
        if value is None:
            value = {"participation": "quiet"}
        world_state = item.get("world_state") or {}
        participation = value.get("participation")
        if participation not in {"active", "quiet"}:
            participation = "quiet"
        active_reason = str(value.get("active_reason") or "").strip()
        active_source = str(value.get("active_source") or "").strip()
        location_name = str(value.get("location") or character.get("location_name") or "").strip()
        if (
            participation == "active"
            and location_name not in location_by_name
            and location_name != (character.get("location_name") or "")
        ):
            participation = "quiet"
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
    if target_time:
        transition["world_time"] = target_time
    transition = resolve_transition_state(context, transition)
    transition["narration"] = await review_transition(client, context, visible_actions, transition, profile, target_time)
    transition["ready_commitment_ids"] = clock["ready_commitment_ids"]
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
                              target_time: str = "", preview_id: str | None = None) -> dict[str, Any]:
    with store.world_operation(world_id):
        clock = store.time_preview(world_id) if preview_id else None
        if preview_id and (not clock or clock["id"] != preview_id):
            raise ValueError("时间预告已过期，请刷新后再推进")
        return await _advance_world_pulse(store=store, client=client, profile=profile,
                                          world_id=world_id, target_time=target_time, clock=clock)


_preview_locks = {}

async def prepare_time_preview(store, client, profile, world_id):
    from uuid import uuid4
    key=(str(store.path),world_id)
    lock=_preview_locks.setdefault(key, asyncio.Lock())
    async with lock:
        existing=store.time_preview(world_id)
        if existing: return existing
        if world_id in store._active_worlds or store.pending_settlements(world_id):
            raise ValueError("请等当前回合完成后规划下一时刻")
        stamp=store.preview_fingerprint(world_id)
        context=store.cognition_sync_context(world_id)
        value=await appointment_time(store,client,context,profile)
        value["id"]="preview_"+uuid4().hex
        return store.save_time_preview(world_id,stamp,value)
