from __future__ import annotations

import json

from gongjie.ai.providers import ContextMessage, RequestContext
from gongjie.domain.locations import location_view
from gongjie.domain.transcripts import dialogue_memory, previous_dialogue, turn_user_text


def scene_payload(context: dict) -> dict:
    return {
        'world': {k: context['world'][k] for k in ('name', 'premise', 'rules', 'current_time')},
        'character': {k: context['character'].get(k, '') for k in ('name', 'role', 'persona')},
        'scene': context['scene'], 'player': context.get('player', {}),
        'known_locations': [location_view(p) for p in context['locations']],
        'world_region_id': context['world']['id'],
        'known_facts': context.get('public_events', [])[-12:] + context.get('knowledge', [])[-12:],
        'previous_dialogue': dialogue_memory(previous_dialogue(context, 8)),
        'user': turn_user_text(context),
    }


def planning_request(context: dict, feedback: str = '') -> RequestContext:
    return RequestContext(system='''你是场景规划员。只规划本轮地理边界，不写正文、不裁决事件、不替玩家选择。
先区分三种情况：stay=原地点内的小范围动作/普通相处（窗边、书架旁、读台前不建地点）；mention=仅谈起别处，尚无确认需要；resolve=玩家本轮问路、约在某处见面或明确准备前往，需要确认目的地。
结合近期对话理解“走吧”“带路”等指代，不仅匹配词语。当前已确认场景和承诺优先；rejected 历史只可帮助理解玩家指的是哪个提议，不证明已抵达、调查完成或获准通行。本轮为空表示继续写，只能 stay/mention，不能补出玩家出发决定。
现有地点及别名复用 ID；普通局部环境允许补充，世界书没有穷举不构成拒绝理由。新地点优先归属一个已知地点或区域；相关区域尚未登记时，可用 world_region_id 作为世界根区域，并按世界背景描述具体方位，不能随意挂靠另一间无关房屋。只补地理与普通外观，不附带秘密、历史、调查材料、特殊物品、核心入口、权限或改变世界规则。无法确定它属于这个世界时留在原场景沟通。
当前角色只可使用提供的认知；仅听说和亲历不同。既有明确限制仍有效，不把玩家要求当成成功通行。不因本轮没有复述旧承诺而取消它。
严格只返回以下一种 JSON：
{"mode":"stay"} 或 {"mode":"mention"} 或
{"mode":"resolve","purpose":"information|appointment|travel","source_quote":"本轮玩家逐字原文","target_id":"现有ID","new_location":null} 或
{"mode":"resolve","purpose":"information|appointment|travel","source_quote":"本轮玩家逐字原文","target_id":null,"new_location":{"name":"地点名","parent_id":"已有地点ID","description":"普通空间与外观，不超过120字"}}
询问与未来约定分别使用 information/appointment；只有玩家本轮实际打算出发才用 travel。方案不是已发生事实。
''' + (f'\n上一方案的问题：{feedback}。只修正冲突；无法确定时保留原现场。' if feedback else ''),
        messages=[ContextMessage(role='user', content=json.dumps(scene_payload(context), ensure_ascii=False))],
        max_tokens=1200, temperature=0, thinking=False)


def plan_review_request(context: dict, plan: dict) -> RequestContext:
    return RequestContext(system='''你是地点设定核对员。核对 scene_plan 的用途、玩家来源、地理归属和世界边界。
普通地点允许按世界背景补充；“原来没有登记/没有逐字证据证明此地存在”不是拒绝理由。复用已有地点时检查是否同一处而非仅名字相似。新地点不得附送禁区通行权、暗道捷径、秘密、历史、关键物品、完成的调查或违背世界规则的设施。玩家问路/约定不授权抵达。依据当前玩家发言和已确认状态理解简短出发话语，旧拒绝正文不证明行动已经完成。
沿用已确认承诺和世界字段不需要重新证明，也不得因旧 rejected 台词否定它们。已知普通环境内的窗边/书架旁不应独立建点。
只返回 {"approved":true或false,"issues":["具体冲突，最多3条"]}。没有具体冲突则通过。''',
        messages=[ContextMessage(role='user', content=json.dumps({**scene_payload(context), 'scene_plan': plan}, ensure_ascii=False))],
        max_tokens=8192, temperature=0, thinking=True, reasoning_effort='low')


