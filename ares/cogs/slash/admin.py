import discord
from discord import app_commands
from discord.ext import commands

from ares.database import (
    clear_admin_role_id,
    get_admin_role_id,
    set_admin_role_id,
)
from ares.permissions import (
    AresAdminRequired,
    DiscordAdministratorRequired,
    ares_admin_only,
    discord_admin_only,
)


class AdminCog(commands.Cog):
    """Configure the Ares administrative permission role."""

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

    @app_commands.command(
        name="setadminrole",
        description="Set the role allowed to administer Ares.",
    )
    @app_commands.describe(
        role="Role that should receive Ares administrative access.",
    )
    @app_commands.default_permissions(
        administrator=True
    )
    @discord_admin_only()
    async def set_admin_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ):
        """Set the Ares Admin role."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        if role.is_default():
            await interaction.response.send_message(
                "The `@everyone` role cannot be the Ares Admin role.",
                ephemeral=True,
            )
            return

        if role.managed:
            await interaction.response.send_message(
                (
                    "That role is managed by Discord, a bot, or an "
                    "integration and cannot be used as the Ares Admin role."
                ),
                ephemeral=True,
            )
            return

        set_admin_role_id(
            interaction.guild.id,
            role.id,
        )

        await interaction.response.send_message(
            (
                "Ares administrative role updated.\n\n"
                f"Role: {role.mention}\n"
                f"Role ID: `{role.id}`\n\n"
                "Members with this role can now use Ares "
                "configuration commands."
            ),
            ephemeral=True,
        )

    @set_admin_role.error
    async def set_admin_role_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        """Handle /setadminrole permission errors."""

        if isinstance(
            error,
            DiscordAdministratorRequired,
        ):
            await interaction.response.send_message(
                (
                    "Only the server owner or a member with Discord's "
                    "**Administrator** permission can change the "
                    "Ares Admin role."
                ),
                ephemeral=True,
            )
            return

        raise error

    @app_commands.command(
        name="clearadminrole",
        description="Remove the configured Ares Admin role.",
    )
    @app_commands.default_permissions(
        administrator=True
    )
    @discord_admin_only()
    async def clear_admin_role(
        self,
        interaction: discord.Interaction,
    ):
        """Clear the Ares Admin role."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        current_role_id = get_admin_role_id(
            interaction.guild.id
        )

        if current_role_id is None:
            await interaction.response.send_message(
                "No Ares Admin role is currently configured.",
                ephemeral=True,
            )
            return

        clear_admin_role_id(
            interaction.guild.id
        )

        await interaction.response.send_message(
            (
                "The Ares Admin role has been cleared.\n\n"
                "Until another role is configured, only the server "
                "owner and Discord Administrators can use protected "
                "Ares configuration commands."
            ),
            ephemeral=True,
        )

    @clear_admin_role.error
    async def clear_admin_role_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        """Handle /clearadminrole permission errors."""

        if isinstance(
            error,
            DiscordAdministratorRequired,
        ):
            await interaction.response.send_message(
                (
                    "Only the server owner or a member with Discord's "
                    "**Administrator** permission can clear the "
                    "Ares Admin role."
                ),
                ephemeral=True,
            )
            return

        raise error

    @app_commands.command(
        name="adminstatus",
        description="Show the current Ares Admin role.",
    )
    @ares_admin_only()
    async def admin_status(
        self,
        interaction: discord.Interaction,
    ):
        """Display the current Ares administrative role."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        role_id = get_admin_role_id(
            interaction.guild.id
        )

        if role_id is None:
            await interaction.response.send_message(
                (
                    "**Ares Admin Role:** Not configured\n\n"
                    "Only the server owner and Discord Administrators "
                    "currently have Ares administrative access."
                ),
                ephemeral=True,
            )
            return

        role = interaction.guild.get_role(
            role_id
        )

        if role is None:
            await interaction.response.send_message(
                (
                    "**Ares Admin Role:** Configured role no longer exists\n"
                    f"Stored Role ID: `{role_id}`\n\n"
                    "A Discord Administrator should run `/setadminrole` "
                    "to configure a replacement."
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            (
                "**Ares Admin Role**\n\n"
                f"Role: {role.mention}\n"
                f"Role ID: `{role.id}`"
            ),
            ephemeral=True,
        )

    @admin_status.error
    async def admin_status_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        """Handle /adminstatus permission errors."""

        if isinstance(
            error,
            AresAdminRequired,
        ):
            await interaction.response.send_message(
                "You do not have Ares administrative access.",
                ephemeral=True,
            )
            return

        raise error


async def setup(
    bot: commands.Bot,
):
    """Load Ares administrative configuration."""

    await bot.add_cog(
        AdminCog(bot)
    )
