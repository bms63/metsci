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

- `.github/workflows/refresh-events.yml` scrapes weekly (Mondays) and commits `data/events.json`.
- `.github/workflows/deploy-pages.yml` deploys the static site to GitHub Pages.
