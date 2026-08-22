# Data model

**Supabase/Postgres is the source of truth.** The schema lives in
`supabase/migrations/`; `src/source_supabase.py` reads it back as the same
`{tab: csv_text}` the spreadsheet used to publish, so every rule below still
holds and every downstream module is unchanged.

Results arrive from reporters through `/report`, which writes via the
`submit_match_report` RPC and triggers a rebuild. Every build still validates
the whole dataset first (`validate.py`); any ERROR aborts the build before a
page is written. The validated fetch is committed to `data/canonical/`, making
git history the audit log and giving the validator a baseline to detect
accidental row deletion.

The original 13-tab Google Spreadsheet (plus seven `nt_*` national-team tabs)
is **deprecated**: still readable with `DATASET_SOURCE=sheets` as an emergency
fallback, no longer written to by anyone. **`lineups` and `officials` have no
tab in it** — both postdate the spreadsheet entirely — so under
`DATASET_SOURCE=sheets` they read as empty tabs
(`dataset.SUPABASE_ONLY_TABS`) and the fallback still builds a whole site,
minus the data it never had.

Reporter-facing tables that are NOT part of the `Dataset` — `reporter_assignments`,
`match_change_log`, `match_media`, `rebuild_state` — are invisible to the build
by design. They carry no rules below and cannot affect a rendered page.

`match_incidents` and `lineup_entries` are **deprecated** (0018). They were in
that list too, which is precisely what was wrong with them: cards,
substitutions and line-ups typed into `/report` were stored correctly and then
rendered nowhere at all. All three are now columns on the `lineups` tab, which
IS part of the `Dataset`. Both tables were empty when they were retired, so
nothing was migrated; they are left in place and must not be written to.

## Entity model

```
clubs ──< teams ──< entries >── competition_seasons >── competitions
                       │                │
                       │             seasons
                       │
matches (home/away team_id, venue_id, competition_id, season_id)
   │
   ├── goals   (match_id, team_id, player_id, assist_player_id) >── players
   └── lineups (match_id, team_id, player_id)                   >── players
```

- **clubs** — the institution (Nyasa Big Bullets). One row per club.
- **teams** — a squad of a club: `gender` + `age_group` + `squad_level`
  live here (`MW_BULL_M1` is the men's first team, `MW_BULL_W1` the women's,
  `MW_BULL_M2` the reserves). `legacy_code` carries the old per-league sheet
  code (`SL_NBB`) that public club-page URLs and historic logo filenames use.
- **competitions** — a league or cup (`MW_SL`), with `type`, `tier`,
  `gender`, `age_group`, `region`, `governing_body`.
- **seasons** — `MW_2025_26` (complete), `MW_2026_27` (active).
- **competition_seasons** — one competition in one season: `sponsor_name`
  (display-name override), `points_win`/`points_draw`, promotion/relegation
  places, `teams_count`, `status`.
- **entries** — a team's participation in a competition+season. Standings
  iterate entries (a team with 0 matches still appears). Carries
  `points_adjustment` (can be negative) + `adjustment_reason`, and `status`
  (blank/active | withdrawn | expelled).
- **venues**, **matches**, **goals**, **players** — as named.
- **lineups** — one row per named player per side per match: the starting XI,
  the bench, who came on for whom, the cards, the armband and the man of the
  match. Deliberately the same shape as `nt_lineups`, so `src/lineups.py` folds
  and renders both.
- **officials** — referees and coaches as identities (0024). `kind` is
  `referee | coach`, and the four match-official roles share the referee kind
  because the same person referees one match and runs the line at the next.
- **trending** — homepage carousel cards (0030). The one editorial table:
  it references nothing and nothing references it, because it is copy about
  football rather than football. Only `status = live` rows render.
- **registrations**, **reporters** — present, currently empty.
- **aliases** — every spelling an entity has ever been filed under.
  Written by `rename_player`/`merge_players` (0022) and their official
  counterparts (0024), read by `search_players`/`search_officials` so a name
  someone used to answer to still finds them. `entity_type` is `player` or
  `official`.

