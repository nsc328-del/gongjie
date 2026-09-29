from __future__ import annotations

from copy import deepcopy
from typing import Any

from gongjie.domain.transcripts import turn_user_text


class PatchValidationError(ValueError):
    def __init__(self, reason: str, issues: list[str] | None = None):
        super().__init__(reason)
        self.issues = issues or []


def validate_patch(
    patch: dict[str, Any], context: dict[str, Any], reply: str,
) -> dict[str, Any]:
    """Validate proposed changes against source text and the actual scene boundary.

    Text entailment is reviewed separately; citations and eligibility are checked
    here so a model verdict can never grant arbitrary knowledge or write scope.
    """
    def require(condition: bool, reason: str) -> None:
        if not condition:
            raise PatchValidationError(reason)

    require(isinstance(patch, dict) and bool(patch), "patch_missing")
    require(set(patch) <= {"event", "scene", "relationship", "player", "evidence"}, "patch_fields")
    result = deepcopy(patch)
    evidence = result.get("evidence", {})
    require(isinstance(evidence, dict), "evidence_invalid")
    latest_user = turn_user_text(context)
    sources = {"user": latest_user, "assistant": reply}
    required_evidence: set[str] = set()

    def citation(value: Any, kinds: set[str], player_only: bool = False) -> None:
        require(isinstance(value, dict), "evidence_missing")
        require(isinstance(value.get("kind"), str) and value["kind"] in kinds, "evidence_not_completed")
        role, quote = value.get("role"), value.get("quote")
        require(isinstance(role, str) and role in sources and (not player_only or role == "user"), "evidence_speaker")
        require(isinstance(quote, str) and bool(quote.strip()) and quote in sources[role], "evidence_quote")

    def proof(path: str, kinds: set[str] = {"occurred", "expressed"}, player_only: bool = False) -> None:
        if path not in evidence:
            raise PatchValidationError('evidence_missing', [
                f'{path} 提议了硬状态变化，但缺少 evidence["{path}"]。请选择本轮支持该变化的 source_id 和 kind；若原文不支持则删除该变化。'
            ])
        citation(evidence.get(path), kinds, player_only)
        required_evidence.add(path)

    scene = context.get("scene") or {}
    player = context.get("player") or {}
    relationship = next((r for r in context.get("relationships", []) if r.get("to_user")), {})
    location_ids = {p["id"]: p["name"] for p in context["locations"]}
    for section, allowed in (
        ("scene", {"location", "location_id", "world_time", "tension", "active_threads", "physical_state", "emotional_state"}),
        ("relationship", {"trust_delta", "desire_delta", "tension_delta", "label", "note"}),
        ("player", {"location", "location_id", "condition", "visible_traces", "commitments"}),
    ):
        value = result.setdefault(section, {})
        require(isinstance(value, dict) and set(value) <= allowed, f"{section}_fields")
        for key, item in value.items():
            if key in {"active_threads", "visible_traces", "commitments"}:
                require(isinstance(item, list) and all(isinstance(x, str) for x in item), f"{key}_list")
                require(len(item) <= {"active_threads": 4, "visible_traces": 8, "commitments": 6}[key], f"{key}_limit")
            elif key not in {"tension", "trust_delta", "desire_delta", "tension_delta"}:
                require(isinstance(item, str), f"{key}_text")
        if section in {"scene", "player"}:
            location_id, location = value.get("location_id"), value.get("location")
            if location_id:
                require(location_id in location_ids, "location_outside_world")
                require(not location or location_ids[location_id] == location, "location_mismatch")
                value["location"] = location_ids[location_id]
            elif location:
                require(location in location_ids.values(), "location_unknown")
            current = scene if section == "scene" else player
            current_location = location_ids.get(current.get("location_id"))
            if value.get("location") and value["location"] != current_location:
                proof(f"{section}.location", {"occurred"})
    if result["scene"].get("world_time") and result["scene"]["world_time"] != (scene.get("world_time") or context["world"]["current_time"]):
        proof("scene.world_time", {"occurred"})
    for section, key, low, high in (
        ("scene", "tension", 0, 100), ("relationship", "trust_delta", -5, 5),
        ("relationship", "desire_delta", -5, 5), ("relationship", "tension_delta", -8, 8),
    ):
        value = result[section].get(key, 0)
        require(type(value) is int and low <= value <= high, f"{key}_range")
    rel = result["relationship"]
    if any(rel.get(k, 0) for k in ("trust_delta", "desire_delta", "tension_delta")) or any(
        rel.get(k) and rel[k] != relationship.get(old) for k, old in (("label", "label"), ("note", "notes"))
    ):
        proof("relationship")
    promises = result["player"].get("commitments")
    existing = {p["text"] for p in player.get("commitments", [])}
    if promises is not None and set(promises) != existing:
        proof("player.commitments", player_only=bool(set(promises) - existing))
    event = result.get("event")
    if event is not None:
        require(isinstance(event, dict), "event_invalid")
        require(set(event) <= {"title", "details", "visibility", "participants", "witnesses", "beliefs", "world_time", "knowledge_sources"}, "event_fields")
        require(all(isinstance(event.get(k), str) and bool(event[k].strip()) for k in ("title", "details")), "event_empty")
        require(isinstance(event.get("visibility"), str) and event["visibility"] in {"public", "private", "secret"}, "event_visibility")
        require(isinstance(event.get("world_time", ""), str), "event_time_text")
        proof("event", {"occurred"})
        eligible = {c["name"] for c in context.get("room_characters", [context["character"]])}
        names = set()
        for key in ("participants", "witnesses"):
            values = event.setdefault(key, [])
            require(isinstance(values, list) and all(isinstance(n, str) for n in values), "event_people")
            require(set(values) <= eligible, "event_person_not_present")
            names.update(values)
        names.add(context["character"]["name"])
        knowledge = event.get("knowledge_sources")
        require(isinstance(knowledge, dict) and set(knowledge) == names, "knowledge_sources_mismatch")
        by_name = {c['name']:c['id'] for c in context.get('room_characters', [context['character']])}
        for name, source in knowledge.items():
            citation(source, {"witnessed", "told"})
            if context.get('room'):
                visible = context.get('observation_sources',{}).get(by_name[name],{}).get(source['role'],'')
                require(source['quote'] in visible, 'knowledge_source_not_observed')
        beliefs = event.setdefault("beliefs", {})
        require(isinstance(beliefs, dict) and set(beliefs) <= names and all(isinstance(v, str) for v in beliefs.values()), "beliefs_invalid")
        if event.get("world_time"):
            expected_time = result["scene"].get("world_time") or scene.get("world_time") or context["world"]["current_time"]
            if event["world_time"] != expected_time:
                raise PatchValidationError("event_time_mismatch", [
                    f"event.world_time 当前为 {event['world_time']!r}，本轮有效现场时间为 {expected_time!r}；请将 event.world_time 留空以继承现场时间，或填写与现场完全相同的值。"
                ])
        # Every declared participant gets knowledge, but only after source validation.
        event["witnesses"] = sorted(names)
    # A model sometimes repeats the input snapshot. Those values are not new
    # actions and must not rewind state when an earlier queued reply commits.
    for section, current in (('scene',scene),('player',player)):
        value = result[section]
        proposed_location = value.get('location_id') or next((pid for pid,name in location_ids.items() if name==value.get('location')), None)
        if proposed_location == current.get('location_id'):
            value.pop('location_id',None)
            value.pop('location',None)
    if result['scene'].get('world_time') == (scene.get('world_time') or context['world']['current_time']):
        result['scene'].pop('world_time',None)
    for key in ('tension', 'active_threads', 'physical_state', 'emotional_state'):
        if key in result['scene'] and result['scene'][key] == scene.get(key):
            result['scene'].pop(key)
    for key in ('trust_delta', 'desire_delta', 'tension_delta'):
        if rel.get(key) == 0:
            rel.pop(key)
    for key, baseline in (('label', 'label'), ('note', 'notes')):
        if key in rel and rel[key] == relationship.get(baseline):
            rel.pop(key)
    for key in ('condition','visible_traces'):
        if key in result['player'] and result['player'][key] == player.get(key):
            result['player'].pop(key)
    if result['player'].get('commitments') == [p['text'] for p in player.get('commitments', [])]:
        result['player'].pop('commitments', None)
    from gongjie.domain.locations import validate_planned_locations
    validate_planned_locations(result, context, reply)
    result["evidence"] = {path: evidence[path] for path in sorted(required_evidence)}
    return result
