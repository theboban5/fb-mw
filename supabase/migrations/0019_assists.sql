-- 0019_assists.sql — the assist column finally gets a way in.
--
-- goals.assist_player_id has been in the schema since 0001 and nt_goals'
-- since 0001 too. Both are FK-checked by validate.py check 2. Both held zero
-- rows, for one reason: no screen ever wrote them. There was nothing wrong
-- with the column and nothing to fix in the build — the gap was entirely at
-- the entry end.
--
-- WHY ON THE GOAL AND NOT ON THE TEAM SHEET. An assist is a property of a
-- goal: it says who passed for THAT one. A per-player tally on a team sheet
-- would count the same thing while losing which goal it belonged to, and would
-- need a new column to hold what an existing column already holds.
--
-- SHAPE. p_assist_player_id is optional and defaults to '', exactly as
-- p_player_id did in 0010, so a reporter who ignores the field lands on
-- today's behaviour. It is refused unless it resolves to a real players row,
-- refused if it is CAF_MW_UNKNOWN (the empty string already says "not
-- identified", unambiguously), and refused if it is the scorer — a player
-- cannot assist themselves, and that is far more likely to be a mis-tap than
-- a fact.
--
-- WHY EACH OLD SIGNATURE IS DROPPED FIRST. Adding a parameter changes the
-- signature, so `create or replace` alone would leave the old arity standing
-- beside the new one. PostgREST resolves an RPC by parameter NAME, and a
-- request carrying the old six keys would then match both functions and fail
-- as ambiguous — which would break every client at once rather than none. With
-- the old signature dropped there is exactly one candidate, and a browser
-- holding a cached app.js keeps working because its six named arguments still
-- satisfy the new function's defaults.

begin;

-- ── submit_match_goal ────────────────────────────────────────────────────────

drop function if exists
  public.submit_match_goal(text, text, text, text, text, text);

create or replace function public.submit_match_goal(
  p_match_id    text,
  p_team_id     text,
  p_player_name text,
  p_minute      text default '',
  p_goal_type   text default '',
  p_player_id   text default '',
  p_assist_player_id text default ''
)
returns setof public.goals
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter  text;
  v_match     public.matches;
  v_scored    integer;
  v_existing  integer;
  v_player_id text;
  v_assist_id text;
  v_goal      public.goals;
begin
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  -- Lock the match so two reporters adding the last scorer at once cannot
  -- both pass the count check below.
  select * into v_match from public.matches
  where match_id = p_match_id for update;
  if not found then
    raise exception 'match not found' using errcode = 'P0002';
  end if;

  if not public.can_report_match(p_match_id) then
    raise exception 'not assigned to this competition' using errcode = '42501';
  end if;

  -- validate.py check 5, first half: a goal must belong to a side that played.
  if p_team_id not in (v_match.home_team_id, v_match.away_team_id) then
    raise exception 'that team did not play in this match'
      using errcode = '22023';
  end if;

  -- ...and check 5 refuses goal rows on a match with no score at all.
  if v_match.home_goals is null or v_match.away_goals is null then
    raise exception 'publish the score before adding scorers'
      using errcode = '22023';
  end if;

  v_scored := case when p_team_id = v_match.home_team_id
                   then v_match.home_goals else v_match.away_goals end;

  select count(*) into v_existing
  from public.goals g
  where g.match_id = p_match_id and g.team_id = p_team_id;

  -- The check that protects every future build. Fewer scorers than goals is
  -- expected and fine; more is what validate.py rejects.
  if v_existing >= v_scored then
    raise exception
      'all % goal(s) for that team already have a scorer', v_scored
      using errcode = '22023';
  end if;

  if p_goal_type not in ('', 'open_play', 'penalty', 'free_kick', 'header',
                         'own_goal') then
    raise exception 'invalid goal type %', p_goal_type using errcode = '22023';
  end if;

  if btrim(p_player_name) = '' then
    raise exception 'a scorer needs a name' using errcode = '22023';
  end if;

  -- An identified scorer, or the 0007 fallback. CAF_MW_UNKNOWN is refused as
  -- an explicit argument rather than accepted: passing it would read as "this
  -- player was identified" and mean the opposite, and the empty string already
  -- says "not identified" unambiguously.
  v_player_id := btrim(coalesce(p_player_id, ''));
  if v_player_id = '' then
    v_player_id := 'CAF_MW_UNKNOWN';
  elsif v_player_id = 'CAF_MW_UNKNOWN' then
    raise exception 'leave the player blank rather than naming the unknown player'
      using errcode = '22023';
  elsif not exists (select 1 from public.players p
                    where p.player_id = v_player_id) then
    raise exception 'that player is not in the database' using errcode = '22023';
  end if;

  -- NEW IN 0019. Unlike the scorer there is no UNKNOWN fallback: an assist
  -- nobody can name is an assist not worth recording, so it stays NULL.
  v_assist_id := nullif(btrim(coalesce(p_assist_player_id, '')), '');
  if v_assist_id is not null then
    if v_assist_id = 'CAF_MW_UNKNOWN' then
      raise exception 'leave the assist blank rather than naming the unknown player'
        using errcode = '22023';
    end if;
    if v_assist_id = v_player_id then
      raise exception 'a player cannot assist their own goal'
        using errcode = '22023';
    end if;
    if not exists (select 1 from public.players p
                   where p.player_id = v_assist_id) then
      raise exception 'that assisting player is not in the database'
        using errcode = '22023';
    end if;
  end if;

  insert into public.goals (
    goal_id, match_id, team_id, player_id, reported_player_name,
    minute, goal_type, assist_player_id,
    source_type, reported_by, reported_at, confidence, ord
  ) values (
    public.next_goal_id(p_match_id), p_match_id, p_team_id,
    v_player_id,
    -- Kept even when the scorer IS identified: it is the provenance of the
    -- identification, and the only record of what was actually typed if the
    -- player_id turns out to have been the wrong pick.
    btrim(p_player_name),
    btrim(coalesce(p_minute, '')), p_goal_type, v_assist_id,
    'reporter', v_reporter, now(), 'unconfirmed',
    (select coalesce(max(ord), 0) + 1 from public.goals)
  )
  returning * into v_goal;

  return next v_goal;
end;
$$;

revoke execute on function
  public.submit_match_goal(text, text, text, text, text, text, text)
  from public, anon;
grant execute on function
  public.submit_match_goal(text, text, text, text, text, text, text)
  to authenticated;

-- ── submit_nt_goal ───────────────────────────────────────────────────────────
-- The national-team half. nt_goals.player_id is a canonical players id from
-- 0018/Phase 1 onward, so the assist can be checked against players here too —
-- which it could not have been while these ids were their own namespace.

drop function if exists
  public.submit_nt_goal(text, text, text, text, text, text, text, text);

create or replace function public.submit_nt_goal(
  p_match_id    text,
  p_team_id     text,
  p_player_name text,
  p_minute      text default '',
  p_stoppage    text default '',
  p_period      text default '',
  p_goal_type   text default '',
  p_player_id   text default '',
  p_assist_player_id text default ''
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
  v_assist_id text;
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

  v_assist_id := btrim(coalesce(p_assist_player_id, ''));
  if v_assist_id <> '' then
    if v_assist_id = btrim(coalesce(p_player_id, '')) then
      raise exception 'a player cannot assist their own goal'
        using errcode = '22023';
    end if;
    if not exists (select 1 from public.players p
                   where p.player_id = v_assist_id) then
      raise exception 'that assisting player is not in the database'
        using errcode = '22023';
    end if;
  end if;

  insert into public.nt_goals (
    goal_id, match_id, team_id, player_name, player_id,
    minute, stoppage, period, goal_type, assist_player_id
  ) values (
    public.next_nt_id('nt_goals'), p_match_id, p_team_id,
    btrim(p_player_name), btrim(coalesce(p_player_id, '')),
    btrim(coalesce(p_minute, '')), btrim(coalesce(p_stoppage, '')),
    btrim(coalesce(p_period, '')), p_goal_type, v_assist_id
  )
  returning * into v_goal;
  return next v_goal;
end;
$$;

revoke execute on function
  public.submit_nt_goal(text, text, text, text, text, text, text, text, text)
  from public, anon;
grant execute on function
  public.submit_nt_goal(text, text, text, text, text, text, text, text, text)
  to authenticated;

commit;
