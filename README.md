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
src/lineups.py       ← team sheets: folding + markup, shared league/national
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

### Player profiles

`/players/{player_id}.html` is one page per person, and since `0018`/`0020` it
covers a whole career rather than a column of goals: a header (shirt, position,
current side), a row of summary tiles, and a **match-stats table** — one row
per match with the opponent, the result, whether they started or came on, their
goals, their assists and their cards.

Two decisions make that page possible, and both were structural rather than
cosmetic:

  * **Team sheets reach the build.** `lineups` is a `Dataset` tab, so an
    appearance is a fact the renderer can count. Before it, "games played" had
    nothing behind it at all.
  * **One player identity.** National-team `player_id`s were their own
    namespace pointing at nothing; `0020` merged our own sides into `players`,
    so club football and international football land on the same page. An
    opponent's id is deliberately left alone — see DATA_MODEL.md.

A page is written for anyone with a goal, an own goal, an assist or an
appearance, and **`hubs.player_page_ids` is the single source of that set**:
`build_player_pages`, `src/search.py` and `src/nt_page.py` all ask it, because
three modules deriving it separately is exactly how a link ends up pointing at
a 404. Being *named in a squad* does not earn a page — a squad member who has
not played yet renders as plain text rather than as a broken link.

**Getting back out, and sideways.** Every route to a profile goes through a
team sheet, and the thing a reader wants next is almost always another name on
that same sheet. So the back link goes *back* — `history.back()` when the
referrer is this site, falling back to the home page for anyone arriving from
a search result, a shared link or with JavaScript off, for whom "Back" would be
a lie. Under the match table, **Switch Player** lists the rest of that squad,
one tap each: everyone with a page who has appeared for the same side, bench
included.

An unused substitute's match shows too, as a **DNP** row. It is not an
appearance and the tiles do not count it as one — but it is a real fact about
that career, and the team sheet the reader arrived from is the very match the
page would otherwise refuse to mention.

There is no career/transfer table yet. That needs data nothing currently
collects.

### Referee and coach pages

`/officials/{official_id}.html` is the same idea for the other people on a
team-sheet graphic (`0024`): a header, counts, and every match they were named
on. A referee's page shows all four match-official roles together — one person
referees on Saturday and runs the line on Wednesday, and a page that split
those would split a career. A coach's page shows W/D/L from their own side's
point of view, and bolds that side in every row.

The role column appears only when the role actually varies, which is the same
rule the player table uses for the side: a coach's every row would otherwise
say "Head coach" down a third of a 390px table.

Names get there by being tapped in `/report`. One that was only typed renders
as plain text, exactly as it did before — nothing was backfilled, because
matching old strings to people automatically is how one referee becomes two.
`officials.official_page_ids` is the single source of who has a page, for the
reason the player set has one.

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
| Debounce gap | `0011_consume_rebuild_pending.sql` + the Rebuild follow-up workflow |
| Match detail | `0007_match_detail.sql` — scorers, cards, subs, line-ups, photos |
| Entry | `0008`/`0009` — `create_fixture`, `create_league`, `reschedule_match` |
| Fixture lists | `0014_fixture_batch.sql` — `create_fixtures`, `resolve_venue`, a whole week in one submission |
| Grounds | `0015_match_venue.sql` — `set_match_venue`, the third narrow door beside `reschedule_match` |
| Scorer identity | `0010_scorer_players.sql` — `create_player`, scorers resolve to a `player_id` |
| Player identity | `0022_player_identity.sql` — `rename_player`, `merge_players`, surname-aware `search_players` |
| Officials | `0023_match_officials.sql` — `set_match_officials`; referee, assistants, fourth official, both coaches |
| Officials registry | `0024_officials_registry.sql` — `officials` table, id columns on `matches`, `create_official`/`rename_official`/`merge_officials`/`search_officials` |
| Match notes | `0025_match_notes.sql` — `matches.notes` + `set_match_notes`; reporter-only, never rendered, never snapshotted |
| Reporter pool | `0026_reporter_admin.sql` + `supabase/functions/manage-reporters` — `#/reporters`: create an account, assign leagues, promote, reset a password |
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

### Setting up a competition

Adding a division means a competition, its teams, their badges, some venues and
a fixture list — in foreign-key order, with ids that follow the conventions.
`scripts/season.py` does all of it from one file:

```bash
python3 scripts/season.py template > new-division.json
$EDITOR new-division.json
python3 scripts/season.py apply new-division.json --dry-run   # read the plan
python3 scripts/season.py apply new-division.json
python3 scripts/season.py logos new-division.json             # where badges go
```

Every section is optional except `season_id`, so the same command adds three
teams mid-season, or a fixture list to a competition that already exists.
Teams and venues are named — not id'd — everywhere, including in fixtures.

Two properties make it safe to re-run:

* **Nothing is created twice.** A club, team, venue or fixture that already
  matches is reused. A team promoted from another division needs only an entry,
  and the tool works that out from the name. Re-running an unchanged file is a
  no-op.
* **Nothing is written until everything resolves.** The whole plan is built and
  checked first, so a typo in the last fixture cannot leave half a division in
  the database. `--dry-run` prints exactly what would be created, including
  every minted id, so you can correct them in the file before committing.

