-- Widen the age_group vocabulary down to u10 (was capped at u15, with no u18).
-- Onboarding the Blantyre District Nthanda U14 League needs u14, and the
-- reporter form now offers everything down to u10 to cover younger age
-- grades without another migration each time one shows up.

alter table public.teams
  drop constraint teams_age_group_check,
  add constraint teams_age_group_check
    check (age_group in ('senior', 'u20', 'u19', 'u17', 'u16', 'u15',
                          'u14', 'u13', 'u12', 'u11', 'u10'));

alter table public.competitions
  drop constraint competitions_age_group_check,
  add constraint competitions_age_group_check
    check (age_group in ('senior', 'u20', 'u19', 'u17', 'u16', 'u15',
                          'u14', 'u13', 'u12', 'u11', 'u10'));

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
  p_points_draw  integer default 1
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
  v_code     text;
  v_club     text;
  v_team     text;
  v_suffix   text;
  v_entry    text;
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

  -- 5. One club + team + entry per name.
  foreach v_name in array p_teams loop
    v_name := trim(coalesce(v_name, ''));
    continue when v_name = '';

    -- Case-insensitive dedupe of the pasted list itself, before touching the
    -- database: the same name twice would otherwise mint two clubs.
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
    -- label, then the team without its redundant leading prefix.
    v_entry := v_comp || '_' || v_label || '_' ||
               regexp_replace(v_team, '^' || v_comp || '_|^MW_', '');

    if not exists (select 1 from public.entries e
                   where e.competition_id = v_comp
                     and e.season_id = v_season
                     and e.team_id = v_team) then
      insert into public.entries
        (entry_id, competition_id, season_id, team_id, status)
      values (v_entry, v_comp, v_season, v_team, 'active');
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
