import unittest

from scripts.inspect_union_transfer import (
    collect_event_like_nodes,
    find_text_matches,
    parse_json_fragments,
    summarize_script_blocks,
)


class InspectUnionTransferTests(unittest.TestCase):
    def test_summarize_script_blocks_flags_event_hints(self):
        html = """
        <html><body>
          <script type=\"application/ld+json\">{"@type":"Event","name":"Band"}</script>
          <script>window.__DATA__ = {"foo":"bar"};</script>
        </body></html>
        """

        summaries = summarize_script_blocks(html)
        self.assertEqual(2, len(summaries))
        self.assertEqual("application/ld+json", summaries[0]["type"])
        self.assertTrue(summaries[0]["event_hints"])
        self.assertFalse(summaries[1]["event_hints"])

    def test_parse_json_fragments_handles_embedded_objects(self):
        body = "window.__INITIAL_STATE__ = {\"events\":[{\"title\":\"Show\",\"start_date\":\"2026-10-01\"}]};"

        fragments = parse_json_fragments(body)
        self.assertEqual(1, len(fragments))
        self.assertEqual("Show", fragments[0]["events"][0]["title"])

    def test_collect_event_like_nodes_finds_nested_paths(self):
        fragment = {
            "calendar": [{"event": {"title": "Show", "start_date": "2026-11-01", "url": "/x"}}]
        }

        nodes = collect_event_like_nodes(fragment)
        paths = {entry["path"] for entry in nodes}
        self.assertIn("$.calendar[0].event", paths)

    def test_find_text_matches_returns_context_snippets(self):
        text = "abc event_id=123 xyz event_id=124"
        snippets = find_text_matches(text, "event_id", context=4)
        self.assertEqual(2, len(snippets))


if __name__ == "__main__":
    unittest.main()
