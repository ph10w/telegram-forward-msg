import tempfile
import unittest
from pathlib import Path

from telegram_voice_forwarder.state import StateStore


class StateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.temp_dir.name) / "state.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_cursor_only_moves_forward(self) -> None:
        self.store.advance_cursor(-1001, 10)
        self.store.advance_cursor(-1001, 4)
        self.assertEqual(self.store.cursor(-1001), 10)

    def test_failed_job_can_be_completed(self) -> None:
        self.store.mark_pending(-1001, 7)
        self.store.mark_failed(-1001, 7, "temporary")
        self.assertEqual(len(self.store.pending_jobs()), 1)

        self.store.mark_pending(-1001, 7)
        self.store.mark_forwarded(-1001, 7)

        self.assertTrue(self.store.is_complete(-1001, 7))
        self.assertEqual(self.store.pending_jobs(), [])
        self.assertEqual(self.store.cursor(-1001), 7)

    def test_reset_removes_cursors_and_forwarding_history(self) -> None:
        self.store.mark_pending(-1001, 7)
        self.store.mark_forwarded(-1001, 7)

        cursor_count, message_count = self.store.reset()

        self.assertEqual(cursor_count, 1)
        self.assertEqual(message_count, 1)
        self.assertEqual(self.store.cursor(-1001), 0)
        self.assertFalse(self.store.is_complete(-1001, 7))


if __name__ == "__main__":
    unittest.main()
