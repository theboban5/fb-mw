-- 0012_nt_reporting.sql — national-team reporting through /report.
--
-- Until now the seven nt_* tables had public read policies and no write path
-- at all: no RPC, no policy, no script. Updating a Malawi international meant
-- editing Postgres by hand with the secret key. That was tolerable while the
-- data arrived a few times a season; it is not a way to run a tournament.
--
-- WHAT THE SCHEMA ALREADY GUARANTEES, AND THIS DOES NOT REPEAT. 0001 put real
-- CHECK constraints on these tables — score present as a pair, played implies
-- a score, scheduled implies none, knockout slot uniqueness, a linked tie
-- carrying no score of its own, the role and stage vocabularies. Single-row
-- rules are the database's job and it already does them. Everything below is
-- the CROSS-ROW half, which is exactly the half validate.py check 9 enforces
-- at build time and a constraint cannot see:
--
--   * goal rows per side never exceed that side's score;
--   * at most eleven starting rows per match and team;
--   * a sub_on row carries a minute, and a replaced_player names someone in
--     the same side's line-up;
--   * a knockout tie belongs to a competition that has a group table, and a
--     group table contains at least one of OUR teams — otherwise the bracket
--     or group renders on no page at all and vanishes silently;
--   * a rival's group row carries its own team_name, having no nt_teams row
--     to read one from.
--
-- A reporter who could write a row breaking any of those could stop every
-- future build. That is the whole reason these are RPCs rather than policies.
--
-- WHO MAY WRITE. can_edit_nt() mirrors can_report_match(): an administrator
-- may edit every national team, and everyone else needs an explicit
-- assignment. Nobody holds one today — the table exists so that a reporter can
-- later be given the U20s or the women's team without another migration.

begin;

-- ── nt_assignments ───────────────────────────────────────────────────────────

create table public.nt_assignments (
  reporter_id  text not null references public.reporters (reporter_id)
               on delete cascade,
  team_code    text not null references public.nt_teams (team_code)
               on delete cascade,
  created_at   timestamptz not null default now(),
  primary key (reporter_id, team_code)
);

create index nt_assignments_reporter_idx on public.nt_assignments (reporter_id);

comment on table public.nt_assignments is
  'Which national teams a reporter may report. Administrators need no row: '
  'can_edit_nt() grants them everything, exactly as can_report_match() does '
  'for club competitions. Written from the CLI only.';

alter table public.nt_assignments enable row level security;

-- Same shape as reporter_assignments: you may read your own, an admin reads
-- all. No insert/update/delete policy — granting a team is a CLI operation.
create policy nt_assignments_read_own
  on public.nt_assignments for select to authenticated
  using (reporter_id = public.current_reporter_id() or public.is_admin());


-- ── The two guards ───────────────────────────────────────────────────────────

