"""Invite-reward economy.

Admins configure, per node, how many invites are needed for a reward and what
that reward grants (RAM, disk, and how many reward containers a user may hold).
``/invitegui`` renders a clean overview; ``/inviteclaim`` redeems a reward.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from .. import checks, config, database, embeds, executor, views

logger = logging.getLogger("cryzoncloud.invites")


class InviteConfigModal(discord.ui.Modal, title="CryzonCloud • Invite Reward Setup"):
    invites = discord.ui.TextInput(label="Invites required per reward", placeholder="1", default="1", max_length=4)
    ram = discord.ui.TextInput(label="RAM per reward (GB)", placeholder="1", default="1", max_length=3)
    disk = discord.ui.TextInput(label="Disk per reward (GB)", placeholder="10", default="10", max_length=4)
    containers = discord.ui.TextInput(label="Max reward containers per user", placeholder="1", default="1", max_length=3)

    def __init__(self, node_id: int):
        super().__init__()
        self.node_id = node_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            invites = max(1, int(str(self.invites.value).strip()))
            ram = max(1, int(str(self.ram.value).strip()))
            disk = max(1, int(str(self.disk.value).strip()))
            containers = max(1, int(str(self.containers.value).strip()))
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error("Invalid Input", "All values must be whole numbers."), ephemeral=True
            )
            return
        database.set_invite_config(
            self.node_id,
            enabled=True,
            invites_per_reward=invites,
            ram_per_reward=ram,
            disk_per_reward=disk,
            containers_per_reward=containers,
        )
        node = database.get_node(self.node_id)
        e = embeds.success("Invite Rewards Saved", f"Configuration updated for node **{node['name'] if node else self.node_id}**.")
        embeds.add_field(e, "Invites / reward", invites, True)
        embeds.add_field(e, "RAM / reward", f"{ram} GB", True)
        embeds.add_field(e, "Disk / reward", f"{disk} GB", True)
        embeds.add_field(e, "Max containers", containers, True)
        await interaction.response.send_message(embed=e)


class InviteSetupView(discord.ui.View):
    def __init__(self, nodes: list[dict], author_id: int, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.add_item(InviteSetupSelect(nodes))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This menu isn't for you.", ephemeral=True)
            return False
        return True


class InviteSetupSelect(discord.ui.Select):
    def __init__(self, nodes: list[dict]):
        options = []
        for n in nodes[:25]:
            cfg = database.get_invite_config(n["id"])
            state = "configured" if cfg else "not configured"
            options.append(
                discord.SelectOption(label=str(n["name"])[:100], value=str(n["id"]), description=state)
            )
        super().__init__(placeholder="Select a node to configure…", options=options or [
            discord.SelectOption(label="No nodes", value="none")
        ])

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message(embed=embeds.error("No Nodes", "Create a node first."), ephemeral=True)
            return
        await interaction.response.send_modal(InviteConfigModal(int(self.values[0])))


class Invites(commands.Cog):
    """Invite tracking and reward configuration."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._invite_cache: dict[int, dict[str, int]] = {}

    # ----- invite tracking ------------------------------------------------- #
    async def cache_invites(self, guild: discord.Guild):
        try:
            invites = await guild.invites()
            self._invite_cache[guild.id] = {inv.code: inv.uses or 0 for inv in invites}
        except (discord.Forbidden, discord.HTTPException):
            self._invite_cache[guild.id] = {}

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await self.cache_invites(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        if invite.guild:
            self._invite_cache.setdefault(invite.guild.id, {})[invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        if invite.guild:
            self._invite_cache.get(invite.guild.id, {}).pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        before = dict(self._invite_cache.get(guild.id, {}))
        await self.cache_invites(guild)
        after = self._invite_cache.get(guild.id, {})
        used_code = None
        for code, uses in after.items():
            if uses > before.get(code, 0):
                used_code = code
                break
        if not used_code:
            return
        try:
            invite = await self.bot.fetch_invite(used_code)
            inviter = invite.inviter
        except Exception:  # noqa: BLE001
            inviter = None
        if inviter and inviter.id != member.id:
            database.record_invite(str(inviter.id), str(member.id), used_code, None)
            total = database.count_invites(str(inviter.id))
            inviter_member = member.guild.get_member(inviter.id)
            if inviter_member:
                try:
                    await inviter_member.send(
                        embed=embeds.brand(
                            "Invite Tracked!",
                            f"**{member}** joined using your invite `{used_code}`.\n"
                            f"You now have **{total}** invite(s).",
                        )
                    )
                except discord.HTTPException:
                    pass

    # ----- commands -------------------------------------------------------- #
    @app_commands.command(name="invitesetup", description="Configure invite rewards per node.")
    @checks.require_perm("invite")
    async def invitesetup(self, interaction: discord.Interaction):
        nodes = database.list_nodes()
        if not nodes:
            await interaction.response.send_message(
                embed=embeds.error("No Nodes", "Create a node before configuring invites."), ephemeral=True
            )
            return
        e = embeds.brand(
            "Invite Reward Setup",
            "Pick a node to configure.\n"
            "You'll set how many invites are needed per reward and what the reward grants "
            "(RAM, disk, and how many reward containers a user may hold).",
        )
        await interaction.response.send_message(embed=e, view=InviteSetupView(nodes, interaction.user.id), ephemeral=True)

    @app_commands.command(name="invitegui", description="Open the invite-reward dashboard.")
    async def invitegui(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        configs = database.list_invite_configs()
        my_invites = database.count_invites(str(interaction.user.id))

        e = embeds.brand(
            "🎁 CryzonCloud • Invite Rewards",
            "Invite friends to the server to earn **free VPS resources**!\n"
            "Each invite counts toward a reward tier on the node of your choice.",
        )
        if not configs:
            embeds.add_field(e, "No Rewards Configured", "An admin must run `/invitesetup` first.", False)
        else:
            for cfg in configs:
                node = database.get_node(cfg["node_id"])
                name = node["name"] if node else f"Node {cfg['node_id']}"
                status = "🟢 active" if cfg["enabled"] else "🔴 disabled"
                need = cfg["invites_per_reward"]
                earned = my_invites // need
                field = (
                    f"**{status}**\n"
                    f"• **{need}** invite(s) → **{cfg['ram_per_reward']} GB RAM**, "
                    f"**{cfg['disk_per_reward']} GB disk**\n"
                    f"• Max reward containers: **{cfg['containers_per_reward']}**\n"
                    f"• You can claim **{earned}** reward(s) here"
                )
                embeds.add_field(e, f"🖥️ {name}", field, False)

        embeds.add_field(e, "📨 Your Invites", f"**{my_invites}** tracked invite(s)", True)
        leaderboard = database.invites_leaderboard(5)
        if leaderboard:
            lb = "\n".join(f"`#{i+1}` <@{row['inviter_id']}> — **{row['total']}**" for i, row in enumerate(leaderboard))
            embeds.add_field(e, "🏆 Top Inviters", lb, False)
        embeds.add_field(e, "How to claim", "Use `/inviteclaim node:<id>` to redeem a reward VPS.", False)
        await interaction.followup.send(embed=e, ephemeral=True)

    @app_commands.command(name="invites", description="Show your invite count.")
    async def invites(self, interaction: discord.Interaction):
        total = database.count_invites(str(interaction.user.id))
        await interaction.response.send_message(
            embed=embeds.brand("Your Invites", f"You have **{total}** tracked invite(s)."), ephemeral=True
        )

    @app_commands.command(name="inviteclaim", description="Redeem an invite reward as a VPS.")
    @app_commands.describe(node_id="The node to claim a reward on.")
    async def inviteclaim(self, interaction: discord.Interaction, node_id: int):
        cfg = database.get_invite_config(node_id)
        if not cfg or not cfg["enabled"]:
            await interaction.response.send_message(
                embed=embeds.error("No Rewards", "This node has no active invite rewards."), ephemeral=True
            )
            return
        node = database.get_node(node_id)
        if not node:
            await interaction.response.send_message(embed=embeds.error("No Node", "That node no longer exists."), ephemeral=True)
            return
        user_id = str(interaction.user.id)
        my_invites = database.count_invites(user_id)
        need = cfg["invites_per_reward"]
        earned = my_invites // need
        # Count already-claimed reward containers (containers named cryzon-reward-*)
        claimed = len([v for v in database.list_vps_for_user(user_id) if v["container_name"].startswith("cryzon-reward")])
        if earned <= claimed:
            await interaction.response.send_message(
                embed=embeds.error(
                    "Not Enough Invites",
                    f"You need **{need}** invite(s) per reward.\n"
                    f"You have **{my_invites}** invite(s) → **{earned}** reward(s), already claimed **{claimed}**.",
                ),
                ephemeral=True,
            )
            return
        if claimed >= cfg["containers_per_reward"]:
            await interaction.response.send_message(
                embed=embeds.error("Container Limit", f"You've reached the max of **{cfg['containers_per_reward']}** reward containers on this node."),
                ephemeral=True,
            )
            return
        if database.count_vps_on_node(node_id) >= (node.get("total_vps") or 0):
            await interaction.response.send_message(embed=embeds.error("Node Full", "This node has no free capacity."), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        import secrets

        name = f"cryzon-reward-{str(interaction.user.id)[-6:]}-{secrets.token_hex(2)}"
        password = secrets.token_urlsafe(12)
        msg = await interaction.followup.send(embed=embeds.info("Claiming…", f"Provisioning your reward on **{node['name']}**."), ephemeral=True)
        code, out = await executor.lxc_launch(
            node, name, config.DEFAULT_OS_IMAGE, cfg["ram_per_reward"], 1, cfg["disk_per_reward"], config.DEFAULT_STORAGE_POOL
        )
        if code != 0:
            await msg.edit(embed=embeds.error("Claim Failed", f"```{out[:1200]}```"))
            return
        await executor.lxc_exec(node, name, f"echo 'root:{password}' | chpasswd", timeout=60)
        ip = await executor.container_ip(node, name)
        expires = (datetime.now(timezone.utc) + timedelta(days=config.DEFAULT_VPS_EXPIRATION_DAYS)).isoformat()
        vps_id = database.create_vps(
            name, user_id, node_id, ram=cfg["ram_per_reward"], cpu=1, disk=cfg["disk_per_reward"],
            os_image=config.DEFAULT_OS_IMAGE, password=password, expires_at=expires,
        )
        if ip:
            database.update_vps(vps_id, ip=ip)
        e = embeds.success("Reward Claimed!", f"Your invite-reward VPS **#{vps_id}** is live.")
        embeds.add_field(e, "Container", name, True)
        embeds.add_field(e, "RAM", f"{cfg['ram_per_reward']} GB", True)
        embeds.add_field(e, "Disk", f"{cfg['disk_per_reward']} GB", True)
        embeds.add_field(e, "IP", ip or "pending", True)
        embeds.add_field(e, "Root Password", f"`{password}`", False)
        await msg.edit(embed=e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Invites(bot))
