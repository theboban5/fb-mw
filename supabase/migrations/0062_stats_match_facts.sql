-- 0062_stats_match_facts.sql — one row per played match, for #/stats.
--
-- WHAT WAS WRONG. The reporters asked for the sentences they post: "27 goals
-- on matchday 15 in the FDH Bank Premiership", "4 red cards this weekend
-- across all leagues", clean sheets, and which referee shows the most cards.
-- The only aggregate in the portal is the Compare tab (0039), and it cannot
-- say any of them: it is admin-only, it is grouped by competition-season so
-- it has no idea what a weekend is, and it knows nothing about cards.
--
-- WHAT THIS DOES. `stats_match_facts`: one row per played match, every
-- season, carrying raw counts — the score, each side's cards off its team
-- sheet, the goal rows by type, and the referee. The browser filters by
-- period server-side (a date range, a season, or one competition's matchday)
-- and by competition locally, so any combination of competitions is a chip
-- tap with no round-trip. 0039's argument for counts over rates, unchanged:
-- every figure on the screen is a sum or a ratio of two sums, regrouped by
-- competition, team or referee in the browser, with one SQL definition of
-- each count and no second definition of "goals per match" to drift.
--
-- Open to every active reporter, not only admins. Everything here is already
-- public on everyleague.co — matches, goals and lineups have carried a
-- public read policy since 0001 — so the gate is "signed into the portal",
-- the same as the portal itself. anon is still revoked: an unauthenticated
-- aggregate endpoint on a public database is a cost with no reader.
--
-- WHAT COUNTS. status 'played', source_type <> 'placeholder', both goals
-- present. NOT 'awarded': a walkover's 3-0 is a ruling, and "goals this
-- weekend" counting goals nobody scored would be the first thing a reader
-- caught. (The Compare tab includes walkovers; it measures results, this
-- measures football.)
--
-- CARDS ARE A TEAM SHEET'S. They live on lineups (0018) and nowhere else, so
-- a side with no sheet has no cards on the record — not zero cards. Each side
-- says whether it has a sheet, and the screen prints the coverage beside
-- every card figure. Counted the way src/lineups.cards_html draws them: a
-- yellow is yellow_card without yellow_red_card; a second yellow is its own
-- count; a straight red is red_card. "Red cards" on the screen is the last
-- two together — both are a sending-off.
--
-- THE TRADE. Today 34 matches have a team sheet and 16 name a referee, so the
-- card and referee figures will be honest and thin for a while. The screen
-- says how thin rather than hiding the section; the officials prompt (0061)
-- and the match-report import (0054) are what fill it.

begin;

create view public.stats_match_facts with (security_invoker = true) as
with played as (
  select m.*
  from public.matches m
  where m.status = 'played'
    and m.source_type <> 'placeholder'
    and m.home_goals is not null
    and m.away_goals is not null
),
cards as (
  select l.match_id, l.team_id,
         count(*)::integer                                                    as sheet_rows,
         count(*) filter (where l.yellow_card and not l.yellow_red_card)::integer as yellow,
         count(*) filter (where l.yellow_red_card)::integer                   as second_yellow,
         count(*) filter (where l.red_card)::integer                          as red
  from public.lineups l
  group by l.match_id, l.team_id
),
goal_types as (
  select g.match_id,
         count(*)::integer                                                as goal_rows,
         count(*) filter (where g.goal_type = 'penalty')::integer         as g_penalty,
         count(*) filter (where g.goal_type = 'free_kick')::integer       as g_free_kick,
         count(*) filter (where g.goal_type = 'header')::integer          as g_header,
         count(*) filter (where g.goal_type = 'open_play')::integer       as g_open_play,
         count(*) filter (where g.goal_type = 'own_goal')::integer        as g_own_goal,
         count(*) filter (where coalesce(g.goal_type, '') = '')::integer  as g_untyped
  from public.goals g
  group by g.match_id
)
select
  p.match_id,
  p.public_id,
  p.competition_id,
  p.season_id,
  coalesce(nullif(cs.sponsor_name, ''), c.name)              as competition_name,
  c.type                                                     as competition_type,
  c.level,
  case when c.gender = 'w' then 'women'
       when c.age_group <> 'senior' then 'youth'
       else 'men' end                                        as category,
  p.date,
  p.matchday,
  p.stage,

  p.home_team_id,
  p.away_team_id,
  coalesce(nullif(ht.display_name, ''), p.home_team_id)      as home_name,
  coalesce(nullif(at.display_name, ''), p.away_team_id)      as away_name,
  p.home_goals,
  p.away_goals,

  (hc.match_id is not null)                                  as home_sheet,
  (ac.match_id is not null)                                  as away_sheet,
  coalesce(hc.yellow, 0)                                     as home_yellow,
  coalesce(ac.yellow, 0)                                     as away_yellow,
  coalesce(hc.second_yellow, 0)                              as home_second_yellow,
  coalesce(ac.second_yellow, 0)                              as away_second_yellow,
  coalesce(hc.red, 0)                                        as home_red,
  coalesce(ac.red, 0)                                        as away_red,

  coalesce(gt.goal_rows, 0)                                  as goal_rows,
  coalesce(gt.g_penalty, 0)                                  as g_penalty,
  coalesce(gt.g_free_kick, 0)                                as g_free_kick,
  coalesce(gt.g_header, 0)                                   as g_header,
  coalesce(gt.g_open_play, 0)                                as g_open_play,
  coalesce(gt.g_own_goal, 0)                                 as g_own_goal,
  coalesce(gt.g_untyped, 0)                                  as g_untyped,

  -- A name is a label on an id (CLAUDE.md): where the referee resolved, the
  -- registry's name, so a renamed referee is one row in the table and not two.
  nullif(btrim(p.referee), '')                               as referee,
  nullif(p.referee_id, '')                                   as referee_id,
  coalesce(nullif(o.known_as, ''), o.full_name,
           nullif(btrim(p.referee), ''))                     as referee_name
from played p
join public.competitions c         on c.competition_id = p.competition_id
join public.competition_seasons cs on cs.competition_id = p.competition_id
                                  and cs.season_id = p.season_id
left join public.teams ht   on ht.team_id = p.home_team_id
left join public.teams at   on at.team_id = p.away_team_id
left join cards hc          on hc.match_id = p.match_id and hc.team_id = p.home_team_id
left join cards ac          on ac.match_id = p.match_id and ac.team_id = p.away_team_id
left join goal_types gt     on gt.match_id = p.match_id
left join public.officials o on o.official_id = nullif(p.referee_id, '')
where public.current_reporter_id() is not null;

comment on view public.stats_match_facts is
  'Any active reporter. One row per played (not awarded) match across every '
  'season: score, per-side cards off the team sheet with whether a sheet '
  'exists, goal rows by type, and the referee. Raw counts for #/stats.';

revoke all on public.stats_match_facts from anon;
grant select on public.stats_match_facts to authenticated;

commit;