create or replace function public.can_edit_nt(p_team_code text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select public.is_admin() or exists (
    select 1
    from public.nt_assignments a
    join public.reporters r on r.reporter_id = a.reporter_id
    where a.team_code = p_team_code
      and r.active
      and r.auth_user_id = (select auth.uid())
  );
$$;

comment on function public.can_edit_nt(text) is
  'May the caller report this national team? Administrator, or assigned.';

-- A competition is not owned by a row anywhere — it is a NAME shared by
-- nt_competitions, nt_knockout, nt_squads and nt_matches. So "may I edit this
-- competition" resolves through the group table: whichever of our teams has a
-- row in it. A competition with no group table yet belongs to nobody, which is
-- why creating one is an administrator's act (or an assigned reporter naming
-- their own team as the first row).
create or replace function public.can_edit_nt_competition(p_competition_name text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select public.is_admin() or exists (
    select 1
    from public.nt_competitions c
    where c.competition_name = p_competition_name
      and public.can_edit_nt(c.team_code)
  );
$$;

revoke execute on function public.can_edit_nt(text) from public, anon;
revoke execute on function public.can_edit_nt_competition(text) from public, anon;
grant execute on function public.can_edit_nt(text) to authenticated;
grant execute on function public.can_edit_nt_competition(text) to authenticated;


-- ── Minting ids ──────────────────────────────────────────────────────────────
-- Every nt_* id in the data is a plain counter rendered as text ('1'..'41').
-- Keep it that way: they are opaque, and a new shape would only invite someone
-- to parse them.

create or replace function public.next_nt_id(p_table text)
returns text
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_max integer;
begin
  case p_table
    when 'nt_matches' then
      select max(match_id::integer) into v_max from public.nt_matches
        where match_id ~ '^[0-9]+$';
    when 'nt_goals' then
      select max(goal_id::integer) into v_max from public.nt_goals
        where goal_id ~ '^[0-9]+$';
    when 'nt_knockout' then
      select max(tie_id::integer) into v_max from public.nt_knockout
        where tie_id ~ '^[0-9]+$';
    when 'nt_squads' then
      select max(squad_id::integer) into v_max from public.nt_squads
        where squad_id ~ '^[0-9]+$';
    else
      raise exception 'no id sequence for %', p_table using errcode = '22023';
  end case;
  return (coalesce(v_max, 0) + 1)::text;
end;
$$;

revoke execute on function public.next_nt_id(text)
  from public, anon, authenticated;


-- ── Fixtures ─────────────────────────────────────────────────────────────────

create or replace function public.create_nt_match(
  p_team_code   text,
  p_competition text,
  p_opponent    text,
  p_date        text default '',
  p_kickoff     text default '',
  p_home_away   text default '',
  p_neutral     boolean default false,
  p_venue       text default '',
  p_city        text default '',
  p_country     text default ''
)
returns setof public.nt_matches
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_match public.nt_matches;
begin
  if not public.can_edit_nt(p_team_code) then
    raise exception 'you are not assigned to that national team'
      using errcode = '42501';
  end if;
  if btrim(coalesce(p_opponent, '')) = '' then
    raise exception 'a fixture needs an opponent' using errcode = '22023';
  end if;
  if btrim(coalesce(p_competition, '')) = '' then
    raise exception 'a fixture needs a competition' using errcode = '22023';
  end if;

  insert into public.nt_matches (
    match_id, team_code, competition, opponent, date, kickoff,
    home_away, neutral, venue, city, country, status
  ) values (
    public.next_nt_id('nt_matches'), p_team_code, btrim(p_competition),
    btrim(p_opponent), btrim(coalesce(p_date, '')),
    btrim(coalesce(p_kickoff, '')), btrim(coalesce(p_home_away, '')),
    coalesce(p_neutral, false), btrim(coalesce(p_venue, '')),
    btrim(coalesce(p_city, '')), btrim(coalesce(p_country, '')), 'scheduled'
  )
  returning * into v_match;
  return next v_match;
end;
$$;


create or replace function public.update_nt_fixture(
  p_match_id    text,
  p_competition text,
  p_opponent    text,
  p_date        text default '',
  p_kickoff     text default '',
  p_home_away   text default '',
  p_neutral     boolean default false,
  p_venue       text default '',
  p_city        text default '',
  p_country     text default ''
)
returns setof public.nt_matches
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_match public.nt_matches;
begin
  select * into v_match from public.nt_matches
  where match_id = p_match_id for update;
  if not found then
    raise exception 'match not found' using errcode = 'P0002';
  end if;
  if not public.can_edit_nt(v_match.team_code) then
    raise exception 'you are not assigned to that national team'
      using errcode = '42501';
  end if;

  -- Deliberately cannot write the SCORE. Same split as the club side, and for
  -- the same reason: being allowed to fix a venue is not permission to change
  -- a result. submit_nt_result is the only door to the score.
  update public.nt_matches set
    competition = btrim(coalesce(p_competition, competition)),
    opponent    = btrim(coalesce(p_opponent, opponent)),
    date        = btrim(coalesce(p_date, '')),
    kickoff     = btrim(coalesce(p_kickoff, '')),
    home_away   = btrim(coalesce(p_home_away, '')),
    neutral     = coalesce(p_neutral, false),
    venue       = btrim(coalesce(p_venue, '')),
    city        = btrim(coalesce(p_city, '')),
    country     = btrim(coalesce(p_country, '')),
    updated_at  = now()
  where match_id = p_match_id
  returning * into v_match;
  return next v_match;
end;
$$;


-- ── Results ──────────────────────────────────────────────────────────────────

create or replace function public.submit_nt_result(
  p_match_id          text,
  p_team_score        integer,
  p_opponent_score    integer,
  p_status            text default 'played',
  p_extra_time        boolean default false,
  p_penalty_shootout  text default '',
  p_extra_time_result text default '',
  p_coach             text default null
)
returns setof public.nt_matches
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_match public.nt_matches;
  v_ours  integer;
  v_them  integer;
begin
  select * into v_match from public.nt_matches
  where match_id = p_match_id for update;
  if not found then
    raise exception 'match not found' using errcode = 'P0002';
  end if;
  if not public.can_edit_nt(v_match.team_code) then
    raise exception 'you are not assigned to that national team'
      using errcode = '42501';
  end if;

  if p_status not in ('scheduled', 'played', 'awarded') then
    raise exception 'invalid status %', p_status using errcode = '22023';
  end if;

  -- 'scheduled' means "no result yet", so it clears the score rather than
  -- being rejected: a result entered against the wrong match has to be
  -- retractable, and the table's own constraint forbids keeping both.
  if p_status = 'scheduled' then
    v_ours := null;
    v_them := null;
  else
    if p_team_score is null or p_opponent_score is null then
      raise exception 'a played match needs both scores' using errcode = '22023';
    end if;
    if p_team_score < 0 or p_opponent_score < 0
       or p_team_score > 99 or p_opponent_score > 99 then
      raise exception 'invalid score' using errcode = '22023';
    end if;
    v_ours := p_team_score;
    v_them := p_opponent_score;
  end if;

  -- validate.py check 9: goal rows per side never exceed that side's score.
  -- Lowering a score under the scorers already recorded would break every
  -- future build, so it is refused here while it is still one person's typo.
  if v_ours is not null then
    if (select count(*) from public.nt_goals g
        where g.match_id = p_match_id and g.team_id = v_match.team_code) > v_ours
    then
      raise exception
        'there are already more scorers than that for %', v_match.team_code
        using errcode = '22023';
    end if;
    if (select count(*) from public.nt_goals g
        where g.match_id = p_match_id and g.team_id <> v_match.team_code) > v_them
    then
      raise exception 'there are already more scorers than that for the opponent'
        using errcode = '22023';
    end if;
  elsif exists (select 1 from public.nt_goals g where g.match_id = p_match_id) then
    raise exception
      'remove the scorers before setting this match back to not played'
      using errcode = '22023';
  end if;

  update public.nt_matches set
    team_score        = v_ours,
    opponent_score    = v_them,
    status            = p_status,
    extra_time        = coalesce(p_extra_time, false),
    penalty_shootout  = btrim(coalesce(p_penalty_shootout, '')),
    extra_time_result = btrim(coalesce(p_extra_time_result, '')),
    coach             = btrim(coalesce(p_coach, coach)),
    updated_at        = now()
  where match_id = p_match_id
  returning * into v_match;
  return next v_match;
end;
$$;


-- ── Scorers ──────────────────────────────────────────────────────────────────
-- team_id is NOT a foreign key here and is not resolved: a goal may belong to
-- the opponent, who has no nt_teams row (see 0001). Passing our own team_code
-- means we scored it; anything else is theirs.

create or replace function public.submit_nt_goal(
  p_match_id    text,
  p_team_id     text,
  p_player_name text,
  p_minute      text default '',
  p_stoppage    text default '',
  p_period      text default '',
  p_goal_type   text default '',
  p_player_id   text default ''
)
returns setof public.nt_goals
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_match public.nt_matches;
  v_goal  public.nt_goals;
  v_score integer;
  v_have  integer;
begin
  select * into v_match from public.nt_matches
  where match_id = p_match_id for update;
  if not found then
    raise exception 'match not found' using errcode = 'P0002';
  end if;
  if not public.can_edit_nt(v_match.team_code) then
    raise exception 'you are not assigned to that national team'
      using errcode = '42501';
  end if;
  if btrim(coalesce(p_player_name, '')) = '' then
    raise exception 'a scorer needs a name' using errcode = '22023';
  end if;
  if p_goal_type not in ('', 'penalty', 'own goal', 'own_goal') then
    raise exception 'invalid goal type %', p_goal_type using errcode = '22023';
  end if;
  if v_match.team_score is null then
    raise exception 'publish the score before adding scorers'
      using errcode = '22023';
  end if;

  -- Which side, and therefore which score to count against. An own goal
  -- already credits the side that benefits, so counting by team_id lines up.
  if p_team_id = v_match.team_code then
    v_score := v_match.team_score;
    select count(*) into v_have from public.nt_goals g
      where g.match_id = p_match_id and g.team_id = v_match.team_code;
  else
    v_score := v_match.opponent_score;
    select count(*) into v_have from public.nt_goals g
      where g.match_id = p_match_id and g.team_id <> v_match.team_code;
  end if;

  if v_have >= v_score then
    raise exception 'all % goal(s) for that side already have a scorer', v_score
      using errcode = '22023';
  end if;

  insert into public.nt_goals (
    goal_id, match_id, team_id, player_name, player_id,
    minute, stoppage, period, goal_type
  ) values (
    public.next_nt_id('nt_goals'), p_match_id, p_team_id,
    btrim(p_player_name), btrim(coalesce(p_player_id, '')),
    btrim(coalesce(p_minute, '')), btrim(coalesce(p_stoppage, '')),
    btrim(coalesce(p_period, '')), p_goal_type
  )
  returning * into v_goal;
  return next v_goal;
end;
$$;


create or replace function public.delete_nt_goal(p_goal_id text)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_goal  public.nt_goals;
  v_match public.nt_matches;
begin
  select * into v_goal from public.nt_goals where goal_id = p_goal_id;
  if not found then
    return false;
  end if;
  select * into v_match from public.nt_matches where match_id = v_goal.match_id;
  if not public.can_edit_nt(v_match.team_code) then
    raise exception 'you are not assigned to that national team'
      using errcode = '42501';
  end if;
  delete from public.nt_goals where goal_id = p_goal_id;
  return true;
end;
$$;


-- ── Line-ups, which are also the cards and the substitutions ─────────────────
-- Unlike the club side there is no incidents table: a booking is a flag on the
-- player's line-up row, and a substitution is a role=sub_on row carrying the
-- minute and the name it replaced. So one save writes the XI, the bench, the
-- cards and the changes together — which is also how they are read off a team
-- sheet.
--
-- WHOLE SIDE AT A TIME, replacing what is there. The rules below are about the
-- set (eleven starters; a replaced name that is present), so validating a row
-- in isolation cannot work, and an edit is "here is the team sheet as it now
-- stands" rather than a patch.

create or replace function public.save_nt_lineup(
  p_match_id text,
  p_team_id  text,
  p_rows     jsonb
)
returns setof public.nt_lineups
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_match    public.nt_matches;
  v_names    text[];
  v_starting integer;
  v_row      jsonb;
  v_ord      integer := 0;
begin
  select * into v_match from public.nt_matches
  where match_id = p_match_id for update;
  if not found then
    raise exception 'match not found' using errcode = 'P0002';
  end if;
  if not public.can_edit_nt(v_match.team_code) then
    raise exception 'you are not assigned to that national team'
      using errcode = '42501';
  end if;
  if jsonb_typeof(p_rows) <> 'array' then
    raise exception 'the line-up must be a list' using errcode = '22023';
  end if;

  select array_agg(btrim(r->>'player_name')) into v_names
  from jsonb_array_elements(p_rows) r;
  v_names := coalesce(v_names, '{}');

  if exists (select 1 from unnest(v_names) n where coalesce(n, '') = '') then
    raise exception 'every line-up row needs a player name' using errcode = '22023';
  end if;
  -- The table's primary key is (match_id, player_name), so a repeat would fail
  -- on insert anyway — said here because "duplicate key" is not a sentence a
  -- reporter can act on.
  if (select count(distinct n) from unnest(v_names) n) <> array_length(v_names, 1) then
    raise exception 'the same player is listed twice' using errcode = '22023';
  end if;

  select count(*) into v_starting
  from jsonb_array_elements(p_rows) r where r->>'role' = 'starting';
  if v_starting > 11 then
    raise exception 'a starting XI is eleven players, not %', v_starting
      using errcode = '22023';
  end if;

  for v_row in select * from jsonb_array_elements(p_rows) loop
    if coalesce(v_row->>'role', '') not in ('starting', 'sub_on', 'unused_sub') then
      raise exception 'invalid line-up role %', coalesce(v_row->>'role', '')
        using errcode = '22023';
    end if;
    if v_row->>'role' = 'sub_on'
       and btrim(coalesce(v_row->>'minute_on', '')) = '' then
      raise exception 'a substitute who came on needs the minute (%)',
        v_row->>'player_name' using errcode = '22023';
    end if;
    if btrim(coalesce(v_row->>'replaced_player', '')) <> ''
       and not (btrim(v_row->>'replaced_player') = any (v_names)) then
      raise exception
        'replaced player % is not in this line-up', v_row->>'replaced_player'
        using errcode = '22023';
    end if;
  end loop;

  delete from public.nt_lineups
  where match_id = p_match_id and team_id = p_team_id;

  for v_row in select * from jsonb_array_elements(p_rows) loop
    v_ord := v_ord + 1;
    insert into public.nt_lineups (
      match_id, team_id, player_name, player_id, shirt_number, position,
      role, captain, minute_on, minute_off, replaced_player,
      yellow_card, yellow_red_card, red_card, ord
    ) values (
      p_match_id, p_team_id, btrim(v_row->>'player_name'),
      btrim(coalesce(v_row->>'player_id', '')),
      btrim(coalesce(v_row->>'shirt_number', '')),
      btrim(coalesce(v_row->>'position', '')),
      v_row->>'role',
      coalesce((v_row->>'captain')::boolean, false),
      btrim(coalesce(v_row->>'minute_on', '')),
      btrim(coalesce(v_row->>'minute_off', '')),
      btrim(coalesce(v_row->>'replaced_player', '')),
      coalesce((v_row->>'yellow_card')::boolean, false),
      coalesce((v_row->>'yellow_red_card')::boolean, false),
      coalesce((v_row->>'red_card')::boolean, false),
      v_ord
    );
  end loop;

  return query
    select * from public.nt_lineups
    where match_id = p_match_id and team_id = p_team_id order by ord;
end;
$$;


-- ── Competitions: the group table is what makes one exist ────────────────────
-- There is no competitions table for national teams. A competition is a NAME,
-- and what gives it a page is a group table containing one of our teams —
-- validate.py check 9 rejects a group with none, and a bracket whose
-- competition_name matches no group row, because both would render nowhere and
-- fail silently. So creating a competition IS creating that first group row.

create or replace function public.create_nt_competition(
  p_competition_name text,
  p_team_code        text,
  p_group_name       text default '',
  p_wikipedia_url    text default ''
)
returns setof public.nt_competitions
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_row public.nt_competitions;
begin
  if not public.can_edit_nt(p_team_code) then
    raise exception 'you are not assigned to that national team'
      using errcode = '42501';
  end if;
  if btrim(coalesce(p_competition_name, '')) = '' then
    raise exception 'a competition needs a name' using errcode = '22023';
  end if;
  if not exists (select 1 from public.nt_teams t
                 where t.team_code = p_team_code) then
    raise exception 'that is not a national team' using errcode = '22023';
  end if;

  insert into public.nt_competitions (
    team_code, competition_name, group_name, team_name, wikipedia_url
  ) values (
    p_team_code, btrim(p_competition_name), btrim(coalesce(p_group_name, '')),
    (select team_name from public.nt_teams where team_code = p_team_code),
    btrim(coalesce(p_wikipedia_url, ''))
  )
  returning * into v_row;
  return next v_row;
end;
$$;


create or replace function public.upsert_nt_group_row(
  p_competition_name text,
  p_group_name       text,
  p_team_code        text,
  p_team_name        text default '',
  p_position         text default '',
  p_played           text default '',
  p_won              text default '',
  p_drawn            text default '',
  p_lost             text default '',
  p_goals_for        text default '',
  p_goals_against    text default '',
  p_points           text default '',
  p_wikipedia_url    text default ''
)
returns setof public.nt_competitions
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_row  public.nt_competitions;
  v_ours boolean;
begin
  if not public.can_edit_nt_competition(p_competition_name) then
    raise exception 'you are not assigned to that competition'
      using errcode = '42501';
  end if;
  if btrim(coalesce(p_team_code, '')) = '' then
    raise exception 'a group row needs a team code' using errcode = '22023';
  end if;

  -- A rival's code (NIGERIA_W) has no nt_teams row to read a display name
  -- from, so it must carry its own or render nameless — validate.py check 9.
  v_ours := exists (select 1 from public.nt_teams t
                    where t.team_code = p_team_code);
  if not v_ours and btrim(coalesce(p_team_name, '')) = '' then
    raise exception 'a team that is not one of ours needs a name here'
      using errcode = '22023';
  end if;

  -- The primary key is (team_code, competition_name) — group_name is NOT part
  -- of it. A team therefore appears at most once per competition, and moving
  -- it between groups is an update of this row rather than a second one.
  insert into public.nt_competitions (
    team_code, competition_name, group_name, team_name, position,
    played, won, drawn, lost, goals_for, goals_against, points,
    wikipedia_url, last_update
  ) values (
    p_team_code, btrim(p_competition_name), btrim(coalesce(p_group_name, '')),
    case when v_ours
      then (select team_name from public.nt_teams where team_code = p_team_code)
      else btrim(p_team_name) end,
    btrim(coalesce(p_position, '')), btrim(coalesce(p_played, '')),
    btrim(coalesce(p_won, '')), btrim(coalesce(p_drawn, '')),
    btrim(coalesce(p_lost, '')), btrim(coalesce(p_goals_for, '')),
    btrim(coalesce(p_goals_against, '')), btrim(coalesce(p_points, '')),
    btrim(coalesce(p_wikipedia_url, '')), to_char(now(), 'YYYY-MM-DD')
  )
  on conflict (team_code, competition_name) do update set
    group_name    = excluded.group_name,
    team_name     = excluded.team_name,
    position      = excluded.position,
    played        = excluded.played,
    won           = excluded.won,
    drawn         = excluded.drawn,
    lost          = excluded.lost,
    goals_for     = excluded.goals_for,
    goals_against = excluded.goals_against,
    points        = excluded.points,
    wikipedia_url = excluded.wikipedia_url,
    last_update   = excluded.last_update,
    updated_at    = now()
  returning * into v_row;
  return next v_row;
end;
$$;


create or replace function public.delete_nt_group_row(
  p_competition_name text,
  p_team_code        text
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_row public.nt_competitions;
begin
  if not public.can_edit_nt_competition(p_competition_name) then
    raise exception 'you are not assigned to that competition'
      using errcode = '42501';
  end if;

  select * into v_row from public.nt_competitions
  where competition_name = p_competition_name and team_code = p_team_code;
  if not found then
    return false;
  end if;

  -- Removing the last row of ours would orphan the whole group AND any bracket
  -- hanging off the competition name — both would render on no page, and
  -- validate.py check 9 would fail the next build rather than let them vanish
  -- quietly. Refuse while it is still one click to undo. The group is keyed by
  -- (competition_name, group_name), which is why the check reads group_name
  -- off the row rather than taking it as an argument that could disagree.
  if exists (select 1 from public.nt_teams where team_code = p_team_code)
     and not exists (
       select 1 from public.nt_competitions c
       join public.nt_teams t on t.team_code = c.team_code
       where c.competition_name = p_competition_name
         and c.group_name = v_row.group_name
         and c.team_code <> p_team_code)
  then
    raise exception
      'that is the only one of our teams in this group — removing it would '
      'leave the group and its bracket on no page'
      using errcode = '22023';
  end if;

  delete from public.nt_competitions
  where competition_name = p_competition_name and team_code = p_team_code;
  return true;
end;
$$;


-- ── Brackets ─────────────────────────────────────────────────────────────────

create or replace function public.upsert_nt_tie(
  p_competition_name text,
  p_stage            text,
  p_slot             integer,
  p_home_name        text default '',
  p_away_name        text default '',
  p_home_from        text default '',
  p_away_from        text default '',
  p_home_score       integer default null,
  p_away_score       integer default null,
  p_home_pens        integer default null,
  p_away_pens        integer default null,
  p_extra_time       boolean default false,
  p_date             text default '',
  p_kickoff          text default '',
  p_venue            text default '',
  p_city             text default '',
  p_status           text default 'scheduled',
  p_nt_match_id      text default ''
)
returns setof public.nt_knockout
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_tie      public.nt_knockout;
  v_match_id text;
  v_linked   boolean;
begin
  if not public.can_edit_nt_competition(p_competition_name) then
    raise exception 'you are not assigned to that competition'
      using errcode = '42501';
  end if;

  -- validate.py check 9: a bracket whose competition_name matches no group
  -- row belongs to no page. Create the group table first.
  if not exists (select 1 from public.nt_competitions c
                 where c.competition_name = p_competition_name) then
    raise exception 'that competition has no group table yet'
      using errcode = '22023';
  end if;

  v_match_id := nullif(btrim(coalesce(p_nt_match_id, '')), '');
  v_linked := v_match_id is not null;
  if v_linked and not exists (select 1 from public.nt_matches m
                              where m.match_id = v_match_id) then
    raise exception 'that match does not exist' using errcode = '22023';
  end if;

  -- A linked tie takes its score, date and venue from the nt_matches row it
  -- names (see nt._link_match), and 0001 forbids it carrying a score of its
  -- own. Blanking the rest here stops the two disagreeing, which validate.py
  -- reports as a contradiction where nt_matches wins.
  insert into public.nt_knockout (
    tie_id, competition_name, stage, slot, home_name, away_name,
    home_from, away_from, home_score, away_score, home_pens, away_pens,
    extra_time, date, kickoff, venue, city, status, nt_match_id
  ) values (
    public.next_nt_id('nt_knockout'), btrim(p_competition_name), p_stage,
    coalesce(p_slot, 0), btrim(coalesce(p_home_name, '')),
    btrim(coalesce(p_away_name, '')), btrim(coalesce(p_home_from, '')),
    btrim(coalesce(p_away_from, '')),
    case when v_linked then null else p_home_score end,
    case when v_linked then null else p_away_score end,
    case when v_linked then null else p_home_pens end,
    case when v_linked then null else p_away_pens end,
    case when v_linked then false else coalesce(p_extra_time, false) end,
    case when v_linked then '' else btrim(coalesce(p_date, '')) end,
    case when v_linked then '' else btrim(coalesce(p_kickoff, '')) end,
    case when v_linked then '' else btrim(coalesce(p_venue, '')) end,
    case when v_linked then '' else btrim(coalesce(p_city, '')) end,
    case when v_linked then 'scheduled' else coalesce(p_status, 'scheduled') end,
    v_match_id
  )
  on conflict (competition_name, stage, slot) do update set
    home_name   = excluded.home_name,
    away_name   = excluded.away_name,
    home_from   = excluded.home_from,
    away_from   = excluded.away_from,
    home_score  = excluded.home_score,
    away_score  = excluded.away_score,
    home_pens   = excluded.home_pens,
    away_pens   = excluded.away_pens,
    extra_time  = excluded.extra_time,
    date        = excluded.date,
    kickoff     = excluded.kickoff,
    venue       = excluded.venue,
    city        = excluded.city,
    status      = excluded.status,
    nt_match_id = excluded.nt_match_id,
    updated_at  = now()
  returning * into v_tie;
  return next v_tie;
end;
$$;


create or replace function public.delete_nt_tie(p_tie_id text)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_tie public.nt_knockout;
begin
  select * into v_tie from public.nt_knockout where tie_id = p_tie_id;
  if not found then
    return false;
  end if;
  if not public.can_edit_nt_competition(v_tie.competition_name) then
    raise exception 'you are not assigned to that competition'
      using errcode = '42501';
  end if;

  -- A slot fed by a tie that no longer exists renders a blank forever, and
  -- validate.py check 9 fails the build over it.
  if exists (select 1 from public.nt_knockout k
             where k.home_from like '%:' || p_tie_id
                or k.away_from like '%:' || p_tie_id) then
    raise exception 'another tie is waiting on this one — clear that first'
      using errcode = '22023';
  end if;

  delete from public.nt_knockout where tie_id = p_tie_id;
  return true;
end;
$$;


-- ── Squads ───────────────────────────────────────────────────────────────────
-- One squad is many rows sharing a squad_id, so it is saved whole like a
-- line-up: a squad announcement is a list, and editing it means republishing
-- the list rather than patching a player at a time.

create or replace function public.save_nt_squad(
  p_team_id             text,
  p_competition         text,
  p_players             jsonb,
  p_squad_id            text default '',
  p_announcement_date   text default '',
  p_competition_context text default '',
  p_coach               text default ''
)
returns setof public.nt_squads
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_squad_id text;
  v_row      jsonb;
  v_ord      integer := 0;
begin
  if not public.can_edit_nt(p_team_id) then
    raise exception 'you are not assigned to that national team'
      using errcode = '42501';
  end if;
  if not exists (select 1 from public.nt_teams where team_code = p_team_id) then
    raise exception 'that is not a national team' using errcode = '22023';
  end if;
  if jsonb_typeof(p_players) <> 'array' or jsonb_array_length(p_players) = 0 then
    raise exception 'a squad needs at least one player' using errcode = '22023';
  end if;

  v_squad_id := nullif(btrim(coalesce(p_squad_id, '')), '');
  if v_squad_id is null then
    v_squad_id := public.next_nt_id('nt_squads');
  end if;

  delete from public.nt_squads where squad_id = v_squad_id;

  for v_row in select * from jsonb_array_elements(p_players) loop
    if btrim(coalesce(v_row->>'player_name', '')) = '' then
      raise exception 'every squad row needs a player name' using errcode = '22023';
    end if;
    v_ord := v_ord + 1;
    insert into public.nt_squads (
      squad_id, competition, team_id, announcement_date, competition_context,
      player_name, player_id, position, shirt_number, club,
      domestic_team_id, club_country, notes, coach, ord
    ) values (
      v_squad_id, btrim(coalesce(p_competition, '')), p_team_id,
      btrim(coalesce(p_announcement_date, '')),
      btrim(coalesce(p_competition_context, '')),
      btrim(v_row->>'player_name'), btrim(coalesce(v_row->>'player_id', '')),
      btrim(coalesce(v_row->>'position', '')),
      btrim(coalesce(v_row->>'shirt_number', '')),
      btrim(coalesce(v_row->>'club', '')),
      btrim(coalesce(v_row->>'domestic_team_id', '')),
      btrim(coalesce(v_row->>'club_country', '')),
      btrim(coalesce(v_row->>'notes', '')),
      btrim(coalesce(p_coach, '')), v_ord
    );
  end loop;

  return query select * from public.nt_squads
    where squad_id = v_squad_id order by ord;
end;
$$;


-- ── Grants ───────────────────────────────────────────────────────────────────
-- Every one of these is guarded internally by can_edit_nt / can_edit_nt_
-- competition, so `authenticated` is the right grant and the function is the
-- boundary — the same pattern as submit_match_report.

do $$
declare fn text;
begin
  foreach fn in array array[
    'create_nt_match(text,text,text,text,text,text,boolean,text,text,text)',
    'update_nt_fixture(text,text,text,text,text,text,boolean,text,text,text)',
    'submit_nt_result(text,integer,integer,text,boolean,text,text,text)',
    'submit_nt_goal(text,text,text,text,text,text,text,text)',
    'delete_nt_goal(text)',
    'save_nt_lineup(text,text,jsonb)',
    'create_nt_competition(text,text,text,text)',
    'upsert_nt_group_row(text,text,text,text,text,text,text,text,text,text,text,text,text)',
    'delete_nt_group_row(text,text)',
    'upsert_nt_tie(text,text,integer,text,text,text,text,integer,integer,integer,integer,boolean,text,text,text,text,text,text)',
    'delete_nt_tie(text)',
    'save_nt_squad(text,text,jsonb,text,text,text,text)'
  ] loop
    execute format('revoke execute on function public.%s from public, anon', fn);
    execute format('grant execute on function public.%s to authenticated', fn);
  end loop;
end;
$$;

commit;
