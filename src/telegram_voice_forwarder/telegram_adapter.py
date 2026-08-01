from __future__ import annotations

from datetime import datetime
from typing import Any

from telethon import TelegramClient, utils

from .config import BaseConfig
from .models import DialogInfo


def build_client(config: BaseConfig) -> TelegramClient:
    config.session_path.parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(
        str(config.session_path),
        config.api_id,
        config.api_hash,
        auto_reconnect=True,
        connection_retries=None,
        retry_delay=2,
        entity_cache_limit=config.entity_cache_limit,
    )


async def start_client(client: TelegramClient, config: BaseConfig) -> tuple[str, int]:
    await client.start(phone=config.phone)
    me = await client.get_me()
    return utils.get_display_name(me), me.id


async def load_dialogs(client: TelegramClient) -> tuple[DialogInfo, ...]:
    dialogs: list[DialogInfo] = []
    async for dialog in client.iter_dialogs():
        if dialog.is_group:
            kind = "Gruppe"
        elif dialog.is_channel:
            kind = "Kanal"
        elif dialog.is_user:
            kind = "Benutzer"
        else:
            kind = "Sonstiges"
        dialogs.append(DialogInfo(dialog.id, kind, dialog.name))
    return tuple(dialogs)


class TelethonResetGateway:
    def __init__(self, client: TelegramClient, config: BaseConfig) -> None:
        self._client = client
        self._config = config
        self._target: Any = None

    async def start(self) -> None:
        await start_client(self._client, self._config)
        await self._client.get_dialogs()

    async def resolve_target(self, reference: int | str) -> int:
        self._target = await self._client.get_entity(reference)
        return utils.get_peer_id(self._target)

    async def boundary_before(
        self, reference: int | str, cutoff: datetime
    ) -> tuple[int, int]:
        entity = await self._client.get_entity(reference)
        source_id = utils.get_peer_id(entity)
        messages = await self._client.get_messages(
            entity, limit=1, offset_date=cutoff
        )
        return source_id, messages[0].id if messages else 0

    async def delete_target_messages(self, message_ids: tuple[int, ...]) -> None:
        if self._target is None:
            raise RuntimeError("Zielchat wurde nicht aufgelöst.")
        for index in range(0, len(message_ids), 100):
            await self._client.delete_messages(
                self._target,
                list(message_ids[index : index + 100]),
                revoke=True,
            )

    async def close(self) -> None:
        await self._client.disconnect()
