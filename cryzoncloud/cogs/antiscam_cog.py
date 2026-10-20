"""MAS Anti-Scam Imagine System.

Watches every image posted in the server, compares it against the stored scam
reference hashes, and enforces the configured action based on similarity.
"""
from __future__ import annotations

import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from .. import antiscam, checks, config, database, embeds

logger = logging.getLogger("cryzoncloud.antiscam")

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


def _is_image(attachment: discord.Attachment) -> bool:
    ctype = (attachment.content_type or "").lower()
    if ctype.startswith("image/"):
        return True
    return attachment.filename.lower().endswith(IMAGE_EXTS)


def build_report_embed(
    message: discord.Message,
    percent: float,
    action: str,
    label: str | None,
) -> discord.Embed:
    e = discord.Embed(
        title="🛡️ MAS ANTI SCAM IMAGINE SYSTEM",
        description=(
            "A suspicious image was detected and matched against the scam database.\n"
            "**This is an automated report.**"
        ),
        color=config.COLOR_DANGER,
        timestamp=discord.utils.utcnow(),
    )
    e.set_footer(text=config.FOOTER_TEXT, icon_url=config.BOT_ICON_URL)
    e.add_field(name="👤 User", value=f"{message.author.mention}\n`{message.author.id}`", inline=True)
    e.add_field(name="📍 Channel", value=f"{message.channel.mention}", inline=True)
    e.add_field(name="🎯 Match", value=f"**{percent:.1f}%**", inline=True)
    e.add_field(name="⚙️ Action", value=action.upper(), inline=True)
    if label:
        e.add_field(name="🏷️ Reference", value=label, inline=True)
    e.add_field(name="🔗 Original Message", value=f"[Jump to message]({message.jump_url})", inline=False)
    e.add_field(
        name="📤 Forward",
        value=f"Forward this report to <#{config.ANTISCAM_REPORT_CHANNEL}> or share the jump link above.",
        inline=False,
    )
    return e


class AntiScam(commands.Cog):
    """Detect and action MrBeast-style scam images."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not config.ANTISCAM_ENABLED:
            return
        if message.author.bot or not message.guild:
            return
        if not message.attachments:
            return
        if checks.is_admin(message.author.id):
            return

        references = database.list_scam_hashes()
        if not references:
            return

        for attachment in message.attachments:
            if not _is_image(attachment):
                continue
            try:
                data = await attachment.read()
            except Exception as exc:  # noqa: BLE001
                logger.debug("attachment read failed: %s", exc)
                continue
            try:
                uploaded = antiscam.compute_hashes(data)
            except Exception as exc:  # noqa: BLE001
                logger.debug("hash compute failed: %s", exc)
                continue

            percent, entry = antiscam.find_best_match(uploaded, references)
            action = antiscam.action_for(percent)
            if not action:
                continue
            label = entry.get("label") if entry else None
            await self._enforce(message, data, attachment, percent, action, label)
            return  # handle one image per message

    async def _enforce(self, message, data: bytes, attachment, percent: float, action: str, label):
        guild = message.guild
        member = message.author
        invite = config.ANTISCAM_INVITE

        # Report channel notification (for ban/kick/report tiers)
        if action in ("ban", "kick", "report"):
            await self._report(message, data, attachment, percent, action, label)

        if action == "ban":
            await self._safe_delete(message)
            await self._dm(member, "banned", invite)
            try:
                await guild.ban(member, reason=f"MAS Anti-Scam: {percent:.0f}% scam image match")
            except discord.Forbidden:
                logger.warning("Missing ban permission")
        elif action == "kick":
            await self._safe_delete(message)
            await self._dm(member, "kicked", invite)
            try:
                await guild.kick(member, reason=f"MAS Anti-Scam: {percent:.0f}% scam image match")
            except discord.Forbidden:
                logger.warning("Missing kick permission")
        elif action == "delete":
            await self._safe_delete(message)

    async def _safe_delete(self, message: discord.Message):
        try:
            await message.delete()
        except discord.HTTPException:
            pass

    async def _dm(self, member: discord.Member, verb: str, invite: str):
        try:
            e = discord.Embed(
                title="🛡️ MAS Anti-Scam Imagine System",
                description=(
                    f"You were **{verb}** because you posted an image matching a known scam.\n\n"
                    f"If this was a mistake, you may rejoin here:\n{invite}"
                ),
                color=config.COLOR_DANGER,
            )
            e.set_footer(text=config.FOOTER_TEXT, icon_url=config.BOT_ICON_URL)
            await member.send(embed=e)
        except discord.HTTPException:
            pass

    async def _report(self, message, data: bytes, attachment, percent: float, action: str, label):
        channel = self.bot.get_channel(config.ANTISCAM_REPORT_CHANNEL)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(config.ANTISCAM_REPORT_CHANNEL)
            except Exception:  # noqa: BLE001
                logger.warning("Anti-scam report channel not found")
                return
        e = build_report_embed(message, percent, action, label)
        file = discord.File(io.BytesIO(data), filename=attachment.filename)
        e.set_image(url=f"attachment://{attachment.filename}")
        try:
            await channel.send(embed=e, file=file)
        except discord.HTTPException as exc:
            logger.warning("Failed to send anti-scam report: %s", exc)

    # ----- management ------------------------------------------------------ #
    @app_commands.command(name="addscamimage", description="Add a scam reference image to the database.")
    @app_commands.describe(image="The scam image to register.", label="Optional label for this reference.")
    @checks.require_perm("scam")
    async def addscamimage(self, interaction: discord.Interaction, image: discord.Attachment, label: str | None = None):
        if not _is_image(image):
            await interaction.response.send_message(embed=embeds.error("Not An Image", "Attach an image file."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        data = await image.read()
        try:
            pairs = antiscam.hash_reference(data)
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(embed=embeds.error("Hash Failed", f"`{exc}`"), ephemeral=True)
            return
        for algo, h in pairs:
            database.add_scam_hash(h, algo, label or image.filename, interaction.user.id)
        e = embeds.success("Scam Image Added", f"Registered **{len(pairs)}** hashes for `{label or image.filename}`.")
        await interaction.followup.send(embed=e, ephemeral=True)

    @app_commands.command(name="scamimages", description="List all scam reference hashes.")
    @checks.require_perm("scam")
    async def scamimages(self, interaction: discord.Interaction):
        rows = database.list_scam_hashes()
        if not rows:
            await interaction.response.send_message(embed=embeds.info("Empty", "No scam images registered yet."), ephemeral=True)
            return
        lines = [f"`#{r['id']}` **{r['label']}** — `{r['algo']}` `{r['hash'][:12]}…`" for r in rows[:40]]
        e = embeds.brand("Scam Image Database", f"{len(rows)} hash entries.")
        embeds.add_field(e, "Entries", "\n".join(lines)[:1024], False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="removescamimage", description="Remove a scam hash entry by ID.")
    @app_commands.describe(entry_id="The hash entry ID (from /scamimages).")
    @checks.require_perm("scam")
    async def removescamimage(self, interaction: discord.Interaction, entry_id: int):
        database.delete_scam_hash(entry_id)
        await interaction.response.send_message(embed=embeds.success("Removed", f"Deleted hash entry `#{entry_id}`."), ephemeral=True)

    @app_commands.command(name="cleanscamimages", description="Clear the entire scam image database.")
    @checks.require_perm("scam")
    async def cleanscamimages(self, interaction: discord.Interaction):
        database.clear_scam_hashes()
        await interaction.response.send_message(embed=embeds.success("Cleared", "All scam hashes removed."), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiScam(bot))
