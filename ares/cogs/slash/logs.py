import sqlite3
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ares.config import ARES_RED, ROLE_DB_PATH
from ares.database import (
    get_log_channel,
    set_log_channel,
)


VOICE_LOG_TYPE = "voice_activity"
HEARTBEAT_SECONDS = 60
LEADERBOARD_LIMIT = 10

LOG_TYPES = {
    "deleted_messages": "Deleted Messages",
    "moderator_actions": "Moderator Actions",
    "server_leaves": "Server Leaves",
    VOICE_LOG_TYPE: "Voice Activity",
}

LEADERBOARD_CHOICES = [
    app_commands.Choice(
        name="Total Time",
        value="total_time",
    ),
    app_commands.Choice(
        name="This Week",
        value="week",
    ),
    app_commands.Choice(
        name="This Month",
        value="month",
    ),
    app_commands.Choice(
        name="Sessions",
        value="sessions",
    ),
    app_commands.Choice(
        name="Longest Session",
        value="longest_session",
    ),
]


class LogsCog(commands.Cog):
    """Configure Ares logging and voice activity statistics."""

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot
        self._voice_reconciled = False
        self.initialize_voice_database()

    async def cog_load(self):
        """Start the voice-session heartbeat."""
        if not self.voice_heartbeat.is_running():
            self.voice_heartbeat.start()

    def cog_unload(self):
        """Stop the voice-session heartbeat."""
        self.voice_heartbeat.cancel()

    # ========================================================
    # DATABASE
    # ========================================================

    @staticmethod
    def initialize_voice_database():
        """Create persistent voice-session storage."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_sessions (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                joined_at INTEGER NOT NULL,
                last_seen_at INTEGER NOT NULL,
                left_at INTEGER,
                duration_seconds INTEGER
            )
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_voice_sessions_guild_user
            ON voice_sessions (
                guild_id,
                user_id
            )
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_voice_sessions_open
            ON voice_sessions (
                guild_id,
                user_id,
                left_at
            )
            """
        )

        connection.commit()
        connection.close()

    @staticmethod
    def start_voice_session(
        guild_id: int,
        user_id: int,
        channel_id: int,
        started_at: int,
    ):
        """Start a persistent voice session."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                session_id,
                joined_at,
                last_seen_at
            FROM voice_sessions
            WHERE guild_id = ?
            AND user_id = ?
            AND left_at IS NULL
            ORDER BY session_id DESC
            LIMIT 1
            """,
            (
                guild_id,
                user_id,
            ),
        )

        existing = cursor.fetchone()

        if existing is not None:
            session_id = int(
                existing[0]
            )

            joined_at = int(
                existing[1]
            )

            last_seen_at = int(
                existing[2]
            )

            safe_left_at = max(
                joined_at,
                last_seen_at,
            )

            cursor.execute(
                """
                UPDATE voice_sessions
                SET
                    left_at = ?,
                    duration_seconds = ?
                WHERE session_id = ?
                """,
                (
                    safe_left_at,
                    max(
                        0,
                        safe_left_at - joined_at,
                    ),
                    session_id,
                ),
            )

        cursor.execute(
            """
            INSERT INTO voice_sessions (
                guild_id,
                user_id,
                channel_id,
                joined_at,
                last_seen_at,
                left_at,
                duration_seconds
            )
            VALUES (?, ?, ?, ?, ?, NULL, NULL)
            """,
            (
                guild_id,
                user_id,
                channel_id,
                started_at,
                started_at,
            ),
        )

        connection.commit()
        connection.close()

    @staticmethod
    def close_voice_session(
        guild_id: int,
        user_id: int,
        ended_at: int,
    ) -> tuple[int | None, int | None, int]:
        """Close the newest open voice session for a member."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                session_id,
                channel_id,
                joined_at
            FROM voice_sessions
            WHERE guild_id = ?
            AND user_id = ?
            AND left_at IS NULL
            ORDER BY session_id DESC
            LIMIT 1
            """,
            (
                guild_id,
                user_id,
            ),
        )

        result = cursor.fetchone()

        channel_id = None
        joined_at = None
        duration = 0

        if result is not None:
            session_id = int(
                result[0]
            )

            channel_id = int(
                result[1]
            )

            joined_at = int(
                result[2]
            )

            safe_ended_at = max(
                joined_at,
                ended_at,
            )

            duration = max(
                0,
                safe_ended_at - joined_at,
            )

            cursor.execute(
                """
                UPDATE voice_sessions
                SET
                    last_seen_at = ?,
                    left_at = ?,
                    duration_seconds = ?
                WHERE session_id = ?
                """,
                (
                    safe_ended_at,
                    safe_ended_at,
                    duration,
                    session_id,
                ),
            )

        connection.commit()
        connection.close()

        return (
            channel_id,
            joined_at,
            duration,
        )

    @staticmethod
    def heartbeat_open_sessions(
        current_time: int,
    ):
        """Mark all currently open sessions as observed."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE voice_sessions
            SET last_seen_at = ?
            WHERE left_at IS NULL
            """,
            (
                current_time,
            ),
        )

        connection.commit()
        connection.close()

    @staticmethod
    def close_stale_open_sessions():
        """Close sessions left open by a prior bot shutdown."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                session_id,
                joined_at,
                last_seen_at
            FROM voice_sessions
            WHERE left_at IS NULL
            """
        )

        rows = cursor.fetchall()

        for (
            session_id,
            joined_at,
            last_seen_at,
        ) in rows:
            joined_at = int(
                joined_at
            )

            safe_left_at = max(
                joined_at,
                int(last_seen_at),
            )

            cursor.execute(
                """
                UPDATE voice_sessions
                SET
                    left_at = ?,
                    duration_seconds = ?
                WHERE session_id = ?
                """,
                (
                    safe_left_at,
                    max(
                        0,
                        safe_left_at - joined_at,
                    ),
                    int(session_id),
                ),
            )

        connection.commit()
        connection.close()

    # ========================================================
    # HELPERS
    # ========================================================

    @staticmethod
    def parse_channel_id(
        destination: str,
    ) -> int | None:
        """Extract a Discord channel ID from an ID or channel mention."""

        destination = destination.strip()

        if destination.isdigit():
            return int(
                destination
            )

        if (
            destination.startswith("<#")
            and destination.endswith(">")
        ):
            raw_id = destination[
                2:-1
            ]

            if raw_id.isdigit():
                return int(
                    raw_id
                )

        return None

    @staticmethod
    def format_duration(
        seconds: int,
    ) -> str:
        """Format seconds as a compact human-readable duration."""

        seconds = max(
            0,
            int(seconds),
        )

        days, remainder = divmod(
            seconds,
            86400,
        )

        hours, remainder = divmod(
            remainder,
            3600,
        )

        minutes, _seconds = divmod(
            remainder,
            60,
        )

        parts = []

        if days > 0:
            parts.append(
                f"{days}d"
            )

        if hours > 0:
            parts.append(
                f"{hours}h"
            )

        if minutes > 0:
            parts.append(
                f"{minutes}m"
            )

        if not parts:
            parts.append(
                "<1m"
            )

        return " ".join(
            parts
        )

    @staticmethod
    def discord_timestamp(
        unix_time: int,
    ) -> str:
        """Return Discord absolute and relative timestamps."""

        return (
            f"<t:{unix_time}:f> "
            f"(<t:{unix_time}:R>)"
        )

    @staticmethod
    def is_counted_voice_channel(
        guild: discord.Guild,
        channel: discord.VoiceChannel | discord.StageChannel | None,
    ) -> bool:
        """Return whether a channel should count toward voice statistics."""

        if channel is None:
            return False

        if (
            guild.afk_channel is not None
            and channel.id == guild.afk_channel.id
        ):
            return False

        return True

    async def send_voice_log(
        self,
        guild: discord.Guild,
        embed: discord.Embed,
    ):
        """Send a voice log to the configured destination."""

        channel_id = get_log_channel(
            guild.id,
            VOICE_LOG_TYPE,
        )

        if channel_id is None:
            return

        channel = guild.get_channel(
            channel_id
        )

        if not isinstance(
            channel,
            discord.TextChannel,
        ):
            return

        try:
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    # ========================================================
    # /LOGS
    # ========================================================

    @app_commands.command(
        name="logs",
        description="Set the destination for an Ares log type.",
    )
    @app_commands.describe(
        log_type="The type of activity to log.",
        destination="Existing channel ID or channel mention.",
    )
    @app_commands.choices(
        log_type=[
            app_commands.Choice(
                name="Deleted Messages",
                value="deleted_messages",
            ),
            app_commands.Choice(
                name="Moderator Actions",
                value="moderator_actions",
            ),
            app_commands.Choice(
                name="Server Leaves",
                value="server_leaves",
            ),
            app_commands.Choice(
                name="Voice Activity",
                value=VOICE_LOG_TYPE,
            ),
        ]
    )
    @app_commands.default_permissions(
        manage_guild=True
    )
    @app_commands.checks.has_permissions(
        manage_guild=True
    )
    async def logs(
        self,
        interaction: discord.Interaction,
        log_type: app_commands.Choice[str],
        destination: str,
    ):
        """Configure an existing channel as a log destination."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        channel_id = self.parse_channel_id(
            destination
        )

        if channel_id is None:
            await interaction.response.send_message(
                (
                    "Invalid destination.\n\n"
                    "Provide either a channel ID or channel mention."
                ),
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(
            channel_id
        )

        if not isinstance(
            channel,
            discord.TextChannel,
        ):
            await interaction.response.send_message(
                "The log destination must be an existing text channel.",
                ephemeral=True,
            )
            return

        bot_member = interaction.guild.me

        if bot_member is None:
            await interaction.response.send_message(
                "Unable to verify my server permissions.",
                ephemeral=True,
            )
            return

        permissions = channel.permissions_for(
            bot_member
        )

        missing_permissions = []

        if not permissions.view_channel:
            missing_permissions.append(
                "View Channel"
            )

        if not permissions.send_messages:
            missing_permissions.append(
                "Send Messages"
            )

        if not permissions.embed_links:
            missing_permissions.append(
                "Embed Links"
            )

        if missing_permissions:
            await interaction.response.send_message(
                (
                    f"I cannot use {channel.mention} as a log destination.\n\n"
                    "Missing permissions: **"
                    + ", ".join(
                        missing_permissions
                    )
                    + "**"
                ),
                ephemeral=True,
            )
            return

        set_log_channel(
            interaction.guild.id,
            log_type.value,
            channel.id,
        )

        await interaction.response.send_message(
            (
                f"**{LOG_TYPES[log_type.value]}** logging configured.\n\n"
                f"Destination: {channel.mention}\n"
                f"Channel ID: `{channel.id}`"
            ),
            ephemeral=True,
        )

    # ========================================================
    # /VOICELEADERBOARD
    # ========================================================

    @app_commands.command(
        name="voiceleaderboard",
        description="Show the Ares voice activity leaderboard.",
    )
    @app_commands.describe(
        mode="Statistic to rank members by.",
    )
    @app_commands.choices(
        mode=LEADERBOARD_CHOICES
    )
    async def voiceleaderboard(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str] | None = None,
    ):
        """Display persistent voice statistics."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        selected_mode = (
            mode.value
            if mode is not None
            else "total_time"
        )

        current_time = int(
            time.time()
        )

        rows = self.get_leaderboard_rows(
            interaction.guild.id,
            selected_mode,
            current_time,
        )

        title_by_mode = {
            "total_time": "TOTAL TIME",
            "week": "THIS WEEK",
            "month": "THIS MONTH",
            "sessions": "SESSIONS",
            "longest_session": "LONGEST SESSION",
        }

        lines = []

        rank = 1

        for (
            user_id,
            value,
        ) in rows:
            member = interaction.guild.get_member(
                int(user_id)
            )

            if (
                member is None
                or member.bot
            ):
                continue

            safe_name = discord.utils.escape_markdown(
                member.display_name
            )

            if selected_mode == "sessions":
                value_text = (
                    f"{int(value):,} "
                    + (
                        "session"
                        if int(value) == 1
                        else "sessions"
                    )
                )
            else:
                value_text = self.format_duration(
                    int(value)
                )

            lines.append(
                f"**{rank}.** {safe_name} — **{value_text}**"
            )

            rank += 1

            if rank > LEADERBOARD_LIMIT:
                break

        if not lines:
            lines.append(
                "No voice activity has been recorded yet."
            )

        embed = discord.Embed(
            title=(
                "ARES GROUP // VOICE LEADERBOARD"
            ),
            description="\n".join(
                lines
            ),
            color=ARES_RED,
        )

        embed.add_field(
            name="MODE",
            value=title_by_mode[
                selected_mode
            ],
            inline=False,
        )

        embed.set_footer(
            text=(
                "Bots and the server AFK channel are excluded."
            )
        )

        embed.timestamp = discord.utils.utcnow()

        await interaction.response.send_message(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @staticmethod
    def get_leaderboard_rows(
        guild_id: int,
        mode: str,
        current_time: int,
    ) -> list[tuple[int, int]]:
        """Return leaderboard rows for a requested voice statistic."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        if mode == "sessions":
            cursor.execute(
                """
                SELECT
                    user_id,
                    COUNT(*)
                FROM voice_sessions
                WHERE guild_id = ?
                GROUP BY user_id
                ORDER BY COUNT(*) DESC, user_id ASC
                LIMIT ?
                """,
                (
                    guild_id,
                    LEADERBOARD_LIMIT * 3,
                ),
            )

        elif mode == "longest_session":
            cursor.execute(
                """
                SELECT
                    user_id,
                    MAX(
                        CASE
                            WHEN left_at IS NULL
                                THEN ? - joined_at
                            ELSE duration_seconds
                        END
                    )
                FROM voice_sessions
                WHERE guild_id = ?
                GROUP BY user_id
                ORDER BY 2 DESC, user_id ASC
                LIMIT ?
                """,
                (
                    current_time,
                    guild_id,
                    LEADERBOARD_LIMIT * 3,
                ),
            )

        elif mode in (
            "week",
            "month",
        ):
            now = datetime.fromtimestamp(
                current_time,
                tz=timezone.utc,
            )

            if mode == "week":
                start = now.replace(
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                )

                start_unix = int(
                    start.timestamp()
                    - (
                        now.weekday()
                        * 86400
                    )
                )
            else:
                start_unix = int(
                    now.replace(
                        day=1,
                        hour=0,
                        minute=0,
                        second=0,
                        microsecond=0,
                    ).timestamp()
                )

            cursor.execute(
                """
                SELECT
                    user_id,
                    SUM(
                        MAX(
                            0,
                            MIN(
                                COALESCE(left_at, ?),
                                ?
                            )
                            - MAX(
                                joined_at,
                                ?
                            )
                        )
                    )
                FROM voice_sessions
                WHERE guild_id = ?
                AND joined_at < ?
                AND COALESCE(left_at, ?) > ?
                GROUP BY user_id
                ORDER BY 2 DESC, user_id ASC
                LIMIT ?
                """,
                (
                    current_time,
                    current_time,
                    start_unix,
                    guild_id,
                    current_time,
                    current_time,
                    start_unix,
                    LEADERBOARD_LIMIT * 3,
                ),
            )

        else:
            cursor.execute(
                """
                SELECT
                    user_id,
                    SUM(
                        CASE
                            WHEN left_at IS NULL
                                THEN ? - joined_at
                            ELSE duration_seconds
                        END
                    )
                FROM voice_sessions
                WHERE guild_id = ?
                GROUP BY user_id
                ORDER BY 2 DESC, user_id ASC
                LIMIT ?
                """,
                (
                    current_time,
                    guild_id,
                    LEADERBOARD_LIMIT * 3,
                ),
            )

        rows = cursor.fetchall()

        connection.close()

        return [
            (
                int(user_id),
                int(value or 0),
            )
            for (
                user_id,
                value,
            ) in rows
        ]

    # ========================================================
    # VOICE EVENTS
    # ========================================================

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """Record voice joins, leaves, and channel moves."""

        if member.bot:
            return

        if before.channel == after.channel:
            return

        current_time = int(
            time.time()
        )

        guild = member.guild

        before_counted = self.is_counted_voice_channel(
            guild,
            before.channel,
        )

        after_counted = self.is_counted_voice_channel(
            guild,
            after.channel,
        )

        old_duration = 0
        old_joined_at = None

        if before_counted:
            (
                _old_channel_id,
                old_joined_at,
                old_duration,
            ) = self.close_voice_session(
                guild.id,
                member.id,
                current_time,
            )

        if after_counted:
            self.start_voice_session(
                guild.id,
                member.id,
                after.channel.id,
                current_time,
            )

        if (
            before.channel is None
            and after.channel is not None
        ):
            embed = discord.Embed(
                title="VOICE JOIN",
                color=ARES_RED,
            )

            embed.add_field(
                name="Member",
                value=member.mention,
                inline=False,
            )

            embed.add_field(
                name="Channel",
                value=after.channel.mention,
                inline=False,
            )

            embed.add_field(
                name="Joined",
                value=self.discord_timestamp(
                    current_time
                ),
                inline=False,
            )

            if not after_counted:
                embed.add_field(
                    name="Leaderboard",
                    value="Not counted",
                    inline=False,
                )

            await self.send_voice_log(
                guild,
                embed,
            )

        elif (
            before.channel is not None
            and after.channel is None
        ):
            embed = discord.Embed(
                title="VOICE LEAVE",
                color=ARES_RED,
            )

            embed.add_field(
                name="Member",
                value=member.mention,
                inline=False,
            )

            embed.add_field(
                name="Channel",
                value=before.channel.mention,
                inline=False,
            )

            if old_joined_at is not None:
                embed.add_field(
                    name="Joined",
                    value=self.discord_timestamp(
                        old_joined_at
                    ),
                    inline=False,
                )

            embed.add_field(
                name="Left",
                value=self.discord_timestamp(
                    current_time
                ),
                inline=False,
            )

            embed.add_field(
                name="Time Stayed",
                value=(
                    self.format_duration(
                        old_duration
                    )
                    if before_counted
                    else "Not counted"
                ),
                inline=False,
            )

            await self.send_voice_log(
                guild,
                embed,
            )

        elif (
            before.channel is not None
            and after.channel is not None
        ):
            embed = discord.Embed(
                title="VOICE MOVE",
                color=ARES_RED,
            )

            embed.add_field(
                name="Member",
                value=member.mention,
                inline=False,
            )

            embed.add_field(
                name="Moved",
                value=(
                    f"{before.channel.mention} "
                    f"→ {after.channel.mention}"
                ),
                inline=False,
            )

            embed.add_field(
                name="Time in Previous Channel",
                value=(
                    self.format_duration(
                        old_duration
                    )
                    if before_counted
                    else "Not counted"
                ),
                inline=False,
            )

            await self.send_voice_log(
                guild,
                embed,
            )

    @commands.Cog.listener()
    async def on_ready(self):
        """Recover voice tracking cleanly after a bot restart."""

        if self._voice_reconciled:
            return

        self.close_stale_open_sessions()

        current_time = int(
            time.time()
        )

        for guild in self.bot.guilds:
            for channel in guild.voice_channels:
                if not self.is_counted_voice_channel(
                    guild,
                    channel,
                ):
                    continue

                for member in channel.members:
                    if member.bot:
                        continue

                    self.start_voice_session(
                        guild.id,
                        member.id,
                        channel.id,
                        current_time,
                    )

        self._voice_reconciled = True

    @tasks.loop(
        seconds=HEARTBEAT_SECONDS
    )
    async def voice_heartbeat(self):
        """Persist a recent checkpoint for active voice sessions."""

        self.heartbeat_open_sessions(
            int(
                time.time()
            )
        )

    @voice_heartbeat.before_loop
    async def before_voice_heartbeat(self):
        """Wait until Discord is ready before heartbeat updates."""

        await self.bot.wait_until_ready()

    # ========================================================
    # ERRORS
    # ========================================================

    @logs.error
    async def logs_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        """Handle /logs permission failures."""

        if isinstance(
            error,
            app_commands.MissingPermissions,
        ):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    (
                        "You need the **Manage Server** permission "
                        "to configure Ares logging."
                    ),
                    ephemeral=True,
                )
            return

        raise error


async def setup(
    bot: commands.Bot,
):
    """Load the logging configuration cog."""

    await bot.add_cog(
        LogsCog(bot)
    )
