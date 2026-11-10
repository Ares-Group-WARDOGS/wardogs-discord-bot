#!/usr/bin/env python3

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

BASE_DIR = os.environ.get(
    "WARDOGS_BALANCE_DIR",
    os.path.dirname(os.path.abspath(__file__)),
)

CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
LEGACY_MODE_FILE = os.path.join(BASE_DIR, "mode")
STATE_FILE = os.path.join(BASE_DIR, "state.json")

RED = "Valkyra"
BLUE = "Lonestar"
GREEN = "Manticore"
WAITING = "White"

VALID_MODES = {"normal", "dry-run", "50v50"}

POLL_SECONDS = 2
DEFAULT_GRACE_SECONDS = 30
MAX_TEAM_DIFFERENCE = 1
MAX_MOVES_PER_TICK = 5

TEAM_LABELS = {
    RED: "RED (Valkyra)",
    GREEN: "GREEN (Manticore)",
}

# Message templates. Placeholders: {name} {target} {seconds}
DEFAULT_MESSAGES = {
    "warn": (
        "Teams are unbalanced. Please switch to {target} within "
        "{seconds} seconds or Auto Balance may move you."
    ),
    "moved": (
        "Auto Balance moved you to {target} to keep the teams even. "
        "Thanks for helping keep the match balanced."
    ),
    "assigned": (
        "Auto Balance assigned you to {target} because this server "
        "is running 50v50."
    ),
}

MESSAGE_PLACEHOLDERS = ("name", "target", "seconds")

_config_lock = threading.Lock()
_log_hooks = []


def log(message, error=False):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + message
    print(line, file=sys.stderr if error else sys.stdout, flush=True)

    for hook in list(_log_hooks):
        try:
            hook(line, error)
        except Exception:
            pass


def default_config():
    return {
        "enabled": False,
        "dry_run": False,
        "grace_seconds": DEFAULT_GRACE_SECONDS,
        "rcon": {
            "host": os.environ.get("WARDOGS_RCON_HOST", "").strip(),
            "port": os.environ.get("WARDOGS_RCON_PORT", "").strip(),
            "password": os.environ.get("WARDOGS_RCON_PASSWORD", "").strip(),
        },
        "messages": dict(DEFAULT_MESSAGES),
        "panel": {"password_hash": ""},
    }


def _legacy_mode():
    try:
        with open(LEGACY_MODE_FILE, "r", encoding="utf-8") as file:
            mode = file.read().strip()
    except FileNotFoundError:
        return None

    return mode if mode in VALID_MODES else None


def _normalize_config(raw):
    config = default_config()

    if not isinstance(raw, dict):
        return config

    config["enabled"] = bool(raw.get("enabled", config["enabled"]))
    config["dry_run"] = bool(raw.get("dry_run", config["dry_run"]))

    try:
        grace = int(raw.get("grace_seconds", DEFAULT_GRACE_SECONDS))
    except (TypeError, ValueError):
        grace = DEFAULT_GRACE_SECONDS
    config["grace_seconds"] = max(5, min(grace, 600))

    rcon = raw.get("rcon")
    if isinstance(rcon, dict):
        for key in ("host", "port", "password"):
            value = rcon.get(key)
            if value is not None and str(value).strip():
                config["rcon"][key] = str(value).strip()

    messages = raw.get("messages")
    if isinstance(messages, dict):
        for key in DEFAULT_MESSAGES:
            value = messages.get(key)
            if isinstance(value, str) and value.strip():
                config["messages"][key] = value.strip()

    panel = raw.get("panel")
    if isinstance(panel, dict) and isinstance(panel.get("password_hash"), str):
        config["panel"]["password_hash"] = panel["password_hash"]

    return config


def load_config():
    with _config_lock:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as file:
                raw = json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            raw = None

        config = _normalize_config(raw)

        if raw is None:
            # First run: carry over the old mode file, if any.
            legacy = _legacy_mode()
            if legacy == "50v50":
                config["enabled"] = True
            elif legacy == "dry-run":
                config["enabled"] = True
                config["dry_run"] = True

        return config


def save_config(config):
    config = _normalize_config(config)

    with _config_lock:
        os.makedirs(BASE_DIR, exist_ok=True)
        temp = CONFIG_FILE + ".tmp"

        with open(temp, "w", encoding="utf-8") as file:
            json.dump(config, file, indent=2, sort_keys=True)

        try:
            os.chmod(temp, 0o600)  # holds the RCON password
        except OSError:
            pass

        os.replace(temp, CONFIG_FILE)

    return config


