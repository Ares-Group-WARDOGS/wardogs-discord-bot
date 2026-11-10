import sqlite3

from ares.config import ROLE_DB_PATH


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def initialize_database():
    """Create and migrate persistent Ares bot database tables."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    # ========================================================
    # ROLE PANELS
    # ========================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS role_panels (
            message_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reaction_roles (
            message_id INTEGER NOT NULL,
            emoji TEXT NOT NULL,
            role_id INTEGER NOT NULL,
            PRIMARY KEY (message_id, emoji)
        )
        """
    )

    # ========================================================
    # GUILD SETTINGS
    # ========================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER PRIMARY KEY,
            command_prefix TEXT NOT NULL DEFAULT '!',
            admin_role_id INTEGER
        )
        """
    )

    # Older Ares databases were created before the Ares Admin
    # role existed. Add the column if necessary.
    cursor.execute(
        """
        PRAGMA table_info(guild_settings)
        """
    )

    guild_settings_columns = {
        row[1]
        for row in cursor.fetchall()
    }

    if "admin_role_id" not in guild_settings_columns:
        cursor.execute(
            """
            ALTER TABLE guild_settings
            ADD COLUMN admin_role_id INTEGER
            """
        )

    # ========================================================
    # WELCOME SETTINGS
    # ========================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS welcome_settings (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            enabled INTEGER NOT NULL DEFAULT 0,
            reaction_emoji TEXT
        )
        """
    )

    # Older Ares databases were created before welcome
    # reactions existed. Add the column if necessary.
    cursor.execute(
        """
        PRAGMA table_info(welcome_settings)
        """
    )

    welcome_columns = {
        row[1]
        for row in cursor.fetchall()
    }

    if "reaction_emoji" not in welcome_columns:
        cursor.execute(
            """
            ALTER TABLE welcome_settings
            ADD COLUMN reaction_emoji TEXT
            """
        )

    # ========================================================
    # WELCOME BACKGROUNDS
    # ========================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS welcome_backgrounds (
            guild_id INTEGER PRIMARY KEY,
            image_data BLOB NOT NULL,
            original_filename TEXT,
            updated_at INTEGER NOT NULL
        )
        """
    )

    # ========================================================
    # LOG SETTINGS
    # ========================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS log_settings (
            guild_id INTEGER NOT NULL,
            log_type TEXT NOT NULL,
            channel_id INTEGER NOT NULL,
            PRIMARY KEY (guild_id, log_type)
        )
        """
    )

    # ========================================================
    # ROSTER SETTINGS
    # ========================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS roster_settings (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            message_id INTEGER
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS roster_tabs (
            guild_id INTEGER NOT NULL,
            tab_key TEXT NOT NULL,
            name TEXT NOT NULL,
            emoji TEXT NOT NULL,
            role_id INTEGER NOT NULL,
            sort_order INTEGER NOT NULL,
            PRIMARY KEY (guild_id, tab_key)
        )
        """
    )

    # ========================================================
    # ROLE PANEL GROUP LIMITS
    # ========================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS role_groups (
            group_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            name_key TEXT NOT NULL,
            max_roles INTEGER NOT NULL,
            UNIQUE (guild_id, name_key)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS role_group_roles (
            group_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL UNIQUE,
            PRIMARY KEY (group_id, role_id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS role_selections (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            selected_at INTEGER NOT NULL,
            PRIMARY KEY (
                guild_id,
                user_id,
                group_id,
                role_id
            )
        )
        """
    )

    # ========================================================
    # COUNTDOWNS
    # ========================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS countdowns (
            countdown_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL UNIQUE,
            target_unix INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            name TEXT NOT NULL DEFAULT 'SERVER SLAM',
            last_name TEXT,
            last_renamed_at INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    cursor.execute(
        """
        PRAGMA table_info(countdowns)
        """
    )

    countdown_columns = {
        row[1]
        for row in cursor.fetchall()
    }

    if "name" not in countdown_columns:
        cursor.execute(
            """
            ALTER TABLE countdowns
            ADD COLUMN name TEXT NOT NULL DEFAULT 'SERVER SLAM'
            """
        )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_countdowns_guild_order
        ON countdowns (
            guild_id,
            countdown_id
        )
        """
    )

    # ========================================================
    # BUMP REMINDER SETTINGS
    # ========================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS bump_reminder_settings (
            guild_id INTEGER PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS bump_reminders (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            due_at INTEGER NOT NULL
        )
        """
    )

    # ========================================================
    # SAVE SCHEMA CHANGES
    # ========================================================

    connection.commit()
    connection.close()


