import asyncio
import re
import time

import discord
from discord.ext import commands

from ares.prefixes import get_guild_prefix

from ares.config import ARES_RED
from ares.database import (
    delete_role_selection,
    get_reaction_locations_for_role,
    get_reaction_role,
    get_role_group,
    get_role_group_for_role,
    get_role_selections,
    get_roles_for_group,
    save_reaction_role,
    save_role_panel,
    save_role_selection,
)


GROUP_RECONCILE_DEBOUNCE_SECONDS = 0.65
GROUP_RECONCILE_SETTLE_SECONDS = 0.35
MAX_GROUP_RECONCILE_PASSES = 12


class RolePanelCog(commands.Cog):
    """Handle persistent reaction-role panels."""

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

        # One lock per member/group prevents rapid reactions from
        # racing each other and temporarily exceeding the group limit.
        self.group_locks: dict[
            tuple[int, int, int],
            asyncio.Lock,
        ] = {}

        # Grouped reactions are reconciled after a short quiet period.
        # One worker handles each guild/member/group combination and
        # re-runs whenever more reaction events arrive during processing.
        self.group_reconcile_tasks: dict[
            tuple[int, int, int],
            asyncio.Task,
        ] = {}

        self.group_reconcile_generations: dict[
            tuple[int, int, int],
            int,
        ] = {}

    @commands.group(
        name="rolepanel",
        invoke_without_command=True,
    )
    @commands.has_permissions(
        manage_roles=True,
    )
    async def rolepanel(
        self,
        ctx: commands.Context,
    ):
        """Manage reaction role panels."""
        prefix = get_guild_prefix(
            self.bot,
            ctx.guild.id if ctx.guild else None,
        )
        warning = await ctx.send(
            f"Use `{prefix}rolepanel create` "
            "followed by the panel configuration."
        )

        await warning.delete(
            delay=5
        )

    @rolepanel.command(
        name="create",
        aliases=["create:"],
    )
    @commands.has_permissions(
        manage_roles=True,
    )
    async def rolepanel_create(
        self,
        ctx: commands.Context,
        *,
        content: str,
    ):
        """Create a reaction-role panel while preserving input order."""

        content = content.lstrip()

        if content.startswith(":"):
            content = content[1:].lstrip()

        lines = content.splitlines()

        while lines and not lines[0].strip():
            lines.pop(0)

        while lines and not lines[-1].strip():
            lines.pop()

        if (
            lines
            and lines[-1].strip().upper() == "EOF"
        ):
            lines.pop()

        while lines and not lines[-1].strip():
            lines.pop()

        if not lines:
            warning = await ctx.send(
                "Role panel configuration is empty."
            )

            await warning.delete(
                delay=5
            )
            return

        title = lines.pop(0).strip()

        role_pattern = re.compile(
            r"^(.+?):\s+(\S+)\s+(?:<@&)?(\d+)>?$"
        )

        display_lines = []
        role_entries = []

        bot_member = ctx.guild.me

        for raw_line in lines:
            line = raw_line.strip()

            if not line:
                display_lines.append("")
                continue

            match = role_pattern.match(
                line
            )

            if not match:
                display_lines.append(
                    raw_line
                )
                continue

            label = match.group(1).strip()
            emoji_text = match.group(2).strip()
            role_id = int(
                match.group(3)
            )

            role = ctx.guild.get_role(
                role_id
            )

            if role is None:
                warning = await ctx.send(
                    f"Could not find role `{label}` "
                    f"with ID `{role_id}`."
                )

                await warning.delete(
                    delay=8
                )
                return

            if (
                bot_member is not None
                and role >= bot_member.top_role
            ):
                warning = await ctx.send(
                    f"I cannot manage **{role.name}** "
                    "because that role is above or equal "
                    "to my highest role."
                )

                await warning.delete(
                    delay=8
                )
                return

            role_entries.append(
                (
                    label,
                    emoji_text,
                    role,
                )
            )

            display_lines.append(
                f"**{label}:** {emoji_text}"
            )

            display_lines.append("")

        if not role_entries:
            warning = await ctx.send(
                "No valid role entries were found.\n"
                "Format: `Medic: 🩺 ROLE_ID`"
            )

            await warning.delete(
                delay=8
            )
            return

        while (
            display_lines
            and not display_lines[-1].strip()
        ):
            display_lines.pop()

        try:
            await ctx.message.delete()

        except discord.HTTPException:
            pass

        embed = discord.Embed(
            title=title,
            description="\n".join(
                display_lines
            ),
            color=ARES_RED,
        )

        embed.set_footer(
            text=(
                "React below to assign "
                "or remove a role."
            )
        )

        panel_message = await ctx.send(
            embed=embed
        )

        save_role_panel(
            panel_message.id,
            ctx.guild.id,
            ctx.channel.id,
        )

        for (
            label,
            emoji_text,
            role,
        ) in role_entries:
            try:
                parsed_emoji = (
                    discord.PartialEmoji.from_str(
                        emoji_text
                    )
                )

                if parsed_emoji.id is None:
                    reaction = emoji_text

                else:
                    reaction = parsed_emoji

                await panel_message.add_reaction(
                    reaction
                )

                save_reaction_role(
                    panel_message.id,
                    emoji_text,
                    role.id,
                )

            except discord.HTTPException as error:
                print(
                    "ERROR adding reaction "
                    f"{emoji_text}: {error}"
                )

    @rolepanel_create.error
    async def rolepanel_create_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ):
        """Handle role panel creation errors."""

        if isinstance(
            error,
            commands.MissingPermissions,
        ):
            warning = await ctx.send(
                f"{ctx.author.mention}, "
                "you need Manage Roles "
                "to create role panels."
            )

            await warning.delete(
                delay=5
            )
            return

        if isinstance(
            error,
            commands.MissingRequiredArgument,
        ):
            warning = await ctx.send(
                "Use `!rolepanel create` followed by "
                "the panel configuration."
            )

            await warning.delete(
                delay=8
            )
            return

        raise error

    @staticmethod
    def normalize_emoji(
        emoji: discord.PartialEmoji,
    ) -> str:
        """Convert Discord reaction emoji to stored text."""

        if emoji.id is not None:
            return str(emoji)

        return emoji.name

    def get_group_lock(
        self,
        guild_id: int,
        user_id: int,
        group_id: int,
    ) -> asyncio.Lock:
        """Return the serialization lock for one member and role group."""

        key = (
            guild_id,
            user_id,
            group_id,
        )

        lock = self.group_locks.get(
            key
        )

        if lock is None:
            lock = asyncio.Lock()

            self.group_locks[
                key
            ] = lock

        return lock

    @staticmethod
    def parse_saved_reaction(
        emoji_text: str,
    ) -> str | discord.PartialEmoji:
        """Convert a stored reaction string back into a Discord emoji."""

        parsed = discord.PartialEmoji.from_str(
            emoji_text
        )

        if parsed.id is None:
            return emoji_text

        return parsed

    async def remove_member_reaction_for_role(
        self,
        guild: discord.Guild,
        member: discord.Member,
        role_id: int,
    ) -> bool:
        """Remove a member's reaction for a configured role.

        Returns True when every known reaction location was successfully
        processed. A failed removal is retried by a later reconciliation
        pass instead of silently dropping the selection record.
        """

        locations = get_reaction_locations_for_role(
            role_id
        )

        if not locations:
            return True

        all_succeeded = True

        for (
            message_id,
            channel_id,
            emoji_text,
        ) in locations:
            channel = guild.get_channel(
                channel_id
            )

            if not isinstance(
                channel,
                discord.TextChannel,
            ):
                all_succeeded = False
                continue

            try:
                message = await channel.fetch_message(
                    message_id
                )

            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ) as error:
                all_succeeded = False

                print(
                    "ERROR fetching role-panel message "
                    f"{message_id}: {error}"
                )
                continue

            reaction = self.parse_saved_reaction(
                emoji_text
            )

            try:
                await message.remove_reaction(
                    reaction,
                    member,
                )

                print(
                    "ROLE GROUP REACTION REMOVE: "
                    f"{member} -> {emoji_text}"
                )

            except discord.Forbidden:
                all_succeeded = False

                print(
                    "ERROR: Cannot remove another member's reaction. "
                    "Ares needs Manage Messages in the role-panel channel."
                )

            except (
                discord.NotFound,
                discord.HTTPException,
            ) as error:
                all_succeeded = False

                print(
                    "ERROR removing grouped reaction "
                    f"{emoji_text}: {error}"
                )

        return all_succeeded

    def schedule_group_reconcile(
        self,
        guild: discord.Guild,
        user_id: int,
        group_id: int,
        group_name: str,
    ):
        """Mark a member/group dirty and ensure a worker is running."""

        key = (
            guild.id,
            user_id,
            group_id,
        )

        self.group_reconcile_generations[
            key
        ] = (
            self.group_reconcile_generations.get(
                key,
                0,
            )
            + 1
        )

        existing = self.group_reconcile_tasks.get(
            key
        )

        if (
            existing is not None
            and not existing.done()
        ):
            return

        self.group_reconcile_tasks[
            key
        ] = asyncio.create_task(
            self.group_reconcile_worker(
                guild,
                user_id,
                group_id,
                group_name,
            )
        )

    async def group_reconcile_worker(
        self,
        guild: discord.Guild,
        user_id: int,
        group_id: int,
        group_name: str,
    ):
        """Wait for reaction bursts, then reconcile until fully stable."""

        key = (
            guild.id,
            user_id,
            group_id,
        )

        try:
            passes = 0

            while passes < MAX_GROUP_RECONCILE_PASSES:
                observed_generation = (
                    self.group_reconcile_generations.get(
                        key,
                        0,
                    )
                )

                # Let a burst of clicks finish before touching Discord.
                await asyncio.sleep(
                    GROUP_RECONCILE_DEBOUNCE_SECONDS
                )

                # More clicks arrived while waiting. Restart the quiet
                # period so we reconcile the user's final intended state.
                if (
                    self.group_reconcile_generations.get(
                        key,
                        0,
                    )
                    != observed_generation
                ):
                    continue

                changed = await self.reconcile_group_once(
                    guild,
                    user_id,
                    group_id,
                    group_name,
                )

                passes += 1

                if changed:
                    # Give Discord role/reaction writes and their gateway
                    # events time to settle, then check again.
                    await asyncio.sleep(
                        GROUP_RECONCILE_SETTLE_SECONDS
                    )
                    continue

                # The first clean pass is not enough. Wait once more and
                # perform the requested final verification against fresh
                # Discord/member state after the last edit/event.
                await asyncio.sleep(
                    GROUP_RECONCILE_SETTLE_SECONDS
                )

                if (
                    self.group_reconcile_generations.get(
                        key,
                        0,
                    )
                    != observed_generation
                ):
                    continue

                final_changed = await self.reconcile_group_once(
                    guild,
                    user_id,
                    group_id,
                    group_name,
                )

                passes += 1

                if final_changed:
                    await asyncio.sleep(
                        GROUP_RECONCILE_SETTLE_SECONDS
                    )
                    continue

                if (
                    self.group_reconcile_generations.get(
                        key,
                        0,
                    )
                    == observed_generation
                ):
                    print(
                        "ROLE GROUP STABLE: "
                        f"guild={guild.id} user={user_id} "
                        f"group={group_name}"
                    )
                    return

            print(
                "WARNING: Role-group reconciliation reached "
                f"{MAX_GROUP_RECONCILE_PASSES} passes: "
                f"guild={guild.id} user={user_id} "
                f"group={group_name}"
            )

        finally:
            current = asyncio.current_task()

            if self.group_reconcile_tasks.get(
                key
            ) is current:
                self.group_reconcile_tasks.pop(
                    key,
                    None,
                )

            # If an event landed during final cleanup, make sure it is not
            # stranded after this worker exits.
            latest_generation = (
                self.group_reconcile_generations.get(
                    key,
                    0,
                )
            )

            if latest_generation > 0:
                # A stable worker returns only after seeing the latest
                # generation. Leaving the counter in place is harmless;
                # the next event increments it and starts a fresh worker.
                pass

    async def reconcile_group_once(
        self,
        guild: discord.Guild,
        user_id: int,
        group_id: int,
        group_name: str,
    ) -> bool:
        """Perform one serialized role/reaction conformity pass."""

        lock = self.get_group_lock(
            guild.id,
            user_id,
            group_id,
        )

        async with lock:
            group = get_role_group(
                guild.id,
                group_name,
            )

            if (
                group is None
                or group[0] != group_id
            ):
                return False

            max_roles = group[2]

            group_role_ids = get_roles_for_group(
                guild.id,
                group_name,
            )

            if group_role_ids is None:
                return False

            configured_role_ids = set(
                group_role_ids
            )

            try:
                member = await guild.fetch_member(
                    user_id
                )

            except discord.HTTPException:
                member = guild.get_member(
                    user_id
                )

                if member is None:
                    return False

            tracked = get_role_selections(
                guild.id,
                user_id,
                group_id,
            )

            # Ignore stale rows for roles no longer configured in the
            # group, and clean them out immediately.
            valid_tracked = []

            for (
                tracked_role_id,
                selected_at,
            ) in tracked:
                if tracked_role_id not in configured_role_ids:
                    delete_role_selection(
                        guild.id,
                        user_id,
                        group_id,
                        tracked_role_id,
                    )
                    continue

                valid_tracked.append(
                    (
                        tracked_role_id,
                        selected_at,
                    )
                )

            # Newest selections win. The user can click as many reactions
            # as they want during the debounce window; only the newest
            # max_roles selections survive the reconciliation.
            newest_first = sorted(
                valid_tracked,
                key=lambda item: (
                    item[1],
                    item[0],
                ),
                reverse=True,
            )

            keep_rows = newest_first[
                :max_roles
            ]

            excess_rows = newest_first[
                max_roles:
            ]

            keep_ids = {
                role_id
                for (
                    role_id,
                    _selected_at,
                ) in keep_rows
            }

            current_role_ids = {
                current_role.id
                for current_role in member.roles
            }

            changed = False

            # Remove excess selections one at a time. We remove the role,
            # then the user's reaction, then the DB selection only after
            # the reaction cleanup succeeds. A failure therefore remains
            # visible to the next pass and is retried.
            for (
                excess_role_id,
                _selected_at,
            ) in reversed(
                excess_rows
            ):
                excess_role = guild.get_role(
                    excess_role_id
                )

                if (
                    excess_role is not None
                    and excess_role_id in current_role_ids
                ):
                    try:
                        await member.remove_roles(
                            excess_role,
                            reason=(
                                "Ares reaction-role group limit: "
                                f"{group_name}"
                            ),
                        )

                        current_role_ids.discard(
                            excess_role_id
                        )

                        changed = True

                        print(
                            "ROLE GROUP EVICT: "
                            f"{member} -> {excess_role.name} "
                            f"from {group_name}"
                        )

                    except discord.Forbidden:
                        print(
                            "ERROR: Cannot remove "
                            f"{excess_role.name} while enforcing "
                            f"group {group_name}. Check role hierarchy."
                        )
                        continue

                    except discord.HTTPException as error:
                        print(
                            "ERROR removing grouped role "
                            f"{excess_role.name}: {error}"
                        )
                        continue

                reaction_removed = (
                    await self.remove_member_reaction_for_role(
                        guild,
                        member,
                        excess_role_id,
                    )
                )

                if reaction_removed:
                    delete_role_selection(
                        guild.id,
                        user_id,
                        group_id,
                        excess_role_id,
                    )

                    changed = True

            # Any tracked selection that survived the limit must have its
            # Discord role. Add missing roles one at a time.
            for (
                keep_role_id,
                _selected_at,
            ) in keep_rows:
                if keep_role_id in current_role_ids:
                    continue

                keep_role = guild.get_role(
                    keep_role_id
                )

                if keep_role is None:
                    continue

                try:
                    await member.add_roles(
                        keep_role,
                        reason=(
                            "Ares reaction-role group reconciliation: "
                            f"{group_name}"
                        ),
                    )

                    current_role_ids.add(
                        keep_role_id
                    )

                    changed = True

                    print(
                        "ROLE GROUP ASSIGN: "
                        f"{member} -> {keep_role.name} "
                        f"in {group_name}"
                    )

                except discord.Forbidden:
                    print(
                        "ERROR: Cannot assign "
                        f"{keep_role.name}. Check role hierarchy."
                    )

                except discord.HTTPException as error:
                    print(
                        "ERROR assigning grouped role "
                        f"{keep_role.name}: {error}"
                    )

            # A user removing a reaction deletes its tracking row first.
            # Remove any now-untracked group role so roles and reactions
            # converge on the same state.
            for configured_role_id in configured_role_ids:
                if (
                    configured_role_id in keep_ids
                    or configured_role_id not in current_role_ids
                ):
                    continue

                stale_role = guild.get_role(
                    configured_role_id
                )

                if stale_role is None:
                    continue

                try:
                    await member.remove_roles(
                        stale_role,
                        reason=(
                            "Ares reaction-role group reconciliation: "
                            f"{group_name}"
                        ),
                    )

                    current_role_ids.discard(
                        configured_role_id
                    )

                    changed = True

                    print(
                        "ROLE GROUP STALE REMOVE: "
                        f"{member} -> {stale_role.name} "
                        f"from {group_name}"
                    )

                except discord.Forbidden:
                    print(
                        "ERROR: Cannot remove "
                        f"{stale_role.name}. Check role hierarchy."
                    )

                except discord.HTTPException as error:
                    print(
                        "ERROR removing stale grouped role "
                        f"{stale_role.name}: {error}"
                    )

            return changed

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self,
        payload: discord.RawReactionActionEvent,
    ):
        """Record grouped reaction intent and reconcile after a short wait."""

        if self.bot.user is None:
            return

        if payload.user_id == self.bot.user.id:
            return

        if payload.guild_id is None:
            return

        emoji_text = self.normalize_emoji(
            payload.emoji
        )

        role_id = get_reaction_role(
            payload.message_id,
            emoji_text,
        )

        if role_id is None:
            return

        guild = self.bot.get_guild(
            payload.guild_id
        )

        if guild is None:
            return

        role = guild.get_role(
            role_id
        )

        if role is None:
            print(
                f"Configured role {role_id} "
                "no longer exists."
            )
            return

        group = get_role_group_for_role(
            role.id
        )

        # Ungrouped reactions retain the original immediate behavior.
        if (
            group is None
            or group[1] != guild.id
        ):
            member = payload.member

            if member is None:
                try:
                    member = await guild.fetch_member(
                        payload.user_id
                    )

                except discord.HTTPException:
                    return

            try:
                await member.add_roles(
                    role,
                    reason=(
                        "Ares reaction role "
                        "assignment"
                    ),
                )

                print(
                    f"ROLE ADD: {member} "
                    f"-> {role.name}"
                )

            except discord.Forbidden:
                print(
                    "ERROR: Cannot assign "
                    f"{role.name}. "
                    "Check role hierarchy."
                )

            except discord.HTTPException as error:
                print(
                    f"ERROR assigning "
                    f"{role.name}: {error}"
                )

            return

        (
            group_id,
            _guild_id,
            group_name,
            _max_roles,
        ) = group

        # The DB is the ordered intent queue. Every click gets a fresh
        # timestamp; the reconciler later keeps only the newest allowed
        # selections for this group.
        save_role_selection(
            guild.id,
            payload.user_id,
            group_id,
            role.id,
            time.time_ns(),
        )

        print(
            "ROLE GROUP INTENT ADD: "
            f"user={payload.user_id} role={role.name} "
            f"group={group_name}"
        )

        self.schedule_group_reconcile(
            guild,
            payload.user_id,
            group_id,
            group_name,
        )

    @commands.Cog.listener()
    async def on_raw_reaction_remove(
        self,
        payload: discord.RawReactionActionEvent,
    ):
        """Record grouped reaction removal and reconcile after a short wait."""

        if self.bot.user is None:
            return

        if payload.user_id == self.bot.user.id:
            return

        if payload.guild_id is None:
            return

        emoji_text = self.normalize_emoji(
            payload.emoji
        )

        role_id = get_reaction_role(
            payload.message_id,
            emoji_text,
        )

        if role_id is None:
            return

        guild = self.bot.get_guild(
            payload.guild_id
        )

        if guild is None:
            return

        role = guild.get_role(
            role_id
        )

        if role is None:
            return

        group = get_role_group_for_role(
            role.id
        )

        # Ungrouped reactions retain the original immediate behavior.
        if (
            group is None
            or group[1] != guild.id
        ):
            member = guild.get_member(
                payload.user_id
            )

            if member is None:
                try:
                    member = await guild.fetch_member(
                        payload.user_id
                    )

                except discord.HTTPException:
                    return

            try:
                await member.remove_roles(
                    role,
                    reason=(
                        "Ares reaction role "
                        "removal"
                    ),
                )

                print(
                    f"ROLE REMOVE: {member} "
                    f"-> {role.name}"
                )

            except discord.Forbidden:
                print(
                    "ERROR: Cannot remove "
                    f"{role.name}. "
                    "Check role hierarchy."
                )

            except discord.HTTPException as error:
                print(
                    f"ERROR removing "
                    f"{role.name}: {error}"
                )

            return

        (
            group_id,
            _guild_id,
            group_name,
            _max_roles,
        ) = group

        delete_role_selection(
            guild.id,
            payload.user_id,
            group_id,
            role.id,
        )

        print(
            "ROLE GROUP INTENT REMOVE: "
            f"user={payload.user_id} role={role.name} "
            f"group={group_name}"
        )

        self.schedule_group_reconcile(
            guild,
            payload.user_id,
            group_id,
            group_name,
        )


async def setup(
    bot: commands.Bot,
):
    """Load the role panel cog."""

    await bot.add_cog(
        RolePanelCog(bot)
    )
