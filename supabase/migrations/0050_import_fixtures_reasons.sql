-- 0050_import_fixtures_reasons.sql — 0044's bug, made again.
--
-- WHAT WAS WRONG. resolve_import_fixtures (0049) appended to its `reasons`
-- list with
--
--     v_reasons := v_reasons || 'no_competition';
--
-- which is ambiguous: `||` carries both `anyarray || anyelement` and
-- `anyarray || anyarray`, and against an untyped literal PostgreSQL picks the
-- second, tries to parse `no_competition` as an array literal, and raises
-- 22P02. One raise aborts the whole function, so a single fixture the resolver
-- had anything to SAY about took the rest of the graphic down with it.
--
-- 0044 fixed exactly this, in exactly these words, six migrations ago.
--
-- HOW IT CAME BACK, because that is the useful part. 0044 repaired 0043 the
-- way this file repairs 0049 — with `create or replace` in a NEW migration,
-- which is the house rule and is right. The side effect is that
-- 0043_import_matching.sql still contains the broken construct: it is the
-- record of what was asked for in August, not the definition that is running.
-- 0049 was written by reading 0043 as though it were the current source, and
-- copied the one line 0044 exists to remove.
--
-- THE LESSON IS ABOUT THE DIRECTORY, NOT THE OPERATOR. supabase/migrations is
-- an append-only log. Any function in it may have been superseded by a later
-- `create or replace`, and the only way to know what a function does today is
-- to grep the whole directory for its name and read the LAST one. A file is
-- not a source of truth about the server; it is a record of one request to it.
--
-- WHAT THIS DOES. Replaces resolve_import_fixtures with array_append, which
-- has one meaning. Nothing else changes — the body below is 0049's, verbatim,
-- with seventeen appends rewritten. import_kickoff, import_venue_match and
-- resolve_and_save_import_fixtures append nothing and are untouched.

begin;

