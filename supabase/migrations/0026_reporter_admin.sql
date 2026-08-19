-- 0026_reporter_admin.sql — managing the reporter pool from /report.
--
-- WHAT WAS WRONG. Every reporter operation lived in scripts/reporters.py,
-- which needs SUPABASE_SECRET_KEY and therefore a trusted laptop. The README
-- stated that as a principle — "Administration is CLI-only ... there is no
-- admin portal" — and for creating a login it is still the right instinct.
-- But it made the ORDINARY operations require the laptop too: a reporter
-- turns up, and until somebody is sitting at a checkout of this repo with the
-- secret key in .env, they cannot be given a league. Assigning a competition
-- is a two-column insert that cannot break anything, and it was gated behind
-- the same door as minting credentials.
--
-- WHAT THIS DOES. Five admin-only functions, split by what they actually
-- need, which is not the same for all of them:
--
--   * admin_assign_competition / admin_unassign_competition
--     admin_set_reporter_role / admin_set_reporter_active
--         Ordinary authenticated calls, gated on is_admin(), exactly like
--         merge_players (0022). They touch nothing but public tables.
--
--   * admin_create_reporter
--         Callable ONLY with the secret key — see the grant at the bottom.
--         A reporter needs a LOGIN, and a login is an auth.users row that
--         only the GoTrue admin API can make. The browser cannot hold the key
--         that does it, so the Edge Function `manage-reporters` creates the
--         auth user and then calls this to make the reporters row and its
--         assignments in one transaction.
--
-- WHY WRITES ARE RPCs AND NOT AN RLS POLICY. The house rule is that reporter
-- writes go through RPCs where a bad row could break the build, and plain RLS
-- where it could not. `reporters` is part of the Dataset (src/dataset.py
-- parses it; validate.py requires a unique reporter_id and a non-blank name),
-- so a row typed in a hurry CAN abort a build and stop every future deploy
-- for everyone. That puts it firmly on the RPC side. `reporter_assignments`
-- is not in the Dataset at all and could safely have been a policy; it is an
-- RPC anyway, so that "who may change the reporter pool" is one answer in one
-- place rather than two mechanisms to keep in step.
--
-- THE ONE RULE THAT CANNOT BE UNDONE FROM HERE. Nothing in this portal can
-- restore an administrator, so nothing in this portal may remove the last
-- one. Demoting or deactivating the final active admin would lock every
-- person out of the screen that grants the role, and the only way back would
-- be the CLI these functions exist to avoid needing. Both functions refuse.
-- Self-changes are refused for the same reason, one step earlier: an admin
-- may not demote or deactivate their own account, because the overwhelmingly
-- likely reading of that tap is a mis-tap on the wrong person's card.
--
-- NO REBUILD FOLLOWS ANY OF THIS. No page on everyleague.co renders a
-- reporter — there is no byline anywhere in src/render.py — so unlike a
-- rename or a scoreline, none of these calls changes the published site. The
-- portal deliberately does not nudge a build after them.

begin;


-- ── Minting an id ────────────────────────────────────────────────────────────
-- MW_REP_001, MW_REP_002, ... — the same shape scripts/reporters.py has always
-- produced, so the two paths cannot drift into two numbering schemes.
--
-- Highest plus one, with the same caveat as create_player and create_official:
-- the digits are a counter and carry no meaning. Do not read anything out of
-- them. Rows whose id does not fit the pattern are ignored rather than
-- crashing the sequence, because a hand-made id is a thing this schema has
-- always tolerated (scripts/reporters.py has --reporter-id).

create or replace function public.next_reporter_id()
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select 'MW_REP_' || lpad((coalesce(max(
           (substring(r.reporter_id from '^MW_REP_(\d+)$'))::integer), 0) + 1
         )::text, 3, '0')
  from public.reporters r
  where r.reporter_id ~ '^MW_REP_\d+$'
$$;

comment on function public.next_reporter_id() is
  'The next MW_REP_NNN. Internal to admin_create_reporter; granted to nobody.';

-- Not a public question. Every caller that legitimately needs an id is in
-- this file, and handing out the next one invites a client to insert with it.
revoke execute on function public.next_reporter_id()
  from public, anon, authenticated;


