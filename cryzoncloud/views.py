"""Reusable interactive components."""
from __future__ import annotations

from typing import Callable, List, Optional

import discord

from . import config, embeds


class NodeSelect(discord.ui.Select):
    def __init__(self, nodes: List[dict], placeholder: str = "Select a node…"):
        options = []
        for n in nodes[:25]:
            tags = ""
            try:
                import json

                tags = ", ".join(json.loads(n.get("tags") or "[]"))
            except Exception:  # noqa: BLE001
                tags = ""
            desc = f"{n.get('location', '?')} • {n.get('kind', 'agent')}"
            if tags:
                desc += f" • {tags}"
            options.append(
                discord.SelectOption(
                    label=str(n["name"])[:100],
                    value=str(n["id"]),
                    description=desc[:100],
                )
            )
        super().__init__(placeholder=placeholder, options=options or [
            discord.SelectOption(label="No nodes available", value="none")
        ], min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message(
                embed=embeds.error("No Nodes", "There are no nodes configured yet."), ephemeral=True
            )
            return
        self.view.selected = int(self.values[0])
        await interaction.response.defer()
        self.view.stop()


class NodeSelectView(discord.ui.View):
    def __init__(self, nodes: List[dict], placeholder: str = "Select a node…", timeout: float = 120):
        super().__init__(timeout=timeout)
        self.selected: Optional[int] = None
        self.add_item(NodeSelect(nodes, placeholder))


class ConfirmView(discord.ui.View):
    def __init__(self, author_id: int, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This prompt isn't for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        await interaction.response.defer()
        self.stop()
