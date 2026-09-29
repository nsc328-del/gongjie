from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from gongjie.ai.providers import ProviderClient, ProviderError, ReplySnapshot
from gongjie.cognition.compiler import parse_room_reply, room_turn_request_context, room_turn_system
from gongjie.scenes.rooms import stream_room_turn
from gongjie.storage.store import Store


def public_observations(context):
    """Only for these fixtures, whose input and ordinary replies are public."""
    if '你是同场观察编译员' in context.system:
        payload = json.loads(context.messages[0].content)
        return ReplySnapshot(content=json.dumps({
            member['id']: {'quotes': [payload['content']]} for member in payload['members']
        }, ensure_ascii=False), finish_reason='stop')
    if '你是同场观察核对员' in context.system:
        return ReplySnapshot(content='{"approved":true,"issues":[]}', finish_reason='stop')
    return None


class FakeRoomClient:
    """洛恩·维瑟尔沉默、伊芙琳·薇尔回应；裁判按提问方返回对应 JSON。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def response(self, context, profile):
        observed = public_observations(context)
        if observed is not None:
            return observed
        if '你是场景规划员' in context.system:
            return ReplySnapshot(content='{"mode":"stay"}', finish_reason='stop')
        system = context.system
        self.calls.append(context.messages[0].content)
        if "硬状态证据核对员" in system:
            return ReplySnapshot(content='{"approved":true,"issues":[]}')
        if "状态裁判" in system:
            return ReplySnapshot(content=json.dumps({
                "event": {
                    "title": "伊芙琳·薇尔在房间里应下玩家",
                    "details": "伊芙琳·薇尔当着洛恩·维瑟尔的面，低声应了玩家的话。",
                    "visibility": "private", "participants": ["伊芙琳·薇尔", "洛恩·维瑟尔"],
                    "witnesses": ["伊芙琳·薇尔", "洛恩·维瑟尔"],
                    "knowledge_sources": {name: {"source_id": "assistant_0", "kind": "witnessed"} for name in ("伊芙琳·薇尔", "洛恩·维瑟尔")},
                    "evidence": {"source_id": "assistant_0", "kind": "occurred"},
                },
                "scene": {"world_time": {"value":"钟响之前", "source_id":"assistant_0", "kind":"occurred"}, "tension": 40},
                "relationship": {"trust_delta": 2, "desire_delta": 3, "note": "在旁人面前应了他。", "evidence":{"source_id":"assistant_0", "kind":"occurred"}},
            }, ensure_ascii=False))
        if "你是洛恩·维瑟尔" in system:
            return ReplySnapshot(content=json.dumps({"silent": True}, ensure_ascii=False), finish_reason='stop')
        return ReplySnapshot(content=json.dumps({"reply": "……我在听。"}, ensure_ascii=False), finish_reason='stop')


class RoomTurnTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "world.db")
        self.store.initialize()
        self.world_id = "world_daylight"
        self.room = self.store.create_room(
            self.world_id, "夜谈", "daylight_throne", ["daylight_lorne", "daylight_evelyn"]
        )
        self.room_id = self.room["room"]["id"]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_room_storage_roundtrip(self) -> None:
        room = self.store.get_room(self.room_id)
        self.assertEqual(len(room["characters"]), 2)
        user = self.store.add_room_message(self.room_id, "user", None, "今晚有人愿意留下吗？")
        self.assertEqual(user["role"], "user")
        reply = self.store.add_room_message(self.room_id, "assistant", "daylight_lorne", "我留下。")
        self.assertEqual(reply["character_name"], "洛恩·维瑟尔")
        self.assertEqual(len(self.store.get_room(self.room_id)["messages"]), 2)

    def test_room_context_keeps_per_character_knowledge(self) -> None:
        ctx = self.store.room_character_context(self.room_id, "daylight_lorne")
        self.assertEqual(ctx["character"]["name"], "洛恩·维瑟尔")
        self.assertEqual(ctx["room"]["id"], self.room_id)
        present = {c["id"] for c in ctx["room_characters"]}
        self.assertEqual(present, {"daylight_lorne", "daylight_evelyn"})
        self.assertNotIn("daylight_severin", present)

    def test_room_context_keeps_latest_sixty_messages(self) -> None:
        for number in range(1, 62):
            self.store.add_room_message(
                self.room_id, "user", None, f"消息{number}",
                observations={cid: f"消息{number}" for cid in ('daylight_lorne', 'daylight_evelyn')},
            )
        messages = self.store.room_character_context(
            self.room_id, "daylight_lorne"
        )["messages"]
        self.assertEqual(len(messages), 60)
        self.assertEqual(messages[0]["content"], "消息2")
        self.assertEqual(messages[-1]["content"], "消息61")

    def test_room_scene_and_world_record_survive_reload(self) -> None:
        context = self.store.room_character_context(self.room_id, 'daylight_evelyn')
        views = {member['id']: '我把门反锁了。' for member in context['room_characters']}
        context['observation_sources'] = {cid: {'assistant': text} for cid, text in views.items()}
        reply = self.store.add_room_message(
            self.room_id, "assistant", "daylight_evelyn", "我把门反锁了。", observations=views,
        )
        result = self.store.apply_room_patch(
            self.room_id,
            "daylight_evelyn",
            reply["id"],
            {
                "evidence": {key: {"role": "assistant", "quote": "我把门反锁了。", "kind": "occurred"} for key in ("event", "scene.world_time")},
                "event": {
                    "title": "房门反锁", "details": "伊芙琳·薇尔反锁了房门。",
                    "visibility": "private",
                    "participants": ["伊芙琳·薇尔", "洛恩·维瑟尔"],
                    "witnesses": ["伊芙琳·薇尔", "洛恩·维瑟尔"],
                    "knowledge_sources": {name: {"role": "assistant", "quote": "我把门反锁了。", "kind": "witnessed"} for name in ("伊芙琳·薇尔", "洛恩·维瑟尔")},
                },
                "scene": {
                    "world_time": "钟响之前", "tension": 78,
                    "active_threads": ["门外的脚步"],
                    "physical_state": "房门已经反锁", "emotional_state": "警惕",
                },
                "relationship": {},
            },
            context=context,
        )
        room = self.store.get_room(self.room_id)
        own_scene = self.store.room_character_context(self.room_id, 'daylight_evelyn')['scene']
        self.assertEqual(own_scene["tension"], 78)
        self.assertEqual(own_scene["active_threads"], ["门外的脚步"])
        self.assertEqual(own_scene["physical_state"], "房门已经反锁")
        self.assertFalse(self.store.room_character_context(self.room_id, 'daylight_lorne')['scene']['active_threads'])
        self.assertEqual(room["records"][0]["source_room_message_id"], reply["id"])
        self.assertEqual(result["record"]["source_room_message_id"], reply["id"])
        settlement = room["settlements"][0]
        self.assertEqual(settlement["source_room_message_id"], reply["id"])
        self.assertEqual(settlement["event_id"], result["event_id"])
        self.assertEqual(settlement["scene"]["active_threads"], ["门外的脚步"])
        self.assertEqual(room["messages"][-1]["settlement"]["id"], settlement["id"])
        with sqlite3.connect(self.store.path) as connection:
            participant_ids = {
                row[0] for row in connection.execute(
                    "SELECT character_id FROM event_participants WHERE event_id=?",
                    (result["event_id"],),
                )
            }
        self.assertEqual(participant_ids, {"daylight_lorne", "daylight_evelyn"})

    def test_compiler_room_turn_prompt_asks_json_and_no_loop(self) -> None:
        ctx = self.store.room_character_context(self.room_id, "daylight_lorne")
        req = room_turn_request_context(ctx, {"max_tokens": 16384, "temperature": 0.9})
        system = req.system
        self.assertIn("只对玩家", system)
        self.assertIn("不得接续、转述、评论或回应其他角色的发言", system)
        self.assertIn("第三人称限知叙述", system)
        self.assertIn("洛恩·维瑟尔垂下眼", system)
        self.assertIn("silent", system)
        self.assertEqual(parse_room_reply('{"reply": "我在。"}'), {"reply": "我在。"})
        self.assertEqual(parse_room_reply('{"silent": true}'), {"silent": True})
        self.assertEqual(parse_room_reply("```json\n{\"silent\": true}\n```"), {"silent": True})

    def test_stream_room_turn_reply_and_silence(self) -> None:
        fake = FakeRoomClient()
        provider = ProviderClient()
        provider.response = fake.response
        events = stream_room_turn(
            store=self.store,
            provider_client=provider,
            profile_loader=lambda: {"max_tokens": 16384, "temperature": 0.9},
            room_id=self.room_id,
            content="今晚有人愿意留下吗？",
            is_disconnected=lambda: asyncio.sleep(0),
        )
        collected: list[dict] = []
        for chunk in asyncio.run(self._drain(events)):
            collected.append(json.loads(chunk))
        kinds = [item["type"] for item in collected]
        self.assertIn("user", kinds)
        self.assertIn("room_silent", kinds)
        self.assertIn("room_reply", kinds)
        self.assertIn("world_patch", kinds)
        self.assertIn("done", kinds)
        replies = [item for item in collected if item["type"] == "room_reply"]
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["message"]["character_name"], "伊芙琳·薇尔")
        world_patch = next(item for item in collected if item["type"] == "world_patch")
        self.assertEqual(world_patch["source_message_id"], replies[0]["message"]["id"])
        room = self.store.get_room(self.room_id)
        assistant_msgs = [m for m in room["messages"] if m["role"] == "assistant"]
        self.assertEqual(len(assistant_msgs), 1)
        world = self.store.get_world(self.world_id)
        with sqlite3.connect(self.store.path) as connection:
            rows = connection.execute(
                "SELECT title, visibility FROM world_events WHERE world_id=?", (self.world_id,)
            ).fetchall()
        self.assertEqual([r[0] for r in rows], ["伊芙琳·薇尔在房间里应下玩家"])
        self.assertEqual(rows[0][1], "private")
        relationship = next(
            item for item in world["relationships"]
            if item["from_character_id"] == "daylight_evelyn" and item["to_user"]
        )
        self.assertEqual(relationship["trust"], 12)

    def test_room_adjudication_retries_once_before_world_patch(self) -> None:
        class FlakyAdjudicationClient(FakeRoomClient):
            def __init__(self) -> None:
                super().__init__()
                self.patch_calls = 0

            async def response(self, context, profile):
                if '你是场景规划员' in context.system:
                    return ReplySnapshot(content='{"mode":"stay"}')
                if "状态裁判" in context.system:
                    self.patch_calls += 1
                    if self.patch_calls == 1:
                        raise ProviderError("provider", "裁判暂时不可用", retryable=True)
                return await super().response(context, profile)

        fake = FlakyAdjudicationClient()
        provider = ProviderClient()
        provider.response = fake.response
        events = stream_room_turn(
            store=self.store,
            provider_client=provider,
            profile_loader=lambda: {"max_tokens": 16384, "temperature": 0.9},
            room_id=self.room_id,
            content="今晚有人愿意留下吗？",
            is_disconnected=lambda: asyncio.sleep(0),
        )
        collected = [
            json.loads(chunk)
            for chunk in asyncio.run(self._drain(events))
        ]
        kinds = [item["type"] for item in collected]
        self.assertIn("world_patch", kinds)
        self.assertNotIn("patch_skipped", kinds)
        self.assertEqual(fake.patch_calls, 2)

    def test_one_character_failure_does_not_stop_later_members(self) -> None:
        class PartialFailureClient(FakeRoomClient):
            async def response(self, context, profile):
                observed = public_observations(context)
                if observed is not None:
                    return observed
                if '你是场景规划员' in context.system:
                    return ReplySnapshot(content='{"mode":"stay"}')
                if "状态裁判" in context.system:
                    return ReplySnapshot(content=json.dumps({
                        "event": None, "scene": {}, "relationship": {},
                    }, ensure_ascii=False))
                if "你是洛恩·维瑟尔" in context.system:
                    raise ProviderError("provider", "洛恩·维瑟尔暂时失联")
                return ReplySnapshot(content=json.dumps(
                    {"reply": "我还在。"}, ensure_ascii=False
                ), finish_reason='stop')

        fake = PartialFailureClient()
        provider = ProviderClient()
        provider.response = fake.response
        events = stream_room_turn(
            store=self.store,
            provider_client=provider,
            profile_loader=lambda: {"max_tokens": 16384, "temperature": 0.9},
            room_id=self.room_id,
            content="还有人在吗？",
            is_disconnected=lambda: asyncio.sleep(0),
        )
        collected = [
            json.loads(chunk)
            for chunk in asyncio.run(self._drain(events))
        ]
        self.assertEqual(
            [item["type"] for item in collected if item["type"] in {"error", "room_reply", "done"}],
            ["error", "room_reply", "done"],
        )
        self.assertEqual(
            next(item for item in collected if item["type"] == "room_reply")["message"]["character_name"],
            "伊芙琳·薇尔",
        )

    def test_private_paper_and_personal_state_never_reach_other_character_pipeline(self) -> None:
        public_action = '洛恩放下茶杯，把折起的纸条递给玩家。'
        secret = '青铜海鸥'
        body = public_action + '\n洛恩交给玩家的纸条上写着口令青铜海鸥。\n他心里盘算着让谁保守秘密。'
        other_reply = '伊芙琳端起自己的茶杯。'

        class Client:
            def __init__(self):
                self.character_requests = []

            async def response(self, request, profile):
                system = request.system
                payload = request.messages[0].content
                if '你是同场观察编译员' in system:
                    data = json.loads(payload)
                    observed = {}
                    for member in data['members']:
                        text = public_action if data['content'] == body and member['id'] == 'daylight_evelyn' else data['content']
                        observed[member['id']] = {'quotes': [text]}
                    value = observed
                elif '你是同场观察核对员' in system:
                    value = {'approved': True, 'issues': []}
                elif '你是场景规划员' in system:
                    name = json.loads(payload)['character']['name']
                    self.character_requests.append(('planning', name, request))
                    value = {'mode': 'stay'}
                elif '硬状态证据核对员' in system:
                    name = '洛恩·维瑟尔' if json.loads(payload)['assistant'] == body else '伊芙琳·薇尔'
                    self.character_requests.append(('review', name, request))
                    value = {'approved': True, 'issues': []}
                elif '状态裁判' in system:
                    name = payload.split('角色：', 1)[1].splitlines()[0]
                    self.character_requests.append(('adjudication', name, request))
                    if name == '洛恩·维瑟尔':
                        value = {
                            'event': {
                                'title': '洛恩递出折起的纸条',
                                'details': '洛恩交给玩家的纸条上写着口令青铜海鸥。',
                                'visibility': 'secret', 'participants': ['洛恩·维瑟尔'],
                                'witnesses': ['伊芙琳·薇尔'],
                                'evidence': {'source_id': 'assistant_1', 'kind': 'occurred'},
                                'knowledge_sources': {
                                    '洛恩·维瑟尔': {'source_id': 'assistant_1', 'kind': 'witnessed'},
                                    '伊芙琳·薇尔': {'source_id': 'assistant_0', 'kind': 'witnessed'},
                                },
                            },
                            'scene': {'tension': 66, 'active_threads': ['保守青铜海鸥'],
                                      'physical_state': '洛恩按住袖口', 'emotional_state': '担心青铜海鸥泄露'},
                        }
                    else:
                        value = {'scene': {'physical_state': other_reply}}
                else:
                    name = '洛恩·维瑟尔' if '你是洛恩·维瑟尔' in system else '伊芙琳·薇尔'
                    self.character_requests.append(('prose', name, request))
                    value = {'reply': body if name == '洛恩·维瑟尔' else other_reply}
                return ReplySnapshot(content=json.dumps(value, ensure_ascii=False), finish_reason='stop')

        client = Client()
        events = [json.loads(chunk) for chunk in asyncio.run(self._drain(stream_room_turn(
            store=self.store, provider_client=client, profile_loader=lambda: {}, room_id=self.room_id,
            content='我坐下了。', is_disconnected=lambda: asyncio.sleep(0),
        )))]
        self.assertNotIn('error', [event['type'] for event in events])
        self.assertNotIn('patch_skipped', [event['type'] for event in events])
        self.assertEqual(len([event for event in events if event['type'] == 'world_patch']), 2)
        requests = [(phase, request) for phase, name, request in client.character_requests if name == '伊芙琳·薇尔']
        self.assertEqual({phase for phase, _ in requests}, {'planning', 'prose', 'adjudication', 'review'})
        for phase, request in requests:
            with self.subTest(phase=phase):
                text = request.system + '\n'.join(message.content for message in request.messages)
                self.assertIn(public_action, text)
                self.assertNotIn(secret, text)
                self.assertNotIn('他心里盘算着', text)
                self.assertNotIn('洛恩按住袖口', text)
        reopened = Store(self.store.path)
        reopened.initialize()
        own = reopened.room_character_context(self.room_id, 'daylight_lorne')
        other = reopened.room_character_context(self.room_id, 'daylight_evelyn')
        self.assertEqual(own['scene']['active_threads'], ['保守青铜海鸥'])
        self.assertNotIn(secret, json.dumps({k: other[k] for k in ('messages', 'scene', 'knowledge')}, ensure_ascii=False))
        self.assertEqual(other['knowledge'][0]['details'], public_action)

    async def _drain(self, events):
        result = []
        async for chunk in events:
            result.append(chunk)
        return result


if __name__ == "__main__":
    unittest.main()
