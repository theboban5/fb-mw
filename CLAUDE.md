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
src/officials.py       referee + coach pages (the officials registry, 0024)
src/trending.py        the homepage carousel (the `trending` tab, 0030)
src/matches_page.py    /matches/ — every match on one date, any date
static/report/results_grid.js  the matchday grid's rules, DOM-free and tested
static/report/fixture_import.js  the fixture importer's rules, same bargain
static/report/error_report.js  what to report when a screen breaks (0052)
src/nt.py, nt_page.py  national teams (the nt_* tabs, /scorchers/)
src/search.py          the site search index
static/report/app.js   the reporter portal (one file, no framework)
supabase/migrations/   version-controlled schema; numbered, applied in order
supabase/functions/    Edge Functions — the only place a secret may live
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
- **A name is a label on an id.** Wherever a row has a resolvable `player_id`,
  the name that renders comes from `players`, never from the row's own
  `player_id`-adjacent name column — goals always worked this way, team sheets
  do since 0022. It is what makes entering "A. Josephy" off a graphic safe:
  one rename moves every page. Fix names through `#/players` in the portal
  (`rename_player`, or `merge_players` for a duplicate), never by hand.
- **`hubs.player_page_ids` is the single source of which players get a page.**
  The pages, the search index, the national-team page and every team sheet's
  links all ask it. Deriving that set anywhere else is how a link 404s.
- **Position on a team sheet is optional** (league and national alike) — youth
  and lower-league sheets arrive as names and nothing else. Anyone without one
  renders in an unlabelled group, never dropped.
- **An unused substitute gets a page but not an appearance.** "Games played"
  must not quietly become "games named in a squad". Their match still shows on
  their profile, as a DNP row — a career is not only the games you played.
- **A referee and a coach are people now** (0024), by exactly the rule above:
  `matches.referee` is the name as reported, `matches.referee_id` is who that
  turned out to be, and a blank id renders plain text. Nothing was backfilled,
  and nothing validates the id columns — an unresolvable one degrades to text.
- **Graceful degradation is the house style.** Missing data renders *nothing* —
  never a placeholder, never a build failure. Most matches have no team sheet,
  most players no `dob`, most competitions no logo.

---

## Working on it

```bash
python3 -m unittest discover -s tests -q      # ~590 tests, ~2s, no network
npm test                                      # the reporter grid's rules, no deps
RLS_LIVE=1 python3 -m unittest tests.test_rls_live   # opt-in, hits real Supabase
DATASET_LOCAL_DIR=data/canonical python3 build.py --dist /tmp/site --no-snapshot
python3 -m http.server -d /tmp/site 8000      # then look at it at 390px wide
```

`npm test` runs `node --test 'tests/js/*.mjs'`. **There is nothing to install
and this is still not a Node project** — the root `package.json` exists only
because Node reads a bare `.js` as CommonJS unless a package.json says
otherwise, and the portal's modules are ESM because browsers load them that
way. It covers `static/report/results_grid.js`,
`static/report/fixture_import.js` and `static/report/error_report.js`, which
import nothing; the moment one of them needs `document` or `supabase`, its
tests stop meaning anything.

`python3 -m unittest discover` is **green**. It ended with one long-standing
failure until 7 Sep 2026 — the search index's raw-byte ceiling, which had been
raised once and gone red again sixteen days later. It is gone, replaced by a
shape assertion; see "A tripwire that fired on success" below.

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

## Recent work (Sep 2026)

A matchday's scorers in one submission, migration `0053` — `#/results`:

- 0041 made a matchday's RESULTS one screen and left its scorers on
  `#/m/<public_id>`, one match at a time. The graphic that gives eight results
  is the same graphic that names the scorers under them, so the half that fills
  in top-scorer tables still cost eight screens. **The Women's Premiership has
  260 goals and zero goal rows** — that is an entry cost, not a reporting
  failure.
- `apply_match_goal` + `submit_match_goals`, which is **0041's move exactly**:
  the rules move out of `submit_match_goal` into one internal function with two
  callers, because two of them ARE validate.py check 5 and a second copy kept by
  hand is a bug with a date on it. `submit_match_goal`'s signature and behaviour
  are unchanged; browsers in the field keep working.
- **A flat list of goals, each carrying its own match_id**, not a list of
  matches each holding goals. Nesting would address a failure two indexes deep
  for no gain — every failure here is about one scorer.
- **It has to be a SECOND PASS**, and the block only renders on a row whose
  score is PUBLISHED. `apply_match_goal` refuses a goal on a match with no
  score, so offering the box earlier would collect names the database is about
  to reject. Closed until tapped: eight open scorer forms is unreadable at 390px
  and most rows never get one.
- **`teamId` is the side that BENEFITED and is always explicit.** For an own
  goal that is not the side the scorer plays for, and a rule reading "invert
  when own_goal" is one confident line from filing a goal against the wrong
  team, invisibly. The `<select>` asks, exactly as `sideButtons` does.
- **`gridRowHtml` grew a `scorers` flag that defaults OFF.** Both screens build
  rows with `grid.gridRow`, so `#/import`'s review rows would have grown the
  block on any already-played fixture — on a screen where nothing wires it.
  Buttons that do nothing.
