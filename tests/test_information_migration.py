import json
import tempfile
import unittest
from pathlib import Path

from gongjie.storage import information
from gongjie.storage.store import Store


class InformationMigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Path(self.directory.name) / 'world.db')
        self.store.initialize()
        self.world = 'world_daylight'
        self.room = self.store.create_room(self.world, '旧同场', 'daylight_throne',
                                          ['daylight_lorne', 'daylight_evelyn'])['room']['id']
        self.public = '洛恩把折起的纸条递给玩家。'
        self.secret = '纸条上写着口令青铜海鸥。'
        self.body = self.public + '\n' + self.secret
        self.user_text = '我只在洛恩耳边说：“红钥匙藏在衣袖里。”'
        self.user = self.store.add_room_message(self.room, 'user', None, self.user_text,
            observations={'daylight_lorne': self.user_text, 'daylight_evelyn': ''})
        context = self.store.room_character_context(self.room, 'daylight_lorne')
        legacy_views = {c['id']: self.body for c in context['room_characters']}
        context['observation_sources'] = {cid: {'assistant': text} for cid, text in legacy_views.items()}
        self.message = self.store.add_room_message(self.room, 'assistant', 'daylight_lorne', self.body,
                                                  observations=legacy_views)
        source = {'role': 'assistant', 'quote': self.body, 'kind': 'witnessed'}
        result = self.store.apply_room_patch(self.room, 'daylight_lorne', self.message['id'], {
            'event': {'title': '旧纸条事件', 'details': self.body, 'visibility': 'secret',
                      'participants': ['洛恩·维瑟尔'], 'witnesses': ['伊芙琳·薇尔'],
                      'knowledge_sources': {name: source for name in ('洛恩·维瑟尔', '伊芙琳·薇尔')}},
            'scene': {'physical_state': '洛恩握着茶杯。'},
            'evidence': {'event': {'role': 'assistant', 'quote': self.body, 'kind': 'occurred'}},
        }, context=context)
        self.event_id = result['event_id']
        # This emulates the former room implementation's over-broad source, not
        # the new observer compiler: even a believed variant may contain a leak.
        with self.store.transaction() as c:
            c.execute("UPDATE character_knowledge SET state='misunderstood',belief=? WHERE event_id=? AND character_id=?",
                      ('她以为青铜海鸥已经公开。', self.event_id, 'daylight_evelyn'))
        self.frozen = self.store.room_character_context(self.room, 'daylight_evelyn')
        self.frozen['knowledge'] = [{'id': self.event_id, 'title': '所知版本', 'details': self.secret,
            'source': '原有认知', 'knowledge_state': 'misunderstood', 'belief': '她以为青铜海鸥已经公开。'}]

    def raw_knowledge(self, c, character_id='daylight_evelyn'):
        return [dict(row) for row in c.execute('''SELECT e.*,k.state,k.belief,k.source FROM character_knowledge k
            JOIN world_events e ON e.id=k.event_id WHERE k.character_id=? AND e.id=?''', (character_id, self.event_id))]

    def test_legacy_success_scene_and_saved_knowledge_are_restricted_after_migration(self):
        context = self.store.room_character_context(self.room, 'daylight_lorne')
        second = self.store.add_room_message(self.room, 'assistant', 'daylight_lorne', '无效提案。',
            observations={'daylight_lorne': '无效提案。', 'daylight_evelyn': '无效提案。'}, settlement_context=context)
        rejected = self.store.reject_settlement(second['id'], 'validation_error', [])
        with self.store.transaction() as c:
            self.assertEqual(c.execute('SELECT decision FROM turn_settlements WHERE event_id=?', (self.event_id,)).fetchone()[0], '{}')
            # A rejected stored proposal must never overwrite the accepted one.
            c.execute('UPDATE turn_settlements SET scene=? WHERE id=?',
                      (json.dumps({'physical_state': '不应恢复的状态'}), rejected['settlement']['id']))
            c.execute('DELETE FROM room_personal_states WHERE room_id=?', (self.room,))
            c.execute('UPDATE room_message_visibility SET views=NULL WHERE message_id=?', (self.message['id'],))
        checkpoint = self.store.save_world(self.world, '旧格式')
        with self.store.transaction() as c:
            payload = json.loads(c.execute('SELECT payload FROM world_saves WHERE id=?', (checkpoint['id'],)).fetchone()[0])
            payload['version'] = 16
            for table in ('room_personal_states', 'room_message_visibility', 'character_room_presence', 'player_scene_presence'):
                payload['tables'].pop(table)
            c.execute('UPDATE world_saves SET payload=? WHERE id=?', (json.dumps(payload, ensure_ascii=False), checkpoint['id']))
        self.store.initialize()
        with self.store.read() as c:
            self.assertEqual(information.personal_state(c, self.room, 'daylight_lorne')['physical_state'], '洛恩握着茶杯。')
            self.assertEqual(information.restrict_room_knowledge(c, self.raw_knowledge(c), 'daylight_evelyn'), [])
        self.store.load_world_save(self.world, checkpoint['id'])
        with self.store.transaction() as c:
            self.assertEqual(information.personal_state(c, self.room, 'daylight_lorne')['physical_state'], '洛恩握着茶杯。')
            information.save_views(c, self.message['id'], {'daylight_lorne': self.body, 'daylight_evelyn': self.public})
            observed = information.restrict_room_knowledge(c, self.raw_knowledge(c), 'daylight_evelyn')
            self.assertEqual(observed[0]['details'], self.public)
            self.assertNotIn('青铜海鸥', json.dumps(observed, ensure_ascii=False))
            recovered = information.recovery_context(c, self.frozen, self.message['id'])
            self.assertNotIn('青铜海鸥', json.dumps(recovered['knowledge'], ensure_ascii=False))
            self.assertEqual(recovered['knowledge'][0]['knowledge_state'], 'witnessed')
            # Read projections never rewrite the underlying fact or old ledger.
            self.assertIn('青铜海鸥', c.execute('SELECT details FROM world_events WHERE id=?', (self.event_id,)).fetchone()[0])
            self.assertIn('青铜海鸥', self.raw_knowledge(c)[0]['source'])
        current = self.store.save_world(self.world, '已补齐观察')
        self.store.load_world_save(self.world, current['id'])
        with self.store.read() as c:
            self.assertEqual(information.restrict_room_knowledge(c, self.raw_knowledge(c), 'daylight_evelyn')[0]['details'], self.public)

    def test_user_source_uses_its_original_message_and_public_propagation_stays_public(self):
        with self.store.transaction() as c:
            information.save_views(c, self.message['id'], {'daylight_lorne': self.body, 'daylight_evelyn': self.body})
            c.execute('UPDATE character_knowledge SET source=? WHERE event_id=? AND character_id=?',
                      (json.dumps({'role': 'user', 'kind': 'told', 'quote': self.user_text}, ensure_ascii=False), self.event_id, 'daylight_evelyn'))
            self.assertEqual(information.restrict_room_knowledge(c, self.raw_knowledge(c), 'daylight_evelyn'), [])
            c.execute("UPDATE world_events SET visibility='public' WHERE id=?", (self.event_id,))
            # An old on-scene record still cannot use publicness to undo its boundary.
            self.assertEqual(information.restrict_room_knowledge(c, self.raw_knowledge(c), 'daylight_evelyn'), [])
        self.store.commit_cognition_sync(self.world, [self.event_id], {})
        with self.store.read() as c:
            other = self.raw_knowledge(c, 'daylight_severin')
            self.assertEqual(other[0]['source'], '世界公开变化')
            self.assertEqual(information.restrict_room_knowledge(c, other, 'daylight_severin'), other)


if __name__ == '__main__':
    unittest.main()
