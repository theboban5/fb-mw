-- 0065_fam_registration_numbers.sql — a FAM registration number is who a
-- player IS, so a pre-match report can name everyone on it exactly.
--
-- WHAT WAS WRONG. 0064 let a pre-match report in as a PDF, and the first one
-- saved (Kamuzu Barracks v Chitipa United, 1 Nov 2026) put 37 names on the
-- match and linked 11 of them. The other 26 went on as plain text: no
-- profile, no link to their other matches, not on the club's squad. For 18
-- of them the site had nobody with that surname at all — they were simply
-- new — and the reader links only by name, unique-or-nothing, and never
-- invents a player. That rule is right for a WhatsApp graphic that says
-- "MWALE". It is wrong for a document that prints, beside every name, the
-- number the FA itself uses to tell two people apart:
--
--     Micium Mhone (000584M95)
--
-- The number was read and kept on report_imports.extracted, and nothing
-- could do anything with it.
--
-- WHAT THIS DOES.
--
--   * player_fam_regs (fam_reg_no → player_id). A number names one player;
--     a player may hold more than one, because the person is the fact and a
--     re-registration is a new number, not a new person. A table of its own
--     rather than a column on players because players is the Dataset's and
--     data/canonical/ is public: the number carries a sex and a birth year
--     (six digits, M/F, two digits), and a list of every footballer's would
--     be published by the next build. Here it is readable only through the
--     two functions below, by a signed-in reporter. Nothing renders it.
--
--   * fam_players(numbers) — who holds each of these, for the match screen
--     to link a staged sheet BEFORE it tries names. A number beats a name:
--     "J. Issac Simfukwe" and "J. Isaac Simfukwe" are one man if the FA says
--     so, and two Gift Phiris are two if it says that.
--
--   * register_fam_players(rows) — called on save, one row per sheet line
--     that carries a number, each {reg_no, player_id, name}:
--       - a number someone already holds is that person ('known'). If the
--         row was linked to somebody else, that is a 'conflict', named, and
--         the reporter's link stands — the screen says so instead of quietly
--         re-pointing a line someone chose.
--       - a new number on a linked row is attached to that player
--         ('attached'), unless they already hold a different one: a
--         name-match is evidence, not proof, and one wrong attachment would
--         link that number to the wrong person on every sheet after. That is
--         0046's collision rule, for people.
--       - a new number on an unlinked row creates the player ('created'),
--         under the name as printed, ALWAYS as a new id. create_player's
--         idempotence on the name is exactly wrong here: the number is the
--         evidence, and the name is just a label on it (DATA_MODEL.md). A
--         duplicate of someone the site knew under another club is one merge
--         from repaired, and merge_players now carries the numbers; linking
--         to the wrong Gift Phiri is a career filed under someone else.
--     The reply says, per row, what happened, plus how many existing players
--     share each created name exactly, so the screen can say "check these".
--
--   * merge_players repoints the numbers too. It lists every table holding a
--     player_id exhaustively (0022) and this is a ninth; without it a merge
--     would cascade the loser's number away and the next sheet would mint
--     the duplicate again.
--
-- The number is checked here (^\d{6}[MF]\d{2}$) as well as in the client,
-- because it is read off a document by a model: evidence, never trusted. No
-- birth year is read out of it, here or anywhere.
--
-- THE TRADE. A sheet saved from a pre-match report now CREATES players, a
-- dozen at a time, where before a reporter created each one with a tap. Most
-- of those are people the site has never seen and should have had pages for.
-- Some will be someone it already knew, spelled another way, at another club
-- — those are duplicates, the screen names them, and #/players merges them.
-- The screen also lets the reporter save the sheet as names only instead.

begin;

create table public.player_fam_regs (
  fam_reg_no  text primary key
              check (fam_reg_no ~ '^[0-9]{6}[MF][0-9]{2}$'),
  player_id   text not null references public.players (player_id)
              on delete cascade,
  created_by  text,
  created_at  timestamptz not null default now()
);

create index player_fam_regs_player on public.player_fam_regs (player_id);

alter table public.player_fam_regs enable row level security;
-- No policies: every read and write is one of the functions below. This
-- project's default privileges grant new objects to anon (0048), so the
-- revoke is not decoration.
revoke all on public.player_fam_regs from public, anon, authenticated;

comment on table public.player_fam_regs is
  'FAM registration numbers, read off pre-match reports (0064/0065). A number '
  'names one player; never in the Dataset, never rendered.';

-- ── fam_players ──────────────────────────────────────────────────────────────

create or replace function public.fam_players(p_reg_nos text[])
returns table (fam_reg_no text, player_id text, full_name text)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if public.current_reporter_id() is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;
  return query
    select r.fam_reg_no, p.player_id, p.full_name
    from public.player_fam_regs r
    join public.players p on p.player_id = r.player_id
    where r.fam_reg_no = any (
      select upper(btrim(x)) from unnest(coalesce(p_reg_nos, '{}')) x
      limit 100);
end;
$$;

comment on function public.fam_players(text[]) is
  'Who holds each of these FAM registration numbers (0065).';

revoke execute on function public.fam_players(text[]) from public, anon;
grant execute on function public.fam_players(text[]) to authenticated;

-- ── register_fam_players ─────────────────────────────────────────────────────

