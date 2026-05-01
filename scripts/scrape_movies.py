#!/usr/bin/env python3
"""Scrape movie showtimes from Landmark Theatres and write data/movies.json."""

from __future__ import annotations

import csv
import json
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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


def fetch_html(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


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
        return m.group(1) if m else value


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
            link = v.strip()
            if not link.startswith("http"):
                link = "https://www.landmarktheatres.com" + link
            return link
    offers = node.get("offers")
    if isinstance(offers, dict):
        v = offers.get("url") or offers.get("link")
        if isinstance(v, str) and v.strip():
            return v.strip()
    if isinstance(offers, list):
        for offer in offers:
            if isinstance(offer, dict):
                v = offer.get("url") or offer.get("link")
                if isinstance(v, str) and v.strip():
                    return v.strip()
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


def scrape_all_dates() -> tuple[list[dict[str, str]], list[str]]:
    all_movies: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    errors: list[str] = []
    today = date.today()
    empty_streak = 0

    for offset in range(MAX_DAYS):
        current = today + timedelta(days=offset)
        date_str = current.isoformat()

        try:
            html = fetch_html(f"{BASE_URL}?date={date_str}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            errors.append(f"{date_str}: {exc}")
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

    return all_movies, errors


def main() -> None:
    all_movies, errors = scrape_all_dates()

    if not all_movies:
        all_movies = load_existing_movies()

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
