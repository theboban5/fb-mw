-- 0046_team_aliases.sql — teaching the importer the names a league actually
-- prints.
--
-- WHAT WAS WRONG. A real import came back:
--
--     NOT MATCHED — Read as: MAFCO FC 1–2 MOYALE FC
--     That team is not in a competition you report.
--
-- The database has Moyale Barracks; the graphic says MOYALE FC. That is not an
-- error in either of them — it is the club under the name its league prints,
-- and there are dozens of these. The matcher was already built to cope:
-- import_team_candidates (0043) reads team aliases at tier 2 and club aliases
-- at tier 4, and 30 club aliases are already in the table, filed by hand as
-- 'fast entry' short codes.
--
-- WHAT WAS MISSING IS ANY WAY TO WRITE ONE. rename_player (0022) and
-- rename_official (0024) put a person's old spelling into `aliases` as a side
-- effect of correcting it; nothing anywhere writes an alias for a team or a
-- club. So the one repair a reporter could see the need for — "this is Moyale
-- Barracks, remember that" — was the one repair the portal could not make, and
-- the same graphic failed the same way every week.
--
-- WHAT THIS DOES.
--
--   alias_entity        the dedupe-and-compute-ord half of alias_player (0022),
--                       generalised, so nothing here reimplements it.
--   add_team_alias      a reporter records a name for a team they report.
--   remove_team_alias   an admin takes one back.
--   rename_team         an admin corrects a display name; the old one becomes
--                       an alias, exactly as rename_player has always worked.
--   search_report_teams the picker's list: teams in the caller's competitions,
--                       with their clubs and their existing aliases.
--
-- WHY THE ALIAS GOES ON THE TEAM AND NOT THE CLUB. A club alias reaches every
-- team of that club through tier 4 — Moyale Barracks AND Moyale Sisters — so
-- filing "MOYALE FC" at club level would turn a red row into an AMBIGUOUS one
-- wherever a reporter covers both, which is the same fix failing more quietly.
-- A team alias resolves at tier 2, alone, and still works in a second
-- competition next season because team_id is stable across entries.
--
-- WHY A REPORTER MAY DO THIS AND MAY NOT MINT A CLUB. create_league is admin
-- only because a duplicate club splits a club's history across the site
-- permanently and cannot be repaired by editing one row. An alias is the
-- opposite kind of object: it references nothing, nothing references it,
-- deleting it undoes it completely, and the person who can see that the graphic
-- says MOYALE FC is the person holding the phone. THE SCOPE IS THE
-- AUTHORIZATION, as it is in 0043 — a reporter may name a team they are
-- assigned to report, and no other.
--
-- THE ONE GUARD THAT MATTERS is the collision check in add_team_alias. MW_MB is
-- Moyale Barracks and MW_MR is Moyale Reserve FC — two different clubs — so an
-- alias filed carelessly does not fail to match, it matches the WRONG team, and
-- publishes a result against it. So a name that another team already answers to
-- at tier 1 or tier 2 is refused, and the message says which team that is.
-- Tiers 3 and 4 are deliberately NOT a collision: a team alias outranks a club
-- name, so filing "Bullets" on the men's first team is how the four Bullets
-- squads stop being ambiguous, not a way of making them so.
--
-- NOTHING HERE RENDERS. src/search.py reads aliases for competition and club
-- ids only, so a team alias reaches no page and no search row — which is why
-- writing one needs no rebuild. rename_team is the exception, and its caller
-- nudges one.

begin;

-- ── alias_entity ─────────────────────────────────────────────────────────────
-- alias_player (0022) with the entity type as an argument. It is not a
-- replacement for it: that one is private to rename_player/merge_players and
-- hard-codes 'player' precisely so neither can file a spelling under the wrong
-- kind of thing. This one has the same job for the kinds this file writes, and
-- is private for the same reason.
--
-- The existence check is not a uniqueness guarantee — aliases has no natural
-- key by design (0001) — it is what keeps a reporter tapping the same name
-- twice from growing the tab forever. Returning the id either way makes the
-- call idempotent, which is what lets the client show what it just wrote.

create or replace function public.alias_entity(
  p_entity_type text,
  p_entity_id   text,
  p_alias_text  text,
  p_context     text default ''
)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_text text;
  v_id   bigint;
