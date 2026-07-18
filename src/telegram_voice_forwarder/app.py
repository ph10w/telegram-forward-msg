from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from telethon import TelegramClient, events, helpers, utils
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import MessageEntityTextUrl

from .config import BaseConfig, ForwarderConfig
from .state import StateStore

LOGGER = logging.getLogger(__name__)
CAPTION_LIMIT = 1024
SOURCE_LINK_LABEL = "Ursprungsnachricht"


def is_voice_message(message: Any, *, include_video_notes: bool = False) -> bool:
    return bool(message.voice or (include_video_notes and message.video_note))


def media_duration_seconds(message: Any) -> float | None:
    """Return Telegram's media duration without downloading the media."""
    duration = getattr(getattr(message, "file", None), "duration", None)
    if duration is None:
        media = getattr(message, "voice", None) or getattr(message, "video_note", None)
        for attribute in getattr(media, "attributes", ()):
            duration = getattr(attribute, "duration", None)
            if duration is not None:
                break
    if duration is None:
        return None
    try:
        return float(duration)
    except (TypeError, ValueError):
        return None


def telegram_message_link(
    source_id: int, message_id: int, *, username: str | None = None
) -> str | None:
    if username:
        return f"https://t.me/{username.lstrip('@')}/{message_id}"
    marked_id = str(source_id)
    if marked_id.startswith("-100"):
        return f"https://t.me/c/{marked_id[4:]}/{message_id}"
    return None


async def message_author(message: Any) -> str | None:
    post_author = (getattr(message, "post_author", None) or "").strip()
    if post_author:
        return " ".join(post_author.splitlines())

    sender = getattr(message, "sender", None)
    get_sender = getattr(message, "get_sender", None)
    if sender is None and callable(get_sender):
        try:
            sender = await get_sender()
        except RPCError:
            LOGGER.warning("Autor von Nachricht %s konnte nicht geladen werden", message.id)
    if sender is None:
        return None

    name = " ".join(utils.get_display_name(sender).splitlines()).strip()
    if not name:
        name = " ".join(
            part
            for part in (
                (getattr(sender, "first_name", None) or "").strip(),
                (getattr(sender, "last_name", None) or "").strip(),
            )
            if part
        ) or (getattr(sender, "title", None) or "").strip()
    username = (getattr(sender, "username", None) or "").strip().lstrip("@")
    if name and username:
        return f"{name} (@{username})"
    if username:
        return f"@{username}"
    return name or None


def linked_caption(
    message: Any,
    link: str,
    *,
    author: str | None = None,
    original_date: str | None = None,
) -> tuple[str, list[Any]]:
    original = getattr(message, "raw_text", None) or ""
    separator = "\n\n" if original else ""
    author_text = f"👤 Autor: {author}\n" if author else ""
    date_text = f"🕒 Originaldatum: {original_date}\n" if original_date else ""
    link_text = f"🔗 {SOURCE_LINK_LABEL}"
    suffix = separator + author_text + date_text + link_text
    caption = original + suffix
    entities = list(getattr(message, "entities", None) or [])

    if len(helpers.add_surrogate(caption)) > CAPTION_LIMIT:
        suffix_length = len(helpers.add_surrogate(suffix))
        available = max(0, CAPTION_LIMIT - suffix_length - 1)
        shortened = helpers.add_surrogate(original)[:available]
        if shortened and 0xD800 <= ord(shortened[-1]) <= 0xDBFF:
            shortened = shortened[:-1]
        original = helpers.del_surrogate(shortened).rstrip() + "…"
        separator = "\n\n" if original else ""
        caption = original + separator + author_text + date_text + link_text
        entities = []

    label_prefix = original + separator + author_text + date_text + "🔗 "
    entities.append(
        MessageEntityTextUrl(
            offset=len(helpers.add_surrogate(label_prefix)),
            length=len(helpers.add_surrogate(SOURCE_LINK_LABEL)),
            url=link,
        )
    )
    return caption, entities


def build_client(config: BaseConfig) -> TelegramClient:
    config.session_path.parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(
        str(config.session_path),
        config.api_id,
        config.api_hash,
        auto_reconnect=True,
        connection_retries=None,
        retry_delay=2,
    )


