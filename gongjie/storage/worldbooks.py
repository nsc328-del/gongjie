"""Public, versioned world templates. Local story files are never scanned."""
import json
from typing import Any

from gongjie.paths import PROJECT_ROOT

BUILTINS = {"daylight": PROJECT_ROOT / "worldbooks/白昼将死/白昼将死.worldbook.json"}


def load_worldbook(template_id: str) -> dict[str, Any]:
    if template_id not in BUILTINS:
        raise ValueError("unknown_world_template")
    value = json.loads(BUILTINS[template_id].read_text(encoding="utf-8"))
    if value.get("format") != "gongjie-worldbook" or value.get("version") != 1:
        raise ValueError("invalid_world_template")
    return value["book"]


def public_templates() -> list[dict[str, Any]]:
    return [{"id": key, "world": load_worldbook(key)["world"]} for key in BUILTINS]
