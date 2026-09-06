-- 0049_import_fixtures.sql — reading a fixture list off a picture.
--
-- 0043 turns raw names into RESULTS: it finds the fixture the graphic is about
-- and the competition falls out of the match it found. A fixture list has no
-- match to find, so almost every question is different — which competition,
-- what kick-off, which ground, and above all WHETHER THIS FIXTURE IS ALREADY
-- THERE. Everything else it inherits: the model reads, the database resolves,
-- confidence comes from evidence, the scope is the authorization, and nothing
-- here creates anything.
--
-- WHAT THE DATA SAYS THIS FEATURE IS FOR. The obvious use — a MATCH DAY poster
-- from the Super League — is almost never new information: the top leagues'
-- fixture lists are entered, and the poster confirms a date, a kick-off and a
-- ground that are already in the row. The competitions that need this are the
-- district and youth leagues, where the fixture list mostly does not exist at
-- all (MW_MDU14: sixteen teams, zero fixtures; MW_MGDU20: ten teams, zero).
--
-- So the resolver answers BOTH questions, and the difference between them is a
-- state on the row rather than a failure: `new` is added, `existing_agrees` is
-- a tick and publishes nothing, `existing_differs` names what disagrees and
-- offers the correction. An importer that only added would do nothing at all
-- on half the graphics anybody actually sends.
--
-- WHAT IS NOT HERE. Any write path. Publishing is create_fixtures (0014);
-- correcting is reschedule_match (0009) and set_match_venue (0015). All three
-- already exist, are already granted to authenticated, and already check the
-- caller's assignment — an imported fixture goes in through exactly the door a
-- typed one does. No team is minted, no club, and NO VENUE: resolve_venue
-- creates a ground it does not recognise, which is right when a reporter typed
-- it and wrong when a model read it off a photograph, so this file matches
-- against the venues that exist and stops there.

begin;

-- ── import_kickoff ───────────────────────────────────────────────────────────
-- "2:30 PM" is what the graphic prints and '^[0-9]{1,2}:[0-9]{2}$' is what
-- insert_fixture accepts. Nothing in between existed.
--
-- Returns {"kickoff": "14:30", "guessed": true|false} rather than a bare text,
-- because the guess has to travel with the value. A BARE "2:30" on a Malawian
-- results graphic is a half past two in the afternoon — every league in the
-- country kicks off between one and four — but that is an inference about
-- football and not something the picture said, and this system's rule is that
-- an inference is shown to a person rather than quietly applied. So it is
-- made, and flagged, and the row says "read '2:30' as 14:30".
--
-- Follows import_safe_date's discipline otherwise: an unreadable value costs
-- its own field and never the import, because a blank kick-off is legal
-- everywhere and a raise here would throw away the other seven fixtures.
--
-- THE BARE FORM REQUIRES A COLON, and that is not fussiness. "06.09.2026" in a
-- kickoff field — a date the model filed one column over — matches
-- ([0-9]{1,2})[.]([0-9]{2}) as "06.09" and would become a confident 18:09. A
-- dot is only accepted next to an explicit am/pm, where it can only be a time.

create or replace function public.import_kickoff(p_text text)
returns jsonb
language plpgsql
immutable
set search_path = ''
as $$
declare
  v_raw     text;
  v_parts   text[];
  v_hour    integer;
  v_minute  integer;
  v_guessed boolean := false;
begin
  v_raw := upper(btrim(coalesce(p_text, '')));
  if v_raw = '' then
    return jsonb_build_object('kickoff', null, 'guessed', false);
  end if;

  -- With a meridiem: "2:30 PM", "2.30pm", "02:30 P.M."
  v_parts := regexp_match(v_raw, '([0-9]{1,2})[:.]([0-9]{2})\s*([AP])\.?M\.?');
  if v_parts is null then
    -- Bare, colon only. See the note above about dotted dates.
    v_parts := regexp_match(v_raw, '([0-9]{1,2}):([0-9]{2})');
    if v_parts is null then
      return jsonb_build_object('kickoff', null, 'guessed', false);
    end if;
    v_parts := array[v_parts[1], v_parts[2], null];
  end if;

  v_hour   := v_parts[1]::integer;
  v_minute := v_parts[2]::integer;
  if v_minute > 59 then
    return jsonb_build_object('kickoff', null, 'guessed', false);
  end if;

  if v_parts[3] = 'P' then
    if v_hour < 12 then v_hour := v_hour + 12; end if;
  elsif v_parts[3] = 'A' then
    if v_hour = 12 then v_hour := 0; end if;
  elsif v_hour < 9 then
    -- The inference, and the flag that goes with it.
    v_hour := v_hour + 12;
    v_guessed := true;
  end if;

  if v_hour > 23 then
    return jsonb_build_object('kickoff', null, 'guessed', false);
  end if;

  return jsonb_build_object(
    'kickoff', lpad(v_hour::text, 2, '0') || ':' || lpad(v_minute::text, 2, '0'),
    'guessed', v_guessed);
