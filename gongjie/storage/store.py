from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from gongjie.domain.knowledge import knowledge_projection, public_identity, player_observation
from gongjie.domain.locations import known_locations, project_location_fields
from gongjie.storage.geography import migrate as migrate_geography, commit_plan, reset_generated
from gongjie.domain.patches import validate_patch
from uuid import uuid4

from gongjie.storage.worldbooks import load_worldbook
from gongjie.storage import presence, information
from gongjie.storage.location_knowledge import commit_mentions


SCHEMA_VERSION = 17


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _looks_like_clock_time(value: str) -> bool:
    """Return True when the value is a real-world clock/ISO time, not story time."""
    if not value:
        return False
    import re

    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", value):
        return True
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:?\d{2})?", value):
        return True
    return False


def _sanitize_world_time(value: str | None, fallback: str = "") -> str:
    clean = str(value or "").strip()
    if _looks_like_clock_time(clean):
        return fallback
    return clean


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS world_saves (id TEXT PRIMARY KEY,world_id TEXT NOT NULL,name TEXT NOT NULL,world_time TEXT NOT NULL,created_at TEXT NOT NULL,automatic INTEGER NOT NULL,payload TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS world_time_previews (world_id TEXT PRIMARY KEY REFERENCES worlds(id) ON DELETE CASCADE,fingerprint TEXT NOT NULL,payload TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS worlds (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    premise TEXT NOT NULL DEFAULT '',
    atmosphere TEXT NOT NULL DEFAULT '',
    rules TEXT NOT NULL DEFAULT '',
    opening_narration TEXT NOT NULL DEFAULT '',
    current_time TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS locations (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    mood TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS characters (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    age INTEGER NOT NULL,
    pronouns TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    tagline TEXT NOT NULL DEFAULT '',
    opening_hook TEXT NOT NULL DEFAULT '',
    persona TEXT NOT NULL DEFAULT '',
    speech_style TEXT NOT NULL DEFAULT '',
    desire_profile TEXT NOT NULL DEFAULT '{}',
    signature_behaviors TEXT NOT NULL DEFAULT '[]',
    loss_of_control TEXT NOT NULL DEFAULT '',
    current_location_id TEXT REFERENCES locations(id) ON DELETE SET NULL,
    accent TEXT NOT NULL DEFAULT '#bc5d45',
    sort_order INTEGER NOT NULL DEFAULT 0,
    state_summary TEXT NOT NULL DEFAULT '',
    summary_updated_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relationships (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    from_character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    to_character_id TEXT REFERENCES characters(id) ON DELETE CASCADE,
    to_user INTEGER NOT NULL DEFAULT 0,
    label TEXT NOT NULL DEFAULT '',
    trust INTEGER NOT NULL DEFAULT 0,
    desire INTEGER NOT NULL DEFAULT 0,
    tension INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    CHECK ((to_character_id IS NOT NULL AND to_user = 0) OR (to_character_id IS NULL AND to_user = 1))
);

CREATE TABLE IF NOT EXISTS world_reset_baselines (
    world_id TEXT PRIMARY KEY REFERENCES worlds(id) ON DELETE CASCADE,
    current_time TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS character_reset_baselines (
    character_id TEXT PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
    current_location_id TEXT REFERENCES locations(id) ON DELETE SET NULL,
    state_summary TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relationship_reset_baselines (
    relationship_id TEXT PRIMARY KEY REFERENCES relationships(id) ON DELETE CASCADE,
    label TEXT NOT NULL DEFAULT '',
    trust INTEGER NOT NULL DEFAULT 0,
    desire INTEGER NOT NULL DEFAULT 0,
    tension INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS world_flow_states (
    world_id TEXT PRIMARY KEY REFERENCES worlds(id) ON DELETE CASCADE,
    player_location_id TEXT REFERENCES locations(id) ON DELETE SET NULL,
    player_condition TEXT NOT NULL DEFAULT '',
    visible_traces TEXT NOT NULL DEFAULT '[]',
    pulse_narration TEXT NOT NULL DEFAULT '',
    pulse_number INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player_commitments (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    character_id TEXT REFERENCES characters(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(world_id, character_id, text)
);

CREATE TABLE IF NOT EXISTS character_world_states (
    character_id TEXT PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
    intention TEXT NOT NULL DEFAULT '',
    surface_state TEXT NOT NULL DEFAULT '',
    encounter_hook TEXT NOT NULL DEFAULT '',
    availability TEXT NOT NULL DEFAULT 'reachable'
        CHECK (availability IN ('present','reachable','incoming','unavailable')),
    pulse_number INTEGER NOT NULL DEFAULT 0,
    interacted_since_pulse INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(world_id, character_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content TEXT NOT NULL DEFAULT '',
    reasoning TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'complete' CHECK (status IN ('streaming','complete','error','cancelled')),
    finish_reason TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    sequence INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_sequence
ON messages(conversation_id, sequence);

CREATE TABLE IF NOT EXISTS scene_states (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    location_id TEXT REFERENCES locations(id) ON DELETE SET NULL,
    world_time TEXT NOT NULL DEFAULT '',
    tension INTEGER NOT NULL DEFAULT 0,
    active_threads TEXT NOT NULL DEFAULT '[]',
    physical_state TEXT NOT NULL DEFAULT '',
    emotional_state TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS world_events (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    source_conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
    source_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    visibility TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('public','private','secret')),
    location_id TEXT REFERENCES locations(id) ON DELETE SET NULL,
    world_time TEXT NOT NULL DEFAULT '',
    cognition_synced INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_world_created
ON world_events(world_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_world_sync
ON world_events(world_id, cognition_synced, created_at);

CREATE TABLE IF NOT EXISTS event_participants (
    event_id TEXT NOT NULL REFERENCES world_events(id) ON DELETE CASCADE,
    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'participant',
    PRIMARY KEY(event_id, character_id)
);

CREATE TABLE IF NOT EXISTS character_knowledge (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES world_events(id) ON DELETE CASCADE,
    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'witnessed' CHECK (state IN ('witnessed','heard','believed','misunderstood')),
    belief TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(event_id, character_id)
);

CREATE TABLE IF NOT EXISTS provider_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    protocol TEXT NOT NULL CHECK (protocol IN ('openai-compatible','anthropic-messages')),
    base_url TEXT NOT NULL,
    path TEXT NOT NULL,
    model TEXT NOT NULL,
    max_tokens INTEGER NOT NULL DEFAULT 8192,
    temperature REAL NOT NULL DEFAULT 0.9,
    is_active INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scene_rooms (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    location_id TEXT REFERENCES locations(id) ON DELETE SET NULL,
    world_time TEXT NOT NULL DEFAULT '',
    tension INTEGER NOT NULL DEFAULT 0,
    active_threads TEXT NOT NULL DEFAULT '[]',
    physical_state TEXT NOT NULL DEFAULT '',
    emotional_state TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS room_characters (
    room_id TEXT NOT NULL REFERENCES scene_rooms(id) ON DELETE CASCADE,
    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(room_id, character_id)
);

CREATE TABLE IF NOT EXISTS room_messages (
    id TEXT PRIMARY KEY,
    room_id TEXT NOT NULL REFERENCES scene_rooms(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user','assistant')),
    character_id TEXT REFERENCES characters(id) ON DELETE SET NULL,
    content TEXT NOT NULL DEFAULT '',
    sequence INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_room_messages_room_sequence
ON room_messages(room_id, sequence);

CREATE TABLE IF NOT EXISTS room_event_sources (
    event_id TEXT PRIMARY KEY REFERENCES world_events(id) ON DELETE CASCADE,
    room_id TEXT REFERENCES scene_rooms(id) ON DELETE SET NULL,
    room_message_id TEXT REFERENCES room_messages(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_room_event_sources_room
ON room_event_sources(room_id, room_message_id);

CREATE TABLE IF NOT EXISTS turn_settlements (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
    room_id TEXT REFERENCES scene_rooms(id) ON DELETE CASCADE,
    source_message_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
    source_room_message_id TEXT REFERENCES room_messages(id) ON DELETE CASCADE,
    event_id TEXT REFERENCES world_events(id) ON DELETE SET NULL,
    scene TEXT NOT NULL DEFAULT '{}',
    relationship TEXT NOT NULL DEFAULT '{}',
    player TEXT NOT NULL DEFAULT '{}',
    evidence TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    CHECK (
        (source_message_id IS NOT NULL AND source_room_message_id IS NULL)
        OR (source_message_id IS NULL AND source_room_message_id IS NOT NULL)
    ),
    UNIQUE(source_message_id),
    UNIQUE(source_room_message_id)
);

CREATE INDEX IF NOT EXISTS idx_turn_settlements_conversation
ON turn_settlements(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_turn_settlements_room
ON turn_settlements(room_id, created_at);

CREATE TABLE IF NOT EXISTS conversation_scene_entries (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    pulse_number INTEGER NOT NULL,
    message_id TEXT NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
    entry_kind TEXT NOT NULL CHECK (entry_kind IN ('initial','reentry')),
    created_at TEXT NOT NULL,
    PRIMARY KEY(conversation_id, pulse_number)
);

CREATE TABLE IF NOT EXISTS player_profiles (
    world_id TEXT PRIMARY KEY REFERENCES worlds(id) ON DELETE CASCADE,
    profile TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS settlement_jobs (
    position INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL UNIQUE,
    world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
    room_id TEXT REFERENCES scene_rooms(id) ON DELETE CASCADE,
    source_message_id TEXT UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
    source_room_message_id TEXT UNIQUE REFERENCES room_messages(id) ON DELETE CASCADE,
    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    context TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','failed','complete')),
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    CHECK ((source_message_id IS NOT NULL AND source_room_message_id IS NULL)
        OR (source_message_id IS NULL AND source_room_message_id IS NOT NULL))
);
"""


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._active_worlds: set[str] = set()

    @contextmanager
    def world_operation(self, world_id: str, *, recovery: bool = False) -> Iterator[None]:
        if world_id in self._active_worlds:
            raise ValueError("世界正在留下痕迹，请稍候")
        if not recovery and self.pending_settlements(world_id):
            raise ValueError("还有未记下的余波，请先重新记录")
        self._active_worlds.add(world_id)
        try:
            yield
        finally:
            self._active_worlds.discard(world_id)

    def pending_settlements(self, world_id: str) -> list[dict[str, Any]]:
        with self.read() as connection:
            return [dict(row) for row in connection.execute(
                """SELECT source_id,conversation_id,room_id,character_id,status,reason
                   FROM settlement_jobs WHERE world_id=? AND status!='complete'
                   ORDER BY position""", (world_id,),
            )]

    @staticmethod
    def _enqueue_settlement(connection: sqlite3.Connection, source_id: str, context: dict[str, Any]) -> None:
        room_id = (context.get("room") or {}).get("id")
        conversation_id = (context.get("conversation") or {}).get("id")
        connection.execute(
            """INSERT INTO settlement_jobs(source_id,world_id,conversation_id,room_id,
                   source_message_id,source_room_message_id,character_id,context,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (source_id, context["world"]["id"], conversation_id, room_id,
             None if room_id else source_id, source_id if room_id else None,
             context["character"]["id"], _json(context), now()),
        )

    def settlement_job(self, source_id: str) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute("SELECT * FROM settlement_jobs WHERE source_id=?", (source_id,)).fetchone()
            if not row:
                raise KeyError("settlement_not_found")
            job = dict(row)
            table = "room_messages" if job["room_id"] else "messages"
            job["reply"] = connection.execute(f"SELECT content FROM {table} WHERE id=?", (source_id,)).fetchone()[0]
            job["context"] = _loads(job["context"], {})
        return job

    def start_settlement(self, source_id: str) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE settlement_jobs SET status='pending',reason='' WHERE source_id=? AND status!='complete'", (source_id,))

    def fail_settlement(self, source_id: str, reason: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE settlement_jobs SET status='failed',reason=? WHERE source_id=? AND status!='complete'",
                (reason, source_id),
            )

    @classmethod
    def _existing_patch_result(cls, connection: sqlite3.Connection, source_id: str) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT * FROM turn_settlements WHERE source_message_id=? OR source_room_message_id=?",
            (source_id, source_id),
        ).fetchone()
        if not row:
            return None
        settlement = cls._settlement(row)
        event = connection.execute("SELECT * FROM world_events WHERE id=?", (row["event_id"],)).fetchone()
        record = dict(event) if event else None
        if record and row["source_room_message_id"]:
            record["source_room_message_id"] = row["source_room_message_id"]
        return {"event_id": row["event_id"], "record": record, "settlement": settlement}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        connection = self._connect()
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "branches" in tables:
                self._migrate_from_branches(connection)
            connection.executescript(SCHEMA)
            self._ensure_v4_columns(connection)
            self._ensure_character_art(connection)
            self._ensure_v6_columns(connection)
            self._ensure_v7_baselines(connection)
            self._ensure_v8_flow(connection)
            if "evidence" not in {row[1] for row in connection.execute("PRAGMA table_info(turn_settlements)")}:
                connection.execute("ALTER TABLE turn_settlements ADD COLUMN evidence TEXT NOT NULL DEFAULT '{}'")
            if "decision" not in {row[1] for row in connection.execute("PRAGMA table_info(turn_settlements)")}:
                connection.execute("ALTER TABLE turn_settlements ADD COLUMN decision TEXT NOT NULL DEFAULT '{}'")
            if "ready_commitment_ids" not in {r[1] for r in connection.execute("PRAGMA table_info(world_flow_states)")}:
                connection.execute("ALTER TABLE world_flow_states ADD COLUMN ready_commitment_ids TEXT NOT NULL DEFAULT '[]'")
            migrate_geography(connection)
            presence.migrate(connection)
            information.migrate(connection)
            information.migrate_saves(connection, SCHEMA_VERSION)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
        finally:
            connection.close()
        with self.read() as connection:
            count = connection.execute("SELECT COUNT(*) FROM worlds").fetchone()[0]
        if count == 0:
            self._seed()

    @staticmethod
    def _ensure_character_art(connection: sqlite3.Connection) -> None:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(characters)")}
        if "art_key" in columns:
            return
        connection.execute("ALTER TABLE characters ADD COLUMN art_key TEXT NOT NULL DEFAULT ''")
        for character in load_worldbook("daylight")["characters"]:
            connection.execute(
                "UPDATE characters SET art_key=? WHERE name=? AND age=? AND persona=?",
                (character.get("art_key", ""), character["name"], character["age"], character["persona"]),
            )

    @staticmethod
    def _ensure_v4_columns(connection: sqlite3.Connection) -> None:
        world_columns = {row[1] for row in connection.execute("PRAGMA table_info(worlds)")}
        added_world_opening = "opening_narration" not in world_columns
        if added_world_opening:
            connection.execute(
                "ALTER TABLE worlds ADD COLUMN opening_narration TEXT NOT NULL DEFAULT ''"
            )
        character_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(characters)")
        }
        added_character_opening = "opening_hook" not in character_columns
        if added_character_opening:
            connection.execute(
                "ALTER TABLE characters ADD COLUMN opening_hook TEXT NOT NULL DEFAULT ''"
            )
        if added_world_opening:
            connection.execute(
                "UPDATE worlds SET opening_narration=premise WHERE opening_narration=''"
            )
        if added_character_opening:
            connection.execute(
                "UPDATE characters SET opening_hook=tagline WHERE opening_hook=''"
            )

    @staticmethod
    def _ensure_v6_columns(connection: sqlite3.Connection) -> None:
        room_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(scene_rooms)")
        }
        additions = {
            "world_time": "TEXT NOT NULL DEFAULT ''",
            "tension": "INTEGER NOT NULL DEFAULT 0",
            "active_threads": "TEXT NOT NULL DEFAULT '[]'",
            "physical_state": "TEXT NOT NULL DEFAULT ''",
            "emotional_state": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in additions.items():
            if column not in room_columns:
                connection.execute(
                    f"ALTER TABLE scene_rooms ADD COLUMN {column} {definition}"
                )
        connection.execute(
            """UPDATE scene_rooms
               SET world_time=(
                   SELECT worlds.current_time FROM worlds WHERE worlds.id=scene_rooms.world_id
               )
               WHERE world_time=''"""
        )

    @staticmethod
    def _capture_reset_baseline(
        connection: sqlite3.Connection, world_id: str, captured_at: str
    ) -> None:
        connection.execute(
            """INSERT OR IGNORE INTO world_reset_baselines(world_id,current_time,captured_at)
               SELECT worlds.id,worlds.current_time,? FROM worlds WHERE worlds.id=?""",
            (captured_at, world_id),
        )
        connection.execute(
            """INSERT OR IGNORE INTO character_reset_baselines(
                   character_id,current_location_id,state_summary,captured_at)
               SELECT id,current_location_id,state_summary,? FROM characters WHERE world_id=?""",
            (captured_at, world_id),
        )
        connection.execute(
            """INSERT OR IGNORE INTO relationship_reset_baselines(
                   relationship_id,label,trust,desire,tension,notes,captured_at)
               SELECT id,label,trust,desire,tension,notes,? FROM relationships WHERE world_id=?""",
            (captured_at, world_id),
        )

    @classmethod
    def _ensure_v7_baselines(cls, connection: sqlite3.Connection) -> None:
        captured_at = now()
        world_ids = [
            row[0] for row in connection.execute("SELECT id FROM worlds").fetchall()
        ]
        for world_id in world_ids:
            cls._capture_reset_baseline(connection, world_id, captured_at)

    @staticmethod
    def _ensure_v8_flow(connection: sqlite3.Connection) -> None:
        created = now()
        connection.execute(
            """INSERT OR IGNORE INTO world_flow_states(
                   world_id,player_location_id,player_condition,visible_traces,
                   pulse_narration,pulse_number,updated_at)
               SELECT id,NULL,'','[]','',0,? FROM worlds""",
            (created,),
        )
        connection.execute(
            """INSERT OR IGNORE INTO character_world_states(
                   character_id,intention,surface_state,encounter_hook,availability,
                   pulse_number,interacted_since_pulse,updated_at)
               SELECT id,'',state_summary,opening_hook,'reachable',0,0,?
               FROM characters""",
            (created,),
        )

    def _migrate_from_branches(self, connection: sqlite3.Connection) -> None:
        """Collapse each legacy conversation onto its canonical visible history."""
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            character_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(characters)")
            }
            if "state_summary" not in character_columns:
                connection.execute(
                    "ALTER TABLE characters ADD COLUMN state_summary TEXT NOT NULL DEFAULT ''"
                )
            if "summary_updated_at" not in character_columns:
                connection.execute(
                    "ALTER TABLE characters ADD COLUMN summary_updated_at TEXT"
                )

            connection.executescript(
                """
                CREATE TABLE conversations_v3 (
                    id TEXT PRIMARY KEY,
                    world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
                    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(world_id, character_id)
                );
                CREATE TABLE messages_v3 (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations_v3(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('user','assistant')),
                    content TEXT NOT NULL DEFAULT '', reasoning TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'complete', finish_reason TEXT,
                    input_tokens INTEGER, output_tokens INTEGER, sequence INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE scene_states_v3 (
                    conversation_id TEXT PRIMARY KEY REFERENCES conversations_v3(id) ON DELETE CASCADE,
                    location_id TEXT REFERENCES locations(id) ON DELETE SET NULL,
                    world_time TEXT NOT NULL DEFAULT '', tension INTEGER NOT NULL DEFAULT 0,
                    active_threads TEXT NOT NULL DEFAULT '[]', physical_state TEXT NOT NULL DEFAULT '',
                    emotional_state TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
                );
                CREATE TABLE world_events_v3 (
                    id TEXT PRIMARY KEY,
                    world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
                    source_conversation_id TEXT REFERENCES conversations_v3(id) ON DELETE SET NULL,
                    source_message_id TEXT REFERENCES messages_v3(id) ON DELETE SET NULL,
                    title TEXT NOT NULL, details TEXT NOT NULL DEFAULT '',
                    visibility TEXT NOT NULL DEFAULT 'private',
                    location_id TEXT REFERENCES locations(id) ON DELETE SET NULL,
                    world_time TEXT NOT NULL DEFAULT '', cognition_synced INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                """
            )

            def visible_messages(branch_id: str | None) -> list[sqlite3.Row]:
                if not branch_id:
                    return []
                branch = connection.execute(
                    "SELECT * FROM branches WHERE id=?", (branch_id,)
                ).fetchone()
                if not branch:
                    return []
                inherited: list[sqlite3.Row] = []
                if branch["parent_branch_id"]:
                    inherited = visible_messages(branch["parent_branch_id"])
                    fork_id = branch["forked_from_message_id"]
                    if fork_id:
                        cut = next(
                            (i for i, item in enumerate(inherited) if item["id"] == fork_id),
                            None,
                        )
                        inherited = inherited[: cut + 1] if cut is not None else []
                local = connection.execute(
                    "SELECT * FROM messages WHERE branch_id=? ORDER BY sequence",
                    (branch_id,),
                ).fetchall()
                return [*inherited, *local]

            copied_message_ids: set[str] = set()
            conversations = connection.execute("SELECT * FROM conversations").fetchall()
            for conversation in conversations:
                connection.execute(
                    "INSERT INTO conversations_v3 VALUES (?,?,?,?,?,?)",
                    (
                        conversation["id"], conversation["world_id"],
                        conversation["character_id"], conversation["title"],
                        conversation["created_at"], conversation["updated_at"],
                    ),
                )
                selected = conversation["canonical_branch_id"] or conversation["active_branch_id"]
                for sequence, message in enumerate(visible_messages(selected), 1):
                    copied_message_ids.add(message["id"])
                    connection.execute(
                        "INSERT INTO messages_v3 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            message["id"], message["conversation_id"], message["role"],
                            message["content"], message["reasoning"], message["status"],
                            message["finish_reason"], message["input_tokens"],
                            message["output_tokens"], sequence, message["created_at"],
                            message["updated_at"],
                        ),
                    )
                scene = connection.execute(
                    "SELECT * FROM scene_states WHERE branch_id=?", (selected,)
                ).fetchone()
                if scene:
                    connection.execute(
                        "INSERT INTO scene_states_v3 VALUES (?,?,?,?,?,?,?,?)",
                        (
                            conversation["id"], scene["location_id"], scene["world_time"],
                            scene["tension"], scene["active_threads"],
                            scene["physical_state"], scene["emotional_state"],
                            scene["updated_at"],
                        ),
                    )

            canonical_events = connection.execute(
                "SELECT * FROM world_events WHERE is_canon=1 ORDER BY created_at"
            ).fetchall()
            copied_event_ids: set[str] = set()
            for event in canonical_events:
                source_message_id = (
                    event["source_message_id"]
                    if event["source_message_id"] in copied_message_ids
                    else None
                )
                source_conversation_id = None
                if source_message_id:
                    row = connection.execute(
                        "SELECT conversation_id FROM messages_v3 WHERE id=?",
                        (source_message_id,),
                    ).fetchone()
                    source_conversation_id = row[0] if row else None
                copied_event_ids.add(event["id"])
                connection.execute(
                    "INSERT INTO world_events_v3 VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        event["id"], event["world_id"], source_conversation_id,
                        source_message_id, event["title"], event["details"],
                        event["visibility"], event["location_id"], event["world_time"],
                        1, event["created_at"],
                    ),
                )

            if copied_event_ids:
                marks = ",".join("?" for _ in copied_event_ids)
                connection.execute(
                    f"DELETE FROM event_participants WHERE event_id NOT IN ({marks})",
                    tuple(copied_event_ids),
                )
                connection.execute(
                    f"DELETE FROM character_knowledge WHERE event_id NOT IN ({marks})",
                    tuple(copied_event_ids),
                )
            else:
                connection.execute("DELETE FROM event_participants")
                connection.execute("DELETE FROM character_knowledge")

            connection.executescript(
                """
                DROP TABLE IF EXISTS scene_snapshots;
                DROP TABLE scene_states;
                DROP TABLE messages;
                DROP TABLE branches;
                DROP TABLE conversations;
                DROP TABLE world_events;
                ALTER TABLE conversations_v3 RENAME TO conversations;
                ALTER TABLE messages_v3 RENAME TO messages;
                ALTER TABLE scene_states_v3 RENAME TO scene_states;
                ALTER TABLE world_events_v3 RENAME TO world_events;
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

    def _seed(self) -> None:
        self._create_world({}, load_worldbook("daylight"), world_id="world_daylight", preserve_ids=True)
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO provider_profiles VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("provider_default", "我的模型", "openai-compatible", "https://api.openai.com", "/v1/chat/completions", "", 16384, 0.95, 1, now()),
            )

    @staticmethod
    def _character(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["desire_profile"] = _loads(result.get("desire_profile"), {})
        result["signature_behaviors"] = _loads(result.get("signature_behaviors"), [])
        return result

    @staticmethod
    def _scene(row: sqlite3.Row | None) -> dict[str, Any]:
        if not row:
            return {
                "location_id": None, "world_time": "", "tension": 0,
                "active_threads": [], "physical_state": "", "emotional_state": "",
            }
        result = dict(row)
        result["active_threads"] = _loads(result.get("active_threads"), [])
        return result

    @staticmethod
    def _settlement(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for key in ("scene", "relationship", "player", "evidence", "decision"):
            result[key] = _loads(result.get(key), {})
        return result

    @staticmethod
    def _player_state(
        row: sqlite3.Row | None, commitments: list[sqlite3.Row] | None = None
    ) -> dict[str, Any]:
        if not row:
            return {
                "location_id": None, "location_name": None, "condition": "",
                "visible_traces": [], "commitments": [],
            }
        result = dict(row)
        return {
            "location_id": result.get("player_location_id"),
            "location_name": result.get("player_location_name"),
            "condition": result.get("player_condition", ""),
            "visible_traces": _loads(result.get("visible_traces"), []),
            "commitments": [dict(item) for item in (commitments or [])],
        }

    @staticmethod
    def _insert_turn_settlement(
        connection: sqlite3.Connection,
        *,
        world_id: str,
        conversation_id: str | None = None,
        room_id: str | None = None,
        source_message_id: str | None = None,
        source_room_message_id: str | None = None,
        event_id: str | None = None,
        patch: dict[str, Any],
        created_at: str,
    ) -> dict[str, Any]:
        def text(value: Any, limit: int) -> str:
            return str(value or "").strip()[:limit]

        def string_list(value: Any, limit: int, item_limit: int) -> list[str]:
            if not isinstance(value, list):
                return []
            return list(dict.fromkeys(
                text(item, item_limit) for item in value if text(item, item_limit)
            ))[:limit]

        raw_scene = patch.get("scene") if isinstance(patch.get("scene"), dict) else {}
        scene: dict[str, Any] = {}
        for key in ("location_id", "location"):
            if text(raw_scene.get(key), 120):
                scene[key] = text(raw_scene[key], 120)
        world_time = _sanitize_world_time(text(raw_scene.get("world_time"), 120))
        if world_time:
            scene["world_time"] = world_time
        if "tension" in raw_scene:
            try:
                scene["tension"] = max(0, min(100, int(raw_scene["tension"])))
            except (TypeError, ValueError):
                pass
        for key, limit in (("active_threads", 4), ("visible_traces", 8)):
            # visible_traces belongs to player, but keeping the allowlist here
            # makes accidental cross-section data harmless if a model returns it.
            if key in raw_scene:
                scene[key] = string_list(raw_scene[key], limit, 160)
        for key in ("physical_state", "emotional_state"):
            if key in raw_scene:
                scene[key] = text(raw_scene[key], 200)

        raw_relationship = (
            patch.get("relationship")
            if isinstance(patch.get("relationship"), dict) else {}
        )
        relationship: dict[str, Any] = {}
        for key, low, high in (
            ("trust_delta", -5, 5),
            ("desire_delta", -5, 5),
            ("tension_delta", -8, 8),
        ):
            if key in raw_relationship:
                try:
                    relationship[key] = max(low, min(high, int(raw_relationship[key])))
                except (TypeError, ValueError):
                    pass
        for key, limit in (("label", 80), ("note", 200)):
            value = text(raw_relationship.get(key), limit)
            if value:
                relationship[key] = value

        raw_player = patch.get("player") if isinstance(patch.get("player"), dict) else {}
        player: dict[str, Any] = {}
        for key, limit in (("location_id", 120), ("location", 120), ("condition", 200)):
            value = text(raw_player.get(key), limit)
            if value or (key == "condition" and key in raw_player):
                player[key] = value
        if "visible_traces" in raw_player:
            player["visible_traces"] = string_list(raw_player["visible_traces"], 8, 160)
        if "commitments" in raw_player:
            player["commitments"] = string_list(raw_player["commitments"], 6, 160)

        settlement = {
            "id": uid("settle"),
            "world_id": world_id,
            "conversation_id": conversation_id,
            "room_id": room_id,
            "source_message_id": source_message_id,
            "source_room_message_id": source_room_message_id,
            "event_id": event_id,
            "scene": scene,
            "relationship": relationship,
            "player": player,
            "created_at": created_at,
            "evidence": patch.get("evidence", {}),
        }
        connection.execute(
            """INSERT INTO turn_settlements(
                   id,world_id,conversation_id,room_id,source_message_id,
                   source_room_message_id,event_id,scene,relationship,player,created_at,evidence)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                settlement["id"], settlement["world_id"], settlement["conversation_id"],
                settlement["room_id"], settlement["source_message_id"],
                settlement["source_room_message_id"], settlement["event_id"],
                _json(settlement["scene"]), _json(settlement["relationship"]),
                _json(settlement["player"]), settlement["created_at"],
                _json(patch.get("evidence", {})),
            ),
        )
        connection.execute(
            "UPDATE settlement_jobs SET status='complete',reason='' WHERE source_id=?",
            (source_message_id or source_room_message_id,),
        )
        return settlement

    def reject_settlement(self, source_id: str, reason: str, issues: list[str]) -> dict[str, Any]:
        """Terminal decision without applying any part of an invalid proposal."""
        with self.transaction() as connection:
            existing = self._existing_patch_result(connection, source_id)
            if existing:
                return existing
            job = connection.execute("SELECT * FROM settlement_jobs WHERE source_id=?", (source_id,)).fetchone()
            if not job:
                raise KeyError("settlement_not_found")
            settlement = self._insert_turn_settlement(
                connection, world_id=job["world_id"], conversation_id=job["conversation_id"],
                room_id=job["room_id"], source_message_id=None if job["room_id"] else source_id,
                source_room_message_id=source_id if job["room_id"] else None,
                patch={}, created_at=now(),
            )
            decision = {"outcome": "rejected", "reason": reason, "issues": issues[:4]}
            connection.execute("UPDATE turn_settlements SET decision=? WHERE id=?", (_json(decision), settlement["id"]))
            settlement["decision"] = decision
            return {"event_id": None, "record": None, "settlement": settlement}

    def time_preview(self, world_id):
        from gongjie.storage.previews import cached
        with self.read() as c: return cached(c, world_id)

    def preview_fingerprint(self, world_id):
        from gongjie.storage.previews import fingerprint
        with self.read() as c: return fingerprint(c, world_id)

    def save_time_preview(self, world_id, fingerprint_value, value):
        from gongjie.storage.previews import fingerprint
        with self.transaction() as c:
            world = c.execute('SELECT w.current_time FROM worlds w WHERE id=?', (world_id,)).fetchone()
            if not world:
                raise KeyError("world_not_found")
            target = value.get("world_time")
            if (not isinstance(target, str) or not target.strip() or len(target) > 120
                    or not _sanitize_world_time(target) or target.strip() == world["current_time"].strip()):
                raise ValueError("未能确定有效的下一时刻，请重新规划")
            if fingerprint(c, world_id) != fingerprint_value: raise ValueError("世界已有变化，请刷新时间预告")
            c.execute("INSERT OR REPLACE INTO world_time_previews VALUES (?,?,?)", (world_id,fingerprint_value,_json(value)))
        return value

    def list_saves(self, world_id):
        self.get_world(world_id)
        with self.read() as c:
            return [dict(r) for r in c.execute("SELECT id,world_id,name,world_time,created_at,automatic FROM world_saves WHERE world_id=? ORDER BY created_at DESC,rowid DESC", (world_id,))]

    def save_world(self, world_id, name=""):
        from gongjie.storage.saves import insert_save
        with self.world_operation(world_id), self.transaction() as c:
            return insert_save(c, world_id, name)

    def load_world_save(self, world_id, save_id):
        from gongjie.storage.saves import restore
        with self.world_operation(world_id), self.transaction() as c:
            return restore(c, world_id, save_id)

    def list_worlds(self) -> list[dict[str, Any]]:
        with self.read() as connection:
            rows = connection.execute(
                """SELECT w.*,
                          (SELECT COUNT(*) FROM characters c WHERE c.world_id=w.id) AS character_count,
                          (SELECT COUNT(*) FROM world_events e WHERE e.world_id=w.id AND e.cognition_synced=0) AS pending_sync_count,
                          (SELECT e.title FROM world_events e WHERE e.world_id=w.id AND e.visibility='public' ORDER BY e.created_at DESC LIMIT 1) AS latest_public_event
                   FROM worlds w ORDER BY w.updated_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def create_world(self, values: dict[str, Any]) -> dict[str, Any]:
        template_id = values.get("template_id")
        book = load_worldbook(template_id) if template_id else {"world": {}, "locations": [], "characters": [], "relationships": []}
        return self._create_world(values, book)

    def _create_world(
        self, values: dict[str, Any], book: dict[str, Any], *,
        world_id: str | None = None, preserve_ids: bool = False,
    ) -> dict[str, Any]:
        world_id = world_id or uid("world")
        created = now()
        defaults = book["world"]
        fields = ("name", "premise", "atmosphere", "rules", "opening_narration", "current_time")
        world = {key: str(values.get(key) or defaults.get(key, "")).strip() for key in fields}
        if not world["name"]:
            raise ValueError("world_name_empty")
        locations = {item["id"]: item["id"] if preserve_ids else uid("loc") for item in book["locations"]}
        characters = {item["id"]: item["id"] if preserve_ids else uid("char") for item in book["characters"]}
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO worlds(id,name,premise,atmosphere,rules,opening_narration,current_time,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (world_id, *(world[key] for key in fields), created, created),
            )
            for order, item in enumerate(book["locations"]):
                connection.execute(
                    "INSERT INTO locations(id,world_id,name,description,mood,sort_order) VALUES (?,?,?,?,?,?)",
                    (locations[item["id"]], world_id, item["name"], item.get("description", ""), item.get("mood", ""), order),
                )
            for order, item in enumerate(book["characters"]):
                connection.execute(
                    """INSERT INTO characters(
                        id,world_id,name,age,pronouns,role,tagline,opening_hook,persona,speech_style,
                        desire_profile,signature_behaviors,loss_of_control,current_location_id,
                        accent,sort_order,state_summary,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (characters[item["id"]], world_id, item["name"], item["age"], item.get("pronouns", ""),
                     item.get("role", ""), item.get("tagline", ""), item.get("opening_hook", ""),
                     item.get("persona", ""), item.get("speech_style", ""), _json(item.get("desire_profile", {})),
                     _json(item.get("signature_behaviors", [])), item.get("loss_of_control", ""),
                     locations.get(item.get("current_location_id")), item.get("accent", "#668475"),
                     order, item.get("tagline", ""), created, created),
                )
                connection.execute(
                    "UPDATE characters SET art_key=? WHERE id=?",
                    (item.get("art_key", ""), characters[item["id"]]),
                )
            for item in book["relationships"]:
                connection.execute(
                    "INSERT INTO relationships VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (uid("rel"), world_id, characters[item["from_character_id"]],
                     characters.get(item.get("to_character_id")), int(item["to_user"]),
                     item.get("label", ""), item.get("trust", 0), item.get("desire", 0),
                     item.get("tension", 0), item.get("notes", ""), created),
                )
            self._capture_reset_baseline(connection, world_id, created)
            self._ensure_v8_flow(connection)
        return self.get_world(world_id)

    def update_world(self, world_id: str, values: dict[str, Any]) -> dict[str, Any]:
        allowed = ("name", "premise", "atmosphere", "rules", "opening_narration", "current_time")
        updates = {
            key: str(values[key]).strip()
            for key in allowed if key in values and values[key] is not None
        }
        if not updates:
            return self.get_world(world_id)
        assignments = ",".join(f"{key}=?" for key in updates)
        with self.transaction() as connection:
            result = connection.execute(
                f"UPDATE worlds SET {assignments}, updated_at=? WHERE id=?",
                [*updates.values(), now(), world_id],
            )
            if not result.rowcount:
                raise KeyError("world_not_found")
            if "current_time" in updates:
                connection.execute(
                    "UPDATE scene_states SET world_time=? WHERE conversation_id IN (SELECT id FROM conversations WHERE world_id=?)",
                    (updates["current_time"], world_id),
                )
                connection.execute("UPDATE scene_rooms SET world_time=? WHERE world_id=?", (updates["current_time"], world_id))
                connection.execute(
                    """UPDATE world_reset_baselines
                       SET current_time=?,captured_at=? WHERE world_id=?""",
                    (updates["current_time"], now(), world_id),
                )
        return self.get_world(world_id)

    def create_location(self, world_id: str, values: dict[str, Any]) -> dict[str, Any]:
        location_id = uid("loc")
        with self.transaction() as connection:
            if not connection.execute("SELECT 1 FROM worlds WHERE id=?", (world_id,)).fetchone():
                raise KeyError("world_not_found")
            order = connection.execute(
                "SELECT COALESCE(MAX(sort_order),-1)+1 FROM locations WHERE world_id=?",
                (world_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO locations(id,world_id,name,description,mood,sort_order) VALUES (?,?,?,?,?,?)",
                (location_id, world_id, values["name"].strip(), values.get("description", "").strip(), values.get("mood", "").strip(), order),
            )
            row = connection.execute("SELECT * FROM locations WHERE id=?", (location_id,)).fetchone()
        return dict(row)

    def create_character(self, world_id: str, values: dict[str, Any]) -> dict[str, Any]:
        character_id = uid("char")
        created = now()
        with self.transaction() as connection:
            if not connection.execute("SELECT 1 FROM worlds WHERE id=?", (world_id,)).fetchone():
                raise KeyError("world_not_found")
            order = connection.execute(
                "SELECT COALESCE(MAX(sort_order),-1)+1 FROM characters WHERE world_id=?",
                (world_id,),
            ).fetchone()[0]
            location_id = values.get("current_location_id")
            if location_id and not connection.execute(
                "SELECT 1 FROM locations WHERE id=? AND world_id=?", (location_id, world_id)
            ).fetchone():
                raise ValueError("invalid_location")
            tagline = values.get("tagline", "").strip()
            connection.execute(
                """INSERT INTO characters(
                       id,world_id,name,age,pronouns,role,tagline,opening_hook,persona,speech_style,
                       desire_profile,signature_behaviors,loss_of_control,current_location_id,
                       accent,sort_order,state_summary,summary_updated_at,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    character_id, world_id, values["name"].strip(), int(values["age"]),
                    values.get("pronouns", "").strip(), values.get("role", "").strip(),
                    tagline, values.get("opening_hook", "").strip(), values.get("persona", "").strip(),
                    values.get("speech_style", "").strip(), _json(values.get("desire_profile", {})),
                    _json(values.get("signature_behaviors", [])),
                    values.get("loss_of_control", "").strip(), location_id,
                    values.get("accent", "#bc5d45"), order, tagline, None, created, created,
                ),
            )
            connection.execute(
                "INSERT INTO relationships VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (uid("rel"), world_id, character_id, None, 1, "未命名的牵引", 0, 0, 0, "", created),
            )
            connection.execute(
                """INSERT INTO character_world_states(
                       character_id,intention,surface_state,encounter_hook,availability,
                       pulse_number,interacted_since_pulse,updated_at)
                   VALUES (?,?,?,?,?,0,0,?)""",
                (
                    character_id, "", tagline,
                    values.get("opening_hook", "").strip(), "reachable", created,
                ),
            )
            self._capture_reset_baseline(connection, world_id, created)
        return self.get_character(character_id)

    def update_character(self, character_id: str, values: dict[str, Any]) -> dict[str, Any]:
        text_fields = (
            "name", "pronouns", "role", "tagline", "opening_hook", "persona", "speech_style",
            "loss_of_control", "current_location_id", "accent",
        )
        updates: dict[str, Any] = {
            key: str(values[key]).strip() if values[key] is not None else None
            for key in text_fields if key in values
        }
        if "age" in values:
            updates["age"] = int(values["age"])
        if "desire_profile" in values:
            updates["desire_profile"] = _json(values["desire_profile"])
        if "signature_behaviors" in values:
            updates["signature_behaviors"] = _json(values["signature_behaviors"])
        if not updates:
            return self.get_character(character_id)
        with self.transaction() as connection:
            current = connection.execute(
                "SELECT world_id FROM characters WHERE id=?", (character_id,)
            ).fetchone()
            if not current:
                raise KeyError("character_not_found")
            if updates.get("current_location_id") and not connection.execute(
                "SELECT 1 FROM locations WHERE id=? AND world_id=?",
                (updates["current_location_id"], current["world_id"]),
            ).fetchone():
                raise ValueError("invalid_location")
            assignments = ",".join(f"{key}=?" for key in updates)
            connection.execute(
                f"UPDATE characters SET {assignments}, updated_at=? WHERE id=?",
                [*updates.values(), now(), character_id],
            )
            baseline_updates: dict[str, Any] = {}
            if "current_location_id" in updates:
                presence.clear_departed(connection, current["world_id"])
                baseline_updates["current_location_id"] = updates["current_location_id"]
            if "tagline" in updates:
                baseline_updates["state_summary"] = updates["tagline"]
            if baseline_updates:
                baseline_assignments = ",".join(
                    f"{key}=?" for key in baseline_updates
                )
                connection.execute(
                    f"""UPDATE character_reset_baselines
                         SET {baseline_assignments},captured_at=? WHERE character_id=?""",
                    [*baseline_updates.values(), now(), character_id],
                )
        return self.get_character(character_id)

    def reset_world(self, world_id: str, confirmation: str) -> dict[str, Any]:
        """Delete story progress and restore the last authored world baseline."""
        reset_at = now()
        with self.transaction() as connection:
            world = connection.execute(
                "SELECT id,name FROM worlds WHERE id=?", (world_id,)
            ).fetchone()
            if not world:
                raise KeyError("world_not_found")
            if confirmation.strip() != world["name"]:
                raise ValueError("reset_confirmation_mismatch")

            self._capture_reset_baseline(connection, world_id, reset_at)
            counts = {
                "conversations": connection.execute(
                    "SELECT COUNT(*) FROM conversations WHERE world_id=?", (world_id,)
                ).fetchone()[0],
                "messages": connection.execute(
                    """SELECT COUNT(*) FROM messages m
                       JOIN conversations c ON c.id=m.conversation_id
                       WHERE c.world_id=?""",
                    (world_id,),
                ).fetchone()[0],
                "rooms": connection.execute(
                    "SELECT COUNT(*) FROM scene_rooms WHERE world_id=?", (world_id,)
                ).fetchone()[0],
                "events": connection.execute(
                    "SELECT COUNT(*) FROM world_events WHERE world_id=?", (world_id,)
                ).fetchone()[0],
            }

            connection.execute("DELETE FROM world_events WHERE world_id=?", (world_id,))
            connection.execute("DELETE FROM conversations WHERE world_id=?", (world_id,))
            connection.execute("DELETE FROM player_scene_presence WHERE world_id=?", (world_id,))
            connection.execute("DELETE FROM scene_rooms WHERE world_id=?", (world_id,))
            connection.execute("DELETE FROM player_commitments WHERE world_id=?", (world_id,))
            connection.execute(
                """UPDATE characters
                   SET current_location_id=(
                         SELECT b.current_location_id FROM character_reset_baselines b
                         WHERE b.character_id=characters.id
                       ),
                       state_summary=COALESCE((
                         SELECT b.state_summary FROM character_reset_baselines b
                         WHERE b.character_id=characters.id
                       ),tagline),
                       summary_updated_at=NULL,updated_at=?
                   WHERE world_id=?""",
                (reset_at, world_id),
            )
            connection.execute(
                """UPDATE relationships
                   SET label=COALESCE((
                         SELECT b.label FROM relationship_reset_baselines b
                         WHERE b.relationship_id=relationships.id
                       ),label),
                       trust=COALESCE((
                         SELECT b.trust FROM relationship_reset_baselines b
                         WHERE b.relationship_id=relationships.id
                       ),trust),
                       desire=COALESCE((
                         SELECT b.desire FROM relationship_reset_baselines b
                         WHERE b.relationship_id=relationships.id
                       ),desire),
                       tension=COALESCE((
                         SELECT b.tension FROM relationship_reset_baselines b
                         WHERE b.relationship_id=relationships.id
                       ),tension),
                       notes=COALESCE((
                         SELECT b.notes FROM relationship_reset_baselines b
                         WHERE b.relationship_id=relationships.id
                       ),notes),
                       updated_at=?
                   WHERE world_id=?""",
                (reset_at, world_id),
            )
            connection.execute(
                """UPDATE worlds
                   SET current_time=COALESCE((
                         SELECT b.current_time FROM world_reset_baselines b
                         WHERE b.world_id=worlds.id
                       ),current_time),updated_at=?
                   WHERE id=?""",
                (reset_at, world_id),
            )
            connection.execute(
                """UPDATE world_flow_states
                   SET player_location_id=NULL,player_condition='',visible_traces='[]',
                       pulse_narration='',pulse_number=0,ready_commitment_ids='[]',updated_at=?
                   WHERE world_id=?""",
                (reset_at, world_id),
            )
            connection.execute(
                """UPDATE character_world_states
                   SET intention='',
                       surface_state=COALESCE((
                           SELECT c.tagline FROM characters c
                           WHERE c.id=character_world_states.character_id
                       ),''),
                       encounter_hook=COALESCE((
                           SELECT c.opening_hook FROM characters c
                           WHERE c.id=character_world_states.character_id
                       ),''),
                       availability='reachable',pulse_number=0,
                       interacted_since_pulse=0,updated_at=?
                   WHERE character_id IN (
                       SELECT id FROM characters WHERE world_id=?
                   )""",
                (reset_at, world_id),
            )
            reset_generated(connection, world_id)
        return {"world": self.get_world(world_id), "cleared": counts}

    def get_world(self, world_id: str) -> dict[str, Any]:
        with self.read() as connection:
            world = connection.execute("SELECT * FROM worlds WHERE id=?", (world_id,)).fetchone()
            if not world:
                raise KeyError("world_not_found")
            locations = [dict(row) for row in connection.execute(
                "SELECT * FROM locations WHERE world_id=? ORDER BY sort_order,name", (world_id,)
            )]
            characters = [self._character(row) for row in connection.execute(
                """SELECT c.*, l.name AS location_name,
                          ws.surface_state,ws.encounter_hook,ws.availability,
                          ws.pulse_number,ws.interacted_since_pulse,
                          EXISTS(
                              SELECT 1 FROM conversations cv
                              JOIN messages m ON m.conversation_id=cv.id
                              WHERE cv.character_id=c.id
                          ) AS has_messages
                   FROM characters c LEFT JOIN locations l ON l.id=c.current_location_id
                   LEFT JOIN character_world_states ws ON ws.character_id=c.id
                   WHERE c.world_id=? ORDER BY c.sort_order,c.name""", (world_id,)
            )]
            events = [dict(row) for row in connection.execute(
                """SELECT e.*, l.name AS location_name
                   FROM world_events e LEFT JOIN locations l ON l.id=e.location_id
                   WHERE e.world_id=? AND e.visibility='public'
                   ORDER BY e.created_at DESC LIMIT 80""", (world_id,)
            )]
            relationships = [dict(row) for row in connection.execute(
                "SELECT * FROM relationships WHERE world_id=?", (world_id,)
            )]
            pending_sync_count = connection.execute(
                "SELECT COUNT(*) FROM world_events WHERE world_id=? AND cognition_synced=0",
                (world_id,),
            ).fetchone()[0]
            flow = connection.execute(
                """SELECT f.*,l.name AS player_location_name
                   FROM world_flow_states f
                   LEFT JOIN locations l ON l.id=f.player_location_id
                   WHERE f.world_id=?""",
                (world_id,),
            ).fetchone()
            commitments = connection.execute(
                """SELECT pc.id,pc.character_id,pc.text,c.name AS character_name
                   FROM player_commitments pc
                   LEFT JOIN characters c ON c.id=pc.character_id
                   WHERE pc.world_id=? ORDER BY pc.created_at""",
                (world_id,),
            ).fetchall()
            player = self._player_state(flow, list(commitments))
            ready = _loads(flow["ready_commitment_ids"], []) if flow else []
            for promise in player["commitments"]:
                promise["ready_to_visit"] = promise["id"] in ready
            pulse = {
                "number": flow["pulse_number"] if flow else 0,
                "narration": flow["pulse_narration"] if flow else "",
            }
        return {
            "world": dict(world), "player_profile": self.get_player_profile(world_id), "locations": locations, "characters": characters,
            "events": events, "relationships": relationships,
            "pending_sync_count": pending_sync_count, "player": player, "pulse": pulse, "time_preview": self.time_preview(world_id),
            "pending_settlements": self.pending_settlements(world_id),
        }

    def get_player_profile(self, world_id: str) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute("SELECT profile FROM player_profiles WHERE world_id=?", (world_id,)).fetchone()
        return _loads(row["profile"], {}) if row else {}

    def save_player_profile(self, world_id: str, values: dict[str, Any]) -> dict[str, Any]:
        profile = {key: values.get(key, "") for key in ("name", "pronouns", "appearance", "identity")}
        profile["age"] = values.get("age")
        if profile["age"] is not None and not 18 <= profile["age"] <= 999:
            raise ValueError("玩家年龄必须成年")
        with self.transaction() as connection:
            if not connection.execute("SELECT 1 FROM worlds WHERE id=?", (world_id,)).fetchone():
                raise KeyError("world_not_found")
            connection.execute("INSERT INTO player_profiles(world_id,profile) VALUES (?,?) ON CONFLICT(world_id) DO UPDATE SET profile=excluded.profile", (world_id, _json(profile)))
        return profile

    def get_character(self, character_id: str) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute(
                """SELECT c.*, l.name AS location_name, l.description AS location_description
                   FROM characters c LEFT JOIN locations l ON l.id=c.current_location_id
                   WHERE c.id=?""", (character_id,)
            ).fetchone()
        if not row:
            raise KeyError("character_not_found")
        return self._character(row)

    def get_or_create_conversation(self, world_id: str, character_id: str) -> str:
        if self.pending_settlements(world_id) or world_id in self._active_worlds:
            with self.read() as connection:
                existing = connection.execute(
                    "SELECT id FROM conversations WHERE world_id=? AND character_id=?",
                    (world_id, character_id),
                ).fetchone()
            if existing:
                return existing["id"]
            raise ValueError("还有未记下的余波，请先重新记录")
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM conversations WHERE world_id=? AND character_id=?",
                (world_id, character_id),
            ).fetchone()
            character = connection.execute(
                "SELECT name,current_location_id FROM characters WHERE id=? AND world_id=?",
                (character_id, world_id),
            ).fetchone()
            if not character:
                raise KeyError("character_not_found")
            entered_at = now()
            connection.execute(
                """INSERT INTO world_flow_states(
                       world_id,player_location_id,player_condition,visible_traces,
                       pulse_narration,pulse_number,updated_at)
                   VALUES (?,?,'','[]','',0,?)
                   ON CONFLICT(world_id) DO UPDATE SET
                       player_location_id=excluded.player_location_id,
                       updated_at=excluded.updated_at""",
                (world_id, character["current_location_id"], entered_at),
            )
            presence.enter_private(connection, world_id, character_id, entered_at)
            if existing:
                return existing["id"]
            conversation_id = uid("conv")
            created = entered_at
            connection.execute(
                "INSERT INTO conversations VALUES (?,?,?,?,?,?)",
                (conversation_id, world_id, character_id, f"与{character['name']}的对话", created, created),
            )
            connection.execute(
                "INSERT INTO scene_states VALUES (?,?,?,?,?,?,?,?)",
                (conversation_id, character["current_location_id"], "", 0, "[]", "", "", created),
            )
            return conversation_id

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        with self.read() as connection:
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
            if not conversation:
                raise KeyError("conversation_not_found")
            messages = [dict(row) for row in connection.execute(
                "SELECT m.*, json_extract(t.decision, '$.outcome') AS settlement_outcome FROM messages m LEFT JOIN turn_settlements t ON t.source_message_id=m.id WHERE m.conversation_id=? ORDER BY m.sequence",
                (conversation_id,),
            )]
            scene = self._scene(connection.execute(
                "SELECT * FROM scene_states WHERE conversation_id=?", (conversation_id,)
            ).fetchone())
            character = self._character(connection.execute(
                """SELECT c.*, l.name AS location_name, l.description AS location_description
                   FROM characters c LEFT JOIN locations l ON l.id=c.current_location_id
                   WHERE c.id=?""", (conversation["character_id"],)
            ).fetchone())
            scene["world_time"] = connection.execute(
                "SELECT worlds.current_time FROM worlds WHERE id=?", (conversation["world_id"],)
            ).fetchone()["current_time"]
            scene["location_id"] = character["current_location_id"]
            knowledge = [dict(row) for row in connection.execute(
                """SELECT k.*, e.title, e.details, e.world_time, e.visibility, l.name AS location_name
                   FROM character_knowledge k JOIN world_events e ON e.id=k.event_id
                   LEFT JOIN locations l ON l.id=e.location_id
                   WHERE k.character_id=? ORDER BY e.created_at DESC LIMIT 60""",
                (character["id"],),
            )]
            knowledge = information.restrict_room_knowledge(connection, knowledge, character['id'])
            knowledge = [{**knowledge_projection(item), 'state':item.get('state') or item.get('knowledge_state'),
                          'title':(knowledge_projection(item).get('details') or knowledge_projection(item).get('title',''))[:100]}
                         for item in knowledge]
            records = [dict(row) for row in connection.execute(
                """SELECT e.*, l.name AS location_name
                   FROM world_events e LEFT JOIN locations l ON l.id=e.location_id
                   WHERE e.source_conversation_id=? ORDER BY e.created_at""",
                (conversation_id,),
            )]
            settlements = [self._settlement(row) for row in connection.execute(
                """SELECT * FROM turn_settlements
                   WHERE conversation_id=? ORDER BY created_at, id""",
                (conversation_id,),
            )]
            settlement_by_message = {
                item["source_message_id"]: item for item in settlements
            }
            jobs = {row["source_id"]: dict(row) for row in connection.execute(
                "SELECT source_id,status,reason FROM settlement_jobs WHERE world_id=? AND status!='complete'",
                (conversation["world_id"],),
            )}
            entries = {
                row["message_id"]: {"pulse_number": row["pulse_number"], "entry_kind": row["entry_kind"]}
                for row in connection.execute(
                    "SELECT message_id,pulse_number,entry_kind FROM conversation_scene_entries WHERE conversation_id=?",
                    (conversation_id,),
                )
            }
            for message in messages:
                if message["id"] in entries:
                    message["scene_entry"] = entries[message["id"]]
                if message["id"] in jobs:
                    message["settlement_job"] = jobs[message["id"]]
                settlement = settlement_by_message.get(message["id"])
                if settlement:
                    message["settlement"] = settlement
            player_row = connection.execute(
                """SELECT f.*,l.name AS player_location_name
                   FROM world_flow_states f LEFT JOIN locations l ON l.id=f.player_location_id
                   WHERE f.world_id=?""",
                (conversation["world_id"],),
            ).fetchone()
            pulse_number = int(player_row["pulse_number"] if player_row else 0)
            scene_entry_row = connection.execute(
                """SELECT e.conversation_id,e.pulse_number,e.message_id,e.entry_kind,
                          e.created_at,m.content
                   FROM conversation_scene_entries e
                   JOIN messages m ON m.id=e.message_id
                   WHERE e.conversation_id=? AND e.pulse_number=?""",
                (conversation_id, pulse_number),
            ).fetchone()
            character_world_state = connection.execute(
                "SELECT availability FROM character_world_states WHERE character_id=?",
                (conversation["character_id"],),
            ).fetchone()
            commitments = connection.execute(
                """SELECT pc.id,pc.character_id,pc.text,c.name AS character_name
                   FROM player_commitments pc LEFT JOIN characters c ON c.id=pc.character_id
                   WHERE pc.world_id=? ORDER BY pc.created_at""",
                (conversation["world_id"],),
            ).fetchall()
        availability = (
            character_world_state["availability"] if character_world_state else "reachable"
        )
        needs_scene_entry = availability != "unavailable" and (
            not messages if pulse_number == 0 else scene_entry_row is None
        )
        return {
            "conversation": dict(conversation), "messages": messages, "scene": scene,
            "character": character, "knowledge": knowledge, "records": records,
            "settlements": settlements,
            "pending_settlements": self.pending_settlements(conversation["world_id"]),
            "player": self._player_state(player_row, list(commitments)),
            "pulse": {
                "number": pulse_number,
                "narration": player_row["pulse_narration"] if player_row else "",
            },
            "scene_entry": dict(scene_entry_row) if scene_entry_row else None,
            "needs_scene_entry": needs_scene_entry,
        }

    def add_message(
        self, conversation_id: str, role: str, content: str, status: str = "complete"
    ) -> dict[str, Any]:
        created = now()
        with self.transaction() as connection:
            if not connection.execute(
                "SELECT 1 FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone():
                raise KeyError("conversation_not_found")
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM messages WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()[0]
            message_id = uid("msg")
            connection.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (message_id, conversation_id, role, content, "", status, None, None, None, sequence, created, created),
            )
            connection.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?", (created, conversation_id)
            )
            row = connection.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        return dict(row)

    def add_opening_message(
        self, conversation_id: str, content: str, *, reasoning: str = "",
        finish_reason: str | None = None, input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        created = now()
        with self.transaction() as connection:
            conversation = connection.execute(
                "SELECT world_id,character_id FROM conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
            if not conversation:
                raise KeyError("conversation_not_found")
            flow = connection.execute(
                "SELECT pulse_number FROM world_flow_states WHERE world_id=?",
                (conversation["world_id"],),
            ).fetchone()
            pulse_number = int(flow["pulse_number"] if flow else 0)
            availability = connection.execute(
                "SELECT availability FROM character_world_states WHERE character_id=?",
                (conversation["character_id"],),
            ).fetchone()
            if availability and availability["availability"] == "unavailable":
                raise ValueError("character_unavailable")
            existing_entry = connection.execute(
                """SELECT m.* FROM conversation_scene_entries e
                   JOIN messages m ON m.id=e.message_id
                   WHERE e.conversation_id=? AND e.pulse_number=?""",
                (conversation_id, pulse_number),
            ).fetchone()
            if existing_entry:
                return dict(existing_entry), False
            existing_messages = connection.execute(
                "SELECT m.*, json_extract(t.decision, '$.outcome') AS settlement_outcome FROM messages m LEFT JOIN turn_settlements t ON t.source_message_id=m.id WHERE m.conversation_id=? ORDER BY m.sequence",
                (conversation_id,),
            ).fetchall()
            if pulse_number == 0 and existing_messages:
                return dict(existing_messages[0]), False
            message_id = uid("msg")
            sequence = (existing_messages[-1]["sequence"] if existing_messages else 0) + 1
            entry_kind = "reentry" if existing_messages else "initial"
            connection.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    message_id, conversation_id, "assistant", content, reasoning,
                    "complete", finish_reason, input_tokens, output_tokens, sequence,
                    created, created,
                ),
            )
            connection.execute(
                """INSERT INTO conversation_scene_entries(
                       conversation_id,pulse_number,message_id,entry_kind,created_at)
                   VALUES (?,?,?,?,?)""",
                (conversation_id, pulse_number, message_id, entry_kind, created),
            )
            connection.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?", (created, conversation_id)
            )
            row = connection.execute(
                "SELECT * FROM messages WHERE id=?", (message_id,)
            ).fetchone()
        return dict(row), True

    def update_message(
        self, message_id: str, *, content: str, reasoning: str = "",
        status: str = "complete", finish_reason: str | None = None,
        input_tokens: int | None = None, output_tokens: int | None = None,
        settlement_context: dict[str, Any] | None = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """UPDATE messages SET content=?,reasoning=?,status=?,finish_reason=?,
                          input_tokens=?,output_tokens=?,updated_at=? WHERE id=?""",
                (content, reasoning, status, finish_reason, input_tokens, output_tokens, now(), message_id),
            )
            if settlement_context is not None:
                if status != "complete" or not content.strip():
                    raise ValueError("invalid_settlement_message")
                self._enqueue_settlement(connection, message_id, settlement_context)

    def delete_message(self, message_id: str) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM messages WHERE id=?", (message_id,))

    def context_for_turn(self, conversation_id: str) -> dict[str, Any]:
        with self.read() as connection:
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
            if not conversation:
                raise KeyError("conversation_not_found")
            world = dict(connection.execute(
                "SELECT * FROM worlds WHERE id=?", (conversation["world_id"],)
            ).fetchone())
            character = self._character(connection.execute(
                """SELECT c.*, l.name AS location_name, l.description AS location_description
                   FROM characters c LEFT JOIN locations l ON l.id=c.current_location_id
                   WHERE c.id=?""", (conversation["character_id"],)
            ).fetchone())
            scene = self._scene(connection.execute(
                "SELECT * FROM scene_states WHERE conversation_id=?", (conversation_id,)
            ).fetchone())
            previous_row = connection.execute(
                """SELECT t.scene,j.context FROM turn_settlements t JOIN messages m ON m.id=t.source_message_id
                   LEFT JOIN settlement_jobs j ON j.source_id=t.source_message_id
                   WHERE t.conversation_id=? AND json_extract(t.decision, '$.outcome') IS NOT 'rejected' ORDER BY m.sequence DESC LIMIT 1""", (conversation_id,),
            ).fetchone()
            previous_scene = dict(scene)
            if previous_row:
                previous_patch = _loads(previous_row["scene"], {})
                previous_context = _loads(previous_row["context"], {})
                previous_scene.update(previous_context.get("scene") or {})
                previous_scene.update(previous_patch)
                previous_scene["world_time"] = previous_patch.get("world_time") or (previous_context.get("world") or {}).get("current_time") or previous_scene.get("world_time") or ""
            scene["world_time"] = world["current_time"]
            scene["location_id"] = character.get("current_location_id")
            messages = [dict(row) for row in connection.execute(
                "SELECT m.*, json_extract(t.decision, '$.outcome') AS settlement_outcome FROM messages m LEFT JOIN turn_settlements t ON t.source_message_id=m.id WHERE m.conversation_id=? ORDER BY m.sequence",
                (conversation_id,),
            )]
            public_events = [dict(row) for row in connection.execute(
                """SELECT * FROM (
                       SELECT e.*, k.state AS knowledge_state, k.belief, k.source,
                              l.name AS location_name FROM character_knowledge k
                       JOIN world_events e ON e.id=k.event_id
                       LEFT JOIN locations l ON l.id=e.location_id
                       WHERE k.character_id=? AND e.world_id=? AND e.visibility='public'
                       ORDER BY e.created_at DESC LIMIT 120
                   ) recent ORDER BY created_at""", (character["id"], world["id"])
            )]
            knowledge = [dict(row) for row in connection.execute(
                """SELECT * FROM (
                       SELECT e.*, k.state AS knowledge_state, k.belief, k.confidence,
                              k.source, l.name AS location_name
                       FROM character_knowledge k JOIN world_events e ON e.id=k.event_id
                       LEFT JOIN locations l ON l.id=e.location_id
                       WHERE k.character_id=? AND e.visibility!='public'
                       ORDER BY e.created_at DESC LIMIT 120
                   ) recent ORDER BY created_at""", (character["id"],)
            )]
            public_events = information.restrict_room_knowledge(connection, public_events, character['id'])
            knowledge = information.restrict_room_knowledge(connection, knowledge, character['id'])
            relationships = [dict(row) for row in connection.execute(
                "SELECT * FROM relationships WHERE from_character_id=?", (character["id"],)
            )]
            locations = [dict(row) for row in connection.execute(
                "SELECT * FROM locations WHERE world_id=? ORDER BY sort_order", (world["id"],)
            )]
            locations = known_locations(locations, character['id'])
            character = project_location_fields(character, locations, id_key='current_location_id')
            scene = project_location_fields(scene, locations)
            previous_scene = project_location_fields(previous_scene, locations)
            characters = [self._character(row) for row in connection.execute(
                "SELECT * FROM characters WHERE world_id=? ORDER BY sort_order", (world["id"],)
            )]
            player_row = connection.execute(
                """SELECT f.*,l.name AS player_location_name
                   FROM world_flow_states f LEFT JOIN locations l ON l.id=f.player_location_id
                   WHERE f.world_id=?""",
                (world["id"],),
            ).fetchone()
            commitments = connection.execute(
                """SELECT pc.id,pc.character_id,pc.text,c.name AS character_name
                   FROM player_commitments pc LEFT JOIN characters c ON c.id=pc.character_id
                   WHERE pc.world_id=? AND (pc.character_id=? OR pc.character_id IS NULL)
                   ORDER BY pc.created_at""",
                (world["id"], character["id"]),
            ).fetchall()
            world_state = connection.execute(
                "SELECT * FROM character_world_states WHERE character_id=?",
                (character["id"],),
            ).fetchone()
        return {
            "conversation": dict(conversation), "world": world, "character": character,
            "player_profile": self.get_player_profile(world["id"]),
            "scene": scene, "previous_scene": previous_scene, "messages": messages, "public_events": [knowledge_projection(item) for item in public_events],
            "knowledge": [knowledge_projection(item) for item in knowledge], "relationships": relationships,
            "locations": locations, "characters": [public_identity(item) for item in characters],
            "player": player_observation(project_location_fields(self._player_state(player_row, list(commitments)), locations), [item["name"] for item in characters]),
            "pulse": {
                "number": int(player_row["pulse_number"] if player_row else 0),
                "narration": player_row["pulse_narration"] if player_row else "",
            },
            "character_world_state": dict(world_state) if world_state else {},
        }

    @staticmethod
    def _apply_player_patch(
        connection: sqlite3.Connection,
        *,
        world_id: str,
        character_id: str,
        scene_location_id: str | None,
        patch: dict[str, Any],
        updated_at: str,
    ) -> None:
        connection.execute(
            """INSERT OR IGNORE INTO world_flow_states(
                   world_id,player_location_id,player_condition,visible_traces,
                   pulse_narration,pulse_number,updated_at)
               VALUES (?,NULL,'','[]','',0,?)""",
            (world_id, updated_at),
        )
        player_patch = patch.get("player") if isinstance(patch.get("player"), dict) else {}
        location_id = player_patch.get("location_id")
        if location_id and not connection.execute(
            "SELECT 1 FROM locations WHERE id=? AND world_id=?",
            (location_id, world_id),
        ).fetchone():
            location_id = None
        if not location_id and player_patch.get("location"):
            found = connection.execute(
                "SELECT id FROM locations WHERE world_id=? AND name=?",
                (world_id, str(player_patch["location"]).strip()),
            ).fetchone()
            location_id = found["id"] if found else None
        current = connection.execute(
            "SELECT * FROM world_flow_states WHERE world_id=?", (world_id,)
        ).fetchone()
        effective_location = location_id or scene_location_id or current["player_location_id"]
        condition = (
            str(player_patch.get("condition") or "").strip()
            if "condition" in player_patch else current["player_condition"]
        )
        traces = (
            [str(item).strip() for item in player_patch.get("visible_traces", []) if str(item).strip()][:8]
            if isinstance(player_patch.get("visible_traces"), list)
            else _loads(current["visible_traces"], [])
        )
        connection.execute(
            """UPDATE world_flow_states
               SET player_location_id=?,player_condition=?,visible_traces=?,updated_at=?
               WHERE world_id=?""",
            (effective_location, condition, _json(traces), updated_at, world_id),
        )
        if isinstance(player_patch.get("commitments"), list):
            promises = list(dict.fromkeys(
                str(item).strip()[:160] for item in player_patch["commitments"] if str(item).strip()
            ))
            existing = {row["text"]: row["id"] for row in connection.execute(
                "SELECT id,text FROM player_commitments WHERE world_id=? AND character_id=?",
                (world_id, character_id),
            )}
            for text, commitment_id in existing.items():
                if text not in promises:
                    connection.execute("DELETE FROM player_commitments WHERE id=?", (commitment_id,))
            for text in promises:
                if text not in existing:
                    connection.execute(
                        "INSERT INTO player_commitments VALUES (?,?,?,?,?)",
                        (uid("promise"), world_id, character_id, text, updated_at),
                    )
        connection.execute(
            """UPDATE character_world_states
               SET surface_state=CASE WHEN ?!='' THEN ? ELSE surface_state END,
                   encounter_hook=CASE WHEN ?!='' THEN ? ELSE encounter_hook END,
                   availability=CASE WHEN (SELECT current_location_id FROM characters WHERE id=character_world_states.character_id) = ? THEN 'present' ELSE 'reachable' END,interacted_since_pulse=1,updated_at=?
               WHERE character_id=?""",
            (
                str((patch.get("scene") or {}).get("emotional_state") or "").strip(),
                str((patch.get("scene") or {}).get("emotional_state") or "").strip(),
                str((patch.get("scene") or {}).get("physical_state") or "").strip(),
                str((patch.get("scene") or {}).get("physical_state") or "").strip(),
                effective_location, updated_at, character_id,
            ),
        )

    def enter_room(self, room_id: str) -> dict[str, Any]:
        entered_at = now()
        current = self.get_room(room_id)
        world_id = current["world"]["id"]
        if self.pending_settlements(world_id) or world_id in self._active_worlds:
            return current
        with self.transaction() as connection:
            room = connection.execute(
                "SELECT world_id,location_id FROM scene_rooms WHERE id=?", (room_id,)
            ).fetchone()
            if not room:
                raise KeyError("room_not_found")
            connection.execute(
                """INSERT INTO world_flow_states(
                       world_id,player_location_id,player_condition,visible_traces,
                       pulse_narration,pulse_number,updated_at)
                   VALUES (?,?,'','[]','',0,?)
                   ON CONFLICT(world_id) DO UPDATE SET
                       player_location_id=excluded.player_location_id,
                       updated_at=excluded.updated_at""",
                (room["world_id"], room["location_id"], entered_at),
            )
            presence.enter_room(connection, world_id, room_id, entered_at)
        return self.get_room(room_id)

    def assert_conversation_entry(self, conversation_id: str) -> None:
        with self.read() as connection:
            presence.assert_private(connection, conversation_id)

    def assert_room_entry(self, room_id: str) -> None:
        with self.read() as connection:
            presence.assert_room(connection, room_id)

    def room_is_current(self, room_id: str) -> bool:
        with self.read() as connection:
            return presence.room_is_current(connection, room_id)

    def apply_world_patch(
        self, conversation_id: str, source_message_id: str, patch: dict[str, Any],
        *, context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if context is None:
            context = self.context_for_turn(conversation_id)
            source_index = next((i for i, m in enumerate(context["messages"]) if m["id"] == source_message_id), len(context["messages"]))
            context["messages"] = context["messages"][:source_index]
        created = now()
        with self.transaction() as connection:
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
            if not conversation:
                raise KeyError("conversation_not_found")
            message = connection.execute(
                "SELECT role,content,status FROM messages WHERE id=? AND conversation_id=?",
                (source_message_id, conversation_id),
            ).fetchone()
            if not message or message["role"] != "assistant":
                raise ValueError("invalid_patch_source")
            if message["status"] != "complete":
                raise ValueError("invalid_patch_source")
            existing = self._existing_patch_result(connection, source_message_id)
            if existing:
                return existing
            patch = validate_patch(patch, context, message["content"])
            commit_mentions(connection, context, source_message_id)
            commit_plan(connection, context, source_message_id, message["content"])
            character_id = conversation["character_id"]
            scene_patch = patch.get("scene") or {}
            if scene_patch:
                location_id = scene_patch.get("location_id")
                if not location_id and scene_patch.get("location"):
                    found = connection.execute(
                        "SELECT id FROM locations WHERE world_id=? AND name=?",
                        (conversation["world_id"], scene_patch["location"]),
                    ).fetchone()
                    location_id = found["id"] if found else None
                current = connection.execute(
                    "SELECT * FROM scene_states WHERE conversation_id=?", (conversation_id,)
                ).fetchone()
                threads = scene_patch.get("active_threads")
                effective_location = location_id or context["scene"].get("location_id")
                effective_time = _sanitize_world_time(
                    scene_patch.get("world_time"),
                    context["world"]["current_time"],
                ) or context["world"]["current_time"]
                connection.execute(
                    """INSERT INTO scene_states(conversation_id,location_id,world_time,tension,
                              active_threads,physical_state,emotional_state,updated_at)
                       VALUES (?,?,?,?,?,?,?,?)
                       ON CONFLICT(conversation_id) DO UPDATE SET location_id=excluded.location_id,
                              world_time=excluded.world_time,tension=excluded.tension,
                              active_threads=excluded.active_threads,physical_state=excluded.physical_state,
                              emotional_state=excluded.emotional_state,updated_at=excluded.updated_at""",
                    (
                        conversation_id, effective_location, effective_time,
                        max(0, min(100, int(scene_patch.get("tension", current["tension"] if current else 0)))),
                        _json(threads if isinstance(threads, list) else _loads(current["active_threads"], []) if current else []),
                        scene_patch.get("physical_state", current["physical_state"] if current else ""),
                        scene_patch.get("emotional_state", current["emotional_state"] if current else ""),
                        created,
                    ),
                )
                if effective_location:
                    connection.execute(
                        "UPDATE characters SET current_location_id=?,updated_at=? WHERE id=?",
                        (effective_location, created, character_id),
                    )
                    presence.clear_departed(connection, conversation["world_id"])
                if effective_time:
                    connection.execute(
                        "UPDATE worlds SET current_time=?,updated_at=? WHERE id=?",
                        (effective_time, created, conversation["world_id"]),
                    )

            event = patch.get("event")
            event_id = None
            record = None
            if isinstance(event, dict) and event.get("title"):
                event_id = uid("event")
                visibility = event.get("visibility") if event.get("visibility") in {"public", "private", "secret"} else "private"
                state = connection.execute(
                    "SELECT location_id FROM scene_states WHERE conversation_id=?",
                    (conversation_id,),
                ).fetchone()
                location_id = state["location_id"] if state else None
                event_world_time = (
                    _sanitize_world_time(event.get("world_time"))
                    or _sanitize_world_time(scene_patch.get("world_time"))
                    or connection.execute(
                        "SELECT worlds.current_time FROM worlds WHERE id=?",
                        (conversation["world_id"],),
                    ).fetchone()[0]
                )
                connection.execute(
                    "INSERT INTO world_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        event_id, conversation["world_id"], conversation_id, source_message_id,
                        str(event["title"])[:80], str(event.get("details", "")), visibility,
                        location_id, event_world_time, 0, created,
                    ),
                )
                character_by_name = {
                    row["name"]: row["id"] for row in connection.execute(
                        "SELECT id,name FROM characters WHERE world_id=?", (conversation["world_id"],)
                    )
                }
                participant_ids = {character_id}
                for name in event.get("participants", []):
                    if name in character_by_name:
                        participant_ids.add(character_by_name[name])
                for participant_id in participant_ids:
                    connection.execute(
                        "INSERT OR IGNORE INTO event_participants VALUES (?,?,?)",
                        (event_id, participant_id, "participant"),
                    )
                witness_names = set(event.get("witnesses", []))
                active_name = next(
                    (name for name, cid in character_by_name.items() if cid == character_id), None
                )
                if active_name:
                    witness_names.add(active_name)
                beliefs = event.get("beliefs") if isinstance(event.get("beliefs"), dict) else {}
                for name in witness_names:
                    knowledge_character_id = character_by_name.get(name)
                    if not knowledge_character_id:
                        continue
                    belief = str(beliefs.get(name, ""))
                    source = event["knowledge_sources"][name]
                    knowledge_state = "misunderstood" if belief else ("heard" if source["kind"] == "told" else "witnessed")
                    connection.execute(
                        """INSERT OR REPLACE INTO character_knowledge
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (
                            uid("know"), event_id, knowledge_character_id, knowledge_state,
                            belief, 0.7 if belief else 1.0, _json(source), created,
                        ),
                    )
                record = {
                    "id": event_id, "title": str(event["title"])[:80],
                    "details": str(event.get("details", "")), "visibility": visibility,
                    "world_time": event_world_time,
                    "cognition_synced": 0,
                }

            relationship = patch.get("relationship") or {}
            if relationship:
                row = connection.execute(
                    "SELECT * FROM relationships WHERE from_character_id=? AND to_user=1",
                    (character_id,),
                ).fetchone()
                if row:
                    connection.execute(
                        """UPDATE relationships SET trust=?,desire=?,tension=?,label=?,notes=?,updated_at=?
                           WHERE id=?""",
                        (
                            max(-100, min(100, row["trust"] + int(relationship.get("trust_delta", 0)))),
                            max(-100, min(100, row["desire"] + int(relationship.get("desire_delta", 0)))),
                            max(0, min(100, row["tension"] + int(relationship.get("tension_delta", 0)))),
                            relationship.get("label") or row["label"],
                            relationship.get("note") or row["notes"], created, row["id"],
                        ),
                    )
            scene_location = connection.execute(
                "SELECT location_id FROM scene_states WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            self._apply_player_patch(
                connection,
                world_id=conversation["world_id"],
                character_id=character_id,
                scene_location_id=scene_location["location_id"] if scene_location else None,
                patch=patch,
                updated_at=created,
            )
            settlement = self._insert_turn_settlement(
                connection,
                world_id=conversation["world_id"],
                conversation_id=conversation_id,
                source_message_id=source_message_id,
                event_id=event_id,
                patch=patch,
                created_at=created,
            )
            connection.execute(
                "UPDATE worlds SET updated_at=? WHERE id=?", (created, conversation["world_id"])
            )
        return {"event_id": event_id, "record": record, "settlement": settlement}

    def cognition_sync_context(self, world_id: str) -> dict[str, Any]:
        with self.read() as connection:
            world_row = connection.execute("SELECT * FROM worlds WHERE id=?", (world_id,)).fetchone()
            if not world_row:
                raise KeyError("world_not_found")
            world = dict(world_row)
            locations = [dict(row) for row in connection.execute(
                "SELECT * FROM locations WHERE world_id=? ORDER BY sort_order", (world_id,)
            )]
            player_row = connection.execute(
                """SELECT f.*,l.name AS player_location_name
                   FROM world_flow_states f LEFT JOIN locations l ON l.id=f.player_location_id
                   WHERE f.world_id=?""",
                (world_id,),
            ).fetchone()
            all_commitments = connection.execute(
                """SELECT pc.id,pc.character_id,pc.text,c.name AS character_name
                   FROM player_commitments pc LEFT JOIN characters c ON c.id=pc.character_id
                   WHERE pc.world_id=? ORDER BY pc.created_at""",
                (world_id,),
            ).fetchall()
            player = self._player_state(player_row, list(all_commitments))
            pending = [dict(row) for row in connection.execute(
                """SELECT e.*, l.name AS location_name FROM world_events e
                   LEFT JOIN locations l ON l.id=e.location_id
                   WHERE e.world_id=? AND e.cognition_synced=0 ORDER BY e.created_at""",
                (world_id,),
            )]
            characters: list[dict[str, Any]] = []
            for row in connection.execute(
                """SELECT c.*, l.name AS location_name FROM characters c
                   LEFT JOIN locations l ON l.id=c.current_location_id
                   WHERE c.world_id=? ORDER BY c.sort_order""", (world_id,)
            ):
                character = self._character(row)
                relationship = connection.execute(
                    "SELECT * FROM relationships WHERE from_character_id=? AND to_user=1",
                    (character["id"],),
                ).fetchone()
                known = {
                    item["event_id"]: dict(item) for item in connection.execute(
                        "SELECT event_id,state AS knowledge_state,belief,source FROM character_knowledge WHERE character_id=?",
                        (character["id"],),
                    )
                }
                relevant_pending = [
                    {**event, **known.get(event["id"], {})} for event in pending
                    if event["visibility"] == "public" or event["id"] in known
                ]
                knowledge = [dict(item) for item in connection.execute(
                    """SELECT e.id,e.title,e.details,e.world_time,e.location_id,e.visibility,k.state,k.belief,k.source
                       FROM character_knowledge k JOIN world_events e ON e.id=k.event_id
                       WHERE k.character_id=? ORDER BY e.created_at DESC LIMIT 30""",
                    (character["id"],),
                )]
                relevant_pending = [knowledge_projection(item) for item in information.restrict_room_knowledge(connection,relevant_pending,character['id'])]
                knowledge = information.restrict_room_knowledge(connection,knowledge,character['id'])
                world_state = connection.execute(
                    "SELECT * FROM character_world_states WHERE character_id=?",
                    (character["id"],),
                ).fetchone()
                personal_locations = known_locations(locations, character['id'])
                characters.append({
                    "character": project_location_fields(character, personal_locations, id_key='current_location_id'),
                    "locations": personal_locations,
                    "relationship": dict(relationship) if relationship else {},
                    "new_events": relevant_pending,
                    "knowledge": [knowledge_projection(item) for item in knowledge],
                    "commitments": [
                        dict(item) for item in all_commitments
                        if item["character_id"] in {None, character["id"]}
                    ],
                    "world_state": dict(world_state) if world_state else {},
                })
        return {
            "world": world, "pending_events": pending, "characters": characters,
            "locations": locations, "player": player,
        }

    def commit_cognition_sync(
        self, world_id: str, event_ids: list[str], summaries: dict[str, str]
    ) -> dict[str, Any]:
        if not event_ids:
            return {"synced_events": 0, "updated_characters": []}
        updated_at = now()
        with self.transaction() as connection:
            marks = ",".join("?" for _ in event_ids)
            events = connection.execute(
                f"SELECT * FROM world_events WHERE world_id=? AND id IN ({marks}) AND cognition_synced=0",
                (world_id, *event_ids),
            ).fetchall()
            characters = connection.execute(
                "SELECT id FROM characters WHERE world_id=?", (world_id,)
            ).fetchall()
            for event in events:
                if event["visibility"] != "public":
                    continue
                for character in characters:
                    if connection.execute(
                        "SELECT 1 FROM character_knowledge WHERE event_id=? AND character_id=?",
                        (event["id"], character["id"]),
                    ).fetchone():
                        continue
                    connection.execute(
                        "INSERT INTO character_knowledge VALUES (?,?,?,?,?,?,?,?)",
                        (uid("know"), event["id"], character["id"], "heard", "", 0.85, "世界公开变化", updated_at),
                    )
            updated_characters: list[str] = []
            for character_id, summary in summaries.items():
                clean = summary.strip()
                if not clean:
                    continue
                result = connection.execute(
                    """UPDATE characters SET state_summary=?,summary_updated_at=?,updated_at=?
                       WHERE id=? AND world_id=?""",
                    (clean, updated_at, updated_at, character_id, world_id),
                )
                if result.rowcount:
                    updated_characters.append(character_id)
            presence.clear_departed(connection, world_id)
            actual_event_ids = [event["id"] for event in events]
            if actual_event_ids:
                actual_marks = ",".join("?" for _ in actual_event_ids)
                connection.execute(
                    f"UPDATE world_events SET cognition_synced=1 WHERE id IN ({actual_marks})",
                    actual_event_ids,
                )
            connection.execute(
                "UPDATE worlds SET updated_at=? WHERE id=?", (updated_at, world_id)
            )
        return {
            "synced_events": len(actual_event_ids),
            "updated_characters": updated_characters,
        }

    def commit_world_pulse(
        self,
        world_id: str,
        event_ids: list[str],
        actions: dict[str, dict[str, Any]],
        transition: dict[str, Any],
    ) -> dict[str, Any]:
        updated_at = now()
        with self.transaction() as connection:
            world_row = connection.execute(
                "SELECT worlds.current_time FROM worlds WHERE id=?", (world_id,)
            ).fetchone()
            if not world_row:
                raise KeyError("world_not_found")
            next_time = _sanitize_world_time(transition.get("world_time"))
            if not next_time or next_time == world_row["current_time"]:
                raise ValueError("世界时间没有向前，请重新选择时刻")
            events: list[sqlite3.Row] = []
            if event_ids:
                marks = ",".join("?" for _ in event_ids)
                events = connection.execute(
                    f"""SELECT * FROM world_events
                        WHERE world_id=? AND id IN ({marks}) AND cognition_synced=0""",
                    (world_id, *event_ids),
                ).fetchall()
            characters = connection.execute(
                "SELECT id FROM characters WHERE world_id=?", (world_id,)
            ).fetchall()
            for event in events:
                if event["visibility"] != "public":
                    continue
                for character in characters:
                    if connection.execute(
                        "SELECT 1 FROM character_knowledge WHERE event_id=? AND character_id=?",
                        (event["id"], character["id"]),
                    ).fetchone():
                        continue
                    connection.execute(
                        "INSERT INTO character_knowledge VALUES (?,?,?,?,?,?,?,?)",
                        (
                            uid("know"), event["id"], character["id"], "heard", "",
                            0.85, "世界公开变化", updated_at,
                        ),
                    )
            current_pulse = connection.execute(
                "SELECT pulse_number FROM world_flow_states WHERE world_id=?", (world_id,)
            ).fetchone()
            pulse_number = (current_pulse["pulse_number"] if current_pulse else 0) + 1
            updated_characters: list[str] = []
            for character_id, action in actions.items():
                if not connection.execute(
                    "SELECT 1 FROM characters WHERE id=? AND world_id=?",
                    (character_id, world_id),
                ).fetchone():
                    continue
                location_id = action.get("location_id")
                if location_id and connection.execute(
                    "SELECT 1 FROM locations WHERE id=? AND world_id=?",
                    (location_id, world_id),
                ).fetchone():
                    connection.execute(
                        "UPDATE characters SET current_location_id=? WHERE id=?",
                        (location_id, character_id),
                    )
                summary = str(action.get("surface_state") or "").strip()
                if summary:
                    connection.execute(
                        """UPDATE characters SET state_summary=?,summary_updated_at=?,updated_at=?
                           WHERE id=?""",
                        (summary[:160], updated_at, updated_at, character_id),
                    )
                availability = action.get("availability")
                if availability not in {"present", "reachable", "incoming", "unavailable"}:
                    availability = "reachable"
                connection.execute(
                    """INSERT INTO character_world_states(
                           character_id,intention,surface_state,encounter_hook,availability,
                           pulse_number,interacted_since_pulse,updated_at)
                       VALUES (?,?,?,?,?,?,0,?)
                       ON CONFLICT(character_id) DO UPDATE SET
                           intention=excluded.intention,
                           surface_state=excluded.surface_state,
                           encounter_hook=excluded.encounter_hook,
                           availability=excluded.availability,
                           pulse_number=excluded.pulse_number,
                           interacted_since_pulse=0,
                           updated_at=excluded.updated_at""",
                    (
                        character_id, str(action.get("intention") or "")[:240], summary[:160],
                        str(action.get("encounter_hook") or "")[:240], availability,
                        pulse_number, updated_at,
                    ),
                )
                updated_characters.append(character_id)
            presence.clear_departed(connection, world_id)
            actual_event_ids = [event["id"] for event in events]
            if actual_event_ids:
                actual_marks = ",".join("?" for _ in actual_event_ids)
                connection.execute(
                    f"UPDATE world_events SET cognition_synced=1 WHERE id IN ({actual_marks})",
                    actual_event_ids,
                )
            next_time = _sanitize_world_time(transition.get("world_time"))
            if next_time:
                connection.execute(
                    "UPDATE worlds SET current_time=?,updated_at=? WHERE id=?",
                    (next_time[:120], updated_at, world_id),
                )
                connection.execute(
                    """UPDATE scene_states SET world_time=?,updated_at=?
                       WHERE conversation_id IN (
                           SELECT id FROM conversations WHERE world_id=?
                       )""",
                    (next_time[:120], updated_at, world_id),
                )
                connection.execute(
                    "UPDATE scene_rooms SET world_time=?,updated_at=? WHERE world_id=?",
                    (next_time[:120], updated_at, world_id),
                )
            flow = connection.execute(
                "SELECT * FROM world_flow_states WHERE world_id=?", (world_id,)
            ).fetchone()
            traces = transition.get("visible_traces")
            if not isinstance(traces, list):
                traces = _loads(flow["visible_traces"], []) if flow else []
            condition = (
                str(transition.get("player_condition") or "").strip()
                if "player_condition" in transition
                else (flow["player_condition"] if flow else "")
            )
            connection.execute(
                """INSERT INTO world_flow_states(
                       world_id,player_location_id,player_condition,visible_traces,
                       pulse_narration,pulse_number,updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(world_id) DO UPDATE SET
                       player_condition=excluded.player_condition,
                       visible_traces=excluded.visible_traces,
                       pulse_narration=excluded.pulse_narration,
                       pulse_number=excluded.pulse_number,
                       updated_at=excluded.updated_at""",
                (
                    world_id, flow["player_location_id"] if flow else None,
                    condition[:200], _json([str(item)[:160] for item in traces[:8]]),
                    str(transition.get("narration") or "")[:800], pulse_number, updated_at,
                ),
            )
            valid_promises = {r[0] for r in connection.execute("SELECT id FROM player_commitments WHERE world_id=?", (world_id,))}
            ready = transition.get("ready_commitment_ids", [])
            if not isinstance(ready, list) or any(not isinstance(i, str) or i not in valid_promises for i in ready):
                raise ValueError("赴约入口无效")
            connection.execute("UPDATE world_flow_states SET ready_commitment_ids=? WHERE world_id=?", (_json(ready), world_id))
            connection.execute(
                "UPDATE worlds SET updated_at=? WHERE id=?", (updated_at, world_id)
            )
        return {
            "synced_events": len(actual_event_ids),
            "updated_characters": updated_characters,
            "pulse_number": pulse_number,
        }

    def active_provider(self) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute(
                "SELECT * FROM provider_profiles WHERE is_active=1 ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            raise KeyError("provider_not_configured")
        return dict(row)

    def save_provider(self, values: dict[str, Any]) -> dict[str, Any]:
        profile_id = values.get("id") or "provider_default"
        protocol = values.get("protocol", "openai-compatible")
        if protocol not in {"openai-compatible", "anthropic-messages"}:
            raise ValueError("invalid_protocol")
        with self.transaction() as connection:
            connection.execute("UPDATE provider_profiles SET is_active=0")
            connection.execute(
                """INSERT INTO provider_profiles(id,name,protocol,base_url,path,model,max_tokens,
                          temperature,is_active,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,1,?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name,protocol=excluded.protocol,
                          base_url=excluded.base_url,path=excluded.path,model=excluded.model,
                          max_tokens=excluded.max_tokens,temperature=excluded.temperature,
                          is_active=1,updated_at=excluded.updated_at""",
                (
                    profile_id, values.get("name", "我的模型"), protocol,
                    values["base_url"].rstrip("/"), values["path"], values["model"],
                    int(values.get("max_tokens", 16384)),
                    float(values.get("temperature", 0.95)), now(),
                ),
            )
        return self.active_provider()

    # ---------- 同场房间 ----------

    def list_rooms(self, world_id: str) -> list[dict[str, Any]]:
        with self.read() as connection:
            rows = connection.execute(
                """SELECT r.*, l.name AS location_name,
                          (SELECT COUNT(*) FROM room_characters rc WHERE rc.room_id=r.id) AS member_count,
                          (SELECT content FROM room_messages m WHERE m.room_id=r.id ORDER BY m.sequence DESC LIMIT 1) AS last_content,
                          (SELECT GROUP_CONCAT(c.name, '、' ORDER BY rc.sort_order)
                             FROM room_characters rc JOIN characters c ON c.id=rc.character_id
                            WHERE rc.room_id=r.id) AS member_names
                   FROM scene_rooms r LEFT JOIN locations l ON l.id=r.location_id
                   WHERE r.world_id=? ORDER BY r.updated_at DESC""",
                (world_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_room(
        self, world_id: str, name: str, location_id: str | None, character_ids: list[str]
    ) -> dict[str, Any]:
        room_id = uid("room")
        created = now()
        with self.transaction() as connection:
            if not connection.execute("SELECT 1 FROM worlds WHERE id=?", (world_id,)).fetchone():
                raise KeyError("world_not_found")
            if location_id and not connection.execute(
                "SELECT 1 FROM locations WHERE id=? AND world_id=?", (location_id, world_id)
            ).fetchone():
                raise ValueError("invalid_location")
            valid = {
                row[0] for row in connection.execute(
                    "SELECT id FROM characters WHERE world_id=?", (world_id,)
                )
            }
            members = list(dict.fromkeys(character_ids))
            if not members:
                raise ValueError("room_members_required")
            if any(character_id not in valid for character_id in members):
                raise ValueError("invalid_room_member")
            if location_id is None:
                player = connection.execute("SELECT player_location_id FROM world_flow_states WHERE world_id=?", (world_id,)).fetchone()
                location_id = player["player_location_id"] if player else None
                if location_id is None:
                    location_id = connection.execute("SELECT current_location_id FROM characters WHERE id=?", (members[0],)).fetchone()[0]
            world_time = connection.execute(
                "SELECT worlds.current_time FROM worlds WHERE id=?", (world_id,)
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO scene_rooms(
                       id,world_id,name,location_id,world_time,tension,active_threads,
                       physical_state,emotional_state,created_at,updated_at)
                   VALUES (?,?,?,?,?,0,'[]','','',?,?)""",
                (
                    room_id, world_id, (name or "同场").strip(), location_id,
                    world_time, created, created,
                ),
            )
            for order, character_id in enumerate(members):
                connection.execute(
                    "INSERT INTO room_characters VALUES (?,?,?)",
                    (room_id, character_id, order),
                )
            presence.gather_room(connection, world_id, room_id, location_id, members, created)
        return self.get_room(room_id)

    def delete_room(self, room_id: str) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM scene_rooms WHERE id=?", (room_id,))

    def get_room(self, room_id: str) -> dict[str, Any]:
        with self.read() as connection:
            room = connection.execute("SELECT * FROM scene_rooms WHERE id=?", (room_id,)).fetchone()
            if not room:
                raise KeyError("room_not_found")
            world = dict(connection.execute(
                "SELECT * FROM worlds WHERE id=?", (room["world_id"],)
            ).fetchone())
            location = dict(connection.execute(
                "SELECT * FROM locations WHERE id=?", (room["location_id"],)
            ).fetchone()) if room["location_id"] else None
            characters = [self._character(row) for row in connection.execute(
                """SELECT c.*, l.name AS location_name FROM room_characters rc
                   JOIN characters c ON c.id=rc.character_id
                   LEFT JOIN locations l ON l.id=c.current_location_id
                   WHERE rc.room_id=? ORDER BY rc.sort_order""", (room_id,)
            )]
            present_ids = set(presence.member_ids(connection, room_id))
            characters = [item for item in characters if item["id"] in present_ids]
            messages = [dict(row) for row in connection.execute(
                """SELECT m.*, c.name AS character_name, c.accent AS character_accent
                   FROM room_messages m LEFT JOIN characters c ON c.id=m.character_id
                   WHERE m.room_id=? ORDER BY m.sequence""", (room_id,)
            )]
            records = [dict(row) for row in connection.execute(
                """SELECT e.*, s.room_message_id AS source_room_message_id,
                          l.name AS location_name
                   FROM room_event_sources s JOIN world_events e ON e.id=s.event_id
                   LEFT JOIN locations l ON l.id=e.location_id
                   WHERE s.room_id=? ORDER BY e.created_at""",
                (room_id,),
            )]
            settlements = [self._settlement(row) for row in connection.execute(
                """SELECT * FROM turn_settlements
                   WHERE room_id=? ORDER BY created_at, id""",
                (room_id,),
            )]
            settlement_by_message = {
                item["source_room_message_id"]: item for item in settlements
            }
            jobs = {row["source_id"]: dict(row) for row in connection.execute(
                "SELECT source_id,status,reason FROM settlement_jobs WHERE world_id=? AND status!='complete'",
                (room["world_id"],),
            )}
            for message in messages:
                if message["id"] in jobs:
                    message["settlement_job"] = jobs[message["id"]]
                settlement = settlement_by_message.get(message["id"])
                if settlement:
                    message["settlement"] = settlement
            player_row = connection.execute(
                """SELECT f.*,l.name AS player_location_name
                   FROM world_flow_states f LEFT JOIN locations l ON l.id=f.player_location_id
                   WHERE f.world_id=?""",
                (room["world_id"],),
            ).fetchone()
            commitments = connection.execute(
                """SELECT pc.id,pc.character_id,pc.text,c.name AS character_name
                   FROM player_commitments pc LEFT JOIN characters c ON c.id=pc.character_id
                   WHERE pc.world_id=? ORDER BY pc.created_at""",
                (room["world_id"],),
            ).fetchall()
        room_data = dict(room)
        room_data["active_threads"] = _loads(room_data.get("active_threads"), [])
        scene = {
            "location_id": room_data.get("location_id"),
            "location_name": (location or {}).get("name"),
            "world_time": world.get("current_time") or "",
            "tension": room_data.get("tension", 0),
            "active_threads": room_data.get("active_threads", []),
            "physical_state": room_data.get("physical_state", ""),
            "emotional_state": room_data.get("emotional_state", ""),
        }
        return {
            "room": room_data, "world": world, "location": location,
            "scene": scene, "characters": characters, "messages": messages,
            "records": records, "settlements": settlements,
            "pending_settlements": self.pending_settlements(room["world_id"]),
            "player": self._player_state(player_row, list(commitments)),
        }

    def add_room_message(
        self, room_id: str, role: str, character_id: str | None, content: str,
        *, settlement_context: dict[str, Any] | None = None,
        observations: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        created = now()
        with self.transaction() as connection:
            if not connection.execute("SELECT 1 FROM scene_rooms WHERE id=?", (room_id,)).fetchone():
                raise KeyError("room_not_found")
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM room_messages WHERE room_id=?",
                (room_id,),
            ).fetchone()[0]
            message_id = uid("rmsg")
            connection.execute(
                "INSERT INTO room_messages VALUES (?,?,?,?,?,?,?,?)",
                (message_id, room_id, role, character_id, content, sequence, created, created),
            )
            world_id = connection.execute("SELECT world_id FROM scene_rooms WHERE id=?", (room_id,)).fetchone()[0]
            information.add_message(connection, world_id, message_id, presence.member_ids(connection, room_id), observations)
            if settlement_context is not None:
                if role != "assistant" or not content.strip():
                    raise ValueError("invalid_settlement_message")
                self._enqueue_settlement(connection, message_id, settlement_context)
            connection.execute(
                "UPDATE scene_rooms SET updated_at=? WHERE id=?", (created, room_id)
            )
            row = connection.execute(
                """SELECT m.*, c.name AS character_name, c.accent AS character_accent
                   FROM room_messages m LEFT JOIN characters c ON c.id=m.character_id
                   WHERE m.id=?""", (message_id,)
            ).fetchone()
        return dict(row)

    def room_character_context(self, room_id: str, character_id: str) -> dict[str, Any]:
        """同场回合中，单个角色的独立上下文（只含他有权知道的内容）。"""
        with self.read() as connection:
            room = connection.execute("SELECT * FROM scene_rooms WHERE id=?", (room_id,)).fetchone()
            if not room:
                raise KeyError("room_not_found")
            present_ids = set(presence.member_ids(connection, room_id))
            if character_id not in present_ids:
                raise ValueError("角色已经离开这个现场")
            world = dict(connection.execute(
                "SELECT * FROM worlds WHERE id=?", (room["world_id"],)
            ).fetchone())
            character_row = connection.execute(
                """SELECT c.*, l.name AS location_name, l.description AS location_description
                   FROM room_characters rc JOIN characters c ON c.id=rc.character_id
                   LEFT JOIN locations l ON l.id=c.current_location_id
                   WHERE rc.room_id=? AND c.id=?""",
                (room_id, character_id),
            ).fetchone()
            if not character_row:
                raise KeyError("character_not_found")
            character = self._character(character_row)
            room_location = dict(connection.execute(
                "SELECT * FROM locations WHERE id=?", (room["location_id"],)
            ).fetchone()) if room["location_id"] else None
            transcript = [dict(row) for row in connection.execute(
                """SELECT * FROM (
                       SELECT m.*, c.name AS character_name, json_extract(t.decision, '$.outcome') AS settlement_outcome
                       FROM room_messages m LEFT JOIN characters c ON c.id=m.character_id
                       LEFT JOIN turn_settlements t ON t.source_room_message_id=m.id
                       WHERE m.room_id=? ORDER BY m.sequence DESC LIMIT 60
                   ) recent ORDER BY sequence""",
                (room_id,),
            )]
            turn_user_id = next((m['id'] for m in reversed(transcript) if m['role']=='user'), None)
            transcript = information.project_messages(connection, transcript, character_id)
            view_row = connection.execute('SELECT views FROM room_message_visibility WHERE message_id=?', (turn_user_id,)).fetchone()
            user_views = _loads(view_row['views'], {}) if view_row else {}
            own_scene = information.personal_state(connection, room_id, character_id)
            public_events = [dict(row) for row in connection.execute(
                """SELECT * FROM (
                       SELECT e.*, k.state AS knowledge_state, k.belief, k.source,
                              l.name AS location_name FROM character_knowledge k
                       JOIN world_events e ON e.id=k.event_id
                       LEFT JOIN locations l ON l.id=e.location_id
                       WHERE k.character_id=? AND e.world_id=? AND e.visibility='public'
                       ORDER BY e.created_at DESC LIMIT 80
                   ) recent ORDER BY created_at""", (character_id, world["id"])
            )]
            knowledge = [dict(row) for row in connection.execute(
                """SELECT * FROM (
                       SELECT e.*, k.state AS knowledge_state, k.belief, k.confidence,
                              k.source, l.name AS location_name
                       FROM character_knowledge k JOIN world_events e ON e.id=k.event_id
                       LEFT JOIN locations l ON l.id=e.location_id
                       WHERE k.character_id=? AND e.visibility!='public'
                       ORDER BY e.created_at DESC LIMIT 80
                   ) recent ORDER BY created_at""", (character_id,)
            )]
            public_events = information.restrict_room_knowledge(connection, public_events, character['id'])
            knowledge = information.restrict_room_knowledge(connection, knowledge, character['id'])
            relationships = [dict(row) for row in connection.execute(
                "SELECT * FROM relationships WHERE from_character_id=?", (character_id,)
            )]
            locations = [dict(row) for row in connection.execute(
                "SELECT * FROM locations WHERE world_id=? ORDER BY sort_order", (world["id"],)
            )]
            locations = known_locations(locations, character['id'])
            character = project_location_fields(character, locations, id_key='current_location_id')
            room_characters = [self._character(row) for row in connection.execute(
                """SELECT c.* FROM room_characters rc
                   JOIN characters c ON c.id=rc.character_id
                   WHERE rc.room_id=? ORDER BY rc.sort_order""",
                (room_id,),
            )]
            room_characters = [item for item in room_characters if item["id"] in present_ids]
            player_row = connection.execute(
                """SELECT f.*,l.name AS player_location_name
                   FROM world_flow_states f LEFT JOIN locations l ON l.id=f.player_location_id
                   WHERE f.world_id=?""",
                (world["id"],),
            ).fetchone()
            commitments = connection.execute(
                """SELECT pc.id,pc.character_id,pc.text,c.name AS character_name
                   FROM player_commitments pc LEFT JOIN characters c ON c.id=pc.character_id
                   WHERE pc.world_id=? AND (pc.character_id=? OR pc.character_id IS NULL)
                   ORDER BY pc.created_at""",
                (world["id"], character_id),
            ).fetchall()
            world_state = connection.execute(
                "SELECT * FROM character_world_states WHERE character_id=?",
                (character_id,),
            ).fetchone()
            world_names = [row["name"] for row in connection.execute("SELECT name FROM characters WHERE world_id=?", (world["id"],))]
        scene = {
            "location_id": (room_location or {}).get("id"),
            "location_name": (room_location or {}).get("name") or character.get("location_name"),
            "world_time": world.get("current_time") or "",
            "tension": own_scene.get("tension", 0),
            "active_threads": own_scene.get("active_threads", []),
            "physical_state": own_scene.get("physical_state", ""),
            "emotional_state": own_scene.get("emotional_state", ""),
        }
        scene = project_location_fields(scene, locations)
        return {
            "room": {k:room[k] for k in ('id','name','world_id','location_id')},
            "turn_user_id": turn_user_id,
            "observation_sources": {cid:{'user':text} for cid,text in user_views.items()}, "world": world, "character": character,
            "player_profile": self.get_player_profile(world["id"]),
            "scene": scene, "messages": transcript, "public_events": [knowledge_projection(item) for item in public_events],
            "knowledge": [knowledge_projection(item) for item in knowledge], "relationships": relationships,
            "locations": locations, "characters": [public_identity(item) for item in room_characters],
            "room_characters": [public_identity(item) for item in room_characters],
            "player": player_observation(project_location_fields(self._player_state(player_row, list(commitments)), locations), world_names),
            "character_world_state": dict(world_state) if world_state else {},
        }

    def apply_room_patch(
        self, room_id: str, character_id: str, source_message_id: str,
        patch: dict[str, Any],
        *, context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = context or self.room_character_context(room_id, character_id)
        created = now()
        with self.transaction() as connection:
            room = connection.execute("SELECT * FROM scene_rooms WHERE id=?", (room_id,)).fetchone()
            if not room:
                raise KeyError("room_not_found")
            world_id = room["world_id"]
            character = connection.execute(
                "SELECT id,name FROM characters WHERE id=? AND world_id=?",
                (character_id, world_id),
            ).fetchone()
            if not character:
                raise KeyError("character_not_found")
            source_message = connection.execute(
                """SELECT role,character_id,content FROM room_messages
                   WHERE id=? AND room_id=?""",
                (source_message_id, room_id),
            ).fetchone()
            if (
                not source_message
                or source_message["role"] != "assistant"
                or source_message["character_id"] != character_id
            ):
                raise ValueError("invalid_patch_source")

            existing = self._existing_patch_result(connection, source_message_id)
            if existing:
                return existing
            context = information.recovery_context(connection,context,source_message_id)
            patch = validate_patch(patch, context, source_message["content"])
            commit_mentions(connection, context, source_message_id)
            commit_plan(connection, context, source_message_id, source_message["content"])
            scene_patch = patch.get("scene") or {}
            world_time = _sanitize_world_time(scene_patch.get("world_time"))
            location_id = room["location_id"]
            requested_location_id = scene_patch.get("location_id")
            if requested_location_id:
                found = connection.execute(
                    "SELECT id FROM locations WHERE id=? AND world_id=?",
                    (requested_location_id, world_id),
                ).fetchone()
                if found:
                    location_id = found["id"]
            elif scene_patch.get("location"):
                found = connection.execute(
                    "SELECT id FROM locations WHERE world_id=? AND name=?",
                    (world_id, scene_patch["location"]),
                ).fetchone()
                if found:
                    location_id = found["id"]
            effective_world_time = world_time or context['world']['current_time']
            departed = location_id != room["location_id"]
            information.save_personal_state(connection, world_id, room_id, character_id, scene_patch)
            room_world_time = world_time or connection.execute(
                "SELECT worlds.current_time FROM worlds WHERE id=?", (world_id,),
            ).fetchone()["current_time"]
            connection.execute("UPDATE scene_rooms SET world_time=?,updated_at=? WHERE id=?", (room_world_time,created,room_id))
            connection.execute("UPDATE characters SET current_location_id=?,updated_at=? WHERE id=?", (location_id, created, character_id))
            if departed:
                presence.enter_private(connection, world_id, character_id, created)
            if world_time:
                connection.execute(
                    "UPDATE worlds SET current_time=?,updated_at=? WHERE id=?",
                    (world_time, created, world_id),
                )

            event = patch.get("event")
            event_id = None
            record = None
            if isinstance(event, dict) and event.get("title"):
                event_id = uid("event")
                visibility = event.get("visibility") if event.get("visibility") in {"public", "private", "secret"} else "private"
                event_world_time = (
                    _sanitize_world_time(event.get("world_time"))
                    or effective_world_time
                )
                connection.execute(
                    """INSERT INTO world_events(
                           id,world_id,source_conversation_id,source_message_id,title,
                           details,visibility,location_id,world_time,cognition_synced,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        event_id, world_id, None, None,
                        str(event["title"])[:80], str(event.get("details", "")), visibility,
                        location_id, event_world_time, 0, created,
                    ),
                )
                connection.execute(
                    "INSERT INTO room_event_sources VALUES (?,?,?)",
                    (event_id, room_id, source_message_id),
                )
                character_by_name = {
                    item["name"]: item["id"] for item in context["room_characters"]
                }
                participant_ids = {character_id}
                for name in event.get("participants", []):
                    if name in character_by_name:
                        participant_ids.add(character_by_name[name])
                for participant_id in participant_ids:
                    connection.execute(
                        "INSERT OR IGNORE INTO event_participants VALUES (?,?,?)",
                        (event_id, participant_id, "participant"),
                    )
                witness_names = set(event.get("witnesses", []))
                witness_names.add(character["name"])
                beliefs = event.get("beliefs") if isinstance(event.get("beliefs"), dict) else {}
                for name in witness_names:
                    knowledge_character_id = character_by_name.get(name)
                    if not knowledge_character_id:
                        continue
                    belief = str(beliefs.get(name, ""))
                    source = event["knowledge_sources"][name]
                    knowledge_state = "misunderstood" if belief else ("heard" if source["kind"] == "told" else "witnessed")
                    connection.execute(
                        """INSERT OR REPLACE INTO character_knowledge
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (
                            uid("know"), event_id, knowledge_character_id, knowledge_state,
                            belief, 0.7 if belief else 1.0, _json(source), created,
                        ),
                    )
                record = {
                    "id": event_id, "title": str(event["title"])[:80],
                    "details": str(event.get("details", "")), "visibility": visibility,
                    "world_time": event_world_time, "cognition_synced": 0,
                    "source_room_message_id": source_message_id,
                }

            relationship = patch.get("relationship") or {}
            if relationship:
                row = connection.execute(
                    "SELECT * FROM relationships WHERE from_character_id=? AND to_user=1",
                    (character_id,),
                ).fetchone()
                if row:
                    connection.execute(
                        """UPDATE relationships SET trust=?,desire=?,tension=?,label=?,notes=?,updated_at=?
                           WHERE id=?""",
                        (
                            max(-100, min(100, row["trust"] + int(relationship.get("trust_delta", 0)))),
                            max(-100, min(100, row["desire"] + int(relationship.get("desire_delta", 0)))),
                            max(0, min(100, row["tension"] + int(relationship.get("tension_delta", 0)))),
                            relationship.get("label") or row["label"],
                            relationship.get("note") or row["notes"], created, row["id"],
                        ),
                    )
            self._apply_player_patch(
                connection,
                world_id=world_id,
                character_id=character_id,
                scene_location_id=None,
                patch=patch,
                updated_at=created,
            )
            settlement = self._insert_turn_settlement(
                connection,
                world_id=world_id,
                room_id=room_id,
                source_room_message_id=source_message_id,
                event_id=event_id,
                patch=patch,
                created_at=created,
            )
            connection.execute(
                "UPDATE worlds SET updated_at=? WHERE id=?", (created, world_id)
            )
        return {"event_id": event_id, "record": record, "settlement": settlement}
