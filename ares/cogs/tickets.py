import asyncio
import re
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import commands

from ares.config import ARES_RED, ROLE_DB_PATH


GENERAL_TICKET_EMOJI = "⬛"
REPORT_TICKET_EMOJI = "🟧"

GENERAL_TICKET_TYPE = "general"
REPORT_TICKET_TYPE = "report"

PANEL_CHANNEL_NAME = "create-a-ticket"


class ResolveTicketView(discord.ui.View):
    """Persistent moderator-only ticket resolution controls."""

    def __init__(
        self,
        cog: "TicketSystemCog",
    ):
        super().__init__(
            timeout=None
        )
        self.cog = cog

    @discord.ui.button(
        label="Resolved",
        style=discord.ButtonStyle.success,
        custom_id="ares:tickets:resolved",
        emoji="✅",
    )
    async def resolved(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ):
        """Delete a ticket when an authorized helper resolves it."""

        await self.cog.resolve_ticket_from_button(
            interaction
        )


class TicketSystemCog(commands.Cog):
    """Ares reaction-based support ticket system."""

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot
        self.initialize_database()
        self.resolve_view = ResolveTicketView(
            self
        )

    async def cog_load(self):
        """Register the persistent Resolve button."""

        self.bot.add_view(
            self.resolve_view
        )

    # ========================================================
    # DATABASE
    # ========================================================

    @staticmethod
    def initialize_database():
        """Create persistent ticket-system tables."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ticket_panels (
                guild_id INTEGER PRIMARY KEY,
                panel_channel_id INTEGER NOT NULL,
                panel_message_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                helper_role_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                channel_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                ticket_type TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(guild_id, user_id)
            )
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tickets_guild_user
            ON tickets (
                guild_id,
                user_id
            )
            """
        )

        connection.commit()
        connection.close()

    @staticmethod
    def save_panel(
        guild_id: int,
        panel_channel_id: int,
        panel_message_id: int,
        category_id: int,
        helper_role_id: int,
    ):
        """Save or replace ticket panel configuration."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO ticket_panels (
                guild_id,
                panel_channel_id,
                panel_message_id,
                category_id,
                helper_role_id,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id)
            DO UPDATE SET
                panel_channel_id = excluded.panel_channel_id,
                panel_message_id = excluded.panel_message_id,
                category_id = excluded.category_id,
                helper_role_id = excluded.helper_role_id,
                created_at = excluded.created_at
            """,
            (
                guild_id,
                panel_channel_id,
                panel_message_id,
                category_id,
                helper_role_id,
                int(
                    time.time()
                ),
            ),
        )

        connection.commit()
        connection.close()

    @staticmethod
    def get_panel(
        guild_id: int,
    ) -> tuple[int, int, int, int] | None:
        """Return ticket panel configuration for a guild."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                panel_channel_id,
                panel_message_id,
                category_id,
                helper_role_id
            FROM ticket_panels
            WHERE guild_id = ?
            """,
            (
                guild_id,
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
            int(row[3]),
        )

    @staticmethod
    def get_ticket_for_user(
        guild_id: int,
        user_id: int,
    ) -> tuple[int, str] | None:
        """Return an active ticket for a member."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                channel_id,
                ticket_type
            FROM tickets
            WHERE guild_id = ?
            AND user_id = ?
            """,
            (
                guild_id,
                user_id,
            ),
        )

        row = cursor.fetchone()

        connection.close()

        if row is None:
            return None

        return (
            int(row[0]),
            str(row[1]),
        )

    @staticmethod
    def get_ticket_by_channel(
        channel_id: int,
    ) -> tuple[int, int, str] | None:
        """Return guild, owner, and type for a ticket channel."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                guild_id,
                user_id,
                ticket_type
            FROM tickets
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
            str(row[2]),
        )

    @staticmethod
    def save_ticket(
        channel_id: int,
        guild_id: int,
        user_id: int,
        ticket_type: str,
    ):
        """Save an active ticket."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO tickets (
                channel_id,
                guild_id,
                user_id,
                ticket_type,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                channel_id,
                guild_id,
                user_id,
                ticket_type,
                int(
                    time.time()
                ),
            ),
        )

        connection.commit()
        connection.close()

    @staticmethod
    def delete_ticket(
        channel_id: int,
    ):
        """Delete an active ticket record."""

        connection = sqlite3.connect(
            ROLE_DB_PATH
        )

        cursor = connection.cursor()

        cursor.execute(
            """
            DELETE FROM tickets
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

    @staticmethod
    def parse_id(
        value: str,
    ) -> int | None:
        """Parse a raw Discord ID, role mention, or channel mention."""

        value = value.strip()

        if value.isdigit():
            return int(
                value
            )

        match = re.fullmatch(
            r"<[@#&]!?(\d+)>",
            value,
        )

        if match is None:
            return None

        return int(
            match.group(1)
        )

    @staticmethod
    def safe_channel_fragment(
        member: discord.Member,
    ) -> str:
        """Return a safe text-channel name fragment."""

        value = member.display_name.casefold()

        value = re.sub(
            r"[^a-z0-9-]+",
            "-",
            value,
        )

        value = re.sub(
            r"-+",
            "-",
            value,
        ).strip("-")

        if not value:
            return (
                f"user-{member.id}"
            )

        return value[
            :40
        ]

    @staticmethod
    def build_panel_embed() -> discord.Embed:
        """Build the public ticket creation panel."""

        embed = discord.Embed(
            title="ARES GROUP // SUPPORT",
            description=(
                "Need assistance? React below to open a private ticket.\n\n"
                f"{GENERAL_TICKET_EMOJI} **General Help**\n"
                "Questions, support, server help, or other general issues.\n\n"
                f"{REPORT_TICKET_EMOJI} **Report a Member**\n"
                "Report conduct from a member in an Ares Group community server.\n\n"
                "**One active ticket per member at a time.**"
            ),
            color=ARES_RED,
        )

        embed.set_footer(
            text="ARES GROUP // SUPPORT TICKETS"
        )

        return embed

    @staticmethod
    def build_ticket_embed(
        member: discord.Member,
        ticket_type: str,
    ) -> discord.Embed:
        """Build the initial ticket prompt."""

        if ticket_type == REPORT_TICKET_TYPE:
            title = "ARES GROUP // MEMBER REPORT"
            prompt = (
                "Please provide as much of the following as possible:\n\n"
                "• The member's Discord name and/or Steam name\n"
                "• What happened\n"
                "• Which Ares community server it happened on\n"
                "• Approximate time of the incident\n"
                "• Any screenshots, clips, logs, or other evidence\n\n"
                "A member of the Ares team will review your report."
            )
        else:
            title = "ARES GROUP // GENERAL HELP"
            prompt = (
                "Please describe what you need help with and include as much "
                "relevant detail as possible.\n\n"
                "A member of the Ares team will be with you shortly."
            )

        embed = discord.Embed(
            title=title,
            description=prompt,
            color=ARES_RED,
        )

        embed.add_field(
            name="Opened By",
            value=member.mention,
            inline=False,
        )

        embed.set_footer(
            text="A moderator can close this ticket with the Resolved button."
        )

        return embed

    @staticmethod
    def is_ticket_helper(
        member: discord.Member,
        helper_role: discord.Role | None,
    ) -> bool:
        """Return whether a member may resolve tickets."""

        if member.guild.owner_id == member.id:
            return True

        if member.guild_permissions.administrator:
            return True

        if member.guild_permissions.manage_guild:
            return True

        if (
            helper_role is not None
            and helper_role in member.roles
        ):
            return True

        return False

    async def remove_panel_reaction(
        self,
        payload: discord.RawReactionActionEvent,
    ):
        """Best effort removal so panel reactions behave like buttons."""

        guild = self.bot.get_guild(
            payload.guild_id
        )

        if guild is None:
            return

        channel = guild.get_channel(
            payload.channel_id
        )

        if not isinstance(
            channel,
            discord.TextChannel,
        ):
            return

        try:
            message = await channel.fetch_message(
                payload.message_id
            )

            member = guild.get_member(
                payload.user_id
            )

            if member is None:
                return

            await message.remove_reaction(
                payload.emoji,
                member,
            )

        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    async def notify_existing_ticket(
        self,
        member: discord.Member,
        channel: discord.TextChannel,
    ):
        """Tell a member they already have an active ticket."""

        try:
            await member.send(
                (
                    "You already have an active Ares ticket: "
                    f"{channel.mention}"
                )
            )
        except (
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    # ========================================================
    # /TICKETSETUP
    # ========================================================

    @app_commands.command(
        name="ticketsetup",
        description="Create or replace the Ares support ticket panel.",
    )
    @app_commands.describe(
        category_id="Category ID where the ticket system should live.",
        helper_role_id="Role ID allowed to view and resolve tickets.",
    )
    @app_commands.default_permissions(
        manage_guild=True
    )
    @app_commands.checks.has_permissions(
        manage_guild=True
    )
    async def ticket_setup(
        self,
        interaction: discord.Interaction,
        category_id: str,
        helper_role_id: str,
    ):
        """Create create-a-ticket and configure the ticket system."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "Run this command inside a server.",
                ephemeral=True,
            )
            return

        parsed_category_id = self.parse_id(
            category_id
        )

        parsed_helper_role_id = self.parse_id(
            helper_role_id
        )

        if parsed_category_id is None:
            await interaction.response.send_message(
                "Provide a valid category ID.",
                ephemeral=True,
            )
            return

        if parsed_helper_role_id is None:
            await interaction.response.send_message(
                "Provide a valid helper role ID.",
                ephemeral=True,
            )
            return

        guild = interaction.guild

        category = guild.get_channel(
            parsed_category_id
        )

        if not isinstance(
            category,
            discord.CategoryChannel,
        ):
            await interaction.response.send_message(
                "That category ID does not point to a category in this server.",
                ephemeral=True,
            )
            return

        helper_role = guild.get_role(
            parsed_helper_role_id
        )

        if helper_role is None:
            await interaction.response.send_message(
                "That helper role ID does not point to a role in this server.",
                ephemeral=True,
            )
            return

        bot_member = guild.me

        if bot_member is None:
            await interaction.response.send_message(
                "Unable to verify my permissions.",
                ephemeral=True,
            )
            return

        if not bot_member.guild_permissions.manage_channels:
            await interaction.response.send_message(
                "Ares needs **Manage Channels** to configure tickets.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        existing_panel = self.get_panel(
            guild.id
        )

        panel_channel = None

        if existing_panel is not None:
            candidate = guild.get_channel(
                existing_panel[0]
            )

            if (
                isinstance(
                    candidate,
                    discord.TextChannel,
                )
                and candidate.category_id == category.id
                and candidate.name == PANEL_CHANNEL_NAME
            ):
                panel_channel = candidate

        if panel_channel is None:
            for channel in category.text_channels:
                if channel.name == PANEL_CHANNEL_NAME:
                    panel_channel = channel
                    break

        try:
            if panel_channel is None:
                panel_channel = await guild.create_text_channel(
                    PANEL_CHANNEL_NAME,
                    category=category,
                    reason=(
                        f"Ares ticket setup by "
                        f"{interaction.user}"
                    ),
                )

            panel_permissions = panel_channel.permissions_for(
                bot_member
            )

            missing_permissions = []

            if not panel_permissions.view_channel:
                missing_permissions.append(
                    "View Channel"
                )

            if not panel_permissions.send_messages:
                missing_permissions.append(
                    "Send Messages"
                )

            if not panel_permissions.embed_links:
                missing_permissions.append(
                    "Embed Links"
                )

            if not panel_permissions.add_reactions:
                missing_permissions.append(
                    "Add Reactions"
                )

            if not panel_permissions.manage_messages:
                missing_permissions.append(
                    "Manage Messages"
                )

            if missing_permissions:
                await interaction.followup.send(
                    (
                        f"Ares cannot use {panel_channel.mention}.\n\n"
                        "Missing permissions: **"
                        + ", ".join(
                            missing_permissions
                        )
                        + "**"
                    ),
                    ephemeral=True,
                )
                return

            panel_message = await panel_channel.send(
                embed=self.build_panel_embed(),
                allowed_mentions=discord.AllowedMentions.none(),
            )

            await panel_message.add_reaction(
                GENERAL_TICKET_EMOJI
            )

            await panel_message.add_reaction(
                REPORT_TICKET_EMOJI
            )

        except (
            discord.Forbidden,
            discord.HTTPException,
        ) as error:
            await interaction.followup.send(
                (
                    "I could not create the ticket system.\n\n"
                    f"`{error}`"
                ),
                ephemeral=True,
            )
            return

        self.save_panel(
            guild.id,
            panel_channel.id,
            panel_message.id,
            category.id,
            helper_role.id,
        )

        await interaction.followup.send(
            (
                "Ticket system configured.\n\n"
                f"Panel Channel: {panel_channel.mention}\n"
                f"Category: **{category.name}**\n"
                f"Helper Role: **{helper_role.name}**"
            ),
            ephemeral=True,
        )

    # ========================================================
    # REACTION HANDLER
    # ========================================================

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self,
        payload: discord.RawReactionActionEvent,
    ):
        """Create a ticket from a panel reaction."""

        if payload.guild_id is None:
            return

        if (
            self.bot.user is not None
            and payload.user_id == self.bot.user.id
        ):
            return

        panel = self.get_panel(
            payload.guild_id
        )

        if panel is None:
            return

        (
            panel_channel_id,
            panel_message_id,
            category_id,
            helper_role_id,
        ) = panel

        if payload.message_id != panel_message_id:
            return

        if payload.channel_id != panel_channel_id:
            return

        emoji = str(
            payload.emoji
        )

        if emoji == GENERAL_TICKET_EMOJI:
            ticket_type = GENERAL_TICKET_TYPE
        elif emoji == REPORT_TICKET_EMOJI:
            ticket_type = REPORT_TICKET_TYPE
        else:
            return

        guild = self.bot.get_guild(
            payload.guild_id
        )

        if guild is None:
            return

        member = guild.get_member(
            payload.user_id
        )

        if member is None:
            try:
                member = await guild.fetch_member(
                    payload.user_id
                )
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ):
                return

        if member.bot:
            return

        existing = self.get_ticket_for_user(
            guild.id,
            member.id,
        )

        if existing is not None:
            existing_channel = guild.get_channel(
                existing[0]
            )

            if isinstance(
                existing_channel,
                discord.TextChannel,
            ):
                await self.notify_existing_ticket(
                    member,
                    existing_channel,
                )

                await self.remove_panel_reaction(
                    payload
                )
                return

            self.delete_ticket(
                existing[0]
            )

        category = guild.get_channel(
            category_id
        )

        helper_role = guild.get_role(
            helper_role_id
        )

        panel_channel = guild.get_channel(
            panel_channel_id
        )

        if not isinstance(
            category,
            discord.CategoryChannel,
        ):
            return

        if helper_role is None:
            return

        if not isinstance(
            panel_channel,
            discord.TextChannel,
        ):
            return

        bot_member = guild.me

        if bot_member is None:
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False,
            ),
            member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            helper_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            bot_member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                manage_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
        }

        safe_name = self.safe_channel_fragment(
            member
        )

        type_prefix = (
            "report"
            if ticket_type == REPORT_TICKET_TYPE
            else "help"
        )

        channel_name = (
            f"{type_prefix}-{safe_name}"
        )[
            :100
        ]

        try:
            ticket_channel = await guild.create_text_channel(
                channel_name,
                category=category,
                overwrites=overwrites,
                topic=(
                    f"Ares {ticket_type} ticket | "
                    f"User ID: {member.id}"
                ),
                position=(
                    panel_channel.position
                    + 1
                ),
                reason=(
                    f"Ares ticket opened by {member}"
                ),
            )
        except (
            discord.Forbidden,
            discord.HTTPException,
        ) as error:
            print(
                "TICKETS: Failed to create ticket channel: "
                f"{error}"
            )
            return

        try:
            self.save_ticket(
                ticket_channel.id,
                guild.id,
                member.id,
                ticket_type,
            )
        except sqlite3.IntegrityError:
            try:
                await ticket_channel.delete(
                    reason="Duplicate active ticket protection"
                )
            except discord.HTTPException:
                pass
            return

        try:
            await ticket_channel.send(
                content=(
                    f"{member.mention} {helper_role.mention}\n"
                    "Your ticket has been created. "
                    "A member of the Ares team will be with you shortly."
                ),
                embed=self.build_ticket_embed(
                    member,
                    ticket_type,
                ),
                view=self.resolve_view,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=True,
                    everyone=False,
                ),
            )
        except (
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

        await self.remove_panel_reaction(
            payload
        )

    # ========================================================
    # RESOLVE BUTTON
    # ========================================================

    async def resolve_ticket_from_button(
        self,
        interaction: discord.Interaction,
    ):
        """Resolve a ticket from the moderator button."""

        if (
            interaction.guild is None
            or not isinstance(
                interaction.channel,
                discord.TextChannel,
            )
            or not isinstance(
                interaction.user,
                discord.Member,
            )
        ):
            await interaction.response.send_message(
                "This button only works inside an Ares ticket.",
                ephemeral=True,
            )
            return

        ticket = self.get_ticket_by_channel(
            interaction.channel.id
        )

        if ticket is None:
            await interaction.response.send_message(
                "This channel is not an active Ares ticket.",
                ephemeral=True,
            )
            return

        panel = self.get_panel(
            interaction.guild.id
        )

        if panel is None:
            await interaction.response.send_message(
                "The Ares ticket configuration could not be found.",
                ephemeral=True,
            )
            return

        helper_role = interaction.guild.get_role(
            panel[3]
        )

        if not self.is_ticket_helper(
            interaction.user,
            helper_role,
        ):
            await interaction.response.send_message(
                "Only Ares moderators can resolve tickets.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            (
                f"✅ Ticket resolved by "
                f"{interaction.user.mention}. "
                "This channel will be deleted in 3 seconds."
            ),
            allowed_mentions=discord.AllowedMentions(
                users=True
            ),
        )

        await asyncio.sleep(
            3
        )

        self.delete_ticket(
            interaction.channel.id
        )

        try:
            await interaction.channel.delete(
                reason=(
                    f"Ares ticket resolved by "
                    f"{interaction.user}"
                )
            )
        except discord.HTTPException:
            pass

    # ========================================================
    # !CLOSE - MODERATORS ONLY
    # ========================================================

    @commands.command(
        name="close",
    )
    async def close_ticket(
        self,
        ctx: commands.Context,
    ):
        """Close the current Ares ticket as a moderator."""

        if (
            ctx.guild is None
            or not isinstance(
                ctx.channel,
                discord.TextChannel,
            )
            or not isinstance(
                ctx.author,
                discord.Member,
            )
        ):
            return

        ticket = self.get_ticket_by_channel(
            ctx.channel.id
        )

        if ticket is None:
            return

        panel = self.get_panel(
            ctx.guild.id
        )

        if panel is None:
            return

        helper_role = ctx.guild.get_role(
            panel[3]
        )

        if not self.is_ticket_helper(
            ctx.author,
            helper_role,
        ):
            await ctx.send(
                "Only Ares moderators can close tickets."
            )
            return

        await ctx.send(
            (
                f"✅ Ticket resolved by {ctx.author.mention}. "
                "This channel will be deleted in 3 seconds."
            ),
            allowed_mentions=discord.AllowedMentions(
                users=True
            ),
        )

        await asyncio.sleep(
            3
        )

        self.delete_ticket(
            ctx.channel.id
        )

        try:
            await ctx.channel.delete(
                reason=(
                    f"Ares ticket resolved by "
                    f"{ctx.author}"
                )
            )
        except discord.HTTPException:
            pass

    # ========================================================
    # CLEANUP
    # ========================================================

    @commands.Cog.listener()
    async def on_guild_channel_delete(
        self,
        channel: discord.abc.GuildChannel,
    ):
        """Clear a ticket record if its channel is deleted manually."""

        if isinstance(
            channel,
            discord.TextChannel,
        ):
            ticket = self.get_ticket_by_channel(
                channel.id
            )

            if ticket is not None:
                self.delete_ticket(
                    channel.id
                )

    # ========================================================
    # ERRORS
    # ========================================================

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        """Handle ticket setup permission errors."""

        if isinstance(
            error,
            app_commands.MissingPermissions,
        ):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    (
                        "You need the **Manage Server** permission "
                        "to configure Ares tickets."
                    ),
                    ephemeral=True,
                )
            return

        raise error


async def setup(
    bot: commands.Bot,
):
    """Load the Ares ticket system."""

    await bot.add_cog(
        TicketSystemCog(bot)
    )
