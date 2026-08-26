"""Telegram Bot API target transport using a private, temporary relay message."""

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from telethon import TelegramClient, utils
from telethon.tl.types import (
    MessageEntityBlockquote,
    MessageEntityBold,
    MessageEntityCode,
    MessageEntityCustomEmoji,
    MessageEntityEmail,
    MessageEntityHashtag,
    MessageEntityItalic,
    MessageEntityMention,
    MessageEntityPhone,
    MessageEntityPre,
    MessageEntitySpoiler,
    MessageEntityStrike,
    MessageEntityTextUrl,
    MessageEntityUnderline,
    MessageEntityUrl,
)

from .bot_api import BotApi
from .config import BaseConfig, ChatRef
from .errors import TelegramBotApiError
from .telegram_adapter import start_client

LOGGER = logging.getLogger(__name__)
RELAY_TIMEOUT_SECONDS = 45


@dataclass(frozen=True, slots=True)
class BotTarget:
    id: int
    title: str
    username: str | None


def _bot_entities(entities: list[Any] | None) -> list[dict[str, object]] | None:
    if not entities:
        return None
    entity_types: dict[type[Any], str] = {
        MessageEntityMention: "mention",
        MessageEntityHashtag: "hashtag",
        MessageEntityUrl: "url",
        MessageEntityEmail: "email",
        MessageEntityBold: "bold",
        MessageEntityItalic: "italic",
        MessageEntityCode: "code",
        MessageEntityPhone: "phone_number",
        MessageEntityUnderline: "underline",
        MessageEntityStrike: "strikethrough",
        MessageEntityBlockquote: "blockquote",
        MessageEntitySpoiler: "spoiler",
        MessageEntityCustomEmoji: "custom_emoji",
        MessageEntityTextUrl: "text_link",
        MessageEntityPre: "pre",
    }
    converted: list[dict[str, object]] = []
    for entity in entities:
        kind = entity_types.get(type(entity))
        if kind is None:
            continue
        item: dict[str, object] = {
            "type": kind,
            "offset": entity.offset,
            "length": entity.length,
        }
        if isinstance(entity, MessageEntityTextUrl):
            item["url"] = entity.url
        elif isinstance(entity, MessageEntityPre):
            item["language"] = entity.language
        elif isinstance(entity, MessageEntityCustomEmoji):
            item["custom_emoji_id"] = str(entity.document_id)
        converted.append(item)
    return converted or None


