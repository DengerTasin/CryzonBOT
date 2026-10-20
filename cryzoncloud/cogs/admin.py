"""Admin & permission management commands."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .. import checks, config, database, embeds, permissions


class PermSelect(discord.ui.Select):
    def __init__(self, defaults: list[str] | None = None):
        options = [
            discord.SelectOption(
                label=permissions.PERMISSION_LABELS[p],
                value=p,
                description=f"Grant the '{p}' permission",
                default=(defaults is not None and p in defaults),
            )
            for p in permissions.ALL_PERMISSIONS
        ]
        super().__init__(
            placeholder="Select the permissions to grant…",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.chosen = list(self.values)
        await interaction.response.defer()


class AdminGrantView(discord.ui.View):
    def __init__(self, target: discord.abc.User, author_id: int, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.target = target
        self.author_id = author_id
        self.chosen: list[str] = []
        self.add_item(PermSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This menu isn't for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Grant", style=discord.ButtonStyle.success, emoji="✅", row=1)
    async def grant(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.chosen:
            await interaction.response.send_message("Select at least one permission first.", ephemeral=True)
            return
        database.add_admin(str(self.target.id), self.chosen, added_by=str(self.author_id))
        e = embeds.success(
            "Admin Granted",
            f"{self.target.mention} is now an admin.",
        )
        embeds.add_field(e, "Permissions", permissions.describe_perms(self.chosen), False)
        await interaction.response.edit_message(embed=e, view=None)
        self.stop()

    @discord.ui.button(label="Grant All", style=discord.ButtonStyle.primary, emoji="👑", row=1)
    async def grant_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        database.add_admin(str(self.target.id), ["all"], added_by=str(self.author_id))
        e = embeds.success("Admin Granted", f"{self.target.mention} now has **all** permissions.")
        await interaction.response.edit_message(embed=e, view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️", row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=embeds.info("Cancelled", "No changes made."), view=None)
        self.stop()


class Admin(commands.Cog):
    """Manage CryzonCloud administrators and their granular permissions."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="adminadd", description="Add an admin with specific permissions.")
    @app_commands.describe(user="The user to promote.", perms="Optional comma-separated perms (e.g. create,delete). Omit for a GUI.")
    @checks.require_perm("admin")
    async def adminadd(self, interaction: discord.Interaction, user: discord.User, perms: str | None = None):
        if permissions.is_root(user.id):
            await interaction.response.send_message(
                embed=embeds.info("Already Root Admin", f"{user.mention} is a root admin with all permissions."),
                ephemeral=True,
            )
            return
        if perms:
            chosen = permissions.normalize_perms([p.strip() for p in perms.split(",")])
            if not chosen:
                await interaction.response.send_message(
                    embed=embeds.error("Invalid Permissions", "None of those permissions are valid."),
                    ephemeral=True,
                )
                return
            database.add_admin(str(user.id), list(chosen), added_by=str(interaction.user.id))
            e = embeds.success("Admin Granted", f"{user.mention} is now an admin.")
            embeds.add_field(e, "Permissions", permissions.describe_perms(chosen), False)
            await interaction.response.send_message(embed=e)
            return
        e = embeds.brand(
            "Grant Admin",
            f"Choose the permissions to grant **{user.mention}**.\n"
            "Use the dropdown, then press **Grant** — or **Grant All** for full access.",
        )
        await interaction.response.send_message(embed=e, view=AdminGrantView(user, interaction.user.id), ephemeral=True)

    @app_commands.command(name="adminremove", description="Remove an admin.")
    @app_commands.describe(user="The admin to demote.")
    @checks.require_perm("admin")
    async def adminremove(self, interaction: discord.Interaction, user: discord.User):
        if permissions.is_root(user.id):
            await interaction.response.send_message(
                embed=embeds.error("Cannot Remove", "Root admins cannot be removed."), ephemeral=True
            )
            return
        if not database.get_admin(str(user.id)):
            await interaction.response.send_message(
                embed=embeds.error("Not Admin", f"{user.mention} is not an admin."), ephemeral=True
            )
            return
        database.remove_admin(str(user.id))
        await interaction.response.send_message(
            embed=embeds.success("Admin Removed", f"{user.mention} is no longer an admin.")
        )

    @app_commands.command(name="adminperms", description="Edit an admin's permissions.")
    @app_commands.describe(user="The admin to edit.", perms="Comma-separated perms, or 'all'.")
    @checks.require_perm("admin")
    async def adminperms(self, interaction: discord.Interaction, user: discord.User, perms: str):
        if permissions.is_root(user.id):
            await interaction.response.send_message(
                embed=embeds.info("Root Admin", "Root admins always hold every permission."), ephemeral=True
            )
            return
        if not database.get_admin(str(user.id)):
            await interaction.response.send_message(
                embed=embeds.error("Not Admin", f"{user.mention} is not an admin."), ephemeral=True
            )
            return
        chosen = permissions.normalize_perms([p.strip() for p in perms.split(",")])
        if not chosen:
            await interaction.response.send_message(
                embed=embeds.error("Invalid Permissions", "None of those permissions are valid."), ephemeral=True
            )
            return
        database.set_admin_perms(str(user.id), list(chosen))
        e = embeds.success("Permissions Updated", f"{user.mention}'s permissions were updated.")
        embeds.add_field(e, "Permissions", permissions.describe_perms(chosen), False)
        await interaction.response.send_message(embed=e)

    async def _render_admin_list(self, interaction: discord.Interaction):
        e = embeds.brand("CryzonCloud • Admin Team", "Everyone with administrative access.")
        roots = []
        for rid in config.DEFAULT_ADMINS:
            roots.append(f"👑 <@{rid}> (`{rid}`) — **all permissions**")
        embeds.add_field(e, "Root Admins", "\n".join(roots), False)
        admins = [a for a in database.list_admins() if int(a["user_id"]) not in config.DEFAULT_ADMINS]
        if admins:
            lines = []
            for a in admins:
                lines.append(f"🛡️ <@{a['user_id']}> (`{a['user_id']}`)\n> {permissions.describe_perms(a['perms'])}")
            embeds.add_field(e, f"Admins ({len(admins)})", "\n".join(lines)[:1024], False)
        else:
            embeds.add_field(e, "Admins", "No additional admins yet.", False)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="admins", description="List all admins and their permissions.")
    @checks.require_admin()
    async def admins(self, interaction: discord.Interaction):
        await self._render_admin_list(interaction)

    @app_commands.command(name="adminlist", description="List all admins and their permissions.")
    @checks.require_admin()
    async def adminlist(self, interaction: discord.Interaction):
        await self._render_admin_list(interaction)


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
