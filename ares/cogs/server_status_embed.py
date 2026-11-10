import asyncio
import hashlib
import os
import sqlite3
import time

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from ares.config import ARES_RED, ROLE_DB_PATH


WARCON_BASE_URL = os.getenv(
    "WARCON_BASE_URL",
    "http://127.0.0.1:3000",
).rstrip("/")

WARCON_API_KEY = os.getenv(
    "WARCON_API_KEY",
    "",
).strip()

DEFAULT_WARCON_SERVER_ID = os.getenv(
    "WARCON_SERVER_ID",
    "",
).strip()

STATUS_REFRESH_SECONDS = 30
FORCE_REFRESH_SECONDS = 300
SEEDING_THRESHOLD = 20
FIFTY_V_FIFTY_MAX_PLAYERS = 100
SCORE_MAX = 100
BAR_SEGMENTS = 10

MAP_NAMES = {
    "Kavkazi": "Bakurani",
    "Europe": "Ozeti",
    "NorthAmerica": "Zestafona",
}

WARCON_MAP_ART_BASE = (
    "https://raw.githubusercontent.com/"
    "warcon-app/warcon/main/static/maps"
)


class ServerStatusEmbedCog(commands.Cog):
    """Maintain persistent live WARDOGS server status embeds."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_session: aiohttp.ClientSession | None = None
        self.last_render_hash: dict[tuple[int, str], str] = {}
        self.last_forced_refresh: dict[tuple[int, str], int] = {}
        self.initialize_database()

    async def cog_load(self):
        self.http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        )
        self.status_loop.start()

    def cog_unload(self):
        self.status_loop.cancel()
        if self.http_session is not None and not self.http_session.closed:
            asyncio.create_task(self.http_session.close())

    @staticmethod
    def db() -> sqlite3.Connection:
        return sqlite3.connect(ROLE_DB_PATH)

    @classmethod
    def initialize_database(cls):
        connection = cls.db()
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS server_status_embeds (
                guild_id INTEGER NOT NULL,
                server_id TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, server_id)
            )
            """
        )
        connection.commit()
        connection.close()

    @classmethod
    def save_embed_config(
        cls,
        guild_id: int,
        server_id: str,
        channel_id: int,
        message_id: int | None,
    ):
        now = int(time.time())
        connection = cls.db()
        connection.execute(
            """
            INSERT INTO server_status_embeds (
                guild_id,
                server_id,
                channel_id,
                message_id,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, server_id)
            DO UPDATE SET
                channel_id = excluded.channel_id,
                message_id = excluded.message_id,
                updated_at = excluded.updated_at
            """,
            (guild_id, server_id, channel_id, message_id, now, now),
        )
        connection.commit()
        connection.close()

    @classmethod
    def set_message_id(
        cls,
        guild_id: int,
        server_id: str,
        message_id: int,
    ):
        connection = cls.db()
        connection.execute(
            """
            UPDATE server_status_embeds
            SET message_id = ?, updated_at = ?
            WHERE guild_id = ? AND server_id = ?
            """,
            (message_id, int(time.time()), guild_id, server_id),
        )
        connection.commit()
        connection.close()

    @classmethod
    def delete_embed_config(cls, guild_id: int, server_id: str):
        connection = cls.db()
        connection.execute(
            """
            DELETE FROM server_status_embeds
            WHERE guild_id = ? AND server_id = ?
            """,
            (guild_id, server_id),
        )
        connection.commit()
        connection.close()

    @classmethod
    def get_embed_configs(cls) -> list[tuple[int, str, int, int | None]]:
        connection = cls.db()
        rows = connection.execute(
            """
            SELECT guild_id, server_id, channel_id, message_id
            FROM server_status_embeds
            ORDER BY guild_id, server_id
            """
        ).fetchall()
        connection.close()
        return [
            (
                int(row[0]),
                str(row[1]),
                int(row[2]),
                int(row[3]) if row[3] is not None else None,
            )
            for row in rows
        ]

    @classmethod
    def get_embed_config(
        cls,
        guild_id: int,
        server_id: str,
    ) -> tuple[int, int | None] | None:
        connection = cls.db()
        row = connection.execute(
            """
            SELECT channel_id, message_id
            FROM server_status_embeds
            WHERE guild_id = ? AND server_id = ?
            """,
            (guild_id, server_id),
        ).fetchone()
        connection.close()
        if row is None:
            return None
        return (
            int(row[0]),
            int(row[1]) if row[1] is not None else None,
        )

    @staticmethod
    def warcon_configured() -> bool:
        return bool(WARCON_BASE_URL and WARCON_API_KEY)

    async def fetch_summary(
        self,
        server_id: str,
    ) -> tuple[dict | None, str | None]:
        if self.http_session is None or self.http_session.closed:
            return None, "Ares HTTP session is unavailable."
        if not self.warcon_configured():
            return None, "WARCON read access is not configured."

        url = f"{WARCON_BASE_URL}/api/servers/{server_id}/summary"
        headers = {
            "Authorization": f"Bearer {WARCON_API_KEY}",
            "Accept": "application/json",
        }

        try:
            async with self.http_session.get(url, headers=headers) as response:
                try:
                    payload = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    text = await response.text()
                    return (
                        None,
                        f"WARCON returned HTTP {response.status}: {text[:200]}",
                    )

                if response.status != 200:
                    return (
                        None,
                        f"WARCON returned HTTP {response.status}: "
                        f"{self.extract_error(payload)}",
                    )

                if not isinstance(payload, dict):
                    return None, "WARCON returned an unexpected response."

                return payload, None

        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            return None, str(error)

    @staticmethod
    def extract_error(payload: object) -> str:
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if message:
                    return str(message)
            message = payload.get("message")
            if message:
                return str(message)
        return "Unknown WARCON error."

    @staticmethod
    def get_status(summary: dict) -> dict:
        status = summary.get("status")
        return status if isinstance(status, dict) else {}

    @staticmethod
    def get_scores(status: dict) -> list[dict]:
        raw_scores = status.get("scores")
        if not isinstance(raw_scores, list):
            raw_scores = status.get("factionScores")
        if not isinstance(raw_scores, list):
            return []
        return [row for row in raw_scores if isinstance(row, dict)]

    @staticmethod
    def player_count(status: dict) -> int:
        if "playerCount" in status:
            return int(status.get("playerCount", 0) or 0)
        players = status.get("players")
        if isinstance(players, dict):
            return int(players.get("current", 0) or 0)
        return 0

    @staticmethod
    def max_players(status: dict) -> int:
        if "maxPlayers" in status:
            return int(status.get("maxPlayers", 0) or 0)
        players = status.get("players")
        if isinstance(players, dict):
            return int(players.get("max", 0) or 0)
        return 0

    @staticmethod
    def display_map_name(raw_map: str) -> str:
        return MAP_NAMES.get(raw_map, raw_map or "Unknown")

    @staticmethod
    def is_fifty_v_fifty(server_name: str) -> bool:
        return "50" in server_name

    @staticmethod
    def score_for_faction(scores: list[dict], faction_name: str) -> int:
        for row in scores:
            if str(row.get("name", "")).casefold() == faction_name.casefold():
                return max(
                    0,
                    min(SCORE_MAX, int(row.get("score", 0) or 0)),
                )
        return 0

    @staticmethod
    def score_bar(score: int, *, filled: str) -> str:
        score = max(0, min(SCORE_MAX, score))
        filled_count = round(score / SCORE_MAX * BAR_SEGMENTS)
        filled_count = max(0, min(BAR_SEGMENTS, filled_count))
        return filled * filled_count + "⬛" * (BAR_SEGMENTS - filled_count)

    @staticmethod
    def map_banner_url(raw_map: str, lighting: str) -> str | None:
        if raw_map not in MAP_NAMES or not lighting:
            return None
        return f"{WARCON_MAP_ART_BASE}/{raw_map}/{lighting}-wide.webp"

    def build_embed(self, summary: dict, server_id: str) -> discord.Embed:
        status = self.get_status(summary)
        server_name = str(
            status.get(
                "serverName",
                summary.get("serverName", "WARDOGS Community Server"),
            )
        )
        raw_map = str(status.get("map", "") or "")
        map_name = self.display_map_name(raw_map)
        lighting = str(status.get("lighting", "") or "")
        online = self.player_count(status)
        fifty_v_fifty = self.is_fifty_v_fifty(server_name)

        if fifty_v_fifty:
            maximum = FIFTY_V_FIFTY_MAX_PLAYERS
        else:
            maximum = self.max_players(status)
            if maximum <= 0:
                maximum = FIFTY_V_FIFTY_MAX_PLAYERS

        seeding = online < SEEDING_THRESHOLD
        state_text = "🌱 **SEEDING**" if seeding else "⚔️ **LIVE**"
        description = f"{state_text}\n**{online} / {maximum} players**"
        if seeding:
            description += (
                f"\nServer goes live at **{SEEDING_THRESHOLD} players**."
            )

        embed = discord.Embed(
            title=server_name,
            description=description,
            color=ARES_RED,
        )

        banner_url = self.map_banner_url(raw_map, lighting)
        if banner_url is not None:
            embed.set_image(url=banner_url)

        embed.add_field(
            name="Map",
            value=f"**{map_name}**",
            inline=True,
        )
        embed.add_field(
            name="Players",
            value=f"**{online} / {maximum}**",
            inline=True,
        )
        embed.add_field(
            name="Status",
            value="**SEEDING**" if seeding else "**LIVE**",
            inline=True,
        )

        scores = self.get_scores(status)

        if fifty_v_fifty:
            red_score = self.score_for_faction(scores, "Valkyra")
            green_score = self.score_for_faction(scores, "Manticore")
            red_bar = self.score_bar(red_score, filled="🟥")
            green_bar = self.score_bar(green_score, filled="🟩")

            embed.add_field(
                name=f"🔴 RED  {red_score} / {SCORE_MAX}",
                value=red_bar,
                inline=False,
            )
            embed.add_field(
                name=f"🟢 GREEN  {green_score} / {SCORE_MAX}",
                value=green_bar,
                inline=False,
            )
        else:
            for score_row in scores:
                faction_name = str(score_row.get("name", "Unknown"))
                score = max(
                    0,
                    min(SCORE_MAX, int(score_row.get("score", 0) or 0)),
                )
                bar = self.score_bar(score, filled="🟦")
                embed.add_field(
                    name=f"{faction_name}  {score} / {SCORE_MAX}",
                    value=bar,
                    inline=False,
                )

        embed.set_footer(text="ARES GROUP // WARCON LIVE")
        embed.timestamp = discord.utils.utcnow()
        return embed

    def build_offline_embed(self, server_id: str, error: str) -> discord.Embed:
        embed = discord.Embed(
            title="ARES GROUP // SERVER STATUS",
            description=(
                "🔴 **SERVER STATUS UNAVAILABLE**\n\n"
                "Ares could not read the current WARCON status."
            ),
            color=discord.Color.red(),
        )
        embed.add_field(
            name="WARCON",
            value=f"`{error[:800]}`",
            inline=False,
        )
        embed.set_footer(text="ARES GROUP // WARCON LIVE")
        embed.timestamp = discord.utils.utcnow()
        return embed

    @staticmethod
    def render_hash(embed: discord.Embed) -> str:
        data = embed.to_dict()
        data.pop("timestamp", None)
        encoded = repr(data).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def refresh_one(
        self,
        guild_id: int,
        server_id: str,
        channel_id: int,
        message_id: int | None,
        *,
        force: bool = False,
    ) -> bool:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return False

        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return False

        summary, error = await self.fetch_summary(server_id)
        if summary is None:
            embed = self.build_offline_embed(
                server_id,
                error or "Unknown error",
            )
        else:
            embed = self.build_embed(summary, server_id)

        key = (guild_id, server_id)
        now = int(time.time())
        current_hash = self.render_hash(embed)
        last_hash = self.last_render_hash.get(key)
        last_forced = self.last_forced_refresh.get(key, 0)

        should_edit = (
            force
            or message_id is None
            or current_hash != last_hash
            or now - last_forced >= FORCE_REFRESH_SECONDS
        )
        if not should_edit:
            return True

        if message_id is not None:
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                self.last_render_hash[key] = current_hash
                self.last_forced_refresh[key] = now
                return True
            except discord.NotFound:
                message_id = None
            except (discord.Forbidden, discord.HTTPException) as error_obj:
                print(
                    "SERVER STATUS: Could not edit message "
                    f"{message_id}: {error_obj}"
                )
                return False

        try:
            message = await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException) as error_obj:
            print(
                "SERVER STATUS: Could not create status message: "
                f"{error_obj}"
            )
            return False

        self.set_message_id(guild_id, server_id, message.id)
        self.last_render_hash[key] = current_hash
        self.last_forced_refresh[key] = now
        return True

    async def refresh_all(self):
        for guild_id, server_id, channel_id, message_id in self.get_embed_configs():
            await self.refresh_one(
                guild_id,
                server_id,
                channel_id,
                message_id,
            )

    @tasks.loop(seconds=STATUS_REFRESH_SECONDS)
    async def status_loop(self):
        try:
            await self.refresh_all()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"SERVER STATUS: Refresh loop error: {error}")

    @status_loop.before_loop
    async def before_status_loop(self):
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="putserverembed",
        description="Create or move a persistent live WARDOGS server embed.",
    )
    @app_commands.describe(
        channel="Channel where the live server embed should stay.",
        server_id="WARCON server ID. Defaults to the configured Ares server.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def putserverembed(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        server_id: str | None = None,
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command inside a server.",
                ephemeral=True,
            )
            return

        chosen_server_id = (
            server_id.strip()
            if server_id is not None
            else DEFAULT_WARCON_SERVER_ID
        )
        if not chosen_server_id:
            await interaction.response.send_message(
                (
                    "No WARCON server ID was provided and "
                    "WARCON_SERVER_ID is not configured."
                ),
                ephemeral=True,
            )
            return

        bot_member = interaction.guild.me
        if bot_member is None:
            await interaction.response.send_message(
                "Ares could not verify its Discord permissions.",
                ephemeral=True,
            )
            return

        permissions = channel.permissions_for(bot_member)
        missing = []
        if not permissions.view_channel:
            missing.append("View Channel")
        if not permissions.send_messages:
            missing.append("Send Messages")
        if not permissions.embed_links:
            missing.append("Embed Links")
        if not permissions.read_message_history:
            missing.append("Read Message History")

        if missing:
            await interaction.response.send_message(
                (
                    f"Ares cannot maintain a live server embed in "
                    f"{channel.mention}.\n\nMissing permissions: "
                    + ", ".join(missing)
                ),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        await interaction.response.defer(ephemeral=True)

        summary, error = await self.fetch_summary(chosen_server_id)
        if summary is None:
            await interaction.followup.send(
                f"Ares could not read that WARCON server.\n\n`{error}`",
                ephemeral=True,
            )
            return

        existing = self.get_embed_config(
            interaction.guild.id,
            chosen_server_id,
        )
        old_message_id = existing[1] if existing is not None else None
        old_channel_id = existing[0] if existing is not None else None

        if (
            old_message_id is not None
            and old_channel_id is not None
            and old_channel_id != channel.id
        ):
            old_channel = interaction.guild.get_channel(old_channel_id)
            if isinstance(old_channel, discord.TextChannel):
                try:
                    old_message = await old_channel.fetch_message(old_message_id)
                    await old_message.delete()
                except (
                    discord.NotFound,
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    pass
            old_message_id = None

        self.save_embed_config(
            interaction.guild.id,
            chosen_server_id,
            channel.id,
            old_message_id,
        )

        success = await self.refresh_one(
            interaction.guild.id,
            chosen_server_id,
            channel.id,
            old_message_id,
            force=True,
        )
        if not success:
            await interaction.followup.send(
                "Ares could not create the live server embed.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            (
                "Live server embed configured.\n\n"
                f"Channel: {channel.mention}\n"
                f"Server ID: `{chosen_server_id}`\n"
                "Refresh: **every 30 seconds when needed**\n"
                "50v50 mode: **automatic when the server name contains `50`**\n"
                "Seeding: **under 20 players**"
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="removeserverembed",
        description="Remove a persistent live WARDOGS server embed.",
    )
    @app_commands.describe(
        server_id="WARCON server ID. Defaults to the configured Ares server.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def removeserverembed(
        self,
        interaction: discord.Interaction,
        server_id: str | None = None,
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command inside a server.",
                ephemeral=True,
            )
            return

        chosen_server_id = (
            server_id.strip()
            if server_id is not None
            else DEFAULT_WARCON_SERVER_ID
        )
        if not chosen_server_id:
            await interaction.response.send_message(
                "No WARCON server ID was provided.",
                ephemeral=True,
            )
            return

        existing = self.get_embed_config(
            interaction.guild.id,
            chosen_server_id,
        )
        if existing is None:
            await interaction.response.send_message(
                "No live embed is configured for that WARCON server.",
                ephemeral=True,
            )
            return

        channel_id, message_id = existing
        channel = interaction.guild.get_channel(channel_id)

        if isinstance(channel, discord.TextChannel) and message_id is not None:
            try:
                message = await channel.fetch_message(message_id)
                await message.delete()
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ):
                pass

        self.delete_embed_config(
            interaction.guild.id,
            chosen_server_id,
        )
        self.last_render_hash.pop(
            (interaction.guild.id, chosen_server_id),
            None,
        )
        self.last_forced_refresh.pop(
            (interaction.guild.id, chosen_server_id),
            None,
        )

        await interaction.response.send_message(
            "Live server embed removed.",
            ephemeral=True,
        )

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        if isinstance(error, app_commands.MissingPermissions):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    (
                        "You need the **Manage Server** permission "
                        "to configure live server embeds."
                    ),
                    ephemeral=True,
                )
            return
        raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerStatusEmbedCog(bot))
