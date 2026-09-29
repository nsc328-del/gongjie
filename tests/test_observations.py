import json
import unittest

from gongjie.ai.providers import ProviderError, ReplySnapshot
from gongjie.scenes.observations import observe_message


MEMBERS = [{'id': 'lorne', 'name': '洛恩'}, {'id': 'evelyn', 'name': '伊芙琳'}]


class Client:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    async def response(self, request, profile):
        self.requests.append(request)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, ReplySnapshot):
            return response
        return ReplySnapshot(content=json.dumps(response, ensure_ascii=False), finish_reason='stop')


APPROVED = {'approved': True, 'issues': []}


class ObservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_public_motion_whisper_and_thought(self):
        content = '洛恩放下茶杯。他凑到玩家耳边低声说：“暗号是青铜海鸥。”他心想，终于骗过了伊芙琳。'
        public = '洛恩放下茶杯。'
        candidate = {'lorne': {'quotes': [content]}, 'evelyn': {'quotes': [public]}}
        client = Client(candidate, APPROVED)
        result = await observe_message(client, {}, content=content, author_id='lorne', members=MEMBERS)
        self.assertEqual(result, {'lorne': content, 'evelyn': public})
        self.assertNotIn('青铜海鸥', result['evelyn'])
        self.assertNotIn('骗过', result['evelyn'])

    async def test_player_public_speech_and_directed_whisper(self):
        public = '我向大家说：“晚安。”'
        client = Client({m['id']: {'quotes': [public]} for m in MEMBERS}, APPROVED)
        self.assertEqual(await observe_message(client, {}, content=public, author_id=None, members=MEMBERS),
                         {'lorne': public, 'evelyn': public})
        whisper = '我避开伊芙琳的目光，只在洛恩耳边说：“钥匙在窗台。”'
        client = Client({'lorne': {'quotes': [whisper]}, 'evelyn': {'quotes': []}}, APPROVED)
        self.assertEqual(await observe_message(client, {}, content=whisper, author_id=None, members=MEMBERS),
                         {'lorne': whisper, 'evelyn': ''})

    async def test_privacy_review_repairs_once_without_exposing_failed_candidate(self):
        content = '洛恩点头。他只在心里记下暗号。'
        bad = {'lorne': {'quotes': [content]}, 'evelyn': {'quotes': [content]}}
        good = {'lorne': {'quotes': []}, 'evelyn': {'quotes': ['洛恩点头。']}}
        client = Client(bad, {'approved': False, 'issues': ['evelyn 不可见洛恩心声']}, good, APPROVED)
        result = await observe_message(client, {}, content=content, author_id='lorne', members=MEMBERS)
        self.assertEqual(result, {'lorne': content, 'evelyn': '洛恩点头。'})
        self.assertIn('evelyn 不可见洛恩心声', client.requests[2].system)
        # The review checks the actual result, including the program's author memory.
        self.assertEqual(json.loads(client.requests[-1].messages[0].content)['observations']['lorne']['quotes'], [content])

    async def test_bad_sources_or_recipients_fail_closed(self):
        content = '洛恩放下茶杯。伊芙琳抬头。'
        base = {'lorne': {'quotes': []}, 'evelyn': {'quotes': []}}
        candidates = [
            {**base, 'evelyn': {'quotes': ['凭空出现的暗号']}},
            {**base, 'evelyn': {'quotes': ['洛恩放下茶杯。', '洛恩放下茶杯。']}},
            {**base, 'evelyn': {'quotes': ['伊芙琳抬头。', '洛恩放下茶杯。']}},
            {**base, 'evelyn': {'quotes': ['洛恩放下茶杯。', '下茶杯。']}},
            {**base, 'stranger': {'quotes': [content]}},
            {'lorne': {'quotes': [content]}},
        ]
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                client = Client(candidate, candidate)
                with self.assertRaises(ProviderError) as error:
                    await observe_message(client, {}, content=content, author_id='lorne', members=MEMBERS)
                self.assertEqual(error.exception.code, 'room_observation')
                self.assertEqual(len(client.requests), 2)

    async def test_model_and_review_failure_never_return_full_text(self):
        content = '我在心里决定离开。'
        proposal = {m['id']: {'quotes': []} for m in MEMBERS}
        for responses in (
            [ProviderError('network', 'offline', retryable=True)],
            [ReplySnapshot(content='{}', finish_reason='length')],
            [proposal, ReplySnapshot(content='{"approved":true}', finish_reason=None)],
            [proposal, {'approved': 'true', 'issues': []}, proposal, {'approved': 'true', 'issues': []}],
            [proposal, {'approved': False, 'issues': ['有误']}, proposal, {'approved': False, 'issues': ['仍有误']}],
        ):
            with self.subTest(responses=responses), self.assertRaises(ProviderError):
                await observe_message(Client(*responses), {}, content=content, author_id=None, members=MEMBERS)

    async def test_request_only_contains_message_and_public_identities(self):
        members = [dict(MEMBERS[0], private_notes='不得进入请求', role='皇帝'), MEMBERS[1]]
        content = '她点了点头。'
        previous = {'lorne': '伊芙琳来到桌边。'}
        candidate = {m['id']: {'quotes': [content]} for m in MEMBERS}
        client = Client(candidate, APPROVED)
        await observe_message(client, {'api_key': '不能出现在提示词', 'max_tokens': 128}, content=content,
                              author_id=None, members=members, previous_views=previous)
        initial, review = client.requests
        data = json.loads(initial.messages[0].content)
        self.assertEqual(set(data), {'content', 'author_id', 'members', 'previous_views'})
        self.assertEqual(data['members'], MEMBERS)
        self.assertNotIn('不得进入请求', initial.messages[0].content)
        self.assertNotIn('不能出现在提示词', initial.messages[0].content)
        self.assertFalse(initial.thinking)
        self.assertTrue(review.thinking)
        self.assertEqual(review.reasoning_effort, 'low')
        self.assertGreaterEqual(review.max_tokens, 8192)
        with self.assertRaises(ValueError):
            await observe_message(Client(), {}, content=content, author_id='stranger', members=MEMBERS)


if __name__ == '__main__':
    unittest.main()
