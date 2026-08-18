-- 0015_match_venue.sql — correcting the ground on a fixture that already exists.
--
-- WHY. A fixture list announces a ground weeks ahead, and then reality
-- happens: the pitch is waterlogged, the graphic said "TO BE ANNOUNCED" and
-- the announcement came later, or the ground was simply typed wrong. 0014 gave
-- the portal a way to SAY where a match is; this is the way to change it.
--
-- It is a third narrow door, next to the two 0009 opened. submit_match_report
-- deliberately refuses to touch venue_id — the narrow-update guarantee in 0003
-- exists precisely so that permission to report a score is not permission to
-- restructure the fixture — and reschedule_match deliberately writes date and
-- kickoff and nothing else. Widening either would give that guarantee away for
-- the sake of one column. So: its own function, writing venue_id and nothing
-- else, behind its own button, appending to the same audit log.

begin;

create or replace function public.set_match_venue(
  p_match_id   text,
  p_venue_name text default '',
  p_venue_id   text default null
)
returns table (
  match_id   text,
  venue_id   text,
  venue_name text
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_match    public.matches;
  v_venue    text;
  v_old      jsonb;
  v_new      jsonb;
  v_oldname  text;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;

  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  -- Locked for the rest of the transaction, so a venue change and a result
  -- landing together serialize instead of racing.
  -- Aliased, not bare: this function has an OUT parameter called match_id, so
  -- an unqualified column of that name in a query would be ambiguous to
  -- plpgsql and fail at runtime rather than at creation.
  select * into v_match
  from public.matches m
  where m.match_id = p_match_id
  for update;

  if not found then
    raise exception 'match not found' using errcode = 'P0002';
  end if;

  if not public.can_report_match(p_match_id) then
    raise exception 'not assigned to this competition' using errcode = '42501';
  end if;

  -- An explicit id wins over a typed name, exactly as in create_fixture: a
  -- caller holding an id has already chosen a venue, and re-resolving its name
  -- could only disagree.
  v_venue := nullif(coalesce(p_venue_id, ''), '');
  if v_venue is not null then
    if not exists (select 1 from public.venues v where v.venue_id = v_venue) then
      raise exception 'unknown venue %', v_venue using errcode = 'P0002';
    end if;
  else
    -- Clearing the box is a legitimate answer and means what NULL already
    -- means: the ground is not settled. resolve_venue returns NULL for an
    -- empty name and for "TBA" alike, so both routes land in the same place.
    v_venue := public.resolve_venue(p_venue_name);
  end if;

  select v.name into v_oldname
  from public.venues v where v.venue_id = v_match.venue_id;

  if v_venue is not distinct from v_match.venue_id then
    -- The reporter asked for a state that is already true, which is not an
    -- error. Returning the row rather than raising matches reschedule_match.
    match_id := v_match.match_id;
    venue_id := v_match.venue_id;
    venue_name := v_oldname;
    return next;
    return;
  end if;

  update public.matches m
  set venue_id   = v_venue,
      updated_at = now()
  where m.match_id = p_match_id;

  -- The name goes into the log beside the id. A log entry that reads
  -- MW_LIKUNI -> MW_MKANDA needs a join to mean anything, and the point of the
  -- log is that someone can account for the change later without one.
  v_old := jsonb_build_object('venue_id', v_match.venue_id, 'venue', v_oldname);
  v_new := jsonb_build_object('venue_id', v_venue,
                              'venue', (select v.name from public.venues v
                                        where v.venue_id = v_venue));

  insert into public.match_change_log
    (match_id, changed_by, old_values, new_values)
  values (p_match_id, v_reporter, v_old, v_new);

  match_id := p_match_id;
  venue_id := v_venue;
  select v.name into venue_name
  from public.venues v where v.venue_id = v_venue;
  return next;
end;
$$;

comment on function public.set_match_venue(text, text, text) is
  'Move a fixture to another ground. Writes venue_id only, resolves a typed '
  'name through resolve_venue, and appends to match_change_log.';

revoke execute on function public.set_match_venue(text, text, text)
  from public, anon;
grant execute on function public.set_match_venue(text, text, text)
  to authenticated;

commit;
