# WARDOGS 50v50 auto balancer

Keeps two WARDOGS teams even during a match by talking to the game server over RCON, with a small web panel to tune it.

- Runs as the `ares-balancer` container (see `../docker-compose.yml`), listening on `127.0.0.1:8765` only.
- Panel auth: set a panel password (`python3 panel.py reset-password`), or put it behind your own sign-in and set
  `WARDOGS_PANEL_AUTH=site`, which turns the panel password off and forces the localhost bind.
- Settings (RCON host, port, password, messages, on/off) live in `balancer-data/config.json`. Never commit that file.
- Tests: `python3 -m unittest test_balance` (from this folder).
- Command line inside the container: `docker exec ares-balancer python3 balance.py status`.
