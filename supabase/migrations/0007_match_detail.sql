-- 0007_match_detail.sql — scorers, cards, substitutions, line-ups, photos.
--
-- The governing constraint here is not the schema, it is validate.py. Every
-- build runs it, and any ERROR aborts the build before a page is written — so
-- a reporter who can write data that fails a check can stop the site
-- deploying. That is the real risk in this migration, and it shapes the whole
-- design:
--
--   * GOALS GO THROUGH AN RPC, not an RLS policy, because check 5 ("goal rows
--     per match+side never exceed that side's score") is an aggregate over
--     sibling rows. Only a function can count them before inserting. A plain
--     INSERT policy would let a reporter add a third scorer to a 2-1 match and
--     break every subsequent build.
--
--   * CARDS, SUBSTITUTIONS, LINE-UPS AND MEDIA use ordinary RLS policies,
--     because they live in new tables that validate.py has never heard of and
--     the renderers never read. They cannot break a build. They also have no
--     structural fields to protect — unlike matches, every column is
--     reportable content — so the narrow-RPC treatment would be ceremony.
--
-- Reporter-entered scorers keep the existing convention exactly: player_id is
-- the reserved CAF_MW_UNKNOWN and the typed name goes to reported_player_name.
-- Such goals count toward team and match totals but never enter scorer
-- rankings, which is already how the build treats CAF_MW_UNKNOWN. Reconciling
-- a name to a canonical player later is a matter of setting player_id, and the
-- goal joins the rankings at the next build with nothing else to change.

begin;

-- ── Reporter-facing goal id ──────────────────────────────────────────────────
-- The historic ids look like MW_CRFA_2627_G-0001. Minting one would mean
-- turning MW_2026_27 into "2627" — deriving meaning by parsing an id, which
-- DATA_MODEL.md forbids outright. Ids are opaque and only have to be unique,
-- so a reporter goal is named after the match it belongs to. No parsing, no
-- collisions, and obvious to read in a snapshot diff.

create or replace function public.next_goal_id(p_match_id text)
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select p_match_id || '_G' || (
    coalesce(
      (select max(substring(g.goal_id from '_G([0-9]+)$')::integer)
       from public.goals g
       where g.match_id = p_match_id
         and g.goal_id ~ ('^' || replace(p_match_id, '_', '\_') || '_G[0-9]+$')),
      0) + 1
  );
$$;

revoke execute on function public.next_goal_id(text) from public, anon, authenticated;


-- ── submit_match_goal ────────────────────────────────────────────────────────

create or replace function public.submit_match_goal(
  p_match_id    text,
  p_team_id     text,
  p_player_name text,
  p_minute      text default '',
  p_goal_type   text default ''
)
returns setof public.goals
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_match    public.matches;
  v_scored   integer;
  v_existing integer;
  v_goal     public.goals;
begin
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  -- Lock the match so two reporters adding the last scorer at once cannot
  -- both pass the count check below.
  select * into v_match from public.matches
  where match_id = p_match_id for update;
  if not found then
    raise exception 'match not found' using errcode = 'P0002';
  end if;

  if not public.can_report_match(p_match_id) then
    raise exception 'not assigned to this competition' using errcode = '42501';
  end if;

  -- validate.py check 5, first half: a goal must belong to a side that played.
  if p_team_id not in (v_match.home_team_id, v_match.away_team_id) then
    raise exception 'that team did not play in this match'
      using errcode = '22023';
  end if;

  -- ...and check 5 refuses goal rows on a match with no score at all.
  if v_match.home_goals is null or v_match.away_goals is null then
    raise exception 'publish the score before adding scorers'
      using errcode = '22023';
  end if;

  v_scored := case when p_team_id = v_match.home_team_id
                   then v_match.home_goals else v_match.away_goals end;

  select count(*) into v_existing
  from public.goals g
  where g.match_id = p_match_id and g.team_id = p_team_id;

  -- The check that protects every future build. Fewer scorers than goals is
  -- expected and fine; more is what validate.py rejects.
  if v_existing >= v_scored then
    raise exception
      'all % goal(s) for that team already have a scorer', v_scored
      using errcode = '22023';
  end if;

  if p_goal_type not in ('', 'open_play', 'penalty', 'free_kick', 'header',
                         'own_goal') then
    raise exception 'invalid goal type %', p_goal_type using errcode = '22023';
  end if;

  if btrim(p_player_name) = '' then
    raise exception 'a scorer needs a name' using errcode = '22023';
  end if;

  insert into public.goals (
    goal_id, match_id, team_id, player_id, reported_player_name,
    minute, goal_type, source_type, reported_by, reported_at, confidence, ord
  ) values (
    public.next_goal_id(p_match_id), p_match_id, p_team_id,
    -- The reserved unresolved-scorer row. Counts toward totals, never enters
    -- scorer rankings, until someone maps the name to a real player_id.
    'CAF_MW_UNKNOWN', btrim(p_player_name),
    btrim(coalesce(p_minute, '')), p_goal_type,
    'reporter', v_reporter, now(), 'unconfirmed',
    (select coalesce(max(ord), 0) + 1 from public.goals)
  )
  returning * into v_goal;

  return next v_goal;
end;
$$;

revoke execute on function public.submit_match_goal(text, text, text, text, text)
  from public, anon;
grant execute on function public.submit_match_goal(text, text, text, text, text)
  to authenticated;


-- ── delete_match_goal ────────────────────────────────────────────────────────
-- A mistyped scorer has to be removable, or the count check above becomes a
-- trap: a wrong name would occupy one of the match's goal slots forever.

create or replace function public.delete_match_goal(p_goal_id text)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_goal     public.goals;
begin
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  select * into v_goal from public.goals where goal_id = p_goal_id;
  if not found then
    return false;
  end if;

  if not public.can_report_match(v_goal.match_id) then
    raise exception 'not assigned to this competition' using errcode = '42501';
  end if;

  -- Only your own, unless you are an admin. A reporter correcting a colleague's
  -- entry is a conversation, not a permission.
  if not public.is_admin() and v_goal.reported_by is distinct from v_reporter then
    raise exception 'you can only remove scorers you added yourself'
      using errcode = '42501';
  end if;

  delete from public.goals where goal_id = p_goal_id;
  return true;
end;
$$;

revoke execute on function public.delete_match_goal(text) from public, anon;
grant execute on function public.delete_match_goal(text) to authenticated;


-- ── match_incidents — cards and substitutions ────────────────────────────────
-- A new table rather than more goals rows: a card is not a goal, and the
-- existing goals model is load-bearing for standings and scorer charts.
--
-- Shaped so a richer live-event model can grow here later — minute, stoppage
-- and period are already present — but nothing consumes it yet and no live
-- engine is being built.

create table public.match_incidents (
  incident_id           bigint generated by default as identity primary key,
  match_id              text not null
                        references public.matches (match_id) on delete cascade,
  team_id               text not null references public.teams (team_id),
  incident_type         text not null
                        check (incident_type in ('yellow_card', 'second_yellow',
                                                 'red_card', 'substitution')),
  -- CONVENTION: for a substitution, player_name is the player coming ON and
  -- secondary_player_name is the player going OFF. For a card, player_name is
  -- the player booked and secondary_player_name is empty.
  player_name           text not null check (player_name <> ''),
  secondary_player_name text not null default '',
  -- For later reconciliation, exactly as goals: the name is what was reported,
  -- the id is filled in when someone identifies the player.
  player_id             text references public.players (player_id),
  minute                text not null default '',
  stoppage              text not null default '',
  period                text not null default '',
  notes                 text not null default '',
  source_type           text not null default 'reporter',
  reported_by           text references public.reporters (reporter_id),
  reported_at           timestamptz not null default now(),
  confidence            text not null default 'unconfirmed'
                        check (confidence in ('unconfirmed', 'confirmed', 'official')),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  constraint match_incidents_sub_has_both
    check (incident_type <> 'substitution' or secondary_player_name <> '')
);

create index match_incidents_match_idx on public.match_incidents (match_id);
create index match_incidents_reported_by_idx on public.match_incidents (reported_by);

comment on table public.match_incidents is
  'Cards and substitutions. For a substitution: player_name is ON, '
  'secondary_player_name is OFF.';


-- ── lineup_entries ───────────────────────────────────────────────────────────
-- Plain names on purpose. A reporter in a regional league will not have squad
-- records to pick from, and forcing a master-player lookup would mean no
-- line-ups get entered at all.

create table public.lineup_entries (
  id            bigint generated by default as identity primary key,
  match_id      text not null
                references public.matches (match_id) on delete cascade,
  team_id       text not null references public.teams (team_id),
  player_name   text not null check (player_name <> ''),
  player_id     text references public.players (player_id),
  shirt_number  text not null default '',
  role          text not null check (role in ('starter', 'substitute')),
  position      text not null default '',
  reported_by   text references public.reporters (reporter_id),
  created_at    timestamptz not null default now(),
  ord           integer not null default 0,
  -- One row per named player per side. Re-entering a line-up replaces rather
  -- than duplicates.
  unique (match_id, team_id, player_name)
);

create index lineup_entries_match_idx on public.lineup_entries (match_id, team_id);


-- ── match_media ──────────────────────────────────────────────────────────────
-- Metadata for objects in the `match-media` storage bucket. The bytes live in
-- storage; this is what the site would read to display them.

create table public.match_media (
  id            uuid primary key default gen_random_uuid(),
  match_id      text not null
                references public.matches (match_id) on delete cascade,
  -- <match public_id>/<uuid>.<ext> — see the storage policies below, which
  -- authorize an upload by reading the match id out of the first path segment.
  storage_path  text not null unique,
  caption       text not null default '',
  content_type  text not null default '',
  byte_size     integer,
  reported_by   text references public.reporters (reporter_id),
  created_at    timestamptz not null default now()
);

create index match_media_match_idx on public.match_media (match_id);


-- ── RLS ──────────────────────────────────────────────────────────────────────
-- Public read: these are football facts intended for publication, and the site
-- will display them once there is a match page to display them on.
--
-- Writes are ordinary policies here (unlike matches). There are no structural
-- fields to protect — every column is reportable content — and none of these
-- tables can fail validate.py, so a narrow RPC would be ceremony. What the
-- policies must do is stop a reporter forging authorship, which is what the
-- WITH CHECK on reported_by does.

alter table public.match_incidents enable row level security;
alter table public.lineup_entries  enable row level security;
alter table public.match_media     enable row level security;

do $$
declare t text;
begin
  foreach t in array array['match_incidents', 'lineup_entries', 'match_media']
  loop
    execute format(
      'create policy %I on public.%I for select to anon, authenticated using (true)',
      t || '_public_read', t);

    -- Insert only for a match you may report, and only in your own name.
    execute format($f$
      create policy %I on public.%I for insert to authenticated
      with check (public.can_report_match(match_id)
                  and reported_by = public.current_reporter_id())
    $f$, t || '_reporter_insert', t);

    -- Edit and remove only what you entered yourself; admins correct anything.
    execute format($f$
      create policy %I on public.%I for update to authenticated
      using (public.can_report_match(match_id)
             and (public.is_admin() or reported_by = public.current_reporter_id()))
      with check (public.can_report_match(match_id))
    $f$, t || '_reporter_update', t);

    execute format($f$
      create policy %I on public.%I for delete to authenticated
      using (public.can_report_match(match_id)
             and (public.is_admin() or reported_by = public.current_reporter_id()))
    $f$, t || '_reporter_delete', t);
  end loop;
end;
$$;


-- ── Storage: the match-media bucket ──────────────────────────────────────────

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('match-media', 'match-media', true, 5242880,
        array['image/jpeg', 'image/png', 'image/webp'])
on conflict (id) do update
  set public             = excluded.public,
      file_size_limit    = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

-- Public bucket because the static site has to be able to <img src> the
-- result: a signed URL would expire long before the page is rebuilt. The
-- 5 MB cap and the MIME allow-list are enforced by storage itself, so a
-- client that skips the resize step still cannot upload a 40 MB photo.

-- Uploads are authorized by reading the match out of the object's own path,
-- which is what makes "<match public_id>/<file>" a rule and not a convention.
create or replace function public.can_report_media_path(p_name text)
returns boolean
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_folder text;
  v_match  text;
begin
  v_folder := split_part(p_name, '/', 1);
  -- A path whose first segment is not a uuid belongs to no match.
  if v_folder !~ '^[0-9a-fA-F-]{36}$' then
    return false;
  end if;
  select m.match_id into v_match
  from public.matches m where m.public_id = v_folder::uuid;
  if v_match is null then
    return false;
  end if;
  return public.can_report_match(v_match);
end;
$$;

revoke execute on function public.can_report_media_path(text) from public, anon;
grant execute on function public.can_report_media_path(text) to authenticated;

drop policy if exists match_media_read on storage.objects;
drop policy if exists match_media_insert on storage.objects;
drop policy if exists match_media_update on storage.objects;
drop policy if exists match_media_delete on storage.objects;

create policy match_media_read on storage.objects
  for select to anon, authenticated
  using (bucket_id = 'match-media');

-- NOT "any authenticated user may upload": only a reporter assigned to the
-- competition of the match named in the path.
create policy match_media_insert on storage.objects
  for insert to authenticated
  with check (bucket_id = 'match-media'
              and public.can_report_media_path(name));

create policy match_media_update on storage.objects
  for update to authenticated
  using (bucket_id = 'match-media' and public.can_report_media_path(name));

create policy match_media_delete on storage.objects
  for delete to authenticated
  using (bucket_id = 'match-media' and public.can_report_media_path(name));

commit;
