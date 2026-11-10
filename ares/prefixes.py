import discord
from discord.ext import commands

from ares.config import DEFAULT_COMMAND_PREFIX


def get_guild_prefix(
    bot: commands.Bot,
    guild_id: int | None,
) -> str:
    """Return the configured prefix for a guild."""

    if guild_id is None:
        return DEFAULT_COMMAND_PREFIX

    prefixes = getattr(
        bot,
        "prefixes",
        {},
    )

    return prefixes.get(
        guild_id,
        DEFAULT_COMMAND_PREFIX,
    )


def resolve_command_prefix(
    bot: commands.Bot,
    message: discord.Message,
) -> str:
    """Resolve the prefix for an incoming Discord message."""

    guild_id = (
        message.guild.id
        if message.guild is not None
        else None
    )

    return get_guild_prefix(
        bot,
        guild_id,
    )