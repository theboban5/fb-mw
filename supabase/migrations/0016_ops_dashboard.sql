-- 0016_ops_dashboard.sql — the administrator's Coverage & Data Backlog views.
--
-- This migration adds NO new football data and changes no existing table. It is
-- a read-only lens over what is already there: which rounds are incomplete,
-- which results are overdue, which played matches are missing scorers, venues
-- or sources. Fixing any of it still happens through /report and the existing
-- RPCs, so there remains exactly one write path into public.matches.
--
-- WHY VIEWS AND NOT RPCs, WHICH IS THE HOUSE STYLE ELSEWHERE.
-- Every other server-side object here is a narrow function, because every other
-- one WRITES. These only read, and the dashboard's central feature is that a
-- count is clickable: tapping "22 overdue" must open exactly those 22 rows.
-- A view lets PostgREST answer both the aggregate and the drill-down from one
-- definition with an ordinary filter; a function returning JSON would need a
-- second, hand-kept copy of every predicate, and the two would drift.
--
-- WHY THE ADMIN CHECK IS INSIDE EACH VIEW BODY.
-- `security_invoker = true` makes the base tables' RLS apply as the caller, and
-- that alone is not enough here: matches, goals, entries and clubs all carry a
-- public read policy from 0001, so RLS would happily show this to a reporter.
-- The `where public.is_admin()` in each body is therefore the real gate, and it
-- is repeated per view rather than inherited so that each object is safe on its
-- own if a later migration grants one of them somewhere new.
--
-- Worth being honest about what that gate protects. The counts are computed
-- from anon-readable tables, so this is an administrator's TOOL rather than
-- secret data — the one genuinely restricted input is rebuild_state, which is
-- why that alone goes through a SECURITY DEFINER function at the bottom.

begin;

-- ── The clock ────────────────────────────────────────────────────────────────
-- Every "is it late?" question in this file is asked against the Malawi
-- calendar day, which is what matches.date already means and what the site is
-- rendered in (build.py, TZ_OFFSET_HOURS).

create or replace function public.ops_today()
returns date
language sql
stable
set search_path = ''
as $$
  -- CAT, UTC+2, no DST. A fixed offset rather than a named zone, so this can
  -- never drift with a tzdata update and always matches build.py exactly.
  select ((now() at time zone 'UTC') + interval '2 hours')::date
$$;

comment on function public.ops_today() is
  'Today in Malawi (CAT, UTC+2). The only clock the ops views use.';


-- ── Cup round ordering ───────────────────────────────────────────────────────
-- Mirrors adapt.STAGE_ORDER exactly, including that the third-place play-off
-- sorts BEFORE the final. Alphabetical ordering would put the final first,
-- which is how "next round" gets the wrong answer on a cup.

create or replace function public.ops_stage_order(p_stage text)
returns integer
language sql
immutable
set search_path = ''
as $$
  select case p_stage
           when 'r64' then 0 when 'r32' then 1 when 'r16' then 2
           when 'qf'  then 3 when 'sf'  then 4 when '3p'  then 5
           when 'final' then 6
         end
$$;

comment on function public.ops_stage_order(text) is
  'adapt.STAGE_ORDER in SQL. NULL for anything outside the knockout vocabulary.';


-- ── ops_competition_settings ─────────────────────────────────────────────────
-- Deliberately tiny. It holds ONLY what cannot be derived:
--
--   * which competitions are cups, where a round has no derivable size;
--   * the pre-tracker cutoff, so historical import rows can be folded away;
--   * an override for a genuine exception (nothing needs one today).
--
-- The expected size of a round is NOT stored. A matchday is a logical round,
-- not a weekend: a fixture postponed out of its weekend keeps its matchday and
-- changes only its date, so in the final tally every matchday of a league holds
-- exactly floor(active entries / 2). That makes the expectation computable, and
-- turns a round that does not match it into a data defect to report rather than
-- a number somebody has to maintain. See ops_matchday_status.

