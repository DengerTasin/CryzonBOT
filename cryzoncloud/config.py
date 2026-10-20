"""Central configuration for CryzonCloud.

All tunables are read from the environment (``.env``) with safe defaults so the
bot boots even before the operator finishes configuring everything.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# --------------------------------------------------------------------------- #
# Identity / branding
# --------------------------------------------------------------------------- #
BOT_NAME = os.getenv("BOT_NAME", "CryzonCloud")
BOT_VERSION = os.getenv("BOT_VERSION", "1.0.0")
BOT_TAGLINE = os.getenv("BOT_TAGLINE", "Cloud VPS Control Plane")
BOT_DEVELOPER = os.getenv("BOT_DEVELOPER", "CryzonCloud Team")
PREFIX = os.getenv("PREFIX", "!")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

# Brand assets (override in .env with your own URLs)
BOT_ICON_URL = os.getenv(
    "BOT_ICON_URL",
    "https://i.postimg.cc/XYsyy94s/file-000000008e748211ae382f76fb5cd51b.png",
)
BOT_THUMBNAIL_URL = os.getenv("BOT_THUMBNAIL_URL", BOT_ICON_URL)
BOT_BANNER_URL = os.getenv("BOT_BANNER_URL", BOT_ICON_URL)

# --------------------------------------------------------------------------- #
# Admins & permissions
# --------------------------------------------------------------------------- #
# These two IDs are the *root* admins. They always hold every permission and
# cannot be removed through the bot.
DEFAULT_ADMINS = [
    int(x)
    for x in os.getenv(
        "DEFAULT_ADMINS", "1329035198649864202,1352328694965735635"
    ).split(",")
    if x.strip().isdigit()
]

# --------------------------------------------------------------------------- #
# Profile cloning
# --------------------------------------------------------------------------- #
# The bot mirrors this account's presence, status and bio.
PROFILE_SOURCE_ID = int(os.getenv("PROFILE_SOURCE_ID") or "1329035198649864202")
# Shown as the "bio" when the source account's real bio is private/unavailable.
PROFILE_FALLBACK_BIO = os.getenv("PROFILE_FALLBACK_BIO", "BOT V8")
PROFILE_CLONE_ENABLED = os.getenv("PROFILE_CLONE_ENABLED", "true").lower() == "true"
PROFILE_CLONE_INTERVAL = int(os.getenv("PROFILE_CLONE_INTERVAL") or "60")  # seconds

# --------------------------------------------------------------------------- #
# MAS Anti-Scam Imagine System
# --------------------------------------------------------------------------- #
ANTISCAM_ENABLED = os.getenv("ANTISCAM_ENABLED", "true").lower() == "true"
ANTISCAM_REPORT_CHANNEL = int(
    os.getenv("ANTISCAM_REPORT_CHANNEL") or "1477994759162298388"
)
ANTISCAM_INVITE = os.getenv("ANTISCAM_INVITE", "https://discord.gg/mFj88abMUu")
# Similarity thresholds (percent)
ANTISCAM_BAN_THRESHOLD = int(os.getenv("ANTISCAM_BAN_THRESHOLD") or "90")
ANTISCAM_KICK_THRESHOLD = int(os.getenv("ANTISCAM_KICK_THRESHOLD") or "70")
ANTISCAM_DELETE_THRESHOLD = int(os.getenv("ANTISCAM_DELETE_THRESHOLD") or "60")
ANTISCAM_REPORT_THRESHOLD = int(os.getenv("ANTISCAM_REPORT_THRESHOLD") or "40")
# Ignore matches below this (noise floor)
ANTISCAM_MIN_MATCH = int(os.getenv("ANTISCAM_MIN_MATCH") or "40")

# --------------------------------------------------------------------------- #
# VPS defaults
# --------------------------------------------------------------------------- #
DEFAULT_VPS_EXPIRATION_DAYS = int(os.getenv("DEFAULT_VPS_EXPIRATION_DAYS") or "30")
DEFAULT_STORAGE_POOL = os.getenv("DEFAULT_STORAGE_POOL", "default")
DEFAULT_OS_IMAGE = os.getenv("DEFAULT_OS_IMAGE", "ubuntu:24.04")
MAX_VPS_PER_USER = int(os.getenv("MAX_VPS_PER_USER") or "3")
MAX_RAM_PER_VPS = int(os.getenv("MAX_RAM_PER_VPS") or "8")     # GB
MAX_CPU_PER_VPS = int(os.getenv("MAX_CPU_PER_VPS") or "4")
MAX_DISK_PER_VPS = int(os.getenv("MAX_DISK_PER_VPS") or "80")  # GB

# Container image catalogue
OS_IMAGES = {
    "Ubuntu 24.04 LTS": "ubuntu:24.04",
    "Ubuntu 22.04 LTS": "ubuntu:22.04",
    "Ubuntu 20.04 LTS": "ubuntu:20.04",
    "Debian 12 (Bookworm)": "images:debian/12",
    "Debian 11 (Bullseye)": "images:debian/11",
    "Debian 10 (Buster)": "images:debian/10",
    "Alpine 3.20": "images:alpine/3.20",
    "AlmaLinux 9": "images:almalinux/9",
    "Rocky Linux 9": "images:rockylinux/9",
}

# --------------------------------------------------------------------------- #
# Nodes / Cloudflare
# --------------------------------------------------------------------------- #
NODE_AGENT_PORT = int(os.getenv("NODE_AGENT_PORT") or "8787")
NODE_DEFAULT_CAPACITY = int(os.getenv("NODE_DEFAULT_CAPACITY") or "20")
CLOUDFLARE_API_BASE = os.getenv("CLOUDFLARE_API_BASE", "https://api.cloudflare.com/client/v4")

# --------------------------------------------------------------------------- #
# Local node (the host the bot itself runs on)
# --------------------------------------------------------------------------- #
LOCAL_NODE_ENABLED = os.getenv("LOCAL_NODE_ENABLED", "true").lower() == "true"
LOCAL_NODE_NAME = os.getenv("LOCAL_NODE_NAME", "CryzonCloud-Local")

# --------------------------------------------------------------------------- #
# Database / runtime
# --------------------------------------------------------------------------- #
DB_PATH = os.getenv("DB_PATH", "cryzoncloud.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# --------------------------------------------------------------------------- #
# Colours
# --------------------------------------------------------------------------- #
COLOR_PRIMARY = 0x5865F2      # blurple
COLOR_SUCCESS = 0x2ECC71      # green
COLOR_ERROR = 0xED4245        # red
COLOR_WARNING = 0xFEE75C      # yellow
COLOR_INFO = 0x3498DB         # blue
COLOR_DARK = 0x1E1F22         # near-black
COLOR_CRYZON = 0x00D4FF       # cyan brand
COLOR_DANGER = 0x992D22       # dark red

FOOTER_TEXT = f"{BOT_NAME} • {BOT_TAGLINE}"
