-- 0011_consume_rebuild_pending.sql — make `pending` mean something.
--
-- THE GAP THIS CLOSES. claim_rebuild debounces: a publish inside the cooldown
-- does not dispatch a build, it sets rebuild_state.pending instead. 0004's
-- comment justifies that by observing the running build reads the database
-- "a good half-minute after it was dispatched", so a change made during the
-- cooldown is *almost always* swept up by the build already on its way.
--
-- Almost. On 2026-08-16 two results were published 26 seconds apart:
--
--   09:08:35.195  Agumbala Stars v Yizo Yizo published
--   09:08:35.749  claimed -> build dispatched, 60s cooldown starts
--   09:09:01.287  Brothers in Arms v Ndirande Dortmund published
--   09:09:01.580  folded into the cooldown: pending = true, no dispatch
--
-- The running build had already read Supabase by 09:09:01, so the second
-- result was in neither build. `pending` recorded it faithfully and nothing
-- read the flag — no workflow, no function — so the only backstop was the
-- 05:07 UTC cron, twenty hours away. A reporter watched a result they had
-- entered correctly fail to appear, which is the exact failure this whole
-- pipeline exists to prevent.
--
-- This will get MORE common, not less: the /report "next match" button is
-- built to make a reporter work through a matchday quickly, and quickly means
-- publishes less than a minute apart.
--
-- THE FIX is one atomic test-and-clear, called by a workflow that runs after
-- a successful deploy. If a report landed while the build was running, the
-- flag is consumed and one more build is dispatched.
--
-- WHY IT CANNOT LOOP. The flag is CLEARED by the same statement that reports
-- it, so a follow-up build finds pending=false unless something new was
-- published in the meantime — in which case another build is exactly what is
-- wanted. Worst case is one extra build per burst of reports.
--
-- It also books the dispatch (last_dispatched_at, dispatch_count) exactly as
-- claim_rebuild does. A follow-up IS a dispatch; recording it keeps the
-- cooldown honest, so a reporter publishing at that moment is debounced
-- against the real state rather than a stale timestamp.
--
-- CALL THIS ONLY AFTER THE BUILD HAS ACTUALLY BEEN DISPATCHED. The name says
-- consume, and consuming is destructive: this clears the only record that a
-- result is unbuilt. The follow-up workflow's first live run called it first
-- and then failed to dispatch, which destroyed the signal it existed to act
-- on. The caller must look at `pending`, dispatch, and only then consume — so
-- that anything going wrong leaves the flag set for the next attempt.



begin;

create or replace function public.consume_rebuild_pending()
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
  -- Test and clear in ONE statement. Reading the flag and then clearing it
  -- would drop a publish that landed between the two, which is the same class
  -- of race this function exists to close.
  update public.rebuild_state
  set pending            = false,
      last_dispatched_at = now(),
      dispatch_count     = dispatch_count + 1,
      updated_at         = now()
  where id and pending;

  -- FOUND, not a value read back: it is set by the UPDATE itself and is never
  -- NULL. 0006 exists because `returning true into ...` gave NULL when no row
  -- matched, and `not NULL` silently never fired.
  return found;
end;
$$;

revoke execute on function public.consume_rebuild_pending()
  from public, anon, authenticated;
grant execute on function public.consume_rebuild_pending() to service_role;

comment on function public.consume_rebuild_pending() is
  'Returns true if a report landed while a build was running, and clears the '
  'flag in the same statement. Called once after a successful deploy; a true '
  'answer means dispatch one more build. EXECUTE is service_role only, for '
  'the same reason as claim_rebuild: dispatching a deploy costs build '
  'minutes and a reporter must not be able to do it directly.';

commit;