Badges are files, not data. `logos` prints the exact path for each — the
renderer tries `logos/clubs/<legacy_code>` first and then `logos/clubs/<club_id>`,
so it names whichever key that team actually carries. A missing badge is fine.

An administrator can also create a competition from the phone, at
`/report/#/league/new` — name, short code and a pasted list of team names, in
one call. That is the fast path; `season.py` is the thorough one, and the only
one that does venues, badges and a whole fixture list from a reviewed file.
See "Entering fixtures and competitions from the portal".

The old `static/admin/` entry UI and `tools/entry/` Apps Script wrote to the
Google Sheet and are **dead** — the sheet is no longer read. Use this instead.

### Match detail (optional, per match)

Below the result, `/report` offers collapsed sections a reporter can ignore
entirely: goalscorers, a team sheet per side, and photos. Each saves
independently and immediately — none of it is part of the publish, so a failed
photo upload can never cost someone the result they already got in.

There used to be four detail sections instead of two: Goalscorers, Cards,
Substitutions and Line-ups, each with its own free-text name box, so entering
one substitution meant typing two names that nothing checked against the eleven
already entered. Worse, three of the four wrote to `match_incidents` and
`lineup_entries`, which are not part of the `Dataset` — everything typed into
them was stored correctly and rendered nowhere at all. `0018` folded all three
into one `lineups` tab that the build does read (see DATA_MODEL.md).

**Goalscorers and team sheets go through RPCs; media uses an ordinary RLS
policy.** That asymmetry is deliberate. `validate.py` fails the build when a
match carries more goal rows than its score (check 5) or a side fields twelve
starters (check 10), and a failed build deploys nothing — so a plain INSERT
policy would let one reporter silently stop the site updating for everyone.
`submit_match_goal()` counts the existing rows under a row lock and refuses;
`save_lineup()` re-checks every cross-row rule before it writes anything. Media
lives in a table `validate.py` has never heard of and the renderers never read,
so it cannot break a build and needs no such gate.

**Naming a scorer resolves to a `player_id`** (migration `0010`). The reporter
types a few letters, the app searches `players` and shows who it found —
ranked so that anyone who has already scored for either of these two teams
comes first — and the reporter taps the right one. A goal named that way is an
ordinary goal in every respect: it ranks in the league's top-scorer table and
appears on the player's own page.

When the player is genuinely new, one further tap creates them. Creating a
player is its **own** RPC (`create_player`), never a side effect of adding a
goal, because `players` is a canonical table and a typo in it becomes a
permanent person. `create_player` is idempotent on the name — two reporters
typing "Thandiwe Phiri" get one player, not two — and `submit_match_goal`
still refuses to mint anybody.

**The picker never blocks a save.** `p_player_id` is optional: a reporter with
no signal, or one who simply types a name and presses Add, falls back to the
original convention — `player_id = CAF_MW_UNKNOWN` with the typed name in
`reported_player_name`. Such a goal now *renders* (see below); it shows under
the result and ranks by name, but links to no player page. Reconciling later is
a matter of setting `player_id`; the goal joins the canonical rankings at the
next build.

> **What changed and why.** Until `0010`, every reporter-entered scorer was
> written against `CAF_MW_UNKNOWN` — and `adapt.league_data` dropped every
> `CAF_MW_UNKNOWN` goal before rendering. The data was stored correctly and
> displayed nowhere, so from the reporter's side the feature was
> indistinguishable from broken, with silence as the only evidence. The
> adapter now falls back to `reported_player_name` rather than dropping the
> goal, and only a goal with *neither* an id nor a name is skipped.

**The team sheet is squad-first.** A reporter covering a club covers it every
week, so the second week should not be the first week's typing again: the
screen lists everyone that side has already fielded or scored through, and a
tap puts them on the sheet carrying their `player_id`, their last shirt number
and their last position. The squad is derived from `lineups` and `goals` rather
than maintained anywhere, so it fills itself in as sheets are saved. Typing is
what happens when someone is genuinely new, and it runs the same
`create_player` path the scorer picker uses.

That tap is the whole design. A name on a team sheet is worth something; a name
that resolves to a player is worth much more — it is what makes the name
clickable under the result, what gives them a profile page, and what makes
"games played" a number rather than a guess. The tap is *faster* than typing
AND it is the path that carries the id.

**Pasting still works**, behind a "paste a sheet instead" toggle, with the same
`(C)` / `[Y]` / `62' for X` shorthand spelled out beside the box — a sheet that
arrives as a screenshot in a WhatsApp group is still a sheet. Pasted names are
matched against the squad, so anyone already known arrives linked; anyone else
comes in as a name only and is marked as such on their row — and can be linked
afterwards from that row, without deleting it and losing the shirt, position,
card and substitution attached to it.

