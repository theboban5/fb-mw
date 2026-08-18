# EveryLeague (fb-mw) — orientation

A static football results site for Malawi: **everyleague.co**. Leagues, cups,
youth and regional divisions, plus the national teams. Community-maintained,
read on a phone in a place where data is expensive, so every page is
hand-written HTML/CSS with almost no JavaScript.

Two halves:

- **The site** — `build.py` reads Supabase, validates everything, renders static
  HTML into `docs/`, which GitHub Pages serves.
- **The reporter portal** — `/report`, a no-framework SPA (`static/report/app.js`)
  where reporters enter results, scorers and team sheets on their phones.

Python 3.12, one dependency (Pillow, only for logo downscaling). No build step,
no bundler, no framework. Keep it that way.

---

## Read these first

- `DATA_MODEL.md` — the schema, ID conventions, enums, and every rule the build
  enforces. **The single most useful file in the repo.** Read it before touching
  data.
- `README.md` — how it works, the Supabase migration story, the reporter app in
  detail, deployment. Long; skim the headings and jump.

---

## Traps that will cost you an hour each

**A local build overwrites `docs/` AND `data/canonical/`.** `--dist` redirects
only the site; the snapshot always goes to the fixed canonical directory. Always:

```bash
DATASET_LOCAL_DIR=data/canonical python3 build.py --dist /tmp/site --no-snapshot
```

Reading the committed CSVs in text mode also converts CRLF→LF, so a careless run
rewrites every line of every tab. Recover with
`git checkout -- data/canonical docs && git clean -fd docs`.

**`DATASET_SOURCE` defaults to `sheets`, not Supabase.** A bare `python3 build.py`
reads a deprecated Google Sheet and reports drift errors that mean nothing. Use
`DATASET_LOCAL_DIR` (offline) or `DATASET_SOURCE=supabase` (real).

**Migrations are a separate deploy from git.** Pushing a `supabase/migrations/*.sql`
changes nothing on the server. `.env` is not shell-sourceable (unquoted URLs with
`&`), so use the repo's own parser:

```bash
DB_URL=$(python3 -c "
import sys, os; sys.path.insert(0, '.')
from src import supabase_client as sb
sb.load_dotenv('.env'); print(os.environ['SUPABASE_DB_URL'])")
npx --yes supabase db push --dry-run --db-url "$DB_URL"
```

Apply the migration **before** deploying an `app.js` that calls a new RPC.

**SSH to github.com:22 is blocked on this machine.** Push over HTTPS:

```bash
git -c credential.helper='!gh auth git-credential' \
  push https://github.com/theboban5/fb-mw.git <branch>
```

**`validate.py` aborts the build on any ERROR, and a failed build deploys
nothing.** That is the safety net, and it is why reporter writes go through RPCs
that re-check the same rules — one bad row must never be able to stop every
future deploy for everyone.

---

## Layout

```
build.py               entry point: fetch → validate → snapshot → render
validate.py            10 checks; any ERROR aborts before a page is written
src/dataset.py         the tab data layer (only place that knows a URL)
src/source_supabase.py Postgres → the same {tab: csv_text} the sheet produced
src/adapt.py           schema → renderer-ready per-league shapes
src/standings.py       table computation      src/scorers.py  goal aggregation
src/render.py          data → HTML (league, cup, club pages)
src/lineups.py         team sheets: folding + markup, shared league/national
src/hubs.py            club hubs + player profiles (cross-competition)
src/matches_page.py    /matches/ — every match on one date, any date
src/nt.py, nt_page.py  national teams (the nt_* tabs, /scorchers/)
src/search.py          the site search index
static/report/app.js   the reporter portal (one file, no framework)
supabase/migrations/   version-controlled schema; numbered, applied in order
social/                post-pack generator; never publishes, a human posts
data/canonical/        last validated fetch — drift baseline + audit log
docs/                  build output, served by GitHub Pages
```

---

## Things that are true and easy to get wrong

- **Never derive meaning by parsing an ID.** Always join through the tabs. The
  only sanctioned string transform is the competition slug for URLs.
- **The current season comes from `seasons.status == 'active'`**, never the clock.
- **`source_type=placeholder` rows render nowhere** — they parse, then vanish.
- **Own goals never appear in scorer tables** but do count in the Own Goals total.
- **A blank `player_id` means "not identified yet"**, renders as plain text, and
  earns no page. `CAF_MW_UNKNOWN` is the reserved id and must never become a link.
- **`hubs.player_page_ids` is the single source of which players get a page.**
  The pages, the search index, the national-team page and every team sheet's
  links all ask it. Deriving that set anywhere else is how a link 404s.
- **Position on a team sheet is optional** (league and national alike) — youth
  and lower-league sheets arrive as names and nothing else. Anyone without one
  renders in an unlabelled group, never dropped.
- **An unused substitute gets a page but not an appearance.** "Games played"
  must not quietly become "games named in a squad".
- **Graceful degradation is the house style.** Missing data renders *nothing* —
  never a placeholder, never a build failure. Most matches have no team sheet,
  most players no `dob`, most competitions no logo.

---

## Working on it

```bash
python3 -m unittest discover -s tests -q      # ~590 tests, ~2s, no network
RLS_LIVE=1 python3 -m unittest tests.test_rls_live   # opt-in, hits real Supabase
DATASET_LOCAL_DIR=data/canonical python3 build.py --dist /tmp/site --no-snapshot
python3 -m http.server -d /tmp/site 8000      # then look at it at 390px wide
```

Live tests (`*_live.py`) are skipped unless `RLS_LIVE=1`; they namespace every
fixture and clean up, and they never mutate a real match.

### Conventions worth matching

- **Comments explain *why*, and say what was wrong before.** The codebase reads
  like a series of decisions with their reasons attached, including the ones
  that were mistakes. Migration headers especially: `WHAT WAS WRONG`, then
  `WHAT THIS DOES`, then the trade stated plainly. Match that register — it is
  the most distinctive thing about this repo.
- **Mobile-first, always.** Most readers are on a phone. Check any UI change at
  ~390px before anything else. Tables scroll inside their own container; the
  page body never scrolls sideways.
- **CSS is hand-written** (`static/style.css` for the site, `report.css` for the
  portal). `v2-*` classes are the site's shared layer, `el-*` the newer shared
  components, `nt-*` national-team-specific, `rp-*` the reporter portal.
- **The reporter portal must never lose typed data.** Every save keeps its state
  on failure, every lookup is optional, and a failed player search still saves
  the name. A reporter is standing at a touchline on a weak connection.
- Reporter writes go through **RPCs** where a bad row could break the build
  (goals, team sheets), and plain RLS policies where it could not (media).

---

## Recent work (Aug 2026)

Team sheets and player profiles, migrations `0018`–`0021`:

- `lineups` tab (league/cup team sheets) — deliberately the same shape as
  `nt_lineups` so `src/lineups.py` folds and renders both from one implementation.
- Assists on the goal (`goals.assist_player_id`, dormant since 0001).
- National-team `player_id`s merged into the canonical `players` registry, so a
  profile shows club and country together. Opponents keep their own ids and
  render as plain text — this site knows one match of their career.
- Player profiles: header, summary tiles, per-match stats table. A page is
  written for anyone with a goal, an assist, an appearance or a bench call.
- The reporter's team sheet is one squad-first screen: tap a name and it arrives
  carrying its `player_id`. That tap is the design — it is faster than typing
  *and* it is the path that carries the id, which is what makes names clickable.

Not built: the Wikipedia-style senior-career table on a profile. No data for it.
