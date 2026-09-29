"""Geography persistence; no prompts or provider calls."""
from __future__ import annotations

import json

from gongjie.domain.locations import location_key, metadata, validate_resolution
from gongjie.domain.patches import PatchValidationError


def migrate(connection):
    if 'geography' not in {r[1] for r in connection.execute('PRAGMA table_info(locations)')}:
        connection.execute("ALTER TABLE locations ADD COLUMN geography TEXT NOT NULL DEFAULT '{}'")
    # Existing checkpoints are upgraded once, so restore still has a single format.
    for row in connection.execute('SELECT id,payload FROM world_saves').fetchall():
        payload = json.loads(row['payload'])
        if payload['version'] == 15:
            for place in payload['tables']['locations']:
                place.setdefault('geography', '{}')
            payload['version'] = 16
            connection.execute('UPDATE world_saves SET payload=? WHERE id=?', (json.dumps(payload, ensure_ascii=False), row['id']))


def commit_plan(connection, context, source_id, reply):
    plan = context.get('scene_plan') or {}
    result = plan.get('resolution') or {}
    if not result.get('confirmed'):
        return
    validate_resolution(result, plan, reply)
    target = plan['target']
    world_id = context['world']['id']
    row = connection.execute('SELECT * FROM locations WHERE id=? AND world_id=?', (target['id'], world_id)).fetchone()
    if not row:
        if not plan.get('create') or (target['parent_id'] != world_id and not connection.execute('SELECT 1 FROM locations WHERE id=? AND world_id=?', (target['parent_id'], world_id)).fetchone()):
            raise PatchValidationError('location_parent_missing')
        key = location_key(target['name'])
        for other in connection.execute('SELECT * FROM locations WHERE world_id=?', (world_id,)):
            if key in {location_key(n) for n in [other['name'], *metadata(dict(other)).get('aliases', [])]}:
                raise PatchValidationError('location_duplicate')
        geo = {'generated': True, 'parent_id': target['parent_id'], 'aliases': target['aliases'], 'source_id': source_id, 'known_by': []}
        connection.execute('INSERT INTO locations(id,world_id,name,description,mood,sort_order,geography) VALUES (?,?,?,?,?,?,?)',
                           (target['id'], world_id, target['name'], target['description'], '',
                            connection.execute('SELECT COALESCE(MAX(sort_order),-1)+1 FROM locations WHERE world_id=?', (world_id,)).fetchone()[0], json.dumps(geo, ensure_ascii=False)))
    else:
        geo = metadata(dict(row))
    if geo.get('generated'):
        known = set(geo.get('known_by', [])) | {context['character']['id']}
        geo['known_by'] = sorted(known)
        sources = geo.setdefault('knowledge_sources', {})
        for character_id in known:
            if character_id not in sources:
                sources[character_id] = {'source_id': source_id, 'kind': 'witnessed' if result.get('arrived') else 'told'}
        if result.get('arrived'):
            version = geo.get('known_versions', {}).get(context['character']['id'])
            if version is not None:
                version.update(route_known=True, description=result['quote'])
        connection.execute('UPDATE locations SET geography=? WHERE id=?', (json.dumps(geo, ensure_ascii=False), target['id']))


def check_plan(connection, context, plan):
    if not plan.get('create'):
        return
    key = location_key(plan['target']['name'])
    for row in connection.execute('SELECT * FROM locations WHERE world_id=?', (context['world']['id'],)):
        if key in {location_key(n) for n in [row['name'], *metadata(dict(row)).get('aliases', [])]}:
            # Do not disclose another character's private geographic description.
            raise PatchValidationError('location_duplicate', ['该新地点方案无法作为独立地点登记。只能使用当前可知的已有地点；尚不了解去向时留在原地沟通，不编造路线。'])


def reset_generated(connection, world_id):
    places = {r['id']: dict(r) for r in connection.execute('SELECT * FROM locations WHERE world_id=?', (world_id,))}
    keep = {r[0] for r in connection.execute('SELECT current_location_id FROM characters WHERE world_id=?', (world_id,)) if r[0]}
    keep |= {p['id'] for p in places.values() if not metadata(p).get('generated')}
    # Authored character baselines may deliberately reference a discovered location.
    for place_id in list(keep):
        while place_id in places:
            parent = metadata(places[place_id]).get('parent_id')
            if not parent or parent in keep:
                break
            keep.add(parent)
            place_id = parent
    for place_id in places.keys() - keep:
        connection.execute('DELETE FROM locations WHERE id=?', (place_id,))