- **Four layout bugs, all found at 390px and none by a test**: `.rp-btn` is
  `width:100%` so the × took a line of its own; the sticky publish bar went four
  elements tall and covered a grid row; `flex-basis:auto` on the name made it
  claim a line and push the × to a third (wrapping is decided before shrinking,
  so `min-width:0` does not save it); and `> span` also matched the SAVED badge
  beside it, so the two grew in step and the name wrapped to pay for it.
- **`DATA_MODEL.md` was right and the old brief was wrong**: an unidentified
  goal with a reported name *does* rank in the scorer table, under that name,
  with a blank `player_id` so nothing links (`src/adapt.py`). It earns no player
  page, and two spellings rank as two people — that is the whole cost, and it is
  smaller than "never reaches a scorer table". Only 6 of 1195 goals are
  unidentified: reporters do tap the picker, so the grid reuses it.
- `possessive()` — "Mighty Wanderers'", not "Mighty Wanderers's". The
  single-match screen had been getting this wrong since 0007 on nearly every
  club name in the country, and a 1-0 now reads "…only goal already has a
  scorer" rather than "All 1 of…".
- Not built: scorers from `#/import`, and `resolve_scorer_candidates`.

**`SYSTEM_PROMPT` never mentioned scorers at all** — fixed the same day:

- The schema has asked for `team_side`, `own_goal`, `penalty` and `minute`
  since 0042 and the instructions defined none of them, so every scorer row in
  `report_imports.extracted` was read under a convention nobody wrote down. No
  fixture covered an own goal either (`WITH_SCORERS` is three ordinary goals),
  which is why nothing caught it.
- **`team_side` is now defined as WHERE THE NAME IS PRINTED** — an observation
  about the picture, not a conclusion about who the goal counted for. That is
  the extractor's existing bargain ("a date you inferred from context is worse
  than no date at all") applied to the one field where the two readings differ:
  `goals.team_id` is the beneficiary and an own-goal scorer plays for the other
  side. The prompt says outright not to move a scorer to the side it thinks the
  goal counted for, because graphics disagree with each other and moving it
  destroys the evidence.
- **Nothing is backfilled and nothing can be.** An own goal stored before this
  change has no fact to repair it from — which is exactly why `#/results` asks
  the reporter for the side outright.
- The prompt grew ~1,100 characters, which is one cache write on the first
  import after the deploy. It also sits it further above the 1,024-token
  minimum cacheable prefix on Sonnet 5 that the file's own comment names — it
  was close to that line before, and the stored `cache_read_input_tokens` will
  say whether that was ever costing anything.

A tripwire that fired on success, no migration — `tests/test_search.py`:

- The search index had a raw-byte ceiling "against indexing a whole category
  that does not belong". It **never once fired for that reason**. It was 80 kB,
  went red at 1,016 records, was raised to 120 kB on 22 August, and was red
  again sixteen days later at 122,392 — both times because players had arrived,
  which is the site working. A test everyone reads past is the `#/add` failure
  in miniature: not a wrong answer, an unread one.
- **It could not be satisfied honestly either.** The obvious shrink is to store
  an id and derive the URL from the type, dropping `players/` and `.html` from a
  thousand rows: **−21 kB raw and −0.6 kB on the wire**, because those prefixes
  are the most compressible bytes in the file and gzip deduplicates them to
  nothing. The ceiling was denominated in bytes no reader ever pays for.
- **And one total cannot separate the two failures.** Indexing every match costs
  ~52 kB; ordinary player growth is ~12 kB a month. Any ceiling generous enough
  to survive a year of success is one the mistake no longer trips.
- So the size assertion is now **gzip only** — 20.5 kB against 40 kB, the one
  number with a person on the other end of it, reached in about two years at
  the current rate. Beside it sit the two things the byte count was conflating:
  every row's URL must match **one of the five forms the build writes** (a
  category with no page needs a sixth form or a new type; both fail by name),
  and **mean bytes per row** stays under 120 (85 today) — rows getting *fatter*
  is worth catching, rows getting *more numerous* is not.
- Verified by injecting each mistake into the real index: all 872 matches as
  rows → caught; the same smuggled in under a new type → caught; a paragraph in
  every player's `meta` → caught; **another year of players → passes silently**,
  which is the whole point.

When a screen breaks, migration `0052` — `#/ops?tab=errors`:

- **`#/add` threw on every draw for a month and nobody said anything**, because
  of HOW it failed rather than how badly. The first draw painted "Loading
  teams…", the throw landed on the second, and the spinner stayed. On the
  connection this app is written for, a spinner that never resolves is not a
  broken screen — it is Tuesday. That is the failure mode to build against: an
  error a reporter can SEE gets mentioned; a screen that merely never finishes
  does not.
- Two halves, and the second is the one a reporter experiences: the error is
  recorded once per distinct problem per session, and `safeRoute` replaces the
  spinner with a sentence saying the screen did not draw, that it is not their
  connection, and that nothing they saved is gone. **Not the message and not a
  stack** — "Cannot read properties of undefined" is true, useless and
  frightening.
