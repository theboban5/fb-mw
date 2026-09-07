-- 0053_match_goals_batch.sql — a matchday's scorers in one submission.
--
-- WHAT WAS WRONG. 0041 made a matchday's RESULTS one screen and one button,
-- and left its scorers exactly where they were: one match at a time, on
-- #/m/<public_id>, reached by going back to the list and finding the next one.
-- So the graphic that gives eight results in one picture — which is the same
-- graphic that names the scorers under them — still costs eight screens the
-- moment anyone wants more than the score. The shape 0041 named ("eight
-- screens, eight round trips") was only half fixed, and the half left over is
-- the half that fills in the top-scorer tables.
--
-- It shows in the data. The Women's Premiership has 260 goals and no goal rows
-- at all, so its scorer coverage is 0%; #/ops?tab=compare renders that as a
-- Coverage bar rather than a top-scorer panel precisely because the column
-- would be empty. That is not a reporting failure, it is an entry cost.
--
-- WHAT THIS DOES. 0041's move, for goals.
--
--   apply_match_goal    every rule submit_match_goal enforced, moved out into
--                       one internal function so the single and batch paths
--                       cannot drift.
--   submit_match_goal   unchanged behaviour and unchanged signature; its body
--                       is now the authorization plus a call.
--   submit_match_goals  a list, in one call, row by row: a bad row is reported
--                       against its own line and the good rows around it are
--                       still saved.
--
-- THE DRIFT IS THE WHOLE POINT, exactly as it was in 0041. Two of the rules
-- inside submit_match_goal ARE validate.py check 5 — a goal on a match with no
-- score, and more scorers for a side than that side scored — and an ERROR from
-- check 5 fails the build and deploys nothing for anybody. The reserved-id
-- rule (CAF_MW_UNKNOWN is what a blank player_id becomes, and is refused as an
-- explicit argument because passing it reads as "identified" and means the
-- opposite) is what keeps an unidentified scorer off a player page that does
-- not exist. A second copy of that list maintained by hand is a bug with a
-- date on it, so there is one copy and two callers.
--
-- IT IS A FLAT LIST OF GOALS, NOT A LIST OF MATCHES EACH HOLDING GOALS. Each
-- row carries its own match_id. Nesting would have made a failure address two
-- indexes deep — "row 4, scorer 2" — for no gain, and every failure here is
-- about one scorer: this name is a third scorer for a side that scored twice,
-- that player_id is not in the database. The client groups them by match for
-- display because that is a drawing decision, not a protocol one.
--
-- PARTIAL SUCCESS IS THE POINT, as in 0014 and 0041. A reporter typing eight
-- matches' scorers on one bar of signal must not lose seven of them because
-- the eighth names a third scorer in a 2-1. Rule 1 of the portal, stated at
-- the database.
--
-- WHAT THIS DOES NOT DO. It opens no new door: every row goes through the same
-- insert submit_match_goal has always made, with the same reporter derived the
-- same way. It publishes nothing that could not have been typed one at a time.
-- It does not touch delete_match_goal (0007) — removing a scorer is still one
-- at a time and still only your own — and it does not touch the national-team
-- path (submit_nt_goal), which has its own score columns and its own
-- assignment check and no batch screen to call one.
--
-- ONE DELIBERATE BEHAVIOUR CHANGE, and it is a repair. 0019 tested the scorer
-- name with `btrim(p_player_name) = ''`, which is NULL rather than true when
-- the name is NULL — so the guard did not fire, and the insert then hit a NOT
-- NULL column and returned 23502. A reporter saw a raw Postgres error where
-- the function had a sentence ready for exactly that case. The same held for a
-- NULL goal_type. Both are coalesced now: a NULL name raises 'a scorer needs a
-- name' like a blank one, and a NULL goal_type is the blank type. Reachable
-- only by passing an explicit null (both arguments default to ''), which is
-- why it went unnoticed, and it is still worth closing — raw database errors
-- never reach a reporter is a rule of this portal, not an aspiration.
--
-- SCORERS STILL CANNOT GO INLINE IN THE GRID ROW. apply_match_goal refuses a
-- goal on a match with no score, and it is right to; so entering scorers is a
-- SECOND PASS over rows that have just been published, never a taller row. The
-- single-match screen has staged scorers before their score existed since
-- 0007's day (state.pendingGoals / flushPendingGoals), and this is the same
-- bargain for a whole matchday: publish the scores, then name the scorers.

begin;

-- ── apply_match_goal ─────────────────────────────────────────────────────────
-- The rules, once. The caller has already locked the match and established
-- that this reporter may report it: those two things differ between the single
-- and batch paths (one match versus one competition) and everything after them
-- does not.
--
-- p_match is passed as a record rather than an id so the lock cannot be
-- accidentally skipped — there is no way to call this without having selected
-- the row first, which is how apply_match_report is built for the same reason.

create or replace function public.apply_match_goal(
  p_match            public.matches,
  p_team_id          text,
  p_player_name      text,
  p_minute           text,
  p_goal_type        text,
  p_player_id        text,
  p_assist_player_id text,
  p_reporter         text
)
returns public.goals
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_scored    integer;
  v_existing  integer;
  v_player_id text;
  v_assist_id text;
  v_goal      public.goals;
begin
  -- validate.py check 5, first half: a goal must belong to a side that played.
  if p_team_id not in (p_match.home_team_id, p_match.away_team_id) then
    raise exception 'that team did not play in this match'
      using errcode = '22023';
  end if;

  -- ...and check 5 refuses goal rows on a match with no score at all.
  if p_match.home_goals is null or p_match.away_goals is null then
    raise exception 'publish the score before adding scorers'
      using errcode = '22023';
  end if;

  v_scored := case when p_team_id = p_match.home_team_id
                   then p_match.home_goals else p_match.away_goals end;

  select count(*) into v_existing
  from public.goals g
  where g.match_id = p_match.match_id and g.team_id = p_team_id;

  -- The check that protects every future build. Fewer scorers than goals is
  -- expected and fine; more is what validate.py rejects.
  --
  -- Counted fresh on every call, which is what makes the batch safe: a list
  -- naming three scorers for a side that scored twice saves the first two and
  -- reports the third against its own line, rather than being pre-counted
  -- somewhere and rejected whole.
  if v_existing >= v_scored then
    raise exception
      'all % goal(s) for that team already have a scorer', v_scored
      using errcode = '22023';
  end if;

  if p_goal_type not in ('', 'open_play', 'penalty', 'free_kick', 'header',
                         'own_goal') then
    raise exception 'invalid goal type %', p_goal_type using errcode = '22023';
  end if;

  if btrim(coalesce(p_player_name, '')) = '' then
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

  -- 0019's rule. Unlike the scorer there is no UNKNOWN fallback: an assist
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
    public.next_goal_id(p_match.match_id), p_match.match_id, p_team_id,
    v_player_id,
    -- Kept even when the scorer IS identified: it is the provenance of the
    -- identification, and the only record of what was actually typed if the
    -- player_id turns out to have been the wrong pick.
    btrim(p_player_name),
    btrim(coalesce(p_minute, '')), coalesce(p_goal_type, ''), v_assist_id,
    'reporter', p_reporter, now(), 'unconfirmed',
    (select coalesce(max(ord), 0) + 1 from public.goals)
  )
  returning * into v_goal;

  return v_goal;
