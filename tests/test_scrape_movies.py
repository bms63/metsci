import unittest
from unittest.mock import MagicMock, patch

from scripts.scrape_movies import (
    _PLAYWRIGHT_AVAILABLE,
    extract_movies_from_html,
    fetch_html_with_browser,
    normalize_date,
    scrape_with_playwright,
    _extract_movies_from_page,
    _list_theaters,
    _select_theater,
)


class NormalizeDateTests(unittest.TestCase):
    def test_iso_date_passthrough(self):
        self.assertEqual("2026-05-02", normalize_date("2026-05-02"))

    def test_iso_datetime_truncated(self):
        self.assertEqual("2026-05-02", normalize_date("2026-05-02T19:30:00Z"))

    def test_unix_timestamp_seconds(self):
        # 2025-05-02 00:00:00 UTC
        self.assertEqual("2025-05-02", normalize_date(1746144000))

    def test_unix_timestamp_milliseconds(self):
        self.assertEqual("2025-05-02", normalize_date(1746144000000))

    def test_empty_string(self):
        self.assertEqual("TBA", normalize_date(""))

    def test_none(self):
        self.assertEqual("TBA", normalize_date(None))


class ExtractMoviesFromHtmlTests(unittest.TestCase):
    def test_extracts_from_jsonld_screening_event(self):
        html = """
        <html><head>
          <script type="application/ld+json">
          {
            "@type": "ScreeningEvent",
            "name": "Test Film",
            "startDate": "2026-05-02T19:30:00Z",
            "location": {"name": "Nuart Theatre"},
            "url": "https://www.landmarktheatres.com/movies/test-film"
          }
          </script>
        </head><body></body></html>
        """
        movies = extract_movies_from_html(html, "2026-05-02")
        self.assertEqual(1, len(movies))
        self.assertEqual("Test Film", movies[0]["title"])
        self.assertEqual("2026-05-02", movies[0]["date"])
        self.assertEqual("Nuart Theatre", movies[0]["location"])

    def test_deduplicates_same_title_date_location(self):
        entry = {
            "@type": "ScreeningEvent",
            "name": "Dupe Film",
            "startDate": "2026-05-02",
            "location": {"name": "Theater A"},
        }
        import json

        block = json.dumps(entry)
        html = f"""
        <html><head>
          <script type="application/ld+json">{block}</script>
          <script type="application/ld+json">{block}</script>
        </head><body></body></html>
        """
        movies = extract_movies_from_html(html, "2026-05-02")
        self.assertEqual(1, len(movies))

    def test_returns_empty_for_no_data(self):
        movies = extract_movies_from_html("<html><body>No data</body></html>", "2026-05-02")
        self.assertEqual([], movies)

    def test_extracts_from_next_data(self):
        import json

        payload = {
            "props": {
                "pageProps": {
                    "screenings": [
                        {
                            "@type": "ScreeningEvent",
                            "name": "Next Film",
                            "startDate": "2026-05-03",
                            "location": {"name": "Landmark Theater"},
                        }
                    ]
                }
            }
        }
        html = f"""
        <html><head>
          <script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>
        </head><body></body></html>
        """
        movies = extract_movies_from_html(html, "2026-05-03")
        self.assertEqual(1, len(movies))
        self.assertEqual("Next Film", movies[0]["title"])


class FetchHtmlWithBrowserTests(unittest.TestCase):
    def test_falls_back_to_urllib_when_playwright_unavailable(self):
        """When _PLAYWRIGHT_AVAILABLE is False, fetch_html_with_browser uses urllib."""
        with patch("scripts.scrape_movies._PLAYWRIGHT_AVAILABLE", False):
            with patch("scripts.scrape_movies.fetch_html", return_value="<html/>") as mock_fetch:
                result = fetch_html_with_browser("https://example.com")
        mock_fetch.assert_called_once_with("https://example.com")
        self.assertEqual("<html/>", result)


