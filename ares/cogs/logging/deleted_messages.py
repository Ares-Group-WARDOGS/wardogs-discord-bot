import os

import discord
from discord.ext import commands

from ares.config import ARES_RED
from ares.database import get_log_channel


class DeletedMessagesCog(commands.Cog):
    """Log deleted Discord messages and preserve attachments."""

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

    @staticmethod
    def is_image_attachment(
        attachment: discord.Attachment,
    ) -> bool:
        """Return whether an attachment appears to be an image."""

        if (
            attachment.content_type is not None
            and attachment.content_type.startswith("image/")
        ):
            return True

        extension = os.path.splitext(
            attachment.filename
        )[1].lower()

        return extension in {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".bmp",
        }

    @commands.Cog.listener()
    async def on_raw_message_delete(
        self,
        payload: discord.RawMessageDeleteEvent,
    ):
        """Log a deleted message and preserve cached attachments."""

        if payload.guild_id is None:
            return

        log_channel_id = get_log_channel(
            payload.guild_id,
            "deleted_messages",
        )

        if log_channel_id is None:
            return

        guild = self.bot.get_guild(
            payload.guild_id
        )

        if guild is None:
            return

        log_channel = guild.get_channel(
            log_channel_id
        )

        if not isinstance(
            log_channel,
            discord.TextChannel,
        ):
            return

        source_channel = guild.get_channel(
            payload.channel_id
        )

        cached_message = payload.cached_message

        embed = discord.Embed(
            title="ARES // MESSAGE DELETED",
            color=ARES_RED,
        )

        # ====================================================
        # CHANNEL
        # ====================================================

        if source_channel is not None:
            embed.add_field(
                name="Channel",
                value=source_channel.mention,
                inline=True,
            )

        else:
            embed.add_field(
                name="Channel ID",
                value=f"`{payload.channel_id}`",
                inline=True,
            )

        # ====================================================
        # MESSAGE ID
        # ====================================================

        embed.add_field(
            name="Message ID",
            value=f"`{payload.message_id}`",
            inline=True,
        )

        files = []
        first_image_filename = None

        # ====================================================
        # CACHED MESSAGE DATA
        # ====================================================

        if cached_message is not None:
            embed.add_field(
                name="Author",
                value=(
                    f"{cached_message.author.mention}\n"
                    f"`{cached_message.author.id}`"
                ),
                inline=False,
            )

            # ------------------------------------------------
            # CONTENT
            # ------------------------------------------------

            if cached_message.content:
                content = cached_message.content

                if len(content) > 1000:
                    content = (
                        content[:997]
                        + "..."
                    )

                embed.add_field(
                    name="Content",
                    value=content,
                    inline=False,
                )

            else:
                embed.add_field(
                    name="Content",
                    value="*No text content.*",
                    inline=False,
                )

            # ------------------------------------------------
            # ATTACHMENTS
            # ------------------------------------------------

            if cached_message.attachments:
                preserved_names = []
                failed_names = []

                # Discord messages support a limited number
                # of uploaded files, so preserve up to 10.
                for index, attachment in enumerate(
                    cached_message.attachments[:10]
                ):
                    safe_filename = (
                        f"deleted_{payload.message_id}_"
                        f"{index}_{attachment.filename}"
                    )

                    try:
                        # use_cached=True tells discord.py to use
                        # Discord's cached/proxy attachment URL.
                        # This is important after the source
                        # message has already been deleted.
                        deleted_file = await attachment.to_file(
                            filename=safe_filename,
                            use_cached=True,
                        )

                        files.append(
                            deleted_file
                        )

                        preserved_names.append(
                            attachment.filename
                        )

                        if (
                            first_image_filename is None
                            and self.is_image_attachment(
                                attachment
                            )
                        ):
                            first_image_filename = (
                                safe_filename
                            )

                    except (
                        discord.HTTPException,
                        discord.NotFound,
                        discord.Forbidden,
                    ) as error:
                        failed_names.append(
                            attachment.filename
                        )

                        print(
                            "ERROR preserving deleted attachment "
                            f"{attachment.filename}: {error}"
                        )

                attachment_lines = []

                if preserved_names:
                    attachment_lines.append(
                        "**Preserved:**"
                    )

                    attachment_lines.extend(
                        preserved_names
                    )

                if failed_names:
                    if attachment_lines:
                        attachment_lines.append(
                            ""
                        )

                    attachment_lines.append(
                        "**Could not recover:**"
                    )

                    attachment_lines.extend(
                        failed_names
                    )

                if attachment_lines:
                    attachment_text = "\n".join(
                        attachment_lines
                    )

                    if len(attachment_text) > 1000:
                        attachment_text = (
                            attachment_text[:997]
                            + "..."
                        )

                    embed.add_field(
                        name="Attachments",
                        value=attachment_text,
                        inline=False,
                    )

        # ====================================================
        # MESSAGE WAS NOT CACHED
        # ====================================================

        else:
            embed.add_field(
                name="Content",
                value=(
                    "*Message was not cached, so its author, "
                    "content, and attachments could not be recovered.*"
                ),
                inline=False,
            )

        # ====================================================
        # DISPLAY FIRST RECOVERED IMAGE
        # ====================================================

        if first_image_filename is not None:
            embed.set_image(
                url=(
                    "attachment://"
                    f"{first_image_filename}"
                )
            )

        embed.timestamp = discord.utils.utcnow()

        # ====================================================
        # SEND LOG
        # ====================================================

        try:
            if files:
                await log_channel.send(
                    embed=embed,
                    files=files,
                )

            else:
                await log_channel.send(
                    embed=embed
                )

        except discord.Forbidden:
            print(
                "ERROR: Cannot send deleted-message log."
            )

        except discord.HTTPException as error:
            print(
                "ERROR sending deleted-message log: "
                f"{error}"
            )


async def setup(
    bot: commands.Bot,
):
    """Load deleted-message logging."""

    await bot.add_cog(
        DeletedMessagesCog(bot)
    )