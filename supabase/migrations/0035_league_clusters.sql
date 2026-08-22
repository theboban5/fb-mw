-- 0035_league_clusters.sql — a league that is four tables, not one.
--
-- WHAT WAS WRONG. The 2026 NRFA Division Two League is thirty-two clubs split
-- into four clusters of eight. Each cluster plays its own round-robin, and the
-- top two of each go into a quarter-final at the end of the season. Nothing in
-- the portal could say that. `create_league` writes one entries row per pasted
-- name and leaves entries."group" — a column that has existed since 0001 and
-- has never once been written — at its default of ''. So the only shape a
-- competition could be created in was a single table, and the two ways of
-- getting this league onto the site were both bad:
--
--   * Four competitions (MW_NRFA2A … MW_NRFA2D). Four names, four slugs, four
--     rows on the landing page, four scorer charts, four league logos — and
--     then a quarter-final between two of them that belongs to none of them.
--     It is one competition; splitting the id splits everything downstream
--     that is genuinely shared.
--   * One competition, one table of thirty-two. A club sitting 19th in a table
--     it is not actually playing in, and no way to see the eight teams it IS
--     playing. The number the reader wants — 3rd of 8, two off qualification —
--     is not on the page at all.
--
-- WHAT THIS DOES. It writes the column that was already there.
--
--   * `create_league` takes `p_groups`, a second array running alongside
--     `p_teams`: the cluster the team on that line plays in. Passing nothing
--     is the single-table league every existing competition is, unchanged.
--   * `set_entry_group` moves one team between clusters, because a cluster
--     list is read off a graphic and the first thing anyone does with 32 names
--     typed into four boxes is find one in the wrong box. Admin only, and it
--     has NO SCREEN in /report yet — the same state rename_official has been
--     in since 0024. Until it gets one this is a call an administrator makes
--     with the RPC directly.
--
-- The label is free text and deliberately so. It is a heading and a filter
-- chip, never an id: nothing joins on it, nothing parses it, and a competition
-- that calls them Groups or Zones or Pool 1 gets exactly what it typed. The
-- build sorts the tables by it (standings.group_key) and that is the whole of
-- its meaning.
--
-- WHAT IS NOT HERE. The quarter-finals. A knockout stage inside a competition
-- whose type is 'league' has nowhere to live yet — validate.py check 7 reads
-- the knockout stage vocabulary off competitions.type, so `qf` on a league
-- match is an ERROR and an ERROR is a build that deploys nothing. That is a
-- later migration, and it is not needed until the group stage finishes.

begin;

-- ── set_entry_group ──────────────────────────────────────────────────────────
-- One team, one cluster. Admin only for the same reason create_league is: it
-- decides the shape of a competition rather than reporting what happened in a
-- match. It cannot create an entry — a team that is not in the competition
-- cannot be put in one of its clusters — so the worst it can do is move a row
-- between two tables that both already exist.

create or replace function public.set_entry_group(
  p_competition_id text,
  p_season_id      text,
  p_team_id        text,
  p_group          text
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_label text;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  if not public.is_admin() then
    raise exception 'only an administrator can change a competition''s clusters'
      using errcode = '42501';
  end if;

  v_label := left(trim(coalesce(p_group, '')), 40);

  update public.entries
  set "group" = v_label, updated_at = now()
  where competition_id = p_competition_id
    and season_id = p_season_id
    and team_id = p_team_id;

  if not found then
    raise exception 'that team is not entered in % for %',
      p_competition_id, p_season_id using errcode = 'P0002';
  end if;

  return v_label;
end;
$$;

comment on function public.set_entry_group(text, text, text, text) is
  'Admin only. Moves one entry into a cluster/group, or out of one with a '
  'blank label. The label is a heading, never an id.';

revoke execute on function public.set_entry_group(text, text, text, text)
  from public, anon;
grant execute on function public.set_entry_group(text, text, text, text)
  to authenticated;


-- ── create_league gains its clusters ─────────────────────────────────────────
-- The old signature is DROPPED rather than left beside this one, for the
-- reason 0008 dropped the 4-argument submit_match_report: two overloads that
-- differ only by a defaulted trailing parameter are ambiguous to PostgREST
-- ("Could not choose the best candidate function"), and the failure would land
-- on the one screen an administrator uses to start a season. A browser still
-- running the old app.js sends the old argument list, resolves to this
-- function with p_groups defaulted, and creates exactly the competition it
-- always did.

drop function if exists public.create_league(text, text, text[], text, text,
  text, integer, text, text, text, text, integer, integer);

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
  p_groups       text[] default null
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
    (competition_id, country, name, type, tier, gender, age_group, region)
  values
    (v_comp, upper(coalesce(nullif(p_country, ''), 'MW')), trim(p_name),
     p_type, p_tier, p_gender, p_age_group, coalesce(p_region, ''));

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
    raise exception 'add at least two different teams (got %)', v_created
      using errcode = '22023';
  end if;

  -- teams_count is denormalized and read by the renderers; set it from what
  -- was actually created rather than from what was pasted.
  update public.competition_seasons
  set teams_count = v_created, updated_at = now()
  where competition_id = v_comp and season_id = v_season;

  return v_comp;
end;
$$;

comment on function public.create_league(text, text, text[], text, text, text,
  integer, text, text, text, text, integer, integer, text[]) is
  'Admin only. Creates a competition, its season row, and a club+team+entry '
  'per pasted name, in one transaction. p_groups is the cluster each team '
  'plays in, one per line of p_teams, or NULL for a single-table league. '
  'Returns the new competition_id.';

revoke execute on function public.create_league(text, text, text[], text, text,
  text, integer, text, text, text, text, integer, integer, text[])
  from public, anon;
grant execute on function public.create_league(text, text, text[], text, text,
  text, integer, text, text, text, text, integer, integer, text[])
  to authenticated;

commit;
