# EverLeague — Malawi football results

A lightweight, mobile-first static site for football standings and results at
every level of the Malawi pyramid. No backend, no database, no JavaScript
framework — data lives in one normalized Google Spreadsheet (13 tabs,
published as CSV) and a single Python script builds the whole site.

**Live site:** https://everyleague.co

## How it works

```
build.py             ← entry point: fetch → validate → snapshot → render
validate.py          ← data validation; any ERROR aborts the build
src/dataset.py       ← the 13-tab data layer (only place that knows the URLs)
src/adapt.py         ← new schema → renderer-ready per-league shapes
src/standings.py     ← standings computation
src/scorers.py       ← goalscorer aggregation
src/render.py        ← data → HTML
templates/base.html  ← page shell
static/style.css     ← hand-written, mobile-first
data/canonical/      ← last validated fetch (drift baseline + audit log)
docs/                ← build output (served by GitHub Pages)
tests/               ← unit tests
DATA_MODEL.md        ← the schema, ID conventions, enums, and build rules
```

See `DATA_MODEL.md` for the spreadsheet schema and the rules the build
enforces (placeholder exclusion, own-goal handling, season resolution, …).

## Local development

Requires Python 3.9+. Pillow (optional) downscales logos.

```bash
python build.py                  # fetch, validate, build into docs/
python build.py --dist staging --no-snapshot   # build elsewhere, e.g. parity checks
python -m http.server -d docs 8000             # preview
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
