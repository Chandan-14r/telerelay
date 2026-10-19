"""
Configuration for Telegram Downloader.

STEP 1 — Get API_ID and API_HASH (required for BOTH user and bot mode):
  1. Go to https://my.telegram.org
  2. Log in with your phone number
  3. Click on "API development tools"
  4. Create a new application (if you haven't already)
  5. Copy your api_id and api_hash

STEP 2 — Choose an authentication mode:

  Mode A (User Account):
    Leave BOT_TOKEN empty. On first run, Telethon will ask for your
    phone number and login code. Your own account is used.

  Mode B (Bot):
    Set BOT_TOKEN to the token from @BotFather. The bot must be added
    to the target chat/channel (usually as admin) to access messages.
"""

import os

# Try to load local .env file if available
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

# ── Required credentials ──
API_ID = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")

# ── Optional bot mode ──
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# ── Persistent String Session (keeps you logged in across restarts & on cloud/Render) ──
STRING_SESSION = os.environ.get("TELEGRAM_STRING_SESSION", "")

# Session file name (stores your login session so you don't have to re-auth)
SESSION_NAME = "tg_downloader_session"

# Default download directory
DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
