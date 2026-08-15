-- 0005_claim_rebuild_grant.sql — say out loud who may claim a rebuild.
--
-- 0004 revoked EXECUTE on claim_rebuild from public, anon and authenticated,
-- and then relied on service_role still being able to call it. It can, but
-- only because of a platform default rather than anything this schema says —
-- which is a poor thing for a security-relevant grant to rest on. State it.

begin;

grant execute on function public.claim_rebuild(integer) to service_role;

comment on function public.claim_rebuild(integer) is
  'Returns true at most once per cooldown window. The caller dispatches only '
  'when it returns true. EXECUTE is service_role only: the Edge Function is '
  'the sole caller, because dispatching a deploy costs build minutes and a '
  'reporter must not be able to do it directly.';

commit;