def mode_from_config(config):
    if not config.get("enabled"):
        return "normal"

    return "dry-run" if config.get("dry_run") else "50v50"


def read_mode():
    return mode_from_config(load_config())


def write_mode(mode):
    if mode not in VALID_MODES:
        raise ValueError("Invalid mode")

    config = load_config()
    config["enabled"] = mode != "normal"
    config["dry_run"] = mode == "dry-run"
    save_config(config)


def render_message(template, **values):
    text = template
    for key in MESSAGE_PLACEHOLDERS:
        text = text.replace("{" + key + "}", str(values.get(key, "")))
    return text


def grace_seconds():
    return load_config()["grace_seconds"]


def default_state():
    return {
        "initialized": False,
        "players": {},
        "pending": {},
        "next_equal_team": RED,
    }


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return default_state()

    if not isinstance(state, dict):
        return default_state()

    if not isinstance(state.get("players"), dict):
        state["players"] = {}

    if not isinstance(state.get("pending"), dict):
        state["pending"] = {}

    if state.get("next_equal_team") not in {RED, GREEN}:
        state["next_equal_team"] = RED

    state["initialized"] = bool(state.get("initialized", False))

    return state


def save_state(state):
    os.makedirs(BASE_DIR, exist_ok=True)
    temp = STATE_FILE + ".tmp"

    with open(temp, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2, sort_keys=True)

    os.replace(temp, STATE_FILE)


def request(method, path, body=None, rcon=None):
    rcon = rcon or load_config()["rcon"]
    host = rcon.get("host", "")
    port = rcon.get("port", "")
    password = rcon.get("password", "")

    if not host or not port or not password:
        raise RuntimeError(
            "RCON host, port and password must be set in the panel"
        )

    url = f"http://{host}:{port}{path}"

    data = None
    headers = {
        "Authorization": f"Bearer {password}",
        "Accept": "application/json",
    }

    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )

    with urllib.request.urlopen(req, timeout=5) as response:
        raw = response.read()

    if not raw:
        return {}

    return json.loads(raw.decode("utf-8"))


def get_players(rcon=None):
    data = request("GET", "/v1/players", rcon=rcon)

    if isinstance(data, list):
        return data

    if isinstance(data, dict) and isinstance(data.get("players"), list):
        return data["players"]

    raise RuntimeError("Unexpected /v1/players response")


def player_by_id(players, steam_id):
    for player in players:
        if str(player.get("steamId", "")) == steam_id:
            return player

    return None


def faction_counts(players):
    red = 0
    green = 0
    blue = 0

    for player in players:
        faction = player.get("faction")

        if faction == RED:
            red += 1
        elif faction == GREEN:
            green += 1
        elif faction == BLUE:
            blue += 1

    return red, green, blue


def imbalance_info(players):
    red, green, _ = faction_counts(players)
    difference = red - green

    if abs(difference) <= MAX_TEAM_DIFFERENCE:
        return None, None, 0

    if difference > 0:
        overloaded = RED
        target = GREEN
    else:
        overloaded = GREEN
        target = RED

    return overloaded, target, abs(difference) // 2


def choose_smaller_team(players, state, exclude_steam_id=None):
    red = 0
    green = 0

    for player in players:
        steam_id = str(player.get("steamId", ""))

        if exclude_steam_id and steam_id == exclude_steam_id:
            continue

        faction = player.get("faction")

        if faction == RED:
            red += 1
        elif faction == GREEN:
            green += 1

    if red < green:
        return RED

    if green < red:
        return GREEN

    target = state.get("next_equal_team", RED)

    if target not in {RED, GREEN}:
        target = RED

    state["next_equal_team"] = GREEN if target == RED else RED
    return target


def move_player(player, target):
    steam_id = str(player.get("steamId", ""))

    if not steam_id:
        raise RuntimeError("Player has no SteamID")

    return request(
        "PATCH",
        f"/v1/players/{steam_id}",
        {"faction": target},
    )


def whisper_player(player, message):
    steam_id = str(player.get("steamId", ""))

    if not steam_id:
        raise RuntimeError("Player has no SteamID")

    return request(
        "POST",
        f"/v1/players/{steam_id}/message",
        {"message": message},
    )


def safe_whisper(player, message):
    try:
        whisper_player(player, message)
        return True
    except Exception as error:
        log(
            f"[WHISPER ERROR] {player.get('name', 'Unknown')} | {error}",
            error=True,
        )
        return False


