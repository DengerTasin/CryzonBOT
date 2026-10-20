#!/usr/bin/env bash
# =============================================================================
#  CryzonCloud — Cloud Node Setup Script
#  Provisions an LXD host and starts the CryzonCloud node agent.
#
#  Usage:
#     sudo CRYZON_API_KEY="<api-key-from-/createnode>" \
#          CRYZON_TUNNEL="node1.example.com" \
#          bash node_setup.sh
# =============================================================================
set -euo pipefail

API_KEY="${CRYZON_API_KEY:-}"
PORT="${CRYZON_PORT:-8787}"
TUNNEL="${CRYZON_TUNNEL:-}"

echo "==> CryzonCloud node setup"

# --- 1. System packages ---------------------------------------------------- #
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y lxd lxd-client curl python3 python3-pip
elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y lxd curl python3 python3-pip
elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y lxd curl python3 python3-pip
fi

# --- 2. Initialise LXD ----------------------------------------------------- #
if ! sudo lxc info >/dev/null 2>&1; then
    sudo lxd init --auto
fi
sudo usermod -aG lxd "$USER" || true

# --- 3. Node agent --------------------------------------------------------- #
sudo mkdir -p /opt/cryzoncloud
if [ ! -f /opt/cryzoncloud/node_agent.py ]; then
    echo "!! Copy node_agent.py to /opt/cryzoncloud/ before continuing."
fi
pip3 install --quiet flask

cat >/etc/systemd/system/cryzoncloud-agent.service <<EOF
[Unit]
Description=CryzonCloud Node Agent
After=network.target

[Service]
Type=simple
Environment=CRYZON_API_KEY=${API_KEY}
Environment=CRYZON_PORT=${PORT}
WorkingDirectory=/opt/cryzoncloud
ExecStart=/usr/bin/python3 /opt/cryzoncloud/node_agent.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now cryzoncloud-agent

# --- 4. Cloudflare Tunnel (optional) -------------------------------------- #
if [ -n "${TUNNEL}" ]; then
    if ! command -v cloudflared >/dev/null 2>&1; then
        curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
            -o /usr/local/bin/cloudflared
        sudo chmod +x /usr/local/bin/cloudflared
    fi
    echo "==> Configure the tunnel manually (one-time):"
    echo "    cloudflared tunnel login"
    echo "    cloudflared tunnel create cryzon-node"
    echo "    cloudflared tunnel route dns cryzon-node ${TUNNEL}"
    echo "    cloudflared tunnel run --url http://localhost:${PORT} cryzon-node"
fi

echo "==> Done. Agent should be reachable at http://<host>:${PORT}/api/ping"
