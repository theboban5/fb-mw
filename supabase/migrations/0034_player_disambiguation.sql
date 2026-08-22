-- 0034_player_disambiguation.sql — two real people may share one name.
--
-- WHAT WAS WRONG. Every player tool in this repo was built on an assumption
-- that has now failed: that a name identifies a person. 0021 merged duplicates
-- by hand, 0022 built rename_player and merge_players to repair them, and
-- create_player has been idempotent on the name since 0010 so that typing what
-- is on screen finds a player rather than cloning them. All of that is right
-- for its actual case — one person filed twice — and all of it is wrong for
-- this one:
--
--   * Steve Phiri plays for Mzuzu City Hammers Youth in the Mzuzu District
--     U20 league. Steven Phiri plays for Chizumulu United in the NRFA. They
--     are two people. The U20 league's scorer table links Steve's goals to
--     Steven's page.
--   * Gift Phiri of Mpira Mmudzi Mwathu appears TWICE in that same table:
--     once linked to the Gift Phiri who plays in the National Division, and
--     once unlinked, because the second entry could not be filed anywhere.
--
-- Neither is a data-entry mistake. Both are what the portal makes happen:
--
--   1. THE PICKER SHOWS A NAME AND NOTHING ELSE. A reporter entering an U20
--      match types "Steve Phiri", sees one "Steve Phiri", and taps it. There
--      is nothing on the row that could tell them it is a senior player from
--      an island 300km away. search_players ranked by confidence and returned
--      no fact a human could rank BY.
--   2. AND WHEN THEY DO KNOW, THEY CANNOT ACT ON IT. create_player resolves
--      an existing name instead of inserting, and the portal hides "＋ Add as
--      a new player" entirely when the typed name matches a row exactly. So
--      the correct action — a second Gift Phiri — was not merely discouraged,
--      it was unreachable. The unlinked row in that table is a reporter doing
--      the only thing left.
--
-- WHAT THIS DOES. Two changes, and the first is what makes the second safe.
--
--   * search_players also returns the CLUBS a player has actually been named
--     for, most recent first, and their team_ids. Derived, never stored:
--     players has no club column and should not get one (a career is not a
--     column), so this reads the team sheets and the goals — the record of
--     who a player has turned out for is already in the database, it had just
--     never been shown to the person who needed it.
--   * create_player takes p_force. False, the default, keeps the idempotence
--     0010 argued for and every existing caller relies on. True inserts a new
--     row under a name that already exists — the deliberate act of saying
--     "this is a different person", offered only once the reporter is looking
--     at the other one's club and can see it is not him.
--
-- THE TRADE, STATED PLAINLY. Two players may now hold the same name, and the
-- only thing standing between that and the mess 0021 cleaned up by hand is
-- that the reporter can now see who they are choosing between. That is a
-- weaker guarantee than "the database will not let you", and it is the right
-- one: the database cannot know whether two Gift Phiris are one person, and
-- the reporter looking at both clubs can. merge_players is still there for
-- when they get it wrong.
--
-- WHAT THIS DOES NOT DO. rename_player still refuses to rename INTO a name
-- another player holds. That refusal is unchanged on purpose: renaming into a
-- collision is nearly always the mistake 0022 described, and the legitimate
-- case — a genuinely different person — now has create_player(force) as its
-- front door instead. Nothing here is admin-gated either: creating a row is
-- not destructive, it happens at a touchline, and merge_players (which IS
-- admin-only, because it deletes) is the undo.

begin;

-- ── search_players ───────────────────────────────────────────────────────────
-- The matching half is 0022's, unchanged and re-stated here because a
-- create-or-replace cannot add a column to a returns-table. What is new is
-- everything after `ranked`.
--
-- WHY A LATERAL, AND WHY AFTER THE LIMIT. The club lookup runs for the twelve
-- rows that survived ranking, not for every candidate — twelve index probes on
-- lineups_player_idx and goals_player_idx rather than a scan of both tables
-- joined to matches. It costs the same at 800 players as at 80,000.
--
-- WHY own_goal IS EXCLUDED. goals.team_id names the BENEFICIARY (DATA_MODEL,
-- and src/adapt.py depends on it), so an own goal's team_id is the scorer's
-- OPPONENT. Counting one would file a player at the club he scored against,
-- which is precisely the wrong answer to the only question this column exists
-- to answer. A team sheet has no such ambiguity, and is the stronger signal
-- anyway: it says he was in the squad, not merely that he touched the ball.

