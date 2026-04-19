#!/usr/bin/env python3
"""Inspect Union Transfer pages to locate event data structures."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.scrape_events import _find_union_transfer_event_links, _iter_json_fragments, fetch_html

DEFAULT_URL = "https://www.utphilly.com/calendar/"
SCRIPT_BLOCK_PATTERN = re.compile(
    r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script[^>]*>",
    flags=re.IGNORECASE | re.DOTALL,
)
TYPE_ATTR_PATTERN = re.compile(r"\btype\s*=\s*([\"'])(.*?)\1", flags=re.IGNORECASE)
EVENT_HINT_PATTERN = re.compile(
    r"\b(event|events|startDate|start_date|showDate|event_id|date|performer|artist)\b",
    flags=re.IGNORECASE,
)
KEY_HINT_PATTERN = {
    "@type",
    "name",
    "title",
    "event",
    "events",
    "startDate",
    "start_date",
    "eventDate",
    "eventDateLocal",
    "showDate",
    "date",
    "dateTime",
    "performer",
    "artist",
    "url",
    "link",
    "eventUrl",
    "eventLink",
    "offers",
}


@dataclass
class ScriptBlock:
    index: int
    attrs: str
    body: str



def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()



def extract_script_blocks(html: str) -> list[ScriptBlock]:
    blocks: list[ScriptBlock] = []
    for index, match in enumerate(SCRIPT_BLOCK_PATTERN.finditer(html)):
        blocks.append(
            ScriptBlock(
                index=index,
                attrs=match.group("attrs") or "",
                body=(match.group("body") or "").strip(),
            )
        )
    return blocks



def summarize_script_blocks(html: str, max_preview: int = 180) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for block in extract_script_blocks(html):
        type_match = TYPE_ATTR_PATTERN.search(block.attrs)
        script_type = type_match.group(2).strip() if type_match else ""
        preview = _collapse_whitespace(block.body)[:max_preview]
        summaries.append(
            {
                "index": block.index,
                "type": script_type or "inline/unspecified",
                "length": len(block.body),
                "event_hints": bool(EVENT_HINT_PATTERN.search(block.body)),
                "preview": preview,
            }
        )
    return summaries



def find_text_matches(text: str, needle: str, context: int = 140) -> list[str]:
    if not needle:
        return []

    lowered = text.lower()
    target = needle.lower()
    start = 0
    snippets: list[str] = []

    while True:
        idx = lowered.find(target, start)
        if idx < 0:
            break
        left = max(0, idx - context)
        right = min(len(text), idx + len(needle) + context)
        snippets.append(text[left:right].strip())
        start = idx + len(target)

    return snippets



def _iter_json_objects(value: Any, path: str = "$"):
    if isinstance(value, dict):
        yield value, path
        for key, nested in value.items():
            yield from _iter_json_objects(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from _iter_json_objects(nested, f"{path}[{index}]")



def parse_json_fragments(text: str) -> list[Any]:
    content = text.strip()
    if not content:
        return []

    fragments: list[Any] = []
    try:
        fragments.append(json.loads(content))
        return fragments
    except json.JSONDecodeError:
        pass

    for parsed in _iter_json_fragments(content):
        fragments.append(parsed)

    return fragments



def collect_event_like_nodes(value: Any) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []

    for node, path in _iter_json_objects(value):
        if not isinstance(node, dict):
            continue

        matching_keys = sorted(key for key in node if key in KEY_HINT_PATTERN)
        if not matching_keys:
            continue

        sample = {key: node.get(key) for key in matching_keys[:6]}
        matches.append(
            {
                "path": path,
                "matching_keys": matching_keys,
                "sample": sample,
            }
        )

    return matches



def inspect_url(
    *,
    url: str,
    show_scripts: bool,
    script_indexes: list[int],
    find_terms: list[str],
    list_event_links: bool,
    context: int,
    max_preview: int,
) -> dict[str, Any]:
    html = fetch_html(url)

    result: dict[str, Any] = {
        "url": url,
        "html_length": len(html),
    }

    if show_scripts:
        result["scripts"] = summarize_script_blocks(html, max_preview=max_preview)

    if list_event_links:
        result["event_detail_links"] = _find_union_transfer_event_links(html, url)

    if find_terms:
        result["text_matches"] = {
            term: find_text_matches(html, term, context=context) for term in find_terms
        }

    if script_indexes:
        scripts = extract_script_blocks(html)
        selected: list[dict[str, Any]] = []

        for index in script_indexes:
            if index < 0 or index >= len(scripts):
                selected.append(
                    {
                        "index": index,
                        "error": f"out of range (script count: {len(scripts)})",
                    }
                )
                continue

            block = scripts[index]
            parsed_fragments = parse_json_fragments(block.body)
            event_like_nodes: list[dict[str, Any]] = []
            for fragment in parsed_fragments:
                event_like_nodes.extend(collect_event_like_nodes(fragment))

            selected.append(
                {
                    "index": index,
                    "type": (
                        TYPE_ATTR_PATTERN.search(block.attrs).group(2).strip()
                        if TYPE_ATTR_PATTERN.search(block.attrs)
                        else "inline/unspecified"
                    ),
                    "length": len(block.body),
                    "body": block.body,
                    "json_fragments_found": len(parsed_fragments),
                    "event_like_nodes": event_like_nodes,
                }
            )

        result["selected_scripts"] = selected

    return result



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch and inspect Union Transfer pages to find where event/show "
            "information is stored."
        )
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Page URL to inspect")
    parser.add_argument(
        "--show-scripts",
        action="store_true",
        help="List all script blocks with type, length, and event-keyword hints",
    )
    parser.add_argument(
        "--script-index",
        type=int,
        action="append",
        default=[],
        help="Inspect a specific script block index (repeatable)",
    )
    parser.add_argument(
        "--find",
        action="append",
        default=[],
        help="Find a term in page HTML and print context snippets (repeatable)",
    )
    parser.add_argument(
        "--list-event-links",
        action="store_true",
        help="Extract /events/detail/?event_id=... links from the page",
    )
    parser.add_argument(
        "--context",
        type=int,
        default=140,
        help="Context chars around --find term matches",
    )
    parser.add_argument(
        "--max-preview",
        type=int,
        default=180,
        help="Max chars for each script preview in --show-scripts output",
    )
    return parser.parse_args()



def main() -> None:
    args = parse_args()

    show_scripts = args.show_scripts
    list_event_links = args.list_event_links
    find_terms: list[str] = args.find

    if not (show_scripts or args.script_index or find_terms or list_event_links):
        show_scripts = True
        list_event_links = True
        find_terms = ["event_id", "startDate", "performer"]

    try:
        payload = inspect_url(
            url=args.url,
            show_scripts=show_scripts,
            script_indexes=args.script_index,
            find_terms=find_terms,
            list_event_links=list_event_links,
            context=args.context,
            max_preview=args.max_preview,
        )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        payload = {
            "url": args.url,
            "error": str(exc),
        }

    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
