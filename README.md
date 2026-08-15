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
src/source_supabase.py ← Postgres → the same {tab: csv_text} (see below)
src/supabase_client.py ← minimal stdlib PostgREST client
supabase/migrations/ ← version-controlled schema
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
that shows a kickoff *in place of* the score (a fixture's time is the answer
to "what's on today"), and the only one that shows a season a competition is
no longer building. Elsewhere — competition matches tabs, club pages, cup
brackets — the kickoff sits beside the date in the caption line.

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

## Data source: the Supabase migration

**Supabase/Postgres is the source of truth.** The move was a *source swap,
not a rewrite*: everything downstream of `dataset.fetch_all()` consumes
`{tab: csv_text}`, so Postgres entered as a second implementation of that one
function and the validator, parsers, standings, renderers and national-team
code are untouched.

The Google Spreadsheet is **deprecated** — still readable as an emergency
fallback, no longer written to by anyone. Google Forms are gone from the
workflow entirely.

```
DATASET_SOURCE=supabase   the Postgres tables (what CI runs)
DATASET_SOURCE=sheets     the deprecated spreadsheet, emergency fallback
DATASET_LOCAL_DIR=DIR     outranks both: a directory of {tab}.csv, fully offline
```

Note the code default is still `sheets`; CI sets `supabase` explicitly. That
way a stray local script cannot write to production data by accident, and the
fallback is one environment variable away.

The whole migration, done and verified:

| | |
|---|---|
| Schema | `supabase/migrations/0001_core_schema.sql`, applied |
| Data | all 2682 rows imported from `data/canonical/` |
| Read path | `src/source_supabase.py`; `validate.py` passes unmodified |
| Parity | **1184 rendered files byte-identical** to the CSV build |
| Auth | `0002_auth.sql`; reporters mapped to Supabase Auth, RLS verified live |
| Reporter app | `static/report/` — login, fixtures, deep links, score entry |
| Publishing | `0003_reporting.sql` — `submit_match_report` RPC + audit log |
| Auto-deploy | `supabase/functions/trigger-rebuild` + `claim_rebuild()` debounce |
| Match detail | `0007_match_detail.sql` — scorers, cards, subs, line-ups, photos |
| Cutover | CI reads Supabase; the spreadsheet is deprecated |

The workflow this replaced:

```
was:  reporter → WhatsApp → Google Form → Sheet → hand-edit → commit → deploy
now:  reporter → everyleague.co/report → Supabase → automatic rebuild → live
```

### Setup

Copy `.env.example` to `.env` and fill in the Supabase values. **`.env` is
gitignored and must stay that way** — `SUPABASE_SECRET_KEY` bypasses Row Level
Security and must never reach a browser or a commit.

```bash
# Apply migrations (needs SUPABASE_DB_URL; the API keys cannot execute DDL)
npx supabase db push --db-url "$SUPABASE_DB_URL"

# Seed Postgres from the last validated snapshot. Idempotent — re-runnable.
python3 scripts/import_canonical.py            # --dry-run to parse only

# Prove the swap changes nothing: builds both sources and diffs the trees
python3 scripts/parity.py

# Build from Supabase
DATASET_SOURCE=supabase python3 build.py --dist staging --no-snapshot
```

`scripts/parity.py` masks exactly two things, each for a stated reason: the
wall-clock "last updated" stamp, and the search-index cache-buster (it hashes
the source CSV text, which legitimately differs — Sheets published 865 blank
trailing rows and left `source_type` blank where the parser reads `unknown`).
Every other byte must match, and does.

### Match detail (optional, per match)

Below the result, `/report` offers collapsed sections a reporter can ignore
entirely: goalscorers, cards, substitutions, line-ups and photos. Each saves
independently and immediately — none of it is part of the publish, so a failed
photo upload can never cost someone the result they already got in.

**Goalscorers go through an RPC; everything else uses ordinary RLS policies.**
That asymmetry is deliberate. `validate.py` check 5 fails the build when a
match carries more goal rows than its score, and a failed build deploys
nothing — so a plain INSERT policy would let a reporter add a third scorer to a
2-1 match and silently stop the site updating for everyone.
`submit_match_goal()` counts the existing rows under a row lock and refuses.
Cards, substitutions, line-ups and media live in new tables that `validate.py`
has never heard of and the renderers never read, so they cannot break a build
and need no such gate.

A reporter-entered scorer keeps the existing convention exactly:
`player_id = CAF_MW_UNKNOWN` with the typed name in `reported_player_name`. The
goal counts toward team and match totals and stays out of scorer rankings,
which is already how the build treats `CAF_MW_UNKNOWN`. **No player row is
created for a free-text name.** Reconciling later is a matter of setting
`player_id`; the goal joins the rankings at the next build.

Line-ups take plain names, one per line, so a whole team can be pasted in.
Photos are shrunk to 1600px on the phone before upload; the `match-media`
bucket independently caps size at 5 MB and restricts MIME types, and uploads
are authorized by reading the match id out of the object's own path — so the
path layout is a rule, not a convention.

