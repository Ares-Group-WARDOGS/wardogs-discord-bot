import asyncio
import json
import os
import re
import sqlite3
import time
from collections import defaultdict, deque
from dataclasses import dataclass

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from ares.config import ARES_RED, ROLE_DB_PATH


WARCON_BASE_URL = os.getenv("WARCON_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
WARCON_SERVER_ID = os.getenv("WARCON_SERVER_ID", "").strip()
WARCON_API_KEY = os.getenv("WARCON_API_KEY", "").strip()

TRACKING_MODE_LOCAL = "local"
TRACKING_MODE_GLOBAL = "global"
DEFAULT_CASH_THRESHOLD = 5000
CASH_WINDOW_SECONDS = 300
ALERT_COOLDOWN_SECONDS = 300
SESSION_ABSENCE_GRACE_SECONDS = 120
SAMPLE_HEARTBEAT_SECONDS = 60
STREAM_RETRY_SECONDS = 5
AUTH_RETRY_SECONDS = 30
LEADERBOARD_REFRESH_SECONDS = 120
LEADERBOARD_LIMIT = 10
STEAM_ID_PATTERN = re.compile(r"^\d{17}$")


@dataclass
class TrackingSettings:
    guild_id: int
    channel_id: int
    mode: str
    cash_threshold: int


@dataclass
class SessionState:
    started_at: int
    last_seen_at: int
    missing_since: int | None
    player_name: str
    faction: str
    map_name: str
    start_cash: int
    current_cash: int
    cash_net: int
    start_kills: int
    current_kills: int
    start_deaths: int
    current_deaths: int


class StatTrackingCog(commands.Cog):
    """Track WARDOGS player statistics through the WARCON API."""

    stattracking = app_commands.Group(
        name="stattracking",
        description="Configure Ares WARDOGS stat tracking.",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_session: aiohttp.ClientSession | None = None
        self.stream_task: asyncio.Task | None = None
        self.latest_players: dict[str, dict] = {}
        self.cash_windows: dict[tuple[int, str], deque[tuple[int, int]]] = defaultdict(deque)
        # WARDOGS exposes death losses as a negative cash counter which resets
        # to zero on respawn. Keep the most-negative value until that reset.
        self.pending_cash_losses: dict[tuple[int, str], int] = {}
        self.last_alerts: dict[tuple[int, str, str], int] = {}
        self.last_saved: dict[tuple[int, str], tuple[int, int, int, str, int]] = {}
        self.active_sessions: dict[tuple[int, str], SessionState] = {}
        self.previous_match_state: dict[int, tuple[str, str, int]] = {}
        self.leaderboard_task: asyncio.Task | None = None
        self.initialize_database()

    async def cog_load(self):
        if not self.warcon_configured():
            print(
                "STAT TRACKING: WARCON integration is not configured. "
                "Set WARCON_SERVER_ID and WARCON_API_KEY."
            )
            return
        self.http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10, sock_read=None)
        )
        self.stream_task = asyncio.create_task(
            self.stream_loop(),
            name="ares-warcon-stat-stream",
        )

        self.leaderboard_task = asyncio.create_task(
            self.leaderboard_loop(),
            name="ares-warcon-leaderboards",
        )

    def cog_unload(self):
        if self.stream_task is not None:
            self.stream_task.cancel()
        if self.leaderboard_task is not None:
            self.leaderboard_task.cancel()
        if self.http_session is not None and not self.http_session.closed:
            asyncio.create_task(self.http_session.close())

    # ========================================================
    # DATABASE
    # ========================================================

    @staticmethod
    def db() -> sqlite3.Connection:
        return sqlite3.connect(ROLE_DB_PATH)

    @classmethod
    def initialize_database(cls):
        connection = cls.db()
        cursor = connection.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS stat_tracking_settings (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                mode TEXT NOT NULL DEFAULT 'local',
                cash_threshold INTEGER NOT NULL DEFAULT 5000,
                updated_at INTEGER NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tracked_users (
                guild_id INTEGER NOT NULL,
                discord_id INTEGER NOT NULL,
                steam_id TEXT NOT NULL,
                last_name TEXT,
                linked_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, discord_id),
                UNIQUE (guild_id, steam_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS player_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                steam_id TEXT NOT NULL,
                player_name TEXT NOT NULL,
                faction TEXT NOT NULL,
                cash INTEGER NOT NULL,
                kills INTEGER NOT NULL,
                deaths INTEGER NOT NULL,
                observed_at INTEGER NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_player_samples_lookup
            ON player_samples (guild_id, steam_id, observed_at)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS stat_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                steam_id TEXT NOT NULL,
                player_name TEXT NOT NULL,
                faction TEXT NOT NULL,
                map_name TEXT NOT NULL,
                started_at INTEGER NOT NULL,
                ended_at INTEGER NOT NULL,
                starting_cash INTEGER NOT NULL,
                ending_cash INTEGER NOT NULL,
                cash_delta INTEGER NOT NULL,
                kills_delta INTEGER NOT NULL,
                deaths_delta INTEGER NOT NULL,
                end_reason TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_stat_sessions_lookup
            ON stat_sessions (guild_id, steam_id, ended_at)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS stat_alert_state (
                guild_id INTEGER NOT NULL,
                steam_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                alerted_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, steam_id, direction)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS stat_leaderboards (
                guild_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER,
                scope TEXT NOT NULL DEFAULT 'local',
                PRIMARY KEY (guild_id, kind)
            )
            """
        )
        connection.commit()
        connection.close()

    @classmethod
    def save_settings(cls, guild_id: int, channel_id: int, mode: str, cash_threshold: int):
        connection = cls.db()
        connection.execute(
            """
            INSERT INTO stat_tracking_settings
                (guild_id, channel_id, mode, cash_threshold, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                mode = excluded.mode,
                cash_threshold = excluded.cash_threshold,
                updated_at = excluded.updated_at
            """,
            (guild_id, channel_id, mode, cash_threshold, int(time.time())),
        )
        connection.commit()
        connection.close()

    @classmethod
    def get_settings(cls, guild_id: int) -> TrackingSettings | None:
        connection = cls.db()
        row = connection.execute(
            """
            SELECT guild_id, channel_id, mode, cash_threshold
            FROM stat_tracking_settings
            WHERE guild_id = ?
            """,
            (guild_id,),
        ).fetchone()
        connection.close()
        if row is None:
            return None
        return TrackingSettings(int(row[0]), int(row[1]), str(row[2]), int(row[3]))

    @classmethod
    def get_all_settings(cls) -> list[TrackingSettings]:
        connection = cls.db()
        rows = connection.execute(
            """
            SELECT guild_id, channel_id, mode, cash_threshold
            FROM stat_tracking_settings
            """
        ).fetchall()
        connection.close()
        return [TrackingSettings(int(r[0]), int(r[1]), str(r[2]), int(r[3])) for r in rows]

    @classmethod
    def get_link_for_discord(cls, guild_id: int, discord_id: int) -> tuple[str, str | None] | None:
        connection = cls.db()
        row = connection.execute(
            """
            SELECT steam_id, last_name
            FROM tracked_users
            WHERE guild_id = ? AND discord_id = ?
            """,
            (guild_id, discord_id),
        ).fetchone()
        connection.close()
        if row is None:
            return None
        return str(row[0]), str(row[1]) if row[1] is not None else None

    @classmethod
    def get_link_for_steam(cls, guild_id: int, steam_id: str) -> tuple[int, str | None] | None:
        connection = cls.db()
        row = connection.execute(
            """
            SELECT discord_id, last_name
            FROM tracked_users
            WHERE guild_id = ? AND steam_id = ?
            """,
            (guild_id, steam_id),
        ).fetchone()
        connection.close()
        if row is None:
            return None
        return int(row[0]), str(row[1]) if row[1] is not None else None

    @classmethod
    def save_link(cls, guild_id: int, discord_id: int, steam_id: str, last_name: str | None):
        connection = cls.db()
        connection.execute(
            """
            INSERT INTO tracked_users
                (guild_id, discord_id, steam_id, last_name, linked_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, discord_id) DO UPDATE SET
                steam_id = excluded.steam_id,
                last_name = excluded.last_name,
                linked_at = excluded.linked_at
            """,
            (guild_id, discord_id, steam_id, last_name, int(time.time())),
        )
        connection.commit()
        connection.close()

    @classmethod
    def update_link_name(cls, guild_id: int, steam_id: str, player_name: str):
        connection = cls.db()
        connection.execute(
            """
            UPDATE tracked_users
            SET last_name = ?
            WHERE guild_id = ? AND steam_id = ?
            """,
            (player_name, guild_id, steam_id),
        )
        connection.commit()
        connection.close()

    @classmethod
    def delete_link(cls, guild_id: int, discord_id: int):
        connection = cls.db()
        connection.execute(
            """
            DELETE FROM tracked_users
            WHERE guild_id = ? AND discord_id = ?
            """,
            (guild_id, discord_id),
        )
        connection.commit()
        connection.close()

    @classmethod
    def save_sample(
        cls,
        guild_id: int,
        steam_id: str,
        player_name: str,
        faction: str,
        cash: int,
        kills: int,
        deaths: int,
        observed_at: int,
    ):
        connection = cls.db()
        connection.execute(
            """
            INSERT INTO player_samples
                (guild_id, steam_id, player_name, faction, cash, kills, deaths, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, steam_id, player_name, faction, cash, kills, deaths, observed_at),
        )
        connection.commit()
        connection.close()

    @classmethod
    def save_session(cls, guild_id: int, steam_id: str, state: SessionState, ended_at: int, reason: str):
        connection = cls.db()
        connection.execute(
            """
            INSERT INTO stat_sessions
                (guild_id, steam_id, player_name, faction, map_name, started_at,
                 ended_at, starting_cash, ending_cash, cash_delta, kills_delta,
                 deaths_delta, end_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                steam_id,
                state.player_name,
                state.faction,
                state.map_name,
                state.started_at,
                ended_at,
                state.start_cash,
                state.current_cash,
                state.cash_net,
                max(0, state.current_kills),
                max(0, state.current_deaths),
                reason,
            ),
        )
        connection.commit()
        connection.close()

    @classmethod
    def get_latest_sample(cls, guild_id: int, steam_id: str) -> tuple | None:
        connection = cls.db()
        row = connection.execute(
            """
            SELECT player_name, faction, cash, kills, deaths, observed_at
            FROM player_samples
            WHERE guild_id = ? AND steam_id = ?
            ORDER BY observed_at DESC
            LIMIT 1
            """,
            (guild_id, steam_id),
        ).fetchone()
        connection.close()
        return row

    @classmethod
    def save_alert_time(cls, guild_id: int, steam_id: str, direction: str, alerted_at: int):
        connection = cls.db()
        connection.execute(
            """
            INSERT INTO stat_alert_state (guild_id, steam_id, direction, alerted_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, steam_id, direction) DO UPDATE SET
                alerted_at = excluded.alerted_at
            """,
            (guild_id, steam_id, direction, alerted_at),
        )
        connection.commit()
        connection.close()

    @classmethod
    def get_alert_time(cls, guild_id: int, steam_id: str, direction: str) -> int:
        connection = cls.db()
        row = connection.execute(
            """
            SELECT alerted_at
            FROM stat_alert_state
            WHERE guild_id = ? AND steam_id = ? AND direction = ?
            """,
            (guild_id, steam_id, direction),
        ).fetchone()
        connection.close()
        return int(row[0]) if row else 0


    # ========================================================
    # STATIC LEADERBOARDS
    # ========================================================

    @classmethod
    def save_leaderboard_config(
        cls,
        guild_id: int,
        kind: str,
        channel_id: int,
        scope: str,
    ):
        connection = cls.db()
        connection.execute(
            """
            INSERT INTO stat_leaderboards
                (guild_id, kind, channel_id, message_id, scope)
            VALUES (?, ?, ?, NULL, ?)
            ON CONFLICT(guild_id, kind) DO UPDATE SET
                channel_id = excluded.channel_id,
                message_id = NULL,
                scope = excluded.scope
            """,
            (guild_id, kind, channel_id, scope),
        )
        connection.commit()
        connection.close()

    @classmethod
    def get_leaderboard_configs(cls) -> list[tuple[int, str, int, int | None, str]]:
        connection = cls.db()
        rows = connection.execute(
            """
            SELECT guild_id, kind, channel_id, message_id, scope
            FROM stat_leaderboards
            """
        ).fetchall()
        connection.close()
        return [
            (
                int(row[0]),
                str(row[1]),
                int(row[2]),
                int(row[3]) if row[3] is not None else None,
                str(row[4]),
            )
            for row in rows
        ]

    @classmethod
    def set_leaderboard_message(cls, guild_id: int, kind: str, message_id: int):
        connection = cls.db()
        connection.execute(
            """
            UPDATE stat_leaderboards
            SET message_id = ?
            WHERE guild_id = ? AND kind = ?
            """,
            (message_id, guild_id, kind),
        )
        connection.commit()
        connection.close()

    @classmethod
    def leaderboard_rows(
        cls,
        guild_id: int,
        kind: str,
        scope: str,
    ) -> list[tuple[str, int]]:
        connection = cls.db()
        aggregate = "SUM(cash_delta)" if kind == "cash" else "SUM(kills_delta)"

        if scope == TRACKING_MODE_LOCAL:
            rows = connection.execute(
                f"""
                SELECT s.steam_id, {aggregate} AS total_value
                FROM stat_sessions AS s
                INNER JOIN tracked_users AS t
                    ON t.guild_id = s.guild_id
                    AND t.steam_id = s.steam_id
                WHERE s.guild_id = ?
                GROUP BY s.steam_id
                ORDER BY total_value DESC
                LIMIT ?
                """,
                (guild_id, LEADERBOARD_LIMIT),
            ).fetchall()
        else:
            rows = connection.execute(
                f"""
                SELECT steam_id, {aggregate} AS total_value
                FROM stat_sessions
                WHERE guild_id = ?
                GROUP BY steam_id
                ORDER BY total_value DESC
                LIMIT ?
                """,
                (guild_id, LEADERBOARD_LIMIT),
            ).fetchall()

        connection.close()
        return [(str(row[0]), int(row[1] or 0)) for row in rows]

    async def leaderboard_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self.refresh_all_leaderboards()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                print(f"STAT TRACKING: Leaderboard refresh error: {error}")
            await asyncio.sleep(LEADERBOARD_REFRESH_SECONDS)

    async def refresh_all_leaderboards(self):
        for guild_id, kind, channel_id, message_id, scope in self.get_leaderboard_configs():
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue

            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue

            embed = self.build_leaderboard_embed(guild, kind, scope)

            if message_id is None:
                try:
                    message = await channel.send(
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except (discord.Forbidden, discord.HTTPException):
                    continue
                self.set_leaderboard_message(guild_id, kind, message.id)
                continue

            try:
                message = await channel.fetch_message(message_id)
                await message.edit(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.NotFound:
                try:
                    message = await channel.send(
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except (discord.Forbidden, discord.HTTPException):
                    continue
                self.set_leaderboard_message(guild_id, kind, message.id)
            except (discord.Forbidden, discord.HTTPException):
                continue

    def build_leaderboard_embed(
        self,
        guild: discord.Guild,
        kind: str,
        scope: str,
    ) -> discord.Embed:
        is_cash = kind == "cash"
        embed = discord.Embed(
            title=(
                "ARES GROUP // CASH LEADERBOARD"
                if is_cash
                else "ARES GROUP // KILL LEADERBOARD"
            ),
            description=(
                "Linked Ares members"
                if scope == TRACKING_MODE_LOCAL
                else "All tracked server players"
            ),
            color=ARES_RED,
        )

        rows = self.leaderboard_rows(guild.id, kind, scope)
        if not rows:
            embed.add_field(
                name="Leaderboard",
                value="No completed tracked matches yet.",
                inline=False,
            )
        else:
            lines = []
            for rank, (steam_id, value) in enumerate(rows, start=1):
                link = self.get_link_for_steam(guild.id, steam_id)
                if link is not None:
                    name = f"<@{link[0]}>"
                else:
                    sample = self.get_latest_sample(guild.id, steam_id)
                    name = str(sample[0]) if sample is not None else "Unknown Player"

                value_text = (
                    self.format_cash(value, signed=True)
                    if is_cash
                    else f"{value:,} kills"
                )
                lines.append(f"**{rank}.** {name}  •  **{value_text}**")

            embed.add_field(
                name="Top Players",
                value="\n".join(lines),
                inline=False,
            )

        embed.set_footer(text="Refreshes every 2 minutes")
        embed.timestamp = discord.utils.utcnow()
        return embed

    # ========================================================
    # HELPERS
    # ========================================================

    @staticmethod
    def warcon_configured() -> bool:
        return bool(WARCON_SERVER_ID and WARCON_API_KEY)

    @staticmethod
    def normalize_player(player: dict) -> dict:
        return {
            "name": str(player.get("name", "Unknown")),
            "steam_id": str(player.get("steamId", "")),
            "faction": str(player.get("faction", "Unknown")),
            "cash": int(player.get("cash", 0) or 0),
            "kills": int(player.get("kills", 0) or 0),
            "deaths": int(player.get("deaths", 0) or 0),
            "ping": int(player.get("ping", 0) or 0),
        }

    @staticmethod
    def should_track(settings: TrackingSettings, linked: bool) -> bool:
        return settings.mode == TRACKING_MODE_GLOBAL or linked

    @staticmethod
    def format_cash(amount: int, signed: bool = False) -> str:
        return f"{amount:+,}" if signed else f"{amount:,}"

    @staticmethod
    def kd(kills: int, deaths: int) -> str:
        return f"{kills:.2f}" if deaths <= 0 else f"{kills / deaths:.2f}"

    # ========================================================
    # WARCON SSE
    # ========================================================

    async def stream_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            if self.http_session is None or self.http_session.closed:
                return

            url = f"{WARCON_BASE_URL}/api/live/events?ids={WARCON_SERVER_ID}"
            headers = {
                "Authorization": f"Bearer {WARCON_API_KEY}",
                "Accept": "text/event-stream",
            }

            try:
                async with self.http_session.get(url, headers=headers) as response:
                    if response.status in (401, 403):
                        print(f"STAT TRACKING: WARCON auth failed with HTTP {response.status}")
                        await asyncio.sleep(AUTH_RETRY_SECONDS)
                        continue
                    if response.status != 200:
                        print(f"STAT TRACKING: WARCON SSE returned HTTP {response.status}")
                        await asyncio.sleep(STREAM_RETRY_SECONDS)
                        continue

                    event_name = ""
                    data_lines: list[str] = []

                    async for raw_line in response.content:
                        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                        if not line:
                            await self.dispatch_sse_event(event_name, data_lines)
                            event_name = ""
                            data_lines = []
                        elif line.startswith(":"):
                            continue
                        elif line.startswith("event:"):
                            event_name = line[len("event:"):].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[len("data:"):].strip())

            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                print(f"STAT TRACKING: WARCON connection error: {error}")
            except Exception as error:
                print(f"STAT TRACKING: Unexpected WARCON error: {error}")

            await asyncio.sleep(STREAM_RETRY_SECONDS)

    async def dispatch_sse_event(self, event_name: str, data_lines: list[str]):
        if event_name != "live" or not data_lines:
            return
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        if payload.get("serverId") != WARCON_SERVER_ID or not payload.get("ok", False):
            return
        await self.process_live_observation(payload)

    async def process_live_observation(self, live: dict):
        observed_at = int(time.time())
        status = live.get("status") or {}
        map_name = str(status.get("map", "Unknown"))
        experiences = status.get("experiences") or []
        experience = str(experiences[0]) if experiences else ""
        scores = status.get("scores") or []
        total_score = sum(
            int(item.get("score", 0) or 0)
            for item in scores
            if isinstance(item, dict)
        )
        players = [
            self.normalize_player(player)
            for player in (live.get("players") or [])
            if isinstance(player, dict)
        ]

        previous_players = self.latest_players
        current_player_map = {
            player["steam_id"]: player
            for player in players
            if player["steam_id"]
        }
        global_player_reset = self.detect_global_player_reset(
            previous_players,
            current_player_map,
        )
        self.latest_players = current_player_map

        for settings in self.get_all_settings():
            guild = self.bot.get_guild(settings.guild_id)
            if guild is None:
                continue

            match_reset = self.detect_match_reset(
                settings.guild_id,
                map_name,
                experience,
                total_score,
            )

            if match_reset or global_player_reset:
                await self.finish_all_sessions(settings, observed_at, "match_end")
                self.clear_guild_cash_windows(settings.guild_id)

            current_ids: set[str] = set()
            for player in players:
                steam_id = player["steam_id"]
                if not steam_id:
                    continue
                link = self.get_link_for_steam(settings.guild_id, steam_id)
                linked = link is not None
                if not self.should_track(settings, linked):
                    continue
                current_ids.add(steam_id)
                if linked:
                    self.update_link_name(settings.guild_id, steam_id, player["name"])
                await self.process_player(settings, player, map_name, observed_at)

            await self.handle_missing_players(settings, current_ids, observed_at)

    def detect_match_reset(
        self,
        guild_id: int,
        map_name: str,
        experience: str,
        total_score: int,
    ) -> bool:
        previous = self.previous_match_state.get(guild_id)
        self.previous_match_state[guild_id] = (map_name, experience, total_score)
        if previous is None:
            return False
        previous_map, previous_experience, previous_total_score = previous
        if previous_map != map_name or previous_experience != experience:
            return True
        return previous_total_score >= 50 and total_score <= 5 and total_score < previous_total_score

    @staticmethod
    def detect_global_player_reset(
        previous_players: dict[str, dict],
        current_players: dict[str, dict],
    ) -> bool:
        """Detect a server-wide round reset without treating normal deaths as resets."""

        common_ids = set(previous_players) & set(current_players)
        if len(common_ids) < 10:
            return False

        reset_signals = 0
        white_transitions = 0
        counter_resets = 0
        large_cash_drops = 0

        for steam_id in common_ids:
            before = previous_players[steam_id]
            after = current_players[steam_id]

            became_white = (
                before["faction"].casefold() != "white"
                and after["faction"].casefold() == "white"
            )
            counters_reset = (
                after["kills"] < before["kills"]
                or after["deaths"] < before["deaths"]
            )
            large_cash_drop = (
                after["cash"] <= before["cash"] - DEFAULT_CASH_THRESHOLD
            )

            if became_white:
                white_transitions += 1
            if counters_reset:
                counter_resets += 1
            if large_cash_drop:
                large_cash_drops += 1

            if (
                (became_white and large_cash_drop)
                or (counters_reset and large_cash_drop)
                or (became_white and counters_reset)
            ):
                reset_signals += 1

        required = max(5, int(len(common_ids) * 0.40))

        return (
            reset_signals >= required
            and (
                white_transitions >= required
                or counter_resets >= required
            )
            and large_cash_drops >= required
        )

    async def process_player(
        self,
        settings: TrackingSettings,
        player: dict,
        map_name: str,
        observed_at: int,
    ):
        steam_id = player["steam_id"]
        key = (settings.guild_id, steam_id)
        session = self.active_sessions.get(key)

        # Do not create a brand-new gameplay session from a lobby/transition
        # observation. White by itself is not treated as a death or match end.
        if player["faction"].casefold() == "white" and session is None:
            return

        # Per-match kill/death counters moving backwards is still a strong
        # indication that WARCON crossed into a new match state.
        if session is not None and (
            player["kills"] < session.current_kills
            or player["deaths"] < session.current_deaths
        ):
            await self.finish_session(
                settings,
                steam_id,
                observed_at,
                "match_end",
            )
            self.clear_player_cash_window(settings.guild_id, steam_id)
            session = None

        if session is None:
            session = SessionState(
                started_at=observed_at,
                last_seen_at=observed_at,
                missing_since=None,
                player_name=player["name"],
                faction=player["faction"],
                map_name=map_name,
                start_cash=player["cash"],
                current_cash=player["cash"],
                cash_net=0,
                start_kills=player["kills"],
                current_kills=player["kills"],
                start_deaths=player["deaths"],
                current_deaths=player["deaths"],
            )
            self.active_sessions[key] = session
        else:
            previous_cash = session.current_cash
            current_cash = player["cash"]

            if current_cash < 0:
                # A negative counter is a pending death/loss. Do not compare it
                # to a previous positive value because that would combine a
                # counter reset with the actual loss. Keep the deepest value.
                pending = self.pending_cash_losses.get(key)
                if pending is None or current_cash < pending:
                    self.pending_cash_losses[key] = current_cash

            elif previous_cash < 0:
                # Negative -> 0 is the normal respawn reset. If the exact zero
                # observation is skipped and WARCON next sees a positive value,
                # finalize the pending loss anyway and treat the positive value
                # as activity from a fresh zero baseline.
                pending_loss = self.pending_cash_losses.pop(
                    key,
                    previous_cash,
                )
                session.cash_net += pending_loss
                await self.maybe_send_finalized_loss(
                    settings,
                    player,
                    pending_loss,
                    observed_at,
                    session.faction,
                )

                self.cash_windows.pop(key, None)
                if current_cash > 0:
                    session.cash_net += current_cash

            elif previous_cash > 0 and current_cash == 0:
                # Positive -> 0 is a counter reset, not a cash loss. This is the
                # end-of-match/respawn style transition that previously created
                # false negative alerts. Start a fresh rolling baseline.
                self.cash_windows.pop(key, None)

            else:
                # Normal non-negative movement. This is used for match net cash
                # and for rolling gain detection.
                session.cash_net += current_cash - previous_cash

            session.last_seen_at = observed_at
            session.missing_since = None
            session.player_name = player["name"]
            if player["faction"].casefold() != "white":
                session.faction = player["faction"]
            session.map_name = map_name
            session.current_cash = current_cash
            session.current_kills = player["kills"]
            session.current_deaths = player["deaths"]

        self.maybe_save_sample(settings.guild_id, player, observed_at)
        await self.maybe_send_cash_alert(settings, player, observed_at)

    def clear_player_cash_window(self, guild_id: int, steam_id: str):
        """Drop rolling and pending cash state at a real match boundary."""

        key = (guild_id, steam_id)
        self.cash_windows.pop(key, None)
        self.pending_cash_losses.pop(key, None)

    def clear_guild_cash_windows(self, guild_id: int):
        """Drop all rolling and pending cash state for a guild."""

        window_keys = [
            key
            for key in self.cash_windows
            if key[0] == guild_id
        ]
        for key in window_keys:
            self.cash_windows.pop(key, None)

        pending_keys = [
            key
            for key in self.pending_cash_losses
            if key[0] == guild_id
        ]
        for key in pending_keys:
            self.pending_cash_losses.pop(key, None)

    def maybe_save_sample(self, guild_id: int, player: dict, observed_at: int):
        key = (guild_id, player["steam_id"])
        current = (player["cash"], player["kills"], player["deaths"], player["faction"])
        previous = self.last_saved.get(key)
        should_save = (
            previous is None
            or current != previous[:4]
            or observed_at - previous[4] >= SAMPLE_HEARTBEAT_SECONDS
        )
        if not should_save:
            return
        self.save_sample(
            guild_id,
            player["steam_id"],
            player["name"],
            player["faction"],
            player["cash"],
            player["kills"],
            player["deaths"],
            observed_at,
        )
        self.last_saved[key] = (*current, observed_at)

    async def maybe_send_finalized_loss(
        self,
        settings: TrackingSettings,
        player: dict,
        loss_amount: int,
        observed_at: int,
        gameplay_faction: str,
    ):
        """Publish a real death loss after the negative counter resets."""

        if loss_amount >= 0:
            return
        if abs(loss_amount) < settings.cash_threshold:
            return

        direction = "loss"
        alert_key = (
            settings.guild_id,
            player["steam_id"],
            direction,
        )
        last_alert = self.last_alerts.get(alert_key)
        if last_alert is None:
            last_alert = self.get_alert_time(
                settings.guild_id,
                player["steam_id"],
                direction,
            )
        if observed_at - last_alert < ALERT_COOLDOWN_SECONDS:
            return

        alert_player = dict(player)
        if alert_player["faction"].casefold() == "white":
            alert_player["faction"] = gameplay_faction

        if await self.send_cash_alert(
            settings,
            alert_player,
            0,
            0,
            loss_amount,
        ):
            self.last_alerts[alert_key] = observed_at
            self.save_alert_time(
                settings.guild_id,
                player["steam_id"],
                direction,
                observed_at,
            )

    async def maybe_send_cash_alert(
        self,
        settings: TrackingSettings,
        player: dict,
        observed_at: int,
    ):
        """Publish rolling positive gains. Losses use negative -> zero logic."""

        key = (settings.guild_id, player["steam_id"])
        current_cash = player["cash"]

        # Negative values are pending losses and are finalized only when the
        # WARDOGS counter resets to zero on respawn.
        if current_cash < 0:
            return

        window = self.cash_windows[key]

        # A zero following positive cash is a reset, not a negative movement.
        # Clear the old samples so a match/counter reset cannot become a loss.
        if current_cash == 0:
            if window and window[-1][1] > 0:
                window.clear()
            window.append((observed_at, 0))
            return

        window.append((observed_at, current_cash))
        minimum_time = observed_at - CASH_WINDOW_SECONDS
        while len(window) > 1 and window[0][0] < minimum_time:
            window.popleft()
        if len(window) < 2:
            return

        starting_cash = window[0][1]
        cash_delta = current_cash - starting_cash

        # Rolling cashflow alerts are gain-only. A true loss is represented by
        # the game's negative counter and handled by maybe_send_finalized_loss.
        if cash_delta < settings.cash_threshold:
            return

        direction = "gain"
        alert_key = (settings.guild_id, player["steam_id"], direction)
        last_alert = self.last_alerts.get(alert_key)
        if last_alert is None:
            last_alert = self.get_alert_time(
                settings.guild_id,
                player["steam_id"],
                direction,
            )
        if observed_at - last_alert < ALERT_COOLDOWN_SECONDS:
            return

        alert_player = player
        if player["faction"].casefold() == "white":
            session = self.active_sessions.get(key)
            if session is not None:
                alert_player = dict(player)
                alert_player["faction"] = session.faction

        if await self.send_cash_alert(
            settings,
            alert_player,
            starting_cash,
            current_cash,
            cash_delta,
        ):
            self.last_alerts[alert_key] = observed_at
            self.save_alert_time(
                settings.guild_id,
                player["steam_id"],
                direction,
                observed_at,
            )

    async def send_cash_alert(
        self,
        settings: TrackingSettings,
        player: dict,
        starting_cash: int,
        ending_cash: int,
        cash_delta: int,
    ) -> bool:
        channel = self.bot.get_channel(settings.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return False

        is_gain = cash_delta > 0
        embed = discord.Embed(
            title="💰 CASH GAIN" if is_gain else "📉 CASH LOSS",
            description=(
                f"## {self.format_cash(cash_delta, signed=True)}\n"
                "in the last 5 minutes"
            ),
            color=discord.Color.green() if is_gain else discord.Color.red(),
        )
        self.add_player_identity(
            embed,
            settings.guild_id,
            player["steam_id"],
            player["name"],
        )
        embed.add_field(
            name="Performance",
            value=(
                f"**{player['kills']} K**  •  "
                f"**{player['deaths']} D**  •  "
                f"**{player['faction']}**"
            ),
            inline=False,
        )
        embed.set_footer(text="ARES GROUP // WARCON LIVE")
        embed.timestamp = discord.utils.utcnow()

        try:
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    # ========================================================
    # MATCH / SESSION SUMMARIES
    # ========================================================

    async def handle_missing_players(
        self,
        settings: TrackingSettings,
        current_ids: set[str],
        observed_at: int,
    ):
        keys = [key for key in self.active_sessions if key[0] == settings.guild_id]
        for key in keys:
            steam_id = key[1]
            if steam_id in current_ids:
                continue
            state = self.active_sessions.get(key)
            if state is None:
                continue
            if state.missing_since is None:
                state.missing_since = observed_at
                continue
            if observed_at - state.missing_since < SESSION_ABSENCE_GRACE_SECONDS:
                continue
            await self.finish_session(settings, steam_id, observed_at, "player_left")

    async def finish_all_sessions(
        self,
        settings: TrackingSettings,
        ended_at: int,
        reason: str,
    ):
        steam_ids = [key[1] for key in self.active_sessions if key[0] == settings.guild_id]
        for steam_id in steam_ids:
            await self.finish_session(settings, steam_id, ended_at, reason)

    async def finish_session(
        self,
        settings: TrackingSettings,
        steam_id: str,
        ended_at: int,
        reason: str,
    ):
        key = (settings.guild_id, steam_id)
        state = self.active_sessions.pop(key, None)
        if state is None:
            return
        # Ignore empty transition sessions entirely. They should not become
        # matches in storage or generate zero-value public summaries.
        meaningful = (
            state.cash_net != 0
            or state.current_kills > 0
            or state.current_deaths > 0
        )
        if not meaningful:
            self.clear_player_cash_window(settings.guild_id, steam_id)
            return

        self.save_session(settings.guild_id, steam_id, state, ended_at, reason)
        self.clear_player_cash_window(settings.guild_id, steam_id)

        # Disconnects are stored for history but stay out of the public feed.
        # Only an actual match boundary gets a public summary.
        if reason != "match_end":
            return

        # Global mode can track everyone for future leaderboards, but summaries
        # only post for linked Discord members so the feed does not explode.
        if self.get_link_for_steam(settings.guild_id, steam_id) is None:
            return
        await self.send_session_summary(settings, steam_id, state, reason)

    async def send_session_summary(
        self,
        settings: TrackingSettings,
        steam_id: str,
        state: SessionState,
        reason: str,
    ):
        channel = self.bot.get_channel(settings.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        kills_delta = max(0, state.current_kills)
        deaths_delta = max(0, state.current_deaths)
        cash_delta = state.cash_net
        title = "🏁 MATCH SUMMARY"

        embed = discord.Embed(
            title=title,
            description=(
                f"## {self.format_cash(cash_delta, signed=True)}\n"
                "net cash"
            ),
            color=ARES_RED,
        )
        self.add_player_identity(embed, settings.guild_id, steam_id, state.player_name)
        embed.add_field(
            name="Performance",
            value=(
                f"**{kills_delta} K**  •  "
                f"**{deaths_delta} D**  •  "
                f"**{self.kd(kills_delta, deaths_delta)} K/D**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Match",
            value=f"**{state.map_name}**  •  **{state.faction}**",
            inline=False,
        )
        embed.set_footer(text="ARES GROUP // WARCON STATS")
        embed.timestamp = discord.utils.utcnow()
        try:
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    def add_player_identity(
        self,
        embed: discord.Embed,
        guild_id: int,
        steam_id: str,
        player_name: str,
    ):
        link = self.get_link_for_steam(guild_id, steam_id)
        identity = f"**{player_name}**"
        if link is not None:
            identity = f"<@{link[0]}>\n{identity}"
        embed.add_field(
            name="Player",
            value=identity,
            inline=False,
        )

    # ========================================================
    # USER COMMANDS
    # ========================================================

    @app_commands.command(
        name="track",
        description="Legacy alias for /link.",
    )
    @app_commands.describe(steamid="Your 17-digit SteamID64.")
    async def track(
        self,
        interaction: discord.Interaction,
        steamid: str,
    ):
        """Keep the old command working while using the shared link system."""

        priority_cog = self.bot.get_cog(
            "PriorityQueueCog"
        )

        if priority_cog is None:
            await interaction.response.send_message(
                "Steam linking isn't set up on this bot. (Ares Group runs it through ares-group.org; this open-source copy leaves it out.)",
                ephemeral=True,
            )
            return

        await priority_cog.handle_link(
            interaction,
            steamid,
        )

    @app_commands.command(
        name="untrack",
        description="Legacy alias for /unlink.",
    )
    async def untrack(
        self,
        interaction: discord.Interaction,
    ):
        """Keep the old command working while safely cleaning priority access."""

        priority_cog = self.bot.get_cog(
            "PriorityQueueCog"
        )

        if priority_cog is None:
            await interaction.response.send_message(
                "Steam linking isn't set up on this bot. (Ares Group runs it through ares-group.org; this open-source copy leaves it out.)",
                ephemeral=True,
            )
            return

        await priority_cog.handle_unlink(
            interaction
        )

    @app_commands.command(
        name="profile",
        description="Show a linked member's current WARDOGS stat profile.",
    )
    @app_commands.describe(member="Optional Discord member. Defaults to yourself.")
    async def profile(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ):
        if interaction.guild is None:
            await interaction.response.send_message("Use this command inside the Ares server.", ephemeral=True)
            return

        target = member if member is not None else interaction.user
        link = self.get_link_for_discord(interaction.guild.id, target.id)
        if link is None:
            await interaction.response.send_message(
                f"{target.mention} has not linked a SteamID64 with `/link`.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        steam_id = link[0]
        live_player = self.latest_players.get(steam_id)
        if live_player is not None:
            player_name = live_player["name"]
            faction = live_player["faction"]
            cash = live_player["cash"]
            kills = live_player["kills"]
            deaths = live_player["deaths"]
            ping = live_player["ping"]
            online = True
            observed_at = int(time.time())
        else:
            sample = self.get_latest_sample(interaction.guild.id, steam_id)
            if sample is None:
                player_name = link[1] or "Not observed yet"
                faction = "Unknown"
                cash = kills = deaths = ping = observed_at = 0
                online = False
            else:
                player_name = str(sample[0])
                faction = str(sample[1])
                cash = int(sample[2])
                kills = int(sample[3])
                deaths = int(sample[4])
                observed_at = int(sample[5])
                ping = 0
                online = False

        embed = discord.Embed(
            title="ARES GROUP // WARDOGS PROFILE",
            description=f"{target.mention}\n**{player_name}**",
            color=ARES_RED,
        )
        embed.add_field(name="Status", value="Online" if online else "Offline", inline=True)
        embed.add_field(name="Observed Cash", value=self.format_cash(cash), inline=True)
        embed.add_field(name="Faction", value=faction, inline=True)
        embed.add_field(name="Combat", value=f"**{kills} K**  •  **{deaths} D**", inline=False)
        if online:
            embed.add_field(name="Ping", value=f"{ping} ms", inline=True)
        elif observed_at > 0:
            embed.add_field(name="Last Observed", value=f"<t:{observed_at}:R>", inline=True)
        embed.set_footer(
            text="Observed cash is the latest value WARCON saw."
        )
        await interaction.response.send_message(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ========================================================
    # ADMIN SETTINGS
    # ========================================================


    @stattracking.command(
        name="leaderboard",
        description="Create or move a static cash or kill leaderboard.",
    )
    @app_commands.describe(
        kind="Which leaderboard to maintain.",
        channel="Channel where Ares should keep the leaderboard.",
        scope="Local shows linked members. Global shows all tracked players.",
    )
    @app_commands.choices(
        kind=[
            app_commands.Choice(name="Cash", value="cash"),
            app_commands.Choice(name="Kills", value="kills"),
        ],
        scope=[
            app_commands.Choice(name="Local - linked members only", value=TRACKING_MODE_LOCAL),
            app_commands.Choice(name="Global - everyone tracked", value=TRACKING_MODE_GLOBAL),
        ],
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def stattracking_leaderboard(
        self,
        interaction: discord.Interaction,
        kind: app_commands.Choice[str],
        channel: discord.TextChannel,
        scope: app_commands.Choice[str],
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command inside a server.",
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
                f"Ares cannot maintain a leaderboard in {channel.mention}.\n\n"
                "Missing permissions: " + ", ".join(missing),
                ephemeral=True,
            )
            return

        self.save_leaderboard_config(
            interaction.guild.id,
            kind.value,
            channel.id,
            scope.value,
        )
        await self.refresh_all_leaderboards()

        await interaction.response.send_message(
            f"Static **{kind.name}** leaderboard configured.\n\n"
            f"Channel: {channel.mention}\n"
            f"Scope: **{scope.name}**\n"
            "Refresh cadence: **every 2 minutes**",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @stattracking.command(
        name="setup",
        description="Set the stat feed channel, scope, and cash threshold.",
    )
    @app_commands.describe(
        channel="Channel where Ares should post WARCON stat embeds.",
        mode="Local tracks linked members. Global records all server players.",
        threshold="Cash change required for a 5-minute gain/loss alert.",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Local - linked Discord members only", value=TRACKING_MODE_LOCAL),
            app_commands.Choice(name="Global - everyone on the WARDOGS server", value=TRACKING_MODE_GLOBAL),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def stattracking_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        mode: app_commands.Choice[str],
        threshold: app_commands.Range[int, 1000, 1000000] = DEFAULT_CASH_THRESHOLD,
    ):
        if interaction.guild is None:
            await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
            return

        bot_member = interaction.guild.me
        if bot_member is None:
            await interaction.response.send_message("Ares could not verify its Discord permissions.", ephemeral=True)
            return

        permissions = channel.permissions_for(bot_member)
        missing = []
        if not permissions.view_channel:
            missing.append("View Channel")
        if not permissions.send_messages:
            missing.append("Send Messages")
        if not permissions.embed_links:
            missing.append("Embed Links")
        if missing:
            await interaction.response.send_message(
                f"Ares cannot use {channel.mention}. Missing permissions: " + ", ".join(missing),
                ephemeral=True,
            )
            return

        self.save_settings(interaction.guild.id, channel.id, mode.value, int(threshold))
        await interaction.response.send_message(
            (
                "WARDOGS stat tracking configured.\n\n"
                f"Channel: {channel.mention}\n"
                f"Mode: **{mode.name}**\n"
                f"Cash alert: **{self.format_cash(int(threshold))}** net movement within 5 minutes\n\n"
                "Linked Discord users are displayed inside embeds without receiving a ping."
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @stattracking.command(name="mode", description="Change between local and global stat tracking.")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Local - linked Discord members only", value=TRACKING_MODE_LOCAL),
            app_commands.Choice(name="Global - everyone on the WARDOGS server", value=TRACKING_MODE_GLOBAL),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def stattracking_mode(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
    ):
        if interaction.guild is None:
            await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
            return
        settings = self.get_settings(interaction.guild.id)
        if settings is None:
            await interaction.response.send_message("Run `/stattracking setup` first.", ephemeral=True)
            return
        self.save_settings(settings.guild_id, settings.channel_id, mode.value, settings.cash_threshold)
        await interaction.response.send_message(f"Stat tracking mode changed to **{mode.name}**.", ephemeral=True)

    @stattracking.command(name="channel", description="Change the stat feed destination.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def stattracking_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        if interaction.guild is None:
            await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
            return
        settings = self.get_settings(interaction.guild.id)
        if settings is None:
            await interaction.response.send_message("Run `/stattracking setup` first.", ephemeral=True)
            return
        self.save_settings(settings.guild_id, channel.id, settings.mode, settings.cash_threshold)
        await interaction.response.send_message(
            f"Stat feed moved to {channel.mention}.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @stattracking.command(name="threshold", description="Change the 5-minute cash alert threshold.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def stattracking_threshold(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1000, 1000000],
    ):
        if interaction.guild is None:
            await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
            return
        settings = self.get_settings(interaction.guild.id)
        if settings is None:
            await interaction.response.send_message("Run `/stattracking setup` first.", ephemeral=True)
            return
        self.save_settings(settings.guild_id, settings.channel_id, settings.mode, int(amount))
        await interaction.response.send_message(
            f"Cash alert threshold changed to **{self.format_cash(int(amount))}** within 5 minutes.",
            ephemeral=True,
        )

    @stattracking.command(name="status", description="Show the current WARDOGS stat tracking configuration.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def stattracking_status(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
            return
        settings = self.get_settings(interaction.guild.id)
        if settings is None:
            await interaction.response.send_message(
                "WARDOGS stat tracking has not been configured. Run `/stattracking setup`.",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(settings.channel_id)
        channel_text = channel.mention if isinstance(channel, discord.TextChannel) else f"`{settings.channel_id}` (missing)"
        embed = discord.Embed(title="ARES GROUP // STAT TRACKING", color=ARES_RED)
        embed.add_field(
            name="WARCON",
            value="Configured" if self.warcon_configured() else "Missing environment settings",
            inline=False,
        )
        embed.add_field(name="Channel", value=channel_text, inline=False)
        embed.add_field(name="Mode", value=settings.mode.title(), inline=True)
        embed.add_field(
            name="Cash Threshold",
            value=f"{self.format_cash(settings.cash_threshold)} within 5 minutes",
            inline=True,
        )
        embed.add_field(name="Cached Online Players", value=str(len(self.latest_players)), inline=True)
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        if isinstance(error, app_commands.MissingPermissions):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "You need the **Manage Server** permission to configure WARDOGS stat tracking.",
                    ephemeral=True,
                )
            return
        raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(StatTrackingCog(bot))
