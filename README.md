# Telegram Voice Forwarder

This tool monitors one or more Telegram chats and transfers new voice messages
to a private channel using your personal Telegram account. It uses Telegram's
MTProto API through Telethon; audio files are never downloaded locally.

## Requirements

- Python 3.13 or newer
- Your account must be a member of the source chats.
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

Run commands from the directory containing `.env`. If `TELEGRAM_SESSION` or
`STATE_DB` is relative and `.env` was loaded from another directory, the tool
stops with a configuration error to prevent using the wrong runtime data.

Separate multiple source chats with commas:

```dotenv
TELEGRAM_SOURCE_CHATS=-1001234567890,@another_group
TELEGRAM_TARGET_CHAT=-1009876543210
```

Configure the voice-duration threshold in seconds with
`MIN_VOICE_DURATION_SECONDS`. Use a decimal point for fractional values; `0`
disables the threshold:

```dotenv
MIN_VOICE_DURATION_SECONDS=3.5
```

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

SQLite stores the processing state, so a restart continues from the last
processed message. Only the first scan uses `INITIAL_SCAN_LIMIT`; set it to `0`
to skip initial history.

Reset all known messages, or only a recent period, with:

```powershell
python -m telegram_voice_forwarder reset
python -m telegram_voice_forwarder reset=1W
python -m telegram_voice_forwarder reset --source=-1001234567890
python -m telegram_voice_forwarder reset=1W --source=-1001234567890
```

Periods accept `H`, `D`, or `W`. A time-limited reset uses original Telegram
timestamps. Both reset modes remove known history and safely tracked target
messages before changing local state; older unmatched target messages are
reported. Stop the monitor before running a reset. Use `--source=CHAT` with a
numeric ID or username to limit either reset mode to one source chat; state and
target messages belonging to other sources remain untouched.

## Architecture

Business rules live in the dependency-free `core.py`; Telegram and SQLite are
adapters behind contracts from `ports.py`. `bootstrap.py` wires the application
together, while `cli.py` only handles commands. Architecture tests enforce the
one-way dependency and call hierarchy.

## Running continuously

Keep `.env`, the Telegram session, and SQLite state persistent and protected.

### Raspberry Pi OS service

Configure `.env`, then install and start the systemd service:

```bash
bash scripts/install-raspberry-pi-service.sh
```

The installer adds missing APT dependencies, requires Python 3.13+, creates the
virtual environment, performs an interactive Telegram login when needed, and
enables automatic startup. It uses the current user, or `SUDO_USER` when run
through `sudo`; override this with `SERVICE_USER=pi`.

```bash
sudo systemctl status telegram-voice-forwarder.service
sudo journalctl -u telegram-voice-forwarder.service -f
```

### Windows service

The Windows installer uses [Shawl](https://github.com/mtkennerly/shawl). Before
running it:

1. Complete setup and the interactive Telegram login.
2. Put `shawl.exe` in `tools\`, `PATH`, or `SHAWL_EXE`.
3. From a non-elevated terminal, install
   [gsudo](https://gerardog.github.io/gsudo/docs/install):

   ```powershell
   winget install gerardog.gsudo
   ```

Install or uninstall the service from the project directory:

```powershell
.\scripts\install-windows-service.bat
.\scripts\uninstall-windows-service.bat
```

The service starts automatically and restarts after failures. It runs as
`LocalSystem` by default; select another account in `services.msc` if needed.

```powershell
sc.exe query TelegramVoiceForwarder
sc.exe stop TelegramVoiceForwarder
sc.exe start TelegramVoiceForwarder
```

Application and Shawl logs are stored in `data\logs\service_rCURRENT.log` and
`data\logs\shawl_rCURRENT.log`. Uninstalling preserves all project and runtime
data.

## Security and Telegram rules

The session grants access to your Telegram account. Never commit or share it,
and never include it in a public container image. Telegram also notes that
third-party clients are monitored and that abuse such as spam can result in an
account ban. Only use this tool in groups where you are allowed to transfer the
messages.
