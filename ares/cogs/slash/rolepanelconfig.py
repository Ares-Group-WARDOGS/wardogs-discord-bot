import discord
from discord import app_commands
from discord.ext import commands

from ares.database import (
    add_role_to_group,
    create_role_group,
    delete_role_group,
    get_role_group,
    get_role_groups,
    get_roles_for_group,
    remove_role_from_group,
    set_role_group_limit,
)
from ares.permissions import (
    AresAdminRequired,
    ares_admin_only,
)


MAX_GROUP_LIMIT = 100


class RolePanelConfigCog(commands.Cog):
    """Configure generic selection limits for reaction-role panels."""

    rolepanel = app_commands.Group(
        name="rolepanel",
        description="Configure Ares reaction-role limits.",
    )

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

    # ========================================================
    # HELPERS
    # ========================================================

    async def group_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Suggest configured role groups."""

        if interaction.guild is None:
            return []

        groups = get_role_groups(
            interaction.guild.id
        )

        current_key = current.casefold()

        return [
            app_commands.Choice(
                name=name,
                value=name,
            )
            for (
                _group_id,
                name,
                _max_roles,
            ) in groups
            if current_key in name.casefold()
        ][:25]

    @staticmethod
    async def send_not_found(
        interaction: discord.Interaction,
        group_name: str,
    ):
        """Send a standard missing-group response."""

        await interaction.response.send_message(
            (
                "No role group exists with the name "
                f"`{group_name}`."
            ),
            ephemeral=True,
        )

    # ========================================================
    # /ROLEPANEL GROUP-CREATE
    # ========================================================

    @rolepanel.command(
        name="group-create",
        description="Create a limited role-selection group.",
    )
    @app_commands.describe(
        name="Name of the group, such as Combat Roles.",
        max_roles="Maximum roles a member may hold from this group.",
    )
    @ares_admin_only()
    async def group_create(
        self,
        interaction: discord.Interaction,
        name: str,
        max_roles: app_commands.Range[int, 1, MAX_GROUP_LIMIT],
    ):
        """Create a generic role-limit group."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        name = name.strip()

        if not name:
            await interaction.response.send_message(
                "Group name cannot be empty.",
                ephemeral=True,
            )
            return

        if len(name) > 80:
            await interaction.response.send_message(
                "Group names cannot exceed 80 characters.",
                ephemeral=True,
            )
            return

        created = create_role_group(
            interaction.guild.id,
            name,
            int(max_roles),
        )

        if not created:
            await interaction.response.send_message(
                (
                    "A role group with that name already exists. "
                    "Group names are case-insensitive."
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            (
                "Role group created.\n\n"
                f"Name: **{name}**\n"
                f"Maximum roles: **{max_roles}**"
            ),
            ephemeral=True,
        )

    # ========================================================
    # /ROLEPANEL GROUP-ADD
    # ========================================================

    @rolepanel.command(
        name="group-add",
        description="Add a Discord role to a limited role group.",
    )
    @app_commands.describe(
        group="Configured role group.",
        role="Discord role to add to the group.",
    )
    @ares_admin_only()
    async def group_add(
        self,
        interaction: discord.Interaction,
        group: str,
        role: discord.Role,
    ):
        """Add a role to a configured group."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        if role.is_default():
            await interaction.response.send_message(
                "`@everyone` cannot be added to a role group.",
                ephemeral=True,
            )
            return

        result = add_role_to_group(
            interaction.guild.id,
            group,
            role.id,
        )

        if result == "group_missing":
            await self.send_not_found(
                interaction,
                group,
            )
            return

        if result == "already_in_group":
            await interaction.response.send_message(
                (
                    f"{role.mention} is already in "
                    f"the **{group}** group."
                ),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        if result == "role_in_other_group":
            await interaction.response.send_message(
                (
                    f"{role.mention} already belongs to another "
                    "limited role group. Remove it from that group first."
                ),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        saved_group = get_role_group(
            interaction.guild.id,
            group,
        )

        group_name = (
            saved_group[1]
            if saved_group is not None
            else group
        )

        await interaction.response.send_message(
            (
                "Role added to group.\n\n"
                f"Group: **{group_name}**\n"
                f"Role: {role.mention}"
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @group_add.autocomplete(
        "group"
    )
    async def group_add_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete /rolepanel group-add group names."""

        return await self.group_autocomplete(
            interaction,
            current,
        )

    # ========================================================
    # /ROLEPANEL GROUP-LIMIT
    # ========================================================

    @rolepanel.command(
        name="group-limit",
        description="Change the maximum selections for a role group.",
    )
    @app_commands.describe(
        group="Configured role group.",
        max_roles="New maximum roles allowed from this group.",
    )
    @ares_admin_only()
    async def group_limit(
        self,
        interaction: discord.Interaction,
        group: str,
        max_roles: app_commands.Range[int, 1, MAX_GROUP_LIMIT],
    ):
        """Change a role group's maximum selection count."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        updated = set_role_group_limit(
            interaction.guild.id,
            group,
            int(max_roles),
        )

        if not updated:
            await self.send_not_found(
                interaction,
                group,
            )
            return

        saved_group = get_role_group(
            interaction.guild.id,
            group,
        )

        group_name = (
            saved_group[1]
            if saved_group is not None
            else group
        )

        await interaction.response.send_message(
            (
                f"**{group_name}** now allows a maximum of "
                f"**{max_roles}** role(s) per member."
            ),
            ephemeral=True,
        )

    @group_limit.autocomplete(
        "group"
    )
    async def group_limit_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete /rolepanel group-limit group names."""

        return await self.group_autocomplete(
            interaction,
            current,
        )

    # ========================================================
    # /ROLEPANEL GROUP-REMOVE
    # ========================================================

    @rolepanel.command(
        name="group-remove",
        description="Remove a Discord role from a limited role group.",
    )
    @app_commands.describe(
        group="Configured role group.",
        role="Discord role to remove from the group.",
    )
    @ares_admin_only()
    async def group_remove(
        self,
        interaction: discord.Interaction,
        group: str,
        role: discord.Role,
    ):
        """Remove a role from a configured group."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        result = remove_role_from_group(
            interaction.guild.id,
            group,
            role.id,
        )

        if result == "group_missing":
            await self.send_not_found(
                interaction,
                group,
            )
            return

        if result == "role_missing":
            await interaction.response.send_message(
                (
                    f"{role.mention} is not assigned to "
                    f"the **{group}** group."
                ),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        saved_group = get_role_group(
            interaction.guild.id,
            group,
        )

        group_name = (
            saved_group[1]
            if saved_group is not None
            else group
        )

        await interaction.response.send_message(
            (
                "Role removed from group.\n\n"
                f"Group: **{group_name}**\n"
                f"Role: {role.mention}"
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @group_remove.autocomplete(
        "group"
    )
    async def group_remove_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete /rolepanel group-remove group names."""

        return await self.group_autocomplete(
            interaction,
            current,
        )

    # ========================================================
    # /ROLEPANEL GROUP-DELETE
    # ========================================================

    @rolepanel.command(
        name="group-delete",
        description="Delete a limited role-selection group.",
    )
    @app_commands.describe(
        group="Configured role group to delete.",
    )
    @ares_admin_only()
    async def group_delete(
        self,
        interaction: discord.Interaction,
        group: str,
    ):
        """Delete a configured role group."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        saved_group = get_role_group(
            interaction.guild.id,
            group,
        )

        if saved_group is None:
            await self.send_not_found(
                interaction,
                group,
            )
            return

        deleted = delete_role_group(
            interaction.guild.id,
            group,
        )

        if not deleted:
            await self.send_not_found(
                interaction,
                group,
            )
            return

        await interaction.response.send_message(
            (
                "Role group deleted.\n\n"
                f"Group: **{saved_group[1]}**\n\n"
                "Discord roles themselves were not deleted."
            ),
            ephemeral=True,
        )

    @group_delete.autocomplete(
        "group"
    )
    async def group_delete_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete /rolepanel group-delete group names."""

        return await self.group_autocomplete(
            interaction,
            current,
        )

    # ========================================================
    # /ROLEPANEL GROUP-STATUS
    # ========================================================

    @rolepanel.command(
        name="group-status",
        description="Show configured reaction-role groups and limits.",
    )
    @app_commands.describe(
        group="Optional group name. Leave blank to show all groups.",
    )
    @ares_admin_only()
    async def group_status(
        self,
        interaction: discord.Interaction,
        group: str | None = None,
    ):
        """Display one or all configured role groups."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        if group is not None:
            saved_group = get_role_group(
                interaction.guild.id,
                group,
            )

            if saved_group is None:
                await self.send_not_found(
                    interaction,
                    group,
                )
                return

            groups = [
                saved_group
            ]

        else:
            groups = get_role_groups(
                interaction.guild.id
            )

        if not groups:
            await interaction.response.send_message(
                "No limited role groups are configured.",
                ephemeral=True,
            )
            return

        sections = []

        for (
            _group_id,
            group_name,
            max_roles,
        ) in groups:
            role_ids = get_roles_for_group(
                interaction.guild.id,
                group_name,
            )

            role_lines = []

            if role_ids:
                for role_id in role_ids:
                    role = interaction.guild.get_role(
                        role_id
                    )

                    if role is None:
                        role_lines.append(
                            f"- Missing role (`{role_id}`)"
                        )
                    else:
                        role_lines.append(
                            f"- {role.mention}"
                        )

            else:
                role_lines.append(
                    "- No roles assigned"
                )

            sections.append(
                (
                    f"**{group_name}**\n"
                    f"Maximum: **{max_roles}**\n"
                    + "\n".join(
                        role_lines
                    )
                )
            )

        await interaction.response.send_message(
            "\n\n".join(
                sections
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @group_status.autocomplete(
        "group"
    )
    async def group_status_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete /rolepanel group-status group names."""

        return await self.group_autocomplete(
            interaction,
            current,
        )

    # ========================================================
    # ERROR HANDLER
    # ========================================================

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        """Handle role-panel configuration errors."""

        if isinstance(
            error,
            AresAdminRequired,
        ):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    (
                        "You need Ares administrative access "
                        "to configure role groups."
                    ),
                    ephemeral=True,
                )
            return

        raise error


async def setup(
    bot: commands.Bot,
):
    """Load reaction-role group configuration commands."""

    await bot.add_cog(
        RolePanelConfigCog(bot)
    )