# ============================================================
# ROLE PANELS
# ============================================================

def save_role_panel(
    message_id: int,
    guild_id: int,
    channel_id: int,
):
    """Save a role panel."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO role_panels (
            message_id,
            guild_id,
            channel_id
        )
        VALUES (?, ?, ?)
        """,
        (
            message_id,
            guild_id,
            channel_id,
        ),
    )

    connection.commit()
    connection.close()


def save_reaction_role(
    message_id: int,
    emoji: str,
    role_id: int,
):
    """Save an emoji-to-role mapping."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO reaction_roles (
            message_id,
            emoji,
            role_id
        )
        VALUES (?, ?, ?)
        """,
        (
            message_id,
            emoji,
            role_id,
        ),
    )

    connection.commit()
    connection.close()


def get_reaction_role(
    message_id: int,
    emoji: str,
) -> int | None:
    """Return the role ID associated with a reaction."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT role_id
        FROM reaction_roles
        WHERE message_id = ?
        AND emoji = ?
        """,
        (
            message_id,
            emoji,
        ),
    )

    result = cursor.fetchone()

    connection.close()

    if result is None:
        return None

    return int(
        result[0]
    )


# ============================================================
# REACTION ROLE LOOKUP HELPERS
# ============================================================

def delete_role_panel(
    message_id: int,
):
    """Delete a role panel and all of its emoji-role mappings."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM reaction_roles
        WHERE message_id = ?
        """,
        (
            message_id,
        ),
    )

    cursor.execute(
        """
        DELETE FROM role_panels
        WHERE message_id = ?
        """,
        (
            message_id,
        ),
    )

    connection.commit()
    connection.close()



def get_reaction_locations_for_role(
    role_id: int,
) -> list[tuple[int, int, str]]:
    """Return panel message, channel, and emoji mappings for a role."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            reaction_roles.message_id,
            role_panels.channel_id,
            reaction_roles.emoji
        FROM reaction_roles
        INNER JOIN role_panels
            ON role_panels.message_id = reaction_roles.message_id
        WHERE reaction_roles.role_id = ?
        """,
        (
            role_id,
        ),
    )

    rows = cursor.fetchall()

    connection.close()

    return [
        (
            int(message_id),
            int(channel_id),
            str(emoji),
        )
        for (
            message_id,
            channel_id,
            emoji,
        ) in rows
    ]


# ============================================================
# COMMAND PREFIX
# ============================================================

def load_command_prefixes() -> dict[int, str]:
    """Load all saved command prefixes."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT guild_id, command_prefix
        FROM guild_settings
        """
    )

    rows = cursor.fetchall()

    connection.close()

    return {
        int(guild_id): prefix
        for guild_id, prefix in rows
    }


def set_command_prefix(
    guild_id: int,
    prefix: str,
):
    """Save the command prefix for a guild."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO guild_settings (
            guild_id,
            command_prefix
        )
        VALUES (?, ?)
        ON CONFLICT(guild_id)
        DO UPDATE SET
            command_prefix = excluded.command_prefix
        """,
        (
            guild_id,
            prefix,
        ),
    )

    connection.commit()
    connection.close()


# ============================================================
# ARES ADMIN ROLE
# ============================================================

def set_admin_role_id(
    guild_id: int,
    role_id: int,
):
    """Save the Ares Admin role for a guild."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO guild_settings (
            guild_id,
            admin_role_id
        )
        VALUES (?, ?)
        ON CONFLICT(guild_id)
        DO UPDATE SET
            admin_role_id = excluded.admin_role_id
        """,
        (
            guild_id,
            role_id,
        ),
    )

    connection.commit()
    connection.close()


def get_admin_role_id(
    guild_id: int,
) -> int | None:
    """Return the configured Ares Admin role ID."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT admin_role_id
        FROM guild_settings
        WHERE guild_id = ?
        """,
        (
            guild_id,
        ),
    )

    result = cursor.fetchone()

    connection.close()

    if result is None or result[0] is None:
        return None

    return int(
        result[0]
    )


