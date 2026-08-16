-- 0009_reschedule.sql — moving a fixture to another day, and the season-range
-- rule that 0008 should have enforced.
--
-- Two things, because they are the same rule seen twice.
--
-- 1. RESCHEDULE. A fixture list is published weeks ahead and then reality
--    happens: a match is moved. Until now the only way to correct that was the
--    CLI, because submit_match_report deliberately refuses to touch `date` —
--    the narrow-update guarantee in 0003 exists precisely so that permission
--    to report a score is not permission to move a fixture into another
--    season. That guarantee is kept here: rescheduling gets its OWN function
--    that writes date and kickoff and nothing else, rather than widening the
--    reporting RPC. Two narrow doors, not one wide one.
--
-- 2. VALIDATE.PY CHECK 6, which 0008 missed. `check_dates` requires every
--    match date to fall inside its season's start..end range, and a date
--    outside it fails the build — which means NO deploy at all, including
--    everyone else's results. create_fixture never checked this, so a
--    mistyped year (2031 instead of 2027 — one keystroke) would have been
--    accepted by the database and then taken the site down at the next build.
--    Both functions enforce it now.

begin;

-- ── The season-range rule, in one place ──────────────────────────────────────
-- Shared by create_fixture and reschedule_match so the two cannot drift. It
-- raises rather than returns a boolean: the message names the actual bounds,
-- which is the difference between "that date is wrong" and a reporter knowing
-- what to type instead.

create or replace function public.assert_date_in_season(
  p_season_id text,
  p_date      date
)
returns void
language plpgsql
stable
set search_path = ''
as $$
declare
  v_start date;
  v_end   date;
  v_label text;
begin
  -- A fixture with no date is legitimate and always has been: a fixture list
  -- often exists before a calendar does.
  if p_date is null then
    return;
  end if;

  select s.start_date, s.end_date, s.label
  into v_start, v_end, v_label
  from public.seasons s
  where s.season_id = p_season_id;

  if not found then
    raise exception 'unknown season %', p_season_id using errcode = 'P0002';
  end if;

  if p_date < v_start or p_date > v_end then
    raise exception
      'that date is outside the % season (% to %)',
      v_label, to_char(v_start, 'DD Mon YYYY'), to_char(v_end, 'DD Mon YYYY')
      using errcode = '22023';
  end if;
end;
$$;

comment on function public.assert_date_in_season(text, date) is
  'validate.py check 6, enforced at write time. A date outside the season '
  'fails the build, and a failed build deploys nothing at all.';


-- ── create_fixture: same as 0008 plus the season-range check ─────────────────

create or replace function public.create_fixture(
  p_competition_id text,
  p_home_team_id   text,
  p_away_team_id   text,
  p_season_id      text default null,
  p_date           date default null,
  p_kickoff        text default '',
  p_venue_id       text default null,
  p_matchday       integer default null,
  p_stage          text default ''
)
returns setof public.matches
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_season   text;
  v_type     text;
  v_stage    text;
  v_match    public.matches;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;

  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  -- 1. The competition must exist, and gives us the type that decides which
  --    stage vocabulary applies.
  select c.type into v_type
  from public.competitions c
  where c.competition_id = p_competition_id;
  if not found then
    raise exception 'unknown competition %', p_competition_id
      using errcode = 'P0002';
  end if;

  -- 2. Season: the active one unless told otherwise. seasons.status='active'
  --    is the single source of "now" for the whole build (DATA_MODEL.md), and
  --    reading the clock here instead would be a second, disagreeing answer.
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

  -- 3. Authorized for this competition.
  if not public.can_report_competition(p_competition_id, v_season) then
    raise exception 'not assigned to this competition'
      using errcode = '42501';
  end if;

  -- 4. Two different teams (validate.py check 4).
  if p_home_team_id is null or p_away_team_id is null
     or p_home_team_id = '' or p_away_team_id = '' then
    raise exception 'both teams are required' using errcode = '22023';
  end if;
  if p_home_team_id = p_away_team_id then
    raise exception 'a team cannot play itself' using errcode = '22023';
  end if;

  -- 5. Kickoff matches the column's own constraint, but said in a sentence a
  --    reporter can act on rather than as a check-violation code.
  if p_kickoff is not null and p_kickoff <> ''
     and p_kickoff !~ '^[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?$' then
    raise exception 'kickoff must look like 15:00 (Malawi time)'
      using errcode = '22023';
  end if;

  -- 5b. VALIDATE.PY CHECK 6. A mistyped year is one keystroke away and would
  --     otherwise fail every build until someone found it.
  perform public.assert_date_in_season(v_season, p_date);

  -- 6. VALIDATE.PY CHECK 3, enforced at the source. A match whose teams are
  --    not entered in the competition+season fails the build, and a failed
  --    build means nothing deploys at all — including everyone else's results.
  if not exists (select 1 from public.entries e
                 where e.competition_id = p_competition_id
                   and e.season_id = v_season
                   and e.team_id = p_home_team_id) then
    raise exception 'home team is not entered in this competition this season'
      using errcode = '23503';
  end if;
  if not exists (select 1 from public.entries e
                 where e.competition_id = p_competition_id
                   and e.season_id = v_season
                   and e.team_id = p_away_team_id) then
    raise exception 'away team is not entered in this competition this season'
      using errcode = '23503';
  end if;

  if p_venue_id is not null and p_venue_id <> ''
     and not exists (select 1 from public.venues v
                     where v.venue_id = p_venue_id) then
    raise exception 'unknown venue %', p_venue_id using errcode = 'P0002';
  end if;

  -- 7. Stage (validate.py check 7). On a cup it must come from the knockout
  --    vocabulary; on a league it is the free-form md_<n> the matchday implies.
  v_stage := lower(trim(coalesce(p_stage, '')));
  if v_type = 'cup' then
    if v_stage = '' then
      raise exception 'a cup fixture needs a round' using errcode = '22023';
    end if;
    if v_stage not in ('r64', 'r32', 'r16', 'qf', 'sf', 'final', '3p') then
      raise exception 'invalid cup round %', v_stage using errcode = '22023';
    end if;
  elsif v_stage = '' and p_matchday is not null then
    v_stage := 'md_' || p_matchday::text;
  end if;

  -- 8. Duplicate guard. Not a constraint on the table — the same two teams
  --    legitimately meet twice in a season, and in a cup replay more than
  --    that. What is almost always a mistake is the SAME fixture entered
  --    twice on the same day, which is what a double-tap on a phone produces.
  if p_date is not null and exists (
       select 1 from public.matches m
       where m.competition_id = p_competition_id
         and m.season_id = v_season
         and m.home_team_id = p_home_team_id
         and m.away_team_id = p_away_team_id
         and m.date = p_date) then
    raise exception 'that fixture is already in the list for %', p_date
      using errcode = '23505';
  end if;

  insert into public.matches (
    match_id, competition_id, season_id, stage, matchday, date, kickoff,
    venue_id, home_team_id, away_team_id, status, source_type, confidence)
  values (
    public.next_match_id(p_competition_id, v_season),
    p_competition_id, v_season, v_stage, p_matchday, p_date,
    coalesce(p_kickoff, ''), nullif(p_venue_id, ''),
    p_home_team_id, p_away_team_id,
    -- No score, by construction: a fixture is a fixture until someone reports
    -- it. source_type records who put the fixture in; the result that lands on
    -- it later overwrites this with its own provenance.
    'scheduled', 'reporter', 'unconfirmed')
  returning * into v_match;

  return next v_match;
