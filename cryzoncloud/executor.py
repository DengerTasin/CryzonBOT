"""Node execution layer.

CryzonCloud can drive three kinds of nodes:

* ``local`` – the host the bot itself runs on (direct ``subprocess``)
* ``ssh``   – a remote host reached over SSH (paramiko)
* ``agent`` – a remote host running ``node_agent.py`` (HTTP + Cloudflare tunnel)

Every node ultimately exposes the same primitive: ``run(node, command)`` which
returns ``(exit_code, stdout+stderr)``.
"""
from __future__ import annotations

import asyncio
import logging
import shlex
from typing import Dict, Optional, Tuple

import aiohttp

logger = logging.getLogger("cryzoncloud.executor")

DEFAULT_TIMEOUT = 180


# --------------------------------------------------------------------------- #
# Local execution
# --------------------------------------------------------------------------- #
async def _run_local(command: str, timeout: int) -> Tuple[int, str]:
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "Command timed out"
    return proc.returncode or 0, out.decode(errors="replace")


# --------------------------------------------------------------------------- #
# SSH execution
# --------------------------------------------------------------------------- #
def _run_ssh_sync(node: Dict, command: str, timeout: int) -> Tuple[int, str]:
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=node["ssh_host"],
            port=int(node.get("ssh_port") or 22),
            username=node.get("ssh_user") or "root",
            password=node.get("ssh_pass") or None,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
        )
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        code = stdout.channel.recv_exit_status()
        return code, (out + err).strip()
    finally:
        client.close()


async def _run_ssh(node: Dict, command: str, timeout: int) -> Tuple[int, str]:
    return await asyncio.to_thread(_run_ssh_sync, node, command, timeout)


# --------------------------------------------------------------------------- #
# Agent execution (HTTP)
# --------------------------------------------------------------------------- #
async def _run_agent(node: Dict, command: str, timeout: int) -> Tuple[int, str]:
    url = (node.get("url") or "").rstrip("/")
    if not url:
        return 1, "Node has no agent URL configured."
    endpoint = f"{url}/api/exec"
    headers = {"X-API-Key": node.get("api_key") or ""}
    payload = {"command": command, "timeout": timeout}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                endpoint, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout + 15)
            ) as resp:
                data = await resp.json(content_type=None)
                return int(data.get("code", 0)), str(data.get("output", ""))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent call to %s failed: %s", url, exc)
        return 1, f"Agent unreachable: {exc}"


# --------------------------------------------------------------------------- #
# Public dispatcher
# --------------------------------------------------------------------------- #
async def run(node: Dict, command: str, timeout: int = DEFAULT_TIMEOUT) -> Tuple[int, str]:
    kind = (node.get("kind") or "agent").lower()
    if kind == "local":
        return await _run_local(command, timeout)
    if kind == "ssh":
        return await _run_ssh(node, command, timeout)
    return await _run_agent(node, command, timeout)


async def ping(node: Dict) -> bool:
    """Return True if the node responds."""
    kind = (node.get("kind") or "agent").lower()
    if kind == "local":
        code, _ = await _run_local("command -v lxc >/dev/null 2>&1 && echo ok || echo ok", 15)
        return code == 0
    if kind == "ssh":
        try:
            code, _ = await _run_ssh(node, "echo ok", 15)
            return code == 0
        except Exception:
            return False
    url = (node.get("url") or "").rstrip("/")
    if not url:
        return False
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{url}/api/ping",
                headers={"X-API-Key": node.get("api_key") or ""},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return resp.status == 200
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# LXC helpers
# --------------------------------------------------------------------------- #
def _q(value) -> str:
    return shlex.quote(str(value))


async def lxc_launch(node: Dict, name: str, image: str, ram: int, cpu: int, disk: int,
                     pool: str = "default") -> Tuple[int, str]:
    """Create + start an LXC container with dedicated RAM/CPU/disk limits."""
    cmd = (
        f"lxc launch {_q(image)} {_q(name)} -s {_q(pool)} "
        f"-c limits.memory={_q(str(ram) + 'GB')} "
        f"-c limits.cpu={_q(str(cpu))} "
        f"-c limits.disk={_q(str(disk) + 'GB')}"
    )
    return await run(node, cmd, timeout=300)


async def lxc_set_memory(node: Dict, name: str, ram: int) -> Tuple[int, str]:
    return await run(node, f"lxc config set {_q(name)} limits.memory={_q(str(ram) + 'GB')}", 60)


async def lxc_start(node: Dict, name: str) -> Tuple[int, str]:
    return await run(node, f"lxc start {_q(name)}", 60)


async def lxc_stop(node: Dict, name: str) -> Tuple[int, str]:
    return await run(node, f"lxc stop {_q(name)}", 60)


async def lxc_delete(node: Dict, name: str) -> Tuple[int, str]:
    return await run(node, f"lxc delete {_q(name)} --force", 120)


async def lxc_exec(node: Dict, name: str, inner: str, timeout: int = 120) -> Tuple[int, str]:
    return await run(node, f"lxc exec {_q(name)} -- bash -lc {_q(inner)}", timeout)


async def lxc_list(node: Dict) -> Tuple[int, str]:
    return await run(node, "lxc list --format csv -c n,s,4", 60)


async def lxc_config_get(node: Dict, name: str, key: str) -> Tuple[int, str]:
    return await run(node, f"lxc config get {_q(name)} {_q(key)}", 30)


async def container_ip(node: Dict, name: str) -> Optional[str]:
    code, out = await run(node, f"lxc list {_q(name)} --format csv -c 4", 30)
    if code != 0 or not out:
        return None
    first = out.strip().splitlines()[0].strip().strip('"')
    ip = first.split(",")[0].strip() if first else ""
    if not ip or ip.lower() == "none":
        return None
    return ip
