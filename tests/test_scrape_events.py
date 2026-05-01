import unittest

from scripts import scrape_events
from scripts.scrape_events import (
    _find_union_transfer_event_links,
    extract_event_nodes,
    extract_band_names,
    fetch_html_with_browser,
    normalize_date,
    scrape_source,
)


class ScrapeEventsTests(unittest.TestCase):
    def test_find_union_transfer_event_links(self):
        html = """
        <html><body>
          <a href="/events/detail/?event_id=1146309">GoldFord</a>
          <script>
            window.__events = [{"url":"\\/events\\/detail\\/?event_id=1146310"}];
          </script>
          <a href="https://www.utphilly.com/events/detail/?event_id=1146309">Duplicate</a>
        </body></html>
        """
        links = _find_union_transfer_event_links(html, "https://www.utphilly.com/calendar/")
        self.assertEqual(
            [
                "https://www.utphilly.com/events/detail/?event_id=1146309",
                "https://www.utphilly.com/events/detail/?event_id=1146310",
            ],
            links,
        )

    def test_scrape_source_union_transfer_uses_detail_pages_and_h2_fallback(self):
        source = {"venue": "Union Transfer", "url": "https://www.utphilly.com/calendar/"}
        pages = {
            "https://www.utphilly.com/calendar/": """
                <a href="/events/detail/?event_id=1146309">GoldFord</a>
                <a href="/events/detail/?event_id=1146310">No JSON-LD</a>
            """,
            "https://www.utphilly.com/events/detail/?event_id=1146309": """
                <script type="application/ld+json">
                  {"@type":"Event","name":"GoldFord","startDate":"2026-09-20","url":"/events/detail/?event_id=1146309"}
                </script>
            """,
            "https://www.utphilly.com/events/detail/?event_id=1146310": "<h2>Manual Fallback Band</h2>",
        }

        original_fetch_html = scrape_events.fetch_html
        scrape_events.fetch_html = lambda url: pages[url]
        try:
            events = scrape_source(source)
        finally:
            scrape_events.fetch_html = original_fetch_html

        self.assertEqual(2, len(events))
        self.assertEqual("GoldFord", events[0]["bands"])
        self.assertEqual("2026-09-20", events[0]["date"])
        self.assertEqual("Manual Fallback Band", events[1]["bands"])
        self.assertEqual("TBA", events[1]["date"])

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

    def test_extract_event_nodes_with_nested_event_fields(self):
        html = """
        <html><body>
          <script>
            window.__DATA__ = {
              "calendar": [{
                "event": {
                  "headline": "Nested Show",
                  "eventDateLocal": "2026-08-01T20:00:00-04:00",
                  "eventLink": "/events/nested-show"
                }
              }]
            };
          </script>
        </body></html>
        """
        events = extract_event_nodes(html)
        self.assertEqual(1, len(events))
        self.assertEqual("Nested Show", events[0]["headline"])

    def test_extract_event_nodes_with_offer_url_only(self):
        html = """
        <html><body>
          <script type="application/ld+json">
            {
              "@type": "Event",
              "name": "Offer URL Show",
              "startDate": "2026-06-02",
              "offers": [{"url": "/tickets/offer-url-show"}]
            }
          </script>
        </body></html>
        """
        events = extract_event_nodes(html)
        self.assertEqual(1, len(events))
        self.assertEqual("Offer URL Show", events[0]["name"])

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
        self.assertEqual("2024-07-03", normalize_date(1720000000))
        self.assertEqual("TBA", normalize_date(None))

    def test_fetch_html_with_browser_fallback_without_playwright(self):
        """When Playwright is not installed, fetch_html_with_browser delegates to fetch_html."""
        captured = []
        original_fetch_html = scrape_events.fetch_html
        original_playwright_flag = scrape_events._PLAYWRIGHT_AVAILABLE
        scrape_events.fetch_html = lambda url: captured.append(url) or "<html/>"
        scrape_events._PLAYWRIGHT_AVAILABLE = False
        try:
            result = fetch_html_with_browser("https://example.com/")
        finally:
            scrape_events.fetch_html = original_fetch_html
            scrape_events._PLAYWRIGHT_AVAILABLE = original_playwright_flag

        self.assertEqual(["https://example.com/"], captured)
        self.assertEqual("<html/>", result)


if __name__ == '__main__':
    unittest.main()
