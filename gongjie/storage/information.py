"""Persist recipient views, never interpret story text in the storage layer."""
import json

from gongjie.domain.knowledge import knowledge_projection

TABLES = ('room_message_visibility', 'room_personal_states')


def migrate(c):
    c.executescript('''
        CREATE TABLE IF NOT EXISTS room_message_visibility (
            world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
            message_id TEXT PRIMARY KEY REFERENCES room_messages(id) ON DELETE CASCADE,
            audience TEXT NOT NULL, views TEXT
        );
        CREATE TABLE IF NOT EXISTS room_personal_states (
            world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
            room_id TEXT NOT NULL REFERENCES scene_rooms(id) ON DELETE CASCADE,
            character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            state TEXT NOT NULL, PRIMARY KEY(room_id,character_id)
        );
    ''')
    # Old rooms had a fixed roster. Backfill observation jobs, not raw visibility.
    for m in c.execute('''SELECT m.*,r.world_id FROM room_messages m JOIN scene_rooms r ON r.id=m.room_id
                         WHERE m.id NOT IN (SELECT message_id FROM room_message_visibility)''').fetchall():
        audience = [r[0] for r in c.execute('SELECT character_id FROM room_characters WHERE room_id=? ORDER BY sort_order', (m['room_id'],))]
        c.execute('INSERT INTO room_message_visibility VALUES (?,?,?,NULL)', (m['world_id'], m['id'], json.dumps(audience)))
    # Reconstruct only each speaker's own continuity, never the last shared state.
    existing_keys = {(r[0],r[1]) for r in c.execute('SELECT room_id,character_id FROM room_personal_states')}
    for row in c.execute('''SELECT t.world_id,t.room_id,m.character_id,t.scene FROM turn_settlements t
                           JOIN room_messages m ON m.id=t.source_room_message_id
                           WHERE t.room_id IS NOT NULL AND json_extract(t.decision,'$.outcome') IS NOT 'rejected'
                           ORDER BY t.created_at''').fetchall():
        if (row['room_id'],row['character_id']) not in existing_keys:
            save_personal_state(c,row['world_id'],row['room_id'],row['character_id'],json.loads(row['scene']))


def add_message(c, world_id, message_id, audience, views=None):
    if views is not None and set(views) != set(audience):
        raise ValueError('observation_audience_mismatch')
    c.execute('INSERT INTO room_message_visibility VALUES (?,?,?,?)',
              (world_id,message_id,json.dumps(audience),json.dumps(views,ensure_ascii=False) if views is not None else None))


def project_messages(c, messages, character_id):
    result = []
    for message in messages:
        record = c.execute('SELECT views FROM room_message_visibility WHERE message_id=?', (message['id'],)).fetchone()
        views = json.loads(record['views']) if record and record['views'] is not None else {}
        # An unprojected message is never treated as public by a caller.
        result.append({**message, 'content': views.get(character_id, '')})
    return result


def restrict_room_knowledge(c, items, character_id):
    """Constrain legacy acquisition sources by the recipient's saved observation.

    The event remains unchanged. Already projected frozen items can recover their
    original source from the acquisition ledger and receive the same restriction.
    """
    result = []
    for original in items:
        item = dict(original)
        event_id = item.get('event_id') or item.get('id')
        event = c.execute('''SELECT s.room_message_id,m.room_id,m.sequence,e.visibility
                             FROM room_event_sources s JOIN room_messages m ON m.id=s.room_message_id
                             JOIN world_events e ON e.id=s.event_id WHERE s.event_id=?''', (event_id,)).fetchone()
        if not event:
            result.append(item)
            continue
        acquired = c.execute('SELECT source,state FROM character_knowledge WHERE event_id=? AND character_id=?',
                             (event_id, character_id)).fetchone()
        # Public pulse propagation is a separate legitimate acquisition channel;
        # it is not limited to people who observed the original private scene.
        if event['visibility'] == 'public' and (
                not acquired or (acquired['source'] == '世界公开变化' and acquired['state'] == 'heard')):
            result.append(item)
            continue
        raw_source = item.get('source')
        if not isinstance(raw_source, str) or not raw_source.lstrip().startswith('{'):
            raw_source = acquired['source'] if acquired else ''
        try:
            source = json.loads(raw_source)
        except (ValueError, TypeError):
            source = None
        if (not isinstance(source, dict) or source.get('role') not in ('user', 'assistant')
                or source.get('kind') not in ('witnessed', 'told')
                or not isinstance(source.get('quote'), str) or not source['quote'].strip()):
            continue
        message_id = event['room_message_id']
        if source['role'] == 'user':
            user = c.execute('''SELECT id FROM room_messages WHERE room_id=? AND role='user' AND sequence<?
                                ORDER BY sequence DESC LIMIT 1''', (event['room_id'], event['sequence'])).fetchone()
            message_id = user['id'] if user else None
        row = c.execute('SELECT views FROM room_message_visibility WHERE message_id=?', (message_id,)).fetchone()
        views = json.loads(row['views']) if row and row['views'] is not None else {}
        visible = views.get(character_id, '')
        quote = source['quote']
        complete = bool(visible) and quote in visible
        # Keep only whole selected fragments: never manufacture a substring by
        # trimming away a negation, a condition, or the attribution of a claim.
        allowed = quote if complete else '\n'.join(part for part in visible.split('\n') if part.strip() and part in quote)
        if not allowed:
            continue
        if not complete:
            item['knowledge_state'] = 'heard' if source['kind'] == 'told' else 'witnessed'
            item['belief'] = ''
        item['source'] = json.dumps({**source, 'quote': allowed}, ensure_ascii=False)
        result.append(knowledge_projection(item))
    return result


