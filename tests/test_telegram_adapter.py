import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telegram_voice_forwarder.config import BaseConfig
from telegram_voice_forwarder.models import DialogInfo
from telegram_voice_forwarder.telegram_adapter import (
    TelethonResetGateway,
    build_client,
    load_dialogs,
    start_client,
)


def config(root: Path) -> BaseConfig:
    return BaseConfig(
        api_id=123,
        api_hash="secret",
        phone="+4912345",
        session_path=root / "session",
        state_db=root / "state.sqlite3",
        log_level="INFO",
        entity_cache_limit=250,
    )


class AsyncDialogs:
    def __init__(self, *dialogs: object) -> None:
        self._dialogs = dialogs

    def __aiter__(self):
        async def iterate():
            for dialog in self._dialogs:
                yield dialog

        return iterate()


class TelegramAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_builds_client_from_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            current = config(Path(temp_dir))
            with patch(
                "telegram_voice_forwarder.telegram_adapter.TelegramClient"
            ) as client_type:
                client = build_client(current)

            self.assertIs(client, client_type.return_value)
            client_type.assert_called_once_with(
                str(current.session_path),
                123,
                "secret",
                auto_reconnect=True,
                connection_retries=None,
                retry_delay=2,
                entity_cache_limit=250,
            )

    async def test_starts_client_and_returns_account_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            current = config(Path(temp_dir))
            account = SimpleNamespace(id=42, first_name="Alice", last_name="Example")
            client = SimpleNamespace(
                start=AsyncMock(),
                get_me=AsyncMock(return_value=account),
            )

            with patch(
                "telegram_voice_forwarder.telegram_adapter.utils.get_display_name",
                return_value="Alice Example",
            ):
                identity = await start_client(client, current)

            self.assertEqual(identity, ("Alice Example", 42))
            client.start.assert_awaited_once_with(phone="+4912345")

    async def test_maps_telegram_dialogs_to_transport_neutral_models(self) -> None:
        dialogs = (
            SimpleNamespace(
                id=-1001,
                name="Group",
                is_group=True,
                is_channel=False,
                is_user=False,
            ),
            SimpleNamespace(
                id=-1002,
                name="Channel",
                is_group=False,
                is_channel=True,
                is_user=False,
            ),
            SimpleNamespace(
                id=3,
                name="User",
                is_group=False,
                is_channel=False,
                is_user=True,
            ),
        )
        client = MagicMock()
        client.iter_dialogs.return_value = AsyncDialogs(*dialogs)

        result = await load_dialogs(client)

        self.assertEqual(
            result,
            (
                DialogInfo(-1001, "Gruppe", "Group"),
                DialogInfo(-1002, "Kanal", "Channel"),
                DialogInfo(3, "Benutzer", "User"),
            ),
        )

    async def test_reset_gateway_resolves_boundaries_and_deletes_in_batches(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            current = config(Path(temp_dir))
            target = SimpleNamespace(id=3)
            source = SimpleNamespace(id=1)
            client = SimpleNamespace(
                start=AsyncMock(),
                get_me=AsyncMock(
                    return_value=SimpleNamespace(id=42, first_name="Alice")
                ),
                get_dialogs=AsyncMock(),
                get_entity=AsyncMock(side_effect=(target, source)),
                get_messages=AsyncMock(return_value=[SimpleNamespace(id=17)]),
                delete_messages=AsyncMock(),
                disconnect=AsyncMock(),
            )
            cutoff = datetime(2026, 8, 1, tzinfo=UTC)
            gateway = TelethonResetGateway(client, current)

            with patch(
                "telegram_voice_forwarder.telegram_adapter.utils.get_peer_id",
                side_effect=(-1003, -1001),
            ):
                await gateway.start()
                target_id = await gateway.resolve_target("@target")
                boundary = await gateway.boundary_before("@source", cutoff)
                await gateway.delete_target_messages(tuple(range(1, 102)))
                await gateway.close()

            self.assertEqual(target_id, -1003)
            self.assertEqual(boundary, (-1001, 17))
            client.get_dialogs.assert_awaited_once_with()
            client.get_messages.assert_awaited_once_with(
                source,
                limit=1,
                offset_date=cutoff,
            )
            self.assertEqual(client.delete_messages.await_count, 2)
            self.assertEqual(
                client.delete_messages.await_args_list[0].args,
                (target, list(range(1, 101))),
            )
            self.assertTrue(client.delete_messages.await_args_list[0].kwargs["revoke"])
            self.assertEqual(
                client.delete_messages.await_args_list[1].args,
                (target, [101]),
            )
            client.disconnect.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
