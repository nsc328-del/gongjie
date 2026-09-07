import json
from typing import Any


def knowledge_projection(item: dict[str, Any]) -> dict[str, Any]:
    """Remove objective content before a belief enters any character context."""
    state = item.get("knowledge_state") or item.get("state") or "heard"
    result = {
        key: item[key] for key in (
            "id", "title", "details", "world_time", "location_name", "visibility",
            "source", "confidence", "belief",
        ) if key in item
    }
    result["knowledge_state"] = state
    # The source ledger retains quotes for audits; character prompts only need
    # the acquisition channel, never a quote that could include extra knowledge.
    source = item.get("source") or ""
    if isinstance(source, str) and source.startswith("{"):
        try:
            record = json.loads(source)
            result["source"] = "被告知" if record.get("kind") == "told" else "亲历"
        except (ValueError, AttributeError):
            result["source"] = "已有认知"
    if state in {"misunderstood", "believed"}:
        belief = str(item.get("belief") or "尚不确定")
        result.update(title="所知版本", details=belief, belief=belief, source="原有认知")
        result.pop("world_time", None)
        result.pop("location_name", None)
    return result


def public_identity(character: dict[str, Any]) -> dict[str, Any]:
    return {key: character[key] for key in ("id", "name", "age", "role")}


def player_observation(player: dict[str, Any], names: list[str]) -> dict[str, Any]:
    """Named interpersonal details belong to scenes/knowledge, not body observations."""
    def intrinsic(text: str) -> bool:
        return not any(name and name in text for name in names)

    condition = str(player.get("condition") or "")
    return {
        **player,
        "condition": condition if intrinsic(condition) else "",
        "visible_traces": [text for text in player.get("visible_traces", []) if intrinsic(text)],
    }
