-- 0014_fixture_batch.sql — a whole fixture list in one submission, with the
-- stadium and where it came from.
--
-- WHY. A fixture list arrives as one Facebook graphic: a week, seven matches,
-- two dates, a kick-off time repeated seven times, and a ground under every
-- pairing. The portal took them one at a time, with no field for the ground
-- and no field for the graphic it was read off. Three consequences, all real:
--
--   1. Seven round trips over one or two bars of signal, each of which can
--      fail on its own and leave the list half entered with no record of how
--      far the reporter got.
--   2. matches.venue_id stayed NULL for every fixture entered from a phone,
--      so the site showed no ground for them while the sheet-imported rows
--      had one — the same column, populated for the old data and not the new.
--   3. matches.source_ref, which submit_match_report has written since 0008,
--      was blank until a RESULT landed. "Where did this fixture come from?"
--      is the same question as "where did this score come from?" and had no
--      answer for the weeks in between.
--
-- WHAT THIS ADDS.
--
--   insert_fixture     every rule create_fixture enforced, moved out into one
--                      internal function so the single and batch paths cannot
--                      drift — the same reason assert_date_in_season exists.
--   resolve_venue      a ground NAME to a venue_id, reusing an existing venue
--                      or minting one. "TBA" resolves to no venue at all.
--   create_fixture     unchanged behaviour, plus p_source_ref and
--                      p_venue_name.
--   create_fixtures    a list, in one call, row by row: a bad row is reported
--                      against its own line and the good rows around it are
--                      still saved.
--
-- PARTIAL SUCCESS IS THE POINT. All-or-nothing would mean one duplicate
-- pairing — the commonest mistake, because the reporter is copying from a
-- picture — throws away six correct fixtures and the typing that produced
-- them. That is rule 1 of the portal ("entered data is never destroyed by a
-- failure") stated at the database. So each row runs in its own exception
-- block, and the function returns a row per input row saying what happened,
-- for the client to put back on the failed lines.

begin;

-- ── A venue code from a venue name ───────────────────────────────────────────
-- Same job as club_code_from_name and deliberately not the same rule: a ground
-- is named for its place ("Likuni Ground", "Dapp Ground", "Mkanda Primary
-- School") and its initials would be noise. The place word is what identifies
-- it, so the generic half is dropped and the rest is kept whole — which is
-- what the venue_ids added by hand since the import already look like
-- (MW_LIKUNI, MW_DAPP, MW_MANGO, MW_KAMPALA).
--
-- Proposes only. Uniqueness is settled by the caller, which is the only place
-- that can see what is already taken.

create or replace function public.venue_code_from_name(p_name text)
returns text
language plpgsql
immutable
set search_path = ''
as $$
declare
  v_clean text;
  v_words text[];
  v_kept  text[] := '{}';
  v_word  text;
begin
  v_clean := regexp_replace(upper(public.unaccent_fallback(p_name)),
                            '[^A-Z0-9 ]', '', 'g');
  v_clean := trim(regexp_replace(v_clean, ' +', ' ', 'g'));
  v_words := array_remove(string_to_array(v_clean, ' '), '');

  if array_length(v_words, 1) is null then
    return 'V';
  end if;

  -- Words that appear in a third of the venue table and distinguish nothing.
  foreach v_word in array v_words loop
    if v_word not in ('GROUND', 'GROUNDS', 'STADIUM', 'SCHOOL', 'PRIMARY',
                      'SECONDARY', 'COMMUNITY', 'THE', 'OF', 'FC') then
      v_kept := v_kept || v_word;
    end if;
  end loop;

  -- A name made ENTIRELY of generic words ("Community Ground") still has to
  -- produce something; falling back to the whole name keeps it readable.
  if array_length(v_kept, 1) is null then
    v_kept := v_words;
  end if;

  -- Trimmed to something that still reads as the ground's name, with any
  -- separator the cut left dangling removed — MW_MKANDA, not MW_MKANDA_.
  v_clean := regexp_replace(left(array_to_string(v_kept, '_'), 16), '_+$', '');
  return coalesce(nullif(v_clean, ''), 'V');
end;
$$;


-- ── A ground name to a venue_id ──────────────────────────────────────────────
-- matches.venue_id is a foreign key, and the reporter reading a graphic has a
-- NAME. Something has to bridge the two, and the alternatives were both bad:
-- offering only the 77 venues already in the table means the ground on the
-- picture ("Mkanda Primary School") cannot be entered at all, and leaving the
-- column NULL is what we already had.
--
-- So a reporter may mint a venue, which create_league's admin-only rule
-- explicitly does not allow for a club. The difference is what the id MEANS. A
-- club_id is an identity: a second one for the same club splits its history
-- across the site permanently and is not repairable by editing one row. A
-- venue_id is a label on a place, referenced by matches.venue_id and nothing
-- else, and a duplicate is a merge — an afternoon's tidying, not a corruption.
--
-- Matching is exact after normalizing case, punctuation and spacing, and
-- deliberately no cleverer. Stripping "Ground"/"Stadium" to match harder would
-- merge Mchinji Stadium, Mchinji Mini Stadium and Mchinji Community Ground,
-- which are three different places. The form offers the existing names as an
-- autocomplete list instead, which is where near-duplicates are actually
-- prevented — by the reporter picking the one that is already there.

create or replace function public.resolve_venue(p_name text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_name  text;
  v_norm  text;
  v_id    text;
  v_code  text;
  v_n     integer;
begin
  v_name := trim(regexp_replace(coalesce(p_name, ''), '\s+', ' ', 'g'));
  if v_name = '' then
    return null;
  end if;

  -- A fixture list that says the ground is not settled yet is telling us
  -- exactly what a NULL venue_id already means. Writing it down as a venue
  -- called "TBA" would put "To be announced" on the site as if it were a
  -- place, and leave the row looking answered when it is not.
  if upper(regexp_replace(v_name, '[^A-Za-z]', '', 'g')) in
     ('TBA', 'TBC', 'TOBEANNOUNCED', 'TOBECONFIRMED', 'VENUETBA', 'VENUETBC',
      'NOTANNOUNCED', 'UNKNOWN', 'NA') then
    return null;
  end if;

  v_norm := upper(regexp_replace(public.unaccent_fallback(v_name),
                                 '[^A-Za-z0-9]', '', 'g'));

  select v.venue_id into v_id
  from public.venues v
  where upper(regexp_replace(public.unaccent_fallback(v.name),
                             '[^A-Za-z0-9]', '', 'g')) = v_norm
  order by v.ord
  limit 1;
  if v_id is not null then
    return v_id;
  end if;

  v_code := public.venue_code_from_name(v_name);
  v_id := 'MW_' || v_code;
  v_n := 2;
  while exists (select 1 from public.venues v where v.venue_id = v_id) loop
    v_id := 'MW_' || v_code || v_n::text;
    v_n := v_n + 1;
  end loop;

  -- city and capacity stay blank: 60 of the 76 imported rows have no capacity
  -- and the graphic does not carry either. Blank is the honest value, and both
  -- columns are free text that can be filled in later without touching the id.
  insert into public.venues (venue_id, name)
  values (v_id, v_name);

  return v_id;
end;
$$;

comment on function public.resolve_venue(text) is
  'Ground name -> venue_id, reusing an existing venue or minting one. '
  'A "TBA" name resolves to NULL, which is what an unfixed venue already is.';

-- Internal. Reached only through create_fixture/create_fixtures, which run as
-- the owner and have already established who the caller is and what they may
-- write to. Nothing calls it over the Data API.
revoke execute on function public.resolve_venue(text)
  from public, anon, authenticated;


-- ── One fixture, all the rules, no authorization ─────────────────────────────
-- The body of create_fixture from 0009, verbatim in substance, lifted out so
-- that the single-fixture and whole-list paths run the SAME checks. The two
-- drifting apart is the failure this shape exists to prevent: every rule here
-- is a validate.py check, and a rule enforced on one path and not the other
-- means a build that fails on rows entered the other way.
--
-- Authorization is NOT here. It stays in the two callers, which is where the
-- caller is known. This function trusts p_season_id to have been resolved and
-- p_competition_id to have been authorized already, which is exactly why it is
-- not granted to anybody.

create or replace function public.insert_fixture(
  p_competition_id text,
  p_season_id      text,
  p_comp_type      text,
  p_home_team_id   text,
  p_away_team_id   text,
  p_date           date,
  p_kickoff        text,
  p_venue_id       text,
  p_matchday       integer,
  p_stage          text,
  p_source_ref     text
)
returns public.matches
language plpgsql
set search_path = ''
as $$
declare
  v_stage text;
  v_match public.matches;
begin
  -- Two different teams (validate.py check 4).
  if p_home_team_id is null or p_away_team_id is null
     or p_home_team_id = '' or p_away_team_id = '' then
    raise exception 'both teams are required' using errcode = '22023';
  end if;
  if p_home_team_id = p_away_team_id then
    raise exception 'a team cannot play itself' using errcode = '22023';
  end if;

  -- Kickoff matches the column's own constraint, but said in a sentence a
  -- reporter can act on rather than as a check-violation code.
  if p_kickoff is not null and p_kickoff <> ''
     and p_kickoff !~ '^[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?$' then
    raise exception 'kickoff must look like 15:00 (Malawi time)'
      using errcode = '22023';
  end if;

  -- VALIDATE.PY CHECK 6. A mistyped year is one keystroke away and would
  -- otherwise fail every build until someone found it.
  perform public.assert_date_in_season(p_season_id, p_date);

  -- VALIDATE.PY CHECK 3, enforced at the source. A match whose teams are not
  -- entered in the competition+season fails the build, and a failed build
  -- means nothing deploys at all — including everyone else's results.
  if not exists (select 1 from public.entries e
                 where e.competition_id = p_competition_id
                   and e.season_id = p_season_id
                   and e.team_id = p_home_team_id) then
    raise exception 'home team is not entered in this competition this season'
      using errcode = '23503';
  end if;
  if not exists (select 1 from public.entries e
                 where e.competition_id = p_competition_id
                   and e.season_id = p_season_id
                   and e.team_id = p_away_team_id) then
    raise exception 'away team is not entered in this competition this season'
      using errcode = '23503';
  end if;

  if p_venue_id is not null and p_venue_id <> ''
     and not exists (select 1 from public.venues v
                     where v.venue_id = p_venue_id) then
    raise exception 'unknown venue %', p_venue_id using errcode = 'P0002';
  end if;

  -- Stage (validate.py check 7). On a cup it must come from the knockout
  -- vocabulary; on a league it is the free-form md_<n> the matchday implies.
  v_stage := lower(trim(coalesce(p_stage, '')));
  if p_comp_type = 'cup' then
    if v_stage = '' then
      raise exception 'a cup fixture needs a round' using errcode = '22023';
    end if;
    if v_stage not in ('r64', 'r32', 'r16', 'qf', 'sf', 'final', '3p') then
      raise exception 'invalid cup round %', v_stage using errcode = '22023';
    end if;
  elsif v_stage = '' and p_matchday is not null then
    v_stage := 'md_' || p_matchday::text;
  end if;

  -- Duplicate guard. Not a constraint on the table — the same two teams
  -- legitimately meet twice in a season, and in a cup replay more than that.
  -- What is almost always a mistake is the SAME fixture entered twice on the
  -- same day, which is what a double-tap on a phone produces — and what
  -- submitting a list twice produces, which is now the likelier way in.
  if p_date is not null and exists (
       select 1 from public.matches m
       where m.competition_id = p_competition_id
         and m.season_id = p_season_id
         and m.home_team_id = p_home_team_id
         and m.away_team_id = p_away_team_id
         and m.date = p_date) then
    raise exception 'that fixture is already in the list for %', p_date
      using errcode = '23505';
  end if;

  insert into public.matches (
    match_id, competition_id, season_id, stage, matchday, date, kickoff,
    venue_id, home_team_id, away_team_id, status, source_type, source_ref,
    confidence)
  values (
    public.next_match_id(p_competition_id, p_season_id),
    p_competition_id, p_season_id, v_stage, p_matchday, p_date,
    coalesce(p_kickoff, ''), nullif(p_venue_id, ''),
    p_home_team_id, p_away_team_id,
    -- No score, by construction: a fixture is a fixture until someone reports
    -- it. source_type records who put the fixture in; the result that lands on
    -- it later overwrites this with its own provenance.
    --
    -- source_ref is where the fixture was READ, and it survives that
    -- overwrite: submit_match_report keeps the existing source_ref when the
    -- reporter does not supply a new one, so the graphic that announced the
    -- match still explains the row if nobody says where the score came from.
    'scheduled', 'reporter', left(trim(coalesce(p_source_ref, '')), 500),
    'unconfirmed')
  returning * into v_match;

  return v_match;
end;
$$;

revoke execute on function public.insert_fixture(text, text, text, text, text,
  date, text, text, integer, text, text) from public, anon, authenticated;


-- ── create_fixture, now with a source and a ground ───────────────────────────
-- Same contract as 0009 plus two trailing arguments, and the body reduced to
-- what only it can do: establish the caller, resolve the season, check the
-- assignment. Everything after that is insert_fixture.
--
-- The 9-argument version is DROPPED rather than kept alongside, for the reason
-- 0008 gives about submit_match_report: two overloads differing only by
-- defaulted trailing parameters are ambiguous to PostgREST ("Could not choose
-- the best candidate function"), which would break every call. A browser still
-- running the old app.js sends nine arguments and resolves to this function
-- with the two new ones defaulted — the outcome is identical to today's.

drop function if exists public.create_fixture(text, text, text, text, date,
                                              text, text, integer, text);

create or replace function public.create_fixture(
  p_competition_id text,
  p_home_team_id   text,
  p_away_team_id   text,
  p_season_id      text default null,
  p_date           date default null,
  p_kickoff        text default '',
  p_venue_id       text default null,
  p_matchday       integer default null,
  p_stage          text default '',
  p_source_ref     text default '',
  p_venue_name     text default ''
)
returns setof public.matches
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_season text;
  v_type   text;
  v_venue  text;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  if public.current_reporter_id() is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  select c.type into v_type
  from public.competitions c
  where c.competition_id = p_competition_id;
  if not found then
    raise exception 'unknown competition %', p_competition_id
      using errcode = 'P0002';
  end if;

  -- The active season unless told otherwise. seasons.status='active' is the
  -- single source of "now" for the whole build (DATA_MODEL.md), and reading
  -- the clock here instead would be a second, disagreeing answer.
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

  if not public.can_report_competition(p_competition_id, v_season) then
    raise exception 'not assigned to this competition'
      using errcode = '42501';
  end if;

  -- An explicit id wins over a typed name: the caller that has one has already
  -- chosen a specific venue, and re-resolving its name could only disagree.
  v_venue := nullif(coalesce(p_venue_id, ''), '');
  if v_venue is null then
    v_venue := public.resolve_venue(p_venue_name);
  end if;

  return next public.insert_fixture(
    p_competition_id, v_season, v_type, p_home_team_id, p_away_team_id,
    p_date, p_kickoff, v_venue, p_matchday, p_stage, p_source_ref);
end;
$$;

comment on function public.create_fixture(text, text, text, text, date, text,
                                          text, integer, text, text, text) is
  'Add one scheduled fixture to a competition the caller is assigned to. '
  'Enforces validate.py checks 3, 4, 6 and 7 at insert time. p_venue_name '
  'is resolved to a venue_id, minting one if the ground is new.';

revoke execute on function public.create_fixture(text, text, text, text, date,
  text, text, integer, text, text, text) from public, anon;
grant execute on function public.create_fixture(text, text, text, text, date,
  text, text, integer, text, text, text) to authenticated;


-- ── create_fixtures: a whole week's list, one submission ─────────────────────
-- p_fixtures is a JSON array, one object per line of the graphic:
--
--   [{"home": "MW_LIK_M1", "away": "MW_MSP_M1", "date": "2026-08-22",
--     "kickoff": "14:30", "venue": "Likuni Ground", "matchday": 5},
--    {"home": "...", "away": "...", "stage": "qf"}]
--
-- date, kickoff, venue, matchday and stage are all optional per row; the
-- client sends the shared ones down every row rather than making this function
-- guess what "applies to all" meant.
--
-- RETURNS ONE ROW PER INPUT ROW, in order, whether it worked or not. The
-- client puts `message` back on the line that failed and leaves the reporter
-- looking at exactly the fixtures still to fix — which is only possible
-- because `idx` says WHICH line, and because the rows around it were kept.
--
-- The authorization work is done ONCE, before the loop, rather than per row:
-- every row goes into the one competition the reporter chose, so re-asking
-- would be the same question answered n times over a phone connection.

create or replace function public.create_fixtures(
  p_competition_id text,
  p_fixtures       jsonb,
  p_source_ref     text default '',
  p_season_id      text default null
)
returns table (
  idx       integer,
  ok        boolean,
  match_id  text,
  public_id uuid,
  message   text
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_season text;
  v_type   text;
  v_row    jsonb;
  v_i      integer := 0;
  v_venue  text;
  v_match  public.matches;
  v_date   date;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  if public.current_reporter_id() is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  if p_fixtures is null or jsonb_typeof(p_fixtures) <> 'array' then
    raise exception 'send a list of fixtures' using errcode = '22023';
  end if;
  if jsonb_array_length(p_fixtures) = 0 then
    raise exception 'add at least one fixture' using errcode = '22023';
  end if;
  -- A ceiling, not a guess at what a fixture list looks like: the biggest
  -- round in the country is comfortably under this, and it stops a malformed
  -- or replayed call from holding a connection open inserting for minutes.
  if jsonb_array_length(p_fixtures) > 60 then
    raise exception 'that is more than 60 fixtures — send them in two goes'
      using errcode = '22023';
  end if;

  select c.type into v_type
  from public.competitions c
  where c.competition_id = p_competition_id;
  if not found then
    raise exception 'unknown competition %', p_competition_id
      using errcode = 'P0002';
  end if;

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

  if not public.can_report_competition(p_competition_id, v_season) then
    raise exception 'not assigned to this competition'
      using errcode = '42501';
  end if;

  for v_row in select * from jsonb_array_elements(p_fixtures) loop
    v_i := v_i + 1;
    begin
      -- A blank date is a fixture with no day yet, which is legitimate and
      -- always has been. '' would be a cast error, so it is normalized here
      -- rather than made the client's problem.
      v_date := nullif(trim(coalesce(v_row->>'date', '')), '')::date;

      -- Resolving the ground is inside the per-row block on purpose: a new
      -- venue minted for a row that then fails its own checks would otherwise
      -- be left behind with nothing pointing at it. The subtransaction takes
      -- it back out with the failed insert.
      v_venue := nullif(coalesce(v_row->>'venue_id', ''), '');
      if v_venue is null then
        v_venue := public.resolve_venue(v_row->>'venue');
      end if;

      v_match := public.insert_fixture(
        p_competition_id, v_season, v_type,
        trim(coalesce(v_row->>'home', '')),
        trim(coalesce(v_row->>'away', '')),
        v_date,
        trim(coalesce(v_row->>'kickoff', '')),
        v_venue,
        nullif(trim(coalesce(v_row->>'matchday', '')), '')::integer,
        coalesce(v_row->>'stage', ''),
        p_source_ref);

      idx := v_i; ok := true;
      match_id := v_match.match_id; public_id := v_match.public_id;
      message := '';
      return next;

    exception
      -- Every raise in insert_fixture is already a sentence written for a
      -- reporter to act on, so it is carried out as-is; the client shows it
      -- against the line it belongs to. Catching everything (rather than the
      -- listed error codes) is deliberate: an unforeseen failure on row four
      -- must still not discard rows one to three.
      when others then
        idx := v_i; ok := false;
        match_id := null; public_id := null;
        message := sqlerrm;
        return next;
    end;
  end loop;
end;
$$;

comment on function public.create_fixtures(text, jsonb, text, text) is
  'Add a whole fixture list in one call. Returns a row per input row: a bad '
  'row is reported against its index and the good rows around it are saved.';

revoke execute on function public.create_fixtures(text, jsonb, text, text)
  from public, anon;
grant execute on function public.create_fixtures(text, jsonb, text, text)
  to authenticated;

commit;
