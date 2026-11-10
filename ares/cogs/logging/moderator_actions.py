import asyncio
from datetime import timedelta

import discord
from discord.ext import commands

from ares.config import ARES_RED
from ares.database import get_log_channel


class ModeratorActionsCog(commands.Cog):
    """Log moderation actions performed in the server."""

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

        if not hasattr(
            self.bot,
            "recent_moderated_departures",
        ):
            self.bot.recent_moderated_departures = set()

    async def get_log_channel(
        self,
        guild: discord.Guild,
    ) -> discord.TextChannel | None:
        """Return the configured moderator log channel."""

        channel_id = get_log_channel(
            guild.id,
            "moderator_actions",
        )

        if channel_id is None:
            return None

        channel = guild.get_channel(
            channel_id
        )

        if not isinstance(
            channel,
            discord.TextChannel,
        ):
            return None

        return channel

    async def find_recent_audit_entry(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
        target_id: int,
    ) -> discord.AuditLogEntry | None:
        """Find a recent audit entry for a target."""

        try:
            async for entry in guild.audit_logs(
                limit=10,
                action=action,
            ):
                target = entry.target

                if target is None:
                    continue

                if getattr(
                    target,
                    "id",
                    None,
                ) != target_id:
                    continue

                age = (
                    discord.utils.utcnow()
                    - entry.created_at
                )

                if age <= timedelta(
                    seconds=15
                ):
                    return entry

        except discord.Forbidden:
            return None

        except discord.HTTPException:
            return None

        return None

    async def send_action_log(
        self,
        guild: discord.Guild,
        title: str,
        member_text: str,
        moderator: discord.abc.User | None = None,
        reason: str | None = None,
    ):
        """Send a standardized moderator-action log."""

        channel = await self.get_log_channel(
            guild
        )

        if channel is None:
            return

        embed = discord.Embed(
            title=title,
            color=ARES_RED,
        )

        embed.add_field(
            name="Member",
            value=member_text,
            inline=False,
        )

        if moderator is not None:
            embed.add_field(
                name="Moderator",
                value=(
                    f"{moderator.mention}\n"
                    f"`{moderator.id}`"
                ),
                inline=False,
            )

        else:
            embed.add_field(
                name="Moderator",
                value="*Unavailable*",
                inline=False,
            )

        if reason:
            embed.add_field(
                name="Reason",
                value=reason,
                inline=False,
            )

        embed.timestamp = discord.utils.utcnow()

        try:
            await channel.send(
                embed=embed
            )

        except discord.HTTPException as error:
            print(
                "ERROR sending moderator-action log: "
                f"{error}"
            )

    # ========================================================
    # BAN
    # ========================================================

    @commands.Cog.listener()
    async def on_member_ban(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
    ):
        """Log a member ban."""

        self.bot.recent_moderated_departures.add(
            user.id
        )

        await asyncio.sleep(
            1
        )

        entry = await self.find_recent_audit_entry(
            guild,
            discord.AuditLogAction.ban,
            user.id,
        )

        moderator = (
            entry.user
            if entry is not None
            else None
        )

        reason = (
            entry.reason
            if entry is not None
            else None
        )

        await self.send_action_log(
            guild,
            "ARES // MEMBER BANNED",
            (
                f"{user.mention}\n"
                f"`{user.id}`"
            ),
            moderator,
            reason,
        )

    # ========================================================
    # UNBAN
    # ========================================================

    @commands.Cog.listener()
    async def on_member_unban(
        self,
        guild: discord.Guild,
        user: discord.User,
    ):
        """Log a member unban."""

        await asyncio.sleep(
            1
        )

        entry = await self.find_recent_audit_entry(
            guild,
            discord.AuditLogAction.unban,
            user.id,
        )

        moderator = (
            entry.user
            if entry is not None
            else None
        )

        reason = (
            entry.reason
            if entry is not None
            else None
        )

        await self.send_action_log(
            guild,
            "ARES // MEMBER UNBANNED",
            (
                f"{user.mention}\n"
                f"`{user.id}`"
            ),
            moderator,
            reason,
        )

    # ========================================================
    # KICK
    # ========================================================

    @commands.Cog.listener()
    async def on_member_remove(
        self,
        member: discord.Member,
    ):
        """Detect and log kicks through the audit log."""

        await asyncio.sleep(
            1
        )

        entry = await self.find_recent_audit_entry(
            member.guild,
            discord.AuditLogAction.kick,
            member.id,
        )

        if entry is None:
            return

        self.bot.recent_moderated_departures.add(
            member.id
        )

        await self.send_action_log(
            member.guild,
            "ARES // MEMBER KICKED",
            (
                f"{member.mention}\n"
                f"`{member.id}`"
            ),
            entry.user,
            entry.reason,
        )

    # ========================================================
    # TIMEOUT / MUTE
    # ========================================================

    @commands.Cog.listener()
    async def on_member_update(
        self,
        before: discord.Member,
        after: discord.Member,
    ):
        """Log timeout changes."""

        if (
            before.timed_out_until
            == after.timed_out_until
        ):
            return

        now = discord.utils.utcnow()

        before_timeout = (
            before.timed_out_until is not None
            and before.timed_out_until > now
        )

        after_timeout = (
            after.timed_out_until is not None
            and after.timed_out_until > now
        )

        if (
            not before_timeout
            and after_timeout
        ):
            title = "ARES // MEMBER TIMED OUT"

        elif (
            before_timeout
            and not after_timeout
        ):
            title = "ARES // TIMEOUT REMOVED"

        else:
            title = "ARES // TIMEOUT CHANGED"

        await asyncio.sleep(
            1
        )

        entry = await self.find_recent_audit_entry(
            after.guild,
            discord.AuditLogAction.member_update,
            after.id,
        )

        moderator = (
            entry.user
            if entry is not None
            else None
        )

        reason = (
            entry.reason
            if entry is not None
            else None
        )

        member_text = (
            f"{after.mention}\n"
            f"`{after.id}`"
        )

        if after_timeout:
            member_text += (
                "\n\n"
                "Timeout expires: "
                f"<t:{int(after.timed_out_until.timestamp())}:F>"
            )

        await self.send_action_log(
            after.guild,
            title,
            member_text,
            moderator,
            reason,
        )


async def setup(
    bot: commands.Bot,
):
    """Load moderator-action logging."""

    await bot.add_cog(
        ModeratorActionsCog(bot)
    )