**A short name is safe to enter.** A Malawian team-sheet graphic gives an
initial and a surname ("4. A. Josephy") and that is what goes in, because the
alternative is not entering the line-up at all. Since `0022` the id is the
person and the name is a label on it: the site renders an identified row's name
from `players`, so correcting the spelling later moves every team sheet, scorer
line, profile and search record with it. The `#/players` screen is where that
correction happens — search, rename (any reporter; the old spelling is kept as
an alias), or merge two ids that turned out to be one person (admins only, since
it deletes a row). The picker itself matches surname-with-initial, so typing
"Andrew Josephy" finds the existing "A. Josephy" rather than offering to make a
second one.

Cards are one control with four states — none, yellow, second yellow, red —
cycled by tapping, rather than three checkboxes that could express "yellow AND
red AND second yellow", which is not a thing that can happen and which the
database refuses anyway. The captain is a toggle, and setting it on one player
takes it off whoever had it. A starter's minute off is **derived** from the
substitution that replaced them (`0013` for the national team, `0018` for the
league): asking a reporter to type 63 twice is how two numbers that must agree
end up not agreeing. An explicitly typed minute off still wins, because a
sending-off leaves at a minute no substitution records.

**Assists** are recorded on the goal, beside the scorer, using the same picker
(`0019`). An assist is a property of a goal — it says who passed for *that*
one — so a per-player tally on the team sheet would count the same thing while
losing which goal it belonged to. Unlike a scorer there is no reported-name
fallback: an assist that resolves to nobody is not recorded, because there is
nowhere to keep it.

Where it *shows* is the assister's own name on the team sheet, as a red A. It
used to be in brackets after the scorer on the result line, which put two
people inside what a reader scans as one fact — "Samson Phiri 31' (Rahim
Mtondera)" reads for a moment like a substitution. On a match with no team
sheet entered it therefore shows nowhere at all, and their profile counts it
anyway: an assist is a fact about the player, and the scorer line is about the
goal.

**Officials and coaches** (`0023`, `0024`) are six optional boxes saved as one
panel: referee, two assistants, fourth official, and a head coach per side.
Each carries its label above it, not only inside it as a placeholder — a
placeholder is gone the moment the first letter is typed, and six
identical-looking boxes with nothing to tell them apart is how an assistant
ends up in the fourth official's row.

Each box is a picker, and the tap is the same design as the team sheet's: type,
tap the person, and the id that arrives with them is what makes their name a
link on the site. Tapping is optional — a name that was only typed saves and
renders exactly as it did before `0024`, as plain text — and a blank box means
blank, which is how a mistyped name gets removed. They render inside the
line-up block: each coach under its own side, where the graphic puts it, and
the referee at the foot.

**Notes** (`0025`) sit at the bottom of the same screen: free text, optional,
and never shown on everyleague.co. Somewhere to write down that the second goal
might be Phiri, or that the graphic lists twelve names. Unlike "where is this
from?", which is a fact about the match and rides along in the public data
snapshot, a note about people stays in Postgres and nowhere else.

Photos are shrunk to 1600px on the phone before upload; the `match-media`
bucket independently caps size at 5 MB and restricts MIME types, and uploads
are authorized by reading the match id out of the object's own path — so the
path layout is a rule, not a convention.

**What reaches the site.** Goals, assists and team sheets all render: a
Line-ups toggle under the result on league, cup and national-team pages, with
every identified name linking to that player's profile. Photos are still
captured and displayed nowhere — that one is data collection ahead of a match
page, not an oversight.

### Reporter accounts

Two ways in, and they do the same things. `#/reporters` in the portal is for
an administrator with a phone; the CLI is for a trusted machine and is still
the only place some of it can happen.

**From `/report` (`0026` + the `manage-reporters` function).** An
administrator gets a Reporters screen: create an account with an email and a
generated password, tap leagues on and off, promote or demote, deactivate,
reset a password. This used to require somebody sitting at a checkout of this
repo with the secret key in `.env`, which meant a reporter who turned up on a
Saturday waited until Monday for a league.

The split inside it is the interesting part, and it is a split by *what needs
the key*, not by what feels risky:

| | where it runs | why |
|---|---|---|
| assign / unassign a competition | RPC, `is_admin()` | touches two public columns |
| change a role, activate, deactivate | RPC, `is_admin()` | same |
| **create an account, reset a password** | Edge Function | needs the GoTrue admin API, and therefore the secret key, which must never be in a browser |

`admin_create_reporter` takes an `auth_user_id` and a role, so anyone who
could call it could attach an admin row to their own login. It is revoked
from `authenticated` outright — **the grant is the authorization** — and is
reachable only with the secret key, i.e. only from the Edge Function, which
confirms the caller is an active admin before it does anything. That property
is asserted directly in `tests/test_reporter_admin_live.py` rather than left
to be inferred from the portal never calling it.

Two rules the portal cannot talk its way past, because nothing in it could
undo either: **an admin may not change their own role or deactivate their own
account** (the overwhelmingly likely reading of that tap is a mis-tap on the
wrong card), and **the last active administrator may not be demoted or
deactivated** — that would lock everybody out of the screen that grants the
role, and the only way back would be the CLI this exists to avoid needing.

A created password is shown **once**, on screen, with a copy button. There is
no SMTP on this project and nothing will ever mail it; it can be reset, never
read back. None of this triggers a rebuild — no page on everyleague.co
renders a reporter.