exception
  when others then
    return jsonb_build_object('kickoff', null, 'guessed', false);
end;
$$;

comment on function public.import_kickoff(text) is
  'A printed kick-off to HH:MM, with a flag when the 24-hour reading was '
  'inferred rather than stated. Unreadable costs the field, never the import.';

revoke execute on function public.import_kickoff(text) from public, anon;
grant execute on function public.import_kickoff(text) to authenticated;


-- ── import_venue_match ───────────────────────────────────────────────────────
-- resolve_venue (0014) with the insert taken out.
--
-- That function mints a venue it does not recognise, and its header argues the
-- case: a venue_id is a label on a place rather than an identity, a duplicate
-- is an afternoon's tidying, and offering only the 77 grounds already in the
-- table would mean the one on the paper cannot be entered at all. Every word
-- of that is about A REPORTER TYPING IT. A model reading "MPIRA STADIUN" off a
-- compressed screenshot is a different actor with a different failure mode:
-- nobody typed it, nobody would notice, and the venues table grows a twin of a
-- ground that was already there.
--
-- So this matches and returns null. The client shows the raw name and offers
-- the reporter the tap that creates it, which puts the decision back with the
-- person resolve_venue's reasoning is actually about.

create or replace function public.import_venue_match(p_name text)
returns text
language sql
stable
security definer
set search_path = ''
as $$
  with wanted as (
    select btrim(regexp_replace(coalesce(p_name, ''), '\s+', ' ', 'g')) as name
  ),
  key as (
    select case
      -- The same list resolve_venue treats as "no ground yet", for the same
      -- reason: a venue called "TBA" would put "To be announced" on the site
      -- as though it were a place.
      when upper(regexp_replace(w.name, '[^A-Za-z]', '', 'g')) in
           ('TBA', 'TBC', 'TOBEANNOUNCED', 'TOBECONFIRMED', 'VENUETBA',
            'VENUETBC', 'NOTANNOUNCED', 'UNKNOWN', 'NA') then ''
      else upper(regexp_replace(public.unaccent_fallback(w.name),
                                '[^A-Za-z0-9]', '', 'g'))
    end as k
    from wanted w
  )
  select v.venue_id
  from public.venues v, key
  where key.k <> ''
    and upper(regexp_replace(public.unaccent_fallback(v.name),
                             '[^A-Za-z0-9]', '', 'g')) = key.k
  order by v.ord
  limit 1
$$;

comment on function public.import_venue_match(text) is
  'Ground name -> an EXISTING venue_id, or null. resolve_venue without the '
  'insert: nothing is created from a model reading.';

revoke execute on function public.import_venue_match(text) from public, anon;
grant execute on function public.import_venue_match(text) to authenticated;


