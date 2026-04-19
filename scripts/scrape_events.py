#!/usr/bin/env python3
"""Scrape events from configured venues and write data/events.json."""

from __future__ import annotations

import json
import csv
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "events.json"
CSV_FILE = Path(__file__).resolve().parent.parent / "raw-data" / "events.csv"

SOURCES = [
    {
        "venue": "Underground Arts",
        "url": "https://undergroundarts.org/events/",
    },
    {
        "venue": "Union Transfer",
        "url": "https://www.utphilly.com/calendar/",
    },
    {
        "venue": "The Fillmore",
        "url": "https://www.thefillmorephilly.com/shows",
    },
]

SCRIPT_TAG_PATTERN = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script[^>]*>",
    flags=re.IGNORECASE | re.DOTALL,
)
INLINE_SCRIPT_PATTERN = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script[^>]*>",
    flags=re.IGNORECASE | re.DOTALL,
)
EVENT_KEYWORD_PATTERN = re.compile(
    r"\b(event|startDate|start_date|showDate)\b",
    flags=re.IGNORECASE,
)

EVENT_DATE_KEYS = (
    "startDate",
    "start_date",
    "eventDate",
    "event_date",
    "eventDateLocal",
    "showDate",
    "date",
    "dateTime",
    "startsAt",
    "start",
)
EVENT_NAME_KEYS = ("name", "title", "eventName", "event_name", "headline", "artist")
EVENT_URL_KEYS = ("url", "link", "eventUrl", "event_url", "permalink", "eventLink")
UNION_TRANSFER_EVENT_LINK_PATTERN = re.compile(
    r"(?:https?://www\.utphilly\.com)?(?:\\?/)+events(?:\\?/)+detail(?:\\?/)+\?event_id=\d+",
    flags=re.IGNORECASE,
)
H2_TEXT_PATTERN = re.compile(r"<h2[^>]*>(.*?)</h2>", flags=re.IGNORECASE | re.DOTALL)
TAG_PATTERN = re.compile(r"<[^>]+>")


def fetch_html(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Facehuggers Event Scraper)"},
    )
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


