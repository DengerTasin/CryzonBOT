"""Node management commands.

``/createnode`` walks the operator through a guided flow (basics → node type →
connection details) and then prints the exact shell commands needed to bring the
cloud node online, including the Cloudflare Tunnel setup.
"""
from __future__ import annotations

import json
import secrets

import discord
from discord import app_commands
from discord.ext import commands

from .. import checks, config, database, embeds, executor, views

# In-memory creation state: user_id -> partial node dict
_PENDING: dict[int, dict] = {}


def _mask(value: str | None, keep: int = 4) -> str:
    if not value:
        return "—"
    if len(value) <= keep * 2:
        return "•" * len(value)
    return f"{value[:keep]}…{value[-keep:]}"


def node_setup_instructions(node: dict) -> str:
    """Return copy-paste shell commands to provision this node."""
    kind = node.get("kind", "agent")
    if kind == "local":
        return (
            "```bash\n"
            "# This node runs on the bot host itself.\n"
            "sudo apt update && sudo apt install -y lxd\n"
            "sudo lxd init --auto\n"
            "sudo usermod -aG lxd $USER\n"
            "```"
        )
    if kind == "ssh":
        return (
            "```bash\n"
            "# On the remote host, install LXD so CryzonCloud can drive it over SSH:\n"
            "sudo apt update && sudo apt install -y lxd\n"
            "sudo lxd init --auto\n"
            "sudo usermod -aG lxd $USER\n"
            "```"
        )
    # agent node
    api_key = node.get("api_key") or "<API_KEY>"
    port = config.NODE_AGENT_PORT
    tunnel = node.get("cf_tunnel") or "<your-tunnel-hostname>"
    return (
        "```bash\n"
        "# 1) Install LXD + dependencies\n"
        "sudo apt update && sudo apt install -y lxd curl python3 python3-pip\n"
        "sudo lxd init --auto\n\n"
        "# 2) Get the CryzonCloud node agent (copy node_agent.py to the node)\n"
        "mkdir -p /opt/cryzoncloud && cd /opt/cryzoncloud\n"
        "pip3 install flask\n\n"
        "# 3) Run the agent (listens on the tunnel/local port)\n"
        f"CRYZON_API_KEY={api_key} CRYZON_PORT={port} "
        "python3 node_agent.py &\n\n"
        "# 4) Expose it to the bot through a Cloudflare Tunnel\n"
        "curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 "
        "-o /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared\n"
        f"cloudflared tunnel login\n"
        f"cloudflared tunnel create cryzon-node\n"
        f"cloudflared tunnel route dns cryzon-node {tunnel}\n"
        f"cloudflared tunnel run --url http://localhost:{port} cryzon-node\n"
        "```\n"
        f"Agent URL to use in the bot: `https://{tunnel}`\n"
        f"API key: `{api_key}`"
    )


# --------------------------------------------------------------------------- #
# Modals
# --------------------------------------------------------------------------- #
class NodeBasicsModal(discord.ui.Modal, title="CryzonCloud • New Node (1/2)"):
    name = discord.ui.TextInput(label="Node name", placeholder="e.g. cryzon-us-east", max_length=60)
    location = discord.ui.TextInput(label="Location", placeholder="e.g. New York, US", max_length=80, required=False)
    capacity = discord.ui.TextInput(label="Max VPS capacity", placeholder="e.g. 20", default="20", max_length=5)
    ram_total = discord.ui.TextInput(label="Total RAM (GB)", placeholder="e.g. 64", default="0", max_length=6, required=False)
    disk_total = discord.ui.TextInput(label="Total Disk (GB)", placeholder="e.g. 1000", default="0", max_length=7, required=False)

    def __init__(self, cog: "Nodes"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            capacity = int(str(self.capacity.value).strip())
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error("Invalid Capacity", "Capacity must be a whole number."), ephemeral=True
            )
            return
        try:
            ram_total = int(str(self.ram_total.value).strip() or 0)
            disk_total = int(str(self.disk_total.value).strip() or 0)
        except ValueError:
            ram_total = disk_total = 0

        _PENDING[interaction.user.id] = {
            "name": str(self.name.value).strip(),
            "location": (str(self.location.value).strip() or "Unknown"),
            "total_vps": capacity,
            "ram_total": ram_total,
            "disk_total": disk_total,
        }
        e = embeds.brand(
            "Choose Node Type",
            f"Node **{_PENDING[interaction.user.id]['name']}** created in memory.\n"
            "Now pick how CryzonCloud should reach it:",
        )
        embeds.add_field(e, "🖥️ Local", "Runs on the bot host (LXD installed here).", True)
        embeds.add_field(e, "🔗 SSH", "Remote host reached over SSH.", True)
        embeds.add_field(e, "☁️ Cloudflare Agent", "Remote host + node agent via Cloudflare Tunnel.", True)
        await interaction.response.send_message(embed=e, view=NodeTypeView(self.cog, interaction.user.id), ephemeral=True)