create table public.ops_competition_settings (
  competition_id       text not null,
  season_id            text not null,
  -- '' is the competition-wide default row. A non-empty value overrides one
  -- round only, keyed exactly as ops_match_flags.round_key ('7', 'qf').
  stage                text not null default '',
  matchday_mode        text not null default 'round'
                       check (matchday_mode in ('round', 'cup')),
  -- NULL means derive. Set this only when a competition genuinely does not
  -- play floor(n/2) per round.
  matches_per_matchday integer check (matches_per_matchday > 0),
  matchdays_total      integer check (matchdays_total > 0),
  -- Matches created before this are "pre-tracker" — the historical import —
  -- and are hidden by default in the backlog lists, never dropped from the
  -- totals. NULL falls back to the coarser source_type test; see
  -- ops_match_flags.is_pre_tracker.
  backlog_from         date,
  note                 text not null default '',
  updated_by           text references public.reporters (reporter_id),
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  primary key (competition_id, season_id, stage),
  foreign key (competition_id, season_id)
    references public.competition_seasons (competition_id, season_id)
    on delete cascade
);

create trigger ops_competition_settings_set_updated_at
  before update on public.ops_competition_settings
  for each row execute function public.set_updated_at();

comment on table public.ops_competition_settings is
  'Per competition-season dashboard settings. Holds only what cannot be '
  'derived; round sizes are computed, not stored.';

alter table public.ops_competition_settings enable row level security;

-- Administrators only, for everything. No anon policy at all, and an ordinary
-- reporter gets nothing rather than a filtered subset.
create policy ops_settings_admin_read
  on public.ops_competition_settings for select to authenticated
  using (public.is_admin());
create policy ops_settings_admin_insert
  on public.ops_competition_settings for insert to authenticated
  with check (public.is_admin());
create policy ops_settings_admin_update
  on public.ops_competition_settings for update to authenticated
  using (public.is_admin()) with check (public.is_admin());
create policy ops_settings_admin_delete
  on public.ops_competition_settings for delete to authenticated
  using (public.is_admin());

revoke all on public.ops_competition_settings from anon;
grant select, insert, update, delete
  on public.ops_competition_settings to authenticated;


-- ── ops_match_flags ──────────────────────────────────────────────────────────
-- The base view: one row per match of the active season in an active
-- competition-season, with a boolean per backlog category. Every count in the
-- dashboard is a `count(*) filter (where <flag>)` over this, and every
-- clickable count is the same view filtered by that flag — which is what keeps
-- the number and the list it opens from ever disagreeing.

