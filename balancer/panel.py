#!/usr/bin/env python3
"""WARDOGS Game Mode panel.

Runs the 50v50 auto balancer in a background thread and serves a small
web page for turning it on/off, setting the RCON connection and editing
the in-game messages. Standard library only, so it runs on a bare
python3 install.

The panel password is optional. With none set the page is open, which is
fine on a LAN or Tailscale. Set, change or remove it from the page; it is
stored as a salted PBKDF2 hash in config.json. If you get locked out:

    python3 panel.py reset-password

Environment variables (all optional):
    WARDOGS_PANEL_AUTH      "site" = ares-group.org signs you in (balance.ares-group.org, owner only);
                            the panel's own password is switched off and the panel only listens on
                            127.0.0.1, so the site's gate is the only way in. Default: the panel password.
    WARDOGS_PANEL_HOST      bind address (default 0.0.0.0)
    WARDOGS_PANEL_PORT      port (default 8080)
    WARDOGS_BALANCE_DIR     where config.json / state.json live
"""

import collections
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import balance

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(HERE, "static", "index.html")

PANEL_HOST = os.environ.get("WARDOGS_PANEL_HOST", "0.0.0.0")
SITE_AUTH = os.environ.get("WARDOGS_PANEL_AUTH", "").strip().lower() == "site"
if SITE_AUTH and PANEL_HOST not in ("127.0.0.1", "localhost", "::1"):
    # Without its own password the panel must never be reachable except through the site.
    print(f"WARDOGS_PANEL_AUTH=site: ignoring WARDOGS_PANEL_HOST={PANEL_HOST}, listening on 127.0.0.1 only", flush=True)
    PANEL_HOST = "127.0.0.1"
PANEL_PORT = int(os.environ.get("WARDOGS_PANEL_PORT", "8080"))
MAX_BODY = 64 * 1024

SESSION_COOKIE = "wardogs_session"
SESSION_SECONDS = 30 * 24 * 3600
MIN_PASSWORD_LENGTH = 8
PBKDF2_ITERATIONS = 240_000
LOGIN_MAX_FAILURES = 8
LOGIN_LOCKOUT_SECONDS = 300


# --- panel password + sessions ------------------------------------------
def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        algo, iterations, salt, expected = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), expected)


def password_hash():
    return balance.load_config()["panel"]["password_hash"]


def set_panel_password(password):
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > 200:
        raise ValueError("Password is too long")
    config = balance.load_config()
    config["panel"]["password_hash"] = hash_password(password)
    balance.save_config(config)


class Sessions:
    def __init__(self):
        self.lock = threading.Lock()
        self.tokens = {}
        self.failures = {}

    def create(self):
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.tokens[token] = time.time() + SESSION_SECONDS
        return token

    def valid(self, token):
        if not token:
            return False
        with self.lock:
            expires = self.tokens.get(token)
            if expires and expires > time.time():
                return True
            self.tokens.pop(token, None)
            return False

    def drop(self, token):
        with self.lock:
            self.tokens.pop(token, None)

    def drop_all_except(self, keep):
        with self.lock:
            self.tokens = {t: e for t, e in self.tokens.items() if t == keep}

    def locked_out(self, ip):
        with self.lock:
            count, until = self.failures.get(ip, (0, 0))
            return count >= LOGIN_MAX_FAILURES and until > time.time()

    def record_failure(self, ip):
        with self.lock:
            count, until = self.failures.get(ip, (0, 0))
            if until < time.time():
                count = 0
            self.failures[ip] = (count + 1, time.time() + LOGIN_LOCKOUT_SECONDS)

    def clear_failures(self, ip):
        with self.lock:
            self.failures.pop(ip, None)


sessions = Sessions()


class Runtime:
    """Shared state between the balancer thread and the web handlers."""

    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.state = balance.load_state()
        self.logs = collections.deque(maxlen=300)
        self.last_tick = None
        self.last_error = None
        self.connected = False
        self.started_at = time.time()

    def add_log(self, line, error):
        self.logs.append({"line": line, "error": bool(error)})


runtime = Runtime()
balance._log_hooks.append(runtime.add_log)


def rcon_configured(config):
    rcon = config["rcon"]
    return bool(rcon.get("host") and rcon.get("port") and rcon.get("password"))


def balancer_loop():
    last_mode = None

    while True:
        config = balance.load_config()
        mode = balance.mode_from_config(config)

        if mode != last_mode:
            balance.log(f"[MODE] {last_mode or 'startup'} -> {mode}")
            last_mode = mode

        if rcon_configured(config):
            with runtime.lock:
                error = balance.safe_tick(runtime.state, config)
                runtime.last_tick = time.time()
                runtime.last_error = error
                runtime.connected = error is None
        else:
            with runtime.lock:
                runtime.connected = False
                runtime.last_error = "RCON connection not configured"

        runtime.wake.wait(balance.POLL_SECONDS)
        runtime.wake.clear()


