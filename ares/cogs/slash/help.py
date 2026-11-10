import discord
from discord import app_commands
from discord.ext import commands

from ares.config import ARES_RED
from ares.prefixes import get_guild_prefix
from ares.permissions import (
    is_ares_admin_member,
    is_discord_administrator,
)


class HelpCog(commands.Cog):
    """Display Ares bot command information."""

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

    @app_commands.command(
        name="help",
        description="Display Ares Group bot commands.",
    )
    async def help_command(
        self,
        interaction: discord.Interaction,
    ):
        """Display commands available through Ares."""

        guild_id = (
            interaction.guild.id
            if interaction.guild is not None
            else None
        )

        prefix = get_guild_prefix(
            self.bot,
            guild_id,
        )

        embed = discord.Embed(
            title="ARES GROUP // COMMAND DIRECTORY",
            description=(
                "Available Ares Group bot commands and utilities."
            ),
            color=ARES_RED,
        )

        # ====================================================
        # GENERAL
        # ====================================================

        embed.add_field(
            name="GENERAL",
            value=(
                "**/help**\n"
                "Display this command directory."
            ),
            inline=False,
        )

        member = (
            interaction.user
            if isinstance(
                interaction.user,
                discord.Member,
            )
            else None
        )

        # ====================================================
        # ANNOUNCEMENTS
        # ====================================================

        if (
            member is not None
            and member.guild_permissions.manage_messages
        ):
            embed.add_field(
                name="ANNOUNCEMENTS",
                value=(
                    f"**{prefix}announce <message>**\n"
                    "Post an Ares Group announcement. "
                    "Images attached to the command message "
                    "are included in the announcement."
                ),
                inline=False,
            )

        # ====================================================
        # ROLE PANELS
        # ====================================================

        if (
            member is not None
            and member.guild_permissions.manage_roles
        ):
            embed.add_field(
                name="ROLE PANELS",
                value=(
                    f"**{prefix}rolepanel create**\n"
                    "Create a persistent reaction-role panel.\n\n"
                    "**Format:**\n"
                    "```text\n"
                    f"{prefix}rolepanel create\n\n"
                    "PANEL TITLE\n\n"
                    "Panel description.\n\n"
                    "Role Name: EMOJI ROLE_ID\n"
                    "Role Name: EMOJI ROLE_ID\n\n"
                    "EOF\n"
                    "```"
                ),
                inline=False,
            )

        # ====================================================
        # SERVER CONFIGURATION
        # ====================================================

        if (
            member is not None
            and is_ares_admin_member(
                member
            )
        ):
            embed.add_field(
                name="SERVER CONFIGURATION",
                value=(
                    "**/logs**\n"
                    "Configure Ares logging destinations.\n\n"
                    "**/setwelcomechannel**\n"
                    "Enable, disable, or change member welcomes.\n\n"
                    "**/setwelcomebackground / clearwelcomebackground**\n"
                    "Set or remove the image behind welcome avatars.\n\n"
                    "**/setprefix**\n"
                    "Change the prefix used for Ares text commands.\n\n"
                    "**/bumpreminder**\n"
                    "Enable or disable DISBOARD bump reminders.\n\n"
                    "**/countdown create / remove / status**\n"
                    "Create and manage locked voice-channel countdowns.\n\n"
                    "**/adminstatus**\n"
                    "Show the configured Ares Admin role.\n\n"
                    "**/roster create / add / remove / refresh / status**\n"
                    "Create and maintain the persistent combat-role roster.\n\n"
                    "**/rolepanel group-create / group-add / group-limit**\n"
                    "**/rolepanel group-remove / group-delete / group-status**\n"
                    "Configure selection limits for reaction-role groups."
                ),
                inline=False,
            )

        if (
            member is not None
            and is_discord_administrator(
                member
            )
        ):
            embed.add_field(
                name="ARES ACCESS CONTROL",
                value=(
                    "**/setadminrole**\n"
                    "Set the role allowed to administer Ares.\n\n"
                    "**/clearadminrole**\n"
                    "Remove the configured Ares Admin role."
                ),
                inline=False,
            )

        embed.set_footer(
            text="ARES GROUP // ADMINISTRATION"
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )


async def setup(
    bot: commands.Bot,
):
    """Load the help cog."""

    await bot.add_cog(
        HelpCog(bot)
    )
