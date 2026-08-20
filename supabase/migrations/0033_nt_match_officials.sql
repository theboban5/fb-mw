-- 0033_nt_match_officials.sql — the international entry sheet catches up to
-- the club one.
--
-- WHAT WAS MISSING. Backdating the men's national team means filling in a
-- fixture, a score, scorers and a team sheet from a match-day graphic — and
-- the graphic carries more than that. A league match has had a source link
-- since 0008, a referee and both coaches since 0023/0024, and a private
-- working note since 0025. nt_matches has none of them: date, kickoff, venue,
-- city and country can already be corrected through update_nt_fixture (0012),
-- but there was nowhere to record where a result came from, who took charge
-- of it, or what a reporter was not yet sure about.
--
-- WHAT THIS DOES. The same six things, on nt_matches, in the same shape:
--
--   source_ref                              - where the result was read
--   notes                                    - private, never rendered
--   referee, assistant_referee_1,
--     assistant_referee_2, fourth_official   - the match officials
--   coach, opponent_coach                    - one bench each
--
-- `coach` already existed (0001) as free text written by submit_nt_result;
-- this adds the id it resolves to and a name for the other bench,
-- `opponent_coach`, which nt_matches never had — for the same reason
-- nt_goals.team_id and nt_lineups.team_id are not foreign keys: the opponent
-- has no nt_teams row to point at, so their coach is a name and nothing else,
-- the way matches.away_coach would be if away_team_id were not real either.
--
-- ONE REGISTRY, NOT A SECOND ONE. `officials` (0024) already holds referees
-- and coaches as people, kind-separated and searched by
-- search_officials(term, kind). Nothing here is national-team-specific: an
-- international referee and a league one who happen to share a name are one
-- row, exactly as 0024 intended.
--
-- source_ref JOINS THE SNAPSHOT; notes DOES NOT. Same split as matches
-- (0025's argument, restated): a source link is a fact about the match and
-- belongs in the public audit log, a reporter's private doubt about a person
-- does not. src/source_supabase.py is updated in the same commit to keep
-- that true for both.
--
-- WHAT THIS DOES NOT DO. No backfill, no rendering, no change to
-- match_change_log (nt_matches was never logged there — see 0012). The men's
-- national team has no page yet: src/nt.py builds only
-- SCORCHERS = "MW_W". This is entirely about giving the reporter portal
-- somewhere to put what a match-day graphic says, ready for whenever that
-- page exists.

begin;

alter table public.nt_matches
  add column if not exists source_ref            text not null default '',
  add column if not exists notes                  text not null default '',
  add column if not exists referee                text not null default '',
  add column if not exists assistant_referee_1    text not null default '',
  add column if not exists assistant_referee_2    text not null default '',
  add column if not exists fourth_official        text not null default '',
  add column if not exists opponent_coach         text not null default '',
  add column if not exists referee_id             text references public.officials (official_id),
  add column if not exists assistant_referee_1_id text references public.officials (official_id),
  add column if not exists assistant_referee_2_id text references public.officials (official_id),
  add column if not exists fourth_official_id     text references public.officials (official_id),
  add column if not exists coach_id               text references public.officials (official_id),
  add column if not exists opponent_coach_id      text references public.officials (official_id);

comment on column public.nt_matches.notes is
  'Working notes for reporters: what is uncertain, what to check later. Never '
  'rendered and deliberately absent from the public data snapshot, exactly '
  'like matches.notes (0025).';
comment on column public.nt_matches.coach is
  'Our own bench. Free text, as reported; coach_id is who that turned out to '
  'be.';
comment on column public.nt_matches.opponent_coach is
  'Their bench. Free text with nothing to resolve the SIDE against, for the '
  'same reason nt_goals.team_id is not a foreign key: the opponent has no '
  'nt_teams row.';

create index if not exists nt_matches_referee_id_idx
  on public.nt_matches (referee_id) where referee_id is not null;
create index if not exists nt_matches_assistant_referee_1_id_idx
  on public.nt_matches (assistant_referee_1_id) where assistant_referee_1_id is not null;
create index if not exists nt_matches_assistant_referee_2_id_idx
  on public.nt_matches (assistant_referee_2_id) where assistant_referee_2_id is not null;
create index if not exists nt_matches_fourth_official_id_idx
  on public.nt_matches (fourth_official_id) where fourth_official_id is not null;
create index if not exists nt_matches_coach_id_idx
  on public.nt_matches (coach_id) where coach_id is not null;
create index if not exists nt_matches_opponent_coach_id_idx
  on public.nt_matches (opponent_coach_id) where opponent_coach_id is not null;


-- ── submit_nt_result gains p_source_ref ───────────────────────────────────────
-- Same blank-keeps-existing rule submit_match_report has had since 0008: a
-- correction resubmitted without retyping the link must not erase it. The
-- parameter list is growing, so the old 8-argument overload is dropped first
-- — the same step 0024 took with set_match_officials — or Postgres would keep
-- both instead of replacing one.

drop function if exists public.submit_nt_result(
  text, integer, integer, text, boolean, text, text, text);

create or replace function public.submit_nt_result(
  p_match_id          text,
  p_team_score        integer,
  p_opponent_score    integer,
  p_status            text default 'played',
  p_extra_time        boolean default false,
  p_penalty_shootout  text default '',
  p_extra_time_result text default '',
  p_coach             text default null,
  p_source_ref        text default ''
)
returns setof public.nt_matches
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_match  public.nt_matches;
  v_ours   integer;
  v_them   integer;
  v_source text;
begin
  select * into v_match from public.nt_matches
  where match_id = p_match_id for update;
  if not found then
    raise exception 'match not found' using errcode = 'P0002';
  end if;
  if not public.can_edit_nt(v_match.team_code) then
    raise exception 'you are not assigned to that national team'
      using errcode = '42501';
  end if;

  if p_status not in ('scheduled', 'played', 'awarded') then
    raise exception 'invalid status %', p_status using errcode = '22023';
  end if;

  -- 'scheduled' means "no result yet", so it clears the score rather than
  -- being rejected: a result entered against the wrong match has to be
  -- retractable, and the table's own constraint forbids keeping both.
  if p_status = 'scheduled' then
    v_ours := null;
    v_them := null;
  else
    if p_team_score is null or p_opponent_score is null then
      raise exception 'a played match needs both scores' using errcode = '22023';
    end if;
    if p_team_score < 0 or p_opponent_score < 0
       or p_team_score > 99 or p_opponent_score > 99 then
      raise exception 'invalid score' using errcode = '22023';
    end if;
    v_ours := p_team_score;
    v_them := p_opponent_score;
  end if;

  -- validate.py check 9: goal rows per side never exceed that side's score.
  if v_ours is not null then
    if (select count(*) from public.nt_goals g
        where g.match_id = p_match_id and g.team_id = v_match.team_code) > v_ours
    then
      raise exception
        'there are already more scorers than that for %', v_match.team_code
        using errcode = '22023';
    end if;
    if (select count(*) from public.nt_goals g
        where g.match_id = p_match_id and g.team_id <> v_match.team_code) > v_them
    then
      raise exception 'there are already more scorers than that for the opponent'
        using errcode = '22023';
    end if;
  elsif exists (select 1 from public.nt_goals g where g.match_id = p_match_id) then
    raise exception
      'remove the scorers before setting this match back to not played'
      using errcode = '22023';
  end if;

  -- Free text, length-capped so a paste accident cannot put a megabyte in the
  -- row. Blank leaves whatever was already recorded: a correction submitted
  -- without re-typing the link must not erase the link (0029's rule).
  v_source := left(btrim(coalesce(p_source_ref, '')), 500);
  if v_source = '' then
    v_source := v_match.source_ref;
  end if;

  update public.nt_matches set
    team_score        = v_ours,
    opponent_score    = v_them,
    status            = p_status,
    extra_time        = coalesce(p_extra_time, false),
    penalty_shootout  = btrim(coalesce(p_penalty_shootout, '')),
    extra_time_result = btrim(coalesce(p_extra_time_result, '')),
    coach             = btrim(coalesce(p_coach, coach)),
    source_ref        = v_source,
    updated_at        = now()
  where match_id = p_match_id
  returning * into v_match;
  return next v_match;
end;
$$;

revoke execute on function public.submit_nt_result(
  text, integer, integer, text, boolean, text, text, text, text) from public, anon;
grant execute on function public.submit_nt_result(
  text, integer, integer, text, boolean, text, text, text, text) to authenticated;


-- ── set_nt_match_officials ────────────────────────────────────────────────────
-- set_match_officials (0023, widened by 0024), for nt_matches. All twelve
-- values are sent every time and a blank one clears its column — the portal
-- submits the whole panel as one save, so "not sent" and "cleared" would
-- otherwise be the same keystroke. An id is kept only if it names a real
-- officials row and a name sits beside it; anything else is silently dropped
-- and the name saves as plain text, exactly as it would have before this
-- migration existed.
--
-- No match_change_log entry: nt_matches writes were never logged there (0012)
-- — the table's foreign key is to matches, not this one.

create or replace function public.set_nt_match_officials(
  p_match_id                text,
  p_referee                 text default '',
  p_assistant_referee_1     text default '',
  p_assistant_referee_2     text default '',
  p_fourth_official         text default '',
  p_coach                   text default '',
  p_opponent_coach          text default '',
  p_referee_id              text default '',
  p_assistant_referee_1_id  text default '',
  p_assistant_referee_2_id  text default '',
  p_fourth_official_id      text default '',
  p_coach_id                text default '',
  p_opponent_coach_id       text default ''
)
returns setof public.nt_matches
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_match public.nt_matches;
  v_vals  text[];
  v_ids   text[];
  v_one   text;
  v_i     int;
begin
  select * into v_match from public.nt_matches
  where match_id = p_match_id for update;
  if not found then
    raise exception 'match not found' using errcode = 'P0002';
  end if;
  if not public.can_edit_nt(v_match.team_code) then
    raise exception 'you are not assigned to that national team'
      using errcode = '42501';
  end if;

  v_vals := array[
    btrim(regexp_replace(coalesce(p_referee, ''), '\s+', ' ', 'g')),
    btrim(regexp_replace(coalesce(p_assistant_referee_1, ''), '\s+', ' ', 'g')),
    btrim(regexp_replace(coalesce(p_assistant_referee_2, ''), '\s+', ' ', 'g')),
    btrim(regexp_replace(coalesce(p_fourth_official, ''), '\s+', ' ', 'g')),
    btrim(regexp_replace(coalesce(p_coach, ''), '\s+', ' ', 'g')),
    btrim(regexp_replace(coalesce(p_opponent_coach, ''), '\s+', ' ', 'g'))
  ];
  foreach v_one in array v_vals loop
    if length(v_one) > 80 then
      raise exception 'that name is too long' using errcode = '22023';
    end if;
  end loop;

  v_ids := array[
    btrim(coalesce(p_referee_id, '')),
    btrim(coalesce(p_assistant_referee_1_id, '')),
    btrim(coalesce(p_assistant_referee_2_id, '')),
    btrim(coalesce(p_fourth_official_id, '')),
    btrim(coalesce(p_coach_id, '')),
    btrim(coalesce(p_opponent_coach_id, ''))
  ];
  for v_i in 1..6 loop
    if v_ids[v_i] <> '' and (
         v_vals[v_i] = ''
         or not exists (select 1 from public.officials o
                        where o.official_id = v_ids[v_i])) then
      v_ids[v_i] := '';
    end if;
  end loop;

  if (v_match.referee, v_match.assistant_referee_1, v_match.assistant_referee_2,
      v_match.fourth_official, v_match.coach, v_match.opponent_coach,
      v_match.referee_id, v_match.assistant_referee_1_id,
      v_match.assistant_referee_2_id, v_match.fourth_official_id,
      v_match.coach_id, v_match.opponent_coach_id)
     is not distinct from
     (v_vals[1], v_vals[2], v_vals[3], v_vals[4], v_vals[5], v_vals[6],
      nullif(v_ids[1], ''), nullif(v_ids[2], ''), nullif(v_ids[3], ''),
      nullif(v_ids[4], ''), nullif(v_ids[5], ''), nullif(v_ids[6], '')) then
    return next v_match;
    return;
  end if;

  update public.nt_matches m
  set referee                = v_vals[1],
      assistant_referee_1    = v_vals[2],
      assistant_referee_2    = v_vals[3],
      fourth_official        = v_vals[4],
      coach                  = v_vals[5],
      opponent_coach         = v_vals[6],
      referee_id             = nullif(v_ids[1], ''),
      assistant_referee_1_id = nullif(v_ids[2], ''),
      assistant_referee_2_id = nullif(v_ids[3], ''),
      fourth_official_id     = nullif(v_ids[4], ''),
      coach_id               = nullif(v_ids[5], ''),
      opponent_coach_id      = nullif(v_ids[6], ''),
      updated_at             = now()
  where m.match_id = p_match_id
  returning * into v_match;

  return next v_match;
end;
$$;

comment on function public.set_nt_match_officials(
  text, text, text, text, text, text, text,
  text, text, text, text, text, text) is
  'Referee, assistants, fourth official and both benches for an nt_matches '
  'row — the name as reported and, where the reporter tapped one, the '
  'registry id it belongs to. Writes those twelve columns only; a blank '
  'argument clears its column, because the portal sends the whole panel as '
  'one save.';

revoke execute on function public.set_nt_match_officials(
  text, text, text, text, text, text, text,
  text, text, text, text, text, text) from public, anon;
grant execute on function public.set_nt_match_officials(
  text, text, text, text, text, text, text,
  text, text, text, text, text, text) to authenticated;


-- ── set_nt_match_notes ────────────────────────────────────────────────────────
-- set_match_notes (0025), for nt_matches. Not logged to match_change_log for
-- the same reason 0025 gave: that table is the record of what the site said,
-- and a note has never been on the site.

create or replace function public.set_nt_match_notes(
  p_match_id text,
  p_notes    text default ''
)
returns setof public.nt_matches
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_notes text;
  v_match public.nt_matches;
begin
  select * into v_match from public.nt_matches where match_id = p_match_id;
  if not found then
    raise exception 'match not found' using errcode = 'P0002';
  end if;
  if not public.can_edit_nt(v_match.team_code) then
    raise exception 'you are not assigned to that national team'
      using errcode = '42501';
  end if;

  -- Trimmed at the ends only. Runs of whitespace are NOT collapsed the way a
  -- name's are: this is prose, and line breaks in it are the reporter's own.
  v_notes := btrim(coalesce(p_notes, ''));
  if length(v_notes) > 4000 then
    raise exception 'that note is too long' using errcode = '22023';
  end if;

  update public.nt_matches m
  set notes = v_notes, updated_at = now()
  where m.match_id = p_match_id
  returning * into v_match;

  return next v_match;
end;
$$;

comment on function public.set_nt_match_notes(text, text) is
  'Reporter working notes for one nt_matches row. Blank clears them. Never '
  'rendered.';

revoke execute on function public.set_nt_match_notes(text, text) from public, anon;
grant execute on function public.set_nt_match_notes(text, text) to authenticated;

commit;
