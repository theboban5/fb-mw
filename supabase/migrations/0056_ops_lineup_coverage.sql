-- 0056_ops_lineup_coverage.sql — which teams' team sheets are still missing.
--
-- WHAT WAS WRONG. Team sheets now arrive a club report at a time (0054–0055),
-- and the question that decides where the next hour goes is "whose reports
-- haven't I found yet?". Nothing could answer it. ops_match_flags has a flag
-- per backlog category and none of them is a team sheet, and a sheet belongs
-- to a SIDE rather than a match — one club's report is one sheet, so "this
-- match has a lineup" is half an answer at best. At the time of writing 42 of
-- the season's 1,944 possible sheets exist, 22 of them in the Super League.
--
-- WHAT THIS DOES. Two views, 0016's arrangement exactly:
--
--   * ops_lineup_sides — one row per side of every PLAYED match of the active
--     season, with has_lineup. It is the drill-down: tapping "5 missing" opens
--     this filtered by team, competition and has_lineup = false.
--   * ops_team_lineups — the same rows counted per team per competition. It
--     is the list of chips.
--
-- The second selects from the first, so the number on a chip and the list it
-- opens cannot disagree — the reason 0016 used views at all.
--
-- 'played' only. A walkover ('awarded') has no team sheet to find, and an
-- abandoned match rarely does; counting either would leave a team red for a
-- report that does not exist. Any lineups row for the side counts as a sheet:
-- a partial one is a different job (it is open on the match screen already),
-- and "has nothing at all" is the one this list is for.
--
-- THE TRADE. A team is counted per competition, not per club, so Moyale
-- Barracks in the Super League and the Airtel Top 8 are two rows. That is the
-- unit a sheet is filed under, and the one the tab groups by.

begin;

create view public.ops_lineup_sides with (security_invoker = true) as
select
  f.match_id,
  f.public_id,
  f.competition_id,
  f.season_id,
  f.competition_type,
  f.competition_tier,
  f.round_key,
  f.date,
  f.kickoff,
  f.home_name,
  f.away_name,
  f.home_goals,
  f.away_goals,
  f.is_pre_tracker,
  s.team_id,
  s.team_name,
  s.is_home,
  exists (select 1 from public.lineups l
          where l.match_id = f.match_id and l.team_id = s.team_id) as has_lineup
from public.ops_match_flags f
cross join lateral (values
  (f.home_team_id, f.home_name, true),
  (f.away_team_id, f.away_name, false)
) as s (team_id, team_name, is_home)
where f.status = 'played'
  and public.is_admin();

comment on view public.ops_lineup_sides is
  'Admin only. One row per side of each played active-season match, with '
  'whether that side has a team sheet. The Lineups tab''s drill-down.';


create view public.ops_team_lineups with (security_invoker = true) as
select
  x.competition_id,
  coalesce(nullif(cs.sponsor_name, ''), c.name)            as competition_name,
  x.competition_tier,
  x.team_id,
  x.team_name,
  count(*)::integer                                        as played,
  count(*) filter (where x.has_lineup)::integer            as with_lineup,
  count(*) filter (where not x.has_lineup)::integer        as missing
from public.ops_lineup_sides x
join public.competitions c on c.competition_id = x.competition_id
join public.competition_seasons cs
  on cs.competition_id = x.competition_id and cs.season_id = x.season_id
where public.is_admin()
group by x.competition_id, cs.sponsor_name, c.name, x.competition_tier,
         x.team_id, x.team_name;

comment on view public.ops_team_lineups is
  'Admin only. Team sheets per team per competition this season: played, '
  'with a sheet, missing. Counts ops_lineup_sides, so a chip and its list agree.';


-- anon revoked outright (this project's default privileges grant it new
-- objects — 0048); authenticated may select, and gets zero rows unless
-- is_admin() in the body says otherwise.
revoke all on public.ops_lineup_sides from anon;
revoke all on public.ops_team_lineups from anon;

grant select on public.ops_lineup_sides to authenticated;
grant select on public.ops_team_lineups to authenticated;

commit;
