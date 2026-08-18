-- 0020_nt_players_canonical.sql — one player, one id, one page.
--
-- WHAT WAS WRONG. A national-team player_id was its own namespace — MW_W_014,
-- MW_INT_003 — absent from `players` and pointing at nothing. DATA_MODEL.md
-- said so plainly and treated it as a fact of life: "There are no links to
-- /players/ pages." That was tolerable while a team sheet was a list of names
-- on one page. It stops being tolerable the moment those names are clickable,
-- because the same person now has to be the same person in both halves of the
-- site: Tabitha Chawinga in a Scorchers XI and Tabitha Chawinga in a league
-- match were two unrelated strings, and no profile could show both.
--
-- WHAT THIS DOES. Every name on OUR OWN national-team rows is resolved to a
-- canonical CAF_MW_###### id — reusing the existing players row when the name
-- already matches one, minting a new id when it does not — and nt_squads,
-- nt_lineups and nt_goals are repointed at it.
--
-- WHAT IT DELIBERATELY LEAVES ALONE: the opponents. nt_goals holds both sides,
-- and an opponent's scorer carries an id of their own (INT_LIB_KOSIAH,
-- W_INT_NI_002). Those are not Malawian players. Minting CAF_MW ids for them
-- would file a Nigerian international in Malawi's player registry and give her
-- a page in a database that knows one match of her career. The test is
-- team_id: a row belongs to us when its team_id is an nt_teams.team_code, and
-- only those rows are touched. Everything else keeps the id it has and keeps
-- rendering as plain text, exactly as today.
--
-- THE MERGE RISK, STATED PLAINLY. Matching by name is what create_player has
-- done since 0010, and this reuses that rule so the two cannot disagree. It
-- also means a Malawi international who shares a name with a league player is
-- merged into that player. Six names do so at the time of writing. That is the
-- intended outcome in every case anyone has checked — Malawi's internationals
-- play in Malawi's leagues — but it is a judgement, not a certainty, so every
-- reuse is raised as a NOTICE. Read them; splitting one afterwards is a matter
-- of minting an id and repointing the rows, exactly as merging was.
--
-- RE-RUNNABLE. A row already carrying a CAF_MW id is left alone, so running
-- this twice changes nothing the second time.

begin;

do $$
declare
  v_name    text;
  v_player  public.players;
  v_new     integer := 0;
  v_reused  integer := 0;
begin
  for v_name in
    -- One pass over the three tabs, our sides only, normalized the way
    -- create_player normalizes: trimmed, with runs of whitespace collapsed,
    -- because "Tabitha  Chawinga" and "Tabitha Chawinga" are one person and
    -- the difference does not survive being rendered.
    select distinct btrim(regexp_replace(player_name, '\s+', ' ', 'g'))
    from (
      select team_id, player_name, player_id from public.nt_squads
      union all
      select team_id, player_name, player_id from public.nt_lineups
      union all
      select team_id, player_name, player_id from public.nt_goals
    ) r
    where r.team_id in (select team_code from public.nt_teams)
      and btrim(coalesce(r.player_name, '')) <> ''
      and coalesce(r.player_id, '') !~ '^CAF_MW_'
  loop
    -- 0010's rule verbatim: case-insensitive, matching known_as as well as
    -- full_name, lowest ord winning so the answer is stable when duplicates
    -- already exist.
    select * into v_player
    from public.players p
    where lower(p.full_name) = lower(v_name)
       or (p.known_as <> '' and lower(p.known_as) = lower(v_name))
    order by p.ord
    limit 1;

    if found then
      v_reused := v_reused + 1;
      raise notice 'REUSED % -> % (check this is the same person)',
        v_name, v_player.player_id;
    else
      insert into public.players (player_id, full_name, status)
      values (public.next_player_id(), v_name, 'active')
      returning * into v_player;
      v_new := v_new + 1;
    end if;

    update public.nt_squads s
      set player_id = v_player.player_id
      where s.team_id in (select team_code from public.nt_teams)
        and btrim(regexp_replace(s.player_name, '\s+', ' ', 'g')) = v_name;
    update public.nt_lineups l
      set player_id = v_player.player_id
      where l.team_id in (select team_code from public.nt_teams)
        and btrim(regexp_replace(l.player_name, '\s+', ' ', 'g')) = v_name;
    update public.nt_goals g
      set player_id = v_player.player_id
      where g.team_id in (select team_code from public.nt_teams)
        and btrim(regexp_replace(g.player_name, '\s+', ' ', 'g')) = v_name;
  end loop;

  raise notice 'national-team players: % minted, % reused', v_new, v_reused;
end;
$$;

comment on column public.nt_lineups.player_id is
  'A canonical players.player_id for OUR sides (0020). An opponent''s row keeps '
  'its own INT_* id, which resolves to nothing and renders as plain text.';
comment on column public.nt_goals.player_id is
  'A canonical players.player_id for OUR sides (0020). An opponent''s row keeps '
  'its own INT_* id, which resolves to nothing and renders as plain text.';

commit;