create view public.ops_match_flags with (security_invoker = true) as
select
  m.match_id,
  m.public_id,
  m.competition_id,
  m.season_id,
  c.type                         as competition_type,
  c.tier                         as competition_tier,
  m.stage,
  m.matchday,

  -- The round a match belongs to, as one text key across both shapes: the
  -- matchday number on a league, the knockout stage on a cup. NULL means the
  -- match has not been assigned to a round at all (2 such fixtures today) —
  -- they are bucketed as unassigned, never silently dropped.
  case when c.type = 'cup' then nullif(m.stage, '')
       else nullif(m.matchday::text, '') end          as round_key,
  case when c.type = 'cup' then coalesce(public.ops_stage_order(m.stage), 99)
       else coalesce(m.matchday, 9999) end            as round_sort,

  m.date,
  m.kickoff,
  m.status,
  m.home_team_id,
  m.away_team_id,
  ht.display_name                as home_name,
  at.display_name                as away_name,
  m.home_goals,
  m.away_goals,
  m.venue_id,
  m.source_type,
  m.source_ref,
  m.confidence,
  m.reported_by,
  m.reported_at,
  m.verified_by,
  m.verified_at,
  m.created_at,
  m.updated_at,

  g.goals_recorded,
  -- Goals the score implies. NULL for anything without a score, so the
  -- comparison below is simply not true rather than accidentally zero.
  (m.home_goals + m.away_goals)  as goals_expected,

  -- ── The backlog flags ──────────────────────────────────────────────────────

  -- Overdue: a result was due and has not arrived. Same-day fixtures are never
  -- overdue whatever the kickoff — kickoffs slip, and 57% of played matches
  -- carry no kickoff at all, so a time-of-day grace period would be guesswork
  -- over a minority of rows.
  (m.status = 'scheduled' and m.date is not null
   and m.date < public.ops_today())                   as is_overdue,

  -- Nothing can be late without a due date. An undated fixture is a scheduling
  -- gap, which is a different job for a different person.
  (m.status = 'scheduled' and m.date is null)         as is_unscheduled,

  -- Postponed is a decision already taken, awaiting a new date. Counting it as
  -- overdue would mean its round could never be marked complete.
  (m.status = 'postponed')                            as awaiting_reschedule,

  -- Missing scorers, and the single most important predicate in this file.
  -- It is `recorded < implied by the score`, NOT `no goal rows`: 44 matches
  -- this season finished 0-0, and every one of them is COMPLETE. Defining this
  -- as an absence of rows would report all 44 as gaps.
  --
  -- 'awarded' is excluded on purpose — a walkover has a score and no goals.
  -- 'abandoned' likewise: goals before the abandonment are rarely recorded.
  (m.status = 'played'
   and g.goals_recorded < (m.home_goals + m.away_goals))
                                                      as missing_scorers,
  (m.status = 'played' and g.goals_recorded > 0
   and g.goals_recorded < (m.home_goals + m.away_goals))
                                                      as scorers_partial,

  -- A cancelled match needs no ground. Future fixtures are included: a venue is
  -- most useful before the match, and set_match_venue (0015) exists to fill it.
  (m.venue_id is null and m.status <> 'cancelled')    as missing_venue,

  -- source_ref is `not null default ''`, so this is emptiness, never NULL.
  (m.status in ('played', 'awarded') and m.source_ref = '')
                                                      as missing_source,
  (m.status in ('played', 'awarded')
   and m.source_type in ('unknown', 'placeholder'))   as weak_source_type,

  -- Reads 0 today and that is correct, not a fault: the bulk import wrote
  -- every row 'confirmed'. It fills as reporters publish, since
  -- submit_match_report sets 'unconfirmed' for a reporter and 'confirmed' for
  -- an admin.
  (m.status in ('played', 'awarded') and m.confidence = 'unconfirmed')
                                                      as is_unconfirmed,

  -- A league row whose stage and matchday disagree. One such row today
  -- (MW_SRFA_2627_086: stage md_12, matchday 11). Harmless to the site, which
  -- reads matchday, but it means one of the two is wrong.
  (c.type <> 'cup' and m.stage like 'md=_%' escape '='
   and m.matchday is not null
   and m.stage <> 'md_' || m.matchday::text)          as stage_matchday_mismatch,

  -- Pre-tracker: the historical import, which dominates the venue and source
  -- backlogs. Hidden by default in the lists and ALWAYS counted in the totals —
  -- these rows still need filling in eventually, so they are folded away, never
  -- dropped.
  --
  -- backlog_from is the real test and the seed at the foot of this file sets
  -- it. The source_type fallback exists only for a competition-season with no
  -- settings row, and is deliberately coarser: every match still missing a
  -- source_ref today has a non-reporter source_type, so the fallback would hide
  -- the entire sources backlog rather than the historical part of it.
  (case when st.backlog_from is not null
        then m.created_at < st.backlog_from
        else m.source_type <> 'reporter' end)         as is_pre_tracker

from public.matches m
join public.competitions c
  on c.competition_id = m.competition_id
join public.seasons s
  on s.season_id = m.season_id and s.status = 'active'
join public.competition_seasons cs
  on cs.competition_id = m.competition_id
 and cs.season_id = m.season_id
 and cs.status = 'active'
join public.teams ht on ht.team_id = m.home_team_id
join public.teams at on at.team_id = m.away_team_id
left join public.ops_competition_settings st
  on st.competition_id = m.competition_id
 and st.season_id = m.season_id
 and st.stage = ''
left join lateral (
  select count(*)::integer as goals_recorded
  from public.goals gl
  where gl.match_id = m.match_id
) g on true
where public.is_admin();

comment on view public.ops_match_flags is
  'One row per active-season match with a boolean per backlog category. Every '
  'dashboard count aggregates this; every clickable count filters it.';


-- ── ops_competition_expectation ──────────────────────────────────────────────
-- How many fixtures one round of a competition should hold.

create view public.ops_competition_expectation with (security_invoker = true) as
select
  cs.competition_id,
  cs.season_id,
  c.type as competition_type,
  count(e.entry_id) filter (where e.status = 'active')::integer as active_entries,
  -- floor(n/2), which integer division already gives. An odd entry count means
  -- one team is idle each round, so 15 teams expect 7 — not 7.5.
  --
  -- A cup gets NULL: the size of a round depends on the bracket and on whether
  -- ties are two-legged, and legs are inferred from mirrored fixtures with no
  -- `leg` column to read. A confident wrong number is worse than none.
  case when c.type = 'cup' then null
       else (count(e.entry_id) filter (where e.status = 'active') / 2)::integer
  end as derived_per_round,
  st.matches_per_matchday as override_per_round,
  coalesce(st.matches_per_matchday,
           case when c.type = 'cup' then null
                else (count(e.entry_id) filter (where e.status = 'active') / 2)::integer
           end) as expected_per_round,
  st.matchdays_total,
  st.backlog_from
