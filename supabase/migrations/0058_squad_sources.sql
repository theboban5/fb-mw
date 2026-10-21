-- 0058_squad_sources.sql — where a squad list came from.
--
-- WHAT WAS WRONG. A match carries source_ref so a result can be checked later;
-- a squad written on #/squad (0057) carried nothing. It is compiled the way a
-- match report is — a club's /team page, a signing graphic, a WhatsApp from
-- the team manager — and a shirt number or a date of birth that looks wrong in
-- March has to be traceable to whichever of those said it.
--
-- WHAT THIS DOES. squad_sources: one row per team per season, holding free
-- text, written whole by set_squad_sources. ONE FOR THE PAGE, NOT ONE PER
-- PERSON: the sources are shared — one website gave twenty players — and a
-- column on registrations would be the same link typed twenty times, as
-- matches.source_ref was before 0041's shared source box.
--
-- WRITTEN WHOLE, like the score screen's source box rather than 0055's
-- append: the squad screen shows the whole text, so a blank save is a
-- deliberate clear and a correction is an edit. 2,000 characters, not
-- matches.source_ref's 500, because a squad's sources are a list of links and
-- 500 is four of them.
--
-- NEVER RENDERED, AND NOT PUBLIC. Not in the Dataset, and readable only by a
-- signed-in reporter: it is working material, and a source can be a phone
-- number or a person's name as easily as a URL. Same scope as every other
-- squad write, can_name_team via squad_scope (0057).

begin;

create table if not exists public.squad_sources (
  team_id     text not null references public.teams (team_id),
  season_id   text not null references public.seasons (season_id),
  source_ref  text not null default '' check (length(source_ref) <= 2000),
  updated_by  text references public.reporters (reporter_id),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  primary key (team_id, season_id)
);

comment on table public.squad_sources is
  'Where a team''s squad list for a season came from: links or plain words, '
  'one text for the whole squad. Never rendered (0058).';

drop trigger if exists squad_sources_updated_at on public.squad_sources;
create trigger squad_sources_updated_at
  before update on public.squad_sources
  for each row execute function public.set_updated_at();

alter table public.squad_sources enable row level security;
drop policy if exists squad_sources_reporter_read on public.squad_sources;
create policy squad_sources_reporter_read on public.squad_sources
  for select to authenticated
  using (public.current_reporter_id() is not null);

-- This project's default privileges grant new objects to anon (see 0048);
-- RLS already returns it nothing, and this makes it refuse outright.
revoke all on public.squad_sources from anon;


create or replace function public.set_squad_sources(
  p_team_id    text,
  p_season_id  text,
  p_source_ref text
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_season text;
  v_text   text;
begin
  v_season := public.squad_scope(p_team_id, p_season_id);
  -- Trailing spaces off each line and blank lines off the ends; the lines
  -- themselves are the reporter's and stay as typed.
  v_text := btrim(regexp_replace(coalesce(p_source_ref, ''), '[ \t]+(\r?\n)', '\1', 'g'),
                  E' \t\r\n');
  if length(v_text) > 2000 then
    raise exception 'the sources are too long — 2,000 characters at most'
      using errcode = '22023';
  end if;

  insert into public.squad_sources (team_id, season_id, source_ref, updated_by)
  values (p_team_id, v_season, v_text, public.current_reporter_id())
  on conflict (team_id, season_id) do update
    set source_ref = excluded.source_ref,
        updated_by = excluded.updated_by;
  return v_text;
end;
$$;

comment on function public.set_squad_sources(text, text, text) is
  'Replace where a team''s squad list for a season came from. Written whole; '
  'a blank clears it (0058).';

revoke execute on function public.set_squad_sources(text, text, text) from public, anon;
grant execute on function public.set_squad_sources(text, text, text) to authenticated;

commit;
