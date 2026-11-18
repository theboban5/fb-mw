-- 0072_derby_referrals.sql — Bring your friends to the Derby: a personal link
-- per fan, and a count of who each one brought.
--
-- WHAT WAS WRONG. 0071 can grow the updates list and cannot say who grew it.
-- After Know Your Flames the next big date is Nyasa Big Bullets v Mighty
-- Wanderers (Sunday 22 November), and the campaign for it is a referral race:
-- 50,000 MWK for whoever brings the most people, two VIP derby tickets drawn
-- among everyone who brought at least three. A race needs a count per person,
-- and the obvious count is the wrong one. A UTM link or a click counter says
-- how many times a link was opened, which a fan can raise by refreshing their
-- own page, and GA4 cannot say whose link it was at all. What the campaign
-- wants is people on the list, so that is what is counted: a NEW phone number
-- that signed up through somebody's link.
--
-- WHAT THIS DOES.
--
--   * update_signups grows a ref_code (the person's own link), referred_by
--     (whose link brought them), an optional score prediction, and follows:
--     which of Facebook, the WhatsApp channel and LinkedIn they say they
--     follow EveryLeague on.
--   * referral_campaigns  a race's window. A code belongs to a PERSON, not a
--                         race, so the next campaign is one row here and
--                         every link already shared keeps working. The same
--                         times as src/derby.py; tests/test_derby.py compares.
--   * referral_draws      the ticket draw, quiz_draws' shape.
--   * apply_update_signup the rules of 0071's submit_update_signup, moved into
--                         one internal function with two callers (0041's move):
--                         submit_update_signup keeps its signature and its
--                         answers, and submit_derby_signup is the second.
--   * referral_rows       ONE definition of "a referral that counts", read by
--                         the page's count and by both admin screens, so the
--                         number a fan sees and the number the prize is
--                         decided on cannot disagree.
--
-- WHAT COUNTS. A sign-up whose referred_by is the code, made inside the
-- race's window, by a phone number that was not already on the list from a
-- quiz entry (opt_in_updates). A phone already in update_signups cannot be
-- referred at all: referred_by is only ever written on a NEW row. The page
-- says "a new person, with their own phone number"; this is that sentence.
--
-- THE TRADE, a third anon write path after 0067 and 0071, with the same
-- guards: no table grants, a honeypot, 0071's row cap, no per-address limit
-- (carrier-grade NAT). The count can still be gamed by typing other people's
-- numbers, or invented ones, and nothing here can stop that, because nothing
-- here can send an SMS. The defence is a human one and the rules say so: the
-- top referrer is not paid until some of the people they brought have been
-- phoned. admin_referral_referees exists for exactly that call. The follow is
-- the same: no platform lets a page verify one, so it is a tick the winner is
-- asked to show before they are paid.
--
-- A CODE IS NOT A SECRET. Typing a number already on the list returns that
-- row's code, because that is how someone who lost their link, or one of the
-- 228 quiz subscribers, gets one. All it reveals is what 0071's "already on
-- the list" already did; knowing someone's code lets you send people to them.

begin;

alter table public.update_signups
  add column ref_code    text unique,
  add column referred_by text references public.update_signups (ref_code),
  add column pred_home   smallint check (pred_home between 0 and 20),
  add column pred_away   smallint check (pred_away between 0 and 20),
  add column follows     text[] not null default '{}'
    check (follows <@ array['facebook', 'whatsapp', 'linkedin']);

create index update_signups_referred_by on public.update_signups (referred_by)
  where referred_by is not null;

comment on column public.update_signups.ref_code is
  'This person''s own referral code (0072): /derby/?r=<code>. Six characters '
  'from an alphabet with no I, L, O, 0 or 1. Random, never derived from the '
  'phone.';
comment on column public.update_signups.referred_by is
  'The ref_code whose link brought this person (0072). Written only on a new '
  'row, never on someone already on the list.';
comment on column public.update_signups.follows is
  'Where they said they follow EveryLeague (0072): facebook, whatsapp, '
  'linkedin. Self-declared; nothing can verify it.';

create table public.referral_campaigns (
  campaign_id text primary key,
  title       text not null,
  opens_at    timestamptz not null,
  closes_at   timestamptz not null,
  check (closes_at > opens_at)
);

comment on table public.referral_campaigns is
  'A referral race''s window (0072). Only sign-ups inside it count. The same '
  'times as src/derby.py.';

-- The same times as src/derby.py. tests/test_derby.py reads this file.
insert into public.referral_campaigns (campaign_id, title, opens_at, closes_at)
values ('derby-2026', 'Bring your friends to the Derby',
        '2026-11-19 00:00+02', '2026-11-21 12:00+02');

create table public.referral_draws (
  id            bigint generated by default as identity primary key,
  campaign_id   text not null references public.referral_campaigns (campaign_id),
  drawn_at      timestamptz not null default now(),
  drawn_by      text,
  min_referrals integer not null,
  pool          integer not null,   -- referrers eligible for this draw
  winners       bigint[] not null   -- update_signups.id, in the order drawn
);

comment on table public.referral_draws is
  'The ticket draws of a referral race (0072), quiz_draws'' shape.';

alter table public.referral_campaigns enable row level security;
alter table public.referral_draws     enable row level security;
revoke all on public.referral_campaigns, public.referral_draws
  from anon, authenticated;


-- ── referral_new_code ────────────────────────────────────────────────────────
-- 31 characters, six places: about 887 million codes, so a collision is a
-- retry, not a problem. gen_random_uuid() for the bytes, because it is the OS's
-- secure source and needs no extension (0068's reason for the draw).

create or replace function public.referral_new_code()
returns text
language plpgsql
volatile
set search_path = ''
as $$
declare
  v_alphabet constant text := 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';
  v_bytes bytea;
  v_code  text;
  i       integer;
begin
  loop
    v_bytes := uuid_send(gen_random_uuid());
    v_code := '';
    for i in 0..5 loop
      v_code := v_code || substr(v_alphabet,
                                 get_byte(v_bytes, i) % 31 + 1, 1);
    end loop;
    exit when not exists (select 1 from public.update_signups s
                          where s.ref_code = v_code);
  end loop;
  return v_code;
end;
$$;

revoke execute on function public.referral_new_code()
  from public, anon, authenticated;


-- ── apply_update_signup ──────────────────────────────────────────────────────
-- 0071's submit_update_signup, every check in the same order with the same
-- words, plus what a referral needs. Internal: granted to nobody. Returns
-- {ok, status, message, id}; the id is the row the person now is (new or
-- already there), and the callers decide what of it the browser sees.

create or replace function public.apply_update_signup(
  p_name      text,
  p_phone     text,
  p_email     text,
  p_source    text,
  p_ref       text     default null,
  p_pred_home integer  default null,
  p_pred_away integer  default null,
  p_follows   text[]   default '{}'
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_name    text;
  v_phone   text := '';
  v_email   text;
  v_ref     text;
  v_follows text[];
  v_id      bigint;
begin
  v_name := left(btrim(regexp_replace(coalesce(p_name, ''), '\s+', ' ', 'g')),
                 80);
  if length(v_name) < 2 then
    return jsonb_build_object('ok', false, 'status', 'invalid',
      'message', 'Please enter your name.');
  end if;

  if btrim(coalesce(p_phone, '')) <> '' then
    v_phone := public.quiz_normalise_phone(p_phone);
    if v_phone is null then
      return jsonb_build_object('ok', false, 'status', 'invalid',
        'message', 'That phone number does not look right, '
                   'like 0991 234 567. Fix it, or leave it empty.');
    end if;
  end if;

  v_email := lower(btrim(coalesce(p_email, '')));
  if v_email <> '' and (length(v_email) > 120
                        or v_email !~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$') then
    return jsonb_build_object('ok', false, 'status', 'invalid',
      'message', 'That email address does not look right. '
                 'Fix it, or leave it empty.');
  end if;

  if v_phone = '' and v_email = '' then
    return jsonb_build_object('ok', false, 'status', 'invalid',
      'message', 'Please give a phone number or an email address, '
                 'so we can reach you.');
  end if;

  -- Only the three known values, each once; anything else is dropped rather
  -- than refused, because a stale page sending an old value is not the
  -- person's fault.
  select coalesce(array_agg(distinct f order by f), '{}') into v_follows
  from unnest(coalesce(p_follows, '{}')) f
  where f in ('facebook', 'whatsapp', 'linkedin');

  -- The phone is who someone is; the email only when there is no phone.
  select s.id into v_id from public.update_signups s
  where v_phone <> '' and s.phone = v_phone;
  if v_id is null then
    select s.id into v_id from public.update_signups s
    where v_email <> '' and s.email = v_email;
  end if;
  if v_id is not null then
    -- Already on the list. A follow they tick now is still news; nothing
    -- else about the row changes, and above all not referred_by.
    update public.update_signups s
      set follows = (select coalesce(array_agg(distinct f order by f), '{}')
                     from unnest(s.follows || v_follows) f)
      where s.id = v_id and not (v_follows <@ s.follows);
    return jsonb_build_object('ok', true, 'status', 'already', 'id', v_id,
      'message', 'You are already on the list. We will be in touch.');
  end if;

  if (select count(*) from public.update_signups) >= 50000 then
    return jsonb_build_object('ok', false, 'status', 'full',
      'message', 'Sorry, we cannot take more sign-ups right now.');
  end if;

  -- A code nobody holds is ignored, not refused: the friend still joins the
  -- list, which is the point, and a typo in a link is not their fault.
  select s.ref_code into v_ref from public.update_signups s
  where s.ref_code = upper(btrim(coalesce(p_ref, '')));

  begin
    insert into public.update_signups
      (name, phone, email, source, referred_by, pred_home, pred_away, follows)
    values (v_name, v_phone, v_email, coalesce(nullif(p_source, ''), 'quiz'),
            v_ref,
            case when p_pred_home between 0 and 20
                  and p_pred_away between 0 and 20 then p_pred_home end,
            case when p_pred_home between 0 and 20
                  and p_pred_away between 0 and 20 then p_pred_away end,
            v_follows)
    returning id into v_id;
  exception when unique_violation then
    -- Two taps on a slow connection: the first one got in.
    select s.id into v_id from public.update_signups s
    where (v_phone <> '' and s.phone = v_phone)
       or (v_email <> '' and s.email = v_email)
    limit 1;
    return jsonb_build_object('ok', true, 'status', 'already', 'id', v_id,
      'message', 'You are already on the list. We will be in touch.');
  end;

  return jsonb_build_object('ok', true, 'status', 'signed_up', 'id', v_id);
end;
$$;

revoke execute on function public.apply_update_signup(
  text, text, text, text, text, integer, integer, text[])
  from public, anon, authenticated;


-- ── submit_update_signup (0071), now a caller ────────────────────────────────
-- Same signature, same answers: the honeypot first, then the rules, and the
-- row id never leaves the database.

create or replace function public.submit_update_signup(
  p_name    text,
  p_phone   text default '',
  p_email   text default '',
  p_website text default ''
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
begin
  if coalesce(p_website, '') <> '' then
    return jsonb_build_object('ok', true, 'status', 'signed_up');
  end if;
  return public.apply_update_signup(p_name, p_phone, p_email, 'quiz') - 'id';
end;
$$;


-- ── referral_rows ────────────────────────────────────────────────────────────
-- Every sign-up that came through a link, with whether it counts in this
-- race and, when it does not, why. The single definition.

create or replace function public.referral_rows(p_campaign text)
returns table (
  signup_id   bigint,
  referred_by text,
  created_at  timestamptz,
  counts      boolean,
  reason      text      -- '' when it counts
)
language sql
stable
security definer
set search_path = ''
as $$
  select s.id, s.referred_by, s.created_at,
         r.reason = '',
         r.reason
  from public.update_signups s
  join public.referral_campaigns c on c.campaign_id = p_campaign
  cross join lateral (select case
      when s.phone = '' then 'no phone number'
      when s.created_at < c.opens_at then 'before the race opened'
      when s.created_at >= c.closes_at then 'after the race closed'
      when exists (select 1 from public.quiz_entries q
                   where q.phone = s.phone and q.opt_in_updates)
        then 'already on the list from the quiz'
      else '' end as reason) r
  where s.referred_by is not null;
$$;

revoke execute on function public.referral_rows(text)
  from public, anon, authenticated;


-- ── referral_count ───────────────────────────────────────────────────────────
-- What the page shows a fan about their own link: how many count, and how
-- many signed up through it at all (so a link shared before the race opens
-- does not look broken). Two integers and nothing about who.

create or replace function public.referral_count(p_campaign text, p_code text)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select jsonb_build_object(
    'counted', count(*) filter (where r.counts),
    'used', count(*))
  from public.referral_rows(p_campaign) r
  where r.referred_by = upper(btrim(coalesce(p_code, '')));
$$;

revoke execute on function public.referral_count(text, text) from public;
grant execute on function public.referral_count(text, text)
  to anon, authenticated;


-- ── submit_derby_signup ──────────────────────────────────────────────────────
-- The /derby/ form. A phone number is required here (it is the identity a
-- referral stands on), and so is at least one follow. Returns {ok, status,
-- message, code, counted, used}: the person's own code, minted now if the row
-- has none, whether they are new or already on the list.

create or replace function public.submit_derby_signup(
  p_name      text,
  p_phone     text,
  p_email     text    default '',
  p_ref       text    default '',
  p_pred_home integer default null,
  p_pred_away integer default null,
  p_follows   text[]  default '{}',
  p_campaign  text    default 'derby-2026',
  p_website   text    default ''
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_out  jsonb;
  v_id   bigint;
  v_code text;
begin
  if coalesce(p_website, '') <> '' then
    return jsonb_build_object('ok', true, 'status', 'signed_up',
                              'code', '', 'counted', 0, 'used', 0);
  end if;
  if btrim(coalesce(p_phone, '')) = '' then
    return jsonb_build_object('ok', false, 'status', 'invalid',
      'message', 'Please enter your phone number, like 0991 234 567. '
                 'It is how we know each person once.');
  end if;
  if not (coalesce(p_follows, '{}')
          && array['facebook', 'whatsapp', 'linkedin']) then
    return jsonb_build_object('ok', false, 'status', 'invalid',
      'message', 'Follow EveryLeague on Facebook, WhatsApp or LinkedIn, '
                 'and tick where you follow us.');
  end if;

  v_out := public.apply_update_signup(p_name, p_phone, p_email, 'derby',
                                      p_ref, p_pred_home, p_pred_away,
                                      p_follows);
  if not (v_out ->> 'ok')::boolean then
    return v_out;
  end if;
  v_id := (v_out ->> 'id')::bigint;

  select s.ref_code into v_code from public.update_signups s where s.id = v_id;
  if v_code is null then
    -- A unique violation here is two taps minting at once; the loser reads
    -- the winner's code.
    begin
      update public.update_signups set ref_code = public.referral_new_code()
        where id = v_id and ref_code is null;
    exception when unique_violation then
      null;
    end;
    select s.ref_code into v_code from public.update_signups s where s.id = v_id;
  end if;

  return (v_out - 'id')
    || jsonb_build_object('code', v_code)
    || public.referral_count(p_campaign, v_code)
    || case when v_out ->> 'status' = 'already' then jsonb_build_object(
         'message', 'You are already on our list, so here is your link again.')
       else '{}'::jsonb end;
end;
$$;

revoke execute on function public.submit_derby_signup(
  text, text, text, text, integer, integer, text[], text, text) from public;
grant execute on function public.submit_derby_signup(
  text, text, text, text, integer, integer, text[], text, text)
  to anon, authenticated;


-- ── admin_referral_campaigns ─────────────────────────────────────────────────

create or replace function public.admin_referral_campaigns()
returns table (
  campaign_id text,
  title       text,
  opens_at    timestamptz,
  closes_at   timestamptz,
  signups     integer,   -- every derby sign-up, whenever it came
  referred    integer,   -- of those, through somebody's link
  counted     integer,   -- of those, counting in this race
  referrers   integer,   -- people with at least one that counts
  facebook    integer,   -- derby sign-ups who ticked each follow
  whatsapp    integer,
  linkedin    integer
)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise exception 'Only an administrator can see referrals.'
      using errcode = '42501';
  end if;
  return query
  select c.campaign_id, c.title, c.opens_at, c.closes_at,
         (select count(*)::int from public.update_signups s
          where s.source = 'derby'),
         (select count(*)::int from public.referral_rows(c.campaign_id)),
         (select count(*)::int from public.referral_rows(c.campaign_id) r
          where r.counts),
         (select count(distinct r.referred_by)::int
          from public.referral_rows(c.campaign_id) r where r.counts),
         (select count(*)::int from public.update_signups s
          where s.source = 'derby' and 'facebook' = any (s.follows)),
         (select count(*)::int from public.update_signups s
          where s.source = 'derby' and 'whatsapp' = any (s.follows)),
         (select count(*)::int from public.update_signups s
          where s.source = 'derby' and 'linkedin' = any (s.follows))
  from public.referral_campaigns c
  order by c.opens_at desc;
end;
$$;


-- ── admin_referrers ──────────────────────────────────────────────────────────
-- The race, ranked: everyone whose link brought anyone, most counted first.
-- `place` is shared by a tie, so two people on 12 are both 1st.

create or replace function public.admin_referrers(p_campaign text)
returns table (
  signup_id  bigint,
  name       text,
  phone      text,
  email      text,
  code       text,
  follows    text[],
  counted    integer,
  uncounted  integer,
  place      integer,   -- shared by a tie
  drawn      boolean    -- already won a ticket in this race's draw
)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise exception 'Only an administrator can see referrals.'
      using errcode = '42501';
  end if;
  return query
  with per as (
    select r.referred_by,
           (count(*) filter (where r.counts))::int as counted,
           (count(*) filter (where not r.counts))::int as uncounted
    from public.referral_rows(p_campaign) r
    group by r.referred_by
  )
  select s.id, s.name, s.phone, s.email, s.ref_code, s.follows,
         per.counted, per.uncounted,
         (rank() over (order by per.counted desc))::int,
         exists (select 1 from public.referral_draws d
                 where d.campaign_id = p_campaign and s.id = any (d.winners))
  from per
  join public.update_signups s on s.ref_code = per.referred_by
  order by per.counted desc, s.name;
end;
$$;


-- ── admin_referral_referees ──────────────────────────────────────────────────
-- Who one person brought, with numbers, for the call before paying: ring a
-- few and ask whether a friend sent them the link.

create or replace function public.admin_referral_referees(
  p_campaign text, p_code text)
returns table (
  signup_id  bigint,
  created_at timestamptz,
  name       text,
  phone      text,
  email      text,
  follows    text[],
  pred_home  smallint,
  pred_away  smallint,
  counts     boolean,
  reason     text
)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise exception 'Only an administrator can see referrals.'
      using errcode = '42501';
  end if;
  return query
  select s.id, s.created_at, s.name, s.phone, s.email, s.follows,
         s.pred_home, s.pred_away, r.counts, r.reason
  from public.referral_rows(p_campaign) r
  join public.update_signups s on s.id = r.signup_id
  where r.referred_by = upper(btrim(coalesce(p_code, '')))
  order by s.created_at;
end;
$$;


-- ── admin_draw_referral_tickets ──────────────────────────────────────────────
-- admin_draw_quiz's rules: after the close, gen_random_uuid() order, never
-- the same person twice (a redraw for an unreachable winner leaves out
-- everyone already drawn), one referral_draws row per draw. p_exclude is the
-- 50k winner, ticked on the screen: the rules give one prize per person, and
-- who won the cash is a human decision made after phoning their referrals.

create or replace function public.admin_draw_referral_tickets(
  p_campaign text,
  p_count    integer,
  p_min      integer  default 3,
  p_exclude  bigint[] default '{}'
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_campaign public.referral_campaigns;
  v_pool     integer;
  v_ids      bigint[];
begin
  if not public.is_admin() then
    raise exception 'Only an administrator can draw the tickets.'
      using errcode = '42501';
  end if;
  select * into v_campaign from public.referral_campaigns c
  where c.campaign_id = p_campaign;
  if not found then
    raise exception 'No referral campaign %.', p_campaign;
  end if;
  if now() < v_campaign.closes_at then
    raise exception 'The race is still open. Draw after it closes.';
  end if;
  if p_count is null or p_count not between 1 and 10 then
    raise exception 'Draw between 1 and 10 winners.';
  end if;
  if p_min is null or p_min < 1 then
    raise exception 'The minimum is at least one referral.';
  end if;

  with eligible as (
    select s.id
    from public.referral_rows(p_campaign) r
    join public.update_signups s on s.ref_code = r.referred_by
    where r.counts
      and s.id <> all (coalesce(p_exclude, '{}'))
      and not exists (select 1 from public.referral_draws d
                      where d.campaign_id = p_campaign and s.id = any (d.winners))
    group by s.id
    having count(*) >= p_min
  )
  select (select count(*) from eligible),
         (select coalesce(array_agg(id), '{}') from
            (select id from eligible order by gen_random_uuid()
             limit p_count) picked)
  into v_pool, v_ids;

  insert into public.referral_draws
    (campaign_id, drawn_by, min_referrals, pool, winners)
  values (p_campaign, public.current_reporter_id(), p_min, v_pool, v_ids);

  return jsonb_build_object('pool', v_pool, 'winners', to_jsonb(v_ids));
end;
$$;


-- ── admin_referral_draws ─────────────────────────────────────────────────────

create or replace function public.admin_referral_draws(p_campaign text)
returns table (
  id            bigint,
  drawn_at      timestamptz,
  drawn_by      text,
  min_referrals integer,
  pool          integer,
  winners       jsonb      -- [{id, name, phone, email, code}] in order drawn
)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise exception 'Only an administrator can see referral draws.'
      using errcode = '42501';
  end if;
  return query
  select d.id, d.drawn_at, coalesce(rp.name, d.drawn_by, ''),
         d.min_referrals, d.pool,
         coalesce((select jsonb_agg(jsonb_build_object(
                     'id', s.id, 'name', s.name, 'phone', s.phone,
                     'email', s.email, 'code', s.ref_code) order by w.ord)
                   from unnest(d.winners) with ordinality w(signup_id, ord)
                   join public.update_signups s on s.id = w.signup_id),
                  '[]'::jsonb)
  from public.referral_draws d
  left join public.reporters rp on rp.reporter_id = d.drawn_by
  where d.campaign_id = p_campaign
  order by d.drawn_at desc;
end;
$$;


-- ── admin_update_signups (0071), with the new columns ───────────────────────
-- The subscriber CSV keeps working unchanged; the return type grows, so it is
-- dropped and recreated rather than replaced.

drop function public.admin_update_signups();

create function public.admin_update_signups()
returns table (
  id          bigint,
  created_at  timestamptz,
  name        text,
  phone       text,
  email       text,
  source      text,
  ref_code    text,
  referred_by text,
  follows     text[]
)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise exception 'Only an administrator can see the sign-up list.'
      using errcode = '42501';
  end if;
  return query
  select s.id, s.created_at, s.name, s.phone, s.email, s.source,
         s.ref_code, s.referred_by, s.follows
  from public.update_signups s
  order by s.created_at;
end;
$$;

revoke execute on function public.admin_update_signups() from public, anon;
revoke execute on function public.admin_referral_campaigns() from public, anon;
revoke execute on function public.admin_referrers(text) from public, anon;
revoke execute on function public.admin_referral_referees(text, text)
  from public, anon;
revoke execute on function public.admin_draw_referral_tickets(
  text, integer, integer, bigint[]) from public, anon;
revoke execute on function public.admin_referral_draws(text) from public, anon;
grant execute on function public.admin_update_signups() to authenticated;
grant execute on function public.admin_referral_campaigns() to authenticated;
grant execute on function public.admin_referrers(text) to authenticated;
grant execute on function public.admin_referral_referees(text, text)
  to authenticated;
grant execute on function public.admin_draw_referral_tickets(
  text, integer, integer, bigint[]) to authenticated;
grant execute on function public.admin_referral_draws(text) to authenticated;

commit;
