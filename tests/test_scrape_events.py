import unittest

from scripts import scrape_events
from scripts.scrape_events import (
    _extract_aeg_data_file_url,
    _event_from_aeg_json,
    _scrape_union_transfer_from_aeg_json,
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

    def test_extract_aeg_data_file_url_found(self):
        html = """
        <div class="js-axs-events-section"
             data-file="https://aegwebprod.blob.core.windows.net/json/events/289/events.json"
             data-limit="12">
        </div>
        """
        url = _extract_aeg_data_file_url(html)
        self.assertEqual(
            "https://aegwebprod.blob.core.windows.net/json/events/289/events.json", url
        )

    def test_extract_aeg_data_file_url_not_found(self):
        html = "<html><body>No widget here</body></html>"
        self.assertEqual("", _extract_aeg_data_file_url(html))

    def test_event_from_aeg_json_maps_fields(self):
        source = {"venue": "Union Transfer", "url": "https://www.utphilly.com/calendar/"}
        aeg_event = {
            "name": "Night Owls",
            "date": "2026-08-15",
            "eventUrl": "/events/detail/?event_id=9001",
            "artists": [{"name": "Night Owls"}, {"name": "The Openers"}],
        }
        event = _event_from_aeg_json(aeg_event, source)
        self.assertEqual("2026-08-15", event["date"])
        self.assertEqual("Night Owls, The Openers", event["bands"])
        self.assertEqual("Union Transfer", event["venue"])
        self.assertIn("event_id=9001", event["link"])

    def test_event_from_aeg_json_artist_string(self):
        source = {"venue": "Union Transfer", "url": "https://www.utphilly.com/calendar/"}
        aeg_event = {
            "name": "Solo Act",
            "date": "2026-09-01",
            "eventUrl": "/events/detail/?event_id=9002",
            "artists": "Solo Act",
        }
        event = _event_from_aeg_json(aeg_event, source)
        self.assertEqual("Solo Act", event["bands"])

    def test_event_from_aeg_json_falls_back_to_name(self):
        source = {"venue": "Union Transfer", "url": "https://www.utphilly.com/calendar/"}
        aeg_event = {
            "name": "The Show Title",
            "date": "2026-10-01",
            "eventUrl": "/events/detail/?event_id=9003",
        }
        event = _event_from_aeg_json(aeg_event, source)
        self.assertEqual("The Show Title", event["bands"])

    def test_scrape_union_transfer_from_aeg_json_success(self):
        source = {"venue": "Union Transfer", "url": "https://www.utphilly.com/calendar/"}
        import json as _json
        events_payload = _json.dumps([
            {"name": "Band A", "date": "2026-06-10", "eventUrl": "/events/detail/?event_id=1"},
            {"name": "Band B", "date": "2026-06-20", "eventUrl": "/events/detail/?event_id=2"},
        ])
        original_fetch_html = scrape_events.fetch_html
        scrape_events.fetch_html = lambda url: events_payload
        try:
            events = _scrape_union_transfer_from_aeg_json(
                source, "https://aegwebprod.blob.core.windows.net/json/events/289/events.json"
            )
        finally:
            scrape_events.fetch_html = original_fetch_html

        self.assertEqual(2, len(events))
        self.assertEqual("Band A", events[0]["bands"])
        self.assertEqual("2026-06-10", events[0]["date"])

    def test_scrape_union_transfer_from_aeg_json_returns_empty_on_network_error(self):
        import urllib.error
        source = {"venue": "Union Transfer", "url": "https://www.utphilly.com/calendar/"}
        original_fetch_html = scrape_events.fetch_html

        def _raise(url):
            raise urllib.error.URLError("no address")

        scrape_events.fetch_html = _raise
        try:
            events = _scrape_union_transfer_from_aeg_json(
                source, "https://aegwebprod.blob.core.windows.net/json/events/289/events.json"
            )
        finally:
            scrape_events.fetch_html = original_fetch_html

        self.assertEqual([], events)

    def test_scrape_source_union_transfer_uses_aeg_json_when_available(self):
        """When data-file URL is in the page HTML, AEG JSON is fetched directly."""
        import json as _json
        source = {"venue": "Union Transfer", "url": "https://www.utphilly.com/calendar/"}
        calendar_html = """
        <div class="js-axs-events-section"
             data-file="https://aegwebprod.blob.core.windows.net/json/events/289/events.json">
        </div>
        """
        events_payload = _json.dumps([
            {"name": "AEG Band", "date": "2026-07-04", "eventUrl": "/events/detail/?event_id=42"},
        ])
        pages = {
            "https://www.utphilly.com/calendar/": calendar_html,
            "https://aegwebprod.blob.core.windows.net/json/events/289/events.json": events_payload,
        }
        original_fetch_html = scrape_events.fetch_html
        scrape_events.fetch_html = lambda url: pages[url]
        try:
            events = scrape_source(source)
        finally:
            scrape_events.fetch_html = original_fetch_html

        self.assertEqual(1, len(events))
        self.assertEqual("AEG Band", events[0]["bands"])
        self.assertEqual("2026-07-04", events[0]["date"])

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
