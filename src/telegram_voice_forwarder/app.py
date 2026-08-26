import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from telethon import TelegramClient, events, helpers, utils
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import Channel, Chat, MessageEntityTextUrl

from .config import ForwarderConfig
from .core import (
    ActiveBlock as CoreActiveBlock,
    BlockCloseReason,
    BlockPolicy,
    MessageAction,
    MessageFacts,
    SourceKind,
)
from .errors import TelegramBotApiError
from .ports import MonitoringStateRepository

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


def message_timestamp(message: Any) -> datetime:
    forwarded = getattr(message, "fwd_from", None)
    value = getattr(forwarded, "date", None) or getattr(message, "date", None)
    if not isinstance(value, datetime):
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def telegram_message_link(
    source_id: int, message_id: int, *, username: str | None = None
) -> str | None:
    if username:
        return f"https://t.me/{username.lstrip('@')}/{message_id}"
    marked_id = str(source_id)
    if marked_id.startswith("-100"):
        return f"https://t.me/c/{marked_id[4:]}/{message_id}"
    return None


async def message_author(message: Any) -> str:
    _, label = await message_author_details(message)
    return label


async def message_author_details(message: Any) -> tuple[str, str]:
    post_author = (getattr(message, "post_author", None) or "").strip()
    if post_author:
        label = " ".join(post_author.splitlines())
        return f"signature:{label.casefold()}", label

    sender = getattr(message, "sender", None)
    get_sender = getattr(message, "get_sender", None)
    if sender is None and callable(get_sender):
        try:
            sender = await get_sender()
        except RPCError:
            LOGGER.warning("Autor von Nachricht %s konnte nicht geladen werden", message.id)
    if sender is None:
        return "unknown", "Unbekannter Autor"

    return _entity_author_details(
        sender,
        getattr(message, "sender_id", None) or getattr(sender, "id", None),
    )


def _entity_author_details(
    entity: Any,
    entity_id: int | None = None,
) -> tuple[str, str]:
    name = " ".join(utils.get_display_name(entity).splitlines()).strip()
    if not name:
        name = " ".join(
            part
            for part in (
                (getattr(entity, "first_name", None) or "").strip(),
                (getattr(entity, "last_name", None) or "").strip(),
            )
            if part
        ) or (getattr(entity, "title", None) or "").strip()
    username = (getattr(entity, "username", None) or "").strip().lstrip("@")
    if name and username:
        label = f"{name} (@{username})"
    elif username:
        label = f"@{username}"
    else:
        label = name or "Unbekannter Autor"

    if entity_id is not None:
        return f"sender:{entity_id}", label
    if username:
        return f"username:{username.casefold()}", label
    return f"label:{label.casefold()}", label


async def forwarded_author_details(message: Any) -> tuple[str, str]:
    header = getattr(message, "fwd_from", None)
    if header is None:
        return await message_author_details(message)

    post_author = (getattr(header, "post_author", None) or "").strip()
    if post_author:
        label = " ".join(post_author.splitlines())
        return f"forward-signature:{label.casefold()}", label

    forward = getattr(message, "forward", None)
    entity = None
    entity_id: int | None = None
    if forward is not None:
        entity = getattr(forward, "sender", None) or getattr(forward, "chat", None)
        entity_id = getattr(forward, "sender_id", None) or getattr(
            forward, "chat_id", None
        )
        if entity is None:
            getter = (
                getattr(forward, "get_sender", None)
                if getattr(forward, "sender_id", None) is not None
                else getattr(forward, "get_chat", None)
            )
            if callable(getter):
                try:
                    entity = await getter()
                except (RPCError, ValueError):
                    LOGGER.warning(
                        "Originalautor von Nachricht %s konnte nicht geladen werden",
                        message.id,
                    )
    if entity is not None:
        return _entity_author_details(
            entity,
            entity_id or getattr(entity, "id", None),
        )

    from_name = (
        (getattr(header, "from_name", None) or "").strip()
        or (getattr(header, "saved_from_name", None) or "").strip()
    )
    if from_name:
        label = " ".join(from_name.splitlines())
        return f"forward-name:{label.casefold()}", label

    from_id = getattr(header, "from_id", None)
    if from_id is not None:
        try:
            return f"sender:{utils.get_peer_id(from_id)}", "Unbekannter Originalautor"
        except (TypeError, ValueError):
            pass
    return "forward:unknown", "Unbekannter Originalautor"