**Not yet rendered publicly.** The site has no per-match page, so cards,
substitutions, line-ups and photos are captured but displayed nowhere. That is
data collection ahead of a match page, not an oversight. Goals are the
exception: they already feed scorer totals.

### Reporter accounts

Administration is CLI-only and runs with `SUPABASE_SECRET_KEY` — there is no
admin portal, and no signed-in user can grant themselves anything.

```bash
python3 scripts/reporters.py create --name "James Banda" \
        --email james@example.com --competition MW_NRFA   # repeatable
python3 scripts/reporters.py assign     --reporter MW_REP_001 --competition MW_SRFA
python3 scripts/reporters.py unassign   --reporter MW_REP_001 --competition MW_SRFA
python3 scripts/reporters.py deactivate --reporter MW_REP_001
python3 scripts/reporters.py activate   --reporter MW_REP_001
python3 scripts/reporters.py password   --reporter MW_REP_001   # reset
python3 scripts/reporters.py list
```

`create` confirms the email immediately, so no SMTP is needed: hand over the
generated password and they can sign in at once. It is printed **once**.
`--season` scopes an assignment to a single season; the default is every
season of that competition. `--admin` makes a reporter who may report every
competition without any assignment.

Deactivating leaves assignments in place — it is reversible, and is not the
same as forgetting what someone covered. `active` alone gates every check.

**Disable public signup** in the Supabase dashboard (Authentication →
Sign In / Providers → uncheck "Allow new users to sign up"). The schema does
not depend on that being set: a new `auth.users` row with no `reporters` row
resolves to a NULL reporter and can do nothing at all.

### The reporter app (`/report`)

A single hash-routed page under `static/report/`, copied to `docs/report/` by
the ordinary static tree copy. No framework, no bundler, no build step.

```
/report/#/              today · awaiting result · upcoming · recently reported
/report/#/login         email + password (no public signup)
/report/#/m/<public-id> the reporting screen — this is the WhatsApp link
/report/#/account       change password, sign out
```

Routing is by **hash** rather than real paths for two reasons: GitHub Pages
cannot rewrite `/report/m/<id>` onto a file, and one cached page means moving
between the fixture list and a match costs no network at all — which is the
difference that matters on a weak connection.

Sessions persist, so tapping a WhatsApp link days later goes straight to the
match. If the session has gone, the link renders login in place and returns to
that same match on success.

`supabase-js` is vendored, not loaded from a CDN — see
`static/report/vendor/README.md` for the pinned version and how to reproduce
the bundle.

Three rules the client keeps, all of which matter at the side of a pitch:

1. **A failure never destroys what was typed.** The score lives in state; a
   failed publish leaves it on screen to retry.
2. **Nothing submits twice.** Every submit disables its own button for the
   duration of the request.
3. **No database error ever reaches the reporter.** Every failure maps to a
   sentence saying what to do about it.

**Configuration.** `build.py` writes `docs/report/config.js` from
`SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`. In CI these come from repository
**variables** (Settings → Secrets and variables → Actions → Variables). If they
are unset the build warns and `/report` ships unconfigured — a missing reporter
key must never be able to stop a results deploy. `build.py` refuses to build if
the publishable slot holds something that looks like a secret key.

### Publishing a result

`submit_match_report(match_id, home_score, away_score, status)` is the **only**
write path a reporter has. There is no UPDATE policy on `matches` at all,
because being authorized to report a result is not permission to edit the row —
a generic UPDATE would let anyone who can report a score also swap the teams or
move the fixture into another season. The RPC writes four columns and derives
the rest:

```
score + status  →  source_type='reporter', reported_by, reported_at,
                   confidence, updated_at, and a match_change_log entry
```

It refuses anything else: an unknown status (`full_time` included — the UI says
"Full time", the database says `played`), a played result missing a score, a
score on an unplayed match, a negative or 3-digit score, and `awarded` from
anyone but an admin. The match row is locked `for update`, so two reporters
publishing the same match serialize rather than race.

**Confidence policy.** The site already renders `confidence='unconfirmed'` as
an asterisk with a "result not yet confirmed" legend on the by-date pages. That
is exactly right for a result phoned in from the touchline, so a reporter's
submission publishes immediately *and marked*:

| | |
|---|---|
| reporter | `confidence='unconfirmed'` — shows with an asterisk |
| admin | `confidence='confirmed'`, and the admin is recorded in `verified_by`/`verified_at` |

**Audit.** `match_change_log` is append-only: no role is granted INSERT, UPDATE
or DELETE on it through the API, and only the RPC writes to it (as the owner).
A reporter correcting 2–1 to 2–2 leaves both rows behind, each with who and
when. Anon cannot read it; a reporter sees only matches they can report; admins
see everything. An unchanged re-publish adds no row.

