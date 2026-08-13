from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from gongjie.ai.providers import ProviderClient, ReplySnapshot
from gongjie.cognition.compiler import parse_room_reply, room_turn_request_context, room_turn_system
from gongjie.scenes.rooms import stream_room_turn
from gongjie.storage.store import Store


class FakeRoomClient:
    """沈砚沉默、林照回应；裁判按提问方返回对应 JSON。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def response(self, context, profile):
        system = context.system
        self.calls.append(context.messages[0].content)
        if "状态裁判" in system:
            return ReplySnapshot(content=json.dumps({
                "event": {
                    "title": "林照在房间里应下玩家",
                    "details": "林照当着沈砚的面，低声应了玩家的话。",
                    "visibility": "private", "participants": ["林照", "沈砚"],
                    "witnesses": ["林照", "沈砚"],
                },
                "scene": {"world_time": "钟响之前", "tension": 40},
                "relationship": {"trust_delta": 2, "desire_delta": 3, "note": "在旁人面前应了他。"},
            }, ensure_ascii=False))
        if "你是沈砚" in system:
            return ReplySnapshot(content=json.dumps({"silent": True}, ensure_ascii=False))
        return ReplySnapshot(content=json.dumps({"reply": "……我在听。"}, ensure_ascii=False))


class RoomTurnTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "world.db")
        self.store.initialize()
        self.world_id = "world_velvet_harbor"
        self.room = self.store.create_room(
            self.world_id, "夜谈", "loc_house", ["char_shenyan", "char_linzhao"]
        )
        self.room_id = self.room["room"]["id"]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_room_storage_roundtrip(self) -> None:
        room = self.store.get_room(self.room_id)
        self.assertEqual(len(room["characters"]), 2)
        user = self.store.add_room_message(self.room_id, "user", None, "今晚有人愿意留下吗？")
        self.assertEqual(user["role"], "user")
        reply = self.store.add_room_message(self.room_id, "assistant", "char_shenyan", "我留下。")
        self.assertEqual(reply["character_name"], "沈砚")
        self.assertEqual(len(self.store.get_room(self.room_id)["messages"]), 2)

    def test_room_context_keeps_per_character_knowledge(self) -> None:
        ctx = self.store.room_character_context(self.room_id, "char_shenyan")
        self.assertEqual(ctx["character"]["name"], "沈砚")
        self.assertEqual(ctx["room"]["id"], self.room_id)
        self.assertIn("char_linzhao", {c["id"] for c in ctx["room_characters"]})

    def test_compiler_room_turn_prompt_asks_json_and_no_loop(self) -> None:
        ctx = self.store.room_character_context(self.room_id, "char_shenyan")
        req = room_turn_request_context(ctx, {"max_tokens": 16384, "temperature": 0.9})
        system = req.system
        self.assertIn("只对玩家", system)
        self.assertIn("不得接续、转述、评论或回应其他角色的发言", system)
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
        for chunk in asyncio.new_event_loop().run_until_complete(self._drain(events)):
            collected.append(json.loads(chunk))
        kinds = [item["type"] for item in collected]
        self.assertIn("user", kinds)
        self.assertIn("room_silent", kinds)
        self.assertIn("room_reply", kinds)
        self.assertIn("world_patch", kinds)
        self.assertIn("done", kinds)
        replies = [item for item in collected if item["type"] == "room_reply"]
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["message"]["character_name"], "林照")
        room = self.store.get_room(self.room_id)
        assistant_msgs = [m for m in room["messages"] if m["role"] == "assistant"]
        self.assertEqual(len(assistant_msgs), 1)
        world = self.store.get_world(self.world_id)
        import sqlite3
        with sqlite3.connect(self.store.path) as connection:
            rows = connection.execute(
                "SELECT title, visibility FROM world_events WHERE world_id=?", (self.world_id,)
            ).fetchall()
        self.assertEqual([r[0] for r in rows], ["林照在房间里应下玩家"])
        self.assertEqual(rows[0][1], "private")
        relationship = next(
            item for item in world["relationships"]
            if item["from_character_id"] == "char_linzhao" and item["to_user"]
        )
        self.assertEqual(relationship["trust"], 12)

    async def _drain(self, events):
        result = []
        async for chunk in events:
            result.append(chunk)
        return result


if __name__ == "__main__":
    unittest.main()