def public_config(config):
    rcon = config["rcon"]
    return {
        "enabled": config["enabled"],
        "dry_run": config["dry_run"],
        "grace_seconds": config["grace_seconds"],
        "rcon": {
            "host": rcon.get("host", ""),
            "port": rcon.get("port", ""),
            "password_set": bool(rcon.get("password")),
        },
        "messages": config["messages"],
        "default_messages": balance.DEFAULT_MESSAGES,
    }


def status_payload():
    config = balance.load_config()
    now = time.time()

    with runtime.lock:
        players = []
        counts = {balance.RED: 0, balance.GREEN: 0, balance.BLUE: 0, "other": 0}
        pending = runtime.state.get("pending", {})

        for steam_id, info in runtime.state.get("players", {}).items():
            faction = info.get("faction", "Unknown")
            counts[faction if faction in counts else "other"] += 1
            timer = pending.get(steam_id)
            players.append({
                "steamId": steam_id,
                "name": info.get("name", "Unknown"),
                "faction": faction,
                "pending_seconds": (
                    max(0, round(float(timer.get("deadline", 0)) - now))
                    if timer else None
                ),
                "pending_target": timer.get("target_team") if timer else None,
            })

        players.sort(key=lambda p: (p["faction"], p["name"].lower()))

        return {
            "mode": balance.mode_from_config(config),
            "enabled": config["enabled"],
            "dry_run": config["dry_run"],
            "connected": runtime.connected,
            "configured": rcon_configured(config),
            "last_error": runtime.last_error,
            "last_tick": runtime.last_tick,
            "counts": {
                "red": counts[balance.RED],
                "green": counts[balance.GREEN],
                "blue": counts[balance.BLUE],
                "other": counts["other"],
            },
            "players": players,
            "logs": list(runtime.logs)[-120:],
        }


def apply_config_update(body):
    config = balance.load_config()

    if "enabled" in body:
        config["enabled"] = bool(body["enabled"])
    if "dry_run" in body:
        config["dry_run"] = bool(body["dry_run"])
    if "grace_seconds" in body:
        config["grace_seconds"] = body["grace_seconds"]

    rcon = body.get("rcon")
    if isinstance(rcon, dict):
        if "host" in rcon:
            config["rcon"]["host"] = str(rcon["host"]).strip()
        if "port" in rcon:
            port = str(rcon["port"]).strip()
            if port and not (port.isdigit() and 0 < int(port) < 65536):
                raise ValueError("Port must be a number between 1 and 65535")
            config["rcon"]["port"] = port
        # Blank password means "keep the current one".
        if rcon.get("password"):
            config["rcon"]["password"] = str(rcon["password"]).strip()

    messages = body.get("messages")
    if isinstance(messages, dict):
        for key in balance.DEFAULT_MESSAGES:
            if key in messages:
                text = str(messages[key]).strip()
                if len(text) > 500:
                    raise ValueError(f"Message '{key}' is too long (500 max)")
                config["messages"][key] = text or balance.DEFAULT_MESSAGES[key]

    before = balance.load_config()
    saved = balance.save_config(config)

    if saved["enabled"] and not before["enabled"]:
        # Fresh tracking when the mode is turned on (clears old timers).
        with runtime.lock:
            runtime.state = balance.default_state()
            balance.save_state(runtime.state)

    if before["enabled"] != saved["enabled"] or before["dry_run"] != saved["dry_run"]:
        balance.log(f"[PANEL] Game mode set to {balance.mode_from_config(saved)}")
    if before["rcon"] != saved["rcon"]:
        balance.log(
            f"[PANEL] RCON set to {saved['rcon']['host']}:{saved['rcon']['port']}"
        )
    if before["messages"] != saved["messages"]:
        balance.log("[PANEL] Messages updated")
    if before["grace_seconds"] != saved["grace_seconds"]:
        balance.log(f"[PANEL] Grace period set to {saved['grace_seconds']}s")

    runtime.wake.set()
    return saved


