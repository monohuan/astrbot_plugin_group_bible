import unittest

from data.plugins.astrbot_plugin_group_bible.main import GroupBiblePlugin


class Poke:
    def __init__(self, target):
        self.id = target
        self.qq = None


class FakeStore:
    def __init__(self, entry):
        self.entry = entry

    async def random(self, group_id, **kwargs):
        return self.entry


class FakeEvent:
    def __init__(self, target="999"):
        self.message_obj = type(
            "MessageObject",
            (),
            {"raw_message": {"target_id": target, "user_id": "123"}},
        )()
        self.parts = [Poke(target)]
        self.llm_values = []
        self.stopped = False

    def get_messages(self):
        return self.parts

    def get_self_id(self):
        return "999"

    def get_group_id(self):
        return "888"

    def should_call_llm(self, value):
        self.llm_values.append(value)

    def stop_event(self):
        self.stopped = True


def make_plugin(entry):
    plugin = object.__new__(GroupBiblePlugin)
    plugin.config = {
        "enable_poke_random": True,
        "poke_cooldown_seconds": 0,
        "random_pool_copies": 3,
        "random_pool_reset_minutes": 30,
    }
    plugin.store = FakeStore(entry)
    plugin._poke_last_at = {}
    plugin.sent = []

    async def send_entry(event, selected):
        plugin.sent.append(selected)

    plugin._send_entry = send_entry
    return plugin


class PokeCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_bible_takes_over_and_blocks_llm_poke(self):
        entry = object()
        plugin = make_plugin(entry)
        event = FakeEvent()

        await plugin.poke_random_bible(event)

        self.assertEqual(event.llm_values, [False])
        self.assertTrue(event.stopped)
        self.assertEqual(plugin.sent, [entry])

    async def test_empty_group_hands_event_to_llm_poke(self):
        plugin = make_plugin(None)
        event = FakeEvent()

        await plugin.poke_random_bible(event)

        self.assertEqual(event.llm_values, [])
        self.assertFalse(event.stopped)
        self.assertEqual(plugin.sent, [])

    async def test_poking_another_member_is_untouched(self):
        plugin = make_plugin(object())
        event = FakeEvent(target="777")

        await plugin.poke_random_bible(event)

        self.assertEqual(event.llm_values, [])
        self.assertFalse(event.stopped)
        self.assertEqual(plugin.sent, [])


if __name__ == "__main__":
    unittest.main()
