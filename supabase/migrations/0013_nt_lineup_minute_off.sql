-- 0013_nt_lineup_minute_off.sql — derive when a starter came off.
--
-- nt_page renders "↓ 63'" beside a starter from nt_lineups.minute_off, and the
-- pre-Supabase data carried it. The /report team sheet added in 0012 did not:
-- there was no field for it and nothing worked it out, so the first match
-- entered through the portal rendered a starting XI with no substitution
-- arrows at all, while the SUBSTITUTIONS list below it read correctly. Same
-- match, two views, disagreeing — from one missing column.
--
-- THE FIX IS DERIVATION, NOT ANOTHER FIELD. A sub_on row already says
-- everything: "on at 63' for Chikondi Gondwe" means Gondwe came off at 63.
-- Asking a reporter to type 63 twice — once as the arrival, once as the
-- departure — is how two numbers that must agree end up not agreeing. So the
-- departure is computed from the arrival, in the one place that already knows
-- the whole team sheet.
--
-- An explicit minute_off is LEFT ALONE. A player sent off, or withdrawn with
-- nobody replacing them, leaves at a minute no substitution records, and that
-- has to stay sayable.

begin;

create or replace function public.save_nt_lineup(
  p_match_id text,
  p_team_id  text,
  p_rows     jsonb
)
returns setof public.nt_lineups
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_match    public.nt_matches;
  v_names    text[];
  v_starting integer;
  v_row      jsonb;
  v_ord      integer := 0;
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
  if jsonb_typeof(p_rows) <> 'array' then
    raise exception 'the line-up must be a list' using errcode = '22023';
  end if;

  select array_agg(btrim(r->>'player_name')) into v_names
  from jsonb_array_elements(p_rows) r;
  v_names := coalesce(v_names, '{}');

  if exists (select 1 from unnest(v_names) n where coalesce(n, '') = '') then
    raise exception 'every line-up row needs a player name' using errcode = '22023';
  end if;
  if (select count(distinct n) from unnest(v_names) n) <> array_length(v_names, 1) then
    raise exception 'the same player is listed twice' using errcode = '22023';
  end if;

  select count(*) into v_starting
  from jsonb_array_elements(p_rows) r where r->>'role' = 'starting';
  if v_starting > 11 then
    raise exception 'a starting XI is eleven players, not %', v_starting
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
  end loop;

  delete from public.nt_lineups
  where match_id = p_match_id and team_id = p_team_id;

  for v_row in select * from jsonb_array_elements(p_rows) loop
    v_ord := v_ord + 1;
    insert into public.nt_lineups (
      match_id, team_id, player_name, player_id, shirt_number, position,
      role, captain, minute_on, minute_off, replaced_player,
      yellow_card, yellow_red_card, red_card, ord
    ) values (
      p_match_id, p_team_id, btrim(v_row->>'player_name'),
      btrim(coalesce(v_row->>'player_id', '')),
      btrim(coalesce(v_row->>'shirt_number', '')),
      btrim(coalesce(v_row->>'position', '')),
      v_row->>'role',
      coalesce((v_row->>'captain')::boolean, false),
      btrim(coalesce(v_row->>'minute_on', '')),
      btrim(coalesce(v_row->>'minute_off', '')),
      btrim(coalesce(v_row->>'replaced_player', '')),
      coalesce((v_row->>'yellow_card')::boolean, false),
      coalesce((v_row->>'yellow_red_card')::boolean, false),
      coalesce((v_row->>'red_card')::boolean, false),
      v_ord
    );
  end loop;

  -- NEW IN 0013. Whoever a substitute replaced came off when that substitute
  -- came on. Done after the insert rather than per row because it reads across
  -- the sheet, and only where minute_off was left blank so an explicitly
  -- recorded departure — a sending-off, a withdrawal with no replacement —
  -- still wins.
  update public.nt_lineups off_row
  set minute_off = sub.minute_on,
      updated_at = now()
  from public.nt_lineups sub
  where off_row.match_id = p_match_id
    and off_row.team_id  = p_team_id
    and off_row.minute_off = ''
    and sub.match_id = p_match_id
    and sub.team_id  = p_team_id
    and sub.role = 'sub_on'
    and sub.minute_on <> ''
    and sub.replaced_player = off_row.player_name;

  return query
    select * from public.nt_lineups
    where match_id = p_match_id and team_id = p_team_id order by ord;
end;
$$;

revoke execute on function public.save_nt_lineup(text, text, jsonb)
  from public, anon;
grant execute on function public.save_nt_lineup(text, text, jsonb)
  to authenticated;

commit;