- **Nothing in the reporting path may throw.** It runs on a page that has
  already gone wrong, so every call is inside a try and the RPC's promise is
  swallowed. `record_portal_error` returns quietly rather than raising for the
  same reason, and caps at 20 an hour per reporter — the client dedupes too,
  and neither is trusted to be the only one working. 201 throws produce 2
  reports, verified in a real browser.
- **Four columns and no page contents**: route, message, stack, browser. The
  route is `/m/…` rather than the match id, and the query string goes, because
  it can carry a search term. Admin-only, not even the reporter who hit it.
- `ops_portal_errors` groups by route and message, so `#/add` would have been
  ONE line — "/add · Cannot read properties of undefined · 340 times · 11
  reporters · first seen 5 September". The urgent strip counts distinct broken
  screens, not hits, and puts them first.
- Anon is refused the RPC outright, so an error on the sign-in screen is not
  recorded. A real gap, and the right trade against an unauthenticated write
  endpoint on a public database.
- The tests caught the `token=None` trap `test_import_matching_live` warns
  about: a default of `None` behind `token or self.tokens["a"]` runs the anon
  check as an authorized reporter. It is invisible when the assertion is only
  about a status code.

Reading a fixture list off a picture, migrations `0049`–`0051` — `#/import`:

- **The extraction contract did not change.** It already asked for `date`,
  `kickoff`, `venue_raw`, `matchday` and `competition_hint` and already
  returned `document_kind: "fixtures"`, so every fixture graphic already in
  `report_imports.extracted` can be replayed. That is what 0042's "I have saved
  what was in it" was for.
- **A fixture graphic asks a different question.** A results graphic asks which
  fixture this is; a fixture graphic asks whether the fixture exists yet.
  Answering it with the results matcher returns "no fixture between these
  teams" on every row — true and useless.
- **The obvious use is not the useful one.** Both fixtures on the Super League
  MATCH DAY poster that prompted this were already stored with that date, that
  kick-off and those grounds — a poster CONFIRMS a top league's list. Where the
  list genuinely does not exist is district and youth football: MW_MDU14 has
  sixteen teams and zero fixtures, MW_MGDU20 ten and zero. So the state is on
  the row: `new` publishes, `existing_agrees` is a tick, `existing_differs`
  offers a correction through `reschedule_match`/`set_match_venue`. Detected in
  the resolver, not left to `insert_fixture`'s duplicate guard — "already in
  the list" arriving as a per-row failure AFTER publishing reads as an error
  and is the opposite of one.
- **The competition comes from the teams; the hint only ever breaks a tie**,
  and only between competitions `entries` already nominated. The tie is real:
  every Airtel Top 8 side is also a Super League side, so every row of a cup
  graphic shares two and votes for neither. A running intersection beside the
  vote is what stops the resolver giving up on every cup.
- **`import_venue_match` is `resolve_venue` with the insert removed.** That
  function mints an unknown ground and argues the case well — but every word of
  the argument is about a reporter TYPING it. A matched ground is pre-filled
  with the name `venues` holds; an unmatched one is left blank with the printed
  name beside it.
- **`import_kickoff`** turns "2:30 PM" into 14:30. A bare "2:30" is read as
  afternoon and FLAGGED, because that is an inference. The bare form requires a
  colon — `06.09.2026` in a kickoff field would otherwise become 18:09.
- `0050` is **0044's bug made again**: `v_reasons || 'literal'` resolves to
  array_cat and raises 22P02. It came back because 0044 repaired 0043 with
  `create or replace` in a NEW migration, so `0043_import_matching.sql` still
  contains the broken line — it is a record of a request, not the definition
  that is running. **To know what a function does today, grep the whole
  migrations directory for its name and read the LAST one.**
- `0051` is two things the live tests found: the competition was tested before
  the teams, so an unknown name reported "I cannot tell which league" instead
  of the one thing a reporter can fix; and the cup gap above.
- Not built: `document_kind: "mixed"` still goes down the results path.

How a result was submitted, migrations `0047`–`0048` — `#/ops?tab=submitted`:

- `match_change_log.source` defaulted to `'reporter'` from 0003 and **no
  function ever set it**, so a result a model read off a screenshot and a
  reporter approved was, in the audit trail, identical to one typed by hand.
  The row that answers "where did this come from" was already being written; it
  just was not being filled in.
- **The channel is derived, never claimed.** There is deliberately no
  `p_channel` argument. `submit_match_reports` takes an optional
  `p_import_id`, validates it exists and belongs to the caller, and the channel
  follows: an import means `'import'`, no import means `'grid'`,
  `submit_match_report` means `'single'`. So `source='import'` cannot appear on
  a row with nothing behind it. 0043's rule about confidence, one table over.
- **`match_change_log` is a MATCH audit, not a result log** — seven functions
  write to it (score, reschedule, venue, officials in two forms, matchday), and
  only the first records a channel. `ops_submissions` labels each row by which
  keys its payload carries, because none of the other six says what it was.
  Discovered by the CHECK constraint refusing to apply: eight rows already read
  `'admin'`, a bulk matchday correction made by hand with the secret key in
  August — 0003's own suggestion working as intended, so `'admin'` stays legal.
