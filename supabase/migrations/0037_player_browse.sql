-- 0037_player_browse.sql — finding a player to fix does not start with a name.
--
-- WHAT WAS WRONG. The `#/players` screen (0022) can only be reached by typing
-- at least two letters of a name into search_players, which ranks candidates
-- against a typed guess and returns at most twelve. That is the right shape
-- for "a reporter just typed a name and needs to confirm it resolved to the
-- right person" — it is the wrong shape for the actual admin job of cleaning
-- up duplicates: "show me everyone ever named for Mzuzu City Hammers Youth"
-- or "walk the whole U20 roster and see what looks wrong". There was no way
-- to browse at all, only to guess a spelling and search for it. Finding the
-- second Steve Phiri (0034) meant already knowing to look for "Steve Phiri".
--
-- WHAT THIS DOES. browse_players(p_term, p_team_id, p_competition_id,
-- p_limit, p_offset): a listing, not a ranking. p_term is optional — blank
-- lists every player alphabetically — and p_team_id/p_competition_id narrow
-- the list to players who actually turn out for that team or in that
-- competition. "Actually turn out for" is derived from lineups and goals
-- joined through matches, never stored, for the same reason 0034 derives
-- search_players' club hints that way: players carries no club column and
-- should not get one, because a career is not a column. Own goals are
-- excluded from the goals side of that join for the same reason 0034
-- excludes them from search_players — goals.team_id names the beneficiary,
-- so an own goal's team_id is the scorer's opponent.
--
-- p_competition_id alone (no team) is what makes "walk the U20 roster"
-- possible without already knowing every club in it. p_team_id alone (no
-- competition) is what makes "everyone who has worn this shirt" possible
-- across however many competitions or seasons that team has played in — a
-- club that moved divisions keeps its history findable by team either way.
--
-- total_count rides along on every row as a window function so the portal
-- can offer "Load more" against a stable count without a second query, the
-- same trade browse screens elsewhere in Postgrest-fronted apps make.
--
-- WHAT THIS DOES NOT DO. It does not replace search_players — the merge
-- picker still needs a ranked guess against a typed name, not a filtered
-- list, and changing its shape would touch the team-sheet and scorer pickers
-- that depend on it. It also does not add a season filter: a player's whole
-- history at a club is the useful browsing unit, and a name that turns out
-- to be two people is exactly as findable across seasons as within one.

begin;

create function public.browse_players(
  p_term           text    default null,
  p_team_id        text    default null,
  p_competition_id text    default null,
  p_limit          integer default 40,
  p_offset         integer default 0
)
returns table (
  player_id   text,
  full_name   text,
  known_as    text,
  -- Same shape as search_players' club hints (0034), for the same reason:
  -- a name alone does not tell two Phiris apart.
  teams       text,
  team_ids    text[],
  total_count bigint
)
language sql
stable
security definer
set search_path = ''
as $$
  with term as (
    select nullif(
      btrim(regexp_replace(coalesce(p_term, ''), '\s+', ' ', 'g')), ''
    ) as t
  ),
  -- Only built when a team or competition filter is actually given — an
  -- unfiltered browse (or a term-only search) has no reason to touch
  -- lineups/goals/matches at all.
  filtered as (
    select distinct a.player_id
    from (
      select l.player_id, l.team_id, m.competition_id
      from public.lineups l
      join public.matches m on m.match_id = l.match_id
      union all
      select g.player_id, g.team_id, m.competition_id
      from public.goals g
      join public.matches m on m.match_id = g.match_id
      where g.goal_type <> 'own_goal'
    ) a
    where (p_team_id is null or a.team_id = p_team_id)
      and (p_competition_id is null or a.competition_id = p_competition_id)
  ),
  base as (
    select p.player_id, p.full_name, p.known_as
    from public.players p, term x
    where p.player_id <> 'CAF_MW_UNKNOWN'
      and (
        (p_team_id is null and p_competition_id is null)
        or p.player_id in (select player_id from filtered)
      )
      and (x.t is null
           or lower(p.full_name) like '%' || lower(x.t) || '%'
           or lower(p.known_as) like '%' || lower(x.t) || '%')
  ),
  counted as (
    select b.*, count(*) over ()::bigint as total_count
    from base b
    order by lower(coalesce(nullif(b.known_as, ''), b.full_name))
    limit greatest(p_limit, 0)
    offset greatest(p_offset, 0)
  )
  select c.player_id, c.full_name, c.known_as,
         coalesce(s.teams, '') as teams,
         coalesce(s.team_ids, array[]::text[]) as team_ids,
         c.total_count
  from counted c
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
        where l.player_id = c.player_id
        union all
        select g.team_id, m.date
        from public.goals g
        join public.matches m on m.match_id = g.match_id
        where g.player_id = c.player_id and g.goal_type <> 'own_goal'
        union all
        select g.team_id, m.date
        from public.goals g
        join public.matches m on m.match_id = g.match_id
        where g.assist_player_id = c.player_id and g.goal_type <> 'own_goal'
      ) a
      join public.teams tm on tm.team_id = a.team_id
      group by tm.team_id, tm.display_name
    ) x
    where x.rn <= 2
  ) s on true
  order by lower(coalesce(nullif(c.known_as, ''), c.full_name));
$$;

comment on function public.browse_players(text, text, text, integer, integer) is
  'Players screen listing for the reporter portal (0037): optional name '
  'filter, optional team/competition filter derived from lineups+goals, '
  'paginated with a running total_count. search_players (0034) stays the '
  'ranked picker every other screen uses; this is the browse behind '
  '#/players so an admin can find a duplicate without already knowing its '
  'spelling.';

revoke execute on function
  public.browse_players(text, text, text, integer, integer) from public, anon;
grant execute on function
  public.browse_players(text, text, text, integer, integer) to authenticated;

commit;