def test_connection(body):
    config = balance.load_config()
    rcon = dict(config["rcon"])

    for key in ("host", "port"):
        if body.get(key):
            rcon[key] = str(body[key]).strip()
    if body.get("password"):
        rcon["password"] = str(body["password"]).strip()

    started = time.time()
    try:
        players = balance.get_players(rcon)
    except Exception as error:
        reason = getattr(error, "reason", None) or getattr(error, "code", None) or error
        return {"ok": False, "error": str(reason)}

    red, green, blue = balance.faction_counts(players)
    return {
        "ok": True,
        "ms": round((time.time() - started) * 1000),
        "players": len(players),
        "counts": {"red": red, "green": green, "blue": blue},
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "WardogsPanel/1.0"

    def log_message(self, fmt, *args):
        pass  # keep the service log about balancing, not HTTP noise

    # --- helpers -------------------------------------------------------
    def session_token(self):
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE:
                return value
        return None

    def authorized(self):
        # Behind ares-group.org (owner-only gate, localhost-only bind): the site already signed you in.
        if SITE_AUTH:
            return True
        # No password set = open panel (meant for LAN / Tailscale use).
        return not password_hash() or sessions.valid(self.session_token())

    def set_session_cookie(self, token, max_age=SESSION_SECONDS):
        self.extra_headers.append((
            "Set-Cookie",
            f"{SESSION_COOKIE}={token}; Path=/; Max-Age={max_age}; "
            "HttpOnly; SameSite=Strict",
        ))

    def send_json(self, payload, status=200):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        for name, value in self.extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise ValueError("Expected application/json")

        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("Request too large")

        raw = self.rfile.read(length) if length else b"{}"
        body = json.loads(raw or b"{}")
        if not isinstance(body, dict):
            raise ValueError("Expected a JSON object")
        return body

    def guard(self):
        if self.authorized():
            return True
        self.send_json({"error": "Login required", "login": True}, 401)
        return False

    # --- routes --------------------------------------------------------
    def do_GET(self):
        self.extra_headers = []

        if self.path == "/healthz":
            return self.send_json({"ok": True})

        if self.path == "/api/auth":
            return self.send_json({
                "password_set": bool(password_hash()) and not SITE_AUTH,
                "logged_in": self.authorized(),
                "site_auth": SITE_AUTH,
            })

        if self.path in ("/", "/index.html"):
            with open(INDEX_FILE, "rb") as file:
                data = file.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif not self.guard():
            return
        elif self.path == "/api/status":
            self.send_json(status_payload())
        elif self.path == "/api/config":
            self.send_json(public_config(balance.load_config()))
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        self.extra_headers = []

        try:
            body = self.read_json()

            if self.path in ("/api/login", "/api/logout"):
                return self.handle_auth(body)

            if not self.guard():
                return

            if self.path == "/api/password" and SITE_AUTH:
                raise ValueError("Sign-in is handled by ares-group.org")
            if self.path == "/api/password":
                had_password = bool(password_hash())
                if had_password and not verify_password(
                    str(body.get("current", "")), password_hash()
                ):
                    raise ValueError("Current password is wrong")
                new = str(body.get("new", ""))
                if new != str(body.get("confirm", new)):
                    raise ValueError("New passwords don't match")

                if new:
                    set_panel_password(new)
                    token = self.session_token()
                    if not sessions.valid(token):
                        token = sessions.create()
                        self.set_session_cookie(token)
                    sessions.drop_all_except(token)
                    balance.log(
                        "[PANEL] Panel password "
                        + ("changed" if had_password else "set")
                    )
                else:
                    config = balance.load_config()
                    config["panel"]["password_hash"] = ""
                    balance.save_config(config)
                    sessions.drop_all_except(None)
                    balance.log("[PANEL] Panel password removed; panel is open")

                self.send_json({"ok": True, "password_set": bool(new)})
            elif self.path == "/api/config":
                saved = apply_config_update(body)
                self.send_json(public_config(saved))
            elif self.path == "/api/test":
                self.send_json(test_connection(body))
            else:
                self.send_json({"error": "Not found"}, 404)
        except ValueError as error:
            self.send_json({"error": str(error)}, 400)
        except Exception as error:
            balance.log(f"[PANEL ERROR] {error}", error=True)
            self.send_json({"error": "Server error"}, 500)

    def handle_auth(self, body):
        ip = self.client_address[0]

        if self.path == "/api/logout":
            sessions.drop(self.session_token())
            self.set_session_cookie("", max_age=0)
            return self.send_json({"ok": True})

        if self.path == "/api/login":
            if not password_hash():
                return self.send_json({"ok": True})
            if sessions.locked_out(ip):
                return self.send_json(
                    {"error": "Too many attempts. Try again in a few minutes."}, 429
                )
            if not verify_password(str(body.get("password", "")), password_hash()):
                sessions.record_failure(ip)
                time.sleep(1)
                balance.log(f"[PANEL] Failed login from {ip}", error=True)
                return self.send_json({"error": "Wrong password"}, 401)
            sessions.clear_failures(ip)
        else:
            return self.send_json({"error": "Not found"}, 404)

        self.set_session_cookie(sessions.create())
        self.send_json({"ok": True})


def reset_password_cli():
    first = getpass.getpass("New panel password: ")
    if first != getpass.getpass("Again: "):
        print("Passwords don't match.")
        return 1
    try:
        set_panel_password(first)
    except ValueError as error:
        print(error)
        return 1
    print("Panel password updated. Restart the service to sign out old sessions.")
    return 0


def main():
    if sys.argv[1:] == ["reset-password"]:
        return reset_password_cli()

    config = balance.load_config()
    balance.save_config(config)  # write config.json on first run (migrates env/mode)

    threading.Thread(target=balancer_loop, daemon=True).start()

    server = ThreadingHTTPServer((PANEL_HOST, PANEL_PORT), Handler)
    balance.log(f"[PANEL] Listening on http://{PANEL_HOST}:{PANEL_PORT}")
    if not password_hash():
        balance.log("[PANEL] No panel password set; page is open (set one from the page)")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
