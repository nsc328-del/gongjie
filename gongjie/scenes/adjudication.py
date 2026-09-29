from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from gongjie.ai.providers import ContextMessage, ProviderClient, ProviderError, RequestContext
from gongjie.domain.patches import PatchValidationError, validate_patch
from gongjie.domain.transcripts import dialogue_memory, previous_dialogue, turn_user_text
from gongjie.domain.locations import location_patch


def source_catalog(context: dict[str, Any], reply: str) -> dict[str, dict[str, str]]:
    user = turn_user_text(context)
    result = {}
    for role, content in (("user", user), ("assistant", reply)):
        for index, match in enumerate(re.finditer(r"[^\n。！？]+[。！？]?", content)):
            if match.group().strip():
                result[f"{role}_{index}"] = {"role": role, "quote": match.group()}
    # A public action and hidden contents can share one sentence. Offer exact
    # observed fragments as citations without disclosing another user's whisper
    # to the character whose settlement this is.
    seen = {(s['role'],s['quote']) for s in result.values()}
    for views in context.get('observation_sources', {}).values():
        for role, visible in views.items():
            text = {'user':user,'assistant':reply}.get(role,'')
            for quote in visible.split('\n'):
                if quote.strip() and quote in text and (role,quote) not in seen:
                    result[f'observation_{len(result)}'] = {'role':role,'quote':quote}
                    seen.add((role,quote))
    return result


