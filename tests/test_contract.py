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
        self.assertIn('action == "story"', source)
        self.assertIn('@filter.command("故事")', source)
        self.assertNotIn('action == "delete"', source)

    def test_story_uses_current_session_provider_and_configured_range(self):
        source = (PLUGIN / "main.py").read_text(encoding="utf-8")
        self.assertIn('"story_min_entries", 3, 1, 20', source)
        self.assertIn('"story_max_entries", 6, 1, 20', source)
        self.assertIn("get_current_chat_provider_id(umo)", source)
        self.assertIn("self.context.llm_generate(", source)
        self.assertIn("self.store.sample_story(", source)
        self.assertIn("include_images=caption_provider is not None", source)
        self.assertIn("self._format_story(story, entries)", source)
        self.assertIn("Nodes([node])", source)
        self.assertIn('"story_clues": story_clues', source)
        self.assertNotIn('"image_descriptions":', source)

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
        self.assertIn('class="bubble{% if images %}', template)
        self.assertIn("border:1px solid #303238", template)
        self.assertNotIn("bubble:before", template)
        self.assertIn("width=320", template)
        self.assertNotIn("min-height:140px", template)
        self.assertIn("flex-wrap:wrap", template)
        self.assertIn("overflow-wrap:anywhere", template)
        self.assertIn(".bubble{display:inline-block;width:auto;max-width:100%", template)
        self.assertIn(".bubble.image-only{display:block;width:100%", template)
        self.assertIn('class="message"', template)
        self.assertIn('class="message-main"', template)
        self.assertNotIn('class="badge">群圣经</span>', template)
        self.assertIn('class="avatar" src="{{ avatar }}"', template)
        self.assertNotIn("fit-content", template)
        self.assertNotIn("collect by", template)
        self.assertIn('<div class="name-row"><span class="name">{{ author_name }}</span></div>', template)
        self.assertIn('class="meta-row"', template)
        self.assertLess(template.index('class="entry-id"'), template.index('class="time"'))
        renderer = (PLUGIN / "renderer.py").read_text(encoding="utf-8")
        self.assertIn('%Y-%m-%d %H:%M:%S', renderer)


if __name__ == "__main__":
    unittest.main()
