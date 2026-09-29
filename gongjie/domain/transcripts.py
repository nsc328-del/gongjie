"""Select dialogue without promoting old text into a new turn's evidence."""
from typing import Any


def completed_messages(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [m for m in context.get("messages", [])
            if m.get("content") and m.get("status", "complete") == "complete"]


def turn_user_message(context: dict[str, Any]) -> dict[str, Any] | None:
    if context.get("continuation"):
        return None
    if 'turn_user_id' in context:
        return next((m for m in context.get('messages', []) if m.get('id')==context['turn_user_id'] and m.get('role')=='user'), None)
    messages = completed_messages(context)
    # Room members answer the same input even after another member has spoken.
    if context.get("room"):
        return next((m for m in reversed(messages) if m["role"] == "user"), None)
    return messages[-1] if messages and messages[-1]["role"] == "user" else None


def turn_user_text(context: dict[str, Any]) -> str:
    return (turn_user_message(context) or {}).get("content", "")


def previous_dialogue(context: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    current = turn_user_message(context)
    return [m for m in completed_messages(context) if m is not current][-limit:]


def dialogue_memory(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: m[key] for key in ("role", "content", "character_name", "settlement_outcome") if key in m}
            for m in messages]
