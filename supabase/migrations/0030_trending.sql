-- 0030_trending.sql — the front of the homepage, editable without a deploy.
--
-- WHAT WAS WRONG. The one editorial slot on everyleague.co was
-- `_scorchers_feature()` in build.py: three hand-written sentences inside a
-- Python f-string. Its own docstring says what that cost — "it said 'Follow
-- the Scorchers as they take on Cameroon on Sunday' for a day after Cameroon
-- had won that final 3-0" — because changing a sentence meant editing Python,
-- pushing to main, and waiting for GitHub Actions. So it was changed roughly
-- never, and the first thing a reader saw was usually last month's story.
--
-- WHAT THIS DOES. One table of cards, written from /report by an
-- administrator, rendered as a carousel where that single card used to be. A
-- card is deliberately the smallest thing that can carry a story: a photo, an
-- eyebrow, a headline, a paragraph, and a link — nearly always a link to
-- somewhere else on this site, which is the entire point. The WAFCON card
-- pointed at /scorchers/ and that is the shape every card here should take:
-- the homepage is a way IN, not a destination.
--
-- THREE STATES, NOT A BOOLEAN. draft | live | archived.
--   * draft    — written, not on the site. A weekend preview gets typed on
--                Thursday and goes live on Friday.
--   * live     — rendered, in sort_order.
--   * archived — off the site, still findable. The ask was explicit: "you can
--                still find them later in the archive". A delete would have
--                answered "so they don't show up" and lost the other half.
-- Duplicating an archived card into a new draft is how last month's preview
-- becomes this month's, which is why duplicate_trending_card() exists.
--
-- WHY AN RPC PER OPERATION, when match_media (0007) got plain RLS policies.
-- The rule this repo uses is "RPC where a bad row could break the build, RLS
-- where it could not", and `trending` is a Dataset tab: src/dataset.py parses
-- it and validate.py checks it, so a row typed in a hurry CAN abort a build
-- and stop every future deploy for everyone. Every limit below — the lengths,
-- the link scheme, the status vocabulary — is therefore enforced here, in the
-- one place a browser cannot go round, and re-checked at build time by
-- validate.check_trending.
--
-- WHY THE LINK IS CHECKED AND NOT JUST STORED. The value goes straight into
-- an href on the site's most-visited page. A relative path or an https URL are
-- the only two things that can legitimately appear there; everything else
-- (javascript:, data:) is either a mistake or an attack, and a CHECK
-- constraint is the cheapest place in the world to say so.
--
-- THE BYTES ARE NEVER DELETED. An image lives in the `trending-media` bucket
-- and the row holds its object name. Clearing an image, deleting a card or
-- duplicating one NEVER touches the object — because duplicate_trending_card
-- copies the path, so two rows can share one object, and "delete the file when
-- the row goes" would blank the archive copy of a card somebody duplicated
-- last month. Orphaned objects therefore accumulate; at three cards a week and
-- ~150 KB each that is about 23 MB a year against a 1 GB free tier, which is
-- a much better trade than a reference count nobody would remember to keep
-- right.

begin;


-- ── trending ─────────────────────────────────────────────────────────────────

