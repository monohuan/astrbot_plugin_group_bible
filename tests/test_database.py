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


if __name__ == "__main__":
    unittest.main()
