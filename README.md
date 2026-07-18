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

In this example, messages shorter than 3.5 seconds are ignored, while messages
that are exactly 3.5 seconds long are transferred. The default value `0`
disables the filter.

Telegram IDs for supergroups and channels usually start with `-100`. Configure
private groups without a public username by using their numeric ID.

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
transferred again. The command uses the path configured in `STATE_DB` and does
not require Telegram credentials or a network connection. Stop the running
monitor before resetting its state.

If the source group has Telegram's content protection setting enabled,
Telegram refuses the transfer. The tool logs this error and deliberately does
not attempt to bypass the protection.

Each voice note is published as a single message containing the original text,
the original author's display name, the original timestamp in the local time
zone, and a clickable `Original message` link. If available, the author's public
`@username` is included as well. For posts from anonymous administrators, the
tool uses Telegram's author signature when available.

Telegram's forwarding API does not allow adding a custom caption. The tool
therefore creates a server-side copy using the media reference already stored
by Telegram. The audio file is still neither downloaded nor uploaded again.
Links to private supergroups only work for Telegram users who are members of
the source group. Round video messages are forwarded normally without a link
because Telegram does not support captions on video notes.

## Running continuously

On Linux, the process can run as a systemd service, for example. Make sure the
`.session` file and SQLite database are stored on a persistent volume with
restricted access. A normal process stop with `Ctrl+C` closes the connection
and database cleanly.

## Security and Telegram rules

The session grants access to your Telegram account. Never commit or share it,
and never include it in a public container image. Telegram also notes that
third-party clients are monitored and that abuse such as spam can result in an
account ban. Only use this tool in groups where you are allowed to transfer the
messages.
