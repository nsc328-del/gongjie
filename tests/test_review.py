from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from gongjie.ai.providers import ProviderClient, ProviderError, RequestContext, ReplySnapshot
from gongjie.api.schemas import WorldInput, CharacterInput, RoomInput, ProviderInput
from gongjie.cognition.compiler import request_context, opening_request_context, parse_room_reply
from gongjie.scenes.turns import stream_turn
from gongjie.scenes.rooms import stream_room_turn
from gongjie.storage.store import Store


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'review.db')
        self.store.initialize()
        self.world = 'world_daylight'
        self.conv = self.store.get_or_create_conversation(self.world, 'daylight_lorne')

    def tearDown(self):
        self.temp.cleanup()

    def run_turn(self, client, profile_loader=lambda: {}):
        async def run():
            return [json.loads(e) async for e in stream_turn(
                store=self.store, provider_client=client, profile_loader=profile_loader,
                conversation_id=self.conv, content='你好。', continuation=False,
                is_disconnected=lambda: asyncio.sleep(0),
            )]
        return asyncio.run(run())

    def test_broken_transport_never_settles_partial_reply(self):
        real_client = httpx.AsyncClient
        def respond(request):
            if not json.loads(request.content).get('stream'):
                return httpx.Response(200, json={'choices':[{'message':{'content':'{"mode":"stay"}'},'finish_reason':'stop'}]})
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"没有写完的正文"}}]}\n\n')
        transport = httpx.MockTransport(respond)
        profile = {'protocol': 'openai-compatible', 'model': 'test', 'base_url': 'https://example.test', 'path': '/chat', 'api_key': 'test'}
        with patch('gongjie.ai.providers.httpx.AsyncClient', side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)):
            events = self.run_turn(ProviderClient(), lambda: profile)
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(events[-1]['error']['code'], 'stream_incomplete')
        saved = self.store.get_conversation(self.conv)
        self.assertEqual(saved['messages'][-1]['status'], 'error')
        self.assertEqual(saved['messages'][-1]['content'], '没有写完的正文')
        self.assertFalse(saved['settlements'])
        self.assertFalse(saved['pending_settlements'])

    def test_anthropic_completion_preserves_start_usage(self):
        chunks = [
            {'type': 'message_start', 'message': {'model': 'test', 'usage': {'input_tokens': 91, 'output_tokens': 0, 'cache_read_input_tokens': 30}}},
            {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': '完成'}},
            {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 7}},
            {'type': 'message_stop'},
        ]
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)))
        real_client = httpx.AsyncClient
        profile = {'protocol': 'anthropic-messages', 'model': 'test', 'base_url': 'https://example.test', 'path': '/messages', 'api_key': 'test'}
        async def run():
            return [s async for s in ProviderClient().stream(RequestContext('', []), profile)]
        with patch('gongjie.ai.providers.httpx.AsyncClient', side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)):
            snapshots = asyncio.run(run())
        self.assertEqual(snapshots[-1].content, '完成')
        self.assertEqual(snapshots[-1].usage.input_tokens, 91)
        self.assertEqual(snapshots[-1].usage.output_tokens, 7)
        self.assertEqual(snapshots[-1].usage.cached_input_tokens, 30)

    def test_missing_profile_does_not_save_phantom_player_turns(self):
        def missing():
            raise ProviderError('auth', '先连接模型')
        self.run_turn(object(), missing)
        self.assertFalse(self.store.get_conversation(self.conv)['messages'])
        room = self.store.create_room(self.world, '会面', 'daylight_throne', ['daylight_lorne'])
        async def run():
            return [e async for e in stream_room_turn(store=self.store, provider_client=object(), profile_loader=missing,
                room_id=room['room']['id'], content='你好', is_disconnected=lambda: asyncio.sleep(0))]
        asyncio.run(run())
        self.assertFalse(self.store.get_room(room['room']['id'])['messages'])

    def test_closing_stream_cleans_up_and_cancelled_text_is_not_reused(self):
        class Client:
            closed = False
            async def response(self, context, profile):
                return ReplySnapshot(content='{"mode":"stay"}')
            async def stream(self, context, profile):
                try:
                    yield ReplySnapshot(content='未完成动作')
                finally:
                    self.closed = True
        client = Client()
        async def run():
            stream = stream_turn(store=self.store, provider_client=client, profile_loader=lambda: {}, conversation_id=self.conv,
                content='你好', continuation=False, is_disconnected=lambda: asyncio.sleep(0))
            async for raw in stream:
                if json.loads(raw)['type'] == 'delta':
                    await stream.aclose()
                    break
        asyncio.run(run())
        self.assertTrue(client.closed)
        self.assertEqual(self.store.get_conversation(self.conv)['messages'][-1]['status'], 'cancelled')
        context = self.store.context_for_turn(self.conv)
        self.assertNotIn('未完成动作', str(request_context(context, {}).messages))
        self.assertNotIn('未完成动作', opening_request_context(context, {}, reentry=True).system)
        self.assertFalse(self.store._active_worlds)

    def test_current_clock_survives_switching_scenes_and_empty_states_clear(self):
        reply = self.store.add_message(self.conv, 'assistant', '洛恩·维瑟尔安静坐下。')
        self.store.apply_world_patch(self.conv, reply['id'], {'event': None, 'scene': {'physical_state': '衣袖微湿', 'emotional_state': '紧张', 'world_time': self.store.get_world(self.world)['world']['current_time']}})
        historical_time = self.store.get_world(self.world)['world']['current_time']
        # Another scene can advance the shared clock without touching this scene's snapshot.
        with self.store.transaction() as c:
            c.execute('UPDATE worlds SET current_time=? WHERE id=?', ('次日午后', self.world))
        context = self.store.context_for_turn(self.conv)
        self.assertEqual(context['scene']['world_time'], '次日午后')
        self.assertEqual(self.store.get_conversation(self.conv)['scene']['world_time'], '次日午后')
        self.assertEqual(context['previous_scene']['world_time'], historical_time)
        reply = self.store.add_message(self.conv, 'assistant', '洛恩·维瑟尔点头。')
        self.store.apply_world_patch(self.conv, reply['id'], {'event': None, 'scene': {'physical_state': '', 'emotional_state': ''}})
        self.assertEqual(self.store.get_world(self.world)['world']['current_time'], '次日午后')
        self.assertEqual(self.store.get_conversation(self.conv)['scene']['physical_state'], '')
        self.assertEqual(self.store.get_conversation(self.conv)['scene']['emotional_state'], '')
        room = self.store.create_room(self.world, '会面', 'daylight_throne', ['daylight_lorne'])
        with self.store.transaction() as c:
            c.execute('UPDATE worlds SET current_time=? WHERE id=?', ('次日黄昏', self.world))
        self.assertEqual(self.store.get_room(room['room']['id'])['scene']['world_time'], '次日黄昏')
        self.store.update_world(self.world, {'current_time': '次日夜晚'})
        self.assertEqual(self.store.get_conversation(self.conv)['scene']['world_time'], '次日夜晚')
        self.assertEqual(self.store.get_room(room['room']['id'])['scene']['world_time'], '次日夜晚')

    def test_event_time_failure_gives_concrete_feedback_and_recovers(self):
        class Client:
            attempts = 0
            feedback = ''
            async def stream(self, context, profile):
                yield ReplySnapshot(content='洛恩·维瑟尔点头。', finish_reason='stop')
            async def response(self, context, profile):
                if '你是场景规划员' in context.system:
                    return ReplySnapshot(content='{"mode":"stay"}')
                if '硬状态证据核对员' in context.system:
                    return ReplySnapshot(content='{"approved":true}')
                self.attempts += 1
                self.feedback = context.system
                return ReplySnapshot(content=json.dumps({
                    'event': {'title':'点头', 'details':'洛恩·维瑟尔点头。', 'visibility':'private', 'participants':['洛恩·维瑟尔'],
                        'witnesses':['洛恩·维瑟尔'], **({'world_time':'错误的时刻'} if self.attempts == 1 else {}),
                        'evidence': {'source_id':'assistant_0', 'kind':'occurred'},
                        'knowledge_sources': {'洛恩·维瑟尔': {'source_id':'assistant_0', 'kind':'witnessed'}}},
                }, ensure_ascii=False))
        client = Client()
        events = self.run_turn(client)
        self.assertIn('world_patch', [e['type'] for e in events])
        self.assertEqual(client.attempts, 2)
        self.assertIn('event 不填写 world_time', client.feedback)
        self.assertFalse(self.store.pending_settlements(self.world))

    def test_bad_room_json_is_an_error_not_silence_and_names_cannot_be_blank(self):
        for value in ({}, {'silent': 'false'}, {'reply': {}}, {'reply': ''}, {'reply': '答复', 'silent': True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_room_reply(json.dumps(value))
        self.assertEqual(parse_room_reply('{"silent":true}'), {'silent': True})
        for model, kwargs in ((WorldInput, {}), (CharacterInput, {'age':23}), (RoomInput, {})):
            with self.subTest(model=model), self.assertRaises(ValidationError):
                model(name='   ', **kwargs)
        self.assertEqual(WorldInput(name=' 新世界 ').name, '新世界')

    def test_empty_world_advances_and_unavailable_character_can_return(self):
        from gongjie.cognition.pulse import advance_world_pulse
        world = self.store.create_world({'name':'新世界','current_time':'第一日清晨'})['world']['id']
        class Client:
            async def response(self, request, profile):
                if '独立行动决定' in request.system:
                    payload = json.loads(request.messages[0].content)
                    return ReplySnapshot(content=json.dumps({'participation':'active','active_reason':'visible_change',
                        'active_source':'availability','availability':'reachable','location':payload['角色']['当前地点'],
                        'surface_state':'现在可以见面。','encounter_hook':'房门打开了。'},ensure_ascii=False))
                return ReplySnapshot(content='{"world_time":"第二日清晨","narration":"天亮了。","player_condition_action":"clear","retained_trace_indices":[]}')
        with self.assertRaisesRegex(ValueError, "还没有可以行动的角色"):
            asyncio.run(advance_world_pulse(store=self.store, client=Client(), profile={}, world_id=world))
        self.assertEqual(self.store.get_world(world)['world']['current_time'], '第一日清晨')
        with self.store.transaction() as c:
            c.execute("UPDATE character_world_states SET availability='unavailable' WHERE character_id='daylight_lorne'")
        asyncio.run(advance_world_pulse(store=self.store, client=Client(), profile={}, world_id=self.world))
        shenyan = next(c for c in self.store.get_world(self.world)['characters'] if c['id']=='daylight_lorne')
        self.assertEqual(shenyan['availability'], 'reachable')

    def test_secret_save_is_atomic_and_never_replaces_corrupt_file(self):
        from gongjie.ops.secrets import SecretStore
        path = Path(self.temp.name) / 'keys.json'
        secrets = SecretStore(path)
        secrets.set('one', 'test-one')
        before = path.read_bytes()
        with patch('gongjie.ops.secrets.os.replace', side_effect=OSError('模拟保存失败')):
            with self.assertRaises(OSError):
                secrets.set('two', 'test-two')
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        path.write_text('{bad json')
        self.assertFalse(secrets.has('one'))
        with self.assertRaises(ValueError):
            secrets.set('two', 'test-two')
        self.assertEqual(path.read_text(), '{bad json')
        self.assertEqual(list(path.parent.glob('keys.json.*')), [])
