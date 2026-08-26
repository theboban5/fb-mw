-- 0039_competition_level.sql — what level is this competition, and how does
-- each level compare?
--
-- WHAT WAS WRONG. Nothing in this database knows whether a competition is
-- national, regional or district football, and the three columns that look
-- like they might all say something else:
--
--   * `tier` is a rung inside its own pyramid, not a level. The Super League
--     and the Blantyre District U16 League are both tier 1. Sorting by tier
--     puts a district youth league above the National Division League, which
--     is exactly backwards.
--   * `region` says which regional FA's competition it is, and is blank on the
--     three Mzuzu district leagues — the very rows you would want it for. It
--     also reads SRFA on both Blantyre DISTRICT leagues, so it cannot separate
--     the two things it is being asked to separate.
--   * `governing_body` is the best of the three and is blank on eight of
--     twenty rows, all of them added most recently.
--
-- So the question the reporters actually asked — how is district football
-- faring against regional and national, in youth against women's against
-- men's — could not be asked of this database at all. Category could: gender
-- and age_group have been constrained enums since 0001 and need nothing here.
-- Level had to become a column.
--
-- WHAT THIS DOES.
--
--   * `competitions.level`, one of national/regional/district, NULLABLE. A
--     competition with no level is not an error and is never guessed at: it
--     falls into an "Unclassified" bucket on the Compare tab with a Level
--     select sitting next to it. Blank is a real answer here for the same
--     reason it is on matches.referee_id (0024).
--   * `set_competition_level`, so a competition can be reclassified without a
--     migration, and `create_league` gains `p_level` so a new one arrives with
--     its level already set.
--   * Two read-only views, `ops_competition_stats` and `ops_team_stats`, that
--     feed the new Compare tab in /report.
--
-- THE TRADE. Level is a fourth thing about a competition that a human types
-- and nothing verifies, so it can be wrong and no build will ever notice. That
-- is deliberate: an ERROR here would be a competition that stops every deploy
-- for everyone (CLAUDE.md), and the cost of a mislabelled league is one row in
-- the wrong bar on one admin-only screen. The CHECK constraint stops a typo;
-- judgement is the administrator's, and it is two taps to change.
--
-- WHY THESE VIEWS SPAN EVERY SEASON, WHICH NO OTHER ops_* VIEW DOES.
-- Every view in 0016 scopes to seasons.status = 'active', because a backlog is
-- about what is late right now. This is not a backlog — it is the record — and
-- scoping it the same way would silently drop the Women's Premiership, whose
-- only season (MW_2025_26) is complete. That is the entire women's football
-- dataset, dropped from the screen built to compare women's football. Season
-- is a filter on these views, never a predicate inside them.
--
-- WHY COUNTS AND NOT RATES. Every figure the Compare tab draws — goals per
-- match, clean sheet %, home win %, scorer coverage — is a ratio of two sums.
-- Emitting the sums lets the browser regroup by level, category, region, tier
-- or season with no second round-trip and, more importantly, no second SQL
-- definition of "goals per match" to drift away from this one. A view that
-- returned 2.11 could only ever be regrouped by asking Postgres again.

begin;

-- ── The column ───────────────────────────────────────────────────────────────

alter table public.competitions
  add column if not exists level text;

-- Named, so a later migration can drop it by name rather than hunting the
-- system catalogue for an anonymous check.
do $$
begin
  if not exists (select 1 from pg_constraint
                 where conname = 'competitions_level_check') then
    alter table public.competitions
      add constraint competitions_level_check
      check (level is null or level in ('national', 'regional', 'district'));
  end if;
end
$$;

comment on column public.competitions.level is
  'national | regional | district, or NULL for not yet classified. Not the '
  'same thing as tier, which is a rung within one pyramid.';


-- ── Backfill ─────────────────────────────────────────────────────────────────
-- Written out by id rather than derived from the name or the governing body,
-- because both rules get several of these twenty wrong and a wrong rule left in
-- the file would be reapplied by the next person who trusted it.

