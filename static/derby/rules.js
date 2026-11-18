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

/* The message a friend receives: what it is, what they could win, and the
 * link last, so WhatsApp's preview card sits under the words. */
export function shareText({ cash, home, away }, url) {
  return `Join me in EveryLeague's ${home} v ${away} giveaway! You could win `
    + `${cash} or VIP tickets for the Derby. Tap my link to join: ${url}`;
}

/* The line a friend sees on arriving: who sent them, by first name when the
 * database knows it (0073), and what joining does for that person. */
export function inviteLine(name) {
  const who = (name || "").trim();
  return who
    ? `${who} invited you. Join below and ${who} gets a point.`
    : "A friend invited you. Join below and they get a point.";
}

export function whatsappUrl(text) {
  return `https://wa.me/?text=${encodeURIComponent(text)}`;
}

export function facebookUrl(url) {
  return `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(url)}`;
}

/* A person's points as the page shows them: the number, big, and one line
 * under it. Before the race a sign-up through the link earns nothing yet,
 * and the line says so rather than leaving a 0 that looks like a broken
 * link. */
export function pointsView({ counted = 0, used = 0 } = {}, phase) {
  const friends = (n) => (n === 1 ? "1 friend has" : `${n} friends have`);
  if (phase === "before") {
    return { n: 0, note: used
      ? `${friends(used)} joined with your link already. Points start counting when the competition opens.`
      : "Points start counting when the competition opens. Get sharing!" };
  }
  if (phase === "closed") {
    return { n: counted, note: "It's over. We will contact the winners." };
  }
  return { n: counted, note: counted
    ? "Keep sharing: the most points wins."
    : "Nobody has joined with your link yet. Send it to a friend!" };
}

/* What is wrong with the form, as [field, sentence], or null when it can be
 * sent. Fields: "follows", "name", "phone", "email". In the page's order, so
 * the first problem is the one nearest the top. */
export function formProblem({ follows = [], name = "", phone = "", email = "" }) {
  if (!follows.some((f) => FOLLOW_VALUES.includes(f))) {
    return ["follows", "Tap WhatsApp, Facebook or LinkedIn to follow us first."];
  }
  if (name.trim().length < 2) return ["name", "Please enter your name."];
  if (!normalisePhone(phone)) {
    return ["phone", "Please enter your phone number, like 0991 234 567."];
  }
  if (!emailOk(email)) return ["email", "That email address does not look right. Fix it, or leave it empty."];
  return null;
}
