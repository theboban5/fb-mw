-- 0051_import_fixtures_competition.sql — saying the useful thing, and finding
-- the competition on a cup graphic.
--
-- TWO THINGS 0049 GOT WRONG, both found by asking the resolver real questions
-- rather than by reading it.
--
-- 1. IT ANSWERED THE WRONG QUESTION ABOUT AN UNKNOWN NAME. The competition was
--    tested before the teams were, and a row with one unreadable name has no
--    competition either — the pairing has no shared `entries` row to read one
--    from. So a graphic naming "CRECK SC", which the database holds as Creck
--    Sporting, came back "I could not tell which league this is". The reporter
--    can fix an unknown name with one tap (add_team_alias, 0046) and can do
--    nothing at all with an unknown league. The checks are reordered so the
--    reason a person can act on is the reason they are given.
--
-- 2. IT GAVE UP ON EVERY CUP. The competition is read from what the teams have
--    in common, and a row votes only when its two teams share exactly ONE
--    competition. Every Airtel Top 8 side is also a Super League side, so every
--    row of a Top 8 graphic shares two, abstains, and the vote comes back
--    empty — no competition proposed at all, on a graphic whose answer was
--    never in doubt to a person.
--
--    The fix keeps a running INTERSECTION beside the vote: what every row
--    agreed was possible. One survivor is the answer; several is exactly the
--    tie `competition_hint` exists to break, and now it can, still choosing
--    only between competitions `entries` put on the table. The rule that the
--    model never decides is intact — it is offered a shortlist the data wrote.
--
-- The body below is 0050's, with those two changes and the two locals they
-- need. import_kickoff, import_venue_match and resolve_and_save_import_fixtures
-- are untouched.

begin;

-- ── import_competition_by_hint ───────────────────────────────────────────────
-- The hint against a SHORTLIST, never against the whole table. It is a string
-- the model produced, and 0043's rule is that the model's own output decides
-- nothing — so it may only pick between competitions the teams have already
-- nominated, and returns null rather than guessing.
--
-- It reads competition_seasons.sponsor_name as well as competitions.name
-- because that is what a graphic actually prints: the Super League of Malawi
-- is "FDH Bank Premiership" on every poster it publishes.

create or replace function public.import_competition_by_hint(
  p_hint      text,
  p_candidates text[],
  p_season_id text
)
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select c.competition_id
  from public.competitions c
  left join public.competition_seasons cs
    on cs.competition_id = c.competition_id and cs.season_id = p_season_id
  where btrim(coalesce(p_hint, '')) <> ''
    and c.competition_id = any (p_candidates)
    and (public.import_normalize(c.name) = public.import_normalize(p_hint)
         or public.import_normalize(coalesce(cs.sponsor_name, '')) =
            public.import_normalize(p_hint))
  order by c.competition_id
  limit 1
$$;

comment on function public.import_competition_by_hint(text, text[], text) is
  'A competition_hint resolved against a shortlist the teams produced. Reads '
  'the sponsor name too, because that is what a graphic prints.';

revoke execute on function public.import_competition_by_hint(text, text[], text)
  from public, anon, authenticated;


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
  v_common     text[];
  v_by_hint    text;
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

    -- One vote per item that named exactly one competition. An item that
    -- could be in two says nothing about WHICH, so it abstains from the vote.
    if array_length(v_shared, 1) = 1 then
      v_votes := array_append(v_votes, v_shared[1]);
    end if;

    -- ...but it still narrows the field, so it joins a running intersection.
    -- THIS IS WHAT SAVES A CUP. Every Airtel Top 8 side is also a Super League
    -- side, so every row of a Top 8 graphic names two competitions, abstains,
    -- and the vote above comes back empty — no competition at all, on a
    -- graphic where the answer was never in doubt to a person. What the rows
    -- AGREE on is still exactly those two, which the hint can then choose
    -- between.
    if array_length(v_shared, 1) >= 1 then
      if v_common is null then
        v_common := v_shared;
      else
        select coalesce(array_agg(x), '{}') into v_common
        from unnest(v_common) x where x = any (v_shared);
      end if;
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
  -- In order of how much the DATA says, never the model: what most rows named
  -- on their own, then what every row agreed was possible, and the hint only
  -- ever choosing between competitions the teams had already nominated.
  if v_chosen is null then
    select v into v_chosen
    from unnest(v_votes) v
    group by v
    order by count(*) desc, v
    limit 1;

    if v_chosen is not null then
      v_source := 'teams';
      -- A split vote is a tie, and a tie is the one thing the hint may settle.
      if v_hint <> ''
         and (select count(distinct v) from unnest(v_votes) v) > 1 then
        v_by_hint := public.import_competition_by_hint(v_hint, v_votes, v_season);
        if v_by_hint is not null then
          v_chosen := v_by_hint;
          v_source := 'hint';
        end if;
      end if;
    elsif array_length(v_common, 1) = 1 then
      -- Nobody could name one alone, but every row agreed on the same one.
      v_chosen := v_common[1];
      v_source := 'teams';
    elsif array_length(v_common, 1) > 1 then
      -- The cup case. Now the hint earns its keep — still choosing only from
      -- competitions `entries` put on the table.
      v_by_hint := public.import_competition_by_hint(v_hint, v_common, v_season);
      if v_by_hint is not null then
        v_chosen := v_by_hint;
        v_source := 'hint';
      end if;
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
    -- ORDER MATTERS HERE, AND THE COMPETITION USED TO BE ASKED FIRST. A row
    -- with one unreadable name resolves to no competition either — the pairing
    -- has no shared entry to read one from — so the reporter was told "I
    -- cannot tell which league this is" when the actionable truth was "I do
    -- not know this team". They can fix the second with one tap and can do
    -- nothing whatever with the first.
    if v_pick_home is null or v_pick_away is null then
      v_state := 'blocked';
      v_reasons := array_append(v_reasons, 'team_not_found');
    elsif v_pick_home->>'team_id' = v_pick_away->>'team_id' then
      v_state := 'blocked';
      v_reasons := array_append(v_reasons, 'same_team');
    elsif v_chosen is null then
      v_state := 'blocked';
      v_reasons := array_append(v_reasons, 'no_competition');
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
