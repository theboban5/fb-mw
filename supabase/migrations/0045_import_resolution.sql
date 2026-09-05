-- 0045_import_resolution.sql — matching an import, and keeping what was
-- matched.
--
-- WHY THIS IS A SEPARATE CALL FROM THE EDGE FUNCTION. Extraction needs the
-- ANTHROPIC key, so it runs in a function that holds one. Matching needs
-- auth.uid(), because WHICH COMPETITIONS MAY BE PROPOSED is a fact about the
-- caller — and the Edge Function cannot call PostgREST as the caller: the
-- platform injects SUPABASE_ANON_KEY, and on a project using the new API keys
-- that slot holds a digest rather than a usable key. trigger-rebuild's header
-- records the day that cost, and the answer there was the same as here: do not
-- fake the caller's identity, split the work along the line where the identity
-- actually matters.
--
-- So: the Edge Function extracts and stores; the client, holding its own
-- session, calls this. It is one round trip, and the matcher runs as the
-- reporter, which is the only way its scope means anything.
--
-- WHY RESOLVE AND SAVE ARE ONE FUNCTION AND NOT TWO. If the client resolved
-- and then saved, the two could differ — a client could show one thing and
-- record another, by accident or otherwise, and report_imports.resolved is
-- supposed to be evidence of what the reporter was shown. Doing both here
-- means the stored payload IS the returned payload, by construction.
--
-- IT STILL PUBLISHES NOTHING. The output is a proposal. Publishing is
-- submit_match_reports (0041), from rows a person has looked at, authorized
-- exactly as it always was.

begin;

create or replace function public.resolve_and_save_import(
  p_import_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_row      public.report_imports;
  v_items    jsonb;
  v_resolved jsonb;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  select r.* into v_row
  from public.report_imports r
  where r.import_id = p_import_id
  for update;
  if not found then
    raise exception 'that import no longer exists' using errcode = 'P0002';
  end if;

  -- An admin may read anyone's import (the 0042 policy) so they can help with
  -- one that went wrong. Resolving it AS THEMSELVES would silently widen the
  -- proposal to every competition, which is not what "help with this reporter's
  -- import" means — so this is the owner's, or an admin's own.
  if v_row.reporter_id is distinct from v_reporter and not public.is_admin() then
    raise exception 'that import belongs to another reporter'
      using errcode = '42501';
  end if;

  if v_row.extracted is null then
    raise exception 'that import has not been read yet' using errcode = '22023';
  end if;

  v_items := coalesce(v_row.extracted->'results', '[]'::jsonb);
  if jsonb_typeof(v_items) <> 'array' then
    v_items := '[]'::jsonb;
  end if;

  -- The matcher, run as the caller. Scoped to their competitions, creates
  -- nothing, decides confidence from evidence rather than from the model.
  v_resolved := public.resolve_import_candidates(v_items, null);

  update public.report_imports
  set resolved    = v_resolved,
      reviewed_at = coalesce(reviewed_at, now())
  where import_id = p_import_id;

  return v_resolved;
end;
$$;

comment on function public.resolve_and_save_import(uuid) is
  'Match an extracted import against fixtures the CALLER may report, store the '
  'proposal, and return it. Publishes nothing.';

revoke execute on function public.resolve_and_save_import(uuid)
  from public, anon;
grant execute on function public.resolve_and_save_import(uuid) to authenticated;

commit;