create or replace function public.resolve_import_fixtures(
  p_items          jsonb,
  p_competition_id text default null,
  p_season_id      text default null
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_reporter   text;
  v_season     text;
  v_comps      text[];
  v_scope      text[];
  v_item       jsonb;
  v_i          integer := 0;
  v_first      jsonb := '[]'::jsonb;
  v_items      jsonb := '[]'::jsonb;
  v_home       jsonb;
  v_away       jsonb;
  v_home_ids   text[];
  v_away_ids   text[];
  v_shared     text[];
  v_votes      text[] := '{}';
  v_hint       text := '';
  v_chosen     text;
  v_source     text;
  v_best_rank  integer;
  v_reasons    text[];
  v_conf       text;
  v_state      text;
  v_date       date;
  v_md         integer;
  v_kick       jsonb;
  v_venue      text;
  v_venue_name text;
  v_pick_home  jsonb;
  v_pick_away  jsonb;
  v_existing   jsonb;
  v_differs    text[];
  v_reversed   boolean;
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
    raise exception 'send a list of fixtures' using errcode = '22023';
  end if;
  -- The same ceiling create_fixtures enforces, said before a reporter has
  -- waited for a list that would be refused.
  if jsonb_array_length(p_items) > 60 then
    raise exception 'that is more than 60 fixtures in one import'
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

  -- THE AUTHORIZED SET, resolved once — 0043's rule, and the reason the review
  -- screen cannot show a row create_fixtures would refuse.
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
      'kind', 'fixtures', 'season_id', v_season,
      'competitions', '[]'::jsonb, 'competition_id', null,
      'competition_source', null, 'items', '[]'::jsonb,
      'note', 'no competitions assigned');
  end if;

  -- A competition the reporter has already chosen narrows everything below;
  -- one they may not report is refused rather than quietly widened.
  if p_competition_id is not null and p_competition_id <> '' then
    if not (p_competition_id = any (v_comps)) then
      raise exception 'not assigned to this competition' using errcode = '42501';
    end if;
    v_scope := array[p_competition_id];
    v_chosen := p_competition_id;
    v_source := 'given';
  else
    v_scope := v_comps;
  end if;

  -- ── Pass 1: names, and which competitions could hold this pairing ──────────
  for v_item in select * from jsonb_array_elements(p_items) loop
    v_i := v_i + 1;

    select coalesce(jsonb_agg(jsonb_build_object(
             'team_id', k.team_id, 'name', k.team_name,
             'rank', k.rank, 'method', k.method) order by k.rank), '[]'::jsonb)
    into v_home
    from public.import_team_candidates(
           v_item->>'home_team_raw', v_scope, v_season) k;

    select coalesce(jsonb_agg(jsonb_build_object(
             'team_id', k.team_id, 'name', k.team_name,
             'rank', k.rank, 'method', k.method) order by k.rank), '[]'::jsonb)
    into v_away
    from public.import_team_candidates(
           v_item->>'away_team_raw', v_scope, v_season) k;

    select coalesce(array_agg(x->>'team_id'), '{}') into v_home_ids
    from jsonb_array_elements(v_home) x;
    select coalesce(array_agg(x->>'team_id'), '{}') into v_away_ids
    from jsonb_array_elements(v_away) x;

    -- Every competition in scope that has BOTH of these teams entered this
    -- season. That is the evidence the competition is read from.
    select coalesce(array_agg(distinct e1.competition_id), '{}')
    into v_shared
    from public.entries e1
    join public.entries e2
      on e2.competition_id = e1.competition_id and e2.season_id = e1.season_id
    where e1.season_id = v_season
      and e1.competition_id = any (v_scope)
      and e1.team_id = any (v_home_ids)
      and e2.team_id = any (v_away_ids);

    -- One vote per item that named exactly one competition. An item that could
    -- be in two says nothing about which, so it abstains rather than being
    -- counted twice.
    if array_length(v_shared, 1) = 1 then
      v_votes := array_append(v_votes, v_shared[1]);
    end if;

    if v_hint = '' then
      v_hint := btrim(coalesce(v_item->>'competition_hint', ''));
    end if;

    v_first := v_first || jsonb_build_array(jsonb_build_object(
      'idx', v_i, 'item', v_item,
      'home_candidates', v_home, 'away_candidates', v_away,
      'shared', to_jsonb(v_shared)));
  end loop;

  -- ── Pass 2: which competition is this? ────────────────────────────────────
  if v_chosen is null then
    select v into v_chosen
    from unnest(v_votes) v
    group by v
    order by count(*) desc, v
    limit 1;
    if v_chosen is not null then
      v_source := 'teams';
    end if;

    -- The hint breaks a tie and nothing else. Applied only when the teams left
    -- more than one competition standing and the hint names one of them —
    -- never to overrule what `entries` says.
    if v_hint <> '' and v_chosen is not null
       and (select count(distinct v) from unnest(v_votes) v) > 1 then
      declare v_by_hint text;
      begin
        select c.competition_id into v_by_hint
        from public.competitions c
        left join public.competition_seasons cs
          on cs.competition_id = c.competition_id and cs.season_id = v_season
        where c.competition_id = any (v_votes)
          and (public.import_normalize(c.name) = public.import_normalize(v_hint)
               or public.import_normalize(coalesce(cs.sponsor_name, '')) =
                  public.import_normalize(v_hint))
        limit 1;
        if v_by_hint is not null then
          v_chosen := v_by_hint;
          v_source := 'hint';
        end if;
      end;
    end if;
  end if;

  -- ── Pass 3: everything that depends on knowing the competition ────────────
  for v_item in select * from jsonb_array_elements(v_first) loop
    v_reasons := '{}';
    v_state   := 'new';
    v_existing := null;
    v_differs := '{}';
    v_reversed := false;

    v_home := v_item->'home_candidates';
    v_away := v_item->'away_candidates';

    -- Narrowed to the chosen competition: a team that answers to the name but
    -- plays in another league is not this fixture's team, and leaving it in
    -- the list would offer the reporter a row create_fixtures refuses.
    if v_chosen is not null then
      select coalesce(jsonb_agg(x order by (x->>'rank')::integer), '[]'::jsonb)
      into v_home
      from jsonb_array_elements(v_home) x
      where exists (select 1 from public.entries e
                    where e.competition_id = v_chosen and e.season_id = v_season
                      and e.team_id = x->>'team_id');
      select coalesce(jsonb_agg(x order by (x->>'rank')::integer), '[]'::jsonb)
      into v_away
      from jsonb_array_elements(v_away) x
      where exists (select 1 from public.entries e
                    where e.competition_id = v_chosen and e.season_id = v_season
                      and e.team_id = x->>'team_id');
    end if;

    v_pick_home := case when jsonb_array_length(v_home) > 0 then v_home->0 end;
    v_pick_away := case when jsonb_array_length(v_away) > 0 then v_away->0 end;

    v_date := public.import_safe_date(v_item->'item'->>'date');
    v_md   := public.import_safe_int(v_item->'item'->>'matchday');
    v_kick := public.import_kickoff(v_item->'item'->>'kickoff');

    v_venue := public.import_venue_match(v_item->'item'->>'venue_raw');
    v_venue_name := null;
    if v_venue is not null then
      select v.name into v_venue_name
      from public.venues v where v.venue_id = v_venue;
    end if;

    -- ── The state, which is what the screen is really asking ────────────────
    if v_chosen is null then
      v_state := 'blocked';
      v_reasons := array_append(v_reasons, 'no_competition');
    elsif v_pick_home is null or v_pick_away is null then
      v_state := 'blocked';
      v_reasons := array_append(v_reasons, 'team_not_found');
    elsif v_pick_home->>'team_id' = v_pick_away->>'team_id' then
      v_state := 'blocked';
      v_reasons := array_append(v_reasons, 'same_team');
    else
      -- IS IT ALREADY THERE? Asked here rather than left to insert_fixture's
      -- duplicate guard, because "already in the list for 2026-09-06" arriving
      -- as a per-row failure AFTER publishing reads as an error, and it is not
      -- one — it is the graphic agreeing with the database.
      select coalesce(jsonb_agg(jsonb_build_object(
               'match_id', m.match_id, 'public_id', m.public_id,
               'date', m.date, 'kickoff', m.kickoff, 'status', m.status,
               'venue_id', m.venue_id, 'venue_name', vn.name,
               'matchday', m.matchday, 'stage', m.stage)
             order by m.date), '[]'::jsonb)
      into v_existing
      from public.matches m
      left join public.venues vn on vn.venue_id = m.venue_id
      where m.competition_id = v_chosen
        and m.season_id = v_season
        and m.home_team_id = v_pick_home->>'team_id'
        and m.away_team_id = v_pick_away->>'team_id';

      -- The same two teams listed the other way round on the same day is
      -- almost always the graphic putting the winner first, not a second
      -- fixture — and publishing it would create a duplicate the duplicate
      -- guard cannot see, because it checks the orientation as given.
      if v_date is not null then
        select exists (
          select 1 from public.matches m
          where m.competition_id = v_chosen and m.season_id = v_season
            and m.home_team_id = v_pick_away->>'team_id'
            and m.away_team_id = v_pick_home->>'team_id'
            and m.date = v_date)
        into v_reversed;
      end if;

      if jsonb_array_length(v_existing) = 0 then
        v_state := 'new';
        v_existing := null;
        if v_reversed then
          v_state := 'blocked';
          v_reasons := array_append(v_reasons, 'reversed_existing');
        end if;
      elsif jsonb_array_length(v_existing) > 1 then
        -- Two legs, or a replay. Which one the graphic means is a person's
        -- call, and adding a third is never the answer.
        v_state := 'several';
        v_reasons := array_append(v_reasons, 'several_existing');
      else
        v_existing := v_existing->0;
        if v_date is not null and (v_existing->>'date') is distinct from v_date::text
        then v_differs := array_append(v_differs, 'date'); end if;
        if (v_kick->>'kickoff') is not null
           and coalesce(v_existing->>'kickoff', '') is distinct from (v_kick->>'kickoff')
        then v_differs := array_append(v_differs, 'kickoff'); end if;
        if v_venue is not null
           and coalesce(v_existing->>'venue_id', '') is distinct from v_venue
        then v_differs := array_append(v_differs, 'venue'); end if;

        if array_length(v_differs, 1) is null then
          v_state := 'existing_agrees';
          v_reasons := array_append(v_reasons, 'already_listed');
        else
          v_state := 'existing_differs';
          v_reasons := array_append(v_reasons, 'differs_from_listed');
        end if;
      end if;
    end if;

    -- ── Confidence: how well was this READ, which is a separate question ────
    -- state says what to do about the row; confidence says how much of the
    -- reading to trust. A perfectly-read fixture that is already in the list
    -- is green and does nothing.
    v_best_rank := greatest(
      coalesce((v_pick_home->>'rank')::integer, 99),
      coalesce((v_pick_away->>'rank')::integer, 99));

    if v_state in ('blocked', 'several') then
      v_conf := 'red';
    else
      v_conf := 'green';
      if v_best_rank >= 5 then
        v_conf := 'yellow';
        v_reasons := array_append(v_reasons, 'name_guessed');
      end if;
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
      if v_date is null then
        v_conf := 'yellow';
        v_reasons := array_append(v_reasons, 'no_date');
      end if;
      if (v_kick->>'guessed')::boolean then
        v_conf := 'yellow';
        v_reasons := array_append(v_reasons, 'kickoff_guessed');
      end if;
      -- A ground that was printed and could not be matched. Not an error: the
      -- fixture publishes with no venue, exactly as one typed without a ground
      -- does, and the reporter is offered the tap that creates it.
      if v_venue is null
         and btrim(coalesce(v_item->'item'->>'venue_raw', '')) <> '' then
        v_conf := 'yellow';
        v_reasons := array_append(v_reasons, 'venue_unknown');
      end if;
      if v_state = 'existing_differs' then
        v_conf := 'yellow';
      end if;
    end if;

    v_items := v_items || jsonb_build_array(jsonb_build_object(
      'idx', v_item->'idx',
      'confidence', v_conf,
      'state', v_state,
      'reasons', to_jsonb(v_reasons),
      'raw', jsonb_build_object(
        'home', v_item->'item'->>'home_team_raw',
        'away', v_item->'item'->>'away_team_raw',
        'date', v_item->'item'->>'date',
        'kickoff', v_item->'item'->>'kickoff',
        'matchday', v_item->'item'->>'matchday',
        'venue', v_item->'item'->>'venue_raw',
        'competition_hint', v_item->'item'->>'competition_hint'),
      'home_candidates', v_home,
      'away_candidates', v_away,
      'home', v_pick_home,
      'away', v_pick_away,
      'date', v_date,
      'kickoff', v_kick->'kickoff',
      'kickoff_guessed', v_kick->'guessed',
      'matchday', v_md,
      'venue_id', v_venue,
      'venue_name', v_venue_name,
      'existing', v_existing,
      'differs', to_jsonb(v_differs)));
  end loop;

  return jsonb_build_object(
    'kind', 'fixtures',
    'season_id', v_season,
    'competitions', to_jsonb(v_comps),
    'competition_id', v_chosen,
    'competition_source', v_source,
    'items', v_items);
end;
$$;

comment on function public.resolve_import_fixtures(jsonb, text, text) is
  'Extracted fixture rows -> teams, dates, kick-offs, grounds, and whether '
  'each fixture is already in the list. Scoped to the caller''s competitions; '
  'creates nothing.';

revoke execute on function public.resolve_import_fixtures(jsonb, text, text)
  from public, anon;
grant execute on function public.resolve_import_fixtures(jsonb, text, text)
  to authenticated;

commit;
