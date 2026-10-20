"""SQLite persistence layer for CryzonCloud.

A single module-level connection guarded by a lock keeps things simple and
thread-safe (the bot runs blocking DB calls inside ``asyncio.to_thread``).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import config

logger = logging.getLogger("cryzoncloud.db")

_conn: Optional[sqlite3.Connection] = None
_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL;")
        _conn.execute("PRAGMA foreign_keys=ON;")
    return _conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS nodes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT UNIQUE NOT NULL,
    location           TEXT DEFAULT 'Unknown',
    kind               TEXT DEFAULT 'agent',      -- local | ssh | agent
    url                TEXT,                       -- agent base url
    api_key            TEXT,                       -- agent auth key
    cf_tunnel          TEXT,                       -- cloudflare tunnel hostname
    cf_token           TEXT,                       -- cloudflare api token (masked in UI)
    ssh_host           TEXT,
    ssh_port           INTEGER DEFAULT 22,
    ssh_user           TEXT DEFAULT 'root',
    ssh_pass           TEXT,
    total_vps          INTEGER DEFAULT 20,
    ram_total          INTEGER DEFAULT 0,          -- GB
    disk_total         INTEGER DEFAULT 0,          -- GB
    tags               TEXT DEFAULT '[]',
    enabled            INTEGER DEFAULT 1,
    created_at         TEXT
);

CREATE TABLE IF NOT EXISTS vps (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    container_name TEXT UNIQUE NOT NULL,
    user_id        TEXT NOT NULL,
    node_id        INTEGER NOT NULL,
    ram            INTEGER DEFAULT 1,
    cpu            INTEGER DEFAULT 1,
    disk           INTEGER DEFAULT 10,
    os_image       TEXT DEFAULT 'ubuntu:24.04',
    status         TEXT DEFAULT 'running',         -- running | stopped | suspended
    password       TEXT,
    ip             TEXT,
    created_at     TEXT,
    expires_at     TEXT,
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS admins (
    user_id   TEXT PRIMARY KEY,
    perms     TEXT DEFAULT '[]',
    added_by  TEXT,
    added_at  TEXT
);

CREATE TABLE IF NOT EXISTS invites (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    inviter_id    TEXT NOT NULL,
    invitee_id    TEXT,
    code          TEXT,
    node_id       INTEGER,
    reward_granted INTEGER DEFAULT 0,
    joined_at     TEXT
);

CREATE TABLE IF NOT EXISTS invite_config (
    node_id            INTEGER PRIMARY KEY,
    enabled            INTEGER DEFAULT 1,
    invites_per_reward INTEGER DEFAULT 1,
    ram_per_reward     INTEGER DEFAULT 1,
    disk_per_reward    INTEGER DEFAULT 10,
    containers_per_reward INTEGER DEFAULT 1,
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scam_hashes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    hash      TEXT NOT NULL,
    algo      TEXT DEFAULT 'phash',
    label     TEXT,
    added_by  TEXT,
    added_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_scam_hash ON scam_hashes(hash);
"""


def init_db() -> None:
    with _lock:
        conn = get_conn()
        conn.executescript(SCHEMA)
        conn.commit()
    # Seed root admins
    for uid in config.DEFAULT_ADMINS:
        ensure_admin(str(uid), ["all"], added_by="system")
    logger.info("Database ready at %s", config.DB_PATH)


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #
def execute(query: str, params: tuple = (), *, fetch: str = "none"):
    with _lock:
        conn = get_conn()
        cur = conn.execute(query, params)
        if fetch == "one":
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None
        if fetch == "all":
            rows = cur.fetchall()
            conn.commit()
            return [dict(r) for r in rows]
        conn.commit()
        return cur.lastrowid


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def get_setting(key: str, default: Any = None) -> Any:
    row = execute("SELECT value FROM settings WHERE key=?", (key,), fetch="one")
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return row["value"]


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #
def create_node(
    name: str,
    *,
    location: str = "Unknown",
    kind: str = "agent",
    url: Optional[str] = None,
    api_key: Optional[str] = None,
    cf_tunnel: Optional[str] = None,
    cf_token: Optional[str] = None,
    ssh_host: Optional[str] = None,
    ssh_port: int = 22,
    ssh_user: str = "root",
    ssh_pass: Optional[str] = None,
    total_vps: int = config.NODE_DEFAULT_CAPACITY,
    ram_total: int = 0,
    disk_total: int = 0,
    tags: Optional[List[str]] = None,
) -> int:
    return execute(
        """INSERT INTO nodes
           (name, location, kind, url, api_key, cf_tunnel, cf_token,
            ssh_host, ssh_port, ssh_user, ssh_pass, total_vps, ram_total,
            disk_total, tags, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            name, location, kind, url, api_key, cf_tunnel, cf_token,
            ssh_host, ssh_port, ssh_user, ssh_pass, total_vps, ram_total,
            disk_total, json.dumps(tags or []), _now(),
        ),
    )


def get_node(node_id: int) -> Optional[Dict]:
    return execute("SELECT * FROM nodes WHERE id=?", (node_id,), fetch="one")


def get_node_by_name(name: str) -> Optional[Dict]:
    return execute("SELECT * FROM nodes WHERE name=?", (name,), fetch="one")


def list_nodes() -> List[Dict]:
    return execute("SELECT * FROM nodes ORDER BY id", fetch="all")


def delete_node(node_id: int) -> None:
    execute("DELETE FROM nodes WHERE id=?", (node_id,))


def count_vps_on_node(node_id: int) -> int:
    row = execute("SELECT COUNT(*) AS c FROM vps WHERE node_id=?", (node_id,), fetch="one")
    return row["c"] if row else 0


# --------------------------------------------------------------------------- #
# VPS
# --------------------------------------------------------------------------- #
def create_vps(
    container_name: str,
    user_id: str,
    node_id: int,
    *,
    ram: int,
    cpu: int,
    disk: int,
    os_image: str,
    password: str,
    expires_at: str,
    status: str = "running",
) -> int:
    return execute(
        """INSERT INTO vps
           (container_name, user_id, node_id, ram, cpu, disk, os_image,
            status, password, created_at, expires_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            container_name, user_id, node_id, ram, cpu, disk, os_image,
            status, password, _now(), expires_at,
        ),
    )


