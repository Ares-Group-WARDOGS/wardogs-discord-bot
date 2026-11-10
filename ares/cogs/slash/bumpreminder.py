import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ares.database import (
    delete_bump_reminder,
    get_bump_reminder_enabled,
    get_due_bump_reminders,
    save_bump_reminder,
    set_bump_reminder_enabled,
)
from ares.permissions import AresAdminRequired, ares_admin_only


# ============================================================
# DISBOARD CONFIGURATION
# ============================================================

DISBOARD_BOT_ID = 302050872383242240

BUMP_REMINDER_SECONDS = 7200


class BumpReminderCog(commands.Cog):
    """Handle DISBOARD bump reminders."""

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

    # ========================================================
    # COG LIFECYCLE
    # ========================================================

    async def cog_load(
        self,
    ):
        """Start the persistent reminder checker."""

        if not self.reminder_task.is_running():
            self.reminder_task.start()

    async def cog_unload(
        self,
    ):
        """Stop the reminder checker."""

        self.reminder_task.cancel()

    # ========================================================
    # /BUMPREMINDER
    # ========================================================

    @app_commands.command(
        name="bumpreminder",
        description="Enable or disable DISBOARD bump reminders.",
    )
    @app_commands.describe(
        mode="Turn DISBOARD bump reminders on or off.",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(
                name="On",
                value="on",
            ),
            app_commands.Choice(
                name="Off",
                value="off",
            ),
        ]
    )
    @ares_admin_only()
    async def bump_reminder(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
    ):
        """Enable or disable bump reminders."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        guild_id = interaction.guild.id

        # ----------------------------------------------------
        # ENABLE
        # ----------------------------------------------------

        if mode.value == "on":
            set_bump_reminder_enabled(
                guild_id,
                True,
            )

            await interaction.response.send_message(
                (
                    "DISBOARD bump reminders are now "
                    "**enabled**.\n\n"
                    "After a successful `/bump`, Ares will "
                    "ping the member who bumped the server "
                    "when the 2-hour cooldown expires."
                ),
                ephemeral=True,
            )

            return

        # ----------------------------------------------------
        # DISABLE
        # ----------------------------------------------------

        set_bump_reminder_enabled(
            guild_id,
            False,
        )

        # Cancel any reminder that is already pending.
        delete_bump_reminder(
            guild_id
        )

        await interaction.response.send_message(
            (
                "DISBOARD bump reminders are now "
                "**disabled**.\n\n"
                "Any pending bump reminder has been cancelled."
            ),
            ephemeral=True,
        )

    # ========================================================
    # SLASH COMMAND ERROR HANDLER
    # ========================================================

    @bump_reminder.error
    async def bump_reminder_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        """Handle /bumpreminder errors."""

        if isinstance(
            error,
            AresAdminRequired,
        ):
            await interaction.response.send_message(
                (
                    "You need Ares administrative access "
                    "to configure bump reminders."
                ),
                ephemeral=True,
            )
            return

        raise error

    # ========================================================
    # SUCCESSFUL BUMP DETECTION
    # ========================================================

    @staticmethod
    def is_successful_disboard_bump(
        message: discord.Message,
    ) -> bool:
        """Return whether a message is a successful DISBOARD bump."""

        if message.author.id != DISBOARD_BOT_ID:
            return False

        # ----------------------------------------------------
        # NORMAL MESSAGE CONTENT
        # ----------------------------------------------------

        if "bump done!" in message.content.lower():
            return True

        # ----------------------------------------------------
        # EMBED CONTENT
        # ----------------------------------------------------

        for embed in message.embeds:
            text_parts = []

            if embed.title:
                text_parts.append(
                    embed.title
                )

            if embed.description:
                text_parts.append(
                    embed.description
                )

            for field in embed.fields:
                text_parts.append(
                    field.name
                )

                text_parts.append(
                    field.value
                )

            combined_text = " ".join(
                text_parts
            ).lower()

            if "bump done!" in combined_text:
                return True

        return False

    async def process_disboard_message(
        self,
        message: discord.Message,
    ):
        """Process a possible successful DISBOARD bump."""

        if message.guild is None:
            return

        # Feature defaults to off unless explicitly enabled.
        if not get_bump_reminder_enabled(
            message.guild.id
        ):
            return

        if not self.is_successful_disboard_bump(
            message
        ):
            return

        # Discord attaches the member who invoked /bump
        # to DISBOARD's interaction response.
        metadata = message.interaction_metadata

        if metadata is None:
            print(
                "BUMP: Successful DISBOARD bump detected, "
                "but interaction metadata was unavailable."
            )
            return

        user = metadata.user

        if user is None:
            print(
                "BUMP: Successful DISBOARD bump detected, "
                "but the invoking user was unavailable."
            )
            return

        due_at = (
            int(time.time())
            + BUMP_REMINDER_SECONDS
        )

        # One pending reminder per guild.
        #
        # A new successful bump replaces the previous reminder.
        save_bump_reminder(
            message.guild.id,
            message.channel.id,
            user.id,
            due_at,
        )

        print(
            "BUMP: Successful DISBOARD bump detected. "
            f"Guild={message.guild.id} "
            f"User={user.id} "
            f"Channel={message.channel.id} "
            f"Due={due_at}"
        )

    # ========================================================
    # MESSAGE LISTENERS
    # ========================================================

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message,
    ):
        """Watch new DISBOARD messages for successful bumps."""

        await self.process_disboard_message(
            message
        )

    @commands.Cog.listener()
    async def on_message_edit(
        self,
        before: discord.Message,
        after: discord.Message,
    ):
        """Watch edited DISBOARD messages for successful bumps."""

        await self.process_disboard_message(
            after
        )

    # ========================================================
    # PERSISTENT REMINDER CHECKER
    # ========================================================

    @tasks.loop(
        seconds=30
    )
    async def reminder_task(
        self,
    ):
        """Send bump reminders whose cooldown has expired."""

        current_time = int(
            time.time()
        )

        reminders = get_due_bump_reminders(
            current_time
        )

        for (
            guild_id,
            channel_id,
            user_id,
            due_at,
        ) in reminders:
            # ------------------------------------------------
            # VERIFY FEATURE STILL ENABLED
            # ------------------------------------------------

            if not get_bump_reminder_enabled(
                guild_id
            ):
                delete_bump_reminder(
                    guild_id
                )
                continue

            # ------------------------------------------------
            # FIND GUILD
            # ------------------------------------------------

            guild = self.bot.get_guild(
                guild_id
            )

            if guild is None:
                continue

            # ------------------------------------------------
            # FIND ORIGINAL BUMP CHANNEL
            # ------------------------------------------------

            channel = guild.get_channel_or_thread(
                channel_id
            )

            if channel is None:
                print(
                    "BUMP: Reminder channel no longer exists. "
                    f"Guild={guild_id} "
                    f"Channel={channel_id}"
                )

                delete_bump_reminder(
                    guild_id
                )
                continue

            # ------------------------------------------------
            # SEND REMINDER
            # ------------------------------------------------

            try:
                await channel.send(
                    (
                        f"<@{user_id}> the server can be "
                        "bumped again! Use `/bump`."
                    ),
                    allowed_mentions=discord.AllowedMentions(
                        users=True
                    ),
                )

            except discord.Forbidden:
                print(
                    "BUMP: Missing permission to send reminder. "
                    f"Guild={guild_id} "
                    f"Channel={channel_id}"
                )
                continue

            except discord.HTTPException as error:
                print(
                    "ERROR sending bump reminder: "
                    f"{error}"
                )
                continue

            # Only remove it after the message successfully sends.
            delete_bump_reminder(
                guild_id
            )

            print(
                "BUMP: Reminder sent. "
                f"Guild={guild_id} "
                f"User={user_id}"
            )

    @reminder_task.before_loop
    async def before_reminder_task(
        self,
    ):
        """Wait until Ares is connected before checking reminders."""

        await self.bot.wait_until_ready()


async def setup(
    bot: commands.Bot,
):
    """Load the DISBOARD bump reminder cog."""

    await bot.add_cog(
        BumpReminderCog(bot)
    )
