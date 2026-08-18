-- 0022_player_identity.sql — a name you can change your mind about.
--
-- WHAT WAS WRONG. A team sheet arrives as a photograph of a graphic, and the
-- graphic says "4. A. Josephy". That is all anyone has on the night: an
-- initial and a surname. 0010 already decided the right thing to do with a
-- name nobody can resolve — mint a players row and carry on, because a scorer
-- who never reaches the scorer table is indistinguishable from the feature not
-- working. So "A. Josephy" becomes a real player with a real id, a profile and
-- a link from every sheet they appear on.
--
-- Then the first name turns up. And there was no way to say so. Merging two
-- spellings was documented in 0010 as "a manual job: set the surviving
-- player_id on the goals and delete the loser", and 0021 is what that looks
-- like when it actually happens: a migration written by hand for eight rows,
-- for two people, because there was nothing else to write. Correcting a name
-- had no story at all — an UPDATE against players in the SQL editor, with
-- nothing to say a correction had been made and no defence against the
-- collision that turns one person into two.
--
-- WHAT THIS DOES. Two functions, and one rule shared between them: the id is
-- the person, the name is a label on the id, and a label can be repainted.
--
--   * rename_player  — the label changes. The previous spelling is kept in
--     aliases, which is what that table is for ("alternate spellings, for
--     matching incoming text to an entity") and is also, now, the record that
--     a correction happened at all.
--   * merge_players  — two ids turn out to be one person. Everything the loser
--     is named on is repointed at the winner, the loser's spellings become
--     aliases of the winner, and the loser row goes.
--
-- THE PERMISSION SPLIT, AND WHY. Renaming is open to any active reporter: it
-- is the other half of create_player, which they already have, and it is
-- reversible — rename it back and the aliases pile up harmlessly. Merging
-- DELETES a row and repoints history across seven tables; it cannot be undone
-- from the portal, so it is admin-only. That asymmetry is deliberate and is
-- the only place these two functions differ on authorization.
--
-- WHAT THIS DOES NOT DO. It does not rewrite lineups.player_name or
-- goals.reported_player_name. Those columns hold the name AS REPORTED and
-- that is their whole job — "A. Josephy" is a true statement about what the
-- graphic said. The site stopped rendering them for identified rows in the
-- same change: an identified team-sheet row resolves its name through players,
-- exactly as a goal has since the schema was written. So one rename here moves
-- every team sheet, every scorer line, every profile and the search index at
-- the next build, and the archive of what was actually typed survives
-- underneath it.

begin;

-- ── alias_player ─────────────────────────────────────────────────────────────
-- Every spelling a player has ever been filed under, kept against the id that
-- outlived it. Private to this file: both functions below are the only callers
-- and neither wants the caller choosing entity_type.
--
-- aliases has no natural key by design (0001: "a duplicate alias_text is legal
-- (two clubs can share a nickname)"), so a repeat rename would otherwise pile
-- up identical rows. The existence check below is not a uniqueness guarantee —
-- it is what keeps renaming back and forth from growing the tab forever.

create or replace function public.alias_player(
  p_player_id  text,
  p_alias_text text,
  p_context    text
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  p_alias_text := btrim(coalesce(p_alias_text, ''));
  if p_alias_text = '' then
    return;
  end if;
  if exists (
    select 1 from public.aliases a
    where a.entity_type = 'player'
      and a.entity_id = p_player_id
      and lower(a.alias_text) = lower(p_alias_text)
  ) then
    return;
  end if;
  -- ord is `not null` with no default on this table (0001), so it is computed
  -- rather than left to the identity column every other tab has.
  insert into public.aliases (alias_text, entity_type, entity_id, context, ord)
  values (p_alias_text, 'player', p_player_id, p_context,
          coalesce((select max(a.ord) from public.aliases a), 0) + 1);
end;
$$;

revoke execute on function public.alias_player(text, text, text)
  from public, anon, authenticated;


-- ── rename_player ────────────────────────────────────────────────────────────
-- p_known_as is three-valued on purpose. NULL leaves it alone, which is what a
-- caller who only wants to fix the full name should send; '' clears it, which
-- is the only way to undo a known_as once set. Defaulting it to NULL means the
-- one-argument call a reporter makes cannot silently wipe a nickname.
--
-- The collision check is the important part. create_player is idempotent on
-- the name, so once two players exist under one spelling the search shows one
-- and hides the other forever — the exact failure 0021 was written to repair.
-- Renaming INTO an existing name is therefore refused, and the message names
-- the id to merge with, because that is the next thing the caller has to do.

create or replace function public.rename_player(
  p_player_id text,
  p_full_name text,
  p_known_as  text default null
)
returns setof public.players
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_name     text;
  v_known    text;
  v_old      public.players;
  v_clash    text;
  v_player   public.players;
begin
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  select * into v_old from public.players p
  where p.player_id = p_player_id for update;
  if not found then
    raise exception 'player not found' using errcode = 'P0002';
  end if;

  -- The reserved row for "scorer not identified yet". It is referenced by
  -- goals throughout the dataset and src/adapt.py keys off the id itself, so
  -- there is nothing a name could usefully say about it.
  if p_player_id = 'CAF_MW_UNKNOWN' then
    raise exception 'CAF_MW_UNKNOWN is the reserved unidentified-player row'
      using errcode = '22023';
  end if;

  -- Same normalization as create_player: collapse runs of whitespace as well
  -- as trimming, because "Andrew  Josephy" and "Andrew Josephy" are one person
  -- and the difference is invisible on screen.
  v_name := btrim(regexp_replace(coalesce(p_full_name, ''), '\s+', ' ', 'g'));
  if length(v_name) < 2 then
    raise exception 'a player needs a name' using errcode = '22023';
  end if;
  if length(v_name) > 80 then
    raise exception 'that name is too long' using errcode = '22023';
  end if;

  v_known := case when p_known_as is null then v_old.known_as
             else btrim(regexp_replace(p_known_as, '\s+', ' ', 'g')) end;
  if length(v_known) > 80 then
    raise exception 'that name is too long' using errcode = '22023';
  end if;

  select p.player_id into v_clash
  from public.players p
  where p.player_id <> p_player_id
    and (lower(p.full_name) = lower(v_name)
         or (p.known_as <> '' and lower(p.known_as) = lower(v_name)))
  order by p.ord
  limit 1;
  if v_clash is not null then
    raise exception
      'there is already a player called % (%) — merge them instead',
      v_name, v_clash using errcode = '23505';
  end if;

  if v_old.full_name = v_name and v_old.known_as = v_known then
    -- Asked for a state that is already true. Returning the row rather than
    -- raising matches set_match_venue and reschedule_match.
    return next v_old;
    return;
  end if;

  -- Both old spellings are kept, not just the one that changed: a player who
  -- went by a nickname is findable under it long after it stops being the
  -- name on the page.
  perform public.alias_player(
    p_player_id, v_old.full_name,
    'former spelling, renamed by ' || v_reporter || ' on ' || current_date);
  if v_old.known_as <> '' and v_old.known_as <> v_known then
    perform public.alias_player(
      p_player_id, v_old.known_as,
      'former known_as, renamed by ' || v_reporter || ' on ' || current_date);
  end if;

  update public.players p
  set full_name = v_name, known_as = v_known, updated_at = now()
  where p.player_id = p_player_id
  returning * into v_player;

  return next v_player;
end;
$$;

comment on function public.rename_player(text, text, text) is
  'Correct a player''s name. The previous spelling is kept in aliases. Refuses '
  'a name another player already answers to — merge_players is for that.';

revoke execute on function public.rename_player(text, text, text)
  from public, anon;
grant execute on function public.rename_player(text, text, text) to authenticated;


-- ── merge_players ────────────────────────────────────────────────────────────
-- 0021, generalized: what that migration did by hand for two people, for any
-- two ids, with the checks it did not need because a human had already looked.
--
-- SEVEN TABLES hold a player_id, and two of them hold two (a scorer and an
-- assister). Missing one would leave half a career on a deleted id and fail
-- the FK, so they are listed exhaustively here rather than discovered:
--
--   goals.player_id, goals.assist_player_id, lineups.player_id,
--   registrations.player_id, nt_goals.player_id, nt_goals.assist_player_id,
--   nt_lineups.player_id, nt_squads.player_id
--
-- THE COLLISION THAT MATTERS is two ids on the same team sheet. lineups keys
-- on (match_id, team_id, player_name) and nt_lineups on (match_id,
-- player_name), so repointing does not violate anything — but it would leave
-- one person listed twice in one match, which renders as two players and
-- silently doubles their appearance count. That is checked first and refused,
-- because the fix is a judgement (which row is the real one) that this
-- function has no way to make.

create or replace function public.merge_players(
  p_loser  text,
  p_winner text
)
returns table (
  player_id text,
  full_name text,
  goals_moved     integer,
  assists_moved   integer,
  lineups_moved   integer,
  nt_rows_moved   integer
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_loser   public.players;
  v_winner  public.players;
  v_clash   text;
  v_n       integer;
begin
  if not public.is_admin() then
    raise exception 'merging players is an admin action' using errcode = '42501';
  end if;

  if p_loser = p_winner then
    raise exception 'those are the same player' using errcode = '22023';
  end if;
  if 'CAF_MW_UNKNOWN' in (p_loser, p_winner) then
    raise exception 'CAF_MW_UNKNOWN is the reserved unidentified-player row'
      using errcode = '22023';
  end if;

  -- Locked by id order, not by role: locking the loser then the winner would
  -- deadlock against an admin running the mirror-image merge at the same
  -- moment. Reading the rows afterwards is a second, unlocked statement
  -- because the lock is already held by then.
  perform 1 from public.players p
  where p.player_id in (p_loser, p_winner)
  order by p.player_id for update;

  select * into v_loser  from public.players p where p.player_id = p_loser;
  select * into v_winner from public.players p where p.player_id = p_winner;
  if v_loser.player_id is null then
    raise exception 'player % not found', p_loser using errcode = 'P0002';
  end if;
  if v_winner.player_id is null then
    raise exception 'player % not found', p_winner using errcode = 'P0002';
  end if;

  select l.match_id into v_clash
  from public.lineups l
  join public.lineups w
    on w.match_id = l.match_id and w.team_id = l.team_id
   and w.player_id = p_winner
  where l.player_id = p_loser
  limit 1;
  if v_clash is not null then
    raise exception
      'both ids are on the same team sheet in match % — remove one row first',
      v_clash using errcode = '23505';
  end if;

  select l.match_id into v_clash
  from public.nt_lineups l
  join public.nt_lineups w
    on w.match_id = l.match_id and w.player_id = p_winner
  where l.player_id = p_loser
  limit 1;
  if v_clash is not null then
    raise exception
      'both ids are on the same national-team sheet in match % — remove one '
      'row first', v_clash using errcode = '23505';
  end if;

  update public.goals g set player_id = p_winner where g.player_id = p_loser;
  get diagnostics v_n = row_count;  goals_moved := v_n;

  update public.goals g set assist_player_id = p_winner
  where g.assist_player_id = p_loser;
  get diagnostics v_n = row_count;  assists_moved := v_n;

  update public.lineups l set player_id = p_winner where l.player_id = p_loser;
  get diagnostics v_n = row_count;  lineups_moved := v_n;

  -- registrations keys on (player_id, team_id, season_id), so a repoint CAN
  -- collide here — both ids registered to the same team in the same season is
  -- a duplicate, not a conflict, and the loser's row is simply dropped.
  delete from public.registrations r
  where r.player_id = p_loser
    and exists (select 1 from public.registrations w
                where w.player_id = p_winner and w.team_id = r.team_id
                  and w.season_id = r.season_id);
  update public.registrations r set player_id = p_winner
  where r.player_id = p_loser;

  nt_rows_moved := 0;
  update public.nt_goals g set player_id = p_winner where g.player_id = p_loser;
  get diagnostics v_n = row_count;  nt_rows_moved := nt_rows_moved + v_n;
  update public.nt_goals g set assist_player_id = p_winner
  where g.assist_player_id = p_loser;
  get diagnostics v_n = row_count;  nt_rows_moved := nt_rows_moved + v_n;
  update public.nt_lineups l set player_id = p_winner where l.player_id = p_loser;
  get diagnostics v_n = row_count;  nt_rows_moved := nt_rows_moved + v_n;
  update public.nt_squads s set player_id = p_winner where s.player_id = p_loser;
  get diagnostics v_n = row_count;  nt_rows_moved := nt_rows_moved + v_n;

  -- The loser's spellings survive as aliases of the winner. This is the only
  -- record that the id ever existed, and it is what stops the next import
  -- minting it again from the same source text.
  perform public.alias_player(
    p_winner, v_loser.full_name, 'merged from ' || p_loser);
  if v_loser.known_as <> '' then
    perform public.alias_player(
      p_winner, v_loser.known_as, 'merged from ' || p_loser);
  end if;
  -- Aliases already pointing at the loser move too, rather than being orphaned
  -- against an id that is about to stop existing.
  update public.aliases a
  set entity_id = p_winner
  where a.entity_type = 'player' and a.entity_id = p_loser;

  delete from public.players p where p.player_id = p_loser;

  player_id := p_winner;
  full_name := v_winner.full_name;
  return next;
end;
$$;

comment on function public.merge_players(text, text) is
  'Two ids turn out to be one person: repoint every row at the winner, keep '
  'the loser''s spellings as aliases, delete the loser. Admin only — it '
  'deletes a row and cannot be undone from the portal.';

revoke execute on function public.merge_players(text, text) from public, anon;
grant execute on function public.merge_players(text, text) to authenticated;


-- ── search_players ───────────────────────────────────────────────────────────
-- WHY THIS EXISTS. The portal searched players with a PostgREST ilike on
-- full_name and known_as. That finds a substring and nothing else, which is
-- precisely the wrong shape for the problem this migration is about: a sheet
-- entered as "A. Josephy" is invisible to someone typing "Andrew Josephy",
-- and the picker's answer to invisible is "＋ Add as a new player". The tool
-- for fixing duplicates should not itself be a machine for making them.
--
-- So the search matches, in this order of confidence:
--
--   1. the whole term, as a substring of either name (what it always did);
--   2. an ALIAS — every spelling this player has ever been filed under,
--      including the ones rename_player and merge_players put there;
--   3. the SURNAME. The last word of the typed name against the last word of
--      theirs, when the initials do not contradict each other. "A. Josephy"
--      and "Andrew Josephy" share a surname and agree on A; "A. Josephy" and
--      "Beatrice Josephy" share a surname and disagree, so they do not match.
--
-- Rank is the point as much as recall: an exact hit must stay at the top, or
-- a reporter picking the first row gets a surname-mate of the player they
-- meant. Hence a numeric score rather than a bare union.

create or replace function public.search_players(p_term text)
returns table (
  player_id text,
  full_name text,
  known_as  text,
  matched   text,     -- the spelling that matched, when it was an alias
  score     integer
)
language sql
stable
security definer
set search_path = ''
as $$
  with term as (
    select btrim(regexp_replace(coalesce(p_term, ''), '\s+', ' ', 'g')) as t
  ),
  parts as (
    select t,
           lower(t) as lt,
           lower(split_part(t, ' ', greatest(1, array_length(string_to_array(t, ' '), 1))))
             as surname,
           lower(left(t, 1)) as initial
    from term
  ),
  candidates as (
    select p.player_id, p.full_name, p.known_as,
           lower(coalesce(nullif(p.known_as, ''), p.full_name)) as label,
           lower(split_part(
             coalesce(nullif(p.known_as, ''), p.full_name), ' ',
             greatest(1, array_length(string_to_array(
               coalesce(nullif(p.known_as, ''), p.full_name), ' '), 1)))) as surname,
           lower(left(coalesce(nullif(p.known_as, ''), p.full_name), 1)) as initial
    from public.players p
    where p.player_id <> 'CAF_MW_UNKNOWN'
  ),
  hits as (
    select c.player_id, c.full_name, c.known_as, '' as matched,
           case when c.label = x.lt then 100
                when c.label like x.lt || '%' then 80
                else 60 end as score
    from candidates c, parts x
    where x.lt <> '' and (lower(c.full_name) like '%' || x.lt || '%'
                          or lower(c.known_as) like '%' || x.lt || '%')
    union all
    select c.player_id, c.full_name, c.known_as, a.alias_text as matched, 50
    from candidates c
    join public.aliases a
      on a.entity_type = 'player' and a.entity_id = c.player_id
    cross join parts x
    where x.lt <> '' and lower(a.alias_text) like '%' || x.lt || '%'
    union all
    -- The surname branch. Guarded on a surname of three characters or more:
    -- below that it is an initial or a particle and matches half the country.
    select c.player_id, c.full_name, c.known_as, '' as matched, 30
    from candidates c, parts x
    where length(x.surname) >= 3
      and c.surname = x.surname
      and (x.initial = c.initial
           or x.surname = x.lt          -- they typed a surname alone
           or c.surname = c.label)      -- we hold a surname alone
  )
  select h.player_id, h.full_name, h.known_as,
         (array_agg(h.matched order by h.score desc))[1] as matched,
         max(h.score)::integer as score
  from hits h
  group by h.player_id, h.full_name, h.known_as
  order by max(h.score) desc, h.full_name
  limit 12;
$$;

comment on function public.search_players(text) is
  'Player lookup for the reporter portal: substring, alias, then surname with '
  'agreeing initials, ranked. Finds "Andrew Josephy" when the sheet says '
  '"A. Josephy", which is the whole point.';

revoke execute on function public.search_players(text) from public;
grant execute on function public.search_players(text) to anon, authenticated;

commit;