class BotRelayClient:
    """A target-client facade consumed by ``VoiceForwarder``."""

    def __init__(
        self,
        api: BotApi,
        source_client: TelegramClient,
        target_chat: ChatRef,
    ) -> None:
        self._api = api
        self._source_client = source_client
        self._target_chat = target_chat
        self._user_id: int | None = None
        self._bot_entity: Any = None
        self._offset: int | None = None

    async def start(self, user_id: int) -> None:
        webhook = await self._call("getWebhookInfo")
        if isinstance(webhook, dict) and webhook.get("url"):
            raise TelegramBotApiError(
                "Für den Relay-Bot ist ein Webhook konfiguriert; getUpdates kann "
                "nicht gleichzeitig verwendet werden."
            )
        identity = await self._call("getMe")
        username = identity.get("username") if isinstance(identity, dict) else None
        if not username:
            raise TelegramBotApiError("Telegram lieferte keinen Benutzernamen für den Bot.")
        self._bot_entity = await self._source_client.get_entity(f"@{username}")
        self._user_id = user_id
        updates = await self._call("getUpdates", offset=-1, timeout=0, limit=1)
        if updates:
            self._offset = int(updates[-1]["update_id"]) + 1

    async def get_entity(self, reference: ChatRef) -> BotTarget:
        chat = await self._call("getChat", chat_id=reference)
        if not isinstance(chat, dict) or not isinstance(chat.get("id"), int):
            raise TelegramBotApiError("Telegram lieferte keinen gültigen Zielchat.")
        title = str(chat.get("title") or chat.get("username") or chat["id"])
        username = chat.get("username")
        return BotTarget(chat["id"], title, str(username) if username else None)

    async def get_dialogs(self) -> None:
        return None

    async def send_message(
        self, entity: BotTarget, text: str, *, parse_mode: object = None
    ) -> SimpleNamespace:
        del entity, parse_mode
        result = await self._call(
            "sendMessage",
            chat_id=self._target_chat,
            text=text,
            disable_notification=False,
        )
        return SimpleNamespace(id=self._message_id(result))

    async def edit_message(
        self,
        entity: BotTarget,
        message_id: int,
        text: str,
        *,
        parse_mode: object = None,
    ) -> None:
        del entity, parse_mode
        await self._call(
            "editMessageText",
            chat_id=self._target_chat,
            message_id=message_id,
            text=text,
        )

    async def copy_message(
        self,
        source_id: int,
        source_message_id: int,
        message: Any,
        *,
        caption: str | None,
        entities: list[Any] | None,
    ) -> SimpleNamespace:
        del source_id, source_message_id
        if self._user_id is None or self._bot_entity is None:
            raise RuntimeError("Bot-Relay wurde nicht gestartet.")

        marker = f"telegram-voice-forwarder:{secrets.token_urlsafe(18)}"
        relay_message: Any = None
        incoming_message_id: int | None = None
        try:
            media = message.voice or message.video_note
            relay_message = await self._source_client.send_file(
                self._bot_entity,
                media,
                caption=marker,
                parse_mode=None,
                silent=True,
                voice_note=bool(message.voice),
                video_note=bool(message.video_note),
            )
            incoming_message_id = await self._wait_for_relay(marker)
            parameters: dict[str, object] = {
                "chat_id": self._target_chat,
                "from_chat_id": self._user_id,
                "message_id": incoming_message_id,
                "disable_notification": False,
            }
            if message.voice:
                parameters["caption"] = caption
                parameters["caption_entities"] = _bot_entities(entities)
            result = await self._call("copyMessage", **parameters)
            return SimpleNamespace(id=self._message_id(result))
        finally:
            if incoming_message_id is not None:
                try:
                    await self._call(
                        "deleteMessage",
                        chat_id=self._user_id,
                        message_id=incoming_message_id,
                    )
                except TelegramBotApiError:
                    LOGGER.warning("Bot-Relay-Nachricht konnte nicht gelöscht werden")
            elif relay_message is not None:
                try:
                    await self._source_client.delete_messages(
                        self._bot_entity, [relay_message.id], revoke=True
                    )
                except Exception:
                    LOGGER.warning(
                        "Nicht zugestellte Bot-Relay-Nachricht konnte nicht gelöscht werden",
                        exc_info=True,
                    )

    async def delete_messages(
        self, entity: BotTarget, message_ids: list[int], *, revoke: bool = True
    ) -> None:
        del entity, revoke
        for index in range(0, len(message_ids), 100):
            await self._call(
                "deleteMessages",
                chat_id=self._target_chat,
                message_ids=message_ids[index : index + 100],
            )

    async def _wait_for_relay(self, marker: str) -> int:
        if self._user_id is None:
            raise RuntimeError("Bot-Relay wurde nicht gestartet.")
        deadline = time.monotonic() + RELAY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            updates = await self._call(
                "getUpdates",
                offset=self._offset,
                timeout=min(10, max(1, int(deadline - time.monotonic()))),
                allowed_updates=["message"],
            )
            for update in updates or []:
                self._offset = int(update["update_id"]) + 1
                message = update.get("message")
                if not isinstance(message, dict):
                    continue
                chat = message.get("chat")
                sender = message.get("from")
                if (
                    isinstance(chat, dict)
                    and chat.get("id") == self._user_id
                    and isinstance(sender, dict)
                    and sender.get("id") == self._user_id
                    and message.get("caption") == marker
                ):
                    return self._message_id(message)
        raise TelegramBotApiError("Bot-Relay-Nachricht wurde nicht rechtzeitig empfangen.")

    async def _call(self, method: str, **parameters: object) -> Any:
        return await asyncio.to_thread(self._api.call, method, **parameters)

    @staticmethod
    def _message_id(message: object) -> int:
        if not isinstance(message, dict) or not isinstance(message.get("message_id"), int):
            raise TelegramBotApiError("Telegram lieferte keine Ziel-Nachrichten-ID.")
        return message["message_id"]


class BotResetGateway:
    def __init__(
        self,
        client: TelegramClient,
        config: BaseConfig,
        api: BotApi,
        target_chat: ChatRef,
    ) -> None:
        self._client = client
        self._config = config
        self._api = api
        self._target_chat = target_chat

    async def start(self) -> None:
        await start_client(self._client, self._config)

    async def resolve_target(self, reference: ChatRef) -> int:
        chat = await asyncio.to_thread(self._api.call, "getChat", chat_id=reference)
        if not isinstance(chat, dict) or not isinstance(chat.get("id"), int):
            raise TelegramBotApiError("Telegram lieferte keinen gültigen Zielchat.")
        return chat["id"]

    async def resolve_source(self, reference: ChatRef) -> int:
        entity = await self._client.get_entity(reference)
        return utils.get_peer_id(entity)

    async def boundary_before(
        self, reference: ChatRef, cutoff: datetime
    ) -> tuple[int, int]:
        entity = await self._client.get_entity(reference)
        source_id = utils.get_peer_id(entity)
        messages = await self._client.get_messages(entity, limit=1, offset_date=cutoff)
        return source_id, messages[0].id if messages else 0

    async def delete_target_messages(self, message_ids: tuple[int, ...]) -> None:
        for index in range(0, len(message_ids), 100):
            await asyncio.to_thread(
                self._api.call,
                "deleteMessages",
                chat_id=self._target_chat,
                message_ids=message_ids[index : index + 100],
            )

    async def close(self) -> None:
        await self._client.disconnect()
