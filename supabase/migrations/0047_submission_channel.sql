-- 0047_submission_channel.sql — HOW a result was submitted, recorded at last.
--
-- WHAT WAS WRONG. match_change_log.source has defaulted to 'reporter' since
-- 0003, where its own comment said "everything is 'reporter' today; a future
-- bulk correction or import can say so without being mistaken for a person."
-- No FUNCTION has ever set it — the eight rows reading 'admin' are a bulk
-- matchday correction someone made by hand with the secret key on 2026-08-25,
-- which is that comment working exactly as intended and is the reason 'admin'
-- stays legal below. Every row any code path has written says 'reporter'.
--
-- So the import shipped in 0042–0045 and a result a model read off a blurry
-- screenshot and a reporter approved is, in the audit trail, indistinguishable
-- from one typed on a single match screen. report_imports records no
-- match_ids either, so the join back to the evidence does not exist.
--
-- That is the one question an operator actually asks about a wrong scoreline:
-- where did this come from? The row that answers it was already being written.
-- It just was not being filled in.
--
-- WHAT THE LOG ACTUALLY IS, which is worth stating because it is easy to read
-- this table as a result log and it is not. SEVEN functions write to it:
-- apply_match_report (score/status/source_ref), reschedule_match (date,
-- kickoff), set_match_venue (venue_id), set_match_officials in both its 0023
-- and 0024 forms (referee and coaches), and set_match_matchday (stage,
-- matchday). It is the audit of a MATCH, and only the first of those is a
-- result being submitted. The view at the bottom therefore labels each row by
-- which keys its payload carries, rather than pretending the others are
-- scorelines with the score missing.
--
-- WHAT THIS DOES.
--
--   match_change_log.source      constrained, and actually written
--   match_change_log.import_id   the evidence, one join away
--   apply_match_report           takes both and records them
--   submit_match_report          says 'single'
--   submit_match_reports         takes an optional import, and derives the rest
--   ops_submissions              one row per change, with everything beside it
--
-- THE CHANNEL IS DERIVED, NEVER CLAIMED. There is deliberately no p_channel
-- parameter. A client that could name its own channel could lie about it, and
-- worse, could disagree with itself — source = 'import' on a row with no
-- import_id is a record that cannot be checked against anything. So the batch
-- RPC takes an import id or does not, and the channel follows from that:
--
--   submit_match_report                   -> 'single'
--   submit_match_reports, no import       -> 'grid'
--   submit_match_reports, with an import  -> 'import', id validated
--
-- It is the same instinct as 0043's confidence, which comes from how a name
-- matched rather than from what the model said about itself.
--
-- NOTHING IS BACKFILLED. Every existing row keeps source = 'reporter', which
-- honestly means "we did not record it" — and the screen says so rather than
-- guessing. Inventing a channel for 504 historical rows would put a fact in
-- the audit log that nobody ever observed, which is the opposite of what an
-- audit log is for.
--
-- WHY THE LOG AND NOT matches. matches.reported_by / reported_at hold only the
-- LAST submission. "What was submitted on 6 September" has to show a match
-- twice if it was published and then corrected, and has to keep saying so
-- after somebody edits it again on the 8th. The log is per-event and
-- append-only; matches is current state. It is also the only place that knows
-- what the result was BEFORE.

begin;

-- ── The two columns ──────────────────────────────────────────────────────────

alter table public.match_change_log
  add column if not exists import_id uuid
    references public.report_imports (import_id) on delete set null;

comment on column public.match_change_log.import_id is
  'The AI import this change was published from, when it was one. Nullable, '
  'and ON DELETE SET NULL for match_change_log.changed_by''s reason: losing '
  'the evidence must not lose the history.';

-- Both legacy values stay legal, for different reasons.
--
-- 'reporter' is the default 496 rows hold and it means "written before 0047,
-- channel not recorded" — the screen says exactly that rather than dressing a
-- default up as an answer.
--
-- 'admin' is the eight rows of the hand-made matchday correction, and it stays
-- because it is TRUE and because the next such correction should go on saying
-- so. It is the one value no function writes: a person with the secret key,
-- outside the portal entirely, which is a real way for the data to change and
-- worth being able to see.
alter table public.match_change_log
  drop constraint if exists match_change_log_source_check;
alter table public.match_change_log
  add constraint match_change_log_source_check
  check (source in ('single', 'grid', 'import', 'reporter', 'admin'));

comment on column public.match_change_log.source is
  'How the change arrived: single (one match screen), grid (the matchday '
  'grid), import (published from an AI import, see import_id), admin (by hand '
  'with the secret key). The default ''reporter'' means it predates 0047 and '
  'was never recorded.';


-- ── apply_match_report ───────────────────────────────────────────────────────
-- Dropped and recreated because the argument list changes. It is internal —
-- granted to nobody, reachable only through the two functions below, both of
-- which are recreated in this same transaction — so there is no window in
-- which anything can call the old one.

