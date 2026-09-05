-- 0041_match_report_batch.sql — a whole matchday's results in one submission.
--
-- WHAT WAS WRONG. A matchday is not a match. It arrives the way a fixture list
-- arrives — one graphic, eight results, read off a phone at a bus stage — and
-- the portal took them one at a time: open the match, tap the steppers, type
-- the source, publish, go back, find the next one. Eight screens, eight round
-- trips, and the source re-typed eight times because it is the SAME source for
-- all eight. On one bar of signal each of those trips can fail on its own, and
-- the reporter has no way of seeing how far they got except by re-reading the
-- list they just walked.
--
-- That is the shape 0014 already fixed for fixtures. This is the same fix for
-- results, and it is deliberately built the same way.
--
-- WHAT THIS DOES.
--
--   apply_match_report    every rule submit_match_report enforced, moved out
--                         into one internal function so the single and batch
--                         paths cannot drift — exactly why insert_fixture
--                         exists beside create_fixture.
--   submit_match_report   unchanged behaviour and unchanged signature; its
--                         body is now the authorization plus a call.
--   submit_match_reports  a list, in one call, row by row: a bad row is
--                         reported against its own line and the good rows
--                         around it are still published.
--
-- THE DRIFT IS THE WHOLE POINT OF THE REFACTOR. Every rule in the reporting
-- path is load-bearing somewhere else: the score/status agreement is
-- validate.py check 4, and an ERROR there fails the build and deploys nothing
-- for everyone. The admin-only gate on 'awarded' is a policy about who may
-- record a walkover. 0029's confidence rule decides whether the site prints an
-- asterisk beside the score. match_change_log is the only record that a
-- correction happened at all. A second copy of that list, maintained by hand
-- alongside the first, is a bug with a date on it — so there is one copy and
-- two callers.
--
-- PARTIAL SUCCESS IS THE POINT, as it was in 0014. All-or-nothing would mean
-- one already-published match — the commonest collision, because two reporters
-- at the same ground is a good problem to have — throws away seven correct
-- results and the typing that produced them. Rule 1 of the portal ("entered
-- data is never destroyed by a failure"), stated at the database.
--
-- WHAT THIS DOES NOT DO. It opens no new door. Every row still goes through
-- the same narrow update submit_match_report has always made: four reporting
-- columns and the provenance beside them. home_team_id, competition_id, date,
-- kickoff and venue_id are absent from the statement here for the same reason
-- they are absent there — being authorized to report a score has never been
-- permission to edit the fixture, and a batch is not a reason to change that.

begin;

-- ── One result, all the rules, no authorization ──────────────────────────────
-- The body of submit_match_report from 0029, verbatim in substance, lifted out
-- so that the single-result and whole-matchday paths run the SAME checks.
--
-- It takes the match ROW rather than the id: both callers have already
-- SELECT ... FOR UPDATE'd it, and re-reading it here would either drop that
-- lock's meaning or take a second one. p_reporter and p_is_admin come in for
-- the same reason — the caller has established who this is, and asking again
-- per row is the same question answered n times.
--
-- Authorization is NOT here. It stays in the two callers, which is where the
-- caller is known. This function trusts that the right to write to this match
-- has already been established, which is exactly why it is granted to nobody.

create or replace function public.apply_match_report(
  p_match      public.matches,
  p_home_score integer,
  p_away_score integer,
  p_status     text,
  p_source_ref text,
  p_reporter   text,
  p_is_admin   boolean
)
returns public.matches
language plpgsql
set search_path = ''
as $$
declare
  v_match  public.matches;
  v_old    jsonb;
  v_new    jsonb;
  v_scored boolean;
  v_source text;
begin
  -- Validate. These mirror validate.py check 4 and the table's own
  -- constraints; catching them here produces a message a reporter can act on
  -- rather than a constraint-violation code.
  if p_status not in ('scheduled', 'played', 'postponed', 'abandoned',
                      'cancelled', 'awarded') then
    raise exception 'invalid status %', p_status
      using errcode = '22023';
  end if;

  -- 'awarded' is an administrative decision — a walkover, a forfeit — not
  -- something observed at a ground. It counts into standings with its recorded
  -- score, so it stays out of the reporter path.
  if p_status = 'awarded' and not p_is_admin then
    raise exception 'only an administrator can record an awarded result'
      using errcode = '42501';
  end if;

  v_scored := p_status in ('played', 'awarded');

  if v_scored then
    if p_home_score is null or p_away_score is null then
      raise exception 'invalid score: a % result needs both scores', p_status
        using errcode = '22023';
    end if;
    if p_home_score < 0 or p_away_score < 0
       or p_home_score > 99 or p_away_score > 99 then
      raise exception 'invalid score: goals must be between 0 and 99'
        using errcode = '22023';
    end if;
  else
    -- A fixture that was not played carries no score. Rejecting rather than
    -- silently discarding: if the client sent one, the two disagree about what
    -- is being published and the reporter should see that.
    if p_home_score is not null or p_away_score is not null then
      raise exception 'invalid score: a % match cannot carry a score', p_status
        using errcode = '22023';
    end if;
  end if;

  -- Free text, length-capped so a paste accident cannot put a megabyte in the
  -- row. Blank leaves whatever was already recorded: a correction submitted
  -- without re-typing the link must not erase the link. This is also what
  -- makes the grid's one shared source safe — a row that already has its own
  -- source and is published from a screen whose shared box is empty keeps the
  -- source it had.
  v_source := left(trim(coalesce(p_source_ref, '')), 500);
  if v_source = '' then
    v_source := p_match.source_ref;
  end if;

  v_old := jsonb_build_object(
    'home_goals', p_match.home_goals,
    'away_goals', p_match.away_goals,
    'status',     p_match.status,
    'source_ref', p_match.source_ref);
  v_new := jsonb_build_object(
    'home_goals', p_home_score,
    'away_goals', p_away_score,
    'status',     p_status,
    'source_ref', v_source);

  -- Write ONLY the reporting fields. home_team_id, away_team_id,
  -- competition_id, season_id, date, kickoff, venue_id and every other
  -- structural column are absent from this statement by design.
  update public.matches
  set home_goals  = p_home_score,
      away_goals  = p_away_score,
      status      = p_status,
      source_type = 'reporter',
      source_ref  = v_source,
      reported_by = p_reporter,
      reported_at = now(),
      -- 0029: confirmed for anyone allowed to submit, not only an admin. The
      -- reporter vouches for their own result, so they are the verifier.
      confidence  = 'confirmed',
      verified_by = p_reporter,
      verified_at = now(),
      updated_at  = now()
  where match_id = p_match.match_id
  returning * into v_match;

  -- Record it — but only when something actually changed. Re-tapping publish
  -- on an unchanged result is a no-op worth attributing (the update above
  -- still refreshes reported_by/reported_at) and not worth a log row. This is
  -- what makes the grid's "publish everything again" safe: a matchday
  -- resubmitted after a dropped connection adds log rows only for the results
  -- that actually landed differently.
  if v_old is distinct from v_new then
    insert into public.match_change_log
      (match_id, changed_by, old_values, new_values)
    values (p_match.match_id, p_reporter, v_old, v_new);
  end if;

  return v_match;
end;
$$;

comment on function public.apply_match_report(public.matches, integer, integer,
  text, text, text, boolean) is
  'Internal. The reporting rules, once, for both submit_match_report and '
  'submit_match_reports. Does no authorization — its callers do.';

-- Internal. Reached only through the two functions below, which run as the
-- owner and have already established who the caller is and what they may
-- write to. Nothing calls it over the Data API.
revoke execute on function public.apply_match_report(public.matches, integer,
  integer, text, text, text, boolean) from public, anon, authenticated;


-- ── submit_match_report, unchanged, now one caller of two ────────────────────
-- Same signature, same behaviour, same guarantees. The body is reduced to what
-- only it can do: establish the caller, find and lock the match, check the
-- assignment. Everything after that is apply_match_report.
--
-- Not dropped and recreated — the signature is identical, so `create or
-- replace` is enough and every browser still running the current app.js keeps
-- working through the deploy. (0008's warning about ambiguous overloads
-- applies to CHANGING the argument list, which this does not.)

create or replace function public.submit_match_report(
  p_match_id   text,
  p_home_score integer,
  p_away_score integer,
  p_status     text,
  p_source_ref text default ''
)
returns setof public.matches
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_match    public.matches;
begin
  -- 1. Authenticated.
  if (select auth.uid()) is null then
    raise exception 'not authenticated'
      using errcode = '28000';
  end if;

  -- 2. A live reporter identity. current_reporter_id() already requires
  --    reporters.active, so a deactivated account fails here even though its
  --    login still works.
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  -- 3. The match must exist. Locked for the rest of the transaction so two
  --    reporters publishing the same match serialize instead of racing, and
  --    the audit rows come out in the order the changes actually happened.
  select * into v_match
  from public.matches
  where match_id = p_match_id
  for update;

  if not found then
    raise exception 'match not found'
      using errcode = 'P0002';
  end if;

  -- 4. Authorized for THIS match. The same question the UI asks, asked again
  --    where it counts — the client is not trusted.
  if not public.can_report_match(p_match_id) then
    raise exception 'not assigned to this competition'
      using errcode = '42501';
  end if;

  return next public.apply_match_report(
    v_match, p_home_score, p_away_score, p_status, p_source_ref,
    v_reporter, public.is_admin());
end;
$$;

comment on function public.submit_match_report(text, integer, integer, text, text) is
  'The only single-match reporter write path. Updates score/status/source_ref '
  'only, sets provenance, and appends to match_change_log.';


-- ── submit_match_reports: a whole matchday, one submission ───────────────────
-- p_reports is a JSON array, one object per line of the grid:
--
--   [{"match_id": "MW_SL_2627_045", "home": 2, "away": 1, "status": "played"},
--    {"match_id": "MW_SL_2627_046", "status": "postponed"},
--    {"match_id": "MW_SL_2627_047", "home": 0, "away": 0, "status": "played",
--     "expect": {"home": null, "away": null, "status": "scheduled"}}]
--
-- home/away are absent or null on a status that carries no score, which is the
-- same contract submit_match_report has always had.
--
-- RETURNS ONE ROW PER INPUT ROW, in order, whether it worked or not. The
-- client puts `message` back on the line that failed and leaves the reporter
-- looking at exactly the results still to fix — which is only possible because
-- `idx` says WHICH line, and because the rows around it were kept.
--
-- `expect` IS THE CONFLICT GUARD, AND IT IS OPTIONAL.
-- The grid loads a matchday, the reporter fills it in on a slow phone, and in
-- between someone else may publish one of the same matches. Without `expect`
-- the last write silently wins, which is the one outcome nobody can detect
-- afterwards. With it, a row says what it believed was saved when it was
-- drawn; if the database has moved since, THAT ROW alone is refused with a
-- sentence naming the result that is actually there, and the other seven still
-- publish. The reporter then sees the real value and decides.
--
-- It is optional because it is a guard, not a protocol: a row deliberately
-- correcting a published result sends no `expect` and overwrites, which is
-- exactly what submit_match_report has always done and what a correction IS.
-- The client is what decides to ask — see the grid's per-row "Replace 2–1"
-- confirmation, which is the gesture that drops `expect` from the row.
--
-- The authorization work is done ONCE, before the loop, rather than per row:
-- every row goes into the one competition and season the reporter chose, and
-- each row is pinned to that pair below, which makes the single check exactly
-- equivalent to can_report_match() asked n times over a phone connection.

create or replace function public.submit_match_reports(
  p_competition_id text,
  p_reports        jsonb,
  p_source_ref     text default '',
  p_season_id      text default null
)
returns table (
  idx        integer,
  ok         boolean,
  match_id   text,
  home_goals integer,
  away_goals integer,
  status     text,
  message    text
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_is_admin boolean;
  v_season   text;
  v_row      jsonb;
  v_i        integer := 0;
  v_match    public.matches;
  v_result   public.matches;
  v_id       text;
  v_expect   jsonb;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  if p_reports is null or jsonb_typeof(p_reports) <> 'array' then
    raise exception 'send a list of results' using errcode = '22023';
  end if;
  if jsonb_array_length(p_reports) = 0 then
    raise exception 'add at least one result' using errcode = '22023';
  end if;
  -- The same ceiling create_fixtures uses, for the same reason: no matchday in
  -- the country is near it, and it stops a malformed or replayed call from
  -- holding a connection open updating for minutes.
  if jsonb_array_length(p_reports) > 60 then
    raise exception 'that is more than 60 results — send them in two goes'
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

  v_is_admin := public.is_admin();

  for v_row in select * from jsonb_array_elements(p_reports) loop
    v_i := v_i + 1;
    begin
      v_id := nullif(trim(coalesce(v_row->>'match_id', '')), '');
      if v_id is null then
        raise exception 'that line has no match on it' using errcode = '22023';
      end if;

      -- Locked for the rest of the transaction, exactly as the single path
      -- does it: two reporters publishing the same matchday serialize instead
      -- of racing, and the audit rows come out in the order the changes
      -- actually happened.
      --
      -- Aliased because this function's OUT parameters are named match_id,
      -- status, home_goals and away_goals: an unqualified column of any of
      -- those names is ambiguous to plpgsql and fails at run time.
      select m.* into v_match
      from public.matches m
      where m.match_id = v_id
      for update;

      if not found then
        raise exception 'match not found' using errcode = 'P0002';
      end if;

      -- THE ROW IS PINNED TO THE AUTHORIZED PAIR. Without this the one-time
      -- check above would be a check on the competition the client NAMED
      -- rather than on the match it sent, and a reporter assigned to one
      -- league could publish into another by putting its match_id on a line.
      -- can_report_match() asks (competition, season) of the match itself, so
      -- requiring both to equal the pair already authorized makes the single
      -- check exactly as strong, and no stronger.
      if v_match.competition_id is distinct from p_competition_id
         or v_match.season_id is distinct from v_season then
        raise exception 'that match is not in this competition this season'
          using errcode = '42501';
      end if;

      -- The conflict guard. Present only on a row the client drew from saved
      -- data and did not ask the reporter to confirm over; absent on a
      -- deliberate correction.
      v_expect := v_row->'expect';
      if v_expect is not null and jsonb_typeof(v_expect) = 'object' then
        if v_match.status is distinct from nullif(v_expect->>'status', '')
           or v_match.home_goals is distinct from (v_expect->>'home')::integer
           or v_match.away_goals is distinct from (v_expect->>'away')::integer
        then
          -- Named, not just refused: the reporter has to see what is actually
          -- there to decide whether their own line is the correction or the
          -- mistake.
          raise exception 'someone else published % while you were entering '
                          'this — check it and send it again',
            case when v_match.status in ('played', 'awarded')
                   and v_match.home_goals is not null
                 then v_match.home_goals || '–' || v_match.away_goals
                 else v_match.status end
            using errcode = '40001';
        end if;
      end if;

      v_result := public.apply_match_report(
        v_match,
        nullif(trim(coalesce(v_row->>'home', '')), '')::integer,
        nullif(trim(coalesce(v_row->>'away', '')), '')::integer,
        trim(coalesce(v_row->>'status', '')),
        p_source_ref,
        v_reporter, v_is_admin);

      idx := v_i; ok := true;
      match_id := v_result.match_id;
      home_goals := v_result.home_goals;
      away_goals := v_result.away_goals;
      status := v_result.status;
      message := '';
      return next;

    exception
      -- Every raise in apply_match_report is already a sentence written for a
      -- reporter to act on, so it is carried out as-is; the client shows it
      -- against the line it belongs to. Catching everything (rather than the
      -- listed error codes) is deliberate: an unforeseen failure on row four
      -- must still not discard rows one to three.
      when others then
        idx := v_i; ok := false;
        match_id := v_id;
        home_goals := null; away_goals := null; status := null;
        message := sqlerrm;
        return next;
    end;
  end loop;
end;
$$;

comment on function public.submit_match_reports(text, jsonb, text, text) is
  'Publish a whole matchday in one call. Returns a row per input row: a bad '
  'row is reported against its index and the good rows around it are saved.';

revoke execute on function public.submit_match_reports(text, jsonb, text, text)
  from public, anon;
grant execute on function public.submit_match_reports(text, jsonb, text, text)
  to authenticated;

commit;