- **Nothing is backfilled.** 496 rows keep `'reporter'`, which now means "not
  recorded", and the screen says so. Inventing a channel for them would put a
  fact in an audit log that nobody ever observed.
- The day is bucketed in CAT in SQL (`at time zone 'Africa/Blantyre'`) and the
  clock time is formatted in CAT in JS, because a Malawian Saturday evening
  lands after 22:00 UTC and the two halves must agree.
- `submit_match_reports` was **dropped and recreated**, not overloaded: a
  defaulted parameter added by `create or replace` makes a second function and
  PostgREST's named call then matches both. Inside the migration's transaction
  the window is milliseconds, and the four-argument call every browser in the
  field makes still resolves afterwards — there is a live test that says so.
- `0048` is the `revoke all ... from anon` that `0047` forgot. **This project's
  default privileges grant new objects to `anon`**, which is why 0016 and 0039
  both revoke explicitly; the view's own `is_admin()` meant no row ever leaked,
  but it answered 200 where every other ops view answers 401. Caught by
  test_ops_live's access sweep, one line after adding the view to its list.
- Not built: the daily email roundup. No mail infrastructure exists at all —
  scheduler, provider, secret, renderer, recipients, SPF/DKIM — five new moving
  parts for what the screen already shows.

The names a league actually prints, migration `0046` — `#/teams`:

- An import came back **NOT MATCHED — MAFCO FC 1–2 MOYALE FC**. The database
  has Moyale Barracks. That is not an error in either of them, and the matching
  half was already built — `import_team_candidates` reads team aliases at tier
  2 and club aliases at tier 4, and 30 club rows were already in the table.
  **What was missing was any way to write one**: 0022 and 0024 file a person's
  old spelling as a side effect of renaming them, and nothing anywhere wrote an
  alias for a team. So the one repair a reporter could see the need for was the
  one the portal could not make.
- **The alias goes on the TEAM, not the club.** A club alias reaches every team
  of that club through tier 4 — Moyale Barracks *and* Moyale Sisters — so
  filing it there would turn a red row into an ambiguous one wherever a
  reporter covers both, which is the same fix failing more quietly. Tier 2
  resolves alone, and survives into next season because `team_id` is stable
  across entries.
- **The guard is the collision check.** `MW_MB` is Moyale Barracks and `MW_MR`
  is Moyale Reserve FC — different clubs — so a careless alias does not fail to
  match, it matches the wrong team and publishes a result against it. A name
  another team answers to at tier 1 or 2 is refused and the message names that
  team. Tiers 3 and 4 are deliberately not a collision: a team alias outranks a
  club name, so filing "Bullets" on the men's first team is how four Bullets
  squads stop being ambiguous, not a way of making them so.
- **Adding a name is a reporter's; removing and renaming are an admin's.** An
  alias references nothing and nothing references it, so a wrong one is one
  delete from repaired — unlike a duplicate club, which is why `create_league`
  is still the only way to mint one and `#/teams` has no "add a team".
- The unmatched import row **re-resolves through the same RPC** rather than
  patching itself: the matcher is the only thing allowed to decide which
  fixture a row is. What the reporter already typed on the other rows survives
  it (`currentEdits()` keys on `match_id`, because re-resolving reorders rows).
- **Recording a name needs no rebuild.** `src/search.py` reads `aliases` for
  competition and club ids only, so a team alias renders nowhere. `rename_team`
  does need one — a `display_name` is on the standings table and every fixture
  line — which is the other half of why it is admin-only.
- Not built: club-level aliases from the portal, and any way to add a team.

## Recent work (Aug 2026)

Reading results off a picture, migrations `0042`–`0045` — `#/import`:

- **The model reads, the database resolves.** There is no `match_id`,
  `team_id`, `competition_id` or `season_id` anywhere in the extraction schema,
  and `normalizeItem()` copies fields across BY NAME — so an invented id is not
  stripped so much as never picked up. A model asked for an id produces one:
  well-formed, plausible, and wrong in a way nothing downstream can detect.
- **It publishes through the grid, not beside it.** The review rows are the
  same objects `#/results` uses, drawn by the same `gridRowHtml`, wired by the
  same `wireGridRows`, published by the same `submit_match_reports`. That is
  why `gridRowHtml` and friends are at module scope — two screens draw them.
  Nothing here can put a result on the site that could not have been typed.
- **Green is pre-filled, yellow is offered, red is not publishable.** A yellow
  row never tapped simply does not publish and the greens around it still do —
  the brief's "publish the confident ones without losing the unresolved one",
  using the rule the grid already had rather than a second concept.
- **Confidence comes from evidence, never from the model.** `0043` decides it
  from how the name matched, how many fixtures the pairing has, whether the
  date agrees, and whether another team answers to the same name. The model's
  own certainty is not consulted anywhere in the system.
- **The scope IS the authorization** (`0043`): candidates come only from
  competitions the caller may report, so the review screen cannot show a row
  `submit_match_reports` would refuse. Batch consensus breaks ties and only
  ties, and always to yellow.