def forwarded_origin(message: Any) -> tuple[int, int] | None:
    header = getattr(message, "fwd_from", None)
    if header is None:
        return None

    origin_peer = getattr(header, "saved_from_peer", None)
    origin_message_id = getattr(header, "saved_from_msg_id", None)
    if origin_peer is None or not isinstance(origin_message_id, int):
        origin_peer = getattr(header, "from_id", None)
        origin_message_id = getattr(header, "channel_post", None)
    if origin_peer is None or not isinstance(origin_message_id, int):
        return None
    try:
        return utils.get_peer_id(origin_peer), origin_message_id
    except (TypeError, ValueError):
        return None


def collection_header(author: str, count: int) -> str:
    noun = "Sprachnachricht" if count == 1 else "Sprachnachrichten"
    return f"👤 Autor: {author}\n🎙️ {count} {noun}"


def linked_caption(
    message: Any,
    link: str,
    *,
    original_date: str | None = None,
    author: str | None = None,
) -> tuple[str, list[Any]]:
    original = getattr(message, "raw_text", None) or ""
    separator = "\n\n" if original else ""
    label = original_date or SOURCE_LINK_LABEL
    author_text = f"👤 Autor: {author}\n" if author else ""
    link_text = f"🕒 {label}"
    suffix = separator + author_text + link_text
    caption = original + suffix
    entities = list(getattr(message, "entities", None) or [])

    if len(helpers.add_surrogate(caption)) > CAPTION_LIMIT:
        suffix_length = len(helpers.add_surrogate(suffix))
        available = max(0, CAPTION_LIMIT - suffix_length - 1)
        shortened = helpers.add_surrogate(original)[:available]
        if shortened and 0xD800 <= ord(shortened[-1]) <= 0xDBFF:
            shortened = shortened[:-1]
        original = (
            helpers.del_surrogate(shortened).rstrip() + "…" if shortened else ""
        )
        separator = "\n\n" if original else ""
        caption = original + separator + author_text + link_text
        entities = []

    label_prefix = original + separator + author_text + "🕒 "
    entities.append(
        MessageEntityTextUrl(
            offset=len(helpers.add_surrogate(label_prefix)),
            length=len(helpers.add_surrogate(label)),
            url=link,
        )
    )
    return caption, entities


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    id: int
    entity: Any
    name: str
    kind: SourceKind = SourceKind.GROUP


def source_kind(entity: Any) -> SourceKind:
    if isinstance(entity, Channel):
        if entity.megagroup:
            return SourceKind.SUPERGROUP
        return SourceKind.CHANNEL
    if isinstance(entity, Chat):
        return SourceKind.GROUP
    raise ValueError("Als Telegram-Quelle sind nur Gruppen und Kanäle erlaubt.")


