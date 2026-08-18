-- 0025_match_notes.sql — somewhere to write down what you are not sure about.
--
-- WHAT WAS MISSING. A reporter working off a Facebook graphic is constantly
-- half-sure of something: the second goal might be Phiri or it might be Banda,
-- the graphic lists twelve names, the post says 3–1 and the comments say 3–2.
-- There was nowhere to put any of that. `source_ref` is the closest thing and
-- it is not it — that column answers "where did this come from", one link,
-- written by submit_match_report, and burying a paragraph of doubt in it
-- would wreck the one job it does.
--
-- WHAT THIS DOES. One free-text column and one narrow function. Optional,
-- unlimited in practice (4000 characters), and NEVER RENDERED. Nothing in
-- build.py reads it; there is no markup for it anywhere.
--
-- AND IT STAYS OUT OF THE SNAPSHOT. src/source_supabase.py does not list it,
-- so it is not in data/canonical/matches.csv and does not reach GitHub. That
-- is a deliberate departure from source_ref, which IS in the snapshot: a
-- source link is a fact about the match and belongs in the public audit log,
-- whereas this column is a working note about people — "the coach told me he
-- was not really injured" — and a public git history is the wrong place for
-- one, permanently and irreversibly. The notes live in Postgres, which is
-- backed up, and nowhere else.
--
-- WHO CAN READ IT. The same reporters who can write it. matches has a public
-- read policy covering every column, so anyone with the publishable key can
-- select this one — it is not a secret, it is simply not published. If it ever
-- needs to be, that is a column-level policy and a separate decision; saying
-- so here rather than implying a privacy this does not provide.

begin;

alter table public.matches
  add column if not exists notes text not null default '';

comment on column public.matches.notes is
  'Working notes for reporters: what is uncertain, what to check later. Never '
  'rendered on everyleague.co and deliberately absent from the public data '
  'snapshot. Not a secret — matches has a public read policy — just unpublished.';


-- ── set_match_notes ──────────────────────────────────────────────────────────
-- The sixth narrow door, beside submit_match_report, reschedule_match,
-- set_match_venue, save_lineup and set_match_officials. It writes one column,
-- for the reason 0003 gave and every migration since has restated: permission
-- to report a result is not permission to restructure the fixture, and the way
-- that guarantee survives is that no function is ever widened to save writing
-- another one.
--
-- Not logged to match_change_log. That table is the record of what the SITE
-- said and when it changed; a note has never been on the site. Logging it
-- would put the paragraph the column exists to keep unpublished into a second
-- table, for no reader.

create or replace function public.set_match_notes(
  p_match_id text,
  p_notes    text default ''
)
returns setof public.matches
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_notes    text;
  v_match    public.matches;
begin
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  if not exists (select 1 from public.matches m where m.match_id = p_match_id) then
    raise exception 'match not found' using errcode = 'P0002';
  end if;

  if not public.can_report_match(p_match_id) then
    raise exception 'not assigned to this competition' using errcode = '42501';
  end if;

  -- Trimmed at the ends only. Runs of whitespace are NOT collapsed the way a
  -- name's are: this is prose, and line breaks in it are the reporter's own.
  v_notes := btrim(coalesce(p_notes, ''));
  if length(v_notes) > 4000 then
    raise exception 'that note is too long' using errcode = '22023';
  end if;

  update public.matches m
  set notes = v_notes, updated_at = now()
  where m.match_id = p_match_id
  returning * into v_match;

  return next v_match;
end;
$$;

comment on function public.set_match_notes(text, text) is
  'Reporter working notes for one match. Blank clears them. Never rendered.';

revoke execute on function public.set_match_notes(text, text) from public, anon;
grant execute on function public.set_match_notes(text, text) to authenticated;

commit;