- **The Edge Function does not match, deliberately.** Matching needs
  `auth.uid()` and the function cannot call PostgREST as the caller — the
  `SUPABASE_ANON_KEY` slot holds a digest on a new-API-key project, which is
  the trap `trigger-rebuild`'s header records. So it does the part needing the
  ANTHROPIC secret, and the client does the part needing the REPORTER's
  identity (`resolve_and_save_import`, `0045`).
- `extract.js`, `provider.js`, `fetch_page.js` are plain `.js` importing
  nothing, so `node --test` loads them; `index.ts` is a Deno wrapper holding
  nothing that matters. 92 JS tests, no key, no network, no cost.
- **`report_imports` is evidence, not a write path.** The row is created BEFORE
  the model runs, so a failure still records what was submitted. Screenshots go
  in a PRIVATE bucket — never `match-media`, which is public because the site
  has to `<img src>` it.
- Two bugs the tests caught and no reading would have: `new URL()`
  canonicalizes `[::ffff:127.0.0.1]` to `[::ffff:7f00:1]`, so an SSRF check
  looking for dotted quads missed every IPv4-mapped address; and
  `collectReports` cleared `row.error` while both screens were calling it to
  render, so a failed row's reason was wiped before it was ever drawn.
- Not built: publishing scorers (read and stored, deliberately not published),
  fixture-list import (recognised and deferred, extraction kept), and an admin
  screen over `report_imports`.

A matchday in one submission, migration `0041` — `#/results`:

- A matchday is not a match, and the portal only knew how to report one. Eight
  results meant eight screens and eight round trips on one bar of signal, with
  the **same source typed eight times** because it is the same graphic. The
  grid is 0014's fixture form applied to scores: shared things once at the top,
  a line per match, one button, per-line answers.
- **`apply_match_report` is the whole point of the migration.** The rules moved
  out of `submit_match_report` into an internal function with two callers —
  `insert_fixture`'s relationship to `create_fixture`/`create_fixtures`,
  exactly. Every rule there is load-bearing elsewhere (score/status agreement
  is validate.py check 4, and an ERROR deploys nothing for anyone), so a second
  copy kept by hand is a bug with a date on it. `submit_match_report`'s
  signature and behaviour are unchanged; browsers in the field keep working.
- **Authorization once, then each row pinned to that competition and season.**
  Without the pin the single check would be about the competition the *client
  named* rather than the match it sent, and a reporter could publish into a
  league they are not assigned to by putting its `match_id` on a line.
- **`expect` is an optional conflict guard**, not a protocol. A row says what
  the client believed was saved; if the database moved while the reporter was
  typing, that row alone is refused with a sentence NAMING what is actually
  there, and the rest publish. A deliberate correction omits it — which is what
  the grid's "Replace 2–1" tap does, and why that tap means something.
- **A blank shared source leaves each row's own alone.** Already true since
  0008; it is what makes one box at the top of a screen full of history safe. A
  *new* result with no source anywhere is refused client-side instead: it is
  one tap to answer and the reporter is the only person who will ever know.
- **The rules live in `static/report/results_grid.js`, which imports nothing.**
  Which lines changed, what to send, how to fold the answer back — plain
  functions over plain objects, so "after a failure, is everything the reporter
  typed still there?" is an assertion rather than a person with aeroplane mode.
- It caught a real bug that no unit test would have: gating the score boxes on
  `isScored()` disabled them on `scheduled` — the one row every reporter opens
  the screen to fill in. `acceptsScore()` is the distinction, and looking at the
  screen at 390px is what found it.
- The conflict block is rendered for every already-decided row and shipped
  `hidden`, then revealed — the carousel-dots pattern. Redrawing the line the
  moment it became a conflict would throw away the box being typed into and
  take the focus with it, which is the team-sheet lesson below.
- Not built: scorers from the grid, and a season filter. The grid is also
  **Phase 2's review screen** — an AI importer fills these same rows and
  publishes through this same button. There is one grid, not two.

Comparing levels, migration `0039` — `#/ops?tab=compare`:

- The reporters asked how district football compares with regional and
  national, and youth with women's and men's. Nothing in the database could
  answer it: **`tier` is a rung inside one pyramid**, so the Super League and
  the Blantyre District U16 League are both tier 1; `region` names the regional
  FA and reads SRFA on both Blantyre *district* leagues; `governing_body` was
  blank on eight of twenty rows, all of them the district youth leagues the
  question was about. So `competitions.level` — `national | regional |
  district`, nullable, backfilled by id. Category needed nothing: `gender` +
  `age_group` already are the answer, and women's beats youth where both apply.
- **A competition with no level renders in an Unclassified bucket with a Level
  select beside it** (`set_competition_level`), because the only thing that can
  go wrong with a nullable column is a competition created without it, and the
  screen that notices should be the screen that fixes it. `create_league` takes
  `p_level` too. Nothing validates the value — the DB `CHECK` is the guard, and
  a wrong level is a wrong bar on one admin screen, never a failed build.
