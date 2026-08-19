-- 0028_man_of_the_match.sql — a star beside one name on the team sheet.
--
-- WHAT WAS WRONG. Nothing was wrong; this is a thing the site could not say.
-- Man of the match is announced at almost every game we cover — it is read out
-- at the ground and it is the second line of most Facebook posts after the
-- score — and a reporter had nowhere to put it. The nearest thing was the
-- match notes column (0025), which renders nowhere by design.
--
-- WHAT THIS DOES. One boolean on the team sheet, exactly as `captain` is one
-- boolean on the team sheet. The alternative was a pair of columns on
-- `matches` (a name as reported plus the id it turned out to be, as the
-- officials went in at 0024), and it was rejected for two reasons:
--
--   * the award belongs to a player IN THIS MATCH, which is the sentence a
--     lineups row already is. On matches it would be a second place where a
--     player is named per match, with its own spelling and its own id, able to
--     disagree with the sheet three feet away from it.
--   * every renderer that would draw it already holds the sheet. A star beside
--     the name costs src/lineups.py one badge and the profile table one glyph;
--     via `matches` it would have cost both of them a join.
--
-- THE TRADE, STATED PLAINLY. A man of the match who is not on the team sheet
-- cannot be recorded at all. That is a real loss — most matches on this site
-- have no team sheet — and it is accepted because the alternative asks every
-- reporter to type a name that the sheet, when it exists, already holds
-- correctly. A sheet is the thing to encourage; this makes it worth one more
-- tap rather than working around its absence.
--
-- ONE PER MATCH, ACROSS BOTH SIDES. Unlike the armband, which is one per side.
-- A partial unique index says so, and save_lineup clears the other side before
-- it inserts — so marking the away keeper takes the star off the home striker
-- rather than failing with a constraint error the reporter cannot act on. A
-- sheet saved with nobody starred leaves the other side's star alone: not
-- naming one is not the same as saying there wasn't one.
--
-- Nothing is backfilled. Every existing row is false, which reads as "nobody
-- recorded one", and that is exactly true.

begin;

alter table public.lineups
  add column motm boolean not null default false;
alter table public.nt_lineups
  add column motm boolean not null default false;

comment on column public.lineups.motm is
  'Man of the match. One per match across BOTH sides — see the unique index. '
  'Unlike captain, which is one per side.';
comment on column public.nt_lineups.motm is
  'Man of the match. One per match, across both sides — this tab holds the '
  'opponent''s rows too (they carry ids from no registry and render as text).';

create unique index lineups_one_motm_per_match
  on public.lineups (match_id) where motm;
create unique index nt_lineups_one_motm_per_match
  on public.nt_lineups (match_id) where motm;


-- ── save_lineup ──────────────────────────────────────────────────────────────
-- 0018's function with `motm` carried through, plus the cross-side clear. Two
-- new rules, both of them the kind a CHECK constraint cannot see:
--
--   * one starred name per sheet, refused with a sentence rather than a
--     "duplicate key" from the index;
--   * an unused substitute cannot be man of the match. They did not play. The
--     database is the only place that can say so before it reaches a page.

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

  return query
    select * from public.lineups
    where match_id = p_match_id and team_id = p_team_id order by ord;
end;
$$;

comment on function public.save_lineup(text, text, jsonb) is
  'Replace one side''s whole team sheet for one match. Cross-row rules are '
  'checked before anything is written; minute_off is derived from the '
  'substitutions afterwards; a man of the match named here takes the star off '
  'the other side.';

revoke execute on function public.save_lineup(text, text, jsonb)
  from public, anon;
grant execute on function public.save_lineup(text, text, jsonb)
  to authenticated;


-- ── save_nt_lineup ───────────────────────────────────────────────────────────
-- 0013's function with the same column carried through, cross-side clear and
-- all. nt_lineups holds the OPPONENT's rows as well as ours — they carry ids
-- from no registry and render as plain text — so the two sheets of a national
-- -team match are two calls here exactly as they are on the league side, and
-- one of them starring somebody has to unstar the other.

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
  v_motm     integer;
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
    if coalesce((v_row->>'motm')::boolean, false)
       and v_row->>'role' = 'unused_sub' then
      raise exception
        '% did not play, so cannot be man of the match', v_row->>'player_name'
        using errcode = '22023';
    end if;
  end loop;

  delete from public.nt_lineups
  where match_id = p_match_id and team_id = p_team_id;

  -- One star per match, and this call replaces one side of it. Same rule and
  -- same reason as save_lineup above.
  if v_motm = 1 then
    update public.nt_lineups
    set motm = false, updated_at = now()
    where match_id = p_match_id and team_id <> p_team_id and motm;
  end if;

  for v_row in select * from jsonb_array_elements(p_rows) loop
    v_ord := v_ord + 1;
    insert into public.nt_lineups (
      match_id, team_id, player_name, player_id, shirt_number, position,
      role, captain, motm, minute_on, minute_off, replaced_player,
      yellow_card, yellow_red_card, red_card, ord
    ) values (
      p_match_id, p_team_id, btrim(v_row->>'player_name'),
      btrim(coalesce(v_row->>'player_id', '')),
      btrim(coalesce(v_row->>'shirt_number', '')),
      btrim(coalesce(v_row->>'position', '')),
      v_row->>'role',
      coalesce((v_row->>'captain')::boolean, false),
      coalesce((v_row->>'motm')::boolean, false),
      btrim(coalesce(v_row->>'minute_on', '')),
      btrim(coalesce(v_row->>'minute_off', '')),
      btrim(coalesce(v_row->>'replaced_player', '')),
      coalesce((v_row->>'yellow_card')::boolean, false),
      coalesce((v_row->>'yellow_red_card')::boolean, false),
      coalesce((v_row->>'red_card')::boolean, false),
      v_ord
    );
  end loop;

  -- 0013. Whoever a substitute replaced came off when that substitute came on.
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
