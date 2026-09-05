-- 0044_import_reasons_fix.sql — the resolver could not say WHY.
--
-- WHAT WAS WRONG. 0043 builds a `reasons` list per item — 'sides_swapped',
-- 'name_guessed', 'already_has_result' — and appended to it with
--
--     v_reasons := v_reasons || 'no_fixture';
--
-- which is ambiguous and resolves the wrong way. `||` carries both
-- `anyarray || anyelement` and `anyarray || anyarray`, and against an untyped
-- literal PostgreSQL picks the second: it tried to parse `no_fixture` as an
-- array literal and raised 22P02, "malformed array literal".
--
-- The shape of the failure is worth recording, because it is the kind that
-- survives a careless test pass. Every item that resolved cleanly appended NO
-- reason and worked perfectly; the error fired only on the items that had
-- something to say — an unknown name, a swapped pairing, two candidate
-- fixtures. So the happy path was green and every interesting path was a 400,
-- and since one raise aborts the whole function, a single unrecognised team on
-- a graphic took the other seven results down with it. That is precisely the
-- failure mode 0043's own import_safe_date exists to prevent, reintroduced two
-- functions later by an operator overload.
--
-- WHAT THIS DOES. Replaces resolve_import_candidates with array_append, which
-- has one meaning. Nothing else changes — the body below is 0043's, verbatim,
-- with twelve appends rewritten.
--
-- WHY A NEW MIGRATION RATHER THAN AN EDIT TO 0043. 0043 is applied. A
-- migration that has run is a record of what the database was asked to do, and
-- editing one makes the directory disagree with the server for everyone who
-- has already pushed it.

begin;

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
        v_reasons := array_append(v_reasons, 'narrowed_by_date');
      else
        v_reasons := array_append(v_reasons, 'date_not_found');
      end if;
    end if;

    if v_md is not null and jsonb_array_length(v_fixtures) > 1 then
      if exists (select 1 from jsonb_array_elements(v_fixtures) x
                 where (x->>'matchday_matches')::boolean) then
        select coalesce(jsonb_agg(x), '[]'::jsonb) into v_fixtures
        from jsonb_array_elements(v_fixtures) x
        where (x->>'matchday_matches')::boolean;
        v_reasons := array_append(v_reasons, 'narrowed_by_matchday');
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
      v_reasons := array_append(v_reasons, 'team_not_found');
    elsif v_n = 0 then
      v_conf := 'red';
      v_reasons := array_append(v_reasons, 'no_fixture');
    elsif v_n > 1 then
      -- Left for pass 3, which may be able to break the tie.
      v_conf := 'red';
      v_reasons := array_append(v_reasons, 'several_fixtures');
    else
      v_conf := 'green';
      -- ...and then everything that takes the certainty back off it.
      if v_best_rank >= 5 then
        v_conf := 'yellow';
        v_reasons := array_append(v_reasons, 'name_guessed');
      end if;
      -- TWO TEAMS ANSWER TO THIS NAME. The senior/reserve case, and the whole
      -- reason a close call is never resolved silently: both are in the same
      -- competition, both are real, and only a person knows which was meant.
      if jsonb_array_length(v_home) > 1
         and (v_home->1->>'rank')::integer = (v_home->0->>'rank')::integer then
        v_conf := 'yellow';
        v_reasons := array_append(v_reasons, 'home_name_ambiguous');
      end if;
      if jsonb_array_length(v_away) > 1
         and (v_away->1->>'rank')::integer = (v_away->0->>'rank')::integer then
        v_conf := 'yellow';
        v_reasons := array_append(v_reasons, 'away_name_ambiguous');
      end if;
      if v_pick->>'orientation' = 'flipped' then
        v_conf := 'yellow';
        v_reasons := array_append(v_reasons, 'sides_swapped');
      end if;
      -- Already has a result. Not the importer's call to overwrite — the grid
      -- asks, exactly as it does for a typed correction.
      if (v_pick->>'status') is distinct from 'scheduled' then
        v_conf := 'yellow';
        v_reasons := array_append(v_reasons, 'already_has_result');
      end if;
      if v_date is not null and not (v_pick->>'date_matches')::boolean then
        v_conf := 'yellow';
        v_reasons := array_append(v_reasons, 'date_disagrees');
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
commit;
