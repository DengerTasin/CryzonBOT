"""Profile cloning.

The bot mirrors the *presence*, *status* and *bio* of the configured source
account (default ``1329035198649864202``). Usernames are intentionally left
alone — only the live presence/status and the "about me" text are copied.

If the source account's bio is private (or the API refuses to expose it), the
bot falls back to the configured placeholder (``BOT V8``).
"""
from __future__ import annotations

import logging
from typing import Optional

import discord

from . import config

logger = logging.getLogger("cryzoncloud.profile")


def _status_from_member(member: Optional[discord.Member]) -> discord.Status:
    if member is None:
        return discord.Status.online
    return member.status


def _activity_from_member(member: Optional[discord.Member]) -> Optional[discord.Activity]:
    if member is None:
        return None
    for activity in member.activities:
        if isinstance(activity, (discord.Game, discord.Streaming, discord.Activity, discord.CustomActivity)):
            return activity
    return None


async def fetch_bio(bot: discord.Client, user_id: int) -> str:
    """Best-effort fetch of the source account's bio.

    Falls back to ``config.PROFILE_FALLBACK_BIO`` when unavailable/private.
    """
    # 1) discord.py may expose .bio on the user object
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        bio = getattr(user, "bio", None)
        if bio:
            return bio
    except Exception as exc:  # noqa: BLE001
        logger.debug("fetch_user bio failed: %s", exc)

    # 2) Raw HTTP user-profile endpoint
    try:
        data = await bot.http.get_user_profile(user_id)
        profile = data.get("user_profile", data) if isinstance(data, dict) else {}
        bio = profile.get("bio") if isinstance(profile, dict) else None
        if bio:
            return bio
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_user_profile failed (likely private): %s", exc)

    return config.PROFILE_FALLBACK_BIO


async def build_profile(bot: discord.Client) -> dict:
    """Collect the source account's presence + bio."""
    uid = config.PROFILE_SOURCE_ID
    member: Optional[discord.Member] = None
    for guild in bot.guilds:
        member = guild.get_member(uid)
        if member:
            break
    return {
        "status": _status_from_member(member),
        "activity": _activity_from_member(member),
        "bio": await fetch_bio(bot, uid),
    }


async def apply_profile(bot: discord.Client, profile: dict) -> None:
    """Push the cloned presence to Discord."""
    try:
        await bot.change_presence(
            status=profile.get("status", discord.Status.online),
            activity=profile.get("activity"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("change_presence failed: %s", exc)


async def clone_loop(bot: discord.Client) -> None:
    """Periodically re-apply the cloned profile."""
    import asyncio

    try:
        await bot.wait_until_ready()
    except Exception:  # noqa: BLE001
        return
    while not bot.is_closed():
        try:
            profile = await build_profile(bot)
            await apply_profile(bot, profile)
            bio = profile.get("bio")
            logger.info(
                "Profile cloned • status=%s • bio=%s",
                profile.get("status"),
                (bio[:60] + "…") if bio and len(bio) > 60 else bio,
            )
        except Exception:
            logger.exception("profile clone loop error")
        await asyncio.sleep(max(30, config.PROFILE_CLONE_INTERVAL))
