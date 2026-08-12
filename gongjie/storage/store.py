from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4


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


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS worlds (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    premise TEXT NOT NULL DEFAULT '',
    atmosphere TEXT NOT NULL DEFAULT '',
    rules TEXT NOT NULL DEFAULT '',
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
    persona TEXT NOT NULL DEFAULT '',
    speech_style TEXT NOT NULL DEFAULT '',
    desire_profile TEXT NOT NULL DEFAULT '{}',
    signature_behaviors TEXT NOT NULL DEFAULT '[]',
    loss_of_control TEXT NOT NULL DEFAULT '',
    current_location_id TEXT REFERENCES locations(id) ON DELETE SET NULL,
    accent TEXT NOT NULL DEFAULT '#bc5d45',
    sort_order INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    active_branch_id TEXT,
    canonical_branch_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(world_id, character_id)
);

CREATE TABLE IF NOT EXISTS branches (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    parent_branch_id TEXT REFERENCES branches(id) ON DELETE SET NULL,
    forked_from_message_id TEXT,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
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

CREATE INDEX IF NOT EXISTS idx_messages_branch_sequence ON messages(branch_id, sequence);

CREATE TABLE IF NOT EXISTS scene_states (
    branch_id TEXT PRIMARY KEY REFERENCES branches(id) ON DELETE CASCADE,
    location_id TEXT REFERENCES locations(id) ON DELETE SET NULL,
    world_time TEXT NOT NULL DEFAULT '',
    tension INTEGER NOT NULL DEFAULT 0,
    active_threads TEXT NOT NULL DEFAULT '[]',
    physical_state TEXT NOT NULL DEFAULT '',
    emotional_state TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scene_snapshots (
    message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    location_id TEXT REFERENCES locations(id) ON DELETE SET NULL,
    world_time TEXT NOT NULL DEFAULT '',
    tension INTEGER NOT NULL DEFAULT 0,
    active_threads TEXT NOT NULL DEFAULT '[]',
    physical_state TEXT NOT NULL DEFAULT '',
    emotional_state TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS world_events (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    source_branch_id TEXT REFERENCES branches(id) ON DELETE SET NULL,
    source_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    visibility TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('public','private','secret')),
    location_id TEXT REFERENCES locations(id) ON DELETE SET NULL,
    world_time TEXT NOT NULL DEFAULT '',
    is_canon INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_world_canon ON world_events(world_id, is_canon, created_at);

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
"""


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

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
        with self._connect() as connection:
            connection.executescript(SCHEMA)
        with self.read() as connection:
            count = connection.execute("SELECT COUNT(*) FROM worlds").fetchone()[0]
        if count == 0:
            self._seed()

    def _seed(self) -> None:
        created = now()
        world_id = "world_velvet_harbor"
        locations = [
            ("loc_house", "夜航公馆", "临海旧宅，所有房间都记得曾经发生过的事。", "潮湿、安静、灯影低垂"),
            ("loc_club", "鸦色会所", "只对熟客开放的地下会所，消息与欲望在这里等价交换。", "喧闹表面下的危险亲密"),
            ("loc_pier", "十三号码头", "雾气终年不散，离开与归来都要从这里经过。", "海盐、锈迹、未说出口的决定"),
        ]
        characters = [
            {
                "id": "char_shenyan", "name": "沈砚", "age": 34, "pronouns": "他",
                "role": "夜航公馆的主人", "tagline": "克制得越久，失控时越诚实。",
                "persona": "沉静、精确、掌控欲强。很少解释自己，但会记住对方每一次细微退缩和靠近。不会无条件顺从，也不会轻易暴露嫉妒。",
                "speech": "短句，低声，极少使用夸张语气；真正动摇时反而会叫对方的全名。",
                "desire": {"control": 86, "devotion": 72, "jealousy": 68, "risk": 35, "tenderness": 63},
                "behaviors": ["替对方收好散乱的东西", "在关键问题上沉默逼近", "用具体行动代替承诺"],
                "loss": "不再给退路，但仍会观察对方每一个真实反应。", "location": "loc_house", "accent": "#b65e4b", "order": 0,
            },
            {
                "id": "char_cenye", "name": "岑野", "age": 29, "pronouns": "他",
                "role": "会所的情报掮客", "tagline": "把所有真话说得像玩笑。",
                "persona": "敏锐、顽劣、喜欢试探边界。对秘密有职业性的耐心，对真正喜欢的人却缺乏耐心。享受三人关系里的不稳定平衡。",
                "speech": "松弛、带笑、会故意曲解措辞；认真时会突然收掉所有玩笑。",
                "desire": {"control": 42, "devotion": 54, "jealousy": 78, "risk": 91, "tenderness": 38},
                "behaviors": ["借玩笑逼出真心", "在别人看不到时留下暗示", "主动制造必须选择的局面"],
                "loss": "笑意消失，直接承认占有欲，并把选择推到眼前。", "location": "loc_club", "accent": "#8f6ac7", "order": 1,
            },
            {
                "id": "char_linzhao", "name": "林照", "age": 31, "pronouns": "她",
                "role": "十三号码头的医生", "tagline": "温柔不是退让，是她最锋利的控制。",
                "persona": "冷静、体贴、观察力近乎残酷。习惯照顾所有人，却不允许别人轻易看见自己的需要。知道沈砚与岑野的大部分旧事，但只说必要的部分。",
                "speech": "温和而明确，擅长用一个问题改变谈话方向；愤怒时礼貌得近乎疏离。",
                "desire": {"control": 67, "devotion": 84, "jealousy": 47, "risk": 52, "tenderness": 88},
                "behaviors": ["先确认身体反应再追问情绪", "替人保守秘密但记得代价", "用照顾建立无法忽视的存在感"],
                "loss": "停止替所有人留体面，坦白自己的需要并要求明确回应。", "location": "loc_pier", "accent": "#4d9085", "order": 2,
            },
        ]
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO worlds VALUES (?,?,?,?,?,?,?,?)",
                (world_id, "绯港", "一座把秘密留得比人更久的海港。三名彼此牵连的成年人，各自与你拥有独立关系，却生活在同一条会被改写的时间线上。", "潮湿、华丽、危险、亲密", "所有角色均为成年人；角色拥有自主意志；发生过的事会改变关系、知识与之后的选择；意图不是事实。", "雾季 · 第七码头钟响前", created, created),
            )
            for order, (location_id, name, description, mood) in enumerate(locations):
                connection.execute(
                    "INSERT INTO locations VALUES (?,?,?,?,?,?)",
                    (location_id, world_id, name, description, mood, order),
                )
            for item in characters:
                connection.execute(
                    "INSERT INTO characters VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (item["id"], world_id, item["name"], item["age"], item["pronouns"], item["role"], item["tagline"], item["persona"], item["speech"], _json(item["desire"]), _json(item["behaviors"]), item["loss"], item["location"], item["accent"], item["order"], created, created),
                )
                relationship_id = uid("rel")
                connection.execute(
                    "INSERT INTO relationships VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (relationship_id, world_id, item["id"], None, 1, "未命名的牵引", 10, 18, 12, "关系会由共同经历塑形。", created),
                )
            connection.execute(
                "INSERT INTO provider_profiles VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("provider_default", "我的模型", "openai-compatible", "https://api.openai.com", "/v1/chat/completions", "", 16384, 0.95, 1, created),
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
            return {"location_id": None, "world_time": "", "tension": 0, "active_threads": [], "physical_state": "", "emotional_state": ""}
        result = dict(row)
        result["active_threads"] = _loads(result.get("active_threads"), [])
        return result

    def list_worlds(self) -> list[dict[str, Any]]:
        with self.read() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM worlds ORDER BY created_at")]

    def create_world(self, values: dict[str, Any]) -> dict[str, Any]:
        world_id = uid("world")
        created = now()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO worlds VALUES (?,?,?,?,?,?,?,?)",
                (
                    world_id, values["name"].strip(), values.get("premise", "").strip(),
                    values.get("atmosphere", "").strip(), values.get("rules", "").strip(),
                    values.get("current_time", "").strip(), created, created,
                ),
            )
        return self.get_world(world_id)

    def update_world(self, world_id: str, values: dict[str, Any]) -> dict[str, Any]:
        allowed = ("name", "premise", "atmosphere", "rules", "current_time")
        updates = {key: str(values[key]).strip() for key in allowed if key in values and values[key] is not None}
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
        return self.get_world(world_id)

    def create_location(self, world_id: str, values: dict[str, Any]) -> dict[str, Any]:
        location_id = uid("loc")
        with self.transaction() as connection:
            world = connection.execute("SELECT 1 FROM worlds WHERE id=?", (world_id,)).fetchone()
            if not world:
                raise KeyError("world_not_found")
            order = connection.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM locations WHERE world_id=?", (world_id,)).fetchone()[0]
            connection.execute(
                "INSERT INTO locations VALUES (?,?,?,?,?,?)",
                (location_id, world_id, values["name"].strip(), values.get("description", "").strip(), values.get("mood", "").strip(), order),
            )
            row = connection.execute("SELECT * FROM locations WHERE id=?", (location_id,)).fetchone()
        return dict(row)

    def create_character(self, world_id: str, values: dict[str, Any]) -> dict[str, Any]:
        character_id = uid("char")
        created = now()
        with self.transaction() as connection:
            world = connection.execute("SELECT 1 FROM worlds WHERE id=?", (world_id,)).fetchone()
            if not world:
                raise KeyError("world_not_found")
            order = connection.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM characters WHERE world_id=?", (world_id,)).fetchone()[0]
            location_id = values.get("current_location_id")
            if location_id and not connection.execute("SELECT 1 FROM locations WHERE id=? AND world_id=?", (location_id, world_id)).fetchone():
                raise ValueError("invalid_location")
            connection.execute(
                "INSERT INTO characters VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    character_id, world_id, values["name"].strip(), int(values["age"]), values.get("pronouns", "").strip(),
                    values.get("role", "").strip(), values.get("tagline", "").strip(), values.get("persona", "").strip(),
                    values.get("speech_style", "").strip(), _json(values.get("desire_profile", {})),
                    _json(values.get("signature_behaviors", [])), values.get("loss_of_control", "").strip(), location_id,
                    values.get("accent", "#bc5d45"), order, created, created,
                ),
            )
            connection.execute(
                "INSERT INTO relationships VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (uid("rel"), world_id, character_id, None, 1, "未命名的牵引", 0, 0, 0, "", created),
            )
        return self.get_character(character_id)

    def update_character(self, character_id: str, values: dict[str, Any]) -> dict[str, Any]:
        text_fields = ("name", "pronouns", "role", "tagline", "persona", "speech_style", "loss_of_control", "current_location_id", "accent")
        updates: dict[str, Any] = {key: (str(values[key]).strip() if values[key] is not None else None) for key in text_fields if key in values}
        if "age" in values:
            updates["age"] = int(values["age"])
        if "desire_profile" in values:
            updates["desire_profile"] = _json(values["desire_profile"])
        if "signature_behaviors" in values:
            updates["signature_behaviors"] = _json(values["signature_behaviors"])
        if not updates:
            return self.get_character(character_id)
        with self.transaction() as connection:
            current = connection.execute("SELECT world_id FROM characters WHERE id=?", (character_id,)).fetchone()
            if not current:
                raise KeyError("character_not_found")
            if updates.get("current_location_id") and not connection.execute("SELECT 1 FROM locations WHERE id=? AND world_id=?", (updates["current_location_id"], current["world_id"])).fetchone():
                raise ValueError("invalid_location")
            assignments = ",".join(f"{key}=?" for key in updates)
            connection.execute(f"UPDATE characters SET {assignments}, updated_at=? WHERE id=?", [*updates.values(), now(), character_id])
        return self.get_character(character_id)

    def get_world(self, world_id: str) -> dict[str, Any]:
        with self.read() as connection:
            world = connection.execute("SELECT * FROM worlds WHERE id=?", (world_id,)).fetchone()
            if not world:
                raise KeyError("world_not_found")
            locations = [dict(row) for row in connection.execute("SELECT * FROM locations WHERE world_id=? ORDER BY sort_order, name", (world_id,))]
            characters = [self._character(row) for row in connection.execute("SELECT * FROM characters WHERE world_id=? ORDER BY sort_order, name", (world_id,))]
            events = [dict(row) for row in connection.execute(
                """SELECT e.*, l.name AS location_name
                   FROM world_events e LEFT JOIN locations l ON l.id=e.location_id
                   WHERE e.world_id=? AND e.is_canon=1 ORDER BY e.created_at DESC LIMIT 80""", (world_id,))]
            relationships = [dict(row) for row in connection.execute("SELECT * FROM relationships WHERE world_id=?", (world_id,))]
        return {"world": dict(world), "locations": locations, "characters": characters, "events": events, "relationships": relationships}

    def get_character(self, character_id: str) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute(
                """SELECT c.*, l.name AS location_name, l.description AS location_description
                   FROM characters c LEFT JOIN locations l ON l.id=c.current_location_id WHERE c.id=?""", (character_id,)).fetchone()
        if not row:
            raise KeyError("character_not_found")
        return self._character(row)

    def get_or_create_conversation(self, world_id: str, character_id: str) -> str:
        with self.transaction() as connection:
            existing = connection.execute("SELECT id FROM conversations WHERE world_id=? AND character_id=?", (world_id, character_id)).fetchone()
            if existing:
                return existing["id"]
            character = connection.execute("SELECT name, current_location_id FROM characters WHERE id=? AND world_id=?", (character_id, world_id)).fetchone()
            if not character:
                raise KeyError("character_not_found")
            conversation_id = uid("conv")
            branch_id = uid("branch")
            created = now()
            connection.execute(
                "INSERT INTO conversations VALUES (?,?,?,?,?,?,?,?)",
                (conversation_id, world_id, character_id, f"与{character['name']}的对话", branch_id, branch_id, created, created),
            )
            connection.execute(
                "INSERT INTO branches VALUES (?,?,?,?,?,?)",
                (branch_id, conversation_id, None, None, "正史", created),
            )
            connection.execute(
                "INSERT INTO scene_states VALUES (?,?,?,?,?,?,?,?)",
                (branch_id, character["current_location_id"], "", 0, "[]", "", "", created),
            )
            return conversation_id

    def _visible_messages(self, connection: sqlite3.Connection, branch_id: str) -> list[dict[str, Any]]:
        branch = connection.execute("SELECT * FROM branches WHERE id=?", (branch_id,)).fetchone()
        if not branch:
            raise KeyError("branch_not_found")
        messages: list[dict[str, Any]] = []
        if branch["parent_branch_id"]:
            messages = self._visible_messages(connection, branch["parent_branch_id"])
            fork_id = branch["forked_from_message_id"]
            if fork_id:
                cut = next((index for index, item in enumerate(messages) if item["id"] == fork_id), None)
                if cut is None:
                    raise RuntimeError("branch_fork_message_missing")
                messages = messages[: cut + 1]
        local = connection.execute(
            "SELECT * FROM messages WHERE branch_id=? ORDER BY sequence", (branch_id,)
        ).fetchall()
        messages.extend(dict(row) for row in local)
        return messages

    def get_conversation(self, conversation_id: str, branch_id: str | None = None) -> dict[str, Any]:
        with self.read() as connection:
            conversation = connection.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if not conversation:
                raise KeyError("conversation_not_found")
            selected_branch = branch_id or conversation["active_branch_id"]
            branches = [dict(row) for row in connection.execute("SELECT * FROM branches WHERE conversation_id=? ORDER BY created_at", (conversation_id,))]
            messages = self._visible_messages(connection, selected_branch)
            scene = self._scene(connection.execute("SELECT * FROM scene_states WHERE branch_id=?", (selected_branch,)).fetchone())
            character = self._character(connection.execute(
                """SELECT c.*, l.name AS location_name, l.description AS location_description
                   FROM characters c LEFT JOIN locations l ON l.id=c.current_location_id WHERE c.id=?""",
                (conversation["character_id"],),
            ).fetchone())
            knowledge = [dict(row) for row in connection.execute(
                """SELECT k.*, e.title, e.details, e.world_time, e.visibility
                   FROM character_knowledge k JOIN world_events e ON e.id=k.event_id
                   WHERE k.character_id=? AND e.is_canon=1 ORDER BY e.created_at DESC LIMIT 60""", (character["id"],))]
        return {
            "conversation": dict(conversation), "branch_id": selected_branch, "branches": branches,
            "messages": messages, "scene": scene, "character": character, "knowledge": knowledge,
        }

    def add_message(self, conversation_id: str, branch_id: str, role: str, content: str, status: str = "complete") -> dict[str, Any]:
        created = now()
        with self.transaction() as connection:
            valid = connection.execute("SELECT 1 FROM branches WHERE id=? AND conversation_id=?", (branch_id, conversation_id)).fetchone()
            if not valid:
                raise KeyError("branch_not_found")
            sequence = connection.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM messages WHERE branch_id=?", (branch_id,)).fetchone()[0]
            message_id = uid("msg")
            connection.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (message_id, conversation_id, branch_id, role, content, "", status, None, None, None, sequence, created, created),
            )
            connection.execute("UPDATE conversations SET active_branch_id=?, updated_at=? WHERE id=?", (branch_id, created, conversation_id))
            row = connection.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        return dict(row)

    def update_message(self, message_id: str, *, content: str, reasoning: str = "", status: str = "complete", finish_reason: str | None = None, input_tokens: int | None = None, output_tokens: int | None = None) -> None:
        with self.transaction() as connection:
            connection.execute(
                """UPDATE messages SET content=?, reasoning=?, status=?, finish_reason=?, input_tokens=?, output_tokens=?, updated_at=? WHERE id=?""",
                (content, reasoning, status, finish_reason, input_tokens, output_tokens, now(), message_id),
            )

    def fork_branch(self, conversation_id: str, parent_branch_id: str, from_message_id: str, title: str | None = None) -> str:
        created = now()
        with self.transaction() as connection:
            visible = self._visible_messages(connection, parent_branch_id)
            if not any(item["id"] == from_message_id for item in visible):
                raise KeyError("fork_message_not_visible")
            branch_id = uid("branch")
            count = connection.execute("SELECT COUNT(*) FROM branches WHERE conversation_id=?", (conversation_id,)).fetchone()[0]
            connection.execute(
                "INSERT INTO branches VALUES (?,?,?,?,?,?)",
                (branch_id, conversation_id, parent_branch_id, from_message_id, title or f"支线 {count}", created),
            )
            parent_scene = None
            visible_ids = [item["id"] for item in visible[: next(index for index, item in enumerate(visible) if item["id"] == from_message_id) + 1]]
            for message_id in reversed(visible_ids):
                parent_scene = connection.execute("SELECT * FROM scene_snapshots WHERE message_id=?", (message_id,)).fetchone()
                if parent_scene:
                    break
            if not parent_scene:
                parent_scene = connection.execute(
                    """SELECT c.current_location_id AS location_id, '' AS world_time, 0 AS tension,
                              '[]' AS active_threads, '' AS physical_state, '' AS emotional_state
                       FROM conversations v JOIN characters c ON c.id=v.character_id WHERE v.id=?""",
                    (conversation_id,),
                ).fetchone()
            if parent_scene:
                connection.execute(
                    "INSERT INTO scene_states VALUES (?,?,?,?,?,?,?,?)",
                    (branch_id, parent_scene["location_id"], parent_scene["world_time"], parent_scene["tension"], parent_scene["active_threads"], parent_scene["physical_state"], parent_scene["emotional_state"], created),
                )
            else:
                connection.execute("INSERT INTO scene_states(branch_id,updated_at) VALUES (?,?)", (branch_id, created))
            connection.execute("UPDATE conversations SET active_branch_id=?, updated_at=? WHERE id=?", (branch_id, created, conversation_id))
        return branch_id

    def activate_branch(self, conversation_id: str, branch_id: str) -> None:
        with self.transaction() as connection:
            result = connection.execute(
                "UPDATE conversations SET active_branch_id=?, updated_at=? WHERE id=? AND EXISTS (SELECT 1 FROM branches WHERE id=? AND conversation_id=?)",
                (branch_id, now(), conversation_id, branch_id, conversation_id),
            )
            if not result.rowcount:
                raise KeyError("branch_not_found")

    def _branch_ancestry(self, connection: sqlite3.Connection, branch_id: str) -> set[str]:
        ancestry: set[str] = set()
        current: str | None = branch_id
        while current:
            ancestry.add(current)
            row = connection.execute("SELECT parent_branch_id FROM branches WHERE id=?", (current,)).fetchone()
            current = row["parent_branch_id"] if row else None
        return ancestry

    def promote_branch(self, conversation_id: str, branch_id: str) -> None:
        with self.transaction() as connection:
            conversation = connection.execute("SELECT world_id FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            valid = connection.execute("SELECT 1 FROM branches WHERE id=? AND conversation_id=?", (branch_id, conversation_id)).fetchone()
            if not conversation or not valid:
                raise KeyError("branch_not_found")
            all_branch_ids = [row[0] for row in connection.execute("SELECT id FROM branches WHERE conversation_id=?", (conversation_id,))]
            visible_message_ids = [item["id"] for item in self._visible_messages(connection, branch_id)]
            if all_branch_ids:
                placeholders = ",".join("?" for _ in all_branch_ids)
                connection.execute(f"UPDATE world_events SET is_canon=0 WHERE source_branch_id IN ({placeholders})", all_branch_ids)
            if visible_message_ids:
                placeholders = ",".join("?" for _ in visible_message_ids)
                connection.execute(f"UPDATE world_events SET is_canon=1 WHERE source_message_id IN ({placeholders})", visible_message_ids)
            connection.execute(
                "UPDATE conversations SET canonical_branch_id=?, active_branch_id=?, updated_at=? WHERE id=?",
                (branch_id, branch_id, now(), conversation_id),
            )
            scene = connection.execute("SELECT * FROM scene_states WHERE branch_id=?", (branch_id,)).fetchone()
            if scene:
                character_id = connection.execute("SELECT character_id FROM conversations WHERE id=?", (conversation_id,)).fetchone()[0]
                if scene["location_id"]:
                    connection.execute("UPDATE characters SET current_location_id=?, updated_at=? WHERE id=?", (scene["location_id"], now(), character_id))
                if scene["world_time"]:
                    connection.execute("UPDATE worlds SET current_time=?, updated_at=? WHERE id=?", (scene["world_time"], now(), conversation["world_id"]))

    def context_for_turn(self, conversation_id: str, branch_id: str) -> dict[str, Any]:
        with self.read() as connection:
            conversation = connection.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if not conversation:
                raise KeyError("conversation_not_found")
            world = dict(connection.execute("SELECT * FROM worlds WHERE id=?", (conversation["world_id"],)).fetchone())
            character = self._character(connection.execute(
                """SELECT c.*, l.name AS location_name, l.description AS location_description
                   FROM characters c LEFT JOIN locations l ON l.id=c.current_location_id WHERE c.id=?""",
                (conversation["character_id"],),
            ).fetchone())
            scene = self._scene(connection.execute("SELECT * FROM scene_states WHERE branch_id=?", (branch_id,)).fetchone())
            messages = self._visible_messages(connection, branch_id)
            public_events = [dict(row) for row in connection.execute(
                """SELECT e.*, l.name AS location_name FROM world_events e LEFT JOIN locations l ON l.id=e.location_id
                   WHERE e.world_id=? AND e.is_canon=1 AND e.visibility='public' ORDER BY e.created_at LIMIT 120""", (world["id"],))]
            knowledge = [dict(row) for row in connection.execute(
                """SELECT e.*, k.state AS knowledge_state, k.belief, k.confidence, k.source, l.name AS location_name
                   FROM character_knowledge k JOIN world_events e ON e.id=k.event_id
                   LEFT JOIN locations l ON l.id=e.location_id
                   WHERE k.character_id=? AND e.is_canon=1 AND e.visibility!='public' ORDER BY e.created_at LIMIT 120""", (character["id"],))]
            relationships = [dict(row) for row in connection.execute("SELECT * FROM relationships WHERE from_character_id=?", (character["id"],))]
            locations = [dict(row) for row in connection.execute("SELECT * FROM locations WHERE world_id=? ORDER BY sort_order", (world["id"],))]
            characters = [self._character(row) for row in connection.execute("SELECT * FROM characters WHERE world_id=? ORDER BY sort_order", (world["id"],))]
        return {"conversation": dict(conversation), "world": world, "character": character, "scene": scene, "messages": messages, "public_events": public_events, "knowledge": knowledge, "relationships": relationships, "locations": locations, "characters": characters}

    def apply_world_patch(self, conversation_id: str, branch_id: str, source_message_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        created = now()
        with self.transaction() as connection:
            conversation = connection.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if not conversation:
                raise KeyError("conversation_not_found")
            character_id = conversation["character_id"]
            is_canon = int(branch_id == conversation["canonical_branch_id"])
            scene_patch = patch.get("scene") or {}
            if scene_patch:
                location_id = scene_patch.get("location_id")
                if not location_id and scene_patch.get("location"):
                    found = connection.execute("SELECT id FROM locations WHERE world_id=? AND name=?", (conversation["world_id"], scene_patch["location"])).fetchone()
                    location_id = found["id"] if found else None
                current = connection.execute("SELECT * FROM scene_states WHERE branch_id=?", (branch_id,)).fetchone()
                threads = scene_patch.get("active_threads")
                connection.execute(
                    """INSERT INTO scene_states(branch_id,location_id,world_time,tension,active_threads,physical_state,emotional_state,updated_at)
                       VALUES (?,?,?,?,?,?,?,?)
                       ON CONFLICT(branch_id) DO UPDATE SET location_id=excluded.location_id, world_time=excluded.world_time,
                       tension=excluded.tension, active_threads=excluded.active_threads, physical_state=excluded.physical_state,
                       emotional_state=excluded.emotional_state, updated_at=excluded.updated_at""",
                    (
                        branch_id,
                        location_id or (current["location_id"] if current else None),
                        scene_patch.get("world_time") or (current["world_time"] if current else ""),
                        max(0, min(100, int(scene_patch.get("tension", current["tension"] if current else 0)))),
                        _json(threads if isinstance(threads, list) else _loads(current["active_threads"], []) if current else []),
                        scene_patch.get("physical_state") or (current["physical_state"] if current else ""),
                        scene_patch.get("emotional_state") or (current["emotional_state"] if current else ""),
                        created,
                    ),
                )
                if is_canon:
                    effective_location = location_id or (current["location_id"] if current else None)
                    effective_time = scene_patch.get("world_time") or (current["world_time"] if current else "")
                    if effective_location:
                        connection.execute("UPDATE characters SET current_location_id=?, updated_at=? WHERE id=?", (effective_location, created, character_id))
                    if effective_time:
                        connection.execute("UPDATE worlds SET current_time=?, updated_at=? WHERE id=?", (effective_time, created, conversation["world_id"]))
            event = patch.get("event")
            event_id = None
            if isinstance(event, dict) and event.get("title"):
                event_id = uid("event")
                visibility = event.get("visibility") if event.get("visibility") in {"public", "private", "secret"} else "private"
                location_id = scene_patch.get("location_id") if isinstance(scene_patch, dict) else None
                if not location_id:
                    state = connection.execute("SELECT location_id FROM scene_states WHERE branch_id=?", (branch_id,)).fetchone()
                    location_id = state["location_id"] if state else None
                connection.execute(
                    "INSERT INTO world_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (event_id, conversation["world_id"], branch_id, source_message_id, event["title"], event.get("details", ""), visibility, location_id, event.get("world_time", ""), is_canon, created),
                )
                character_by_name = {row["name"]: row["id"] for row in connection.execute("SELECT id,name FROM characters WHERE world_id=?", (conversation["world_id"],))}
                participant_ids = {character_id}
                for name in event.get("participants", []):
                    if name in character_by_name:
                        participant_ids.add(character_by_name[name])
                for participant_id in participant_ids:
                    connection.execute("INSERT OR IGNORE INTO event_participants VALUES (?,?,?)", (event_id, participant_id, "participant"))
                witnesses = set(event.get("witnesses", []))
                if visibility == "public":
                    witnesses.update(character_by_name)
                else:
                    witnesses.add(next(name for name, cid in character_by_name.items() if cid == character_id))
                beliefs = event.get("beliefs") if isinstance(event.get("beliefs"), dict) else {}
                for name in witnesses:
                    knowledge_character_id = character_by_name.get(name)
                    if not knowledge_character_id:
                        continue
                    belief = beliefs.get(name, "")
                    state = "misunderstood" if belief else "witnessed"
                    connection.execute(
                        "INSERT OR REPLACE INTO character_knowledge VALUES (?,?,?,?,?,?,?,?)",
                        (uid("know"), event_id, knowledge_character_id, state, belief, 0.7 if belief else 1.0, "亲历" if name in event.get("witnesses", []) else "公开事件", created),
                    )
            relationship = patch.get("relationship") or {}
            if relationship:
                row = connection.execute("SELECT * FROM relationships WHERE from_character_id=? AND to_user=1", (character_id,)).fetchone()
                if row:
                    connection.execute(
                        """UPDATE relationships SET trust=?, desire=?, tension=?, label=?, notes=?, updated_at=? WHERE id=?""",
                        (
                            max(-100, min(100, row["trust"] + int(relationship.get("trust_delta", 0)))),
                            max(-100, min(100, row["desire"] + int(relationship.get("desire_delta", 0)))),
                            max(0, min(100, row["tension"] + int(relationship.get("tension_delta", 0)))),
                            relationship.get("label") or row["label"], relationship.get("note") or row["notes"], created, row["id"],
                        ),
                    )
            state = connection.execute("SELECT * FROM scene_states WHERE branch_id=?", (branch_id,)).fetchone()
            if state:
                connection.execute(
                    """INSERT OR REPLACE INTO scene_snapshots
                       (message_id,branch_id,location_id,world_time,tension,active_threads,physical_state,emotional_state,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (source_message_id, branch_id, state["location_id"], state["world_time"], state["tension"], state["active_threads"], state["physical_state"], state["emotional_state"], created),
                )
        return {"event_id": event_id, "is_canon": bool(is_canon)}

    def active_provider(self) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute("SELECT * FROM provider_profiles WHERE is_active=1 ORDER BY updated_at DESC LIMIT 1").fetchone()
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
                """INSERT INTO provider_profiles(id,name,protocol,base_url,path,model,max_tokens,temperature,is_active,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,1,?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name,protocol=excluded.protocol,base_url=excluded.base_url,
                   path=excluded.path,model=excluded.model,max_tokens=excluded.max_tokens,temperature=excluded.temperature,
                   is_active=1,updated_at=excluded.updated_at""",
                (profile_id, values.get("name", "我的模型"), protocol, values["base_url"].rstrip("/"), values["path"], values["model"], int(values.get("max_tokens", 16384)), float(values.get("temperature", 0.95)), now()),
            )
        return self.active_provider()