- `ops_competition_stats` / `ops_team_stats` are 0016's pattern exactly, with
  two differences that carry weight. They **span every season**, because every
  other `ops_*` view scopes to the active one and the Women's Premiership's
  only season is complete — scoping it the same way would drop the entire
  women's dataset from the screen built to compare women's football. And they
  **emit counts, never rates**: every figure on the tab is a ratio of two sums,
  so the browser regroups by level, category or season with no second
  round-trip and no second SQL definition of "goals per match" to drift.
- **A clean sheet belongs to a SIDE**, so a 0-0 is two and the denominator is
  `played * 2`. Counting matches would make the column mean something different
  in a league with more goalless draws.
- **The one visualisation is a bar** — label, track, figure — and that is the
  whole budget. A plotted axis is unreadable at 390px and a six-column table is
  worse; three elements with no script still read as text if the CSS never
  arrives. Every bar is one accent except Coverage, the only block where a
  short bar is a job rather than a fact about football.
- Not built: top scorers by segment. Scorer coverage is 100% in the Super
  League and **0% in the Women's Premiership** (260 goals, no goal rows), so
  that panel would render an empty women's column. Coverage says so instead.
  Discipline, positions, assists and attendance are out for the same reason.

Who a player plays for, no migration:

- A profile could name a club only from a team sheet, and 1007 of the 1060
  identified goals in this dataset belong to a match that has no sheet. So a
  ten-goal U20 scorer's page opened with his name and a table saying "10 —
  Mzuzu District FCB Katswiri U20 League", and never said who for. All 971
  pages name a club now.
- **A goal is evidence of a side.** `goals.team_id` was in every row the
  goals table was already counting — the same "who has actually worn this
  shirt" rule 0034 built for the portal's club hints. The scorer and the
  assister are teammates by definition; an own goal is read INVERTED (it
  names the beneficiary, so it says which side the scorer was not on, and a
  league match has only two), which is the whole record of seven players.
- `hubs.TeamCredit` is that fact and is deliberately weaker than an
  `Appearance`: which side and when, nothing about starting, so it can never
  be counted as a game played. `Career.side` picks between them — club over
  country, a team sheet over a goal, newest first.
- The competition under the club is a **level, not the last thing they
  played in**: a Super League season ends in the Airtel Top 8, which
  introduced nine players by a cup. `Career._level` prefers a league.
- `player_goal_credits` keys on (season, competition, **team**), so Goals by
  Competition names the club under each competition — a second line in the
  cell, never a fourth column, because that table is `table-layout: fixed`
  on a phone and the GOALS figure is what must stay visible. A player who
  moved mid-season is two rows now instead of one merged total.
- Switch Player works in a league that has never had a team sheet, which is
  where it was empty and most needed.

Browsing `#/players`, migration `0037`:

- `search_players` (0022/0034) only ever ranks a typed guess against a name —
  the right shape for "confirm this is the person the reporter meant", the
  wrong one for "walk this roster and see what looks wrong", which had no way
  in at all short of already knowing a misspelled name to search for.
- `browse_players` is a listing, not a ranking: an optional term (blank lists
  everyone alphabetically), optional League and Team filters, paginated with a
  running `total_count` for "Load more". League narrows to a competition's
  whole player pool; League + Team narrows to one squad — both derived from
  `lineups`/`goals` joined through `matches`, the same "who has actually worn
  this shirt" logic 0034 built for `search_players`' club hints, own goals
  excluded for the same reason (`goals.team_id` is the beneficiary).
- The `#/players` screen keeps its search box but gains the two filter
  `<select>`s beside it; picking a League populates Team from `entries` for
  that competition, any season, so a duplicate from a season that already
  ended is still findable by team. `searchPlayers`/`search_players` are
  unchanged — the merge picker still needs a ranked guess, not a filtered
  list — and `browsePlayers` falls back to an unfiltered `ilike` listing if
  the RPC is not yet live, the same deploy-ordering trade 0034 made.
- Not built: a season filter. A player's whole history at a club is treated as
  the useful browsing unit.

A league that is four tables, migration `0035`:

- The 2026 NRFA Division Two League is 32 clubs in four clusters of eight,
  each playing its own round-robin, top two into a quarter-final later in the
  season. `entries."group"` has been in the schema since 0001 and had never
  once been written, so the only shape a competition could be created in was
  a single table.
- **The label is a heading and a filter chip, never an id.** Free text, capped
  at 40 characters. Nothing joins on it, nothing parses it; the tables sort by
  it (`standings.group_key`) and that is the whole of its meaning.
- **A rank is a rank inside a cluster.** `Standing` carries `group` and
  `position`, and `compute_standings` fills the position in per group. It used
  to be the row's index in the returned list, worked out again by the
  standings table, the club page and the club hub — the same fact derived
  three times, right only while a competition was one table. Every consumer
  reads it off the row now.
- The standings page is one table per cluster under a chip strip; the strip
  ships `hidden` and JS reveals it, the matchday pager's bargain exactly, so
  with no JS every cluster shows under its own heading. It opens on **All** —
  no cluster is the one a reader is presumed to have come for. The season
  overview draws one chart per cluster, because that chart's y-axis IS the
  table.
- A team with no cluster is not an error: it gets an "Other teams" table at
  the bottom rather than being dropped or filed under Cluster A.
