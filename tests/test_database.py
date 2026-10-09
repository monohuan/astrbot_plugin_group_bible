import asyncio
import tempfile
import unittest
from pathlib import Path

from data.plugins.astrbot_plugin_group_bible.database import (
    BibleStore,
    DuplicateBibleError,
)


def run(coro):
    return asyncio.run(coro)


def payload(group="100", message="200"):
    return {
        "group_id": group,
        "source_message_id": message,
        "author_id": "300",
        "author_name": "测试用户",
        "source_time": 1_700_000_000,
        "segments": [{"type": "text", "text": "测试圣经"}],
        "plain_text": "测试圣经",
        "collector_id": "400",
        "collector_name": "收录者",
        "created_at": 1_700_000_001,
    }


class BibleStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = BibleStore(Path(self.tmp.name) / "bible.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_store_crud_and_group_isolation(self):
        first = run(self.store.add(payload()))
        second = run(self.store.add(payload(group="101")))

        self.assertGreater(first.id, 0)
        self.assertGreater(second.id, first.id)
        self.assertEqual(run(self.store.count("100")), 1)
        self.assertEqual(run(self.store.count("101")), 1)
        self.assertIsNone(run(self.store.get("101", first.id)))
        self.assertEqual(run(self.store.random("100")).plain_text, "测试圣经")

        rows, total = run(self.store.list_page("100", 1, 10))
        self.assertEqual(total, 1)
        self.assertEqual([row.id for row in rows], [first.id])

        removed = run(self.store.delete("100", first.id))
        self.assertEqual(removed.id, first.id)
        self.assertEqual(run(self.store.count("100")), 0)

    def test_duplicate_source_message_is_rejected_per_group(self):
        run(self.store.add(payload()))
        with self.assertRaises(DuplicateBibleError):
            run(self.store.add(payload()))

    def test_random_draws_every_entry_once_before_repeating(self):
        entries = [
            run(self.store.add(payload(message=str(200 + index)))) for index in range(6)
        ]

        first_cycle = [run(self.store.random("100")).id for _ in entries]
        self.assertEqual(set(first_cycle), {entry.id for entry in entries})
        self.assertEqual(len(first_cycle), len(set(first_cycle)))

        next_draw = run(self.store.random("100")).id
        self.assertIn(next_draw, {entry.id for entry in entries})
        self.assertNotEqual(next_draw, first_cycle[-1])

    def test_random_draw_state_survives_store_recreation(self):
        db_path = Path(self.tmp.name) / "bible.db"
        entries = [
            run(self.store.add(payload(message=str(300 + index)))) for index in range(4)
        ]
        first_id = run(self.store.random("100")).id

        reopened = BibleStore(db_path)
        rest = [run(reopened.random("100")).id for _ in range(3)]
        self.assertNotIn(first_id, rest)
        self.assertEqual({first_id, *rest}, {entry.id for entry in entries})

    def test_new_and_deleted_entries_update_current_draw_cycle(self):
        original = [
            run(self.store.add(payload(message=str(400 + index)))) for index in range(4)
        ]
        already_drawn = run(self.store.random("100")).id
        new_entry = run(self.store.add(payload(message="499")))
        to_delete = next(entry for entry in original if entry.id != already_drawn)
        run(self.store.delete("100", to_delete.id))

        remaining_live_ids = {
            entry.id
            for entry in [*original, new_entry]
            if entry.id not in {already_drawn, to_delete.id}
        }
        draws = [run(self.store.random("100")).id for _ in remaining_live_ids]
        self.assertEqual(set(draws), remaining_live_ids)
        self.assertNotIn(to_delete.id, draws)

    def test_concurrent_random_draws_do_not_duplicate(self):
        entries = [
            run(self.store.add(payload(message=str(500 + index)))) for index in range(8)
        ]

        async def draw_together():
            return await asyncio.gather(
                *(self.store.random("100") for _ in range(len(entries)))
            )

        draws = run(draw_together())
        self.assertEqual(
            {entry.id for entry in draws},
            {entry.id for entry in entries},
        )


if __name__ == "__main__":
    unittest.main()
