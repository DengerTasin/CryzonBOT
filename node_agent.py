#!/usr/bin/env python3
"""CryzonCloud Node Agent.

Runs on every *cloud* node. It exposes a small authenticated HTTP API that the
CryzonCloud bot calls (usually through a Cloudflare Tunnel) to create and manage
LXC containers.

Run it with:
    CRYZON_API_KEY=<key> CRYZON_PORT=8787 python3 node_agent.py

Environment:
    CRYZON_API_KEY   Shared secret the bot sends in the X-API-Key header.
    CRYZON_PORT      Port to listen on (default 8787).
    CRYZON_HOST      Bind address (default 0.0.0.0).
"""
from __future__ import annotations

import os
import shlex
import subprocess

from flask import Flask, jsonify, request

API_KEY = os.getenv("CRYZON_API_KEY", "")
PORT = int(os.getenv("CRYZON_PORT", "8787"))
HOST = os.getenv("CRYZON_HOST", "0.0.0.0")

app = Flask(__name__)


def _auth() -> bool:
    if not API_KEY:
        return True  # open mode (not recommended)
    return request.headers.get("X-API-Key", "") == API_KEY


def _run(command: str, timeout: int = 180):
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return 124, "Command timed out"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


@app.before_request
def _check_auth():
    if request.path == "/api/ping":
        return None
    if not _auth():
        return jsonify({"error": "unauthorized"}), 401
    return None


@app.get("/api/ping")
def ping():
    return jsonify({"ok": True, "agent": "cryzoncloud", "host": os.uname().nodename})


@app.post("/api/exec")
def exec_cmd():
    data = request.get_json(force=True, silent=True) or {}
    command = data.get("command", "")
    timeout = int(data.get("timeout", 180))
    if not command:
        return jsonify({"error": "no command"}), 400
    code, output = _run(command, timeout)
    return jsonify({"code": code, "output": output})


@app.post("/api/lxc/launch")
def lxc_launch():
    d = request.get_json(force=True, silent=True) or {}
    name = d.get("name")
    image = d.get("image", "ubuntu:24.04")
    ram = d.get("ram", 1)
    cpu = d.get("cpu", 1)
    disk = d.get("disk", 10)
    pool = d.get("pool", "default")
    if not name:
        return jsonify({"error": "name required"}), 400
    cmd = (
        f"lxc launch {shlex.quote(image)} {shlex.quote(name)} -s {shlex.quote(pool)} "
        f"-c limits.memory={shlex.quote(str(ram) + 'GB')} "
        f"-c limits.cpu={shlex.quote(str(cpu))} "
        f"-c limits.disk={shlex.quote(str(disk) + 'GB')}"
    )
    code, output = _run(cmd, 300)
    return jsonify({"code": code, "output": output})


@app.post("/api/lxc/<name>/<action>")
def lxc_action(name, action):
    mapping = {
        "start": f"lxc start {shlex.quote(name)}",
        "stop": f"lxc stop {shlex.quote(name)}",
        "delete": f"lxc delete {shlex.quote(name)} --force",
        "restart": f"lxc restart {shlex.quote(name)}",
    }
    if action not in mapping:
        return jsonify({"error": "unknown action"}), 400
    code, output = _run(mapping[action], 120)
    return jsonify({"code": code, "output": output})


@app.get("/api/lxc/list")
def lxc_list():
    code, output = _run("lxc list --format csv -c n,s,4", 60)
    return jsonify({"code": code, "output": output})


@app.get("/api/stats")
def stats():
    code, output = _run(
        "echo CPU:$(nproc); free -m | awk '/Mem:/{print \"RAM_TOTAL:\"$2\" RAM_USED:\"$3}'; "
        "df -h / | awk 'NR==2{print \"DISK:\"$2\" USED:\"$3}'",
        30,
    )
    return jsonify({"code": code, "output": output})


if __name__ == "__main__":
    print(f"CryzonCloud node agent listening on {HOST}:{PORT}")
    app.run(host=HOST, port=PORT)