def get_vps(vps_id: int) -> Optional[Dict]:
    return execute("SELECT * FROM vps WHERE id=?", (vps_id,), fetch="one")


def get_vps_by_container(name: str) -> Optional[Dict]:
    return execute("SELECT * FROM vps WHERE container_name=?", (name,), fetch="one")


def list_vps_for_user(user_id: str) -> List[Dict]:
    return execute("SELECT * FROM vps WHERE user_id=? ORDER BY id", (user_id,), fetch="all")


def list_all_vps() -> List[Dict]:
    return execute("SELECT * FROM vps ORDER BY id", fetch="all")


def update_vps(vps_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE vps SET {cols} WHERE id=?", (*fields.values(), vps_id))


def delete_vps(vps_id: int) -> None:
    execute("DELETE FROM vps WHERE id=?", (vps_id,))


# --------------------------------------------------------------------------- #
# Admins
# --------------------------------------------------------------------------- #
def ensure_admin(user_id: str, perms: List[str], added_by: str = "system") -> None:
    existing = get_admin(user_id)
    if existing:
        return
    execute(
        "INSERT INTO admins(user_id, perms, added_by, added_at) VALUES(?,?,?,?)",
        (str(user_id), json.dumps(perms), added_by, _now()),
    )


def get_admin(user_id: str) -> Optional[Dict]:
    row = execute("SELECT * FROM admins WHERE user_id=?", (str(user_id),), fetch="one")
    if row:
        try:
            row["perms"] = json.loads(row["perms"])
        except (json.JSONDecodeError, TypeError):
            row["perms"] = []
    return row


def add_admin(user_id: str, perms: List[str], added_by: str) -> None:
    execute(
        "INSERT INTO admins(user_id, perms, added_by, added_at) VALUES(?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET perms=excluded.perms",
        (str(user_id), json.dumps(perms), added_by, _now()),
    )


def set_admin_perms(user_id: str, perms: List[str]) -> None:
    execute("UPDATE admins SET perms=? WHERE user_id=?", (json.dumps(perms), str(user_id)))


def remove_admin(user_id: str) -> None:
    execute("DELETE FROM admins WHERE user_id=?", (str(user_id),))


def list_admins() -> List[Dict]:
    rows = execute("SELECT * FROM admins ORDER BY added_at", fetch="all")
    for r in rows:
        try:
            r["perms"] = json.loads(r["perms"])
        except (json.JSONDecodeError, TypeError):
            r["perms"] = []
    return rows


# --------------------------------------------------------------------------- #
# Invite rewards
# --------------------------------------------------------------------------- #
def set_invite_config(
    node_id: int,
    *,
    enabled: bool,
    invites_per_reward: int,
    ram_per_reward: int,
    disk_per_reward: int,
    containers_per_reward: int,
) -> None:
    execute(
        """INSERT INTO invite_config
           (node_id, enabled, invites_per_reward, ram_per_reward, disk_per_reward, containers_per_reward)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(node_id) DO UPDATE SET
             enabled=excluded.enabled,
             invites_per_reward=excluded.invites_per_reward,
             ram_per_reward=excluded.ram_per_reward,
             disk_per_reward=excluded.disk_per_reward,
             containers_per_reward=excluded.containers_per_reward""",
        (node_id, int(enabled), invites_per_reward, ram_per_reward, disk_per_reward, containers_per_reward),
    )


def get_invite_config(node_id: int) -> Optional[Dict]:
    return execute("SELECT * FROM invite_config WHERE node_id=?", (node_id,), fetch="one")


def list_invite_configs() -> List[Dict]:
    return execute("SELECT * FROM invite_config ORDER BY node_id", fetch="all")


def record_invite(inviter_id: str, invitee_id: str, code: str, node_id: Optional[int]) -> None:
    execute(
        "INSERT INTO invites(inviter_id, invitee_id, code, node_id, joined_at) VALUES(?,?,?,?,?)",
        (str(inviter_id), str(invitee_id), code, node_id, _now()),
    )


def count_invites(inviter_id: str) -> int:
    row = execute("SELECT COUNT(*) AS c FROM invites WHERE inviter_id=?", (str(inviter_id),), fetch="one")
    return row["c"] if row else 0


def invites_leaderboard(limit: int = 10) -> List[Dict]:
    return execute(
        "SELECT inviter_id, COUNT(*) AS total FROM invites GROUP BY inviter_id ORDER BY total DESC LIMIT ?",
        (limit,),
        fetch="all",
    )


# --------------------------------------------------------------------------- #
# Scam hashes
# --------------------------------------------------------------------------- #
def add_scam_hash(h: str, algo: str, label: str, added_by: str) -> None:
    execute(
        "INSERT INTO scam_hashes(hash, algo, label, added_by, added_at) VALUES(?,?,?,?,?)",
        (h, algo, label, str(added_by), _now()),
    )


def list_scam_hashes() -> List[Dict]:
    return execute("SELECT * FROM scam_hashes ORDER BY id", fetch="all")


def delete_scam_hash(entry_id: int) -> None:
    execute("DELETE FROM scam_hashes WHERE id=?", (entry_id,))


def clear_scam_hashes() -> None:
    execute("DELETE FROM scam_hashes")
