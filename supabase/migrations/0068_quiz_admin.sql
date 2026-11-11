-- 0068_quiz_admin.sql — the quiz entries and the draw, in #/ops?tab=quiz.
--
-- WHAT WAS WRONG. 0067 put the entries where only the secret key could read
-- them, and the draw in scripts/quiz.py, on a trusted laptop. That is the
-- right boundary and the wrong place for the two people running the campaign:
-- the draw for Round 1 happens on a Friday evening before kick-off in Cairo,
-- and "find the laptop with .env on it" is not a step anyone should have to
-- take at 18:05 on a match day.
--
-- WHAT THIS DOES. Four admin-only functions, every one gated by is_admin() in
-- its own body (0016's arrangement), and the answer key moved INTO the
-- database so it can mark an entry itself:
--
--   * quizzes.answer_key      {question_id: winning value}, the same values
--                             as src/quiz.py (tests/test_quiz.py compares).
--   * quizzes.score_question  the question answered by a match result, and
--     quizzes.score_match_id  the nt_matches row it is read from. Round 2's
--                             "score in Cairo" is the only one. Its answer
--                             does not exist until the match has been
--                             played, so the key is incomplete until then
--                             and nobody can be drawn.
--   * admin_quiz_rounds()     per round: window, entries, all-correct, opted in.
--   * admin_quiz_entries(q)   every entry in a round, marked.
--   * admin_draw_quiz(q, n)   n winners at random from the all-correct entries.
--   * admin_quiz_draws()      every draw, with who won.
--
-- A REDRAW NEVER DRAWS THE SAME PERSON TWICE. The rules say a winner who
-- cannot be reached within 24 hours is replaced, and the replacement is the
-- second draw: entries already drawn in this round are left out of the pool.
-- So "draw 1 more" after an unreachable winner is exactly the rule, and every
-- draw is its own quiz_draws row, never an edit of the first.
--
-- `order by gen_random_uuid()`, not random(): random() is a seeded PRNG, and
-- gen_random_uuid() draws from the operating system's secure source. Nobody
-- is going to attack a jersey draw; the cost of doing it properly is nothing.
--
-- STILL NOT PUBLIC. Admin is every reporters.role = 'admin', the same set
-- that sees #/ops at all. A phone number is not shown to a reporter, and none
-- of this is in the Dataset.

begin;

alter table public.quizzes
  add column answer_key     jsonb not null default '{}'::jsonb,
  add column score_question text,
  add column score_match_id text;

update public.quizzes set answer_key =
  '{"last-win": "2008", "ss-scorers": "adepoju-njaliwa", "japan": "jaziya"}'
  where quiz_id = 'flames-egypt-1';
update public.quizzes set answer_key =
  '{"venue": "bingu", "matches": "680"}',
  score_question = 'cairo', score_match_id = '16'
  where quiz_id = 'flames-egypt-2';

alter table public.quiz_draws add column drawn_by text;


-- ── quiz_answer_key ──────────────────────────────────────────────────────────
-- The whole key, or NULL while a score question's match is unplayed. NULL and
-- not a partial key, because `answers @> partial` would put every entry that
-- got the OTHER questions right into the pool.

create or replace function public.quiz_answer_key(p_quiz text)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_quiz  public.quizzes;
  v_match record;
begin
  select * into v_quiz from public.quizzes q where q.quiz_id = p_quiz;
  if not found then
    return null;
  end if;
  if v_quiz.score_question is null then
    return v_quiz.answer_key;
  end if;
  select m.status, m.team_score, m.opponent_score into v_match
  from public.nt_matches m where m.match_id = v_quiz.score_match_id;
  if not found or v_match.status is distinct from 'played'
     or v_match.team_score is null or v_match.opponent_score is null then
    return null;
  end if;
  return v_quiz.answer_key || jsonb_build_object(v_quiz.score_question,
    v_match.team_score::int || '-' || v_match.opponent_score::int);
end;
$$;

revoke execute on function public.quiz_answer_key(text)
  from public, anon, authenticated;


-- ── admin_quiz_rounds ────────────────────────────────────────────────────────

create or replace function public.admin_quiz_rounds()
returns table (
  quiz_id     text,
  title       text,
  opens_at    timestamptz,
  closes_at   timestamptz,
  answer_key  jsonb,     -- NULL until it can be marked
  entries     integer,
  correct     integer,   -- NULL when answer_key is
  opted_in    integer,
  drawn       integer    -- entries already drawn as winners
)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise exception 'Only an administrator can see quiz entries.'
      using errcode = '42501';
  end if;
  return query
  select q.quiz_id, q.title, q.opens_at, q.closes_at, k.key,
         count(e.id)::int,
         case when k.key is null then null
              else (count(e.id) filter (where e.answers @> k.key))::int end,
         (count(e.id) filter (where e.opt_in_updates))::int,
         (select count(distinct w)::int
          from public.quiz_draws d, unnest(d.winners) w
          where d.quiz_id = q.quiz_id)
  from public.quizzes q
  cross join lateral (select public.quiz_answer_key(q.quiz_id) as key) k
  left join public.quiz_entries e on e.quiz_id = q.quiz_id
  group by q.quiz_id, q.title, q.opens_at, q.closes_at, k.key
  order by q.opens_at;
end;
$$;


-- ── admin_quiz_entries ───────────────────────────────────────────────────────

create or replace function public.admin_quiz_entries(p_quiz text)
returns table (
  id             bigint,
  created_at     timestamptz,
  name           text,
  phone          text,
  email          text,
  opt_in_updates boolean,
  answers        jsonb,
  correct        boolean   -- NULL while the key is incomplete
)
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_key jsonb;
begin
  if not public.is_admin() then
    raise exception 'Only an administrator can see quiz entries.'
      using errcode = '42501';
  end if;
  v_key := public.quiz_answer_key(p_quiz);
  return query
  select e.id, e.created_at, e.name, e.phone, e.email, e.opt_in_updates,
         e.answers, case when v_key is null then null else e.answers @> v_key end
  from public.quiz_entries e
  where e.quiz_id = p_quiz
  order by e.created_at desc;
end;
$$;


-- ── admin_draw_quiz ──────────────────────────────────────────────────────────

create or replace function public.admin_draw_quiz(p_quiz text, p_winners integer)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_quiz    public.quizzes;
  v_key     jsonb;
  v_entries integer;
  v_pool    integer;
  v_ids     bigint[];
begin
  if not public.is_admin() then
    raise exception 'Only an administrator can draw the quiz.'
      using errcode = '42501';
  end if;
  select * into v_quiz from public.quizzes q where q.quiz_id = p_quiz;
  if not found then
    raise exception 'No quiz %.', p_quiz;
  end if;
  if now() < v_quiz.closes_at then
    raise exception 'This round is still open. Draw after it closes.';
  end if;
  if p_winners is null or p_winners not between 1 and 10 then
    raise exception 'Draw between 1 and 10 winners.';
  end if;
  v_key := public.quiz_answer_key(p_quiz);
  if v_key is null then
    raise exception 'Enter the result of the match this round asks about '
                    'first: the score question cannot be marked without it.';
  end if;

  select count(*) into v_entries from public.quiz_entries e
  where e.quiz_id = p_quiz;

  -- Right answers, and not drawn before in this round (a redraw).
  with eligible as (
    select e.id from public.quiz_entries e
    where e.quiz_id = p_quiz and e.answers @> v_key
      and not exists (select 1 from public.quiz_draws d
                      where d.quiz_id = p_quiz and e.id = any (d.winners))
  )
  select (select count(*) from eligible),
         (select coalesce(array_agg(id), '{}') from
            (select id from eligible order by gen_random_uuid()
             limit p_winners) picked)
  into v_pool, v_ids;

  insert into public.quiz_draws
    (quiz_id, entries, pool, winners, answer_key, drawn_by)
  values (p_quiz, v_entries, v_pool, v_ids, v_key,
          public.current_reporter_id());

  return jsonb_build_object('entries', v_entries, 'pool', v_pool,
                            'winners', to_jsonb(v_ids));
end;
$$;


-- ── admin_quiz_draws ─────────────────────────────────────────────────────────

create or replace function public.admin_quiz_draws()
returns table (
  id        bigint,
  quiz_id   text,
  drawn_at  timestamptz,
  drawn_by  text,
  entries   integer,
  pool      integer,
  winners   jsonb       -- [{id, name, phone, email}] in the order drawn
)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if not public.is_admin() then
    raise exception 'Only an administrator can see quiz draws.'
      using errcode = '42501';
  end if;
  return query
  select d.id, d.quiz_id, d.drawn_at, coalesce(r.name, d.drawn_by, ''),
         d.entries, d.pool,
         coalesce((select jsonb_agg(jsonb_build_object(
                     'id', e.id, 'name', e.name, 'phone', e.phone,
                     'email', e.email) order by w.ord)
                   from unnest(d.winners) with ordinality w(entry_id, ord)
                   join public.quiz_entries e on e.id = w.entry_id),
                  '[]'::jsonb)
  from public.quiz_draws d
  left join public.reporters r on r.reporter_id = d.drawn_by
  order by d.drawn_at desc;
end;
$$;

revoke execute on function public.admin_quiz_rounds() from public, anon;
revoke execute on function public.admin_quiz_entries(text) from public, anon;
revoke execute on function public.admin_draw_quiz(text, integer) from public, anon;
revoke execute on function public.admin_quiz_draws() from public, anon;
grant execute on function public.admin_quiz_rounds() to authenticated;
grant execute on function public.admin_quiz_entries(text) to authenticated;
grant execute on function public.admin_draw_quiz(text, integer) to authenticated;
grant execute on function public.admin_quiz_draws() to authenticated;

commit;
