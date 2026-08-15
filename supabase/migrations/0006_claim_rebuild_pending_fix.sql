-- 0006_claim_rebuild_pending_fix.sql — three-valued logic bug in claim_rebuild.
--
-- 0004 wrote:
--
--     update ... returning true into v_claimed;
--     if not v_claimed then ... set pending = true ... end if;
--
-- When the UPDATE matches no row — the coalesced case, which is the common one
-- — `INTO` assigns NULL rather than leaving the variable at its initialised
-- false. `not NULL` is NULL, not true, so the branch never ran and `pending`
-- was never set. The function still RETURNED the right answer (the final
-- coalesce saw to that), so debouncing worked correctly and only the
-- diagnostic flag was silently dead.
--
-- FOUND is the right test: it is set by the UPDATE itself and is never NULL.

begin;

create or replace function public.claim_rebuild(p_cooldown_seconds integer default 60)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
  -- Atomic: the WHERE clause is the decision. A second caller arriving while
  -- this runs waits on the row lock, then re-evaluates against the timestamp
  -- this statement just wrote, and so fails the condition.
  update public.rebuild_state
  set last_dispatched_at = now(),
      pending            = false,
      dispatch_count     = dispatch_count + 1,
      updated_at         = now()
  where id
    and (last_dispatched_at is null
         or now() - last_dispatched_at
            > make_interval(secs => greatest(p_cooldown_seconds, 0)));

  if found then
    return true;
  end if;

  -- Folded into the run already on its way. That run checks out the repo and
  -- reads the database fresh — a good half-minute after it was dispatched — so
  -- a change made during the cooldown is almost always in the build that is
  -- already coming. `pending` records the small remainder, and the daily cron
  -- is the backstop that guarantees it eventually ships.
  update public.rebuild_state
  set pending = true, updated_at = now()
  where id;

  return false;
end;
$$;

commit;
