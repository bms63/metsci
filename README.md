# metsci

Facehuggers is a simple static event aggregator for:
- Underground Arts
- Union Transfer
- The Fillmore
- Landmark Theatres (movies)

## Local run

```bash
python scripts/scrape_events.py
python scripts/scrape_movies.py
python -m http.server 8000
```

Then open `http://localhost:8000`.

The site has two tabs:
- **Concerts** – upcoming shows at Philadelphia music venues
- **Movies** – upcoming Landmark Theatres showtimes (date, title, genre, location)


## Union Transfer investigation helper

Use this script to inspect page sections and locate where show/event data lives:

```bash
# Default run – shows scripts, event links, HTML event elements, API patterns, and text matches
python scripts/inspect_union_transfer.py

# List all script blocks with event-keyword hints
python scripts/inspect_union_transfer.py --show-scripts

# Inspect a specific script block by index (from --show-scripts output)
python scripts/inspect_union_transfer.py --show-scripts --script-index 3

# Search text around specific keywords
python scripts/inspect_union_transfer.py --url https://www.utphilly.com/events/detail/?event_id=1146309 --find startDate --find performer

# Extract /events/detail/?event_id=... links
python scripts/inspect_union_transfer.py --list-event-links

# Scan HTML tags for elements whose class names / data-attributes suggest events
python scripts/inspect_union_transfer.py --show-html-events

# Scan scripts for fetch/XHR/axios calls, API config URLs, and window.* assignments
python scripts/inspect_union_transfer.py --show-api-patterns

# Save the raw HTML for offline inspection (useful when the site uses JS rendering)
python scripts/inspect_union_transfer.py --dump-html /tmp/utphilly_calendar.html
```

It prints JSON with script summaries, extracted event detail links, HTML event elements,
API/network-call patterns, optional text match snippets, and parsed event-like JSON nodes.

## Automation

- `scripts/scrape_events.py` writes `data/events.json` and `raw-data/events.csv`.
- `scripts/scrape_movies.py` writes `data/movies.json` and `raw-data/movies.csv`.
- `.github/workflows/refresh-events.yml` scrapes weekly (Mondays), runs on pull requests and pushes to `main`, and commits refreshed scrape outputs (`data/events.json`, `raw-data/events.csv`, `data/movies.json`, `raw-data/movies.csv`).
- `.github/workflows/deploy-pages.yml` deploys the static site to GitHub Pages after `Refresh event data` completes successfully on `main`.
