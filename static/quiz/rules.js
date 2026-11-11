/* Know Your Flames — the quiz page's rules, DOM-free (0067).
 *
 * Imports nothing, so tests/js/test_quiz_rules.mjs can hold them to account
 * without a browser. quiz.js is the half that touches the page.
 *
 * None of this is the guard. submit_quiz_entry re-checks the window, the
 * phone number and the answers on the database clock; these exist so a fan
 * hears "that number looks short" before a round trip on a weak signal, not
 * after it.
 */

/* The same rule as public.quiz_normalise_phone, line for line. A Malawian
 * number comes back as +265 and nine digits however it was typed; a number
 * written with + or 00 is international and kept. Anything else is null. */
export function normalisePhone(raw) {
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim();
  let plus = trimmed.startsWith("+");
  let digits = trimmed.replace(/[^0-9]/g, "");
  if (!plus && digits.startsWith("00")) {
    plus = true;
    digits = digits.slice(2);
  }
  if (plus || (digits.startsWith("265") && digits.length === 12)) {
    if (digits.startsWith("265")) return digits.length === 12 ? "+" + digits : null;
    return plus && digits.length >= 8 && digits.length <= 15 ? "+" + digits : null;
  }
  if (digits.startsWith("0") && digits.length === 10) return "+265" + digits.slice(1);
  if (digits.length === 9) return "+265" + digits;
  return null;
}

/* Blank is fine (email is optional); otherwise the database's shape. */
export function emailOk(raw) {
  const v = (raw || "").trim();
  return v === "" || (v.length <= 120 && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v));
}

/* Which round the page is about at `now` (ms), given [{id, opens, closes}]
 * in ms and in order:
 *   {state: "open",    quiz}        — take entries for `quiz`
 *   {state: "between", quiz: next}  — one closed, `next` not open yet
 *   {state: "before",  quiz: first} — nothing open yet
 *   {state: "over",    quiz: last}  — every round has closed */
export function phaseAt(now, quizzes) {
  if (!quizzes.length) return { state: "over", quiz: null };
  for (const q of quizzes) {
    if (now >= q.opens && now < q.closes) return { state: "open", quiz: q };
  }
  const next = quizzes.find((q) => now < q.opens);
  if (!next) return { state: "over", quiz: quizzes[quizzes.length - 1] };
  return { state: next === quizzes[0] ? "before" : "between", quiz: next };
}

/* A score answer, "1-2" with Malawi first, from the two boxes. Whole numbers
 * 0–30 only; anything else is unanswered. */
export function scoreValue(home, away) {
  const ok = (s) => /^\s*\d{1,2}\s*$/.test(s || "") && Number(s) <= 30;
  return ok(home) && ok(away) ? `${Number(home)}-${Number(away)}` : "";
}

/* The question ids with no answer yet, in page order. */
export function missingAnswers(questionIds, answers) {
  return questionIds.filter((id) => !answers[id]);
}
