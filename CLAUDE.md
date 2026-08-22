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