create or replace function public.register_fam_players(p_rows jsonb)
returns table (
  reg_no     text,
  player_id  text,
  full_name  text,
  outcome    text,      -- known | attached | created | conflict | invalid
  namesakes  integer,   -- existing players with exactly this name (created)
  message    text
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_row      jsonb;
  v_reg      text;
  v_linked   text;
  v_name     text;
  v_holder   public.players;
  v_player   public.players;
  v_other    text;
begin
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;
  if jsonb_typeof(p_rows) is distinct from 'array' then
    raise exception 'expected a list of rows' using errcode = '22023';
  end if;
  -- Two benches and two XIs are under fifty. Anything far past that is not
  -- a team sheet.
  if jsonb_array_length(p_rows) > 60 then
    raise exception 'too many rows at once (at most 60)' using errcode = '22023';
  end if;

  for v_row in select * from jsonb_array_elements(p_rows) loop
    v_reg    := upper(btrim(coalesce(v_row ->> 'reg_no', '')));
    v_linked := btrim(coalesce(v_row ->> 'player_id', ''));
    v_name   := btrim(regexp_replace(coalesce(v_row ->> 'name', ''), '\s+', ' ', 'g'));
    reg_no := v_reg;  player_id := '';  full_name := '';
    namesakes := 0;   message := '';

    if v_reg !~ '^[0-9]{6}[MF][0-9]{2}$' then
      outcome := 'invalid';
      message := format('"%s" is not a FAM registration number.', v_reg);
      return next;  continue;
    end if;

    -- Serialises two reporters saving the same sheet at once: the second
    -- finds the first's row instead of failing on the primary key.
    perform pg_advisory_xact_lock(hashtext('fam_reg:' || v_reg));

    select p.* into v_holder
    from public.player_fam_regs r
    join public.players p on p.player_id = r.player_id
    where r.fam_reg_no = v_reg;

    if v_holder.player_id is not null then
      player_id := v_holder.player_id;
      full_name := v_holder.full_name;
      if v_linked <> '' and v_linked <> v_holder.player_id then
        outcome := 'conflict';
        message := format('%s is %s''s FAM number, not the player that line '
          'is linked to — the link was left as it is.', v_reg, v_holder.full_name);
      else
        outcome := 'known';
      end if;
      return next;  continue;
    end if;

    if v_linked <> '' then
      select * into v_player from public.players p where p.player_id = v_linked;
      if v_player.player_id is null then
        -- Merged away while the sheet was open. One line, not the batch.
        outcome := 'invalid';
        message := format('The player that line was linked to (%s) no longer '
          'exists, so %s was not added.', v_linked, v_reg);
        return next;  continue;
      end if;
      select r.fam_reg_no into v_other
      from public.player_fam_regs r where r.player_id = v_linked limit 1;
      player_id := v_player.player_id;
      full_name := v_player.full_name;
      if v_other is not null then
        outcome := 'conflict';
        message := format('%s already has FAM number %s, so %s was not added '
          'to them.', v_player.full_name, v_other, v_reg);
      else
        insert into public.player_fam_regs (fam_reg_no, player_id, created_by)
        values (v_reg, v_linked, v_reporter);
        outcome := 'attached';
      end if;
      return next;  continue;
    end if;

    if length(v_name) < 2 or length(v_name) > 80 then
      outcome := 'invalid';
      message := format('%s has no usable name to add them under.', v_reg);
      return next;  continue;
    end if;

    select count(*)::integer into namesakes
    from public.players p
    where lower(p.full_name) = lower(v_name)
       or (p.known_as <> '' and lower(p.known_as) = lower(v_name));

    insert into public.players (player_id, full_name, status)
    values (public.next_player_id(), v_name, 'active')
    returning * into v_player;
    insert into public.player_fam_regs (fam_reg_no, player_id, created_by)
    values (v_reg, v_player.player_id, v_reporter);

    player_id := v_player.player_id;
    full_name := v_player.full_name;
    outcome := 'created';
    return next;
  end loop;
end;
$$;

comment on function public.register_fam_players(jsonb) is
  'A saved sheet''s FAM numbers: link who holds one, attach a new one to a '
  'linked player who has none, create the player for an unlinked one (0065).';

revoke execute on function public.register_fam_players(jsonb) from public, anon;
grant execute on function public.register_fam_players(jsonb) to authenticated;

-- ── merge_players, carrying the numbers ──────────────────────────────────────
-- 0022's function exactly, plus player_fam_regs among the repointed tables.
-- Its primary key is the number, so a repoint cannot collide: the winner
-- simply ends up holding both, which is what a merge says — one person.

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

  delete from public.registrations r
  where r.player_id = p_loser
    and exists (select 1 from public.registrations w
                where w.player_id = p_winner and w.team_id = r.team_id
                  and w.season_id = r.season_id);
  update public.registrations r set player_id = p_winner
  where r.player_id = p_loser;

  -- 0065. Before the delete below, which would otherwise cascade them away.
  update public.player_fam_regs f set player_id = p_winner
  where f.player_id = p_loser;

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

  perform public.alias_player(
    p_winner, v_loser.full_name, 'merged from ' || p_loser);
  if v_loser.known_as <> '' then
    perform public.alias_player(
      p_winner, v_loser.known_as, 'merged from ' || p_loser);
  end if;
  update public.aliases a
  set entity_id = p_winner
  where a.entity_type = 'player' and a.entity_id = p_loser;

  delete from public.players p where p.player_id = p_loser;

  player_id := p_winner;
  full_name := v_winner.full_name;
  return next;
end;
$$;

revoke execute on function public.merge_players(text, text) from public, anon;
grant execute on function public.merge_players(text, text) to authenticated;

commit;
