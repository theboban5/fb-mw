-- 0069_quiz_round2_midnight.sql — Round 2 of Know Your Flames opens at
-- midnight, not 23:00.
--
-- WHAT WAS WRONG. 0067 opened Round 2 at 23:00 CAT on 13 November, about two
-- hours after kick-off in Cairo. Its first question is the Cairo score, and
-- the answer is on the Flames Results page only once somebody has entered the
-- result in /report and the site has rebuilt. An hour is too tight for that
-- on a match night: a fan sent to "Find it" would find the fixture still
-- unplayed.
--
-- WHAT THIS DOES. Moves flames-egypt-2's opens_at to 00:00 CAT on the 14th.
-- The close is unchanged. src/quiz.py carries the same time for the page, and
-- tests/test_quiz.py reads 0067's seed with this update applied on top.
--
-- The page and this row must move together: if only this one moved, the page
-- would show the form at 23:00 and every entry would come back "not opened
-- yet" for an hour. Deploy the page first, then this.

begin;

update public.quizzes set opens_at = '2026-11-14 00:00+02'
  where quiz_id = 'flames-egypt-2';

commit;
