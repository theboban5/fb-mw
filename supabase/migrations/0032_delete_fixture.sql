-- 0032_delete_fixture.sql — an admin can remove a fixture nobody should have entered.
--
-- WHAT WAS WRONG. A double-tap on #/add left MW_NRFA with two identical,
-- dateless Chizumulu United vs Chibavi Real Stars fixtures (insert_fixture's
-- duplicate guard, 0014, only fires when a date is set — a dateless resubmit
-- sails past it). Getting rid of them meant someone with the secret key
-- running a DELETE by hand: `matches` has no RLS policy at all — writes go
-- through RPCs by design (0003) — and no RPC has ever deleted a row from it.
-- `cancelled` (DATA_MODEL.md) is the nearest built-in thing and does not
-- help: a cancelled match still renders, with a CANC badge, because that
-- status means "was going to happen and did not," not "should never have
-- existed."
--
-- WHAT THIS DOES. delete_fixture, admin-only, exactly as narrow as
-- delete_match_goal (0007) but with more to lose, so it checks more before it
-- acts:
--   * status must be 'scheduled' — a played, postponed, abandoned or awarded
--     match carries a real result and is never this function's business.
--   * no goals and no lineups may reference it. Either means somebody has
--     already reported something onto this fixture, and losing that silently
--     to an admin clearing out clutter would be worse than the clutter.
-- A missing match_id returns false rather than raising, the same as
-- delete_match_goal: "already gone" is not an error.

begin;

create or replace function public.delete_fixture(p_match_id text)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_match public.matches;
begin
  if not public.is_admin() then
    raise exception 'only an administrator can delete a fixture'
      using errcode = '42501';
  end if;

  select * into v_match from public.matches where match_id = p_match_id;
  if not found then
    return false;
  end if;

  if v_match.status <> 'scheduled' then
    raise exception 'only a scheduled fixture can be deleted — this one is %',
      v_match.status using errcode = '22023';
  end if;

  if exists (select 1 from public.goals where match_id = p_match_id) then
    raise exception 'this fixture has scorers recorded and cannot be deleted'
      using errcode = '22023';
  end if;

  if exists (select 1 from public.lineups where match_id = p_match_id) then
    raise exception 'this fixture has a team sheet and cannot be deleted'
      using errcode = '22023';
  end if;

  delete from public.matches where match_id = p_match_id;
  return true;
end;
$$;

comment on function public.delete_fixture(text) is
  'Admin-only. Removes a scheduled fixture with no goals or lineups attached — '
  'the fix for a duplicate or mistaken entry, not a way to unpublish a result.';

revoke execute on function public.delete_fixture(text) from public, anon;
grant execute on function public.delete_fixture(text) to authenticated;

commit;
