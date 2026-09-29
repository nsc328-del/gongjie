import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from gongjie.ai.providers import ReplySnapshot
from gongjie.domain.locations import location_patch, validate_plan
from gongjie.scenes.rooms import stream_room_turn
from gongjie.scenes.settlements import settle_message
from gongjie.scenes.turns import stream_turn
from gongjie.storage.store import Store


class PresenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'world.db')
        self.store.initialize()
        self.world = 'world_daylight'

    def room(self, members=('daylight_lorne', 'daylight_evelyn'), location='daylight_throne'):
        return self.store.create_room(self.world, '会面', location, list(members))['room']['id']

    async def test_gather_private_entry_and_second_room_keep_one_actual_presence(self):
        room = self.room()
        result = self.store.get_room(room)
        self.assertEqual(result['player']['location_id'], 'daylight_throne')
        self.assertTrue(all(c['current_location_id'] == 'daylight_throne' for c in result['characters']))
        reply = self.store.add_room_message(room, 'assistant', 'daylight_evelyn', '伊芙琳放下茶杯。')
        self.store.apply_room_patch(room, 'daylight_evelyn', reply['id'], {'scene': {'physical_state': '放下茶杯'}})
        self.assertEqual(self.store.get_character('daylight_evelyn')['current_location_id'], 'daylight_throne')
        # A private meeting in the same physical location still ends that person's room participation.
        conv = self.store.get_or_create_conversation(self.world, 'daylight_evelyn')
        self.store.assert_conversation_entry(conv)
        self.assertEqual([c['id'] for c in self.store.get_room(room)['characters']], ['daylight_lorne'])
        self.store.enter_room(room)
        self.assertEqual([c['id'] for c in self.store.get_room(room)['characters']], ['daylight_lorne'])
        with self.assertRaisesRegex(ValueError, '离开'):
            self.store.room_character_context(room, 'daylight_evelyn')
        other = self.room(('daylight_lorne',), 'daylight_throne')
        self.assertEqual(self.store.get_room(room)['characters'], [])
        self.assertEqual([c['id'] for c in self.store.get_room(other)['characters']], ['daylight_lorne'])
        with self.store.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM room_characters WHERE room_id=?', (room,)).fetchone()[0], 2)

    async def test_pulse_clears_departed_members_without_recalling_or_removing_stayers(self):
        room = self.room()
        self.store.commit_world_pulse(self.world, [], {
            'daylight_lorne': {'location_id': 'daylight_throne', 'availability': 'reachable'},
            'daylight_evelyn': {'location_id': 'daylight_garden', 'availability': 'reachable'},
        }, {'world_time': '停昼七年·校时日·早钟之后'})
        self.assertEqual([c['id'] for c in self.store.get_room(room)['characters']], ['daylight_lorne'])
        self.store.enter_room(room)
        context = self.store.room_character_context(room, 'daylight_lorne')
        self.assertEqual([c['id'] for c in context['room_characters']], ['daylight_lorne'])
        self.assertEqual(self.store.get_character('daylight_evelyn')['current_location_id'], 'daylight_garden')

    async def test_stale_private_and_room_tabs_cannot_create_messages_or_call_provider(self):
        class NoCalls:
            async def response(self, *_):
                raise AssertionError('stale scene reached provider')
            async def stream(self, *_):
                raise AssertionError('stale scene reached provider')
                yield
        room = self.room()
        conv = self.store.get_or_create_conversation(self.world, 'daylight_evelyn')
        self.store.get_or_create_conversation(self.world, 'daylight_lorne')
        # All people are at the same location, so location equality alone is insufficient.
        args = dict(store=self.store, provider_client=NoCalls(), profile_loader=lambda: {},
                    content='你看见了吗？', is_disconnected=lambda: asyncio.sleep(0))
        private = [json.loads(e) async for e in stream_turn(**args, conversation_id=conv, continuation=False)]
        group = [json.loads(e) async for e in stream_room_turn(**args, room_id=room)]
        self.assertEqual([e['type'] for e in private], ['error'])
        self.assertEqual([e['type'] for e in group], ['error'])
        self.assertEqual(self.store.get_conversation(conv)['messages'], [])
        self.assertEqual(self.store.get_room(room)['messages'], [])

    async def test_presence_survives_reload_save_restore_and_is_cleared_on_reset(self):
        room = self.room()
        saved = self.store.save_world(self.world, '共同在场')
        self.store.get_or_create_conversation(self.world, 'daylight_evelyn')
        self.store.load_world_save(self.world, saved['id'])
        reopened = Store(self.store.path)
        reopened.initialize()
        reopened.assert_room_entry(room)
        self.assertEqual({c['id'] for c in reopened.get_room(room)['characters']}, {'daylight_lorne', 'daylight_evelyn'})
        reopened.reset_world(self.world, reopened.get_world(self.world)['world']['name'])
        with reopened.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM player_scene_presence').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM character_room_presence').fetchone()[0], 0)

    async def test_destination_moves_only_speaker_and_player_and_stops_room_round(self):
        room = self.room()
        class Travel:
            def __init__(self): self.plans = 0
            async def response(self, request, profile):
                if '你是同场观察编译员' in request.system:
                    payload = json.loads(request.messages[0].content)
                    value = {m['id']: {'quotes': [payload['content']]} for m in payload['members']}
                elif '你是同场观察核对员' in request.system:
                    value = {'approved': True, 'issues': []}
                elif '你是场景规划员' in request.system:
                    self.plans += 1
                    value = {'mode': 'resolve', 'purpose': 'travel', 'source_quote': '走吧',
                             'new_location': {'name': '金枝侧廊', 'parent_id': 'daylight_throne', 'description': '金枝厅旁一段安静侧廊。'}}
                elif '地点设定核对员' in request.system or '硬状态证据核对员' in request.system:
                    value = {'approved': True, 'issues': []}
                elif '转场正文核对员' in request.system:
                    value = {'approved': True, 'issues': [], 'resolution': {'confirmed': True, 'arrived': True, 'source_id': 'p0'}}
                elif '状态裁判' in request.system:
                    value = {}
                else:
                    value = {'reply': '洛恩带你来到金枝侧廊，在拱门前停下。'}
                return ReplySnapshot(content=json.dumps(value, ensure_ascii=False), finish_reason='stop')
        client = Travel()
        events = [json.loads(e) async for e in stream_room_turn(store=self.store, provider_client=client, profile_loader=lambda: {},
                   room_id=room, content='走吧', is_disconnected=lambda: asyncio.sleep(0))]
        self.assertNotIn('error', [e['type'] for e in events])
        self.assertEqual(client.plans, 1)
        self.assertEqual(len([e for e in events if e['type'] == 'room_reply']), 1)
        old = self.store.get_room(room)
        place = next(p for p in self.store.get_world(self.world)['locations'] if p['name'] == '金枝侧廊')
        self.assertEqual(old['scene']['location_id'], 'daylight_throne')
        self.assertEqual([c['id'] for c in old['characters']], ['daylight_evelyn'])
        self.assertEqual(self.store.get_character('daylight_lorne')['current_location_id'], place['id'])
        self.assertEqual(self.store.get_character('daylight_evelyn')['current_location_id'], 'daylight_throne')
        self.assertEqual(old['player']['location_id'], place['id'])
        self.assertFalse(self.store.room_is_current(room))
        self.assertNotIn(place['id'], {p['id'] for p in self.store.room_character_context(room, 'daylight_evelyn')['locations']})
        self.store.enter_room(room)
        self.assertEqual([c['id'] for c in self.store.get_room(room)['characters']], ['daylight_evelyn'])

    async def test_queued_room_snapshot_cannot_rewind_player_after_earlier_arrival(self):
        class NoCalls:
            async def response(self, *_):
                raise AssertionError('prepared settlement unexpectedly called provider')

        room = self.room()
        members = ('daylight_lorne', 'daylight_evelyn')
        user = '洛恩跟我去内阁书库。'
        self.store.add_room_message(room, 'user', None, user,
                                    observations={cid: user for cid in members})
        arrival = self.store.room_character_context(room, 'daylight_lorne')
        arrival_text = '洛恩和玩家抵达内阁书库，早钟已经响过，玩家袖口沾了墨迹。'
        new_time = '停昼七年·校时日·早钟之后'
        arrival['scene_plan'] = validate_plan({
            'mode': 'resolve', 'purpose': 'travel', 'source_quote': user,
            'target_id': 'daylight_archive',
        }, arrival)
        arrival['scene_plan']['resolution'] = {
            'confirmed': True, 'arrived': True, 'quote': arrival_text,
        }
        arrival['prepared_patch'] = {'reply': arrival_text, 'patch': location_patch({
            'scene': {'world_time': new_time},
            'player': {'condition': '袖口沾了墨迹', 'visible_traces': ['袖口墨迹']},
            'evidence': {'scene.world_time': {
                'role': 'assistant', 'quote': arrival_text, 'kind': 'occurred',
            }},
        }, arrival)}
        first = self.store.add_room_message(
            room, 'assistant', 'daylight_lorne', arrival_text,
            observations={'daylight_lorne': arrival_text, 'daylight_evelyn': ''},
            settlement_context=arrival,
        )
        # The second reply was generated before the first durable job committed.
        # Its model repeats the entire old snapshot alongside one genuine change.
        frozen = self.store.room_character_context(room, 'daylight_evelyn')
        old_location = next(p for p in frozen['locations'] if p['id'] == frozen['player']['location_id'])
        reply = '伊芙琳信任你能处理好这件事，将余下的茶具收好。'
        old_trust = next(r['trust'] for r in frozen['relationships'] if r['to_user'])
        frozen['scene_plan'] = {'mode': 'stay'}
        frozen['prepared_patch'] = {'reply': reply, 'patch': {
            'scene': {'location_id': frozen['scene']['location_id'],
                      'world_time': frozen['scene']['world_time'], 'physical_state': '收好了茶具'},
            'player': {'location': old_location['name'],
                       'condition': frozen['player']['condition'],
                       'visible_traces': frozen['player']['visible_traces']},
            'relationship': {'trust_delta': 1},
            'event': {'title': '伊芙琳收好茶具', 'details': '伊芙琳将余下的茶具收好。',
                      'visibility': 'private', 'knowledge_sources': {
                          frozen['character']['name']: {
                              'role': 'assistant', 'quote': reply, 'kind': 'witnessed',
                          },
                      }},
            'evidence': {'relationship': {'role': 'assistant', 'quote': reply, 'kind': 'expressed'},
                         'event': {'role': 'assistant', 'quote': reply, 'kind': 'occurred'}},
        }}
        second = self.store.add_room_message(
            room, 'assistant', 'daylight_evelyn', reply,
            observations={cid: reply for cid in members}, settlement_context=frozen,
        )
        await settle_message(self.store, NoCalls(), {}, first['id'])
        before = self.store.get_world(self.world)
        self.assertEqual(before['player']['location_id'], 'daylight_archive')
        self.assertEqual(before['player']['condition'], '袖口沾了墨迹')
        self.assertEqual(before['player']['visible_traces'], ['袖口墨迹'])
        self.assertEqual(before['world']['current_time'], new_time)
        await settle_message(self.store, NoCalls(), {}, second['id'])
        after = self.store.get_world(self.world)
        self.assertEqual(after['world']['current_time'], new_time)
        for key in ('location_id', 'condition', 'visible_traces'):
            self.assertEqual(after['player'][key], before['player'][key])
        context = self.store.room_character_context(room, 'daylight_evelyn')
        self.assertEqual(context['scene']['world_time'], new_time)
        self.assertEqual(context['scene']['physical_state'], '收好了茶具')
        self.assertEqual(next(r['trust'] for r in context['relationships'] if r['to_user']), old_trust + 1)
        with self.store.read() as connection:
            settlement = connection.execute('SELECT player,scene FROM turn_settlements WHERE source_room_message_id=?',
                                            (second['id'],)).fetchone()
            stored_room_time = connection.execute('SELECT world_time FROM scene_rooms WHERE id=?', (room,)).fetchone()[0]
            event_time = connection.execute('''SELECT e.world_time FROM world_events e
                JOIN room_event_sources s ON s.event_id=e.id WHERE s.room_message_id=?''',
                (second['id'],)).fetchone()[0]
        self.assertEqual(json.loads(settlement['player']), {})
        self.assertEqual(json.loads(settlement['scene']), {'physical_state': '收好了茶具'})
        self.assertEqual(stored_room_time, new_time)
        self.assertEqual(event_time, frozen['world']['current_time'])


if __name__ == '__main__':
    unittest.main()
