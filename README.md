# EverLeague — Malawi football results

A lightweight, mobile-first static site for football standings and results at
every level of the Malawi pyramid. No backend, no database, no JavaScript
framework — data lives in one normalized Google Spreadsheet (13 league tabs
plus six national-team tabs, published as CSV) and a single Python script
builds the whole site.

**Live site:** https://everyleague.co

## How it works

```
build.py             ← entry point: fetch → validate → snapshot → render
validate.py          ← data validation; any ERROR aborts the build
src/dataset.py       ← the tab data layer (only place that knows the URLs)
src/adapt.py         ← new schema → renderer-ready per-league shapes
src/standings.py     ← standings computation
src/scorers.py       ← goalscorer aggregation
src/render.py        ← data → HTML
src/hubs.py          ← club hub + player pages (cross-competition views)
src/matches_page.py  ← /matches/ — every match on one date, any date
src/nt.py            ← national-team tabs (nt_*), filtered to one team
src/nt_page.py       ← the national-team pages (/scorchers/)
src/flags.py         ← country name → static/flags/<code>.png
templates/base.html  ← page shell
static/style.css     ← hand-written, mobile-first
data/canonical/      ← last validated fetch (drift baseline + audit log)
docs/                ← build output (served by GitHub Pages)
tests/               ← unit tests
DATA_MODEL.md        ← the schema, ID conventions, enums, and build rules
```

See `DATA_MODEL.md` for the spreadsheet schema and the rules the build
enforces (placeholder exclusion, own-goal handling, season resolution, the
separate `nt_*` national-team schema, …).

### The by-date view

`/matches/` is today's football across every competition, and
`/matches/YYYY-MM-DD.html` is any other date — written for every date that has
a match, plus a contiguous window around today (`matches_page.WINDOW_BACK` /
`WINDOW_FORWARD`) so the day-by-day arrows never dead-end. It is the only page
built from `matches.kickoff`, and the only one that shows a season a
competition is no longer building.

A date only has fixtures on it if the sheet's `date` column is filled in
ahead of time: an undated match belongs to no day and appears nowhere in this
view. Entering dates further out is what makes the forward half of the
calendar useful.

## Local development

Requires Python 3.9+. Pillow (optional) downscales logos.

```bash
python build.py                  # fetch, validate, build into docs/
python build.py --dist staging --no-snapshot   # build elsewhere, e.g. parity checks
python -m http.server -d docs 8931             # preview
python -m unittest discover -s tests           # tests
```

To build offline, point `DATASET_LOCAL_DIR` at a directory of `{tab}.csv`
files (e.g. a copy of `data/canonical/`):

```bash
DATASET_LOCAL_DIR=data/canonical python build.py --no-snapshot
```

## Deploying

`.github/workflows/deploy.yml` builds and deploys via GitHub Pages
(artifact deploy): hourly by cron, on every push to main, and on demand via
"Run workflow". A failed validation fails the build job, so a broken sheet
can never deploy a partial site. Successful builds commit the fetched CSVs
to `data/canonical/`, making git history the data audit log.

If Pages ever reports "Deployment failed, try again later", check that the
Pages source is still "GitHub Actions" (workflow), not "Deploy from branch".
