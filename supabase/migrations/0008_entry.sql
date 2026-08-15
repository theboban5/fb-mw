-- 0008_entry.sql — creating fixtures and leagues from /report, and recording
-- where a result came from.
--
-- Until now a reporter could do exactly one thing: put a score on a fixture
-- that already existed. Everything upstream of that — the competition, its
-- teams, their entries, the fixture list itself — was CLI work against the
-- spreadsheet. This migration moves that upstream half into the portal, at
-- three levels of privilege:
--
--   submit_match_report  reporter, assigned competition   (existing, extended)
--   create_fixture       reporter, assigned competition   (new)
--   create_league        admin only                       (new)
--
-- THE CONSTRAINT THAT SHAPES ALL OF THIS: validate.py runs as the first step
-- of every build and a single bad row fails the job, which means no deploy at
-- all — a typo entered on a phone must not be able to take the site down. So
-- every rule the validator enforces is enforced HERE too, at insert time,
-- where it can still be shown to the person who caused it:
--
--   check 2 (foreign keys)  → every reference resolved before insert
--   check 3 (match entries) → both teams must hold an entries row for the
--                             competition+season (create_fixture, step 6)
--   check 4 (consistency)   → no self-play; a scheduled match carries no score
--   check 7 (cup rules)     → stage vocabulary depends on competitions.type
--
-- IDs follow DATA_MODEL.md "ID conventions (as built)" and are minted only
-- here. They are never parsed to recover meaning — minting is the one moment
-- an ID's shape is decided, and after that it is opaque forever.

begin;

-- ── Authorization at competition level ───────────────────────────────────────
-- can_report_match() answers "may I touch THIS match?", which cannot be asked
-- about a match that does not exist yet. This is the same question one level
-- up, and it is deliberately a separate function rather than a nullable
-- argument on the existing one: that function is documented as the single
-- place match-level assignment will be added, and widening its signature now
-- would put two unrelated concerns in one body.

create or replace function public.can_report_competition(
  p_competition_id text,
  p_season_id      text default null
)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select public.is_admin() or exists (
    select 1
    from public.reporter_assignments a
    join public.reporters r
      on r.reporter_id = a.reporter_id
    where a.competition_id = p_competition_id
      -- NULL on the assignment = every season. NULL argument = "any season of
      -- this competition", which is what the fixture form asks before a season
      -- has been chosen.
      and (a.season_id is null or p_season_id is null
           or a.season_id = p_season_id)
      and r.auth_user_id = (select auth.uid())
      and r.active
  )
$$;

comment on function public.can_report_competition(text, text) is
  'May the caller enter data for this competition? Competition-level twin of '
  'can_report_match, for rows that do not exist yet.';

revoke execute on function public.can_report_competition(text, text)
  from public, anon;
grant execute on function public.can_report_competition(text, text)
  to authenticated;


-- ── ID minting ───────────────────────────────────────────────────────────────

-- unaccent is an extension and may not be installed; this keeps ID minting
-- from depending on that. Only the handful of accented characters that
-- actually occur in Malawian club names are mapped.
create or replace function public.unaccent_fallback(p_text text)
returns text
language sql
immutable
set search_path = ''
as $$
  select translate(p_text,
                   'áàâäãéèêëíìîïóòôöõúùûüñçÁÀÂÄÃÉÈÊËÍÌÎÏÓÒÔÖÕÚÙÛÜÑÇ',
                   'aaaaaeeeeiiiiooooouuuuncAAAAAEEEEIIIIOOOOOUUUUNC')
$$;


-- A short alphabetic code from a club name, used to build club_id. Multi-word
-- names give their initials (Blue Eagles → BE), single words give their first
-- four letters. This only ever proposes; uniqueness is settled by the caller,
-- which is the only place that can see what is already taken.
create or replace function public.club_code_from_name(p_name text)
returns text
language plpgsql
immutable
set search_path = ''
as $$
declare
  v_clean text;
  v_words text[];
  v_code  text;