end;
$$;

comment on function public.apply_match_goal(public.matches, text, text, text,
  text, text, text, text) is
  'Every rule a goal row must satisfy, with the match already locked and the '
  'caller already authorized. Internal: submit_match_goal and '
  'submit_match_goals are the two callers, and the point of it is that there '
  'is only one copy of validate.py check 5.';

revoke execute on function public.apply_match_goal(public.matches, text, text,
  text, text, text, text, text) from public, anon, authenticated;


-- ── submit_match_goal ────────────────────────────────────────────────────────
-- Unchanged signature, unchanged behaviour, unchanged return type. Every
-- browser in the field calls this and keeps working; its body is now the
-- authorization plus a call.
--
-- create or replace, NOT drop and recreate: the argument list is identical to
-- 0019's, so there is no second function to be ambiguous with. (0047 had to
-- drop submit_match_reports because it was ADDING a defaulted parameter, which
-- makes a second overload PostgREST's named call then matches both of.)

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
  v_reporter text;
  v_match    public.matches;
begin
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  -- Lock the match so two reporters adding the last scorer at once cannot
  -- both pass the count check inside apply_match_goal.
  select * into v_match from public.matches
  where match_id = p_match_id for update;
  if not found then
    raise exception 'match not found' using errcode = 'P0002';
  end if;

  if not public.can_report_match(p_match_id) then
    raise exception 'not assigned to this competition' using errcode = '42501';
  end if;

  return next public.apply_match_goal(
    v_match, p_team_id, p_player_name, p_minute, p_goal_type,
    p_player_id, p_assist_player_id, v_reporter);
end;
$$;

revoke execute on function
  public.submit_match_goal(text, text, text, text, text, text, text)
  from public, anon;
grant execute on function
  public.submit_match_goal(text, text, text, text, text, text, text)
  to authenticated;


-- ── submit_match_goals ───────────────────────────────────────────────────────
-- A whole matchday's scorers. Authorization once against the competition, then
-- EVERY ROW PINNED TO THAT COMPETITION AND SEASON — without the pin the single
-- check would be about the competition the client NAMED rather than the match
-- it sent, and a reporter assigned to one league could write a goal into
-- another by putting its match_id on a line. 0041's rule, and the reason it is
-- stated again here rather than assumed.

