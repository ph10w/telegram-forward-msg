import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

type ChatRef = int | str


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


def parse_chat_ref(value: str) -> ChatRef:
    value = value.strip()
    if not value:
        raise ConfigError("Eine leere Telegram-Chat-Referenz ist nicht erlaubt.")
    try:
        return int(value)
    except ValueError:
        return value


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Die Umgebungsvariable {name} fehlt.")
    return value


def _integer(name: str, default: int | None = None, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        if default is None:
            raise ConfigError(f"Die Umgebungsvariable {name} fehlt.")
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} muss eine ganze Zahl sein.") from exc
    if value < minimum:
        raise ConfigError(f"{name} muss mindestens {minimum} sein.")
    return value


def _number(name: str, default: float = 0.0, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} muss eine Zahl sein.") from exc
    if not math.isfinite(value) or value < minimum:
        raise ConfigError(f"{name} muss mindestens {minimum:g} sein.")
    return value


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} muss true oder false sein.")


def _load_environment() -> Path | None:
    dotenv_file = find_dotenv()
    if not dotenv_file:
        load_dotenv()
        return None

    dotenv_path = Path(dotenv_file).resolve()
    load_dotenv(dotenv_path)
    return dotenv_path.parent


def _runtime_path(name: str, default: str, dotenv_directory: Path | None) -> Path:
    path = Path(os.getenv(name, default))
    if path.is_absolute():
        return path

    current_directory = Path.cwd().resolve()
    if dotenv_directory is None:
        raise ConfigError(
            f"{name} ist relativ ({path}), aber in {current_directory} wurde keine "
            ".env gefunden. Führe den Befehl aus dem Verzeichnis der .env aus "
            "oder verwende einen absoluten Pfad."
        )
    if current_directory != dotenv_directory:
        raise ConfigError(
            f"{name} ist relativ ({path}), aber die geladene .env liegt in "
            f"{dotenv_directory}. Führe den Befehl aus diesem Verzeichnis aus "
            "oder verwende einen absoluten Pfad."
        )
    return path


def _notification_settings() -> tuple[str | None, int | None]:
    token = os.getenv("TELEGRAM_NOTIFICATION_BOT_TOKEN", "").strip() or None
    raw_chat_id = os.getenv("TELEGRAM_NOTIFICATION_CHAT_ID", "").strip() or None
    if token is None and raw_chat_id is None:
        return None, None
    if token is None or raw_chat_id is None:
        raise ConfigError(
            "TELEGRAM_NOTIFICATION_BOT_TOKEN und TELEGRAM_NOTIFICATION_CHAT_ID "
            "müssen gemeinsam gesetzt sein."
        )
    try:
        chat_id = int(raw_chat_id)
    except ValueError as exc:
        raise ConfigError(
            "TELEGRAM_NOTIFICATION_CHAT_ID muss eine positive ganze Zahl sein."
        ) from exc
    if chat_id <= 0:
        raise ConfigError(
            "TELEGRAM_NOTIFICATION_CHAT_ID muss die positive ID eines privaten "
            "Bot-Chats sein."
        )
    return token, chat_id


@dataclass(frozen=True, slots=True)
class BaseConfig:
    api_id: int
    api_hash: str
    phone: str | None
    session_path: Path
    state_db: Path
    log_level: str
    entity_cache_limit: int

    @classmethod
    def from_env(cls) -> "BaseConfig":
        dotenv_directory = _load_environment()
        session_path = _runtime_path(
            "TELEGRAM_SESSION", "data/telegram-monitor", dotenv_directory
        )
        state_db = _runtime_path("STATE_DB", "data/forwarder.sqlite3", dotenv_directory)
        phone = os.getenv("TELEGRAM_PHONE", "").strip() or None
        return cls(
            api_id=_integer("TELEGRAM_API_ID", minimum=1),
            api_hash=_required("TELEGRAM_API_HASH"),
            phone=phone,
            session_path=session_path,
            state_db=state_db,
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            entity_cache_limit=_integer("TELETHON_ENTITY_CACHE_LIMIT", 500, minimum=100),
        )


@dataclass(frozen=True, slots=True)
class ForwarderConfig(BaseConfig):
    source_chats: tuple[ChatRef, ...]
    target_chat: ChatRef
    initial_scan_limit: int
    min_voice_duration_seconds: float
    include_video_notes: bool
    notification_bot_token: str | None = field(default=None, repr=False)
    notification_chat_id: int | None = None

    @classmethod
    def from_env(cls) -> "ForwarderConfig":
        base = BaseConfig.from_env()
        notification_bot_token, notification_chat_id = _notification_settings()
        raw_sources = _required("TELEGRAM_SOURCE_CHATS")
        sources = tuple(parse_chat_ref(item) for item in raw_sources.split(",") if item.strip())
        if not sources:
            raise ConfigError("TELEGRAM_SOURCE_CHATS enthält keine Quelle.")
        return cls(
            api_id=base.api_id,
            api_hash=base.api_hash,
            phone=base.phone,
            session_path=base.session_path,
            state_db=base.state_db,
            log_level=base.log_level,
            entity_cache_limit=base.entity_cache_limit,
            source_chats=sources,
            target_chat=parse_chat_ref(_required("TELEGRAM_TARGET_CHAT")),
            initial_scan_limit=_integer("INITIAL_SCAN_LIMIT", 100),
            min_voice_duration_seconds=_number("MIN_VOICE_DURATION_SECONDS"),
            include_video_notes=_boolean("INCLUDE_VIDEO_NOTES", False),
            notification_bot_token=notification_bot_token,
            notification_chat_id=notification_chat_id,
        )