begin
  -- Strip everything that is not a letter, a digit or a space. Accents and
  -- punctuation must not reach an ID: DATA_MODEL.md pins the separator as
  -- underscore and the alphabet as ASCII.
  v_clean := regexp_replace(upper(public.unaccent_fallback(p_name)),
                            '[^A-Z0-9 ]', '', 'g');
  v_clean := trim(regexp_replace(v_clean, ' +', ' ', 'g'));

  -- "FC" and "United" carry no distinguishing information and appear in half
  -- the names in the country; dropping them makes the initials mean something.
  v_words := array_remove(
    array_remove(
      array_remove(string_to_array(v_clean, ' '), 'FC'), 'AFC'), '');

  if array_length(v_words, 1) is null then
    return 'X';
  elsif array_length(v_words, 1) = 1 then
    v_code := left(v_words[1], 4);
  else
    -- Initials, capped at 4 so MW_<code> stays short enough to read.
    v_code := '';
    for i in 1 .. least(array_length(v_words, 1), 4) loop
      v_code := v_code || left(v_words[i], 1);
    end loop;
  end if;

  return coalesce(nullif(v_code, ''), 'X');
end;
$$;


-- The next free match_id for a competition+season, in the established
-- MW_SL_2627_001 shape: competition, the season's two years, a zero-padded
-- sequence. The sequence is derived by counting, not by a counter table, so it
-- cannot drift out of step with the rows it names.
create or replace function public.next_match_id(
  p_competition_id text,
  p_season_id      text
)
returns text
language plpgsql
stable
set search_path = ''
as $$
declare
  v_digits text;
  v_label  text;
  v_prefix text;
  v_next   integer;
begin
  -- "2026/27" → 2627, which is the shape every existing match_id already uses
  -- (MW_SL_2627_001). Written as explicit cases rather than a clever
  -- expression because this string is baked into permanent IDs: it must be
  -- obvious, on reading, exactly what each season label produces.
  select regexp_replace(coalesce(s.label, ''), '[^0-9]', '', 'g')
  into v_digits
  from public.seasons s where s.season_id = p_season_id;

  if v_digits ~ '^[0-9]{6}$' then          -- "2026/27"   → 202627
    v_label := right(v_digits, 4);         --             → 2627
  elsif v_digits ~ '^[0-9]{8}$' then       -- "2026/2027" → 20262027
    v_label := substr(v_digits, 3, 2) || substr(v_digits, 7, 2);
  elsif coalesce(v_digits, '') <> '' then  -- "2026"      → 2026
    v_label := v_digits;
  else
    -- A label with no digits at all: fall back to the season id, then to a
    -- constant, so this can never return NULL and produce a NULL match_id.
    v_label := coalesce(
      nullif(regexp_replace(p_season_id, '[^0-9]', '', 'g'), ''), '0000');
  end if;

  v_prefix := p_competition_id || '_' || v_label || '_';

  -- The highest sequence already used, +1. Reading the trailing digits of an
  -- existing ID is legitimate here and nowhere else: this function is what
  -- assigns them, so it is the one place that owns their meaning.
  select coalesce(max(substring(m.match_id from '([0-9]+)$')::integer), 0) + 1
  into v_next
  from public.matches m
  where m.match_id like v_prefix || '%'
    and m.match_id ~ ('^' || v_prefix || '[0-9]+$');

  return v_prefix || lpad(v_next::text, 3, '0');
end;
$$;


-- ── create_fixture ───────────────────────────────────────────────────────────
-- Adds one scheduled fixture. A fixture is not a result: it is inserted with
-- status='scheduled' and no score, which is exactly what validate.py check 4
-- demands, and it becomes reportable through the ordinary path immediately.

