from __future__ import annotations

import json
import os
from pathlib import Path


class SecretStore:
    def __init__(self, path: Path):
        self.path = path

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def has(self, profile_id: str) -> bool:
        return bool(self.get(profile_id))

    def get(self, profile_id: str) -> str:
        environment_key = os.getenv("GONGJIE_API_KEY")
        if environment_key:
            return environment_key
        return self._read().get(profile_id, "")

    def set(self, profile_id: str, api_key: str) -> None:
        if not api_key:
            return
        values = self._read()
        values[profile_id] = api_key
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
        os.chmod(self.path, 0o600)