## ID conventions (as built)

- Separator is **underscore**, country prefix `MW_`. Do not "fix" to hyphens.
- `club_id` like `MW_BULL`; `team_id` like `MW_BULL_M1` (club id + squad
  suffix).
- **Exception:** U16 competition teams use bare IDs like `MW_U16_BLU` with no
  squad suffix, where `team_id == club_id`. Handled by joining on the teams
  tab, never by parsing the ID.
- `player_id` is `CAF_MW_000123`, plus the reserved `CAF_MW_UNKNOWN` for
  goals whose scorer is not yet identified. New ids continue the six-digit
  sequence — `create_player()` mints the highest plus one. The digits are a
  counter and carry no meaning; do not read anything else out of them.
- `official_id` is `MW_OFF_000123` — the `MW_` prefix every other id in this
  schema uses, minted by `create_official()` as the highest plus one. There is
  no reserved "unknown" row: an unresolved official is a blank id, not a
  pointer at a placeholder, because unlike a goal there is nothing that has to
  be attributed to somebody.
- `card_id` is `MW_TRD_000123`, minted by `save_trending_card()` as the
  highest plus one. Same shape, same rule: a counter, no meaning.
- `match_id` like `MW_SL_2627_001`. Opaque string everywhere.
- **General rule: NEVER derive meaning by parsing an ID. Always join through
  the tabs.** The only sanctioned string transform is presentational
  (competition slug for URLs: strip the country prefix, lowercase — and the
  six original slugs are pinned in `src/adapt.py` regardless).

## Enums (as built)

