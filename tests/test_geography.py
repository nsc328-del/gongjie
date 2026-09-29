import asyncio
import json
import tempfile
import unittest
import httpx
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from gongjie.ai.providers import ReplySnapshot, ProviderClient, ProviderError, RequestContext
from gongjie.cognition.compiler import request_context
from gongjie.cognition.pulse import character_pulse_context
from gongjie.domain.locations import validate_plan, validate_resolution
from gongjie.domain.patches import PatchValidationError, validate_patch
from gongjie.scenes.turns import stream_turn
from gongjie.scenes.rooms import stream_room_turn
from gongjie.scenes.settlements import recover_settlements
from gongjie.storage.store import Store


class PlacesClient:
    def __init__(self, plan, body='塞维安指向窗边。', *, arrived=False, confirmed=True, patch_value=None):
        self.plan = plan
        self.body = body
        self.arrived = arrived
        self.confirmed = confirmed
        self.patch_value = patch_value or {}
        self.requests = []
        self.reject_body_once = False
        self.body_reviews = 0

    async def response(self, request, profile):
        self.requests.append(request)
        if '你是同场观察编译员' in request.system:
            data = json.loads(request.messages[0].content)
            value = {m['id']:{'quotes':[data['content']]} for m in data['members']}
        elif '你是同场观察核对员' in request.system:
            value = {'approved': True, 'issues': []}
        elif '你是场景规划员' in request.system:
            value = self.plan
        elif '你是地点设定核对员' in request.system:
            value = {'approved': True, 'issues': []}
        elif '你是转场正文核对员' in request.system:
            self.body_reviews += 1
            ok = not (self.reject_body_once and self.body_reviews == 1)
            value = {'approved': ok, 'issues': [] if ok else ['正文擅自进入核心'],
                     'resolution': {'confirmed': self.confirmed, 'arrived': self.arrived, 'source_id': 'p0' if self.confirmed else ''}}
        elif '状态裁判' in request.system:
            value = self.patch_value
        elif '硬状态证据核对员' in request.system:
            value = {'approved': True, 'issues': []}
        elif '输出必须是单个 JSON 对象' in request.system:
            value = {'reply': self.body}
        else:
            return ReplySnapshot(content=self.body, finish_reason='stop')
        return ReplySnapshot(content=json.dumps(value, ensure_ascii=False), finish_reason='stop')

    async def stream(self, request, profile):
        self.requests.append(request)
        yield ReplySnapshot(content=self.body, finish_reason='stop')


class GeographyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'world.db')
        self.store.initialize()
        self.world = 'world_daylight'
        self.conv = self.store.get_or_create_conversation(self.world, 'daylight_severin')

    def new_plan(self, purpose='travel', quote='走吧'):
        return {'mode': 'resolve', 'purpose': purpose, 'source_quote': quote, 'target_id': None,
                'new_location': {'name': '外围检修通道', 'parent_id': 'daylight_gate', 'description': '外廊旁的一段窄通道。'}}

    async def turn(self, client, content='走吧', *, continuation=False):
        return [json.loads(e) async for e in stream_turn(store=self.store, provider_client=client, profile_loader=lambda: {},
                conversation_id=self.conv, content=content, continuation=continuation, is_disconnected=lambda: asyncio.sleep(0))]

    async def test_mention_information_then_arrival_and_save_restore(self):
        initial = self.store.save_world(self.world, '出发前')
        for mode in ('stay', 'mention'):
            events = await self.turn(PlacesClient({'mode': mode}), '窗边有书铺的方向吗？')
            self.assertEqual(events[-1]['type'], 'done')
            self.assertEqual(len(self.store.get_world(self.world)['locations']), 6)
        info = PlacesClient(self.new_plan('information', '那里怎么走'), '塞维安说明外围检修通道在日轮外廊旁。')
        await self.turn(info, '那里怎么走')
        world = self.store.get_world(self.world)
        place = next(p for p in world['locations'] if p['name'] == '外围检修通道')
        self.assertEqual(world['player']['location_id'], 'daylight_archive')
        self.assertEqual(len(world['locations']), 7)
        travel = PlacesClient({'mode': 'resolve', 'purpose': 'travel', 'source_quote': '走吧', 'target_id': place['id'], 'new_location': None},
                              '塞维安带你抵达外围检修通道，在门内停下。', arrived=True)
        events = await self.turn(travel)
        self.assertIn('world_patch', [e['type'] for e in events])
        world = self.store.get_world(self.world)
        self.assertEqual(world['player']['location_id'], place['id'])
        self.assertEqual(self.store.context_for_turn(self.conv)['scene']['location_id'], place['id'])
        source = events[-1]['message_id']
        context = self.store.settlement_job(source)['context']
        self.assertTrue(context['scene_plan']['resolution']['arrived'])
        current = self.store.save_world(self.world, '抵达后')
        self.store.load_world_save(self.world, initial['id'])
        self.assertEqual(len(self.store.get_world(self.world)['locations']), 6)
        self.store.load_world_save(self.world, current['id'])
        self.assertEqual(self.store.get_world(self.world)['player']['location_id'], place['id'])
        reopened = Store(self.store.path); reopened.initialize()
        self.assertEqual(reopened.context_for_turn(self.conv)['scene']['location_id'], place['id'])
        # A different character and its autonomous pulse must not get the new private geography.
        other = self.store.get_or_create_conversation(self.world, 'daylight_lorne')
        other_context = self.store.context_for_turn(other)
        self.assertNotIn(place['id'], [p['id'] for p in other_context['locations']])
        self.assertNotIn(place['name'], request_context(other_context, {}).system)
        sync = self.store.cognition_sync_context(self.world)
        lorne = next(c for c in sync['characters'] if c['character']['id'] == 'daylight_lorne')
        self.assertNotIn(place['name'], character_pulse_context(lorne, sync, {}, 2000).messages[0].content)
        self.store.reset_world(self.world, world['world']['name'])
        self.assertEqual(len(self.store.get_world(self.world)['locations']), 6)

    async def test_no_ghost_location_on_failed_commit_and_recovery_is_idempotent(self):
        client = PlacesClient(self.new_plan(), '塞维安带你抵达外围检修通道。', arrived=True)
        with patch.object(self.store, '_insert_turn_settlement', side_effect=OSError('disk error')):
            events = await self.turn(client)
        self.assertIn('patch_skipped', [e['type'] for e in events])
        self.assertEqual(len(self.store.get_world(self.world)['locations']), 6)
        self.assertEqual(self.store.get_world(self.world)['player']['location_id'], 'daylight_archive')
        planning_calls = len([r for r in client.requests if '你是场景规划员' in r.system])
        result = await recover_settlements(self.store, client, {}, self.world)
        self.assertFalse(result['pending_settlements'])
        await recover_settlements(self.store, client, {}, self.world)
        self.assertEqual(len(self.store.get_world(self.world)['locations']), 7)
        self.assertEqual(planning_calls, len([r for r in client.requests if '你是场景规划员' in r.system]))

    async def test_rejected_or_unconfirmed_reply_never_creates_geography(self):
        blocked = PlacesClient(self.new_plan(), '塞维安摇头：现在不去。', confirmed=False)
        await self.turn(blocked)
        self.assertEqual(len(self.store.get_world(self.world)['locations']), 6)
        invalid = PlacesClient(self.new_plan(), '塞维安带你抵达外围检修通道。', arrived=True, patch_value={'unexpected': True})
        events = await self.turn(invalid)
        self.assertIn('error', [e['type'] for e in events])
        self.assertNotIn('delta', [e['type'] for e in events])
        self.assertFalse(self.store.pending_settlements(self.world))
        self.assertEqual(len(self.store.get_world(self.world)['locations']), 6)

    async def test_body_review_precedes_publication_and_room_uses_same_path(self):
        client = PlacesClient(self.new_plan(), '塞维安带你抵达外围检修通道。', arrived=True)
        client.reject_body_once = True
        stream = stream_turn(store=self.store, provider_client=client, profile_loader=lambda: {}, conversation_id=self.conv,
                             content='走吧', continuation=False, is_disconnected=lambda: asyncio.sleep(0))
        async for encoded in stream:
            event = json.loads(encoded)
            if event['type'] == 'delta':
                self.assertEqual(client.body_reviews, 2)
                self.assertEqual(len(self.store.get_world(self.world)['locations']), 6)
        room = self.store.create_room(self.world, '检修前', 'daylight_gate', ['daylight_lorne', 'daylight_evelyn'])['room']['id']
        plan = self.new_plan(); plan['new_location']['name'] = '外廊侧室'
        client = PlacesClient(plan, '洛恩带你进入外廊侧室，在入口停下。', arrived=True)
        events = [json.loads(e) async for e in stream_room_turn(store=self.store, provider_client=client, profile_loader=lambda: {}, room_id=room,
                     content='走吧', is_disconnected=lambda: asyncio.sleep(0))]
        self.assertNotIn('error', [e['type'] for e in events])
        place = next(p for p in self.store.get_world(self.world)['locations'] if p['name'] == '外廊侧室')
        self.assertEqual(self.store.get_room(room)['scene']['location_id'], 'daylight_gate')
        self.assertEqual(self.store.get_character('daylight_lorne')['current_location_id'], place['id'])
        self.assertEqual(self.store.get_character('daylight_evelyn')['current_location_id'], 'daylight_gate')
        self.assertEqual(len([e for e in events if e['type'] == 'room_reply']), 1)

    async def test_plan_boundaries_source_alias_and_unauthorized_arrival(self):
        self.store.add_message(self.conv, 'user', '走吧')
        context = self.store.context_for_turn(self.conv)
        for transform in (
            lambda p: p.update(source_quote='伪造原文'),
            lambda p: p['new_location'].update(parent_id='other_world'),
            lambda p: p['new_location'].update(permission='核心通行权'),
            lambda p: p.update(mode=[]),
        ):
            value = self.new_plan(); transform(value)
            with self.assertRaises(PatchValidationError): validate_plan(value, context)
        with self.assertRaises(PatchValidationError):
            validate_plan(self.new_plan(), {**context, 'continuation': True})
        same = self.new_plan(); same['new_location']['name'] = ' 日轮外廊 '
        resolved = validate_plan(same, context)
        self.assertEqual(resolved['target']['id'], 'daylight_gate')
        self.assertNotIn('create', resolved)
        root = self.new_plan(); root['new_location']['parent_id'] = self.world
        self.assertTrue(validate_plan(root, {**context, 'locations': []})['create'])
        plan = validate_plan(self.new_plan('information'), context)
        with self.assertRaises(PatchValidationError):
            validate_resolution({'confirmed': True, 'arrived': True, 'quote': '进入。'}, plan, '进入。')
        with self.assertRaises(PatchValidationError) as error:
            validate_patch({'relationship': {'tension_delta': 2}}, context, '他警觉地看着你。')
        self.assertIn('evidence["relationship"]', error.exception.issues[0])

    async def test_hidden_duplicate_is_stopped_before_body_publication(self):
        await self.turn(PlacesClient(self.new_plan(), '塞维安带你抵达外围检修通道。', arrived=True))
        other = self.store.get_or_create_conversation(self.world, 'daylight_lorne')
        client = PlacesClient(self.new_plan(), '洛恩带你抵达另一处外围检修通道。', arrived=True)
        events = [json.loads(e) async for e in stream_turn(store=self.store, provider_client=client, profile_loader=lambda: {},
            conversation_id=other, content='走吧', continuation=False, is_disconnected=lambda: asyncio.sleep(0))]
        self.assertIn('error', [e['type'] for e in events])
        self.assertNotIn('delta', [e['type'] for e in events])
        self.assertFalse(self.store.pending_settlements(self.world))
        self.assertEqual(len(self.store.get_world(self.world)['locations']), 7)

    async def test_told_location_reuses_identity_without_registry_details_and_survives_save(self):
        plan = self.new_plan('information','那里怎么走')
        plan['new_location']['description'] = '通道内部墙面有铜色三角纹。'
        await self.turn(PlacesClient(plan,'塞维安介绍外围检修通道。'),'那里怎么走')
        place = next(p for p in self.store.get_world(self.world)['locations'] if p['name']=='外围检修通道')
        self.conv = self.store.get_or_create_conversation(self.world,'daylight_lorne')
        self.assertNotIn(place['id'],[p['id'] for p in self.store.context_for_turn(self.conv)['locations']])
        told = PlacesClient({'mode':'stay'},'洛恩点头，记住了你说的名字。')
        events = await self.turn(told,'我告诉你，外围检修通道在日轮外廊旁。')
        self.assertIn('world_patch',[e['type'] for e in events])
        context = self.store.context_for_turn(self.conv)
        known = next(p for p in context['locations'] if p['id']==place['id'])
        self.assertIn('玩家提及',known['description'])
        self.assertNotIn('铜色三角纹',json.dumps(context,ensure_ascii=False))
        appointment = PlacesClient({'mode':'resolve','purpose':'appointment','source_quote':'约在外围检修通道见面',
                                    'target_id':place['id']},'洛恩答应约在外围检修通道入口见面。')
        events = await self.turn(appointment,'那我们约在外围检修通道见面。')
        self.assertNotIn('error',[e['type'] for e in events])
        self.assertEqual(len(self.store.get_world(self.world)['locations']),7)
        self.assertNotIn('铜色三角纹', '\n'.join(r.system+'\n'+r.messages[0].content for r in appointment.requests))
        saved = self.store.save_world(self.world,'已告知去向')
        self.store.load_world_save(self.world,saved['id'])
        self.assertIn(place['id'],[p['id'] for p in self.store.context_for_turn(self.conv)['locations']])
        stranger = self.store.get_or_create_conversation(self.world,'daylight_evelyn')
        self.assertNotIn(place['id'],[p['id'] for p in self.store.context_for_turn(stranger)['locations']])
        # Physically joining a new place grants current-place familiarity only.
        room = self.store.create_room(self.world,'当面碰头',place['id'],['daylight_lorne','daylight_evelyn'])['room']['id']
        context = self.store.room_character_context(room,'daylight_evelyn')
        self.assertIn(place['id'],[p['id'] for p in context['locations']])
        self.assertNotIn('铜色三角纹',json.dumps(context,ensure_ascii=False))
        validate_patch({'scene':{'location_id':place['id']}},context,'她站在入口。')

    async def test_existing_checkpoints_migrate_without_losing_progress(self):
        save = self.store.save_world(self.world, '旧格式')
        with self.store.transaction() as c:
            payload = json.loads(c.execute('SELECT payload FROM world_saves WHERE id=?', (save['id'],)).fetchone()[0])
            payload['version'] = 15
            for place in payload['tables']['locations']:
                place.pop('geography')
            c.execute('UPDATE world_saves SET payload=? WHERE id=?', (json.dumps(payload, ensure_ascii=False), save['id']))
        self.store.initialize()
        self.store.load_world_save(self.world, save['id'])
        self.assertEqual(len(self.store.get_world(self.world)['locations']), 6)
        self.assertEqual(self.store.get_world(self.world)['player']['location_id'], 'daylight_archive')

    async def test_disconnect_keeps_worker_and_manual_recovery_joins_it(self):
        started, release = asyncio.Event(), asyncio.Event()
        class Client(PlacesClient):
            patch_calls = 0
            async def response(self, request, profile):
                if '状态裁判' in request.system:
                    self.patch_calls += 1
                    started.set()
                    await release.wait()
                return await super().response(request, profile)
        client = Client({'mode': 'stay'})
        stream = stream_turn(store=self.store, provider_client=client, profile_loader=lambda: {}, conversation_id=self.conv,
                             content='你好', continuation=False, is_disconnected=lambda: asyncio.sleep(0))
        async for raw in stream:
            if json.loads(raw)['type'] == 'assistant_complete':
                await started.wait()
                await stream.aclose()
                break
        self.assertTrue(self.store.pending_settlements(self.world))
        recovery = asyncio.create_task(recover_settlements(self.store, client, {}, self.world))
        await asyncio.sleep(0)
        release.set()
        self.assertFalse((await recovery)['pending_settlements'])
        self.assertEqual(client.patch_calls, 1)

    async def test_provider_truncation_is_never_a_semantic_rejection(self):
        original = httpx.AsyncClient
        profile = {'protocol':'openai-compatible','model':'test','base_url':'https://example.test','path':'chat','api_key':'test'}
        for body in ('', '{"approved":'):
            transport = httpx.MockTransport(lambda req: httpx.Response(200, json={'choices':[{'finish_reason':'length','message':{'content':body}}]}))
            with patch('gongjie.ai.providers.httpx.AsyncClient', side_effect=lambda **kw: original(transport=transport, **kw)):
                with self.assertRaises(ProviderError) as error:
                    await ProviderClient().response(RequestContext('', []), profile)
                self.assertEqual(error.exception.code, 'response_incomplete')
