# Data model

The site builds from a single published Google Spreadsheet with 13 tabs,
fetched as CSV by `src/dataset.py` (the only module that knows the URLs).
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
   pens require a full, level score, come in pairs, and are never level
   themselves; a cup match's stage must be in the knockout vocabulary
   (league stages stay free-form).
8. **Drift**: any match/team/club/player ID present in the previous
   `data/canonical/` snapshot but missing from the current fetch is a hard
   fail (catches accidental row deletion). Escape hatch:
   `python validate.py --allow-deletions` (or the same flag on `build.py`).
