-- 0073_referral_inviter.sql — "Chikondi invited you": the inviter's first name
-- for a friend arriving on /derby/?r=<code>.
--
-- WHAT WAS WRONG. A friend opening a link read "A friend sent you here", and
-- then a page about referrals, codes and counting. The campaign's readers are
-- mostly not people who sign up for things online; a link from someone they
-- know is the one reason they have to trust a form asking for their phone
-- number, and the page did not say who that someone was.
--
-- WHAT THIS DOES. One function, granted to anon: a code in, the first word of
-- that person's name out, capitalised, or '' for a code nobody holds.
--
-- THE TRADE. 0072 promised a code reveals nothing. This reveals a first name,
-- and only to someone holding the link, which its owner sent them. A code is
-- six random characters from 31, so walking them to collect names is ~887
-- million guesses for a list of first names: not worth guarding further. No
-- surname, no phone, no count.

begin;

create or replace function public.referral_inviter(p_code text)
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce((
    select left(initcap(split_part(btrim(s.name), ' ', 1)), 30)
    from public.update_signups s
    where s.ref_code = upper(btrim(coalesce(p_code, '')))
  ), '');
$$;

comment on function public.referral_inviter(text) is
  'The first name behind a referral code (0073), for /derby/''s "X invited '
  'you". Granted to anon; nothing else about the person.';

revoke execute on function public.referral_inviter(text) from public;
grant execute on function public.referral_inviter(text) to anon, authenticated;

commit;
