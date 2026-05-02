#!/usr/bin/env python3
"""Scrape movie showtimes from Landmark Theatres and write data/movies.json.

The Landmark Theatres website is a Gatsby SPA whose JavaScript is served from
a CDN.  When the CDN is reachable (normal environments, GitHub Actions), the
Playwright code path renders the page fully, selects each theater from the
location dropdown, and reads the rendered HTML.  When the CDN is unreachable
(sandboxed/restricted networks), the script falls back to urllib and finally to
whatever data is already cached in ``data/movies.json``.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

# Playwright is an optional dependency used for JS-rendered pages.
# When not installed, the scraper falls back to urllib (which returns an empty
# shell for the Gatsby-based Landmark Theatres site).
try:
    from playwright.sync_api import (
        sync_playwright as _sync_playwright,
        TimeoutError as _PlaywrightTimeoutError,
    )

    _PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PLAYWRIGHT_AVAILABLE = False

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "movies.json"
CSV_FILE = Path(__file__).resolve().parent.parent / "raw-data" / "movies.csv"

BASE_URL = "https://www.landmarktheatres.com/showtimes/"
MAX_DAYS = 90
MAX_EMPTY_DAYS = 3
USER_AGENT = "Mozilla/5.0 (Facehuggers Movie Scraper)"

NEXT_DATA_PATTERN = re.compile(
    r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
JSONLD_PATTERN = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script[^>]*>",
    flags=re.IGNORECASE | re.DOTALL,
)
INLINE_SCRIPT_PATTERN = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script[^>]*>",
    flags=re.IGNORECASE | re.DOTALL,
)
MOVIE_KEYWORD_PATTERN = re.compile(
    r"\b(movie|film|showtime|screening|genre|ScreeningEvent)\b",
    flags=re.IGNORECASE,
)

MOVIE_NAME_KEYS = ("name", "title", "filmTitle", "movieTitle", "headline")
MOVIE_GENRE_KEYS = ("genre", "genres", "filmGenre", "movieGenre")
MOVIE_DATE_KEYS = ("startDate", "showDate", "date", "dateTime", "startsAt", "start")
MOVIE_URL_KEYS = ("url", "link", "ticketUrl", "buyTicketsLink", "permalink")
LOCATION_NAME_KEYS = ("name", "title", "theaterName")

SCREENING_TYPES = frozenset({"ScreeningEvent", "MovieEvent", "Event", "TheaterEvent"})


def _warn(message: str) -> None:
    """Print a warning that is visible in GitHub Actions as an annotation.

    When running inside a GitHub Actions workflow (``GITHUB_ACTIONS=true``),
    the message is emitted using the ``::warning::`` workflow command so that
    it appears as a yellow warning annotation in the Actions UI.  In all other
    environments the message is written to *stderr*.
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning::{message}", flush=True)
    else:
        print(f"WARNING: {message}", file=sys.stderr, flush=True)


