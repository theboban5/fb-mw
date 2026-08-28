-- 0040_competition_accent_color.sql — what colour is this competition's card?
--
-- WHAT WAS WRONG. The #/graphics card generator (no migration — a portal-only
-- canvas renderer) draws every fixture board, results board and scorer
-- leaderboard in the same house green, whichever competition it is for. A
-- Women's Premiership card and a Super League card differ only in their text.
-- Nothing in the database says a competition has its own colour, because
-- nothing needed to before there was a place that drew one per competition.
--
-- WHAT THIS DOES.
--
--   * `competitions.accent_color`, a 6-digit hex colour, NULLABLE. Unlike
--     `level` (0039), NULL here is not a gap to close — every competition is
--     correctly unbranded until an administrator opts one in, and there is no
--     backfill: there is no "right" green for the Blantyre District U16
--     League, only an admin's eventual choice or none at all.
--   * `set_competition_accent_color`, so a competition's colour can be set or
--     cleared without a migration. `create_league` does NOT gain a matching
--     parameter — branding a competition is an afterthought a card generator
--     made possible, not a fact worth asking for at creation time, the same
--     way `set_entry_group` (0035) shipped with no creation-time parameter.
--
-- THE TRADE. This column is read by nothing on the built site — only by the
-- reporter portal's canvas renderer, at draw time. A wrong or garish colour
-- is one card looking odd, never a build failure and never a page anyone but
-- an admin can reach; the CHECK constraint only stops a value that would
-- crash a canvas `fillStyle`, not a value that would clash with anything.

begin;

-- ── The column ───────────────────────────────────────────────────────────────

alter table public.competitions
  add column if not exists accent_color text;

-- Named, so a later migration can drop it by name rather than hunting the
-- system catalogue for an anonymous check. 6-digit hex only — no shorthand
-- (#fff), no rgb()/hsl() — so it drops straight into a canvas fillStyle with
-- no parsing on the JS side.
do $$
begin
  if not exists (select 1 from pg_constraint
                 where conname = 'competitions_accent_color_check') then
    alter table public.competitions
      add constraint competitions_accent_color_check
      check (accent_color is null or accent_color ~ '^#[0-9a-fA-F]{6}$');
  end if;
end
$$;

comment on column public.competitions.accent_color is
  'A 6-digit hex colour (#rrggbb), or NULL for the house green. Portal-only: '
  'nothing on the built site reads this, only the #/graphics card generator.';

-- No backfill. Every competition starts NULL and stays NULL until an
-- administrator picks a colour for it on the Compare tab — that is the
-- correct steady state, not a data gap the way an unset level was.


-- ── set_competition_accent_color ─────────────────────────────────────────────
-- Admin only, for the same reason set_competition_level is: it describes the
-- shape of a competition rather than reporting what happened in a match.

create or replace function public.set_competition_accent_color(
  p_competition_id text,
  p_accent_color   text
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_color text;
begin
  if (select auth.uid()) is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  if not public.is_admin() then
    raise exception 'only an administrator can change a competition''s accent colour'
      using errcode = '42501';
  end if;

  -- An empty string is how a colour input's "Clear" control says "none", and
  -- it means NULL here: no colour is a real answer, not a fourth colour.
  v_color := nullif(trim(coalesce(p_accent_color, '')), '');
  if v_color is not null and v_color !~ '^#[0-9a-fA-F]{6}$' then
    raise exception 'accent colour must be a 6-digit hex value, e.g. #3fb37a'
      using errcode = '22023';
  end if;

  update public.competitions
  set accent_color = v_color
  where competition_id = p_competition_id;

  if not found then
    raise exception 'unknown competition %', p_competition_id
      using errcode = 'P0002';
  end if;

  return coalesce(v_color, '');
end;
$$;

comment on function public.set_competition_accent_color(text, text) is
  'Admin only. Sets a competition''s card accent colour, or clears it with a '
  'blank string. A 6-digit hex value only.';

revoke execute on function public.set_competition_accent_color(text, text)
  from public, anon;
grant execute on function public.set_competition_accent_color(text, text)
  to authenticated;

commit;
