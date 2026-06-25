# Under-16s Development League — static site

A lightweight, mobile-first static site that shows the league **standings** and
**results by matchday**. Data comes from two Google Sheet tabs published as CSV.
No backend, no database, no JavaScript framework — the build reads the CSVs,
validates them, computes the table, and writes plain HTML.

## How it works

```
config.py            ← the only file you edit: league name + the two CSV URLs
build.py             ← one command: fetch → validate → compute → render
src/data.py          ← load + validate CSVs (the data layer)
src/standings.py     ← pure standings computation
src/render.py        ← data → HTML
templates/base.html  ← page shell
static/style.css     ← hand-written, mobile-first
dist/                ← build output (deployed; not committed)
tests/               ← unit tests for the standings + validation rules
sample-data/         ← example CSVs to preview the site locally
```

The **data layer** (`src/data.py`) is kept separate from rendering, so adding
more leagues later is more data, not a rewrite.

## Google Sheet format

**teams** tab: `code, name, location`  — `code` (e.g. `BLU`) is the join key;
`location` may be blank.

**matches** tab: `matchday, date, home_code, away_code, home_goals, away_goals`
— `date` is `YYYY-MM-DD`; leave both goal columns blank for a match not yet
played (it's excluded from the table). Every `home_code`/`away_code` **must**
match a `code` in the teams tab.

## Build

Requires Python 3.9+ (standard library only — nothing to install).

```bash
python build.py            # writes the site to dist/
```

If a team code in **matches** doesn't exist in **teams**, the build **fails
loudly**, lists every offending row, and writes nothing — so a typo can never
silently produce a wrong table. It also rejects malformed dates, non-integer or
half-filled scores, and duplicate team codes.

### Preview locally with the sample data

```bash
CSV_URL_TEAMS=sample-data/teams.csv \
CSV_URL_MATCHES=sample-data/matches.csv \
python build.py && python -m http.server -d dist 8000
# open http://localhost:8000
```

### Run the tests

```bash
python -m unittest discover -s tests
```

## Going live

1. In your Google Sheet: **File → Share → Publish to web**, publish each tab
   as **CSV**, and copy the two URLs.
2. Paste them into `config.py` as `CSV_URL_TEAMS` and `CSV_URL_MATCHES`
   (or, on GitHub, store them as the repo secrets of the same names — the
   workflow reads them from there).

## Deploy (GitHub Pages)

Deployment uses GitHub Actions (`.github/workflows/deploy.yml`).

1. Push this repo to GitHub.
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. **Settings → Secrets and variables → Actions** → add `CSV_URL_TEAMS` and
   `CSV_URL_MATCHES`.

The site rebuilds **on every push to `main`** and whenever you click **Run
workflow** (Actions tab). After new scores land in the sheet, trigger a run to
publish them. To make rebuilds automatic later, add a `schedule:` cron trigger
to the workflow.