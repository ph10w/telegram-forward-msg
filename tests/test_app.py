import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram_voice_forwarder.app import (
    VoiceForwarder,
    build_client,
    linked_caption,
    message_author,
    telegram_message_link,
)
from telegram_voice_forwarder.config import ForwarderConfig
from telegram_voice_forwarder.state import StateStore


def test_config(root: Path) -> ForwarderConfig:
    return ForwarderConfig(
        api_id=123,
        api_hash="secret",
        phone=None,
        session_path=root / "session",
        state_db=root / "state.sqlite3",
        log_level="INFO",
        entity_cache_limit=500,
        source_chats=(-1001,),
        target_chat=-1002,
        initial_scan_limit=100,
        min_voice_duration_seconds=0,
        include_video_notes=False,
    )


class VoiceForwarderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.state = StateStore(root / "state.sqlite3")
        self.client = SimpleNamespace(
            forward_messages=AsyncMock(),
            send_file=AsyncMock(),
        )
        self.forwarder = VoiceForwarder(self.client, test_config(root), self.state)
        self.forwarder.target = object()

    def test_builds_client_with_configured_entity_cache_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = test_config(Path(temp_dir))
            with patch("telegram_voice_forwarder.app.TelegramClient") as client_class:
                build_client(config)

        self.assertEqual(client_class.call_args.kwargs["entity_cache_limit"], 500)

    async def asyncTearDown(self) -> None:
        self.state.close()
        self.temp_dir.cleanup()

    async def test_forwards_voice_once(self) -> None:
        voice = SimpleNamespace(
            id=11,
            voice=object(),
            video_note=None,
            raw_text="Originaltext",
            entities=[],
            date=datetime(2026, 7, 16, 12, 50, 32, tzinfo=timezone.utc),
            post_author=None,
            sender=SimpleNamespace(
                first_name="Alice",
                last_name="Example",
                username="alice",
            ),
        )

        await self.forwarder.process_message(-1001, voice)
        await self.forwarder.process_message(-1001, voice)

        self.client.send_file.assert_awaited_once()
        args, kwargs = self.client.send_file.await_args
        self.assertEqual(args, (self.forwarder.target, voice.voice))
        self.assertEqual(
            kwargs["caption"],
            "Originaltext\n\n👤 Autor: Alice Example (@alice)\n"
            "🕒 Originaldatum: 16.07.2026 14:50:32\n"
            "🔗 Ursprungsnachricht",
        )
        self.assertEqual(kwargs["formatting_entities"][-1].url, "https://t.me/c/1/11")
        self.assertTrue(kwargs["voice_note"])
        self.client.forward_messages.assert_not_awaited()
        self.assertTrue(self.state.is_complete(-1001, 11))
        self.assertEqual(self.state.cursor(-1001), 11)

    async def test_skips_non_voice_message(self) -> None:
        text = SimpleNamespace(id=12, voice=None, video_note=None)

        await self.forwarder.process_message(-1001, text)

        self.client.forward_messages.assert_not_awaited()
        self.client.send_file.assert_not_awaited()
        self.assertEqual(self.state.cursor(-1001), 12)

    async def test_ignores_voice_shorter_than_configured_minimum(self) -> None:
        root = Path(self.temp_dir.name)
        config = replace(test_config(root), min_voice_duration_seconds=3.0)
        forwarder = VoiceForwarder(self.client, config, self.state)
        forwarder.target = self.forwarder.target
        short_voice = SimpleNamespace(
            id=13,
            voice=object(),
            video_note=None,
            file=SimpleNamespace(duration=2.9),
        )

        await forwarder.process_message(-1001, short_voice)

        self.client.forward_messages.assert_not_awaited()
        self.client.send_file.assert_not_awaited()
        self.assertTrue(self.state.is_complete(-1001, 13))
        self.assertEqual(self.state.cursor(-1001), 13)

    async def test_forwards_voice_equal_to_configured_minimum(self) -> None:
        root = Path(self.temp_dir.name)
        config = replace(test_config(root), min_voice_duration_seconds=3.0)
        forwarder = VoiceForwarder(self.client, config, self.state)
        forwarder.target = self.forwarder.target
        voice = SimpleNamespace(
            id=14,
            voice=object(),
            video_note=None,
            file=SimpleNamespace(duration=3.0),
        )

        await forwarder.process_message(-1001, voice)

        self.client.send_file.assert_awaited_once()

    def test_builds_public_and_private_message_links(self) -> None:
        self.assertEqual(
            telegram_message_link(-1001806942431, 433238),
            "https://t.me/c/1806942431/433238",
        )
        self.assertEqual(
            telegram_message_link(-1001806942431, 433238, username="example"),
            "https://t.me/example/433238",
        )

    def test_linked_caption_keeps_link_with_long_text(self) -> None:
        message = SimpleNamespace(raw_text="x" * 1100, entities=[])

        caption, entities = linked_caption(message, "https://t.me/c/1/2")

        self.assertLessEqual(len(caption), 1024)
        self.assertTrue(caption.endswith("🔗 Ursprungsnachricht"))
        self.assertEqual(entities[-1].url, "https://t.me/c/1/2")

    def test_linked_caption_contains_original_date(self) -> None:
        message = SimpleNamespace(raw_text="Text", entities=[])

        caption, _ = linked_caption(
            message,
            "https://t.me/c/1/2",
            original_date="16.07.2026 14:50:32",
        )

        self.assertIn("🕒 Originaldatum: 16.07.2026 14:50:32", caption)

    async def test_uses_anonymous_admin_signature_as_author(self) -> None:
        message = SimpleNamespace(post_author="Redaktion", sender=None)

        author = await message_author(message)

        self.assertEqual(author, "Redaktion")


if __name__ == "__main__":
    unittest.main()