def player_snapshot(players, previous=None, now=None):
    previous = previous or {}
    now = time.time() if now is None else now
    result = {}

    for player in players:
        steam_id = str(player.get("steamId", ""))

        if not steam_id:
            continue

        old = previous.get(steam_id, {})

        result[steam_id] = {
            "name": player.get("name", "Unknown"),
            "faction": player.get("faction", "Unknown"),
            "joined_at": old.get("joined_at", now),
            "eligible": bool(old.get("eligible", True)),
        }

    return result


def baseline_snapshot(players):
    now = time.time()
    result = {}

    for player in players:
        steam_id = str(player.get("steamId", ""))

        if not steam_id:
            continue

        result[steam_id] = {
            "name": player.get("name", "Unknown"),
            "faction": player.get("faction", "Unknown"),
            "joined_at": now,
            "eligible": True,
        }

    return result


def team_label(team):
    return TEAM_LABELS.get(team, team)


def required_candidate_ids(players, state, overloaded, moves_needed):
    candidates = []

    for player in players:
        steam_id = str(player.get("steamId", ""))

        if not steam_id or player.get("faction") != overloaded:
            continue

        info = state.get("players", {}).get(steam_id, {})

        candidates.append(
            (
                float(info.get("joined_at", 0) or 0),
                steam_id,
            )
        )

    candidates.sort(reverse=True)

    return [
        steam_id
        for _, steam_id in candidates[:moves_needed]
    ]


def clear_irrelevant_pending(state, desired_ids):
    pending = state.setdefault("pending", {})

    for steam_id in list(pending):
        if steam_id not in desired_ids:
            pending.pop(steam_id, None)


def warn_candidates(players, state, desired_ids, overloaded, target, now, mode, config):
    messages = config["messages"]
    grace = config["grace_seconds"]
    pending = state.setdefault("pending", {})

    for steam_id in desired_ids:
        if steam_id in pending:
            continue

        player = player_by_id(players, steam_id)

        if player is None:
            continue

        pending[steam_id] = {
            "started_at": now,
            "deadline": now + grace,
            "from_team": overloaded,
            "target_team": target,
        }

        name = player.get("name", "Unknown")

        log(
            f"[WARN] {name} ({steam_id}) | "
            f"switch to {team_label(target)} within "
            f"{grace}s",
        )

        if mode == "50v50":
            safe_whisper(
                player,
                render_message(
                    messages["warn"],
                    name=name,
                    target=team_label(target),
                    seconds=grace,
                ),
            )


def execute_expired_moves(players, state, desired_ids, now, mode, config):
    messages = config["messages"]
    grace = config["grace_seconds"]
    moves = 0

    for steam_id in desired_ids:
        if moves >= MAX_MOVES_PER_TICK:
            break

        pending = state.setdefault("pending", {})
        timer = pending.get(steam_id)

        if not timer:
            continue

        if now < float(timer.get("deadline", 0) or 0):
            continue

        overloaded, target, moves_needed = imbalance_info(players)

        if overloaded is None or target is None or moves_needed <= 0:
            break

        player = player_by_id(players, steam_id)

        if player is None:
            pending.pop(steam_id, None)
            continue

        if player.get("faction") != overloaded:
            pending.pop(steam_id, None)
            continue

        name = player.get("name", "Unknown")

        if mode == "dry-run":
            log(
                f"[WOULD MOVE] {name} ({steam_id}) "
                f"{overloaded} -> {target} after grace period",
            )
            pending.pop(steam_id, None)
            player["faction"] = target
            moves += 1
            continue

        if mode != "50v50":
            pending.pop(steam_id, None)
            continue

        log(
            f"[MOVE] {name} ({steam_id}) "
            f"{overloaded} -> {target} after grace period",
        )

        move_player(player, target)

        safe_whisper(
            player,
            render_message(
                messages["moved"],
                name=name,
                target=team_label(target),
                seconds=grace,
            ),
        )

        pending.pop(steam_id, None)
        player["faction"] = target

        state["players"].setdefault(steam_id, {})["faction"] = target
        moves += 1

    return moves


