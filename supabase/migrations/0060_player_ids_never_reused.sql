-- 0060_player_ids_never_reused.sql — a player id, once handed out, is never
-- handed out again.
--
-- WHAT WAS WRONG. next_player_id (0010) is "highest six-digit id plus one",
-- which is a sequence only for as long as nothing is deleted. merge_players
-- (0022) deletes the loser. On 24 Oct 2026 a stray "Kumwembe" was created as
-- CAF_MW_001235 and merged into the Kumwembe he was a few minutes later; the
-- next player created, Moses Shaba, was handed CAF_MW_001235. So:
--
--   * /players/CAF_MW_001235.html is a different person from one build to the
--     next, and a link anyone shared to the first now shows the second;
--   * the alias merge_players wrote, "merged from CAF_MW_001235", names an id
--     that is somebody else — the one fact recording the merge now points at
--     the wrong man;
--   * and it is what quietly unstuck the build that merge had stopped:
--     validate.py's drift check saw the deleted id "come back". The build now
--     recognises a merge outright (check_drift, _merged_players), so nothing
--     should depend on an id being recycled again.
--
-- Only the HIGHEST id can be reused this way — merging away an older
-- duplicate frees an id below the maximum, which max+1 never revisits — and
-- the newest player is exactly the one most likely to be a duplicate typed a
-- minute ago. So this was always going to happen on the first quick merge.
--
-- max+1 also raced: two reporters creating players in the same instant read
-- the same maximum and the second insert failed on the primary key.
--
-- WHAT THIS DOES. public.player_id_seq, started past every id that has ever
-- existed as far as the database can tell: the highest live id, and the
-- highest id an alias says was merged away. next_player_id takes nextval —
-- or max+1 if that is somehow higher (a row inserted by hand with an explicit
-- id), moving the sequence up to it — so it is never behind the table either.
-- Same signature, same format, same callers (create_player, 0020's
-- nt_players backfill); it is VOLATILE now, because nextval is.
--
-- THE TRADE. Ids get gaps: a merged duplicate's number is simply never used,
-- and a transaction that rolls back after calling it burns one too. Nothing
-- reads meaning into the number — DATA_MODEL.md's first rule — so a gap costs
-- nothing, and an id that names two people over its life costs a great deal.

begin;

create sequence if not exists public.player_id_seq as integer minvalue 1;

select setval('public.player_id_seq', greatest(
  coalesce((select max(substring(p.player_id from '^CAF_MW_([0-9]{6})$')::integer)
            from public.players p
            where p.player_id ~ '^CAF_MW_[0-9]{6}$'), 0),
  coalesce((select max(substring(a.context from '^merged from CAF_MW_([0-9]{6})$')::integer)
            from public.aliases a
            where a.entity_type = 'player'
              and a.context ~ '^merged from CAF_MW_[0-9]{6}$'), 0),
  1));

revoke all on sequence public.player_id_seq from public, anon, authenticated;

create or replace function public.next_player_id()
returns text
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_next integer;
  v_max  integer;
begin
  v_next := nextval('public.player_id_seq');
  select max(substring(p.player_id from '^CAF_MW_([0-9]{6})$')::integer)
    into v_max
  from public.players p
  where p.player_id ~ '^CAF_MW_[0-9]{6}$';
  if v_max is not null and v_max >= v_next then
    v_next := v_max + 1;
    perform setval('public.player_id_seq', v_next);
  end if;
  return 'CAF_MW_' || lpad(v_next::text, 6, '0');
end;
$$;

comment on function public.next_player_id() is
  'Next CAF_MW_dddddd player id. Never reuses one, including a merged-away id (0060).';

revoke execute on function public.next_player_id() from public, anon, authenticated;

commit;
