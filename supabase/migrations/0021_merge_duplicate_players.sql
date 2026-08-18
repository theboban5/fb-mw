-- 0021_merge_duplicate_players.sql — two people, four player rows, back to two.
--
-- WHAT HAPPENED. 0020 resolved every national-team name to a canonical players
-- row, reusing 0010's rule: match on the name, case-insensitively, and mint a
-- new id when nothing matches. That rule cannot see through a SPELLING. Two
-- people were spelled two ways across the nt_* tabs, so each was minted twice:
--
--   Vanessa Chikupira  in nt_goals,   Vanessa Chikupila  in nt_lineups/nt_squads
--   Babatunde Adepoju  in nt_goals,   Babatune Adepoju   in nt_squads
--
-- The inconsistency predates 0020 — it was in the tabs all along — but it cost
-- nothing while those ids pointed at no page. It costs something now: each
-- person's goals sit on one profile and their appearances on another, so
-- neither page is true and the site states two different careers for one
-- player.
--
-- WHICH SPELLING SURVIVES was a judgement about real people's names, not
-- something to infer from row counts, and it was made by the person who runs
-- this site: Chikupira and Babatunde. The loser's rows are repointed AND
-- renamed — the name is what renders on a national-team page, so leaving it
-- would fix the statistics and keep showing the wrong spelling.
--
-- WHY A MIGRATION for eight rows. Merging duplicates was documented in 0010 as
-- "a manual job: set the surviving player_id on the goals and delete the
-- loser". Doing it by hand leaves nothing behind that says a merge happened,
-- and the next person to notice two Adepojus in git history has no way to tell
-- a merge from a deletion. This is that record. It is also idempotent: the
-- loser is gone after the first run and every statement below then matches
-- nothing.

begin;

do $$
declare
  v_merge   record;
  v_moved   integer;
begin
  for v_merge in
    select * from (values
      -- (loser id, winner id, the spelling that survives)
      ('CAF_MW_000498', 'CAF_MW_000494', 'Vanessa Chikupira'),
      ('CAF_MW_000512', 'CAF_MW_000522', 'Babatunde Adepoju')
    ) as m(loser, winner, name)
  loop
    if not exists (select 1 from public.players p where p.player_id = v_merge.loser) then
      raise notice 'already merged: %', v_merge.name;
      continue;
    end if;
    if not exists (select 1 from public.players p where p.player_id = v_merge.winner) then
      raise exception 'survivor % does not exist', v_merge.winner;
    end if;

    -- The name moves with the id. nt_lineups keys on (match_id, player_name)
    -- and nt_squads on (squad_id, player_name), so a rename is a primary-key
    -- change: it would fail loudly if the winner already had a row in the same
    -- match or squad, which is exactly the collision worth failing on.
    update public.nt_lineups
      set player_id = v_merge.winner, player_name = v_merge.name
      where player_id = v_merge.loser;
    get diagnostics v_moved = row_count;
    raise notice '% : % nt_lineups rows', v_merge.name, v_moved;

    update public.nt_squads
      set player_id = v_merge.winner, player_name = v_merge.name
      where player_id = v_merge.loser;
    get diagnostics v_moved = row_count;
    raise notice '% : % nt_squads rows', v_merge.name, v_moved;

    update public.nt_goals
      set player_id = v_merge.winner, player_name = v_merge.name
      where player_id = v_merge.loser;

    -- Neither loser is referenced from the league side today — both were
    -- minted by 0020 minutes ago. Repointed anyway: a merge that leaves one
    -- reference behind is a foreign-key error at the delete below, and this
    -- migration should not depend on when it happens to be run.
    update public.goals set player_id = v_merge.winner
      where player_id = v_merge.loser;
    update public.goals set assist_player_id = v_merge.winner
      where assist_player_id = v_merge.loser;
    update public.lineups set player_id = v_merge.winner
      where player_id = v_merge.loser;
    update public.registrations set player_id = v_merge.winner
      where player_id = v_merge.loser;

    delete from public.players where player_id = v_merge.loser;
    raise notice 'merged % into %', v_merge.loser, v_merge.winner;
  end loop;
end;
$$;

commit;
