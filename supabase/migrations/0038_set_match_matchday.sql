-- 0038_set_match_matchday.sql — moving a fixture to another matchday.
--
-- WHAT WAS WRONG. A fixture list is entered by hand, matchday by matchday, and
-- a reporter can mistype the round: the next batch of CRFA1 fixtures went in
-- as matchday 9 while the actual matchday 9 had already been played, so two
-- rounds sat under one heading on /crfa/results.html. There was no way to fix
-- it short of the CLI — reschedule_match (0009) moves date and kickoff only,
-- deliberately, and widening it would blur the one-thing-at-a-time guarantee
-- that makes it safe to expose to every reporter.
--
-- WHAT THIS DOES. Its own narrow door, same shape as reschedule_match: writes
-- `matchday` and, to keep them from disagreeing (the exact drift
-- ops_matchday_status.stage_matchday_mismatch in 0016 exists to catch),
-- `stage` alongside it — always `md_<n>`, the same derivation create_fixture
-- already uses. Teams, competition, season, date and score are untouched.
--
-- Cup rounds are out of scope on purpose: DATA_MODEL.md is explicit that a
-- cup's round order comes from `stage` and the sheet's matchday column is
-- ignored, so "move to another matchday" has no meaning there — a reporter
-- reassigns a cup tie by editing its round instead.

begin;

create or replace function public.set_match_matchday(
  p_match_id  text,
  p_matchday  integer default null
)
returns setof public.matches
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_match    public.matches;
  v_type     text;
  v_stage    text;
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

  -- Locked for the rest of the transaction, same reasoning as
  -- reschedule_match: a matchday move and a result landing together
  -- serialize instead of racing.
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

  select c.type into v_type
  from public.competitions c
  where c.competition_id = v_match.competition_id;

  if v_type = 'cup' then
    raise exception
      'this is a cup tie — its round comes from the stage, not a matchday'
      using errcode = '22023';
  end if;

  if p_matchday is not null and p_matchday < 0 then
    raise exception 'matchday cannot be negative' using errcode = '22023';
  end if;

  v_stage := case when p_matchday is null then '' else 'md_' || p_matchday::text end;

  v_old := jsonb_build_object('matchday', v_match.matchday, 'stage', v_match.stage);
  v_new := jsonb_build_object('matchday', p_matchday, 'stage', v_stage);

  if v_old is not distinct from v_new then
    -- Nothing changed. Return the row rather than raising, exactly as
    -- reschedule_match does for the same case.
    return next v_match;
    return;
  end if;

  update public.matches
  set matchday   = p_matchday,
      stage      = v_stage,
      updated_at = now()
  where match_id = p_match_id
  returning * into v_match;

  -- Moving a fixture between rounds is exactly the kind of change someone
  -- will later want to account for, so it lands in the same append-only log
  -- reschedule_match and submit_match_report write to.
  insert into public.match_change_log
    (match_id, changed_by, old_values, new_values)
  values (p_match_id, v_reporter, v_old, v_new);

  return next v_match;
end;
$$;

comment on function public.set_match_matchday(text, integer) is
  'Move a league fixture to another matchday. Writes matchday and the '
  'derived md_<n> stage together, and appends to match_change_log. Cup '
  'ties are rejected — their round is the stage, not a matchday.';

revoke execute on function public.set_match_matchday(text, integer)
  from public, anon;
grant execute on function public.set_match_matchday(text, integer)
  to authenticated;

commit;
