/* Know Your Flames — the quiz page's rules (0067).
 *
 *     node --test 'tests/js/*.mjs'            (or: npm test)
 *
 * normalisePhone is the one that matters: it mirrors
 * public.quiz_normalise_phone, and the unique (quiz, phone) index is only
 * "one entry per person" if every way a Malawian writes a number lands on the
 * same string. The cases are how numbers actually get typed on a phone.
 */

import test from "node:test";
import assert from "node:assert/strict";

import { emailOk, missingAnswers, normalisePhone, phaseAt, scoreValue } from "../../static/quiz/rules.js";

test("every way of writing a Malawian mobile is one number", () => {
  for (const raw of ["0991234567", "0991 234 567", "099-123-4567", "991234567",
    "265991234567", "+265991234567", "+265 991 234 567", "00265991234567",
    " (0991) 234567 "]) {
    assert.equal(normalisePhone(raw), "+265991234567", raw);
  }
  assert.equal(normalisePhone("0888123456"), "+265888123456");
});

test("an international number is kept as written", () => {
  assert.equal(normalisePhone("+44 7700 900123"), "+447700900123");
  assert.equal(normalisePhone("0044 7700 900123"), "+447700900123");
  assert.equal(normalisePhone("+27 82 123 4567"), "+27821234567");
});

test("short, long and empty numbers are refused", () => {
  for (const raw of ["", "   ", "0991234", "09912345678", "+2659912345",
    "+26599123456789", "+1234567", "12345678", "abc", null, undefined]) {
    assert.equal(normalisePhone(raw), null, String(raw));
  }
});

test("email is optional, and otherwise has to look like one", () => {
  assert.ok(emailOk(""));
  assert.ok(emailOk("  "));
  assert.ok(emailOk("fan@example.mw"));
  assert.ok(!emailOk("fan@example"));
  assert.ok(!emailOk("fan example@x.com"));
  assert.ok(!emailOk("a".repeat(115) + "@x.com"));
});

const Q = [
  { id: "r1", opens: 100, closes: 200 },
  { id: "r2", opens: 300, closes: 400 },
];

test("phaseAt: before, open, between, open, over", () => {
  assert.deepEqual(phaseAt(50, Q), { state: "before", quiz: Q[0] });
  assert.deepEqual(phaseAt(100, Q), { state: "open", quiz: Q[0] });
  assert.deepEqual(phaseAt(199, Q), { state: "open", quiz: Q[0] });
  assert.deepEqual(phaseAt(200, Q), { state: "between", quiz: Q[1] });
  assert.deepEqual(phaseAt(300, Q), { state: "open", quiz: Q[1] });
  assert.deepEqual(phaseAt(400, Q), { state: "over", quiz: Q[1] });
  assert.deepEqual(phaseAt(1, []), { state: "over", quiz: null });
});

test("a score is two whole numbers, Malawi first", () => {
  assert.equal(scoreValue("1", "2"), "1-2");
  assert.equal(scoreValue(" 0", "03"), "0-3");
  assert.equal(scoreValue("", "2"), "");
  assert.equal(scoreValue("1.5", "2"), "");
  assert.equal(scoreValue("-1", "2"), "");
  assert.equal(scoreValue("99", "2"), "");
});

test("missing answers come back in page order", () => {
  assert.deepEqual(missingAnswers(["a", "b", "c"], { a: "x", b: "", c: "y" }), ["b"]);
  assert.deepEqual(missingAnswers(["a", "b"], {}), ["a", "b"]);
  assert.deepEqual(missingAnswers(["a"], { a: "1" }), []);
});
