import unittest
from unittest.mock import AsyncMock, Mock, patch

from telegram_voice_forwarder.errors import TelegramBotApiError
from telegram_voice_forwarder.notification_bot_adapter import BotVoiceNotifier


class BotVoiceNotifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_audible_private_notification(self) -> None:
        bot = Mock()
        notifier = BotVoiceNotifier(bot, 123456)

        await notifier.notify_voice("Alice", "https://t.me/c/2/901")

        bot.call.assert_called_once_with(
            "sendMessage",
            chat_id=123456,
            text=(
                "🎙️ Neue Sprachnachricht\n"
                "👤 Autor: Alice\n"
                "🔗 https://t.me/c/2/901"
            ),
            disable_notification=False,
            disable_web_page_preview=True,
        )

    async def test_bot_failure_does_not_escape_after_retries(self) -> None:
        bot = Mock()
        bot.call.side_effect = TelegramBotApiError("temporary failure")
        notifier = BotVoiceNotifier(bot, 123456)

        with (
            patch(
                "telegram_voice_forwarder.notification_bot_adapter.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep,
            self.assertLogs(
                "telegram_voice_forwarder.notification_bot_adapter",
                level="ERROR",
            ),
        ):
            await notifier.notify_voice("Alice", "https://t.me/c/2/901")

        self.assertEqual(bot.call.call_count, 3)
        self.assertEqual(sleep.await_count, 2)
