/* Bring your friends to the Derby — the page's rules (0072).
 *
 *     node --test 'tests/js/*.mjs'            (or: npm test)
 *
 * The link is the product here: a code that does not survive being retyped,
 * or a share message that drops it, is a referral that never reaches the
 * database. And the count line is posted to nobody but it is read by
 * everyone racing, so it must never show a 0 that looks like a broken link.
 */

import test from "node:test";
import assert from "node:assert/strict";

import { countLine, facebookUrl, formProblem, linkFor, phaseAt, predValue,
  refFromSearch, shareText, whatsappUrl } from "../../static/derby/rules.js";

test("a code is read from the link however it was retyped", () => {
  assert.equal(refFromSearch("?r=K7M2QX"), "K7M2QX");
  assert.equal(refFromSearch("?r=k7m2qx"), "K7M2QX");
  assert.equal(refFromSearch("?utm_source=whatsapp&r=K7M2QX&utm_campaign=derby2026"), "K7M2QX");
  assert.equal(refFromSearch("?r=%20K7M2QX%20"), "K7M2QX");
});

test("a code we could not have issued is no code", () => {
  for (const search of ["", "?", "?r=", "?r=K7M2Q", "?r=K7M2QXX", "?r=K7M2Q0",
    "?r=K7M2QI", "?r=K7M2QL", "?r=K7M2Q1", "?r=<script>", "?x=K7M2QX"]) {
    assert.equal(refFromSearch(search), "", search);
  }
});

test("phases split at the open and the close, the close itself closed", () => {
  assert.equal(phaseAt(99, 100, 200), "before");
  assert.equal(phaseAt(100, 100, 200), "open");
  assert.equal(phaseAt(199, 100, 200), "open");
  assert.equal(phaseAt(200, 100, 200), "closed");
});

test("a prediction is optional, and half of one is a mistake", () => {
  assert.deepEqual(predValue("", ""), { ok: true, home: null, away: null });
  assert.deepEqual(predValue(" ", ""), { ok: true, home: null, away: null });
  assert.deepEqual(predValue("2", "1"), { ok: true, home: 2, away: 1 });
  assert.deepEqual(predValue("0", "0"), { ok: true, home: 0, away: 0 });
  for (const [h, a] of [["2", ""], ["", "1"], ["21", "0"], ["-1", "0"], ["1.5", "0"], ["a", "1"]]) {
    assert.equal(predValue(h, a).ok, false, `${h}-${a}`);
  }
});

test("the shown link is short; the shared ones carry the platform", () => {
  const page = "https://everyleague.co/derby/";
  assert.equal(linkFor(page, "K7M2QX"), "https://everyleague.co/derby/?r=K7M2QX");
  const tagged = linkFor(page, "K7M2QX", "whatsapp", "derby2026");
  assert.equal(tagged, "https://everyleague.co/derby/?r=K7M2QX&utm_source=whatsapp"
    + "&utm_medium=referral&utm_campaign=derby2026");
  // And the page reads its own code back out of the tagged link.
  assert.equal(refFromSearch(new URL(tagged).search), "K7M2QX");
});

test("the WhatsApp message carries the link intact, and the prediction when there is one", () => {
  const url = "https://everyleague.co/derby/?r=K7M2QX&utm_source=whatsapp";
  const words = { cash: "MK50,000", home: "Bullets", away: "Wanderers" };
  const plain = shareText({ ...words, pred: null }, url);
  assert.ok(plain.endsWith(url));
  assert.ok(!plain.includes("I say"));
  const withPred = shareText({ ...words, pred: { ok: true, home: 2, away: 1 } }, url);
  assert.match(withPred, /I say Bullets 2–1 Wanderers/);
  const wa = new URL(whatsappUrl(withPred));
  assert.equal(wa.hostname, "wa.me");
  assert.equal(wa.searchParams.get("text"), withPred);
  assert.equal(new URL(facebookUrl(url)).searchParams.get("u"), url);
});

test("a 0-0 prediction is still a prediction", () => {
  const text = shareText({ cash: "MK50,000", home: "Bullets", away: "Wanderers",
    pred: { ok: true, home: 0, away: 0 } }, "u");
  assert.match(text, /I say Bullets 0–0 Wanderers/);
});

test("the count line never shows a bare 0 before the race", () => {
  assert.match(countLine({ counted: 0, used: 0 }, "before"), /not started/);
  assert.match(countLine({ counted: 0, used: 2 }, "before"), /^2 friends signed up .* Only sign-ups once the race starts count/);
  assert.match(countLine({ counted: 0, used: 1 }, "before"), /^1 friend signed up/);
  assert.match(countLine({ counted: 0, used: 0 }, "open"), /Send it to a friend/);
  assert.equal(countLine({ counted: 1, used: 3 }, "open"), "You've brought 1 friend so far.");
  assert.equal(countLine({ counted: 7, used: 7 }, "open"), "You've brought 7 friends so far.");
  assert.equal(countLine({ counted: 7, used: 9 }, "closed"), "You brought 7 friends.");
  assert.match(countLine(undefined, "open"), /Nobody/);
});

test("the form asks for the follow first, then the details, then the prediction", () => {
  const good = { follows: ["facebook"], name: "Chikondi Banda", phone: "0991 234 567",
    email: "", pred: { ok: true, home: null, away: null } };
  assert.equal(formProblem(good), null);
  assert.equal(formProblem({ ...good, follows: [] })[0], "follows");
  assert.equal(formProblem({ ...good, follows: ["twitter"] })[0], "follows");
  assert.equal(formProblem({ ...good, name: " C " })[0], "name");
  assert.equal(formProblem({ ...good, phone: "" })[0], "phone");
  assert.equal(formProblem({ ...good, phone: "12345" })[0], "phone");
  assert.equal(formProblem({ ...good, email: "not-an-email" })[0], "email");
  assert.equal(formProblem({ ...good, pred: { ok: false } })[0], "pred");
  // Two problems: the one nearest the top of the page wins.
  assert.equal(formProblem({ ...good, follows: [], phone: "" })[0], "follows");
});
