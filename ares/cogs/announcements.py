import io
import os

import discord
from discord.ext import commands

from ares.config import ARES_RED
from ares.prefixes import get_guild_prefix


class AnnouncementsCog(commands.Cog):
    """Handle Ares Group announcements."""

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

    @commands.command(
        name="announce",
    )
    @commands.has_permissions(
        manage_messages=True,
    )
    async def announce(
        self,
        ctx: commands.Context,
        *,
        message: str = "",
    ):
        """Post a normal bot announcement with optional images."""

        image_attachments = []

        for attachment in ctx.message.attachments:
            if (
                attachment.content_type
                and attachment.content_type.startswith("image/")
            ):
                image_attachments.append(
                    attachment
                )

        message_text = message.strip()

        should_ping_everyone = message_text.startswith(
            "@everyone"
        )

        if should_ping_everyone:
            message_text = message_text[
                len("@everyone"):
            ].lstrip()

        if not message_text and not image_attachments:
            warning = await ctx.send(
                f"{ctx.author.mention}, "
                "include announcement text or an image."
            )

            await warning.delete(
                delay=5
            )
            return

        files = []
        filenames = []

        for index, attachment in enumerate(
            image_attachments[:10]
        ):
            try:
                image_data = await attachment.read()

                extension = os.path.splitext(
                    attachment.filename
                )[1]

                if not extension:
                    extension = ".png"

                filename = (
                    f"announcement_{index + 1}"
                    f"{extension}"
                )

                files.append(
                    discord.File(
                        io.BytesIO(
                            image_data
                        ),
                        filename=filename,
                    )
                )

                filenames.append(
                    filename
                )

            except discord.HTTPException as error:
                print(
                    "ERROR reading attachment: "
                    f"{error}"
                )

        try:
            await ctx.message.delete()

        except discord.HTTPException:
            pass

        embed = discord.Embed(
            title="ARES GROUP // ANNOUNCEMENT",
            description=(
                message_text
                if message_text
                else None
            ),
            color=ARES_RED,
            timestamp=discord.utils.utcnow(),
        )

        embed.set_footer(
            text=(
                "Issued by "
                f"{ctx.author.display_name}"
            )
        )

        embeds = [
            embed
        ]

        if filenames:
            embed.set_image(
                url=(
                    "attachment://"
                    f"{filenames[0]}"
                )
            )

        for filename in filenames[1:]:
            image_embed = discord.Embed(
                color=ARES_RED
            )

            image_embed.set_image(
                url=(
                    "attachment://"
                    f"{filename}"
                )
            )

            embeds.append(
                image_embed
            )

        await ctx.send(
            content=(
                "@everyone"
                if should_ping_everyone
                else None
            ),
            embeds=embeds,
            files=files,
            allowed_mentions=discord.AllowedMentions(
                everyone=should_ping_everyone,
                users=False,
                roles=False,
                replied_user=False,
            ),
        )

    @announce.error
    async def announce_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ):
        """Handle announcement command errors."""

        prefix = get_guild_prefix(
            self.bot,
            ctx.guild.id
            if ctx.guild
            else None,
        )

        if isinstance(
            error,
            commands.MissingPermissions,
        ):
            warning = await ctx.send(
                f"{ctx.author.mention}, "
                "you do not have permission "
                "to issue announcements."
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
                f"{ctx.author.mention}, "
                f"use `{prefix}announce <message>`."
            )

            await warning.delete(
                delay=5
            )
            return

        raise error


async def setup(
    bot: commands.Bot,
):
    """Load the announcements cog."""

    await bot.add_cog(
        AnnouncementsCog(bot)
    )