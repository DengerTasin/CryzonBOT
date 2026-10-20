"""VPS lifecycle commands (LXC containers with dedicated RAM/CPU/disk)."""
from __future__ import annotations

import asyncio
import secrets
import string
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from .. import checks, config, database, embeds, executor, views

# user_id -> {"node_id": int, "os": str}
_DEPLOY_STATE: dict[int, dict] = {}


def _gen_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _container_name(user_id: int) -> str:
    short = str(user_id)[-6:]
    return f"cryzon-{short}-{secrets.token_hex(2)}"


def _expiry(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _parse_dt(value: str | None):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _days_left(vps: dict) -> int | None:
    dt = _parse_dt(vps.get("expires_at"))
    if not dt:
        return None
    return (dt - datetime.now(timezone.utc)).days


def vps_card(vps: dict, node: dict | None = None) -> discord.Embed:
    status_icon = {"running": "🟢", "stopped": "🔴", "suspended": "🟠"}.get(vps.get("status"), "⚪")
    e = embeds.brand(f"VPS #{vps['id']} • {vps['container_name']}", f"{status_icon} Status: **{vps.get('status')}**")
    embeds.add_field(e, "Owner", f"<@{vps['user_id']}>", True)
    embeds.add_field(e, "Node", f"#{vps['node_id']}" + (f" ({node['name']})" if node else ""), True)
    embeds.add_field(e, "OS", vps.get("os_image"), True)
    embeds.add_field(e, "RAM", f"{vps.get('ram')} GB", True)
    embeds.add_field(e, "CPU", f"{vps.get('cpu')} vCPU", True)
    embeds.add_field(e, "Disk", f"{vps.get('disk')} GB", True)
    if vps.get("ip"):
        embeds.add_field(e, "IP", f"`{vps['ip']}`", True)
    dl = _days_left(vps)
    if dl is not None:
        embeds.add_field(e, "Expires", f"{vps.get('expires_at', '')[:10]} ({dl} days)", True)
    return e


# --------------------------------------------------------------------------- #
# Deploy flow components
# --------------------------------------------------------------------------- #
class OSSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=label, value=image, emoji="🐧")
            for label, image in list(config.OS_IMAGES.items())[:25]
        ]
        super().__init__(placeholder="Select an operating system…", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        self.view.os_image = self.values[0]
        await interaction.response.send_modal(DeployResourcesModal(self.view))


class OSSelectView(discord.ui.View):
    def __init__(self, node_id: int, author_id: int, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.node_id = node_id
        self.author_id = author_id
        self.os_image: str | None = None
        self.add_item(OSSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This menu isn't for you.", ephemeral=True)
            return False
        return True


class DeployResourcesModal(discord.ui.Modal, title="CryzonCloud • VPS Resources"):
    ram = discord.ui.TextInput(label="RAM (GB)", placeholder="2", default="2", max_length=3)
    cpu = discord.ui.TextInput(label="CPU cores", placeholder="1", default="1", max_length=3)
    disk = discord.ui.TextInput(label="Disk (GB)", placeholder="20", default="20", max_length=4)

    def __init__(self, view: OSSelectView):
        super().__init__()
        self.view_ref = view

    async def on_submit(self, interaction: discord.Interaction):
        try:
            ram = int(str(self.ram.value).strip())
            cpu = int(str(self.cpu.value).strip())
            disk = int(str(self.disk.value).strip())
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error("Invalid Input", "RAM, CPU and disk must be whole numbers."), ephemeral=True
            )
            return
        if not (1 <= ram <= config.MAX_RAM_PER_VPS):
            await interaction.response.send_message(
                embed=embeds.error("RAM Out of Range", f"Choose 1–{config.MAX_RAM_PER_VPS} GB."), ephemeral=True
            )
            return
        if not (1 <= cpu <= config.MAX_CPU_PER_VPS):
            await interaction.response.send_message(
                embed=embeds.error("CPU Out of Range", f"Choose 1–{config.MAX_CPU_PER_VPS} cores."), ephemeral=True
            )
            return
        if not (5 <= disk <= config.MAX_DISK_PER_VPS):
            await interaction.response.send_message(
                embed=embeds.error("Disk Out of Range", f"Choose 5–{config.MAX_DISK_PER_VPS} GB."), ephemeral=True
            )
            return
        await interaction.response.defer(thinking=True)
        await _do_deploy(interaction, self.view_ref.node_id, self.view_ref.os_image, ram, cpu, disk)


async def _do_deploy(interaction: discord.Interaction, node_id: int, os_image: str, ram: int, cpu: int, disk: int):
    node = database.get_node(node_id)
    if not node:
        await interaction.followup.send(embed=embeds.error("Node Missing", "That node no longer exists."))
        return
    user_id = str(interaction.user.id)
    existing = database.list_vps_for_user(user_id)
    if len(existing) >= config.MAX_VPS_PER_USER and not checks.user_has(interaction.user.id, "node"):
        await interaction.followup.send(
            embed=embeds.error("Limit Reached", f"You already have {len(existing)} VPS (max {config.MAX_VPS_PER_USER}).")
        )
        return
    if database.count_vps_on_node(node_id) >= (node.get("total_vps") or 0):
        await interaction.followup.send(embed=embeds.error("Node Full", "This node has no free capacity."))
        return

    name = _container_name(interaction.user.id)
    password = _gen_password()
    e = embeds.info("Deploying…", f"Creating **{name}** on node **{node['name']}**.\nThis can take a minute.")
    msg = await interaction.followup.send(embed=e)

    code, out = await executor.lxc_launch(node, name, os_image, ram, cpu, disk, config.DEFAULT_STORAGE_POOL)
    if code != 0:
        await msg.edit(embed=embeds.error("Deploy Failed", f"```{out[:1500]}```"))
        return

    # Configure root password inside the container (best-effort)
    await executor.lxc_exec(
        node, name, f"echo 'root:{password}' | chpasswd && (command -v sshd >/dev/null && systemctl enable --now ssh || true)",
        timeout=90,
    )
    ip = await executor.container_ip(node, name)

    vps_id = database.create_vps(
        name, user_id, node_id, ram=ram, cpu=cpu, disk=disk, os_image=os_image,
        password=password, expires_at=_expiry(config.DEFAULT_VPS_EXPIRATION_DAYS),
    )
    if ip:
        database.update_vps(vps_id, ip=ip)

    vps = database.get_vps(vps_id)
    card = vps_card(vps, node)
    embeds.add_field(card, "Root Password", f"`{password}`", False)
    embeds.add_field(card, "SSH", f"`ssh root@{ip or 'pending'}`", False)
    await msg.edit(embed=card)


class VPS(commands.Cog):
    """Deploy and manage LXC-based VPS instances."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="deploy", description="Deploy a new LXC VPS (dedicated RAM/CPU/disk).")
    @checks.require_perm("create")
    async def deploy(self, interaction: discord.Interaction):
        nodes = database.list_nodes()
        if not nodes:
            await interaction.response.send_message(
                embed=embeds.error("No Nodes", "No nodes are available. Ask an admin to run `/createnode`."), ephemeral=True
            )
            return
        e = embeds.brand("Deploy VPS", "First, choose the node to host your VPS:")
        view = views.NodeSelectView(nodes, "Select a node…")
        await interaction.response.send_message(embed=e, view=view, ephemeral=True)
        await view.wait()
        if view.selected is None:
            return
        node = database.get_node(view.selected)
        e2 = embeds.brand("Deploy VPS", f"Node **{node['name']}** selected. Now pick an OS:")
        await interaction.edit_original_response(embed=e2, view=OSSelectView(view.selected, interaction.user.id))

    @app_commands.command(name="myvps", description="List your VPS instances.")
    async def myvps(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        vps_list = database.list_vps_for_user(str(interaction.user.id))
        if not vps_list:
            await interaction.followup.send(embed=embeds.info("No VPS", "You don't own any VPS yet. Use `/deploy`."), ephemeral=True)
            return
        e = embeds.brand("Your VPS", f"You own {len(vps_list)} instance(s).")
        for v in vps_list:
            node = database.get_node(v["node_id"])
            icon = {"running": "🟢", "stopped": "🔴", "suspended": "🟠"}.get(v["status"], "⚪")
            dl = _days_left(v)
            embeds.add_field(
                e,
                f"{icon} #{v['id']} • {v['container_name']}",
                f"Node: {node['name'] if node else v['node_id']} • {v['ram']}GB/{v['cpu']}vCPU/{v['disk']}GB\n"
                f"IP: `{v.get('ip') or 'pending'}` • Expires in {dl if dl is not None else '—'} days",
                False,
            )
        await interaction.followup.send(embed=e, ephemeral=True)

    @app_commands.command(name="vpsinfo", description="Show details for a VPS by ID.")
    @app_commands.describe(vps_id="The VPS ID.")
    async def vpsinfo(self, interaction: discord.Interaction, vps_id: int):
        vps = database.get_vps(vps_id)
        if not vps:
            await interaction.response.send_message(embed=embeds.error("Not Found", "No VPS with that ID."), ephemeral=True)
            return
        if str(vps["user_id"]) != str(interaction.user.id) and not checks.is_admin(interaction.user.id):
            await interaction.response.send_message(embed=embeds.error("Denied", "That VPS isn't yours."), ephemeral=True)
            return
        node = database.get_node(vps["node_id"])
        card = vps_card(vps, node)
        if checks.is_admin(interaction.user.id):
            embeds.add_field(card, "Root Password", f"`{vps.get('password') or '—'}`", False)
        await interaction.response.send_message(embed=card, ephemeral=True)

    @app_commands.command(name="renew", description="Renew a VPS to extend its expiry.")
    @app_commands.describe(vps_id="The VPS ID.", days="Days to add (default 30).")
    @checks.require_perm("renew")
    async def renew(self, interaction: discord.Interaction, vps_id: int, days: int = config.DEFAULT_VPS_EXPIRATION_DAYS):
        vps = database.get_vps(vps_id)
        if not vps:
            await interaction.response.send_message(embed=embeds.error("Not Found", "No VPS with that ID."), ephemeral=True)
            return
        base = _parse_dt(vps.get("expires_at")) or datetime.now(timezone.utc)
        if base < datetime.now(timezone.utc):
            base = datetime.now(timezone.utc)
        new_expiry = (base + timedelta(days=days)).isoformat()
        database.update_vps(vps_id, expires_at=new_expiry)
        await interaction.response.send_message(
            embed=embeds.success("VPS Renewed", f"VPS **#{vps_id}** extended by **{days}** days.\nNew expiry: `{new_expiry[:10]}`")
        )

    @app_commands.command(name="deletevps", description="Delete a VPS (destroys the container).")
    @app_commands.describe(vps_id="The VPS ID.")
    @checks.require_perm("delete")
    async def deletevps(self, interaction: discord.Interaction, vps_id: int):
        vps = database.get_vps(vps_id)
        if not vps:
            await interaction.response.send_message(embed=embeds.error("Not Found", "No VPS with that ID."), ephemeral=True)
            return
        view = views.ConfirmView(interaction.user.id)
        await interaction.response.send_message(
            embed=embeds.warning("Confirm Deletion", f"Permanently delete VPS **#{vps_id}** (`{vps['container_name']}`)?"),
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if not view.value:
            await interaction.edit_original_response(embed=embeds.info("Cancelled", "Nothing was deleted."), view=None)
            return
        await interaction.edit_original_response(embed=embeds.info("Deleting…", "Removing the container."), view=None)
        node = database.get_node(vps["node_id"])
        if node:
            await executor.lxc_delete(node, vps["container_name"])
        database.delete_vps(vps_id)
        await interaction.edit_original_response(
            embed=embeds.success("VPS Deleted", f"VPS **#{vps_id}** has been destroyed."), view=None
        )

    @app_commands.command(name="suspendvps", description="Suspend a VPS (stops the container).")
    @app_commands.describe(vps_id="The VPS ID.")
    @checks.require_perm("suspend")
    async def suspendvps(self, interaction: discord.Interaction, vps_id: int):
        vps = database.get_vps(vps_id)
        if not vps:
            await interaction.response.send_message(embed=embeds.error("Not Found", "No VPS with that ID."), ephemeral=True)
            return
        await interaction.response.defer()
        node = database.get_node(vps["node_id"])
        if node:
            await executor.lxc_stop(node, vps["container_name"])
        database.update_vps(vps_id, status="suspended")
        await interaction.followup.send(embed=embeds.success("VPS Suspended", f"VPS **#{vps_id}** is now suspended."))

    @app_commands.command(name="unsuspendvps", description="Unsuspend a VPS (starts the container).")
    @app_commands.describe(vps_id="The VPS ID.")
    @checks.require_perm("suspend")
    async def unsuspendvps(self, interaction: discord.Interaction, vps_id: int):
        vps = database.get_vps(vps_id)
        if not vps:
            await interaction.response.send_message(embed=embeds.error("Not Found", "No VPS with that ID."), ephemeral=True)
            return
        await interaction.response.defer()
        node = database.get_node(vps["node_id"])
        if node:
            await executor.lxc_start(node, vps["container_name"])
        database.update_vps(vps_id, status="running")
        await interaction.followup.send(embed=embeds.success("VPS Unsuspended", f"VPS **#{vps_id}** is running again."))

    @app_commands.command(name="execvps", description="Run a shell command inside a VPS container.")
    @app_commands.describe(vps_id="The VPS ID.", command="Command to run.")
    @checks.require_perm("exec")
    async def execvps(self, interaction: discord.Interaction, vps_id: int, command: str):
        vps = database.get_vps(vps_id)
        if not vps:
            await interaction.response.send_message(embed=embeds.error("Not Found", "No VPS with that ID."), ephemeral=True)
            return
        await interaction.response.defer()
        node = database.get_node(vps["node_id"])
        code, out = await executor.lxc_exec(node, vps["container_name"], command)
        e = embeds.brand(f"Exec • VPS #{vps_id}", f"Exit code: `{code}`")
        embeds.add_field(e, "Command", f"```{command[:500]}```", False)
        embeds.add_field(e, "Output", f"```{out[:1500] or '(no output)'}```", False)
        await interaction.followup.send(embed=e)


async def setup(bot: commands.Bot):
    await bot.add_cog(VPS(bot))
