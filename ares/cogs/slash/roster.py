import asyncio
import re

import discord
from discord import app_commands
from discord.ext import commands

from ares.config import ARES_RED
from ares.database import (
    clear_roster_message,
    get_roster_message,
    get_roster_role_ids,
    get_roster_tabs,
    remove_roster_tab,
    save_roster_tab,
    set_roster_message,
)
from ares.permissions import (
    AresAdminRequired,
    ares_admin_only,
)


CUSTOM_EMOJI_PATTERN = re.compile(
    r"^<a?:[A-Za-z0-9_]+:(\d+)>$"
)

MAX_ROSTER_TABS = 20
MAX_FIELD_VALUE_LENGTH = 1024
AUTO_REFRESH_DEBOUNCE_SECONDS = 1.0


class RosterCog(commands.Cog):
    """Create and maintain the persistent Ares combat roster."""

    roster = app_commands.Group(
        name="roster",
        description="Configure the Ares combat roster.",
    )

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot
        self.refresh_locks: dict[int, asyncio.Lock] = {}
        self.refresh_tasks: dict[int, asyncio.Task] = {}

    # ========================================================
    # HELPERS
    # ========================================================

    @staticmethod
    def parse_channel_id(
        destination: str,
    ) -> int | None:
        """Parse a channel ID or Discord channel mention."""

        destination = destination.strip()

        if destination.isdigit():
            return int(
                destination
            )

        match = re.fullmatch(
            r"<#(\d+)>",
            destination,
        )

        if match is None:
            return None

        return int(
            match.group(1)
        )

    @staticmethod
    def validate_emoji(
        guild: discord.Guild,
        emoji: str,
    ) -> str | None:
        """Validate a Unicode or same-server custom emoji."""

        emoji = emoji.strip()

        if not emoji:
            return None

        custom_match = CUSTOM_EMOJI_PATTERN.fullmatch(
            emoji
        )

        if custom_match is None:
            # Unicode emoji are stored as their normal text form.
            return emoji

        emoji_id = int(
            custom_match.group(1)
        )

        if guild.get_emoji(
            emoji_id
        ) is None:
            return None

        return emoji

    @staticmethod
    def member_roster_entry(
        member: discord.Member,
    ) -> str:
        """Return the member as a clickable Discord mention."""

        return member.mention

    def build_roster_section(
        self,
        role: discord.Role,
        name: str,
        emoji: str,
        verified_members: list[discord.Member] | None = None,
    ) -> str:
        """Build one plain-text roster section with exact line spacing."""

        source_members = (
            verified_members
            if verified_members is not None
            else role.members
        )

        members = sorted(
            (
                member
                for member in source_members
                if not member.bot
            ),
            key=lambda member: member.display_name.casefold(),
        )

        safe_name = discord.utils.escape_markdown(
            discord.utils.escape_mentions(
                name
            )
        )

        lines = [
            f"{emoji}  {safe_name} - {len(members)}",
            "",
        ]

        if not members:
            lines.append(
                "*No members assigned.*"
            )
        else:
            for member in members:
                lines.append(
                    self.member_roster_entry(
                        member
                    )
                )

        return "\n".join(
            lines
        )

    def build_roster_embed(
        self,
        guild: discord.Guild,
        verified_members_by_role: dict[
            int,
            list[discord.Member],
        ] | None = None,
    ) -> discord.Embed:
        """Build the current roster as one plain-text embed body."""

        tabs = get_roster_tabs(
            guild.id
        )

        embed = discord.Embed(
            title="ARES GROUP // ROSTER",
            color=ARES_RED,
        )

        if not tabs:
            embed.description = (
                "No combat roles have been configured yet."
            )

            embed.set_footer(
                text="ARES GROUP // COMBAT ROSTER"
            )

            return embed

        sections = []

        for (
            name,
            emoji,
            role_id,
            _sort_order,
        ) in tabs:
            role = guild.get_role(
                role_id
            )

            safe_name = discord.utils.escape_markdown(
                discord.utils.escape_mentions(
                    name
                )
            )

            if role is None:
                sections.append(
                    (
                        f"{emoji}  {safe_name} - unavailable\n\n"
                        "*Configured Discord role no longer exists.*"
                    )
                )

                continue

            verified_members = None

            if verified_members_by_role is not None:
                verified_members = (
                    verified_members_by_role.get(
                        role.id,
                        [],
                    )
                )

            sections.append(
                self.build_roster_section(
                    role,
                    name,
                    emoji,
                    verified_members,
                )
            )

        # Exactly one blank line separates the final member of one
        # role from the next role heading. Each role heading then has
        # one blank line before its first member.
        description = "\n\n".join(
            sections
        )

        # Discord embed descriptions are limited to 4096 characters.
        if len(description) > 4096:
            description = (
                description[:4060]
                + "\n\n*Roster truncated. Use `!roster` again after "
                "roles are split into smaller groups.*"
            )

        embed.description = description

        embed.set_footer(
            text="ARES GROUP // COMBAT ROSTER"
        )

        embed.timestamp = discord.utils.utcnow()

        return embed

    def get_refresh_lock(
        self,
        guild_id: int,
    ) -> asyncio.Lock:
        """Return the refresh lock for a guild."""

        lock = self.refresh_locks.get(
            guild_id
        )

        if lock is None:
            lock = asyncio.Lock()

            self.refresh_locks[
                guild_id
            ] = lock

        return lock

    def schedule_roster_refresh(
        self,
        guild: discord.Guild,
        delay: float = AUTO_REFRESH_DEBOUNCE_SECONDS,
    ):
        """Coalesce bursts of automatic roster refresh requests."""

        existing = self.refresh_tasks.get(
            guild.id
        )

        if (
            existing is not None
            and not existing.done()
        ):
            existing.cancel()

        self.refresh_tasks[
            guild.id
        ] = asyncio.create_task(
            self._debounced_roster_refresh(
                guild,
                delay,
            )
        )

    async def _debounced_roster_refresh(
        self,
        guild: discord.Guild,
        delay: float,
    ):
        """Wait briefly, then perform one roster refresh."""

        try:
            await asyncio.sleep(
                delay
            )

        except asyncio.CancelledError:
            return

        # Remove the timer before doing the network request. If another
        # role event arrives while the edit is in progress, it can queue
        # one follow-up refresh rather than cancelling the active edit.
        current = asyncio.current_task()

        if self.refresh_tasks.get(
            guild.id
        ) is current:
            self.refresh_tasks.pop(
                guild.id,
                None,
            )

        await self.refresh_roster(
            guild
        )

    def cog_unload(
        self,
    ):
        """Cancel pending debounce timers when the cog unloads."""

        for task in self.refresh_tasks.values():
            if not task.done():
                task.cancel()

        self.refresh_tasks.clear()

    async def verify_roster_members(
        self,
        guild: discord.Guild,
    ) -> tuple[
        dict[int, list[discord.Member]],
        int,
        int,
    ]:
        """Resolve roster members directly through Discord.

        This intentionally performs API member lookups only for a manual
        /roster refresh. Automatic refreshes continue using the normal
        member cache.
        """

        tabs = get_roster_tabs(
            guild.id
        )

        role_member_ids: dict[
            int,
            set[int],
        ] = {}

        unique_member_ids: set[int] = set()

        for (
            _name,
            _emoji,
            role_id,
            _sort_order,
        ) in tabs:
            role = guild.get_role(
                role_id
            )

            if role is None:
                continue

            member_ids = {
                member.id
                for member in role.members
                if not member.bot
            }

            role_member_ids[
                role.id
            ] = member_ids

            unique_member_ids.update(
                member_ids
            )

        resolved_members: dict[
            int,
            discord.Member,
        ] = {}

        unresolved_count = 0

        for member_id in sorted(
            unique_member_ids
        ):
            try:
                member = await guild.fetch_member(
                    member_id
                )

            except discord.NotFound:
                unresolved_count += 1
                continue

            except discord.Forbidden:
                # If Discord refuses direct member lookup, do not silently
                # treat the cached member as verified.
                unresolved_count += 1
                continue

            except discord.HTTPException as error:
                unresolved_count += 1

                print(
                    "ROSTER VERIFY: Failed to resolve member "
                    f"{member_id}: {error}"
                )
                continue

            if member.bot:
                continue

            resolved_members[
                member.id
            ] = member

        verified_members_by_role: dict[
            int,
            list[discord.Member],
        ] = {}

        for role_id, member_ids in role_member_ids.items():
            verified_members = []

            for member_id in member_ids:
                member = resolved_members.get(
                    member_id
                )

                if member is None:
                    continue

                # Confirm the freshly fetched member still has this role.
                # This protects against stale role.members cache entries.
                if not any(
                    member_role.id == role_id
                    for member_role in member.roles
                ):
                    continue

                verified_members.append(
                    member
                )

            verified_members_by_role[
                role_id
            ] = verified_members

        return (
            verified_members_by_role,
            len(
                resolved_members
            ),
            unresolved_count,
        )

    async def refresh_roster(
        self,
        guild: discord.Guild,
        verified_members_by_role: dict[
            int,
            list[discord.Member],
        ] | None = None,
    ) -> bool:
        """Edit the persistent roster message with current membership."""

        location = get_roster_message(
            guild.id
        )

        if location is None:
            return False

        channel_id, message_id = location

        channel = guild.get_channel(
            channel_id
        )

        if not isinstance(
            channel,
            discord.TextChannel,
        ):
            clear_roster_message(
                guild.id
            )

            return False

        lock = self.get_refresh_lock(
            guild.id
        )

        async with lock:
            try:
                message = await channel.fetch_message(
                    message_id
                )

            except discord.NotFound:
                clear_roster_message(
                    guild.id
                )

                return False

            except (
                discord.Forbidden,
                discord.HTTPException,
            ):
                return False

            embed = self.build_roster_embed(
                guild,
                verified_members_by_role,
            )

            try:
                await message.edit(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

            except (
                discord.Forbidden,
                discord.HTTPException,
            ):
                return False

        return True

    # ========================================================
    # PREFIX COMMAND: ROSTER
    # ========================================================

    @commands.command(
        name="roster",
    )
    async def roster_command(
        self,
        ctx: commands.Context,
    ):
        """Display the current Ares combat roster."""

        if ctx.guild is None:
            return

        embed = self.build_roster_embed(
            ctx.guild
        )

        await ctx.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ========================================================
    # /ROSTER CREATE
    # ========================================================

    @roster.command(
        name="create",
        description="Create the persistent Ares roster message.",
    )
    @app_commands.describe(
        destination=(
            "Existing channel ID or channel mention for the roster."
        ),
    )
    @ares_admin_only()
    async def roster_create(
        self,
        interaction: discord.Interaction,
        destination: str,
    ):
        """Create or move the persistent roster message."""

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
                    "Invalid destination. Provide an existing "
                    "channel ID or channel mention."
                ),
                ephemeral=True,
            )

            return

        channel = interaction.guild.get_channel(
            channel_id
        )

        if channel is None:
            await interaction.response.send_message(
                (
                    "That channel does not exist in this server.\n\n"
                    f"Channel ID: `{channel_id}`"
                ),
                ephemeral=True,
            )

            return

        if not isinstance(
            channel,
            discord.TextChannel,
        ):
            await interaction.response.send_message(
                "The roster destination must be a text channel.",
                ephemeral=True,
            )

            return

        bot_member = interaction.guild.me

        if bot_member is None:
            await interaction.response.send_message(
                "Unable to verify my permissions.",
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

        if not permissions.read_message_history:
            missing_permissions.append(
                "Read Message History"
            )

        if missing_permissions:
            await interaction.response.send_message(
                (
                    f"I cannot maintain a roster in {channel.mention}.\n\n"
                    "Missing permissions: **"
                    + ", ".join(
                        missing_permissions
                    )
                    + "**"
                ),
                ephemeral=True,
            )

            return

        old_location = get_roster_message(
            interaction.guild.id
        )

        embed = self.build_roster_embed(
            interaction.guild
        )

        try:
            roster_message = await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        except (
            discord.Forbidden,
            discord.HTTPException,
        ) as error:
            await interaction.response.send_message(
                (
                    "I could not create the roster message.\n\n"
                    f"`{error}`"
                ),
                ephemeral=True,
            )

            return

        set_roster_message(
            interaction.guild.id,
            channel.id,
            roster_message.id,
        )

        # Best-effort cleanup of the previous roster message so
        # repeated /roster create calls do not leave stale rosters.
        if old_location is not None:
            old_channel_id, old_message_id = old_location

            if old_message_id != roster_message.id:
                old_channel = interaction.guild.get_channel(
                    old_channel_id
                )

                if isinstance(
                    old_channel,
                    discord.TextChannel,
                ):
                    try:
                        old_message = await old_channel.fetch_message(
                            old_message_id
                        )

                        await old_message.delete()

                    except (
                        discord.NotFound,
                        discord.Forbidden,
                        discord.HTTPException,
                    ):
                        pass

        await interaction.response.send_message(
            (
                "Ares roster created.\n\n"
                f"Destination: {channel.mention}\n"
                f"Message ID: `{roster_message.id}`"
            ),
            ephemeral=True,
        )

    # ========================================================
    # /ROSTER ADD
    # ========================================================

    @roster.command(
        name="add",
        description="Add or update a combat role on the Ares roster.",
    )
    @app_commands.describe(
        name="Display name for the roster section.",
        emoji="Unicode emoji or same-server custom emoji.",
        role="Discord role represented by this roster section.",
    )
    @ares_admin_only()
    async def roster_add(
        self,
        interaction: discord.Interaction,
        name: str,
        emoji: str,
        role: discord.Role,
    ):
        """Add or update a configured roster tab."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )

            return

        name = name.strip()

        if not name:
            await interaction.response.send_message(
                "Roster section name cannot be empty.",
                ephemeral=True,
            )

            return

        if len(name) > 80:
            await interaction.response.send_message(
                "Roster section names cannot exceed 80 characters.",
                ephemeral=True,
            )

            return

        validated_emoji = self.validate_emoji(
            interaction.guild,
            emoji,
        )

        if validated_emoji is None:
            await interaction.response.send_message(
                (
                    "That emoji is invalid or the custom emoji is "
                    "not available in this server."
                ),
                ephemeral=True,
            )

            return

        existing_tabs = get_roster_tabs(
            interaction.guild.id
        )

        existing_keys = {
            tab_name.casefold()
            for (
                tab_name,
                _emoji,
                _role_id,
                _sort_order,
            ) in existing_tabs
        }

        if (
            name.casefold() not in existing_keys
            and len(existing_tabs) >= MAX_ROSTER_TABS
        ):
            await interaction.response.send_message(
                (
                    f"The roster is limited to {MAX_ROSTER_TABS} "
                    "configured sections."
                ),
                ephemeral=True,
            )

            return

        save_roster_tab(
            interaction.guild.id,
            name,
            validated_emoji,
            role.id,
        )

        refreshed = await self.refresh_roster(
            interaction.guild
        )

        response = (
            "Roster section saved.\n\n"
            f"Name: **{name}**\n"
            f"Emoji: {validated_emoji}\n"
            f"Role: **{role.name}**"
        )

        if not refreshed:
            response += (
                "\n\nNo persistent roster message exists yet. "
                "Run `/roster create` when you are ready to publish it."
            )

        await interaction.response.send_message(
            response,
            ephemeral=True,
        )

    # ========================================================
    # /ROSTER REMOVE
    # ========================================================

    @roster.command(
        name="remove",
        description="Remove a combat role from the Ares roster.",
    )
    @app_commands.describe(
        name="Name of the roster section to remove.",
    )
    @ares_admin_only()
    async def roster_remove(
        self,
        interaction: discord.Interaction,
        name: str,
    ):
        """Remove a configured roster tab."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )

            return

        removed = remove_roster_tab(
            interaction.guild.id,
            name.strip(),
        )

        if not removed:
            await interaction.response.send_message(
                (
                    "No roster section exists with the name "
                    f"`{name.strip()}`."
                ),
                ephemeral=True,
            )

            return

        await self.refresh_roster(
            interaction.guild
        )

        await interaction.response.send_message(
            f"Roster section `{name.strip()}` removed.",
            ephemeral=True,
        )

    # ========================================================
    # /ROSTER REFRESH
    # ========================================================

    @roster.command(
        name="refresh",
        description="Immediately refresh the persistent roster.",
    )
    @ares_admin_only()
    async def roster_refresh(
        self,
        interaction: discord.Interaction,
    ):
        """Force an immediate roster update."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )

            return

        await interaction.response.defer(
            ephemeral=True
        )

        (
            verified_members_by_role,
            resolved_count,
            unresolved_count,
        ) = await self.verify_roster_members(
            interaction.guild
        )

        refreshed = await self.refresh_roster(
            interaction.guild,
            verified_members_by_role,
        )

        if not refreshed:
            await interaction.followup.send(
                (
                    "No active roster message could be found. "
                    "Run `/roster create` to create one."
                ),
                ephemeral=True,
            )

            return

        response = (
            "Ares roster refreshed after re-resolving "
            f"**{resolved_count}** member(s) directly through Discord."
        )

        if unresolved_count > 0:
            response += (
                "\n\n"
                f"Skipped **{unresolved_count}** member ID(s) that "
                "Discord could not resolve."
            )

        await interaction.followup.send(
            response,
            ephemeral=True,
        )

    # ========================================================
    # /ROSTER STATUS
    # ========================================================

    @roster.command(
        name="status",
        description="Show the current Ares roster configuration.",
    )
    @ares_admin_only()
    async def roster_status(
        self,
        interaction: discord.Interaction,
    ):
        """Show persistent roster configuration."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )

            return

        location = get_roster_message(
            interaction.guild.id
        )

        tabs = get_roster_tabs(
            interaction.guild.id
        )

        lines = []

        if location is None:
            lines.append(
                "**Roster Message:** Not created"
            )

        else:
            channel_id, message_id = location

            channel = interaction.guild.get_channel(
                channel_id
            )

            if isinstance(
                channel,
                discord.TextChannel,
            ):
                destination = channel.mention

            else:
                destination = (
                    f"`{channel_id}` (channel unavailable)"
                )

            lines.extend(
                [
                    f"**Destination:** {destination}",
                    f"**Message ID:** `{message_id}`",
                ]
            )

        lines.append(
            f"**Configured Sections:** {len(tabs)}"
        )

        if tabs:
            lines.append(
                ""
            )

            for (
                name,
                emoji,
                role_id,
                _sort_order,
            ) in tabs:
                role = interaction.guild.get_role(
                    role_id
                )

                role_name = (
                    role.name
                    if role is not None
                    else f"Missing role ({role_id})"
                )

                lines.append(
                    f"{emoji} **{name}** → {role_name}"
                )

        await interaction.response.send_message(
            "\n".join(
                lines
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ========================================================
    # AUTOMATIC ROSTER UPDATES
    # ========================================================

    @commands.Cog.listener()
    async def on_member_update(
        self,
        before: discord.Member,
        after: discord.Member,
    ):
        """Refresh when a configured combat role changes."""

        before_roles = {
            role.id
            for role in before.roles
        }

        after_roles = {
            role.id
            for role in after.roles
        }

        changed_roles = (
            before_roles
            ^ after_roles
        )

        if not changed_roles:
            return

        roster_role_ids = get_roster_role_ids(
            after.guild.id
        )

        if not (
            changed_roles
            & roster_role_ids
        ):
            return

        self.schedule_roster_refresh(
            after.guild
        )

    @commands.Cog.listener()
    async def on_member_remove(
        self,
        member: discord.Member,
    ):
        """Refresh the persistent roster whenever a member leaves."""

        if get_roster_message(
            member.guild.id
        ) is None:
            return

        # Give Discord.py's member/role cache time to settle, while also
        # coalescing this update with any other nearby roster changes.
        self.schedule_roster_refresh(
            member.guild,
            delay=1.25,
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(
        self,
        role: discord.Role,
    ):
        """Refresh if a configured roster role is deleted."""

        roster_role_ids = get_roster_role_ids(
            role.guild.id
        )

        if role.id not in roster_role_ids:
            return

        self.schedule_roster_refresh(
            role.guild
        )

    @commands.Cog.listener()
    async def on_raw_message_delete(
        self,
        payload: discord.RawMessageDeleteEvent,
    ):
        """Clear roster location if the persistent message is deleted."""

        if payload.guild_id is None:
            return

        location = get_roster_message(
            payload.guild_id
        )

        if location is None:
            return

        _channel_id, message_id = location

        if payload.message_id != message_id:
            return

        clear_roster_message(
            payload.guild_id
        )

        print(
            "ROSTER: Persistent roster message was deleted. "
            f"Guild={payload.guild_id}"
        )

    # ========================================================
    # ERROR HANDLER
    # ========================================================

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        """Handle Ares roster slash-command errors."""

        if isinstance(
            error,
            AresAdminRequired,
        ):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    (
                        "You need Ares administrative access "
                        "to configure the roster."
                    ),
                    ephemeral=True,
                )

            return

        raise error


async def setup(
    bot: commands.Bot,
):
    """Load the Ares roster cog."""

    await bot.add_cog(
        RosterCog(bot)
    )


            