**From the CLI**, which remains the whole surface and the only route to
`--reporter-id`, `--season` and the national-team assignments:

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
resolves to a NULL reporter and can do nothing at all. It is also why the
portal creates accounts through the admin API rather than `signUp()` — which
would be disabled, and would sign the administrator out of their own session
and into the new account halfway through making it.

### The reporter app (`/report`)

A single hash-routed page under `static/report/`, copied to `docs/report/` by
the ordinary static tree copy. No framework, no bundler, no build step.

```
/report/#/              today · awaiting result · upcoming · recently reported
/report/#/login         email + password (no public signup)
/report/#/m/<public-id> the reporting screen — this is the WhatsApp link
/report/#/add           add a whole fixture list to a competition you cover
/report/#/league/new    create a competition and its teams (admin only)
/report/#/reporters     the reporter pool: create, assign, promote (admin only)
/report/#/account       change password, sign out
```

**Filtering the fixture list.** The home screen takes four filters —
competition, matchday, bucket (today / awaiting result / upcoming / recently
reported) and a single date — and they live in the URL
(`#/?comp=MW_SL&md=md_12`) rather than in a variable. The back button therefore
works, a reload keeps the view, and "my league, matchday 12" is a link a
reporter can keep.

`show`, `date` and `md` are applied on the phone, to a list it already holds.
**Competition is not**: played results are capped at 60 per load, and a cap
applied before filtering would hide an older league's results behind sixty
newer ones from elsewhere, so narrowing to a competition re-reads the database
scoped to it.

The matchday filter keys off **`stage`, not `matchday`**, because stage is the
column that works for both kinds of competition: `md_<n>` on a league and the
round on a cup, so one menu offers "Matchday 12" in the Premiership and
"Quarter-final" in the Top 8. The data agrees — 554 of 556 rows carry a stage,
where `matchday` is null on every cup tie. Options are ordered as played
(matchdays numerically, then rounds by depth), they are drawn from the chosen
competition rather than from the filtered result — otherwise picking matchday 5
would leave 5 as the only option and no way back — and changing competition
clears the matchday, since "matchday 12" means different fixtures in each
league.

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

### Entering fixtures and competitions from the portal

Reporting a result needs a fixture to report it against, and a fixture needs a
competition with teams entered in it. Both of those used to be CLI-only
(`scripts/season.py`), which meant a reporter covering a league nobody had
entered a fixture list for could do nothing at all. Two RPCs added in `0008`
close that, at two different levels of privilege:

| | who | what it does |
|---|---|---|
| `create_fixture` | any reporter, for a competition they are assigned to | one scheduled fixture |
| `create_fixtures` | the same | a whole fixture list, one call, reported line by line |
| `reschedule_match` | any reporter who may report that match | moves it — `date` and `kickoff`, nothing else |
| `set_match_venue` | the same | moves it to another ground — `venue_id`, nothing else |
| `create_league` | **admin only** | a competition, its season row, and a club + team + entry per pasted name |

**Why creating a league is admin-only and adding a fixture is not.** It is not
caution about typos. `create_league` is the one call that *mints ids* — a
`club_id` and `team_id`, once written, are referenced by every match, entry and
goal that follows and are never regenerated (DATA_MODEL.md). That makes it a
structural act rather than data entry. A fixture, by contrast, references ids
that already exist and can simply be deleted if wrong.

**Everything validate.py checks, these check first.** The validator runs as the
first step of every build and a single bad row fails the job — which means *no
deploy at all*, including everyone else's results. A typo entered on a phone
must not be able to take the site down, so each rule is enforced again at
insert time where it can still be shown to the person who caused it:

| validate.py | enforced in |
|---|---|
| check 2 — foreign keys | every reference resolved before insert |
| check 3 — both teams entered in the competition+season | `create_fixture` step 6 (and the composite FK behind it) |
| check 4 — no self-play, no score on a scheduled match | `create_fixture` step 4 and the insert itself |
| check 6 — date inside the season's range | `assert_date_in_season`, shared by `create_fixture` and `reschedule_match` |
| check 7 — cup stage vocabulary | `create_fixture` step 7, which reads `competitions.type` |

**Check 6 was missed in `0008` and added in `0009`.** `create_fixture` accepted
any date, so a mistyped year — 2031 for 2027, one keystroke — would have been
stored happily and then failed every build until someone found it. The rule now
lives in one function that both writers call, so the two cannot drift, and the
message names the actual bounds ("outside the 2026/27 season (01 Apr 2026 to
30 Jun 2027)") rather than just refusing.

The fixture form goes further and simply *does not offer* a team that is not
entered in the chosen competition, so the commonest way to break check 3 is
unreachable. The RPC checks it anyway; the client is not the boundary.

### A fixture list is not a fixture

A fixture list does not arrive as a fixture. It arrives as one Facebook
graphic: a week, seven matches, two dates, one kick-off time repeated seven
times, and a ground printed under every pairing. `0008` took them one at a
time, with no field for the ground and no field for the graphic itself, which
cost three things:

* **seven round trips** on a phone with one bar of signal, each able to fail on
  its own and leave the week half entered with nothing saying how far the
  reporter got;