class VoiceForwarder:
    def __init__(
        self,
        client: TelegramClient,
        config: ForwarderConfig,
        state: MonitoringStateRepository,
        target_client: Any | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.state = state
        self.target_client = target_client or client
        self.sources: dict[int, ResolvedSource] = {}
        self.target: Any = None
        self.target_id: int | None = None
        self._processing_lock = asyncio.Lock()

    async def resolve_chats(self) -> None:
        LOGGER.info("Löse Telegram-Zielchat %s auf", self.config.target_chat)
        self.target = await self._resolve_target_entity(self.config.target_chat)
        explicit_target_id = getattr(self.target, "id", None)
        self.target_id = (
            explicit_target_id
            if isinstance(explicit_target_id, int) and explicit_target_id < 0
            else utils.get_peer_id(self.target)
        )

        for reference in self.config.source_chats:
            LOGGER.info("Löse Telegram-Quellchat %s auf", reference)
            entity = await self._resolve_entity(reference)
            source_id = utils.get_peer_id(entity)
            if source_id == self.target_id:
                raise ValueError("Quell- und Zielchat dürfen nicht identisch sein.")
            self.sources[source_id] = ResolvedSource(
                id=source_id,
                entity=entity,
                name=utils.get_display_name(entity),
                kind=source_kind(entity),
            )

        LOGGER.info(
            "Überwache %s Quelle(n); Ziel: %s (ID %s)",
            len(self.sources),
            getattr(self.target, "title", None) or utils.get_display_name(self.target),
            self.target_id,
        )

    async def _resolve_entity(self, reference: int | str) -> Any:
        try:
            return await self.client.get_entity(reference)
        except ValueError:
            LOGGER.info(
                "Telegram-Entity %s fehlt im lokalen Cache; lade Dialogliste",
                reference,
            )
            await self.client.get_dialogs()
            return await self.client.get_entity(reference)

    async def _resolve_target_entity(self, reference: int | str) -> Any:
        try:
            return await self.target_client.get_entity(reference)
        except ValueError:
            LOGGER.info(
                "Telegram-Ziel %s fehlt im Bot-Cache; lade Bot-Dialogliste",
                reference,
            )
            await self.target_client.get_dialogs()
            return await self.target_client.get_entity(reference)

    async def process_message(
        self,
        source_id: int,
        message: Any,
        *,
        assigned_block_id: int | None = None,
    ) -> None:
        is_voice = is_voice_message(
            message, include_video_notes=self.config.include_video_notes
        )
        is_forwarded = getattr(message, "fwd_from", None) is not None
        duration = media_duration_seconds(message) if is_voice else None
        source = self.sources.get(source_id)
        allows_blocks = source.kind.allows_blocks if source is not None else True
        if is_voice and self.state.is_complete(source_id, message.id):
            self.state.advance_cursor(source_id, message.id)
            return

        message_at = message_timestamp(message)
        block = (
            self.state.voice_block(assigned_block_id)
            if assigned_block_id is not None and not is_forwarded and allows_blocks
            else None
        )
        if block is not None and (
            block.source_id != source_id or block.target_chat_id != self.target_id
        ):
            block = None

        if block is None:
            active = self.state.active_voice_block(source_id)
            author_key: str | None = None
            author_label: str | None = None
            if is_voice:
                author_key, author_label = await (
                    forwarded_author_details(message)
                    if is_forwarded
                    else message_author_details(message)
                )
            if self.target_id is None:
                raise RuntimeError("Zielchat-ID ist nicht aufgelöst.")
            decision = BlockPolicy(
                minimum_voice_duration_seconds=(
                    self.config.min_voice_duration_seconds
                ),
                target_chat_id=self.target_id,
            ).decide(
                MessageFacts(
                    is_voice=is_voice,
                    observed_at=message_at,
                    duration_seconds=duration,
                    author_key=author_key,
                    is_forwarded=is_forwarded,
                    allows_blocks=allows_blocks,
                ),
                CoreActiveBlock(
                    author_key=active.author_key,
                    target_chat_id=active.target_chat_id,
                    non_voice_count=active.non_voice_count,
                    last_voice_at=active.last_voice_at,
                )
                if active is not None
                else None,
            )

            if decision.close_reason is BlockCloseReason.TIMEOUT:
                if active is not None:
                    self.state.close_active_voice_block(
                        source_id, active.last_observed_message_id
                    )
                LOGGER.info(
                    "Voice-Block in Quelle %s nach vier Stunden Inaktivität geschlossen",
                    source_id,
                )
                active = None
            elif decision.close_reason in (
                BlockCloseReason.DIFFERENT_AUTHOR_OR_TARGET,
                BlockCloseReason.FORWARDED_MESSAGE,
                BlockCloseReason.BLOCKS_DISABLED,
            ):
                self.state.close_active_voice_block(source_id, message.id)
                active = None

            match decision.action:
                case MessageAction.SKIP_NON_VOICE:
                    self.state.advance_cursor(source_id, message.id)
                    return
                case (
                    MessageAction.RECORD_NON_VOICE
                    | MessageAction.CLOSE_ON_NON_VOICE
                ):
                    should_close = (
                        decision.action is MessageAction.CLOSE_ON_NON_VOICE
                    )
                    self.state.note_non_voice(
                        source_id, message.id, close=should_close
                    )
                    if should_close:
                        LOGGER.info(
                            "Voice-Block in Quelle %s nach fünf "
                            "Nicht-Voice-Nachrichten geschlossen",
                            source_id,
                        )
                    self.state.advance_cursor(source_id, message.id)
                    return
                case MessageAction.IGNORE_SHORT_VOICE:
                    minimum = self.config.min_voice_duration_seconds
                    self.state.mark_ignored(
                        source_id,
                        message.id,
                        f"Dauer {duration:g}s liegt unter dem Minimum von {minimum:g}s",
                        message_at=message_at,
                        author_key=author_key if is_forwarded else None,
                        author_label=author_label if is_forwarded else None,
                        is_forwarded=is_forwarded,
                        duration_seconds=duration,
                    )
                    LOGGER.info(
                        "Kurze erste Sprachnachricht ignoriert "
                        "(Quelle %s, Nachricht %s, %.1fs < %.1fs)",
                        source_id,
                        message.id,
                        duration,
                        minimum,
                    )
                    return
                case MessageAction.FORWARD_STANDALONE:
                    if author_key is None or author_label is None:
                        raise RuntimeError("Autor der Voice-Nachricht fehlt.")
                    if (
                        self.config.min_voice_duration_seconds > 0
                        and duration is None
                    ):
                        LOGGER.warning(
                            "Dauer der weitergeleiteten Nachricht %s aus Quelle %s "
                            "nicht ermittelbar; Nachricht wird verarbeitet",
                            message.id,
                            source_id,
                        )
                    await self._forward_voice_without_block(
                        source_id,
                        message,
                        message_at=message_at,
                        author_key=author_key,
                        author_label=author_label,
                        duration_seconds=duration,
                    )
                    return
                case MessageAction.JOIN_BLOCK:
                    if active is None:
                        raise RuntimeError("Aktiver Voice-Block fehlt.")
                    block = active
                case MessageAction.START_BLOCK:
                    if author_key is None or author_label is None:
                        raise RuntimeError("Autor der Voice-Nachricht fehlt.")
                    if (
                        self.config.min_voice_duration_seconds > 0
                        and duration is None
                    ):
                        LOGGER.warning(
                            "Dauer nicht ermittelbar; Nachricht %s aus Quelle %s "
                            "eröffnet einen Voice-Block",
                            message.id,
                            source_id,
                        )

                    self.state.mark_pending(
                        source_id,
                        message.id,
                        message_at=message_at,
                        is_forwarded=False,
                        duration_seconds=duration,
                    )
                    try:
                        header = await self.target_client.send_message(
                            self.target,
                            collection_header(author_label, 1),
                            parse_mode=None,
                        )
                        header_id = getattr(header, "id", None)
                        if not isinstance(header_id, int):
                            raise RuntimeError("Ziel-ID der Sammelnachricht fehlt.")
                        block = self.state.create_voice_block(
                            source_id,
                            author_key,
                            author_label,
                            self.target_id,
                            header_id,
                            message.id,
                            message_at,
                        )
                    except Exception as exc:
                        self.state.mark_failed(
                            source_id, message.id, f"{type(exc).__name__}: {exc}"
                        )
                        LOGGER.exception(
                            "Sammelnachricht fehlgeschlagen "
                            "(Quelle %s, Nachricht %s)",
                            source_id,
                            message.id,
                        )
                        return
                case _:
                    raise RuntimeError(
                        f"Unbekannte Nachrichtenaktion: {decision.action!r}"
                    )

        self.state.mark_pending(
            source_id,
            message.id,
            block_id=block.id,
            message_at=message_at,
            is_forwarded=False,
            duration_seconds=duration,
        )
        try:
            target_message_id = await self._send_with_retry(source_id, message)
        except Exception as exc:
            self.state.mark_failed(source_id, message.id, f"{type(exc).__name__}: {exc}")
            LOGGER.exception(
                "Weiterleitung fehlgeschlagen (Quelle %s, Nachricht %s)",
                source_id,
                message.id,
            )
            return

        block_count = self.state.mark_forwarded(
            source_id,
            message.id,
            target_chat_id=self.target_id,
            target_message_id=target_message_id,
            block_id=block.id,
            message_at=message_at,
            is_forwarded=False,
            duration_seconds=duration,
        )
        if target_message_id is None:
            LOGGER.warning(
                "Ziel-Nachrichten-ID für Quelle %s, Nachricht %s nicht ermittelbar; "
                "diese Zielnachricht kann bei einem Reset nicht automatisch gelöscht werden",
                source_id,
                message.id,
            )
        if block_count is not None and block_count > 1:
            try:
                await self.target_client.edit_message(
                    self.target,
                    block.header_message_id,
                    collection_header(block.author_label, block_count),
                    parse_mode=None,
                )
            except (RPCError, TelegramBotApiError):
                LOGGER.exception(
                    "Anzahl in Sammelnachricht %s konnte nicht aktualisiert werden",
                    block.header_message_id,
                )
        LOGGER.info(
            "Sprachnachricht weitergeleitet (Quelle %s, Nachricht %s)",
            source_id,
            message.id,
        )

    async def _forward_voice_without_block(
        self,
        source_id: int,
        message: Any,
        *,
        message_at: datetime,
        author_key: str,
        author_label: str,
        duration_seconds: float | None,
    ) -> None:
        if self.target_id is None:
            raise RuntimeError("Zielchat-ID ist nicht aufgelöst.")

        is_forwarded = getattr(message, "fwd_from", None) is not None
        origin = forwarded_origin(message)
        inferred_origin = False
        if is_forwarded and origin is None:
            candidates = (
                self.state.matching_original_message_ids(
                    source_id,
                    self.target_id,
                    message_at,
                    author_key,
                    duration_seconds,
                    message.id,
                )
                if duration_seconds is not None
                else ()
            )
            if len(candidates) == 1:
                origin = source_id, candidates[0]
                inferred_origin = True
                LOGGER.info(
                    "Ursprung einer internen Weiterleitung eindeutig ermittelt "
                    "(Quelle %s, Nachricht %s, Original %s)",
                    source_id,
                    message.id,
                    candidates[0],
                )
            elif len(candidates) > 1:
                LOGGER.warning(
                    "Ursprung einer internen Weiterleitung ist nicht eindeutig; "
                    "Nachricht wird verarbeitet (Quelle %s, Nachricht %s)",
                    source_id,
                    message.id,
                )

        origin_already_forwarded = inferred_origin or (
            origin is not None
            and self.state.has_forwarded_origin(
                *origin,
                self.target_id,
            )
        )
        if origin_already_forwarded:
            if origin is None:
                raise RuntimeError("Ermittelter Nachrichtenursprung fehlt.")
            self.state.mark_ignored(
                source_id,
                message.id,
                "Ursprungsnachricht wurde bereits weitergeleitet",
                target_chat_id=self.target_id,
                message_at=message_at,
                author_key=author_key,
                author_label=author_label,
                origin_chat_id=origin[0],
                origin_message_id=origin[1],
                is_forwarded=is_forwarded,
                duration_seconds=duration_seconds,
            )
            LOGGER.info(
                "Bereits weitergeleitete Ursprungsnachricht ignoriert "
                "(Quelle %s, Nachricht %s, Ursprung %s/%s)",
                source_id,
                message.id,
                origin[0],
                origin[1],
            )
            return

        self.state.mark_pending(
            source_id,
            message.id,
            message_at=message_at,
            author_key=author_key,
            author_label=author_label,
            origin_chat_id=origin[0] if origin is not None else None,
            origin_message_id=origin[1] if origin is not None else None,
            is_forwarded=is_forwarded,
            duration_seconds=duration_seconds,
        )
        try:
            target_message_id = await self._send_with_retry(
                source_id,
                message,
                caption_author=author_label,
            )
        except Exception as exc:
            self.state.mark_failed(source_id, message.id, f"{type(exc).__name__}: {exc}")
            LOGGER.exception(
                "Weiterleitung fehlgeschlagen (Quelle %s, Nachricht %s)",
                source_id,
                message.id,
            )
            return

        self.state.mark_forwarded(
            source_id,
            message.id,
            target_chat_id=self.target_id,
            target_message_id=target_message_id,
            block_id=None,
            message_at=message_at,
            author_key=author_key,
            author_label=author_label,
            origin_chat_id=origin[0] if origin is not None else None,
            origin_message_id=origin[1] if origin is not None else None,
            is_forwarded=is_forwarded,
            duration_seconds=duration_seconds,
        )
        if target_message_id is None:
            LOGGER.warning(
                "Ziel-Nachrichten-ID für Quelle %s, Nachricht %s nicht ermittelbar; "
                "diese Zielnachricht kann bei einem Reset nicht automatisch gelöscht werden",
                source_id,
                message.id,
            )
        LOGGER.info(
            "Sprachnachricht ohne Sammelblock übertragen "
            "(Quelle %s, Nachricht %s, Autor %s)",
            source_id,
            message.id,
            author_label,
        )

    async def _send_with_retry(
        self,
        source_id: int,
        message: Any,
        retries: int = 3,
        *,
        caption_author: str | None = None,
    ) -> int | None:
        source = self.sources.get(source_id)
        username = getattr(source.entity, "username", None) if source else None
        link = telegram_message_link(source_id, message.id, username=username)
        caption: str | None = None
        entities: list[Any] | None = None
        if message.voice and link:
            original_date = message_timestamp(message).astimezone().strftime(
                "%d.%m.%Y %H:%M:%S"
            )
            caption, entities = linked_caption(
                message,
                link,
                original_date=original_date,
                author=caption_author,
            )

        for attempt in range(1, retries + 1):
            try:
                if self.target_client is self.client:
                    sent = await self._send_with_user_account(
                        message,
                        link=link,
                        caption=caption,
                        entities=entities,
                    )
                else:
                    sent = await self.target_client.copy_message(
                        source_id,
                        message.id,
                        message,
                        caption=caption,
                        entities=entities,
                    )
                if isinstance(sent, (list, tuple)):
                    sent = sent[0] if sent else None
                sent_id = getattr(sent, "id", None)
                return int(sent_id) if isinstance(sent_id, int) else None
            except FloodWaitError as exc:
                if attempt == retries or exc.seconds > 60:
                    raise
                LOGGER.warning("Telegram-Limit erreicht; warte %s Sekunden", exc.seconds)
                await asyncio.sleep(exc.seconds + 1)
            except (RPCError, TelegramBotApiError):
                if attempt == retries:
                    raise
                await asyncio.sleep(2 ** (attempt - 1))

    async def _send_with_user_account(
        self,
        message: Any,
        *,
        link: str | None,
        caption: str | None,
        entities: list[Any] | None,
    ) -> Any:
        if message.voice and link:
            return await self.client.send_file(
                self.target,
                message.voice,
                caption=caption,
                formatting_entities=entities,
                voice_note=True,
            )
        return await self.client.forward_messages(self.target, message)


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
            await self.process_message(
                job.source_id,
                message,
                assigned_block_id=job.block_id,
            )

    async def catch_up(self) -> None:
        for source in self.sources.values():
            cursor = self.state.cursor(source.id)
            if not self.state.has_cursor(source.id):
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
