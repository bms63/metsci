# metsci

Facehuggers is a simple static event aggregator for:
- Underground Arts
- Union Transfer
- The Fillmore

## Local run

```bash
python scripts/scrape_events.py
python -m http.server 8000
```

Then open `http://localhost:8000`.

## Automation

- `scripts/scrape_events.py` writes `data/events.json` and `raw-data/events.csv`.
- `.github/workflows/refresh-events.yml` scrapes weekly (Mondays), runs on pull requests and pushes to `main`, and commits refreshed scrape outputs (`data/events.json` and `raw-data/events.csv`).
- `.github/workflows/deploy-pages.yml` deploys the static site to GitHub Pages after `Refresh event data` completes successfully on `main`.