def clear_admin_role_id(
    guild_id: int,
):
    """Clear the configured Ares Admin role for a guild."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE guild_settings
        SET admin_role_id = NULL
        WHERE guild_id = ?
        """,
        (
            guild_id,
        ),
    )

    connection.commit()
    connection.close()


# ============================================================
# WELCOME SETTINGS
# ============================================================

def set_welcome_settings(
    guild_id: int,
    channel_id: int | None,
    enabled: bool,
    reaction_emoji: str | None,
):
    """Save welcome-message settings for a guild."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO welcome_settings (
            guild_id,
            channel_id,
            enabled,
            reaction_emoji
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id)
        DO UPDATE SET
            channel_id = excluded.channel_id,
            enabled = excluded.enabled,
            reaction_emoji = excluded.reaction_emoji
        """,
        (
            guild_id,
            channel_id,
            int(enabled),
            reaction_emoji,
        ),
    )

    connection.commit()
    connection.close()


def get_welcome_settings(
    guild_id: int,
) -> tuple[int | None, bool, str | None] | None:
    """Return the configured welcome settings."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            channel_id,
            enabled,
            reaction_emoji
        FROM welcome_settings
        WHERE guild_id = ?
        """,
        (
            guild_id,
        ),
    )

    result = cursor.fetchone()

    connection.close()

    if result is None:
        return None

    return (
        result[0],
        bool(result[1]),
        result[2],
    )


# ============================================================
# WELCOME BACKGROUNDS
# ============================================================

def set_welcome_background(
    guild_id: int,
    image_data: bytes,
    original_filename: str | None,
    updated_at: int,
):
    """Save the normalized welcome background for a guild."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO welcome_backgrounds (
            guild_id,
            image_data,
            original_filename,
            updated_at
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id)
        DO UPDATE SET
            image_data = excluded.image_data,
            original_filename = excluded.original_filename,
            updated_at = excluded.updated_at
        """,
        (
            guild_id,
            sqlite3.Binary(
                image_data
            ),
            original_filename,
            updated_at,
        ),
    )

    connection.commit()
    connection.close()


def get_welcome_background(
    guild_id: int,
) -> tuple[bytes, str | None] | None:
    """Return the saved welcome background and original filename."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            image_data,
            original_filename
        FROM welcome_backgrounds
        WHERE guild_id = ?
        """,
        (
            guild_id,
        ),
    )

    result = cursor.fetchone()

    connection.close()

    if result is None:
        return None

    return (
        bytes(
            result[0]
        ),
        (
            str(
                result[1]
            )
            if result[1] is not None
            else None
        ),
    )


def clear_welcome_background(
    guild_id: int,
) -> bool:
    """Remove the saved welcome background for a guild."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM welcome_backgrounds
        WHERE guild_id = ?
        """,
        (
            guild_id,
        ),
    )

    removed = cursor.rowcount > 0

    connection.commit()
    connection.close()

    return removed


# ============================================================
# LOG SETTINGS
# ============================================================

def set_log_channel(
    guild_id: int,
    log_type: str,
    channel_id: int,
):
    """Save a log destination for a guild."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO log_settings (
            guild_id,
            log_type,
            channel_id
        )
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, log_type)
        DO UPDATE SET
            channel_id = excluded.channel_id
        """,
        (
            guild_id,
            log_type,
            channel_id,
        ),
    )

    connection.commit()
    connection.close()


def get_log_channel(
    guild_id: int,
    log_type: str,
) -> int | None:
    """Return the configured channel ID for a log type."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT channel_id
        FROM log_settings
        WHERE guild_id = ?
        AND log_type = ?
        """,
        (
            guild_id,
            log_type,
        ),
    )

    result = cursor.fetchone()

    connection.close()

    if result is None:
        return None

    return int(
        result[0]
    )


# ============================================================
# ROSTER SETTINGS
# ============================================================

def set_roster_message(
    guild_id: int,
    channel_id: int,
    message_id: int,
):
    """Save the persistent roster message location."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO roster_settings (
            guild_id,
            channel_id,
            message_id
        )
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id)
        DO UPDATE SET
            channel_id = excluded.channel_id,
            message_id = excluded.message_id
        """,
        (
            guild_id,
            channel_id,
            message_id,
        ),
    )

    connection.commit()
    connection.close()


