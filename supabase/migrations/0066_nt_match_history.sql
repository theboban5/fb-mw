-- 0066_nt_match_history.sql — the columns a national team's history needs.
--
-- WHAT WAS WRONG. nt_matches was built for the tournament being played: a
-- fixture, then a result. RSSSF's list of the Flames' internationals
-- (scripts/rsssf_nt.py, 710 matches since 1957) says five things about a match
-- that the table had nowhere to put, and each of them changes what a page may
-- claim:
--
--   * 35 matches are marked "unsure of status / not a full international" —
--     B teams, club sides, the pre-independence friendlies. Shown, marked, and
--     left out of every record total (the decision for /flames/). Without a
--     column they are indistinguishable from a World Cup qualifier.
--   * three were abandoned (Lesotho 1976 at 53', Zimbabwe 1988 at 107', Kenya
--     2000 at 87'). The status check allowed scheduled | played | awarded, and
--     filing them as played would count them.
--   * the round (a COSAFA semi-final, an AFCON round of 16), the half-time
--     score, the crowd and the shootout score. Only "won/lost on pens" fitted,
--     in extra_time_result.
--   * one match, a 1957 friendly against Northern Rhodesia, is known only to
--     the year. date is '' or 'tbd' or YYYY-MM-DD, and '' renders "Date TBC".
--   * which country an opponent IS. `opponent` is the name of the day
--     ("Swaziland", "Zaire"), and a head-to-head record has to put Swaziland
--     and Eswatini in one line.
--
-- WHAT THIS DOES. Nine nullable or defaulted columns and two widened checks.
-- Nothing is backfilled: every existing row keeps its meaning, an unofficial
-- flag of false, no stage, and source_type 'unknown' — the league table's
-- default for a row nobody recorded the provenance of.
--
--   * source_type is matches.source_type's vocabulary exactly. RSSSF rows are
--     'backfill', and that is what keeps them off /matches/ (as an archived
--     league season is) while the 2025 rows a reporter typed stay on it.
--   * stage uses adapt.STAGE_ORDER's codes plus r1/r2 — RSSSF's "Rd 1" is a
--     group stage in one tournament and a first knockout round in another,
--     so it says "first round" and no more.
--   * date_year stands in for the date only when the date is blank.
--
-- The trade: an abandoned match carries the score at the abandonment, which
-- the played_has_score check now requires, because RSSSF records one for all
-- three and a scoreline is what a reader wants to see beside "abandoned".

alter table public.nt_matches
  add column if not exists unofficial boolean not null default false,
  add column if not exists stage text not null default ''
    check (stage in ('', 'r1', 'r2', 'r64', 'r32', 'r16', 'qf', 'sf', '3p', 'final')),
  add column if not exists half_time text not null default ''
    check (half_time = '' or half_time ~ '^[0-9]+-[0-9]+$'),
  add column if not exists crowd integer check (crowd >= 0),
  add column if not exists shootout_score text not null default ''
    check (shootout_score = '' or shootout_score ~ '^[0-9]+-[0-9]+$'),
  add column if not exists abandoned_minute integer
    check (abandoned_minute between 1 and 150),
  add column if not exists date_year integer
    check (date_year between 1900 and 2100),
  add column if not exists opponent_country text not null default '',
  add column if not exists source_type text not null default 'unknown'
    check (source_type in ('reporter', 'rfa', 'fa', 'club', 'facebook',
                           'newspaper', 'whatsapp', 'backfill', 'placeholder',
                           'unknown'));

alter table public.nt_matches drop constraint if exists nt_matches_status_check;
alter table public.nt_matches add constraint nt_matches_status_check
  check (status in ('scheduled', 'played', 'awarded', 'abandoned'));

alter table public.nt_matches drop constraint if exists nt_matches_played_has_score;
alter table public.nt_matches add constraint nt_matches_played_has_score
  check (status not in ('played', 'awarded', 'abandoned') or team_score is not null);

alter table public.nt_matches add constraint nt_matches_abandoned_minute_only_if_abandoned
  check (abandoned_minute is null or status = 'abandoned');
alter table public.nt_matches add constraint nt_matches_date_year_only_without_date
  check (date_year is null or date = '');

comment on column public.nt_matches.unofficial is
  'Not a full international (RSSSF''s *). Shown, marked, left out of every record total.';
comment on column public.nt_matches.stage is
  'Round within the competition: r1/r2 (first/second round), r64..r16, qf, sf, 3p, final; blank when not a knockout or not known.';
comment on column public.nt_matches.shootout_score is
  'Our penalties first, e.g. 4-5. extra_time_result still says win/loss.';
comment on column public.nt_matches.abandoned_minute is
  'Minute of an abandonment (status abandoned). The score is the score at the time.';
comment on column public.nt_matches.date_year is
  'The year, for a match whose day and month are unknown (date is blank). One row: 1957.';
comment on column public.nt_matches.opponent_country is
  'The country the opponent is, across renames (Swaziland -> Eswatini), for head-to-head records. Blank: same as opponent.';
comment on column public.nt_matches.source_type is
  'Where the row came from, in matches.source_type''s vocabulary. backfill rows stay off /matches/.';
