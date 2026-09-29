"""Refresh packaged worldbook and prompt contracts from the Web sources."""
import ast
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
source = root / 'worldbooks/白昼将死/白昼将死.worldbook.json'
resource = root / 'ios/Sources/GongjieCore/Resources/Daylight.json'
prompt_resource = resource.with_name('Prompts.json')


def scope(module, function=None):
    tree = ast.parse((root / ('gongjie/' + module + '.py')).read_text())
    if function is None:
        return tree
    matches = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function]
    if len(matches) != 1:
        raise SystemExit(f'提示词入口已变化：{module}.{function}')
    return matches[0]


def literal(module, function, prefix):
    matches = [node.value for node in ast.walk(scope(module, function)) if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith(prefix)]
    if len(matches) != 1:
        raise SystemExit(f'提示词契约已变化，需要更新同步映射：{module}.{function} / {prefix}')
    return matches[0]


def observation_prompts():
    # Resolve only literal concatenation. Never import or execute the server runtime.
    tree = scope('scenes/observations', '_request')
    common = literal('scenes/observations', '_request', '你只处理这一条同场消息')

    def value(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and node.id == 'common':
            return common
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return value(node.left) + value(node.right)
        raise SystemExit('观察提示词不再是静态拼接，需要更新同步映射')

    systems = [value(node.value) for node in ast.walk(tree) if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'system' for target in node.targets)]
    if len(systems) != 2:
        raise SystemExit('观察提示词分支已变化，需要更新同步映射')
    return {
        'observation': next(text for text in systems if text.startswith('你是同场观察编译员。')),
        'observationReview': next(text for text in systems if text.startswith('你是同场观察核对员。')),
    }


def prompts():
    mappings = {
        'adjudication': ('scenes/adjudication', 'patch_context', '你是共享世界的状态裁判'),
        'review': ('scenes/adjudication', '_validate_extracted_patch', '你是硬状态证据核对员'),
        'pulse': ('cognition/pulse', 'character_pulse_context', '你只负责共享世界中一名角色'),
        'transition': ('cognition/pulse', 'transition_context', '你负责把共享世界从当前时刻'),
        'transitionReview': ('cognition/pulse', 'review_transition', '你是世界局面校对员'),
        'entryReview': ('cognition/compiler', 'scene_entry_grounding_context', '你是角色扮演接续开场的记忆校对员'),
        'appointment': ('cognition/pulse', 'appointment_time', '你是世界时间规划员'),
        'interaction': ('cognition/compiler', None, '【接话与玩家自主】'),
        'summary': ('cognition/sync', 'summary_context', '你负责为共享世界中的一名角色'),
        'scenePlanning': ('cognition/places', 'planning_request', '你是场景规划员'),
        'scenePlanReview': ('cognition/places', 'plan_review_request', '你是地点设定核对员'),
        'sceneReplyReview': ('cognition/places', 'reply_review_request', '你是转场正文核对员'),
        'sceneInstructions': ('cognition/places', 'plan_instructions', '\n本方案只准入地理'),
    }
    result = {key: literal(*mapping) for key, mapping in mappings.items()}
    writing = next(node.value.split('写作纪律：', 1)[1] for node in ast.walk(scope('cognition/compiler', 'roleplay_system')) if isinstance(node, ast.Constant) and isinstance(node.value, str) and '写作纪律：\n1.' in node.value)
    result['narrative'] = writing + '直接续写故事正文。'
    return result | observation_prompts()


expected_prompts = prompts()
if '--check' in sys.argv:
    if source.read_bytes() != resource.read_bytes():
        raise SystemExit('内置世界书资源需要同步')
    actual = json.loads(prompt_resource.read_text())
    if actual != expected_prompts:
        changed = sorted(key for key in set(actual) | set(expected_prompts) if actual.get(key) != expected_prompts.get(key))
        raise SystemExit('内置提示词资源需要同步：' + ', '.join(changed))
else:
    resource.write_bytes(source.read_bytes())
    prompt_resource.write_text(json.dumps(expected_prompts, ensure_ascii=False, indent=2) + '\n')