create or replace function public.submit_match_goals(
  p_competition_id text,
  p_goals          jsonb,
  p_season_id      text default null
)
returns table (
  idx      integer,
  ok       boolean,
  match_id text,
  goal_id  text,
  message  text
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_season   text;
  v_row      jsonb;
  v_i        integer := 0;
  v_match    public.matches;
  v_goal     public.goals;
  v_id       text;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  if p_goals is null or jsonb_typeof(p_goals) <> 'array' then
    raise exception 'send a list of scorers' using errcode = '22023';
  end if;
  if jsonb_array_length(p_goals) = 0 then
    raise exception 'add at least one scorer' using errcode = '22023';
  end if;
  -- 0041 caps a matchday at 60 RESULTS; a scorer list is the same matchday
  -- counted in goals, so the ceiling is the same order of magnitude times a
  -- plausible goals-per-match. No matchday in the country is near 200, and it
  -- stops a malformed or replayed call holding a connection open inserting for
  -- minutes.
  if jsonb_array_length(p_goals) > 200 then
    raise exception 'that is more than 200 scorers — send them in two goes'
      using errcode = '22023';
  end if;

  if not exists (select 1 from public.competitions c
                 where c.competition_id = p_competition_id) then
    raise exception 'unknown competition %', p_competition_id
      using errcode = 'P0002';
  end if;

  -- The active season unless told otherwise. seasons.status='active' is the
  -- single source of "now" for the whole build (DATA_MODEL.md), and reading
  -- the clock here instead would be a second, disagreeing answer.
  if p_season_id is null then
    select s.season_id into v_season
    from public.seasons s where s.status = 'active' limit 1;
    if v_season is null then
      raise exception 'no active season' using errcode = 'P0002';
    end if;
  else
    v_season := p_season_id;
    if not exists (select 1 from public.seasons s
                   where s.season_id = v_season) then
      raise exception 'unknown season %', v_season using errcode = 'P0002';
    end if;
  end if;

  if not public.can_report_competition(p_competition_id, v_season) then
    raise exception 'not assigned to this competition'
      using errcode = '42501';
  end if;

  for v_row in select * from jsonb_array_elements(p_goals) loop
    v_i := v_i + 1;
    begin
      v_id := nullif(trim(coalesce(v_row->>'match_id', '')), '');
      if v_id is null then
        raise exception 'that line has no match on it' using errcode = '22023';
      end if;

      -- Locked for the rest of the transaction. Several scorers for the same
      -- match re-lock a row this transaction already holds, which is free, and
      -- the lock is what makes the count check inside apply_match_goal safe
      -- against two reporters naming the last scorer at once.
      --
      -- Aliased because this function's OUT parameters are named match_id and
      -- goal_id: an unqualified column of either name is ambiguous to plpgsql
      -- and fails at run time. (0047 records the same trap.)
      select m.* into v_match
      from public.matches m
      where m.match_id = v_id
      for update;

      if not found then
        raise exception 'match not found' using errcode = 'P0002';
      end if;

      if v_match.competition_id is distinct from p_competition_id
         or v_match.season_id is distinct from v_season then
        raise exception 'that match is not in this competition this season'
          using errcode = '42501';
      end if;

      v_goal := public.apply_match_goal(
        v_match,
        trim(coalesce(v_row->>'team_id', '')),
        coalesce(v_row->>'player_name', ''),
        coalesce(v_row->>'minute', ''),
        trim(coalesce(v_row->>'goal_type', '')),
        coalesce(v_row->>'player_id', ''),
        coalesce(v_row->>'assist_player_id', ''),
        v_reporter);

      idx := v_i; ok := true;
      match_id := v_goal.match_id;
      goal_id := v_goal.goal_id;
      message := '';
      return next;

    exception
      -- Every raise in apply_match_goal is already a sentence written for a
      -- reporter to act on, so it is carried out as-is and the client shows it
      -- against the line it belongs to. Catching everything rather than the
      -- listed error codes is deliberate: an unforeseen failure on scorer four
      -- must still not discard scorers one to three.
      when others then
        idx := v_i; ok := false;
        match_id := v_id;
        goal_id := null;
        message := sqlerrm;
        return next;
    end;
  end loop;
end;
$$;

comment on function public.submit_match_goals(text, jsonb, text) is
  'Save a whole matchday''s scorers in one call. A flat list of goals, each '
  'carrying its own match_id. Returns a row per input row: a bad row is '
  'reported against its index and the good rows around it are still saved.';

revoke execute on function public.submit_match_goals(text, jsonb, text)
  from public, anon;
grant execute on function public.submit_match_goals(text, jsonb, text)
  to authenticated;

commit;
