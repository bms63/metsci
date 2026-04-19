import unittest

from scripts.scrape_events import extract_event_nodes, extract_band_names, normalize_date


class ScrapeEventsTests(unittest.TestCase):
    def test_extract_event_nodes_from_jsonld(self):
        html = '''
        <html><body>
          <script type="application/ld+json">
            {
              "@context": "https://schema.org",
              "@type": "Event",
              "name": "Band Night",
              "startDate": "2026-05-01T20:00:00-04:00"
            }
          </script>
        </body></html>
        '''
        events = extract_event_nodes(html)
        self.assertEqual(1, len(events))
        self.assertEqual("Band Night", events[0]["name"])

    def test_extract_band_names_handles_performer_list(self):
        node = {
            "name": "Ignored Title",
            "performer": [{"name": "Alpha"}, {"name": "Beta"}, {"name": "Alpha"}],
        }
        self.assertEqual("Alpha, Beta", extract_band_names(node))

    def test_extract_event_nodes_from_inline_json_payload(self):
        html = """
        <html><body>
          <script>
            window.__INITIAL_STATE__ = {"events":[
              {"title":"Show One","start_date":"2026-05-02","url":"/shows/show-one"},
              {"title":"Show Two","start_date":"2026-05-03","url":"/shows/show-two"}
            ]};
          </script>
        </body></html>
        """
        events = extract_event_nodes(html)
        self.assertEqual(2, len(events))
        self.assertEqual("Show One", events[0]["title"])

    def test_extract_band_names_skips_organization_type(self):
        """Performer entries typed as Organization (venue placeholders) should be
        ignored so the function falls back to the event's own name."""
        node = {
            "name": "Chameleons",
            "performer": {"@type": "Organization", "name": "Organization"},
        }
        self.assertEqual("Chameleons", extract_band_names(node))

    def test_extract_band_names_skips_organization_in_list(self):
        """Mixed performer lists: Organization entries are dropped, real acts kept."""
        node = {
            "name": "Fallback Title",
            "performer": [
                {"@type": "MusicGroup", "name": "The Real Band"},
                {"@type": "Organization", "name": "The Venue"},
            ],
        }
        self.assertEqual("The Real Band", extract_band_names(node))

    def test_extract_band_names_skips_organization_placeholder_string(self):
        node = {
            "name": "Chameleons",
            "performer": "Organization",
        }
        self.assertEqual("Chameleons", extract_band_names(node))

    def test_extract_band_names_skips_organization_placeholder_dict(self):
        node = {
            "name": "Pallbearer",
            "performer": {"name": "Organization"},
        }
        self.assertEqual("Pallbearer", extract_band_names(node))

    def test_extract_band_names_falls_back_to_event_name_when_no_performers(self):
        node = {"name": "Show Title"}
        self.assertEqual("Show Title", extract_band_names(node))

    def test_extract_band_names_falls_back_to_title_when_name_missing(self):
        node = {"title": "Show Title"}
        self.assertEqual("Show Title", extract_band_names(node))

    def test_normalize_date(self):
        self.assertEqual("2026-05-01", normalize_date("2026-05-01T20:00:00-04:00"))
        self.assertEqual("2026-07-09", normalize_date("2026-07-09"))
        self.assertEqual("TBA", normalize_date(None))


if __name__ == '__main__':
    unittest.main()