-- ── Creating a reporter ──────────────────────────────────────────────────────
-- Called by the `manage-reporters` Edge Function, with the secret key, AFTER
-- it has created the auth user. Split that way because the two halves live in
-- different systems: the login is GoTrue's and only its admin API can make
-- one, the reporter is ours. The function creates the login first and deletes
-- it again if this call fails, so a half-made account cannot block a retry —
-- the same rollback scripts/reporters.py has always done.
--
-- p_actor is the administrator who asked, and it is checked here even though
-- the Edge Function has already checked it. That is deliberate belt and
-- braces: the grant below means only the secret key can reach this, and the
-- secret key is exactly the credential for which is_admin() is FALSE (there
-- is no auth.uid() behind it), so without p_actor this function would have no
-- idea whether a person authorised it. Naming an active administrator is the
-- only way in.

create or replace function public.admin_create_reporter(
  p_actor        text,
  p_name         text,
  p_email        text,
  p_auth_user_id uuid,
  p_role         text default 'reporter',
  p_competitions text[] default '{}',
  p_season       text default null,
  p_affiliation  text default '',
  p_region       text default '',
  p_byline       text default ''
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_id     text;
  v_name   text := btrim(coalesce(p_name, ''));
  v_email  text := lower(btrim(coalesce(p_email, '')));
  v_comp   text;
begin
  if not exists (
    select 1 from public.reporters r
    where r.reporter_id = p_actor and r.active and r.role = 'admin'
  ) then
    raise exception 'only an administrator can create a reporter'
      using errcode = '42501';
  end if;

  -- validate.py requires a non-blank name on every reporters row, and a build
  -- that trips on one cannot be fixed from a phone. Refuse it here, where the
  -- person who typed it is still looking at the screen.
  if v_name = '' then
    raise exception 'a reporter needs a name' using errcode = '22023';
  end if;
  if v_email = '' then
    raise exception 'a reporter needs an email address' using errcode = '22023';
  end if;
  if p_role not in ('reporter', 'admin') then
    raise exception 'role must be reporter or admin' using errcode = '22023';
  end if;

  -- The auth user is already unique on email (GoTrue enforces it), but a
  -- second reporters row pointing at a different login with the same address
  -- is the kind of duplicate that is confusing rather than fatal, so it is
  -- worth one sentence here.
  if exists (select 1 from public.reporters r where lower(r.email) = v_email) then
    raise exception 'a reporter already exists with that email address'
      using errcode = '23505';
  end if;

  -- Named rather than derived so the error says which one is wrong. The FK
  -- would catch it, with a message nobody standing in a WhatsApp group can act
  -- on.
  foreach v_comp in array coalesce(p_competitions, '{}') loop
    if not exists (
      select 1 from public.competitions c where c.competition_id = v_comp
    ) then
      raise exception 'no competition %', v_comp using errcode = '22023';
    end if;
  end loop;

  v_id := public.next_reporter_id();

  insert into public.reporters (
    reporter_id, name, email, affiliation, region, public_byline,
    active, role, auth_user_id, ord
  ) values (
    v_id, v_name, v_email, coalesce(p_affiliation, ''), coalesce(p_region, ''),
    coalesce(nullif(btrim(coalesce(p_byline, '')), ''), v_name),
    true, p_role, p_auth_user_id, 0
  );

  foreach v_comp in array coalesce(p_competitions, '{}') loop
    insert into public.reporter_assignments (reporter_id, competition_id, season_id)
    values (v_id, v_comp, p_season)
    on conflict do nothing;
  end loop;

  return v_id;
end;
$$;

comment on function public.admin_create_reporter(
  text, text, text, uuid, text, text[], text, text, text, text) is
  'Creates a reporters row and its assignments for an auth user the caller has '
  'already made. Secret key only — the Edge Function manage-reporters.';

-- THE GRANT IS THE AUTHORIZATION. This must never be reachable from a browser:
-- it takes an auth_user_id as an argument and would otherwise let any signed-in
-- account attach a reporter row — with role 'admin' — to any login it could
-- name. Only the secret key may call it, which in practice means only the Edge
-- Function, which checks the caller is an administrator before it does.
revoke execute on function public.admin_create_reporter(
  text, text, text, uuid, text, text[], text, text, text, text)
  from public, anon, authenticated;
grant execute on function public.admin_create_reporter(
  text, text, text, uuid, text, text[], text, text, text, text)
  to service_role;


-- ── Assignments ──────────────────────────────────────────────────────────────
-- A season_id of NULL means every season of the competition, which is what the
-- portal sends and what scripts/reporters.py has always defaulted to. The
-- column stays because scoping a reporter to one season is a real thing to
-- want at a season boundary (see 0002); it simply has no UI yet.

create or replace function public.admin_assign_competition(
  p_reporter    text,
  p_competition text,
  p_season      text default null
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise exception 'only an administrator can assign a competition'
      using errcode = '42501';
  end if;
  if not exists (
    select 1 from public.reporters r where r.reporter_id = p_reporter
  ) then
    raise exception 'no reporter %', p_reporter using errcode = 'P0002';
  end if;
  if not exists (
    select 1 from public.competitions c where c.competition_id = p_competition
  ) then
    raise exception 'no competition %', p_competition using errcode = '22023';
  end if;

  insert into public.reporter_assignments (reporter_id, competition_id, season_id)
  values (p_reporter, p_competition, p_season)
  on conflict do nothing;
end;
$$;

create or replace function public.admin_unassign_competition(
  p_reporter    text,
  p_competition text,
  p_season      text default null
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise exception 'only an administrator can remove an assignment'
      using errcode = '42501';
  end if;

  -- `is not distinct from` rather than `=`, because the season this deletes is
  -- almost always NULL and `season_id = null` is never true. The all-seasons
  -- row is the one the portal creates, so matching it is the whole job.
  delete from public.reporter_assignments a
  where a.reporter_id = p_reporter
    and a.competition_id = p_competition
    and a.season_id is not distinct from p_season;
end;
$$;


-- ── Role and active ──────────────────────────────────────────────────────────

create or replace function public.admin_set_reporter_role(
  p_reporter text,
  p_role     text
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_self text := public.current_reporter_id();
begin
  if not public.is_admin() then
    raise exception 'only an administrator can change a role'
      using errcode = '42501';
  end if;
  if p_role not in ('reporter', 'admin') then
    raise exception 'role must be reporter or admin' using errcode = '22023';
  end if;
  if p_reporter = v_self then
    raise exception 'you cannot change your own role' using errcode = '42501';
  end if;
  if not exists (
    select 1 from public.reporters r where r.reporter_id = p_reporter
  ) then
    raise exception 'no reporter %', p_reporter using errcode = 'P0002';
  end if;

  -- The last-administrator rule. Unreachable in practice while self-demotion
  -- is refused above — you cannot be the last admin and be demoting somebody
  -- else — but it is the invariant that actually matters, so it is stated
  -- rather than left to be inferred from the rule that happens to imply it.
  if p_role = 'reporter' and not exists (
    select 1 from public.reporters r
    where r.role = 'admin' and r.active and r.reporter_id <> p_reporter
  ) then
    raise exception 'that is the last administrator' using errcode = '42501';
  end if;

  update public.reporters r set role = p_role where r.reporter_id = p_reporter;
end;
$$;

create or replace function public.admin_set_reporter_active(
  p_reporter text,
  p_active   boolean
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_self text := public.current_reporter_id();
begin
  if not public.is_admin() then
    raise exception 'only an administrator can activate or deactivate an account'
      using errcode = '42501';
  end if;
  if p_reporter = v_self then
    raise exception 'you cannot deactivate your own account'
      using errcode = '42501';
  end if;
  if not exists (
    select 1 from public.reporters r where r.reporter_id = p_reporter
  ) then
    raise exception 'no reporter %', p_reporter using errcode = 'P0002';
  end if;

  if not p_active and not exists (
    select 1 from public.reporters r
    where r.role = 'admin' and r.active and r.reporter_id <> p_reporter
  ) then
    raise exception 'that is the last administrator' using errcode = '42501';
  end if;

  -- Assignments are deliberately left in place: deactivating is reversible and
  -- is not the same as forgetting what someone covered. `active` alone gates
  -- every authorization check (see can_report_match).
  update public.reporters r set active = p_active
  where r.reporter_id = p_reporter;
end;
$$;


-- These four are the ordinary kind: a signed-in administrator calls them from
-- the portal, and is_admin() inside each one is the check. anon must never
-- reach them — an unauthenticated visitor has no reporter identity and every
-- one of these would raise, but saying so in the grants is cheaper than
-- relying on it.
revoke execute on function public.admin_assign_competition(text, text, text)
  from public, anon;
grant execute on function public.admin_assign_competition(text, text, text)
  to authenticated;

revoke execute on function public.admin_unassign_competition(text, text, text)
  from public, anon;
grant execute on function public.admin_unassign_competition(text, text, text)
  to authenticated;

revoke execute on function public.admin_set_reporter_role(text, text)
  from public, anon;
grant execute on function public.admin_set_reporter_role(text, text)
  to authenticated;

revoke execute on function public.admin_set_reporter_active(text, boolean)
  from public, anon;
grant execute on function public.admin_set_reporter_active(text, boolean)
  to authenticated;

commit;
