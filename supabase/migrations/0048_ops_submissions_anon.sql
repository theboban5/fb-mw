-- 0048_ops_submissions_anon.sql — the revoke 0047 forgot.
--
-- WHAT WAS WRONG. 0047 created ops_submissions and granted select to
-- authenticated, which is half of what every other ops view does. The other
-- half is `revoke all ... from anon`, and it is not decoration: THIS PROJECT'S
-- DEFAULT PRIVILEGES GRANT NEW OBJECTS TO anon. Both 0016 and 0039 revoke
-- explicitly for exactly that reason — 0039 even says so in a comment two
-- lines above the grant — and a view created without the revoke is readable by
-- an anonymous request.
--
-- WHAT IT COST, precisely: nothing yet, and the reason is worth stating rather
-- than being relieved about. The view carries `where public.is_admin()` in its
-- own body, so an anonymous read returned 200 and an empty array. No row ever
-- left the database. What leaked was the object's existence — anon got an
-- answer where every other ops view gives a 401 — and the defence in depth
-- that the other five have and this one did not.
--
-- It was caught by test_ops_live's access sweep, which asserts "not 'sees no
-- rows' — REFUSED" against every view in one list. That list is the reason
-- this took a minute to find rather than a year: adding ops_submissions to it
-- was one line, and the test that already existed did the rest.
--
-- Fixed here rather than in 0047 because 0047 is applied — the house rule, and
-- the honest version anyway: the mistake happened, and a migration that quietly
-- becomes correct after the fact is a migration nobody can learn from.

begin;

revoke all on public.ops_submissions from anon;

commit;