* **`matches.venue_id` was NULL** for every fixture entered from the portal,
  while the sheet-imported rows around it had a ground — the same column,
  populated for the old data and not the new;
* **`matches.source_ref` was blank until a result landed.** "Where did this
  fixture come from?" is the same question as "where did this score come
  from?", and it had no answer for the weeks in between.

`0014` adds `create_fixtures`, and `/report/#/add` is now the shape of the
picture: the competition, the matchday, the date, the kick-off and the source
said **once** at the top, a numbered line per match underneath, and one button.

**It is deliberately not all-or-nothing.** Each line runs in its own exception
block and the function returns a row per input row — `idx`, `ok`, and the
sentence it raised. One duplicate pairing (the commonest mistake, because the
reporter is copying from a picture) would otherwise throw away six correct
fixtures and the typing that produced them, which is exactly what rule 1 of the
portal exists to prevent. The lines that saved leave the form and appear under
"Added just now"; the lines that did not stay as typed with the reason on them,
so what is left on screen *is* the list of what still needs doing.

**A reporter may mint a venue, and may not mint a club.** `resolve_venue` takes
the ground as a NAME — "Mkanda Primary School" is on the graphic and was not in
the 77-row table — matches it against the existing venues on case, punctuation
and spacing, and creates one when it does not match. That is the opposite of
the `create_league` rule two paragraphs up, and the difference is what the id
*means*: a `club_id` is an identity, and a second one for the same club splits
its history across the site permanently; a `venue_id` is a label on a place,
referenced by `matches.venue_id` and nothing else, where a duplicate is an
afternoon's tidying. The matching is exact on purpose — stripping
"Ground"/"Stadium" to match harder would merge Mchinji Stadium, Mchinji Mini
Stadium and Mchinji Community Ground, which are three different places. The
form offers every existing ground as an autocomplete list instead, which is
where near-duplicates are actually prevented.

A ground given as "TBA", "To be announced" or "Unknown" resolves to **no venue
at all**. That is already what a NULL `venue_id` means, and writing it down as
a place called "TBA" would put it on the site as if it were one.

**Changing a ground after the fact** is `set_match_venue` (`0015`), and it is a
third narrow door rather than a wider one. A fixture list announces a ground
weeks ahead and then the pitch is waterlogged, or the graphic said "TO BE
ANNOUNCED" and the announcement came later. `submit_match_report` still refuses
to touch `venue_id` and `reschedule_match` still writes `date` and `kickoff`
only — that narrow-update guarantee is what makes either of them safe to put in
front of a reporter, and widening one for the sake of a single column would
give it away. So the ground gets its own function, its own button under
**Change date or ground** on the match screen, and its own entry in
`match_change_log` — which records the venue *name* beside the id, because a
log line reading `MW_LIKUNI → MW_MKANDA` needs a join to mean anything and the
point of the log is that it does not.

The rules themselves did not move. `insert_fixture` is the body of `0009`'s
`create_fixture` lifted into one internal function that both the single and the
batch path call, for the same reason `assert_date_in_season` exists: a
validate.py check enforced on one write path and not the other is a build that
fails on rows entered the other way.


`create_league` takes the team list as pasted text, one name per line. Names
are de-duplicated case-insensitively, **a club already in the database is
reused rather than duplicated** (a second `club_id` for the same club would
split its identity across the site permanently), and ids are minted to the
documented conventions — `MW_<initials>` for a club, `<club_id>_<gender><squad>`
for a team. Fewer than two distinct teams rolls the whole thing back: a
competition that cannot hold a fixture is not a thing to have created.

`scripts/season.py` remains the better tool for a whole division with venues,
badges and a full fixture list from one reviewed file. The portal is for the
phone — a league someone needs to start reporting today.

### Moving a fixture

A fixture list is published weeks ahead and then a match gets moved.
`reschedule_match(match_id, date, kickoff)` handles that from the match screen,
under **Move this match**.

**It is a separate function, not extra parameters on `submit_match_report`.**
That RPC refuses to touch `date` on purpose — the narrow-update guarantee in
`0003` exists so that permission to report a score is not permission to move a
fixture into another season. Widening it would have dissolved the guarantee to
save one function. Two narrow doors, not one wide one, and the tests assert
both halves: publishing still cannot move a match, and rescheduling still
cannot change teams, competition, season, venue, score, status or stage.

It also **saves on its own**, like every other section below the result. A
reporter who came to enter a score should not have to think about the date, and
someone fixing a date should not risk the score. Clearing the date is allowed
and means what it means everywhere else in the data — a fixture with no day
yet. The move is appended to `match_change_log` alongside score changes; an
unchanged re-save adds no row and is not an error.

### Publishing a result

`submit_match_report(match_id, home_score, away_score, status, source_ref)` is
the **only** write path a reporter has. There is no UPDATE policy on `matches` at all,
because being authorized to report a result is not permission to edit the row —
a generic UPDATE would let anyone who can report a score also swap the teams or
move the fixture into another season. The RPC writes five columns and derives
the rest:

```
score + status + source_ref  →  source_type='reporter', reported_by,
                                reported_at, confidence, updated_at, and a
                                match_change_log entry
```