class LocalConfirmModal(discord.ui.Modal, title="CryzonCloud • Local Node (2/2)"):
    tags = discord.ui.TextInput(label="Tags (comma separated)", placeholder="local,fast", required=False, max_length=120)

    def __init__(self, cog: "Nodes"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        state = _PENDING.get(interaction.user.id, {})
        tags = [t.strip() for t in (str(self.tags.value) or "").split(",") if t.strip()]
        await self.cog._finalize(interaction, state, kind="local", tags=tags, extra={})


class SSHModal(discord.ui.Modal, title="CryzonCloud • SSH Node (2/2)"):
    ssh_host = discord.ui.TextInput(label="SSH host / IP", placeholder="203.0.113.10", max_length=120)
    ssh_port = discord.ui.TextInput(label="SSH port", placeholder="22", default="22", max_length=5)
    ssh_user = discord.ui.TextInput(label="SSH username", placeholder="root", default="root", max_length=60)
    ssh_pass = discord.ui.TextInput(label="SSH password", placeholder="••••••", required=False, max_length=120)
    tags = discord.ui.TextInput(label="Tags (comma separated)", placeholder="ssh,eu", required=False, max_length=120)

    def __init__(self, cog: "Nodes"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        state = _PENDING.get(interaction.user.id, {})
        tags = [t.strip() for t in (str(self.tags.value) or "").split(",") if t.strip()]
        try:
            port = int(str(self.ssh_port.value).strip() or 22)
        except ValueError:
            port = 22
        extra = {
            "ssh_host": str(self.ssh_host.value).strip(),
            "ssh_port": port,
            "ssh_user": str(self.ssh_user.value).strip() or "root",
            "ssh_pass": str(self.ssh_pass.value) or None,
        }
        await self.cog._finalize(interaction, state, kind="ssh", tags=tags, extra=extra)


class AgentModal(discord.ui.Modal, title="CryzonCloud • Cloudflare Node (2/2)"):
    cf_tunnel = discord.ui.TextInput(label="Cloudflare tunnel hostname", placeholder="node1.example.com", max_length=160)
    agent_url = discord.ui.TextInput(label="Agent URL", placeholder="https://node1.example.com", required=False, max_length=200)
    cf_token = discord.ui.TextInput(label="Cloudflare API token (optional)", required=False, max_length=200)
    api_key = discord.ui.TextInput(label="Agent API key (blank = auto)", required=False, max_length=80)
    tags = discord.ui.TextInput(label="Tags (comma separated)", placeholder="cloud,us", required=False, max_length=120)

    def __init__(self, cog: "Nodes"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        state = _PENDING.get(interaction.user.id, {})
        tags = [t.strip() for t in (str(self.tags.value) or "").split(",") if t.strip()]
        tunnel = str(self.cf_tunnel.value).strip().replace("https://", "").replace("http://", "").strip("/")
        url = str(self.agent_url.value).strip() or f"https://{tunnel}"
        if url and not url.startswith("http"):
            url = f"https://{url}"
        api_key = str(self.api_key.value).strip() or secrets.token_urlsafe(24)
        extra = {
            "cf_tunnel": tunnel,
            "url": url,
            "cf_token": str(self.cf_token.value).strip() or None,
            "api_key": api_key,
        }
        await self.cog._finalize(interaction, state, kind="agent", tags=tags, extra=extra)


class NodeTypeView(discord.ui.View):
    def __init__(self, cog: "Nodes", author_id: int, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This menu isn't for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Local", style=discord.ButtonStyle.secondary, emoji="🖥️")
    async def local(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LocalConfirmModal(self.cog))

    @discord.ui.button(label="SSH", style=discord.ButtonStyle.primary, emoji="🔗")
    async def ssh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SSHModal(self.cog))

    @discord.ui.button(label="Cloudflare Agent", style=discord.ButtonStyle.success, emoji="☁️")
    async def agent(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AgentModal(self.cog))


class Nodes(commands.Cog):
    """Create, inspect and remove CryzonCloud nodes."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _finalize(self, interaction: discord.Interaction, state: dict, *, kind: str, tags: list[str], extra: dict):
        if not state:
            await interaction.response.send_message(
                embed=embeds.error("Session Expired", "Please run `/createnode` again."), ephemeral=True
            )
            return
        try:
            node_id = database.create_node(
                state["name"],
                location=state.get("location", "Unknown"),
                kind=kind,
                total_vps=state.get("total_vps", config.NODE_DEFAULT_CAPACITY),
                ram_total=state.get("ram_total", 0),
                disk_total=state.get("disk_total", 0),
                tags=tags,
                **extra,
            )
        except Exception as exc:  # noqa: BLE001
            await interaction.response.send_message(
                embed=embeds.error("Node Creation Failed", f"`{exc}`\nIs the name already taken?"), ephemeral=True
            )
            return
        _PENDING.pop(interaction.user.id, None)
        node = database.get_node(node_id)
        e = embeds.success("Node Created", f"**{node['name']}** is now registered as node `#{node_id}`.")
        embeds.add_field(e, "Type", kind, True)
        embeds.add_field(e, "Location", node.get("location", "Unknown"), True)
        embeds.add_field(e, "Capacity", f"{node.get('total_vps')} VPS", True)
        if node.get("ram_total"):
            embeds.add_field(e, "Total RAM", f"{node['ram_total']} GB", True)
        if node.get("disk_total"):
            embeds.add_field(e, "Total Disk", f"{node['disk_total']} GB", True)
        embeds.add_field(e, "Tags", ", ".join(tags) or "—", True)
        if kind == "agent":
            embeds.add_field(e, "Agent URL", node.get("url") or "—", False)
            embeds.add_field(e, "API Key", f"`{node.get('api_key')}`", False)
            embeds.add_field(e, "Cloudflare Tunnel", node.get("cf_tunnel") or "—", False)
        embeds.add_field(e, "Setup Commands", node_setup_instructions(node), False)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="createnode", description="Register a new CryzonCloud node (guided setup).")
    @checks.require_perm("node")
    async def createnode(self, interaction: discord.Interaction):
        await interaction.response.send_modal(NodeBasicsModal(self))

    @app_commands.command(name="deletenode", description="Delete a node and all of its VPS records.")
    @app_commands.describe(node_id="The node ID to delete.")
    @checks.require_perm("node")
    async def deletenode(self, interaction: discord.Interaction, node_id: int):
        node = database.get_node(node_id)
        if not node:
            await interaction.response.send_message(embed=embeds.error("Not Found", "No node with that ID."), ephemeral=True)
            return
        count = database.count_vps_on_node(node_id)
        e = embeds.warning(
            "Confirm Node Deletion",
            f"Delete **{node['name']}** (`#{node_id}`)?\n"
            f"This node currently hosts **{count}** VPS record(s).",
        )
        view = views.ConfirmView(interaction.user.id)
        await interaction.response.send_message(embed=e, view=view, ephemeral=True)
        await view.wait()
        if not view.value:
            await interaction.edit_original_response(embed=embeds.info("Cancelled", "Node was not deleted."), view=None)
            return
        database.delete_node(node_id)
        await interaction.edit_original_response(
            embed=embeds.success("Node Deleted", f"Node **{node['name']}** and its records were removed."), view=None
        )

    @app_commands.command(name="nodes", description="List all CryzonCloud nodes.")
    @checks.require_admin()
    async def nodes(self, interaction: discord.Interaction):
        await interaction.response.defer()
        all_nodes = database.list_nodes()
        if not all_nodes:
            await interaction.followup.send(embed=embeds.info("No Nodes", "Create one with `/createnode`."))
            return
        e = embeds.brand("CryzonCloud • Nodes", f"{len(all_nodes)} node(s) registered.")
        for n in all_nodes:
            online = await executor.ping(n)
            used = database.count_vps_on_node(n["id"])
            try:
                tags = ", ".join(json.loads(n.get("tags") or "[]")) or "—"
            except Exception:  # noqa: BLE001
                tags = "—"
            status = "🟢 Online" if online else "🔴 Offline"
            field = (
                f"**Type:** {n.get('kind')} • **Status:** {status}\n"
                f"**Location:** {n.get('location', '?')} • **Tags:** {tags}\n"
                f"**Usage:** {used}/{n.get('total_vps')} VPS"
            )
            if n.get("ram_total"):
                field += f" • **RAM:** {n['ram_total']} GB"
            embeds.add_field(e, f"#{n['id']} • {n['name']}", field, False)
        await interaction.followup.send(embed=e)

    @app_commands.command(name="nodeinfo", description="Show detailed information about a node.")
    @app_commands.describe(node_id="The node ID.")
    @checks.require_admin()
    async def nodeinfo(self, interaction: discord.Interaction, node_id: int):
        node = database.get_node(node_id)
        if not node:
            await interaction.response.send_message(embed=embeds.error("Not Found", "No node with that ID."), ephemeral=True)
            return
        await interaction.response.defer()
        online = await executor.ping(node)
        used = database.count_vps_on_node(node_id)
        e = embeds.brand(f"Node #{node_id} • {node['name']}", "Detailed node information.")
        embeds.add_field(e, "Status", "🟢 Online" if online else "🔴 Offline", True)
        embeds.add_field(e, "Type", node.get("kind"), True)
        embeds.add_field(e, "Location", node.get("location"), True)
        embeds.add_field(e, "Capacity", f"{used}/{node.get('total_vps')}", True)
        if node.get("ram_total"):
            embeds.add_field(e, "Total RAM", f"{node['ram_total']} GB", True)
        if node.get("disk_total"):
            embeds.add_field(e, "Total Disk", f"{node['disk_total']} GB", True)
        if node.get("kind") == "agent":
            embeds.add_field(e, "Agent URL", node.get("url") or "—", False)
            embeds.add_field(e, "API Key", _mask(node.get("api_key")), True)
            embeds.add_field(e, "CF Tunnel", node.get("cf_tunnel") or "—", True)
        elif node.get("kind") == "ssh":
            embeds.add_field(e, "SSH", f"{node.get('ssh_user')}@{node.get('ssh_host')}:{node.get('ssh_port')}", False)
        embeds.add_field(e, "Setup Commands", node_setup_instructions(node), False)
        await interaction.followup.send(embed=e)

    @app_commands.command(name="nodestatus", description="Ping every node and report health.")
    @checks.require_admin()
    async def nodestatus(self, interaction: discord.Interaction):
        await interaction.response.defer()
        all_nodes = database.list_nodes()
        if not all_nodes:
            await interaction.followup.send(embed=embeds.info("No Nodes", "Nothing to check."))
            return
        lines = []
        for n in all_nodes:
            online = await executor.ping(n)
            lines.append(f"{'🟢' if online else '🔴'} **#{n['id']} {n['name']}** — {'online' if online else 'offline'}")
        e = embeds.brand("Node Health", "\n".join(lines))
        await interaction.followup.send(embed=e)

    @app_commands.command(name="nodesetup", description="Re-print the setup commands for a node.")
    @app_commands.describe(node_id="The node ID.")
    @checks.require_perm("node")
    async def nodesetup(self, interaction: discord.Interaction, node_id: int):
        node = database.get_node(node_id)
        if not node:
            await interaction.response.send_message(embed=embeds.error("Not Found", "No node with that ID."), ephemeral=True)
            return
        e = embeds.brand(f"Setup • {node['name']}", node_setup_instructions(node))
        await interaction.response.send_message(embed=e, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Nodes(bot))
