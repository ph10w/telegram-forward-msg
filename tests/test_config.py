import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from telegram_voice_forwarder.config import ConfigError, ForwarderConfig, parse_chat_ref


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        dotenv_file = Path.cwd() / ".env"
        self.enterContext(
            patch(
                "telegram_voice_forwarder.config.find_dotenv",
                return_value=str(dotenv_file),
            )
        )
        self.enterContext(patch("telegram_voice_forwarder.config.load_dotenv"))

    def test_parse_numeric_and_named_chat_references(self) -> None:
        self.assertEqual(parse_chat_ref(" -100123 "), -100123)
        self.assertEqual(parse_chat_ref("@example"), "@example")

    def test_loads_forwarder_config(self) -> None:
        environment = {
            "TELEGRAM_API_ID": "123",
            "TELEGRAM_API_HASH": "secret",
            "TELEGRAM_SOURCE_CHATS": "-1001, @source",
            "TELEGRAM_TARGET_CHAT": "-1002",
            "INITIAL_SCAN_LIMIT": "0",
            "MIN_VOICE_DURATION_SECONDS": "2.5",
            "INCLUDE_VIDEO_NOTES": "yes",
            "TELETHON_ENTITY_CACHE_LIMIT": "250",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = ForwarderConfig.from_env()

        self.assertEqual(config.api_id, 123)
        self.assertEqual(config.source_chats, (-1001, "@source"))
        self.assertEqual(config.target_chat, -1002)
        self.assertEqual(config.initial_scan_limit, 0)
        self.assertEqual(config.min_voice_duration_seconds, 2.5)
        self.assertTrue(config.include_video_notes)
        self.assertEqual(config.entity_cache_limit, 250)
        self.assertEqual(config.state_db, Path("data/forwarder.sqlite3"))
        self.assertIsNone(config.notification_bot_token)
        self.assertIsNone(config.notification_chat_id)

    def test_loads_private_notification_bot_settings(self) -> None:
        environment = {
            "TELEGRAM_API_ID": "123",
            "TELEGRAM_API_HASH": "secret",
            "TELEGRAM_SOURCE_CHATS": "-1001",
            "TELEGRAM_TARGET_CHAT": "-1002",
            "TELEGRAM_NOTIFICATION_BOT_TOKEN": "bot-secret",
            "TELEGRAM_NOTIFICATION_CHAT_ID": "123456",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = ForwarderConfig.from_env()

        self.assertEqual(config.notification_bot_token, "bot-secret")
        self.assertEqual(config.notification_chat_id, 123456)
        self.assertNotIn("bot-secret", repr(config))

    def test_rejects_incomplete_private_notification_settings(self) -> None:
        environment = {
            "TELEGRAM_API_ID": "123",
            "TELEGRAM_API_HASH": "secret",
            "TELEGRAM_SOURCE_CHATS": "-1001",
            "TELEGRAM_TARGET_CHAT": "-1002",
            "TELEGRAM_NOTIFICATION_BOT_TOKEN": "bot-secret",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ConfigError, "müssen gemeinsam gesetzt sein"):
                ForwarderConfig.from_env()

    def test_rejects_non_private_notification_chat_id(self) -> None:
        environment = {
            "TELEGRAM_API_ID": "123",
            "TELEGRAM_API_HASH": "secret",
            "TELEGRAM_SOURCE_CHATS": "-1001",
            "TELEGRAM_TARGET_CHAT": "-1002",
            "TELEGRAM_NOTIFICATION_BOT_TOKEN": "bot-secret",
            "TELEGRAM_NOTIFICATION_CHAT_ID": "-1002",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ConfigError, "positive ID"):
                ForwarderConfig.from_env()

    def test_rejects_invalid_boolean(self) -> None:
        environment = {
            "TELEGRAM_API_ID": "123",
            "TELEGRAM_API_HASH": "secret",
            "TELEGRAM_SOURCE_CHATS": "-1001",
            "TELEGRAM_TARGET_CHAT": "-1002",
            "INCLUDE_VIDEO_NOTES": "perhaps",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(ConfigError):
                ForwarderConfig.from_env()

    def test_rejects_negative_minimum_duration(self) -> None:
        environment = {
            "TELEGRAM_API_ID": "123",
            "TELEGRAM_API_HASH": "secret",
            "TELEGRAM_SOURCE_CHATS": "-1001",
            "TELEGRAM_TARGET_CHAT": "-1002",
            "MIN_VOICE_DURATION_SECONDS": "-0.1",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(ConfigError):
                ForwarderConfig.from_env()

    def test_rejects_entity_cache_limit_below_minimum(self) -> None:
        environment = {
            "TELEGRAM_API_ID": "123",
            "TELEGRAM_API_HASH": "secret",
            "TELEGRAM_SOURCE_CHATS": "-1001",
            "TELEGRAM_TARGET_CHAT": "-1002",
            "TELETHON_ENTITY_CACHE_LIMIT": "99",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(ConfigError):
                ForwarderConfig.from_env()

    def test_rejects_relative_runtime_paths_outside_dotenv_directory(self) -> None:
        environment = {
            "TELEGRAM_API_ID": "123",
            "TELEGRAM_API_HASH": "secret",
            "TELEGRAM_SOURCE_CHATS": "-1001",
            "TELEGRAM_TARGET_CHAT": "-1002",
            "TELEGRAM_SESSION": "data/telegram-monitor",
            "STATE_DB": "data/forwarder.sqlite3",
        }
        with TemporaryDirectory() as dotenv_directory:
            dotenv_file = Path(dotenv_directory, ".env")
            with (
                patch.dict(os.environ, environment, clear=True),
                patch(
                    "telegram_voice_forwarder.config.find_dotenv",
                    return_value=str(dotenv_file),
                ),
                patch("telegram_voice_forwarder.config.load_dotenv"),
                patch(
                    "telegram_voice_forwarder.config.Path.cwd",
                    return_value=Path(dotenv_directory, "elsewhere"),
                ),
            ):
                with self.assertRaisesRegex(ConfigError, "TELEGRAM_SESSION ist relativ"):
                    ForwarderConfig.from_env()

    def test_rejects_relative_runtime_paths_without_dotenv(self) -> None:
        environment = {
            "TELEGRAM_API_ID": "123",
            "TELEGRAM_API_HASH": "secret",
            "TELEGRAM_SOURCE_CHATS": "-1001",
            "TELEGRAM_TARGET_CHAT": "-1002",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("telegram_voice_forwarder.config.find_dotenv", return_value=""),
        ):
            with self.assertRaisesRegex(ConfigError, "keine \\.env gefunden"):
                ForwarderConfig.from_env()

    def test_allows_absolute_runtime_paths_outside_dotenv_directory(self) -> None:
        with TemporaryDirectory() as dotenv_directory:
            environment = {
                "TELEGRAM_API_ID": "123",
                "TELEGRAM_API_HASH": "secret",
                "TELEGRAM_SOURCE_CHATS": "-1001",
                "TELEGRAM_TARGET_CHAT": "-1002",
                "TELEGRAM_SESSION": str(Path(dotenv_directory, "telegram-monitor")),
                "STATE_DB": str(Path(dotenv_directory, "forwarder.sqlite3")),
            }
            with (
                patch.dict(os.environ, environment, clear=True),
                patch(
                    "telegram_voice_forwarder.config.find_dotenv",
                    return_value=str(Path(dotenv_directory, ".env")),
                ),
                patch("telegram_voice_forwarder.config.load_dotenv"),
                patch(
                    "telegram_voice_forwarder.config.Path.cwd",
                    return_value=Path(dotenv_directory, "elsewhere"),
                ),
            ):
                config = ForwarderConfig.from_env()

        self.assertTrue(config.session_path.is_absolute())
        self.assertTrue(config.state_db.is_absolute())


if __name__ == "__main__":
    unittest.main()
