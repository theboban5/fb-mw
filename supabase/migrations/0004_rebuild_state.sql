-- 0004_rebuild_state.sql — coalescing state for the automatic rebuild.
--
-- Saving to Supabase does not update everyleague.co: the site is static HTML
-- built by GitHub Actions. So a published result has to ask for a rebuild.
-- That request goes through a Supabase Edge Function (which holds the GitHub
-- credential); this migration is the small piece of shared state that stops a
-- burst of reports from dispatching a burst of deploys.
--
-- The decision is made HERE, in one atomic UPDATE, rather than in the Edge
-- Function. Two functions invoked at the same instant would both read "no
-- recent dispatch" and both fire; a conditional UPDATE returning whether it
-- touched the row lets exactly one of them win, because the second blocks on
-- the row lock and then sees the first one's timestamp.

begin;

create table public.rebuild_state (
  -- Single-row table: the primary key can only ever be true.
  id                  boolean primary key default true check (id),
  last_dispatched_at  timestamptz,
  -- Set when a request arrived during the cooldown and was folded into the
  -- run that was already coming. Purely diagnostic — it answers "is there a
  -- change that no build has picked up yet?".
  pending             boolean not null default false,
  dispatch_count      bigint not null default 0,
  updated_at          timestamptz not null default now()
);

insert into public.rebuild_state (id) values (true);

comment on table public.rebuild_state is
  'One row. Debounce state for the GitHub Actions rebuild trigger.';

-- Operational state, and nobody outside the Edge Function has any business
-- reading or writing it. RLS on with no policy at all means only the service
-- role (which bypasses RLS) can touch it.
alter table public.rebuild_state enable row level security;
revoke all on public.rebuild_state from anon, authenticated;


-- ── claim_rebuild ────────────────────────────────────────────────────────────

create or replace function public.claim_rebuild(p_cooldown_seconds integer default 60)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_claimed boolean := false;
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
            > make_interval(secs => greatest(p_cooldown_seconds, 0)))
  returning true into v_claimed;

  if not v_claimed then
    -- Folded into the run already on its way. That run checks out the repo and
    -- reads the database fresh — a good half-minute after it was dispatched —
    -- so a change made during the cooldown is almost always in the build that
    -- is already coming. `pending` records the small remainder, and the daily
    -- cron is the backstop that guarantees it eventually ships.
    update public.rebuild_state
    set pending = true, updated_at = now()
    where id;
  end if;

  return coalesce(v_claimed, false);
end;
$$;

comment on function public.claim_rebuild(integer) is
  'Returns true at most once per cooldown window. The caller dispatches only '
  'when it returns true.';

-- Only the Edge Function calls this, with the service role key. A reporter
-- must not be able to dispatch deploys directly.
revoke execute on function public.claim_rebuild(integer)
  from public, anon, authenticated;

commit;