async def start_client(client: TelegramClient, config: BaseConfig) -> None:
    await client.start(phone=config.phone)
    me = await client.get_me()
    LOGGER.info("Angemeldet als %s (ID %s)", utils.get_display_name(me), me.id)


async def list_dialogs(config: BaseConfig) -> None:
    client = build_client(config)
    try:
        await start_client(client, config)
        print(f"{'ID':>16}  {'Typ':<10}  Name")
        print(f"{'-' * 16}  {'-' * 10}  {'-' * 40}")
        async for dialog in client.iter_dialogs():
            if dialog.is_group:
                kind = "Gruppe"
            elif dialog.is_channel:
                kind = "Kanal"
            elif dialog.is_user:
                kind = "Benutzer"
            else:
                kind = "Sonstiges"
            print(f"{dialog.id:>16}  {kind:<10}  {dialog.name}")
    finally:
        await client.disconnect()


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    id: int
    entity: Any
    name: str


class VoiceForwarder:
    def __init__(
        self,
        client: TelegramClient,
        config: ForwarderConfig,
        state: StateStore,
    ) -> None:
        self.client = client
        self.config = config
        self.state = state
        self.sources: dict[int, ResolvedSource] = {}
        self.target: Any = None
        self.target_id: int | None = None
        self._processing_lock = asyncio.Lock()

    async def resolve_chats(self) -> None:
        # Populate the entity cache so numeric IDs of private chats can resolve.
        await self.client.get_dialogs()
        self.target = await self.client.get_entity(self.config.target_chat)
        self.target_id = utils.get_peer_id(self.target)

        for reference in self.config.source_chats:
            entity = await self.client.get_entity(reference)
            source_id = utils.get_peer_id(entity)
            if source_id == self.target_id:
                raise ValueError("Quell- und Zielchat dürfen nicht identisch sein.")
            self.sources[source_id] = ResolvedSource(
                id=source_id,
                entity=entity,
                name=utils.get_display_name(entity),
            )

        LOGGER.info(
            "Überwache %s Quelle(n); Ziel: %s (ID %s)",
            len(self.sources),
            utils.get_display_name(self.target),
            self.target_id,
        )

    async def process_message(self, source_id: int, message: Any) -> None:
        if not is_voice_message(
            message, include_video_notes=self.config.include_video_notes
        ):
            self.state.advance_cursor(source_id, message.id)
            return

        if self.state.is_complete(source_id, message.id):
            self.state.advance_cursor(source_id, message.id)
            return

        minimum = self.config.min_voice_duration_seconds
        duration = media_duration_seconds(message)
        if minimum > 0 and duration is not None and duration < minimum:
            self.state.mark_ignored(
                source_id,
                message.id,
                f"Dauer {duration:g}s liegt unter dem Minimum von {minimum:g}s",
            )
            LOGGER.info(
                "Kurze Sprachnachricht ignoriert (Quelle %s, Nachricht %s, %.1fs < %.1fs)",
                source_id,
                message.id,
                duration,
                minimum,
            )
            return
        if minimum > 0 and duration is None:
            LOGGER.warning(
                "Dauer nicht ermittelbar; Nachricht %s aus Quelle %s wird weitergeleitet",
                message.id,
                source_id,
            )

        self.state.mark_pending(source_id, message.id)
        try:
            await self._send_with_retry(source_id, message)
        except Exception as exc:
            self.state.mark_failed(source_id, message.id, f"{type(exc).__name__}: {exc}")
            LOGGER.exception(
                "Weiterleitung fehlgeschlagen (Quelle %s, Nachricht %s)",
                source_id,
                message.id,
            )
            return

        self.state.mark_forwarded(source_id, message.id)
        LOGGER.info(
            "Sprachnachricht weitergeleitet (Quelle %s, Nachricht %s)",
            source_id,
            message.id,
        )

    async def _send_with_retry(
        self, source_id: int, message: Any, retries: int = 3
    ) -> None:
        source = self.sources.get(source_id)
        username = getattr(source.entity, "username", None) if source else None
        link = telegram_message_link(source_id, message.id, username=username)
        caption: str | None = None
        entities: list[Any] | None = None
        if message.voice and link:
            author = await message_author(message)
            message_date = getattr(message, "date", None)
            original_date = (
                message_date.astimezone().strftime("%d.%m.%Y %H:%M:%S")
                if message_date
                else None
            )
            caption, entities = linked_caption(
                message,
                link,
                author=author,
                original_date=original_date,
            )

        for attempt in range(1, retries + 1):
            try:
                if message.voice and link:
                    await self.client.send_file(
                        self.target,
                        message.voice,
                        caption=caption,
                        formatting_entities=entities,
                        voice_note=True,
                    )
                else:
                    await self.client.forward_messages(self.target, message)
                return
            except FloodWaitError as exc:
                if attempt == retries or exc.seconds > 60:
                    raise
                LOGGER.warning("Telegram-Limit erreicht; warte %s Sekunden", exc.seconds)
                await asyncio.sleep(exc.seconds + 1)
            except RPCError:
                if attempt == retries:
                    raise
                await asyncio.sleep(2 ** (attempt - 1))

    async def retry_pending(self) -> None:
        jobs = self.state.pending_jobs()
        if jobs:
            LOGGER.info("Versuche %s offene Weiterleitung(en) erneut", len(jobs))
        for job in jobs:
            source = self.sources.get(job.source_id)
            if source is None:
                LOGGER.warning(
                    "Offener Auftrag für nicht mehr konfigurierte Quelle %s bleibt liegen",
                    job.source_id,
                )
                continue
            message = await self.client.get_messages(source.entity, ids=job.message_id)
            if message is None:
                self.state.mark_ignored(job.source_id, job.message_id, "Nachricht nicht mehr verfügbar")
                LOGGER.warning(
                    "Nachricht %s aus Quelle %s ist nicht mehr verfügbar",
                    job.message_id,
                    job.source_id,
                )
                continue
            await self.process_message(job.source_id, message)

    async def catch_up(self) -> None:
        for source in self.sources.values():
            cursor = self.state.cursor(source.id)
            if cursor == 0:
                await self._initial_scan(source)
            else:
                count = 0
                async for message in self.client.iter_messages(
                    source.entity, min_id=cursor, reverse=True
                ):
                    await self.process_message(source.id, message)
                    count += 1
                if count:
                    LOGGER.info("%s neue Nachricht(en) in %s nachgeholt", count, source.name)

    async def _initial_scan(self, source: ResolvedSource) -> None:
        limit = self.config.initial_scan_limit
        if limit == 0:
            newest = await self.client.get_messages(source.entity, limit=1)
            if newest:
                self.state.advance_cursor(source.id, newest[0].id)
            LOGGER.info("Historischen Import für %s übersprungen", source.name)
            return

        messages: Iterable[Any] = await self.client.get_messages(source.entity, limit=limit)
        ordered = list(reversed(list(messages)))
        for message in ordered:
            await self.process_message(source.id, message)
        LOGGER.info("%s letzte Nachricht(en) in %s geprüft", len(ordered), source.name)

    async def run(self) -> None:
        await self.resolve_chats()

        async def on_new_message(event: events.NewMessage.Event) -> None:
            source_id = event.chat_id
            if source_id not in self.sources:
                return
            async with self._processing_lock:
                await self.process_message(source_id, event.message)

        self.client.add_event_handler(
            on_new_message,
            events.NewMessage(chats=[source.entity for source in self.sources.values()]),
        )

        # Live updates queue behind this lock while recovery establishes a clean cursor.
        async with self._processing_lock:
            await self.retry_pending()
            await self.catch_up()

        LOGGER.info("Monitoring läuft. Beenden mit Ctrl+C.")
        await self.client.run_until_disconnected()


async def run_forwarder(config: ForwarderConfig) -> None:
    client = build_client(config)
    state = StateStore(config.state_db)
    try:
        await start_client(client, config)
        await VoiceForwarder(client, config, state).run()
    finally:
        state.close()
        await client.disconnect()
