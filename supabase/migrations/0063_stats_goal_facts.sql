-- 0063_stats_goal_facts.sql — who scored, for #/stats.
--
-- WHAT WAS WRONG. #/stats (0062) can say "27 goals on matchday 15" and not
-- who scored them. stats_match_facts is a row per MATCH, and a scorer table
-- is a question about goals. The site has always had one per competition
-- and season (src/scorers.py), but nothing could answer "top scorers in
-- these two leagues since 1 October", which is the line that goes under the
-- matchday graphic.
--
-- WHAT THIS DOES. `stats_goal_facts`: one row per goal that would rank in a
-- scorer table, on exactly the matches stats_match_facts counts (played, not
-- awarded, not placeholder, scored), with the match's date, round and
-- competition beside it, so the screen filters goals by the same period and
-- the same competition chips as everything else.
--
-- The site's rules, unchanged (src/adapt.py, src/scorers.py):
--
--   * OWN GOALS NEVER RANK. They are left out of this view entirely; the
--     match view already counts them as g_own_goal.
--   * A NAME IS A LABEL ON AN ID. An identified scorer's name comes from
--     `players`, so a rename reaches this table.
--   * AN UNIDENTIFIED SCORER STILL RANKS, under the name the reporter typed,
--     with a NULL player_id so the screen links nothing. CAF_MW_UNKNOWN never
--     leaves this view as an id. A goal with neither an id nor a typed name
--     has nothing to show and is skipped, as on the site.
--   * An assist is only ever an id: there is no typed assister to fall back
--     on, so an unresolved one is NULL.
--
-- Same gate as 0062: any active reporter, anon revoked. All of it is public
-- on everyleague.co already.
--
-- THE TRADE. Coverage. The Super League names every scorer and the Women's
-- Premiership names none, so a top-scorer list over both is really a list of
-- one. The screen prints "scorers named for N of M goals", and per
-- competition where that is short. The copyable line only names a top scorer
-- when every goal in scope has a scorer, by the rule 0062 applies to cards.

begin;

create view public.stats_goal_facts with (security_invoker = true) as
select
  g.goal_id,
  g.match_id,
  m.public_id,
  m.competition_id,
  m.season_id,
  m.date,
  m.matchday,
  m.stage,
  g.team_id,
  coalesce(nullif(t.display_name, ''), g.team_id)                 as team_name,
  nullif(g.player_id, 'CAF_MW_UNKNOWN')                           as player_id,
  case when g.player_id = 'CAF_MW_UNKNOWN'
       then btrim(g.reported_player_name)
       else coalesce(nullif(p.known_as, ''), nullif(p.full_name, ''), g.player_id)
  end                                                             as player_name,
  g.goal_type,
  case when a.player_id is not null and a.player_id <> 'CAF_MW_UNKNOWN'
       then a.player_id end                                       as assist_player_id,
  case when a.player_id is not null and a.player_id <> 'CAF_MW_UNKNOWN'
       then coalesce(nullif(a.known_as, ''), nullif(a.full_name, ''), a.player_id)
  end                                                             as assist_name
from public.goals g
join public.matches m    on m.match_id = g.match_id
left join public.teams t on t.team_id = g.team_id
left join public.players p on p.player_id = g.player_id
left join public.players a on a.player_id = g.assist_player_id
where m.status = 'played'
  and m.source_type <> 'placeholder'
  and m.home_goals is not null
  and m.away_goals is not null
  and g.goal_type <> 'own_goal'
  and not (g.player_id = 'CAF_MW_UNKNOWN' and btrim(g.reported_player_name) = '')
  and public.current_reporter_id() is not null;

comment on view public.stats_goal_facts is
  'Any active reporter. One row per goal that ranks in a scorer table (own '
  'goals excluded), on the played matches stats_match_facts counts. An '
  'unidentified scorer has a NULL player_id and the name as reported.';

revoke all on public.stats_goal_facts from anon;
grant select on public.stats_goal_facts to authenticated;

commit;
