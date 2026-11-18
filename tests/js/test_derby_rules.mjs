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

import { facebookUrl, formProblem, inviteLine, linkFor, phaseAt, pointsView,
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

test("the shown link is short; the shared ones carry the platform", () => {
  const page = "https://everyleague.co/derby/";
  assert.equal(linkFor(page, "K7M2QX"), "https://everyleague.co/derby/?r=K7M2QX");
  const tagged = linkFor(page, "K7M2QX", "whatsapp", "derby2026");
  assert.equal(tagged, "https://everyleague.co/derby/?r=K7M2QX&utm_source=whatsapp"
    + "&utm_medium=referral&utm_campaign=derby2026");
  // And the page reads its own code back out of the tagged link.
  assert.equal(refFromSearch(new URL(tagged).search), "K7M2QX");
});

test("the WhatsApp message says what it is and ends with the link intact", () => {
  const url = "https://everyleague.co/derby/?r=K7M2QX&utm_source=whatsapp";
  const text = shareText({ cash: "MK50,000", home: "Bullets", away: "Wanderers" }, url);
  assert.match(text, /Bullets v Wanderers/);
  assert.match(text, /MK50,000/);
  assert.ok(text.endsWith(url));
  const wa = new URL(whatsappUrl(text));
  assert.equal(wa.hostname, "wa.me");
  assert.equal(wa.searchParams.get("text"), text);
  assert.equal(new URL(facebookUrl(url)).searchParams.get("u"), url);
});

test("an invite names the friend when the database knows them", () => {
  assert.equal(inviteLine("Chikondi"), "Chikondi invited you. Join below and Chikondi gets a point.");
  assert.equal(inviteLine(""), "A friend invited you. Join below and they get a point.");
  assert.equal(inviteLine(null), "A friend invited you. Join below and they get a point.");
});

test("points never show a bare 0 without saying why", () => {
  const before = pointsView({ counted: 0, used: 0 }, "before");
  assert.equal(before.n, 0);
  assert.match(before.note, /start counting/);
  const early = pointsView({ counted: 0, used: 2 }, "before");
  assert.equal(early.n, 0);
  assert.match(early.note, /^2 friends have joined with your link already/);
  assert.match(pointsView({ counted: 0, used: 1 }, "before").note, /^1 friend has joined/);
  assert.match(pointsView({ counted: 0, used: 0 }, "open").note, /Send it to a friend/);
  assert.deepEqual(pointsView({ counted: 3, used: 4 }, "open"),
    { n: 3, note: "Keep sharing: the most points wins." });
  assert.equal(pointsView({ counted: 7, used: 9 }, "closed").n, 7);
  assert.equal(pointsView(undefined, "open").n, 0);
});

test("the form asks for the follow first, then the details", () => {
  const good = { follows: ["facebook"], name: "Chikondi Banda", phone: "0991 234 567", email: "" };
  assert.equal(formProblem(good), null);
  assert.equal(formProblem({ ...good, follows: [] })[0], "follows");
  assert.equal(formProblem({ ...good, follows: ["twitter"] })[0], "follows");
  assert.equal(formProblem({ ...good, name: " C " })[0], "name");
  assert.equal(formProblem({ ...good, phone: "" })[0], "phone");
  assert.equal(formProblem({ ...good, phone: "12345" })[0], "phone");
  assert.equal(formProblem({ ...good, email: "not-an-email" })[0], "email");
  // Two problems: the one nearest the top of the page wins.
  assert.equal(formProblem({ ...good, follows: [], phone: "" })[0], "follows");
});
