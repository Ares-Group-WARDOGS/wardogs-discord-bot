import io
import re
import time

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from ares.config import GENERAL_CHANNEL_ID
from ares.database import (
    clear_welcome_background,
    get_welcome_background,
    get_welcome_settings,
    set_welcome_background,
    set_welcome_settings,
)
from ares.permissions import AresAdminRequired, ares_admin_only


CUSTOM_EMOJI_PATTERN = re.compile(
    r"^<a?:[A-Za-z0-9_]+:(\d+)>$"
)

WELCOME_CANVAS_WIDTH = 500
WELCOME_CANVAS_HEIGHT = 280
WELCOME_AVATAR_SIZE = 180
WELCOME_AVATAR_CENTER_X_RATIO = 1 / 3
MAX_WELCOME_BACKGROUND_BYTES = 8 * 1024 * 1024


class WelcomeCog(commands.Cog):
    """Configure and send member welcome messages."""

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

    # ========================================================
    # HELPERS
    # ========================================================

    @staticmethod
    def parse_channel_id(
        channel_value: str,
    ) -> int | None:
        """Parse a raw channel ID or Discord channel mention."""

        channel_value = channel_value.strip()

        if channel_value.isdigit():
            return int(
                channel_value
            )

        match = re.fullmatch(
            r"<#(\d+)>",
            channel_value,
        )

        if match is None:
            return None

        return int(
            match.group(1)
        )

    @staticmethod
    def get_reaction_object(
        guild: discord.Guild,
        reaction_emoji: str,
    ) -> str | discord.Emoji | None:
        """Convert a saved emoji string into a Discord reaction."""

        reaction_emoji = reaction_emoji.strip()

        if not reaction_emoji:
            return None

        custom_match = CUSTOM_EMOJI_PATTERN.fullmatch(
            reaction_emoji
        )

        if custom_match is None:
            # Assume normal Unicode emoji.
            return reaction_emoji

        emoji_id = int(
            custom_match.group(1)
        )

        return guild.get_emoji(
            emoji_id
        )

    @staticmethod
    def normalize_background_image(
        image_bytes: bytes,
    ) -> bytes:
        """Validate and normalize an uploaded background to the welcome canvas."""

        image = Image.open(
            io.BytesIO(
                image_bytes
            )
        )

        image.load()

        image = image.convert(
            "RGBA"
        )

        image = ImageOps.fit(
            image,
            (
                WELCOME_CANVAS_WIDTH,
                WELCOME_CANVAS_HEIGHT,
            ),
            method=Image.Resampling.LANCZOS,
            centering=(
                0.5,
                0.5,
            ),
        )

        output = io.BytesIO()

        image.save(
            output,
            format="PNG",
            optimize=True,
        )

        return output.getvalue()

    @staticmethod
    def create_welcome_canvas(
        background_bytes: bytes | None,
    ) -> Image.Image:
        """Create the welcome canvas using a configured background if available."""

        if background_bytes is None:
            return Image.new(
                "RGBA",
                (
                    WELCOME_CANVAS_WIDTH,
                    WELCOME_CANVAS_HEIGHT,
                ),
                (
                    0,
                    0,
                    0,
                    0,
                ),
            )

        try:
            background = Image.open(
                io.BytesIO(
                    background_bytes
                )
            )

            background.load()

            background = background.convert(
                "RGBA"
            )

            return ImageOps.fit(
                background,
                (
                    WELCOME_CANVAS_WIDTH,
                    WELCOME_CANVAS_HEIGHT,
                ),
                method=Image.Resampling.LANCZOS,
                centering=(
                    0.5,
                    0.5,
                ),
            )

        except (
            UnidentifiedImageError,
            OSError,
        ) as error:
            print(
                "WELCOME: Saved background could not be loaded: "
                f"{error}"
            )

            return Image.new(
                "RGBA",
                (
                    WELCOME_CANVAS_WIDTH,
                    WELCOME_CANVAS_HEIGHT,
                ),
                (
                    0,
                    0,
                    0,
                    0,
                ),
            )

    @staticmethod
    def make_round_avatar(
        avatar: Image.Image,
    ) -> Image.Image:
        """Crop an avatar into a Discord-style circle."""

        avatar = avatar.convert(
            "RGBA"
        )

        mask = Image.new(
            "L",
            avatar.size,
            0,
        )

        draw = ImageDraw.Draw(
            mask
        )

        draw.ellipse(
            (
                0,
                0,
                avatar.width - 1,
                avatar.height - 1,
            ),
            fill=255,
        )

        rounded_avatar = Image.new(
            "RGBA",
            avatar.size,
            (
                0,
                0,
                0,
                0,
            ),
        )

        rounded_avatar.paste(
            avatar,
            (
                0,
                0,
            ),
            mask,
        )

        return rounded_avatar

    # ========================================================
    # /SETWELCOMECHANNEL
    # ========================================================

    @app_commands.command(
        name="setwelcomechannel",
        description="Configure Ares member welcome messages.",
    )
    @app_commands.describe(
        mode="Turn welcome messages on or off.",
        channel_id=(
            "Existing channel ID or channel mention."
        ),
        reaction=(
            "Optional emoji Ares should react to its "
            "welcome message with."
        ),
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
    async def set_welcome_channel(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
        channel_id: str | None = None,
        reaction: str | None = None,
    ):
        """Enable, disable, or configure welcome messages."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        guild = interaction.guild

        # ====================================================
        # DISABLE
        # ====================================================

        if mode.value == "off":
            current_settings = get_welcome_settings(
                guild.id
            )

            if current_settings is None:
                current_channel_id = GENERAL_CHANNEL_ID
                current_reaction = None

            else:
                (
                    current_channel_id,
                    _,
                    current_reaction,
                ) = current_settings

            set_welcome_settings(
                guild.id,
                current_channel_id,
                False,
                current_reaction,
            )

            await interaction.response.send_message(
                (
                    "Welcome messages are now **disabled**.\n\n"
                    "The configured channel and reaction have "
                    "been preserved."
                ),
                ephemeral=True,
            )
            return

        # ====================================================
        # ENABLE
        # ====================================================

        if channel_id is None:
            await interaction.response.send_message(
                (
                    "You must provide a channel when enabling "
                    "welcome messages."
                ),
                ephemeral=True,
            )
            return

        parsed_channel_id = self.parse_channel_id(
            channel_id
        )

        if parsed_channel_id is None:
            await interaction.response.send_message(
                (
                    "Invalid channel.\n\n"
                    "Provide either a channel ID or channel mention."
                ),
                ephemeral=True,
            )
            return

        channel = guild.get_channel(
            parsed_channel_id
        )

        if channel is None:
            await interaction.response.send_message(
                (
                    "That channel does not exist in this server.\n\n"
                    f"Channel ID: `{parsed_channel_id}`"
                ),
                ephemeral=True,
            )
            return

        if not isinstance(
            channel,
            discord.TextChannel,
        ):
            await interaction.response.send_message(
                "The welcome destination must be a text channel.",
                ephemeral=True,
            )
            return

        # ====================================================
        # REACTION
        # ====================================================

        reaction_emoji = None

        if reaction is not None:
            reaction = reaction.strip()

            if reaction:
                custom_match = CUSTOM_EMOJI_PATTERN.fullmatch(
                    reaction
                )

                if custom_match is not None:
                    emoji_id = int(
                        custom_match.group(1)
                    )

                    custom_emoji = guild.get_emoji(
                        emoji_id
                    )

                    if custom_emoji is None:
                        await interaction.response.send_message(
                            (
                                "That custom emoji is not available "
                                "in this server."
                            ),
                            ephemeral=True,
                        )
                        return

                reaction_emoji = reaction

        # ====================================================
        # VERIFY BOT PERMISSIONS
        # ====================================================

        bot_member = guild.me

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

        if not permissions.attach_files:
            missing_permissions.append(
                "Attach Files"
            )

        if (
            reaction_emoji is not None
            and not permissions.add_reactions
        ):
            missing_permissions.append(
                "Add Reactions"
            )

        if missing_permissions:
            permission_text = ", ".join(
                missing_permissions
            )

            await interaction.response.send_message(
                (
                    f"I cannot use {channel.mention} for welcomes.\n\n"
                    f"Missing permissions: **{permission_text}**"
                ),
                ephemeral=True,
            )
            return

        # ====================================================
        # SAVE
        # ====================================================

        set_welcome_settings(
            guild.id,
            channel.id,
            True,
            reaction_emoji,
        )

        if reaction_emoji is None:
            reaction_text = "None"
        else:
            reaction_text = reaction_emoji

        await interaction.response.send_message(
            (
                "Welcome messages are now **enabled**.\n\n"
                f"Channel: {channel.mention}\n"
                f"Reaction: {reaction_text}"
            ),
            ephemeral=True,
        )

    # ========================================================
    # /SETWELCOMECHANNEL ERROR HANDLER
    # ========================================================

    @set_welcome_channel.error
    async def set_welcome_channel_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        """Handle welcome configuration errors."""

        if isinstance(
            error,
            AresAdminRequired,
        ):
            await interaction.response.send_message(
                (
                    "You need Ares administrative access "
                    "to configure welcome messages."
                ),
                ephemeral=True,
            )
            return

        raise error

    # ========================================================
    # /SETWELCOMEBACKGROUND
    # ========================================================

    @app_commands.command(
        name="setwelcomebackground",
        description="Set the image behind member avatars in welcome cards.",
    )
    @app_commands.describe(
        image="PNG, JPEG, or WebP image to use as the welcome background.",
    )
    @ares_admin_only()
    async def set_welcome_background_command(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
    ):
        """Validate, normalize, and persist a welcome background."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        if image.size > MAX_WELCOME_BACKGROUND_BYTES:
            await interaction.response.send_message(
                (
                    "That image is too large. "
                    "Welcome backgrounds are limited to **8 MB**."
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        try:
            image_bytes = await image.read()

            normalized = self.normalize_background_image(
                image_bytes
            )

        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
        ):
            await interaction.followup.send(
                (
                    "That attachment could not be read as an image. "
                    "Use a PNG, JPEG, or WebP file."
                ),
                ephemeral=True,
            )
            return

        except discord.HTTPException as error:
            await interaction.followup.send(
                (
                    "I could not download that attachment.\n\n"
                    f"`{error}`"
                ),
                ephemeral=True,
            )
            return

        set_welcome_background(
            interaction.guild.id,
            normalized,
            image.filename,
            time.time_ns(),
        )

        preview_file = discord.File(
            io.BytesIO(
                normalized
            ),
            filename="welcome_background.png",
        )

        await interaction.followup.send(
            (
                "Welcome background saved.\n\n"
                f"Source: **{image.filename}**\n"
                f"Stored size: **{WELCOME_CANVAS_WIDTH}×"
                f"{WELCOME_CANVAS_HEIGHT}**\n\n"
                "Future welcome cards will place the member avatar "
                "over this image."
            ),
            file=preview_file,
            ephemeral=True,
        )

    # ========================================================
    # /CLEARWELCOMEBACKGROUND
    # ========================================================

    @app_commands.command(
        name="clearwelcomebackground",
        description="Remove the configured welcome-card background.",
    )
    @ares_admin_only()
    async def clear_welcome_background_command(
        self,
        interaction: discord.Interaction,
    ):
        """Return welcome cards to the transparent-background layout."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        removed = clear_welcome_background(
            interaction.guild.id
        )

        if removed:
            response = (
                "Welcome background removed. "
                "Future welcome cards will use the transparent background."
            )

        else:
            response = (
                "No custom welcome background is currently configured."
            )

        await interaction.response.send_message(
            response,
            ephemeral=True,
        )

    # ========================================================
    # MEMBER JOIN EVENT
    # ========================================================

    @commands.Cog.listener()
    async def on_member_join(
        self,
        member: discord.Member,
    ):
        """Send the configured welcome message."""

        settings = get_welcome_settings(
            member.guild.id
        )

        # Preserve the original production behavior if this
        # guild has never explicitly configured welcomes.
        if settings is None:
            channel_id = GENERAL_CHANNEL_ID
            enabled = True
            reaction_emoji = None

        else:
            (
                channel_id,
                enabled,
                reaction_emoji,
            ) = settings

        if not enabled:
            return

        if channel_id is None:
            return

        channel = member.guild.get_channel(
            channel_id
        )

        if channel is None:
            print(
                "ERROR: Could not find configured "
                f"welcome channel {channel_id}"
            )
            return

        if not isinstance(
            channel,
            discord.TextChannel,
        ):
            print(
                "ERROR: Configured welcome channel "
                f"{channel_id} is not a text channel."
            )
            return

        # ====================================================
        # CREATE EMBED
        # ====================================================

        embed = discord.Embed(
            description="Welcome to Ares Group.",
        )

        welcome_file = None

        # ====================================================
        # CREATE AVATAR IMAGE
        # ====================================================

        try:
            avatar_asset = member.display_avatar.replace(
                size=256
            )

            avatar_bytes = await avatar_asset.read()

            avatar = Image.open(
                io.BytesIO(
                    avatar_bytes
                )
            ).convert(
                "RGBA"
            )

            avatar = avatar.resize(
                (
                    WELCOME_AVATAR_SIZE,
                    WELCOME_AVATAR_SIZE,
                ),
                Image.Resampling.LANCZOS,
            )

            avatar = self.make_round_avatar(
                avatar
            )

            background_settings = get_welcome_background(
                member.guild.id
            )

            background_bytes = (
                background_settings[0]
                if background_settings is not None
                else None
            )

            canvas = self.create_welcome_canvas(
                background_bytes
            )

            avatar_center_x = int(
                WELCOME_CANVAS_WIDTH
                * WELCOME_AVATAR_CENTER_X_RATIO
            )

            x = (
                avatar_center_x
                - avatar.width // 2
            )

            y = (
                WELCOME_CANVAS_HEIGHT
                - avatar.height
            ) // 2

            canvas.paste(
                avatar,
                (
                    x,
                    y,
                ),
                avatar,
            )

            image_buffer = io.BytesIO()

            canvas.save(
                image_buffer,
                format="PNG",
            )

            image_buffer.seek(
                0
            )

            welcome_file = discord.File(
                image_buffer,
                filename="welcome_avatar.png",
            )

            embed.set_image(
                url="attachment://welcome_avatar.png"
            )

        except Exception as error:
            print(
                "ERROR generating welcome image: "
                f"{error}"
            )

        # ====================================================
        # SEND WELCOME MESSAGE
        # ====================================================

        try:
            if welcome_file is not None:
                welcome_message = await channel.send(
                    content=member.mention,
                    embed=embed,
                    file=welcome_file,
                    allowed_mentions=(
                        discord.AllowedMentions(
                            users=True
                        )
                    ),
                )

            else:
                welcome_message = await channel.send(
                    content=member.mention,
                    embed=embed,
                    allowed_mentions=(
                        discord.AllowedMentions(
                            users=True
                        )
                    ),
                )

            # =================================================
            # OPTIONAL WELCOME REACTION
            # =================================================

            if reaction_emoji is not None:
                reaction_object = self.get_reaction_object(
                    member.guild,
                    reaction_emoji,
                )

                if reaction_object is None:
                    print(
                        "WELCOME: Configured custom emoji "
                        f"{reaction_emoji} is unavailable."
                    )

                else:
                    try:
                        await welcome_message.add_reaction(
                            reaction_object
                        )

                    except discord.Forbidden:
                        print(
                            "ERROR: Cannot add configured "
                            "welcome reaction."
                        )

                    except discord.HTTPException as error:
                        print(
                            "ERROR adding welcome reaction: "
                            f"{error}"
                        )

            print(
                f"WELCOME: {member} joined "
                f"{member.guild.name}"
            )

        except discord.Forbidden:
            print(
                "ERROR: Cannot send welcome message "
                f"in channel {channel_id}."
            )

        except discord.HTTPException as error:
            print(
                "ERROR sending welcome message: "
                f"{error}"
            )


    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        """Handle welcome configuration access failures."""

        if isinstance(
            error,
            AresAdminRequired,
        ):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    (
                        "You need Ares administrative access "
                        "to configure welcome messages."
                    ),
                    ephemeral=True,
                )
            return

        raise error


# ============================================================
# COG SETUP
# ============================================================

async def setup(
    bot: commands.Bot,
):
    """Load the welcome cog."""

    await bot.add_cog(
        WelcomeCog(bot)
    )
