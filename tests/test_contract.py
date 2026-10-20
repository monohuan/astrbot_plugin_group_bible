from pathlib import Path
import unittest


PLUGIN = Path(__file__).parents[1]


class ContractTests(unittest.TestCase):
    def test_english_command_contract(self):
        source = (PLUGIN / "main.py").read_text(encoding="utf-8")
        self.assertIn('@filter.command("gbible")', source)
        self.assertIn('action: str = ""', source)
        self.assertIn('argument: str = ""', source)
        self.assertNotIn("GreedyStr", source)
        self.assertIn('action == "add"', source)
        self.assertIn('action == "random"', source)
        self.assertIn('action == "del"', source)
        self.assertNotIn('action == "delete"', source)

    def test_poke_handoff_contract(self):
        source = (PLUGIN / "main.py").read_text(encoding="utf-8")
        empty_branch = source.index("if entry is None:", source.index("poke_random_bible"))
        takeover = source.index("event.should_call_llm(False)", empty_branch)
        self.assertNotIn("event.stop_event()", source[empty_branch:takeover])
        self.assertNotIn("event.should_call_llm(False)", source[empty_branch:takeover])
        self.assertIn("priority=10_000", source)

    def test_templates_keep_mutsumi_dark_visual_tokens(self):
        template = (PLUGIN / "assets" / "bible_list.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("#0a0a0c", template)
        self.assertIn("#121214", template)
        self.assertIn("#7CB342", template)

    def test_entry_template_uses_chat_bubble_layout(self):
        template = (PLUGIN / "assets" / "bible.html").read_text(encoding="utf-8")
        self.assertIn('class="bubble{% if images and not text %}', template)
        self.assertIn("border:1px solid #303238", template)
        self.assertNotIn("bubble:before", template)
        self.assertIn("width=400", template)
        self.assertIn("flex-wrap:wrap", template)
        self.assertIn("overflow-wrap:anywhere", template)
        self.assertIn("collect by {{ collector_name }}", template)
        self.assertIn(
            '<span class="name">{{ author_name }}</span><span class="time">{{ timestamp }}</span>',
            template,
        )
        renderer = (PLUGIN / "renderer.py").read_text(encoding="utf-8")
        self.assertIn('%Y-%m-%d %H:%M:%S', renderer)


if __name__ == "__main__":
    unittest.main()