**`source_ref` is where a result came from** — the Facebook post the reporter
saw it in, or free text like "told to me by the referee". The column is not
new and neither is the practice: 257 matches already carry a link there from
the spreadsheet era. It is never rendered publicly; it exists so a result can
be checked later.

**`source_type` deliberately stays `'reporter'`** even when the source is a
Facebook link. It answers "how did this row get here?", and the answer is still
that a reporter typed it — which is what `confidence` and the unconfirmed
asterisk on the public site key off. Repointing it at `'facebook'` would
quietly change how the result renders.

Sending a blank `source_ref` **keeps whatever was already recorded**: a
correction to the score, submitted without re-typing the link, must not erase
the link. The value is trimmed and capped at 500 characters, and it takes part
in the audit log alongside the score.

It refuses anything else: an unknown status (`full_time` included — the UI says
"Full time", the database says `played`), a played result missing a score, a
score on an unplayed match, a negative or 3-digit score, and `awarded` from
anyone but an admin. The match row is locked `for update`, so two reporters
publishing the same match serialize rather than race.

**Confidence policy.** The site renders `confidence='unconfirmed'` as an
asterisk with a "result not yet confirmed" legend on the by-date pages. Until
`0029` that asterisk meant "no admin has typed this yet", so every reporter's
result carried one until an admin re-published the identical score — which put
the mark on the most reliable rows the site gets and overwrote `reported_by` in
the act of removing it.

Since `0029` any authorized submission is confirmed:

| | |
|---|---|
| reporter or admin | `confidence='confirmed'`, submitter recorded in `verified_by`/`verified_at` |
| anything not from `/report` | whatever it was loaded with — usually `unconfirmed` |

The control moved from review to authorization: who is assigned to a
competition is now the whole gate, and a reporter's typo publishes as fact. The
middle ground, if it is ever wanted, is a `trusted` flag on `reporters` keyed in
that same UPDATE — not a return to gating on role. `0029` backfilled nothing;
rows already sitting at `unconfirmed` stay there until someone re-publishes.

**Audit.** `match_change_log` is append-only: no role is granted INSERT, UPDATE
or DELETE on it through the API, and only the RPC writes to it (as the owner).
A reporter correcting 2–1 to 2–2 leaves both rows behind, each with who and
when. Anon cannot read it; a reporter sees only matches they can report; admins
see everything. An unchanged re-publish adds no row.

```bash
RLS_LIVE=1 python3 -m unittest tests.test_reporting_live tests.test_entry_live
```

24 tests covering authorization, validation, the narrow-update guarantee (that
publishing cannot alter teams, competition, season, date, kickoff, venue or
`public_id`) and the audit trail, plus 76 in `test_entry_live` covering
`create_fixture`, `create_fixtures`, `create_league`, `reschedule_match`,
`set_match_venue`, venue resolution and `source_ref`. They build their own throwaway fixtures in
a real competition — no test ever rewrites a genuine scoreline.

Test dates come from `live_support.season_dates()` rather than from literals,
because check 6 now refuses anything outside the season: a hardcoded 2031 is
exactly the mistake the rule exists to catch, so the tests take their dates
from the season itself and stay correct when it rolls over.

`test_entry_live` is the one suite that creates whole competitions, and unlike
a match there is no `source_type='placeholder'` that would make a leaked one
render nowhere. So every id it mints is namespaced `MW_ZZTEST*`, every club it
invents is named `ZZ *`, and `TeardownAuditTest` — named to sort last — fails
loudly if any of it survived. A failed teardown would otherwise put a fake
league on everyleague.co at the next build with nothing to complain about it.

### Automatic rebuild after a publish

everyleague.co is static HTML on GitHub Pages, so a result saved to Postgres is
not live until the site is rebuilt. The reporter app asks for that:

```
publish succeeds → app invokes trigger-rebuild (Edge Function)
                 → GoTrue resolves the caller's token to a user
                 → that user confirmed to be an active reporter
                 → claim_rebuild() debounces
                 → workflow_dispatch on the existing deploy.yml
                 → build → validate → Pages deploy
```

**The GitHub credential lives only in the Edge Function**, as a secret. It is
never in `static/`, never in `config.js`, and never reachable from a browser.

**CORS is not optional here, and its absence is the bug that ran longest.**
The only caller that matters is a browser, and a cross-origin POST carrying
`Authorization` and `apikey` is never sent on its own: the browser sends an
`OPTIONS` **preflight** first and issues the real request only if the answer
allows it. The function answered `405 method not allowed` with no CORS headers,
so the preflight failed, the browser cancelled the POST, and **the function was
never reached from `/report` at all** — from the portal's first day until
2026-08-16.

**How it hid for a day: every fix was verified with `curl`, which sends no
preflight.** It therefore worked perfectly from a terminal and never once from
a phone. `{"dispatched":true}` from a shell proves only that the server half
works. **Verify this function from a browser, or the test does not cover the
only path that is used.** `TriggerRebuildCorsTest` in `tests/test_rebuild_live.py`
now asserts the preflight directly, which is the check that was missing.

