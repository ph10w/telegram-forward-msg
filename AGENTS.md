# AGENTS.md

These instructions apply to the entire repository.

## Project overview

Telegram Voice Forwarder is a Python 3.13+ service that signs in through a
personal Telegram account with Telethon. It monitors configured source groups,
filters voice messages, and publishes matching messages to a private target
channel.

The service uses Telegram media references. Do not introduce unnecessary media
downloads or uploads.

## Repository layout

- `src/telegram_voice_forwarder/app.py`: message filtering, caption
  construction, application orchestration, catch-up scanning, and live event
  handling through an injected Telegram client
- `src/telegram_voice_forwarder/core.py`: pure collection-block policy and
  reset planning and domain decisions without Telegram or SQLite dependencies
- `src/telegram_voice_forwarder/models.py`: neutral persisted/domain value
  objects shared across ports, policies, and adapters
- `src/telegram_voice_forwarder/ports.py`: repository protocols used by the
  application and reset services
- `src/telegram_voice_forwarder/config.py`: environment configuration and
  validation
- `src/telegram_voice_forwarder/state.py`: SQLite schema, migrations, mapping,
  queries, and atomic application of explicit state-transition/reset plans
- `src/telegram_voice_forwarder/reset_service.py`: reset workflow coordinating
  injected Telegram and persistent-state ports
- `src/telegram_voice_forwarder/telegram_adapter.py`: Telethon client creation,
  dialog access, and reset-gateway implementation
- `src/telegram_voice_forwarder/bootstrap.py`: composition root; the only place
  where use cases and concrete adapters are wired together
- `src/telegram_voice_forwarder/cli.py`: argument parsing, command dispatch, and
  user-facing command output
- `tests/`: unit tests for configuration, message processing, captions, and
  persistent state
- `.env.example`: documented configuration without real credentials

## Design decisions

- Keep domain models and decisions in `models.py` and `core.py`. They must stay
  deterministic and independent of Telethon, SQLite, files, environment
  variables, and the event loop.
- Keep all dependency and call directions one-way: `__main__ -> cli ->
  bootstrap -> use cases/adapters -> core/ports/models`. Telegram events enter
  through a handler and flow toward message processing, policies, and ports;
  lower layers never call back into their callers.
- Treat `bootstrap.py` as the composition root and the only place that creates
  and connects concrete adapters. Use cases receive dependencies through the
  protocols in `ports.py`.
- Keep persistence policy-free. `state.py` loads neutral snapshots and applies
  explicit state-transition or reset plans atomically; it must not decide which
  messages or blocks a workflow affects.
- Build reset decisions from persisted original Telegram timestamps. A reset
  expands to complete collection blocks, deletes safely tracked remote messages
  before local history, and leaves local state unchanged if remote deletion
  fails.
- Copy voice notes through Telegram's existing server-side media reference so
  captions can be added without downloading or uploading the audio. Respect
  Telegram content protection and caption/entity constraints.
- Enforce these boundaries with `tests/test_architecture.py`, including allowed
  imports and the absence of import, local-function, and class-method cycles.

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

- Treat `MIN_VOICE_DURATION_SECONDS` as an inclusive threshold for the first
  voice message in a collection block. Once a block is open, subsequent voice
  messages from the same author bypass the minimum-duration filter.
- Keep collection blocks per source chat. A voice message from another author
  closes the active block regardless of duration. Non-voice messages close it
  only after five consecutive occurrences; a joining voice message resets that
  count. Four hours without an accepted voice message also closes the block;
  use Telegram's message timestamps so catch-up scans behave like live traffic.
- Classify every resolved source as a basic group, supergroup, or broadcast
  channel. Allow collection blocks only for basic groups and supergroups.
  Transfer channel voice messages individually with author, text, timestamp,
  and monitored-source link in the same caption.
- Persist collection headers, counts, and active/closed state in `StateStore`
  so blocks continue consistently after a restart.
- Persist `duration_seconds` for every observed voice-message job. Treat it as
  required for successfully transferred, non-forwarded jobs that can serve as
  internal-forward origin candidates.
- `INITIAL_SCAN_LIMIT` only controls the first scan after the cursor has been
  reset.
- The `reset` command removes both scan cursors and known-message history so
  eligible messages can be processed again. It deletes safely tracked messages
  from the currently configured target before changing local state; if a
  Telegram deletion fails, local state must remain intact.
- A reset limited with `--source` must select jobs, blocks, cursors, forwarded
  origin aliases, and target messages only from that source. A full scoped reset
  deletes that source's cursor so `INITIAL_SCAN_LIMIT` applies again; a scoped
  period reset rewinds only that source.
- A time-limited reset rewinds each configured source to the message before
  cutoff and uses each job's persisted original `source_message_at` as its
  primary inclusion criterion. If a block's `last_voice_at` is inside the reset
  window, expand the reset to include the complete block and its target header.
  Rows created by older versions without timestamps use the Telegram-derived
  message-ID boundary as a fallback. A reset must never advance an older cursor,
  and the subsequent catch-up must not be limited by `INITIAL_SCAN_LIMIT`.
- Put the author and editable voice-message count in the collection header.
  Preserve the original text in each copied voice-message caption and use its
  local timestamp as the clickable source-link label.
- Treat voice messages with `fwd_from` as standalone messages. They close any
  active collection block, never join or create a block, and never receive a
  collection header. Persist and display the original forwarded author and
  `fwd_from.date`, while the clickable link continues to target the message in
  the monitored source chat. Deduplicate across source chats when Telegram
  exposes `(original_chat_id, original_message_id)`, scoped to the configured
  target chat. If Telegram omits both IDs for an internal forward, infer
  `(source_id, original_message_id)` only from exactly one earlier successfully
  transferred non-forwarded message in that source with the same original
  author, exact Telegram timestamp, and voice-message duration. Do not infer
  from media IDs, and process ambiguous matches normally.
- Treat `forwarding_jobs.duration_seconds` as part of the current database
  contract. Fresh databases create it directly. Legacy databases must be
  backfilled from Telegram in a separate operational migration before running
  code that relies on it; do not add an inference fallback for missing values.
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
- Keep relative runtime paths anchored operationally by requiring commands to
  run from the directory containing the loaded `.env`; fail configuration
  validation on a mismatch instead of silently rebasing paths.
- Any live Telegram test that posts, edits, forwards, or deletes a message must
  be explicitly authorized. Prefer unit tests with mocked clients.

## Change guidelines

- Keep the asynchronous event loop non-blocking.
- Preserve the design boundaries above. Update `tests/test_architecture.py`
  intentionally when adding a module or a permitted dependency edge.
- Validate new environment values in `config.py`; document them in both
  `.env.example` and `README.md`.
- Put durable processing state in `StateStore` and cover schema or state-machine
  changes with tests.
- Add regression tests for filtering boundaries, caption entities, retry
  behavior, and reset semantics when those areas change.
- Keep compatibility with the stable Telethon 1.x range declared in
  `pyproject.toml` unless an intentional migration updates code and tests.
- Run `git diff --check` and the complete test suite before publishing changes.
