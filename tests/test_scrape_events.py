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

    def test_normalize_date(self):
        self.assertEqual("2026-05-01", normalize_date("2026-05-01T20:00:00-04:00"))
        self.assertEqual("2026-07-09", normalize_date("2026-07-09"))
        self.assertEqual("TBA", normalize_date(None))


if __name__ == '__main__':
    unittest.main()