`Access-Control-Allow-Origin` is `*` on purpose. CORS is not the security
boundary and cannot be — anything can call this with `curl`. The boundary is
the reporter token. `*` is safe precisely because that token is a bearer token
read from `localStorage` rather than an ambient cookie: another origin cannot
read it, so it cannot forge a call in a reporter's name.

**How the caller is identified, and why it is not the obvious way.** The
natural implementation asks `current_reporter_id()` as the caller — but calling
PostgREST as the caller needs a publishable key in the `apikey` header, and the
function cannot get one. The platform injects `SUPABASE_ANON_KEY`, but on a
project using the new API keys that slot holds a 64-character digest rather
than a usable key: PostgREST answers `Invalid API key`, the lookup returns
null, and every reporter is silently told they are "not an active reporter".
That was a second, independent fault on the same path.

So identification runs in two steps, neither of which needs a publishable key:
GoTrue (`/auth/v1/user`) resolves the caller's own token to a user, and the
reporter row is then read with the secret key. The trust boundary is unchanged
— the caller's token still decides who they are, and the `reporters` row still
decides the answer. The publishable key is not a user token, so it stops at
GoTrue with a 401, which is what keeps this from being an open endpoint.

**Diagnosing it next time.** `rebuild_state.dispatch_count` not climbing while
results arrive means the nudge is not landing. `requestRebuild()` no longer
swallows the outcome: it logs to the browser console either way, so opening the
console on the phone answers in one line what previously took a database
forensics session. Silence about a failure the reporter should not be alarmed
by is right; leaving no trace anywhere was not.

**Debounce.** `claim_rebuild()` returns true at most once per cooldown window
(60s default) and is a *single atomic UPDATE* — the decision is the `WHERE`
clause. That matters: if the comparison were done in the Edge Function, two
reports landing together would both read "no recent dispatch" and both
dispatch. `tests.test_rebuild_live` fires eight concurrent claims and asserts
exactly one wins.

A request that arrives during the cooldown is folded into the run already on
its way — that run checks out the repo and reads the database fresh, well after
it was dispatched, so the change is almost always in it anyway. This is the V1
target — "live within the build cycle", not realtime.

**Almost always is not always, and `pending` is how the remainder is caught.**
On 2026-08-16 two results were published 26 seconds apart. The first dispatched
a build; the second landed inside the cooldown, and the running build had
already read Supabase by then. The result was in neither build:

```
09:08:35.195  Agumbala Stars v Yizo Yizo published
09:08:35.749  claimed → build dispatched, 60s cooldown starts
09:09:01.287  Brothers in Arms v Ndirande Dortmund published
09:09:01.580  folded in: pending = true, nothing dispatched
```

`pending` recorded it faithfully — and **nothing read the flag**. Neither the
workflow nor the Edge Function touched it, so the only backstop was the 05:07
cron, twenty hours away. A reporter watched a correctly entered result fail to
appear, which is the exact failure this pipeline exists to prevent. The
`/report` "next match" button makes sub-minute publishes the normal case, so
this was going to get more common, not less.

`consume_rebuild_pending()` (migration `0011`) closes it. The **Rebuild
follow-up** workflow calls it after every successful deploy; a true answer
means one more build is dispatched. It is one atomic test-and-clear, so:

* it **cannot loop** — the flag is cleared by the same statement that reports
  it, so the follow-up build finds nothing pending unless something new was
  published while *it* ran, which is exactly when another build is wanted.
  Worst case is one extra build per burst of reports;
* it **books the dispatch** (`last_dispatched_at`, `dispatch_count`) as
  `claim_rebuild` does, so a reporter publishing at that moment is debounced
  against real state rather than a stale timestamp;
* it is **service_role only**, same boundary as `claim_rebuild` — a true
  answer costs build minutes.

It is a separate workflow rather than a last step of `deploy.yml` because that
workflow declares `concurrency: group: pages, cancel-in-progress: true`: a run
dispatched from inside a run of the same workflow would cancel its own parent,
turning every follow-up into a cancelled run and hiding genuine deploy
failures. `workflow_run` fires only after the triggering run has completed.

**Order matters, and it is look → dispatch → clear.** Consuming first is the
obvious way round and it is wrong: the first live run dispatched into an error
*after* clearing the flag, destroying the record of an unbuilt result — worse
than the gap being closed. Anything failing before the clear now leaves
`pending` set for the next deploy to retry, so a failure costs a late build
rather than a missing result.

**Chain depth is exactly one, which leaves a measured tail.** A run created by
`GITHUB_TOKEN` emits no events that start further runs. `workflow_dispatch` is
excepted so the follow-up's build *does* start, but that build's completion
emits no `workflow_run`, so nothing follows it:

