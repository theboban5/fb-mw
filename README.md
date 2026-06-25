# Football League Site

A lightweight, mobile-first static site for football league standings and results. No backend, no database, no JavaScript framework — data comes from two Google Sheets tabs published as CSV, and a single Python script builds the whole site.

**Live site:** https://theboban5.github.io/fb-mw/

## How it works

```
config.py            ← league name, CSV URLs, timezone
build.py             ← entry point: fetch → validate → compute → render
src/data.py          ← load and validate the CSVs
src/standings.py     ← standings computation
src/render.py        ← data → HTML
templates/base.html  ← page shell
static/style.css     ← hand-written, mobile-first
docs/                ← build output (committed; served by GitHub Pages)
tests/               ← unit tests for standings and validation
sample-data/         ← example CSVs for local preview
```

## Google Sheet format

**Teams tab:** `code, name, location`
`code` (e.g. `BLU`) is the join key used in the matches tab. `location` may be blank.

**Matches tab:** `matchday, date, home_code, away_code, home_goals, away_goals`
`date` must be `YYYY-MM-DD`. Leave both goal columns blank for unplayed matches — they are excluded from the table automatically. Every `home_code` and `away_code` must appear in the teams tab.

## Local development

Requires Python 3.9+ (standard library only — nothing to install).

**Build with the sample data:**

```bash
CSV_URL_TEAMS=sample-data/teams.csv \
CSV_URL_MATCHES=sample-data/matches.csv \
python build.py
```

**Preview in a browser:**

```bash
python -m http.server -d docs 8000
# open http://localhost:8000
```

**Run the tests:**

```bash
python -m unittest discover -s tests
```

## Configuration

Edit `config.py` to set your league name, season, timezone, and the two CSV URLs from Google Sheets.

To get the CSV URLs: in your Google Sheet go to **File → Share → Publish to web**, choose each tab and publish as CSV.

## Deploying

The `docs/` folder is committed to `main` and served by GitHub Pages (Settings → Pages → Deploy from branch → `main` / `/docs`).

To publish updated scores:

1. Run `python build.py` — this overwrites `docs/` with the latest data.
2. Commit and push.

```bash
python build.py
git add docs/
git commit -m "update standings"
git push
```

GitHub Pages will pick up the new files within a minute or two.
