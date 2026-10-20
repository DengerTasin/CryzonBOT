"""Permission helpers for slash commands."""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands

from . import database, permissions


def admin_record(user_id: int) -> Optional[dict]:
    return database.get_admin(str(user_id))


def user_has(user_id: int, perm: str) -> bool:
    return permissions.has_perm(admin_record(user_id), user_id, perm)


def is_admin(user_id: int) -> bool:
    return permissions.is_root(user_id) or admin_record(user_id) is not None


def require_perm(perm: str):
    """Slash-command check requiring ``perm`` (root admins always pass)."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if user_has(interaction.user.id, perm):
            return True
        raise app_commands.CheckFailure(
            f"You need the **`{perm}`** permission to use this command."
        )

    return app_commands.check(predicate)


def require_admin():
    """Slash-command check requiring *any* admin status."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if is_admin(interaction.user.id):
            return True
        raise app_commands.CheckFailure("You must be a CryzonCloud admin to use this command.")

    return app_commands.check(predicate)