from public.competition_seasons cs
join public.competitions c on c.competition_id = cs.competition_id
join public.seasons s on s.season_id = cs.season_id and s.status = 'active'
left join public.entries e
  on e.competition_id = cs.competition_id and e.season_id = cs.season_id
left join public.ops_competition_settings st
  on st.competition_id = cs.competition_id
 and st.season_id = cs.season_id
 and st.stage = ''
where cs.status = 'active'
  and public.is_admin()
group by cs.competition_id, cs.season_id, c.type,
         st.matches_per_matchday, st.matchdays_total, st.backlog_from;

comment on view public.ops_competition_expectation is
  'Expected fixtures per round: an override if set, otherwise floor(active '
  'entries / 2). NULL for cups.';


-- ── ops_matchday_status ──────────────────────────────────────────────────────
-- One row per round. Carries "which past matchdays are incomplete" and "how
-- many fixtures are entered against how many expected", including the surplus
-- and deficit that say which fixtures have been filed under the wrong round.

create view public.ops_matchday_status with (security_invoker = true) as
with rounds as (
  select
    f.competition_id,
    f.season_id,
    f.competition_type,
    f.round_key,
    min(f.round_sort)                                             as round_sort,
    count(*)::integer                                             as entered,
    count(*) filter (where f.status in ('played', 'awarded', 'cancelled'))::integer
                                                                  as resolved,
    count(*) filter (where f.status in ('scheduled', 'postponed'))::integer
                                                                  as outstanding,
    count(*) filter (where f.status = 'scheduled')::integer        as awaiting_result,
    count(*) filter (where f.status = 'played')::integer           as played,
    count(*) filter (where f.date is null)::integer                as undated,
    min(f.date)                                                    as first_date,
    max(f.date)                                                    as last_date
  from public.ops_match_flags f
  group by f.competition_id, f.season_id, f.competition_type, f.round_key
)
select
  r.competition_id,
  r.season_id,
  r.competition_type,
  r.round_key,
  r.round_sort,
  -- What to print. A NULL round_key is a real fixture with no round, not an
  -- empty group, so it gets a label rather than being filtered away.
  case when r.round_key is null then 'unassigned'
       when r.competition_type = 'cup' then r.round_key
       else 'md' || r.round_key end                                as round_label,
  r.entered,
  r.resolved,
  r.outstanding,
  r.awaiting_result,
  r.played,
  r.undated,
  r.first_date,
  r.last_date,
  -- NULL for the unassigned bucket: it is a collection of fixtures with no
  -- round, not a round that is short of them, and printing "2 of 7" against it
  -- invites someone to go looking for five missing matches.
  case when r.round_key is null then null
       else x.expected_per_round end                                as expected,
  -- Positive = more fixtures than a round can hold, so something is filed
  -- here that belongs elsewhere. Negative = the round is short.
  case when x.expected_per_round is null or r.round_key is null then null
       else r.entered - x.expected_per_round end                   as size_delta,

  -- Past: it has at least one dated fixture and the last of them has been and
  -- gone. A round of entirely undated fixtures is never past — it is
  -- unscheduled, which is a different problem.
  (r.last_date is not null and r.last_date < public.ops_today())   as is_past,
  -- Incomplete: past, and still waiting on a result. Postponed does not count
  -- here; it has its own bucket, and including it would mean a round with a
  -- postponement could never clear.
  (r.last_date is not null and r.last_date < public.ops_today()
   and r.awaiting_result > 0)                                      as is_incomplete
from rounds r
left join public.ops_competition_expectation x
  on x.competition_id = r.competition_id and x.season_id = r.season_id
where public.is_admin();

comment on view public.ops_matchday_status is
  'One row per round: entered vs expected, surplus/deficit, and whether a past '
  'round is still waiting on results.';


-- ── ops_competition_summary ──────────────────────────────────────────────────
-- The dashboard's main table: one row per active competition-season.

