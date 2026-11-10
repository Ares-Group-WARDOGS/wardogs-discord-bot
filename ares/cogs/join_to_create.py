import asyncio
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ares.config import ARES_RED, ROLE_DB_PATH
from ares.prefixes import get_guild_prefix


TRIGGER_CHANNEL_NAME = "➕ | Join to Create"
ROOM_PREFIX = "Voice "
EMPTY_DELETE_SECONDS = 300
CLEANUP_INTERVAL_SECONDS = 30
MAX_VOICE_MEMBERS = 99


class JoinToCreateCog(commands.Cog):
    """Create and manage temporary Ares voice channels."""

    voicecategory = app_commands.Group(
        name="voicecategory",
        description="Configure Ares Join to Create voice categories.",
    )

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot
        self._hub_locks: dict[int, asyncio.Lock] = {}
        self.initialize_database()

    async def cog_load(self):
        """Start temporary-room cleanup."""
        if not self.cleanup_empty_rooms.is_running():
            self.cleanup_empty_rooms.start()

    def cog_unload(self):
        """Stop temporary-room cleanup."""
        self.cleanup_empty_rooms.cancel()

    # ========================================================
    # DATABASE
    # ========================================================

    @staticmethod
    def initialize_database():
        """Create persistent Join to Create tables."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_hubs (
                hub_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL UNIQUE,
                trigger_channel_id INTEGER NOT NULL UNIQUE,
                hub_name TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_rooms (
                channel_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                hub_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                empty_since INTEGER,
                FOREIGN KEY (hub_id)
                    REFERENCES voice_hubs(hub_id)
                    ON DELETE CASCADE
            )
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_voice_rooms_hub
            ON voice_rooms (
                hub_id
            )
            """
        )

        connection.commit()
        connection.close()

    @staticmethod
    def save_hub(
        guild_id: int,
        category_id: int,
        trigger_channel_id: int,
        hub_name: str,
    ):
        """Persist a Join to Create hub."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO voice_hubs (
                guild_id,
                category_id,
                trigger_channel_id,
                hub_name,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                category_id,
                trigger_channel_id,
                hub_name,
                int(time.time()),
            ),
        )

        connection.commit()
        connection.close()

    @staticmethod
    def get_hub_by_trigger(
        guild_id: int,
        trigger_channel_id: int,
    ) -> tuple[int, int, int, str] | None:
        """Return the configured hub for a trigger channel."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                hub_id,
                category_id,
                trigger_channel_id,
                hub_name
            FROM voice_hubs
            WHERE guild_id = ?
            AND trigger_channel_id = ?
            """,
            (
                guild_id,
                trigger_channel_id,
            ),
        )

        row = cursor.fetchone()

        connection.close()

        if row is None:
            return None

        return (
            int(row[0]),
            int(row[1]),
            int(row[2]),
            str(row[3]),
        )

    @staticmethod
    def get_hubs(
        guild_id: int,
    ) -> list[tuple[int, int, int, str]]:
        """Return all configured hubs for a guild."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                hub_id,
                category_id,
                trigger_channel_id,
                hub_name
            FROM voice_hubs
            WHERE guild_id = ?
            ORDER BY hub_id ASC
            """,
            (
                guild_id,
            ),
        )

        rows = cursor.fetchall()

        connection.close()

        return [
            (
                int(row[0]),
                int(row[1]),
                int(row[2]),
                str(row[3]),
            )
            for row in rows
        ]

    @staticmethod
    def delete_hub(
        guild_id: int,
        category_id: int,
    ) -> tuple[int, int] | None:
        """Delete a hub record and return its IDs."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                hub_id,
                trigger_channel_id
            FROM voice_hubs
            WHERE guild_id = ?
            AND category_id = ?
            """,
            (
                guild_id,
                category_id,
            ),
        )

        row = cursor.fetchone()

        if row is None:
            connection.close()
            return None

        hub_id = int(
            row[0]
        )

        trigger_channel_id = int(
            row[1]
        )

        cursor.execute(
            """
            DELETE FROM voice_rooms
            WHERE hub_id = ?
            """,
            (
                hub_id,
            ),
        )

        cursor.execute(
            """
            DELETE FROM voice_hubs
            WHERE hub_id = ?
            """,
            (
                hub_id,
            ),
        )

        connection.commit()
        connection.close()

        return (
            hub_id,
            trigger_channel_id,
        )

    @staticmethod
    def save_room(
        channel_id: int,
        guild_id: int,
        hub_id: int,
        owner_id: int,
    ):
        """Persist a temporary voice room."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO voice_rooms (
                channel_id,
                guild_id,
                hub_id,
                owner_id,
                created_at,
                empty_since
            )
            VALUES (?, ?, ?, ?, ?, NULL)
            """,
            (
                channel_id,
                guild_id,
                hub_id,
                owner_id,
                int(time.time()),
            ),
        )

        connection.commit()
        connection.close()

    @staticmethod
    def get_room(
        channel_id: int,
    ) -> tuple[int, int, int, int | None] | None:
        """Return hub, guild, owner, and empty time for a room."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                hub_id,
                guild_id,
                owner_id,
                empty_since
            FROM voice_rooms
            WHERE channel_id = ?
            """,
            (
                channel_id,
            ),
        )

        row = cursor.fetchone()

        connection.close()

        if row is None:
            return None

        return (
            int(row[0]),
            int(row[1]),
            int(row[2]),
            (
                int(row[3])
                if row[3] is not None
                else None
            ),
        )

    @staticmethod
    def get_rooms() -> list[tuple[int, int, int, int, int | None]]:
        """Return all tracked temporary rooms."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                channel_id,
                guild_id,
                hub_id,
                owner_id,
                empty_since
            FROM voice_rooms
            ORDER BY channel_id ASC
            """
        )

        rows = cursor.fetchall()

        connection.close()

        return [
            (
                int(row[0]),
                int(row[1]),
                int(row[2]),
                int(row[3]),
                (
                    int(row[4])
                    if row[4] is not None
                    else None
                ),
            )
            for row in rows
        ]

    @staticmethod
    def get_room_channel_ids_for_hub(
        hub_id: int,
    ) -> list[int]:
        """Return all tracked room channel IDs for a hub."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT channel_id
            FROM voice_rooms
            WHERE hub_id = ?
            """,
            (
                hub_id,
            ),
        )

        rows = cursor.fetchall()

        connection.close()

        return [
            int(row[0])
            for row in rows
        ]

    @staticmethod
    def set_empty_since(
        channel_id: int,
        empty_since: int | None,
    ):
        """Set or clear a room's empty timer."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE voice_rooms
            SET empty_since = ?
            WHERE channel_id = ?
            """,
            (
                empty_since,
                channel_id,
            ),
        )

        connection.commit()
        connection.close()

    @staticmethod
    def delete_room_record(
        channel_id: int,
    ):
        """Delete one room record."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            DELETE FROM voice_rooms
            WHERE channel_id = ?
            """,
            (
                channel_id,
            ),
        )

        connection.commit()
        connection.close()

    # ========================================================
    # HELPERS
    # ========================================================

    def get_hub_lock(
        self,
        hub_id: int,
    ) -> asyncio.Lock:
        """Return a creation lock for one independent hub."""

        lock = self._hub_locks.get(
            hub_id
        )

        if lock is None:
            lock = asyncio.Lock()

            self._hub_locks[
                hub_id
            ] = lock

        return lock

    def next_room_name(
        self,
        guild: discord.Guild,
        hub_id: int,
    ) -> str:
        """Return the lowest available Voice X name in one hub."""

        used_numbers = set()

        for channel_id in self.get_room_channel_ids_for_hub(
            hub_id
        ):
            channel = guild.get_channel(
                channel_id
            )

            if not isinstance(
                channel,
                discord.VoiceChannel,
            ):
                continue

            if not channel.name.startswith(
                ROOM_PREFIX
            ):
                continue

            suffix = channel.name[
                len(ROOM_PREFIX):
            ].strip()

            if suffix.isdigit():
                used_numbers.add(
                    int(suffix)
                )

        number = 1

        while number in used_numbers:
            number += 1

        return (
            f"{ROOM_PREFIX}{number}"
        )

    @staticmethod
    def build_guide_embed(
        owner: discord.Member,
        prefix: str,
    ) -> discord.Embed:
        """Build the command guide shown in a new room."""

        embed = discord.Embed(
            title="ARES GROUP // VOICE CHAT",
            description=(
                f"{owner.mention} created this voice chat.\n\n"
                "The creator can manage this room with the commands below."
            ),
            color=ARES_RED,
        )

        embed.add_field(
            name="ROOM COMMANDS",
            value=(
                f"`{prefix}name <name>`  Rename this voice chat.\n"
                f"`{prefix}lock`  Prevent new members from joining.\n"
                f"`{prefix}unlock`  Allow members to join again.\n"
                f"`{prefix}maxmembers <0-99>`  Set the member limit. "
                "`0` means unlimited.\n"
                f"`{prefix}status`  Show the current room settings."
            ),
            inline=False,
        )

        embed.add_field(
            name="CREATOR CONTROL",
            value=(
                "The room creator can disconnect members from this "
                "voice chat using Discord's normal voice controls."
            ),
            inline=False,
        )

        embed.add_field(
            name="AUTOMATIC CLEANUP",
            value=(
                "This channel is deleted after it has been empty "
                "for 5 minutes."
            ),
            inline=False,
        )

        embed.set_footer(
            text="ARES GROUP // TEMPORARY VOICE"
        )

        return embed

    async def get_managed_room_context(
        self,
        ctx: commands.Context,
    ) -> tuple[
        discord.VoiceChannel,
        int,
    ] | None:
        """Validate that a prefix command was sent in a managed voice room."""

        if (
            ctx.guild is None
            or not isinstance(
                ctx.channel,
                discord.VoiceChannel,
            )
        ):
            await ctx.send(
                "This command only works in an Ares temporary voice chat."
            )
            return None

        room = self.get_room(
            ctx.channel.id
        )

        if room is None:
            await ctx.send(
                "This is not an Ares temporary voice chat."
            )
            return None

        _hub_id, guild_id, owner_id, _empty_since = room

        if guild_id != ctx.guild.id:
            await ctx.send(
                "This voice chat is not registered to this server."
            )
            return None

        return (
            ctx.channel,
            owner_id,
        )

    async def require_room_owner(
        self,
        ctx: commands.Context,
    ) -> tuple[
        discord.VoiceChannel,
        int,
    ] | None:
        """Require the command author to own the temporary room."""

        result = await self.get_managed_room_context(
            ctx
        )

        if result is None:
            return None

        channel, owner_id = result

        if ctx.author.id != owner_id:
            await ctx.send(
                "Only the creator of this voice chat can use that command."
            )
            return None

        return (
            channel,
            owner_id,
        )

    # ========================================================
    # ADMIN SLASH COMMANDS
    # ========================================================

    @voicecategory.command(
        name="create",
        description="Create a Join to Create voice category.",
    )
    @app_commands.describe(
        name="Name of the new voice category.",
    )
    @app_commands.default_permissions(
        manage_guild=True
    )
    @app_commands.checks.has_permissions(
        manage_guild=True
    )
    async def voicecategory_create(
        self,
        interaction: discord.Interaction,
        name: str,
    ):
        """Create a category and its Join to Create trigger."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        name = name.strip()

        if not name:
            await interaction.response.send_message(
                "Category name cannot be empty.",
                ephemeral=True,
            )
            return

        if len(name) > 100:
            await interaction.response.send_message(
                "Category names cannot exceed 100 characters.",
                ephemeral=True,
            )
            return

        guild = interaction.guild

        bot_member = guild.me

        if bot_member is None:
            await interaction.response.send_message(
                "Unable to verify my server permissions.",
                ephemeral=True,
            )
            return

        if not bot_member.guild_permissions.manage_channels:
            await interaction.response.send_message(
                "Ares needs **Manage Channels** to create voice hubs.",
                ephemeral=True,
            )
            return

        if not bot_member.guild_permissions.move_members:
            await interaction.response.send_message(
                "Ares needs **Move Members** to create voice hubs.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        category = None
        trigger = None

        try:
            category = await guild.create_category(
                name,
                reason=(
                    f"Ares Join to Create setup by "
                    f"{interaction.user}"
                ),
            )

            trigger = await guild.create_voice_channel(
                TRIGGER_CHANNEL_NAME,
                category=category,
                reason="Ares Join to Create trigger",
            )

            self.save_hub(
                guild.id,
                category.id,
                trigger.id,
                name,
            )

        except (
            discord.Forbidden,
            discord.HTTPException,
        ) as error:
            if trigger is not None:
                try:
                    await trigger.delete(
                        reason="Rolling back failed Ares voice hub setup"
                    )
                except discord.HTTPException:
                    pass

            if category is not None:
                try:
                    await category.delete(
                        reason="Rolling back failed Ares voice hub setup"
                    )
                except discord.HTTPException:
                    pass

            await interaction.followup.send(
                (
                    "I could not create the voice hub.\n\n"
                    f"`{error}`"
                ),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            (
                "Join to Create voice category created.\n\n"
                f"Category: **{category.name}**\n"
                f"Trigger: {trigger.mention}\n\n"
                "Joining the trigger will create an independent "
                "`Voice X` channel."
            ),
            ephemeral=True,
        )

    @voicecategory.command(
        name="remove",
        description="Remove an Ares Join to Create configuration.",
    )
    @app_commands.describe(
        category_id="Category ID to stop managing.",
    )
    @app_commands.default_permissions(
        manage_guild=True
    )
    @app_commands.checks.has_permissions(
        manage_guild=True
    )
    async def voicecategory_remove(
        self,
        interaction: discord.Interaction,
        category_id: str,
    ):
        """Remove a hub configuration without deleting active rooms."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        category_id = category_id.strip()

        if not category_id.isdigit():
            await interaction.response.send_message(
                "Provide a valid numeric category ID.",
                ephemeral=True,
            )
            return

        parsed_id = int(
            category_id
        )

        result = self.delete_hub(
            interaction.guild.id,
            parsed_id,
        )

        if result is None:
            await interaction.response.send_message(
                "That category is not configured as an Ares voice hub.",
                ephemeral=True,
            )
            return

        _hub_id, trigger_channel_id = result

        trigger = interaction.guild.get_channel(
            trigger_channel_id
        )

        if isinstance(
            trigger,
            discord.VoiceChannel,
        ):
            try:
                await trigger.delete(
                    reason=(
                        f"Ares voice hub removed by "
                        f"{interaction.user}"
                    )
                )
            except discord.HTTPException:
                pass

        await interaction.response.send_message(
            (
                "Ares stopped managing that Join to Create category.\n\n"
                "The trigger channel was removed. Existing temporary "
                "voice channels were left in place."
            ),
            ephemeral=True,
        )

    @voicecategory.command(
        name="status",
        description="Show configured Ares Join to Create categories.",
    )
    @app_commands.default_permissions(
        manage_guild=True
    )
    @app_commands.checks.has_permissions(
        manage_guild=True
    )
    async def voicecategory_status(
        self,
        interaction: discord.Interaction,
    ):
        """Show configured voice hubs."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        hubs = self.get_hubs(
            interaction.guild.id
        )

        if not hubs:
            await interaction.response.send_message(
                "No Ares Join to Create categories are configured.",
                ephemeral=True,
            )
            return

        lines = []

        for (
            hub_id,
            category_id,
            trigger_channel_id,
            hub_name,
        ) in hubs:
            category = interaction.guild.get_channel(
                category_id
            )

            trigger = interaction.guild.get_channel(
                trigger_channel_id
            )

            category_text = (
                category.name
                if isinstance(
                    category,
                    discord.CategoryChannel,
                )
                else f"Missing category ({category_id})"
            )

            trigger_text = (
                trigger.mention
                if isinstance(
                    trigger,
                    discord.VoiceChannel,
                )
                else f"Missing trigger ({trigger_channel_id})"
            )

            room_count = len(
                self.get_room_channel_ids_for_hub(
                    hub_id
                )
            )

            lines.append(
                (
                    f"**{hub_name}**\n"
                    f"Category: {category_text} (`{category_id}`)\n"
                    f"Trigger: {trigger_text}\n"
                    f"Tracked rooms: **{room_count}**"
                )
            )

        embed = discord.Embed(
            title="ARES GROUP // VOICE HUBS",
            description="\n\n".join(
                lines
            ),
            color=ARES_RED,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ========================================================
    # ROOM PREFIX COMMANDS
    # ========================================================

    @commands.command(
        name="name",
    )
    async def room_name(
        self,
        ctx: commands.Context,
        *,
        name: str = "",
    ):
        """Rename a temporary voice room."""

        result = await self.require_room_owner(
            ctx
        )

        if result is None:
            return

        channel, _owner_id = result

        name = name.strip()

        if not name:
            await ctx.send(
                "Usage: `!name <voice chat name>`"
            )
            return

        if len(name) > 100:
            await ctx.send(
                "Voice chat names cannot exceed 100 characters."
            )
            return

        try:
            await channel.edit(
                name=name,
                reason=(
                    f"Temporary voice room renamed by "
                    f"{ctx.author}"
                ),
            )
        except discord.HTTPException as error:
            await ctx.send(
                f"I could not rename this voice chat: `{error}`"
            )
            return

        await ctx.send(
            f"Voice chat renamed to **{discord.utils.escape_markdown(name)}**."
        )

    @commands.command(
        name="lock",
    )
    async def room_lock(
        self,
        ctx: commands.Context,
    ):
        """Lock a temporary voice room."""

        result = await self.require_room_owner(
            ctx
        )

        if result is None:
            return

        channel, _owner_id = result

        overwrite = channel.overwrites_for(
            ctx.guild.default_role
        )

        overwrite.connect = False

        try:
            await channel.set_permissions(
                ctx.guild.default_role,
                overwrite=overwrite,
                reason=(
                    f"Temporary voice room locked by "
                    f"{ctx.author}"
                ),
            )
        except discord.HTTPException as error:
            await ctx.send(
                f"I could not lock this voice chat: `{error}`"
            )
            return

        await ctx.send(
            f"🔒 {ctx.author.mention} locked this voice chat.",
            allowed_mentions=discord.AllowedMentions(
                users=True
            ),
        )

    @commands.command(
        name="unlock",
    )
    async def room_unlock(
        self,
        ctx: commands.Context,
    ):
        """Unlock a temporary voice room."""

        result = await self.require_room_owner(
            ctx
        )

        if result is None:
            return

        channel, _owner_id = result

        overwrite = channel.overwrites_for(
            ctx.guild.default_role
        )

        overwrite.connect = None

        try:
            if overwrite.is_empty():
                await channel.set_permissions(
                    ctx.guild.default_role,
                    overwrite=None,
                    reason=(
                        f"Temporary voice room unlocked by "
                        f"{ctx.author}"
                    ),
                )
            else:
                await channel.set_permissions(
                    ctx.guild.default_role,
                    overwrite=overwrite,
                    reason=(
                        f"Temporary voice room unlocked by "
                        f"{ctx.author}"
                    ),
                )
        except discord.HTTPException as error:
            await ctx.send(
                f"I could not unlock this voice chat: `{error}`"
            )
            return

        await ctx.send(
            f"🔓 {ctx.author.mention} unlocked this voice chat.",
            allowed_mentions=discord.AllowedMentions(
                users=True
            ),
        )

    @commands.command(
        name="maxmembers",
    )
    async def room_max_members(
        self,
        ctx: commands.Context,
        limit: int | None = None,
    ):
        """Set the member limit for a temporary voice room."""

        result = await self.require_room_owner(
            ctx
        )

        if result is None:
            return

        channel, _owner_id = result

        if limit is None:
            await ctx.send(
                "Usage: `!maxmembers <0-99>` — `0` means unlimited."
            )
            return

        if (
            limit < 0
            or limit > MAX_VOICE_MEMBERS
        ):
            await ctx.send(
                f"Member limit must be between 0 and {MAX_VOICE_MEMBERS}."
            )
            return

        try:
            await channel.edit(
                user_limit=limit,
                reason=(
                    f"Temporary voice room limit changed by "
                    f"{ctx.author}"
                ),
            )
        except discord.HTTPException as error:
            await ctx.send(
                f"I could not change the member limit: `{error}`"
            )
            return

        if limit == 0:
            limit_text = "unlimited"
        else:
            limit_text = str(
                limit
            )

        await ctx.send(
            f"Maximum members set to **{limit_text}**."
        )

    @commands.command(
        name="status",
    )
    async def room_status(
        self,
        ctx: commands.Context,
    ):
        """Show temporary voice room settings."""

        result = await self.get_managed_room_context(
            ctx
        )

        if result is None:
            return

        channel, owner_id = result

        owner = ctx.guild.get_member(
            owner_id
        )

        owner_text = (
            owner.mention
            if owner is not None
            else f"`{owner_id}`"
        )

        default_overwrite = channel.overwrites_for(
            ctx.guild.default_role
        )

        locked = (
            default_overwrite.connect is False
        )

        member_limit = (
            "Unlimited"
            if channel.user_limit == 0
            else str(
                channel.user_limit
            )
        )

        embed = discord.Embed(
            title="ARES GROUP // VOICE CHAT STATUS",
            color=ARES_RED,
        )

        embed.add_field(
            name="Name",
            value=channel.name,
            inline=False,
        )

        embed.add_field(
            name="Creator",
            value=owner_text,
            inline=True,
        )

        embed.add_field(
            name="Access",
            value=(
                "🔒 Locked"
                if locked
                else "🔓 Unlocked"
            ),
            inline=True,
        )

        embed.add_field(
            name="Member Limit",
            value=member_limit,
            inline=True,
        )

        embed.add_field(
            name="Current Members",
            value=str(
                len(channel.members)
            ),
            inline=True,
        )

        embed.set_footer(
            text="Deleted after 5 minutes empty"
        )

        await ctx.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ========================================================
    # VOICE EVENT HANDLING
    # ========================================================

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """Create rooms and maintain their vacancy timers."""

        if member.bot:
            return

        if (
            after.channel is not None
            and (
                before.channel is None
                or before.channel.id != after.channel.id
            )
        ):
            hub = self.get_hub_by_trigger(
                member.guild.id,
                after.channel.id,
            )

            if hub is not None:
                await self.create_room_for_member(
                    member,
                    hub,
                )
                return

        current_time = int(
            time.time()
        )

        if (
            before.channel is not None
            and (
                after.channel is None
                or before.channel.id != after.channel.id
            )
        ):
            old_room = self.get_room(
                before.channel.id
            )

            if old_room is not None:
                if len(
                    before.channel.members
                ) == 0:
                    self.set_empty_since(
                        before.channel.id,
                        current_time,
                    )

        if (
            after.channel is not None
            and (
                before.channel is None
                or before.channel.id != after.channel.id
            )
        ):
            new_room = self.get_room(
                after.channel.id
            )

            if new_room is not None:
                self.set_empty_since(
                    after.channel.id,
                    None,
                )

    async def create_room_for_member(
        self,
        member: discord.Member,
        hub: tuple[int, int, int, str],
    ):
        """Create one temporary room and move the triggering member."""

        (
            hub_id,
            category_id,
            _trigger_channel_id,
            _hub_name,
        ) = hub

        guild = member.guild

        category = guild.get_channel(
            category_id
        )

        if not isinstance(
            category,
            discord.CategoryChannel,
        ):
            return

        lock = self.get_hub_lock(
            hub_id
        )

        async with lock:
            room_name = self.next_room_name(
                guild,
                hub_id,
            )

            overwrites = dict(
                category.overwrites
            )

            owner_overwrite = discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                speak=True,
                send_messages=True,
                read_message_history=True,
                move_members=True,
            )

            overwrites[
                member
            ] = owner_overwrite

            try:
                room = await guild.create_voice_channel(
                    room_name,
                    category=category,
                    overwrites=overwrites,
                    reason=(
                        f"Ares temporary voice room for "
                        f"{member}"
                    ),
                )
            except (
                discord.Forbidden,
                discord.HTTPException,
            ) as error:
                print(
                    "VOICE HUB: Failed to create room: "
                    f"{error}"
                )
                return

            self.save_room(
                room.id,
                guild.id,
                hub_id,
                member.id,
            )

            prefix = get_guild_prefix(
                self.bot,
                guild.id,
            )

            guide = self.build_guide_embed(
                member,
                prefix,
            )

            try:
                await room.send(
                    embed=guide,
                    allowed_mentions=discord.AllowedMentions(
                        users=True
                    ),
                )
            except (
                discord.Forbidden,
                discord.HTTPException,
            ) as error:
                print(
                    "VOICE HUB: Could not send room guide: "
                    f"{error}"
                )

            try:
                await member.move_to(
                    room,
                    reason="Ares Join to Create",
                )
            except (
                discord.Forbidden,
                discord.HTTPException,
            ) as error:
                print(
                    "VOICE HUB: Could not move creator: "
                    f"{error}"
                )

                self.set_empty_since(
                    room.id,
                    int(
                        time.time()
                    ),
                )

    # ========================================================
    # CLEANUP
    # ========================================================

    @tasks.loop(
        seconds=CLEANUP_INTERVAL_SECONDS
    )
    async def cleanup_empty_rooms(self):
        """Delete temporary rooms after five vacant minutes."""

        current_time = int(
            time.time()
        )

        for (
            channel_id,
            guild_id,
            _hub_id,
            _owner_id,
            empty_since,
        ) in self.get_rooms():
            guild = self.bot.get_guild(
                guild_id
            )

            if guild is None:
                continue

            channel = guild.get_channel(
                channel_id
            )

            if not isinstance(
                channel,
                discord.VoiceChannel,
            ):
                self.delete_room_record(
                    channel_id
                )
                continue

            if channel.members:
                if empty_since is not None:
                    self.set_empty_since(
                        channel_id,
                        None,
                    )
                continue

            if empty_since is None:
                self.set_empty_since(
                    channel_id,
                    current_time,
                )
                continue

            if (
                current_time - empty_since
                < EMPTY_DELETE_SECONDS
            ):
                continue

            try:
                await channel.delete(
                    reason=(
                        "Ares temporary voice room empty "
                        "for 5 minutes"
                    )
                )
            except discord.NotFound:
                pass
            except (
                discord.Forbidden,
                discord.HTTPException,
            ) as error:
                print(
                    "VOICE HUB: Failed to delete empty room: "
                    f"{error}"
                )
                continue

            self.delete_room_record(
                channel_id
            )

    @cleanup_empty_rooms.before_loop
    async def before_cleanup_empty_rooms(self):
        """Wait for Discord before running cleanup."""

        await self.bot.wait_until_ready()

    # ========================================================
    # ERRORS
    # ========================================================

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        """Handle voice-category slash command errors."""

        if isinstance(
            error,
            app_commands.MissingPermissions,
        ):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    (
                        "You need the **Manage Server** permission "
                        "to configure Ares voice categories."
                    ),
                    ephemeral=True,
                )
            return

        raise error


async def setup(
    bot: commands.Bot,
):
    """Load the Ares Join to Create cog."""

    await bot.add_cog(
        JoinToCreateCog(bot)
    )