- `/report` → New league gained a **Shape** select and a cluster block per
  table (name + team list, add/remove). The screen is redrawn now, so it keeps
  everything in state — and only a `<select>` or a button redraws it, never a
  text box losing focus. The fixture picker shows the cluster beside each team
  name, which is 0034's club hints for the same reason: thirty-two teams
  behind one box.
- `set_entry_group` moves one team between clusters (admin). It has **no
  screen yet** — the state `rename_official` has been in since 0024.
- Not built: the quarter-finals. A `qf` stage on a `type=league` match is an
  ERROR from check 7, and an ERROR deploys nothing, so that is its own
  migration — and it is not needed until the cluster stage ends.

Two people, one name, migration `0034`:

- The Mzuzu District U20 league broke the assumption every player tool rests
  on. Steve Phiri (Mzuzu City Hammers Youth) and Steven Phiri (Chizumulu
  United, NRFA) are two people; the U20 scorer table credited one's goals to
  the other's page. Gift Phiri appeared in that table twice — once linked to
  the National Division player of that name, once unlinked, which is a
  reporter doing the only thing the portal left them.
- **The picker showed a name and nothing else, so there was no fact to choose
  BY.** `search_players` now also returns the clubs a player has been named
  for, derived from `lineups` and `goals` at query time — the record already
  existed, it had just never been shown to the person who needed it. Own goals
  are excluded: `goals.team_id` is the beneficiary, so counting one files a
  player at the club he scored against.
- **And when they did know, they could not act.** `create_player` is
  idempotent on the name and the portal hid "＋ Add as a new player" whenever
  the typed name matched exactly, so a second Gift Phiri was unreachable.
  `create_player(name, force)` inserts anyway — offered only from a list of
  the people who already hold that name, with their clubs beside them.
- The club hint is what makes the force flag safe, not the other way round.
  The trade: the guarantee drops from "the database will not let you" to "the
  reporter can see who they are choosing between", because the database cannot
  know whether two Gift Phiris are one person and the reporter can.
- Three places asked the question and only one of them can now get it wrong
  silently: the scorer picker, the team-sheet link box, and the squad chip
  "＋ Add", which used to call `create_player` blind — the chip list is one
  club's squad, so its silence never meant the name was free. It asks first.
- `knownScorers` is gone. It was a query per match screen that read goals
  alone; `team_ids` says the same thing from the same call and counts team
  sheets too, so a defender with nine appearances is no longer invisible to it.

Team-sheet entry, no migration — four things that made filling one in slow:

- **A `change` fires when a box loses focus, which on a phone is the same
  gesture as the tap onto the next box.** The shirt-number field redrew the
  whole block on change, so that tap landed on a node that had already been
  thrown away and every field after the first cost two taps. Not a mobile
  quirk — a redraw-on-blur. Text boxes now patch only what they control (the
  shirt in the row heading); a `<select>` may still redraw, because its change
  lands when the picker closes and the next tap is a fresh one.
- **Off' fills itself in.** Naming a starter on the substitute's row already
  says when he came off; the minute sat in a placeholder so pale that reporters
  typed it again. It renders filled and read-only, and is NOT stored —
  `save_lineup` derives the same minute, and a copy would go stale the moment
  the substitution was corrected. Editable when nothing derives it (a
  sending-off, an unreplaced withdrawal), and editable again the moment a
  minute is typed, so an explicit one can be taken back out.
- **The bench is one list.** Sub-on used to be its own heading between the XI
  and the unused subs, so marking a substitute as having come on threw them up
  the screen and the reporter's eye went with them. They stay put now; an ↑
  beside the name is what says they came on.
- **A shirt number is the one a reporter entered twice, not the last one
  entered.** `clubSquad` tallies each player's numbers across the active season
  and pre-fills the commonest, ties going to the most recent — so one slip no
  longer has to be corrected every week after. It also ordered by `ord` and
  called that "most recent": `save_lineup` writes ord as 1..N *within a sheet*,
  so that was an arbitrary row, not the latest. It is `created_at` now.

The homepage carousel, migration `0030` — a lite CMS for the front page:

- The featured card was three sentences inside an f-string in `build.py`, and
  its own docstring recorded the cost: it invited readers to follow a final
  that had already been played and lost, because changing it meant editing
  Python and waiting for CI. `trending` is that slot as data.
- **A card is the smallest thing that can carry a story**: photo, eyebrow,
  headline, paragraph, link — nearly always a link to somewhere else on this
  site, because the homepage is a way IN. Everything but the headline is
  optional and every omission renders as nothing.
- **`carousel([])` returns `""` and the old Scorchers card comes back.** That
  is what makes this invisible on a site with no published card, and it is why
  the two never stack — two features above the fold is how neither gets read.
- **The carousel needs no JavaScript.** A scroll-snap flex row IS a phone
  carousel; the inlined script only adds the dots (shipped `hidden`, so they
  are never buttons that do nothing) and the auto-advance: 5s a card, wrapping
  at the end. Input HOLDS it for 15s rather than stopping it for good — a
  reader who swiped once and then sat still should get the rest of the cards.
  A mouse resting on it holds it too, guarded on `pointerType`, because on a
  phone `pointerenter` fires on a tap and `pointerleave` may never come.