def get_roster_message(
    guild_id: int,
) -> tuple[int, int] | None:
    """Return the configured roster channel and message IDs."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT channel_id, message_id
        FROM roster_settings
        WHERE guild_id = ?
        """,
        (
            guild_id,
        ),
    )

    result = cursor.fetchone()

    connection.close()

    if result is None:
        return None

    if result[0] is None or result[1] is None:
        return None

    return (
        int(result[0]),
        int(result[1]),
    )


def clear_roster_message(
    guild_id: int,
):
    """Clear the saved roster message without deleting roster tabs."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO roster_settings (
            guild_id,
            channel_id,
            message_id
        )
        VALUES (?, NULL, NULL)
        ON CONFLICT(guild_id)
        DO UPDATE SET
            channel_id = NULL,
            message_id = NULL
        """,
        (
            guild_id,
        ),
    )

    connection.commit()
    connection.close()


def save_roster_tab(
    guild_id: int,
    name: str,
    emoji: str,
    role_id: int,
):
    """Create or update a roster tab while preserving tab order."""

    tab_key = name.casefold()

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT sort_order
        FROM roster_tabs
        WHERE guild_id = ?
        AND tab_key = ?
        """,
        (
            guild_id,
            tab_key,
        ),
    )

    existing = cursor.fetchone()

    if existing is None:
        cursor.execute(
            """
            SELECT COALESCE(MAX(sort_order), -1) + 1
            FROM roster_tabs
            WHERE guild_id = ?
            """,
            (
                guild_id,
            ),
        )

        sort_order = int(
            cursor.fetchone()[0]
        )

    else:
        sort_order = int(
            existing[0]
        )

    cursor.execute(
        """
        INSERT INTO roster_tabs (
            guild_id,
            tab_key,
            name,
            emoji,
            role_id,
            sort_order
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, tab_key)
        DO UPDATE SET
            name = excluded.name,
            emoji = excluded.emoji,
            role_id = excluded.role_id,
            sort_order = excluded.sort_order
        """,
        (
            guild_id,
            tab_key,
            name,
            emoji,
            role_id,
            sort_order,
        ),
    )

    connection.commit()
    connection.close()


def remove_roster_tab(
    guild_id: int,
    name: str,
) -> bool:
    """Remove a roster tab by name."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM roster_tabs
        WHERE guild_id = ?
        AND tab_key = ?
        """,
        (
            guild_id,
            name.casefold(),
        ),
    )

    removed = cursor.rowcount > 0

    connection.commit()
    connection.close()

    return removed


def get_roster_tabs(
    guild_id: int,
) -> list[tuple[str, str, int, int]]:
    """Return configured roster tabs in display order."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            name,
            emoji,
            role_id,
            sort_order
        FROM roster_tabs
        WHERE guild_id = ?
        ORDER BY sort_order ASC, name COLLATE NOCASE ASC
        """,
        (
            guild_id,
        ),
    )

    rows = cursor.fetchall()

    connection.close()

    return [
        (
            str(name),
            str(emoji),
            int(role_id),
            int(sort_order),
        )
        for (
            name,
            emoji,
            role_id,
            sort_order,
        ) in rows
    ]


def get_roster_role_ids(
    guild_id: int,
) -> set[int]:
    """Return role IDs currently used by the roster."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT role_id
        FROM roster_tabs
        WHERE guild_id = ?
        """,
        (
            guild_id,
        ),
    )

    rows = cursor.fetchall()

    connection.close()

    return {
        int(row[0])
        for row in rows
    }


# ============================================================
# ROLE PANEL GROUP LIMITS
# ============================================================

def create_role_group(
    guild_id: int,
    name: str,
    max_roles: int,
) -> bool:
    """Create a configurable role-limit group."""

    name = name.strip()

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO role_groups (
                guild_id,
                name,
                name_key,
                max_roles
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                guild_id,
                name,
                name.casefold(),
                max_roles,
            ),
        )

    except sqlite3.IntegrityError:
        connection.close()
        return False

    connection.commit()
    connection.close()

    return True


def get_role_group(
    guild_id: int,
    name: str,
) -> tuple[int, str, int] | None:
    """Return a role group by its case-insensitive name."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            group_id,
            name,
            max_roles
        FROM role_groups
        WHERE guild_id = ?
        AND name_key = ?
        """,
        (
            guild_id,
            name.strip().casefold(),
        ),
    )

    result = cursor.fetchone()

    connection.close()

    if result is None:
        return None

    return (
        int(result[0]),
        str(result[1]),
        int(result[2]),
    )


def get_role_groups(
    guild_id: int,
) -> list[tuple[int, str, int]]:
    """Return every configured role group for a guild."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            group_id,
            name,
            max_roles
        FROM role_groups
        WHERE guild_id = ?
        ORDER BY name COLLATE NOCASE ASC
        """,
        (
            guild_id,
        ),
    )

    rows = cursor.fetchall()

    connection.close()

    return [
        (
            int(group_id),
            str(name),
            int(max_roles),
        )
        for (
            group_id,
            name,
            max_roles,
        ) in rows
    ]


def set_role_group_limit(
    guild_id: int,
    name: str,
    max_roles: int,
) -> bool:
    """Change the maximum simultaneous roles for a group."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE role_groups
        SET max_roles = ?
        WHERE guild_id = ?
        AND name_key = ?
        """,
        (
            max_roles,
            guild_id,
            name.strip().casefold(),
        ),
    )

    updated = cursor.rowcount > 0

    connection.commit()
    connection.close()

    return updated


def delete_role_group(
    guild_id: int,
    name: str,
) -> bool:
    """Delete a role group and its associated tracking data."""

    group = get_role_group(
        guild_id,
        name,
    )

    if group is None:
        return False

    group_id = group[0]

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM role_selections
        WHERE guild_id = ?
        AND group_id = ?
        """,
        (
            guild_id,
            group_id,
        ),
    )

    cursor.execute(
        """
        DELETE FROM role_group_roles
        WHERE group_id = ?
        """,
        (
            group_id,
        ),
    )

    cursor.execute(
        """
        DELETE FROM role_groups
        WHERE group_id = ?
        """,
        (
            group_id,
        ),
    )

    connection.commit()
    connection.close()

    return True


