"""Resolve explicitly heard place names without disclosing registry descriptions."""
import json
from copy import deepcopy

from gongjie.domain.locations import location_key, metadata
from gongjie.domain.transcripts import turn_user_text
from gongjie.domain.patches import PatchValidationError


def mentioned_locations(c, context):
    text = turn_user_text(context)
    if not text:
        return context
    world_id, character_id = context['world']['id'], context['character']['id']
    known = {p['id'] for p in context['locations']}
    names = {}
    for row in c.execute('SELECT * FROM locations WHERE world_id=?', (world_id,)):
        place = dict(row)
        for name in [place['name'], *metadata(place).get('aliases', [])]:
            if name and location_key(name) in location_key(text):
                names.setdefault(location_key(name), {})[place['id']] = (place, name)
    # Longest explicit names take precedence over a shorter embedded name.
    matches = {p['id']: (p, heard_name) for name, candidates in names.items() if len(candidates)==1
               and not any(name != other and name in other for other in names)
               for p, heard_name in candidates.values() if p['id'] not in known}
    result = deepcopy(context)
    result['location_mentions'] = []
    for place, heard_name in matches.values():
        mention = {'id':place['id'],'name':heard_name,'character_id':character_id,'quote':text,'role':'user'}
        result['location_mentions'].append(mention)
        # A reported description is a report, not the registry's world fact.
        result['locations'].append({'id':place['id'],'world_id':world_id,'name':heard_name,
            'description':'玩家提及（未因此证实路线或通行权限）：' + text,
            'mood':'','geography':{'generated':True,'parent_id':None,'aliases':[]}})
    return result


def commit_mentions(c, context, source_id):
    character_id, world_id = context['character']['id'], context['world']['id']
    text = turn_user_text(context)
    for mention in context.get('location_mentions', []):
        if mention['character_id'] != character_id or not mention['quote'] or mention['quote'] not in text:
            raise PatchValidationError('location_knowledge_source')
        row = c.execute('SELECT * FROM locations WHERE id=? AND world_id=?', (mention['id'],world_id)).fetchone()
        if not row:
            raise PatchValidationError('location_knowledge_missing')
        geo = metadata(dict(row))
        if not geo.get('generated') or character_id in geo.get('known_by', []):
            continue
        heard_name = mention.get('name')
        if (not isinstance(heard_name, str) or not heard_name
                or location_key(heard_name) not in {location_key(n) for n in [row['name'], *geo.get('aliases', [])] if n}
                or location_key(heard_name) not in location_key(mention['quote'])):
            raise PatchValidationError('location_knowledge_name')
        geo.setdefault('known_by', []).append(character_id)
        geo.setdefault('known_versions', {})[character_id] = {'name':heard_name,'description':'玩家提及（未因此证实路线或通行权限）：' + mention['quote'], 'parent_id':None, 'aliases':[]}
        geo.setdefault('knowledge_sources', {})[character_id] = {'source_id':source_id,'kind':'told','quote':mention['quote']}
        c.execute('UPDATE locations SET geography=? WHERE id=?', (json.dumps(geo,ensure_ascii=False),row['id']))


def witness_location(c, world_id, character_id, location_id, source_id):
    row = c.execute('SELECT * FROM locations WHERE id=? AND world_id=?', (location_id,world_id)).fetchone()
    if not row:
        return
    geo = metadata(dict(row))
    if not geo.get('generated'):
        return
    if character_id not in geo.get('known_by', []):
        geo.setdefault('known_by', []).append(character_id)
        geo.setdefault('known_versions', {})[character_id] = {'description':'当前所在的地点。','parent_id':None,'aliases':[],'route_known':True}
    elif character_id in geo.get('known_versions', {}):
        geo['known_versions'][character_id]['route_known'] = True
    geo.setdefault('knowledge_sources', {})[character_id] = {'source_id':source_id,'kind':'witnessed'}
    c.execute('UPDATE locations SET geography=? WHERE id=?', (json.dumps(geo,ensure_ascii=False),location_id))
