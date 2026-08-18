-- 0023_match_officials.sql — who refereed it, and who picked the team.
--
-- WHAT WAS MISSING. A Malawian team-sheet graphic carries more than eleven
-- names. Along the bottom it says "Head Coach. E. Kafoteka", and the match-day
-- post that goes with it names the referee and his assistants. 0018 gave the
-- site somewhere to put the players and nowhere to put anyone else, so a
-- reporter copying a graphic had to drop half of what was on it.
--
-- WHAT THIS DOES. Six optional text columns on matches, and one narrow
-- function to write them.
--
--   referee, assistant_referee_1, assistant_referee_2, fourth_official
--   home_coach, away_coach
--
-- COLUMNS ON matches, NOT A TABLE OF OFFICIALS. An officials table would be
-- the right shape if this site tracked referees as people — a referee's
-- profile, matches refereed, cards shown. It does not, and building the join
-- table for a feature nobody asked for would mean every reporter typing a
-- name now has to resolve it to an id, which is exactly the friction that kept
-- team sheets out of the site until 0018. These are free text on the match,
-- the way `venue` was free text before venues existed. If a referee ever needs
-- a page, the strings are already there to seed it from.
--
-- WHY THE COACH IS ON THE MATCH and not on the team: a coach is a fact about
-- a match, not a permanent property of a club. Malawian first-division clubs
-- change coach mid-season routinely, and a column on teams would silently
-- rewrite history every time one was sacked — last season's team sheet would
-- start crediting a man who was not there. nt_matches.coach has said the same
-- thing since 0001; these two are its league counterpart, one per side,
-- because a league match has two real teams where an NT match has ours and a
-- name.
--
-- BLANK IS THE ANSWER almost everywhere, and the site renders nothing for it:
-- no "Referee: —", no empty row. Same graceful degradation as kickoff, venue
-- and the team sheet itself.

begin;

alter table public.matches
  add column if not exists referee              text not null default '',
  add column if not exists assistant_referee_1  text not null default '',
  add column if not exists assistant_referee_2  text not null default '',
  add column if not exists fourth_official      text not null default '',
  add column if not exists home_coach           text not null default '',
  add column if not exists away_coach           text not null default '';

comment on column public.matches.referee is
  'Free text, as reported. This site does not track referees as entities.';
comment on column public.matches.home_coach is
  'The coach IN THIS MATCH. On the match rather than on teams, because clubs '
  'change coach and history must not be rewritten when they do.';


-- ── set_match_officials ──────────────────────────────────────────────────────
-- The fifth narrow door, beside submit_match_report, reschedule_match,
-- set_match_venue and save_lineup. It writes these six columns and nothing
-- else, for the reason 0003 gave and 0015 restated: permission to report a
-- result is not permission to restructure the fixture, and the way that
-- guarantee survives is that no function is ever widened to save writing
-- another one.
--
-- ALL SIX ARGUMENTS ARE SENT EVERY TIME, and a blank one clears the column.
-- The portal submits the whole panel as a unit, so "not sent" and "cleared"
-- would be the same keystroke — a reporter deleting a mistyped referee has to
-- be able to leave the box empty and have that mean empty. Nothing is written
-- unless something actually changed, so a save with nothing edited is a no-op
-- rather than a log entry.

create or replace function public.set_match_officials(
  p_match_id             text,
  p_referee              text default '',
  p_assistant_referee_1  text default '',
  p_assistant_referee_2  text default '',
  p_fourth_official      text default '',
  p_home_coach           text default '',
  p_away_coach           text default ''
)
returns setof public.matches
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_match    public.matches;
  v_new      public.matches;
  v_vals     text[];
  v_one      text;
begin
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  select * into v_match from public.matches m
  where m.match_id = p_match_id for update;
  if not found then
    raise exception 'match not found' using errcode = 'P0002';
  end if;

  if not public.can_report_match(p_match_id) then
    raise exception 'not assigned to this competition' using errcode = '42501';
  end if;

  -- Whitespace collapsed exactly as create_player does it, so "E.  Kafoteka"
  -- and "E. Kafoteka" are one name and the difference cannot survive a save.
  v_vals := array[
    btrim(regexp_replace(coalesce(p_referee, ''), '\s+', ' ', 'g')),
    btrim(regexp_replace(coalesce(p_assistant_referee_1, ''), '\s+', ' ', 'g')),
    btrim(regexp_replace(coalesce(p_assistant_referee_2, ''), '\s+', ' ', 'g')),
    btrim(regexp_replace(coalesce(p_fourth_official, ''), '\s+', ' ', 'g')),
    btrim(regexp_replace(coalesce(p_home_coach, ''), '\s+', ' ', 'g')),
    btrim(regexp_replace(coalesce(p_away_coach, ''), '\s+', ' ', 'g'))
  ];
  foreach v_one in array v_vals loop
    if length(v_one) > 80 then
      raise exception 'that name is too long' using errcode = '22023';
    end if;
  end loop;

  if (v_match.referee, v_match.assistant_referee_1, v_match.assistant_referee_2,
      v_match.fourth_official, v_match.home_coach, v_match.away_coach)
     is not distinct from
     (v_vals[1], v_vals[2], v_vals[3], v_vals[4], v_vals[5], v_vals[6]) then
    return next v_match;
    return;
  end if;

  update public.matches m
  set referee             = v_vals[1],
      assistant_referee_1 = v_vals[2],
      assistant_referee_2 = v_vals[3],
      fourth_official     = v_vals[4],
      home_coach          = v_vals[5],
      away_coach          = v_vals[6],
      updated_at          = now()
  where m.match_id = p_match_id
  returning * into v_new;

  insert into public.match_change_log
    (match_id, changed_by, old_values, new_values)
  values (
    p_match_id, v_reporter,
    jsonb_build_object('referee', v_match.referee,
                       'assistant_referee_1', v_match.assistant_referee_1,
                       'assistant_referee_2', v_match.assistant_referee_2,
                       'fourth_official', v_match.fourth_official,
                       'home_coach', v_match.home_coach,
                       'away_coach', v_match.away_coach),
    jsonb_build_object('referee', v_vals[1],
                       'assistant_referee_1', v_vals[2],
                       'assistant_referee_2', v_vals[3],
                       'fourth_official', v_vals[4],
                       'home_coach', v_vals[5],
                       'away_coach', v_vals[6]));

  return next v_new;
end;
$$;

comment on function public.set_match_officials(text, text, text, text, text, text, text) is
  'Referee, assistants, fourth official and both coaches. Writes those six '
  'columns only; a blank argument clears its column, because the portal sends '
  'the whole panel as one save.';

revoke execute on function
  public.set_match_officials(text, text, text, text, text, text, text)
  from public, anon;
grant execute on function
  public.set_match_officials(text, text, text, text, text, text, text)
  to authenticated;

commit;
