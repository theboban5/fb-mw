-- 0036_youth_team_age_signifier.sql — a youth team's name says its age group.
--
-- WHAT WAS WRONG. A youth team's display_name was indistinguishable from its
-- club's other teams by name alone: "Ekhaya" (u19), "Ekhaya FC" (senior) and
-- "Ekhaya Reserve" (senior, squad_level 3) all sat on the same club hub with
-- nothing in the u19 team's name saying which age group it was. Bare-id U16
-- clubs (MW_U16_BLU etc, team_id == club_id) were worse: "Blantyre United"
-- with no youth marker at all, reading exactly like a senior club.
--
-- WHAT THIS DOES. Appends the team's own age_group, uppercased, to its
-- display_name — "Ekhaya" -> "Ekhaya U19", "Blantyre FC" -> "Blantyre FC
-- U16" — for every team whose age_group is not 'senior'. The signifier
-- always matches teams.age_group, which is also what every youth
-- competition's own age_group is drawn from (adapt.py joins teams to
-- competitions through entries, never by parsing a name), so the label on
-- the team is the label on the league it plays in.
--
-- WHAT IT DELIBERATELY DOES NOT TOUCH.
--   Senior teams. age_group = 'senior' is excluded outright — this is a
--   youth-only change.
--   club_id / team_id / legacy_code. The club connection, every join through
--   it, and every public URL keyed on legacy_code are unchanged. A club hub
--   still groups teams by club_id; it now tells them apart by name too:
--   "Ekhaya", "Ekhaya Reserve", "Ekhaya U19".
--
-- A handful of Mzuzu District youth teams (MW_MA_M1, MW_A1A_M1, MW_YB_M1,
-- MW_CRS2_M1, MW_GS2_M1, MW_MT2_M1, MW_CU2_M1, MW_TA_M1, MW_MA2_M1,
-- MW_VSA2_M1, MW_VA2_M1, MW_MW2_M1, MW_CA_M1, MW_HY_M1, MW_HU_M1) hold
-- entries in more than one age-group league at once (MW_MDU14/16/20) under a
-- single team row — a pre-existing entries mismatch this migration does not
-- resolve. Their signifier here follows teams.age_group, which matches the
-- only competition any of them has actually played a match in.
--
-- Idempotent: the where clause only matches rows that don't already end with
-- their own age group.

begin;

update public.teams
   set display_name = display_name || ' ' || upper(age_group)
 where age_group <> 'senior'
   and display_name !~ (' ' || upper(age_group) || '$');

commit;