| build triggered by | follow-up fired? |
|---|---|
| `push` | yes |
| `workflow_dispatch` (user PAT) | yes |
| `workflow_dispatch` (this workflow's `GITHUB_TOKEN`) | **no** |

So one follow-up build per real build. That covers the case this exists for — a
result published while a build was running. It does **not** cover a result
published while the *follow-up* build is running: that sets `pending` again
with no third build to consume it, and waits for the next publish (whose own
build includes it anyway) or the cron. The residual window is roughly half a
minute at the very end of a reporting session. Closing it would need a PAT
instead of `GITHUB_TOKEN`, or a job that polls the build it started.

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
required.

Verify the **preflight** first, because that is the half `curl` normally skips
and the half that actually broke:

```bash
curl -i -X OPTIONS "$SUPABASE_URL/functions/v1/trigger-rebuild" \
     -H "Origin: https://everyleague.co" \
     -H "Access-Control-Request-Method: POST" \
     -H "Access-Control-Request-Headers: authorization,apikey,content-type"
# 204, with access-control-allow-origin / -headers / -methods present.
# A 405 here means no phone can reach the function, however well the POST works.
```

Then the call itself, as a signed-in reporter:

```bash
curl -X POST "$SUPABASE_URL/functions/v1/trigger-rebuild" \
     -H "Authorization: Bearer <a reporter's access token>" \
     -H "apikey: $SUPABASE_PUBLISHABLE_KEY"
# {"dispatched":true}   then {"dispatched":false,"reason":"coalesced"} if repeated
```

**Neither of these is sufficient on its own.** Publish a result from a real
browser and confirm `rebuild_state.dispatch_count` moved. That is the only test
that exercises the path reporters actually use.

Rotate the PAT by re-running `secrets set`; no redeploy is needed.

#### The other function: `manage-reporters`

Same shape, same CORS rules, and **nothing to configure** — it uses only the
`SUPABASE_URL` and service key the platform injects.

```bash
export SUPABASE_ACCESS_TOKEN=sbp_...
npx supabase functions deploy manage-reporters --project-ref <your-project-ref>
```

Deploy order matters, and in the usual direction: `0026` must be applied
before this function is called, because it calls `admin_create_reporter`. A
migration and a function are both separate deploys from git (see CLAUDE.md).
Verify its preflight the same way — swap the name in the `OPTIONS` call above.

Its live tests skip themselves when it is not deployed, so a checkout with the
migration and no function reports a skip rather than a failure:

```bash
RLS_LIVE=1 python3 -m unittest tests.test_reporter_admin_live
```

### Operations: coverage and data backlog

`/report/#/ops`, administrators only. A read-only lens over what is already in
Postgres: which competitions have incomplete past rounds, what is due next,
what is overdue, and what is missing from what has already been published.

It writes **no** football data. Every row links back to its ordinary
`#/m/<public_id>` screen, so corrections go through the existing reporter flow
and there stays exactly one write path into `matches`.

Ten questions, one screen:

| Tab | Answers |
|---|---|
| Overview | Urgent totals, then one row per active competition |
| Results | Results overdue — `scheduled` with a date now past |
| Fixtures | Round sizes, surpluses and deficits, and what is due next |
| Scorers | Played matches with fewer goal rows than the score |
| Venues | Fixtures with no ground |
| Sources | Published results with no `source_ref` |
| Verification | Results still `confidence = 'unconfirmed'` |
| Crests | Clubs whose hub page renders without a logo |
| Site | Whether everyleague.co is up to date |

**A matchday is a logical round, not a weekend.** A fixture postponed out of its
weekend keeps its matchday and changes only its date, so every matchday of a
league should hold exactly `floor(active entries / 2)` fixtures. The Fixtures
tab reports any that do not, and says which of two problems it is: if the
competition's fixture count divides exactly into whole rounds, nothing is
missing and the fault is a fixture filed under the wrong matchday; a remainder
means fixtures were never entered. MW_SRFA is the first kind — 96 fixtures is
exactly twelve rounds of eight, spread across thirteen labels. MW_SRFA2 is the
second.

Two things Postgres cannot answer are published by `build.py` in
`docs/build-info.json`, which `/report` fetches from its own origin — no CORS,
no credential, no extra table:

* **when the site last read the database**, which is what makes "is there a
  saved result the site has not shown yet?" answerable at all;
* **which clubs have no crest**, because a crest is a file in
  `static/logos/clubs/` and `clubs.crest` disagrees with what is on disk (87
  rows populated, 65 clubs actually resolving).

Writing that file never fails the build.

`0016_ops_dashboard.sql` adds the views and one small settings table. The views
carry `where public.is_admin()` in their own bodies — the football tables all
have a public read policy from `0001`, so RLS alone would show this to a
reporter. `anon` is revoked outright; a signed-in reporter gets zero rows. Only
`rebuild_state` is genuinely non-public, and it keeps its "no policy at all"
grants: `ops_rebuild_status()` reads it `SECURITY DEFINER` on an admin's behalf.

`ops_competition_settings` holds only what cannot be derived — which
competitions are cups, and `backlog_from`, the date before which rows are the
historical import and are folded away by default in the backlog lists (never
dropped from the totals). Round sizes are computed, not stored.

Verify the whole boundary and the arithmetic:

```bash
RLS_LIVE=1 python3 -m unittest tests.test_ops_live
```

It signs in as anon, a reporter and an administrator, then recomputes every
count in Python from the raw rows rather than re-running the view's own SQL —
which is what catches a predicate that is subtly wrong, such as a 0-0 draw
counted as missing its scorers.

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