create view public.ops_competition_summary with (security_invoker = true) as
with
-- The round about to be played, which is NOT the earliest round still
-- unfinished. MW_SRFA has an outstanding fixture in md7 from mid-August while
-- md13 is this week's round; md7 is backlog (incomplete_rounds below) and md13
-- is next. Conflating the two makes this column useless.
next_round as (
  select distinct on (f.competition_id, f.season_id)
    f.competition_id,
    f.season_id,
    f.round_key                                    as next_round_key,
    f.round_sort                                   as next_round_sort,
    f.date                                         as next_date
  from public.ops_match_flags f
  where f.status in ('scheduled', 'postponed')
    and (f.date is null or f.date >= public.ops_today())
  order by f.competition_id, f.season_id,
           (f.date is null),   -- a dated fixture wins over an undated one
           f.date,
           f.round_sort
),
rounds as (
  select
    competition_id,
    season_id,
    count(*) filter (where is_incomplete)::integer                as incomplete_rounds,
    coalesce(sum(awaiting_result) filter (where is_incomplete), 0)::integer
                                                                  as incomplete_fixtures,
    count(*) filter (where size_delta > 0)::integer                as rounds_with_surplus,
    count(*) filter (where size_delta < 0)::integer                as rounds_with_deficit,
    coalesce(sum(entered) filter (where round_key is null), 0)::integer
                                                                  as unassigned_fixtures
  from public.ops_matchday_status
  group by competition_id, season_id
),
backlog as (
  select
    competition_id,
    season_id,
    count(*)::integer                                              as matches_total,
    count(*) filter (where is_overdue)::integer                    as overdue,
    count(*) filter (where is_unscheduled)::integer                as unscheduled,
    count(*) filter (where awaiting_reschedule)::integer           as awaiting_reschedule,
    count(*) filter (where missing_scorers)::integer               as missing_scorers,
    count(*) filter (where missing_scorers and not is_pre_tracker)::integer
                                                                   as missing_scorers_current,
    count(*) filter (where missing_venue)::integer                 as missing_venue,
    count(*) filter (where missing_venue and not is_pre_tracker)::integer
                                                                   as missing_venue_current,
    count(*) filter (where missing_source)::integer                as missing_source,
    count(*) filter (where missing_source and not is_pre_tracker)::integer
                                                                   as missing_source_current,
    count(*) filter (where is_unconfirmed)::integer                as unconfirmed,
    count(*) filter (where stage_matchday_mismatch)::integer       as stage_mismatches
  from public.ops_match_flags
  group by competition_id, season_id
)
select
  x.competition_id,
  x.season_id,
  -- The display name exactly as the public site resolves it: the season's
  -- sponsor name when there is one, else the competition's own name.
  coalesce(nullif(cs.sponsor_name, ''), c.name)                    as competition_name,
  c.type                                                            as competition_type,
  c.tier                                                            as competition_tier,
  x.active_entries,
  x.expected_per_round,
  x.override_per_round is not null                                  as expectation_is_override,

  n.next_round_key,
  -- n.competition_id is NULL only when the CTE found no upcoming fixture at
  -- all. Testing next_date instead would swallow the 'unassigned' case, where
  -- a competition's only upcoming fixtures are undated AND carry no matchday —
  -- exactly MW_NRFA today, which then rendered a blank cell.
  case when n.competition_id is null then null
       when n.next_round_key is null then 'unassigned'
       when c.type = 'cup' then n.next_round_key
       else 'md' || n.next_round_key end                            as next_round_label,
  n.next_date,
  -- 'dated'  — a fixture is scheduled for today or later
  -- 'undated'— fixtures exist for the next round but carry no date
  -- 'none'   — nothing upcoming has been entered at all, which is a finding
  --            rather than a blank cell
  case when n.competition_id is null then 'none'
       when n.next_date is not null  then 'dated'
       else 'undated' end                                           as next_round_state,
  coalesce(ms.entered, 0)                                           as next_round_entered,
  ms.expected                                                       as next_round_expected,

  coalesce(r.incomplete_rounds, 0)                                  as incomplete_rounds,
  coalesce(r.incomplete_fixtures, 0)                                as incomplete_fixtures,
  coalesce(r.rounds_with_surplus, 0)                                as rounds_with_surplus,
  coalesce(r.rounds_with_deficit, 0)                                as rounds_with_deficit,
  coalesce(r.unassigned_fixtures, 0)                                as unassigned_fixtures,

  -- Does the fixture count divide cleanly into whole rounds? This is what
  -- separates the two defects. MW_SRFA holds 96 fixtures against 8 per round —
  -- exactly twelve rounds — so nothing is missing and the anomalies are purely
  -- mislabelling. MW_SRFA2 holds 26 against 7, leaving 5 spare, so fixtures
  -- were genuinely never entered.
  case when x.expected_per_round is null or x.expected_per_round = 0 then null
       else coalesce(b.matches_total, 0) % x.expected_per_round end as fixtures_spare,
  case when x.expected_per_round is null or x.expected_per_round = 0 then null
       else coalesce(b.matches_total, 0) / x.expected_per_round end as whole_rounds,

  coalesce(b.matches_total, 0)                                      as matches_total,
  coalesce(b.overdue, 0)                                            as overdue,
  coalesce(b.unscheduled, 0)                                        as unscheduled,
  coalesce(b.awaiting_reschedule, 0)                                as awaiting_reschedule,
  coalesce(b.missing_scorers, 0)                                    as missing_scorers,
  coalesce(b.missing_scorers_current, 0)                            as missing_scorers_current,
  coalesce(b.missing_venue, 0)                                      as missing_venue,
  coalesce(b.missing_venue_current, 0)                              as missing_venue_current,
  coalesce(b.missing_source, 0)                                     as missing_source,
  coalesce(b.missing_source_current, 0)                             as missing_source_current,
  coalesce(b.unconfirmed, 0)                                        as unconfirmed,
  coalesce(b.stage_mismatches, 0)                                   as stage_mismatches
