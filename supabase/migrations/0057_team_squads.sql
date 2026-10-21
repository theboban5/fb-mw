-- 0057_team_squads.sql — writing down a team's squad and its staff.
--
-- WHAT WAS WRONG. A club publishes its whole squad — Karonga United's site lists
-- every player and seven staff, from the head coach to the media officer — and
-- the portal had nowhere to put it. The only way a player became attached to a
-- team was by being named on a team sheet or scoring a goal, so a squad that
-- arrived as a list on a Tuesday had to wait for Saturday's sheet to be typed
-- at all, and the staff behind the head coach had no place anywhere. clubSquad
-- in app.js said so in as many words: "there is no squad-registration screen
-- for club football".
--
-- THE TABLE WAS ALREADY THERE. `registrations` (player, team, season, shirt) has
-- been in the schema since 0001, is read into the Dataset, is repointed by
-- merge_players (0022) — and has never had a row, because nothing could write
-- one. A squad list IS that table. So this migration adds no squad table; it
-- adds the way in.
--
-- WHAT THIS DOES.
--
--   players.birth_year, players.photo_path   the two person facts that had no
--                       column. dob and position already existed.
--   officials.photo_path                     the same, for staff.
--   team_staff          (team, season, official, role): who is on the bench
--                       besides the eleven, against the officials registry.
--   squad-media         a public bucket for the photos.
--   save_squad_player / remove_squad_player   one row at a time.
--   save_squad_staff  / remove_squad_staff    the same for staff.
--   search_players      also reads registrations for its club hints (0034).
--   merge_officials     also repoints team_staff, or the FK would refuse it.
--
-- ONE ROW AT A TIME, BECAUSE A SQUAD ARRIVES A FEW NAMES AT A TIME. Sometimes it
-- is the whole list off a club website; more often it is three names off a
-- signing announcement. So there is no "save the squad" call that replaces a
-- team's list whole, the way save_lineup replaces a sheet — that shape would
-- make "add these three" mean "and forget the other twenty-five". Every call
-- adds or updates one person and touches nobody else.
--
-- A FACT GOES WHERE IT IS TRUE. The shirt number is a fact about this squad in
-- this season — numbers are reissued every year — so it lives on the
-- registration. Position, date of birth and photo are facts about the person,
-- so they live on `players`, where the profile page already reads position as
-- its fallback (src/hubs.py). That is also why every person field below
-- follows one rule: NULL LEAVES IT ALONE, '' CLEARS IT. Adding an existing
-- player to a squad from a pasted list must not wipe the date of birth another
-- reporter entered, and correcting a wrong one must still be possible.
--
-- AN AGE IS NOT A DATE OF BIRTH, so it is not stored as one. A squad list that
-- says "23" says the person was born in one of two calendar years, and
-- inventing 1 January of either would put a false fact on a profile. So an age
-- becomes `birth_year` — current year minus age, which is right for anyone whose
-- birthday has already passed this year and one year late for anyone whose has
-- not. That error is the trade, stated: a year that is sometimes one out,
-- against a date that is always invented. A full date sets birth_year from
-- itself, so the two never disagree.
--
-- STAFF ARE OFFICIALS OF KIND 'coach'. The registry has two kinds (0024):
-- referees, who belong to no side, and coaches, who belong to one. An
-- assistant coach, a goalkeeper trainer, a team doctor and a media officer
-- are all the second sort of person — on a team's side of the touchline — so
-- `coach` here means "a team's bench", and the job is the free-text `role`.
-- That keeps a head coach entered on the squad screen the SAME id the match
-- screen's coach picker finds on Saturday, which is the point of the registry.
-- The trade: the coach picker can now offer a physio by name. It only ever
-- offers names matching what was typed, so that costs a wrong suggestion, not
-- a wrong row.
--
-- THE SCOPE IS THE AUTHORIZATION, as in 0046: can_name_team, so a reporter may
-- write the squad of a team they are assigned to report, and no other.
--
-- WHAT RENDERS. Position does, on a profile with no team sheet behind it — so
-- the portal nudges a rebuild after a save. Nothing else here reaches a page
-- yet: registrations are read into the Dataset and nothing draws them,
-- team_staff is not read at all, and the photos are stored for a squad page
-- that does not exist. Storing them now is what lets that page be built
-- against real data rather than an empty table.
--
-- NOTHING DELETES A PHOTO. The bucket has no delete policy, as trending-media
-- has none for admins either: a removed row leaves its object, and an object
-- costs nothing a reader pays for.

