import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gongjie.ai.providers import ReplySnapshot
from gongjie.cognition.compiler import request_context
from gongjie.domain.patches import PatchValidationError, validate_patch
from gongjie.domain.locations import location_patch
from gongjie.scenes.adjudication import extract_patch, patch_context, resolve_sources, source_catalog
from gongjie.scenes.settlements import settle_message
from gongjie.storage.store import Store


class DecisionTests(unittest.TestCase):
    @staticmethod
    def wire_context():
        return {'world':{'id':'world','current_time':'暮钟前'},'character':{'id':'a','name':'甲'},
                'scene':{'location_id':'hall','world_time':'暮钟前'},
                'player':{'location_id':'hall','commitments':[]},
                'locations':[{'id':'hall','name':'厅堂'}, {'id':'door','name':'门口'}],
                'relationships':[{'to_user':True,'label':'初识','notes':'尚未相识'}],
                'messages':[{'role':'user','content':'我答应明天来。'}]}

    @classmethod
    def repeated_snapshot(cls):
        context = cls.wire_context()
        context['scene'].update(tension=12, active_threads=['仪式尚未开始'],
                                physical_state='甲扶着椅背', emotional_state='平静')
        context['player'].update(condition='袖口干净', visible_traces=['鞋面沾灰'],
                                 commitments=[{'id': 'promise', 'text': '明天来'}])
        raw = {
            'scene': {key: context['scene'][key] for key in
                      ('tension', 'active_threads', 'physical_state', 'emotional_state')},
            'relationship': {'trust_delta': 0, 'desire_delta': 0, 'tension_delta': 0,
                             'label': '初识', 'note': '尚未相识'},
            'player': {'condition': '袖口干净', 'visible_traces': ['鞋面沾灰'],
                       'commitments': {'value': ['明天来'], 'source_id': 'user_0', 'kind': 'expressed'}},
        }
        raw['scene']['world_time'] = {'value': '暮钟前', 'source_id': 'user_0', 'kind': 'occurred'}
        return context, raw

    def test_repeated_snapshot_is_no_change_without_semantic_review(self):
        context, raw = self.repeated_snapshot()
        class Client:
            calls = 0
            async def response(self, request, profile):
                self.calls += 1
                if '硬状态证据核对员' in request.system:
                    raise AssertionError('unchanged state must not incur semantic review')
                return ReplySnapshot(content=json.dumps(raw, ensure_ascii=False), finish_reason='stop')
        client = Client()
        normalized = asyncio.run(extract_patch(client, context, '甲点头。', {}))
        self.assertEqual(normalized, {'scene': {}, 'relationship': {}, 'player': {}, 'evidence': {}})
        self.assertEqual(client.calls, 1)
        self.assertEqual(raw['scene']['tension'], 12)
        self.assertEqual(context['player']['commitments'], [{'id': 'promise', 'text': '明天来'}])

    def test_changed_free_text_still_reaches_semantic_review(self):
        context, raw = self.repeated_snapshot()
        raw['scene']['emotional_state'] = '甲因玩家转述的传闻感到不安，但尚未确认传闻。'
        reviews = []
        class Client:
            async def response(self, request, profile):
                if '硬状态证据核对员' in request.system:
                    reviews.append(json.loads(request.messages[0].content)['proposal'])
                    return ReplySnapshot(content='{"approved":true,"issues":[]}', finish_reason='stop')
                return ReplySnapshot(content=json.dumps(raw, ensure_ascii=False), finish_reason='stop')
        normalized = asyncio.run(extract_patch(Client(), context, '甲听后微微皱眉。', {}))
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0], normalized)
        self.assertEqual(normalized['scene'], {'emotional_state': raw['scene']['emotional_state']})
        self.assertEqual(normalized['relationship'], {})
        self.assertEqual(normalized['player'], {})

    def test_note_only_change_requires_adjacent_evidence_and_retains_semantic_review(self):
        context = self.wire_context()
        body = '甲说：“这次我相信你。”'
        raw = {'relationship':{'trust_delta':0,'desire_delta':0,'tension_delta':0,'note':'甲表达了信任。'}}
        class Client:
            reviews = 0
            requests = []
            async def response(self, request, profile):
                self.requests.append(request)
                if '硬状态证据核对员' in request.system:
                    self.reviews += 1
                    return ReplySnapshot(content='{"approved":true,"issues":[]}')
                return ReplySnapshot(content=json.dumps(raw, ensure_ascii=False))
        client = Client()
        with self.assertRaises(PatchValidationError) as failed:
            asyncio.run(extract_patch(client, context, body, {}))
        self.assertEqual(str(failed.exception), 'evidence_missing')
        self.assertIn('relationship.evidence', ';'.join(failed.exception.issues))
        self.assertNotIn('evidence[', ';'.join(failed.exception.issues))
        self.assertEqual(failed.exception.repair_proposal, raw)
        self.assertEqual(client.reviews, 0)
        raw['relationship']['evidence'] = {'source_id':'assistant_0','kind':'expressed'}
        normalized = asyncio.run(extract_patch(client, context, body, {},
            feedback=';'.join(failed.exception.issues), repair_proposal=failed.exception.repair_proposal))
        self.assertEqual(client.reviews, 1)
        repair_request = client.requests[1]
        self.assertIn('relationship.evidence', repair_request.system)
        self.assertNotIn('evidence[', repair_request.system + repair_request.messages[0].content)
        self.assertEqual(normalized['relationship']['note'], '甲表达了信任。')
        self.assertNotIn('evidence', normalized['relationship'])
        self.assertEqual(normalized['evidence']['relationship']['quote'], source_catalog(context, body)['assistant_0']['quote'])
        request = patch_context(context, body, {})
        self.assertNotIn('本轮角色回复：', request.messages[0].content)
        self.assertNotIn('本轮玩家原文：', request.messages[0].content)
        self.assertEqual(request.messages[0].content.count('我答应明天来。'), 1)
        self.assertIn('"evidence"', request.system)

    def test_only_new_wire_is_accepted_and_program_arrival_needs_no_model_location(self):
        context = self.wire_context()
        body = '甲与你抵达门口。'
        sources = source_catalog(context, body)
        self.assertEqual(validate_patch(resolve_sources({}, sources), context, body)['evidence'], {})
        soft = {'scene':{'physical_state':'放下茶杯'},'player':{'visible_traces':['衣袖湿润']}}
        self.assertEqual(validate_patch(resolve_sources(soft, sources), context, body)['evidence'], {})
        for invalid in ({'evidence':{}}, {'scene':{'location':'厅堂'}}, {'player':{'location_id':'hall'}},
                        {'event':{'world_time':'暮钟前'}}, {'scene':{'world_time':'暮钟前'}},
                        {'player':{'commitments':[]}}):
            with self.subTest(invalid=invalid), self.assertRaises(PatchValidationError):
                resolve_sources(invalid, sources)
        context['scene_plan'] = {'mode':'resolve','purpose':'travel','source_quote':'我答应明天来。',
            'target':{'id':'door','name':'门口'},
            'resolution':{'confirmed':True,'arrived':True,'quote':body}}
        proposed = location_patch(resolve_sources({}, sources), context)
        normalized = validate_patch(proposed, context, body)
        self.assertEqual(normalized['scene']['location_id'], 'door')
        self.assertEqual(normalized['player']['location_id'], 'door')
        self.assertEqual(set(normalized['evidence']), {'scene.location','player.location'})

    def test_decoding_keeps_commitment_event_and_partial_witness_boundaries(self):
        context = self.wire_context()
        context.update(room={'id':'room'}, room_characters=[context['character'], {'id':'b','name':'乙'}],
            observation_sources={'a':{'assistant':'甲递出纸条。暗号是银鹿。'},'b':{'assistant':'甲递出纸条。'}})
        body = '甲递出纸条。暗号是银鹿。'
        sources = source_catalog(context, body)
        event = {'title':'递出纸条','details':body,'visibility':'secret','participants':['甲'],'witnesses':['乙'],
                 'evidence':{'source_id':'assistant_0','kind':'occurred'},
                 'knowledge_sources':{name:{'source_id':'assistant_0','kind':'witnessed'} for name in ('甲','乙')}}
        accepted = validate_patch(resolve_sources({'event':event}, sources), context, body)
        self.assertEqual(accepted['event']['knowledge_sources']['乙']['quote'], '甲递出纸条。')
        invalid = json.loads(json.dumps(event))
        invalid['knowledge_sources']['乙']['source_id'] = 'assistant_1'
        with self.assertRaisesRegex(PatchValidationError, 'knowledge_source_not_observed'):
            validate_patch(resolve_sources({'event':invalid}, sources), context, body)
        invalid = json.loads(json.dumps(event)); invalid['evidence']['kind'] = 'intent'
        with self.assertRaisesRegex(PatchValidationError, 'evidence_not_completed'):
            validate_patch(resolve_sources({'event':invalid}, sources), context, body)
        promise = {'player':{'commitments':{'value':['明天来'],'source_id':'assistant_0','kind':'expressed'}}}
        with self.assertRaisesRegex(PatchValidationError, 'evidence_speaker'):
            validate_patch(resolve_sources(promise, sources), context, body)
        promise['player']['commitments']['source_id'] = 'user_0'
        self.assertEqual(validate_patch(resolve_sources(promise, sources), context, body)['player']['commitments'], ['明天来'])
        with self.assertRaisesRegex(PatchValidationError, 'trust_delta_range'):
            validate_patch(resolve_sources({'relationship':{'trust_delta':6,'evidence':{'source_id':'user_0','kind':'expressed'}}}, sources), context, body)

    def test_repair_uses_previous_source_id_proposal_and_revalidates_the_complete_result(self):
        original = {
            'event': None,
            'scene': {'active_threads': ['等待玩家说明来意'], 'physical_state': '手掌已经空了'},
            'relationship': {'trust_delta': 1, 'note': '洛恩把钥匙借给了玩家。',
                             'evidence': {'source_id': 'assistant_0', 'kind': 'occurred'}},
        }
        issue = 'scene.active_threads 中的“等待玩家说明来意”只是普通问话，不应保存为长期线索。'
        for fix in (True, False):
            with self.subTest(fix=fix), tempfile.TemporaryDirectory() as directory:
                store = Store(Path(directory) / 'repair.db'); store.initialize()
                conv = store.get_or_create_conversation('world_daylight', 'daylight_lorne')
                store.add_message(conv, 'user', '先借给我吧。')
                context = store.context_for_turn(conv)
                body = '洛恩把钥匙交给玩家。\n他问：“你来做什么？”'
                message = store.add_message(conv, 'assistant', body)
                store.update_message(message['id'], content=body, settlement_context=context)

                class Client:
                    extractions = []
                    reviews = []

                    async def response(self, request, profile):
                        if '硬状态证据核对员' in request.system:
                            self.reviews.append(json.loads(request.messages[0].content)['proposal'])
                            approved = fix and len(self.reviews) > 1
                            value = {'approved': approved, 'issues': [] if approved else [issue]}
                        else:
                            self.extractions.append(request)
                            value = json.loads(json.dumps(original))
                            if fix and len(self.extractions) > 1:
                                value['scene']['active_threads'] = []
                        return ReplySnapshot(content=json.dumps(value, ensure_ascii=False), finish_reason='stop')

                client = Client()
                result = asyncio.run(settle_message(store, client, {}, message['id']))
                self.assertEqual(len(client.extractions), 2)
                self.assertEqual(len(client.reviews), 2)
                repair_request = client.extractions[1]
                payload = json.loads(repair_request.messages[0].content.split('待修复的原始提案与具体问题：\n')[1])
                self.assertEqual(payload['proposal'], original)
                self.assertIn(issue, payload['issues'])
                self.assertEqual(payload['proposal']['relationship']['evidence']['source_id'], 'assistant_0')
                self.assertNotIn('quote', payload['proposal']['relationship']['evidence'])
                self.assertIn('正确且有原文支持的字段原样保留', repair_request.system)
                self.assertNotIn('待修复的原始提案与具体问题', client.extractions[0].messages[0].content)
                self.assertFalse(store.pending_settlements('world_daylight'))
                after = store.context_for_turn(conv)
                if fix:
                    self.assertNotEqual(result['result']['settlement'].get('decision', {}).get('outcome'), 'rejected')
                    self.assertEqual(after['scene']['active_threads'], [])
                    self.assertEqual(after['scene']['physical_state'], original['scene']['physical_state'])
                    self.assertEqual(after['relationships'][0]['trust'], context['relationships'][0]['trust'] + 1)
                    self.assertEqual(client.reviews[1]['evidence']['relationship']['quote'], '洛恩把钥匙交给玩家。')
                else:
                    self.assertEqual(result['result']['settlement']['decision']['outcome'], 'rejected')
                    self.assertEqual(after['scene'], context['scene'])
                    self.assertEqual(after['relationships'], context['relationships'])

    def test_references_are_bound_to_this_turn_and_player_speaker(self):
        context = {'messages': [{'role': 'user', 'content': '我答应明天来。'}]}
        sources = source_catalog(context, '他点头。\n等你。')
        value = resolve_sources({'player': {'commitments': {'value':['我答应明天来。'], 'source_id': 'user_0', 'kind': 'expressed'}}}, sources)
        self.assertEqual(value['evidence']['player.commitments']['quote'], '我答应明天来。')
        self.assertEqual(value['evidence']['player.commitments']['role'], 'user')
        for citation in ({'source_id': 'old_0', 'kind': 'occurred'}, {'role': 'user', 'quote': '我答应明天来。', 'kind': 'expressed'}):
            with self.assertRaises(PatchValidationError):
                resolve_sources({'event': {'evidence': citation}}, sources)

    def test_rejected_round_is_terminal_persistent_and_does_not_mutate_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'world.db'); store.initialize()
            conv = store.get_or_create_conversation('world_daylight', 'daylight_lorne')
            store.add_message(conv, 'user', '随便看看。')
            context = store.context_for_turn(conv)
            message = store.add_message(conv, 'assistant', '他声称门外换了守卫。')
            store.update_message(message['id'], content=message['content'], status='complete', settlement_context=context)
            class InvalidClient:
                calls = 0
                async def response(self, request, profile):
                    self.calls += 1
                    return ReplySnapshot(content='{"scene":{"location":"不存在的地点"}}')
            client = InvalidClient()
            result = asyncio.run(settle_message(store, client, {}, message['id']))
            self.assertEqual(result['result']['settlement']['decision']['outcome'], 'rejected')
            self.assertEqual(client.calls, 2)
            self.assertFalse(store.pending_settlements('world_daylight'))
            reopened = Store(store.path); reopened.initialize()
            new_context = reopened.context_for_turn(conv)
            self.assertEqual(new_context['scene'], context['scene'])
            self.assertEqual(new_context['relationships'], context['relationships'])
            self.assertEqual(new_context['knowledge'], context['knowledge'])
            self.assertEqual(request_context(new_context, {}).messages[-1].content, message['content'])
            self.assertIn('状态变化未确认', request_context(new_context, {}).system)
            again = asyncio.run(settle_message(reopened, client, {}, message['id']))
            self.assertEqual(again['result']['settlement']['id'], result['result']['settlement']['id'])
            self.assertEqual(client.calls, 2)
            with reopened.world_operation('world_daylight'):
                reopened.add_message(conv, 'user', '继续聊。')
            self.assertEqual(len(reopened.get_conversation(conv)['settlements']), 1)

    def test_room_rejection_keeps_fifo_and_allows_next_character(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'world.db'); store.initialize()
            room = store.create_room('world_daylight', '同场', 'daylight_throne', ['daylight_lorne', 'daylight_evelyn'])['room']['id']
            first_context = store.room_character_context(room, 'daylight_lorne')
            first_views = {c['id']: '他点头。' for c in first_context['room_characters']}
            first_context['observation_sources'] = {cid: {'assistant': text} for cid, text in first_views.items()}
            first = store.add_room_message(room, 'assistant', 'daylight_lorne', '他点头。', settlement_context=first_context, observations=first_views)
            second_context = store.room_character_context(room, 'daylight_evelyn')
            second_views = {c['id']: '她微笑。' for c in second_context['room_characters']}
            second_context['observation_sources'] = {cid: {'assistant': text} for cid, text in second_views.items()}
            second = store.add_room_message(room, 'assistant', 'daylight_evelyn', '她微笑。', settlement_context=second_context, observations=second_views)
            class Client:
                async def response(self, request, profile): return ReplySnapshot(content='{"unknown":true}')
            with self.assertRaises(ValueError): asyncio.run(settle_message(store, Client(), {}, second['id']))
            asyncio.run(settle_message(store, Client(), {}, first['id']))
            asyncio.run(settle_message(store, Client(), {}, second['id']))
            self.assertFalse(store.pending_settlements('world_daylight'))
            self.assertEqual(len(store.get_room(room)['settlements']), 2)
            self.assertTrue(all(m['settlement_outcome'] == 'rejected' for m in store.room_character_context(room, 'daylight_lorne')['messages']))

    def test_database_failure_never_turns_into_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'world.db'); store.initialize()
            conv = store.get_or_create_conversation('world_daylight', 'daylight_lorne')
            context = store.context_for_turn(conv)
            message = store.add_message(conv, 'assistant', '他点头。')
            store.update_message(message['id'], content=message['content'], status='complete', settlement_context=context)
            class EmptyClient:
                async def response(self, request, profile): return ReplySnapshot(content='{}')
            with patch.object(store, 'apply_world_patch', side_effect=OSError('disk failed')):
                with self.assertRaises(OSError): asyncio.run(settle_message(store, EmptyClient(), {}, message['id']))
            self.assertEqual(len(store.pending_settlements('world_daylight')), 1)
            self.assertFalse(store.get_conversation(conv)['settlements'])
