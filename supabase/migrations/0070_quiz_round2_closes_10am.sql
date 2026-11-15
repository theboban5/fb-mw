-- 0070_quiz_round2_closes_10am.sql — Round 2 of Know Your Flames closes at
-- 10:00 CAT on Monday 16 November, not 15:00.
--
-- WHAT WAS WRONG. Nothing, as such: 0067 closed Round 2 exactly 24 hours
-- before kick-off in Lilongwe. The organisers want the morning instead, so
-- the draw and the calls to the two ticket winners happen in working hours
-- with the rest of Monday to sort out collection.
--
-- WHAT THIS DOES. Moves flames-egypt-2's closes_at to 10:00 CAT on the 16th.
-- The opening (0069) is unchanged. src/quiz.py carries the same time, and
-- tests/test_quiz.py reads 0067's seed with 0069 and this on top.
--
-- Deploy the page first, then this: a page that closes early only hides the
-- form, but a database that closes first refuses entries a page still asks for.

begin;

update public.quizzes set closes_at = '2026-11-16 10:00+02'
  where quiz_id = 'flames-egypt-2';

commit;
