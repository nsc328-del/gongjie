"""Deterministic boundaries for a round's proposed geography."""
from __future__ import annotations

import json
import unicodedata
from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5

from gongjie.domain.patches import PatchValidationError
from gongjie.domain.transcripts import turn_user_text


def location_key(name: str) -> str:
    return ''.join(unicodedata.normalize('NFKC', name).casefold().split())


def metadata(location: dict) -> dict:
    value = location.get('geography') or {}
    return json.loads(value) if isinstance(value, str) else value


def known_locations(locations: list[dict], character_id: str) -> list[dict]:
    result = []
    for place in locations:
        geo = metadata(place)
        if geo.get('generated') and character_id not in geo.get('known_by', []):
            continue
        version = geo.get('known_versions', {}).get(character_id)
        if version:
            place = {**place, 'name':version.get('name', place['name']), 'description':version['description'], 'mood':''}
        # Context copies cannot retain other characters' versions, provenance,
        # or the registry's hidden parent and aliases underneath a partial view.
        visible = version or geo
        projected_geo = {
            'generated': bool(geo.get('generated')),
            'parent_id': visible.get('parent_id'),
            'aliases': list(visible.get('aliases', [])),
            'route_known': version.get('route_known') is True if version else geo.get('route_known', True),
        }
        if geo.get('generated'):
            projected_geo['known_by'] = [character_id]
        if version:
            projected_geo['known_versions'] = {character_id: deepcopy(version)}
        result.append({**place, 'geography': projected_geo})
    return result


def project_location_fields(value: dict, locations: list[dict], *, id_key: str = 'location_id') -> dict:
    """Use the recipient's place label in entity/scene fields as well as the map."""
    place = next((p for p in locations if p['id'] == value.get(id_key)), {})
    result = dict(value)
    for field, source in (('location_name', 'name'), ('location', 'name'), ('location_description', 'description')):
        if field in result:
            result[field] = place.get(source, '')
    return result


def location_view(location: dict) -> dict:
    geo = metadata(location)
    return {k: location.get(k, '') for k in ('id', 'name', 'description')} | {
        'parent_id': geo.get('parent_id'), 'aliases': geo.get('aliases', []),
        'route_known': geo.get('route_known', not (geo.get('generated') and geo.get('parent_id') is None)),
    }


def validate_plan(value: dict, context: dict) -> dict:
    def require(ok, issue):
        if not ok:
            raise PatchValidationError('scene_plan_invalid', [issue])
    require(isinstance(value, dict), '场景方案必须为对象')
    require(set(value) <= {'mode', 'purpose', 'source_quote', 'target_id', 'new_location'}, '场景方案存在多余字段')
    mode = value.get('mode')
    require(isinstance(mode, str) and mode in {'stay', 'mention', 'resolve'}, 'mode 只能为 stay、mention、resolve')
    if mode != 'resolve':
        require(not any(value.get(k) for k in ('target_id', 'new_location', 'purpose', 'source_quote')), 'stay/mention 不提交目的地和动作')
        return {'mode': mode}
    purpose, quote = value.get('purpose'), value.get('source_quote')
    require(isinstance(purpose, str) and purpose in {'information', 'appointment', 'travel'}, '缺少目的地用途')
    require(isinstance(quote, str) and bool(quote.strip()) and quote in turn_user_text(context), '必须引用本轮玩家询问、约定或出发的原文；继续写不能新增玩家出发决定')
    target_id, new = value.get('target_id'), value.get('new_location')
    require(bool(target_id) != bool(new), '请选择现有地点或提出一个新地点')
    locations = {p['id']: p for p in context['locations']}
    if target_id:
        require(isinstance(target_id, str) and target_id in locations, 'target_id 不在当前可知地点中')
        return {'mode': mode, 'purpose': purpose, 'source_quote': quote, 'target': location_view(locations[target_id])}
    require(isinstance(new, dict) and set(new) == {'name', 'parent_id', 'description'}, '新地点只允许 name、parent_id、description')
    require(all(isinstance(v, str) and v.strip() for v in new.values()), '新地点各字段必须为非空文字')
    require(len(new['name']) <= 60 and len(new['description']) <= 300, '地点名称或环境描述过长')
    require(new['parent_id'] in locations or new['parent_id'] == context['world']['id'], '新地点必须归属已有区域或当前世界根区域')
    key = location_key(new['name'])
    matches = [p for p in locations.values() if key in {location_key(n) for n in [p['name'], *metadata(p).get('aliases', [])]}]
    require(len(matches) <= 1, '地点名称有歧义，需选择明确的已有地点 ID')
    if matches:
        return {'mode': mode, 'purpose': purpose, 'source_quote': quote, 'target': location_view(matches[0])}
    target = {'id': 'place_' + uuid5(NAMESPACE_URL, f"{context['world']['id']}:{new['parent_id']}:{key}").hex,
              'name': new['name'].strip(), 'description': new['description'].strip(), 'parent_id': new['parent_id'], 'aliases': []}
    return {'mode': mode, 'purpose': purpose, 'source_quote': quote, 'target': target, 'create': True}


def validate_resolution(value: dict, plan: dict, reply: str) -> dict:
    if (not isinstance(value, dict) or set(value) != {'confirmed', 'arrived', 'quote'}
            or type(value['confirmed']) is not bool or type(value['arrived']) is not bool
            or not isinstance(value['quote'], str)):
        raise PatchValidationError('location_resolution_invalid')
    if value['arrived'] and (not value['confirmed'] or plan['purpose'] != 'travel'):
        raise PatchValidationError('location_arrival_not_authorized')
    if value['confirmed'] and (not value['quote'].strip() or value['quote'] not in reply):
        raise PatchValidationError('location_resolution_source')
    return value


def location_patch(proposal: dict, context: dict) -> dict:
    """Carry the independently reviewed arrival into adjudication without a new guess."""
    result = deepcopy(proposal)
    plan = context.get('scene_plan') or {}
    resolution = plan.get('resolution') or {}
    if resolution.get('arrived'):
        target = plan['target']
        for section in ('scene', 'player'):
            value = result.setdefault(section, {})
            if not isinstance(value, dict):
                raise PatchValidationError('location_patch_invalid')
            value.update(location_id=target['id'], location=target['name'])
            result.setdefault('evidence', {})[section + '.location'] = {
                'role': 'assistant', 'quote': resolution['quote'], 'kind': 'occurred',
            }
    return result


def validate_planned_locations(patch: dict, context: dict, reply: str) -> None:
    plan = context.get('scene_plan')
    if plan is None:
        return  # Historic frozen jobs predate scene planning.
    resolution = plan.get('resolution') or {}
    if resolution:
        validate_resolution(resolution, plan, reply)
    by_name = {p['name']: p['id'] for p in context['locations']}
    for section in ('scene', 'player'):
        current = (context.get(section) or {}).get('location_id')
        expected = plan['target']['id'] if resolution.get('arrived') else current
        value = patch.get(section) or {}
        actual = value.get('location_id') or by_name.get(value.get('location')) or current
        if actual != expected:
            raise PatchValidationError('location_outside_plan', ['地点必须与正文发布前已核对的场景方案一致，提及和约定不等于抵达。'])
