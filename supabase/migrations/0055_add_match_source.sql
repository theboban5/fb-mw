-- 0055_add_match_source.sql — a match's sources are a list, and adding one
-- must not replace the others.
--
-- WHAT WAS WRONG. matches.source_ref has only ever been written by the score
-- RPCs (submit_match_report, submit_match_reports), and only as a REPLACEMENT:
-- p_source_ref is the whole column, and a blank one leaves it alone (0008).
-- That was right for its job — the source of a SCORE — and it left no way to
-- say "and here is where the team sheet came from" without re-publishing the
-- score and retyping every source already there. Reporters had already worked
-- out the format the column needed: rows in the data read
-- "https://www.facebook.com/share/p/…, https://silverstrikers.mw/…", two
-- links typed into one box by hand.
--
-- Match-report import (0054's feature) is the first writer that holds a link
-- and is not publishing a score, so it is the first that needs this.
--
-- WHAT THIS DOES. add_match_source appends one source to a match's list:
--   - a source already in the list is a no-op, so the same report imported
--     twice, or from two screens, lists once;
--   - the list is comma-separated, the format reporters were already typing;
--   - it stays within the 500 characters every writer of the column caps at,
--     and a source that would not fit is refused with a sentence rather than
--     truncated — a link cut short is a link that goes nowhere.
-- It never removes or rewrites anything; that stays with the score screen,
-- whose source box still edits the whole column.
--
-- The change is logged to match_change_log like every other write to a match,
-- with channel 'single' (0047): it comes from one match's screen. Its payload
-- carries only source_ref, so #/ops lists it as 'other' — 0047's deliberately
-- reachable label for a writer with no case of its own.

begin;

create or replace function public.add_match_source(
  p_match_id text,
  p_source   text
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_match    public.matches;
  v_source   text;
  v_old      text;
  v_new      text;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  v_source := btrim(coalesce(p_source, ''));
  if v_source = '' then
    raise exception 'there is no source to add' using errcode = '22023';
  end if;

  select m.* into v_match from public.matches m
  where m.match_id = p_match_id for update;
  if not found then
    raise exception 'match not found' using errcode = 'P0002';
  end if;
  if not public.can_report_match(p_match_id) then
    raise exception 'not assigned to this competition' using errcode = '42501';
  end if;

  v_old := btrim(coalesce(v_match.source_ref, ''));
  -- Already listed: compared as whole entries, so one link that is a prefix
  -- of another is not mistaken for it.
  if v_source = any (string_to_array(regexp_replace(v_old, '\s*,\s*', ',', 'g'), ',')) then
    return v_old;
  end if;

  v_new := case when v_old = '' then v_source else v_old || ', ' || v_source end;
  if length(v_new) > 500 then
    raise exception 'this match already lists as many sources as fit'
      using errcode = '22023';
  end if;

  update public.matches set source_ref = v_new where match_id = p_match_id;

  insert into public.match_change_log (match_id, changed_by, old_values,
                                       new_values, source)
  values (p_match_id, v_reporter, jsonb_build_object('source_ref', v_old),
          jsonb_build_object('source_ref', v_new), 'single');

  return v_new;
end;
$$;

comment on function public.add_match_source(text, text) is
  'Append one source to a match''s comma-separated source_ref, unless it is '
  'already listed. Never removes or rewrites one. Returns the new list.';

-- Explicitly, every time: this project's default privileges grant new
-- functions to anon (0048).
revoke execute on function public.add_match_source(text, text) from public, anon;
grant execute on function public.add_match_source(text, text) to authenticated;

commit;