create or replace function public.create_fixture(
  p_competition_id text,
  p_home_team_id   text,
  p_away_team_id   text,
  p_season_id      text default null,
  p_date           date default null,
  p_kickoff        text default '',
  p_venue_id       text default null,
  p_matchday       integer default null,
  p_stage          text default ''
)
returns setof public.matches
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_season   text;
  v_type     text;
  v_stage    text;
  v_match    public.matches;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;

  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  -- 1. The competition must exist, and gives us the type that decides which
  --    stage vocabulary applies.
  select c.type into v_type
  from public.competitions c
  where c.competition_id = p_competition_id;
  if not found then
    raise exception 'unknown competition %', p_competition_id
      using errcode = 'P0002';
  end if;

  -- 2. Season: the active one unless told otherwise. seasons.status='active'
  --    is the single source of "now" for the whole build (DATA_MODEL.md), and
  --    reading the clock here instead would be a second, disagreeing answer.
  if p_season_id is null then
    select s.season_id into v_season
    from public.seasons s where s.status = 'active' limit 1;
    if v_season is null then
      raise exception 'no active season' using errcode = 'P0002';
    end if;
  else
    v_season := p_season_id;
    if not exists (select 1 from public.seasons s
                   where s.season_id = v_season) then
      raise exception 'unknown season %', v_season using errcode = 'P0002';
    end if;
  end if;

  -- 3. Authorized for this competition.
  if not public.can_report_competition(p_competition_id, v_season) then
    raise exception 'not assigned to this competition'
      using errcode = '42501';
  end if;

  -- 4. Two different teams (validate.py check 4).
  if p_home_team_id is null or p_away_team_id is null
     or p_home_team_id = '' or p_away_team_id = '' then
    raise exception 'both teams are required' using errcode = '22023';
  end if;
  if p_home_team_id = p_away_team_id then
    raise exception 'a team cannot play itself' using errcode = '22023';
  end if;

  -- 5. Kickoff matches the column's own constraint, but said in a sentence a
  --    reporter can act on rather than as a check-violation code.
  if p_kickoff is not null and p_kickoff <> ''
     and p_kickoff !~ '^[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?$' then
    raise exception 'kickoff must look like 15:00 (Malawi time)'
      using errcode = '22023';
  end if;

  -- 6. VALIDATE.PY CHECK 3, enforced at the source. A match whose teams are
  --    not entered in the competition+season fails the build, and a failed
  --    build means nothing deploys at all — including everyone else's results.
  if not exists (select 1 from public.entries e
                 where e.competition_id = p_competition_id
                   and e.season_id = v_season
                   and e.team_id = p_home_team_id) then
    raise exception 'home team is not entered in this competition this season'
      using errcode = '23503';
  end if;
  if not exists (select 1 from public.entries e
                 where e.competition_id = p_competition_id
                   and e.season_id = v_season
                   and e.team_id = p_away_team_id) then
    raise exception 'away team is not entered in this competition this season'
      using errcode = '23503';
  end if;

  if p_venue_id is not null and p_venue_id <> ''
     and not exists (select 1 from public.venues v
                     where v.venue_id = p_venue_id) then
    raise exception 'unknown venue %', p_venue_id using errcode = 'P0002';
  end if;

  -- 7. Stage (validate.py check 7). On a cup it must come from the knockout
  --    vocabulary; on a league it is the free-form md_<n> the matchday implies.
  v_stage := lower(trim(coalesce(p_stage, '')));
  if v_type = 'cup' then
    if v_stage = '' then
      raise exception 'a cup fixture needs a round' using errcode = '22023';
    end if;
    if v_stage not in ('r64', 'r32', 'r16', 'qf', 'sf', 'final', '3p') then
      raise exception 'invalid cup round %', v_stage using errcode = '22023';
    end if;
  elsif v_stage = '' and p_matchday is not null then
    v_stage := 'md_' || p_matchday::text;
  end if;

  -- 8. Duplicate guard. Not a constraint on the table — the same two teams
  --    legitimately meet twice in a season, and in a cup replay more than
  --    that. What is almost always a mistake is the SAME fixture entered
  --    twice on the same day, which is what a double-tap on a phone produces.
  if p_date is not null and exists (
       select 1 from public.matches m
       where m.competition_id = p_competition_id
         and m.season_id = v_season
         and m.home_team_id = p_home_team_id
         and m.away_team_id = p_away_team_id
         and m.date = p_date) then
    raise exception 'that fixture is already in the list for %', p_date
      using errcode = '23505';
  end if;

  insert into public.matches (
    match_id, competition_id, season_id, stage, matchday, date, kickoff,
    venue_id, home_team_id, away_team_id, status, source_type, confidence)
  values (
    public.next_match_id(p_competition_id, v_season),
    p_competition_id, v_season, v_stage, p_matchday, p_date,
    coalesce(p_kickoff, ''), nullif(p_venue_id, ''),
    p_home_team_id, p_away_team_id,
    -- No score, by construction: a fixture is a fixture until someone reports
    -- it. source_type records who put the fixture in; the result that lands on
    -- it later overwrites this with its own provenance.
    'scheduled', 'reporter', 'unconfirmed')
  returning * into v_match;

  return next v_match;
end;
$$;

comment on function public.create_fixture(text, text, text, text, date, text,
                                          text, integer, text) is
  'Add one scheduled fixture to a competition the caller is assigned to. '
  'Enforces validate.py checks 3, 4 and 7 at insert time.';

revoke execute on function public.create_fixture(text, text, text, text, date,
                                                 text, text, integer, text)
  from public, anon;
grant execute on function public.create_fixture(text, text, text, text, date,
                                                text, text, integer, text)
  to authenticated;


