"""World-scoped checkpoints. Provider configuration and other worlds are excluded."""
import json
from uuid import uuid4
from datetime import datetime, timezone

TABLES = ('worlds','locations','characters','relationships','world_reset_baselines','character_reset_baselines','relationship_reset_baselines','world_flow_states','player_commitments','character_world_states','conversations','messages','scene_states','world_events','event_participants','character_knowledge','scene_rooms','room_characters','room_messages','room_event_sources','turn_settlements','conversation_scene_entries','player_profiles','settlement_jobs','character_room_presence','player_scene_presence','room_message_visibility','room_personal_states')

def capture(c, world_id):
    result = {}
    for table in TABLES:
        columns = {r[1] for r in c.execute(f'PRAGMA table_info({table})')}
        if table == 'worlds': clause = 'id=?'
        elif 'world_id' in columns: clause = 'world_id=?'
        else:
            owners = [('character_id','characters'),('conversation_id','conversations'),('event_id','world_events'),('room_id','scene_rooms'),('relationship_id','relationships')]
            key, parent = next((key,parent) for key,parent in owners if key in columns)
            clause = f'{key} IN (SELECT id FROM {parent} WHERE world_id=?)'
        rows = [dict(r) for r in c.execute(f'SELECT * FROM {table} WHERE {clause}', (world_id,))]
        if table == 'settlement_jobs':
            rows.sort(key=lambda r:r['position'])
            for row in rows: row.pop('position')
        result[table] = rows
    return result

def insert_save(c, world_id, label, automatic=False):
    world = c.execute('SELECT * FROM worlds WHERE id=?',(world_id,)).fetchone()
    if not world: raise KeyError('world_not_found')
    value = {'id':'save_'+uuid4().hex, 'world_id':world_id,'name':label.strip()[:80] or world['current_time'], 'world_time':world['current_time'], 'created_at':datetime.now(timezone.utc).isoformat(timespec='seconds'), 'automatic':int(automatic)}
    c.execute('INSERT INTO world_saves VALUES (?,?,?,?,?,?,?)',(*value.values(),json.dumps({'version':c.execute('PRAGMA user_version').fetchone()[0], 'tables':capture(c,world_id)},ensure_ascii=False)))
    return value

def restore(c, world_id, save_id):
    row = c.execute('SELECT payload FROM world_saves WHERE id=? AND world_id=?',(save_id,world_id)).fetchone()
    if not row: raise KeyError('save_not_found')
    payload=json.loads(row['payload'])
    if payload['version'] != c.execute('PRAGMA user_version').fetchone()[0]: raise ValueError('存档版本与当前版本不一致')
    backup=insert_save(c,world_id,'读档前自动备份',True)
    c.execute('PRAGMA defer_foreign_keys=ON')
    c.execute('DELETE FROM worlds WHERE id=?',(world_id,))
    for table in TABLES:
        for item in payload['tables'][table]:
            keys=list(item)
            c.execute(f'INSERT INTO {table} ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)})', [item[k] for k in keys])
    if c.execute('PRAGMA foreign_key_check').fetchone(): raise ValueError('存档数据关联不完整，当前进度未改变')
    return backup