drop function if exists public.apply_match_report(public.matches, integer,
  integer, text, text, text, boolean);

create or replace function public.apply_match_report(
  p_match      public.matches,
  p_home_score integer,
  p_away_score integer,
  p_status     text,
  p_source_ref text,
  p_reporter   text,
  p_is_admin   boolean,
  p_source     text,
  p_import_id  uuid
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
      -- matches.source_type is NOT the channel. It records how the ROW got
      -- here — DATA_MODEL is explicit that anything entered through /report is
      -- 'reporter' even when the reporter read it on Facebook, because
      -- `confidence` and the unconfirmed asterisk key off it. An imported
      -- result is still a reporter's result: they looked at it and approved
      -- it. Which screen they approved it on is the log's business, not this
      -- column's.
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
  --
  -- NEW IN 0047: the channel and the import travel with the change. Note what
  -- this means for the screen built on it — a no-op republish writes nothing,
  -- so "everything submitted today" is everything that CHANGED today, which is
  -- the honest reading of the question and the only one the log can answer.
  if v_old is distinct from v_new then
    insert into public.match_change_log
      (match_id, changed_by, old_values, new_values, source, import_id)
    values (p_match.match_id, p_reporter, v_old, v_new,
            coalesce(nullif(trim(coalesce(p_source, '')), ''), 'single'),
            p_import_id);
  end if;

  return v_match;
end;
$$;

comment on function public.apply_match_report(public.matches, integer, integer,
  text, text, text, boolean, text, uuid) is
  'Internal. The reporting rules, once, for both submit_match_report and '
  'submit_match_reports. Does no authorization — its callers do.';

revoke execute on function public.apply_match_report(public.matches, integer,
  integer, text, text, text, boolean, text, uuid)
  from public, anon, authenticated;


-- ── submit_match_report: unchanged signature, now says 'single' ──────────────
-- Same arguments, same behaviour, same guarantees. Every browser in the field
-- keeps working through this deploy; the only difference is that the log row
-- it writes now says which screen it came from.

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
  if (select auth.uid()) is null then
    raise exception 'not authenticated'
      using errcode = '28000';
  end if;

  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  select * into v_match
  from public.matches
  where match_id = p_match_id
  for update;

  if not found then
    raise exception 'match not found'
      using errcode = 'P0002';
  end if;

  if not public.can_report_match(p_match_id) then
    raise exception 'not assigned to this competition'
      using errcode = '42501';
  end if;

  return next public.apply_match_report(
    v_match, p_home_score, p_away_score, p_status, p_source_ref,
    v_reporter, public.is_admin(), 'single', null);
end;
$$;

comment on function public.submit_match_report(text, integer, integer, text, text) is
  'The only single-match reporter write path. Updates score/status/source_ref '
  'only, sets provenance, and appends to match_change_log as ''single''.';


-- ── submit_match_reports: one optional argument, and a derived channel ───────
-- Dropped and recreated rather than overloaded. Adding a defaulted parameter
-- creates a SECOND function, and PostgREST's named-argument call would then
-- match both and fail as ambiguous — 0008's trap exactly. Doing it inside this
-- transaction means the window in which neither exists is measured in
-- milliseconds and is invisible to any client; afterwards the four-argument
-- named call every browser in the field makes still resolves, uniquely, to
-- this function with p_import_id defaulted to null.
--
-- p_import_id IS VALIDATED, WHICH IS WHAT MAKES THE CHANNEL WORTH RECORDING.
-- Anyone could pass a uuid; not anyone can pass a uuid naming an import that
-- exists and is theirs. Without the check, source = 'import' would be a claim
-- rather than a fact, and an audit log full of claims is not an audit log.

drop function if exists public.submit_match_reports(text, jsonb, text, text);

create or replace function public.submit_match_reports(
  p_competition_id text,
  p_reports        jsonb,
  p_source_ref     text default '',
  p_season_id      text default null,
  p_import_id      uuid default null
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
  v_channel  text;
  v_owner    text;
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

  -- THE CHANNEL, DERIVED. No import named means this is the matchday grid,
  -- which is the only other screen that calls this function.
  v_channel := 'grid';
  if p_import_id is not null then
    select r.reporter_id into v_owner
    from public.report_imports r where r.import_id = p_import_id;
    if v_owner is null then
      raise exception 'that import no longer exists' using errcode = 'P0002';
    end if;
    -- An admin may publish from an import they are helping with; nobody else
    -- may attribute their results to somebody else's submission. Same rule as
    -- resolve_and_save_import (0045).
    if v_owner is distinct from v_reporter and not v_is_admin then
      raise exception 'that import belongs to another reporter'
        using errcode = '42501';
    end if;
    v_channel := 'import';
  end if;

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
        v_reporter, v_is_admin,
        v_channel, p_import_id);

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

comment on function public.submit_match_reports(text, jsonb, text, text, uuid) is
  'Publish a whole matchday in one call. Returns a row per input row: a bad '
  'row is reported against its index and the good rows around it are saved. '
  'p_import_id names the AI import this came from, if any — validated, and '
  'what makes the logged channel ''import'' rather than ''grid''.';

revoke execute on function
  public.submit_match_reports(text, jsonb, text, text, uuid)
  from public, anon;
grant execute on function
  public.submit_match_reports(text, jsonb, text, text, uuid)
  to authenticated;


-- ── ops_submissions ──────────────────────────────────────────────────────────
-- One row per recorded change, with everything an operator needs beside it:
-- which match, by whom, from where, how, when, and what it replaced.
--
-- EVERY ROW, LABELLED — not just the results. The log is a match audit with
-- seven writers (see the header), so filtering it down to score changes would
-- throw away the reschedule that moved the fixture and the venue that was
-- filled in an hour later, which are exactly the neighbouring facts somebody
-- looking at a suspicious scoreline wants. `kind` comes from WHICH KEYS the
-- payload carries, because that is the only thing that distinguishes them —
-- none of the six older writers records what it was.
--
-- security_invoker = true plus `where public.is_admin()` in the body: 0016's
-- arrangement, and its comment applies unchanged — the grant is what makes the
-- view visible at all, and the predicate is the real gate.
--
-- THE DAY IS IN CAT, NOT UTC. changed_at is timestamptz and a Malawian
-- Saturday evening's results land after 22:00 UTC, so bucketing by the UTC
-- date would file half a matchday under Sunday. Africa/Blantyre is UTC+2 with
-- no DST, so the conversion is exact and needs no case analysis.
--
-- No index, deliberately. The table is 504 rows after eighteen months of
-- reporting and grows by a few a day; this is a screen an administrator opens
-- a handful of times, and an expression index on the CAT date is not even
-- possible (`at time zone` is STABLE, not IMMUTABLE). Adding one now would be
-- a guess about a problem that does not exist yet.

create or replace view public.ops_submissions with (security_invoker = true) as
select
  l.id                                              as log_id,
  l.changed_at,
  (l.changed_at at time zone 'Africa/Blantyre')::date as day,
  l.match_id,
  m.public_id,
  m.competition_id,
  c.name                                            as competition_name,
  c.type                                            as competition_type,
  m.season_id,
  m.date                                            as match_date,
  m.stage,
  th.display_name                                   as home_name,
  ta.display_name                                   as away_name,

  -- WHAT KIND OF CHANGE THIS IS, from the shape of the payload. The keys are
  -- each writer's signature and the only thing that tells them apart:
  -- apply_match_report is the one that carries a status, reschedule_match the
  -- one that carries a date, and so on. 'other' is left deliberately reachable
  -- — a writer added later without a case here should show up as unlabelled
  -- rather than be filed as a result.
  case
    when l.new_values ? 'status'   then 'result'
    when l.new_values ? 'date'     then 'reschedule'
    when l.new_values ? 'venue_id' then 'venue'
    when l.new_values ? 'referee'  then 'officials'
    when l.new_values ? 'matchday' then 'matchday'
    else 'other'
  end                                               as kind,

  -- What it replaced and what it became, flattened for a phone. The jsonb is
  -- carried through beside it because everything that is NOT a result has its
  -- own shape, and the screen renders those from the keys themselves.
  l.old_values,
  l.new_values,
  l.old_values->>'status'                           as old_status,
  (l.old_values->>'home_goals')::integer            as old_home,
  (l.old_values->>'away_goals')::integer            as old_away,
  l.new_values->>'status'                           as new_status,
  (l.new_values->>'home_goals')::integer            as new_home,
  (l.new_values->>'away_goals')::integer            as new_away,

  -- The provenance AS IT WAS RECORDED AT THE TIME, not as it stands now.
  -- new_values has carried source_ref since 0041; before that it did not, and
  -- a null here means the row predates it rather than that nothing was said.
  l.new_values->>'source_ref'                       as source_ref,

  l.source,
  l.import_id,
  i.channel                                         as import_channel,
  i.model                                           as import_model,
  l.changed_by,
  r.name                                            as reporter_name
from public.match_change_log l
join public.matches m       on m.match_id = l.match_id
join public.teams th        on th.team_id = m.home_team_id
join public.teams ta        on ta.team_id = m.away_team_id
left join public.competitions c on c.competition_id = m.competition_id
left join public.reporters r    on r.reporter_id = l.changed_by
left join public.report_imports i on i.import_id = l.import_id
where public.is_admin();

comment on view public.ops_submissions is
  'Every recorded change to a result: which match, by whom, how it was '
  'submitted, and what it replaced. Admin only, day bucketed in CAT.';

-- A view is owned by the migration runner, so a signed-in reporter has no
-- privileges on it until granted. The grant is what makes it visible at all;
-- the is_admin() in the body is what decides whether it has anything in it.
grant select on public.ops_submissions to authenticated;

commit;