def fetch_html(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_html_with_browser(
    url: str,
    wait_until: str = "networkidle",
    timeout_ms: int = 60_000,
) -> str:
    """Fetch a JS-rendered page using a headless Chromium browser via Playwright.

    Falls back to :func:`fetch_html` (urllib) when Playwright is not installed.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        return fetch_html(url)

    with _sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                user_agent=USER_AGENT,
                ignore_https_errors=True,
            )
            page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            return page.content()
        finally:
            browser.close()


def _iter_json_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _iter_json_objects(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_json_objects(item)


def _iter_json_fragments(text: str):
    decoder = json.JSONDecoder()
    index = 0
    length = len(text)
    while index < length:
        if text[index] not in "{[":
            index += 1
            continue
        try:
            parsed, consumed = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            index += 1
            continue
        yield parsed
        index += consumed


def _first_str(node: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str) and first.strip():
                return first.strip()
    return ""


def normalize_date(raw: Any) -> str:
    if isinstance(raw, (int, float)):
        timestamp = float(raw)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
        except (ValueError, OSError, OverflowError):
            return "TBA"
    if not isinstance(raw, str) or not raw.strip():
        return "TBA"
    value = raw.strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.date().isoformat()
    except ValueError:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", value)
        return m.group(1) if m else "TBA"


def _extract_genre(node: dict[str, Any]) -> str:
    for key in MOVIE_GENRE_KEYS:
        v = node.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, list):
            genres = [g for g in v if isinstance(g, str) and g.strip()]
            if genres:
                return ", ".join(genres)
    return ""


def _extract_location(node: dict[str, Any]) -> str:
    loc = node.get("location")
    if isinstance(loc, str) and loc.strip():
        return loc.strip()
    if isinstance(loc, dict):
        name = _first_str(loc, LOCATION_NAME_KEYS)
        if name:
            return name
    if isinstance(loc, list) and loc:
        first = loc[0]
        if isinstance(first, dict):
            return _first_str(first, LOCATION_NAME_KEYS)
        if isinstance(first, str) and first.strip():
            return first.strip()
    # Also check direct theater-name fields
    for key in ("theater", "venue", "theaterName", "venueName"):
        v = node.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            name = _first_str(v, LOCATION_NAME_KEYS)
            if name:
                return name
    return ""


def _extract_link(node: dict[str, Any]) -> str:
    for key in MOVIE_URL_KEYS:
        v = node.get(key)
        if isinstance(v, str) and v.strip():
            return urljoin(BASE_URL, v.strip())
    offers = node.get("offers")
    if isinstance(offers, dict):
        v = offers.get("url") or offers.get("link")
        if isinstance(v, str) and v.strip():
            return urljoin(BASE_URL, v.strip())
    if isinstance(offers, list):
        for offer in offers:
            if isinstance(offer, dict):
                v = offer.get("url") or offer.get("link")
                if isinstance(v, str) and v.strip():
                    return urljoin(BASE_URL, v.strip())
    return ""


def _is_screening_node(node: dict[str, Any]) -> bool:
    node_type = node.get("@type")
    if isinstance(node_type, str) and node_type in SCREENING_TYPES:
        return True
    if isinstance(node_type, list) and any(t in SCREENING_TYPES for t in node_type):
        return True
    # Heuristic: has movie-like fields without explicit type
    has_title = bool(_first_str(node, MOVIE_NAME_KEYS))
    has_location = bool(_extract_location(node))
    has_date = bool(_first_str(node, MOVIE_DATE_KEYS))
    has_work = isinstance(node.get("workPresented"), dict)
    return has_title and has_date and (has_location or has_work)


def _movie_from_node(node: dict[str, Any], date_str: str) -> dict[str, str] | None:
    title = _first_str(node, MOVIE_NAME_KEYS)
    genre = _extract_genre(node)

    # ScreeningEvent often nests the Movie in workPresented
    work = node.get("workPresented")
    if isinstance(work, dict):
        work_title = _first_str(work, MOVIE_NAME_KEYS)
        if work_title:
            title = work_title
        if not genre:
            genre = _extract_genre(work)

    if not title:
        return None

    raw_date = _first_str(node, MOVIE_DATE_KEYS)
    event_date = normalize_date(raw_date) if raw_date else date_str
    location = _extract_location(node) or "Landmark Theatres"
    link = _extract_link(node)

    return {
        "date": event_date,
        "title": title,
        "genre": genre or "N/A",
        "location": location,
        "link": link,
    }


def extract_movies_from_html(html: str, date_str: str) -> list[dict[str, str]]:
    movies: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def _add_movies_from_blocks(blocks: list[str]) -> None:
        for raw_block in blocks:
            block = raw_block.strip()
            if not block:
                continue
            parsed_list: list[Any] = []
            try:
                parsed_list.append(json.loads(block))
            except json.JSONDecodeError:
                parsed_list.extend(_iter_json_fragments(block))
            for parsed in parsed_list:
                for node in _iter_json_objects(parsed):
                    if not isinstance(node, dict) or not _is_screening_node(node):
                        continue
                    movie = _movie_from_node(node, date_str)
                    if not movie:
                        continue
                    key = (movie["title"], movie["date"], movie["location"])
                    if key not in seen:
                        seen.add(key)
                        movies.append(movie)

    # Priority 1: __NEXT_DATA__ (Next.js apps embed all page data here)
    next_match = NEXT_DATA_PATTERN.search(html)
    if next_match:
        _add_movies_from_blocks([next_match.group(1)])

    # Priority 2: JSON-LD script tags
    _add_movies_from_blocks(JSONLD_PATTERN.findall(html))

    # Priority 3: Other inline scripts with movie-related keywords
    if not movies:
        extra = [
            s for s in INLINE_SCRIPT_PATTERN.findall(html)
            if MOVIE_KEYWORD_PATTERN.search(s)
        ]
        _add_movies_from_blocks(extra)

    return movies


def load_existing_movies() -> list[dict[str, str]]:
    if not DATA_FILE.exists():
        return []
    try:
        payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    movies = payload.get("movies")
    return movies if isinstance(movies, list) else []


def write_csv(movies: list[dict[str, str]]) -> None:
    CSV_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CSV_FILE.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "title", "genre", "location", "link"])
        writer.writeheader()
        writer.writerows(movies)


# ---------------------------------------------------------------------------
# Playwright-based scraping (used when the CDN that serves the Gatsby JS is
# reachable; gracefully skipped when it is not).
# ---------------------------------------------------------------------------

def _select_theater(page: Any, theater_name: str) -> bool:
    """Open the location dropdown and click the given theater option.

    Returns True if the theater was successfully selected.
    """
    # The location button exposes aria-expanded to indicate the dropdown state.
    loc_button = page.query_selector("button[aria-expanded]")
    if not loc_button:
        return False

    # Open the dropdown if it is not already open.
    if loc_button.get_attribute("aria-expanded") != "true":
        loc_button.click()

    # Wait briefly for the options list to materialise.
    try:
        page.wait_for_function(
            "() => document.querySelectorAll('[role=\"option\"]').length > 0"
            " || document.querySelectorAll('[role=\"listbox\"] li').length > 0",
            timeout=5_000,
        )
    except _PlaywrightTimeoutError:
        return False

    # Find and click the matching option.
    # Escape single-quotes in the theater name for the CSS :has-text() selector.
    escaped = theater_name.replace("'", "\\'")
    theater_opt = page.query_selector(
        f"[role='option']:has-text('{escaped}'), "
        f"[role='listbox'] li:has-text('{escaped}')"
    )
    if not theater_opt:
        return False

    theater_opt.click()
    return True


def _list_theaters(page: Any) -> list[str]:
    """Return the names of all theaters available in the location dropdown."""
    loc_button = page.query_selector("button[aria-expanded]")
    if not loc_button:
        return []

    loc_button.click()

    try:
        page.wait_for_function(
            "() => document.querySelectorAll('[role=\"option\"]').length > 0"
            " || document.querySelectorAll('[role=\"listbox\"] li').length > 0",
            timeout=8_000,
        )
    except _PlaywrightTimeoutError:
        return []

    for selector in ("[role='option']", "[role='listbox'] li"):
        opts = page.query_selector_all(selector)
        if opts:
            names = [o.inner_text().strip() for o in opts]
            return [n for n in names if n]

    return []


def _extract_movies_from_page(
    page: Any, theater_name: str, date_str: str
) -> list[dict[str, str]]:
    """Extract movie showtimes from the currently rendered page.

    Tries the existing HTML-based extraction first (handles JSON-LD and
    ``__NEXT_DATA__``), then falls back to a lightweight DOM query that looks
    for ticket/booking links and their surrounding movie-title elements.
    """
    html = page.content()
    movies: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    # Primary: existing structured-data extraction (JSON-LD, __NEXT_DATA__).
    for m in extract_movies_from_html(html, date_str):
        if m["location"] == "Landmark Theatres":
            m["location"] = theater_name
        key = (m["title"], m["date"], m["location"])
        if key not in seen:
            seen.add(key)
            movies.append(m)

    if movies:
        return movies

    # Fallback: query the rendered DOM via JavaScript for ticket links and
    # the movie headings that contain them.
    try:
        dom_movies: list[dict[str, str]] = page.evaluate(
            """
            (theaterName, dateStr) => {
                const results = [];
                const seen = new Set();

                // Ticket-purchase link patterns shared by most US cinemas.
                const ticketSelectors = [
                    'a[href*="fandango"]',
                    'a[href*="axs.com"]',
                    'a[href*="ticketmaster"]',
                    'a[href*="atom.tickets"]',
                    'a[href*="movieglu"]',
                    'a[href*="vista"]',
                    'a[href*="/tickets"]',
                    'a[href*="showtimes"]',
                ].join(', ');

                const ticketLinks = document.querySelectorAll(ticketSelectors);

                ticketLinks.forEach(link => {
                    let el = link;
                    let title = '';
                    for (let depth = 0; depth < 8 && el; depth++) {
                        const h = el.querySelector && el.querySelector('h1,h2,h3,h4');
                        if (h && h.textContent.trim()) {
                            title = h.textContent.trim();
                            break;
                        }
                        el = el.parentElement;
                    }
                    if (!title) return;
                    const key = title + '|' + dateStr + '|' + theaterName;
                    if (seen.has(key)) return;
                    seen.add(key);
                    results.push({
                        date: dateStr,
                        title: title,
                        genre: 'N/A',
                        location: theaterName,
                        link: link.href || '',
                    });
                });

                // If no ticket links found, try heading elements inside
                // movie-card-like containers.
                if (results.length === 0) {
                    const headings = document.querySelectorAll(
                        '[class*="movie"] h1,[class*="movie"] h2,[class*="movie"] h3,' +
                        '[class*="film"] h2,[class*="film"] h3,' +
                        '[class*="show"] h2,[class*="show"] h3'
                    );
                    headings.forEach(h => {
                        const title = h.textContent.trim();
                        if (!title) return;
                        const link =
                            (h.querySelector('a') || h.closest('a') || {}).href || '';
                        const key = title + '|' + dateStr + '|' + theaterName;
                        if (seen.has(key)) return;
                        seen.add(key);
                        results.push({
                            date: dateStr,
                            title: title,
                            genre: 'N/A',
                            location: theaterName,
                            link: link,
                        });
                    });
                }

                return results;
            }
            """,
            theater_name,
            date_str,
        )
    except Exception:
        dom_movies = []

    for m in (dom_movies or []):
        key = (m["title"], m["date"], m["location"])
        if key not in seen:
            seen.add(key)
            movies.append(m)

    return movies


def scrape_with_playwright() -> tuple[list[dict[str, str]], list[str]]:
    """Scrape showtimes for all Landmark Theatres locations using Playwright.

    The Landmark Theatres site is a Gatsby SPA: the location dropdown and
    showtime listings are rendered by JavaScript.  This function:

    1. Opens the showtimes page for each date (``?date=YYYY-MM-DD``).
    2. Clicks the location dropdown button (identified by ``aria-expanded``).
    3. Iterates every theater option and selects it.
    4. Extracts rendered movie data via :func:`_extract_movies_from_page`.
    5. Stops early once ``MAX_EMPTY_DAYS`` consecutive dates yield no results.

    Returns ``(movies, errors)``.  The list may be empty when the CDN that
    serves the site's JavaScript is unreachable (e.g. sandboxed networks).
    """
    if not _PLAYWRIGHT_AVAILABLE:
        return [], [
            "Playwright not installed – install with: "
            "pip install playwright && python -m playwright install chromium"
        ]

    all_movies: list[dict[str, str]] = []
    errors: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    today = date.today()
    empty_streak = 0
    theater_names: list[str] = []

    try:
        with _sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=USER_AGENT,
                    ignore_https_errors=True,
                )
                page = context.new_page()

                for offset in range(MAX_DAYS):
                    current = today + timedelta(days=offset)
                    date_str = current.isoformat()

                    try:
                        page.goto(
                            f"{BASE_URL}?date={date_str}",
                            wait_until="networkidle",
                            timeout=60_000,
                        )
                    except _PlaywrightTimeoutError:
                        # networkidle may time-out when CDN resources are blocked;
                        # proceed anyway with whatever content loaded.
                        pass
                    except Exception as exc:
                        errors.append(f"{date_str}: navigation error: {exc}")
                        empty_streak += 1
                        if empty_streak >= MAX_EMPTY_DAYS:
                            break
                        continue

                    # Discover theater names on the first usable page load.
                    if not theater_names:
                        theater_names = _list_theaters(page)
                        if not theater_names:
                            errors.append(
                                "No theater options found in the location dropdown – "
                                "the CDN serving the site's JavaScript may be unreachable"
                            )
                            break

                    day_movies: list[dict[str, str]] = []

                    for theater_name in theater_names:
                        try:
                            if not _select_theater(page, theater_name):
                                errors.append(
                                    f"{date_str}/{theater_name}: "
                                    "could not select theater in dropdown"
                                )
                                continue
                            # Brief pause to let the showtime content render.
                            try:
                                page.wait_for_load_state("networkidle", timeout=10_000)
                            except _PlaywrightTimeoutError:
                                pass
                        except Exception as exc:
                            errors.append(f"{date_str}/{theater_name}: {exc}")
                            continue

                        for m in _extract_movies_from_page(page, theater_name, date_str):
                            key = (m["title"], m["date"], m["location"])
                            if key not in seen:
                                seen.add(key)
                                day_movies.append(m)

                    if not day_movies:
                        empty_streak += 1
                        if empty_streak >= MAX_EMPTY_DAYS:
                            break
                    else:
                        empty_streak = 0
                        all_movies.extend(day_movies)

            finally:
                browser.close()

    except Exception as exc:
        errors.append(f"Playwright scraping failed: {type(exc).__name__}: {exc}")

    return all_movies, errors


def scrape_all_dates() -> tuple[list[dict[str, str]], list[str]]:
    """Scrape showtimes, trying Playwright first and falling back to urllib.

    Playwright handles the Gatsby SPA by executing its JavaScript and
    interacting with the location dropdown.  The urllib fallback handles
    legacy/SSR pages that embed data in ``__NEXT_DATA__`` or JSON-LD.
    """
    combined_errors: list[str] = []

    # Primary: Playwright (handles JS-rendered Gatsby SPA).
    if _PLAYWRIGHT_AVAILABLE:
        movies, pw_errors = scrape_with_playwright()
        combined_errors.extend(pw_errors)
        if movies:
            return movies, combined_errors

    # Fallback: urllib HTML scraping (works if the site ever returns server-
    # rendered data, e.g. JSON-LD or __NEXT_DATA__).
    all_movies: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    today = date.today()
    empty_streak = 0

    for offset in range(MAX_DAYS):
        current = today + timedelta(days=offset)
        date_str = current.isoformat()

        try:
            html = fetch_html(f"{BASE_URL}?date={date_str}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            combined_errors.append(f"{date_str}: {type(exc).__name__}: {exc}")
            empty_streak += 1
            if empty_streak >= MAX_EMPTY_DAYS:
                break
            continue

        day_movies = extract_movies_from_html(html, date_str)

        if not day_movies:
            empty_streak += 1
            if empty_streak >= MAX_EMPTY_DAYS:
                break
        else:
            empty_streak = 0
            for m in day_movies:
                key = (m["title"], m["date"], m["location"])
                if key not in seen:
                    seen.add(key)
                    all_movies.append(m)

    return all_movies, combined_errors


def main() -> None:
    all_movies, errors = scrape_all_dates()

    if errors:
        for error in errors:
            _warn(f"Movie scraping error: {error}")

    if not all_movies:
        _warn(
            "No movie data was scraped from any website. "
            "The Landmark Theatres site may be unreachable or its structure may have changed."
        )
        cached = load_existing_movies()
        if cached:
            _warn(
                f"Falling back to {len(cached)} cached movie record(s) from data/movies.json. "
                "Data may be out of date."
            )
            all_movies = cached
        else:
            _warn(
                "No cached movie data found either. data/movies.json will contain an empty list."
            )

    all_movies.sort(key=lambda m: (m["date"], m["location"], m["title"]))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": BASE_URL,
        "movies": all_movies,
        "errors": errors,
    }

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_csv(all_movies)


if __name__ == "__main__":
    main()
