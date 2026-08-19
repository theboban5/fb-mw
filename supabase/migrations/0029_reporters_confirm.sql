-- 0029 — a reporter's own result is confirmed
--
-- WHAT WAS WRONG
-- Since 0003 "confirmed" has meant "an ADMIN typed it": a reporter's
-- submission landed as confidence = 'unconfirmed', which the site renders as
-- an asterisk beside the score and a "result not yet confirmed" legend under
-- the list. That was the right default when the only reporters were the two
-- admins and every other row came from scraping Facebook — the asterisk said
-- "nobody we trust has looked at this yet".
--
-- It stopped saying that the moment we started onboarding people. A reporter
-- is assigned to a competition BY an admin, is the person who was at the
-- ground, and enters the result from the touchline. Their word is the best
-- evidence this site has. Marking it provisional until an admin re-types the
-- identical score put an asterisk on the most reliable rows we get, and made
-- "confirming" a result mean an admin re-publishing someone else's work —
-- which also overwrites reported_by, so the byline was lost in the process.
--
-- WHAT THIS DOES
-- Redefines submit_match_report so ANY authorized reporter's submission is
-- confidence = 'confirmed', with verified_by/verified_at set to them. Nothing
-- else in the function changes; the admin-only gate on 'awarded' stays exactly
-- where it was, and so does can_report_match — a reporter still cannot touch a
-- competition they are not assigned to. This does not backfill: rows already
-- sitting at 'unconfirmed' stay there until someone re-publishes them.
--
-- THE TRADE, PLAINLY
-- 'unconfirmed' now only ever appears on rows that did NOT come from a
-- reporter — a backfill, a scrape, a fixture created without a result. That is
-- a narrower meaning than the enum was designed for and it loses the two-pair-
-- of-eyes step: a reporter's typo publishes as fact, with no visual signal
-- that only one person has seen it. We are buying that back with authorization
-- instead of with review — the control is now WHO gets assigned to a
-- competition, not what happens after they submit. If we ever want the middle
-- ground back, the shape is a `trusted` flag on reporters keyed here, not a
-- return to gating on role.

begin;

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
  v_is_admin boolean;
  v_match    public.matches;
  v_old      jsonb;
  v_new      jsonb;
  v_scored   boolean;
  v_source   text;
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

  v_is_admin := public.is_admin();

  -- 5. Validate. These mirror validate.py check 4 and the table's own
  --    constraints; catching them here produces a message a reporter can act
  --    on rather than a constraint-violation code.
  if p_status not in ('scheduled', 'played', 'postponed', 'abandoned',
                      'cancelled', 'awarded') then
    raise exception 'invalid status %', p_status
      using errcode = '22023';
  end if;

  -- 'awarded' is an administrative decision — a walkover, a forfeit — not
  -- something observed at a ground. It counts into standings with its recorded
  -- score, so it stays out of the reporter path.
  if p_status = 'awarded' and not v_is_admin then
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
  -- without re-typing the link must not erase the link.
  v_source := left(trim(coalesce(p_source_ref, '')), 500);
  if v_source = '' then
    v_source := v_match.source_ref;
  end if;

  v_old := jsonb_build_object(
    'home_goals', v_match.home_goals,
    'away_goals', v_match.away_goals,
    'status',     v_match.status,
    'source_ref', v_match.source_ref);
  v_new := jsonb_build_object(
    'home_goals', p_home_score,
    'away_goals', p_away_score,
    'status',     p_status,
    'source_ref', v_source);

  -- 6. Write ONLY the reporting fields. home_team_id, away_team_id,
  --    competition_id, season_id, date, kickoff, venue_id and every other
  --    structural column are absent from this statement by design.
  update public.matches
  set home_goals  = p_home_score,
      away_goals  = p_away_score,
      status      = p_status,
      source_type = 'reporter',
      source_ref  = v_source,
      reported_by = v_reporter,
      reported_at = now(),
      -- 0029: confirmed for anyone allowed to submit, not only an admin. The
      -- reporter vouches for their own result, so they are the verifier.
      confidence  = 'confirmed',
      verified_by = v_reporter,
      verified_at = now(),
      updated_at  = now()
  where match_id = p_match_id
  returning * into v_match;

  -- 7. Record it — but only when something actually changed. Re-tapping
  --    publish on an unchanged result is a no-op worth attributing (steps 6
  --    still update reported_by/reported_at) and not worth a log row.
  if v_old is distinct from v_new then
    insert into public.match_change_log
      (match_id, changed_by, old_values, new_values)
    values (p_match_id, v_reporter, v_old, v_new);
  end if;

  return next v_match;
end;
$$;

commit;