def handle_blue_player(player, players, state, mode, config):
    messages = config["messages"]
    steam_id = str(player.get("steamId", ""))
    name = player.get("name", "Unknown")

    target = choose_smaller_team(
        players,
        state,
        exclude_steam_id=steam_id,
    )

    if mode == "normal":
        log(
            f"[NORMAL] {name} selected Lonestar; no intervention.",
        )
        return

    if mode == "dry-run":
        log(
            f"[WOULD ASSIGN] {name} ({steam_id}) "
            f"{BLUE} -> {target}",
        )
        return

    if mode != "50v50":
        return

    log(
        f"[ASSIGN] {name} ({steam_id}) "
        f"{BLUE} -> {target}",
    )

    move_player(player, target)

    safe_whisper(
        player,
        render_message(
            messages["assigned"],
            name=name,
            target=team_label(target),
        ),
    )

    player["faction"] = target
    state["players"].setdefault(steam_id, {})["faction"] = target
    state.setdefault("pending", {}).pop(steam_id, None)


def process_tick(state, config=None):
    config = config or load_config()
    players = get_players(config["rcon"])
    now = time.time()

    if not state.get("initialized"):
        state["initialized"] = True
        state["players"] = baseline_snapshot(players)
        state["pending"] = {}
        save_state(state)

        red, green, blue = faction_counts(players)

        log(
            f"[BASELINE] Tracking {len(players)} players | "
            f"Valkyra={red} Manticore={green} Lonestar={blue}",
        )
        return

    previous = state.get("players", {})

    if not isinstance(previous, dict):
        state["players"] = baseline_snapshot(players)
        state["pending"] = {}
        save_state(state)
        return

    current = player_snapshot(
        players,
        previous=previous,
        now=now,
    )

    for steam_id, info in current.items():
        if steam_id not in previous:
            info["joined_at"] = now
            info["eligible"] = True

            log(
                f"[JOIN] {info['name']} | "
                f"initial faction={info['faction']}",
            )
            continue

        old_faction = previous[steam_id].get("faction", "Unknown")
        new_faction = info["faction"]

        if old_faction != new_faction:
            log(
                f"[FACTION] {info['name']} | "
                f"{old_faction} -> {new_faction}",
            )

    state["players"] = current

    connected_ids = set(current)

    for steam_id in list(state.setdefault("pending", {})):
        if steam_id not in connected_ids:
            state["pending"].pop(steam_id, None)

    mode = mode_from_config(config)

    if mode == "normal":
        state["pending"] = {}
        save_state(state)
        return

    for player in players:
        steam_id = str(player.get("steamId", ""))

        if not steam_id:
            continue

        info = state["players"].get(steam_id, {})

        if player.get("faction") == BLUE:
            handle_blue_player(
                player,
                players,
                state,
                mode,
                config,
            )

    overloaded, target, moves_needed = imbalance_info(players)

    if overloaded is None or target is None or moves_needed <= 0:
        if state.get("pending"):
            log(
                "[BALANCED] Teams are within one player; "
                "clearing pending timers.",
            )

        state["pending"] = {}
        save_state(state)
        return

    desired_ids = required_candidate_ids(
        players,
        state,
        overloaded,
        moves_needed,
    )

    clear_irrelevant_pending(
        state,
        set(desired_ids),
    )

    warn_candidates(
        players,
        state,
        desired_ids,
        overloaded,
        target,
        now,
        mode,
        config,
    )

    execute_expired_moves(
        players,
        state,
        desired_ids,
        now,
        mode,
        config,
    )

    state["players"] = player_snapshot(
        players,
        previous=state.get("players", {}),
        now=now,
    )

    save_state(state)


def safe_tick(state, config=None):
    """Run one tick, logging errors instead of raising. Returns error text or None."""
    try:
        process_tick(state, config)
        return None
    except urllib.error.HTTPError as error:
        message = f"RCON HTTP {error.code}: {error.reason}"
    except urllib.error.URLError as error:
        message = f"RCON connection: {error.reason}"
    except Exception as error:
        message = str(error)

    log(f"[ERROR] {message}", error=True)
    return message


def run():
    state = load_state()
    config = load_config()

    log(
        f"[START] Mode: {mode_from_config(config)} | "
        f"poll={POLL_SECONDS}s | "
        f"grace={config['grace_seconds']}s",
    )

    while True:
        safe_tick(state)
        time.sleep(POLL_SECONDS)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "command",
        choices=[
            "run",
            "normal",
            "dry-run",
            "50v50",
            "status",
        ],
    )

    args = parser.parse_args()

    if args.command == "status":
        print(read_mode())
        return

    if args.command in VALID_MODES:
        write_mode(args.command)
        print(f"Mode set to: {args.command}")
        return

    run()


if __name__ == "__main__":
    main()
