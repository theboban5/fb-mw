-- 0042_report_imports.sql — what a reporter submitted, what came back, and
-- whether anything was ever published from it.
--
-- WHY THIS TABLE EXISTS AT ALL. The importer's whole safety story is that a
-- machine reading is a PROPOSAL and a person's tap is the publication. That
-- story is only checkable afterwards if the proposal was written down. Without
-- this table a wrong scoreline on the site is indistinguishable from a typo:
-- match_change_log says a reporter published 3–0 and nothing anywhere says a
-- model read 3–0 off a blurry photo and the reporter agreed. So every
-- submission gets a row, before the model is called, and the row keeps the
-- source, the raw extraction and the match the code proposed — whether or not
-- anything was published from it.
--
-- IT IS ALSO THE RETRY. A reopened import must not be a second charge (the
-- extraction is already here), and an extraction that failed halfway must be
-- resumable rather than re-typed.
--
-- WHAT THIS DOES NOT DO. It is not a write path to anything the site renders.
-- No trigger, no function here touches matches, goals, teams or players.
-- Publishing goes through submit_match_reports (0041), authorized the same way
-- it always was, from rows a reporter has looked at — which is why an
-- AI-prefilled result and a typed one are indistinguishable at the boundary,
-- and why nothing here needs to be trusted with more than it is given.
--
-- THE SHAPE OF THE PAYLOADS IS DELIBERATELY jsonb AND DELIBERATELY UNPARSED
-- BY SQL. `extracted` is what the model said, verbatim, including fields we do
-- not use yet (scorers, venue) and fields a future version will add (fixture
-- lists). `resolved` is what 0043's matcher made of it. Nothing joins on
-- either; they are evidence, and giving evidence a schema it has to satisfy is
-- how you end up unable to record the submission that broke your assumptions.

begin;

-- ── report_imports ───────────────────────────────────────────────────────────

create table public.report_imports (
  import_id      uuid primary key default gen_random_uuid(),

  -- Nullable so that deleting a reporter cannot delete the evidence, the same
  -- reason match_change_log.changed_by is nullable. The row keeps saying what
  -- was submitted even when it can no longer say by whom.
  reporter_id    text references public.reporters (reporter_id) on delete set null,

  -- How it arrived. 'image_url' is an image AND a link together, which is the
  -- Facebook case and a first-class workflow rather than a fallback: the link
  -- is the provenance and the screenshot is the only readable copy.
  channel        text not null
                 check (channel in ('image', 'text', 'url', 'image_url')),

  -- Provenance, kept whether or not it could be read. A Facebook URL that
  -- redirected to a login is still where the result came from, and it is what
  -- gets written to matches.source_ref when the reporter publishes.
  source_url     text not null default '',
  pasted_text    text not null default '',

  -- '<reporter_id>/<uuid>.jpg' in the PRIVATE report-imports bucket. See the
  -- policies below for why the path is a rule rather than a convention.
  storage_path   text not null default '',

  -- What the model said, verbatim. Null until extraction succeeds.
  extracted      jsonb,
  -- What 0043's matcher made of it: candidates, confidence, ambiguities.
  resolved       jsonb,

  -- Which model, and what it cost. Recorded because "should we still be on
  -- Sonnet?" is a question that can only be answered from data, and because a
  -- run of unexpectedly expensive imports is a thing worth being able to see.
  model          text not null default '',
  usage          jsonb,

  status         text not null default 'pending'
                 check (status in ('pending', 'extracted', 'failed',
                                   'published', 'discarded')),
  -- A CATEGORY, never a raw error. 'unreadable_link', 'no_matches_found',
  -- 'model_error', 'bad_output'. The detail goes to the function's logs, which
  -- an operator can read and a reporter cannot — a provider error can name
  -- infrastructure.
  error_category text not null default '',

  created_at     timestamptz not null default now(),
  reviewed_at    timestamptz,
  published_at   timestamptz
);

create index report_imports_reporter_idx
  on public.report_imports (reporter_id, created_at desc);
-- The rate-limit count below is a window over created_at for one reporter,
-- which the index above already serves; this one is for the admin review
-- screen, which asks "what is waiting" across everyone.
create index report_imports_status_idx
  on public.report_imports (status, created_at desc);

