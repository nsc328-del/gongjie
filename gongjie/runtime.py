from __future__ import annotations

from typing import Any

from gongjie.ai.providers import ProviderClient, ProviderError
from gongjie.ops.secrets import SecretStore
from gongjie.paths import DATA_DIR
from gongjie.storage.store import Store


store = Store(DATA_DIR / "gongjie.db")
secrets = SecretStore(DATA_DIR / "secrets.json")
provider_client = ProviderClient()
store.initialize()


def active_profile() -> dict[str, Any]:
    profile = store.active_provider()
    profile["api_key"] = secrets.get(profile["id"])
    if not profile["api_key"]:
        raise ProviderError("auth", "先连接你的模型", retryable=False)
    if not profile["model"].strip():
        raise ProviderError("model_unavailable", "先填写模型名称", retryable=False)
    return profile
