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

# Patterns used for HTML element inspection
HTML_TAG_PATTERN = re.compile(
    r"<(?P<tag>[a-zA-Z][a-zA-Z0-9]*)(?P<attrs>[^>]*)>",
    flags=re.IGNORECASE | re.DOTALL,
)
DATA_ATTR_PATTERN = re.compile(
    r'\bdata-(?P<name>[a-zA-Z][\w-]*)\s*=\s*(?:"(?P<dq>[^"]*)"|\'(?P<sq>[^\']*)\')',
    flags=re.IGNORECASE,
)
CLASS_ATTR_PATTERN = re.compile(
    r'\bclass\s*=\s*(?:"(?P<dq>[^"]*)"|\'(?P<sq>[^\']*)\')',
    flags=re.IGNORECASE,
)
EVENT_CLASS_PATTERN = re.compile(
    r"\b(event|show|concert|gig|performance|lineup|calendar|listing)\b",
    flags=re.IGNORECASE,
)

# Patterns used for API / network-call detection inside script bodies
FETCH_PATTERN = re.compile(
    r'fetch\s*\(\s*(["\'])(?P<url>[^"\']+)\1',
    flags=re.IGNORECASE,
)
AXIOS_PATTERN = re.compile(
    r'axios\s*\.\s*(?:get|post|put|patch|delete)\s*\(\s*(["\'])(?P<url>[^"\']+)\1',
    flags=re.IGNORECASE,
)
XHR_OPEN_PATTERN = re.compile(
    r'\.open\s*\(\s*["\'][A-Z]+["\']\s*,\s*(["\'])(?P<url>[^"\']+)\1',
    flags=re.IGNORECASE,
)
WINDOW_VAR_PATTERN = re.compile(
    r'window\.(?P<varname>[A-Za-z_$][A-Za-z0-9_$]*)\s*=',
)
API_URL_PATTERN = re.compile(
    r'(?:apiUrl|api_url|baseURL|baseUrl|endpoint|apiBase|API_URL)\s*[=:]\s*(["\'])(?P<url>[^"\']+)\1',
    flags=re.IGNORECASE,
)


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



