-- 0061_officials_skipped.sql — "yes, publish without match officials", and a
-- record that someone said it.
--
-- WHAT WAS WRONG. Officials have been six optional boxes on the match screen
-- since 0023, and 16 of 1,085 played matches name a referee. The reporters
-- want "which referee gave the most cards this season", and a referee table
-- over 16 matches answers nothing. Making the boxes required was the obvious
-- fix and the wrong one: a district U16 result arrives as a WhatsApp line with
-- a score in it, and a rule that refuses it until somebody finds the
-- referee's name is a rule that stops the score reaching the site at all —
-- which is the one thing this portal exists to prevent.
--
-- And the blank told nobody anything. A match with no referee might be one
-- where the reporter looked and the club never said, or one where nobody
-- looked because nothing asked. Those are different jobs — the first is
-- finished, the second is a backlog — and the database could not tell them
-- apart.
--
-- WHAT THIS DOES.
--
--   * The portal asks before publishing a played result whose four official
--     boxes are empty, and "Yes, publish without match officials" is a tap,
--     never a refusal. The question lives in the client; this migration is
--     the half that remembers the answer.
--   * `officials_skips` — one row per match whose reporter said so. Latest
--     answer wins (a re-publish without officials refreshes who and when).
--     It is NOT in the Dataset: it renders nowhere, validate.py never sees
--     it, and nothing about it can stop a build.
--   * `skip_match_officials(ids, channel)` — the only writer. It files a row
--     only for a match the caller may report AND whose officials are still
--     blank, so the table cannot claim "published without officials" about a
--     match that has them.
--   * `ops_match_officials` — one row per played match of the active season,
--     for the Officials tab: named, skipped (with who), or never asked. And
--     `ops_competition_officials`, the same rows counted per competition —
--     selected FROM the first, so a count and the list it opens cannot
--     disagree (0056's arrangement).
--
-- "Match officials" means the four of them: referee, two assistants, fourth
-- official. The coaches share the panel but are not officials, and a match
-- with a coach typed and no referee is still a match nobody refereed on the
-- record.
--
-- THE TRADE. The channel is claimed by the client ('single' | 'grid' |
-- 'import'), unlike 0047's, which is derived. It is a label on an admin
-- screen about how a reporter was asked, not a fact about a result, and
-- deriving it would mean threading a new argument through three publish
-- RPCs whose signatures every phone in the field depends on. The skip is a
-- SEPARATE call after the publish for the same reason, and because a result
-- must never wait on its own audit note: if this call fails, the score is
-- already on the site and the only loss is one line on the Officials tab.

begin;

-- ── The table ────────────────────────────────────────────────────────────────

create table if not exists public.officials_skips (
  -- Cascade, not restrict: delete_fixture (0032) must never be refused by a
  -- note about a fixture that turned out to be a duplicate.
  match_id     text primary key
               references public.matches (match_id) on delete cascade,
  reporter_id  text not null references public.reporters (reporter_id),
  channel      text not null check (channel in ('single', 'grid', 'import')),
  skipped_at   timestamptz not null default now()
);

comment on table public.officials_skips is
  'A reporter confirmed publishing this match without match officials. '
  'Admin-visible through ops_match_officials only; never in the Dataset.';

-- No policies: nobody reads or writes this table directly. The RPC below
-- writes it as definer, and the ops view reads it behind is_admin().
alter table public.officials_skips enable row level security;
revoke all on public.officials_skips from anon;


-- ── The writer ───────────────────────────────────────────────────────────────

create or replace function public.skip_match_officials(
  p_match_ids text[],
  p_channel   text
)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text := public.current_reporter_id();
  v_count    integer;
begin
  if v_reporter is null then
    raise exception 'not signed in as an active reporter' using errcode = '42501';
  end if;
  if p_channel is null or p_channel not in ('single', 'grid', 'import') then
    raise exception 'invalid channel %', p_channel using errcode = '22023';
  end if;

  -- A match the caller cannot report is skipped silently rather than raising:
  -- this runs AFTER a publish, and one stray id in a batch must not turn a
  -- matchday that published into an error on the reporter's screen.
  insert into public.officials_skips (match_id, reporter_id, channel, skipped_at)
  select m.match_id, v_reporter, p_channel, now()
  from public.matches m
  where m.match_id = any (coalesce(p_match_ids, '{}'))
    and btrim(coalesce(m.referee, '')) = ''
    and btrim(coalesce(m.assistant_referee_1, '')) = ''
    and btrim(coalesce(m.assistant_referee_2, '')) = ''
    and btrim(coalesce(m.fourth_official, '')) = ''
    and public.can_report_match(m.match_id)
  on conflict (match_id) do update
    set reporter_id = excluded.reporter_id,
        channel     = excluded.channel,
        skipped_at  = excluded.skipped_at;

  get diagnostics v_count = row_count;
  return v_count;
end
$$;

comment on function public.skip_match_officials(text[], text) is
  'Record that the caller published these matches without match officials. '
  'Ignores matches they cannot report and matches that now have officials.';

revoke execute on function public.skip_match_officials(text[], text) from public, anon;
grant execute on function public.skip_match_officials(text[], text) to authenticated;


-- ── ops_match_officials ──────────────────────────────────────────────────────
-- One row per PLAYED match of the active season (ops_match_flags' scope).
-- 'played' only: a postponed match had no referee to name, and a walkover's
-- officials are not a question anyone is asking.
--
-- officials_state is the tab's three buckets, decided here once so the
-- per-competition counts and the list below them cannot disagree:
--   named   — at least one of the four official boxes is filled
--   skipped — none, and a reporter confirmed publishing without them
--   unasked — none, and nobody was asked (everything before 0061)

create view public.ops_match_officials with (security_invoker = true) as
select
  f.match_id,
  f.public_id,
  f.competition_id,
  coalesce(nullif(cs.sponsor_name, ''), c.name)            as competition_name,
  f.season_id,
  f.competition_type,
  f.competition_tier,
  f.round_key,
  f.date,
  f.kickoff,
  f.home_name,
  f.away_name,
  f.home_goals,
  f.away_goals,
  f.is_pre_tracker,
  nullif(btrim(m.referee), '')                              as referee,
  (btrim(coalesce(m.referee, '')) <> '')                    as has_referee,
  o.has_officials,
  k.skipped_at,
  k.channel                                                 as skip_channel,
  r.name                                                    as skipped_by,
  case when o.has_officials then 'named'
       when k.match_id is not null then 'skipped'
       else 'unasked' end                                   as officials_state
from public.ops_match_flags f
join public.matches m       on m.match_id = f.match_id
join public.competitions c  on c.competition_id = f.competition_id
join public.competition_seasons cs
  on cs.competition_id = f.competition_id and cs.season_id = f.season_id
cross join lateral (
  select (btrim(coalesce(m.referee, '')) <> ''
          or btrim(coalesce(m.assistant_referee_1, '')) <> ''
          or btrim(coalesce(m.assistant_referee_2, '')) <> ''
          or btrim(coalesce(m.fourth_official, '')) <> '') as has_officials
) o
left join public.officials_skips k on k.match_id = f.match_id
left join public.reporters r       on r.reporter_id = k.reporter_id
where f.status = 'played'
  and public.is_admin();

comment on view public.ops_match_officials is
  'Admin only. One row per played active-season match: officials named, '
  'skipped by a reporter (who, when, how), or never asked. The Officials tab.';


-- ── ops_competition_officials ────────────────────────────────────────────────
-- The tab's per-competition line. Counted here rather than in the browser
-- because a season of played matches is past PostgREST's 1,000-row page, and
-- a count that silently stopped at a thousand would be the worst kind wrong.

create view public.ops_competition_officials with (security_invoker = true) as
select
  x.competition_id,
  x.competition_name,
  x.competition_tier,
  count(*)::integer                                              as played,
  count(*) filter (where x.officials_state = 'named')::integer   as named,
  count(*) filter (where x.has_referee)::integer                 as with_referee,
  count(*) filter (where x.officials_state = 'skipped')::integer as skipped,
  count(*) filter (where x.officials_state = 'unasked')::integer as unasked
from public.ops_match_officials x
where public.is_admin()
group by x.competition_id, x.competition_name, x.competition_tier;

comment on view public.ops_competition_officials is
  'Admin only. ops_match_officials counted per competition: played, named, '
  'with a referee, skipped by a reporter, never asked.';

-- anon revoked outright (0048); authenticated gets zero rows unless is_admin().
revoke all on public.ops_match_officials from anon;
revoke all on public.ops_competition_officials from anon;
grant select on public.ops_match_officials to authenticated;
grant select on public.ops_competition_officials to authenticated;

commit;
