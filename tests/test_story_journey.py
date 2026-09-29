from __future__ import annotations

import json
import asyncio
import tempfile
import unittest
from pathlib import Path

from gongjie.cognition.compiler import opening_request_context
from gongjie.cognition.pulse import advance_world_pulse, character_pulse_context, transition_context
from gongjie.ai.providers import OpenAICompatibleAdapter, RequestContext, ReplySnapshot
from gongjie.storage.store import Store
from gongjie.domain.patches import validate_patch


class StoryJourneyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'world.db')
        self.store.initialize()
        self.world = 'world_daylight'
        self.conv = self.store.get_or_create_conversation(self.world, 'daylight_lorne')

    def tearDown(self):
        self.temp.cleanup()

    def settle(self, user, reply, player):
        self.store.add_message(self.conv, 'user', user)
        message = self.store.add_message(self.conv, 'assistant', reply)
        return self.store.apply_world_patch(self.conv, message['id'], {
            'event': None, 'player': player,
            'evidence': {'player.commitments': {'role': 'user', 'quote': user, 'kind': 'expressed'}},
        })

    def test_promise_survives_other_meetings_and_reentry_until_fulfilled(self):
        self.store.add_opening_message(self.conv, '洛恩·维瑟尔站在窗边。')
        promise = '明天清晨回金枝厅见洛恩·维瑟尔'
        self.settle('我答应，' + promise, '洛恩·维瑟尔答应等到清晨。', {'commitments': [promise]})
        original = self.store.get_world(self.world)['player']['commitments'][0]
        self.settle('那么明天见。', '洛恩·维瑟尔点了点头。', {'commitments': [promise]})
        self.assertEqual(self.store.get_world(self.world)['player']['commitments'][0]['id'], original['id'])
        other = self.store.get_or_create_conversation(self.world, 'daylight_evelyn')
        self.assertFalse(self.store.context_for_turn(other)['player']['commitments'])
        self.store.commit_world_pulse(self.world, [], {}, {'world_time': '次日清晨', 'narration': '晨雾渐散。'})
        self.store.get_or_create_conversation(self.world, 'daylight_lorne')
        self.assertEqual(len(self.store.context_for_turn(self.conv)['player']['commitments']), 1)
        self.store.add_opening_message(self.conv, '洛恩·维瑟尔仍在窗边等候。')
        again, created = self.store.add_opening_message(self.conv, '这句不应重复保存。')
        self.assertFalse(created)
        history = self.store.get_conversation(self.conv)
        self.assertEqual(history['messages'][-1]['scene_entry']['entry_kind'], 'reentry')
        self.settle('我按约回到金枝厅来见你了。', '洛恩·维瑟尔点头：“你来了。”', {'commitments': []})
        self.assertFalse(self.store.get_world(self.world)['player']['commitments'])
        self.assertEqual(len(self.store.get_conversation(self.conv)['settlements']), 3)

    def test_later_meetings_keep_identity_without_replaying_the_original_night(self):
        before = self.store.context_for_turn(self.conv)
        self.store.add_opening_message(self.conv, '洛恩·维瑟尔望向窗外。')
        self.store.commit_world_pulse(self.world, [], {}, {'world_time': '次日清晨', 'narration': '天亮了。'})
        context = self.store.context_for_turn(self.conv)
        request = opening_request_context(context, {'max_tokens': 4096}, reentry=True)
        for key in ('persona', 'speech_style', 'loss_of_control'):
            self.assertIn(context['character'][key], request.system)
        self.assertIn(json.dumps(context['character']['desire_profile'], ensure_ascii=False), request.system)
        other = self.store.get_or_create_conversation(self.world, 'daylight_evelyn')
        new_context = self.store.context_for_turn(other)
        first = opening_request_context(new_context, {'max_tokens': 4096})
        self.assertNotIn(before['world']['opening_narration'], first.system)
        self.assertNotIn(new_context['character']['opening_hook'], first.system)
        self.assertIn('次日清晨', first.system)

    def test_absent_characters_cannot_observe_player_or_others_promises(self):
        self.settle('我答应明日回来。', '洛恩·维瑟尔点头。', {'commitments': ['秘密约定青鹭17'], 'condition': '袖口带着独有蓝漆', 'visible_traces': ['掌心留着蓝色痕迹']})
        context = self.store.cognition_sync_context(self.world)
        by_name = {item['character']['name']: item for item in context['characters']}
        absent = character_pulse_context(by_name['伊芙琳·薇尔'], context, {}, 4096).messages[0].content
        for hidden in ('秘密约定青鹭17', '袖口带着独有蓝漆', '掌心留着蓝色痕迹', '"location_id": "daylight_throne"'):
            self.assertNotIn(hidden, absent)
        present = character_pulse_context(by_name['洛恩·维瑟尔'], context, {}, 4096).messages[0].content
        self.assertIn('掌心留着蓝色痕迹', present)
        self.assertIn('秘密约定青鹭17', present)
        self.assertNotIn('秘密约定青鹭17', transition_context(context, [], {}, 4096).messages[0].content)
        self.settle('我先走了。', '洛恩·维瑟尔留在窗边。', {'condition': '距离洛恩·维瑟尔一步之遥', 'visible_traces': ['袖口微湿']})
        other = self.store.get_or_create_conversation(self.world, 'daylight_evelyn')
        observed = self.store.context_for_turn(other)['player']
        self.assertEqual(observed['condition'], '')
        self.assertEqual(observed['visible_traces'], ['袖口微湿'])

    def test_invalid_time_does_not_commit_a_pulse(self):
        before = self.store.get_world(self.world)
        for value in ('', '18:43:36', before['world']['current_time']):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.commit_world_pulse(self.world, [], {'daylight_lorne': {'intention': '不应保存'}}, {'world_time': value})
            self.assertEqual(self.store.get_world(self.world), before)

    def test_thinking_preference_is_mapped_only_for_supported_models(self):
        adapter = OpenAICompatibleAdapter()
        profile = {"model": "deepseek-v4-flash", "base_url": "https://example.test", "path": "/v1/chat/completions", "api_key": "test"}
        for stream in (True, False):
            _, _, body = adapter.build_request(RequestContext(system="核对", messages=[], thinking=False), profile, stream=stream)
            self.assertEqual(body["thinking"], {"type": "disabled"})
        _, _, thoughtful = adapter.build_request(RequestContext(system="核对", messages=[], thinking=True, reasoning_effort="low"), profile, stream=False)
        self.assertEqual(thoughtful["reasoning_effort"], "low")
        _, _, default = adapter.build_request(RequestContext(system="叙事", messages=[]), profile, stream=False)
        self.assertNotIn("thinking", default)
        _, _, unrelated = adapter.build_request(RequestContext(system="核对", messages=[], thinking=False), {**profile, "model": "another-model"}, stream=False)
        self.assertNotIn("thinking", unrelated)

    def test_same_state_does_not_send_irrelevant_citations_for_review(self):
        context = self.store.context_for_turn(self.conv)
        result = validate_patch({
            "event": None,
            "scene": {"location": "金枝厅", "world_time": context["world"]["current_time"]},
            "evidence": {"scene.location": {"quote": "旧开场的句子，不是本轮原文"}},
        }, context, "洛恩·维瑟尔点头。")
        self.assertEqual(result["evidence"], {})

    def test_pulse_cannot_invent_player_history_or_mark_distant_character_present(self):
        self.settle('我答应明日回来。', '洛恩·维瑟尔点头。', {'commitments': ['明日回公馆']})
        self.store.get_or_create_conversation(self.world, 'daylight_evelyn')
        class Client:
            invent = False
            async def response(self, request, profile):
                payload = json.loads(request.messages[0].content)
                if '独立行动决定' in request.system:
                    if payload['角色']['姓名'] == '洛恩·维瑟尔':
                        return ReplySnapshot(content=json.dumps({
                            'participation': 'active', 'active_reason': 'commitment',
                            'active_source': payload['玩家可被观察的状态']['commitments'][0]['id'],
                            'location': '金枝厅', 'availability': 'present',
                            'surface_state': '仍在公馆等候。', 'intention': '等玩家赴约',
                        }, ensure_ascii=False))
                    return ReplySnapshot(content='{"participation":"quiet"}')
                return ReplySnapshot(content=json.dumps({
                    'world_time': '第二天午后' if self.invent else '次日清晨',
                    'narration': '雾慢慢散开。', 'player_condition_action': 'clear',
                    'retained_trace_indices': [0] if self.invent else [],
                }, ensure_ascii=False))
        client = Client()
        asyncio.run(advance_world_pulse(store=self.store, client=client, profile={}, world_id=self.world))
        current = self.store.get_world(self.world)
        character = next(c for c in current['characters'] if c['id'] == 'daylight_lorne')
        self.assertEqual(character['availability'], 'reachable')
        client.invent = True
        with self.assertRaisesRegex(ValueError, '玩家痕迹'):
            asyncio.run(advance_world_pulse(store=self.store, client=client, profile={}, world_id=self.world))
        self.assertEqual(self.store.get_world(self.world), current)

    def test_pulse_selects_existing_state_without_rewriting_and_can_clear_it(self):
        original = '玩家坐在茶桌旁，姿态平静而关切。'
        self.settle('我坐下喝茶。', '洛恩·维瑟尔点头。', {
            'condition': original, 'visible_traces': ['袖口微湿', '手背有墨迹'],
        })
        class Client:
            clear = False
            async def response(self, request, profile):
                if '独立行动决定' in request.system:
                    return ReplySnapshot(content='{"participation":"quiet"}')
                return ReplySnapshot(content=json.dumps({
                    'world_time': '次日下午' if self.clear else '次日上午',
                    'narration': '窗外的雾散开了。',
                    'player_condition_action': 'clear' if self.clear else 'keep',
                    'retained_trace_indices': [] if self.clear else [1],
                    # 多余的生成文本不能进入存储，也不需要靠比较原文阻断推进。
                    'player_condition': '姿态平静而关切',
                    'visible_traces': ['去过码头，鞋底留有木屑'],
                }, ensure_ascii=False))
        client = Client()
        for clear in (False, True):
            client.clear = clear
            asyncio.run(advance_world_pulse(store=self.store, client=client, profile={}, world_id=self.world))
            saved = self.store.get_world(self.world)['player']
            self.assertEqual(saved['condition'], '' if clear else original)
            self.assertEqual(saved['visible_traces'], [] if clear else ['手背有墨迹'])

    def test_invalid_state_selections_do_not_commit_any_world_changes(self):
        self.settle('我坐下。', '洛恩·维瑟尔点头。', {'condition': '衣袖微湿', 'visible_traces': ['手背有墨迹']})
        class Client:
            selection = {}
            async def response(self, request, profile):
                if '独立行动决定' in request.system:
                    return ReplySnapshot(content='{"participation":"quiet"}')
                return ReplySnapshot(content=json.dumps({
                    'world_time': '次日上午', 'narration': '晨雾渐散。',
                    'player_condition_action': 'keep', 'retained_trace_indices': [],
                    **self.selection,
                }, ensure_ascii=False))
        before = self.store.get_world(self.world)
        client = Client()
        for selection in (
            {'player_condition_action': '昨夜去了码头'},
            *({'retained_trace_indices': value} for value in ([1], [-1], [True], ['0'], [0, 0], None, {})),
        ):
            with self.subTest(selection=selection):
                client.selection = selection
                with self.assertRaises(ValueError):
                    asyncio.run(advance_world_pulse(store=self.store, client=client, profile={}, world_id=self.world))
                self.assertEqual(self.store.get_world(self.world), before)

    def test_reentry_and_review_separate_current_time_from_historical_scene(self):
        from gongjie.cognition.compiler import scene_entry_grounding_context
        self.store.add_opening_message(self.conv, '你真觉得这样好吗？')
        context = self.store.context_for_turn(self.conv)
        context['world']['current_time'] = '第二天上午十点'
        context['previous_scene'].update(world_time='前日下午三点', active_threads=['等待玩家肯定'])
        context['messages'] = [
            {'role': 'assistant', 'content': '你真觉得这样好吗？'},
            {'role': 'user', 'content': '我已经回答过：很好。'},
        ]
        request = opening_request_context(context, {}, reentry=True)
        self.assertIn('此刻：第二天上午十点', request.system)
        self.assertNotIn('此刻：前日下午三点', request.system)
        self.assertIn('记录时刻：前日下午三点', request.system)
        self.assertIn('我已经回答过：很好。', request.system)
        self.assertEqual([m.role for m in request.messages], ['user'])
        self.assertIn('新时段独立入场', request.messages[0].content)
        review = scene_entry_grounding_context(context, '午后三点，洛恩·维瑟尔又问了一遍。', {})
        ledger = json.loads(review.messages[0].content)['memory_ledger']
        self.assertEqual(ledger['world']['current_time'], '第二天上午十点')
        self.assertEqual(ledger['previous_scene']['world_time'], '前日下午三点')
        self.assertEqual(ledger['historical_dialogue'], context['messages'])
        self.assertNotIn('scene', ledger)
        self.assertIn('旧台词', review.system)
