-- 0075_ideas.sql — the ideas board behind #/ideas.
--
-- WHAT WAS WRONG. Every idea for the site — a 28px stepper thumbs keep
-- missing, a WhatsApp channel launch, "move to a private repo" — lived in a
-- chat thread, a notes app or somebody's head, and the "Not built:" lines in
-- CLAUDE.md were the closest thing to a backlog. Nothing could be sorted,
-- nothing could be ticked off, and a thing done was indistinguishable from a
-- thing forgotten.
--
-- WHAT THIS DOES. Two tables, written from /report by an administrator:
--   * idea_lanes — the board's columns, which are rows rather than an enum
--     because the useful set is not known in advance ("Waiting on FAM" is a
--     column nobody would have designed). Seeded Inbox / Next up / Doing.
--   * ideas      — a title, optional notes, free-text tags, a priority, a
--     size, a lane and an order within it.
-- Done is NOT a lane. Ticking an idea off sets closed_at and outcome
-- ('done', or 'dropped' for a decision not to), which takes it off the board
-- and into the Archive, where it stays findable and can be reopened. A delete
-- exists for an idea typed by mistake, never as the way to finish one.
--
-- WHY PLAIN RLS, when trending (0030) got an RPC per operation. The repo's
-- rule is "RPC where a bad row could break the build, RLS where it could
-- not", and these tables are NOT Dataset tabs and must never become one:
-- data/canonical/ is public, and a note about a marketing plan or the repo
-- does not belong in it. The build never reads them, so a bad row breaks
-- nothing, and the CHECKs below are the whole of the validation.
-- tests/test_ideas_private.py fails if either name ever reaches the tab list.
--
-- WHO. Administrators, for everything, reading included — an ordinary
-- reporter gets nothing rather than a filtered subset (0016's arrangement).
-- anon is revoked explicitly: this project's default privileges grant every
-- new object to anon (0048), and is_admin() being false for it is a second
-- guard, not the first.
--
-- A LANE CANNOT BE DELETED UNDER AN OPEN IDEA. lane_id is ON DELETE SET
-- NULL, and a CHECK requires an open idea to have a lane, so deleting a lane
-- that still holds one fails rather than orphaning it. A closed idea keeps
-- its lane so Reopen can put it back where it came from; if that lane has
-- since gone, its lane_id is simply null and Reopen uses the first lane.
--
-- THE ORDER IS A FLOAT. "Top of the lane" is min - 1, and ▲▼ swaps two
-- neighbours with two plain updates. If the second fails the two share an
-- order and tie on idea_id, which is a card one place out on an admin
-- screen — not worth an RPC.

begin;


-- ── idea_lanes ───────────────────────────────────────────────────────────────

create table if not exists public.idea_lanes (
  lane_id    bigint generated always as identity primary key,
  name       text not null
             check (name = btrim(name) and length(name) between 1 and 30),
  sort_order double precision not null default 0,
  created_at timestamptz not null default now()
);

comment on table public.idea_lanes is
  'Columns of the #/ideas board, in sort_order. Admin-only; never a Dataset '
  'tab. Done is not a lane — see ideas.closed_at.';


-- ── ideas ────────────────────────────────────────────────────────────────────

-- Each tag 1-24 characters and at most twelve of them. A function because a
-- CHECK cannot hold a subquery over the array's elements.
create or replace function public.idea_tags_ok(p_tags text[])
returns boolean
language sql
immutable
set search_path = ''
as $$
  select coalesce(cardinality(p_tags), 0) <= 12
     and not exists (
       select 1 from unnest(p_tags) t
       where t is null or t <> btrim(t) or length(t) not between 1 and 24
     )
$$;

create table if not exists public.ideas (
  idea_id    bigint generated always as identity primary key,
  title      text not null
             check (title = btrim(title) and length(title) between 1 and 200),
  notes      text not null default '' check (length(notes) <= 4000),

  -- Null only once closed and its lane deleted; see the header.
  lane_id    bigint references public.idea_lanes (lane_id) on delete set null,

  -- Free text, offered back as chips. Nothing joins on a tag.
  tags       text[] not null default '{}' check (public.idea_tags_ok(tags)),

  -- 1 high, 2 normal, 3 low: a number so it sorts.
  priority   smallint not null default 2 check (priority between 1 and 3),
  -- Blank is "not sized yet", the common case for a fresh idea.
  size       text not null default ''
             check (size in ('', 'small', 'medium', 'big')),

  sort_order double precision not null default 0,

  -- Both or neither: an idea is open, or it was closed as one of two things.
  closed_at  timestamptz,
  outcome    text check (outcome in ('done', 'dropped')),
  constraint ideas_closed_has_outcome
    check ((closed_at is null) = (outcome is null)),
  constraint ideas_open_has_lane
    check (closed_at is not null or lane_id is not null),

  created_by text references public.reporters (reporter_id)
             default public.current_reporter_id(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

comment on table public.ideas is
  'The #/ideas board: improvements, plans and decisions for the site. '
  'Admin-only and deliberately NOT a Dataset tab — data/canonical/ is public.';

create trigger ideas_set_updated_at
  before update on public.ideas
  for each row execute function public.set_updated_at();

-- The board reads open ideas by lane; the archive reads closed newest first.
create index if not exists ideas_open_idx
  on public.ideas (lane_id, sort_order, idea_id) where closed_at is null;
create index if not exists ideas_closed_idx
  on public.ideas (closed_at desc) where closed_at is not null;


-- ── RLS ──────────────────────────────────────────────────────────────────────

alter table public.idea_lanes enable row level security;
alter table public.ideas enable row level security;

create policy idea_lanes_admin_read
  on public.idea_lanes for select to authenticated using (public.is_admin());
create policy idea_lanes_admin_insert
  on public.idea_lanes for insert to authenticated with check (public.is_admin());
create policy idea_lanes_admin_update
  on public.idea_lanes for update to authenticated
  using (public.is_admin()) with check (public.is_admin());
create policy idea_lanes_admin_delete
  on public.idea_lanes for delete to authenticated using (public.is_admin());

create policy ideas_admin_read
  on public.ideas for select to authenticated using (public.is_admin());
create policy ideas_admin_insert
  on public.ideas for insert to authenticated with check (public.is_admin());
create policy ideas_admin_update
  on public.ideas for update to authenticated
  using (public.is_admin()) with check (public.is_admin());
create policy ideas_admin_delete
  on public.ideas for delete to authenticated using (public.is_admin());

revoke all on public.idea_lanes, public.ideas from anon;
grant select, insert, update, delete
  on public.idea_lanes, public.ideas to authenticated;


-- ── seed ─────────────────────────────────────────────────────────────────────
-- Only into an empty table, so re-running this never resurrects a lane an
-- admin renamed or removed.

insert into public.idea_lanes (name, sort_order)
select v.name, v.sort_order
from (values ('Inbox', 1), ('Next up', 2), ('Doing', 3)) as v (name, sort_order)
where not exists (select 1 from public.idea_lanes);

commit;