def plan_instructions(context: dict) -> str:
    plan = context.get('scene_plan')
    if not plan:
        return ''
    return '\n【本轮场景方案，仅供内部使用】\n' + json.dumps(plan, ensure_ascii=False) + '''
本方案只准入地理，不证明抵达、通行权、秘密或调查结果。stay/mention 保持当前地点，允许窗边等内部移动和谈论别处；resolve 只围绕指定目的地展开。information/appointment 只说明路线或约定，玩家仍在原地。travel 可以承接玩家已经给出的出发选择，但不能添加额外目标、绕过限制或自动完成任务；若路程需要明显跨时，停在出发准备，交由世界推进处理。
新地点普通环境可以自然描写；普通家具/日用物品不承担调查证据作用。重要记录、秘密内容与通行权限仍须已有来源。已确认的当前位置和旧承诺优先于拒绝的历史段落；用本轮实际行动重新承接目的地，不重复宣布旧的未确认调查为真。不得输出方案或校验用语。
'''


def reply_review_request(context: dict, reply: str) -> RequestContext:
    return RequestContext(system='''你是转场正文核对员。核对待发布正文、scene_plan 的用途与玩家授权、已确认状态及角色认知是否一致。
先检查计划是否理解了玩家本轮的指代、询问、预约或实际出发意愿：source_quote 必须支持 purpose 和所指 target；有原文不代表引用支持结论。复用既有地点不重证它已经存在，不能把重名、仅听闻的称呼误作另一个地点，不能把问路或未来约定当成现在出发。即使正文跟随了方案，方案误解玩家也必须拒绝；正文可以纠正为留在原地沟通，不能依错误用途移动玩家。
地点方案允许其中的新地点及普通空间外观存在，不要求证明它原本收录在世界书中。它不授予特殊物品、调查结论、秘密或通行权限。普通桌椅、窗户、纸笔和姿态可自由补足，只有承担关键证据或特殊能力的物品才需硬来源。拒绝历史不证明旧转场和调查已发生，正文应从当前已确认地点承接玩家本轮出发。当前已确认承诺不因历史拒绝而失效。
不得擅自替玩家作新选择，不得擅自转到第二个未准入地点或以赶路偷偷跨越时段。information/appointment 只能介绍/约定，不能写玩家抵达；travel 可以实际移动，也可以停在门前或准备阶段。角色正常拒绝/受阻不是校对失败，但不能同时宣告成功进入。
confirmed 表示正文确认或实际使用了准入地点，不是只声称传闻、否定其存在或未回答；arrived 只在玩家与当前现场实际到达目标时 true。已经来到目标入口/门前也属于到达该地点，不表示进入上锁内门或获得核心权限。若正文离开原址却停在另一个未规划的途中地点，应拒绝并要求收在原址准备出发或完成本次合理抵达，不能返回 approved=true,arrived=false 让现场仍留在原址。
reply_sources 按原文顺序包含整段待发布正文，每个编号对应一个非空段落。通读全部段落，再选择能够支持介绍/抵达的 source_id；不得自抄或改写引用。确认地点不是证明地点内的传闻为真。
只返回 {"approved":true或false,"issues":["具体不一致，最多3条"],"resolution":{"confirmed":true或false,"arrived":true或false,"source_id":"来源编号；未确认留空"}}。''',
        messages=[ContextMessage(role='user', content=json.dumps({**scene_payload(context), 'scene_plan': context['scene_plan'], 'reply_sources': {f'p{i}': p for i,p in enumerate(reply.split('\n')) if p.strip()}}, ensure_ascii=False))],
        max_tokens=8192, temperature=0, thinking=True, reasoning_effort='low')
