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
python scripts/inspect_union_transfer.py
python scripts/inspect_union_transfer.py --show-scripts --script-index 3
python scripts/inspect_union_transfer.py --url https://www.utphilly.com/events/detail/?event_id=1146309 --find startDate --find performer
python scripts/inspect_union_transfer.py --list-event-links
```

It prints JSON with script summaries, extracted event detail links, optional text match snippets, and parsed event-like JSON nodes.

## Automation

- `scripts/scrape_events.py` writes `data/events.json` and `raw-data/events.csv`.
- `scripts/scrape_movies.py` writes `data/movies.json` and `raw-data/movies.csv`.
- `.github/workflows/refresh-events.yml` scrapes weekly (Mondays), runs on pull requests and pushes to `main`, and commits refreshed scrape outputs (`data/events.json`, `raw-data/events.csv`, `data/movies.json`, `raw-data/movies.csv`).
- `.github/workflows/deploy-pages.yml` deploys the static site to GitHub Pages after `Refresh event data` completes successfully on `main`.
