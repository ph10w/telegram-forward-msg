import unittest
from pathlib import Path
from unittest.mock import patch

from telegram_voice_forwarder.notification_bot_setup import (
    _matching_private_chat,
    _read_token,
    _updated_env,
)


class NotificationBotSetupTests(unittest.TestCase):
    def test_token_prompt_explains_botfather_creation(self) -> None:
        with (
            patch(
                "telegram_voice_forwarder.notification_bot_setup._configured_token",
                return_value=None,
            ),
            patch(
                "telegram_voice_forwarder.notification_bot_setup.getpass.getpass",
                return_value="secret-token",
            ) as prompt,
        ):
            token = _read_token(Path(".env"))

        self.assertEqual(token, "secret-token")
        prompt_text = prompt.call_args.args[0]
        self.assertIn("@BotFather", prompt_text)
        self.assertIn("/newbot", prompt_text)
        self.assertIn("input is hidden", prompt_text)

    def test_matches_only_the_expected_private_start_message(self) -> None:
        update = {
            "message": {
                "text": "/start unique-code",
                "chat": {"id": 12345, "type": "private"},
            }
        }

        self.assertEqual(_matching_private_chat(update, "unique-code"), 12345)
        self.assertIsNone(_matching_private_chat(update, "different-code"))
        update["message"]["chat"]["type"] = "group"
        self.assertIsNone(_matching_private_chat(update, "unique-code"))

    def test_updates_notification_values_without_changing_other_settings(self) -> None:
        original = (
            "TELEGRAM_API_ID=123\n"
            "TELEGRAM_NOTIFICATION_BOT_TOKEN=old-token\n"
            "LOG_LEVEL=INFO\n"
        )

        updated = _updated_env(
            original,
            {"TELEGRAM_NOTIFICATION_BOT_TOKEN": "new-token"},
            remove=frozenset({"TELEGRAM_NOTIFICATION_CHAT_ID"}),
        )

        self.assertIn("TELEGRAM_API_ID=123\n", updated)
        self.assertIn("LOG_LEVEL=INFO\n", updated)
        self.assertIn("TELEGRAM_NOTIFICATION_BOT_TOKEN=new-token\n", updated)
        self.assertNotIn("TELEGRAM_NOTIFICATION_CHAT_ID=", updated)
        self.assertNotIn("old-token", updated)
        self.assertEqual(updated.count("TELEGRAM_NOTIFICATION_BOT_TOKEN="), 1)


if __name__ == "__main__":
    unittest.main()