begin;

-- ── person columns ───────────────────────────────────────────────────────────

alter table public.players
  add column if not exists birth_year smallint
    check (birth_year is null or birth_year between 1940 and 2100),
  add column if not exists photo_path text not null default '';

comment on column public.players.birth_year is
  'Year of birth. Set from dob when dob is known; otherwise from an age a '
  'squad list stated, which may be one year late (0057).';
comment on column public.players.photo_path is
  'Object name in the public squad-media bucket, or blank.';

alter table public.officials
  add column if not exists photo_path text not null default '';

comment on column public.officials.photo_path is
  'Object name in the public squad-media bucket, or blank.';


-- ── team_staff ───────────────────────────────────────────────────────────────

create table if not exists public.team_staff (
  team_id      text not null references public.teams (team_id),
  season_id    text not null references public.seasons (season_id),
  official_id  text not null references public.officials (official_id),
  -- Free text, because a club's titles are its own: "Second Assistant Coach",
  -- "Media & Communication Officer". The portal offers the common ones as
  -- chips. Blank is allowed — a name with no title is still a name.
  role         text not null default '' check (length(role) <= 60),
  reported_by  text references public.reporters (reporter_id),
  ord          bigint generated by default as identity,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  primary key (team_id, season_id, official_id)
);

comment on table public.team_staff is
  'A team''s staff in one season: coaches, trainers, doctors, managers. '
  'Officials of kind coach, with the job as free text (0057).';

create index if not exists team_staff_official_idx
  on public.team_staff (official_id);

drop trigger if exists team_staff_updated_at on public.team_staff;
create trigger team_staff_updated_at
  before update on public.team_staff
  for each row execute function public.set_updated_at();

alter table public.team_staff enable row level security;
drop policy if exists team_staff_public_read on public.team_staff;
create policy team_staff_public_read on public.team_staff
  for select to anon, authenticated using (true);

-- registrations had a public read policy and nothing else since 0001. It still
-- has nothing else: every write goes through the functions below.
drop trigger if exists registrations_updated_at on public.registrations;
create trigger registrations_updated_at
  before update on public.registrations
  for each row execute function public.set_updated_at();


-- ── squad_scope ──────────────────────────────────────────────────────────────
-- The checks every write below shares: a live reporter, a team they may name,
-- a real season (the active one when none is sent). Returns the season.

create or replace function public.squad_scope(
  p_team_id   text,
  p_season_id text
)
returns text
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_season text;
begin
  if public.current_reporter_id() is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;
  if not exists (select 1 from public.teams t where t.team_id = p_team_id) then
    raise exception 'team % not found', p_team_id using errcode = 'P0002';
  end if;
  if not public.can_name_team(p_team_id) then
    raise exception 'you do not report a competition this team plays in'
      using errcode = '42501';
  end if;

  if nullif(btrim(coalesce(p_season_id, '')), '') is null then
    select s.season_id into v_season
    from public.seasons s where s.status = 'active';
  else
    select s.season_id into v_season
    from public.seasons s where s.season_id = p_season_id;
  end if;
  if v_season is null then
    raise exception 'season not found' using errcode = 'P0002';
  end if;
  return v_season;
end;
$$;

revoke execute on function public.squad_scope(text, text)
  from public, anon, authenticated;


-- ── squad_photo_path ─────────────────────────────────────────────────────────
-- A photo path is an object name the client uploaded first. All this checks is
-- that it is one of ours in shape — under the folder for its kind of person,
-- no traversal — because the column is later turned into a URL.

create or replace function public.squad_photo_path(p_path text, p_folder text)
returns text
language plpgsql
immutable
set search_path = ''
as $$
declare
  v text := btrim(coalesce(p_path, ''));
begin
  if v = '' then return ''; end if;
  if length(v) > 200 or v like '%..%' or v !~ ('^' || p_folder || '/[A-Za-z0-9_./-]+$') then
    raise exception 'that photo did not upload properly; try it again'
      using errcode = '22023';
  end if;
  return v;
