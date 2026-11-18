/* Bring your friends to the Derby — the page's rules, DOM-free (0072).
 *
 * Imports nothing but the quiz's rules, so tests/js/test_derby_rules.mjs can
 * hold them to account without a browser. derby.js is the half that touches
 * the page.
 *
 * None of this is the guard. submit_derby_signup re-checks the name, the
 * phone, the follow and the prediction, and referral_rows alone decides what
 * counts; these exist so a fan hears "that number looks short" before a
 * round trip on a weak signal, not after it.
 */

import { emailOk, normalisePhone } from "../quiz/rules.js";

/* 0072's referral_new_code alphabet: no I, L, O, 0 or 1, so a code read
 * aloud or copied off a screenshot cannot be mistyped into another one. */
const CODE_RE = /^[ABCDEFGHJKMNPQRSTUVWXYZ2-9]{6}$/;

export const FOLLOW_VALUES = ["whatsapp", "facebook", "linkedin"];

/* The referrer's code from a query string ("?r=k7m2qx&utm_source=…"), or ""
 * when there is none or it is not one we could have issued. Lower case is
 * accepted: people retype links. */
export function refFromSearch(search) {
  let raw = "";
  try { raw = new URLSearchParams(search || "").get("r") || ""; } catch (_) { return ""; }
  const code = raw.trim().toUpperCase();
  return CODE_RE.test(code) ? code : "";
}

/* "before" | "open" | "closed", given ms. */
export function phaseAt(now, opens, closes) {
  if (now < opens) return "before";
  return now < closes ? "open" : "closed";
}

/* The optional prediction from its two boxes: both blank is no prediction,
 * both whole numbers 0–20 is one, anything else is a mistake to point at. */
export function predValue(home, away) {
  const blank = (s) => (s || "").trim() === "";
  if (blank(home) && blank(away)) return { ok: true, home: null, away: null };
  const ok = (s) => /^\s*\d{1,2}\s*$/.test(s || "") && Number(s) <= 20;
  if (ok(home) && ok(away)) return { ok: true, home: Number(home), away: Number(away) };
  return { ok: false };
}

/* A person's link. With a `source` it carries the UTM tags that tell GA4
 * which platform the friend came from; without, it is the short one shown
 * on the page and copied, because a link that looks like a tracking URL is
 * one people are slower to tap. */
export function linkFor(page, code, source = "", campaign = "") {
  const base = `${page}?r=${encodeURIComponent(code)}`;
  if (!source) return base;
  return `${base}&utm_source=${encodeURIComponent(source)}`
    + `&utm_medium=referral&utm_campaign=${encodeURIComponent(campaign)}`;
}

/* The message a friend receives. The prediction is the hook when there is
 * one: "I say Bullets 2–1. What's yours?" is a reason to reply. */
export function shareText({ cash, home, away, pred }, url) {
  const guess = pred && pred.home != null && pred.away != null
    ? ` I say ${home} ${pred.home}–${pred.away} ${away}. What's your score?`
    : "";
  return `${home} v ${away} is on Sunday!${guess} EveryLeague is giving away `
    + `${cash} and VIP tickets for the Derby. Sign up with my link: ${url}`;
}

export function whatsappUrl(text) {
  return `https://wa.me/?text=${encodeURIComponent(text)}`;
}

export function facebookUrl(url) {
  return `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(url)}`;
}

/* What the page says about a person's own link. Before the race, sign-ups
 * through it do not count yet, and the line says so rather than showing a
 * 0 that looks like a broken link. */
export function countLine({ counted = 0, used = 0 } = {}, phase) {
  const people = (n) => (n === 1 ? "1 friend" : `${n} friends`);
  if (phase === "before") {
    return used
      ? `${people(used)} signed up through your link already. Only sign-ups once the race starts count.`
      : "Nobody has signed up through your link yet. The race has not started.";
  }
  if (!counted) {
    return phase === "closed"
      ? "Nobody signed up through your link during the race."
      : "Nobody has signed up through your link yet. Send it to a friend!";
  }
  return phase === "closed"
    ? `You brought ${people(counted)}.`
    : `You've brought ${people(counted)} so far.`;
}

/* What is wrong with the form, as [field, sentence], or null when it can be
 * sent. Fields: "follows", "name", "phone", "email", "pred". In the page's
 * order, so the first problem is the one nearest the top. */
export function formProblem({ follows = [], name = "", phone = "", email = "", pred }) {
  if (!follows.some((f) => FOLLOW_VALUES.includes(f))) {
    return ["follows", "Follow EveryLeague on at least one of these, then tick where you follow us."];
  }
  if (name.trim().length < 2) return ["name", "Please enter your name."];
  if (!normalisePhone(phone)) {
    return ["phone", "Please enter your phone number, like 0991 234 567."];
  }
  if (!emailOk(email)) return ["email", "That email address does not look right. Fix it, or leave it empty."];
  if (pred && !pred.ok) return ["pred", "A prediction is two whole numbers, like 2 and 1. Or leave both empty."];
  return null;
}