-- ── resolve_import_fixtures ──────────────────────────────────────────────────
-- p_items is the extraction, one object per fixture the model read — the same
-- objects 0043 takes, because it is the same extraction:
--
--   [{"idx": 1, "home_team_raw": "EKHAYA FC", "away_team_raw": "KARONGA UNITED",
--     "date": "2026-09-06", "kickoff": "2:30 PM", "venue_raw": "MPIRA STADIUM",
--     "matchday": null, "competition_hint": "FDH BANK PREMIERSHIP"}]
--
-- WHICH COMPETITION IS DECIDED BY THE TEAMS, NOT BY THE HINT. Ekhaya FC and
-- Karonga United are both entered in exactly one competition this season, and
-- that fact is in `entries` — it is evidence. `competition_hint` is a string
-- the model produced, and 0043's whole argument is that the model's own output
-- never decides anything; here it breaks a tie between competitions the teams
-- could both be in, and does nothing else. The reporter confirms either way,
-- because a fixture filed into the wrong competition is not repairable by
-- editing one row.
--
-- THE STATE IS THE POINT. `new` is a fixture to add; `existing_agrees` is the
-- graphic confirming what is already stored, which is the commonest case in
-- the top leagues and is not a failure; `existing_differs` names the fields
-- that disagree and what each currently holds, so the reporter can correct the
-- row instead of being told their graphic is a duplicate.

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
      v_votes := v_votes || v_shared[1];
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
      v_reasons := v_reasons || 'no_competition';
    elsif v_pick_home is null or v_pick_away is null then
      v_state := 'blocked';
      v_reasons := v_reasons || 'team_not_found';
    elsif v_pick_home->>'team_id' = v_pick_away->>'team_id' then
      v_state := 'blocked';
      v_reasons := v_reasons || 'same_team';
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
          v_reasons := v_reasons || 'reversed_existing';
        end if;
      elsif jsonb_array_length(v_existing) > 1 then
        -- Two legs, or a replay. Which one the graphic means is a person's
        -- call, and adding a third is never the answer.
        v_state := 'several';
        v_reasons := v_reasons || 'several_existing';
      else
        v_existing := v_existing->0;
        if v_date is not null and (v_existing->>'date') is distinct from v_date::text
        then v_differs := v_differs || 'date'; end if;
        if (v_kick->>'kickoff') is not null
           and coalesce(v_existing->>'kickoff', '') is distinct from (v_kick->>'kickoff')
        then v_differs := v_differs || 'kickoff'; end if;
        if v_venue is not null
           and coalesce(v_existing->>'venue_id', '') is distinct from v_venue
        then v_differs := v_differs || 'venue'; end if;

        if array_length(v_differs, 1) is null then
          v_state := 'existing_agrees';
          v_reasons := v_reasons || 'already_listed';
        else
          v_state := 'existing_differs';
          v_reasons := v_reasons || 'differs_from_listed';
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
        v_reasons := v_reasons || 'name_guessed';
      end if;
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
      if v_date is null then
        v_conf := 'yellow';
        v_reasons := v_reasons || 'no_date';
      end if;
      if (v_kick->>'guessed')::boolean then
        v_conf := 'yellow';
        v_reasons := v_reasons || 'kickoff_guessed';
      end if;
      -- A ground that was printed and could not be matched. Not an error: the
      -- fixture publishes with no venue, exactly as one typed without a ground
      -- does, and the reporter is offered the tap that creates it.
      if v_venue is null
         and btrim(coalesce(v_item->'item'->>'venue_raw', '')) <> '' then
        v_conf := 'yellow';
        v_reasons := v_reasons || 'venue_unknown';
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


-- ── resolve_and_save_import_fixtures ─────────────────────────────────────────
-- 0045's function for the other kind of document, and for its reason: if the
-- client resolved and then saved, the two could differ, and
-- report_imports.resolved is supposed to be evidence of what the reporter was
-- actually shown. Doing both here makes the stored payload the returned one by
-- construction.
--
-- p_competition_id is the reporter changing their mind on the review screen —
-- re-resolving with the competition pinned, which narrows every name to that
-- league's teams. Cheap, and it means the screen never has to re-derive a
-- match itself.

create or replace function public.resolve_and_save_import_fixtures(
  p_import_id      uuid,
  p_competition_id text default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_row      public.report_imports;
  v_items    jsonb;
  v_resolved jsonb;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;

  select r.* into v_row
  from public.report_imports r
  where r.import_id = p_import_id
  for update;
  if not found then
    raise exception 'that import no longer exists' using errcode = 'P0002';
  end if;

  if v_row.reporter_id is distinct from v_reporter and not public.is_admin() then
    raise exception 'that import belongs to another reporter'
      using errcode = '42501';
  end if;

  if v_row.extracted is null then
    raise exception 'that import has not been read yet' using errcode = '22023';
  end if;

  v_items := coalesce(v_row.extracted->'results', '[]'::jsonb);
  if jsonb_typeof(v_items) <> 'array' then
    v_items := '[]'::jsonb;
  end if;

  v_resolved := public.resolve_import_fixtures(v_items, p_competition_id, null);

  update public.report_imports
  set resolved    = v_resolved,
      reviewed_at = coalesce(reviewed_at, now())
  where import_id = p_import_id;

  return v_resolved;
end;
$$;

comment on function public.resolve_and_save_import_fixtures(uuid, text) is
  'Match an extracted FIXTURE list against the caller''s competitions, store '
  'the proposal, and return it. Publishes nothing.';

revoke execute on function public.resolve_and_save_import_fixtures(uuid, text)
  from public, anon;
grant execute on function public.resolve_and_save_import_fixtures(uuid, text)
  to authenticated;

commit;
