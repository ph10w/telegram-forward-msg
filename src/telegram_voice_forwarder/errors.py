class TelegramServiceError(RuntimeError):
    """A Telegram adapter operation failed."""


class NotificationBotSetupError(RuntimeError):
    """The Telegram target bot could not be configured safely."""


class TelegramBotApiError(RuntimeError):
    """A Telegram Bot API request failed without exposing its token."""