begin
  v_text := btrim(regexp_replace(coalesce(p_alias_text, ''), '\s+', ' ', 'g'));
  if v_text = '' then
    return null;
  end if;

  select a.id into v_id
  from public.aliases a
  where a.entity_type = p_entity_type
    and a.entity_id = p_entity_id
    and lower(a.alias_text) = lower(v_text)
  limit 1;
  if found then
    return v_id;
  end if;

  -- ord is `not null` with no default on this table (0001), so it is computed
  -- rather than left to the identity column every other tab has.
  insert into public.aliases (alias_text, entity_type, entity_id, context, ord)
  values (v_text, p_entity_type, p_entity_id, coalesce(p_context, ''),
          coalesce((select max(a.ord) from public.aliases a), 0) + 1)
  returning id into v_id;

  return v_id;
end;
$$;

comment on function public.alias_entity(text, text, text, text) is
  'Internal. Record a spelling against an entity, once. Callers choose the '
  'entity_type; nothing reachable over the API does.';

revoke execute on function public.alias_entity(text, text, text, text)
  from public, anon, authenticated;


-- ── team_alias_owner ─────────────────────────────────────────────────────────
-- Which OTHER team already answers to this name at tier 1 or tier 2 — that is,
-- exactly as strongly as the alias being written would. NULL means the name is
-- free at that strength.
--
-- Deliberately not scoped to the caller's competitions. `aliases` is one global
-- table with no season and no competition on it, so a name filed here is
-- offered to every reporter's matcher; asking the question inside one scope
-- would let two reporters each file a name the other's imports then resolve
-- wrongly. The check is global because the consequence is.

create or replace function public.team_alias_owner(
  p_alias_text text,
  p_team_id    text
)
returns text
language sql
stable
security definer
set search_path = ''
as $$
  with wanted as (select public.import_normalize(p_alias_text) as key)
  select t.team_id
  from public.teams t, wanted w
  where w.key <> ''
    and t.team_id <> p_team_id
    and (public.import_normalize(t.display_name) = w.key
         or exists (select 1 from public.aliases a
                    where a.entity_type = 'team'
                      and a.entity_id = t.team_id
                      and public.import_normalize(a.alias_text) = w.key))
  order by t.team_id
  limit 1
$$;

revoke execute on function public.team_alias_owner(text, text)
  from public, anon, authenticated;


-- ── can_name_team ────────────────────────────────────────────────────────────
-- May the caller record a name for this team? An admin always; a reporter when
-- the team is entered in a competition they are assigned to.
--
-- ANY SEASON, not the active one. can_report_competition already reads a NULL
-- season as "any season of this competition" (0008), and a reporter who covered
-- a club last season knows what its graphics call it just as well. The narrower
-- rule would refuse the alias for a competition whose season has not been
-- marked active yet, which is when a fixture list — and its spellings — arrive.

