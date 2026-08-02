import math
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

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
        load_dotenv()
        phone = os.getenv("TELEGRAM_PHONE", "").strip() or None
        return cls(
            api_id=_integer("TELEGRAM_API_ID", minimum=1),
            api_hash=_required("TELEGRAM_API_HASH"),
            phone=phone,
            session_path=Path(os.getenv("TELEGRAM_SESSION", "data/telegram-monitor")),
            state_db=Path(os.getenv("STATE_DB", "data/forwarder.sqlite3")),
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

    @classmethod
    def from_env(cls) -> "ForwarderConfig":
        base = BaseConfig.from_env()
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
        )
