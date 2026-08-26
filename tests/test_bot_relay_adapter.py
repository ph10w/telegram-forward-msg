import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

from telethon.tl.types import MessageEntityTextUrl

from telegram_voice_forwarder.bot_relay_adapter import BotRelayClient


class BotRelayClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_starts_private_relay_without_target_access_for_user(self) -> None:
        source_client = SimpleNamespace(get_entity=AsyncMock(return_value=object()))
        relay = BotRelayClient(Mock(), source_client, -1002)
        relay._call = AsyncMock(
            side_effect=[
                {"url": ""},
                {"id": 9, "username": "publisher_bot"},
                [{"update_id": 41}],
            ]
        )

        await relay.start(123)

        source_client.get_entity.assert_awaited_once_with("@publisher_bot")
        self.assertEqual(relay._user_id, 123)
        self.assertEqual(relay._offset, 42)

    async def test_relays_existing_voice_reference_and_deletes_staging_message(
        self,
    ) -> None:
        media = object()
        source_client = SimpleNamespace(
            send_file=AsyncMock(return_value=SimpleNamespace(id=55)),
            delete_messages=AsyncMock(),
        )
        relay = BotRelayClient(Mock(), source_client, -1002)
        relay._user_id = 123
        relay._bot_entity = object()
        relay._wait_for_relay = AsyncMock(return_value=77)
        relay._call = AsyncMock(side_effect=[{"message_id": 88}, True])
        message = SimpleNamespace(voice=media, video_note=None)
        entities = [MessageEntityTextUrl(offset=0, length=4, url="https://example.test")]

        result = await relay.copy_message(
            -1001,
            10,
            message,
            caption="date",
            entities=entities,
        )

        self.assertEqual(result.id, 88)
        source_client.send_file.assert_awaited_once()
        send_args, send_kwargs = source_client.send_file.await_args
        self.assertEqual(send_args, (relay._bot_entity, media))
        self.assertTrue(send_kwargs["caption"].startswith("telegram-voice-forwarder:"))
        self.assertTrue(send_kwargs["silent"])
        self.assertTrue(send_kwargs["voice_note"])
        self.assertEqual(
            relay._call.await_args_list,
            [
                call(
                    "copyMessage",
                    chat_id=-1002,
                    from_chat_id=123,
                    message_id=77,
                    disable_notification=False,
                    caption="date",
                    caption_entities=[
                        {
                            "type": "text_link",
                            "offset": 0,
                            "length": 4,
                            "url": "https://example.test",
                        }
                    ],
                ),
                call("deleteMessage", chat_id=123, message_id=77),
            ],
        )
        source_client.delete_messages.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
