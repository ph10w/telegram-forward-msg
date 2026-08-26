"""Configure the Telegram bot that publishes into the target chat."""

import getpass
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from .bot_api import BotApi
from .errors import NotificationBotSetupError, TelegramBotApiError

TOKEN_VARIABLE = "TELEGRAM_NOTIFICATION_BOT_TOKEN"
CHAT_ID_VARIABLE = "TELEGRAM_NOTIFICATION_CHAT_ID"
TARGET_VARIABLE = "TELEGRAM_TARGET_CHAT"
POLL_TIMEOUT_SECONDS = 30
SETUP_TIMEOUT_SECONDS = 180


def _configured_token(env_path: Path) -> str | None:
    environment_token = os.getenv(TOKEN_VARIABLE, "").strip()
    if environment_token:
        return environment_token
    value = dotenv_values(env_path).get(TOKEN_VARIABLE)
    return str(value).strip() if value else None


def _read_token(env_path: Path) -> str:
    existing = _configured_token(env_path)
    if existing:
        answer = input(f"Use the existing {TOKEN_VARIABLE} from .env? [Y/n] ").strip()
        if answer.lower() not in {"n", "no"}:
            return existing

    token = getpass.getpass(
        "Open the verified @BotFather in Telegram, send /newbot, and complete "
        "the bot creation.\nPaste the returned HTTP API token "
        "(input is hidden): "
    ).strip()
    if not token:
        raise NotificationBotSetupError("No bot token was entered.")
    return token


def _latest_update_offset(bot: BotApi) -> int | None:
    updates = bot.call("getUpdates", timeout=0, limit=100) or []
    if not updates:
        return None
    return max(int(update["update_id"]) for update in updates) + 1


def _matching_private_chat(update: dict[str, Any], start_parameter: str) -> int | None:
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict) or chat.get("type") != "private":
        return None
    text = str(message.get("text", ""))
    if text != f"/start {start_parameter}":
        return None
    chat_id = chat.get("id")
    return int(chat_id) if isinstance(chat_id, int) else None


def _wait_for_private_chat(
    bot: BotApi,
    *,
    offset: int | None,
    start_parameter: str,
) -> int:
    deadline = time.monotonic() + SETUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        updates = bot.call(
            "getUpdates",
            offset=offset,
            timeout=POLL_TIMEOUT_SECONDS,
            allowed_updates=["message"],
        ) or []
        for update in updates:
            offset = int(update["update_id"]) + 1
            chat_id = _matching_private_chat(update, start_parameter)
            if chat_id is not None:
                return chat_id
    raise NotificationBotSetupError(
        "No matching private /start message was received within 3 minutes."
    )


def _updated_env(
    contents: str,
    values: dict[str, str],
    *,
    remove: frozenset[str] = frozenset(),
) -> str:
    remaining = dict(values)
    output: list[str] = []
    for line in contents.splitlines(keepends=True):
        stripped = line.lstrip()
        if any(stripped.startswith(f"{name}=") for name in remove):
            continue
        replaced = False
        for name in tuple(remaining):
            if stripped.startswith(f"{name}="):
                newline = "\r\n" if line.endswith("\r\n") else "\n"
                output.append(f"{name}={remaining.pop(name)}{newline}")
                replaced = True
                break
        if not replaced:
            output.append(line)

    if remaining:
        if output and not output[-1].endswith(("\n", "\r")):
            output[-1] += "\n"
        if output and output[-1].strip():
            output.append("\n")
        output.append("# Bot publisher for TELEGRAM_TARGET_CHAT.\n")
        output.extend(f"{name}={value}\n" for name, value in remaining.items())
    return "".join(output)


def _write_env(
    env_path: Path,
    values: dict[str, str],
    *,
    remove: frozenset[str] = frozenset(),
) -> None:
    original = env_path.read_text(encoding="utf-8")
    updated = _updated_env(original, values, remove=remove)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=env_path.parent,
            prefix=f".{env_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(updated)
            temporary_path = Path(temporary.name)
        temporary_path.replace(env_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def setup_notification_bot(env_path: Path) -> None:
    try:
        if not env_path.is_file():
            raise NotificationBotSetupError(
                "Run this command from the project directory containing .env."
            )

        token = _read_token(env_path)
        bot = BotApi(token)
        identity = bot.call("getMe")
        username = identity.get("username") if isinstance(identity, dict) else None
        bot_id = identity.get("id") if isinstance(identity, dict) else None
        if not username:
            raise NotificationBotSetupError(
                "Telegram returned no username for this bot."
            )
        if not isinstance(bot_id, int):
            raise NotificationBotSetupError("Telegram returned no ID for this bot.")

        offset = _latest_update_offset(bot)
        start_parameter = secrets.token_urlsafe(18)
        print(f"\nOpen this link and press Start:\nhttps://t.me/{username}?start={start_parameter}")
        print("Waiting up to 3 minutes for the private chat ...")
        _wait_for_private_chat(
            bot,
            offset=offset,
            start_parameter=start_parameter,
        )

        target_value = dotenv_values(env_path).get(TARGET_VARIABLE)
        if not target_value:
            raise NotificationBotSetupError(
                f"{TARGET_VARIABLE} is missing from .env."
            )
        target_chat: int | str
        try:
            target_chat = int(str(target_value).strip())
        except ValueError:
            target_chat = str(target_value).strip()
        target = bot.call("getChat", chat_id=target_chat)
        membership = bot.call(
            "getChatMember",
            chat_id=target_chat,
            user_id=bot_id,
        )
        status = membership.get("status") if isinstance(membership, dict) else None
        if status not in {"administrator", "creator"}:
            raise NotificationBotSetupError(
                "Add the bot to TELEGRAM_TARGET_CHAT as an administrator and rerun setup."
            )
        if (
            isinstance(target, dict)
            and target.get("type") == "channel"
            and not membership.get("can_post_messages")
        ):
            raise NotificationBotSetupError(
                "Grant the bot permission to post messages in TELEGRAM_TARGET_CHAT."
            )

        _write_env(
            env_path,
            {TOKEN_VARIABLE: token},
            remove=frozenset({CHAT_ID_VARIABLE}),
        )
        print("Configuration saved. The bot can publish into TELEGRAM_TARGET_CHAT.")
    except TelegramBotApiError as exc:
        raise NotificationBotSetupError(str(exc)) from exc
    except OSError as exc:
        raise NotificationBotSetupError(f"Could not update .env: {exc}") from exc
