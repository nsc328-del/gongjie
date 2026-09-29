#!/usr/bin/env python3
"""Export authored desktop cards for the native app without messages, facts or keys."""
import argparse
import json
import sqlite3
from pathlib import Path


def export_book(database: Path, world_id: str) -> dict:
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute('SELECT name,premise,atmosphere,rules,opening_narration FROM worlds WHERE id=?', (world_id,)).fetchone()
        if row is None:
            raise ValueError('没有找到这个世界')
        world = dict(row)
        # current_time is also an SQLite function: always qualify its column.
        baseline = connection.execute('SELECT world_reset_baselines.current_time AS story_time FROM world_reset_baselines WHERE world_id=?', (world_id,)).fetchone()
        if baseline is None:
            raise ValueError('世界缺少创作底稿，请先在桌面版保存世界')
        world['current_time'] = baseline['story_time']
        locations = [dict(r) for r in connection.execute('SELECT id,name,description,mood FROM locations WHERE world_id=? ORDER BY sort_order', (world_id,))]
        characters = [dict(r) for r in connection.execute('''SELECT c.id,c.name,c.age,c.pronouns,c.role,c.tagline,c.opening_hook,
            c.persona,c.speech_style,c.desire_profile,c.signature_behaviors,c.loss_of_control,c.accent,
            b.current_location_id FROM characters c JOIN character_reset_baselines b ON b.character_id=c.id
            WHERE c.world_id=? ORDER BY c.sort_order''', (world_id,))]
        for character in characters:
            character['desire_profile'] = json.loads(character['desire_profile'])
            character['signature_behaviors'] = json.loads(character['signature_behaviors'])
        relationships = [dict(r) for r in connection.execute('''SELECT r.id,r.from_character_id,r.to_character_id,r.to_user,
            b.label,b.trust,b.desire,b.tension,b.notes FROM relationships r
            JOIN relationship_reset_baselines b ON b.relationship_id=r.id WHERE r.world_id=?''', (world_id,))]
        for relation in relationships:
            relation['to_user'] = bool(relation['to_user'])
    return {'format': 'gongjie-worldbook', 'version': 1, 'book': {'world': world, 'locations': locations, 'characters': characters, 'relationships': relationships}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path(__file__).resolve().parents[2] / 'data/gongjie.db')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--world')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.list:
        with sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True) as connection:
            for world_id, name in connection.execute('SELECT id,name FROM worlds ORDER BY updated_at DESC'):
                print(f'{world_id}\t{name}')
        return
    if not args.world or not args.output:
        parser.error('导出时需要 --world 和 --output')
    data = export_book(args.database, args.world)
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(data, output, ensure_ascii=False, indent=2)
    print(f'世界书已导出：{args.output}')


if __name__ == '__main__':
    main()
