from __future__ import annotations

import asyncio
import itertools
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from gongjie.ai.providers import OpenAICompatibleAdapter, ProviderClient, ProviderError, ReplySnapshot
from gongjie.api.routes.conversations import opening as generate_opening
from gongjie.cognition.compiler import (
    opening_request_context,
    parse_scene_entry_grounding,
    roleplay_system,
    scene_entry_grounding_context,
)
from gongjie.cognition.pulse import advance_world_pulse
from gongjie.cognition.sync import sync_world_cognition
from gongjie.scenes.adjudication import parse_patch, patch_context
from gongjie.scenes.turns import stream_turn
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
        user_text = "；".join((patch.get("player") or {}).get("commitments", [])) or "继续"
        self.store.add_message(self.conversation_id, "user", user_text)
        patch = json.loads(json.dumps(patch))
        patch["evidence"] = {key: {"role": "assistant", "quote": content, "kind": "occurred"}
                             for key in ("event", "scene.location", "scene.world_time", "relationship", "player.location")}
        patch["evidence"]["player.commitments"] = {"role": "user", "quote": user_text, "kind": "expressed"}
        if patch.get("event"):
            patch["event"]["knowledge_sources"] = {
                name: {"role": "assistant", "quote": content, "kind": "witnessed"}
                for name in set(patch["event"].get("participants", []) + patch["event"].get("witnesses", []) + ["沈砚"])
            }
        assistant = self.store.add_message(self.conversation_id, "assistant", content)
        self.store.apply_world_patch(self.conversation_id, assistant["id"], patch)
        return assistant["id"]

    def test_context_keeps_latest_facts_when_event_count_exceeds_limit(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ticks = (
            (start + timedelta(seconds=step)).isoformat(timespec="seconds")
            for step in itertools.count()
        )
        with patch("gongjie.storage.store.now", lambda: next(ticks)):
            for index in range(1, 126):
                self.assistant_turn(f"港钟第{index}次响起。", {
                    "event": {
                        "title": f"港钟第{index}响", "details": "钟声传遍绯港。",
                        "visibility": "public", "participants": ["沈砚"], "witnesses": ["沈砚"],
                    },
                    "scene": {}, "relationship": {},
                })

        titles = [
            event["title"]
            for event in self.store.context_for_turn(self.conversation_id)["public_events"]
        ]
        self.assertEqual(len(titles), 120)
        self.assertEqual(titles[0], "港钟第6响")
        self.assertEqual(titles[-1], "港钟第125响")

    def test_turn_marks_reply_complete_before_world_adjudication(self) -> None:
        class FakeTurnClient:
            async def stream(self, context, profile):
                yield ReplySnapshot(content="沈砚停下话音。", finish_reason="stop")

            async def response(self, context, profile):
                return ReplySnapshot(content=json.dumps({
                    "event": None, "scene": {}, "relationship": {},
                }, ensure_ascii=False))

        fake = FakeTurnClient()
        provider = ProviderClient()
        provider.stream = fake.stream
        provider.response = fake.response
        events = stream_turn(
            store=self.store,
            provider_client=provider,
            profile_loader=lambda: {"max_tokens": 16384, "temperature": 0.9},
            conversation_id=self.conversation_id,
            content="说完了吗？",
            continuation=False,
            is_disconnected=lambda: asyncio.sleep(0),
        )
        collected = asyncio.run(self._drain_turn(events))
        kinds = [item["type"] for item in collected]
        self.assertLess(kinds.index("delta"), kinds.index("assistant_complete"))
        self.assertLess(kinds.index("assistant_complete"), kinds.index("world_patch"))
        self.assertEqual(collected[-1]["type"], "done")
        assistant = next(
            message for message in self.store.get_conversation(self.conversation_id)["messages"]
            if message["role"] == "assistant"
        )
        self.assertEqual(assistant["status"], "complete")

    async def _drain_turn(self, events):
        result = []
        async for chunk in events:
            result.append(json.loads(chunk))
        return result

    def test_adjudication_retries_once_and_still_writes_world_patch(self) -> None:
        class FlakyTurnClient:
            def __init__(self) -> None:
                self.patch_calls = 0

            async def stream(self, context, profile):
                yield ReplySnapshot(content="沈砚点了点头。", finish_reason="stop")

            async def response(self, context, profile):
                self.patch_calls += 1
                if self.patch_calls == 1:
                    raise ProviderError("provider", "裁判暂时不可用", retryable=True)
                return ReplySnapshot(content=json.dumps({
                    "event": None, "scene": {}, "relationship": {},
                }, ensure_ascii=False))

        fake = FlakyTurnClient()
        provider = ProviderClient()
        provider.stream = fake.stream
        provider.response = fake.response
        events = stream_turn(
            store=self.store,
            provider_client=provider,
            profile_loader=lambda: {"max_tokens": 16384, "temperature": 0.9},
            conversation_id=self.conversation_id,
            content="你还在听吗？",
            continuation=False,
            is_disconnected=lambda: asyncio.sleep(0),
        )
        collected = asyncio.run(self._drain_turn(events))
        kinds = [item["type"] for item in collected]
        self.assertIn("world_patch", kinds)
        self.assertNotIn("patch_skipped", kinds)
        self.assertEqual(fake.patch_calls, 2)
        self.assertEqual(collected[-1]["type"], "done")

    def test_patch_skipped_after_two_failures_carries_reason_without_details(self) -> None:
        class BrokenTurnClient:
            async def stream(self, context, profile):
                yield ReplySnapshot(content="沈砚沉默了。", finish_reason="stop")

            async def response(self, context, profile):
                raise ProviderError(
                    "provider", "上游返回 500：内部节点 trace-9f31", retryable=True
                )

        fake = BrokenTurnClient()
        provider = ProviderClient()
        provider.stream = fake.stream
        provider.response = fake.response
        events = stream_turn(
            store=self.store,
            provider_client=provider,
            profile_loader=lambda: {"max_tokens": 16384, "temperature": 0.9},
            conversation_id=self.conversation_id,
            content="说点什么。",
            continuation=False,
            is_disconnected=lambda: asyncio.sleep(0),
        )
        collected = asyncio.run(self._drain_turn(events))
        skipped = [item for item in collected if item["type"] == "patch_skipped"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["reason"], "provider_error")
        self.assertNotIn("trace-9f31", json.dumps(skipped[0], ensure_ascii=False))
        self.assertEqual(collected[-1]["type"], "done")

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
        settlement = conversation["settlements"][0]
        self.assertEqual(settlement["source_message_id"], assistant_id)
        self.assertEqual(settlement["event_id"], conversation["records"][0]["id"])
        self.assertEqual(settlement["scene"]["world_time"], "钟响之后")
        self.assertEqual(settlement["relationship"]["trust_delta"], 3)
        self.assertEqual(
            conversation["messages"][-1]["settlement"]["id"], settlement["id"]
        )
        relationship = next(item for item in world["relationships"] if item["from_character_id"] == "char_shenyan" and item["to_user"])
        self.assertEqual(relationship["trust"], 13)
        with sqlite3.connect(self.store.path) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("branches", tables)

    def test_immediate_settlement_tracks_player_without_leaking_promises(self) -> None:
        assistant_id = self.assistant_turn("沈砚替他系紧领口，约定钟响后再见。", {
            "event": None,
            "scene": {"location_id": "loc_house", "tension": 36},
            "relationship": {},
            "player": {
                "location": "夜航公馆",
                "condition": "领口刚被整理过",
                "visible_traces": ["右肩留着一道新鲜抓痕"],
                "commitments": ["钟响后回夜航公馆见沈砚"],
            },
        })

        conversation = self.store.get_conversation(self.conversation_id)
        settlement = conversation["settlements"][0]
        self.assertEqual(settlement["source_message_id"], assistant_id)
        self.assertIsNone(settlement["event_id"])
        self.assertEqual(
            settlement["player"]["commitments"], ["钟响后回夜航公馆见沈砚"]
        )
        self.assertEqual(conversation["messages"][-1]["settlement"]["scene"]["tension"], 36)

        world = self.store.get_world(self.world_id)
        self.assertEqual(world["player"]["location_name"], "夜航公馆")
        self.assertEqual(world["player"]["visible_traces"], ["右肩留着一道新鲜抓痕"])
        self.assertEqual(world["player"]["commitments"][0]["character_name"], "沈砚")
        shenyan_context = self.store.context_for_turn(self.conversation_id)
        self.assertEqual(len(shenyan_context["player"]["commitments"]), 1)
        cenye = self.store.get_or_create_conversation(self.world_id, "char_cenye")
        cenye_context = self.store.context_for_turn(cenye)
        self.assertEqual(cenye_context["player"]["commitments"], [])
        self.assertEqual(cenye_context["player"]["visible_traces"], ["右肩留着一道新鲜抓痕"])

    def test_world_pulse_advances_time_and_creates_dynamic_encounters(self) -> None:
        class FakePulseClient:
            def __init__(self) -> None:
                self.character_payloads: dict[str, dict] = {}

            async def response(self, context, profile):
                payload = json.loads(context.messages[0].content)
                if "独立行动决定" in context.system:
                    name = payload["角色"]["姓名"]
                    self.character_payloads[name] = payload
                    if name != "沈砚":
                        return ReplySnapshot(content=json.dumps({
                            "participation": "quiet",
                            "active_reason": "",
                            "active_source": "",
                            "intention": "",
                            "surface_state": "",
                            "location": payload["角色"]["当前地点"],
                            "availability": "reachable",
                            "encounter_hook": "",
                        }, ensure_ascii=False))
                    return ReplySnapshot(content=json.dumps({
                        "participation": "active",
                        "active_reason": "new_fact",
                        "active_source": payload["本次新获得或亲历的事实"][0]["id"],
                        "intention": f"{name}决定主动确认玩家的去向",
                        "surface_state": f"{name}已经收拾好情绪，准备离开原处。",
                        "location": payload["角色"]["当前地点"],
                        "availability": "incoming" if name == "沈砚" else "reachable",
                        "encounter_hook": f"{name}留下了一道可以追过去的动静。",
                    }, ensure_ascii=False))
                return ReplySnapshot(content=json.dumps({
                    "world_time": "港钟响过一刻",
                    "narration": "钟声越过屋脊，仍在等待的人终于开始移动，港口重新排列了下一次相遇。",
                    "player_condition": payload["玩家当前状态"].get("condition", ""),
                    "visible_traces": payload["玩家当前状态"].get("visible_traces", []),
                }, ensure_ascii=False))

        self.assistant_turn("沈砚把钥匙收进掌心。", {
            "event": {
                "title": "钥匙被收起", "details": "沈砚收起了钥匙。",
                "visibility": "private", "participants": ["沈砚"], "witnesses": ["沈砚"],
            },
            "scene": {}, "relationship": {}, "player": {},
        })
        client = FakePulseClient()
        result = asyncio.run(advance_world_pulse(
            store=self.store, client=client, profile={"max_tokens": 16384},
            world_id=self.world_id, target_time="次日清晨",
        ))

        world = self.store.get_world(self.world_id)
        self.assertEqual(result["pulse_number"], 1)
        self.assertEqual(world["world"]["current_time"], "港钟响过一刻")
        self.assertIn("开始移动", world["pulse"]["narration"])
        shenyan = next(item for item in world["characters"] if item["id"] == "char_shenyan")
        self.assertEqual(shenyan["availability"], "incoming")
        self.assertIn("追过去", shenyan["encounter_hook"])
        self.assertNotIn("intention", shenyan)
        self.assertIn(
            "主动确认玩家的去向",
            self.store.context_for_turn(self.conversation_id)["character_world_state"]["intention"],
        )
        self.assertEqual(
            client.character_payloads["沈砚"]["世界"]["用户希望推进到的时刻"],
            "次日清晨",
        )
        self.assertEqual(world["pending_sync_count"], 0)
        self.assertTrue(any(
            entry["title"] == "钥匙被收起"
            for entry in client.character_payloads["沈砚"]["此前认知"]
        ))
        self.assertFalse(any(
            entry["title"] == "钥匙被收起"
            for entry in client.character_payloads["岑野"]["此前认知"]
        ))

    def test_world_pulse_skips_character_whose_action_fails(self) -> None:
        class PartialPulseClient:
            async def response(self, context, profile):
                payload = json.loads(context.messages[0].content)
                if "独立行动决定" in context.system:
                    if payload["角色"]["姓名"] == "沈砚":
                        raise ProviderError("provider", "模型暂时不可用", retryable=True)
                    return ReplySnapshot(content=json.dumps({
                        "participation": "active",
                        "active_reason": "visible_change",
                        "active_source": "availability",
                        "intention": "继续自己的生活",
                        "surface_state": "一切如常。",
                        "location": payload["角色"]["当前地点"],
                        "availability": "incoming",
                        "encounter_hook": "港口依旧可以前往。",
                    }, ensure_ascii=False))
                return ReplySnapshot(content=json.dumps({
                    "world_time": "次日清晨",
                    "narration": "夜色退去，港口重新热闹起来。",
                    "player_condition": "",
                    "visible_traces": [],
                }, ensure_ascii=False))

        result = asyncio.run(advance_world_pulse(
            store=self.store, client=PartialPulseClient(), profile={"max_tokens": 16384},
            world_id=self.world_id,
        ))

        self.assertEqual(result["pulse_number"], 1)
        self.assertNotIn("char_shenyan", result["updated_characters"])
        self.assertIn("char_cenye", result["updated_characters"])
        self.assertIn("char_linzhao", result["updated_characters"])
        world = self.store.get_world(self.world_id)
        self.assertIn("重新热闹", world["pulse"]["narration"])
        cenye = next(item for item in world["characters"] if item["id"] == "char_cenye")
        self.assertIn("前往", cenye["encounter_hook"])

    def test_world_pulse_fails_when_every_character_action_fails(self) -> None:
        class BrokenPulseClient:
            async def response(self, context, profile):
                raise ProviderError("provider", "模型不可用", retryable=True)

        with self.assertRaises(ProviderError):
            asyncio.run(advance_world_pulse(
                store=self.store, client=BrokenPulseClient(), profile={"max_tokens": 16384},
                world_id=self.world_id,
            ))
        self.assertEqual(self.store.get_world(self.world_id)["pulse"]["narration"], "")

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

    def test_world_sync_retries_reasoning_only_empty_summary(self) -> None:
        class BudgetAwareSummaryClient:
            def __init__(self) -> None:
                self.budgets: list[int] = []

            async def response(self, context, profile):
                self.budgets.append(context.max_tokens)
                if context.max_tokens < 4096:
                    return ReplySnapshot(reasoning="仍在推理", finish_reason="length")
                return ReplySnapshot(content="他收起原先的漫不经心，开始留意港口传来的动静。")

        self.assistant_turn("港口的钟声已经响起。", {
            "event": {
                "title": "港钟响起", "details": "第七码头的钟声传遍绯港。",
                "visibility": "public", "participants": ["沈砚"], "witnesses": ["沈砚"],
            },
            "scene": {}, "relationship": {},
        })
        client = BudgetAwareSummaryClient()
        result = asyncio.run(sync_world_cognition(
            store=self.store, client=client, profile={"max_tokens": 16384},
            world_id=self.world_id,
        ))

        self.assertEqual(result["synced_events"], 1)
        self.assertIn(2048, client.budgets)
        self.assertIn(4096, client.budgets)
        self.assertTrue(result["summaries"])
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

    def test_reset_world_clears_story_progress_but_keeps_authored_content(self) -> None:
        original = self.store.get_world(self.world_id)
        original_character_ids = {item["id"] for item in original["characters"]}
        original_location_ids = {item["id"] for item in original["locations"]}
        original_time = original["world"]["current_time"]

        assistant_id = self.assistant_turn("沈砚走进鸦色会所。", {
            "event": {
                "title": "沈砚抵达会所", "details": "沈砚已经来到鸦色会所。",
                "visibility": "public", "participants": ["沈砚"], "witnesses": ["沈砚"],
            },
            "scene": {
                "location_id": "loc_club", "world_time": "钟响之后", "tension": 64,
                "active_threads": ["尚未说出口的条件"],
            },
            "relationship": {
                "trust_delta": 5, "desire_delta": 5, "tension_delta": 7,
                "label": "危险的同盟", "note": "在会所重新谈了条件。",
            },
        })
        self.assertTrue(assistant_id)
        asyncio.run(sync_world_cognition(
            store=self.store, client=FakeSummaryClient(), profile={"max_tokens": 512},
            world_id=self.world_id,
        ))
        room = self.store.create_room(
            self.world_id, "会所夜谈", "loc_club", ["char_shenyan", "char_cenye"]
        )
        self.store.add_room_message(room["room"]["id"], "user", None, "都留下。")

        with self.assertRaisesRegex(ValueError, "reset_confirmation_mismatch"):
            self.store.reset_world(self.world_id, "错误名字")
        self.assertTrue(self.store.get_conversation(self.conversation_id)["messages"])

        result = self.store.reset_world(self.world_id, original["world"]["name"])
        reset = result["world"]
        self.assertGreaterEqual(result["cleared"]["messages"], 2)
        self.assertEqual(result["cleared"]["events"], 1)
        self.assertEqual(result["cleared"]["rooms"], 1)
        self.assertEqual(reset["world"]["current_time"], original_time)
        self.assertEqual({item["id"] for item in reset["characters"]}, original_character_ids)
        self.assertEqual({item["id"] for item in reset["locations"]}, original_location_ids)
        self.assertEqual(reset["events"], [])
        self.assertEqual(reset["pending_sync_count"], 0)
        self.assertEqual(self.store.list_rooms(self.world_id), [])
        shenyan = next(item for item in reset["characters"] if item["id"] == "char_shenyan")
        self.assertEqual(shenyan["current_location_id"], "loc_house")
        self.assertEqual(shenyan["state_summary"], shenyan["tagline"])
        relationship = next(
            item for item in reset["relationships"]
            if item["from_character_id"] == "char_shenyan" and item["to_user"]
        )
        self.assertEqual(
            (relationship["label"], relationship["trust"], relationship["desire"], relationship["tension"], relationship["notes"]),
            ("未命名的牵引", 10, 18, 12, "关系会由共同经历塑形。"),
        )
        with self.assertRaises(KeyError):
            self.store.get_conversation(self.conversation_id)
        fresh_conversation = self.store.get_or_create_conversation(
            self.world_id, "char_shenyan"
        )
        self.assertNotEqual(fresh_conversation, self.conversation_id)
        self.assertEqual(self.store.get_conversation(fresh_conversation)["messages"], [])

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
        self.assertIn("第三人称限知叙述", request.system)
        roleplay = roleplay_system(context)
        self.assertIn("不要写“我抬手”", roleplay)
        self.assertIn("400–900", roleplay)
        self.assertIn("本轮明确写出的动作视为已经发生", roleplay)
        self.assertIn("阶段结果或余波", roleplay)
        self.assertIn("软状态", roleplay)
        adjudication = patch_context(
            context, "沈砚停在门边。", {"max_tokens": 16000}
        )
        self.assertEqual(adjudication.max_tokens, 16000)
        self.assertIn("硬状态真正改变", adjudication.system)
        self.assertIn("commitments 只记录玩家亲口作出的明确承诺", adjudication.system)

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

    def test_new_pulse_creates_one_reentry_opening_per_conversation(self) -> None:
        initial, created = self.store.add_opening_message(
            self.conversation_id, "沈砚在钟响前看向门边。"
        )
        self.assertTrue(created)
        self.assertFalse(self.store.get_conversation(self.conversation_id)["needs_scene_entry"])

        self.store.commit_world_pulse(
            self.world_id,
            [],
            {
                "char_shenyan": {
                    "intention": "去窗边等玩家回来",
                    "surface_state": "沈砚换下外套，仍没有离开。",
                    "location_id": "loc_house",
                    "availability": "reachable",
                    "encounter_hook": "夜航公馆的窗重新亮了。",
                },
            },
            {
                "world_time": "港钟响过一刻",
                "narration": "钟声过去，绯港进入了新的时段。",
                "player_condition": "",
                "visible_traces": [],
            },
        )

        waiting = self.store.get_conversation(self.conversation_id)
        self.assertTrue(waiting["needs_scene_entry"])
        self.assertEqual(waiting["pulse"]["number"], 1)
        context = self.store.context_for_turn(self.conversation_id)
        self.assertEqual(context["scene"]["world_time"], "港钟响过一刻")
        request = opening_request_context(context, {"max_tokens": 16000}, reentry=True)
        self.assertIn("新时段接续开场", request.system)
        self.assertIn("夜航公馆的窗重新亮了", request.system)
        self.assertIn(initial["content"], request.system)
        grounding = scene_entry_grounding_context(
            context,
            "沈砚从玩家鞋底的青苔认出他刚去过第七码头。",
            {"max_tokens": 16000},
        )
        grounding_payload = json.loads(grounding.messages[0].content)
        self.assertEqual(grounding_payload["memory_ledger"]["player"]["visible_traces"], [])
        self.assertNotIn(
            "encounter_hook",
            grounding_payload["memory_ledger"]["character_action_state"],
        )
        self.assertNotIn("pulse_narration", grounding.messages[0].content)
        self.assertIn("脚印或苔藓", grounding.system)
        self.assertIn("都不是事实凭证", grounding.system)
        self.assertIn("软连续性", grounding.system)
        self.assertEqual(
            parse_scene_entry_grounding(json.dumps({
                "text": "沈砚抬眼看向来人。",
                "removed": ["无来源的鞋底青苔"],
            }, ensure_ascii=False))["text"],
            "沈砚抬眼看向来人。",
        )

        reentry, reentry_created = self.store.add_opening_message(
            self.conversation_id, "港钟已经响过，沈砚仍站在亮着的窗边。"
        )
        duplicate, duplicate_created = self.store.add_opening_message(
            self.conversation_id, "同一轮不应再生成一次。"
        )
        self.assertTrue(reentry_created)
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate["id"], reentry["id"])
        conversation = self.store.get_conversation(self.conversation_id)
        self.assertFalse(conversation["needs_scene_entry"])
        self.assertEqual(conversation["scene_entry"]["entry_kind"], "reentry")
        self.assertEqual(
            [item["content"] for item in conversation["messages"]],
            [initial["content"], reentry["content"]],
        )

    def test_reentry_is_saved_only_after_grounding_review(self) -> None:
        self.store.add_opening_message(self.conversation_id, "沈砚在窗边等了一夜。")
        self.store.commit_world_pulse(
            self.world_id,
            [],
            {},
            {
                "world_time": "次日清晨",
                "narration": "雾在港口慢慢散开。",
                "player_condition": "",
                "visible_traces": [],
            },
        )

        class AuditClient:
            def __init__(self) -> None:
                self.valid_audit = False
                self.calls = 0

            async def response(self, context, profile):
                self.calls += 1
                if "记忆校对员" not in context.system:
                    return ReplySnapshot(
                        content="沈砚看见玩家鞋底的青苔，认出他刚从第七码头回来。"
                    )
                if not self.valid_audit:
                    return ReplySnapshot(content="无法校对")
                return ReplySnapshot(content=json.dumps({
                    "text": "沈砚从窗边转过身，目光停在来人身上。",
                    "removed": ["无来源的鞋底青苔和玩家路线"],
                }, ensure_ascii=False))

        client = AuditClient()
        with patch("gongjie.api.routes.conversations.store", self.store), patch(
            "gongjie.api.routes.conversations.active_profile",
            lambda: {"max_tokens": 16000},
        ), patch("gongjie.api.routes.conversations.provider_client", client):
            with self.assertRaises(HTTPException):
                asyncio.run(generate_opening(self.conversation_id))
            after_failure = self.store.get_conversation(self.conversation_id)
            self.assertTrue(after_failure["needs_scene_entry"])
            self.assertEqual(len(after_failure["messages"]), 1)

            client.valid_audit = True
            result = asyncio.run(generate_opening(self.conversation_id))

        self.assertEqual(client.calls, 5)
        self.assertTrue(result["created"])
        self.assertEqual(
            result["message"]["content"],
            "沈砚从窗边转过身，目光停在来人身上。",
        )
        self.assertNotIn(
            "青苔", self.store.get_conversation(self.conversation_id)["messages"][-1]["content"]
        )

    def test_quiet_character_is_not_forced_into_pulse_narration(self) -> None:
        class QuietPulseClient:
            def __init__(self) -> None:
                self.transition_payload: dict = {}

            async def response(self, context, profile):
                payload = json.loads(context.messages[0].content)
                if "独立行动决定" in context.system:
                    name = payload["角色"]["姓名"]
                    if name == "岑野":
                        return ReplySnapshot(content=json.dumps({
                            "participation": "active",
                            "active_reason": "new_fact",
                            "active_source": "event_that_does_not_exist",
                            "intention": "去追查一条并不存在的线索",
                            "surface_state": "岑野忽然开始调查。",
                            "location": payload["角色"]["当前地点"],
                            "availability": "reachable",
                            "encounter_hook": "出现了一条新线索。",
                        }, ensure_ascii=False))
                    return ReplySnapshot(content=json.dumps({
                        "participation": "active",
                        "active_reason": "visible_change",
                        "active_source": "availability",
                        "intention": "准备离开",
                        "surface_state": f"{name}已经准备动身。",
                        "location": payload["角色"]["当前地点"],
                        "availability": "incoming",
                        "encounter_hook": f"{name}所在的地方有了新的动静。",
                    }, ensure_ascii=False))
                self.transition_payload = payload
                return ReplySnapshot(content=json.dumps({
                    "world_time": "次日清晨",
                    "narration": "雾在清晨慢慢散开。",
                    "player_condition": "",
                    "visible_traces": [],
                }, ensure_ascii=False))

        self.store.commit_world_pulse(
            self.world_id,
            [],
            {
                "char_cenye": {
                    "intention": "仍在路上",
                    "surface_state": "岑野尚未抵达。",
                    "location_id": "loc_club",
                    "availability": "incoming",
                    "encounter_hook": "",
                }
            },
            {
                "world_time": "深夜",
                "narration": "夜色仍深。",
                "player_condition": "",
                "visible_traces": [],
            },
        )
        client = QuietPulseClient()
        asyncio.run(advance_world_pulse(
            store=self.store, client=client, profile={"max_tokens": 16384},
            world_id=self.world_id,
        ))

        visible_names = {
            item["角色"]
            for item in client.transition_payload["角色在下一时段可被玩家察觉的变化"]
        }
        self.assertNotIn("岑野", visible_names)
        cenye = next(
            item for item in self.store.get_world(self.world_id)["characters"]
            if item["id"] == "char_cenye"
        )
        self.assertEqual(cenye["availability"], "incoming")
        self.assertEqual(cenye["encounter_hook"], "")


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
