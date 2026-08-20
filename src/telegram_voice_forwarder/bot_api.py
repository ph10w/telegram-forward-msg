import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .errors import TelegramBotApiError

REQUEST_TIMEOUT_SECONDS = 40


class BotApi:
    def __init__(self, token: str) -> None:
        self._base_url = f"https://api.telegram.org/bot{token}/"

    def call(self, method: str, **parameters: object) -> Any:
        encoded = urlencode(
            {
                key: json.dumps(value) if isinstance(value, (list, dict)) else value
                for key, value in parameters.items()
                if value is not None
            }
        ).encode("utf-8")
        request = Request(
            self._base_url + method,
            data=encoded,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.load(response)
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                description = payload.get("description", "Telegram API error")
            except (UnicodeDecodeError, json.JSONDecodeError):
                description = f"Telegram API returned HTTP {exc.code}"
            raise TelegramBotApiError(str(description)) from None
        except URLError as exc:
            raise TelegramBotApiError(
                f"Telegram API is unavailable: {exc.reason}"
            ) from None
        except json.JSONDecodeError:
            raise TelegramBotApiError("Telegram API returned invalid JSON") from None

        if not isinstance(payload, dict) or not payload.get("ok"):
            description = (
                payload.get("description", "Telegram API error")
                if isinstance(payload, dict)
                else "Telegram API returned an invalid response"
            )
            raise TelegramBotApiError(str(description))
        return payload.get("result")
