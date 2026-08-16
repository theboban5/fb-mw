-- 0010_scorer_players.sql — reporter-entered scorers become real players.
--
-- WHAT WAS WRONG. 0007 wrote every reporter-entered scorer against the
-- reserved CAF_MW_UNKNOWN row, with the typed name in reported_player_name.
-- That is a sound archival design and it is also, from the reporter's side,
-- indistinguishable from the feature not working: src/adapt.py drops every
-- CAF_MW_UNKNOWN goal before rendering, so a scorer typed into /report was
-- saved correctly and then never appeared anywhere on everyleague.co. The
-- reporter's only evidence was silence.
--
-- WHAT CHANGES. The portal now resolves a typed name to a player_id before
-- submitting: it searches players as you type, and offers to create one when
-- the name is genuinely new. A goal with a real player_id renders on the match
-- line, ranks in the scorer table and gets a player page, with no further
-- change to the build.
--
-- THE TRADE, STATED PLAINLY. players was documented as canonical identities
-- only — "never create a player row per free-text name" — precisely because a
-- typo becomes a permanent person. That rule is deliberately relaxed here, so
-- the design pushes back in three places instead:
--
--   1. Creating a player is its OWN rpc, never a side effect of adding a goal.
--      A reporter in a hurry cannot mint a person by accident; they have to
--      tap a button that says the name is new.
--   2. create_player is idempotent on the name. Two reporters typing
--      "Thandiwe Phiri" get the same player_id, not two, and a reporter who
--      taps twice does not create a twin.
--   3. p_player_id stays OPTIONAL on submit_match_goal. An old client, or a
--      reporter who dismisses the picker, still lands on CAF_MW_UNKNOWN with
--      the name preserved — the 0007 behaviour, unchanged and still valid.
--
-- Merging duplicates that get through remains a manual job: set the surviving
-- player_id on the goals and delete the loser. Nothing here forecloses that.

begin;

-- ── next_player_id ───────────────────────────────────────────────────────────
-- The historic ids are CAF_MW_000001..CAF_MW_000471 plus the reserved
-- CAF_MW_UNKNOWN. A new one continues that sequence: highest six-digit id plus
-- one, zero-padded. The regex is anchored on both ends so CAF_MW_UNKNOWN — and
-- anything else that is not exactly six digits — cannot be cast to an integer
-- and cannot be counted as the maximum.

create or replace function public.next_player_id()
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select 'CAF_MW_' || lpad((
    coalesce(
      (select max(substring(p.player_id from '^CAF_MW_([0-9]{6})$')::integer)
       from public.players p
       where p.player_id ~ '^CAF_MW_[0-9]{6}$'),
      0) + 1)::text, 6, '0');
$$;

revoke execute on function public.next_player_id() from public, anon, authenticated;


-- ── create_player ────────────────────────────────────────────────────────────
-- Deliberately narrow: a name, and nothing else. Position, date of birth and
-- nationality are things a reporter at the side of a pitch does not know and
-- should not be asked to guess — they are added later by whoever reconciles
-- the record. Every column this does not set keeps its table default.

create or replace function public.create_player(p_full_name text)
returns setof public.players
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_name     text;
  v_player   public.players;
begin
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  -- Collapse runs of whitespace as well as trimming: "Thandiwe  Phiri" and
  -- "Thandiwe Phiri" are one person, and the difference is invisible on screen.
  v_name := btrim(regexp_replace(coalesce(p_full_name, ''), '\s+', ' ', 'g'));

  if length(v_name) < 2 then
    raise exception 'a player needs a name' using errcode = '22023';
  end if;
  if length(v_name) > 80 then
    raise exception 'that name is too long' using errcode = '22023';
  end if;

  -- Idempotent on the name, case-insensitively, and matching known_as as well
  -- as full_name: the picker shows a player under whichever of the two reads
  -- as their name, so typing what is on screen must find them rather than
  -- clone them. Lowest ord wins, so the answer is stable if duplicates already
  -- exist from before this migration.
  select * into v_player
  from public.players p
  where lower(p.full_name) = lower(v_name)
     or (p.known_as <> '' and lower(p.known_as) = lower(v_name))
  order by p.ord
  limit 1;
  if found then
    return next v_player;
    return;
  end if;

  insert into public.players (player_id, full_name, status)
  values (public.next_player_id(), v_name, 'active')
  returning * into v_player;

  return next v_player;
end;
$$;

revoke execute on function public.create_player(text) from public, anon;
grant execute on function public.create_player(text) to authenticated;

-- 0001 told the next reader "never create a player row per free-text name".
-- That is no longer true and a stale comment on a load-bearing table is worse
-- than no comment, so it is replaced with the rule that now applies.
comment on table public.players is
  'Canonical identities. Reporters add one through create_player() when the '
  'scorer they name is not already here — a deliberate tap, never a side '
  'effect of submit_match_goal(). A goal whose scorer was not identified '
  'still points at CAF_MW_UNKNOWN with the typed name in '
  'goals.reported_player_name.';


-- ── submit_match_goal, now with an optional player_id ────────────────────────
-- Replaced rather than overloaded. PostgREST resolves an rpc call by the names
-- of the arguments in the body, and two functions differing only by a
-- defaulted trailing parameter are ambiguous for a five-key call — so the old
-- signature is dropped first. Every other rule from 0007 is carried over
-- unchanged, comments included, because they are what stops a reporter
-- breaking a build.

drop function if exists public.submit_match_goal(text, text, text, text, text);

create or replace function public.submit_match_goal(
  p_match_id    text,
  p_team_id     text,
  p_player_name text,
  p_minute      text default '',
  p_goal_type   text default '',
  p_player_id   text default ''
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

  insert into public.goals (
    goal_id, match_id, team_id, player_id, reported_player_name,
    minute, goal_type, source_type, reported_by, reported_at, confidence, ord
  ) values (
    public.next_goal_id(p_match_id), p_match_id, p_team_id,
    v_player_id,
    -- Kept even when the scorer IS identified: it is the provenance of the
    -- identification, and the only record of what was actually typed if the
    -- player_id turns out to have been the wrong pick.
    btrim(p_player_name),
    btrim(coalesce(p_minute, '')), p_goal_type,
    'reporter', v_reporter, now(), 'unconfirmed',
    (select coalesce(max(ord), 0) + 1 from public.goals)
  )
  returning * into v_goal;

  return next v_goal;
end;
$$;

revoke execute on function public.submit_match_goal(text, text, text, text, text, text)
  from public, anon;
grant execute on function public.submit_match_goal(text, text, text, text, text, text)
  to authenticated;

commit;
