from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gongjie.ai.providers import OpenAICompatibleAdapter, ProviderClient, ReplySnapshot
from gongjie.cognition.compiler import roleplay_system
from gongjie.scenes.adjudication import parse_patch
from gongjie.storage.store import Store


class WorldContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "world.db")
        self.store.initialize()
        self.world_id = "world_velvet_harbor"
        self.conversation_id = self.store.get_or_create_conversation(self.world_id, "char_shenyan")
        self.root_branch = self.store.get_conversation(self.conversation_id)["branch_id"]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def assistant_turn(self, content: str, patch: dict) -> str:
        self.store.add_message(self.conversation_id, self.root_branch, "user", "继续")
        assistant = self.store.add_message(self.conversation_id, self.root_branch, "assistant", content)
        self.store.apply_world_patch(self.conversation_id, self.root_branch, assistant["id"], patch)
        return assistant["id"]

    def test_public_and_private_knowledge_have_different_reach(self) -> None:
        self.assistant_turn("沈砚把钥匙收进掌心。", {
            "event": {"title": "钥匙被收起", "details": "沈砚收起了钥匙。", "visibility": "private", "participants": ["沈砚"], "witnesses": ["沈砚"]},
            "scene": {"location": "夜航公馆", "tension": 18, "active_threads": ["钥匙的去向"]},
            "relationship": {},
        })
        self.assistant_turn("港口的钟声传遍绯港。", {
            "event": {"title": "港钟响起", "details": "第七码头的钟声响彻港口。", "visibility": "public", "participants": ["沈砚"], "witnesses": []},
            "scene": {"location": "夜航公馆", "tension": 22, "active_threads": ["钟声后的会面"]},
            "relationship": {},
        })

        other_conversation = self.store.get_or_create_conversation(self.world_id, "char_cenye")
        other_branch = self.store.get_conversation(other_conversation)["branch_id"]
        other_context = self.store.context_for_turn(other_conversation, other_branch)

        self.assertEqual([event["title"] for event in other_context["public_events"]], ["港钟响起"])
        self.assertFalse(any(item["title"] == "钥匙被收起" for item in other_context["knowledge"]))
        prompt = roleplay_system(other_context)
        self.assertIn("港钟响起", prompt)
        self.assertNotIn("钥匙被收起", prompt)

    def test_historical_fork_restores_scene_and_promotes_only_visible_events(self) -> None:
        first = self.assistant_turn("第一刻。", {
            "event": {"title": "第一刻", "details": "第一件事发生。", "visibility": "public", "participants": ["沈砚"], "witnesses": []},
            "scene": {"location": "夜航公馆", "world_time": "第一刻", "tension": 17, "active_threads": ["旧线"]},
            "relationship": {},
        })
        self.assistant_turn("第二刻。", {
            "event": {"title": "第二刻", "details": "第二件事发生。", "visibility": "public", "participants": ["沈砚"], "witnesses": []},
            "scene": {"location": "十三号码头", "world_time": "第二刻", "tension": 71, "active_threads": ["未来线"]},
            "relationship": {},
        })

        branch = self.store.fork_branch(self.conversation_id, self.root_branch, first, "另一条路")
        forked = self.store.get_conversation(self.conversation_id, branch)
        self.assertEqual(forked["scene"]["world_time"], "第一刻")
        self.assertEqual(forked["scene"]["tension"], 17)
        self.assertEqual(forked["scene"]["active_threads"], ["旧线"])

        branch_assistant = self.store.add_message(self.conversation_id, branch, "assistant", "支线发生。")
        self.store.apply_world_patch(self.conversation_id, branch, branch_assistant["id"], {
            "event": {"title": "支线事件", "details": "支线中的事发生。", "visibility": "public", "participants": ["沈砚"], "witnesses": []},
            "scene": {"location": "鸦色会所", "world_time": "支线时刻", "tension": 45, "active_threads": ["新线"]},
            "relationship": {},
        })
        self.store.promote_branch(self.conversation_id, branch)
        titles = [event["title"] for event in self.store.get_world(self.world_id)["events"]]
        self.assertIn("第一刻", titles)
        self.assertIn("支线事件", titles)
        self.assertNotIn("第二刻", titles)
        self.assertEqual(self.store.get_world(self.world_id)["world"]["current_time"], "支线时刻")


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
