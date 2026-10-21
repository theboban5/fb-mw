-- 0054_amend_match_goals.sql — adding what a goal row is missing, without
-- replacing the goal.
--
-- WHAT WAS WRONG. A goal could be inserted (submit_match_goal, 0007/0019/0053)
-- and deleted (delete_match_goal, 0007), and that was the whole of it. So the
-- one fact a scorer line most often lacks — who set it up — could only arrive
-- on the same call that created the goal. In the FDH Bank Premiership, where
-- scorer coverage is effectively complete, that meant 232 of 248 goals had no
-- assist and no way to be given one short of deleting a reporter's work and
-- typing it again.
--
-- It stopped being theoretical with match-report import: a club's report names
-- the assister of a goal that is ALREADY in the database, entered off the
-- league's graphic on the day. Re-inserting it is refused (the side's goals are
-- all accounted for, which is validate.py check 5 doing its job); deleting and
-- re-inserting would change the goal_id, lose the reported_by of whoever typed
-- it, and be a delete performed on someone else's row by an import.
--
-- WHAT THIS DOES.
--
--   goal_assist_id      the assist rules from 0019, moved out of
--                       apply_match_goal into one internal function so the
--                       insert and the amendment cannot drift. 0041's and
--                       0053's move again, for the same reason: a second copy
--                       kept by hand is a bug with a date on it.
--   apply_match_goal    unchanged signature and behaviour; it calls the above.
--   amend_match_goals   a list of {goal_id, assist_player_id, minute,
--                       goal_type} for ONE match. Each named field is written
--                       only where the goal's column is blank.
--
-- FILL BLANKS, NEVER OVERWRITE. An amendment is an import ENRICHING a goal, and
-- the person who entered the goal saw something too. Where the column already
-- holds the same value it is a no-op; where it holds a different one the row is
-- refused whole, with a sentence naming what is there, and nothing on that row
-- changes. A correction stays a deliberate act by hand — delete and re-enter on
-- #/m/ — which is the trade: a wrong assist typed by hand cannot be fixed here.
--
-- OWN GOAL IS NOT AN AMENDMENT. goals.team_id is the beneficiary, so on an
-- ordinary goal the scorer plays for team_id and on an own goal he plays for
-- the other side (DATA_MODEL.md). Turning a blank goal_type into 'own_goal'
-- would silently move the scorer to the other team on every page that reads
-- the row. That is a correction to what the goal IS, so it is refused here.
-- And an own goal is never GIVEN an assist: DATA_MODEL.md says they never
-- carry one, nothing at build time checks it, and this is the one new door
-- an assist could come through.
--
-- PARTIAL SUCCESS, as in 0014, 0041 and 0053: each row reports against its own
-- index and the good rows around a bad one are saved.

begin;

-- ── goal_assist_id ───────────────────────────────────────────────────────────
-- 0019's rule, verbatim from 0053's apply_match_goal. Returns the assist id to
-- store (NULL for "no assist") or raises a sentence a reporter can act on.
-- Unlike the scorer there is no UNKNOWN fallback: an assist nobody can name is
-- an assist not worth recording, so it stays NULL.

create or replace function public.goal_assist_id(
  p_assist_player_id text,
  p_scorer_id        text
)
returns text
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_assist_id text;
begin
  v_assist_id := nullif(btrim(coalesce(p_assist_player_id, '')), '');
  if v_assist_id is null then
    return null;
  end if;
  if v_assist_id = 'CAF_MW_UNKNOWN' then
    raise exception 'leave the assist blank rather than naming the unknown player'
      using errcode = '22023';
  end if;
  if v_assist_id = p_scorer_id then
    raise exception 'a player cannot assist their own goal'
      using errcode = '22023';
  end if;
  if not exists (select 1 from public.players p
                 where p.player_id = v_assist_id) then
    raise exception 'that assisting player is not in the database'
      using errcode = '22023';
  end if;
  return v_assist_id;
end;
$$;

comment on function public.goal_assist_id(text, text) is
  'The assist rules (0019), once: apply_match_goal and amend_match_goals both '
  'call it. Internal.';

revoke execute on function public.goal_assist_id(text, text)
  from public, anon, authenticated;


-- ── apply_match_goal ─────────────────────────────────────────────────────────
-- 0053's body, unchanged except that the assist block is now a call. Same
-- signature, so create or replace replaces it rather than adding an overload.

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
  -- expected and fine; more is what validate.py rejects. Counted fresh on
  -- every call, which is what makes the batch safe.
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
  -- player was identified" and mean the opposite.
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

  v_assist_id := public.goal_assist_id(p_assist_player_id, v_player_id);

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

revoke execute on function public.apply_match_goal(public.matches, text, text,
  text, text, text, text, text) from public, anon, authenticated;


