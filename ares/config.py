import os

import discord


# ============================================================
# DISCORD
# ============================================================

TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = int(os.environ["DISCORD_GUILD_ID"])

GUILD = discord.Object(id=GUILD_ID)


# ============================================================
# WARDOGS LAUNCH
# ============================================================

DEFAULT_WARDOGS_LAUNCH_UNIX = 1789056000

WARDOGS_LAUNCH_UNIX = int(
    os.getenv(
        "WARDOGS_LAUNCH_UNIX",
        str(DEFAULT_WARDOGS_LAUNCH_UNIX),
    )
)

if WARDOGS_LAUNCH_UNIX <= 0:
    WARDOGS_LAUNCH_UNIX = DEFAULT_WARDOGS_LAUNCH_UNIX


# ============================================================
# VISUAL CONFIGURATION
# ============================================================

ARES_RED = discord.Color.from_rgb(
    243,
    13,
    23,
)
# ============================================================
# COMMAND PREFIX
# ============================================================

DEFAULT_COMMAND_PREFIX = "!"

# ============================================================
# COUNTDOWN CHANNEL
# ============================================================

COUNTDOWN_PREFIX = "🔴"
COUNTDOWN_SUFFIX = "until launch"

COUNTDOWN_LIVE_NAME = (
    f"{COUNTDOWN_PREFIX} WARDOGS IS LIVE"
)


# ============================================================
# DATABASE
# ============================================================

ROLE_DB_PATH = "/app/data/ares.db"

# ============================================================
# DISBOARD REMINDER
# ============================================================

DISBOARD_BOT_ID = 302050872383242240
BUMP_REMINDER_SECONDS = 7200

# ============================================================
# CHANNELS
# ============================================================

# Channel for welcome messages (Discord channel ID). 0 = not set.
GENERAL_CHANNEL_ID = int(os.getenv("GENERAL_CHANNEL_ID", "0"))