-- ── create_league ────────────────────────────────────────────────────────────
-- Admin only, and the reason is not caution about typos — it is that this is
-- the one call that MINTS IDs. A club_id and team_id, once written, are
-- referenced by every match, entry and goal that follows and are never
-- regenerated (DATA_MODEL.md). Creating a competition is therefore a
-- structural act, not data entry, and it is the single thing in the portal a
-- plain reporter cannot do.
--
-- One call creates the whole reportable unit: competition, its season row,
-- and a club + team + entry for every name pasted in. Anything less leaves a
-- competition that cannot hold a fixture, which is not a useful thing to have
-- created.

create or replace function public.create_league(
  p_name         text,
  p_short_code   text,
  p_teams        text[],
  p_type         text default 'league',
  p_gender       text default 'm',
  p_age_group    text default 'senior',
  p_tier         integer default null,
  p_region       text default '',
  p_country      text default 'MW',
  p_season_id    text default null,
  p_sponsor_name text default '',
  p_points_win   integer default 3,
  p_points_draw  integer default 1
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_comp     text;
  v_season   text;
  v_label    text;
  v_name     text;
  v_code     text;
  v_club     text;
  v_team     text;
  v_suffix   text;
  v_entry    text;
  v_n        integer;
  v_created  integer := 0;
  v_seen     text[] := '{}';
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  if not public.is_admin() then
    raise exception 'only an administrator can create a competition'
      using errcode = '42501';
  end if;

  -- 1. Shape of the competition itself.
  if coalesce(trim(p_name), '') = '' then
    raise exception 'the competition needs a name' using errcode = '22023';
  end if;
  if p_type not in ('league', 'cup') then
    raise exception 'type must be league or cup' using errcode = '22023';
  end if;
  if p_gender not in ('m', 'w') then
    raise exception 'gender must be m or w' using errcode = '22023';
  end if;
  if p_age_group not in ('senior', 'u20', 'u19', 'u17', 'u16', 'u15') then
    raise exception 'invalid age group %', p_age_group using errcode = '22023';
  end if;

  -- 2. competition_id. Supplied rather than derived: this is the string that
  --    appears in every match_id and in the public URL for the league, and
  --    guessing it from a sponsor-laden name produces something nobody wants
  --    to live with. Constrained to the documented alphabet.
  v_code := upper(regexp_replace(coalesce(p_short_code, ''), '[^A-Za-z0-9]', '', 'g'));
  if v_code = '' then
    raise exception 'the competition needs a short code (e.g. NRFA2)'
      using errcode = '22023';
  end if;
  if length(v_code) > 12 then
    raise exception 'short code must be 12 characters or fewer'
      using errcode = '22023';
  end if;
  v_comp := upper(coalesce(nullif(p_country, ''), 'MW')) || '_' || v_code;

  if exists (select 1 from public.competitions c
             where c.competition_id = v_comp) then
    raise exception 'competition % already exists', v_comp
      using errcode = '23505';
  end if;

  -- 3. Season.
  if p_season_id is null then
    select s.season_id into v_season
    from public.seasons s where s.status = 'active' limit 1;
    if v_season is null then
      raise exception 'no active season' using errcode = 'P0002';
    end if;
  else
    v_season := p_season_id;
  end if;

  select s.label into v_label
  from public.seasons s where s.season_id = v_season;
  if v_label is null then
    raise exception 'unknown season %', v_season using errcode = 'P0002';
  end if;

  -- 4. A competition with no teams cannot hold a fixture, so it is not a
  --    competition yet. Two is the minimum that can play a match.
  if p_teams is null or array_length(p_teams, 1) is null then
    raise exception 'add at least two teams' using errcode = '22023';
  end if;

  insert into public.competitions
    (competition_id, country, name, type, tier, gender, age_group, region)
  values
    (v_comp, upper(coalesce(nullif(p_country, ''), 'MW')), trim(p_name),
     p_type, p_tier, p_gender, p_age_group, coalesce(p_region, ''));

  insert into public.competition_seasons
    (competition_id, season_id, sponsor_name, points_win, points_draw, status)
  values
    (v_comp, v_season, coalesce(p_sponsor_name, ''),
     coalesce(p_points_win, 3), coalesce(p_points_draw, 1), 'active');

  -- 5. One club + team + entry per name.
  foreach v_name in array p_teams loop
    v_name := trim(coalesce(v_name, ''));
    continue when v_name = '';

    -- Case-insensitive dedupe of the pasted list itself, before touching the
    -- database: the same name twice would otherwise mint two clubs.
    if upper(v_name) = any (v_seen) then
      continue;
    end if;
    v_seen := v_seen || upper(v_name);

    -- Reuse a club that is already known by this name. Malawian clubs field
    -- teams across several competitions and the club is the same club — a
    -- second club_id for the same name would split its identity permanently.
    select c.club_id into v_club
    from public.clubs c
    where upper(c.name) = upper(v_name)
    limit 1;

    if v_club is null then
      v_code := public.club_code_from_name(v_name);
      v_club := 'MW_' || v_code;
      -- Collisions are expected — initials are short and clubs share them.
      v_n := 2;
      while exists (select 1 from public.clubs c where c.club_id = v_club) loop
        v_club := 'MW_' || v_code || v_n::text;
        v_n := v_n + 1;
      end loop;

      insert into public.clubs (club_id, name, short_name, region)
      values (v_club, v_name, v_name, coalesce(p_region, ''));
    end if;

    -- team_id = club_id + gender letter + squad level, per DATA_MODEL.md.
    -- squad_level 1: a league is created with its clubs' first teams. A
    -- reserve side is a separate team the same club fields elsewhere.
    v_suffix := upper(p_gender) || '1';
    v_team := v_club || '_' || v_suffix;

    if not exists (select 1 from public.teams t where t.team_id = v_team) then
      insert into public.teams
        (team_id, club_id, gender, age_group, squad_level, display_name)
      values
        (v_team, v_club, p_gender, p_age_group, 1, v_name);
    end if;

    -- entry_id follows the shape already in the data: competition, season
    -- label, then the team without its redundant leading prefix.
    v_entry := v_comp || '_' || v_label || '_' ||
               regexp_replace(v_team, '^' || v_comp || '_|^MW_', '');

    if not exists (select 1 from public.entries e
                   where e.competition_id = v_comp
                     and e.season_id = v_season
                     and e.team_id = v_team) then
      insert into public.entries
        (entry_id, competition_id, season_id, team_id, status)
      values (v_entry, v_comp, v_season, v_team, 'active');
      v_created := v_created + 1;
    end if;
  end loop;

  if v_created < 2 then
    -- Roll the whole thing back rather than leave a competition that cannot
    -- hold a fixture. The exception aborts the function's transaction, so the
    -- competition and season rows inserted above go with it.
    raise exception 'add at least two different teams (got %)', v_created
      using errcode = '22023';
  end if;

  -- teams_count is denormalized and read by the renderers; set it from what
  -- was actually created rather than from what was pasted.
  update public.competition_seasons
  set teams_count = v_created, updated_at = now()
  where competition_id = v_comp and season_id = v_season;

  return v_comp;
end;
$$;

comment on function public.create_league(text, text, text[], text, text, text,
  integer, text, text, text, text, integer, integer) is
  'Admin only. Creates a competition, its season row, and a club+team+entry '
  'per pasted name, in one transaction. Returns the new competition_id.';

revoke execute on function public.create_league(text, text, text[], text, text,
  text, integer, text, text, text, text, integer, integer) from public, anon;
grant execute on function public.create_league(text, text, text[], text, text,
  text, integer, text, text, text, text, integer, integer) to authenticated;


-- ── submit_match_report gains a source ───────────────────────────────────────
-- matches.source_ref has existed since 0001 and has never been written. It is
-- exactly the right column for "where did you see this result?" — a Facebook
-- post URL, or free text for a reporter who was told it over the phone.
--
-- source_type stays 'reporter'. It answers "how did this row get here?", and
-- the answer is still: a reporter typed it. That is what confidence and the
-- unconfirmed asterisk key off, and repointing it at 'facebook' would quietly
-- change how the result renders on the public site.
--
-- The 4-argument version is DROPPED rather than kept alongside. Two overloads
-- differing only by a defaulted trailing parameter are ambiguous to PostgREST
-- ("Could not choose the best candidate function"), which would break every
-- report. Dropping it means a browser still running the old app.js sends four
-- arguments and resolves to this function with p_source_ref defaulted — the
-- outcome is identical to today's.

drop function if exists public.submit_match_report(text, integer, integer, text);

create or replace function public.submit_match_report(
  p_match_id   text,
  p_home_score integer,
  p_away_score integer,
  p_status     text,
  p_source_ref text default ''
)
returns setof public.matches
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_is_admin boolean;
  v_match    public.matches;
  v_old      jsonb;
  v_new      jsonb;
  v_scored   boolean;
  v_source   text;
begin
  -- 1. Authenticated.
  if (select auth.uid()) is null then
    raise exception 'not authenticated'
      using errcode = '28000';
  end if;

  -- 2. A live reporter identity. current_reporter_id() already requires
  --    reporters.active, so a deactivated account fails here even though its
  --    login still works.
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  -- 3. The match must exist. Locked for the rest of the transaction so two
  --    reporters publishing the same match serialize instead of racing, and
  --    the audit rows come out in the order the changes actually happened.
  select * into v_match
  from public.matches
  where match_id = p_match_id
  for update;

  if not found then
    raise exception 'match not found'
      using errcode = 'P0002';
  end if;

  -- 4. Authorized for THIS match. The same question the UI asks, asked again
  --    where it counts — the client is not trusted.
  if not public.can_report_match(p_match_id) then
    raise exception 'not assigned to this competition'
      using errcode = '42501';
  end if;

  v_is_admin := public.is_admin();

  -- 5. Validate. These mirror validate.py check 4 and the table's own
  --    constraints; catching them here produces a message a reporter can act
  --    on rather than a constraint-violation code.
  if p_status not in ('scheduled', 'played', 'postponed', 'abandoned',
                      'cancelled', 'awarded') then
    raise exception 'invalid status %', p_status
      using errcode = '22023';
  end if;

  -- 'awarded' is an administrative decision — a walkover, a forfeit — not
  -- something observed at a ground. It counts into standings with its recorded
  -- score, so it stays out of the reporter path.
  if p_status = 'awarded' and not v_is_admin then
    raise exception 'only an administrator can record an awarded result'
      using errcode = '42501';
  end if;

  v_scored := p_status in ('played', 'awarded');

  if v_scored then
    if p_home_score is null or p_away_score is null then
      raise exception 'invalid score: a % result needs both scores', p_status
        using errcode = '22023';
    end if;
    if p_home_score < 0 or p_away_score < 0
       or p_home_score > 99 or p_away_score > 99 then
      raise exception 'invalid score: goals must be between 0 and 99'
        using errcode = '22023';
    end if;
  else
    -- A fixture that was not played carries no score. Rejecting rather than
    -- silently discarding: if the client sent one, the two disagree about what
    -- is being published and the reporter should see that.
    if p_home_score is not null or p_away_score is not null then
      raise exception 'invalid score: a % match cannot carry a score', p_status
        using errcode = '22023';
    end if;
  end if;

  -- Free text, length-capped so a paste accident cannot put a megabyte in the
  -- row. Blank leaves whatever was already recorded: a correction submitted
  -- without re-typing the link must not erase the link.
  v_source := left(trim(coalesce(p_source_ref, '')), 500);
  if v_source = '' then
    v_source := v_match.source_ref;
  end if;

  v_old := jsonb_build_object(
    'home_goals', v_match.home_goals,
    'away_goals', v_match.away_goals,
    'status',     v_match.status,
    'source_ref', v_match.source_ref);
  v_new := jsonb_build_object(
    'home_goals', p_home_score,
    'away_goals', p_away_score,
    'status',     p_status,
    'source_ref', v_source);

  -- 6. Write ONLY the reporting fields. home_team_id, away_team_id,
  --    competition_id, season_id, date, kickoff, venue_id and every other
  --    structural column are absent from this statement by design.
  update public.matches
  set home_goals  = p_home_score,
      away_goals  = p_away_score,
      status      = p_status,
      source_type = 'reporter',
      source_ref  = v_source,
      reported_by = v_reporter,
      reported_at = now(),
      confidence  = case when v_is_admin then 'confirmed' else 'unconfirmed' end,
      verified_by = case when v_is_admin then v_reporter else v_match.verified_by end,
      verified_at = case when v_is_admin then now() else v_match.verified_at end,
      updated_at  = now()
  where match_id = p_match_id
  returning * into v_match;

  -- 7. Record it — but only when something actually changed. Re-tapping
  --    publish on an unchanged result is a no-op worth attributing (steps 6
  --    still update reported_by/reported_at) and not worth a log row.
  if v_old is distinct from v_new then
    insert into public.match_change_log
      (match_id, changed_by, old_values, new_values)
    values (p_match_id, v_reporter, v_old, v_new);
  end if;

  return next v_match;
end;
$$;

comment on function public.submit_match_report(text, integer, integer, text, text) is
  'The only reporter write path to matches. Updates score/status/source_ref '
  'only, sets provenance, and appends to match_change_log.';

revoke execute on function public.submit_match_report(text, integer, integer, text, text)
  from public, anon;
grant execute on function public.submit_match_report(text, integer, integer, text, text)
  to authenticated;

commit;