- **`image_credit` (0031) is not `image_alt`.** One says whose photo it is and
  renders small under the card; the other is read aloud to someone who cannot
  see it and must not carry a byline. A credit with no photo renders nothing.
- Photos live in a `trending-media` bucket; `build.py` pulls each live card's
  photo local and shrinks it, so the homepage depends on no second origin. A
  failed download falls back to the bucket URL; an offline build renders text.
  **Nothing ever deletes an object** — a duplicate shares its path.
- `/report` → `#/trending` (admin only): three tabs, an editor that opens with
  a preview of the card as the reader will see it, ▲▼ ordering, Duplicate, and
  a two-tap Delete. Only a change touching a LIVE card nudges a rebuild.
- The trade: three states means publishing is a second tap after writing. That
  is on purpose — `save_trending_card` cannot change status, so a hand
  slipping on the way to Save cannot put a half-written preview on the site.

Man of the match, migration `0028`:

- One boolean on the team sheet (`lineups.motm` / `nt_lineups.motm`), because
  the award belongs to a player IN A MATCH and a lineups row already is that
  sentence. On `matches` it would have been a second place a player is named
  per match, free to disagree with the sheet beside it.
- **One per MATCH, across both sides** — the armband beside it is one per side.
  A partial unique index says so, `save_lineup` clears the other side before it
  writes (so marking the away keeper takes the star off the home striker rather
  than failing), and check 10 re-checks it at build time.
- Renders as an amber ★ beside the name wherever a sheet renders, and in the
  role column of the player's own match table. No summary tile yet: a total
  means something only once enough matches carry one.
- In `/report` it is a chip beside Captain, reading `★ MOTM` until it is set
  and `★ Man of the match` when it is — three spelled-out chips do not fit a
  390px row, and the abbreviation is only ambiguous on the rows where it is off.
- The trade: a man of the match who is not on the team sheet cannot be recorded
  at all.

The reporter pool from the portal, migration `0026` + a second Edge Function:

- `#/reporters` (admin only): create an account with a generated password, tap
  leagues on and off, promote/demote, deactivate, reset a password. It replaces
  needing `scripts/reporters.py` — and therefore a trusted laptop — for the
  operations that happen weekly.
- **Split by what needs the secret key, not by what feels risky.** Assigning a
  competition and changing a role are `is_admin()`-gated RPCs. Creating a login
  and resetting a password are the GoTrue admin API, so they live in
  `supabase/functions/manage-reporters` — the same arrangement, and the same
  CORS rules, as `trigger-rebuild`.
- `admin_create_reporter` takes an `auth_user_id` and a role, so **the grant is
  the authorization**: revoked from `authenticated`, reachable only with the
  secret key. It also re-checks its `p_actor`, because the secret key has no
  `auth.uid()` and `is_admin()` is false inside it.
- Two rules nothing here can undo: an admin may not change their own role or
  deactivate themselves, and the last active admin may not be removed.
- None of it triggers a rebuild — no page renders a reporter.
- Not built: national-team assignments (`nt-assign` is still CLI-only), and
  `--season`-scoped assignments, which the RPC supports and the UI does not.

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

Goals on the sheet, officials as people, migrations `0024`–`0025`:

- A ball beside every scorer on a team sheet, one per goal, and a red A beside
  every assister (`lineups.with_goals`, joined in `src/adapt.py` and
  `src/nt.py`). Own goals get their own marker and are filed against the
  scorer's own side — `goals.team_id` is the beneficiary. The scorer line under
  the result no longer names the assister in brackets; with no team sheet the
  assist renders nowhere and still counts on the assister's profile.
- Scorers on the club hub. It had the team sheets and not the scorer block, so
  a match there listed twenty-two names without saying who scored.
- `officials` registry + `/officials/{id}.html`: every match a referee took,
  or a coach's W/D/L. `create_official`, `rename_official`, `merge_officials`
  (admin), `search_officials(term, kind)`. The portal's officials panel is six
  labelled pickers; tapping a name is what makes it a link on the site.
- `matches.notes` — reporter working notes, never rendered, and deliberately
  NOT in `data/canonical/` (that directory is public; a note about people
  should not be).

Not built: an officials management screen in `/report`. `rename_official` and
`merge_officials` exist and have no UI yet.

Player identity and officials, migrations `0022`–`0023`:

- `rename_player` / `merge_players` / `search_players`, and the `#/players`
  screen in the portal. Renaming is any reporter's; merging is admin-only,
  because it deletes a row. Old spellings live on in `aliases`, which is what
  the surname-aware search reads.
- Team sheets resolve an identified row's name through `players`
  (`lineups.with_canonical_names`), so a rename reaches every sheet.
- Officials: six free-text columns on `matches` (referee, two assistants,
  fourth official, both coaches) written by `set_match_officials`, rendered
  inside the line-up block.
- Player profiles: a back link that goes BACK (not home) when the referrer is
  this site, and a "Switch player" list of the rest of that squad.