create table if not exists public.trending (
  card_id      text primary key,

  -- draft | live | archived. See the header: archived is not deleted.
  status       text not null default 'draft'
               check (status in ('draft', 'live', 'archived')),

  -- The small uppercase label above the headline — "Weekend preview",
  -- "Matchday review", "Player of the week". Free text rather than an enum:
  -- this is editorial, the useful set is not known in advance, and the portal
  -- offers the common ones as tappable chips so they stay consistent anyway.
  eyebrow      text not null default '' check (length(eyebrow) <= 40),

  headline     text not null check (headline <> '' and length(headline) <= 90),

  -- One short paragraph. Capped because a card that runs past the fold on a
  -- 390px screen stops being a card; the portal counts down to this number.
  body         text not null default '' check (length(body) <= 400),

  -- Where the card goes. Almost always a path on this site ('/scorchers/'),
  -- occasionally an external https URL, sometimes blank — a card with nothing
  -- to link at renders as a plain card rather than as a dead one.
  --
  -- The relative form is '/' followed by anything that is not another slash
  -- or a backslash: '//everyleague.co.evil' and '/\evil' are protocol-relative
  -- URLs that browsers follow OFF this site, and they would sail through a
  -- naive "starts with a slash" test.
  link_url     text not null default ''
               check (link_url = ''
                      or link_url ~ '^/([^/\\\s][^\s]*)?$'
                      or link_url ~ '^https://[^\s]+$'),
  -- What the button says. Blank falls back to a house default at render time.
  link_label   text not null default '' check (length(link_label) <= 40),

  -- Object name inside the `trending-media` bucket, or blank. A card with no
  -- photo renders text-only — the house rule is that missing data renders
  -- nothing, never a placeholder.
  image_path   text not null default '',
  -- Alt text. Blank is a real answer and renders alt="", which is correct for
  -- a photo that only decorates a headline already saying the same thing.
  image_alt    text not null default '' check (length(image_alt) <= 140),

  -- Editorial order within the carousel; ties break on card_id so the site is
  -- deterministic. Moved with move_trending_card, never typed.
  sort_order   integer not null default 0,

  -- When it first went live. Kept for the archive listing, and it is what
  -- makes "which preview was up that weekend" answerable later.
  published_at timestamptz,

  created_by   text references public.reporters (reporter_id),
  ord          bigint generated by default as identity,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

comment on table public.trending is
  'Homepage carousel cards — the lite CMS behind everyleague.co. Written only '
  'by administrators through the save_trending_card family; every length and '
  'the link scheme are constrained here because this table is a Dataset tab '
  'and a bad row would abort the build for everyone.';

comment on column public.trending.sort_order is
  'Carousel order among live cards, lowest first. Set by move_trending_card, '
  'which swaps two neighbours rather than letting anybody type a number.';

-- The build reads the whole table (drafts and archive included, so the
-- snapshot is a complete record) but the site renders only the live ones, and
-- the portal lists the archive newest-first.
create index if not exists trending_live_idx
  on public.trending (sort_order, card_id) where status = 'live';
create index if not exists trending_status_idx
  on public.trending (status, updated_at desc);

alter table public.trending enable row level security;

-- Public read, like every other football table: these cards are published on
-- the homepage, and the build reads them with the same key everything else
-- uses. A draft is therefore readable by anyone who asks PostgREST for it —
-- that is true of the snapshot in data/canonical/ as well, and a card nobody
-- has published yet is unannounced, not secret.
drop policy if exists trending_public_read on public.trending;
create policy trending_public_read on public.trending
  for select to anon, authenticated using (true);

-- No insert/update/delete policy at all. Every write goes through the
-- SECURITY DEFINER functions below, which is what makes is_admin() the single
-- answer to "who may change the homepage".


-- ── next_trending_id ─────────────────────────────────────────────────────────
-- MW_TRD_000001 and up — the MW_ prefix and six-digit counter every other id
-- minted in this schema uses (MW_OFF_ for officials, MW_REP_ for reporters).
-- The digits are a counter and carry no meaning.

create or replace function public.next_trending_id()
returns text
language sql
security definer
set search_path = ''
as $$
  select 'MW_TRD_' || lpad(
    (coalesce(
      (select max(substring(t.card_id from 8)::int)
       from public.trending t
       where t.card_id ~ '^MW_TRD_[0-9]{6}$'),
      0) + 1)::text, 6, '0');
$$;

revoke execute on function public.next_trending_id() from public, anon, authenticated;


-- ── _trending_admin ──────────────────────────────────────────────────────────
-- The same three lines every function below opens with. is_admin() is checked
-- rather than current_reporter_id() alone: an ordinary reporter reports
-- matches, and the homepage is not a match.

create or replace function public._trending_admin()
returns text
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_reporter text;
begin
  v_reporter := public.current_reporter_id();
  if v_reporter is null then
    raise exception 'reporter account is inactive or not linked'
      using errcode = '42501';
  end if;
  if not public.is_admin() then
    raise exception 'only an administrator can change the homepage'
      using errcode = '42501';
  end if;
  return v_reporter;
end;
$$;

revoke execute on function public._trending_admin() from public, anon, authenticated;


-- ── save_trending_card ───────────────────────────────────────────────────────
-- Create or update, in one function, because the portal's editor is one form
-- either way: a blank p_card_id mints an id, a given one updates that row.
--
-- Status is NOT settable here. Writing a card and publishing it are two
-- different decisions — the whole reason `draft` exists — and folding them
-- into one call is how a half-finished preview ends up on the homepage
-- because a hand slipped on the way to Save.
--
-- Every text is trimmed and its whitespace collapsed, the way create_player
-- and create_official do it: a headline pasted out of a Facebook post arrives
-- with newlines in it that no CSS anywhere will show, so they must not survive
-- into the row and then into the snapshot diff.

create or replace function public.save_trending_card(
  p_card_id    text default null,
  p_eyebrow    text default '',
  p_headline   text default '',
  p_body       text default '',
  p_link_url   text default '',
  p_link_label text default '',
  p_image_path text default '',
  p_image_alt  text default ''
)
returns setof public.trending
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_id       text;
  v_headline text;
  v_link     text;
  v_card     public.trending;
begin
  v_reporter := public._trending_admin();

  -- Collapse every run of whitespace, newlines included. The body keeps
  -- single spaces only: the card renders as one paragraph and always has.
  v_headline := btrim(regexp_replace(coalesce(p_headline, ''), '\s+', ' ', 'g'));
  v_link     := btrim(coalesce(p_link_url, ''));

  if length(v_headline) < 3 then
    raise exception 'a card needs a headline' using errcode = '22023';
  end if;
  if length(v_headline) > 90 then
    raise exception 'that headline is too long (90 characters)'
      using errcode = '22023';
  end if;
  -- Checked here as well as in the CHECK constraint so the portal gets a
  -- sentence a person can act on rather than a constraint name.
  if v_link <> ''
     and v_link !~ '^/([^/\\\s][^\s]*)?$'
     and v_link !~ '^https://[^\s]+$' then
    raise exception 'a link must start with / (a page on this site) or https://'
      using errcode = '22023';
  end if;

  if coalesce(btrim(p_card_id), '') = '' then
    insert into public.trending (
      card_id, status, eyebrow, headline, body, link_url, link_label,
      image_path, image_alt, sort_order, created_by)
    values (
      public.next_trending_id(), 'draft',
      btrim(regexp_replace(coalesce(p_eyebrow, ''), '\s+', ' ', 'g')),
      v_headline,
      btrim(regexp_replace(coalesce(p_body, ''), '\s+', ' ', 'g')),
      v_link,
      btrim(regexp_replace(coalesce(p_link_label, ''), '\s+', ' ', 'g')),
      btrim(coalesce(p_image_path, '')),
      btrim(regexp_replace(coalesce(p_image_alt, ''), '\s+', ' ', 'g')),
      -- A new card sorts after everything already live, so publishing one
      -- never silently jumps the queue in front of a card somebody placed.
      coalesce((select max(t.sort_order) from public.trending t), 0) + 1,
      v_reporter)
    returning * into v_card;
  else
    update public.trending t
    set eyebrow    = btrim(regexp_replace(coalesce(p_eyebrow, ''), '\s+', ' ', 'g')),
        headline   = v_headline,
        body       = btrim(regexp_replace(coalesce(p_body, ''), '\s+', ' ', 'g')),
        link_url   = v_link,
        link_label = btrim(regexp_replace(coalesce(p_link_label, ''), '\s+', ' ', 'g')),
        image_path = btrim(coalesce(p_image_path, '')),
        image_alt  = btrim(regexp_replace(coalesce(p_image_alt, ''), '\s+', ' ', 'g')),
        updated_at = now()
    where t.card_id = btrim(p_card_id)
    returning * into v_card;
    if not found then
      raise exception 'card not found' using errcode = 'P0002';
    end if;
  end if;

  return next v_card;
end;
$$;

comment on function public.save_trending_card(
    text, text, text, text, text, text, text, text) is
  'Create (blank p_card_id) or update one homepage card. Never changes status '
  '— publishing is set_trending_status, deliberately a separate tap.';

revoke execute on function public.save_trending_card(
  text, text, text, text, text, text, text, text) from public, anon;
grant execute on function public.save_trending_card(
  text, text, text, text, text, text, text, text) to authenticated;


-- ── set_trending_status ──────────────────────────────────────────────────────
-- draft -> live is publishing; live -> archived is the "archive" of the ask;
-- archived -> draft is picking it back up. Any transition is allowed, because
-- an admin who archived the wrong card must be able to undo it in one tap.
--
-- published_at is stamped the FIRST time a card goes live and never touched
-- again. It answers "what was on the homepage that weekend", and re-publishing
-- a corrected card must not rewrite that history.

create or replace function public.set_trending_status(
  p_card_id text,
  p_status  text
)
returns setof public.trending
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_status text;
  v_card   public.trending;
begin
  perform public._trending_admin();

  v_status := lower(btrim(coalesce(p_status, '')));
  if v_status not in ('draft', 'live', 'archived') then
    raise exception 'status must be draft, live or archived'
      using errcode = '22023';
  end if;

  update public.trending t
  set status       = v_status,
      published_at = case
                       when v_status = 'live' and t.published_at is null
                         then now()
                       else t.published_at
                     end,
      updated_at   = now()
  where t.card_id = btrim(p_card_id)
  returning * into v_card;
  if not found then
    raise exception 'card not found' using errcode = 'P0002';
  end if;

  return next v_card;
end;
$$;

revoke execute on function public.set_trending_status(text, text) from public, anon;
grant execute on function public.set_trending_status(text, text) to authenticated;


-- ── duplicate_trending_card ──────────────────────────────────────────────────
-- "A copy/duplicate option if you want to re-use/re-purpose." Last month's
-- weekend preview is the skeleton of this month's: same eyebrow, same link,
-- often the same photo, one new headline and paragraph.
--
-- The copy always lands as a DRAFT, whatever the original was, and takes the
-- same image_path — see the header on why that shared object is safe. It does
-- NOT copy published_at: the copy has never been published.

create or replace function public.duplicate_trending_card(p_card_id text)
returns setof public.trending
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
  v_src      public.trending;
  v_card     public.trending;
  v_headline text;
begin
  v_reporter := public._trending_admin();

  select * into v_src from public.trending t where t.card_id = btrim(p_card_id);
  if not found then
    raise exception 'card not found' using errcode = 'P0002';
  end if;

  -- " (copy)" marks it in a list of near-identical drafts, and is trimmed back
  -- if the headline is already at its 90-character limit — the suffix must
  -- never be the reason a legal card refuses to duplicate.
  v_headline := left(v_src.headline || ' (copy)', 90);

  insert into public.trending (
    card_id, status, eyebrow, headline, body, link_url, link_label,
    image_path, image_alt, sort_order, created_by)
  values (
    public.next_trending_id(), 'draft', v_src.eyebrow, v_headline, v_src.body,
    v_src.link_url, v_src.link_label, v_src.image_path, v_src.image_alt,
    coalesce((select max(t.sort_order) from public.trending t), 0) + 1,
    v_reporter)
  returning * into v_card;

  return next v_card;
end;
$$;

revoke execute on function public.duplicate_trending_card(text) from public, anon;
grant execute on function public.duplicate_trending_card(text) to authenticated;


-- ── move_trending_card ───────────────────────────────────────────────────────
-- Swap this card's sort_order with its neighbour's, among the live ones. A
-- ▲/▼ pair rather than a number field, for the same reason the portal uses
-- chips instead of checkboxes: on a phone, tapping "up" is a gesture and
-- typing "2" into a spinner is a chore that also lets two cards claim the
-- same place.

create or replace function public.move_trending_card(
  p_card_id   text,
  p_direction text
)
returns setof public.trending
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_dir   text;
  v_card  public.trending;
  v_other public.trending;
begin
  perform public._trending_admin();

  v_dir := lower(btrim(coalesce(p_direction, '')));
  if v_dir not in ('up', 'down') then
    raise exception 'direction must be up or down' using errcode = '22023';
  end if;

  select * into v_card from public.trending t
  where t.card_id = btrim(p_card_id) for update;
  if not found then
    raise exception 'card not found' using errcode = 'P0002';
  end if;
  if v_card.status <> 'live' then
    raise exception 'only a live card has a place in the carousel'
      using errcode = '22023';
  end if;

  -- The neighbour is found by the same (sort_order, card_id) ordering the site
  -- renders in, so "up" on screen and "up" here cannot disagree — including
  -- when two cards share a sort_order, which nothing prevents.
  if v_dir = 'up' then
    select * into v_other from public.trending t
    where t.status = 'live'
      and (t.sort_order, t.card_id) < (v_card.sort_order, v_card.card_id)
    order by t.sort_order desc, t.card_id desc
    limit 1 for update;
  else
    select * into v_other from public.trending t
    where t.status = 'live'
      and (t.sort_order, t.card_id) > (v_card.sort_order, v_card.card_id)
    order by t.sort_order, t.card_id
    limit 1 for update;
  end if;

  -- Already at the end. Not an error: the button is there on every card and
  -- tapping it at the top should do nothing, not raise.
  if not found then
    return next v_card;
    return;
  end if;

  -- A plain swap leaves the pair unmoved when they share a sort_order, so the
  -- mover is placed one clear of the neighbour instead and the neighbour takes
  -- the mover's old value. Ties are then broken, permanently.
  update public.trending t set sort_order = v_card.sort_order, updated_at = now()
  where t.card_id = v_other.card_id;
  update public.trending t
  set sort_order = case when v_dir = 'up' then v_other.sort_order - 1
                        else v_other.sort_order + 1 end,
      updated_at = now()
  where t.card_id = v_card.card_id
  returning * into v_card;

  return next v_card;
end;
$$;

revoke execute on function public.move_trending_card(text, text) from public, anon;
grant execute on function public.move_trending_card(text, text) to authenticated;


-- ── delete_trending_card ─────────────────────────────────────────────────────
-- Archiving is the normal way to take a card off the site; this is for the
-- one typed by mistake. The image object is deliberately left in the bucket —
-- see the header.

create or replace function public.delete_trending_card(p_card_id text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform public._trending_admin();
  delete from public.trending t where t.card_id = btrim(p_card_id);
  if not found then
    raise exception 'card not found' using errcode = 'P0002';
  end if;
end;
$$;

revoke execute on function public.delete_trending_card(text) from public, anon;
grant execute on function public.delete_trending_card(text) to authenticated;


-- ── Storage: the trending-media bucket ───────────────────────────────────────
-- A second bucket rather than a folder in `match-media`, because the two have
-- opposite access rules: match-media authorizes an upload by reading the match
-- out of the object's path, so that a reporter may add photos to their own
-- matches and nobody else's. Trending images belong to no match and are
-- admin-only, and expressing that inside can_report_media_path would mean one
-- function answering two unrelated questions.
--
-- Public, for the same reason match-media is: the static site has to be able
-- to <img src> the result, and a signed URL would expire long before the next
-- rebuild. 5 MB and the MIME allow-list are enforced by storage itself, so a
-- client that skipped the browser-side resize still cannot push a 40 MB photo
-- up a Malawian mobile connection.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('trending-media', 'trending-media', true, 5242880,
        array['image/jpeg', 'image/png', 'image/webp'])
on conflict (id) do update
  set public             = excluded.public,
      file_size_limit    = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists trending_media_read on storage.objects;
drop policy if exists trending_media_insert on storage.objects;
drop policy if exists trending_media_update on storage.objects;
drop policy if exists trending_media_delete on storage.objects;

create policy trending_media_read on storage.objects
  for select to anon, authenticated
  using (bucket_id = 'trending-media');

create policy trending_media_insert on storage.objects
  for insert to authenticated
  with check (bucket_id = 'trending-media' and public.is_admin());

create policy trending_media_update on storage.objects
  for update to authenticated
  using (bucket_id = 'trending-media' and public.is_admin());

create policy trending_media_delete on storage.objects
  for delete to authenticated
  using (bucket_id = 'trending-media' and public.is_admin());

commit;