```bash
RLS_LIVE=1 python3 -m unittest tests.test_reporting_live
```

24 tests covering authorization, validation, the narrow-update guarantee (that
publishing cannot alter teams, competition, season, date, kickoff, venue or
`public_id`) and the audit trail. They build their own throwaway fixtures in a
real competition — no test ever rewrites a genuine scoreline.

### Automatic rebuild after a publish

everyleague.co is static HTML on GitHub Pages, so a result saved to Postgres is
not live until the site is rebuilt. The reporter app asks for that:

```
publish succeeds → app invokes trigger-rebuild (Edge Function)
                 → caller confirmed to be an active reporter
                 → claim_rebuild() debounces
                 → workflow_dispatch on the existing deploy.yml
                 → build → validate → Pages deploy
```

**The GitHub credential lives only in the Edge Function**, as a secret. It is
never in `static/`, never in `config.js`, and never reachable from a browser.

**Debounce.** `claim_rebuild()` returns true at most once per cooldown window
(60s default) and is a *single atomic UPDATE* — the decision is the `WHERE`
clause. That matters: if the comparison were done in the Edge Function, two
reports landing together would both read "no recent dispatch" and both
dispatch. `tests.test_rebuild_live` fires eight concurrent claims and asserts
exactly one wins.

A request that arrives during the cooldown is folded into the run already on
its way — that run checks out the repo and reads the database fresh, well after
it was dispatched, so the change is almost always in it anyway. `pending`
records the remainder and the daily cron is the backstop. This is the V1 target
— "live within the build cycle", not realtime.

The trigger is **best-effort**: the publish has already succeeded, so a failed
nudge is never shown to the reporter. If it never lands, the cron ships the
result anyway.

#### Deploying the function

Needs a Supabase access token (Account → Access Tokens) and a GitHub
fine-grained PAT with **Actions: read and write**, scoped to this repo only.

```bash
export SUPABASE_ACCESS_TOKEN=sbp_...
npx supabase functions deploy trigger-rebuild --project-ref <your-project-ref>

npx supabase secrets set --project-ref <your-project-ref> \
    GH_TOKEN=github_pat_... \
    GH_REPO=theboban5/fb-mw \
    GH_WORKFLOW=deploy.yml \
    GH_REF=main
```

`GH_REPO`, `GH_WORKFLOW` and `GH_REF` have working defaults; only `GH_TOKEN` is
required. Verify with a manual call as a signed-in reporter:

```bash
curl -X POST "$SUPABASE_URL/functions/v1/trigger-rebuild" \
     -H "Authorization: Bearer <a reporter's access token>" \
     -H "apikey: $SUPABASE_PUBLISHABLE_KEY"
# {"dispatched":true}   then {"dispatched":false,"reason":"coalesced"} if repeated
```

Rotate the PAT by re-running `secrets set`; no redeploy is needed.

### How authorization works

Every question funnels through one function, `public.can_report_match()`:

```
auth.uid() → reporters (active?) → reporter_assignments → competition → match
```

Admins short-circuit the assignment lookup. The helpers are `SECURITY DEFINER`
with an empty `search_path` — required, because a policy on `reporters` that
calls a function reading `reporters` would otherwise recurse forever — and each
returns only a boolean or the caller's own id. `EXECUTE` is granted to
`authenticated` only.

There is deliberately **no** INSERT/UPDATE/DELETE policy on `matches`,
`reporters` or `reporter_assignments`. Being authorized to report a match is
not permission to edit its row; reporting will go through a narrow RPC
(step 7), so a reporter can never swap the teams or move a fixture to another
season.

To add match-level assignment later, do not reshape `reporter_assignments`:
add a `reporter_match_assignments` table and one `OR` clause inside
`can_report_match()`. Every policy inherits it and nothing else changes.

Verify the whole boundary against real signed-in identities:

```bash
RLS_LIVE=1 python3 -m unittest tests.test_rls_live
```

It creates four genuine auth users (assigned, unassigned, inactive, admin),
signs each in over the network for a real JWT, asserts the full matrix, and
tears them all down. It is opt-in so the ordinary suite stays offline.

### One data defect the migration surfaced

15 `goals` rows carried an unevaluated `=AI("Fill an appropriate value…")`
Sheets formula in `verified_by`. Nothing in the build ever read that column, so
it had gone unnoticed; the foreign key to `reporters` rejected it. The importer
drops any cell still holding a formula and prints every one. The original text
remains in git under `data/canonical/`.

## Deploying

`.github/workflows/deploy.yml` builds and deploys via GitHub Pages
(artifact deploy): hourly by cron, on every push to main, and on demand via
"Run workflow". A failed validation fails the build job, so a broken sheet
can never deploy a partial site. Successful builds commit the fetched CSVs
to `data/canonical/`, making git history the data audit log.

If Pages ever reports "Deployment failed, try again later", check that the
Pages source is still "GitHub Actions" (workflow), not "Deploy from branch".
