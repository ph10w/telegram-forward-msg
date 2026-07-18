from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .app import list_dialogs, run_forwarder
from .config import BaseConfig, ConfigError, ForwarderConfig
from .state import StateStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telegram-voice-forwarder",
        description="Telegram-Gruppen überwachen und Sprachnachrichten weiterleiten.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("run", "list-chats", "reset"),
        default="run",
        help=(
            "Monitoring starten, Chat-IDs anzeigen oder Scan-Zustand zurücksetzen "
            "(Standard: run)."
        ),
    )
    return parser


def _configure_logging(level: str) -> None:
    numeric_level = getattr(logging, level, None)
    if not isinstance(numeric_level, int):
        raise ConfigError(f"Unbekanntes LOG_LEVEL: {level}")
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def reset_scan_state() -> tuple[Path, int, int]:
    load_dotenv()
    state_db = Path(os.getenv("STATE_DB", "data/forwarder.sqlite3"))
    state = StateStore(state_db)
    try:
        cursor_count, message_count = state.reset()
    finally:
        state.close()
    return state_db, cursor_count, message_count


def main() -> None:
    args = _parser().parse_args()
    try:
        if args.command == "reset":
            state_db, cursor_count, message_count = reset_scan_state()
            print(
                f"Scan-Zustand zurückgesetzt ({state_db}): "
                f"{cursor_count} Cursor und {message_count} bekannte Nachrichten gelöscht."
            )
        elif args.command == "list-chats":
            config = BaseConfig.from_env()
            _configure_logging(config.log_level)
            asyncio.run(list_dialogs(config))
        else:
            config = ForwarderConfig.from_env()
            _configure_logging(config.log_level)
            asyncio.run(run_forwarder(config))
    except (ConfigError, ValueError) as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except KeyboardInterrupt:
        print("Monitoring beendet.")


if __name__ == "__main__":
    main()