-- ── amend_match_goals ────────────────────────────────────────────────────────
-- One match, because an amendment is always "the goals of the match I am
-- looking at", and so the authorization is can_report_match — the same check
-- submit_match_goal makes — with every row pinned to that match.

create or replace function public.amend_match_goals(
  p_match_id   text,
  p_amendments jsonb
)
returns table (
  idx     integer,
  ok      boolean,
  goal_id text,
  message text
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_match    public.matches;
  v_row      jsonb;
  v_i        integer := 0;
  v_goal     public.goals;
  v_id       text;
  v_assist   text;
  v_minute   text;
  v_type     text;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  if p_amendments is null or jsonb_typeof(p_amendments) <> 'array' then
    raise exception 'send a list of goals' using errcode = '22023';
  end if;
  -- A match does not have fifty goals. The cap is against a malformed or
  -- replayed call, not against football.
  if jsonb_array_length(p_amendments) > 50 then
    raise exception 'that is more than 50 goals for one match'
      using errcode = '22023';
  end if;

  -- Aliased: this function's OUT parameter is named goal_id, and an
  -- unqualified column of that name is ambiguous to plpgsql at run time.
  select m.* into v_match from public.matches m
  where m.match_id = p_match_id for update;
  if not found then
    raise exception 'match not found' using errcode = 'P0002';
  end if;
  if not public.can_report_match(p_match_id) then
    raise exception 'not assigned to this competition' using errcode = '42501';
  end if;

  for v_row in select * from jsonb_array_elements(p_amendments) loop
    v_i := v_i + 1;
    begin
      v_id := nullif(btrim(coalesce(v_row->>'goal_id', '')), '');
      select g.* into v_goal from public.goals g
      where g.goal_id = v_id and g.match_id = p_match_id
      for update;
      if not found then
        raise exception 'that goal is not in this match' using errcode = 'P0002';
      end if;

      v_minute := btrim(coalesce(v_row->>'minute', ''));
      v_type := btrim(coalesce(v_row->>'goal_type', ''));
      if v_type not in ('', 'open_play', 'penalty', 'free_kick', 'header') then
        raise exception
          'an existing goal cannot be made an own goal here — delete it and enter it again'
          using errcode = '22023';
      end if;
      v_assist := public.goal_assist_id(v_row->>'assist_player_id',
                                        v_goal.player_id);
      -- DATA_MODEL.md: own goals never carry an assist. Nothing validates it
      -- at build time, so this, the one new way an assist reaches a saved
      -- goal, is where it is held.
      if v_assist is not null and v_goal.goal_type = 'own_goal' then
        raise exception 'an own goal has no assist' using errcode = '22023';
      end if;

      -- Fill blanks, never overwrite. Equal is a no-op; different refuses the
      -- whole row, naming what is there, before anything on it is written.
      if v_assist is not null and v_goal.assist_player_id is not null
         and v_goal.assist_player_id <> v_assist then
        raise exception 'that goal already has an assist (%)',
          coalesce((select p.full_name from public.players p
                    where p.player_id = v_goal.assist_player_id),
                   v_goal.assist_player_id)
          using errcode = '22023';
      end if;
      if v_minute <> '' and coalesce(v_goal.minute, '') <> ''
         and v_goal.minute <> v_minute then
        raise exception 'that goal already has a minute (%'')', v_goal.minute
          using errcode = '22023';
      end if;
      if v_type <> '' and coalesce(v_goal.goal_type, '') <> ''
         and v_goal.goal_type <> v_type then
        raise exception 'that goal is already recorded as %',
          replace(v_goal.goal_type, '_', ' ')
          using errcode = '22023';
      end if;

      update public.goals g set
        assist_player_id = coalesce(g.assist_player_id, v_assist),
        minute    = case when coalesce(g.minute, '') = '' then v_minute
                         else g.minute end,
        goal_type = case when coalesce(g.goal_type, '') = '' then v_type
                         else g.goal_type end
      where g.goal_id = v_goal.goal_id;

      idx := v_i; ok := true; goal_id := v_goal.goal_id; message := '';
      return next;
    exception
      -- Every raise above is a sentence for a reporter, carried out as-is
      -- against its own line; an unforeseen failure on one goal must not
      -- discard the others. 0053's handler, for the same reason.
      when others then
        idx := v_i; ok := false; goal_id := v_id; message := sqlerrm;
        return next;
    end;
  end loop;
end;
$$;

comment on function public.amend_match_goals(text, jsonb) is
  'Fill in what existing goal rows of one match are missing — assist, minute, '
  'goal type — never overwriting a value already there, and never making a '
  'goal an own goal. One answer per row.';

-- Explicitly, every time: this project's default privileges grant new
-- functions to anon (0048 is the migration that forgot).
revoke execute on function public.amend_match_goals(text, jsonb)
  from public, anon;
grant execute on function public.amend_match_goals(text, jsonb)
  to authenticated;

commit;
