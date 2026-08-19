-- 0027_rename_bdu14.sql — the Nthanda U14 League says which district it is.
--
-- WHAT WAS WRONG. MW_BDU14 was called "Nthanda U14 League", which is what the
-- competition is called by the people running it and not what it is called
-- anywhere else. Malawi has more than one Nthanda youth competition and this
-- one is the Blantyre district's; a reader landing on /bdu14/ from a search
-- result, or scanning the youth group on the home page, had nothing on the
-- page telling them which district's league they were looking at.
--
-- WHAT THIS DOES. Renames the row. That is the whole change:
--
--   Nthanda U14 League  ->  Blantyre District Nthanda U14 League
--
-- WHAT IT DELIBERATELY DOES NOT TOUCH.
--
--   The id. MW_BDU14 already encoded "Blantyre district U14" and stays exactly
--   as it is — an id is opaque here, nothing parses one, and rewriting it
--   would mean rewriting competition_seasons, entries and every matches row
--   that points at it to change a string nobody reads.
--
--   The URL. src/adapt.py:competition_slug derives the slug from the id, never
--   from the name, so the league stays at /bdu14/ and every link to it, on
--   this site and off it, still resolves. That is the reason the slug was
--   built off the id in the first place.
--
--   age_group. It stays 'u14'. The competition IS a U14 competition — the
--   rename adds the district to the name and says nothing about the age
--   group — so the U14 badge build.py renders beside the title stays right,
--   and so do the sixteen u14 team rows entered in it.
--
-- WHY A MIGRATION for one string. There is no admin UI for competitions: the
-- portal edits matches, players, officials and reporters, and a competitions
-- row is only ever changed by hand. Doing this one by hand in the SQL editor
-- would leave nothing in git saying the league was renamed or why, and the
-- next person to find "Blantyre District" in the data and "Nthanda U14" in an
-- old screenshot would have no way to tell a rename from a second competition.
-- This is that record. It is idempotent — the where clause matches nothing on
-- a second run.

begin;

update public.competitions
   set name = 'Blantyre District Nthanda U14 League'
 where competition_id = 'MW_BDU14'
   and name <> 'Blantyre District Nthanda U14 League';

commit;
