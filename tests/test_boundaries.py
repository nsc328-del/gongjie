from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from gongjie.ai.providers import ProviderError, ReplySnapshot
from gongjie.api.routes import conversations, worlds
from gongjie.api.schemas import PulseInput, WorldInput
from gongjie.cognition.compiler import (
    opening_request_context, request_context, room_turn_request_context,
    scene_entry_grounding_context,
)
from gongjie.cognition.pulse import character_pulse_context
from gongjie.cognition.sync import summary_context
from gongjie.domain.patches import PatchValidationError
from gongjie.scenes.adjudication import extract_patch
from gongjie.scenes.rooms import stream_room_turn
from gongjie.scenes.settlements import recover_settlements
from gongjie.scenes.turns import stream_turn
from gongjie.storage.store import Store


PROFILE = {"max_tokens": 4096}


def event_patch(name: str, text: str) -> dict:
    return {
        "event": {
            "title": "钥匙交接", "details": text, "visibility": "private",
            "participants": [name], "witnesses": [name],
            "knowledge_sources": {name: {"role": "assistant", "quote": text, "kind": "witnessed"}},
        },
        "relationship": {"trust_delta": 2},
        "evidence": {
            key: {"role": "assistant", "quote": text, "kind": "occurred"}
            for key in ("event", "relationship")
        },
    }


def wire_patch(value):
    value = deepcopy(value)
    for citation in [*value.get("evidence", {}).values(), *value.get("event", {}).get("knowledge_sources", {}).values()]:
        citation["source_id"] = citation.pop("role") + "_0"
        citation.pop("quote")
    for path, citation in value.pop("evidence", {}).items():
        value[path]["evidence"] = citation
    return value


