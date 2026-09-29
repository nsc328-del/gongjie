from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from gongjie.cognition.compiler import (
    opening_request_context,
    request_context,
    room_turn_request_context,
    scene_entry_grounding_context,
)
from gongjie.cognition.places import planning_request
from gongjie.ai.providers import ReplySnapshot
from gongjie.cognition.pulse import advance_world_pulse, character_pulse_context
from gongjie.cognition.sync import summary_context
from gongjie.domain.knowledge import knowledge_projection
from gongjie.domain.locations import known_locations, location_view
from gongjie.scenes.adjudication import patch_context, source_catalog
from gongjie.storage.location_knowledge import mentioned_locations
from gongjie.storage.store import Store


class InformationProjectionTests(unittest.TestCase):
    def test_heard_alias_survives_commit_reentry_and_pulse_without_registry_name(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'alias.db')
            store.initialize()
            world, listener, place_id = 'world_daylight', 'daylight_evelyn', 'place_old_house'
            secret_name, heard_name = '王后密谋会所', '旧屋'
            with store.transaction() as db:
                db.execute('INSERT INTO locations(id,world_id,name,description,mood,sort_order,geography) VALUES (?,?,?,?,?,?,?)', (
                    place_id, world, secret_name, '登记者独知的内室与路线', '', 7,
                    json.dumps({'generated':True,'parent_id':world,'aliases':[heard_name],
                                'known_by':['daylight_lorne']}, ensure_ascii=False),
                ))
            conv = store.get_or_create_conversation(world, listener)
            store.add_message(conv, 'user', '我只听说旧屋这个名字，不知道怎么去。')
            with store.read() as db:
                context = mentioned_locations(db, store.context_for_turn(conv))
            self.assertEqual(context['location_mentions'][0]['name'], heard_name)
            self.assertEqual(context['location_mentions'][0]['id'], place_id)
            self.assertNotIn(secret_name, json.dumps(context, ensure_ascii=False))
            body = '伊芙琳点头：“我记住旧屋这个名字了。”'
            message = store.add_message(conv, 'assistant', body)
            store.apply_world_patch(conv, message['id'], {'event':None}, context=context)
            store = Store(store.path)
            store.initialize()
            reopened = store.context_for_turn(conv)
            place = next(p for p in reopened['locations'] if p['id'] == place_id)
            self.assertEqual(place['name'], heard_name)
            self.assertFalse(location_view(place)['route_known'])
            self.assertNotIn(secret_name, json.dumps(reopened, ensure_ascii=False))

            # Explicitly gathering at this ID grants arrival, not a new name.
            room = store.create_room(world, '碰面', place_id, [listener])['room']['id']
            room_context = store.room_character_context(room, listener)
            self.assertEqual(room_context['character']['location_name'], heard_name)
            self.assertEqual(room_context['scene']['location_name'], heard_name)
            self.assertEqual(room_context['player']['location_name'], heard_name)
            self.assertNotIn(secret_name, json.dumps(room_context, ensure_ascii=False))
            store.get_or_create_conversation(world, listener)
            private = store.context_for_turn(conv)
            self.assertEqual(private['character']['location_name'], heard_name)
            self.assertEqual(private['player']['location_name'], heard_name)
            sync = store.cognition_sync_context(world)
            item = next(c for c in sync['characters'] if c['character']['id'] == listener)
            self.assertEqual(item['character']['location_name'], heard_name)
            requests = [request_context(private, {}), opening_request_context(private, {}),
                        room_turn_request_context(room_context, {}),
                        character_pulse_context(item, sync, {}, 4096)]
            for request in requests:
                serialized = request.system + '\n' + '\n'.join(m.content for m in request.messages)
                self.assertNotIn(secret_name, serialized)
                self.assertNotIn('登记者独知的内室与路线', serialized)
            other = next(c for c in sync['characters'] if c['character']['id'] == 'daylight_vesper')
            self.assertNotIn(place_id, {p['id'] for p in other['locations']})
            with store.read() as db:
                places = db.execute('SELECT name,geography FROM locations WHERE id=?', (place_id,)).fetchall()
            self.assertEqual(len(places), 1)
            self.assertEqual(places[0]['name'], secret_name)
            self.assertEqual(json.loads(places[0]['geography'])['known_versions'][listener]['name'], heard_name)

    def test_observed_fragment_can_be_cited_without_other_peoples_whispers(self):
        context = {'room':{'id':'room'},'turn_user_id':'u','messages':[{'id':'u','role':'user','content':'大家喝茶吧。'}],
            'observation_sources':{
                'lorne':{'user':'大家喝茶吧。','assistant':'他递出纸条，纸上写着青铜海鸥。'},
                'evelyn':{'user':'我只对伊芙琳说，暗号是银鹿。\n大家喝茶吧。','assistant':'他递出纸条'},
            }}
        sources = source_catalog(context,'他递出纸条，纸上写着青铜海鸥。')
        self.assertIn({'role':'assistant','quote':'他递出纸条'},sources.values())
        self.assertNotIn('银鹿',json.dumps(sources,ensure_ascii=False))

    def test_partial_place_view_does_not_retain_registry_or_other_characters_versions(self):
        place = {
            "id": "place_side_room", "name": "北侧小室", "description": "登记者记录的隐秘环境", "mood": "隐秘气氛",
            "geography": {
                "generated": True, "parent_id": "hidden_parent", "aliases": ["隐藏别名"],
                "known_by": ["lorne", "evelyn"],
                "known_versions": {
                    "evelyn": {"description": "玩家只提过北侧小室这个名字", "parent_id": None, "aliases": []},
                    "lorne": {"description": "洛恩独知的路线暗号", "parent_id": "hidden_parent", "aliases": ["隐藏别名"]},
                },
                "knowledge_sources": {"lorne": {"quote": "洛恩独知的路线暗号"}},
            },
        }
        projected = known_locations([place], "evelyn")
        serialized = json.dumps(projected, ensure_ascii=False)
        for hidden in ("登记者记录的隐秘环境", "隐秘气氛", "hidden_parent", "隐藏别名", "洛恩独知的路线暗号"):
            self.assertNotIn(hidden, serialized)
        self.assertEqual(location_view(projected[0])["description"], "玩家只提过北侧小室这个名字")
        self.assertFalse(location_view(projected[0])["route_known"])
        self.assertEqual(known_locations(projected, "evelyn"), projected)
        self.assertEqual(known_locations(projected, "lorne"), [])
        self.assertIn("known_versions", place["geography"])
        place["geography"]["known_versions"]["evelyn"]["route_known"] = True
        arrived = known_locations([place], "evelyn")
        self.assertTrue(location_view(arrived[0])["route_known"])
        self.assertEqual(known_locations(arrived, "evelyn"), arrived)
        self.assertNotIn("登记者记录的隐秘环境", json.dumps(arrived, ensure_ascii=False))

    def test_pulse_cannot_move_or_publish_arrival_at_a_name_only_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "pulse.db")
            store.initialize()
            world_id, character_id = "world_daylight", "daylight_evelyn"
            with store.transaction() as db:
                geo = {
                    "generated": True, "parent_id": world_id, "known_by": [character_id],
                    "known_versions": {character_id: {
                        "description": "玩家说过北侧小室这个名字。", "parent_id": None, "aliases": [],
                    }},
                }
                db.execute("INSERT INTO locations(id,world_id,name,description,mood,sort_order,geography) VALUES (?,?,?,?,?,?,?)", (
                    "place_mentioned", world_id, "北侧小室", "只有登记者知道的详细环境", "", 7,
                    json.dumps(geo, ensure_ascii=False),
                ))
                db.execute("INSERT INTO world_events VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                    "event_notice", world_id, None, None, "公开钟声", "城中钟声响起。", "public",
                    None, "暮钟后", 0, "2026-09-12T00:00:00",
                ))
            context = store.cognition_sync_context(world_id)
            item = next(c for c in context["characters"] if c["character"]["id"] == character_id)
            self.assertIn("place_mentioned", {p["id"] for p in item["locations"]})
            payload = json.loads(character_pulse_context(item, context, {}, 4096).messages[0].content)
            self.assertNotIn("北侧小室", payload["可选地点"])
            self.assertNotIn("只有登记者知道的详细环境", json.dumps(payload, ensure_ascii=False))

            class Client:
                public_actions = []

                async def response(self, request, profile):
                    data = json.loads(request.messages[0].content)
                    if "独立行动决定" in request.system:
                        if data["角色"]["姓名"] == "伊芙琳·薇尔":
                            value = {
                                "participation": "active", "active_reason": "new_fact", "active_source": "event_notice",
                                "location": "北侧小室", "availability": "reachable",
                                "intention": "去北侧小室。", "surface_state": "她已到达北侧小室。", "encounter_hook": "她在北侧小室等待。",
                            }
                        else:
                            value = {"participation": "quiet"}
                    elif "世界局面校对员" in request.system:
                        value = {"narration": "新的一日开始了。"}
                    else:
                        self.public_actions = data["角色在下一时段可被玩家察觉的变化"]
                        value = {"world_time": "次日清晨", "narration": "新的一日开始了。", "player_condition_action": "clear", "retained_trace_indices": []}
                    return ReplySnapshot(content=json.dumps(value, ensure_ascii=False), finish_reason="stop")

            client = Client()
            asyncio.run(advance_world_pulse(store=store, client=client, profile={}, world_id=world_id, target_time="次日清晨"))
            self.assertEqual(store.get_character(character_id)["current_location_id"], "daylight_garden")
            self.assertFalse(client.public_actions)
            self.assertNotIn("北侧小室", store.get_world(world_id)["pulse"]["narration"])

    def test_partial_observation_never_grants_whole_event_or_coordinates(self):
        quote = "伊芙琳只看见洛恩递出折好的纸条。"
        event = {
            "id": "event_paper", "title": "密令青铜海鸥",
            "details": "纸条命令午夜打开藏书室暗门。", "visibility": "secret",
            "world_time": "午夜", "location_id": "hidden_room", "location_name": "暗门",
            "knowledge_state": "witnessed", "confidence": 1.0, "belief": "",
            "source": json.dumps({"role": "assistant", "kind": "witnessed", "quote": quote}),
        }
        projected = knowledge_projection(event)
        self.assertEqual(projected["details"], quote)
        self.assertEqual(projected["knowledge_state"], "witnessed")
        self.assertEqual(projected["id"], event["id"])
        for field in ("world_time", "location_id", "location_name"):
            self.assertNotIn(field, projected)
        self.assertNotIn("青铜海鸥", json.dumps(projected, ensure_ascii=False))
        self.assertNotIn("暗门", json.dumps(projected, ensure_ascii=False))
        self.assertEqual(knowledge_projection(projected), projected)
        self.assertEqual(event["title"], "密令青铜海鸥")

    def test_told_content_keeps_attribution_and_belief_takes_priority(self):
        event = {
            "id": "event_claim", "title": "王冠藏在密室", "details": "王冠实际在密室。",
            "world_time": "昨夜", "location_id": "hidden_room", "visibility": "private",
            "knowledge_state": "heard", "confidence": 1.0,
            "source": json.dumps({"role": "user", "kind": "told", "quote": "王冠在河底。"}),
        }
        projected = knowledge_projection(event)
        self.assertEqual(projected["details"], "王冠在河底。")
        self.assertIn("玩家告知", projected["title"])
        self.assertIn("未证实", projected["title"])
        self.assertEqual(projected["knowledge_state"], "heard")
        self.assertNotIn("密室", json.dumps(projected, ensure_ascii=False))
        for state in ("misunderstood", "believed"):
            with self.subTest(state=state):
                belief = knowledge_projection({**event, "knowledge_state": state, "belief": "王冠已经被熔化。"})
                self.assertEqual(belief["details"], "王冠已经被熔化。")
                self.assertEqual(belief["belief"], "王冠已经被熔化。")
                self.assertEqual(belief["knowledge_state"], state)
                self.assertNotIn("河底", json.dumps(belief, ensure_ascii=False))
                self.assertNotIn("密室", json.dumps(belief, ensure_ascii=False))
        for source in ('{"quote":"不完整来源"}', '{broken', '{"role":[],"kind":"told","quote":"文字"}'):
            with self.subTest(source=source):
                self.assertEqual(knowledge_projection({**event, "source": source})["details"], "")

    def test_all_character_requests_consume_fragments_and_sync_preserves_beliefs(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "information.db")
            store.initialize()
            world_id, character_id = "world_daylight", "daylight_evelyn"
            conv = store.get_or_create_conversation(world_id, character_id)
            room = store.create_room(world_id, "晚茶", "daylight_garden", [character_id])["room"]["id"]
            visible = "伊芙琳只看见洛恩递出折好的纸条。"
            secret_markers = ("密令青铜海鸥", "真实纸条内容暗号", "真实记录时刻暗号")
            with store.transaction() as db:
                for event_id, state, belief, source, visibility in (
                    ("event_observation", "witnessed", "", {"role": "assistant", "kind": "witnessed", "quote": visible}, "secret"),
                    ("event_belief", "misunderstood", "钥匙已经丢进海里。", {"role": "assistant", "kind": "told", "quote": "真实纸条内容暗号"}, "public"),
                ):
                    db.execute("INSERT INTO world_events VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                        event_id, world_id, None, None, secret_markers[0], secret_markers[1],
                        visibility, None, secret_markers[2], 0, "2026-09-12T00:00:00",
                    ))
                    db.execute("INSERT INTO character_knowledge VALUES (?,?,?,?,?,?,?,?)", (
                        f"know_{event_id}", event_id, character_id, state, belief, 0.7,
                        json.dumps(source, ensure_ascii=False), "2026-09-12T00:00:00",
                    ))
            private_context = store.context_for_turn(conv)
            room_context = store.room_character_context(room, character_id)
            sync_context = store.cognition_sync_context(world_id)
            item = next(c for c in sync_context["characters"] if c["character"]["id"] == character_id)
            summary = summary_context(item, sync_context["world"], {})
            knowledge_requests = [
                request_context(private_context, {}),
                opening_request_context(private_context, {}),
                opening_request_context(private_context, {}, reentry=True),
                scene_entry_grounding_context(private_context, "伊芙琳坐在桌边。", {}),
                room_turn_request_context(room_context, {}),
                planning_request(private_context), planning_request(room_context),
                character_pulse_context(item, sync_context, {}, 4096),
                summary,
            ]
            for request in knowledge_requests:
                text = request.system + "\n" + "\n".join(m.content for m in request.messages)
                with self.subTest(request=request.system[:40]):
                    self.assertIn(visible, text)
                    self.assertIn("钥匙已经丢进海里。", text)
                    for secret in secret_markers:
                        self.assertNotIn(secret, text)
            adjudication = patch_context(private_context, "伊芙琳点头。", {})
            adjudication_text = adjudication.system + "\n" + "\n".join(m.content for m in adjudication.messages)
            for secret in secret_markers:
                self.assertNotIn(secret, adjudication_text)
            payload = json.loads(summary.messages[0].content)
            for field in ("本次获得的认知", "此前认知"):
                belief = next(e for e in payload[field] if e["knowledge_state"] == "misunderstood")
                self.assertEqual(belief["belief"], "钥匙已经丢进海里。")
                self.assertEqual(belief["source"], "原有认知")
            store.commit_cognition_sync(world_id, ["event_belief"], {})
            retained = store.context_for_turn(conv)["public_events"][0]
            self.assertEqual(retained["knowledge_state"], "misunderstood")
            self.assertEqual(retained["details"], "钥匙已经丢进海里。")
            with store.read() as db:
                objective = db.execute("SELECT details FROM world_events WHERE id='event_belief'").fetchone()[0]
            self.assertEqual(objective, secret_markers[1])


if __name__ == "__main__":
    unittest.main()
