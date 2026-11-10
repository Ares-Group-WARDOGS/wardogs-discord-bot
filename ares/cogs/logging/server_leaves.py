import asyncio

import discord
from discord.ext import commands

from ares.config import ARES_RED
from ares.database import get_log_channel


class ServerLeavesCog(commands.Cog):
    """Log members leaving the server."""

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_remove(
        self,
        member: discord.Member,
    ):
        """Log a member leaving the guild."""

        await asyncio.sleep(1)

        moderated_members = getattr(
            self.bot,
            "recent_moderated_departures",
            set(),
        )

        if member.id in moderated_members:
            moderated_members.discard(
                member.id
            )
            return

        channel_id = get_log_channel(
            member.guild.id,
            "server_leaves",
        )

        if channel_id is None:
            return

        channel = member.guild.get_channel(
            channel_id
        )

        if not isinstance(
            channel,
            discord.TextChannel,
        ):
            return

        embed = discord.Embed(
            title="ARES // MEMBER LEFT",
            description=member.mention,
            color=ARES_RED,
        )

        embed.add_field(
            name="User",
            value=(
                f"{member}\n"
                f"`{member.id}`"
            ),
            inline=False,
        )

        embed.timestamp = discord.utils.utcnow()

        try:
            await channel.send(
                embed=embed
            )

        except discord.HTTPException as error:
            print(
                "ERROR sending member-leave log: "
                f"{error}"
            )


async def setup(
    bot: commands.Bot,
):
    """Load server-leave logging."""

    await bot.add_cog(
        ServerLeavesCog(bot)
    )