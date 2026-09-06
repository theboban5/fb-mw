/* The crash reporter's rules, tested on a browser that is not on fire.
 *
 *     node --test tests/js/            (or: npm test)
 *
 * These cannot be asserted about a real failure, because by the time one is
 * happening the thing under test is the thing that has gone wrong. The two
 * that matter: a render loop throwing the same error cannot flood the
 * database, and nothing here can throw on the way to reporting a throw.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  routeOf, errorPayload, dedupeKey, shouldReport, brokenScreenMessage,
  SESSION_CAP,
} from "../../static/report/error_report.js";

// ── Which screen ─────────────────────────────────────────────────────────────

test("the route is the screen's name", () => {
  assert.equal(routeOf("#/add"), "/add");
  assert.equal(routeOf("#/teams"), "/teams");
  assert.equal(routeOf("#/"), "/");
  assert.equal(routeOf(""), "/");
});

test("an identifier in the route is not recorded", () => {
  // A match id is a fixture somebody is reporting on. This table's question is
  // "which screen is broken", and the id is no part of the answer.
  assert.equal(routeOf("#/m/9f3cb1e2-1111-2222-3333-444455556666"), "/m/…");
  assert.equal(routeOf("#/nt/c/Under%2020"), "/nt/…");
});

test("a query string goes, because it can carry a search term", () => {
  assert.equal(routeOf("#/players?q=Gift%20Phiri"), "/players");
  assert.equal(routeOf("#/ops?tab=submitted&day=2026-09-06"), "/ops");
});

// ── What is sent ─────────────────────────────────────────────────────────────

test("a thrown Error keeps its message and stack", () => {
  const err = new TypeError("Cannot read properties of undefined (reading 'status')");
  const p = errorPayload(err, { hash: "#/add", userAgent: "Chrome/1" });
  assert.equal(p.route, "/add");
  assert.match(p.message, /Cannot read properties of undefined/);
  assert.ok(p.stack.length > 0);
  assert.equal(p.user_agent, "Chrome/1");
});

test("a promise rejection is unwrapped", () => {
  const p = errorPayload({ reason: new TypeError("boom") }, { hash: "#/teams" });
  assert.equal(p.message, "boom");
});

test("a DOM ErrorEvent is unwrapped one level further", () => {
  const p = errorPayload({ message: "", error: new Error("deep") },
                         { hash: "#/results" });
  assert.equal(p.message, "deep");
});

test("something that is not an error at all is still described", () => {
  assert.equal(errorPayload("just a string").message, "just a string");
  assert.equal(errorPayload(42).message, "42");
});

test("a value that refuses to be stringified does not throw on the way out", () => {
  // The one case where the reporter itself could become the second failure.
  const hostile = { get message() { throw new Error("no"); } };
  const p = errorPayload(hostile, { hash: "#/add" });
  assert.equal(p.message, "an error that could not be described");
  assert.equal(p.route, "/add");
});

test("a long stack is truncated rather than dropped", () => {
  const err = new Error("x");
  err.stack = "y".repeat(9000);
  assert.equal(errorPayload(err).stack.length, 2000);
});

// ── How often ────────────────────────────────────────────────────────────────

test("a render loop throwing the same error reports once", () => {
  // THE ASSERTION THIS FILE EXISTS FOR. #/add threw on every draw; without
  // this the fix for a silent outage would have been a loud one.
  const seen = new Set();
  const payload = errorPayload(new TypeError("same every time"), { hash: "#/add" });
  let sent = 0;
  for (let i = 0; i < 500; i += 1) {
    if (shouldReport(payload, seen)) { seen.add(dedupeKey(payload)); sent += 1; }
  }
  assert.equal(sent, 1);
});

test("the same error on two screens is two problems", () => {
  const seen = new Set();
  const a = errorPayload(new TypeError("boom"), { hash: "#/add" });
  const b = errorPayload(new TypeError("boom"), { hash: "#/teams" });
  assert.equal(shouldReport(a, seen), true); seen.add(dedupeKey(a));
  assert.equal(shouldReport(b, seen), true);
});

test("a session that has gone wrong in many ways stops reporting", () => {
  const seen = new Set();
  let sent = 0;
  for (let i = 0; i < 50; i += 1) {
    const p = errorPayload(new Error(`different ${i}`), { hash: "#/add" });
    if (shouldReport(p, seen)) { seen.add(dedupeKey(p)); sent += 1; }
  }
  assert.equal(sent, SESSION_CAP);
});

test("an empty message is not a report", () => {
  assert.equal(shouldReport(errorPayload(""), new Set()), false);
});

// ── What the reporter sees ───────────────────────────────────────────────────

test("the message a reporter gets says the two things they can act on", () => {
  const { heading, body } = brokenScreenMessage("/add");
  // Not the stack, and not "Cannot read properties of undefined": true,
  // useless and frightening.
  assert.doesNotMatch(body, /undefined|TypeError|stack/i);
  assert.match(body, /nothing you had already saved/i);
  assert.match(heading, /did not load/i);
});
