import math
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ares.database import (
    create_countdown,
    delete_countdown_by_channel,
    get_all_countdowns,
    get_countdown_by_channel,
    get_countdowns,
    set_countdown_name,
    update_countdown_runtime,
)
from ares.permissions import (
    AresAdminRequired,
    ares_admin_only,
)


COUNTDOWN_TASK_INTERVAL_SECONDS = 30
MAX_COUNTDOWN_NAME_LENGTH = 45

MAX_REASONABLE_UNIX_TIMESTAMP = 253402300799


class CountdownCog(commands.Cog):
    """Create and maintain persistent voice-channel countdowns."""

    countdown = app_commands.Group(
        name="countdown",
        description="Create and manage Ares countdown channels.",
    )

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

    async def cog_load(
        self,
    ):
        """Start the persistent countdown updater."""

        if not self.countdown_task.is_running():
            self.countdown_task.start()

    def cog_unload(
        self,
    ):
        """Stop the countdown updater when the cog unloads."""

        self.countdown_task.cancel()

    # ========================================================
    # FORMATTING
    # ========================================================

    @staticmethod
    def format_unit(
        value: int,
        singular: str,
    ) -> str:
        """Format one countdown unit with correct pluralization."""

        suffix = (
            singular
            if value == 1
            else f"{singular}s"
        )

        return f"{value} {suffix}"

    @classmethod
    def build_channel_name(
        cls,
        name: str,
        target_unix: int,
        now_unix: int | None = None,
    ) -> str:
        """Return the human-readable countdown voice-channel name."""

        if now_unix is None:
            now_unix = int(
                time.time()
            )

        seconds_remaining = (
            target_unix
            - now_unix
        )

        if seconds_remaining <= 0:
            return f"{name}: NOW"

        # Round upward to the next 5-minute boundary. This keeps
        # channel renames sparse enough for Discord while ensuring
        # important boundaries are accurate:
        # 9:27 -> 35 minutes, 9:30 -> 30 minutes, 9:55 -> 5 minutes.
        total_minutes = max(
            1,
            math.ceil(
                seconds_remaining / 60
            ),
        )

        display_minutes = (
            math.ceil(
                total_minutes / 5
            )
            * 5
        )

        days, remaining_minutes = divmod(
            display_minutes,
            24 * 60,
        )

        hours, minutes = divmod(
            remaining_minutes,
            60,
        )

        parts = []

        if days > 0:
            parts.append(
                cls.format_unit(
                    days,
                    "day",
                )
            )

        if hours > 0:
            parts.append(
                cls.format_unit(
                    hours,
                    "hour",
                )
            )

        if minutes > 0:
            parts.append(
                cls.format_unit(
                    minutes,
                    "minute",
                )
            )

        if not parts:
            parts.append(
                cls.format_unit(
                    5,
                    "minute",
                )
            )

        return (
            f"{name}: "
            + ", ".join(
                parts
            )
        )

    # ========================================================
    # PERMISSIONS / ORDER
    # ========================================================

    @staticmethod
    async def ensure_countdown_permissions(
        channel: discord.VoiceChannel,
    ):
        """Keep countdown channels visible and technically joinable."""

        guild = channel.guild

        overwrite = channel.overwrites_for(
            guild.default_role
        )

        changed = False

        if overwrite.view_channel is not True:
            overwrite.view_channel = True
            changed = True

        if overwrite.connect is not True:
            overwrite.connect = True
            changed = True

        if overwrite.speak is not True:
            overwrite.speak = True
            changed = True

        if overwrite.send_messages is not False:
            overwrite.send_messages = False
            changed = True

        if channel.user_limit != 0:
            try:
                await channel.edit(
                    user_limit=0,
                    reason=(
                        "Maintaining Ares countdown "
                        "unlimited user limit"
                    ),
                )

            except discord.Forbidden:
                print(
                    "COUNTDOWN: Missing permission to maintain "
                    f"user limit for channel {channel.id}."
                )

            except discord.HTTPException as error:
                print(
                    "COUNTDOWN: User-limit update failed "
                    f"for {channel.id}: {error}"
                )

        if not changed:
            return

        try:
            await channel.set_permissions(
                guild.default_role,
                overwrite=overwrite,
                reason=(
                    "Maintaining Ares countdown "
                    "visibility and onboarding-compatible permissions"
                ),
            )

        except discord.Forbidden:
            print(
                "COUNTDOWN: Missing permission to maintain "
                f"channel {channel.id}."
            )

        except discord.HTTPException as error:
            print(
                "COUNTDOWN: Permission update failed "
                f"for {channel.id}: {error}"
            )

    async def reorder_countdown_channels(
        self,
        guild: discord.Guild,
    ):
        """Keep countdowns at the top in oldest-created-first order."""

        rows = get_countdowns(
            guild.id
        )

        position = 0

        for (
            _countdown_id,
            channel_id,
            _target_unix,
            _created_at,
            _name,
            _last_name,
            _last_renamed_at,
        ) in rows:
            channel = guild.get_channel(
                channel_id
            )

            if not isinstance(
                channel,
                discord.VoiceChannel,
            ):
                delete_countdown_by_channel(
                    guild.id,
                    channel_id,
                )
                continue

            if channel.position == position:
                position += 1
                continue

            try:
                await channel.edit(
                    position=position,
                    reason=(
                        "Ordering Ares countdowns "
                        "oldest to newest"
                    ),
                )

            except discord.Forbidden:
                print(
                    "COUNTDOWN: Missing Manage Channels "
                    "permission while ordering countdowns."
                )
                return

            except discord.HTTPException as error:
                print(
                    "COUNTDOWN: Failed to position "
                    f"{channel.id}: {error}"
                )
                return

            position += 1

    # ========================================================
    # /COUNTDOWN CREATE
    # ========================================================

    @countdown.command(
        name="create",
        description="Create a display-only voice-channel countdown from a Unix timestamp.",
    )
    @app_commands.describe(
        timestamp="Future Unix timestamp in seconds, for example 1789056000.",
        name="Countdown label, for example SERVER SLAM.",
    )
    @ares_admin_only()
    async def countdown_create(
        self,
        interaction: discord.Interaction,
        timestamp: str,
        name: str,
    ):
        """Create a persistent countdown voice channel."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        timestamp = timestamp.strip()
        name = name.strip()

        if not name:
            await interaction.response.send_message(
                "Countdown name cannot be empty.",
                ephemeral=True,
            )
            return

        if len(name) > MAX_COUNTDOWN_NAME_LENGTH:
            await interaction.response.send_message(
                (
                    "Countdown names cannot exceed "
                    f"**{MAX_COUNTDOWN_NAME_LENGTH} characters**."
                ),
                ephemeral=True,
            )
            return

        if not timestamp.isdigit():
            await interaction.response.send_message(
                (
                    "The timestamp must contain digits only and must be "
                    "a Unix timestamp in **seconds**."
                ),
                ephemeral=True,
            )
            return

        target_unix = int(
            timestamp
        )

        current_unix = int(
            time.time()
        )

        if target_unix > MAX_REASONABLE_UNIX_TIMESTAMP:
            await interaction.response.send_message(
                (
                    "That timestamp is too large. "
                    "Make sure you supplied Unix time in **seconds**, "
                    "not milliseconds."
                ),
                ephemeral=True,
            )
            return

        if target_unix <= current_unix:
            await interaction.response.send_message(
                "The countdown timestamp must be in the future.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        bot_member = guild.me

        if (
            bot_member is None
            or not bot_member.guild_permissions.manage_channels
            or not bot_member.guild_permissions.move_members
        ):
            await interaction.response.send_message(
                (
                    "Ares needs **Manage Channels** and "
                    "**Move Members** to create countdowns."
                ),
                ephemeral=True,
            )
            return

        desired_name = self.build_channel_name(
            name,
            target_unix,
            current_unix,
        )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                speak=True,
                send_messages=False,
            )
        }

        await interaction.response.defer(
            ephemeral=True
        )

        try:
            channel = await guild.create_voice_channel(
                name=desired_name,
                overwrites=overwrites,
                user_limit=0,
                reason="Ares countdown created",
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "Ares could not create the voice channel. Check Manage Channels.",
                ephemeral=True,
            )
            return

        except discord.HTTPException as error:
            await interaction.followup.send(
                (
                    "Discord rejected the countdown channel creation.\n\n"
                    f"`{error}`"
                ),
                ephemeral=True,
            )
            return

        create_countdown(
            guild.id,
            channel.id,
            target_unix,
            time.time_ns(),
            name,
            desired_name,
            current_unix,
        )

        await self.reorder_countdown_channels(
            guild
        )

        await interaction.followup.send(
            (
                "Countdown created.\n\n"
                f"Channel: {channel.mention}\n"
                f"Unix timestamp: `{target_unix}`\n"
                f"Current name: **{desired_name}**\n\n"
                "The channel is visible and joinable for Onboarding, "
                "but Ares immediately disconnects anyone who enters it. "
                "Older countdowns stay above newer countdowns."
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ========================================================
    # /COUNTDOWN RENAME
    # ========================================================

    @countdown.command(
        name="rename",
        description="Change the customizable name of a countdown channel.",
    )
    @app_commands.describe(
        channel="Countdown voice channel to rename.",
        name="Countdown label, for example SERVER SLAM.",
    )
    @ares_admin_only()
    async def countdown_rename(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel,
        name: str,
    ):
        """Change a countdown's stored display name."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        name = name.strip()

        if not name:
            await interaction.response.send_message(
                "Countdown name cannot be empty.",
                ephemeral=True,
            )
            return

        if len(name) > MAX_COUNTDOWN_NAME_LENGTH:
            await interaction.response.send_message(
                (
                    "Countdown names cannot exceed "
                    f"**{MAX_COUNTDOWN_NAME_LENGTH} characters**."
                ),
                ephemeral=True,
            )
            return

        countdown_row = get_countdown_by_channel(
            interaction.guild.id,
            channel.id,
        )

        if countdown_row is None:
            await interaction.response.send_message(
                "That voice channel is not an Ares countdown.",
                ephemeral=True,
            )
            return

        (
            _countdown_id,
            _channel_id,
            target_unix,
            _created_at,
            _old_name,
            _last_name,
            _last_renamed_at,
        ) = countdown_row

        desired_name = self.build_channel_name(
            name,
            target_unix,
        )

        await interaction.response.defer(
            ephemeral=True
        )

        try:
            await channel.edit(
                name=desired_name,
                reason="Ares countdown name changed",
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "Ares could not rename that channel. Check Manage Channels.",
                ephemeral=True,
            )
            return

        except discord.HTTPException as error:
            await interaction.followup.send(
                (
                    "Discord rejected the countdown rename.\n\n"
                    f"`{error}`"
                ),
                ephemeral=True,
            )
            return

        set_countdown_name(
            interaction.guild.id,
            channel.id,
            name,
        )

        update_countdown_runtime(
            channel.id,
            desired_name,
            int(
                time.time()
            ),
        )

        await interaction.followup.send(
            (
                "Countdown renamed.\n\n"
                f"Current channel name: **{desired_name}**"
            ),
            ephemeral=True,
        )

    # ========================================================
    # /COUNTDOWN REMOVE
    # ========================================================

    @countdown.command(
        name="remove",
        description="Delete one Ares countdown voice channel.",
    )
    @app_commands.describe(
        channel="Countdown voice channel to remove.",
    )
    @ares_admin_only()
    async def countdown_remove(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel,
    ):
        """Delete a tracked countdown channel."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        countdown = get_countdown_by_channel(
            interaction.guild.id,
            channel.id,
        )

        if countdown is None:
            await interaction.response.send_message(
                "That voice channel is not an Ares countdown.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        try:
            await channel.delete(
                reason="Ares countdown removed"
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "Ares could not delete that channel. Check Manage Channels.",
                ephemeral=True,
            )
            return

        except discord.HTTPException as error:
            await interaction.followup.send(
                (
                    "Discord rejected the countdown removal.\n\n"
                    f"`{error}`"
                ),
                ephemeral=True,
            )
            return

        delete_countdown_by_channel(
            interaction.guild.id,
            channel.id,
        )

        await self.reorder_countdown_channels(
            interaction.guild
        )

        await interaction.followup.send(
            "Countdown removed.",
            ephemeral=True,
        )

    # ========================================================
    # /COUNTDOWN STATUS
    # ========================================================

    @countdown.command(
        name="status",
        description="Show active Ares countdown channels in creation order.",
    )
    @ares_admin_only()
    async def countdown_status(
        self,
        interaction: discord.Interaction,
    ):
        """Show tracked countdowns without Discord timestamp formatting."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        rows = get_countdowns(
            interaction.guild.id
        )

        if not rows:
            await interaction.response.send_message(
                "No Ares countdown channels are configured.",
                ephemeral=True,
            )
            return

        lines = []

        for index, (
            _countdown_id,
            channel_id,
            target_unix,
            _created_at,
            base_name,
            _last_name,
            _last_renamed_at,
        ) in enumerate(
            rows,
            start=1,
        ):
            channel = interaction.guild.get_channel(
                channel_id
            )

            if isinstance(
                channel,
                discord.VoiceChannel,
            ):
                channel_text = channel.mention
                current_name = channel.name
            else:
                channel_text = f"`{channel_id}` (missing)"
                current_name = "Channel missing"

            lines.append(
                (
                    f"**{index}.** {channel_text}\n"
                    f"Unix: `{target_unix}`\n"
                    f"Base name: **{base_name}**\n"
                    f"Channel: **{current_name}**"
                )
            )

        await interaction.response.send_message(
            "\n\n".join(
                lines
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ========================================================
    # COUNTDOWN VOICE GUARD
    # ========================================================

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """Immediately disconnect users who enter a countdown channel."""

        channel = after.channel

        if channel is None:
            return

        if before.channel is not None and before.channel.id == channel.id:
            return

        if member.bot:
            return

        countdown_row = get_countdown_by_channel(
            member.guild.id,
            channel.id,
        )

        if countdown_row is None:
            return

        bot_member = member.guild.me

        if (
            bot_member is None
            or not bot_member.guild_permissions.move_members
        ):
            print(
                "COUNTDOWN: Cannot disconnect "
                f"{member.id} from {channel.id}; "
                "Ares is missing Move Members."
            )
            return

        try:
            await member.move_to(
                None,
                reason=(
                    "Countdown voice channels are display-only."
                ),
            )

            print(
                "COUNTDOWN: Disconnected "
                f"{member.id} from display channel {channel.id}."
            )

        except discord.Forbidden:
            print(
                "COUNTDOWN: Missing permission to disconnect "
                f"{member.id} from {channel.id}."
            )

        except discord.HTTPException as error:
            print(
                "COUNTDOWN: Failed to disconnect "
                f"{member.id} from {channel.id}: {error}"
            )

    # ========================================================
    # PERSISTENT UPDATER
    # ========================================================

    @tasks.loop(
        seconds=COUNTDOWN_TASK_INTERVAL_SECONDS
    )
    async def countdown_task(
        self,
    ):
        """Keep tracked countdowns synchronized to 5-minute boundaries."""

        current_unix = int(
            time.time()
        )

        for (
            _countdown_id,
            guild_id,
            channel_id,
            target_unix,
            _created_at,
            name,
            _last_name,
            _last_renamed_at,
        ) in get_all_countdowns():
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
                delete_countdown_by_channel(
                    guild_id,
                    channel_id,
                )

                print(
                    "COUNTDOWN: Removed missing channel "
                    f"{channel_id} from the database."
                )
                continue

            await self.ensure_countdown_permissions(
                channel
            )

            desired_name = self.build_channel_name(
                name,
                target_unix,
                current_unix,
            )

            if channel.name == desired_name:
                continue

            # The displayed value is calculated directly from the target
            # Unix timestamp every pass. As soon as the real clock crosses
            # a minute boundary and the desired text changes, rename the
            # channel immediately. There is no rolling "minutes since last
            # rename" timer, so the countdown cannot accumulate drift.
            try:
                await channel.edit(
                    name=desired_name,
                    reason="Updating Ares countdown",
                )

            except discord.Forbidden:
                print(
                    "COUNTDOWN: Missing Manage Channels "
                    f"permission for channel {channel.id}."
                )
                continue

            except discord.HTTPException as error:
                print(
                    "COUNTDOWN: Rename failed "
                    f"for {channel.id}: {error}"
                )
                continue

            update_countdown_runtime(
                channel.id,
                desired_name,
                current_unix,
            )

            print(
                "COUNTDOWN: Updated "
                f"{channel.id} -> {desired_name}"
            )

    @countdown_task.before_loop
    async def before_countdown_task(
        self,
    ):
        """Wait for Discord before touching persistent channels."""

        await self.bot.wait_until_ready()

    # ========================================================
    # CHANNEL DELETION CLEANUP
    # ========================================================

    @commands.Cog.listener()
    async def on_guild_channel_delete(
        self,
        channel: discord.abc.GuildChannel,
    ):
        """Stop tracking a countdown if its voice channel is deleted."""

        removed = delete_countdown_by_channel(
            channel.guild.id,
            channel.id,
        )

        if not removed:
            return

        print(
            "COUNTDOWN: Deleted channel cleanup "
            f"for {channel.id}."
        )

        await self.reorder_countdown_channels(
            channel.guild
        )

    # ========================================================
    # ERROR HANDLER
    # ========================================================

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        """Handle countdown command permission failures."""

        if isinstance(
            error,
            AresAdminRequired,
        ):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    (
                        "You need Ares administrative access "
                        "to manage countdown channels."
                    ),
                    ephemeral=True,
                )
            return

        raise error


async def setup(
    bot: commands.Bot,
):
    """Load the generic Ares countdown system."""

    await bot.add_cog(
        CountdownCog(bot)
    )