from public.ops_competition_expectation x
join public.competitions c on c.competition_id = x.competition_id
join public.competition_seasons cs
  on cs.competition_id = x.competition_id and cs.season_id = x.season_id
left join next_round n
  on n.competition_id = x.competition_id and n.season_id = x.season_id
left join public.ops_matchday_status ms
  on ms.competition_id = x.competition_id and ms.season_id = x.season_id
 and ms.round_key is not distinct from n.next_round_key
left join rounds r
  on r.competition_id = x.competition_id and r.season_id = x.season_id
left join backlog b
  on b.competition_id = x.competition_id and b.season_id = x.season_id
where public.is_admin();

comment on view public.ops_competition_summary is
  'One row per active competition-season: next round, round anomalies and every '
  'backlog count. The dashboard''s main table.';


-- ── ops_dashboard_totals ─────────────────────────────────────────────────────
-- One row, for the urgent summary strip.

create view public.ops_dashboard_totals with (security_invoker = true) as
select
  count(*)::integer                                       as competitions,
  coalesce(sum(overdue), 0)::integer                      as overdue,
  coalesce(sum(unscheduled), 0)::integer                  as unscheduled,
  coalesce(sum(awaiting_reschedule), 0)::integer          as awaiting_reschedule,
  coalesce(sum(incomplete_rounds), 0)::integer            as incomplete_rounds,
  coalesce(sum(incomplete_fixtures), 0)::integer          as incomplete_fixtures,
  coalesce(sum(missing_scorers), 0)::integer              as missing_scorers,
  coalesce(sum(missing_scorers_current), 0)::integer      as missing_scorers_current,
  coalesce(sum(missing_venue), 0)::integer                as missing_venue,
  coalesce(sum(missing_venue_current), 0)::integer        as missing_venue_current,
  coalesce(sum(missing_source), 0)::integer               as missing_source,
  coalesce(sum(missing_source_current), 0)::integer       as missing_source_current,
  coalesce(sum(unconfirmed), 0)::integer                  as unconfirmed,
  coalesce(sum(rounds_with_surplus), 0)::integer          as rounds_with_surplus,
  coalesce(sum(rounds_with_deficit), 0)::integer          as rounds_with_deficit,
  coalesce(sum(unassigned_fixtures), 0)::integer          as unassigned_fixtures,
  coalesce(sum(stage_mismatches), 0)::integer             as stage_mismatches,
  count(*) filter (where next_round_state = 'none')::integer
                                                          as competitions_without_fixtures
from public.ops_competition_summary
-- HAVING, not WHERE. This is a bare aggregate, so it returns exactly one row
-- however few rows it reads — and a non-admin, whose ops_competition_summary is
-- empty, would otherwise be handed a tidy row of zeros instead of nothing at
-- all. No information leaks either way, but a caller cannot tell a refusal from
-- a genuinely quiet day, and the dashboard would render as if it had loaded.
having public.is_admin();

