from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class SecretStore:
    def __init__(self, path: Path):
        self.path = path

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            raise ValueError("本地模型密钥文件无法读取，原文件未改动") from error
        if not isinstance(values, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
            raise ValueError("本地模型密钥文件格式无效，原文件未改动")
        return values

    def has(self, profile_id: str) -> bool:
        try:
            return bool(self.get(profile_id))
        except ValueError:
            return False

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
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(values, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
