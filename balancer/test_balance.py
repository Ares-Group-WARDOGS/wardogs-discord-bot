import os
import tempfile
import unittest

_tmp = tempfile.mkdtemp()
os.environ["WARDOGS_BALANCE_DIR"] = _tmp
for _name in ("WARDOGS_RCON_HOST", "WARDOGS_RCON_PORT", "WARDOGS_RCON_PASSWORD"):
    os.environ.pop(_name, None)

import balance

def p(sid, faction):
    return {"steamId": sid, "name": sid, "faction": faction}

class Tests(unittest.TestCase):
    def test_difference_one_allowed(self):
        players = [p("1", balance.RED), p("2", balance.RED), p("3", balance.GREEN)]
        self.assertEqual((None, None, 0), balance.imbalance_info(players))

    def test_difference_two_needs_one(self):
        players = [p("1", balance.RED), p("2", balance.RED), p("3", balance.RED), p("4", balance.GREEN)]
        self.assertEqual(
            (balance.RED, balance.GREEN, 1),
            balance.imbalance_info(players),
        )

    def test_newest_selected_first(self):
        players = [p("old", balance.RED), p("new", balance.RED), p("g", balance.GREEN)]
        state = {"players": {
            "old": {"joined_at": 1, "eligible": True},
            "new": {"joined_at": 2, "eligible": True},
            "g": {"joined_at": 1, "eligible": True},
        }}
        self.assertEqual(
            ["new"],
            balance.required_candidate_ids(players, state, balance.RED, 1),
        )

    def test_existing_players_can_be_moved(self):
        players = [p("old", balance.RED), p("new", balance.RED), p("g", balance.GREEN)]
        state = {"players": {
            "old": {"joined_at": 1, "eligible": False},
            "new": {"joined_at": 2, "eligible": False},
            "g": {"joined_at": 1, "eligible": False},
        }}
        self.assertEqual(
            ["new", "old"],
            balance.required_candidate_ids(players, state, balance.RED, 2),
        )

class ConfigTests(unittest.TestCase):
    def setUp(self):
        for name in ("config.json", "mode"):
            try:
                os.remove(os.path.join(_tmp, name))
            except FileNotFoundError:
                pass

    def test_render_message(self):
        text = balance.render_message(
            "Hi {name}, go to {target} in {seconds}s {unknown}",
            name="Rook", target="GREEN", seconds=30,
        )
        self.assertEqual("Hi Rook, go to GREEN in 30s {unknown}", text)

    def test_defaults_are_original_messages(self):
        config = balance.load_config()
        self.assertEqual(balance.DEFAULT_MESSAGES, config["messages"])
        self.assertEqual("normal", balance.mode_from_config(config))

    def test_legacy_mode_file_migrates(self):
        with open(os.path.join(_tmp, "mode"), "w") as f:
            f.write("dry-run\n")
        self.assertEqual("dry-run", balance.read_mode())

    def test_blank_message_falls_back_and_grace_clamped(self):
        config = balance.load_config()
        config["messages"]["warn"] = "   "
        config["grace_seconds"] = 9999
        saved = balance.save_config(config)
        self.assertEqual(balance.DEFAULT_MESSAGES["warn"], saved["messages"]["warn"])
        self.assertEqual(600, saved["grace_seconds"])

    def test_cli_mode_roundtrip(self):
        balance.write_mode("50v50")
        self.assertEqual("50v50", balance.read_mode())
        balance.write_mode("normal")
        self.assertEqual("normal", balance.read_mode())


if __name__ == "__main__":
    unittest.main()