comment on view public.ops_dashboard_totals is
  'A single row of grand totals for the dashboard''s urgent strip.';


-- ── Grants ───────────────────────────────────────────────────────────────────
-- anon is revoked from every view, so an unauthenticated request fails at the
-- privilege check and never reaches the is_admin() predicate. A signed-in
-- reporter passes the privilege check and gets zero rows.

revoke all on public.ops_match_flags              from anon;
revoke all on public.ops_competition_expectation  from anon;
revoke all on public.ops_matchday_status          from anon;
revoke all on public.ops_competition_summary      from anon;
revoke all on public.ops_dashboard_totals         from anon;

grant select on public.ops_match_flags              to authenticated;
grant select on public.ops_competition_expectation  to authenticated;
grant select on public.ops_matchday_status          to authenticated;
grant select on public.ops_competition_summary      to authenticated;
grant select on public.ops_dashboard_totals         to authenticated;

revoke execute on function public.ops_today()             from public, anon;
revoke execute on function public.ops_stage_order(text)   from public, anon;
grant execute on function public.ops_today()              to authenticated;
grant execute on function public.ops_stage_order(text)    to authenticated;


-- ── ops_rebuild_status ───────────────────────────────────────────────────────
-- "Is the site up to date?" — the one genuinely non-public input on this
-- dashboard. rebuild_state has RLS on with no policy and is revoked from anon
-- and authenticated (0004), so it is unreadable from any browser session,
-- administrator included. Rather than loosen that table, this SECURITY DEFINER
-- function reads it on an admin's behalf and returns nothing else.
--
-- p_since is the build's data_read_at, which the client takes from
-- /build-info.json. Passing it in rather than storing it keeps CI out of the
-- database: build.py publishes a file, and nothing new needs a credential.

create or replace function public.ops_rebuild_status(p_since timestamptz default null)
returns table (
  pending             boolean,
  last_dispatched_at  timestamptz,
  dispatch_count      bigint,
  state_updated_at    timestamptz,
  newest_match_update timestamptz,
  matches_since       integer
)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  if not public.is_admin() then
    raise exception 'administrators only' using errcode = '42501';
  end if;

  return query
  select
    rs.pending,
    rs.last_dispatched_at,
    rs.dispatch_count,
    rs.updated_at,
    (select max(m.updated_at) from public.matches m),
    (select count(*)::integer from public.matches m
      where p_since is not null and m.updated_at > p_since)
  from public.rebuild_state rs
  where rs.id;
end;
$$;

comment on function public.ops_rebuild_status(timestamptz) is
  'Admin-only reader for rebuild_state plus match freshness. The only reason '
  'rebuild_state itself stays unreadable by every browser role.';

revoke execute on function public.ops_rebuild_status(timestamptz)
  from public, anon;
grant execute on function public.ops_rebuild_status(timestamptz)
  to authenticated;


-- ── Seed ─────────────────────────────────────────────────────────────────────
-- One default row per active competition-season. matchday_mode comes from the
-- competition's own type, so a cup is never given a derived round size.
-- matches_per_matchday stays NULL throughout: nothing currently needs an
-- override, and floor(active entries / 2) is right for all ten leagues.
-- backlog_from IS seeded, because the cutoff is knowable rather than a matter
-- of taste: the historical import landed as one burst, 556 of 596 matches
-- sharing a single created_at day, with everything after it entered through
-- /report. The day after that burst is therefore exactly the line between
-- "backlog we inherited" and "work done since", which is what the lists hide
-- by default. Derived rather than hard-coded so this is also right when
-- applied to a fresh project.

insert into public.ops_competition_settings
  (competition_id, season_id, stage, matchday_mode, backlog_from, note)
select cs.competition_id, cs.season_id, '',
       case when c.type = 'cup' then 'cup' else 'round' end,
       (select ((min(m.created_at) at time zone 'UTC') + interval '2 hours')::date
               + 1
        from public.matches m),
       'seeded by 0016'
from public.competition_seasons cs
join public.competitions c on c.competition_id = cs.competition_id
join public.seasons s on s.season_id = cs.season_id and s.status = 'active'
where cs.status = 'active'
on conflict do nothing;

commit;
