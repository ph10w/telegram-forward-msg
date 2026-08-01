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

SQLite stores scan cursors, forwarding history, pending retries, and collection
blocks. A restart therefore continues from the last processed message. Only the
first scan uses `INITIAL_SCAN_LIMIT`; set it to `0` to skip initial history.

Reset all known messages, or only a recent period, with:

```powershell
python -m telegram_voice_forwarder reset
python -m telegram_voice_forwarder reset=1W
```

Periods accept `H`, `D`, or `W`. A time-limited reset uses original Telegram
timestamps and includes complete collection blocks. Both reset modes remove
known history and safely tracked target messages before changing local state;
older unmatched target messages are reported. Stop the monitor before running
a reset.

## Architecture

Business rules live in the dependency-free `core.py`; Telegram and SQLite are
adapters behind contracts from `ports.py`. `bootstrap.py` wires the application
together, while `cli.py` only handles commands. Architecture tests enforce the
one-way dependency and call hierarchy.

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