end;
$$;


-- ── reschedule_match ─────────────────────────────────────────────────────────
-- Writes `date` and `kickoff`. Nothing else. Teams, competition, season,
-- venue, score and status are absent from the UPDATE by design, exactly as
-- they are in submit_match_report — the guarantee that a reporter cannot
-- restructure a fixture is what makes both of these safe to expose.
--
-- It is deliberately NOT part of publishing. A reporter correcting a score
-- must not be able to move the match by accident, so this is its own call
-- behind its own button.

create or replace function public.reschedule_match(
  p_match_id text,
  p_date     date default null,
  p_kickoff  text default ''
)
returns setof public.matches
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_match    public.matches;
  v_kickoff  text;
  v_old      jsonb;
  v_new      jsonb;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;

  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  -- Locked for the rest of the transaction, so a reschedule and a result
  -- landing together serialize instead of racing.
  select * into v_match
  from public.matches
  where match_id = p_match_id
  for update;

  if not found then
    raise exception 'match not found' using errcode = 'P0002';
  end if;

  if not public.can_report_match(p_match_id) then
    raise exception 'not assigned to this competition' using errcode = '42501';
  end if;

  v_kickoff := trim(coalesce(p_kickoff, ''));
  if v_kickoff <> '' and v_kickoff !~ '^[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?$' then
    raise exception 'kickoff must look like 15:00 (Malawi time)'
      using errcode = '22023';
  end if;

  -- The season is the match's own — this cannot move a fixture between
  -- seasons, so the range checked is always the right one.
  perform public.assert_date_in_season(v_match.season_id, p_date);

  v_old := jsonb_build_object('date', v_match.date, 'kickoff', v_match.kickoff);
  v_new := jsonb_build_object('date', p_date, 'kickoff', v_kickoff);

  if v_old is not distinct from v_new then
    -- Nothing changed. Return the row rather than raising: the reporter asked
    -- for a state that is already true, which is not an error.
    return next v_match;
    return;
  end if;

  update public.matches
  set date       = p_date,
      kickoff    = v_kickoff,
      updated_at = now()
  where match_id = p_match_id
  returning * into v_match;

  -- Moving a fixture is exactly the kind of change someone will later want to
  -- account for, so it lands in the same append-only log as a score change.
  insert into public.match_change_log
    (match_id, changed_by, old_values, new_values)
  values (p_match_id, v_reporter, v_old, v_new);

  return next v_match;
end;
$$;

comment on function public.reschedule_match(text, date, text) is
  'Move a fixture. Writes date and kickoff only, and appends to '
  'match_change_log. Separate from submit_match_report on purpose.';

revoke execute on function public.reschedule_match(text, date, text)
  from public, anon;
grant execute on function public.reschedule_match(text, date, text)
  to authenticated;

commit;
