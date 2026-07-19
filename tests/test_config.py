import os
import unittest
from pathlib import Path
from unittest.mock import patch

from telegram_voice_forwarder.config import ConfigError, ForwarderConfig, parse_chat_ref


class ConfigTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
