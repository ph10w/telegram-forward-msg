# Telegram Voice Forwarder

This tool monitors one or more Telegram groups and transfers new voice messages
to a private channel using your personal Telegram account. It uses Telegram's
MTProto API through Telethon; audio files are never downloaded locally.

## Requirements

- Python 3.10 or newer
- Your account must be a member of the source groups.
- Your account must have permission to post messages in the target channel.
- Consent from the affected group members or another appropriate legal basis
  for forwarding their messages

## Setup

1. Open [my.telegram.org/apps](https://my.telegram.org/apps), register an
   application, and note its `api_id` and `api_hash`.
2. Create and activate a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -e .
   ```

3. Copy `.env.example` to `.env` and enter your credentials:

   ```powershell
   Copy-Item .env.example .env
   ```

4. Sign in and list your available Telegram dialog IDs:

   ```powershell
   python -m telegram_voice_forwarder list-chats
   ```

   On the first run, Telegram asks for your phone number, login code, and, if
   enabled, your two-factor authentication password. The resulting session is
   stored locally at `TELEGRAM_SESSION`. Treat the generated `.session` file
   like a password.

5. Add the source and target IDs to `.env`, then start monitoring:

   ```powershell
   python -m telegram_voice_forwarder run
   ```

Separate multiple source chats with commas:

```dotenv
TELEGRAM_SOURCE_CHATS=-1001234567890,@another_group
TELEGRAM_TARGET_CHAT=-1009876543210
```

You can filter out short voice messages by setting a minimum duration in
seconds. Use a decimal point for fractional values:

```dotenv
MIN_VOICE_DURATION_SECONDS=3.5
```

In this example, a voice message shorter than 3.5 seconds cannot start a new
collection block, while a message that is exactly 3.5 seconds long can. Once a
block is open, every following voice message from the same author is included,
regardless of its duration. The default value `0` disables the filter.

Telegram IDs for supergroups and channels usually start with `-100`. Configure
private groups without a public username by using their numeric ID.

Telethon keeps recently encountered users, chats, and channels in memory. This
project reduces Telethon's default cache limit from 5,000 to 500 entities. You
can adjust it in `.env`, but values below 100 are rejected because an
undersized cache can cause excessive session-database writes:

```dotenv
TELETHON_ENTITY_CACHE_LIMIT=500
```

## Restart behavior

On the first run, the tool inspects the 100 most recent messages in each source
by default. Set `INITIAL_SCAN_LIMIT=0` to start without importing message
history. Afterward, the tool stores the last inspected message ID for each
group. Forwarded messages and failed attempts are persisted in SQLite, which
largely prevents duplicates and allows temporary failures to be retried after a
restart.

To reset the scan state and inspect the latest `INITIAL_SCAN_LIMIT` messages
again on the next run:

```powershell
python -m telegram_voice_forwarder reset
```

This command removes both the scan cursors and the history of forwarded or
ignored messages. Messages found during the next scan can therefore be
transferred again. It also deletes the associated messages created by this
tool in the currently configured `TELEGRAM_TARGET_CHAT`. Stop the running
monitor before resetting its state. The command connects to Telegram and
therefore requires the configured credentials, authorized session, and
permission to delete the account's messages in the target chat.

To reset only a recent time period, append a positive number and `H` (hours),
`D` (days), or `W` (weeks) to `reset`:

```powershell
python -m telegram_voice_forwarder reset=1W
```

This example moves each source cursor back to the last Telegram message before
the one-week cutoff and removes the known-message history after that boundary.
The next monitoring run therefore scans the complete last week, independently
of `INITIAL_SCAN_LIMIT`. Every processed voice message stores its original
Telegram timestamp as `source_message_at`; this timestamp is the primary
criterion for a time-limited reset. If a block's `last_voice_at` falls inside
the reset window, the reset expands to the block's first message and removes
all of its voice messages and its header from the target. This allows the whole
block and its count to be rebuilt consistently. A time-limited reset also
queries Telegram to determine the cursor boundary. Older database rows without
`source_message_at` use their source message ID and that boundary as a
compatibility fallback.

Target-message IDs are stored for new forwards after this feature is installed.
Messages created by older versions do not have this association and cannot be
deleted automatically. The reset reports how many such messages could not be
matched. It also refuses to delete a stored message if its target-chat ID does
not match the currently configured target. If Telegram rejects a deletion, the
local cursor and history are left unchanged.

If the source group has Telegram's content protection setting enabled,
Telegram refuses the transfer. The tool logs this error and deliberately does
not attempt to bypass the protection.

Voice notes are organized into collection blocks. Each block starts with a
separate header containing the author's display name and the number of voice
notes in the block. The header is edited whenever another voice note joins the
current block. If available, the author's public `@username` is included. For
posts from anonymous administrators, the tool uses Telegram's author signature.

A voice note from a different author closes the current block, even if that
note is too short to open a new block. Non-voice messages do not immediately
interrupt a collection: up to four consecutive non-voice messages are allowed.
The fifth closes the block. A voice note that joins the block resets this gap
counter. A block also closes after four hours without an accepted voice note.
The timeout is checked against Telegram's original message timestamps during
historical scans and whenever a new source-channel update is processed.

Each copied voice note preserves its original text and ends with the original
local date and time as a clickable link to the source message. The date is the
link label, so it is not repeated separately.

Telegram's forwarding API does not allow adding a custom caption. The tool
therefore creates a server-side copy using the media reference already stored
by Telegram. The audio file is still neither downloaded nor uploaded again.
Links to private supergroups only work for Telegram users who are members of
the source group. Round video messages are forwarded normally without a link
because Telegram does not support captions on video notes.

## Architecture

Collection rules such as author changes, minimum duration, non-voice gaps, and
the four-hour timeout live in `core.py`. The same module creates explicit reset
plans from neutral snapshots. It has no Telethon or SQLite dependency and can
be tested with plain value objects from `models.py`. Repository contracts live
in `ports.py`; `state.py` is their SQLite adapter and only loads data or applies
explicit decisions atomically. `app.py` translates Telegram updates into core
values and executes the resulting decisions. The reset workflow lives in
`reset_service.py`, while `cli.py` is limited to argument parsing, command
dispatch, and console output.

The call direction is deliberately one-way:

```text
__main__ → cli → bootstrap (composition root)
                       ├─→ monitoring orchestrator → core/ports
                       ├─→ reset use case      → core/ports
                       ├─→ Telegram adapter
                       └─→ SQLite adapter      → core/models

Telegram update → event handler → process message → core decision
                                                       └─→ ports
```

Lower layers never call back into their callers. Use cases never construct or
import concrete adapters; `bootstrap.py` is the only module that wires them
together. Architecture tests reject unapproved internal imports as well as
import, local-function, and class-method call cycles.

## Running continuously

On Linux, the process can run as a systemd service, for example. Make sure the
`.session` file and SQLite database are stored on a persistent volume with
restricted access. A normal process stop with `Ctrl+C` closes the connection
and database cleanly.

### Windows service

Windows cannot run a regular Python console application directly as a native
service. The included installer uses
[Shawl](https://github.com/mtkennerly/shawl) as the service wrapper.

Before installing the service:

1. Install [gsudo](https://gerardog.github.io/gsudo/docs/install) if the script
   should be started from a normal, non-elevated terminal:

   ```powershell
   winget install gerardog.gsudo
   ```

   Restart the terminal after installation. Alternatively, set `GSUDO_EXE` to
   the full path of `gsudo.exe`. gsudo is not required when the terminal is
   already running as Administrator.
2. Download `shawl.exe` from the
   [Shawl releases](https://github.com/mtkennerly/shawl/releases) and place it
   in `tools\shawl.exe`, or make it available through `PATH`. Alternatively,
   set the `SHAWL_EXE` environment variable to its full path.
3. Complete the normal project setup and interactive Telegram login first. A
   service cannot answer the phone-number, login-code, or 2FA prompts.
4. Open Command Prompt or PowerShell in the project directory and run:

   ```powershell
   .\scripts\install-windows-service.bat
   ```

The script installs the `TelegramVoiceForwarder` service, configures automatic
startup and restart behavior, and starts it immediately. It aborts without
changing anything if a service with that name already exists. When necessary,
the complete installer restarts once through gsudo in the current console, so
only one UAC confirmation is needed. The elevated run returns its exit code to
the original process.
Application output is written to rotating `data\logs\service_rCURRENT.log`
files, while Shawl diagnostics are written to
`data\logs\shawl_rCURRENT.log`.

A newly created Shawl service runs as `LocalSystem` by default. Keep `.env` and
the Telegram session protected with restrictive file permissions. If the
service should use a dedicated Windows account, configure that account after
installation in `services.msc`.

Useful service commands:

```powershell
sc.exe query TelegramVoiceForwarder
sc.exe stop TelegramVoiceForwarder
sc.exe start TelegramVoiceForwarder
```

To stop and remove the service, run the included uninstaller from a normal or
elevated terminal:

```powershell
.\scripts\uninstall-windows-service.bat
```

Like the installer, it uses gsudo when elevation is required. It waits for the
service to stop, removes its Windows service registration, and returns success
when the service is already absent. It does not delete the project, `.env`,
Telegram session, SQLite state, or log files.

## Security and Telegram rules

The session grants access to your Telegram account. Never commit or share it,
and never include it in a public container image. Telegram also notes that
third-party clients are monitored and that abuse such as spam can result in an
account ban. Only use this tool in groups where you are allowed to transfer the
messages.
