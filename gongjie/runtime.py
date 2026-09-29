from __future__ import annotations

import json
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from gongjie.ai.providers import ProviderClient, ProviderError
from gongjie.ops.secrets import SecretStore
from gongjie.paths import DATA_DIR
from gongjie.storage.store import Store


current_space: ContextVar[str | None] = ContextVar('gongjie_space', default=None)
access_file = os.getenv('GONGJIE_ACCESS_FILE')
access_config = json.loads(Path(access_file).read_text()) if access_file else None
space_resources: dict[str, dict[str, Any]] = {}


def initialize_space(space_id: str, directory: Path, label: str) -> None:
    database = Store(directory / 'gongjie.db')
    database.initialize()
    # A new process has no surviving workers; unfinished jobs remain recoverable.
    with database.transaction() as connection:
        connection.execute("UPDATE settlement_jobs SET status='failed',reason='interrupted' WHERE status='pending'")
    space_resources[space_id] = {'store': database, 'secrets': SecretStore(directory / 'secrets.json'), 'label': label}


if access_config:
    for space_id, config in access_config['spaces'].items():
        initialize_space(space_id, Path(config['data_dir']), config['label'])
else:
    initialize_space('local', DATA_DIR, '本地存档')


def space_resource(name: str):
    space_id = current_space.get()
    if space_id is None and not access_config:
        space_id = 'local'
    if space_id not in space_resources:
        raise RuntimeError('No authenticated save space')
    return space_resources[space_id][name]


class ScopedResource:
    def __init__(self, name: str):
        self.name = name

    def __getattr__(self, key: str):
        return getattr(space_resource(self.name), key)


store = ScopedResource('store')
secrets = ScopedResource('secrets')
provider_client = ProviderClient()


def active_profile() -> dict[str, Any]:
    profile = store.active_provider()
    profile['api_key'] = secrets.get(profile['id'])
    if not profile['api_key']:
        raise ProviderError('auth', '先连接你的模型', retryable=False)
    if not profile['model'].strip():
        raise ProviderError('model_unavailable', '先填写模型名称', retryable=False)
    return profile
