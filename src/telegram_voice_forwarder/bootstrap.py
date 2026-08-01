from __future__ import annotations

import logging
from datetime import timedelta

from telethon.errors import RPCError

from .app import VoiceForwarder
from .config import BaseConfig, ForwarderConfig
from .errors import TelegramServiceError
from .models import DialogInfo
from .reset_service import ResetResult, reset_scan_state
from .state import StateStore
from .telegram_adapter import (
    TelethonResetGateway,
    build_client,
    load_dialogs,
    start_client,
)

LOGGER = logging.getLogger(__name__)


async def run_monitoring(config: ForwarderConfig) -> None:
    client = build_client(config)
    state = StateStore(config.state_db)
    try:
        name, user_id = await start_client(client, config)
        LOGGER.info("Angemeldet als %s (ID %s)", name, user_id)
        await VoiceForwarder(client, config, state).run()
    except RPCError as exc:
        raise TelegramServiceError(str(exc)) from exc
    finally:
        state.close()
        await client.disconnect()


async def list_available_chats(config: BaseConfig) -> tuple[DialogInfo, ...]:
    client = build_client(config)
    try:
        name, user_id = await start_client(client, config)
        LOGGER.info("Angemeldet als %s (ID %s)", name, user_id)
        return await load_dialogs(client)
    except RPCError as exc:
        raise TelegramServiceError(str(exc)) from exc
    finally:
        await client.disconnect()


async def reset_forwarder(
    config: ForwarderConfig,
    period: timedelta | None,
) -> ResetResult:
    client = build_client(config)
    gateway = TelethonResetGateway(client, config)
    state = StateStore(config.state_db)
    try:
        return await reset_scan_state(
            config,
            period,
            telegram=gateway,
            state=state,
        )
    except RPCError as exc:
        raise TelegramServiceError(str(exc)) from exc