comment on table public.report_imports is
  'One row per AI import submission: what was sent, what was extracted, what '
  'was proposed. Never a write path to matches — publishing goes through '
  'submit_match_reports like every other result.';

alter table public.report_imports enable row level security;

-- A reporter sees their own submissions; an admin sees all of them, because
-- "why did this import go wrong" is an operational question and the answer is
-- in someone else's row. Anon sees nothing: a pasted paragraph can name people
-- and a screenshot can be of anything.
create policy report_imports_read
  on public.report_imports for select to authenticated
  using (public.is_admin()
         or reporter_id = public.current_reporter_id());

-- NO insert, update or delete policy, deliberately. Rows are created by
-- create_report_import (which enforces the rate limit), completed by the Edge
-- Function with the secret key, and closed by set_import_outcome. A generic
-- UPDATE would let a reporter rewrite `extracted` after the fact, which is
-- exactly the record this table exists to keep honest.
revoke insert, update, delete on public.report_imports from anon, authenticated;


-- ── The rate limit ───────────────────────────────────────────────────────────
-- Per reporter, per hour. Not a security boundary — an authorized reporter is
-- not the threat — but every import is a paid model call, and the failure this
-- prevents is mundane: a retry loop on a flaky connection, or a reporter
-- tapping "try again" twenty times on a photo that cannot be read.
--
-- Generous on purpose. A reporter working through a weekend's graphics might
-- legitimately submit two dozen; the cap is set where an accident lives, not
-- where use does.

create or replace function public.import_rate_remaining(p_reporter text)
returns integer
language sql
stable
security definer
set search_path = ''
as $$
  select greatest(0, 40 - count(*)::integer)
  from public.report_imports r
  where r.reporter_id = p_reporter
    and r.created_at > now() - interval '1 hour'
$$;

revoke execute on function public.import_rate_remaining(text)
  from public, anon, authenticated;


-- ── create_report_import ─────────────────────────────────────────────────────
-- The row is created BEFORE the model is called, and that ordering is the
-- point: a submission that fails during extraction still leaves a record
-- saying it was attempted, with the source attached. An import that only came
-- into existence on success would make every failure invisible — including the
-- ones worth acting on, like a link format nobody can read.

