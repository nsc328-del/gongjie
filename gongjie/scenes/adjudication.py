from __future__ import annotations

import json
import re
from typing import Any

from gongjie.ai.providers import ContextMessage, ProviderClient, ProviderError, RequestContext
from gongjie.domain.patches import PatchValidationError, validate_patch


def patch_skip_reason(error: BaseException) -> str:
    """把裁判失败归类为简短、可对外暴露的原因标签，不透传异常细节。"""
    if isinstance(error, PatchValidationError):
        return "validation_error"
    if isinstance(error, ProviderError):
        return "provider_error"
    if isinstance(error, ValueError):
        return "parse_error"
    return "internal_error"


def patch_context(context: dict[str, Any], assistant_content: str, profile: dict[str, Any], *, feedback: str = "") -> RequestContext:
    scene = context["scene"]
    character_names = [item["name"] for item in context.get("room_characters", [context["character"]])]
    latest_user = next((item["content"] for item in reversed(context["messages"]) if item["role"] == "user"), "")
    locations = [item["name"] for item in context["locations"]]
    recent = [item for item in context["messages"] if item["content"]][-4:]
    transcript = "\n".join(f"{item['role']}: {item['content']}" for item in recent)
    system = """你是共享世界的状态裁判，不参与表演。你的任务是判断一轮对话后，哪些硬状态真正改变，并整理此刻的软连续性。
严格区分：说出口的计划、威胁、幻想、试探不是已发生事实。重要事件、玩家承诺与关系变化只有在正文明确发生或表达时才可写入；身体痕迹、衣物、气味、姿态和距离属于软连续性，可以自然概括，不必逐项证明来源。
只返回一个 JSON 对象，不要 Markdown，不要解释。字段必须符合：
{
  "event": null 或 {
    "title": "不超过18字的已发生事件",
    "details": "只写客观发生的事实，不写推测",
    "visibility": "public|private|secret",
    "participants": ["角色名"],
    "witnesses": ["亲眼知情的角色名"],
    "beliefs": {"角色名": "仅当该角色误解时填写其相信的版本"},
    "world_time": "故事内的时间锚点或空字符串，禁止填写现实钟点"
  },
  "scene": {
    "location": "现有地点名或空字符串",
    "world_time": "变化后的时间或空字符串",
    "tension": 0到100,
    "active_threads": ["仍未解决且会继续推动场景的线，最多4条"],
    "physical_state": "角色当前可观察的身体、衣物、姿态与距离等软状态",
    "emotional_state": "当前角色的情绪状态"
  },
  "relationship": {
    "trust_delta": -5到5,
    "desire_delta": -5到5,
    "tension_delta": -8到8,
    "label": "必要时更新，否则空字符串",
    "note": "这一轮留下的关系痕迹，必要时更新，否则空字符串"
  },
  "player": {
    "location": "玩家此刻所在的现有地点名或空字符串",
    "condition": "玩家当前可被外界观察到的整体软状态；没有变化时沿用当前值",
    "visible_traces": ["玩家身上当前可见的身体、衣物、气味等软痕迹，完整列表，最多8条"],
    "commitments": ["玩家对当前角色仍未履行的明确承诺，返回完整列表，最多6条"]
  }
}
若没有值得跨对话保留的事实，event 必须为 null；scene、relationship 与 player 仍按当前局面填写。软状态允许为叙事连续性自然补足、改写或淡去，不需要升级为 event；但不得用软痕迹断言它由某个角色造成、证明玩家去过某处或确认一段未记录的隐秘经历。不要把角色的身体状态写成玩家状态，不要把性格评价当作可见痕迹。player.condition 与 visible_traces 只保留玩家自身能带到下一场的外部状态，不含站在谁身边、与谁相距几步等现场关系；这些属于当前角色 scene.physical_state。换人之后不能沿用“距离上一角色一步”这样的旧现场。提问是否能来、邀请和表示可能在场都不等于已经约定，active_threads 和 relationship.note 也不得偷换为已约定。commitments 只记录玩家亲口作出的明确承诺，角色提出的条件、命令、邀请和期待都不是玩家承诺。relationship 只在本轮互动确实改变关系时调整。

事件精简纪律（非常重要）：
1. 同一连续场景里渐进的细小动作（例如递茶、解发绳、膝行靠近、解开一颗扣子）不要逐轮拆成多个 event。它们属于同一个“相处片段”，只有在你判断它真的会改变角色之后的认知或行为时，才合并写成一条概括性事件。
2. 判断标准：如果另一个角色明天不会因为这件事而改变行动，就不要写进世界。连续亲热中的中间步骤通常不值得成为世界事实，等这段相处真正告一段落或出现明确转折时再写一条。
3. 世界时间只能是“钟响之前、次日清晨、一周后”这样的故事时间锚点；禁止填写 18:43:36、2026-08-13T18:43:36 这类现实钟点和系统时间；拿不准就留空字符串。
"""
    system += """
附加必需契约：顶层 evidence 是对象，以 event、scene.location、scene.world_time、relationship、player.location、player.commitments 为键，每项为 {"role":"user|assistant","quote":"本轮该说话方的逐字原文","kind":"occurred|expressed|intent"}。
证据 quote 必须从下方单列的“本轮玩家原文”或“本轮角色回复”连续复制，禁止省略号、拼接、改写或引用背景旧对话。participants、witnesses、knowledge_sources 都只填写给出的可登记角色名，绝不填写“玩家”“你”或其他称呼。
只有改变了硬状态的字段需要证据。event 只接受已经完成的 occurred；计划、威胁、幻想、条件和否定不能标为 occurred。谈论明日赴约不代表当前时间已到明日，也不代表当前时间发生变化。无实际转场就省略 scene.world_time、scene.location 和 player.location；event.world_time 使用当前世界时间或省略。commitments 的输入与输出均为文字数组，不返回带 id 的对象。如果现有承诺是“明日来见某人”，本轮玩家已经表明按约来到该人面前，就应从待履行列表移除；全部兑现则返回 []，不把已经履行的约定保留下来；同时从 active_threads 中删掉该约定仍待兑现、未到场或爽约后寻找等已经失效的线。尚未兑现的旧承诺逐字沿用，不要改写；兑现或取消须有本轮依据，不能因为本轮没提到就删掉。
新增玩家承诺必须引用玩家本轮明确承诺的原文；不得引用角色的要求。不改变地点、时间、关系和承诺时沿用现状或省略字段。
event 还必须包含 knowledge_sources，对每个参与者、目击者以及当前回复角色，以角色名为键提供 {"role":"user|assistant","quote":"本轮明确让此人亲历或被告知的逐字原文","kind":"witnessed|told"}。不能只凭同场就推定知情，秘密也不能自动共享。私人现场仅当前角色可被登记；同场仅实际成员可被登记。事件名单与来源须一致，不登记不在场者。没有成立的硬变化就 event=null，可以只记录软状态。
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

背景旧对话（仅帮助理解，不可作为本轮证据）：
{transcript}

本轮玩家原文：
{latest_user}

本轮角色回复：
{assistant_content}
"""
    if feedback:
        system += f"\n上次提案没有通过校验：{feedback}。请对照上面的契约重新提取，不能省略真实存在的变化，也不能为通过校验新造证据。"
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
    *, feedback: str = "",
) -> dict[str, Any]:
    reply = await client.response(patch_context(context, assistant_content, profile, feedback=feedback), profile)
    proposal = validate_patch(parse_patch(reply.content), context, assistant_content)
    if proposal.get("event") or any(proposal.get(key) for key in ("scene", "relationship", "player")):
        review = await client.response(RequestContext(
            system="""你是硬状态证据核对员，不续写也不改写提案。核对本轮原文是否真正支持每项硬变化：
事件须已经发生；计划、威胁、猜测、否定、条件、幻想不能变成完成事实。玩家新增承诺必须是玩家明确答应，而非角色要求。也必须检查提案是否遗漏玩家本轮明确兑现或取消的现有承诺：已明确兑现却仍保留在 commitments 或 active_threads 中作为未来待履行事项，同样拒绝。询问“明天能来吗”不是已约定，任何字段都不能如此改写。关系、地点与时间变化必须有对应的实际互动。逐一核对 knowledge_sources：原文是否真的让该人看到或听到该事件，秘密不能仅因同场被共享。引用真实但无法支持结论也必须拒绝。
身体、衣物、气味、姿态等软连续性可以自然变化，不要求证据；但不能在软状态中夹带新物品、完成的调查、秘密因果或其他无来源硬事实。
只返回 {"approved":true或false,"issues":["不通过时逐项指出字段和具体问题，最多4条；通过时为空"]}；任何不确定、遗漏或不受支持的硬变化都返回 false。沿用当前时间、地点和已有状态不是新变化，不要求为它们重新提供证据。""",
            messages=[ContextMessage(role="user", content=json.dumps({
                "current_scene": {**context["scene"], "world_time": context["scene"].get("world_time") or context["world"]["current_time"]},
                "locations": [{"id": item["id"], "name": item["name"]} for item in context["locations"]],
                "player": context.get("player", {}),
                "relationships": context.get("relationships", []),
                "present": [c["name"] for c in context.get("room_characters", [context["character"]])],
                "user": next((m["content"] for m in reversed(context["messages"]) if m["role"] == "user"), ""),
                "assistant": assistant_content,
                "proposal": proposal,
            }, ensure_ascii=False))],
            max_tokens=int(profile.get("max_tokens") or 16384), temperature=0.0, thinking=True, reasoning_effort="low",
        ), profile)
        verdict = parse_patch(review.content)
        issues = verdict.get("issues", [])
        if not isinstance(issues, list) or not all(isinstance(item, str) for item in issues):
            raise PatchValidationError("review_invalid")
        if not set(verdict) <= {"approved", "issues"} or verdict.get("approved") is not True:
            raise PatchValidationError("hard_state_not_supported", issues[:4])
    return proposal
