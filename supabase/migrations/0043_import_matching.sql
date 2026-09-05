-- 0043_import_matching.sql — turning raw names into fixtures, in code.
--
-- THE RULE THIS MIGRATION EXISTS TO ENFORCE: the model extracts, the database
-- resolves. A model asked to pick an Everyleague id will produce one — a
-- plausible, well-formed, confidently-wrong one — and nothing downstream can
-- tell that apart from a right one. So it is never asked. It returns the names
-- as they appear on the graphic, and everything from there is joins against
-- rows we already have.
--
-- That also means the model's own confidence is not used as the confidence.
-- What decides green/yellow/red here is evidence: how a name matched, how many
-- fixtures exist for the pairing, whether the date agrees, and whether another
-- team in the same competition answers to the same name.
--
-- WHY IN SQL AND NOT IN app.js. Matching needs every team, every club, every
-- alias, every entry and every fixture in the season. On the phone that is a
-- download; here it is a join. The reporter app already asks the database to
-- rank a name (search_players, 0022/0034) for exactly this reason.
--
-- THE SCOPE IS THE AUTHORIZATION. Candidates come only from competitions the
-- CALLER may report, in one season. That is not merely a relevance filter —
-- it means an import can never propose a fixture in a league the reporter has
-- no business touching, so the review screen cannot show them a row that
-- submit_match_reports would refuse. can_report_competition is asked again at
-- publish time regardless; this is the same answer, earlier, so the screen
-- tells the truth.
--
-- WHAT IS NOT HERE. Creating anything. No team is minted, no alias is written,
-- no player is looked up. An unresolvable name comes back unresolved and a
-- person deals with it. The importer's failure mode must be "I could not tell"
-- and never "I made one".

begin;

-- ── The normalizer ───────────────────────────────────────────────────────────
-- Case, accents and punctuation removed; nothing else. Deliberately not
-- cleverer, for resolve_venue's reason (0014): stripping words to match harder
-- is how "Blue Eagles" and "Blue Eagles Reserves" become the same club. The
-- generous matching happens in the ranked tiers below, where it is labelled
-- and can be shown to the reporter, rather than hidden inside a key.

create or replace function public.import_normalize(p_text text)
returns text
language sql
immutable
set search_path = ''
as $$
  select upper(regexp_replace(
           public.unaccent_fallback(coalesce(p_text, '')), '[^A-Za-z0-9]', '', 'g'))
$$;

revoke execute on function public.import_normalize(text) from public, anon;
grant execute on function public.import_normalize(text) to authenticated;


-- ── Parsing what a model wrote, without trusting it ──────────────────────────
-- The extraction is asked for an ISO date and an integer matchday. It will
-- usually give them. It will sometimes give "Sat 5 Sep", "MD6", "" or the word
-- "unknown", because the graphic said that and the schema is a request rather
-- than a guarantee.
--
-- A plain ::date cast on any of those raises, and a raise here aborts the
-- WHOLE import — eight readable results thrown away because the model copied
-- a date badly on one of them. That is the opposite of what this feature is
-- for, so an unparseable value becomes NULL, which the matcher already knows
-- how to handle: it means "no date was given", and the pairing is matched
-- without it.

create or replace function public.import_safe_date(p_text text)
returns date
language plpgsql
immutable
set search_path = ''
as $$
begin
  return nullif(trim(coalesce(p_text, '')), '')::date;
exception
  when others then
    return null;
end;
$$;

create or replace function public.import_safe_int(p_text text)
returns integer
language plpgsql
immutable
set search_path = ''
as $$
begin
  -- Digits only: "MD6" and "Matchday 6" both mean 6, and a model that wrote
  -- either is not wrong about the football.
  return nullif(regexp_replace(coalesce(p_text, ''), '[^0-9]', '', 'g'), '')::integer;
exception
  when others then
    return null;
end;
$$;

revoke execute on function public.import_safe_date(text) from public, anon;
revoke execute on function public.import_safe_int(text) from public, anon;
grant execute on function public.import_safe_date(text) to authenticated;
grant execute on function public.import_safe_int(text) to authenticated;


