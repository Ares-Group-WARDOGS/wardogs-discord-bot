import discord
from discord import app_commands
from discord.ext import commands

from ares.config import DEFAULT_COMMAND_PREFIX
from ares.database import set_command_prefix
from ares.permissions import AresAdminRequired, ares_admin_only


class PrefixCog(commands.Cog):
    """Configure the Ares text-command prefix."""

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

    @app_commands.command(
        name="setprefix",
        description="Change the Ares text-command prefix.",
    )
    @app_commands.describe(
        prefix=(
            "New command prefix. "
            "Examples: !, ?, $, ag!"
        )
    )
    @ares_admin_only()
    async def set_prefix(
        self,
        interaction: discord.Interaction,
        prefix: str,
    ):
        """Change the command prefix for this server."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        prefix = prefix.strip()

        if not prefix:
            await interaction.response.send_message(
                "The prefix cannot be empty.",
                ephemeral=True,
            )
            return

        if len(prefix) > 5:
            await interaction.response.send_message(
                "The prefix cannot be longer than 5 characters.",
                ephemeral=True,
            )
            return

        if any(
            character.isspace()
            for character in prefix
        ):
            await interaction.response.send_message(
                "The prefix cannot contain spaces.",
                ephemeral=True,
            )
            return

        if prefix.startswith("/"):
            await interaction.response.send_message(
                (
                    "The prefix cannot begin with `/` because "
                    "Discord reserves it for slash commands."
                ),
                ephemeral=True,
            )
            return

        set_command_prefix(
            interaction.guild.id,
            prefix,
        )

        prefixes = getattr(
            self.bot,
            "prefixes",
            None,
        )

        if prefixes is not None:
            prefixes[
                interaction.guild.id
            ] = prefix

        await interaction.response.send_message(
            (
                f"Command prefix changed to `{prefix}`.\n\n"
                f"Example: `{prefix}announce <message>`"
            ),
            ephemeral=True,
        )

    @set_prefix.error
    async def set_prefix_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        """Handle /setprefix permission errors."""

        if isinstance(
            error,
            AresAdminRequired,
        ):
            await interaction.response.send_message(
                (
                    "You need Ares administrative access "
                    "to change the command prefix."
                ),
                ephemeral=True,
            )
            return

        raise error


async def setup(
    bot: commands.Bot,
):
    """Load the prefix configuration cog."""

    await bot.add_cog(
        PrefixCog(bot)
    )
