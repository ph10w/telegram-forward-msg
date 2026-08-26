import logging
from datetime import timedelta
from pathlib import Path

from telethon.errors import RPCError

from .app import VoiceForwarder
from .bot_api import BotApi
from .bot_relay_adapter import BotRelayClient, BotResetGateway
from .config import BaseConfig, ChatRef, ForwarderConfig
from .errors import TelegramBotApiError, TelegramServiceError
from .models import DialogInfo
from .notification_bot_setup import setup_notification_bot as configure_notification_bot
from .reset_service import ResetResult, reset_scan_state
from .state import StateStore
from .telegram_adapter import (
    build_client,
    load_dialogs,
    start_client,
)

LOGGER = logging.getLogger(__name__)


def setup_notification_bot(env_path: Path) -> None:
    configure_notification_bot(env_path)


async def run_monitoring(config: ForwarderConfig) -> None:
    client = build_client(config)
    state = StateStore(config.state_db)
    try:
        name, user_id = await start_client(client, config)
        LOGGER.info("Angemeldet als %s (ID %s)", name, user_id)
        target_client = BotRelayClient(
            BotApi(config.notification_bot_token),
            client,
            config.target_chat,
        )
        await target_client.start(user_id)
        await VoiceForwarder(client, config, state, target_client).run()
    except (RPCError, TelegramBotApiError) as exc:
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
    except (RPCError, TelegramBotApiError) as exc:
        raise TelegramServiceError(str(exc)) from exc
    finally:
        await client.disconnect()


async def reset_forwarder(
    config: ForwarderConfig,
    period: timedelta | None,
    source_chat: ChatRef | None = None,
) -> ResetResult:
    client = build_client(config)
    gateway = BotResetGateway(
        client,
        config,
        BotApi(config.notification_bot_token),
        config.target_chat,
    )
    state = StateStore(config.state_db)
    try:
        return await reset_scan_state(
            config,
            period,
            source_chat=source_chat,
            telegram=gateway,
            state=state,
        )
    except (RPCError, TelegramBotApiError) as exc:
        raise TelegramServiceError(str(exc)) from exc