-- ── One raw name to candidate teams ──────────────────────────────────────────
-- Ranked tiers, best first. The RANK IS PART OF THE ANSWER: tier 1-4 is an
-- exact match on some name the database already holds for that team, and tier
-- 5 is a guess. The caller shows tier 5 to the reporter and never treats it as
-- settled, which is what "conservative normalization, then candidate fuzzy
-- matches" means in practice.
--
--   1  the team's own display_name          "Blue Eagles"
--   2  an alias recorded for the team       "Eagles" (someone filed it before)
--   3  the club's name or short name        "Blue Eagles FC" -> the club
--   4  an alias recorded for the club
--   5  containment, both directions         "Blue Eagles Fc" ~ "Blue Eagles"
--
-- Tier 5 is guarded by length: a three-letter fragment is inside half the
-- table and matching on it would produce noise that looks like evidence.
--
-- p_competitions scopes the search, and is the caller's authorized set.

create or replace function public.import_team_candidates(
  p_raw          text,
  p_competitions text[],
  p_season_id    text
)
returns table (team_id text, team_name text, rank integer, method text)
language sql
stable
security definer
set search_path = ''
as $$
  with wanted as (
    select public.import_normalize(p_raw) as key
  ),
  -- Every team entered in an authorized competition this season, with the
  -- club behind it. `entries` is what makes a team eligible at all — the same
  -- rule validate.py check 3 enforces, so a candidate that came from here can
  -- always legally hold a fixture.
  pool as (
    select distinct t.team_id, t.display_name, t.club_id
    from public.entries e
    join public.teams t on t.team_id = e.team_id
    where e.competition_id = any (p_competitions)
      and e.season_id = p_season_id
  ),
  scored as (
    select p.team_id, p.display_name, 1 as rank, 'name' as method
    from pool p, wanted w
    where public.import_normalize(p.display_name) = w.key and w.key <> ''

    union all
    select p.team_id, p.display_name, 2, 'team_alias'
    from pool p, wanted w
    join public.aliases a
      on a.entity_type = 'team' and public.import_normalize(a.alias_text) = w.key
    where a.entity_id = p.team_id and w.key <> ''

    union all
    select p.team_id, p.display_name, 3, 'club_name'
    from pool p
    join public.clubs c on c.club_id = p.club_id, wanted w
    where w.key <> ''
      and (public.import_normalize(c.name) = w.key
           or (c.short_name <> '' and public.import_normalize(c.short_name) = w.key))

    union all
    select p.team_id, p.display_name, 4, 'club_alias'
    from pool p, wanted w
    join public.aliases a
      on a.entity_type = 'club' and public.import_normalize(a.alias_text) = w.key
    where a.entity_id = p.club_id and w.key <> ''

    union all
    -- The guess. Both directions, because a graphic says both "Blue Eagles FC"
    -- (longer than the record) and "Blue Eagles" for "Blue Eagles Reserves"
    -- (shorter). Six characters is where a fragment stops being a coincidence
    -- in a table of Malawian club names.
    select p.team_id, p.display_name, 5, 'partial'
    from pool p, wanted w
    where length(w.key) >= 6
      and length(public.import_normalize(p.display_name)) >= 6
      and (public.import_normalize(p.display_name) like '%' || w.key || '%'
           or w.key like '%' || public.import_normalize(p.display_name) || '%')
  )
  select s.team_id, max(s.display_name), min(s.rank)::integer,
         (array_agg(s.method order by s.rank))[1]
  from scored s
  group by s.team_id
  order by min(s.rank), max(s.display_name)
$$;

comment on function public.import_team_candidates(text, text[], text) is
  'Raw name -> candidate teams, ranked by HOW they matched. Tier 5 is a guess '
  'and is labelled as one. Scoped to the caller''s authorized competitions.';

revoke execute on function public.import_team_candidates(text, text[], text)
  from public, anon;
grant execute on function public.import_team_candidates(text, text[], text)
  to authenticated;


