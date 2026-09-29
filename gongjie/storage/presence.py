"""Current scene identities, separate from persistent room history."""
from __future__ import annotations
from gongjie.storage.location_knowledge import witness_location


def migrate(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS character_room_presence (
        character_id TEXT PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
        room_id TEXT NOT NULL REFERENCES scene_rooms(id) ON DELETE CASCADE,
        entered_at TEXT NOT NULL
    )''')
    connection.execute('''CREATE TABLE IF NOT EXISTS player_scene_presence (
        world_id TEXT PRIMARY KEY REFERENCES worlds(id) ON DELETE CASCADE,
        room_id TEXT REFERENCES scene_rooms(id) ON DELETE CASCADE,
        character_id TEXT REFERENCES characters(id) ON DELETE CASCADE,
        entered_at TEXT NOT NULL,
        CHECK ((room_id IS NULL) != (character_id IS NULL))
    )''')


def enter_private(connection, world_id, character_id, entered_at):
    connection.execute('DELETE FROM character_room_presence WHERE character_id=?', (character_id,))
    connection.execute('''INSERT INTO player_scene_presence VALUES (?,NULL,?,?)
        ON CONFLICT(world_id) DO UPDATE SET room_id=NULL,
        character_id=excluded.character_id,entered_at=excluded.entered_at''',
        (world_id, character_id, entered_at))


def enter_room(connection, world_id, room_id, entered_at):
    connection.execute('''INSERT INTO player_scene_presence VALUES (?,?,NULL,?)
        ON CONFLICT(world_id) DO UPDATE SET room_id=excluded.room_id,
        character_id=NULL,entered_at=excluded.entered_at''',
        (world_id, room_id, entered_at))


def gather_room(connection, world_id, room_id, location_id, character_ids, entered_at):
    for character_id in character_ids:
        connection.execute('''INSERT INTO character_room_presence VALUES (?,?,?)
            ON CONFLICT(character_id) DO UPDATE SET room_id=excluded.room_id,
            entered_at=excluded.entered_at''', (character_id, room_id, entered_at))
        connection.execute('UPDATE characters SET current_location_id=?,updated_at=? WHERE id=?',
                           (location_id, entered_at, character_id))
        witness_location(connection, world_id, character_id, location_id, room_id)
        connection.execute("UPDATE character_world_states SET availability='present',updated_at=? WHERE character_id=?",
                           (entered_at, character_id))
    connection.execute('''INSERT INTO world_flow_states(
        world_id,player_location_id,player_condition,visible_traces,pulse_narration,pulse_number,updated_at)
        VALUES (?,?,'','[]','',0,?) ON CONFLICT(world_id) DO UPDATE SET
        player_location_id=excluded.player_location_id,updated_at=excluded.updated_at''',
        (world_id, location_id, entered_at))
    enter_room(connection, world_id, room_id, entered_at)


def member_ids(connection, room_id):
    return [row['character_id'] for row in connection.execute('''
        SELECT p.character_id FROM character_room_presence p
        JOIN room_characters rc ON rc.room_id=p.room_id AND rc.character_id=p.character_id
        JOIN scene_rooms r ON r.id=p.room_id JOIN characters c ON c.id=p.character_id
        WHERE p.room_id=? AND c.world_id=r.world_id AND c.current_location_id IS r.location_id
        ORDER BY rc.sort_order''', (room_id,))]


def clear_departed(connection, world_id):
    connection.execute('''DELETE FROM character_room_presence
        WHERE character_id IN (
            SELECT p.character_id FROM character_room_presence p
            JOIN characters c ON c.id=p.character_id JOIN scene_rooms r ON r.id=p.room_id
            LEFT JOIN character_world_states s ON s.character_id=c.id
            WHERE c.world_id=? AND (c.current_location_id IS NOT r.location_id OR s.availability='unavailable')
        )''', (world_id,))


def assert_private(connection, conversation_id):
    current = connection.execute('''SELECT c.current_location_id, f.player_location_id,
        p.room_id,p.character_id,s.availability FROM conversations v
        JOIN characters c ON c.id=v.character_id
        LEFT JOIN world_flow_states f ON f.world_id=v.world_id
        LEFT JOIN player_scene_presence p ON p.world_id=v.world_id
        LEFT JOIN character_world_states s ON s.character_id=c.id
        WHERE v.id=? AND p.character_id=v.character_id''', (conversation_id,)).fetchone()
    if (not current or current['room_id'] is not None
            or current['current_location_id'] != current['player_location_id']
            or current['availability'] == 'unavailable'):
        raise ValueError('现场已经变化，请重新进入角色')


def room_is_current(connection, room_id):
    row = connection.execute('''SELECT 1 FROM scene_rooms r
        JOIN player_scene_presence p ON p.world_id=r.world_id AND p.room_id=r.id
        JOIN world_flow_states f ON f.world_id=r.world_id
        WHERE r.id=? AND f.player_location_id IS r.location_id''', (room_id,)).fetchone()
    return bool(row)


def assert_room(connection, room_id):
    if not room_is_current(connection, room_id):
        raise ValueError('现场已经变化，请重新进入同场')
    if not member_ids(connection, room_id):
        raise ValueError('这个现场的人已经离开')
