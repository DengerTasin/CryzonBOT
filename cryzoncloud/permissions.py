"""Granular admin permission system for CryzonCloud.

Root admins (``config.DEFAULT_ADMINS``) implicitly hold every permission.
Other admins are stored in the database with an explicit permission set.

Permission keys
---------------
``create``   – deploy new VPS instances
``delete``   – delete VPS instances
``suspend``  – suspend / unsuspend VPS instances
``renew``    – renew / extend VPS expiry
``node``     – create / delete / manage nodes
``admin``    – add / remove admins (and grant permissions)
``invite``   – configure the invite-reward system
``scam``     – manage the anti-scam image database
``exec``     – run raw commands inside containers
``view``     – view admin listings / stats
``all``      – shorthand for every permission
"""
from __future__ import annotations

from typing import Iterable, List, Set

from . import config

ALL_PERMISSIONS: List[str] = [
    "create",
    "delete",
    "suspend",
    "renew",
    "node",
    "admin",
    "invite",
    "scam",
    "exec",
    "view",
]

PERMISSION_LABELS = {
    "create": "Deploy VPS",
    "delete": "Delete VPS",
    "suspend": "Suspend / Unsuspend",
    "renew": "Renew VPS",
    "node": "Manage Nodes",
    "admin": "Manage Admins",
    "invite": "Invite Rewards",
    "scam": "Anti-Scam System",
    "exec": "Container Exec",
    "view": "View Listings",
}


def normalize_perms(perms: Iterable[str]) -> Set[str]:
    """Expand ``all`` and drop unknown keys."""
    out: Set[str] = set()
    for p in perms:
        p = (p or "").strip().lower()
        if p == "all":
            return set(ALL_PERMISSIONS)
        if p in ALL_PERMISSIONS:
            out.add(p)
    return out


def is_root(user_id: int) -> bool:
    return int(user_id) in config.DEFAULT_ADMINS


def has_perm(admin_record: dict | None, user_id: int, perm: str) -> bool:
    """Return True if ``user_id`` holds ``perm``.

    ``admin_record`` is the DB row for the user (may be ``None``).
    Root admins always pass.
    """
    if is_root(user_id):
        return True
    if not admin_record:
        return False
    perms = normalize_perms(admin_record.get("perms") or [])
    return perm in perms or "all" in perms


def describe_perms(perms: Iterable[str]) -> str:
    perms = normalize_perms(perms)
    if not perms:
        return "*No permissions*"
    return ", ".join(f"`{p}`" for p in ALL_PERMISSIONS if p in perms)