class ExtractMoviesFromPageTests(unittest.TestCase):
    def test_uses_html_extraction_first(self):
        """_extract_movies_from_page should call extract_movies_from_html."""
        import json

        entry = {
            "@type": "ScreeningEvent",
            "name": "Page Film",
            "startDate": "2026-05-02",
            "location": {"name": "Theater X"},
        }
        html = (
            '<html><head>'
            f'<script type="application/ld+json">{json.dumps(entry)}</script>'
            '</head><body></body></html>'
        )
        page = MagicMock()
        page.content.return_value = html

        movies = _extract_movies_from_page(page, "Theater X", "2026-05-02")
        self.assertEqual(1, len(movies))
        self.assertEqual("Page Film", movies[0]["title"])
        # page.evaluate should NOT be called when HTML extraction succeeds.
        page.evaluate.assert_not_called()

    def test_falls_back_to_dom_query_when_no_structured_data(self):
        """When HTML extraction returns nothing, DOM JS evaluation is attempted."""
        page = MagicMock()
        page.content.return_value = "<html><body>No data</body></html>"
        page.evaluate.return_value = [
            {
                "date": "2026-05-02",
                "title": "DOM Film",
                "genre": "N/A",
                "location": "Theater Y",
                "link": "https://tickets.example.com/1",
            }
        ]

        movies = _extract_movies_from_page(page, "Theater Y", "2026-05-02")
        page.evaluate.assert_called_once()
        self.assertEqual(1, len(movies))
        self.assertEqual("DOM Film", movies[0]["title"])

    def test_overrides_landmark_theatres_default_location(self):
        """Location 'Landmark Theatres' in extracted data is replaced by theater_name."""
        import json

        entry = {
            "@type": "ScreeningEvent",
            "name": "Override Film",
            "startDate": "2026-05-02",
            # No location field → defaults to "Landmark Theatres"
        }
        html = (
            '<html><head>'
            f'<script type="application/ld+json">{json.dumps(entry)}</script>'
            '</head><body></body></html>'
        )
        page = MagicMock()
        page.content.return_value = html

        movies = _extract_movies_from_page(page, "Nuart Theatre", "2026-05-02")
        self.assertEqual(1, len(movies))
        self.assertEqual("Nuart Theatre", movies[0]["location"])


class ScrapeWithPlaywrightTests(unittest.TestCase):
    def test_returns_error_when_playwright_unavailable(self):
        with patch("scripts.scrape_movies._PLAYWRIGHT_AVAILABLE", False):
            movies, errors = scrape_with_playwright()
        self.assertEqual([], movies)
        self.assertTrue(len(errors) > 0)
        self.assertIn("Playwright", errors[0])

    def test_returns_error_when_no_location_button(self):
        """No theater options → error reported, empty result returned."""
        mock_page = MagicMock()
        # query_selector returns None for 'button[aria-expanded]'
        mock_page.query_selector.return_value = None
        mock_page.wait_for_function.side_effect = Exception("timeout")

        mock_context = MagicMock()
        mock_context.__enter__ = MagicMock(return_value=mock_context)
        mock_context.__exit__ = MagicMock(return_value=False)

        mock_browser = MagicMock()
        mock_browser.new_context.return_value.__enter__ = MagicMock(
            return_value=mock_browser.new_context.return_value
        )
        mock_browser.new_context.return_value.__exit__ = MagicMock(return_value=False)

        with patch("scripts.scrape_movies._PLAYWRIGHT_AVAILABLE", True):
            with patch("scripts.scrape_movies._sync_playwright") as mock_pw_cls:
                mock_pw = MagicMock()
                mock_pw.__enter__ = MagicMock(return_value=mock_pw)
                mock_pw.__exit__ = MagicMock(return_value=False)
                mock_pw_cls.return_value = mock_pw

                mock_browser = MagicMock()
                mock_pw.chromium.launch.return_value = mock_browser

                mock_ctx = MagicMock()
                mock_browser.new_context.return_value = mock_ctx

                mock_page = MagicMock()
                mock_ctx.new_page.return_value = mock_page
                mock_page.goto.return_value = None

                # _list_theaters will return [] because button not found
                mock_page.query_selector.return_value = None

                movies, errors = scrape_with_playwright()

        self.assertEqual([], movies)
        self.assertTrue(any("theater" in e.lower() or "dropdown" in e.lower() for e in errors))
