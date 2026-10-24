-- 0059_lineup_registers_squad.sql — a team sheet adds its players to the squad.
--
-- WHAT WAS WRONG. Since 0057 a squad is `registrations`, and the match screen's
-- team-sheet editor offers that squad as chips. But a sheet never wrote back:
-- a player first seen on a sheet — the usual way anyone arrives, because
-- reporters are backfilling FDH Bank Premiership sheets a match at a time off
-- club posts — was named for the match and stayed absent from #/squad. So the
-- squad screen showed less than the portal knew, and anyone who opened it to
-- build a club's list from its sheets had to type every name a second time.
--
-- WHAT THIS DOES.
--
--   save_lineup     0028's function exactly, plus one step after the insert:
--                   every identified player on the saved side gets a
--                   registration for (this team, the MATCH's season). Not the
--                   active season — a sheet from last season belongs to last
--                   season's squad.
--   a backfill      the same insert over every sheet already saved in the
--                   active season.
--
-- ADD ONLY. save_lineup replaces a side whole, and the registration step must
-- not inherit that: a player left off this week's sheet is still in the squad,
-- and only #/squad's Remove takes anyone out. `on conflict do nothing` also
-- means a registration that already exists keeps its shirt.
--
-- THE SHIRT IS LEFT BLANK, deliberately. clubSquad (app.js) lets a registered
-- shirt beat the tally of numbers typed on sheets, because a registered shirt
-- is the club's word. Copying one match's number into it would freeze that
-- match's number — the "one slip poisons every sheet after it" bug the tally
-- was written to cure. Blank, the tally keeps working until someone types the
-- issued number on #/squad.
--
-- THE AUTHORIZATION DOES NOT CHANGE. Whoever may save this sheet may already
-- name these players for this team; save_squad_player's can_name_team is the
-- same question asked from the other screen. Nothing here reaches a page —
-- validate.py checks only the registrations key, and the profile reads
-- position from `players` — so a wrong registration is one Remove on #/squad.
--
-- THE TRADE. A player named once on a sheet by mistake is in that squad until
-- someone removes them. Against that: every sheet entered becomes squad data
-- for the next one, which is the entire point of entering them.

begin;

-- ── save_lineup ──────────────────────────────────────────────────────────────

create or replace function public.save_lineup(
  p_match_id text,
  p_team_id  text,
  p_rows     jsonb
)
returns setof public.lineups
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_match    public.matches;
  v_names    text[];
  v_starting integer;
  v_motm     integer;
  v_row      jsonb;
  v_ord      integer := 0;
begin
  select * into v_match from public.matches
  where match_id = p_match_id for update;
  if not found then
    raise exception 'match not found' using errcode = 'P0002';
  end if;
  if not public.can_report_match(p_match_id) then
    raise exception 'you are not assigned to that match' using errcode = '42501';
  end if;
  -- A sheet belongs to one of the two teams that played. Without this a typo
  -- files eleven players under a club that was not there, and the FK above
  -- would happily accept it.
  if p_team_id is distinct from v_match.home_team_id
     and p_team_id is distinct from v_match.away_team_id then
    raise exception 'that team did not play in this match' using errcode = '22023';
  end if;
  if jsonb_typeof(p_rows) <> 'array' then
    raise exception 'the line-up must be a list' using errcode = '22023';
  end if;

  select array_agg(btrim(r->>'player_name')) into v_names
  from jsonb_array_elements(p_rows) r;
  v_names := coalesce(v_names, '{}');

  if exists (select 1 from unnest(v_names) n where coalesce(n, '') = '') then
    raise exception 'every line-up row needs a player name' using errcode = '22023';
  end if;
  -- The primary key would reject a repeat anyway — said here because
  -- "duplicate key" is not a sentence a reporter can act on.
  if (select count(distinct n) from unnest(v_names) n) <> array_length(v_names, 1) then
    raise exception 'the same player is listed twice' using errcode = '22023';
  end if;

  select count(*) into v_starting
  from jsonb_array_elements(p_rows) r where r->>'role' = 'starting';
  if v_starting > 11 then
    raise exception 'a starting XI is eleven players, not %', v_starting
      using errcode = '22023';
  end if;

  select count(*) into v_motm
  from jsonb_array_elements(p_rows) r
  where coalesce((r->>'motm')::boolean, false);
  if v_motm > 1 then
    raise exception 'only one player can be man of the match, not %', v_motm
      using errcode = '22023';
  end if;

  for v_row in select * from jsonb_array_elements(p_rows) loop
    if coalesce(v_row->>'role', '') not in ('starting', 'sub_on', 'unused_sub') then
      raise exception 'invalid line-up role %', coalesce(v_row->>'role', '')
        using errcode = '22023';
    end if;
    if v_row->>'role' = 'sub_on'
       and btrim(coalesce(v_row->>'minute_on', '')) = '' then
      raise exception 'a substitute who came on needs the minute (%)',
        v_row->>'player_name' using errcode = '22023';
    end if;
    if btrim(coalesce(v_row->>'replaced_player', '')) <> ''
       and not (btrim(v_row->>'replaced_player') = any (v_names)) then
      raise exception
        'replaced player % is not in this line-up', v_row->>'replaced_player'
        using errcode = '22023';
    end if;
    if btrim(coalesce(v_row->>'player_id', '')) <> ''
       and not exists (select 1 from public.players p
                       where p.player_id = btrim(v_row->>'player_id')) then
      raise exception 'unknown player id %', v_row->>'player_id'
        using errcode = '22023';
    end if;
    -- A second yellow IS a red, so a row cannot hold both. Said here as well
    -- as in the CHECK constraint because "violates check constraint
    -- lineups_card_states" is not a sentence a reporter can act on.
    if coalesce((v_row->>'yellow_red_card')::boolean, false)
       and coalesce((v_row->>'red_card')::boolean, false) then
      raise exception
        '% cannot have a second yellow AND a straight red', v_row->>'player_name'
        using errcode = '22023';
    end if;
    if coalesce((v_row->>'motm')::boolean, false)
       and v_row->>'role' = 'unused_sub' then
      raise exception
        '% did not play, so cannot be man of the match', v_row->>'player_name'
        using errcode = '22023';
    end if;
  end loop;

  delete from public.lineups
  where match_id = p_match_id and team_id = p_team_id;

  -- The award is one per MATCH, and this call only replaces one side of it.
  -- Clearing the other side here is what lets a reporter change their mind
  -- across the two sheets; doing it only when this sheet names somebody is
  -- what stops re-saving an unstarred home sheet from quietly removing the
  -- star the away sheet put on.
  if v_motm = 1 then
    update public.lineups
    set motm = false, updated_at = now()
    where match_id = p_match_id and team_id <> p_team_id and motm;
  end if;

  for v_row in select * from jsonb_array_elements(p_rows) loop
    v_ord := v_ord + 1;
    insert into public.lineups (
      match_id, team_id, player_name, player_id, shirt_number, position,
      role, captain, motm, minute_on, minute_off, replaced_player,
      yellow_card, yellow_red_card, red_card, reported_by, ord
    ) values (
      p_match_id, p_team_id, btrim(v_row->>'player_name'),
      nullif(btrim(coalesce(v_row->>'player_id', '')), ''),
      btrim(coalesce(v_row->>'shirt_number', '')),
      upper(btrim(coalesce(v_row->>'position', ''))),
      v_row->>'role',
      coalesce((v_row->>'captain')::boolean, false),
      coalesce((v_row->>'motm')::boolean, false),
      btrim(coalesce(v_row->>'minute_on', '')),
      btrim(coalesce(v_row->>'minute_off', '')),
      btrim(coalesce(v_row->>'replaced_player', '')),
      coalesce((v_row->>'yellow_card')::boolean, false),
      coalesce((v_row->>'yellow_red_card')::boolean, false),
      coalesce((v_row->>'red_card')::boolean, false),
      public.current_reporter_id(),
      v_ord
    );
  end loop;

  -- Same derivation 0013 added to the NT sheet: whoever a substitute replaced
  -- came off when that substitute came on. After the insert because it reads
  -- across the sheet, and only where minute_off was left blank so an
  -- explicitly recorded departure — a sending-off, a withdrawal with nobody
  -- replacing them — still wins.
  update public.lineups off_row
  set minute_off = sub.minute_on,
      updated_at = now()
  from public.lineups sub
  where off_row.match_id = p_match_id
    and off_row.team_id  = p_team_id
    and off_row.minute_off = ''
    and sub.match_id = p_match_id
    and sub.team_id  = p_team_id
    and sub.role = 'sub_on'
    and sub.minute_on <> ''
    and sub.replaced_player = off_row.player_name;

  -- A team sheet is a squad list for the people on it (0059). Everyone the
  -- sheet IDENTIFIES joins this team's registrations for the match's season —
  -- an unused substitute too, because being named in a matchday squad is
  -- being in the squad. Add only: a player dropped from this sheet is still
  -- in the squad, and a row already there keeps its shirt. The shirt is left
  -- blank on purpose — see the header.
  insert into public.registrations (player_id, team_id, season_id, shirt_number)
  select distinct l.player_id, p_team_id, v_match.season_id, ''
  from public.lineups l
  where l.match_id = p_match_id and l.team_id = p_team_id
    and l.player_id is not null and l.player_id <> 'CAF_MW_UNKNOWN'
  on conflict (player_id, team_id, season_id) do nothing;

  return query
    select * from public.lineups
    where match_id = p_match_id and team_id = p_team_id order by ord;
end;
$$;

comment on function public.save_lineup(text, text, jsonb) is
  'Replace one side''s whole team sheet for one match. Cross-row rules are '
  'checked before anything is written; minute_off is derived from the '
  'substitutions afterwards; a man of the match named here takes the star off '
  'the other side; everyone identified on it joins the team''s registrations '
  'for the match''s season (add only, shirt left blank).';

revoke execute on function public.save_lineup(text, text, jsonb)
  from public, anon;
grant execute on function public.save_lineup(text, text, jsonb)
  to authenticated;


-- ── The sheets already saved this season ─────────────────────────────────────
-- Derived from rows already stored, additive, and the same rule as above.

insert into public.registrations (player_id, team_id, season_id, shirt_number)
select distinct l.player_id, l.team_id, m.season_id, ''
from public.lineups l
join public.matches m on m.match_id = l.match_id
join public.seasons s on s.season_id = m.season_id and s.status = 'active'
where l.player_id is not null and l.player_id <> 'CAF_MW_UNKNOWN'
on conflict (player_id, team_id, season_id) do nothing;

commit;