class RecoveryClient:
    def __init__(self):
        self.failed = True
        self.transcripts = []

    async def stream(self, context, profile):
        yield ReplySnapshot(content="洛恩·维瑟尔把钥匙交给玩家。", finish_reason="stop")

    async def response(self, context, profile):
        if '你是同场观察编译员' in context.system:
            value = json.loads(context.messages[0].content)
            return ReplySnapshot(content=json.dumps({member['id']: {'quotes': [value['content']]} for member in value['members']}, ensure_ascii=False), finish_reason='stop')
        if '你是同场观察核对员' in context.system:
            return ReplySnapshot(content='{"approved":true,"issues":[]}', finish_reason='stop')
        if '你是场景规划员' in context.system:
            return ReplySnapshot(content='{"mode":"stay"}')
        if "硬状态证据核对员" in context.system:
            return ReplySnapshot(content='{"approved":true,"issues":[]}')
        if "状态裁判" in context.system:
            self.transcripts.append(context.messages[0].content)
            if self.failed:
                raise ProviderError("provider", "private provider details")
            sources, _ = json.JSONDecoder().raw_decode(context.messages[0].content.split("本轮来源表：\n", 1)[1])
            text = ''.join(value['quote'] for key, value in sources.items() if key.startswith('assistant_'))
            name = context.messages[0].content.split("角色：", 1)[1].splitlines()[0]
            return ReplySnapshot(content=json.dumps(wire_patch(event_patch(name, text)), ensure_ascii=False))
        name = "洛恩·维瑟尔" if "你是洛恩·维瑟尔" in context.system else "伊芙琳·薇尔"
        return ReplySnapshot(content=json.dumps({"reply": f"{name}把钥匙交给玩家。"}, ensure_ascii=False), finish_reason='stop')


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "world.db")
        self.store.initialize()
        self.world_id = "world_daylight"
        self.conv = self.store.get_or_create_conversation(self.world_id, "daylight_lorne")

    def tearDown(self):
        self.temp.cleanup()

    async def drain(self, stream):
        return [json.loads(packet) async for packet in stream]

    def turn(self, client, content="把钥匙给我。"):
        return stream_turn(store=self.store, provider_client=client,
                           profile_loader=lambda: PROFILE, conversation_id=self.conv,
                           content=content, continuation=False,
                           is_disconnected=lambda: asyncio.sleep(0))

    def test_beliefs_and_other_locations_never_enter_character_requests(self):
        room = self.store.create_room(self.world_id, "会面", "daylight_throne", ["daylight_lorne", "daylight_evelyn"])
        with self.store.transaction() as db:
            for visibility in ("public", "private", "secret"):
                event_id = f"event_{visibility}"
                db.execute("INSERT INTO world_events VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                    event_id, self.world_id, None, None, "客观标题暗号", "真正的钥匙藏在红盒子",
                    visibility, "daylight_garden", "真正的时刻暗号", 0, "2026-01-01",
                ))
                db.execute("INSERT INTO character_knowledge VALUES (?,?,?,?,?,?,?,?)", (
                    f"know_{visibility}", event_id, "daylight_lorne", "misunderstood",
                    "钥匙已经丢进海里", 0.7, '{"quote":"真正的钥匙藏在红盒子"}', "2026-01-01",
                ))
            db.execute("UPDATE world_flow_states SET pulse_narration='真正的钥匙藏在红盒子' WHERE world_id=?", (self.world_id,))
            db.execute("UPDATE characters SET current_location_id='daylight_garden' WHERE id='daylight_severin'")
        context = self.store.context_for_turn(self.conv)
        room_context = self.store.room_character_context(room["room"]["id"], "daylight_lorne")
        sync = self.store.cognition_sync_context(self.world_id)
        item = next(c for c in sync["characters"] if c["character"]["id"] == "daylight_lorne")
        requests = [
            request_context(context, PROFILE), opening_request_context(context, PROFILE),
            opening_request_context(context, PROFILE, reentry=True),
            scene_entry_grounding_context(context, "洛恩·维瑟尔望向窗外。", PROFILE),
            room_turn_request_context(room_context, PROFILE),
            character_pulse_context(item, sync, PROFILE, 4096),
            summary_context(item, sync["world"], PROFILE),
        ]
        for request in requests:
            text = request.system + "\n" + "\n".join(m.content for m in request.messages)
            with self.subTest(request=request.system[:30]):
                self.assertIn("钥匙已经丢进海里", text)
                for secret in ("客观标题暗号", "真正的钥匙藏在红盒子", "真正的时刻暗号"):
                    self.assertNotIn(secret, text)
        self.assertTrue(all(set(c) == {"id", "name", "age", "role"} for c in context["characters"]))
        self.store.commit_cognition_sync(self.world_id, ["event_public"], {})
        refreshed = self.store.context_for_turn(self.conv)
        self.assertEqual(refreshed["public_events"][0]["details"], "钥匙已经丢进海里")
        cenye = self.store.get_or_create_conversation(self.world_id, "daylight_severin")
        self.assertEqual(self.store.context_for_turn(cenye)["public_events"][0]["details"], "真正的钥匙藏在红盒子")
        self.assertEqual(self.store.get_world(self.world_id)["events"][0]["details"], "真正的钥匙藏在红盒子")

    def test_invalid_hard_changes_are_atomic_and_valid_changes_are_idempotent(self):
        self.store.add_message(self.conv, "user", "把钥匙给我。")
        text = "洛恩·维瑟尔把钥匙交给玩家。"
        message = self.store.add_message(self.conv, "assistant", text)
        valid = event_patch("洛恩·维瑟尔", text)
        invalid = []
        value = deepcopy(valid); value["evidence"]["event"]["kind"] = "intent"; invalid.append(value)
        value = deepcopy(valid); value["evidence"]["event"]["quote"] = "并未出现的证据"; invalid.append(value)
        value = deepcopy(valid); value["relationship"]["trust_delta"] = 99; invalid.append(value)
        value = deepcopy(valid); value["scene"] = {"location_id": "not-in-this-world"}; invalid.append(value)
        value = deepcopy(valid); value["event"]["witnesses"].append("塞维安·雪莱"); invalid.append(value)
        value = deepcopy(valid); value["event"]["visibility"] = "secret"; value["event"].pop("knowledge_sources"); invalid.append(value)
        value = deepcopy(valid); value["player"] = {"commitments": ["明天回来"]}; value["evidence"]["player.commitments"] = value["evidence"]["event"]; invalid.append(value)
        before = self.store.get_world(self.world_id)
        for proposal in invalid:
            with self.subTest(proposal=proposal), self.assertRaises(PatchValidationError):
                self.store.apply_world_patch(self.conv, message["id"], proposal)
            self.assertEqual(self.store.get_world(self.world_id), before)
        first = self.store.apply_world_patch(self.conv, message["id"], valid)
        second = self.store.apply_world_patch(self.conv, message["id"], valid)
        self.assertEqual(first["settlement"]["id"], second["settlement"]["id"])
        self.assertEqual(len(self.store.get_conversation(self.conv)["settlements"]), 1)
        self.assertEqual(next(r for r in self.store.get_world(self.world_id)["relationships"] if r["from_character_id"] == "daylight_lorne")["trust"], 10)

    def test_room_membership_does_not_grant_secret_knowledge(self):
        room = self.store.create_room(self.world_id, "会面", "daylight_throne", ["daylight_lorne", "daylight_evelyn"])
        room_id = room["room"]["id"]
        text = "洛恩·维瑟尔遮住掌心，把钥匙悄悄交给玩家。"
        context = self.store.room_character_context(room_id, 'daylight_lorne')
        views = {'daylight_lorne': text, 'daylight_evelyn': ''}
        context['observation_sources'] = {cid: {'assistant': view} for cid, view in views.items()}
        message = self.store.add_room_message(room_id, "assistant", "daylight_lorne", text, observations=views)
        proposal = event_patch("洛恩·维瑟尔", text)
        proposal["event"]["visibility"] = "secret"
        forged = deepcopy(proposal)
        forged["event"]["participants"].append("塞维安·雪莱")
        with self.assertRaises(PatchValidationError):
            self.store.apply_room_patch(room_id, "daylight_lorne", message["id"], forged, context=context)
        self.store.apply_room_patch(room_id, "daylight_lorne", message["id"], proposal, context=context)
        other = self.store.room_character_context(room_id, "daylight_evelyn")
        self.assertFalse(other["knowledge"])
        self.assertNotIn(text, room_turn_request_context(other, PROFILE).messages[0].content)

    def test_semantic_review_rejects_real_quote_of_uncompleted_action(self):
        text = "洛恩·维瑟尔说：如果你离开，我就把钥匙扔进海里。"
        proposal = event_patch("洛恩·维瑟尔", text)
        proposal["event"]["details"] = "钥匙已经被扔进海里。"
        class Client:
            async def response(self, context, profile):
                if '你是场景规划员' in context.system:
                    return ReplySnapshot(content='{"mode":"stay"}')
                return ReplySnapshot(content=json.dumps(
                    {"approved": False, "issues": ["条件式威胁不能证明钥匙已经被扔进海里。"]}
                    if "硬状态证据核对员" in context.system else wire_patch(proposal),
                    ensure_ascii=False,
                ))
        with self.assertRaisesRegex(PatchValidationError, "hard_state_not_supported"):
            asyncio.run(extract_patch(Client(), self.store.context_for_turn(self.conv), text, PROFILE))

    def test_failed_settlement_survives_restart_blocks_progress_and_recovers_once(self):
        client = RecoveryClient()
        events = asyncio.run(self.drain(self.turn(client)))
        self.assertIn("patch_skipped", [e["type"] for e in events])
        self.store = Store(self.store.path)
        self.store.initialize()
        history = self.store.get_conversation(self.conv)
        self.assertEqual(history["messages"][-1]["status"], "complete")
        self.assertEqual(history["messages"][-1]["settlement_job"]["status"], "failed")
        self.assertNotIn("private provider details", json.dumps(history))
        blocked = asyncio.run(self.drain(self.turn(client, "不应被保存的新消息")))
        self.assertEqual([e["type"] for e in blocked], ["error"])
        self.assertEqual(len(self.store.get_conversation(self.conv)["messages"]), 2)
        with patch.object(worlds, "store", self.store), patch.object(worlds, "active_profile", lambda: PROFILE):
            with self.assertRaises(HTTPException):
                asyncio.run(worlds.pulse(self.world_id, PulseInput()))
            with self.assertRaises(HTTPException):
                asyncio.run(worlds.update_world(self.world_id, WorldInput(name="不可覆盖")))
        client.failed = False
        result = asyncio.run(recover_settlements(self.store, client, PROFILE, self.world_id))
        self.assertEqual(len(result["completed"]), 1)
        self.assertFalse(result["pending_settlements"])
        self.assertEqual(client.transcripts[0], client.transcripts[-1])
        again = asyncio.run(recover_settlements(self.store, client, PROFILE, self.world_id))
        self.assertFalse(again["completed"])
        history = self.store.get_conversation(self.conv)
        self.assertEqual(len(history["settlements"]), 1)
        self.assertEqual(history["settlements"][0]["evidence"]["event"]["quote"], history["messages"][-1]["content"])

    def test_disconnect_after_body_preserves_failed_background_job(self):
        async def disconnect():
            stream = self.turn(RecoveryClient())
            async for event in stream:
                if json.loads(event)["type"] == "assistant_complete":
                    await stream.aclose()
                    break
        asyncio.run(disconnect())
        history = self.store.get_conversation(self.conv)
        self.assertEqual(history["messages"][-1]["status"], "complete")
        self.assertEqual(history["messages"][-1]["settlement_job"]["status"], "failed")
        client = RecoveryClient(); client.failed = False
        self.assertEqual(len(asyncio.run(recover_settlements(self.store, client, PROFILE, self.world_id))["completed"]), 1)

    def test_room_replies_continue_and_recovery_preserves_fifo(self):
        room = self.store.create_room(self.world_id, "会面", "daylight_throne", ["daylight_lorne", "daylight_evelyn"])
        room_id = room["room"]["id"]
        client = RecoveryClient()
        events = asyncio.run(self.drain(stream_room_turn(
            store=self.store, provider_client=client, profile_loader=lambda: PROFILE,
            room_id=room_id, content="把钥匙给我。", is_disconnected=lambda: asyncio.sleep(0),
        )))
        replies = [e["message"]["id"] for e in events if e["type"] == "room_reply"]
        self.assertEqual(len(replies), 2)
        self.assertEqual([j["source_id"] for j in self.store.pending_settlements(self.world_id)], replies)
        self.assertFalse(self.store.get_room(room_id)["settlements"])
        client.failed = False
        result = asyncio.run(recover_settlements(self.store, client, PROFILE, self.world_id))
        self.assertEqual(result["completed"], replies)
        self.assertEqual(len(self.store.get_room(room_id)["settlements"]), 2)
        self.assertNotIn("伊芙琳·薇尔把钥匙交给玩家", client.transcripts[-2])
        world = self.store.get_world(self.world_id)
        self.store.reset_world(self.world_id, world["world"]["name"])
        self.assertFalse(self.store.pending_settlements(self.world_id))
