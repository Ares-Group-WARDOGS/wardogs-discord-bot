import discord
from discord.ext import commands

from ares.config import (
    GUILD,
    GUILD_ID,
    TOKEN,
    WARDOGS_LAUNCH_UNIX,
)
from ares.database import (
    initialize_database,
    load_command_prefixes,
)
from ares.prefixes import resolve_command_prefix

EXTENSIONS = (
    "ares.cogs.announcements",
    "ares.cogs.countdown",
    "ares.cogs.rolepanel",
    "ares.cogs.join_to_create",
    "ares.cogs.logging.deleted_messages",
    "ares.cogs.logging.moderator_actions",
    "ares.cogs.logging.server_leaves",
    "ares.cogs.tickets",
    "ares.cogs.warcon_stats",
    "ares.cogs.server_status_embed",

    "ares.cogs.slash.admin",
    "ares.cogs.slash.help",
    "ares.cogs.slash.logs",
    "ares.cogs.slash.prefix",
    "ares.cogs.slash.roster",
    "ares.cogs.slash.rolepanelconfig",
    "ares.cogs.slash.welcome",
    "ares.cogs.slash.bumpreminder",
)


class AresBot(commands.Bot):
    """Main Ares Group Discord bot."""

    def __init__(self):
        intents = discord.Intents.default()

        # Required for !announce and !rolepanel.
        intents.message_content = True

        # Required for member welcome events.
        intents.members = True

        self.prefixes: dict[int, str] = {}

        super().__init__(
            command_prefix=resolve_command_prefix,
            intents=intents,
        )

    async def setup_hook(self):
        """Initialize storage, load features, and sync commands."""

        initialize_database()

        self.prefixes = load_command_prefixes()

        for extension in EXTENSIONS:
            await self.load_extension(extension)

            print(
                f"Loaded extension: {extension}"
            )

        # Keep slash commands guild-specific so changes
        # appear immediately in the Ares server.
        self.tree.copy_global_to(
            guild=GUILD
        )

        synced_commands = await self.tree.sync(
            guild=GUILD
        )

        print(
            f"Synced {len(synced_commands)} slash command(s) "
            f"to guild {GUILD_ID}"
        )

    async def on_ready(self):
        """Report successful Discord connection."""

        print("=" * 60)
        print(f"Logged in as: {self.user}")

        if self.user is not None:
            print(f"Bot ID: {self.user.id}")

        print(f"Guild ID: {GUILD_ID}")
        print(
            "WARDOGS launch Unix timestamp: "
            f"{WARDOGS_LAUNCH_UNIX}"
        )
        print("=" * 60)


def run_bot():
    """Start the Ares bot."""

    bot = AresBot()
    bot.run(TOKEN)