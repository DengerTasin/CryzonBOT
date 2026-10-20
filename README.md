# ☁️ CryzonCloud

**CryzonCloud** is a Discord bot that provisions **LXC containers as VPS instances**
across local and cloud nodes. It ships with a granular admin-permission system, a
node/Cloudflare-tunnel manager, an invite-reward economy, and the **MAS Anti-Scam
Imagine System**.

> Everything is branded **CryzonCloud** — embeds, footer, presence, help and docs.

---

## ✨ Features

| System | What it does |
| --- | --- |
| **Node system** | `/createnode` (guided, incl. Cloudflare Tunnel), `/deletenode`, `/nodes`, `/nodeinfo`, `/nodestatus`, `/nodesetup` |
| **VPS (LXC)** | `/deploy` (dedicated RAM/CPU/disk), `/renew`, `/deletevps`, `/suspendvps`, `/unsuspendvps`, `/myvps`, `/vpsinfo`, `/execvps` |
| **Admins** | Root admins (IDs `1329035198649864202`, `1352328694965735635`) hold **all** perms. `/adminadd` grants a custom perm set, `/adminremove`, `/adminperms`, `/admins`, `/adminlist` |
| **Invite rewards** | `/invitesetup` (per-node RAM/disk/container rewards), `/invitegui` (clean dashboard), `/invites`, `/inviteclaim` |
| **Profile clone** | Mirrors the source account's **status / presence / bio**; shows `BOT V8` when the bio is private |
| **MAS Anti-Scam** | Perceptual-hash image matching with tiered enforcement (ban / kick / delete / report) |

---

## 🧩 Admin permission model

Root admins always have every permission. Other admins get an explicit set:

`create`, `delete`, `suspend`, `renew`, `node`, `admin`, `invite`, `scam`, `exec`, `view`

```
/adminadd  user:@Bob  perms:create,delete      # only create + delete
/adminadd  user:@Bob                            # opens a GUI perm picker
/adminperms user:@Bob perms:all                 # full access
/adminremove user:@Bob
```

---

## 🚀 Quick start (bot host)

```bash
git clone <your-repo> cryzoncloud && cd cryzoncloud
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env → set DISCORD_TOKEN, DEFAULT_ADMINS, etc.

python cryzoncloud.py
```

### Required privileged intents

Enable these in the [Discord Developer Portal](https://discord.com/developers/applications)
→ *Bot* → **Privileged Gateway Intents**:

- ✅ Server Members Intent
- ✅ Presence Intent
- ✅ Message Content Intent

### Install LXD on the bot host (for the local node)

```bash
sudo apt update && sudo apt install -y lxd
sudo lxd init --auto
sudo usermod -aG lxd $USER
newgrp lxd          # or log out / back in
lxc launch ubuntu:24.04 test --config limits.memory=2GB
lxc list
lxc config get test limits.memory
lxc delete test --force
```

---

## 🖥️ Adding a cloud node

### 1. In Discord

```
/createnode
```
Enter the name, location, capacity and RAM/disk totals, then choose **Cloudflare
Agent**. Provide your tunnel hostname (e.g. `node1.example.com`). The bot prints
the **API key** and the exact setup commands.

### 2. On the cloud node (Ubuntu/Debian)

```bash
# --- Install LXD ---
sudo apt update && sudo apt install -y lxd curl python3 python3-pip
sudo lxd init --auto
sudo usermod -aG lxd $USER

# --- Install the node agent ---
sudo mkdir -p /opt/cryzoncloud && cd /opt/cryzoncloud
# copy node_agent.py here (scp, git, etc.)
pip3 install flask

# --- Run the agent ---
CRYZON_API_KEY="<api-key-from-/createnode>" CRYZON_PORT=8787 python3 node_agent.py &
```

### 3. Expose it with a Cloudflare Tunnel

```bash
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
     -o /usr/local/bin/cloudflared && sudo chmod +x /usr/local/bin/cloudflared

cloudflared tunnel login
cloudflared tunnel create cryzon-node
cloudflared tunnel route dns cryzon-node node1.example.com
cloudflared tunnel run --url http://localhost:8787 cryzon-node
```

### 4. One-shot script

```bash
sudo CRYZON_API_KEY="<api-key>" CRYZON_TUNNEL="node1.example.com" bash node_setup.sh
```

The agent installs a `cryzoncloud-agent` systemd service so it survives reboots.

---

## 🐳 Useful LXC day-to-day commands

```bash
lxc list                                   # list containers
lxc launch ubuntu:24.04 my-container --config limits.memory=2GB
lxc config get my-container limits.memory  # verify RAM limit
lxc config set my-container limits.memory=4GB
lxc exec my-container -- bash              # shell access
lxc stop  my-container
lxc start my-container
lxc delete my-container --force
```

---

## 🎁 Invite rewards

```
/invitesetup     # pick a node → set invites/reward, RAM, disk, max containers
/invitegui       # clean dashboard: tiers, your invites, leaderboard
/inviteclaim node:<id>
```

Invites are tracked automatically via the invite cache (`on_member_join`).

---

## 🛡️ MAS Anti-Scam Imagine System

Register reference scam images (e.g. the MrBeast scam):

```
/addscamimage image:<attachment> label:mrbeast-scam
```

Incoming images are hashed (pHash/dHash/wHash/aHash) and compared. Enforcement:

| Match | Action |
| --- | --- |
| **90–100 %** | 🔨 **Ban** + DM the rejoin invite (`https://discord.gg/mFj88abMUu`) |
| **70–89 %** | 👢 **Kick** + DM the rejoin invite |
| **60–69 %** | 🗑️ **Delete** the message |
| **40–59 %** | 🚨 **Report** to channel `1477994759162298388` with a screenshot, jump link and match % |
| **< 40 %** | Ignore |

Management: `/scamimages`, `/removescamimage <id>`, `/cleanscamimages`.

---

## 🗂️ Project layout

```
cryzoncloud.py              # entry point
node_agent.py               # runs on cloud nodes
node_setup.sh               # cloud node provisioning script
requirements.txt
.env.example
cryzoncloud/
  config.py                 # env-driven configuration
  database.py               # SQLite schema + helpers
  permissions.py            # granular permission model
  embeds.py                 # UI helpers
  executor.py               # local / SSH / agent execution + LXC helpers
  antiscam.py               # perceptual-hash matching engine
  profile.py                # presence/status/bio cloning
  checks.py                 # slash-command permission checks
  views.py                  # reusable UI components
  cogs/
    admin.py                # /adminadd /adminremove /admins /adminlist /adminperms
    nodes.py                # /createnode /deletenode /nodes /nodeinfo /nodestatus
    vps.py                  # /deploy /renew /deletevps /suspendvps /unsuspendvps ...
    invites.py              # /invitesetup /invitegui /invites /inviteclaim
    antiscam_cog.py         # anti-scam listener + management
```

---

## 🔐 Notes

- Root admin IDs live in `.env` (`DEFAULT_ADMINS`) and are seeded on first boot.
- The profile clone mirrors **status, presence and bio** only — never the username.
- When the source account's bio is private, the bot displays **`BOT V8`**.