update public.competitions set level = 'national'
where competition_id in ('MW_SL', 'MW_NDL', 'MW_WP', 'MW_TOP8');

update public.competitions set level = 'regional'
where competition_id in ('MW_SRFA', 'MW_SRFA2', 'MW_CRFA', 'MW_CRFA2',
                         'MW_NRFA', 'MW_NRFA2');

-- MW_KU19 (Katswiri U19) is district on the strength of governing_body=BDYFC,
-- the Blantyre district youth body, even though its name does not say so and
-- its region is SRFA. Region and level are different questions: it is a
-- Blantyre competition played under the southern regional FA.
update public.competitions set level = 'district'
where competition_id in ('MW_KU19', 'MW_BDU16', 'MW_BDU14',
                         'MW_MDU20', 'MW_MDU16', 'MW_MDU14',
                         'MW_MGDU20', 'MW_MGDU16', 'MW_MGDU14');

-- MW_U16 (U16 Development League) is left NULL on purpose. It was a demo, not
-- a real competition, and all six of its matches are source_type=placeholder,
-- so it already renders nowhere and contributes nothing to any figure here.
-- Deleting it is a different decision from classifying it, and this migration
-- is not the place to make it.

-- The blanks the level column exposes, filled so the region axis is not half
-- empty. Mzuzu is in the northern region; Blantyre district youth football is
-- BDYFC, the same body already on MW_BDU16 and MW_KU19.
update public.competitions set governing_body = 'BDYFC'
where competition_id = 'MW_BDU14' and coalesce(governing_body, '') = '';

update public.competitions set region = 'NRFA'
where competition_id in ('MW_MDU20', 'MW_MDU16', 'MW_MDU14')
  and coalesce(region, '') = '';

-- The three Mangochi district leagues were created on 25 and 26 August 2026,
-- after the snapshot this migration was written against — MW_MGDU16 already
-- has fifteen played matches. Mangochi is a southern-region district.
update public.competitions set region = 'SRFA'
where competition_id in ('MW_MGDU20', 'MW_MGDU16', 'MW_MGDU14')
  and coalesce(region, '') = '';

update public.competitions set governing_body = 'NRFA'
where competition_id = 'MW_NRFA2' and coalesce(governing_body, '') = '';

-- Known and NOT fixed here: competitions.country holds both 'mw' and 'MW'.
-- Nothing reads it case-sensitively today, and a casing sweep across a column
-- that appears in no id belongs in its own migration where it can be reverted
-- on its own.


-- ── set_competition_level ────────────────────────────────────────────────────
-- Admin only, for the same reason create_league is: it describes the shape of
-- a competition rather than reporting what happened in a match. Unlike
-- set_entry_group (0035), which shipped without one, this DOES get a screen —
-- the Unclassified bucket on the Compare tab. The one thing that can go wrong
-- with a nullable column is a new competition created without it, and the
-- screen that notices is the screen that fixes it.

