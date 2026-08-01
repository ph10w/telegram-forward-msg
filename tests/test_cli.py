import unittest
from datetime import timedelta

from telegram_voice_forwarder.cli import parse_command
from telegram_voice_forwarder.config import ConfigError


class CliTests(unittest.TestCase):
    def test_parses_period_reset_commands(self) -> None:
        self.assertEqual(parse_command("reset=1W"), ("reset", timedelta(weeks=1)))
        self.assertEqual(parse_command("reset=7d"), ("reset", timedelta(days=7)))
        self.assertEqual(parse_command("reset=24H"), ("reset", timedelta(hours=24)))

    def test_keeps_full_reset_command(self) -> None:
        self.assertEqual(parse_command("reset"), ("reset", None))

    def test_rejects_invalid_period_reset_commands(self) -> None:
        for value in ("reset=", "reset=0W", "reset=1M", "reset=1.5W"):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                parse_command(value)


if __name__ == "__main__":
    unittest.main()