create or replace function public.can_name_team(p_team_id text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select public.is_admin() or exists (
    select 1 from public.entries e
    where e.team_id = p_team_id
      and public.can_report_competition(e.competition_id, e.season_id)
  )
$$;

revoke execute on function public.can_name_team(text) from public, anon;
grant execute on function public.can_name_team(text) to authenticated;


-- ── add_team_alias ───────────────────────────────────────────────────────────
-- The whole point of the migration: from an unmatched import row, a reporter
-- says which team it actually is, and the next graphic from that league
-- resolves on its own.
--
-- The length floor is on the NORMALIZED key, not the typed text: tier 2 matches
-- on the normalized form, so "F.C." is two characters as far as matching is
-- concerned and a two-character alias is inside half the country's team names.
-- Three is where the existing hand-filed codes start (BULL, SILV).

create or replace function public.add_team_alias(
  p_team_id    text,
  p_alias_text text
)
returns setof public.aliases
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_team     public.teams;
  v_text     text;
  v_key      text;
  v_owner    text;
  v_name     text;
  v_id       bigint;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  select * into v_team from public.teams t where t.team_id = p_team_id;
  if not found then
    raise exception 'that team is not in the database' using errcode = 'P0002';
  end if;

  if not public.can_name_team(p_team_id) then
    raise exception 'that team is not in a competition you report'
      using errcode = '42501';
  end if;

  v_text := btrim(regexp_replace(coalesce(p_alias_text, ''), '\s+', ' ', 'g'));
  if v_text = '' then
    raise exception 'type the name as the graphic prints it'
      using errcode = '22023';
  end if;
  if length(v_text) > 80 then
    raise exception 'that name is too long' using errcode = '22023';
  end if;

  v_key := public.import_normalize(v_text);
  if length(v_key) < 3 then
    raise exception 'that name is too short to match on' using errcode = '22023';
  end if;

  -- Already the team's own name: nothing to record, and saying so is better
  -- than a silent success that changes nothing.
  if v_key = public.import_normalize(v_team.display_name) then
    raise exception '% is already this team''s name', v_text
      using errcode = '22023';
  end if;

  -- THE GUARD. A name another team already answers to would not fail to match,
  -- it would match the wrong team — Moyale Barracks and Moyale Reserve FC are
  -- different clubs. Named, not merely refused, because the reporter has to see
  -- who they were about to collide with to know what to do next.
  v_owner := public.team_alias_owner(v_text, p_team_id);
  if v_owner is not null then
    select t.display_name into v_name from public.teams t where t.team_id = v_owner;
    raise exception '% already means % — pick that team, or use a fuller name',
      v_text, coalesce(v_name, v_owner)
      using errcode = '23505';
  end if;

  v_id := public.alias_entity('team', p_team_id, v_text, 'reporter');

  return query select a.* from public.aliases a where a.id = v_id;
end;
$$;

comment on function public.add_team_alias(text, text) is
  'Record a name a team is printed under, so the importer resolves it next '
  'time. Scoped to the caller''s competitions; refuses a name another team '
  'already answers to.';

revoke execute on function public.add_team_alias(text, text) from public, anon;
grant execute on function public.add_team_alias(text, text) to authenticated;


-- ── remove_team_alias ────────────────────────────────────────────────────────
-- Admin only, and only ever a team alias. Deleting a row is the one act in this
-- file that cannot be undone by doing the opposite, and the entity_type filter
-- is what stops this becoming a way to delete a player's old spelling — the
-- thing 0022 keeps deliberately, because merge_players depends on it.

create or replace function public.remove_team_alias(p_alias_id bigint)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  if not public.is_admin() then
    raise exception 'only an administrator can remove a name'
      using errcode = '42501';
  end if;

  delete from public.aliases a
  where a.id = p_alias_id and a.entity_type = 'team';

  if not found then
    raise exception 'that name is not a team alias' using errcode = 'P0002';
  end if;
end;
$$;

comment on function public.remove_team_alias(bigint) is
  'Admin only. Delete a team alias. Refuses any other kind of alias row.';

revoke execute on function public.remove_team_alias(bigint) from public, anon;
grant execute on function public.remove_team_alias(bigint) to authenticated;


-- ── rename_team ──────────────────────────────────────────────────────────────
-- rename_player's shape, for a squad. A display_name is on the standings table,
-- every fixture line, the club hub and the search index, so this is a change to
-- the published site and its caller nudges a rebuild — which is the reason it
-- is admin only where add_team_alias is not.
--
-- The old name is kept as an alias, which is the part that matters here: a
-- league that renames itself mid-season goes on printing the old name for
-- weeks, and the importer must go on resolving it.

create or replace function public.rename_team(
  p_team_id      text,
  p_display_name text
)
returns setof public.teams
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_team  public.teams;
  v_name  text;
  v_owner text;
  v_clash text;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  if not public.is_admin() then
    raise exception 'only an administrator can rename a team'
      using errcode = '42501';
  end if;

  select * into v_team from public.teams t
  where t.team_id = p_team_id for update;
  if not found then
    raise exception 'that team is not in the database' using errcode = 'P0002';
  end if;

  v_name := btrim(regexp_replace(coalesce(p_display_name, ''), '\s+', ' ', 'g'));
  if length(v_name) < 2 then
    raise exception 'a team needs a name' using errcode = '22023';
  end if;
  if length(v_name) > 80 then
    raise exception 'that name is too long' using errcode = '22023';
  end if;

  if public.import_normalize(v_name) = public.import_normalize(v_team.display_name)
  then
    -- Punctuation or capitalisation only: store it, but there is nothing to
    -- alias, because the old spelling already matches the new one.
    update public.teams set display_name = v_name, updated_at = now()
    where team_id = p_team_id
    returning * into v_team;
    return next v_team;
    return;
  end if;

  -- Renaming INTO a name another team answers to is the collision
  -- rename_player refuses for the same reason: afterwards the matcher can only
  -- return one of them, and which one is an accident of ordering.
  v_owner := public.team_alias_owner(v_name, p_team_id);
  if v_owner is not null then
    select t.display_name into v_clash from public.teams t where t.team_id = v_owner;
    raise exception '% already means % — rename that one first, or choose '
                    'a different name', v_name, coalesce(v_clash, v_owner)
      using errcode = '23505';
  end if;

  -- The old spelling survives the rename. Filed as 'renamed' rather than
  -- 'reporter' so the two ways a name gets into this table stay tellable apart.
  perform public.alias_entity('team', p_team_id, v_team.display_name, 'renamed');

  update public.teams
  set display_name = v_name, updated_at = now()
  where team_id = p_team_id
  returning * into v_team;

  return next v_team;
end;
$$;

comment on function public.rename_team(text, text) is
  'Admin only. Correct a team''s display name; the previous one is kept as an '
  'alias so the importer still resolves it. Renders on the site — the caller '
  'asks for a rebuild.';

revoke execute on function public.rename_team(text, text) from public, anon;
grant execute on function public.rename_team(text, text) to authenticated;


-- ── search_report_teams ──────────────────────────────────────────────────────
-- The list behind the picker, and behind the teams screen. Scoped to the
-- caller's competitions for 0043's reason: a reporter cannot usefully name a
-- team they may not report, and offering one would put a row on screen that
-- add_team_alias would refuse.
--
-- IT RETURNS THE COMPETITIONS AND THE EXISTING NAMES BESIDE EACH TEAM, which is
-- the whole design. A picker that shows names and nothing else is how 0034's
-- two Gift Phiris happened; Moyale Barracks and Moyale Sisters are the same
-- problem one table over, and the fact that separates them is which competition
-- each plays in.
--
-- The term is matched with import_normalize — the same normalizer the matcher
-- uses — so "moyale fc" finds Moyale Barracks here exactly when tier 5 would
-- have, and the reporter is never shown a list built by different rules from
-- the ones that will judge their answer.

create or replace function public.search_report_teams(
  p_term      text default '',
  p_season_id text default null,
  p_limit     integer default 40
)
returns table (
  team_id      text,
  display_name text,
  club_name    text,
  competitions text,
  aliases      jsonb
)
language sql
stable
security definer
set search_path = ''
as $$
  with scope as (
    select distinct e.team_id
    from public.entries e
    where (p_season_id is null or e.season_id = p_season_id)
      and public.can_report_competition(e.competition_id, e.season_id)
  ),
  wanted as (select public.import_normalize(p_term) as key)
  select
    t.team_id,
    t.display_name,
    coalesce(c.name, ''),
    coalesce((
      select string_agg(distinct co.name, ' · ' order by co.name)
      from public.entries e2
      join public.competitions co on co.competition_id = e2.competition_id
      where e2.team_id = t.team_id
        and (p_season_id is null or e2.season_id = p_season_id)
        and public.can_report_competition(e2.competition_id, e2.season_id)
    ), ''),
    coalesce((
      select jsonb_agg(jsonb_build_object('id', a.id, 'alias_text', a.alias_text)
                       order by a.id)
      from public.aliases a
      where a.entity_type = 'team' and a.entity_id = t.team_id
    ), '[]'::jsonb)
  from scope s
  join public.teams t on t.team_id = s.team_id
  left join public.clubs c on c.club_id = t.club_id, wanted w
  where w.key = ''
     or public.import_normalize(t.display_name) like '%' || w.key || '%'
     or public.import_normalize(coalesce(c.name, '')) like '%' || w.key || '%'
     or public.import_normalize(coalesce(c.short_name, '')) like '%' || w.key || '%'
     or exists (select 1 from public.aliases a
                where a.entity_type = 'team' and a.entity_id = t.team_id
                  and public.import_normalize(a.alias_text) like '%' || w.key || '%')
  order by t.display_name
  limit least(greatest(coalesce(p_limit, 40), 1), 200)
$$;

comment on function public.search_report_teams(text, text, integer) is
  'Teams the caller may report, with their club, their competitions and the '
  'names already recorded for them. Blank term lists them.';

revoke execute on function public.search_report_teams(text, text, integer)
  from public, anon;
grant execute on function public.search_report_teams(text, text, integer)
  to authenticated;

commit;