create or replace function public.set_competition_level(
  p_competition_id text,
  p_level          text
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_level text;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  if not public.is_admin() then
    raise exception 'only an administrator can change a competition''s level'
      using errcode = '42501';
  end if;

  -- An empty string is how a <select> says "none", and it means NULL here:
  -- unclassified is a state, not a fourth level.
  v_level := nullif(trim(coalesce(p_level, '')), '');
  if v_level is not null
     and v_level not in ('national', 'regional', 'district') then
    raise exception 'level must be national, regional or district'
      using errcode = '22023';
  end if;

  update public.competitions
  set level = v_level
  where competition_id = p_competition_id;

  if not found then
    raise exception 'unknown competition %', p_competition_id
      using errcode = 'P0002';
  end if;

  return coalesce(v_level, '');
end;
$$;

comment on function public.set_competition_level(text, text) is
  'Admin only. Sets a competition''s level, or clears it with a blank string.';

revoke execute on function public.set_competition_level(text, text)
  from public, anon;
grant execute on function public.set_competition_level(text, text)
  to authenticated;


-- ── create_league gains its level ────────────────────────────────────────────
-- Dropped and recreated rather than overloaded, for the reason 0035 spelled
-- out: two candidates differing only by a defaulted trailing parameter are
-- ambiguous to PostgREST, and the failure would land on the one screen an
-- administrator uses to start a season. A browser still running the old
-- app.js sends the old argument list, resolves here with p_level defaulted,
-- and creates the competition it always did — unclassified, which the Compare
-- tab will then ask about.

drop function if exists public.create_league(text, text, text[], text, text,
  text, integer, text, text, text, text, integer, integer, text[]);

create or replace function public.create_league(
  p_name         text,
  p_short_code   text,
  p_teams        text[],
  p_type         text default 'league',
  p_gender       text default 'm',
  p_age_group    text default 'senior',
  p_tier         integer default null,
  p_region       text default '',
  p_country      text default 'MW',
  p_season_id    text default null,
  p_sponsor_name text default '',
  p_points_win   integer default 3,
  p_points_draw  integer default 1,
  p_groups       text[] default null,
  p_level        text default null
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_comp     text;
  v_season   text;
  v_label    text;
  v_name     text;
  v_group    text;
  v_level    text;
  v_code     text;
  v_club     text;
  v_team     text;
  v_suffix   text;
  v_entry    text;
  v_i        integer;
  v_n        integer;
  v_created  integer := 0;
  v_seen     text[] := '{}';
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  if not public.is_admin() then
    raise exception 'only an administrator can create a competition'
      using errcode = '42501';
  end if;

  -- 1. Shape of the competition itself.
  if coalesce(trim(p_name), '') = '' then
    raise exception 'the competition needs a name' using errcode = '22023';
  end if;
  if p_type not in ('league', 'cup') then
    raise exception 'type must be league or cup' using errcode = '22023';
  end if;
  if p_gender not in ('m', 'w') then
    raise exception 'gender must be m or w' using errcode = '22023';
  end if;
  -- The vocabulary 0017 widened down to u10; unchanged here, and repeated
  -- only because this function is being replaced whole.
  if p_age_group not in ('senior', 'u20', 'u19', 'u17', 'u16', 'u15',
                          'u14', 'u13', 'u12', 'u11', 'u10') then
    raise exception 'invalid age group %', p_age_group using errcode = '22023';
  end if;

  -- Level is optional and blank means unclassified, exactly as it does in
  -- set_competition_level. A form that has not been updated yet sends nothing
  -- and gets nothing, rather than being refused.
  v_level := nullif(trim(coalesce(p_level, '')), '');
  if v_level is not null
     and v_level not in ('national', 'regional', 'district') then
    raise exception 'level must be national, regional or district'
      using errcode = '22023';
  end if;

  -- 2. competition_id. Supplied rather than derived: this is the string that
  --    appears in every match_id and in the public URL for the league, and
  --    guessing it from a sponsor-laden name produces something nobody wants
  --    to live with. Constrained to the documented alphabet.
  v_code := upper(regexp_replace(coalesce(p_short_code, ''), '[^A-Za-z0-9]', '', 'g'));
  if v_code = '' then
    raise exception 'the competition needs a short code (e.g. NRFA2)'
      using errcode = '22023';
  end if;
  if length(v_code) > 12 then
    raise exception 'short code must be 12 characters or fewer'
      using errcode = '22023';
  end if;
  v_comp := upper(coalesce(nullif(p_country, ''), 'MW')) || '_' || v_code;

  if exists (select 1 from public.competitions c
             where c.competition_id = v_comp) then
    raise exception 'competition % already exists', v_comp
      using errcode = '23505';
  end if;

  -- 3. Season.
  if p_season_id is null then
    select s.season_id into v_season
    from public.seasons s where s.status = 'active' limit 1;
    if v_season is null then
      raise exception 'no active season' using errcode = 'P0002';
    end if;
  else
    v_season := p_season_id;
  end if;

  select s.label into v_label
  from public.seasons s where s.season_id = v_season;
  if v_label is null then
    raise exception 'unknown season %', v_season using errcode = 'P0002';
  end if;

  -- 4. A competition with no teams cannot hold a fixture, so it is not a
  --    competition yet. Two is the minimum that can play a match.
  if p_teams is null or array_length(p_teams, 1) is null then
    raise exception 'add at least two teams' using errcode = '22023';
  end if;

  -- 5. The clusters, when there are any. p_groups is the same list as p_teams
  --    read down a second column, so a length that does not match means the
  --    two columns have drifted apart and every team after the drift would be
  --    filed in the wrong table. Refuse rather than guess: this is the one
  --    call that mints ids, and it is cheaper to retype the form than to
  --    unpick thirty-two entries afterwards.
  if p_groups is not null
     and coalesce(array_length(p_groups, 1), 0) <> array_length(p_teams, 1) then
    raise exception 'one cluster per team, or none at all (% teams, % clusters)',
      array_length(p_teams, 1), coalesce(array_length(p_groups, 1), 0)
      using errcode = '22023';
  end if;

  insert into public.competitions
    (competition_id, country, name, type, tier, gender, age_group, region,
     level)
  values
    (v_comp, upper(coalesce(nullif(p_country, ''), 'MW')), trim(p_name),
     p_type, p_tier, p_gender, p_age_group, coalesce(p_region, ''), v_level);

  insert into public.competition_seasons
    (competition_id, season_id, sponsor_name, points_win, points_draw, status)
  values
    (v_comp, v_season, coalesce(p_sponsor_name, ''),
     coalesce(p_points_win, 3), coalesce(p_points_draw, 1), 'active');

  -- 6. One club + team + entry per name. Indexed rather than FOREACH now,
  --    because the cluster is on the line beside the name and the line number
  --    is the only thing joining them.
  for v_i in 1 .. array_length(p_teams, 1) loop
    v_name := trim(coalesce(p_teams[v_i], ''));
    continue when v_name = '';
    v_group := left(trim(coalesce(p_groups[v_i], '')), 40);

    -- Case-insensitive dedupe of the pasted list itself, before touching the
    -- database: the same name twice would otherwise mint two clubs. A club
    -- pasted into two clusters keeps the first — the second is a mistake in
    -- the list, and set_entry_group is how it gets moved.
    if upper(v_name) = any (v_seen) then
      continue;
    end if;
    v_seen := v_seen || upper(v_name);

    -- Reuse a club that is already known by this name. Malawian clubs field
    -- teams across several competitions and the club is the same club — a
    -- second club_id for the same name would split its identity permanently.
    select c.club_id into v_club
    from public.clubs c
    where upper(c.name) = upper(v_name)
    limit 1;

    if v_club is null then
      v_code := public.club_code_from_name(v_name);
      v_club := 'MW_' || v_code;
      -- Collisions are expected — initials are short and clubs share them.
      v_n := 2;
      while exists (select 1 from public.clubs c where c.club_id = v_club) loop
        v_club := 'MW_' || v_code || v_n::text;
        v_n := v_n + 1;
      end loop;

      insert into public.clubs (club_id, name, short_name, region)
      values (v_club, v_name, v_name, coalesce(p_region, ''));
    end if;

    -- team_id = club_id + gender letter + squad level, per DATA_MODEL.md.
    -- squad_level 1: a league is created with its clubs' first teams. A
    -- reserve side is a separate team the same club fields elsewhere.
    v_suffix := upper(p_gender) || '1';
    v_team := v_club || '_' || v_suffix;

    if not exists (select 1 from public.teams t where t.team_id = v_team) then
      insert into public.teams
        (team_id, club_id, gender, age_group, squad_level, display_name)
      values
        (v_team, v_club, p_gender, p_age_group, 1, v_name);
    end if;

    -- entry_id follows the shape already in the data: competition, season
    -- label, then the team without its redundant leading prefix. The cluster
    -- is NOT in it: an entry_id is permanent and a team can be moved between
    -- clusters between seasons, or corrected an hour after it was typed.
    v_entry := v_comp || '_' || v_label || '_' ||
               regexp_replace(v_team, '^' || v_comp || '_|^MW_', '');

    if not exists (select 1 from public.entries e
                   where e.competition_id = v_comp
                     and e.season_id = v_season
                     and e.team_id = v_team) then
      insert into public.entries
        (entry_id, competition_id, season_id, team_id, "group", status)
      values (v_entry, v_comp, v_season, v_team, v_group, 'active');
      v_created := v_created + 1;
    end if;
  end loop;

  if v_created < 2 then
    -- Roll the whole thing back rather than leave a competition that cannot
    -- hold a fixture. The exception aborts the function's transaction, so the
    -- competition and season rows inserted above go with it.
    raise exception 'add at least two different teams (% usable)', v_created
      using errcode = '22023';
  end if;

  return v_comp;
end;
$$;

comment on function public.create_league(text, text, text[], text, text, text,
  integer, text, text, text, text, integer, integer, text[], text) is
  'Admin only. Creates a competition, its season row, and a club+team+entry '
  'per pasted name, in one transaction. p_groups is the cluster each team '
  'plays in, one per line of p_teams, or NULL for a single-table league. '
  'p_level is national/regional/district, or NULL for unclassified. '
  'Returns the new competition_id.';

revoke execute on function public.create_league(text, text, text[], text, text,
  text, integer, text, text, text, text, integer, integer, text[], text)
  from public, anon;
grant execute on function public.create_league(text, text, text[], text, text,
  text, integer, text, text, text, text, integer, integer, text[], text)
  to authenticated;


-- ── ops_competition_stats ────────────────────────────────────────────────────
-- One row per competition-season, every season, raw counts only.
--
-- The `where public.is_admin()` at the bottom is the real gate, repeated here
-- rather than inherited from anywhere, for the reason 0016 gives at length:
-- matches, goals, entries and lineups all carry a public read policy from
-- 0001, so security_invoker alone would show this to any signed-in reporter.
--
-- What counts as a match here is the same rule the rest of the site uses:
-- status played or awarded (an awarded walkover has a real score and no goal
-- rows), and source_type <> 'placeholder' — placeholder rows parse and then
-- render nowhere, which is why the demo U16 league contributes zero to every
-- figure below without being special-cased anywhere.

create view public.ops_competition_stats with (security_invoker = true) as
with played as (
  select m.competition_id, m.season_id, m.match_id,
         m.home_goals, m.away_goals, m.venue_id, m.source_ref, m.confidence
  from public.matches m
  where m.status in ('played', 'awarded')
    and m.source_type <> 'placeholder'
    and m.home_goals is not null
    and m.away_goals is not null
),
goal_rows as (
  select g.match_id, count(*)::integer as n
  from public.goals g group by g.match_id
),
sheets as (
  select l.match_id, count(*)::integer as n
  from public.lineups l group by l.match_id
),
fixtures as (
  -- Everything on the calendar, played or not, so "38 of 240 fixtures played"
  -- is answerable. Placeholders are excluded here too: a fixture that renders
  -- nowhere is not a fixture anyone is waiting for.
  select m.competition_id, m.season_id,
         count(*)::integer                                       as fixtures_total,
         count(*) filter (where m.status = 'scheduled')::integer  as scheduled,
         count(*) filter (where m.status = 'postponed')::integer  as postponed
  from public.matches m
  where m.source_type <> 'placeholder'
  group by m.competition_id, m.season_id
),
agg as (
  select
    p.competition_id,
    p.season_id,
    count(*)::integer                                             as played,
    sum(p.home_goals + p.away_goals)::integer                     as goals_total,
    sum(p.home_goals)::integer                                    as home_goals_total,
    sum(p.away_goals)::integer                                    as away_goals_total,
    count(*) filter (where p.home_goals > p.away_goals)::integer  as home_wins,
    count(*) filter (where p.home_goals = p.away_goals)::integer  as draws,
    count(*) filter (where p.home_goals < p.away_goals)::integer  as away_wins,

    -- A clean sheet belongs to a SIDE, not to a match, so a 0-0 is two of
    -- them and the denominator is played*2. Counting matches instead would
    -- make "clean sheet %" mean something different in a league with more
    -- goalless draws, which is the opposite of what it is being asked.
    (count(*) filter (where p.away_goals = 0)
     + count(*) filter (where p.home_goals = 0))::integer         as clean_sheet_sides,

    count(*) filter (where abs(p.home_goals - p.away_goals) >= 3)::integer
                                                                  as big_wins,
    sum(abs(p.home_goals - p.away_goals))::integer                as margin_total,

    -- Coverage. goal_rows against goals_total is the same comparison
    -- ops_match_flags.missing_scorers makes one match at a time; own goals are
    -- included on both sides of it because an own goal is a row AND a goal in
    -- the score.
    coalesce(sum(gr.n), 0)::integer                               as goal_rows,
    count(*) filter (where coalesce(sh.n, 0) > 0)::integer        as matches_with_sheet,
    count(*) filter (where p.venue_id is not null)::integer       as matches_with_venue,
    count(*) filter (where coalesce(p.source_ref, '') <> '')::integer
                                                                  as matches_with_source,
    count(*) filter (where p.confidence = 'confirmed')::integer   as matches_confirmed
  from played p
  left join goal_rows gr on gr.match_id = p.match_id
  left join sheets    sh on sh.match_id = p.match_id
  group by p.competition_id, p.season_id
),
team_counts as (
  select e.competition_id, e.season_id, count(*)::integer as teams
  from public.entries e
  where e.status = 'active'
  group by e.competition_id, e.season_id
)
select
  cs.competition_id,
  cs.season_id,
  coalesce(nullif(cs.sponsor_name, ''), c.name)  as competition_name,
  c.name                                         as competition_plain_name,
  c.type                                         as competition_type,
  c.tier                                         as competition_tier,
  c.level,
  nullif(c.region, '')                           as region,
  nullif(c.governing_body, '')                   as governing_body,
  c.gender,
  c.age_group,

  -- The second axis, derived rather than stored because gender and age_group
  -- already are the answer. Women's wins over youth deliberately: there is no
  -- women's youth competition in this dataset yet, and when there is, the row
  -- that matters for "how is women's football faring" is that one.
  case when c.gender = 'w' then 'women'
       when c.age_group <> 'senior' then 'youth'
       else 'men' end                            as category,

  s.status                                       as season_status,
  cs.status                                      as competition_season_status,

  coalesce(tc.teams, 0)                          as teams,
  coalesce(f.fixtures_total, 0)                  as fixtures_total,
  coalesce(f.scheduled, 0)                       as scheduled,
  coalesce(f.postponed, 0)                       as postponed,

  coalesce(a.played, 0)                          as played,
  (coalesce(a.played, 0) * 2)                    as sides,
  coalesce(a.goals_total, 0)                     as goals_total,
  coalesce(a.home_goals_total, 0)                as home_goals_total,
  coalesce(a.away_goals_total, 0)                as away_goals_total,
  coalesce(a.home_wins, 0)                       as home_wins,
  coalesce(a.draws, 0)                           as draws,
  coalesce(a.away_wins, 0)                       as away_wins,
  coalesce(a.clean_sheet_sides, 0)               as clean_sheet_sides,
  coalesce(a.big_wins, 0)                        as big_wins,
  coalesce(a.margin_total, 0)                    as margin_total,
  coalesce(a.goal_rows, 0)                       as goal_rows,
  coalesce(a.matches_with_sheet, 0)              as matches_with_sheet,
  coalesce(a.matches_with_venue, 0)              as matches_with_venue,
  coalesce(a.matches_with_source, 0)             as matches_with_source,
  coalesce(a.matches_confirmed, 0)               as matches_confirmed
from public.competition_seasons cs
join public.competitions c on c.competition_id = cs.competition_id
join public.seasons s      on s.season_id = cs.season_id
left join agg a         on a.competition_id = cs.competition_id
                       and a.season_id = cs.season_id
left join fixtures f    on f.competition_id = cs.competition_id
                       and f.season_id = cs.season_id
left join team_counts tc on tc.competition_id = cs.competition_id
                        and tc.season_id = cs.season_id
where public.is_admin();

comment on view public.ops_competition_stats is
  'Admin only. One row per competition-season across ALL seasons, as raw '
  'counts — every rate the Compare tab shows is a ratio of two of these, so '
  'the browser can regroup by level, category or season without asking again.';


-- ── ops_team_stats ───────────────────────────────────────────────────────────
-- One row per team per competition-season: the leaderboard behind "which club
-- has the better scoring average, and who keeps clean sheets".
--
-- The played CTE is repeated verbatim rather than selected from the view
-- above, so this object is correct on its own if a later migration grants one
-- of the two somewhere new — the same reason 0016 repeats its admin gate in
-- every body instead of layering views on views.

create view public.ops_team_stats with (security_invoker = true) as
with played as (
  select m.competition_id, m.season_id, m.match_id,
         m.home_team_id, m.away_team_id, m.home_goals, m.away_goals
  from public.matches m
  where m.status in ('played', 'awarded')
    and m.source_type <> 'placeholder'
    and m.home_goals is not null
    and m.away_goals is not null
),
sides as (
  -- One row per team per match. Every team-level figure below is an aggregate
  -- over this, which is what makes home and away symmetrical for free.
  select p.competition_id, p.season_id, p.home_team_id as team_id,
         p.home_goals as gf, p.away_goals as ga, true as at_home
  from played p
  union all
  select p.competition_id, p.season_id, p.away_team_id as team_id,
         p.away_goals as gf, p.home_goals as ga, false as at_home
  from played p
)
select
  x.competition_id,
  x.season_id,
  x.team_id,
  t.club_id,
  coalesce(nullif(t.display_name, ''), cl.name, x.team_id) as team_name,
  coalesce(nullif(cs.sponsor_name, ''), c.name)            as competition_name,
  c.level,
  case when c.gender = 'w' then 'women'
       when c.age_group <> 'senior' then 'youth'
       else 'men' end                                      as category,
  c.gender,
  c.age_group,
  c.tier                                                   as competition_tier,
  e."group"                                                as team_group,

  count(*)::integer                                        as played,
  count(*) filter (where x.gf > x.ga)::integer             as won,
  count(*) filter (where x.gf = x.ga)::integer             as drawn,
  count(*) filter (where x.gf < x.ga)::integer             as lost,
  sum(x.gf)::integer                                       as gf,
  sum(x.ga)::integer                                       as ga,
  (sum(x.gf) - sum(x.ga))::integer                         as gd,
  count(*) filter (where x.ga = 0)::integer                as clean_sheets,
  count(*) filter (where x.gf = 0)::integer                as failed_to_score,
  count(*) filter (where x.at_home)::integer               as home_played
from sides x
join public.competitions c        on c.competition_id = x.competition_id
join public.competition_seasons cs on cs.competition_id = x.competition_id
                                  and cs.season_id = x.season_id
left join public.teams t          on t.team_id = x.team_id
left join public.clubs cl         on cl.club_id = t.club_id
left join public.entries e        on e.competition_id = x.competition_id
                                 and e.season_id = x.season_id
                                 and e.team_id = x.team_id
where public.is_admin()
group by x.competition_id, x.season_id, x.team_id, t.club_id, t.display_name,
         cl.name, cs.sponsor_name, c.name, c.level, c.gender, c.age_group,
         c.tier, e."group";

comment on view public.ops_team_stats is
  'Admin only. One row per team per competition-season: P/W/D/L, goals for '
  'and against, clean sheets, failed to score. The Compare tab''s leaderboard.';


-- ── Grants ───────────────────────────────────────────────────────────────────
-- anon revoked outright; authenticated may select, and gets zero rows unless
-- is_admin() inside the view body says otherwise.

revoke all on public.ops_competition_stats from anon;
revoke all on public.ops_team_stats         from anon;

grant select on public.ops_competition_stats to authenticated;
grant select on public.ops_team_stats         to authenticated;

commit;
