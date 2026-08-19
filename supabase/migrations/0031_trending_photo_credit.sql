-- 0031_trending_photo_credit.sql — say whose photo it is.
--
-- WHAT WAS MISSING. 0030 gave a card a photo and a description of the photo,
-- and no way to say where it came from. Almost every image on a card like this
-- is somebody else's work — a club's Facebook page, an association's
-- photographer, a reporter standing at the touchline — and a site that
-- publishes those without a name is taking something. `image_alt` could not
-- do the job: it is read ALOUD to somebody who cannot see the photo, and
-- "Bullets fans at Kamuzu Stadium, photo by the FAM media team" is a worse
-- sentence for that reader than either half alone.
--
-- WHAT THIS DOES. One more optional text column, rendered small under the
-- card and only when there is a photo to credit — a credit with no photo is
-- nonsense, so the renderer drops it rather than the writer having to.
--
-- THE FUNCTION HAS TO BE DROPPED, NOT REPLACED. `create or replace` with a
-- different argument list makes an OVERLOAD, it does not replace: there would
-- then be an 8-argument and a 9-argument save_trending_card, every argument
-- defaulted, and PostgREST could not tell which one a call meant. The old
-- signature is dropped first, and the grant re-issued against the new one —
-- the grant is what makes this reachable at all (see 0030).

begin;

alter table public.trending
  add column if not exists image_credit text not null default ''
    check (length(image_credit) <= 80);

comment on column public.trending.image_credit is
  'Whose photo it is — "FAM Media", "Nyasa Big Bullets FC". Rendered small '
  'under the card, and ONLY when the card has a photo. Deliberately separate '
  'from image_alt, which is read aloud to somebody who cannot see the image '
  'and should not carry a byline.';


-- ── save_trending_card, with the credit ──────────────────────────────────────
-- Otherwise unchanged from 0030: it still cannot set `status`, because writing
-- a card and publishing it are two decisions.

drop function if exists public.save_trending_card(
  text, text, text, text, text, text, text, text);

create or replace function public.save_trending_card(
  p_card_id      text default null,
  p_eyebrow      text default '',
  p_headline     text default '',
  p_body         text default '',
  p_link_url     text default '',
  p_link_label   text default '',
  p_image_path   text default '',
  p_image_alt    text default '',
  p_image_credit text default ''
)
returns setof public.trending
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_reporter text;
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
      image_path, image_alt, image_credit, sort_order, created_by)
    values (
      public.next_trending_id(), 'draft',
      btrim(regexp_replace(coalesce(p_eyebrow, ''), '\s+', ' ', 'g')),
      v_headline,
      btrim(regexp_replace(coalesce(p_body, ''), '\s+', ' ', 'g')),
      v_link,
      btrim(regexp_replace(coalesce(p_link_label, ''), '\s+', ' ', 'g')),
      btrim(coalesce(p_image_path, '')),
      btrim(regexp_replace(coalesce(p_image_alt, ''), '\s+', ' ', 'g')),
      btrim(regexp_replace(coalesce(p_image_credit, ''), '\s+', ' ', 'g')),
      -- A new card sorts after everything already live, so publishing one
      -- never silently jumps the queue in front of a card somebody placed.
      coalesce((select max(t.sort_order) from public.trending t), 0) + 1,
      v_reporter)
    returning * into v_card;
  else
    update public.trending t
    set eyebrow      = btrim(regexp_replace(coalesce(p_eyebrow, ''), '\s+', ' ', 'g')),
        headline     = v_headline,
        body         = btrim(regexp_replace(coalesce(p_body, ''), '\s+', ' ', 'g')),
        link_url     = v_link,
        link_label   = btrim(regexp_replace(coalesce(p_link_label, ''), '\s+', ' ', 'g')),
        image_path   = btrim(coalesce(p_image_path, '')),
        image_alt    = btrim(regexp_replace(coalesce(p_image_alt, ''), '\s+', ' ', 'g')),
        image_credit = btrim(regexp_replace(coalesce(p_image_credit, ''), '\s+', ' ', 'g')),
        updated_at   = now()
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
    text, text, text, text, text, text, text, text, text) is
  'Create (blank p_card_id) or update one homepage card. Never changes status '
  '— publishing is set_trending_status, deliberately a separate tap.';

revoke execute on function public.save_trending_card(
  text, text, text, text, text, text, text, text, text) from public, anon;
grant execute on function public.save_trending_card(
  text, text, text, text, text, text, text, text, text) to authenticated;


-- ── duplicate_trending_card, carrying the credit ─────────────────────────────
-- Same signature as 0030, so this really is a replacement. The copy takes the
-- credit along with the photo, because it is the same photo — that is the
-- whole point of sharing the path.

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
    image_path, image_alt, image_credit, sort_order, created_by)
  values (
    public.next_trending_id(), 'draft', v_src.eyebrow, v_headline, v_src.body,
    v_src.link_url, v_src.link_label, v_src.image_path, v_src.image_alt,
    v_src.image_credit,
    coalesce((select max(t.sort_order) from public.trending t), 0) + 1,
    v_reporter)
  returning * into v_card;

  return next v_card;
end;
$$;

revoke execute on function public.duplicate_trending_card(text) from public, anon;
grant execute on function public.duplicate_trending_card(text) to authenticated;

commit;
