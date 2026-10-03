from pathlib import Path
import unittest


PLUGIN = Path(__file__).parents[1]


class ContractTests(unittest.TestCase):
    def test_english_command_contract(self):
        source = (PLUGIN / "main.py").read_text(encoding="utf-8")
        self.assertIn('@filter.command("gbible")', source)
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
        for name in ("bible.html", "bible_list.html"):
            template = (PLUGIN / "assets" / name).read_text(encoding="utf-8")
            self.assertIn("#0a0a0c", template)
            self.assertIn("#121214", template)
            self.assertIn("#7CB342", template)


if __name__ == "__main__":
    unittest.main()