def add_role_to_group(
    guild_id: int,
    group_name: str,
    role_id: int,
) -> str:
    """Add a Discord role to a role-limit group."""

    group = get_role_group(
        guild_id,
        group_name,
    )

    if group is None:
        return "group_missing"

    group_id = group[0]

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT group_id
        FROM role_group_roles
        WHERE role_id = ?
        """,
        (
            role_id,
        ),
    )

    existing = cursor.fetchone()

    if existing is not None:
        connection.close()

        if int(existing[0]) == group_id:
            return "already_in_group"

        return "role_in_other_group"

    cursor.execute(
        """
        INSERT INTO role_group_roles (
            group_id,
            role_id
        )
        VALUES (?, ?)
        """,
        (
            group_id,
            role_id,
        ),
    )

    connection.commit()
    connection.close()

    return "added"


def remove_role_from_group(
    guild_id: int,
    group_name: str,
    role_id: int,
) -> str:
    """Remove a Discord role from a role-limit group."""

    group = get_role_group(
        guild_id,
        group_name,
    )

    if group is None:
        return "group_missing"

    group_id = group[0]

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM role_group_roles
        WHERE group_id = ?
        AND role_id = ?
        """,
        (
            group_id,
            role_id,
        ),
    )

    removed = cursor.rowcount > 0

    if removed:
        cursor.execute(
            """
            DELETE FROM role_selections
            WHERE guild_id = ?
            AND group_id = ?
            AND role_id = ?
            """,
            (
                guild_id,
                group_id,
                role_id,
            ),
        )

    connection.commit()
    connection.close()

    if not removed:
        return "role_missing"

    return "removed"


