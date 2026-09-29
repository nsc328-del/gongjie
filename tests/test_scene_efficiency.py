import asyncio
import json
import tempfile
import unittest
from collections import Counter
from copy import deepcopy
from pathlib import Path

from gongjie.ai.providers import ReplySnapshot
from gongjie.scenes.turns import stream_turn
from gongjie.storage.store import Store


class SceneClient:
    """Script only provider decisions; run the real scene and storage pipeline."""

    def __init__(self, purpose, user, *, create=False, reject_proposal=False, reject_first_body=False):
        self.user = user
        self.purpose = purpose
        self.create = create
        self.reject_proposal = reject_proposal
        self.reject_first_body = reject_first_body
        self.calls = []
        self.requests = []
        self.target_name = '外廊休息间' if create else '金枝厅'
        self.body = (f'塞维安带你抵达{self.target_name}，在门前停下。' if purpose == 'travel'
                     else f'塞维安说：“{self.target_name}就在宫内，我们现在仍留在书库。”')
        self.rejected_body = '塞维安擅自带你离开书库，直接进入上锁的内室。'

    async def response(self, request, profile):
        self.requests.append(request)
        system = request.system
        if '你是场景规划员' in system:
            kind = 'plan'
            value = {'mode': 'resolve', 'purpose': self.purpose, 'source_quote': self.user}
            if self.create:
                value['new_location'] = {'name': self.target_name, 'parent_id': 'daylight_gate',
                                         'description': '日轮外廊旁供人短暂歇脚的小房间。'}
            else:
                value['target_id'] = 'daylight_throne'
        elif '你是地点设定核对员' in system:
            kind = 'plan_review'
            value = {'approved': True, 'issues': []}
        elif '你是转场正文核对员' in system:
            kind = 'body_review'
            approved = not (self.reject_first_body and kind not in self.calls)
            value = {'approved': approved,
                     'issues': [] if approved else ['玩家只是询问地点，正文擅自替玩家出发并获得内室权限'],
                     'resolution': {'confirmed': approved, 'arrived': approved and self.purpose == 'travel',
                                    'source_id': 'p0' if approved else ''}}
        elif '状态裁判' in system:
            kind = 'proposal'
            value = {'relationship': {'trust_delta': 1,
                                     'evidence': {'source_id': 'assistant_0', 'kind': 'expressed'}}}
        elif '硬状态证据核对员' in system:
            kind = 'patch_review'
            value = {'approved': not self.reject_proposal,
                     'issues': ['relationship.trust_delta 缺乏实际关系变化'] if self.reject_proposal else []}
        else:
            kind = 'body'
            value = self.rejected_body if self.reject_first_body and kind not in self.calls else self.body
        self.calls.append(kind)
        return ReplySnapshot(content=value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                             finish_reason='stop')

    async def stream(self, request, profile):
        raise AssertionError('destination prose must remain buffered until its checks pass')
        yield


class SceneEfficiencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'world.db')
        self.store.initialize()
        self.world = 'world_daylight'
        self.conv = self.store.get_or_create_conversation(self.world, 'daylight_severin')

    async def turn(self, client):
        return [json.loads(packet) async for packet in stream_turn(
            store=self.store, provider_client=client, profile_loader=lambda: {},
            conversation_id=self.conv, content=client.user, continuation=False,
            is_disconnected=lambda: asyncio.sleep(0),
        )]

    async def test_existing_appointment_and_information_use_one_authorization_check(self):
        for purpose, user in (('appointment', '我们稍后约在金枝厅见面，现在先不动身。'),
                              ('information', '金枝厅在哪里？现在只是问路。')):
            with self.subTest(purpose=purpose):
                client = SceneClient(purpose, user)
                events = await self.turn(client)
                self.assertEqual(events[-1]['type'], 'done', events)
                self.assertEqual(client.calls, ['plan', 'body', 'body_review', 'proposal', 'patch_review'])
                review = client.requests[client.calls.index('body_review')]
                self.assertIn('用途与玩家授权', review.system)
                self.assertIn('source_quote 必须支持 purpose', review.system)
                self.assertIn('方案误解玩家也必须拒绝', review.system)
                payload = json.loads(review.messages[0].content)
                self.assertEqual(payload['user'], user)
                self.assertEqual(payload['scene_plan']['purpose'], purpose)
                self.assertEqual(payload['scene_plan']['target']['id'], 'daylight_throne')
                self.assertEqual(payload['scene_plan']['source_quote'], user)
                world = self.store.get_world(self.world)
                self.assertEqual(len(world['locations']), 6)
                self.assertEqual(world['player']['location_id'], 'daylight_archive')
                self.assertEqual(self.store.get_character('daylight_severin')['current_location_id'], 'daylight_archive')
                self.assertFalse(self.store.pending_settlements(self.world))

    async def test_new_destination_retains_admission_then_commits_checked_arrival(self):
        client = SceneClient('travel', '现在带我去日轮外廊旁的外廊休息间。', create=True)
        events = await self.turn(client)
        self.assertEqual(events[-1]['type'], 'done', events)
        self.assertEqual(client.calls, ['plan', 'plan_review', 'body', 'body_review', 'proposal', 'patch_review'])
        world = self.store.get_world(self.world)
        place = next(p for p in world['locations'] if p['name'] == client.target_name)
        self.assertEqual(len(world['locations']), 7)
        self.assertEqual(world['player']['location_id'], place['id'])
        self.assertEqual(self.store.get_character('daylight_severin')['current_location_id'], place['id'])
        job = self.store.settlement_job(events[-1]['message_id'])
        self.assertEqual(job['status'], 'complete')
        self.assertEqual(job['context']['prepared_patch']['reply'], client.body)
        self.assertEqual(job['context']['prepared_patch']['patch']['player']['location_id'], place['id'])

    async def test_repeated_proposal_failure_does_not_rewrite_publish_or_move(self):
        before = deepcopy(self.store.get_world(self.world))
        client = SceneClient('travel', '现在带我去日轮外廊旁的外廊休息间。', create=True, reject_proposal=True)
        client.body += '\n\n塞维安扶住门框，回头看向你。'
        events = await self.turn(client)
        self.assertEqual(events[-1]['type'], 'error', events)
        self.assertEqual(events[-1]['error']['code'], 'scene_settlement')
        self.assertEqual(Counter(client.calls), Counter(plan=1, plan_review=1, body=1, body_review=1,
                                                        proposal=2, patch_review=2))
        self.assertFalse({'delta', 'assistant_complete', 'world_patch'} & {event['type'] for event in events})
        repairs = [request for kind, request in zip(client.calls, client.requests) if kind == 'proposal']
        extracted_bodies = []
        for request in repairs:
            source_text = request.messages[0].content.split('本轮来源表：\n', 1)[1].lstrip()
            sources, _ = json.JSONDecoder().raw_decode(source_text)
            assistant_ids = sorted((key for key in sources if key.startswith('assistant_')),
                                   key=lambda key: int(key.removeprefix('assistant_')))
            extracted_bodies.append(''.join(sources[key]['quote'] for key in assistant_ids))
        self.assertEqual(extracted_bodies, [client.body.replace('\n', '')] * 2)
        self.assertIn('relationship.trust_delta', repairs[1].messages[0].content)
        after = self.store.get_world(self.world)
        self.assertEqual(after['locations'], before['locations'])
        self.assertEqual(after['player'], before['player'])
        self.assertEqual(after['world']['current_time'], before['world']['current_time'])
        self.assertEqual(self.store.get_character('daylight_severin')['current_location_id'], 'daylight_archive')
        self.assertFalse(self.store.pending_settlements(self.world))
        self.assertEqual([m['role'] for m in self.store.get_conversation(self.conv)['messages']], ['user'])
        with self.store.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM turn_settlements').fetchone()[0], 0)

    async def test_rejected_body_rewrites_once_without_publishing_first_candidate(self):
        client = SceneClient('information', '金枝厅在哪里？现在只是问路。', reject_first_body=True)
        events = await self.turn(client)
        self.assertEqual(events[-1]['type'], 'done', events)
        self.assertEqual(client.calls, ['plan', 'body', 'body_review', 'body', 'body_review', 'proposal', 'patch_review'])
        second_request = client.requests[3]
        self.assertIn('正文擅自替玩家出发', second_request.system)
        self.assertNotIn(client.rejected_body, json.dumps(events, ensure_ascii=False))
        messages = self.store.get_conversation(self.conv)['messages']
        self.assertEqual([m['content'] for m in messages if m['role'] == 'assistant'], [client.body])
        self.assertEqual(self.store.get_world(self.world)['player']['location_id'], 'daylight_archive')


if __name__ == '__main__':
    unittest.main()
