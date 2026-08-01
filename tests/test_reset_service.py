import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram_voice_forwarder.config import ForwarderConfig
from telegram_voice_forwarder.reset_service import ResetResult, reset_scan_state
from telegram_voice_forwarder.state import StateStore


class PeriodResetTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_boundaries_and_rewinds_configured_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = ForwarderConfig(
                api_id=123,
                api_hash="secret",
                phone=None,
                session_path=root / "session",
                state_db=root / "state.sqlite3",
                log_level="INFO",
                entity_cache_limit=500,
                source_chats=(-1001, "@second"),
                target_chat=-1003,
                initial_scan_limit=100,
                min_voice_duration_seconds=0,
                include_video_notes=False,
            )
            now = datetime(2026, 8, 1, 12, tzinfo=UTC)
            cutoff = now - timedelta(weeks=1)
            store = StateStore(config.state_db)
            block = store.create_voice_block(
                -1001,
                "sender:42",
                "Alice",
                -1003,
                1080,
                8,
                cutoff - timedelta(days=1),
            )
            for source_id, message_id, target_message_id, block_id, message_at in (
                (-1001, 8, 108, block.id, cutoff - timedelta(days=1)),
                (-1001, 15, 115, block.id, cutoff + timedelta(days=1)),
                (-1002, 25, 125, None, cutoff + timedelta(days=2)),
            ):
                store.mark_pending(
                    source_id,
                    message_id,
                    block_id=block_id,
                    message_at=message_at,
                )
                store.mark_forwarded(
                    source_id,
                    message_id,
                    target_chat_id=-1003,
                    target_message_id=target_message_id,
                    block_id=block_id,
                    message_at=message_at,
                )
            store.close()

            gateway = SimpleNamespace(
                start=AsyncMock(),
                resolve_target=AsyncMock(return_value=-1003),
                boundary_before=AsyncMock(
                    side_effect=((-1001, 10), (-1002, 20))
                ),
                delete_target_messages=AsyncMock(),
                close=AsyncMock(),
            )
            result = await reset_scan_state(
                config,
                timedelta(weeks=1),
                telegram=gateway,
                state=StateStore(config.state_db),
                now=now,
            )

            self.assertEqual(
                result,
                ResetResult(config.state_db, cutoff, 2, 3, 4, 0),
            )
            gateway.start.assert_awaited_once_with()
            gateway.close.assert_awaited_once_with()
            gateway.resolve_target.assert_awaited_once_with(-1003)
            gateway.delete_target_messages.assert_awaited_once_with(
                (108, 115, 125, 1080)
            )
            self.assertEqual(gateway.boundary_before.await_count, 2)
            self.assertEqual(
                gateway.boundary_before.await_args_list[0].args,
                (-1001, cutoff),
            )
            self.assertEqual(
                gateway.boundary_before.await_args_list[1].args,
                ("@second", cutoff),
            )

            store = StateStore(config.state_db)
            try:
                self.assertEqual(store.cursor(-1001), 7)
                self.assertEqual(store.cursor(-1002), 20)
                self.assertFalse(store.is_complete(-1001, 8))
                self.assertFalse(store.is_complete(-1001, 15))
                self.assertFalse(store.is_complete(-1002, 25))
            finally:
                store.close()

    async def test_keeps_local_state_when_target_deletion_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = ForwarderConfig(
                api_id=123,
                api_hash="secret",
                phone=None,
                session_path=root / "session",
                state_db=root / "state.sqlite3",
                log_level="INFO",
                entity_cache_limit=500,
                source_chats=(-1001,),
                target_chat=-1003,
                initial_scan_limit=100,
                min_voice_duration_seconds=0,
                include_video_notes=False,
            )
            store = StateStore(config.state_db)
            store.mark_pending(-1001, 15)
            store.mark_forwarded(
                -1001,
                15,
                target_chat_id=-1003,
                target_message_id=115,
            )
            store.close()

            gateway = SimpleNamespace(
                start=AsyncMock(),
                resolve_target=AsyncMock(return_value=-1003),
                boundary_before=AsyncMock(),
                delete_target_messages=AsyncMock(
                    side_effect=RuntimeError("denied")
                ),
                close=AsyncMock(),
            )
            with self.assertRaisesRegex(RuntimeError, "denied"):
                await reset_scan_state(
                    config,
                    telegram=gateway,
                    state=StateStore(config.state_db),
                )

            gateway.close.assert_awaited_once_with()
            store = StateStore(config.state_db)
            try:
                self.assertEqual(store.cursor(-1001), 15)
                self.assertTrue(store.is_complete(-1001, 15))
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
