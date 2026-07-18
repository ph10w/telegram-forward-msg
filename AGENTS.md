# AGENTS.md

These instructions apply to the entire repository.

## Project overview

Telegram Voice Forwarder is a Python 3.10+ service that signs in through a
personal Telegram account with Telethon. It monitors configured source groups,
filters voice messages, and publishes matching messages to a private target
channel.

The service uses Telegram media references. Do not introduce unnecessary media
downloads or uploads.

## Repository layout

- `src/telegram_voice_forwarder/app.py`: Telegram client, message filtering,
  caption construction, catch-up scanning, and live event handling
- `src/telegram_voice_forwarder/config.py`: environment configuration and
  validation
- `src/telegram_voice_forwarder/state.py`: SQLite cursors, deduplication, and
  retry state
- `src/telegram_voice_forwarder/cli.py`: `run`, `list-chats`, and `reset`
  commands
- `tests/`: unit tests for configuration, message processing, captions, and
  persistent state
- `.env.example`: documented configuration without real credentials

## Development setup

Use a virtual environment and install the package in editable mode:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Run the complete test suite before committing:

```powershell
python -m unittest discover -s tests -v
```

## Behavioral requirements

- Treat `MIN_VOICE_DURATION_SECONDS` as an inclusive threshold: shorter voice
  messages are ignored; messages exactly at the threshold are accepted.
- `INITIAL_SCAN_LIMIT` only controls the first scan after the cursor has been
  reset.
- The `reset` command removes both scan cursors and known-message history so
  eligible messages can be processed again.
- Preserve the original text, author, local timestamp, and clickable source
  link in the copied voice-message caption.
- Preserve Telegram UTF-16 entity offsets when changing captions.
- Private supergroup links use `https://t.me/c/<internal-id>/<message-id>` and
  only work for users who belong to the source group.
- Voice notes are copied through their existing Telegram media reference so a
  custom caption can be added. Round video messages are forwarded normally
  because Telegram does not support captions on video notes.
- Do not advance state in a way that loses failed jobs. Processing must remain
  restart-safe and avoid duplicates where Telegram API semantics permit it.
- Do not bypass Telegram content-protection settings.

## Security rules

- Never commit `.env`, `.session` files, SQLite databases, login codes, phone
  numbers, `api_hash` values, or other account credentials.
- Treat the Telegram session file as a password.
- Do not print message contents, credentials, or session material in logs or
  tests.
- Keep runtime data under the ignored `data/` directory.
- Any live Telegram test that posts, edits, forwards, or deletes a message must
  be explicitly authorized. Prefer unit tests with mocked clients.

## Change guidelines

- Keep the asynchronous event loop non-blocking.
- Validate new environment values in `config.py`; document them in both
  `.env.example` and `README.md`.
- Put durable processing state in `StateStore` and cover schema or state-machine
  changes with tests.
- Add regression tests for filtering boundaries, caption entities, retry
  behavior, and reset semantics when those areas change.
- Keep compatibility with the stable Telethon 1.x range declared in
  `pyproject.toml` unless an intentional migration updates code and tests.
- Run `git diff --check` and the complete test suite before publishing changes.