def find_html_event_elements(html: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Scan HTML tags for elements whose class names or data-attributes suggest events.

    Returns a list of dicts, each describing one matching opening tag with its tag
    name, event-suggestive classes, and any ``data-*`` attributes found.
    """
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for match in HTML_TAG_PATTERN.finditer(html):
        tag = match.group("tag").lower()
        attrs_text = match.group("attrs") or ""

        class_match = CLASS_ATTR_PATTERN.search(attrs_text)
        classes = (class_match.group("dq") or class_match.group("sq") or "") if class_match else ""
        event_classes = [c for c in classes.split() if EVENT_CLASS_PATTERN.search(c)]

        data_attrs: dict[str, str] = {}
        for da in DATA_ATTR_PATTERN.finditer(attrs_text):
            name = da.group("name")
            value = da.group("dq") if da.group("dq") is not None else (da.group("sq") or "")
            data_attrs[f"data-{name}"] = value

        has_event_data = any(
            EVENT_CLASS_PATTERN.search(k) or EVENT_CLASS_PATTERN.search(v)
            for k, v in data_attrs.items()
        )

        if not event_classes and not has_event_data:
            continue

        key = f"{tag}|{classes}|{sorted(data_attrs.items())}"
        if key in seen:
            continue
        seen.add(key)

        results.append(
            {
                "tag": tag,
                "event_classes": event_classes,
                "data_attributes": data_attrs,
            }
        )

        if len(results) >= max_results:
            break

    return results



def find_api_patterns(html: str) -> dict[str, Any]:
    """Scan all inline script bodies for network-call and API-configuration patterns.

    Returns a dict with:
    - ``fetch_urls``: URLs passed to ``fetch()``
    - ``axios_urls``: URLs passed to ``axios.get/post/...()``
    - ``xhr_urls``: URLs passed to ``XMLHttpRequest.open()``
    - ``api_config_urls``: Values of variables like ``apiUrl``, ``baseURL``, etc.
    - ``window_vars``: Names of ``window.*`` assignments (may hold embedded data)
    """
    fetch_urls: list[str] = []
    axios_urls: list[str] = []
    xhr_urls: list[str] = []
    api_config_urls: list[str] = []
    window_vars: list[str] = []

    seen_fetch: set[str] = set()
    seen_axios: set[str] = set()
    seen_xhr: set[str] = set()
    seen_api: set[str] = set()
    seen_vars: set[str] = set()

    for block in extract_script_blocks(html):
        body = block.body

        for m in FETCH_PATTERN.finditer(body):
            u = m.group("url")
            if u not in seen_fetch:
                seen_fetch.add(u)
                fetch_urls.append(u)

        for m in AXIOS_PATTERN.finditer(body):
            u = m.group("url")
            if u not in seen_axios:
                seen_axios.add(u)
                axios_urls.append(u)

        for m in XHR_OPEN_PATTERN.finditer(body):
            u = m.group("url")
            if u not in seen_xhr:
                seen_xhr.add(u)
                xhr_urls.append(u)

        for m in API_URL_PATTERN.finditer(body):
            u = m.group("url")
            if u not in seen_api:
                seen_api.add(u)
                api_config_urls.append(u)

        for m in WINDOW_VAR_PATTERN.finditer(body):
            v = m.group("varname")
            if v not in seen_vars:
                seen_vars.add(v)
                window_vars.append(v)

    return {
        "fetch_urls": fetch_urls,
        "axios_urls": axios_urls,
        "xhr_urls": xhr_urls,
        "api_config_urls": api_config_urls,
        "window_vars": window_vars,
    }



def inspect_url(
    *,
    url: str,
    show_scripts: bool,
    script_indexes: list[int],
    find_terms: list[str],
    list_event_links: bool,
    show_html_events: bool,
    show_api_patterns: bool,
    dump_html: str,
    context: int,
    max_preview: int,
) -> dict[str, Any]:
    html = fetch_html(url)

    if dump_html:
        Path(dump_html).write_text(html, encoding="utf-8")

    result: dict[str, Any] = {
        "url": url,
        "html_length": len(html),
    }

    if dump_html:
        result["html_dumped_to"] = dump_html

    if show_scripts:
        result["scripts"] = summarize_script_blocks(html, max_preview=max_preview)

    if list_event_links:
        result["event_detail_links"] = _find_union_transfer_event_links(html, url)

    if show_html_events:
        result["html_event_elements"] = find_html_event_elements(html)

    if show_api_patterns:
        result["api_patterns"] = find_api_patterns(html)

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
        "--show-html-events",
        action="store_true",
        help=(
            "Scan HTML tags for elements whose class names or data-attributes "
            "suggest event listings"
        ),
    )
    parser.add_argument(
        "--show-api-patterns",
        action="store_true",
        help=(
            "Scan inline scripts for fetch/XHR/axios calls, API config URLs, "
            "and window.* variable assignments"
        ),
    )
    parser.add_argument(
        "--dump-html",
        default="",
        metavar="PATH",
        help="Save the raw fetched HTML to PATH for offline inspection",
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
    show_html_events = args.show_html_events
    show_api_patterns = args.show_api_patterns
    find_terms: list[str] = args.find

    if not (
        show_scripts
        or args.script_index
        or find_terms
        or list_event_links
        or show_html_events
        or show_api_patterns
        or args.dump_html
    ):
        show_scripts = True
        list_event_links = True
        show_html_events = True
        show_api_patterns = True
        find_terms = ["event_id", "startDate", "performer"]

    try:
        payload = inspect_url(
            url=args.url,
            show_scripts=show_scripts,
            script_indexes=args.script_index,
            find_terms=find_terms,
            list_event_links=list_event_links,
            show_html_events=show_html_events,
            show_api_patterns=show_api_patterns,
            dump_html=args.dump_html,
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
