-- 0074_derby_opens_now.sql — the Derby race opens Wednesday 15:00 CAT, not
-- Thursday 00:00.
--
-- WHAT WAS WRONG. Nothing. The race was promoted on the site at lunchtime on
-- Wednesday 18 Nov, and the organisers asked for it to start then rather
-- than at midnight: the banner was already sending people to a page saying
-- "points start Thursday", and a referral made in between would not count.
--
-- WHAT THIS DOES. Moves derby-2026's opens_at. referral_rows reads the
-- window from this row, so the public count, the admin race and the draw
-- move together; src/derby.py says the same time (test_derby compares).
--
-- THE TRADE. None that lands on anyone: at the moment of the change no
-- sign-up had a referred_by (the three test rows were deleted and the two
-- real people they had brought detached), so no count goes up or down.
-- Applied first through PostgREST with the secret key, so the race opened
-- without waiting for a db push; this file is the record, and re-running it
-- sets the same value.

begin;

update public.referral_campaigns set opens_at = '2026-11-18 15:00+02'
where campaign_id = 'derby-2026';

commit;
