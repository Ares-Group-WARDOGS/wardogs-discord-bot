import discord
from discord import app_commands

from ares.database import get_admin_role_id


class AresAdminRequired(app_commands.CheckFailure):
    """Raised when a member lacks Ares administrative access."""


class DiscordAdministratorRequired(app_commands.CheckFailure):
    """Raised when a member is not a Discord server administrator."""


def is_discord_administrator(
    member: discord.Member,
) -> bool:
    """Return whether a member is the owner or a Discord administrator."""

    if member.guild.owner_id == member.id:
        return True

    return member.guild_permissions.administrator


def is_ares_admin_member(
    member: discord.Member,
) -> bool:
    """Return whether a member has administrative access to Ares."""

    if is_discord_administrator(
        member
    ):
        return True

    admin_role_id = get_admin_role_id(
        member.guild.id
    )

    if admin_role_id is None:
        return False

    return any(
        role.id == admin_role_id
        for role in member.roles
    )


def ares_admin_only():
    """Require the configured Ares Admin role or Discord Administrator."""

    async def predicate(
        interaction: discord.Interaction,
    ) -> bool:
        member = interaction.user

        if not isinstance(
            member,
            discord.Member,
        ):
            raise AresAdminRequired()

        if is_ares_admin_member(
            member
        ):
            return True

        raise AresAdminRequired()

    return app_commands.check(
        predicate
    )


def discord_admin_only():
    """Require the server owner or Discord Administrator permission."""

    async def predicate(
        interaction: discord.Interaction,
    ) -> bool:
        member = interaction.user

        if not isinstance(
            member,
            discord.Member,
        ):
            raise DiscordAdministratorRequired()

        if is_discord_administrator(
            member
        ):
            return True

        raise DiscordAdministratorRequired()

    return app_commands.check(
        predicate
    )