def pending_observations(c, room_id):
    result = []
    for row in c.execute('''SELECT m.*,v.audience FROM room_messages m JOIN room_message_visibility v
                           ON v.message_id=m.id WHERE m.room_id=? AND v.views IS NULL ORDER BY m.sequence''', (room_id,)):
        item = dict(row)
        members = []
        for cid in json.loads(item.pop('audience')):
            character = c.execute('SELECT id,name FROM characters WHERE id=?', (cid,)).fetchone()
            if character:
                members.append(dict(character))
        item['members'] = members
        result.append(item)
    return result


def save_views(c, message_id, views):
    row = c.execute('SELECT m.content,v.audience FROM room_messages m JOIN room_message_visibility v ON v.message_id=m.id WHERE m.id=?', (message_id,)).fetchone()
    if not row or set(views) != set(json.loads(row['audience'])) or not all(isinstance(v,str) for v in views.values()):
        raise ValueError('observation_audience_mismatch')
    if any(part not in row['content'] for text in views.values() for part in text.split('\n') if part):
        raise ValueError('observation_source_mismatch')
    c.execute('UPDATE room_message_visibility SET views=? WHERE message_id=?', (json.dumps(views,ensure_ascii=False),message_id))


def recovery_context(c, context, source_id):
    """Rebuild old frozen room messages through stored views, without live witnesses."""
    result = dict(context)
    result['messages'] = project_messages(c, context['messages'], context['character']['id'])
    user_id = context.get('turn_user_id') or next((m['id'] for m in reversed(context['messages']) if m['role']=='user'), None)
    result['turn_user_id'] = user_id
    result['observation_sources'] = {}
    for role, mid in (('user',user_id),('assistant',source_id)):
        row = c.execute('SELECT views FROM room_message_visibility WHERE message_id=?', (mid,)).fetchone()
        for cid,text in json.loads(row['views'] or '{}').items() if row else []:
            result['observation_sources'].setdefault(cid,{})[role] = text
    result['room'] = {key:context['room'][key] for key in ('id','name','world_id','location_id') if key in context['room']}
    own = personal_state(c, result['room']['id'], context['character']['id'])
    result['scene'] = {**context['scene'], **{key:own.get(key,default) for key,default in (
        ('tension',0),('active_threads',[]),('physical_state',''),('emotional_state',''))}}
    for key in ('knowledge', 'public_events'):
        result[key] = restrict_room_knowledge(c, context.get(key, []), context['character']['id'])
    if 'observation_sources' not in context:
        result.pop('prepared_patch',None)
    return result


def personal_state(c, room_id, character_id):
    row = c.execute('SELECT state FROM room_personal_states WHERE room_id=? AND character_id=?', (room_id,character_id)).fetchone()
    return json.loads(row[0]) if row else {}


def save_personal_state(c, world_id, room_id, character_id, patch):
    state = personal_state(c, room_id, character_id)
    state.update(patch)
    c.execute('INSERT OR REPLACE INTO room_personal_states VALUES (?,?,?,?)',
              (world_id,room_id,character_id,json.dumps(state,ensure_ascii=False)))


def migrate_saves(c, version):
    for row in c.execute('SELECT id,payload FROM world_saves').fetchall():
        payload = json.loads(row['payload'])
        if payload['version'] != 16:
            continue
        tables = payload['tables']
        tables['character_room_presence'] = []
        tables['player_scene_presence'] = []
        tables['room_personal_states'] = []
        tables['room_message_visibility'] = []
        rooms = {r['id']:r for r in tables['scene_rooms']}
        for message in tables['room_messages']:
            audience = [r['character_id'] for r in tables['room_characters'] if r['room_id']==message['room_id']]
            tables['room_message_visibility'].append({'world_id':rooms[message['room_id']]['world_id'],
                'message_id':message['id'],'audience':json.dumps(audience),'views':None})
        states = {}
        messages = {m['id']:m for m in tables['room_messages']}
        for t in sorted(tables['turn_settlements'], key=lambda t:t['created_at']):
            if t.get('room_id') and json.loads(t.get('decision') or '{}').get('outcome') != 'rejected':
                character_id = messages[t['source_room_message_id']]['character_id']
                key = (t['room_id'], character_id)
                state = states.setdefault(key, {'world_id':t['world_id'],'room_id':t['room_id'],'character_id':character_id,'state':{}})
                state['state'].update(json.loads(t['scene']))
        for state in states.values():
            state['state'] = json.dumps(state['state'],ensure_ascii=False)
            tables['room_personal_states'].append(state)
        payload['version'] = version
        c.execute('UPDATE world_saves SET payload=? WHERE id=?', (json.dumps(payload,ensure_ascii=False),row['id']))