create or replace function public.create_report_import(
  p_channel      text,
  p_source_url   text default '',
  p_pasted_text  text default '',
  p_storage_path text default ''
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_left     integer;
  v_id       uuid;
  v_url      text;
  v_text     text;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  if p_channel not in ('image', 'text', 'url', 'image_url') then
    raise exception 'invalid submission type %', p_channel using errcode = '22023';
  end if;

  v_url  := left(trim(coalesce(p_source_url, '')), 2000);
  v_text := left(trim(coalesce(p_pasted_text, '')), 20000);

  -- Only http(s) is ever stored. The Edge Function checks this again before it
  -- fetches anything (along with the private-address rules it can actually
  -- evaluate); this is the cheaper half, said where a reporter can see it.
  if v_url <> '' and v_url !~* '^https?://' then
    raise exception 'a source link must start with http:// or https://'
      using errcode = '22023';
  end if;

  -- Something to read. An empty submission is a tap that did nothing, and
  -- charging for it is worse than refusing it.
  if v_url = '' and v_text = '' and coalesce(trim(p_storage_path), '') = '' then
    raise exception 'add a screenshot, some text, or a link'
      using errcode = '22023';
  end if;

  v_left := public.import_rate_remaining(v_reporter);
  if v_left <= 0 then
    raise exception 'that is a lot of imports in one hour — wait a little, '
                    'or enter these results by hand'
      using errcode = '54000';
  end if;

  insert into public.report_imports
    (reporter_id, channel, source_url, pasted_text, storage_path)
  values
    (v_reporter, p_channel, v_url, v_text,
     left(trim(coalesce(p_storage_path, '')), 400))
  returning import_id into v_id;

  return v_id;
end;
$$;

comment on function public.create_report_import(text, text, text, text) is
  'Open an import, before the model is called, so a failure still leaves a '
  'record of what was submitted. Enforces the per-reporter hourly cap.';

revoke execute on function public.create_report_import(text, text, text, text)
  from public, anon;
grant execute on function public.create_report_import(text, text, text, text)
  to authenticated;


-- ── set_import_outcome ───────────────────────────────────────────────────────
-- The reporter closing the loop: they published from this import, or they
-- threw it away. Nothing else about the row may be changed, which is why this
-- is a function with two allowed values rather than an UPDATE policy.
--
-- It records the outcome and NOT what was published. What was published is in
-- matches and match_change_log, written by submit_match_reports, attributed to
-- the reporter — and duplicating it here would create a second version of the
-- truth that could disagree with the first.

create or replace function public.set_import_outcome(
  p_import_id uuid,
  p_status    text
)
returns public.report_imports
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_row      public.report_imports;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  if p_status not in ('published', 'discarded') then
    raise exception 'invalid import outcome %', p_status using errcode = '22023';
  end if;

  select r.* into v_row
  from public.report_imports r
  where r.import_id = p_import_id
  for update;
  if not found then
    raise exception 'that import no longer exists' using errcode = 'P0002';
  end if;

  -- An admin may close anyone's; a reporter only their own. Reading someone
  -- else's is already allowed (the policy above) so that an admin can help;
  -- closing it is a different act.
  if v_row.reporter_id is distinct from v_reporter and not public.is_admin() then
    raise exception 'that import belongs to another reporter'
      using errcode = '42501';
  end if;

  update public.report_imports
  set status       = p_status,
      reviewed_at  = coalesce(reviewed_at, now()),
      published_at = case when p_status = 'published'
                          then coalesce(published_at, now())
                          else published_at end
  where import_id = p_import_id
  returning * into v_row;

  return v_row;
end;
$$;

comment on function public.set_import_outcome(uuid, text) is
  'Close an import as published or discarded. Records the outcome only — what '
  'was published lives in matches and match_change_log.';

revoke execute on function public.set_import_outcome(uuid, text)
  from public, anon;
grant execute on function public.set_import_outcome(uuid, text) to authenticated;


-- ── Storage: the report-imports bucket ───────────────────────────────────────
-- PRIVATE, and emphatically not match-media.
--
-- match-media is public because the static site has to <img src> the photos in
-- it. What a reporter submits to the importer is a different kind of object: a
-- screenshot of somebody's Facebook post, a photo of a printed team sheet, a
-- picture of a WhatsApp thread with names and numbers in it. None of that is
-- publishable content, none of it was offered for publication, and putting it
-- in a public bucket would make every one of them a URL that anybody who
-- guessed it could read. It is evidence for an audit, so it is readable by the
-- reporter who submitted it and by an administrator, and by nobody else.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('report-imports', 'report-imports', false, 10485760,
        array['image/jpeg', 'image/png', 'image/webp'])
on conflict (id) do update
  set public             = excluded.public,
      file_size_limit    = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

-- The path's first segment IS the reporter it belongs to, which is what makes
-- '<reporter_id>/<uuid>.jpg' a rule and not a convention — the same trick
-- 0007 plays with the match public_id, for the same reason: the object is
-- authorized by reading its own name.
create or replace function public.can_touch_import_path(p_name text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select public.is_admin()
      or (public.current_reporter_id() is not null
          and split_part(p_name, '/', 1) = public.current_reporter_id())
$$;

revoke execute on function public.can_touch_import_path(text) from public, anon;
grant execute on function public.can_touch_import_path(text) to authenticated;

drop policy if exists report_imports_object_read on storage.objects;
drop policy if exists report_imports_object_insert on storage.objects;
drop policy if exists report_imports_object_delete on storage.objects;

-- No anon in the TO list anywhere below, unlike match-media's read policy.
create policy report_imports_object_read on storage.objects
  for select to authenticated
  using (bucket_id = 'report-imports' and public.can_touch_import_path(name));

create policy report_imports_object_insert on storage.objects
  for insert to authenticated
  with check (bucket_id = 'report-imports' and public.can_touch_import_path(name));

-- Deletable, unlike match-media's, because these are working files rather than
-- published ones: a reporter who uploaded the wrong photo should be able to
-- take it back, and a retention sweep should be able to clear old ones without
-- a service key. There is no UPDATE policy — an object is written once.
create policy report_imports_object_delete on storage.objects
  for delete to authenticated
  using (bucket_id = 'report-imports' and public.can_touch_import_path(name));

commit;