def get_roles_for_group(
    guild_id: int,
    group_name: str,
) -> list[int] | None:
    """Return role IDs assigned to one configured group."""

    group = get_role_group(
        guild_id,
        group_name,
    )

    if group is None:
        return None

    group_id = group[0]

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT role_id
        FROM role_group_roles
        WHERE group_id = ?
        ORDER BY role_id ASC
        """,
        (
            group_id,
        ),
    )

    rows = cursor.fetchall()

    connection.close()

    return [
        int(row[0])
        for row in rows
    ]


def get_role_group_for_role(
    role_id: int,
) -> tuple[int, int, str, int] | None:
    """Return group information for a configured Discord role."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            role_groups.group_id,
            role_groups.guild_id,
            role_groups.name,
            role_groups.max_roles
        FROM role_group_roles
        INNER JOIN role_groups
            ON role_groups.group_id = role_group_roles.group_id
        WHERE role_group_roles.role_id = ?
        """,
        (
            role_id,
        ),
    )

    result = cursor.fetchone()

    connection.close()

    if result is None:
        return None

    return (
        int(result[0]),
        int(result[1]),
        str(result[2]),
        int(result[3]),
    )


def save_role_selection(
    guild_id: int,
    user_id: int,
    group_id: int,
    role_id: int,
    selected_at: int,
):
    """Record when a member selected a limited-group role."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO role_selections (
            guild_id,
            user_id,
            group_id,
            role_id,
            selected_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (
            guild_id,
            user_id,
            group_id,
            role_id
        )
        DO UPDATE SET
            selected_at = excluded.selected_at
        """,
        (
            guild_id,
            user_id,
            group_id,
            role_id,
            selected_at,
        ),
    )

    connection.commit()
    connection.close()


def delete_role_selection(
    guild_id: int,
    user_id: int,
    group_id: int,
    role_id: int,
):
    """Remove a tracked limited-group role selection."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM role_selections
        WHERE guild_id = ?
        AND user_id = ?
        AND group_id = ?
        AND role_id = ?
        """,
        (
            guild_id,
            user_id,
            group_id,
            role_id,
        ),
    )

    connection.commit()
    connection.close()


def get_role_selections(
    guild_id: int,
    user_id: int,
    group_id: int,
) -> list[tuple[int, int]]:
    """Return tracked selections oldest first."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            role_id,
            selected_at
        FROM role_selections
        WHERE guild_id = ?
        AND user_id = ?
        AND group_id = ?
        ORDER BY selected_at ASC, role_id ASC
        """,
        (
            guild_id,
            user_id,
            group_id,
        ),
    )

    rows = cursor.fetchall()

    connection.close()

    return [
        (
            int(role_id),
            int(selected_at),
        )
        for (
            role_id,
            selected_at,
        ) in rows
    ]


# ============================================================
# COUNTDOWNS
# ============================================================

def create_countdown(
    guild_id: int,
    channel_id: int,
    target_unix: int,
    created_at: int,
    name: str,
    last_name: str,
    last_renamed_at: int,
) -> int:
    """Persist a newly created countdown voice channel."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO countdowns (
            guild_id,
            channel_id,
            target_unix,
            created_at,
            name,
            last_name,
            last_renamed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            channel_id,
            target_unix,
            created_at,
            name,
            last_name,
            last_renamed_at,
        ),
    )

    countdown_id = int(
        cursor.lastrowid
    )

    connection.commit()
    connection.close()

    return countdown_id


def get_countdowns(
    guild_id: int,
) -> list[
    tuple[
        int,
        int,
        int,
        int,
        str,
        str | None,
        int,
    ]
]:
    """Return guild countdowns in creation order, oldest first."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            countdown_id,
            channel_id,
            target_unix,
            created_at,
            name,
            last_name,
            last_renamed_at
        FROM countdowns
        WHERE guild_id = ?
        ORDER BY countdown_id ASC
        """,
        (
            guild_id,
        ),
    )

    rows = cursor.fetchall()

    connection.close()

    return [
        (
            int(countdown_id),
            int(channel_id),
            int(target_unix),
            int(created_at),
            str(name),
            (
                str(last_name)
                if last_name is not None
                else None
            ),
            int(last_renamed_at),
        )
        for (
            countdown_id,
            channel_id,
            target_unix,
            created_at,
            name,
            last_name,
            last_renamed_at,
        ) in rows
    ]


def get_all_countdowns(
) -> list[
    tuple[
        int,
        int,
        int,
        int,
        int,
        str,
        str | None,
        int,
    ]
]:
    """Return every persistent countdown in creation order."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            countdown_id,
            guild_id,
            channel_id,
            target_unix,
            created_at,
            name,
            last_name,
            last_renamed_at
        FROM countdowns
        ORDER BY guild_id ASC, countdown_id ASC
        """
    )

    rows = cursor.fetchall()

    connection.close()

    return [
        (
            int(countdown_id),
            int(guild_id),
            int(channel_id),
            int(target_unix),
            int(created_at),
            str(name),
            (
                str(last_name)
                if last_name is not None
                else None
            ),
            int(last_renamed_at),
        )
        for (
            countdown_id,
            guild_id,
            channel_id,
            target_unix,
            created_at,
            name,
            last_name,
            last_renamed_at,
        ) in rows
    ]


