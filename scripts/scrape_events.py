#!/usr/bin/env python3
"""Scrape events from configured venues and write data/events.json."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "events.json"

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

EVENT_DATE_KEYS = ("startDate", "start_date", "eventDate", "showDate")
EVENT_NAME_KEYS = ("name", "title", "eventName")
EVENT_URL_KEYS = ("url", "link", "eventUrl", "permalink")


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

    def has_nonempty_string_value(keys: tuple[str, ...]) -> bool:
        for key in keys:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return True
        return False

    has_date = has_nonempty_string_value(EVENT_DATE_KEYS)
    has_name = has_nonempty_string_value(EVENT_NAME_KEYS)
    has_url = has_nonempty_string_value(EVENT_URL_KEYS)
    return has_date and has_name and has_url


def _event_marker(node: dict[str, Any]) -> tuple[Any, ...]:
    return (
        node.get("name") or node.get("title") or node.get("eventName"),
        node.get("startDate") or node.get("start_date") or node.get("eventDate") or node.get("showDate"),
        node.get("url") or node.get("link") or node.get("eventUrl") or node.get("permalink"),
    )


def extract_event_nodes(html: str) -> list[dict[str, Any]]:
    event_nodes: list[dict[str, Any]] = []
    seen_nodes: set[tuple[Any, ...]] = set()
    raw_blocks = SCRIPT_TAG_PATTERN.findall(html)
    for script_block in INLINE_SCRIPT_PATTERN.findall(html):
        if re.search(r"\b(event|startDate|start_date|showDate)\b", script_block, flags=re.IGNORECASE):
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
            names.append(candidate.strip())
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
                names.append(name.strip())

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
        return title.strip()

    return "TBA"


def scrape_source(source: dict[str, str]) -> list[dict[str, str]]:
    html = fetch_html(source["url"])
    events: list[dict[str, str]] = []

    for node in extract_event_nodes(html):
        event_url: Any = None
        for url_key in EVENT_URL_KEYS:
            candidate = node.get(url_key)
            if isinstance(candidate, str) and candidate.strip():
                event_url = candidate
                break
        if isinstance(event_url, str):
            link = urljoin(source["url"], event_url)
        else:
            offer = node.get("offers")
            link = ""
            if isinstance(offer, dict) and isinstance(offer.get("url"), str):
                link = urljoin(source["url"], offer["url"])

        raw_start_date: Any = None
        for date_key in EVENT_DATE_KEYS:
            candidate = node.get(date_key)
            if candidate:
                raw_start_date = candidate
                break

        events.append(
            {
                "date": normalize_date(raw_start_date),
                "bands": extract_band_names(node),
                "venue": source["venue"],
                "link": link,
            }
        )

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


if __name__ == "__main__":
    main()
