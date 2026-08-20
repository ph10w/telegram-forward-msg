import asyncio
import logging

from .bot_api import BotApi
from .errors import TelegramBotApiError

LOGGER = logging.getLogger(__name__)
SEND_ATTEMPTS = 3


class BotVoiceNotifier:
    def __init__(self, bot: BotApi, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id

    async def notify_voice(self, author: str, target_message_link: str) -> None:
        text = (
            "🎙️ Neue Sprachnachricht\n"
            f"👤 Autor: {author}\n"
            f"🔗 {target_message_link}"
        )
        for attempt in range(1, SEND_ATTEMPTS + 1):
            try:
                await asyncio.to_thread(
                    self._bot.call,
                    "sendMessage",
                    chat_id=self._chat_id,
                    text=text,
                    disable_notification=False,
                    disable_web_page_preview=True,
                )
                return
            except TelegramBotApiError:
                if attempt == SEND_ATTEMPTS:
                    LOGGER.exception(
                        "Private Bot-Benachrichtigung konnte nicht gesendet werden"
                    )
                    return
                await asyncio.sleep(2 ** (attempt - 1))
