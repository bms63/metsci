import unittest

from scripts.inspect_union_transfer import (
    collect_event_like_nodes,
    find_api_patterns,
    find_html_event_elements,
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

    def test_find_html_event_elements_detects_event_classes(self):
        html = """
        <html><body>
          <div class="event-listing featured">Show A</div>
          <article class="show-card">Show B</article>
          <div class="unrelated">Nothing here</div>
        </body></html>
        """
        elements = find_html_event_elements(html)
        tags = [(e["tag"], e["event_classes"]) for e in elements]
        self.assertIn(("div", ["event-listing"]), tags)
        self.assertIn(("article", ["show-card"]), tags)
        self.assertNotIn("unrelated", [c for _, cs in tags for c in cs])

    def test_find_html_event_elements_detects_data_attributes(self):
        html = """
        <html><body>
          <li data-event-id="42" data-show-date="2026-09-01">Band Night</li>
          <li data-price="10">Ticket</li>
        </body></html>
        """
        elements = find_html_event_elements(html)
        self.assertEqual(1, len(elements))
        self.assertEqual("li", elements[0]["tag"])
        self.assertIn("data-event-id", elements[0]["data_attributes"])
        self.assertIn("data-show-date", elements[0]["data_attributes"])

    def test_find_api_patterns_detects_fetch_urls(self):
        html = """
        <html><body>
          <script>
            fetch('/api/events?venue=ut').then(r => r.json());
            fetch('/api/calendar');
          </script>
        </body></html>
        """
        patterns = find_api_patterns(html)
        self.assertIn("/api/events?venue=ut", patterns["fetch_urls"])
        self.assertIn("/api/calendar", patterns["fetch_urls"])

    def test_find_api_patterns_detects_window_vars(self):
        html = """
        <html><body>
          <script>
            window.__EVENTS__ = [];
            window.APP_CONFIG = {"apiBase": "https://api.utphilly.com"};
          </script>
        </body></html>
        """
        patterns = find_api_patterns(html)
        self.assertIn("__EVENTS__", patterns["window_vars"])
        self.assertIn("APP_CONFIG", patterns["window_vars"])

    def test_find_api_patterns_detects_api_config_urls(self):
        html = """
        <html><body>
          <script>
            var apiUrl = 'https://api.utphilly.com/v1';
            var baseURL = 'https://api.utphilly.com';
          </script>
        </body></html>
        """
        patterns = find_api_patterns(html)
        self.assertIn("https://api.utphilly.com/v1", patterns["api_config_urls"])
        self.assertIn("https://api.utphilly.com", patterns["api_config_urls"])

    def test_find_api_patterns_detects_xhr_urls(self):
        html = """
        <html><body>
          <script>
            var xhr = new XMLHttpRequest();
            xhr.open('GET', '/api/shows');
          </script>
        </body></html>
        """
        patterns = find_api_patterns(html)
        self.assertIn("/api/shows", patterns["xhr_urls"])


if __name__ == "__main__":
    unittest.main()