-- ── resolve_import_candidates ────────────────────────────────────────────────
-- p_items is the extraction, one object per result the model read:
--
--   [{"idx": 1, "home_team_raw": "Blue Eagles", "away_team_raw": "Silver",
--     "home_score": 2, "away_score": 1, "status": "played",
--     "date": "2026-09-05", "matchday": 6}]
--
-- Returns ONE jsonb object, which is exactly what goes into
-- report_imports.resolved. jsonb rather than a TABLE deliberately: the answer
-- is nested (each item carries its own candidate lists), and a function with
-- out-parameters named match_id/competition_id/status would have to fight
-- plpgsql over every unqualified column for the rest of its body.
--
-- THREE PASSES.
--   1. Resolve both names, find every fixture the pairing could be.
--   2. Work out what the SUBMISSION as a whole looks like — the competition
--      and matchday most of the confident items agree on.
--   3. Use that consensus to break ties, and only ties.
--
-- Consensus is supporting evidence, never proof: an item resolved by it is
-- yellow, so a reporter confirms it. A graphic of one league's matchday is the
-- normal case and that is exactly when consensus is strongest — which is also
-- exactly when a wrong tie-break would be least visible.

create or replace function public.resolve_import_candidates(
  p_items     jsonb,
  p_season_id text default null
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_reporter    text;
  v_season      text;
  v_comps       text[];
  v_item        jsonb;
  v_i           integer := 0;
  v_items       jsonb := '[]'::jsonb;
  v_home        jsonb;
  v_away        jsonb;
  v_home_ids    text[];
  v_away_ids    text[];
  v_best_rank   integer;
  v_fixtures    jsonb;
  v_n           integer;
  v_reasons     text[];
  v_conf        text;
  v_pick        jsonb;
  v_date        date;
  v_md          integer;
  v_consensus_c text;
  v_consensus_s text;
  v_pass2       jsonb := '[]'::jsonb;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  if p_items is null or jsonb_typeof(p_items) <> 'array' then
    raise exception 'send a list of results' using errcode = '22023';
  end if;
  if jsonb_array_length(p_items) > 60 then
    raise exception 'that is more than 60 results in one import'
      using errcode = '22023';
  end if;

  if p_season_id is null then
    select s.season_id into v_season
    from public.seasons s where s.status = 'active' limit 1;
    if v_season is null then
      raise exception 'no active season' using errcode = 'P0002';
    end if;
  else
    v_season := p_season_id;
  end if;

  -- THE AUTHORIZED SET, resolved once. An admin gets every competition; a
  -- reporter gets exactly their assignments. Everything below is scoped to
  -- this, so no proposal can name a fixture the caller could not publish.
  if public.is_admin() then
    select coalesce(array_agg(c.competition_id), '{}')
    into v_comps from public.competitions c;
  else
    select coalesce(array_agg(distinct a.competition_id), '{}')
    into v_comps
    from public.reporter_assignments a
    join public.reporters r on r.reporter_id = a.reporter_id
    where r.auth_user_id = (select auth.uid())
      and r.active
      and (a.season_id is null or a.season_id = v_season);
  end if;

  if array_length(v_comps, 1) is null then
    return jsonb_build_object(
      'season_id', v_season, 'competitions', '[]'::jsonb,
      'consensus', null, 'items', '[]'::jsonb,
      'note', 'no competitions assigned');
  end if;

  -- ── Pass 1 ─────────────────────────────────────────────────────────────────
  for v_item in select * from jsonb_array_elements(p_items) loop
    v_i := v_i + 1;
    v_reasons := '{}';

    -- Never a bare cast: see import_safe_date. One badly-copied date must not
    -- cost the other seven results on the graphic.
    v_date := public.import_safe_date(v_item->>'date');
    v_md   := public.import_safe_int(v_item->>'matchday');

    select coalesce(jsonb_agg(jsonb_build_object(
             'team_id', k.team_id, 'name', k.team_name,
             'rank', k.rank, 'method', k.method) order by k.rank), '[]'::jsonb)
    into v_home
    from public.import_team_candidates(
           v_item->>'home_team_raw', v_comps, v_season) k;

    select coalesce(jsonb_agg(jsonb_build_object(
             'team_id', k.team_id, 'name', k.team_name,
             'rank', k.rank, 'method', k.method) order by k.rank), '[]'::jsonb)
    into v_away
    from public.import_team_candidates(
           v_item->>'away_team_raw', v_comps, v_season) k;

    select coalesce(array_agg(x->>'team_id'), '{}') into v_home_ids
    from jsonb_array_elements(v_home) x;
    select coalesce(array_agg(x->>'team_id'), '{}') into v_away_ids
    from jsonb_array_elements(v_away) x;

    -- Every fixture this pairing could be. BOTH ORIENTATIONS are searched
    -- because a results graphic is a picture, and pictures put the winner
    -- first, or the home side on the right, or whatever the designer felt —
    -- the orientation is recorded per candidate so the reporter can see that
    -- the sides came back swapped rather than silently having them swapped.
    select coalesce(jsonb_agg(f order by f->>'date'), '[]'::jsonb)
    into v_fixtures
    from (
      select jsonb_build_object(
               'match_id', m.match_id,
               'public_id', m.public_id,
               'competition_id', m.competition_id,
               'season_id', m.season_id,
               'stage', m.stage,
               'matchday', m.matchday,
               'date', m.date,
               'status', m.status,
               'home_goals', m.home_goals,
               'away_goals', m.away_goals,
               'home_team_id', m.home_team_id,
               'away_team_id', m.away_team_id,
               'home_name', th.display_name,
               'away_name', ta.display_name,
               'orientation', case when m.home_team_id = any (v_home_ids)
                                   then 'as_given' else 'flipped' end,
               'date_matches', (v_date is null or m.date = v_date),
               'matchday_matches', (v_md is null or m.matchday = v_md)
             ) as f
      from public.matches m
      join public.teams th on th.team_id = m.home_team_id
      join public.teams ta on ta.team_id = m.away_team_id
      where m.competition_id = any (v_comps)
        and m.season_id = v_season
        and ((m.home_team_id = any (v_home_ids) and m.away_team_id = any (v_away_ids))
          or (m.home_team_id = any (v_away_ids) and m.away_team_id = any (v_home_ids)))
    ) q;

    -- A date on the graphic is strong evidence, so it NARROWS rather than
    -- merely annotating: two legs of the same pairing are the commonest reason
    -- a pairing has more than one fixture, and the date is what tells them
    -- apart. Only when it leaves something, though — a date that matches
    -- nothing is more likely misread than authoritative.
    if v_date is not null and jsonb_array_length(v_fixtures) > 1 then
      if exists (select 1 from jsonb_array_elements(v_fixtures) x
                 where (x->>'date_matches')::boolean) then
        select coalesce(jsonb_agg(x), '[]'::jsonb) into v_fixtures
        from jsonb_array_elements(v_fixtures) x
        where (x->>'date_matches')::boolean;
        v_reasons := v_reasons || 'narrowed_by_date';
      else
        v_reasons := v_reasons || 'date_not_found';
      end if;
    end if;

    if v_md is not null and jsonb_array_length(v_fixtures) > 1 then
      if exists (select 1 from jsonb_array_elements(v_fixtures) x
                 where (x->>'matchday_matches')::boolean) then
        select coalesce(jsonb_agg(x), '[]'::jsonb) into v_fixtures
        from jsonb_array_elements(v_fixtures) x
        where (x->>'matchday_matches')::boolean;
        v_reasons := v_reasons || 'narrowed_by_matchday';
      end if;
    end if;

    v_n := jsonb_array_length(v_fixtures);
    v_pick := case when v_n = 1 then v_fixtures->0 else null end;

    -- The worse of the two name matches decides how much the pairing is worth.
    v_best_rank := greatest(
      coalesce((v_home->0->>'rank')::integer, 99),
      coalesce((v_away->0->>'rank')::integer, 99));

    if jsonb_array_length(v_home) = 0 or jsonb_array_length(v_away) = 0 then
      v_conf := 'red';
      v_reasons := v_reasons || 'team_not_found';
    elsif v_n = 0 then
      v_conf := 'red';
      v_reasons := v_reasons || 'no_fixture';
    elsif v_n > 1 then
      -- Left for pass 3, which may be able to break the tie.
      v_conf := 'red';
      v_reasons := v_reasons || 'several_fixtures';
    else
      v_conf := 'green';
      -- ...and then everything that takes the certainty back off it.
      if v_best_rank >= 5 then
        v_conf := 'yellow';
        v_reasons := v_reasons || 'name_guessed';
      end if;
      -- TWO TEAMS ANSWER TO THIS NAME. The senior/reserve case, and the whole
      -- reason a close call is never resolved silently: both are in the same
      -- competition, both are real, and only a person knows which was meant.
      if jsonb_array_length(v_home) > 1
         and (v_home->1->>'rank')::integer = (v_home->0->>'rank')::integer then
        v_conf := 'yellow';
        v_reasons := v_reasons || 'home_name_ambiguous';
      end if;
      if jsonb_array_length(v_away) > 1
         and (v_away->1->>'rank')::integer = (v_away->0->>'rank')::integer then
        v_conf := 'yellow';
        v_reasons := v_reasons || 'away_name_ambiguous';
      end if;
      if v_pick->>'orientation' = 'flipped' then
        v_conf := 'yellow';
        v_reasons := v_reasons || 'sides_swapped';
      end if;
      -- Already has a result. Not the importer's call to overwrite — the grid
      -- asks, exactly as it does for a typed correction.
      if (v_pick->>'status') is distinct from 'scheduled' then
        v_conf := 'yellow';
        v_reasons := v_reasons || 'already_has_result';
      end if;
      if v_date is not null and not (v_pick->>'date_matches')::boolean then
        v_conf := 'yellow';
        v_reasons := v_reasons || 'date_disagrees';
      end if;
    end if;

    v_items := v_items || jsonb_build_array(jsonb_build_object(
      'idx', v_i,
      'confidence', v_conf,
      'reasons', to_jsonb(v_reasons),
      'raw', jsonb_build_object(
        'home', v_item->>'home_team_raw', 'away', v_item->>'away_team_raw',
        'home_score', v_item->'home_score', 'away_score', v_item->'away_score',
        'status', v_item->>'status', 'date', v_item->>'date',
        'matchday', v_item->>'matchday',
        'competition_hint', v_item->>'competition_hint'),
      'home_candidates', v_home,
      'away_candidates', v_away,
      'fixtures', v_fixtures,
      'fixture_count', v_n,
      'match', v_pick));
  end loop;

  -- ── Pass 2: what does the submission as a whole look like? ─────────────────
  -- The modal competition+stage among the items that resolved on their own. A
  -- graphic is nearly always one round of one league, and the items that were
  -- easy say which.
  select x->'match'->>'competition_id', x->'match'->>'stage'
  into v_consensus_c, v_consensus_s
  from jsonb_array_elements(v_items) x
  where x->>'confidence' in ('green', 'yellow') and x->'match' is not null
    and jsonb_typeof(x->'match') = 'object'
  group by x->'match'->>'competition_id', x->'match'->>'stage'
  order by count(*) desc, 1
  limit 1;

  -- ── Pass 3: break ties with it, and only ties ──────────────────────────────
  for v_item in select * from jsonb_array_elements(v_items) loop
    if v_item->>'confidence' = 'red'
       and (v_item->'fixture_count')::integer > 1
       and v_consensus_c is not null then
      select coalesce(jsonb_agg(x), '[]'::jsonb) into v_fixtures
      from jsonb_array_elements(v_item->'fixtures') x
      where x->>'competition_id' = v_consensus_c
        and x->>'stage' is not distinct from v_consensus_s;

      if jsonb_array_length(v_fixtures) = 1 then
        v_item := jsonb_set(v_item, '{match}', v_fixtures->0);
        v_item := jsonb_set(v_item, '{fixtures}', v_fixtures);
        v_item := jsonb_set(v_item, '{fixture_count}', '1'::jsonb);
        -- YELLOW, never green. The tie was broken by what the rest of the
        -- page looked like, which is a good reason and not a fact about this
        -- match.
        v_item := jsonb_set(v_item, '{confidence}', '"yellow"'::jsonb);
        v_item := jsonb_set(v_item, '{reasons}',
                            (v_item->'reasons') || '["batch_consensus"]'::jsonb);
      end if;
    end if;
    v_pass2 := v_pass2 || jsonb_build_array(v_item);
  end loop;

  return jsonb_build_object(
    'season_id', v_season,
    'competitions', to_jsonb(v_comps),
    'consensus', case when v_consensus_c is null then null
                      else jsonb_build_object('competition_id', v_consensus_c,
                                              'stage', v_consensus_s) end,
    'items', v_pass2);
end;
$$;

comment on function public.resolve_import_candidates(jsonb, text) is
  'Raw extracted names -> candidate fixtures, with a confidence derived from '
  'evidence rather than from the model. Scoped to the caller''s competitions; '
  'creates nothing.';

revoke execute on function public.resolve_import_candidates(jsonb, text)
  from public, anon;
grant execute on function public.resolve_import_candidates(jsonb, text)
  to authenticated;

commit;