def get_countdown_by_channel(
    guild_id: int,
    channel_id: int,
) -> tuple[int, int, int, int, str, str | None, int] | None:
    """Return one countdown tracked by its Discord channel."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            countdown_id,
            channel_id,
            target_unix,
            created_at,
            name,
            last_name,
            last_renamed_at
        FROM countdowns
        WHERE guild_id = ?
        AND channel_id = ?
        """,
        (
            guild_id,
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
        int(row[2]),
        int(row[3]),
        str(row[4]),
        (
            str(row[5])
            if row[5] is not None
            else None
        ),
        int(row[6]),
    )


def set_countdown_name(
    guild_id: int,
    channel_id: int,
    name: str,
) -> bool:
    """Change the customizable base name for a countdown."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE countdowns
        SET name = ?
        WHERE guild_id = ?
        AND channel_id = ?
        """,
        (
            name,
            guild_id,
            channel_id,
        ),
    )

    updated = cursor.rowcount > 0

    connection.commit()
    connection.close()

    return updated


def update_countdown_runtime(
    channel_id: int,
    last_name: str,
    last_renamed_at: int,
):
    """Save the most recent successful countdown rename."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE countdowns
        SET
            last_name = ?,
            last_renamed_at = ?
        WHERE channel_id = ?
        """,
        (
            last_name,
            last_renamed_at,
            channel_id,
        ),
    )

    connection.commit()
    connection.close()


def delete_countdown_by_channel(
    guild_id: int,
    channel_id: int,
) -> bool:
    """Stop tracking one countdown by its Discord channel."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM countdowns
        WHERE guild_id = ?
        AND channel_id = ?
        """,
        (
            guild_id,
            channel_id,
        ),
    )

    removed = cursor.rowcount > 0

    connection.commit()
    connection.close()

    return removed


# ============================================================
# BUMP REMINDER SETTINGS
# ============================================================

def set_bump_reminder_enabled(
    guild_id: int,
    enabled: bool,
):
    """Enable or disable bump reminders for a guild."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO bump_reminder_settings (
            guild_id,
            enabled
        )
        VALUES (?, ?)
        ON CONFLICT(guild_id)
        DO UPDATE SET
            enabled = excluded.enabled
        """,
        (
            guild_id,
            int(enabled),
        ),
    )

    connection.commit()
    connection.close()


def get_bump_reminder_enabled(
    guild_id: int,
) -> bool:
    """Return whether bump reminders are enabled."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT enabled
        FROM bump_reminder_settings
        WHERE guild_id = ?
        """,
        (
            guild_id,
        ),
    )

    result = cursor.fetchone()

    connection.close()

    if result is None:
        return False

    return bool(
        result[0]
    )


# ============================================================
# PENDING BUMP REMINDERS
# ============================================================

def save_bump_reminder(
    guild_id: int,
    channel_id: int,
    user_id: int,
    due_at: int,
):
    """Save or replace a pending bump reminder."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO bump_reminders (
            guild_id,
            channel_id,
            user_id,
            due_at
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id)
        DO UPDATE SET
            channel_id = excluded.channel_id,
            user_id = excluded.user_id,
            due_at = excluded.due_at
        """,
        (
            guild_id,
            channel_id,
            user_id,
            due_at,
        ),
    )

    connection.commit()
    connection.close()


def get_due_bump_reminders(
    current_time: int,
) -> list[tuple[int, int, int, int]]:
    """Return all bump reminders whose cooldown has expired."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            guild_id,
            channel_id,
            user_id,
            due_at
        FROM bump_reminders
        WHERE due_at <= ?
        """,
        (
            current_time,
        ),
    )

    results = cursor.fetchall()

    connection.close()

    return [
        (
            int(guild_id),
            int(channel_id),
            int(user_id),
            int(due_at),
        )
        for (
            guild_id,
            channel_id,
            user_id,
            due_at,
        ) in results
    ]


def delete_bump_reminder(
    guild_id: int,
):
    """Delete a pending bump reminder."""

    connection = sqlite3.connect(
        ROLE_DB_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM bump_reminders
        WHERE guild_id = ?
        """,
        (
            guild_id,
        ),
    )

    connection.commit()
    connection.close()