def resolve_sources(proposal: dict[str, Any], sources: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Decode the single model format into the domain's normalized proposal."""
    if not isinstance(proposal, dict) or set(proposal) - {"event", "scene", "relationship", "player"}:
        raise PatchValidationError("proposal_fields", ["顶层只允许 event、scene、relationship、player；证据写在对应变化内部。"])
    result = deepcopy(proposal)
    evidence = {}

    def resolve(value: Any) -> dict[str, str]:
        if not isinstance(value, dict) or set(value) != {"source_id", "kind"}:
            raise PatchValidationError("source_reference_required", ["证据只包含 source_id 和 kind，不能抄写 role 或 quote。"])
        source_id = value["source_id"]
        if not isinstance(source_id, str) or source_id not in sources:
            raise PatchValidationError("source_reference_unknown", ["请选择本轮来源表中存在的 source_id。"])
        return {**sources[source_id], "kind": value["kind"]}

    for section in ("event", "relationship"):
        value = result.get(section)
        if isinstance(value, dict) and "evidence" in value:
            evidence[section] = resolve(value.pop("evidence"))
    event = result.get("event")
    if isinstance(event, dict):
        if "world_time" in event:
            raise PatchValidationError("event_time_managed", ["event 不填写 world_time，事件继承本轮有效时钟。"])
        knowledge = event.get("knowledge_sources", {})
        if not isinstance(knowledge, dict):
            raise PatchValidationError("knowledge_sources_mismatch")
        event["knowledge_sources"] = {key: resolve(value) for key, value in knowledge.items()}
    for section, field in (("scene", "world_time"), ("player", "commitments")):
        value = result.get(section)
        if not isinstance(value, dict):
            continue  # The domain validator reports invalid section types.
        if {"location", "location_id"} & set(value):
            raise PatchValidationError("location_managed", [f"{section} 不填写地点；转场只由已核对的场景方案生成。"])
        if field in value:
            change = value[field]
            if not isinstance(change, dict) or set(change) != {"value", "source_id", "kind"}:
                raise PatchValidationError("cited_change_required", [f"{section}.{field} 必须包含 value、source_id、kind。"])
            evidence[f"{section}.{field}"] = resolve({key:change[key] for key in ("source_id", "kind")})
            value[field] = change["value"]
    result["evidence"] = evidence
    return result


def patch_skip_reason(error: BaseException) -> str:
    """把裁判失败归类为简短、可对外暴露的原因标签，不透传异常细节。"""
    if isinstance(error, PatchValidationError):
        return "validation_error"
    if isinstance(error, ProviderError):
        return "provider_error"
    if isinstance(error, ValueError):
        return "parse_error"
    return "internal_error"


def patch_context(
    context: dict[str, Any], assistant_content: str, profile: dict[str, Any], *,
    feedback: str = "", repair_proposal: dict[str, Any] | None = None,
) -> RequestContext:
    scene = context["scene"]
    character_names = [item["name"] for item in context.get("room_characters", [context["character"]])]
    locations = [item["name"] for item in context["locations"]]
    transcript = json.dumps(dialogue_memory(previous_dialogue(context, 4)), ensure_ascii=False)
    system = """你是共享世界的状态裁判，不参与表演。只提取本轮实际变化，不续写剧情。
只返回 JSON 变化对象，不要 Markdown 或解释。未改变的字段省略；没有变化返回 {}。以下是唯一格式，字段按实际需要选用，不照抄整份快照：
{
  "event": {
    "title": "已发生事件，不超过18字",
    "details": "客观发生了什么；转述须保留说话人及未证实归属",
    "visibility": "public|private|secret",
    "participants": ["角色名"],
    "witnesses": ["角色名"],
    "beliefs": {"角色名": "仅当此人误解时填写其相信的版本"},
    "knowledge_sources": {"角色名": {"source_id": "本轮来源编号", "kind": "witnessed|told"}},
    "evidence": {"source_id": "本轮来源编号", "kind": "occurred"}
  },
  "relationship": {
    "trust_delta": -5到5,
    "desire_delta": -5到5,
    "tension_delta": -8到8,
    "label": "改变后的关系标签",
    "note": "本轮改变的关系痕迹",
    "evidence": {"source_id": "本轮来源编号", "kind": "occurred|expressed"}
  },
  "scene": {
    "world_time": {"value": "实际改变后的故事时间", "source_id": "本轮来源编号", "kind": "occurred"},
    "tension": 0到100,
    "active_threads": ["此刻仍有持续意义的具体处境，最多4条"],
    "physical_state": "角色的身体、衣物、姿态、距离等软连续性",
    "emotional_state": "当前角色的情绪"
  },
  "player": {
    "commitments": {"value": ["玩家对当前角色仍未履行的明确承诺，最多6条"], "source_id": "本轮来源编号", "kind": "occurred|expressed"},
    "condition": "玩家自身外部软状态",
    "visible_traces": ["玩家可携带到下一场的身体、衣物、气味痕迹，最多8条"]
  }
}
证据紧邻变化：event.evidence 证明事件，relationship.evidence 证明关系变化；scene.world_time 和 player.commitments 把 value 与来源放在同一对象。不得输出顶层 evidence、自抄 role/quote 或使用旧对话作本轮来源。所有 source_id 必须从本轮来源表选择。引用必须对应变化的行为人；玩家的提议不能证明角色随后答应、写字或安排，角色的发言不能代替玩家承诺。knowledge_sources 只证明该人获得的片段，不必与 event.evidence 相同，也不承包整个事件。
本轮来源表中的 user_N 与 assistant_N 分别按原文顺序列出玩家输入和角色回复；role 表明说话方，quote 是逐字内容，合起来就是本轮完整对话。observation_N 是同一原文中额外可引用的观察片段，不是后来发生的新发言；各人的可见编号限制仍须遵守。
relationship 的数值、label、note 都属于关系变化。即使三个 delta 均为0，只要改了 label 或 note，也必须填写 relationship.evidence。普通问候不必调整关系；没有实质变化就省略 relationship。
重要事件必须已经发生。计划、威胁、猜测、条件、否定和幻想不能标为 occurred；转述只证明转述发生；event、relationship.note、active_threads 和软状态都保留说话人及未确认归属。“玩家说听甲说X”不能变成“甲已做X”或“X属实”。没有值得跨对话保留的事件就省略 event 或填 null。递茶、解衣等连续过程里的细小动作保留为软连续性，不逐步拆成事件；真正改变后来认知或行为的转折才记录。
knowledge_sources 必须分别覆盖 participants、witnesses 及当前回复角色；名单只用本轮可登记角色，不含玩家或不在场者。同场不自动知情，各人只能引用提供给他的可见来源编号；只获得局部观察就只记该片段，不补全秘密。beliefs 仅表示本人误解，不改写客观事件。
地点与事件时间由程序维护：不提交 scene/player.location 或 location_id，不提交 event.world_time。实际抵达由已核对的场景方案自动写入；仅提及、询问和预约不等于抵达或通行。scene.world_time 仅在本轮确实发生时间变化时填写，不把“明天见”写成现在已到明天，禁止现实日期钟点。事件继承该轮有效时钟。
player.commitments 只在列表变化时提交，value 为完整的剩余承诺列表；全部兑现或取消时 value=[]。新增必须引用 user 的明确承诺，角色要求、邀请和玩家询问不构成承诺。只保留玩家明确接受的对象、时间和条件；可结合本轮上下文理解“按你说的时间”，但对方单独提出的安排不自动加入，也不替另一方确认赴约。保留项逐字沿用；兑现、取消须有本轮依据，不能因本轮没提就删掉，也不能将已按约到场的承诺继续保留为未来待办。继续写没有新玩家输入，不能用旧台词补承诺或同意。
active_threads 只保留持续的具体处境。普通问话、等待来意或表态应移除，不转存到 relationship.note 作为待回答义务；如实记下双方刚才的互动不等于强迫玩家回答；背后的真实处境如“授权书尚未签署”可保留。按当前时间和已回应内容清理过时话题，不等于完成事件，也不取消真实承诺。邀请与谈论时间不能在这些字段中变成已约定。
身体、衣物、气味、姿态与情绪可自然补足、改写或淡去，不要求硬证据；但不能借软状态断言隐秘经历、伤痕由谁造成、玩家去过哪里，或夹带新调查、特殊物品、权限与秘密。普通桌椅纸笔可沿用或补足，不承担事实证明。玩家状态只写自身可携带痕迹；与某角色相距几步属于该角色 scene.physical_state，不能带进下一现场。scene.tension 是氛围绝对值，relationship.tension_delta 是关系增量，两者无需相等或换算。
当前场景、玩家状态、已有承诺和关系是权威基线，仅核对本轮增量；旧 rejected 历史不能撤销已确认字段，也不能当作已完成动作、承诺或外界事实。历史可以帮助理解话题和普通物品，不能代替新增硬变化的本轮证据。
本轮已识别的地点名称仅表示玩家实际提及，名称认知随成功结算原子保存，不证明转述的路线、环境、限制或权限属实。无需为登记名称另造 event 或 relationship；原文另有真实变化仍须正常提取。
"""
    player_input = {
        **(context.get("player") or {}),
        "commitments": [item["text"] for item in (context.get("player") or {}).get("commitments", [])],
    }
    user = f"""角色：{context['character']['name']}
本轮可登记为知情者的角色名（玩家不在此列表）：{json.dumps(character_names, ensure_ascii=False)}
当前世界时间：{scene.get("world_time") or context["world"]["current_time"]}
现有地点：{json.dumps(locations, ensure_ascii=False)}
当前场景：{json.dumps(scene, ensure_ascii=False)}
玩家当前状态：{json.dumps(player_input, ensure_ascii=False)}
当前角色对玩家的关系基线：{json.dumps([r for r in context.get("relationships", []) if r.get("to_user")], ensure_ascii=False)}
本轮已核对的场景方案：{json.dumps(context.get("scene_plan"), ensure_ascii=False)}

背景旧对话（仅帮助理解，不可作为本轮证据）：
{transcript}

本轮来源表：
{json.dumps(source_catalog(context, assistant_content), ensure_ascii=False)}
"""
    if context.get('room'):
        sources = source_catalog(context,assistant_content)
        allowed = {person['name']:[sid for sid,source in sources.items()
                   if source['quote'] in context.get('observation_sources',{}).get(person['id'],{}).get(source['role'],'')]
                   for person in context['room_characters']}
        user += '\n各人在这轮实际可见的来源编号（knowledge_sources只从对应名单选择，未看到相关事件的人不登记）：\n' + json.dumps(allowed,ensure_ascii=False)
    if context.get('location_mentions'):
        user += "\n本轮识别的地点名称及玩家实际告知原文（待提交，不是事实判决）：\n" + json.dumps([
            {key: mention.get(key) for key in ('id', 'name', 'quote', 'role')}
            for mention in context['location_mentions']
        ], ensure_ascii=False)
    if feedback:
        system += f"\n上次提案没有通过校验：{feedback}。请对照上面的契约重新提取，不能省略真实存在的变化，也不能为通过校验新造证据。"
    if repair_proposal is not None:
        system += "\n本次是修复上一份提案，不是从头重新提取。逐条处理给出的具体问题；正确且有原文支持的字段原样保留，只修改受问题影响的字段，不做无关改写、不额外创造事件，也不为通过校验删掉真实变化。删除不成立字段不代表其余字段已通过。仍返回完整的变化对象，所有 evidence 和 knowledge_sources 继续使用本轮来源表中的 source_id；修复后会重新执行全部校验。"
        user += "\n待修复的原始提案与具体问题：\n" + json.dumps({
            "proposal": repair_proposal, "issues": feedback,
        }, ensure_ascii=False)
    return RequestContext(
        system=system,
        messages=[ContextMessage(role="user", content=user)],
        max_tokens=int(profile.get("max_tokens") or 16384),
        temperature=0.1, thinking=False,
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
    *, feedback: str = "", repair_proposal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reply = await client.response(patch_context(
        context, assistant_content, profile, feedback=feedback, repair_proposal=repair_proposal,
    ), profile)
    raw_proposal = parse_patch(reply.content)
    try:
        return await _validate_extracted_patch(client, context, assistant_content, profile, deepcopy(raw_proposal))
    except (PatchValidationError, ValueError) as error:
        if isinstance(error, PatchValidationError):
            for path, wire in (("event", "event.evidence"), ("relationship", "relationship.evidence"),
                               ("scene.world_time", "scene.world_time 的 source_id/kind"),
                               ("player.commitments", "player.commitments 的 source_id/kind")):
                error.issues = [issue.replace(f'evidence["{path}"]', wire).replace(f'evidence.{path}', wire) for issue in error.issues]
        error.repair_proposal = deepcopy(raw_proposal)
        raise


async def _validate_extracted_patch(client, context, assistant_content, profile, raw_proposal):
    proposal = validate_patch(location_patch(resolve_sources(raw_proposal, source_catalog(context, assistant_content)), context), context, assistant_content)
    if proposal.get("event") or any(proposal.get(key) for key in ("scene", "relationship", "player")):
        plan = context.get('scene_plan') or {}
        admitted = {
            'purpose': plan.get('purpose'), 'target_id': (plan.get('target') or {}).get('id'),
            'target_name': (plan.get('target') or {}).get('name'), 'resolution': plan.get('resolution'),
        } if plan.get('resolution') else None
        review = await client.response(RequestContext(
            system="""你是硬状态证据核对员，不续写也不改写提案。核对本轮原文是否真正支持每项硬变化：
角色声称的情报不证明情报为真；可以记录“某人声称某事”，不得去掉归属后将其内容确认为外界事实。
事件须已经发生；计划、威胁、猜测、否定、条件、幻想不能变成完成事实。玩家新增承诺必须是玩家明确答应，而非角色要求。也必须检查提案是否遗漏玩家本轮明确兑现或取消的现有承诺：已明确兑现却仍保留在 commitments 或 active_threads 中作为未来待履行事项，同样拒绝。询问“明天能来吗”不是已约定，任何字段都不能如此改写。关系、地点与时间变化必须有对应的实际互动。逐一核对 knowledge_sources：原文是否真的让该人看到或听到该事件，秘密不能仅因同场被共享。引用真实但无法支持结论也必须拒绝。
普通问话、等待玩家说明来意或表态不进入 active_threads，也不转存到 relationship.note 作为持续要求。已有此类记录本轮应移除，即使问题未回答；若背后有仍有效的具体处境，只保留处境本身，例如“授权书尚未签署”，不写“等待玩家回答”。这不代表玩家接受条件或问题已经解决，不删除仍有效的明确承诺。
active_threads 是当前仍有推进价值的话题，不是必须逐项兑现的任务账本。删除已过时、已回答、随世界时刻不再适用的等待或话题，不等于宣告某件事已经完成，不需要本轮额外的完成证据。尤其世界时间已经在酒会之后时，可以移除“明早酒会准备”，但不能据此新增准备完成的事件。仍有效的玩家承诺仍按 commitments 的明确兑现规则核对。
身体、衣物、气味、姿态等软连续性可以自然变化，不要求证据；但不能在软状态中夹带新物品、完成的调查、秘密因果或其他无来源硬事实。
历史中 settlement_outcome=rejected 的正文只保留交谈，不证明其动作结果、承诺或外界情报；不能借新一轮重复提及将旧的未确认主张提升为事实。
current_scene、player 和 relationships 是已确认的权威基线，审查仅针对本轮新增或改变的内容。禁止因为 rejected 历史提到同一承诺或本轮没再次承诺，就质疑基线中的已有承诺、要求重证或删除；旧拒绝决定不能撤销已确认字段。validated_geography 中的地理准入和 resolution 已独立核对，沿用对应转场不是无来源的新地点；无需再次证明该地点原先就在世界书中。普通桌椅、纸笔、窗户等不承担特殊证据作用的环境物品属于可补充细节，不能仅因未登记或曾出现在拒绝段落中就否定本轮的普通使用；特殊记录、调查结果、秘密与通行权限仍需真实来源。
基线与新增必须分开：previous_dialogue 是已保存的本现场历史，允许沿用其中已经出现的普通场景物品与软连续性（例如开场已有的授权书）；沿用不等于本轮新增事件，历史不能代替新硬变化的本轮证据。历史中的推测、台词主张、计划仍不能提升为事实。不要因本轮没再次完整描述旧物品就判为凭空新增。scene.tension 是现场氛围的绝对值，relationship.tension_delta 是关系变化的增量，两者没有相等或换算要求。只依据给出的契约核对，不创造新的字段约束。
validated_geography 是程序接受的本轮地理核对结果，不属于待审 proposal。它允许普通新地点登记，不要求再创造地点形成的事件；locations 已合并该暂存目的地，不能将它误认为重复创建。这里只核对 proposal 其余增量与原文的支持关系，不撤销或重新投票地理准入结论；地点不授予额外权限、历史或秘密。
只返回 {"approved":true或false,"issues":["不通过时逐项指出字段和具体问题，最多4条；通过时为空"]}；任何不确定、遗漏或不受支持的硬变化都返回 false。沿用当前时间、地点和已有状态不是新变化，不要求为它们重新提供证据。""",
            messages=[ContextMessage(role="user", content=json.dumps({
                "previous_dialogue": dialogue_memory(previous_dialogue(context, 8)),
                "current_scene": {**context["scene"], "world_time": context["scene"].get("world_time") or context["world"]["current_time"]},
                "locations": [{"id": item["id"], "name": item["name"]} for item in context["locations"]],
                "player": context.get("player", {}),
                "relationships": context.get("relationships", []),
                "present": [c["name"] for c in context.get("room_characters", [context["character"]])],
                "user": turn_user_text(context),
                "assistant": assistant_content,
                "proposal": proposal,
                "validated_geography": admitted,
            }, ensure_ascii=False))],
            max_tokens=max(int(profile.get("max_tokens") or 16384), 8192), temperature=0.0, thinking=True, reasoning_effort="low",
        ), profile)
        verdict = parse_patch(review.content)
        issues = verdict.get("issues")
        if (set(verdict) != {"approved", "issues"}
                or type(verdict.get("approved")) is not bool
                or not isinstance(issues, list)
                or not all(isinstance(item, str) for item in issues)):
            raise PatchValidationError("review_invalid")
        if not verdict["approved"] or issues:
            raise PatchValidationError("hard_state_not_supported", issues)
    return proposal