def _first_nonempty_string(node: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _first_nonempty_nested_string(node: dict[str, Any], keys: tuple[str, ...]) -> str:
    for nested in _iter_json_objects(node):
        if isinstance(nested, dict):
            value = _first_nonempty_string(nested, keys)
            if value:
                return value
    return ""


def _extract_link(node: dict[str, Any], source_url: str) -> str:
    direct_link = _first_nonempty_nested_string(node, EVENT_URL_KEYS)
    if direct_link:
        return urljoin(source_url, direct_link)

    offer = node.get("offers")
    if isinstance(offer, dict):
        offer_url = _first_nonempty_string(offer, ("url", "link"))
        if offer_url:
            return urljoin(source_url, offer_url)
    elif isinstance(offer, list):
        for entry in offer:
            if isinstance(entry, dict):
                offer_url = _first_nonempty_string(entry, ("url", "link"))
                if offer_url:
                    return urljoin(source_url, offer_url)

    return ""


def _clean_html_text(value: str) -> str:
    text = TAG_PATTERN.sub(" ", value)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _extract_h2_title(html: str) -> str:
    for match in H2_TEXT_PATTERN.findall(html):
        title = _clean_html_text(match)
        if title:
            return title
    return ""


def _find_union_transfer_event_links(html: str, source_url: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()

    for raw_match in UNION_TRANSFER_EVENT_LINK_PATTERN.findall(html):
        raw_link = raw_match.replace("\\/", "/")
        absolute_link = urljoin(source_url, raw_link)
        if absolute_link not in seen:
            seen.add(absolute_link)
            links.append(absolute_link)

    return links


def _event_from_node(node: dict[str, Any], source: dict[str, str], default_link: str = "") -> dict[str, str]:
    link = _extract_link(node, source["url"]) or default_link
    raw_start_date: Any = _first_nonempty_string(node, EVENT_DATE_KEYS)
    bands = _first_nonempty_string(node, EVENT_NAME_KEYS)
    if not bands:
        bands = extract_band_names(node)
    return {
        "date": normalize_date(raw_start_date),
        "bands": bands,
        "venue": source["venue"],
        "link": link,
    }


def _scrape_union_transfer_events(source: dict[str, str], calendar_html: str) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    seen_links: set[str] = set()

    for node in extract_event_nodes(calendar_html):
        event = _event_from_node(node, source)
        if event["link"]:
            seen_links.add(event["link"])
        events.append(event)

    detail_links = _find_union_transfer_event_links(calendar_html, source["url"])
    for detail_link in detail_links:
        if detail_link in seen_links:
            continue

        detail_html = fetch_html(detail_link)
        detail_nodes = extract_event_nodes(detail_html)
        if detail_nodes:
            for node in detail_nodes:
                event = _event_from_node(node, source, default_link=detail_link)
                if event["link"] in seen_links:
                    continue
                seen_links.add(event["link"])
                events.append(event)
            continue

        fallback_title = _extract_h2_title(detail_html)
        if fallback_title:
            seen_links.add(detail_link)
            events.append(
                {
                    "date": "TBA",
                    "bands": fallback_title,
                    "venue": source["venue"],
                    "link": detail_link,
                }
            )

    return events


def _iter_json_fragments(text: str):
    decoder = json.JSONDecoder()
    index = 0
    text_length = len(text)

    while index < text_length:
        char = text[index]
        if char not in "{[":
            index += 1
            continue

        try:
            parsed, consumed = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            index += 1
            continue

        yield parsed
        index += consumed


def _is_event_node(node: dict[str, Any]) -> bool:
    node_type = node.get("@type")
    if isinstance(node_type, list):
        if "Event" in node_type:
            return True
    elif node_type == "Event":
        return True

    has_date = bool(_first_nonempty_string(node, EVENT_DATE_KEYS))
    has_name = bool(_first_nonempty_string(node, EVENT_NAME_KEYS))
    has_url = bool(_first_nonempty_string(node, EVENT_URL_KEYS))
    if not has_url and isinstance(node.get("offers"), (dict, list)):
        has_url = bool(_extract_link(node, ""))
    return has_date and has_name and has_url


def _event_marker(node: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _first_nonempty_string(node, EVENT_NAME_KEYS),
        _first_nonempty_string(node, EVENT_DATE_KEYS),
        _first_nonempty_string(node, EVENT_URL_KEYS),
    )


def extract_event_nodes(html: str) -> list[dict[str, Any]]:
    event_nodes: list[dict[str, Any]] = []
    seen_nodes: set[tuple[Any, ...]] = set()
    raw_blocks = SCRIPT_TAG_PATTERN.findall(html)
    for script_block in INLINE_SCRIPT_PATTERN.findall(html):
        if EVENT_KEYWORD_PATTERN.search(script_block):
            raw_blocks.append(script_block)
    for raw_block in raw_blocks:
        block = raw_block.strip()
        if not block:
            continue

        parsed_blocks: list[Any] = []
        try:
            parsed_blocks.append(json.loads(block))
        except json.JSONDecodeError:
            parsed_blocks.extend(_iter_json_fragments(block))

        for parsed in parsed_blocks:
            for node in _iter_json_objects(parsed):
                if _is_event_node(node):
                    marker = _event_marker(node)
                    if marker in seen_nodes:
                        continue
                    seen_nodes.add(marker)
                    event_nodes.append(node)
    return event_nodes


def normalize_date(raw_date: Any) -> str:
    if isinstance(raw_date, (int, float)):
        timestamp = float(raw_date)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
        except (ValueError, OSError, OverflowError):
            return "TBA"

    if not isinstance(raw_date, str) or not raw_date.strip():
        return "TBA"
    value = raw_date.strip()

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        date_match = re.match(r"^(\d{4}-\d{2}-\d{2})", value)
        if date_match:
            return date_match.group(1)
        return value

    return parsed.date().isoformat()


def extract_band_names(node: dict[str, Any]) -> str:
    performers = node.get("performer")
    names: list[str] = []

    def is_organization_placeholder(value: str) -> bool:
        """Detect Underground Arts placeholder performer labels."""
        return value.strip().lower() == "organization"

    def add_name(candidate: Any):
        if isinstance(candidate, str) and candidate.strip():
            if is_organization_placeholder(candidate):
                return
            names.append(unescape(candidate.strip()))
        elif isinstance(candidate, dict):
            # Skip Organization-typed entries – these are venues/promoters, not acts.
            candidate_type = candidate.get("@type")
            if candidate_type == "Organization" or (
                isinstance(candidate_type, list) and "Organization" in candidate_type
            ):
                return
            name = candidate.get("name")
            if isinstance(name, str) and name.strip():
                if is_organization_placeholder(name):
                    return
                names.append(unescape(name.strip()))

    if isinstance(performers, list):
        for performer in performers:
            add_name(performer)
    else:
        add_name(performers)

    if names:
        deduped = list(dict.fromkeys(names))
        return ", ".join(deduped)

    # Fall back to the event's own name (e.g. show title) when performer data
    # is absent or was entirely made up of Organization placeholders.
    title = node.get("name")
    if not isinstance(title, str) or not title.strip():
        title = node.get("title")
    if isinstance(title, str) and title.strip():
        return unescape(title.strip())

    return "TBA"


def scrape_source(source: dict[str, str]) -> list[dict[str, str]]:
    html = fetch_html(source["url"])
    if source["venue"] == "Union Transfer":
        return _scrape_union_transfer_events(source, html)

    events: list[dict[str, str]] = []

    for node in extract_event_nodes(html):
        events.append(_event_from_node(node, source))

    return events


def sort_key(event: dict[str, str]):
    date = event.get("date", "")
    date_weight = date if re.match(r"^\d{4}-\d{2}-\d{2}$", date) else "9999-99-99"
    return (date_weight, event.get("venue", ""), event.get("bands", ""))


def load_existing_events() -> list[dict[str, str]]:
    if not DATA_FILE.exists():
        return []
    try:
        payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    events = payload.get("events")
    return events if isinstance(events, list) else []


def write_csv(events: list[dict[str, str]]) -> None:
    CSV_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CSV_FILE.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=["date", "bands", "venue", "link"])
        writer.writeheader()
        writer.writerows(events)


def main() -> None:
    all_events: list[dict[str, str]] = []
    errors: list[str] = []

    for source in SOURCES:
        try:
            source_events = scrape_source(source)
            if not source_events:
                errors.append(
                    f"{source['venue']}: no events found – the page may require "
                    "JavaScript rendering or its schema has changed"
                )
            all_events.extend(source_events)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            errors.append(f"{source['venue']}: {exc}")

    if not all_events:
        all_events = load_existing_events()

    all_events.sort(key=sort_key)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": SOURCES,
        "events": all_events,
        "errors": errors,
    }

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_csv(all_events)


if __name__ == "__main__":
    main()
