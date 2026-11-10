# WARDOGS Discord Bot by Ares Group

**An open-source Discord bot for WARDOGS communities and community servers**, built and run by [Ares Group [ARES]](https://ares-group.org/), a North American WARDOGS PMC and LFG community.

It powers the [Ares Group Discord server](https://discord.gg/Y38j3uGEHB) and our 24/7 NA East 50v50 infantry-only WARDOGS community server. Live WARDOGS server status, player stats and leaderboards from [WarCon](https://github.com/warcon-app/warcon), a 50v50 team auto balancer, support tickets, LFG voice channels and moderation logs, all in one Python bot.

> 🎮 **Play with us:** search **Red V Green 50v50 By Ares Group** in the WARDOGS community server browser, or **Join by ID** `54a956d6-4c12-4eb2-8f8f-d6e17c3275f0`.
> Rules, live match details and support: **[ares-group.org](https://ares-group.org/)**

## Features

### WARDOGS server tools
- **Live server status embed.** A Discord embed that keeps itself updated with the map, mode, player count and team scores for your WARDOGS community server, read from WarCon (`/putserverembed`).
- **Player stats and leaderboards.** Tracks kills, cash and session stats for members on your community server via WarCon's event feed (`/profile`, `/leaderboard`, `/stattracking`).
- **50v50 auto balancer.** Keeps two teams even over RCON during a match, with a small web control panel (see [`balancer/`](balancer/)).
- **Launch countdown.** A channel-name countdown to a launch or season start (`/countdown`).

### Community and LFG tools
- **Join-to-create voice channels** for squads and LFG, with lock, rename, limit and group commands.
- **Support tickets** (`/ticketsetup`) with private staff channels.
- **Role panels** so members can pick their own roles.
- **Member roster** (`/roster`).
- **Welcome messages** with a custom background image.
- **Moderation logs:** deleted messages, moderator actions and member leaves.
- **Announcements, Disboard bump reminders** and a custom command prefix.

## Requirements
- Python 3.13 (or Docker)
- A Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications)) with the **Server Members** and **Message Content** intents
- Optional: a [WarCon](https://github.com/warcon-app/warcon) panel for server status and stats, and RCON access to your WARDOGS server for the balancer

## Quick start (Docker)
```sh
git clone https://github.com/Ares-Group-WARDOGS/wardogs-discord-bot.git
cd wardogs-discord-bot
cp .env.example .env        # fill in DISCORD_TOKEN, DISCORD_GUILD_ID, and the WarCon values
docker compose up -d --build
docker logs -f ares-bot
```

## Quick start (Python)
```sh
python -m venv .venv
. .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export $(cat .env | xargs)  # or set the variables another way
python bot.py
```

## Configuration
| Variable | What it is |
|---|---|
| `DISCORD_TOKEN` | Your bot token |
| `DISCORD_GUILD_ID` | The Discord server the bot runs in |
| `GENERAL_CHANNEL_ID` | Default welcome channel (optional; also set with `/setwelcomechannel`) |
| `WARCON_BASE_URL` | Your WarCon panel's address |
| `WARCON_API_KEY` | A WarCon API key with read access to your server |
| `WARCON_SERVER_ID` | Your server's id in WarCon |

The bot stores its data in SQLite under `data/`.

## What's not included
Ares Group links Steam accounts through Steam sign-in on [ares-group.org](https://ares-group.org/) and runs its priority queue there. That part depends on our website, so it isn't in this copy. `/track` and `/untrack` reply that linking isn't set up.

## About Ares Group
[Ares Group [ARES]](https://ares-group.org/about) is a player-run North American WARDOGS PMC: organized squads and coordinated play without strict milsim rules. We run the **Red V Green 50v50 By Ares Group | MAX CASH & XP** community server (NA East, King of the Hill, infantry only, active admins).

- Website: https://ares-group.org
- Discord server: https://discord.gg/Y38j3uGEHB
- Community server rules: https://ares-group.org/rules

Ares Group is a fan community and is not affiliated with BULKHEAD or Team17, the developer and publisher of WARDOGS.

## License
[MIT](LICENSE). Use it, fork it, run it on your own WARDOGS community server. Credit is appreciated.