end;
$$;

revoke execute on function public.squad_photo_path(text, text)
  from public, anon, authenticated;


-- ── save_squad_player ────────────────────────────────────────────────────────
-- Adds a player to a squad, or updates their row. NULL leaves a field alone and
-- '' clears it (see the header); a brand-new registration starts blank.

create or replace function public.save_squad_player(
  p_team_id      text,
  p_season_id    text,
  p_player_id    text,
  p_shirt_number text default null,
  p_position     text default null,
  p_dob          text default null,
  p_birth_year   text default null,
  p_photo_path   text default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_season   text;
  v_shirt    text;
  v_position text;
  v_dob      date;
  v_year     integer;
  v_player   public.players;
  v_reg      public.registrations;
begin
  v_season := public.squad_scope(p_team_id, p_season_id);

  select * into v_player from public.players p
  where p.player_id = p_player_id and p.player_id <> 'CAF_MW_UNKNOWN'
  for update;
  if not found then
    raise exception 'player % not found', p_player_id using errcode = 'P0002';
  end if;

  if p_shirt_number is not null then
    v_shirt := btrim(p_shirt_number);
    if v_shirt !~ '^[0-9]{0,3}$' then
      raise exception 'a shirt number is up to three digits' using errcode = '22023';
    end if;
  end if;

  if p_position is not null then
    v_position := upper(btrim(p_position));
    if v_position not in ('', 'GK', 'DF', 'MF', 'FW') then
      raise exception 'position must be GK, DF, MF or FW' using errcode = '22023';
    end if;
    update public.players p set position = v_position
    where p.player_id = p_player_id;
  end if;

  -- A date wins over a year, and sets the year from itself, so the two columns
  -- can never disagree. Clearing the date leaves the year: "born in 2003" is
  -- still true after "born 4 May 2003" turns out to be the wrong day.
  if p_dob is not null and btrim(p_dob) <> '' then
    begin
      v_dob := btrim(p_dob)::date;
    exception when others then
      raise exception 'that date of birth is not a date' using errcode = '22023';
    end;
    if v_dob > current_date or extract(year from v_dob) < 1940 then
      raise exception 'that date of birth is not possible' using errcode = '22023';
    end if;
    update public.players p
    set dob = v_dob, birth_year = extract(year from v_dob)::smallint
    where p.player_id = p_player_id;
  else
    if p_dob is not null then
      update public.players p set dob = null where p.player_id = p_player_id;
    end if;
    if p_birth_year is not null then
      if btrim(p_birth_year) = '' then
        v_year := null;
      elsif btrim(p_birth_year) ~ '^[0-9]{4}$' then
        v_year := btrim(p_birth_year)::integer;
        if v_year < 1940 or v_year > extract(year from current_date) then
          raise exception 'that age is not possible' using errcode = '22023';
        end if;
      else
        raise exception 'a birth year is four digits' using errcode = '22023';
      end if;
      -- A year that contradicts a date already held is refused rather than
      -- quietly dropping the date: an age off a graphic is the weaker fact.
      if v_year is not null and v_player.dob is not null and p_dob is null
         and extract(year from v_player.dob) <> v_year then
        raise exception 'this player''s date of birth is already recorded as %',
          to_char(v_player.dob, 'DD Mon YYYY') using errcode = '22023';
      end if;
      update public.players p set birth_year = v_year
      where p.player_id = p_player_id;
    end if;
  end if;

  if p_photo_path is not null then
    update public.players p
    set photo_path = public.squad_photo_path(p_photo_path, 'players')
    where p.player_id = p_player_id;
  end if;

  insert into public.registrations (player_id, team_id, season_id, shirt_number)
  values (p_player_id, p_team_id, v_season, coalesce(v_shirt, ''))
  on conflict (player_id, team_id, season_id) do update
    set shirt_number = coalesce(v_shirt, public.registrations.shirt_number)
  returning * into v_reg;

  select * into v_player from public.players p where p.player_id = p_player_id;

  return jsonb_build_object(
    'player_id', v_player.player_id,
    'full_name', v_player.full_name,
    'known_as', v_player.known_as,
    'team_id', v_reg.team_id,
    'season_id', v_reg.season_id,
    'shirt_number', v_reg.shirt_number,
    'position', v_player.position,
    'dob', v_player.dob,
    'birth_year', v_player.birth_year,
    'photo_path', v_player.photo_path);
end;
$$;

comment on function public.save_squad_player(text, text, text, text, text, text, text, text) is
  'Add a player to a team''s squad for a season, or update them. NULL leaves '
  'a field alone, '''' clears it. Shirt is the squad''s; the rest is the '
  'person''s (0057).';

revoke execute on function public.save_squad_player(text, text, text, text, text, text, text, text)
  from public, anon;
grant execute on function public.save_squad_player(text, text, text, text, text, text, text, text)
  to authenticated;


-- ── remove_squad_player ──────────────────────────────────────────────────────
-- Takes the registration only. The player, their goals and their team sheets
-- are untouched: leaving a squad list is not leaving the record.

create or replace function public.remove_squad_player(
  p_team_id   text,
  p_season_id text,
  p_player_id text
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_season text;
begin
  v_season := public.squad_scope(p_team_id, p_season_id);
  delete from public.registrations r
  where r.team_id = p_team_id and r.season_id = v_season
    and r.player_id = p_player_id;
  return found;
end;
$$;

revoke execute on function public.remove_squad_player(text, text, text) from public, anon;
grant execute on function public.remove_squad_player(text, text, text) to authenticated;


-- ── save_squad_staff ─────────────────────────────────────────────────────────

create or replace function public.save_squad_staff(
  p_team_id     text,
  p_season_id   text,
  p_official_id text,
  p_role        text default null,
  p_photo_path  text default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_season   text;
  v_role     text;
  v_official public.officials;
  v_row      public.team_staff;
begin
  v_season := public.squad_scope(p_team_id, p_season_id);

  select * into v_official from public.officials o
  where o.official_id = p_official_id for update;
  if not found then
    raise exception 'official % not found', p_official_id using errcode = 'P0002';
  end if;
  -- A referee on a team's staff is the wrong pool, not a new job: the portal
  -- only ever creates staff as kind coach, so this is a mis-tap upstream.
  if v_official.kind <> 'coach' then
    raise exception '% is in the referees'' list, not a team''s',
      v_official.full_name using errcode = '22023';
  end if;

  if p_role is not null then
    v_role := btrim(regexp_replace(p_role, '\s+', ' ', 'g'));
    if length(v_role) > 60 then
      raise exception 'that role is too long' using errcode = '22023';
    end if;
  end if;

  if p_photo_path is not null then
    update public.officials o
    set photo_path = public.squad_photo_path(p_photo_path, 'staff')
    where o.official_id = p_official_id;
  end if;

  insert into public.team_staff (team_id, season_id, official_id, role, reported_by)
  values (p_team_id, v_season, p_official_id, coalesce(v_role, ''),
          public.current_reporter_id())
  on conflict (team_id, season_id, official_id) do update
    set role = coalesce(v_role, public.team_staff.role)
  returning * into v_row;

  select * into v_official from public.officials o where o.official_id = p_official_id;

  return jsonb_build_object(
    'official_id', v_official.official_id,
    'full_name', v_official.full_name,
    'known_as', v_official.known_as,
    'team_id', v_row.team_id,
    'season_id', v_row.season_id,
    'role', v_row.role,
    'photo_path', v_official.photo_path);
end;
$$;

comment on function public.save_squad_staff(text, text, text, text, text) is
  'Add a person to a team''s staff for a season, or update their role or '
  'photo. NULL leaves a field alone (0057).';

revoke execute on function public.save_squad_staff(text, text, text, text, text) from public, anon;
grant execute on function public.save_squad_staff(text, text, text, text, text) to authenticated;


create or replace function public.remove_squad_staff(
  p_team_id     text,
  p_season_id   text,
  p_official_id text
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_season text;
begin
  v_season := public.squad_scope(p_team_id, p_season_id);
  delete from public.team_staff s
  where s.team_id = p_team_id and s.season_id = v_season
    and s.official_id = p_official_id;
  return found;
end;
$$;

revoke execute on function public.remove_squad_staff(text, text, text) from public, anon;
grant execute on function public.remove_squad_staff(text, text, text) to authenticated;


-- ── squad-media ──────────────────────────────────────────────────────────────
-- Public for the reason match-media and trending-media are: a page has to be
-- able to <img src> it. Any live reporter may upload, because a photo path
-- reaches a row only through save_squad_player/save_squad_staff, which carry
-- the team check; an upload on its own puts nothing on any page. 2 MB, because
-- the portal shrinks a head shot to 800px before it leaves the phone.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('squad-media', 'squad-media', true, 2097152,
        array['image/jpeg', 'image/png', 'image/webp'])
on conflict (id) do update
  set public             = excluded.public,
      file_size_limit    = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists squad_media_read on storage.objects;
drop policy if exists squad_media_insert on storage.objects;

create policy squad_media_read on storage.objects
  for select to anon, authenticated
  using (bucket_id = 'squad-media');

create policy squad_media_insert on storage.objects
  for insert to authenticated
  with check (bucket_id = 'squad-media'
              and public.current_reporter_id() is not null
              and (name like 'players/%' or name like 'staff/%'));


-- ── search_players: a squad list is evidence of a club too ────────────────────
-- 0034's club hints came from lineups and goals, "who has actually worn this
-- shirt". A registration is a club saying so before the first match, and it is
-- exactly the case the hint exists for: two Gift Phiris, one on a squad list
-- typed this week. Unchanged but for the fourth branch; the date of a
-- registration is when it was written, which is the honest "most recent".

create or replace function public.search_players(p_term text)
returns table (
  player_id text,
  full_name text,
  known_as  text,
  matched   text,
  score     integer,
  teams     text,
  team_ids  text[]
)
language sql
stable
security definer
set search_path = ''
as $$
  with term as (
    select btrim(regexp_replace(coalesce(p_term, ''), '\s+', ' ', 'g')) as t
  ),
  parts as (
    select t,
           lower(t) as lt,
           lower(split_part(t, ' ', greatest(1, array_length(string_to_array(t, ' '), 1))))
             as surname,
           lower(left(t, 1)) as initial
    from term
  ),
  candidates as (
    select p.player_id, p.full_name, p.known_as,
           lower(coalesce(nullif(p.known_as, ''), p.full_name)) as label,
           lower(split_part(
             coalesce(nullif(p.known_as, ''), p.full_name), ' ',
             greatest(1, array_length(string_to_array(
               coalesce(nullif(p.known_as, ''), p.full_name), ' '), 1)))) as surname,
           lower(left(coalesce(nullif(p.known_as, ''), p.full_name), 1)) as initial
    from public.players p
    where p.player_id <> 'CAF_MW_UNKNOWN'
  ),
  hits as (
    select c.player_id, c.full_name, c.known_as, '' as matched,
           case when c.label = x.lt then 100
                when c.label like x.lt || '%' then 80
                else 60 end as score
    from candidates c, parts x
    where x.lt <> '' and (lower(c.full_name) like '%' || x.lt || '%'
                          or lower(c.known_as) like '%' || x.lt || '%')
    union all
    select c.player_id, c.full_name, c.known_as, a.alias_text as matched, 50
    from candidates c
    join public.aliases a
      on a.entity_type = 'player' and a.entity_id = c.player_id
    cross join parts x
    where x.lt <> '' and lower(a.alias_text) like '%' || x.lt || '%'
    union all
    select c.player_id, c.full_name, c.known_as, '' as matched, 30
    from candidates c, parts x
    where length(x.surname) >= 3
      and c.surname = x.surname
      and (x.initial = c.initial
           or x.surname = x.lt
           or c.surname = c.label)
  ),
  ranked as (
    select h.player_id, h.full_name, h.known_as,
           (array_agg(h.matched order by h.score desc))[1] as matched,
           max(h.score)::integer as score
    from hits h
    group by h.player_id, h.full_name, h.known_as
    order by max(h.score) desc, h.full_name
    limit 12
  )
  select r.player_id, r.full_name, r.known_as, r.matched, r.score,
         coalesce(s.teams, '') as teams,
         coalesce(s.team_ids, array[]::text[]) as team_ids
  from ranked r
  left join lateral (
    select string_agg(x.display_name, ' · ' order by x.rn) as teams,
           array_agg(x.team_id order by x.rn) as team_ids
    from (
      select tm.team_id, tm.display_name,
             row_number() over (
               order by max(a.played) desc nulls last, count(*) desc,
                        tm.display_name
             ) as rn
      from (
        select l.team_id, m.date as played
        from public.lineups l
        join public.matches m on m.match_id = l.match_id
        where l.player_id = r.player_id
        union all
        select g.team_id, m.date
        from public.goals g
        join public.matches m on m.match_id = g.match_id
        where g.player_id = r.player_id and g.goal_type <> 'own_goal'
        union all
        select g.team_id, m.date
        from public.goals g
        join public.matches m on m.match_id = g.match_id
        where g.assist_player_id = r.player_id and g.goal_type <> 'own_goal'
        union all
        select rg.team_id, coalesce(rg.from_date, rg.created_at::date)
        from public.registrations rg
        where rg.player_id = r.player_id
      ) a
      join public.teams tm on tm.team_id = a.team_id
      group by tm.team_id, tm.display_name
    ) x
    where x.rn <= 2
  ) s on true
  order by r.score desc, r.full_name;
$$;


-- ── merge_officials: repoint team_staff ──────────────────────────────────────
-- 0024's body, plus the one table it could not have known about. Without this
-- the delete at the end trips team_staff's FK and the merge refuses — safe, but
-- it would make every duplicate coach entered on a squad screen unmergeable.
-- A row the winner already holds for the same team and season is dropped
-- first, the same move merge_players makes on registrations.

create or replace function public.merge_officials(
  p_loser  text,
  p_winner text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_loser   public.officials;
  v_winner  public.officials;
  v_matches int := 0;
  v_one     int;
  v_col     text;
begin
  if not public.is_admin() then
    raise exception 'only an admin can merge officials' using errcode = '42501';
  end if;
  if p_loser = p_winner then
    raise exception 'those are the same official' using errcode = '22023';
  end if;

  perform 1 from public.officials o
  where o.official_id in (p_loser, p_winner)
  order by o.official_id for update;

  select * into v_loser from public.officials o where o.official_id = p_loser;
  if not found then
    raise exception 'official % not found', p_loser using errcode = 'P0002';
  end if;
  select * into v_winner from public.officials o where o.official_id = p_winner;
  if not found then
    raise exception 'official % not found', p_winner using errcode = 'P0002';
  end if;
  if v_loser.kind <> v_winner.kind then
    raise exception 'a % and a % are not the same person',
      v_loser.kind, v_winner.kind using errcode = '22023';
  end if;

  foreach v_col in array array[
    'referee_id', 'assistant_referee_1_id', 'assistant_referee_2_id',
    'fourth_official_id', 'home_coach_id', 'away_coach_id'
  ] loop
    execute format(
      'update public.matches set %I = $1 where %I = $2', v_col, v_col)
      using p_winner, p_loser;
    get diagnostics v_one = row_count;
    v_matches := v_matches + v_one;
  end loop;

  delete from public.team_staff s
  where s.official_id = p_loser
    and exists (select 1 from public.team_staff w
                where w.official_id = p_winner
                  and w.team_id = s.team_id and w.season_id = s.season_id);
  update public.team_staff s set official_id = p_winner
  where s.official_id = p_loser;

  -- The winner keeps their own photo; the loser's fills a gap.
  if v_winner.photo_path = '' and v_loser.photo_path <> '' then
    update public.officials o set photo_path = v_loser.photo_path
    where o.official_id = p_winner;
  end if;

  update public.aliases a
  set entity_id = p_winner
  where a.entity_type = 'official' and a.entity_id = p_loser;

  if not exists (
    select 1 from public.aliases a
    where a.entity_type = 'official' and a.entity_id = p_winner
      and lower(a.alias_text) = lower(v_loser.full_name)) then
    insert into public.aliases (alias_text, entity_type, entity_id, context, ord)
    values (v_loser.full_name, 'official', p_winner, 'merged',
            coalesce((select max(a.ord) from public.aliases a), 0) + 1);
  end if;

  delete from public.officials o where o.official_id = p_loser;

  return jsonb_build_object(
    'winner', p_winner, 'loser', p_loser, 'matches', v_matches);
end;
$$;

commit;