drop function if exists public.search_players(text);

create function public.search_players(p_term text)
returns table (
  player_id text,
  full_name text,
  known_as  text,
  matched   text,     -- the spelling that matched, when it was an alias
  score     integer,
  -- Up to two squad display names, most recent first, joined for display:
  -- "Mzuzu City Hammers Youth". Two rather than one because a player who
  -- moved in July is still findable under the club the reporter remembers,
  -- and rather than all of them because this renders on a 390px row.
  teams     text,
  -- The same squads as ids, so a caller can ask "is this one of the two teams
  -- in the match on screen?" without matching on display names.
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
    -- The surname branch. Guarded on a surname of three characters or more:
    -- below that it is an initial or a particle and matches half the country.
    select c.player_id, c.full_name, c.known_as, '' as matched, 30
    from candidates c, parts x
    where length(x.surname) >= 3
      and c.surname = x.surname
      and (x.initial = c.initial
           or x.surname = x.lt          -- they typed a surname alone
           or c.surname = c.label)      -- we hold a surname alone
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
               -- Most recent first, because "who is he playing for now" is
               -- the question a reporter is actually asking. An unscheduled
               -- fixture has no date at all (matches.date is nullable), so
               -- those sort last rather than swallowing the ordering; how
               -- often he was named breaks the tie.
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
      ) a
      join public.teams tm on tm.team_id = a.team_id
      group by tm.team_id, tm.display_name
    ) x
    where x.rn <= 2
  ) s on true
  order by r.score desc, r.full_name;
$$;

comment on function public.search_players(text) is
  'Player lookup for the reporter portal: substring, alias, then surname with '
  'agreeing initials, ranked — plus the clubs each one has actually been '
  'named for, which is what tells two players of the same name apart.';

revoke execute on function public.search_players(text) from public;
grant execute on function public.search_players(text) to anon, authenticated;


-- ── create_player ────────────────────────────────────────────────────────────
-- Unchanged but for p_force, and re-created rather than replaced only because
-- adding a parameter to a function PostgREST resolves by argument name would
-- otherwise leave TWO create_players in the schema and make every one-argument
-- call ambiguous. The default keeps those one-argument calls working, which
-- matters: app.js reaches a phone on its own schedule, and a portal loaded
-- before this migration lands must keep saving players.

drop function if exists public.create_player(text);

create function public.create_player(
  p_full_name text,
  -- Skip the name lookup and insert regardless: THIS IS A DIFFERENT PERSON.
  -- Only ever sent by the portal's second create button, which appears only
  -- when an exact name match is already on screen with its club beside it.
  p_force     boolean default false
)
returns setof public.players
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_name     text;
  v_player   public.players;
begin
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  -- Collapse runs of whitespace as well as trimming: "Thandiwe  Phiri" and
  -- "Thandiwe Phiri" are one person, and the difference is invisible on screen.
  v_name := btrim(regexp_replace(coalesce(p_full_name, ''), '\s+', ' ', 'g'));

  if length(v_name) < 2 then
    raise exception 'a player needs a name' using errcode = '22023';
  end if;
  if length(v_name) > 80 then
    raise exception 'that name is too long' using errcode = '22023';
  end if;

  -- Idempotent on the name, case-insensitively, and matching known_as as well
  -- as full_name: the picker shows a player under whichever of the two reads
  -- as their name, so typing what is on screen must find them rather than
  -- clone them. Lowest ord wins, so the answer is stable if duplicates already
  -- exist from before this migration.
  --
  -- p_force is the one case where that is the wrong answer, and the caller has
  -- to have LOOKED at the existing player to reach it — see the header.
  if not p_force then
    select * into v_player
    from public.players p
    where lower(p.full_name) = lower(v_name)
       or (p.known_as <> '' and lower(p.known_as) = lower(v_name))
    order by p.ord
    limit 1;
    if found then
      return next v_player;
      return;
    end if;
  end if;

  insert into public.players (player_id, full_name, status)
  values (public.next_player_id(), v_name, 'active')
  returning * into v_player;

  return next v_player;
end;
$$;

comment on function public.create_player(text, boolean) is
  'Create a player from a name, idempotent on that name. p_force inserts '
  'anyway: two real people may share one name (0034).';

revoke execute on function public.create_player(text, boolean) from public, anon;
grant execute on function public.create_player(text, boolean) to authenticated;

commit;
