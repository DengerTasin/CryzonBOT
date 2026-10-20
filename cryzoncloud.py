#!/usr/bin/env python3
"""CryzonCloud — main entry point.

Run with:  ``python cryzoncloud.py``

Loads the command cogs, initialises the database, seeds the local node, and
starts the profile-clone loop.
"""
from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from cryzoncloud import config, database, embeds, permissions, profile

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("cryzoncloud")

# --------------------------------------------------------------------------- #
# Intents
# --------------------------------------------------------------------------- #
intents = discord.Intents.default()
intents.guilds = True
intents.members = True            # privileged — required for invites/profile clone
intents.presences = True          # privileged — required for profile clone
intents.message_content = True    # privileged — required for anti-scam + prefix
intents.invites = True

bot = commands.Bot(command_prefix=config.PREFIX, intents=intents, help_command=None)

COGS = [
    "cryzoncloud.cogs.admin",
    "cryzoncloud.cogs.nodes",
    "cryzoncloud.cogs.vps",
    "cryzoncloud.cogs.invites",
    "cryzoncloud.cogs.antiscam_cog",
]


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
@bot.event
async def on_ready():
    logger.info("Logged in as %s (%s)", bot.user, bot.user.id)
    logger.info("CryzonCloud v%s • %d guild(s)", config.BOT_VERSION, len(bot.guilds))

    # Seed a local node if none exist
    if config.LOCAL_NODE_ENABLED and not database.list_nodes():
        try:
            database.create_node(
                config.LOCAL_NODE_NAME,
                location="Local Host",
                kind="local",
                total_vps=config.NODE_DEFAULT_CAPACITY,
                tags=["local"],
            )
            logger.info("Seeded local node '%s'", config.LOCAL_NODE_NAME)
        except Exception:
            logger.exception("Failed to seed local node")

    try:
        synced = await bot.tree.sync()
        logger.info("Synced %d slash command(s)", len(synced))
    except Exception:
        logger.exception("Command sync failed")

    # Initial presence (profile clone will refine it)
    try:
        await bot.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(type=discord.ActivityType.watching, name=f"{config.BOT_NAME} • /help"),
        )
    except Exception:
        pass


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        msg = embeds.error("Permission Denied", str(error))
    else:
        logger.exception("Slash command error", exc_info=error)
        msg = embeds.error("Error", f"`{error}`")
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=msg, ephemeral=True)
        else:
            await interaction.response.send_message(embed=msg, ephemeral=True)
    except Exception:
        pass


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return
    try:
        await ctx.send(embed=embeds.error("Error", f"`{error}`"))
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Core commands
# --------------------------------------------------------------------------- #
@bot.hybrid_command(name="cryzoncloud", description="Show CryzonCloud information.")
async def cryzoncloud_info(ctx: commands.Context):
    e = embeds.brand(
        f"{config.BOT_NAME} v{config.BOT_VERSION}",
        f"{config.BOT_TAGLINE}\n\n"
        "A cloud control plane that provisions **LXC containers** as VPS instances "
        "across local and cloud nodes, with granular admin permissions, an invite-reward "
        "economy, and the MAS Anti-Scam Imagine System.",
    )
    embeds.add_field(e, "Nodes", len(database.list_nodes()), True)
    embeds.add_field(e, "VPS", len(database.list_all_vps()), True)
    embeds.add_field(e, "Admins", len(database.list_admins()), True)
    embeds.add_field(e, "Developer", config.BOT_DEVELOPER, True)
    await ctx.send(embed=e)


@bot.hybrid_command(name="help", description="List CryzonCloud commands.")
async def help_cmd(ctx: commands.Context):
    e = embeds.brand("CryzonCloud • Help", "Use the slash commands below.")
    embeds.add_field(
        e, "🚀 VPS",
        "`/deploy` — create an LXC VPS\n`/myvps` — list your VPS\n`/vpsinfo` — details\n"
        "`/renew` — extend expiry\n`/deletevps` — destroy\n`/suspendvps` / `/unsuspendvps`\n`/execvps` — run a command",
        False,
    )
    embeds.add_field(
        e, "🖥️ Nodes",
        "`/createnode` — guided node setup (incl. Cloudflare)\n`/deletenode` — remove a node\n"
        "`/nodes` — list nodes\n`/nodeinfo` — details\n`/nodestatus` — health\n`/nodesetup` — setup commands",
        False,
    )
    embeds.add_field(
        e, "🛡️ Admins",
        "`/adminadd` — grant admin (+perms)\n`/adminremove` — revoke\n`/adminperms` — edit perms\n"
        "`/admins` / `/adminlist` — list",
        False,
    )
    embeds.add_field(
        e, "🎁 Invites",
        "`/invitesetup` — configure rewards\n`/invitegui` — dashboard\n`/invites` — your count\n`/inviteclaim` — redeem",
        False,
    )
    embeds.add_field(
        e, "🚫 Anti-Scam",
        "`/addscamimage` — register a scam image\n`/scamimages` — list\n"
        "`/removescamimage` — delete one\n`/cleanscamimages` — clear all",
        False,
    )
    await ctx.send(embed=e)


@bot.hybrid_command(name="sync", description="Re-sync slash commands (owner only).")
@commands.is_owner()
async def sync_cmd(ctx: commands.Context):
    await bot.tree.sync()
    await ctx.send(embed=embeds.success("Synced", "Slash commands re-synced."))


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #
async def main():
    database.init_db()
    for ext in COGS:
        try:
            await bot.load_extension(ext)
            logger.info("Loaded %s", ext)
        except Exception:
            logger.exception("Failed to load %s", ext)

    if not config.DISCORD_TOKEN:
        logger.error("No DISCORD_TOKEN set. Create a .env file (see .env.example).")
        return

    if config.PROFILE_CLONE_ENABLED:
        asyncio.create_task(profile.clone_loop(bot))

    await bot.start(config.DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down…")
