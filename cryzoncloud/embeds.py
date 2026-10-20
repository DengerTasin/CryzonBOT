"""Reusable embed / UI helpers for CryzonCloud."""
from __future__ import annotations

import discord

from . import config


def _base(title: str, description: str, color: int) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description or None,
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text=config.FOOTER_TEXT, icon_url=config.BOT_ICON_URL)
    return embed


def embed(title: str, description: str = "", color: int = config.COLOR_PRIMARY) -> discord.Embed:
    return _base(title, description, color)


def success(title: str, description: str = "") -> discord.Embed:
    return _base(f"✅ {title}", description, config.COLOR_SUCCESS)


def error(title: str, description: str = "") -> discord.Embed:
    return _base(f"❌ {title}", description, config.COLOR_ERROR)


def warning(title: str, description: str = "") -> discord.Embed:
    return _base(f"⚠️ {title}", description, config.COLOR_WARNING)


def info(title: str, description: str = "") -> discord.Embed:
    return _base(f"ℹ️ {title}", description, config.COLOR_INFO)


def brand(title: str, description: str = "") -> discord.Embed:
    return _base(f"☁️ {title}", description, config.COLOR_CRYZON)


def add_field(embed: discord.Embed, name: str, value, inline: bool = False) -> discord.Embed:
    if value is None:
        value = "—"
    embed.add_field(name=name, value=str(value)[:1024] or "—", inline=inline)
    return embed


def progress_bar(value: float, maximum: float = 100.0, length: int = 12) -> str:
    if maximum <= 0:
        return "░" * length
    ratio = max(0.0, min(1.0, value / maximum))
    filled = int(round(ratio * length))
    return "█" * filled + "░" * (length - filled)


def human_bytes(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}PB"
