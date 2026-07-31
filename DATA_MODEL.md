# Data model

The site builds from a single published Google Spreadsheet: 13 league tabs
plus six `nt_*` national-team tabs (a separate schema — see "National teams"
below), fetched as CSV by `src/dataset.py` (the only module that knows the URLs).
Every build validates the whole dataset first (`validate.py`); any ERROR
aborts the build before a page is written. The last validated fetch is
committed to `data/canonical/`, making git history the audit log and giving
the validator a baseline to detect accidental row deletion.

## Entity model

```
clubs ──< teams ──< entries >── competition_seasons >── competitions
                       │                │
                       │             seasons
                       │
matches (home/away team_id, venue_id, competition_id, season_id)
   │
goals (match_id, team_id, player_id, assist_player_id) >── players
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
- **registrations**, **reporters**, **aliases** — present, currently empty.

## ID conventions (as built)

- Separator is **underscore**, country prefix `MW_`. Do not "fix" to hyphens.
- `club_id` like `MW_BULL`; `team_id` like `MW_BULL_M1` (club id + squad
  suffix).
- **Exception:** U16 competition teams use bare IDs like `MW_U16_BLU` with no
  squad suffix, where `team_id == club_id`. Handled by joining on the teams
  tab, never by parsing the ID.
- `player_id` is `CAF_MW_000123`, plus the reserved `CAF_MW_UNKNOWN` for
  goals whose scorer is not yet identified.
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
  backfill | placeholder | unknown (blank cells normalize to `unknown`)
- `confidence`: unconfirmed | confirmed | official
- `goals.goal_type`: (blank) | open_play | penalty | free_kick | header | own_goal
- `teams.gender`: m | w ; `teams.age_group`: senior | u20 | u19 | u17 | u16 | u15
  (case-insensitive in the sheet, normalized to lowercase) ;
  `teams.squad_level`: 1–4
- `entries.status`: (blank = active) | active | withdrawn | expelled
- `seasons.status`: active | complete

## Hard rules the build enforces

- **The current season comes from `seasons.status == 'active'`** (exactly one
  row), never from the system clock. A competition without a row for the
  active season builds its most recent season instead (that's how the
  Women's Premiership 25/26 stays up while 26/27 runs).
- **`source_type=placeholder` matches render nowhere** — not in standings,
  results, scorer charts, or stats. They parse without error. (Known-fake
  seed rows pending deletion.)
- **`goals.player_name` (denormalized) is ignored entirely**; names resolve
  via `player_id` → players.
- **Own goals (`goal_type=own_goal`) never appear in scorer tables.** In this
  data an own-goal row credits the benefiting team with the defender as
  player — it is not a scorer credit. They do count in the "Own Goals" total.
- `CAF_MW_UNKNOWN` goals count toward team/match totals but never appear in
  scorer rankings or match scorer lines.
- Only `status=played` matches count for standings; `awarded` matches count
  with their recorded score (and show `awarded_note`).
- Dates are strict `YYYY-MM-DD`; a blank match date is allowed (fixture not
  yet scheduled to a day), anything else fails the build.
- League display name = `competition_seasons.sponsor_name` if non-empty,
  else `competitions.name`. Team display = `teams.display_name`. Club
  display = `clubs.name`.

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
  `neutral`, `venue`/`city`/`country`, `coach`, and the knockout columns
  `extra_time`/`penalty_shootout`/`extra_time_result`.
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
- **National-team `player_id`s are their own namespace** (`MW_W_001`,
  `W_INT_NI_002`) and are absent from the `players` tab, so scorer names come
  from `nt_goals.player_name` — the opposite of the league rule. There are no
  links to `/players/` pages.
- **`nt_squads.domestic_team_id` is the only join back into the league data**
  (`-> teams.team_id`, for the club-hub link). It is blank for foreign-based
  players — expected, not an error.
- **A line-up section renders only when `nt_lineups` has rows for that
  match**, the same graceful degradation as scorers on club pages. Most
  matches have none.
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
   goals→matches/teams/players; entries and competition_seasons→their refs).
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
