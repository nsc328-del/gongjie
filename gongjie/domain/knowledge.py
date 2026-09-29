import json
from typing import Any


def knowledge_projection(item: dict[str, Any]) -> dict[str, Any]:
    """Project only the acquired fragment, never the whole underlying event."""
    state = item.get("knowledge_state") or item.get("state") or "heard"
    result = {
        key: item[key] for key in (
            "id", "title", "details", "world_time", "location_id", "location_name", "visibility",
            "source", "confidence", "belief",
        ) if key in item
    }
    result["knowledge_state"] = state
    if state in {"misunderstood", "believed"}:
        belief = str(item.get("belief") or "尚不确定")
        result.update(title="所知版本", details=belief, belief=belief, source="原有认知")
    else:
        source = item.get("source") or ""
        if not isinstance(source, str) or not source.lstrip().startswith("{"):
            # Public propagation and authored knowledge already grant their
            # stored version. Re-projecting a projected fragment is harmless.
            return result
        try:
            record = json.loads(source)
        except ValueError:
            record = None
        if (
            isinstance(record, dict)
            and record.get("role") in ("user", "assistant")
            and record.get("kind") in ("witnessed", "told")
            and isinstance(record.get("quote"), str)
            and record["quote"].strip()
        ):
            speaker = "玩家" if record["role"] == "user" else "角色"
            told = record["kind"] == "told"
            result.update(
                title=f"{speaker}告知的内容（未证实）" if told else "亲历片段",
                details=record["quote"],
                source=f"{speaker}告知" if told else "亲历",
            )
        else:
            # A broken source cannot grant the objective event as a substitute.
            result.update(title="已有认知", details="", source="来源未明")
        result.pop("belief", None)
    # Event coordinates describe the objective event, not necessarily the
    # acquired fragment. A claimed place/time stays inside the quotation.
    result.pop("world_time", None)
    result.pop("location_name", None)
    result.pop("location_id", None)
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
