import tempfile
import unittest
from pathlib import Path

from quart import Quart

from data.plugins.astrbot_plugin_group_bible.database import BibleStore
from data.plugins.astrbot_plugin_group_bible.web import GroupBibleWebController


class SaveableConfig(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved = 0

    def save_config(self):
        self.saved += 1


def entry_payload(group="100", message="200", text="测试圣经"):
    return {
        "group_id": group,
        "source_message_id": message,
        "author_id": "300",
        "author_name": "测试作者",
        "source_time": 1_700_000_000,
        "segments": [{"type": "text", "text": text}],
        "plain_text": text,
        "collector_id": "400",
        "collector_name": "收录者",
        "created_at": 1_700_000_001,
    }


class WebUITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = BibleStore(Path(self.tmp.name) / "bible.db")
        self.entry = await self.store.add(entry_payload())
        self.config = SaveableConfig(
            add_permission_level=1,
            enable_poke_random=True,
            poke_cooldown_seconds=10,
            list_page_size=10,
            max_text_length=3000,
            max_images=9,
            show_bible_id=True,
        )
        self.changed = 0

        def on_changed():
            self.changed += 1

        self.controller = GroupBibleWebController(
            object(), self.config, self.store, on_changed
        )
        self.app = Quart(__name__)
        self.app.add_url_rule(
            "/bootstrap", view_func=self.controller._wrap(self.controller.bootstrap)
        )
        self.app.add_url_rule(
            "/entries", view_func=self.controller._wrap(self.controller.entries)
        )
        self.app.add_url_rule(
            "/delete",
            view_func=self.controller._wrap(self.controller.delete),
            methods=["POST"],
        )
        self.app.add_url_rule(
            "/config",
            view_func=self.controller._wrap(self.controller.update_config),
            methods=["POST"],
        )
        self.client = self.app.test_client()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_bootstrap_and_search(self):
        response = await self.client.get("/bootstrap")
        payload = await response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["total"], 1)
        self.assertEqual(payload["data"]["groups"][0]["group_id"], "100")

        response = await self.client.get("/entries?query=%E6%B5%8B%E8%AF%95")
        payload = await response.get_json()
        self.assertEqual(payload["data"]["entries"][0]["id"], self.entry.id)

    async def test_update_config_and_delete(self):
        response = await self.client.post(
            "/config",
            json={"add_permission_level": 2, "enable_poke_random": False},
        )
        payload = await response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(self.config["add_permission_level"], 2)
        self.assertFalse(self.config["enable_poke_random"])
        self.assertEqual(self.config.saved, 1)
        self.assertEqual(self.changed, 1)

        response = await self.client.post(
            "/delete", json={"group_id": "100", "id": self.entry.id}
        )
        payload = await response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(await self.store.count("100"), 0)

    async def test_rejects_unknown_or_invalid_config(self):
        response = await self.client.post("/config", json={"unknown": True})
        self.assertEqual(response.status_code, 400)
        response = await self.client.post("/config", json={"add_permission_level": 9})
        self.assertEqual(response.status_code, 400)


class WebUIAssetTests(unittest.TestCase):
    def test_page_assets_are_self_contained(self):
        page = Path(__file__).parents[1] / "pages" / "manage"
        html = (page / "index.html").read_text(encoding="utf-8")
        js = (page / "app.js").read_text(encoding="utf-8")
        self.assertIn("./styles.css", html)
        self.assertIn("./app.js", html)
        self.assertIn("window.AstrBotPluginPage", js)
        self.assertNotIn("http://", html + js)
        self.assertNotIn("https://", html + js)


if __name__ == "__main__":
    unittest.main()