- `matches.status`: scheduled | played | postponed | abandoned | awarded | cancelled
- `matches.stage` is normalized at parse time (lowercased, trimmed,
  `matchday_<n>` → `md_<n>`, collapsing the sheet's historic mix). League
  stages are free-form `md_<n>`; on a `type=cup` competition the stage must
  come from the knockout vocabulary: `r64 | r32 | r16 | qf | sf | final | 3p`
  (`3p` = third-place play-off). Two-legged ties are future work — a `leg`
  column would sit beside `stage`; every tie is single-leg until then.
- `source_type`: reporter | rfa | fa | club | facebook | newspaper | whatsapp |
  backfill | placeholder | unknown (blank cells normalize to `unknown`).
  It records *how the row got here*, not where the fact came from — anything
  entered through `/report` is `reporter` even when the reporter saw it on
  Facebook, because `confidence` and the unconfirmed asterisk key off it.
- `matches.source_ref` is free text: **where** the result came from — usually a
  Facebook post URL, sometimes a sentence. Never rendered publicly; it exists
  so a result can be checked later. Written by `submit_match_report`, which
  leaves it untouched when the caller sends a blank (a score correction must
  not erase the link).
- `confidence`: unconfirmed | confirmed | official. Anything submitted
  through `/report` is `confirmed` since `0029` (reporters included);
  `unconfirmed` now marks rows that did not come from a reporter.
- `goals.goal_type`: (blank) | open_play | penalty | free_kick | header | own_goal
- `lineups.captain` / `nt_lineups.captain`: one per SIDE.
- `lineups.motm` / `nt_lineups.motm`: man of the match, one per MATCH — across
  both sides, unlike the armband above it (0028). A partial unique index on
  `match_id` enforces it, `save_lineup` clears the other side before it writes
  so marking the away keeper takes the star off the home striker rather than
  failing, and check 10 re-checks it at build time. An `unused_sub` cannot hold
  it: they did not play.
- `lineups.role` / `nt_lineups.role`: starting | sub_on | unused_sub
- `lineups.position` / `nt_lineups.position` / `nt_squads.position`:
  (blank) | GK | DF | MF | FW. **Optional on all three, and blank is a real
  answer** — outside the top flight, youth football especially, a line-up
  arrives as a list of names and nothing else, and in a flexible side the
  position someone started in is not a fact anyone recorded. A sheet with no
  positions renders as a plain list under the Starting XI heading; a sheet with
  some renders those in GK/DF/MF/FW order with the rest in an unlabelled group
  beneath. Nobody is ever dropped for having no position.
- `teams.gender`: m | w ; `teams.age_group`: senior | u20 | u19 | u17 | u16 | u15
  (case-insensitive in the sheet, normalized to lowercase) ;
  `teams.squad_level`: 1–4
- `entries.status`: (blank = active) | active | withdrawn | expelled
- `seasons.status`: active | complete
- `trending.status`: draft | live | archived (0030). Only `live` renders.
  `archived` is off the site and kept — the whole reason it is not a delete.

## Hard rules the build enforces

- **The current season comes from `seasons.status == 'active'`** (exactly one
  row), never from the system clock. A competition without a row for the
  active season builds its most recent season instead (that's how the
  Women's Premiership 25/26 stays up while 26/27 runs).
- **`source_type=placeholder` matches render nowhere** — not in standings,
  results, scorer charts, or stats. They parse without error. (Known-fake
  seed rows pending deletion.)
- **A name is a label on an id, and the id is the person.** Wherever a row
  carries a resolvable `player_id`, the name that renders comes from
  `players` — not from the row. That was always true of `goals`; since 0022 it
  is true of `lineups` and `nt_lineups` too (`lineups.with_canonical_names`,
  applied in `src/adapt.py` and `nt.team_data`). It is what makes a team sheet
  entered off a Facebook graphic as "4. A. Josephy" safe: correcting the
  `players` row later moves every team sheet, scorer line, profile and search
  record with it, at the next build. `player_name` and `reported_player_name`
  stay in the database as the archive of what was actually typed.
  `replaced_player` is remapped through the same table, because a substitution
  pairs BY NAME and renaming one side of that comparison would unpair it.
- **`goals.player_name` (denormalized) is ignored entirely**; names resolve
  via `player_id` → players.
- **Own goals (`goal_type=own_goal`) never appear in scorer tables.** In this
  data an own-goal row credits the benefiting team with the defender as
  player — it is not a scorer credit. They do count in the "Own Goals" total.
- `CAF_MW_UNKNOWN` goals count toward team/match totals. Whether they are
  *shown* depends on `reported_player_name` — the name a reporter typed when
  no canonical player existed:
  - **with** a name: rendered on the match scorer line and ranked in the
    scorer table under that name, with a blank `player_id` so nothing links to
    a player page that does not exist;
  - **without** one: dropped from scorer lines and rankings entirely, as
    before — there is nothing to show.
  Setting a real `player_id` later promotes the goal to the canonical
  rankings and a player page at the next build.
- **A line-up renders only where `lineups` has rows for that match** — the same
  graceful degradation `nt_lineups` already had. Almost every match has none,
  and a match with no sheet renders exactly as it always did.
- **An unused substitute is not an appearance.** They are on the team sheet and
  they render in the match's line-up block, but a profile does not count them:
  otherwise "games played" would mean "games named in a squad".
- **Man of the match is a flag on the team sheet, not a column on the match.**
  It renders as a star beside the name wherever that sheet renders, and as a
  star on the player's own match table. The trade: a man of the match who is
  not on the team sheet cannot be recorded at all, which is accepted because
  the alternative — a name and an id on `matches` — is a second place a player
  is named per match, free to disagree with the sheet beside it.
- **A `lineups` row with a blank `player_id`** is a name nobody has identified
  yet — the same state a reported scorer sits in. It renders as plain text
  rather than a link, and earns its player no page and no appearance. Setting a
  real `player_id` later promotes it at the next build.
- **`assist_player_id` is an id or nothing.** Unlike a scorer there is no
  reported-name column to fall back on, so an assist nobody can name is simply
  not recorded. Own goals never carry one. **The scorer line does not name the
  assister** — it did, in brackets, and that put two people inside what a
  reader scans as one fact. The credit renders on the team sheet, beside the
  person who earned it; where no sheet has been entered it renders nowhere,
  and their own profile still counts it.
- Only `status=played` matches count for standings; `awarded` matches count
  with their recorded score (and show `awarded_note`).
- Dates are strict `YYYY-MM-DD`; a blank match date is allowed (fixture not
  yet scheduled to a day), anything else fails the build.
- **`matches.kickoff` is optional and always in Malawi time** (CAT, UTC+2, no
  DST — same rule as `nt_matches.kickoff`). `HH:MM` or Sheets' `HH:MM:SS`;
  `adapt.clock` normalizes both. Blank, `TBD`/`TBA` or anything unreadable
  means "not announced" and renders *nothing* — never a placeholder time,
  and never a build failure. Where a kickoff is known it shows beside the
  date on fixtures and results alike, labelled `14:30 CAT`.
- **Officials are six name columns on `matches` and six id columns beside
  them** (0023, 0024): `referee`, `assistant_referee_1`,
  `assistant_referee_2`, `fourth_official`, `home_coach`, `away_coach`, each
  with a `_id` twin. The name is what was reported, the id is who that turned
  out to be — the identical arrangement to `lineups.player_name` +
  `lineups.player_id`, and rendered by the identical rule: a blank id renders
  the reported name as plain text, a resolved one renders the registry's name
  as a link to `/officials/{official_id}.html`. The coach is on the MATCH, not
  on the team: clubs change coach mid-season, and a column on `teams` would
  rewrite last season's team sheet when they do (`nt_matches.coach` has said
  the same thing since 0001). They render inside the line-up block — each
  coach under its own side, the referee at the foot — and a match with
  officials but no team sheet still opens that block, titled "Match
  officials". Blank renders nothing, as everywhere else.
- **A goal puts a ball beside its scorer on the team sheet, and an assist puts
  a red A beside the assister's.** One mark per goal or assist, so a brace is
  two balls. Joined from the `goals` tab by `player_id` where there is
  one and by name where there is not (`lineups.with_goals`, applied AFTER
  `with_canonical_names` so both sides of that comparison are already spelled
  the registry's way). An own goal is marked apart and counted apart — it is
  filed against the scorer's OWN side, because `goals.team_id` names the
  beneficiary.
- **`matches.notes` is never rendered** (0025). Reporter working notes: what
  is uncertain, what to check later. It is also the one column deliberately
  absent from `data/canonical/` — `source_ref` is a fact about the match and
  belongs in the public audit log, whereas a note about people does not belong
  in a public git history. Not a secret (matches has a public read policy),
  just unpublished.
- League display name = `competition_seasons.sponsor_name` if non-empty,
  else `competitions.name`. Team display = `teams.display_name`. Club
  display = `clubs.name`.

## Player identity (0022)

A team-sheet graphic gives an initial and a surname — "4. A. Josephy" — and
that is what gets entered, because the alternative is not entering the line-up
at all. Three things make that safe:

- **The id is the person; the name is a label on it.** See the rendering rule
  above. Correcting `players.full_name` moves every page that names them.
- **`rename_player(player_id, full_name, known_as)`** — any active reporter.
  The previous spelling is kept in `aliases`, and renaming INTO a name another
  player already answers to is refused, naming the id to merge with instead
  (that collision is exactly what 0021 was written by hand to repair).
- **`merge_players(loser, winner)`** — **admins only**, because it deletes a
  row and repoints eight columns across seven tables. It refuses when both ids
  sit on the same team sheet: repointing would leave one person listed twice
  in one match, and which row is real is a judgement no function can make.

`search_players(term)` backs the reporter portal's picker and matches, in
descending confidence: the whole term as a substring, then an alias, then the
SURNAME with agreeing initials — so "Andrew Josephy" finds the existing
"A. Josephy" instead of offering to create a second one. The `/report` screen
for all of this is `#/players`.

## Two people, one name (0034)

Everything above assumes a name that repeats is a person filed twice. The
Mzuzu District U20 league produced the case where it is not: Steve Phiri of
Mzuzu City Hammers Youth and Steven Phiri of Chizumulu United are two people,
and the picker — which showed names and nothing else — had no way to say so.

- **`search_players` also returns `teams` and `team_ids`**: up to two squads
  the player has actually been named for, most recent first. Derived at query
  time from `lineups` and `goals`, never stored — `players` has no club
  column and must not get one. **Own goals are excluded**, because
  `goals.team_id` is the beneficiary and counting one would file a player at
  the club he scored against. Empty for anyone who has not played yet, which
  renders as nothing.
- **`create_player(full_name, force)`** — `force=false` (the default) keeps
  the idempotence every other caller relies on. `force=true` inserts a second
  row under a name that already exists. The portal offers it only from a list
  of the people who already hold that name, with their clubs beside them.
- Nothing here is admin-gated: creating a row is not destructive, and
  `merge_players` is the undo. `rename_player` still refuses to rename INTO an
  existing name — renaming into a collision is nearly always the mistake 0022
  describes, and a genuinely different person now has a front door of its own.

The guarantee is therefore weaker than "the database will not let you", and
deliberately so: the database cannot know whether two Gift Phiris are one
person, and the reporter looking at both clubs can.

## Officials (0024)

Referees and coaches went in as free text in 0023, and that migration argued
at length that they should stay that way. They did not, for one reason: a page
per referee is worth having, and a page needs an id. The reversal is
deliberately the same shape as the player one, so there is one rule to learn
rather than two.

- **`officials`** — `official_id`, `full_name`, `known_as`, `kind`, `status`.
  One table for both kinds. A referee, an assistant and a fourth official are
  one person taking a different job on a different Saturday; `kind` only
  separates them from coaches, who are different people doing a different
  thing. `search_officials(term, kind)` therefore takes the kind as a required
  argument — a referee picker that offers coaches is how the wrong sort of
  person ends up in the wrong column.
- **`create_official(full_name, kind)`** — idempotent on both, so two
  reporters typing "H. Nkhoma" on two different matches get one person.
- **`rename_official`** (any reporter, old spelling kept in `aliases`) and
  **`merge_officials`** (admins only, because it deletes a row). Far simpler
  than the player equivalents: an official is named on exactly six columns of
  one table.
- **`set_match_officials`** writes all twelve columns every time, and a blank
  argument clears its column — the portal submits the panel as one save, so
  "not sent" and "cleared" would otherwise be the same keystroke. An id that
  resolves to nobody, or one with no name beside it, is silently dropped and
  the name alone is kept.
- **Nothing was backfilled.** Names typed before 0024 stay plain text until
  someone opens the match in /report and taps one onto a registry row. Matching
  those strings automatically is the merge risk 0020 wrote about, and a wrong
  referee is a falsehood about a real person.
- **`src/officials.py` owns the pages.** `official_page_ids` is the single
  source of who has one — an official earns a page by being named on a match —
  and the pages, the search index and every link under a result ask it.
  Nothing validates the id columns: an id that resolves to nobody renders the
  reported name and links nowhere, which is graceful degradation working, not
  an error worth failing a build over.

## Trending (0030)

The one table in this schema that holds editorial copy rather than facts. It
exists because the homepage's featured card used to be three sentences inside
an f-string in `build.py`: changing it meant editing Python and waiting for
CI, so it was changed roughly never and the front of the site aged in public.

- **`trending`** — `card_id`, `status`, `eyebrow`, `headline`, `body`,
  `link_url`, `link_label`, `image_path`, `image_alt`, `image_credit`,
  `sort_order`, `published_at`. Everything but the headline is optional, and
  every omission renders as nothing: no photo is a text-only card, no link is
  a card that is not a link. `src/trending.py` renders the live ones as a
  scroll-snap carousel in place of the old featured card; **no live cards
  renders "" and the hand-written Scorchers card comes back**, which is what
  makes this invisible on a site that has not published one.
- **`image_credit` (0031) is not `image_alt`.** The credit says whose photo it
  is and renders small under the card; the alt text describes the picture and
  is read ALOUD to somebody who cannot see it, so it must not carry a byline.
  A credit renders **only when the card actually shows a photo** — dropped by
  the renderer rather than asked of the writer, because a photo can be cleared
  off a card long after its credit was typed.
- **Writes are admin-only RPCs**, not RLS policies, because this is a Dataset
  tab: a bad row can abort a build and stop every future deploy for everyone.
  `save_trending_card` (create or update — it never changes status, because
  writing and publishing are two decisions), `set_trending_status`,
  `duplicate_trending_card` (always into a draft), `move_trending_card`
  (up/down among the live ones), `delete_trending_card`. The `/report` screen
  is `#/trending`.
- **`link_url` is checked in Postgres and again by the build.** It is the one
  value here that becomes an `href` on the most-visited page, so only two
  forms are legal: a path on this site (`/scorchers/`) or an `https://` URL.
  `//host` and `/\host` are refused — a browser follows both off the site.
- **Photos live in the `trending-media` bucket** and `image_path` is the
  object's name. `build.py` downloads each live card's photo, shrinks it and
  writes it into `docs/trending/`, so the homepage depends on no second
  origin; a download that fails falls back to the bucket's public URL, and an
  offline build renders the cards text-only. **Nothing ever deletes an
  object** — `duplicate_trending_card` copies the path, so two rows can share
  one, and reference-counting them would cost more than the storage does.
- **Drafts and the archive are in the public snapshot.** `data/canonical/` is
  committed to a public repository and this tab goes there with the rest, so
  an unpublished card is unannounced rather than secret. `created_by` and the
  timestamps are held back, the way `matches.notes` is.

## Cups (`competitions.type = cup`)

A cup is just a competitions row with `type=cup` — no extra tabs, no
cup-specific schema. Its rounds are `matches.stage` values; entries, club
hubs, player pages and the snapshot audit trail work exactly as for leagues.

- **Round order is derived from `stage`**, earliest round first; the sheet's
  `matchday` column is ignored for cups (leave it blank). Adding a later
  round is purely a sheet edit.
- **Two-legged ties need no extra column.** Two matches in the same
  competition+season+stage between the same two teams with home and away
  swapped are the two legs of one tie (leg order by date); a repeat with the
  *same* home team is a replay, not a leg. The bracket shows such a pair as
  one tie with the aggregate score and a per-leg breakdown. The winner is
  decided by aggregate, then **away goals**, then the deciding leg's
  shootout — so pens/`extra_time` belong on the second leg's row, and a
  shootout on a deciding leg is valid whenever the aggregate is level, even
  if that leg itself is not.
- Three optional matches columns exist for knockouts only (the validator
  rejects them on leagues): `extra_time` (blank/0/1), `home_pens`,
  `away_pens`. The goals columns hold the full-time-of-record score — after
  extra time when `extra_time=1`; a shootout may exist only alongside a
  full, level score, and can never itself be level. Absent columns read the
  same as blank cells, so older snapshots keep parsing.
- **Shootout kicks are not `goals` rows.** The shootout lives only in the
  pens columns (check 5 would reject kick rows as exceeding the score), and
  penalties never affect standings — a knockout has no table at all.
- An unplayed knockout slot (e.g. the final while the semis run) is a
  rendering concern: the bracket shows an em-dash placeholder. **Never
  invent a "TBD" team** in clubs/teams to hold it.
- Cup pages: `index.html` is the bracket (nav "Bracket"), `results.html`
  the matches grouped by round ("Semi-finals", not "Matchday 1"),
  `goalscorers.html` only when goal data exists. No `overview.html` (a
  position chart is meaningless) and no per-competition `clubs/` pages —
  team names link straight to the cross-competition club hub.

## National teams (`nt_*` tabs)

Six tabs in the same spreadsheet, parsed by `src/nt.py` and rendered by
`src/nt_page.py`. They are **a separate schema, not part of the `Dataset`** —
they share no ids with the league tabs, so nothing in the league build path
sees them. Currently one page is built from them: the women's senior team
(`MW_W`, "Malawi Scorchers") at `/scorchers/`.

- **nt_teams** — `team_code` (`MW_W`, `MW_M`, `MW_U20W`, …) + `team_name` +
  `category`. These are **not** `teams.team_id` values.
- **nt_matches** — one row per match *from our team's perspective*:
  `team_code`, `opponent`, `team_score`/`opponent_score`, `home_away`,
  `neutral`, `venue`/`city`/`country`, `coach`, the optional `kickoff`, and the
  knockout columns `extra_time`/`penalty_shootout`/`extra_time_result`.
  `status` is `scheduled | played | awarded` (no postponed/abandoned yet).
- **nt_goals**, **nt_lineups** — hold **both sides'** rows, distinguished by
  `team_id`: ours is the `team_code`, the opponent's is a code like
  `NIGERIA_W`. `goal_type` is already display vocabulary here — blank,
  `penalty`, or `own goal` (the underscore form also parses).
- **nt_squads** — one row per player per announcement. The **current squad is
  the row group sharing the most recent `announcement_date`**. `notes` carries
  free text; "captain" / "vice-captain" there drives the badge.
- **nt_competitions** — a hand-maintained group table, **one row per team in
  the group**, displayed as-is with its `last_update` and a link out to
  `wikipedia_url`. **Never computed from matches**, unlike a league table.
  Rows belong to a group by `competition_name` + `group_name`. Our row uses an
  `nt_teams` `team_code`; a rival's uses any code unique within the group
  (`NIGERIA_W`) and must fill `team_name`, since there is no `nt_teams` row to
  read a name from. `position` sets the order (blank falls back to points then
  goal difference), and the optional `goals_for`/`goals_against` columns add
  the GOALS and DIFF columns to the table when any row supplies them. A group
  holding only our own row still renders — as a one-line snapshot.

Rules that differ from the league schema, and why:

- **`opponent` is a display name, not a code.** So a goal or line-up row is
  attributed by asking "is this `team_id` our `team_code`?" — the opponent
  side has nothing to resolve against, and the validator does not try.
- **`nt_matches.date` may be the literal `tbd`** (a fixture with no date yet;
  `tba` also parses). It becomes `""` and renders as "Date TBC". Anything
  else must still be strict `YYYY-MM-DD`.
- **`nt_matches.kickoff` is optional and always in Malawi time** (CAT, UTC+2 —
  convert before entering, whatever the venue's clock says). 24-hour `HH:MM`,
  or `HH:MM:SS` as Sheets sometimes exports it; blank / `tbd` means "not
  announced". It renders beside the date on the next-match panel and on the
  landing card — "1 Aug 2026 · 20:00 CAT · Neutral" — and is simply left out
  when blank, so no row has to be filled in for the pages to build.
- **Our own sides' `player_id`s are canonical `players` rows** (0020). They
  were their own namespace (`MW_W_001`) pointing at nothing until team sheets
  became clickable, at which point the same person had to be the same person in
  both halves of the site — a profile shows club football and international
  football together, and could not have done while these were two unrelated
  strings. **An opponent's `player_id` is still their own thing**
  (`W_INT_NI_002`, `INT_LIB_KOSIAH`), resolves to no registry, and renders as
  plain text: this site holds one match of a Liberian international's career
  and has no business publishing a profile of them.
- **Scorer names still come from `nt_goals.player_name`**, not from
  `players` — the opposite of the league rule, and unchanged. It is the only
  column every row of that tab has, because the opponents' rows are in it too.
- **`nt_squads.domestic_team_id` is the only join back into the league data**
  (`-> teams.team_id`, for the club-hub link). It is blank for foreign-based
  players — expected, not an error.
- **A line-up section renders only when `nt_lineups` has rows for that
  match**, the same graceful degradation as scorers on club pages. Most
  matches have none. The markup is shared with the league results table
  (`src/lineups.py`); only the link target differs.
- **The results list is always from Malawi's perspective** (Malawi in the
  left column whatever `home_away` says) so the scorer columns stay aligned
  with the sides above them; home/away/neutral moves into the caption.
- **Country flags replace crests** (`src/flags.py` -> `static/flags/<code>.png`).
  The lookup is by country NAME, because that is all `opponent` gives; an
  unmapped country renders no flag rather than a placeholder. Add a name to
  `flags._NAMES` and the matching PNG to teach it a new one.
- **A group table is the one place rival rows reach the page.** Everything
  else in `NTTeamData` is filtered to one `team_code`; `NTGroup` also carries
  the rows whose code is *not* in `nt_teams`, which is exactly the rivals.

## Validation (`validate.py`, first build step)

1. No duplicate or blank primary keys in any tab.
2. Every FK resolves (teams→clubs; matches→teams/venues/competitions/seasons;
   goals→matches/teams/players; lineups→matches/teams/players, with a blank
   `player_id` allowed; entries and competition_seasons→their refs).
3. Every match participant has an entries row for that competition+season.
4. `home_team_id != away_team_id`; played/awarded ⟹ both goals present;
   scheduled ⟹ goals blank; one-sided scores always fail.
5. Goal rows per match+side never exceed that side's score (fewer is fine —
   incomplete scorer data is expected).
6. Match dates fall inside their season's date range.
7. Knockout coherence: pens/`extra_time` only on `type=cup` competitions;
   pens come in pairs, are never level themselves, sit only on a tie's
   deciding leg, and require the match — or, on the deciding leg of a
   two-legged tie, the aggregate — to be level; a cup match's stage must be
   in the knockout vocabulary (league stages stay free-form).
8. **Drift**: any match/team/club/player/nt_match ID present in the previous
   `data/canonical/` snapshot but missing from the current fetch is a hard
   fail (catches accidental row deletion). Escape hatch:
   `python validate.py --allow-deletions` (or the same flag on `build.py`).
9. **National teams**: `nt_matches.team_code` resolves; score presence agrees
   with status; goal rows per match+side never exceed that side's score; a
   line-up has at most 11 starters per side, every `sub_on` row has a
   `minute_on`, and its `replaced_player` names someone in that line-up.
   `nt_goals`/`nt_lineups` `team_id` is deliberately not resolved — the
   opponent's code has no `nt_teams` row. In `nt_competitions`, a row whose
   `team_code` does not resolve must carry a `team_name` (it is a rival's
   line), and every group must hold at least one row for a team in `nt_teams`
   — otherwise that group belongs to no page and would never render.
10. **Line-ups**: the same cross-row rules as check 9, on the league tab —
    at most 11 starters per match AND side, every `sub_on` row carries a
    `minute_on`, and its `replaced_player` names someone on that same side's
    sheet, and at most one man of the match per match (counted across BOTH
    sides — it is the one flag on this tab that spans them). Plus the one rule
    `nt_lineups` cannot state: a sheet's `team_id` must be one of the two teams
    that actually played. All ERRORs, because
    `src/lineups.py` pairs a substitute to the starter they replaced BY NAME
    — a `replaced_player` naming nobody renders a dangling name, and a twelfth
    starter renders a starting XI that is not one.
11. **Trending**: a LIVE card's `link_url` is a path on this site or an
    `https://` URL, and nothing else. Deliberately the whole check — a card is
    an escaped string in a template, and every length and the status
    vocabulary are constrained by 0030 — and deliberately live-only: an ERROR
    here fails the deploy for the whole site, so refusing to build over a link
    typed into a card nobody has published would be the validator causing the
    outage it exists to prevent.
