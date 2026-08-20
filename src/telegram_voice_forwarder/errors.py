class TelegramServiceError(RuntimeError):
    """A Telegram adapter operation failed."""


class NotificationBotSetupError(RuntimeError):
    """The private Telegram notification bot could not be configured safely."""


class TelegramBotApiError(RuntimeError):
    """A Telegram Bot API request failed without exposing its token."""
