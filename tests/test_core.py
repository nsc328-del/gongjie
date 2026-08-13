from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from gongjie.ai.providers import OpenAICompatibleAdapter, ProviderClient, ReplySnapshot
from gongjie.cognition.compiler import opening_request_context, roleplay_system
from gongjie.cognition.sync import sync_world_cognition
from gongjie.scenes.adjudication import parse_patch
from gongjie.storage.store import Store


class FakeSummaryClient:
    async def response(self, context, profile):
        name = "沈砚" if "沈砚" in context.messages[0].content else "另一个人"
        return ReplySnapshot(content=f"{name}把新发生的事留在沉默里，态度比先前更谨慎。")


class WorldContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "world.db")
        self.store.initialize()
        self.world_id = "world_velvet_harbor"
        self.conversation_id = self.store.get_or_create_conversation(
            self.world_id, "char_shenyan"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def assistant_turn(self, content: str, patch: dict) -> str:
        self.store.add_message(self.conversation_id, "user", "继续")
        assistant = self.store.add_message(self.conversation_id, "assistant", content)
        self.store.apply_world_patch(self.conversation_id, assistant["id"], patch)
        return assistant["id"]

    def test_fact_is_immediately_world_record_without_branch_state(self) -> None:
        assistant_id = self.assistant_turn("港口的钟声已经响起。", {
            "event": {
                "title": "港钟响起", "details": "第七码头的钟声传遍绯港。",
                "visibility": "public", "participants": ["沈砚"], "witnesses": ["沈砚"],
            },
            "scene": {"world_time": "钟响之后", "tension": 28},
            "relationship": {"trust_delta": 3, "note": "共同听过港钟。"},
        })

        world = self.store.get_world(self.world_id)
        self.assertEqual([item["title"] for item in world["events"]], ["港钟响起"])
        self.assertEqual(world["world"]["current_time"], "钟响之后")
        self.assertEqual(world["pending_sync_count"], 1)
        conversation = self.store.get_conversation(self.conversation_id)
        self.assertEqual(conversation["records"][0]["source_message_id"], assistant_id)
        relationship = next(item for item in world["relationships"] if item["from_character_id"] == "char_shenyan" and item["to_user"])
        self.assertEqual(relationship["trust"], 13)
        with sqlite3.connect(self.store.path) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("branches", tables)

    def test_public_fact_reaches_others_only_after_world_sync(self) -> None:
        self.assistant_turn("港口的钟声已经响起。", {
            "event": {
                "title": "港钟响起", "details": "第七码头的钟声传遍绯港。",
                "visibility": "public", "participants": ["沈砚"], "witnesses": ["沈砚"],
            },
            "scene": {}, "relationship": {},
        })
        other = self.store.get_or_create_conversation(self.world_id, "char_cenye")
        self.assertEqual(self.store.context_for_turn(other)["public_events"], [])

        result = asyncio.run(sync_world_cognition(
            store=self.store, client=FakeSummaryClient(), profile={"max_tokens": 512},
            world_id=self.world_id,
        ))

        self.assertEqual(result["synced_events"], 1)
        other_context = self.store.context_for_turn(other)
        self.assertEqual([event["title"] for event in other_context["public_events"]], ["港钟响起"])
        self.assertIn("港钟响起", roleplay_system(other_context))
        self.assertEqual(self.store.get_world(self.world_id)["pending_sync_count"], 0)

    def test_private_fact_never_spreads_during_world_sync(self) -> None:
        self.assistant_turn("沈砚把钥匙收进掌心。", {
            "event": {
                "title": "钥匙被收起", "details": "沈砚收起了钥匙。",
                "visibility": "private", "participants": ["沈砚"], "witnesses": ["沈砚"],
            },
            "scene": {}, "relationship": {},
        })
        other = self.store.get_or_create_conversation(self.world_id, "char_cenye")
        asyncio.run(sync_world_cognition(
            store=self.store, client=FakeSummaryClient(), profile={"max_tokens": 512},
            world_id=self.world_id,
        ))
        other_context = self.store.context_for_turn(other)
        self.assertFalse(any(item["title"] == "钥匙被收起" for item in other_context["knowledge"]))
        self.assertNotIn("钥匙被收起", roleplay_system(other_context))
        self.assertEqual(self.store.get_world(self.world_id)["events"], [])

    def test_preset_creates_an_independent_world_instance(self) -> None:
        bundle = self.store.create_world({
            "name": "另一个绯港", "template_id": "velvet_harbor",
            "premise": "", "atmosphere": "", "rules": "", "current_time": "",
        })
        self.assertEqual(bundle["world"]["name"], "另一个绯港")
        self.assertEqual(len(bundle["locations"]), 3)
        self.assertEqual(len(bundle["characters"]), 3)
        self.assertNotEqual(bundle["characters"][0]["id"], "char_shenyan")
        self.assertTrue(bundle["world"]["opening_narration"])
        self.assertTrue(all(item["opening_hook"] for item in bundle["characters"]))

    def test_opening_is_compiled_from_existing_state_and_saved_once(self) -> None:
        context = self.store.context_for_turn(self.conversation_id)
        request = opening_request_context(context, {"max_tokens": 16000})
        self.assertIn(context["world"]["opening_narration"], request.system)
        self.assertIn(context["character"]["opening_hook"], request.system)
        self.assertIn("不要新造公共事件", request.system)

        first, created = self.store.add_opening_message(
            self.conversation_id, "沈砚抬眼看向门边。"
        )
        second, created_again = self.store.add_opening_message(
            self.conversation_id, "这句不应覆盖第一次开场。"
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(
            [item["content"] for item in self.store.get_conversation(self.conversation_id)["messages"]],
            ["沈砚抬眼看向门边。"],
        )


class ProviderContractTests(unittest.TestCase):
    def test_openai_stream_is_normalized_and_accumulated(self) -> None:
        adapter = OpenAICompatibleAdapter()
        snapshot = ReplySnapshot()
        payloads = [
            '{"model":"example","choices":[{"delta":{"content":"在。"},"finish_reason":null}]}',
            '{"choices":[{"delta":{"reasoning_content":"想"},"finish_reason":null}],"usage":{"prompt_tokens":9,"completion_tokens":2}}',
            '{"choices":[{"delta":{"content":"继续。"},"finish_reason":"stop"}]}',
        ]
        for payload in payloads:
            for event in adapter.parse_stream(payload):
                ProviderClient._apply(snapshot, event)
        self.assertEqual(snapshot.content, "在。继续。")
        self.assertEqual(snapshot.reasoning, "想")
        self.assertEqual(snapshot.finish_reason, "stop")
        self.assertEqual(snapshot.model, "example")
        self.assertEqual(snapshot.usage.input_tokens, 9)

    def test_world_patch_accepts_fenced_json_but_not_prose(self) -> None:
        self.assertEqual(parse_patch('```json\n{"event":null}\n```')["event"], None)
        self.assertEqual(parse_patch('状态如下：{"event":null}')["event"], None)
        with self.assertRaises(ValueError):
            parse_patch("没有结构")


if __name__ == "__main__":
    unittest.main()
