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
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    flags=re.IGNORECASE | re.DOTALL,
)


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


def extract_event_nodes(html: str) -> list[dict[str, Any]]:
    event_nodes: list[dict[str, Any]] = []
    for raw_block in SCRIPT_TAG_PATTERN.findall(html):
        block = raw_block.strip()
        if not block:
            continue
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue

        for node in _iter_json_objects(parsed):
            node_type = node.get("@type")
            if isinstance(node_type, list):
                is_event = "Event" in node_type
            else:
                is_event = node_type == "Event"
            if is_event:
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
    if isinstance(title, str) and title.strip():
        return title.strip()

    return "TBA"


def scrape_source(source: dict[str, str]) -> list[dict[str, str]]:
    html = fetch_html(source["url"])
    events: list[dict[str, str]] = []

    for node in extract_event_nodes(html):
        event_url = node.get("url")
        if isinstance(event_url, str):
            link = urljoin(source["url"], event_url)
        else:
            offer = node.get("offers")
            link = ""
            if isinstance(offer, dict) and isinstance(offer.get("url"), str):
                link = urljoin(source["url"], offer["url"])

        events.append(
            {
                "date": normalize_date(node.get("startDate")),
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
