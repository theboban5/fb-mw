/* The matchday grid's rules, tested without a browser.
 *
 *     node --test tests/js/            (or: npm test)
 *
 * These are the assertions that cannot be made about a rendered screen. The
 * most important one is the last group: after a submission fails — wholly or
 * partly — is everything the reporter typed still on the row? That is rule 1
 * of the portal, and until now it was only ever verified by a person holding a
 * phone and turning aeroplane mode on.
 *
 * Nothing here touches the network, the DOM or Supabase. results_grid.js
 * imports nothing, which is what makes that true and keeps it true.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  gridRow, setScore, setStatus, isChanged, isConflict, savedScoreline,
  unconfirm, collectReports, applyBatchResult, summarize, resolveSource,
  rowsNeedingSource, isScored, acceptsScore, SOURCE_CHOICES,
} from "../../static/report/results_grid.js";

// A match as the portal's MATCH_FIELDS select returns it.
const match = (over = {}) => ({
  match_id: "MW_SL_2627_001",
  public_id: "11111111-1111-1111-1111-111111111111",
  home_team_id: "MW_BE_M1",
  away_team_id: "MW_SIL_M1",
  home: { display_name: "Blue Eagles" },
  away: { display_name: "Silver Strikers" },
  date: "2026-09-05",
  status: "scheduled",
  home_goals: null,
  away_goals: null,
  source_ref: "",
  ...over,
});

const played = (h, a, over = {}) =>
  match({ status: "played", home_goals: h, away_goals: a, ...over });

// ── The row, and what counts as a change ─────────────────────────────────────

test("an untouched row is not changed, so it is never republished", () => {
  const row = gridRow(played(1, 1));
  assert.equal(isChanged(row), false);
  assert.deepEqual(collectReports([row]).reports, []);
});

test("a scheduled fixture starts empty rather than at 0–0", () => {
  const row = gridRow(match());
  assert.equal(row.home, "");
  assert.equal(row.away, "");
  assert.equal(row.status, "scheduled");
});

test("an existing result is shown, not hidden", () => {
  const row = gridRow(played(3, 2));
  assert.equal(row.home, "3");
  assert.equal(row.away, "2");
});

// ── Score behaviour ──────────────────────────────────────────────────────────

test("entering a score defaults the match to played", () => {
  const row = gridRow(match());
  setScore(row, "home", "2");
  assert.equal(row.status, "played");
});

test("a score does not promote a match the reporter already marked abandoned", () => {
  const row = setStatus(gridRow(match()), "abandoned");
  setScore(row, "home", "2");
  assert.equal(row.status, "abandoned");
});

test("scores are digits only and clamped to 99", () => {
  const row = gridRow(match());
  setScore(row, "home", "2a");
  assert.equal(row.home, "2");
  setScore(row, "away", "999");
  assert.equal(row.away, "99");
  setScore(row, "home", "-4");
  assert.equal(row.home, "4");
});

test("postponed, abandoned and cancelled carry no score", () => {
  for (const status of ["postponed", "abandoned", "cancelled", "scheduled"]) {
    const row = gridRow(played(2, 1));
    setStatus(row, status);
    assert.equal(row.home, "", status);
    assert.equal(row.away, "", status);
    assert.equal(isScored(status), false, status);
  }
});

test("a scheduled fixture accepts a typed score", () => {
  // The row every reporter opens this screen to fill in. Gating the boxes on
  // isScored() disabled it, which made the whole grid unusable for its main
  // case — caught by looking at the screen, so it is pinned here.
  assert.equal(acceptsScore("scheduled"), true);
  assert.equal(acceptsScore("played"), true);
  assert.equal(acceptsScore("awarded"), true);
  for (const status of ["postponed", "abandoned", "cancelled"]) {
    assert.equal(acceptsScore(status), false, status);
  }
});

test("a played row with only one score is refused before the network is used", () => {
  const row = gridRow(match());
  setScore(row, "home", "2");
  const { sending, reports, invalid } = collectReports([row]);
  assert.equal(sending.length, 0);
  assert.equal(reports.length, 0);
  assert.equal(invalid, 1);
  assert.match(row.error, /both scores/i);
});

// ── Only changed rows are submitted ──────────────────────────────────────────

test("only the changed rows are sent", () => {
  const rows = [
    gridRow(played(1, 1, { match_id: "A" })),          // untouched
    gridRow(match({ match_id: "B" })),                  // will be filled in
    gridRow(match({ match_id: "C" })),                  // left alone
  ];
  setScore(rows[1], "home", "2");
  setScore(rows[1], "away", "0");

  const { sending, reports } = collectReports(rows);
  assert.deepEqual(sending.map((r) => r.matchId), ["B"]);
  assert.deepEqual(reports[0], {
    match_id: "B", status: "played", home: 2, away: 0,
    expect: { status: "scheduled", home: null, away: null },
  });
});

test("a status-only change with no score is sent", () => {
  const row = gridRow(match());
  setStatus(row, "postponed");
  const { reports } = collectReports([row]);
  assert.deepEqual(reports[0], {
    match_id: "MW_SL_2627_001", status: "postponed", home: null, away: null,
    expect: { status: "scheduled", home: null, away: null },
  });
});

// ── The conflict guard ───────────────────────────────────────────────────────

test("changing an already published result is a conflict until confirmed", () => {
  const row = gridRow(played(1, 1));
  setScore(row, "home", "2");
  assert.equal(isConflict(row), true);
  assert.equal(savedScoreline(row), "1–1");

  const { sending, conflicts } = collectReports([row]);
  assert.equal(sending.length, 0, "a conflict is held back, not sent");
  assert.deepEqual(conflicts, [row]);
  assert.equal(row.error, "", "a conflict is a question, not an error");
});

test("confirming a replacement sends it, without the guard", () => {
  const row = gridRow(played(1, 1));
  setScore(row, "home", "2");
  row.confirmed = true;
  const { sending, reports } = collectReports([row]);
  assert.equal(sending.length, 1);
  assert.equal("expect" in reports[0], false,
    "a deliberate correction must not be refused by its own guard");
});

test("editing again after confirming asks again", () => {
  const row = gridRow(played(1, 1));
  setScore(row, "home", "2");
  row.confirmed = true;
  // 2–1 was agreed to. 4–1 is a different claim.
  unconfirm(setScore(row, "home", "4"));
  assert.equal(isConflict(row), true);
});

test("reporting onto a scheduled fixture is never a conflict", () => {
  const row = gridRow(match());
  setScore(row, "home", "1");
  setScore(row, "away", "0");
  assert.equal(isConflict(row), false);
});

test("a postponed match being given a result is still a conflict", () => {
  // 'postponed' is decided: someone said this did not happen.
  const row = gridRow(match({ status: "postponed" }));
  setScore(row, "home", "1");
  assert.equal(isConflict(row), true);
  assert.equal(savedScoreline(row), "postponed");
});

// ── Source ───────────────────────────────────────────────────────────────────

test("free text wins over a tapped chip", () => {
  assert.equal(
    resolveSource({ text: "https://facebook.com/post/1", choice: "league" }),
    "https://facebook.com/post/1");
});

test("a chip resolves to its recorded wording", () => {
  assert.equal(resolveSource({ choice: "whatsapp" }), "Received via WhatsApp");
  for (const c of SOURCE_CHOICES) {
    assert.ok(resolveSource({ choice: c.key }).length > 0, c.key);
  }
});

test("direct report is attributed and dated", () => {
  assert.equal(
    resolveSource({ direct: true, reporterName: "J. Banda", today: "5 Sep 2026" }),
    "Direct report by J. Banda, 5 Sep 2026");
});

test("a blank source stays blank, so the server keeps each row's own", () => {
  assert.equal(resolveSource({}), "");
  assert.equal(resolveSource({ text: "   " }), "");
});

test("an overlong source is truncated rather than rejected", () => {
  const long = "x".repeat(900);
  assert.equal(resolveSource({ text: long }).length, 500);
});

test("a new result with no provenance anywhere is caught", () => {
  const fresh = gridRow(match());
  setScore(fresh, "home", "1"); setScore(fresh, "away", "0");
  const { sending } = collectReports([fresh]);
  assert.deepEqual(rowsNeedingSource(sending, ""), sending);
  assert.deepEqual(rowsNeedingSource(sending, "League official"), []);
});

test("a row that already has a source needs no shared one", () => {
  const row = gridRow(played(1, 1, { source_ref: "https://example.com/p" }));
  setScore(row, "home", "2");
  row.confirmed = true;
  const { sending } = collectReports([row]);
  assert.deepEqual(rowsNeedingSource(sending, ""), []);
});

// ── Folding the answer back ──────────────────────────────────────────────────

test("published rows become saved data and stop being changed", () => {
  const row = gridRow(match());
  setScore(row, "home", "2"); setScore(row, "away", "1");
  const { sending } = collectReports([row]);

  const { saved, failed } = applyBatchResult(sending, [
    { idx: 1, ok: true, match_id: row.matchId, home_goals: 2, away_goals: 1,
      status: "played", message: "" },
  ]);

  assert.equal(saved, 1);
  assert.equal(failed, 0);
  assert.equal(row.published, true);
  assert.equal(isChanged(row), false, "a published row must not offer to publish again");
  assert.deepEqual(collectReports([row]).reports, []);
});

test("a partial failure keeps the failed row exactly as typed", () => {
  const good = gridRow(match({ match_id: "A" }));
  const bad = gridRow(match({ match_id: "B" }));
  setScore(good, "home", "2"); setScore(good, "away", "1");
  setScore(bad, "home", "3"); setScore(bad, "away", "3");

  const { sending } = collectReports([good, bad]);
  const { saved, failed } = applyBatchResult(sending, [
    { idx: 1, ok: true, match_id: "A", home_goals: 2, away_goals: 1,
      status: "played", message: "" },
    { idx: 2, ok: false, match_id: "B", home_goals: null, away_goals: null,
      status: null, message: "someone else published 0–0 while you were entering this" },
  ]);

  assert.equal(saved, 1);
  assert.equal(failed, 1);
  // Rule 1: the entered values are untouched.
  assert.equal(bad.home, "3");
  assert.equal(bad.away, "3");
  assert.equal(bad.status, "played");
  assert.equal(bad.published, false);
  assert.match(bad.error, /someone else published/);
  // ...and the row that worked is done.
  assert.equal(good.published, true);
  assert.equal(good.error, "");
});

test("a row with no answer at all is treated as unsaved, not as saved", () => {
  const row = gridRow(match());
  setScore(row, "home", "1"); setScore(row, "away", "0");
  const { sending } = collectReports([row]);
  const { saved, failed } = applyBatchResult(sending, []);   // truncated answer
  assert.equal(saved, 0);
  assert.equal(failed, 1);
  assert.equal(row.published, false);
  assert.equal(row.home, "1", "nothing typed is lost");
  assert.match(row.error, /try it again/i);
});

test("a failure message survives the redraw that follows it", () => {
  // THE BUG THIS PINS. Both screens call collectReports while rendering, to
  // count what the publish button should say. collectReports used to clear
  // row.error as it went, so the message applyBatchResult had just written was
  // wiped before it was ever drawn — an amber row saying "needs attention"
  // with nothing on it saying why.
  const row = gridRow(match());
  setScore(row, "home", "1"); setScore(row, "away", "0");
  const { sending } = collectReports([row]);
  applyBatchResult(sending, [{ idx: 1, ok: false, message: "match not found" }],
                   () => "That match no longer exists.");
  assert.equal(row.error, "That match no longer exists.");

  // The redraw. This is what used to destroy it.
  collectReports([row]);
  assert.equal(row.error, "That match no longer exists.");
  collectReports([row]);
  assert.equal(row.error, "That match no longer exists.");
});

test("editing a failed row clears its message, because it stops being true", () => {
  const row = gridRow(match());
  setScore(row, "home", "1"); setScore(row, "away", "0");
  applyBatchResult(collectReports([row]).sending,
                   [{ idx: 1, ok: false, message: "x" }], () => "Something.");
  assert.equal(row.error, "Something.");
  setScore(row, "home", "2");
  assert.equal(row.error, "");
});

test("changing the status of a failed row clears its message too", () => {
  const row = gridRow(match());
  setScore(row, "home", "1"); setScore(row, "away", "0");
  applyBatchResult(collectReports([row]).sending,
                   [{ idx: 1, ok: false, message: "x" }], () => "Something.");
  setStatus(row, "postponed");
  assert.equal(row.error, "");
});

test("a validation error is still recomputed on every collect", () => {
  // The other half: an incomplete row must keep saying so, and must stop
  // saying so the moment it is completed.
  const row = gridRow(match());
  setScore(row, "home", "2");
  assert.match(collectReports([row]).sending.length === 0 ? row.error : "",
               /both scores/i);
  setScore(row, "away", "1");
  assert.equal(row.error, "");
  assert.equal(collectReports([row]).sending.length, 1);
});

test("the failure message reaches the row through humanError", () => {
  const row = gridRow(match());
  setScore(row, "home", "1"); setScore(row, "away", "0");
  const { sending } = collectReports([row]);
  applyBatchResult(sending,
    [{ idx: 1, ok: false, message: "not assigned to this competition" }],
    () => "You are not assigned to this competition.");
  assert.equal(row.error, "You are not assigned to this competition.");
});

test("resubmitting after a partial failure sends only the row that failed", () => {
  const good = gridRow(match({ match_id: "A" }));
  const bad = gridRow(match({ match_id: "B" }));
  setScore(good, "home", "2"); setScore(good, "away", "1");
  setScore(bad, "home", "3"); setScore(bad, "away", "3");

  applyBatchResult(collectReports([good, bad]).sending, [
    { idx: 1, ok: true, match_id: "A", home_goals: 2, away_goals: 1, status: "played" },
    { idx: 2, ok: false, match_id: "B", message: "no connection" },
  ]);

  // The retry. This is the idempotency that matters on a weak connection:
  // pressing publish again must not re-publish what already landed.
  const retry = collectReports([good, bad]);
  assert.deepEqual(retry.sending.map((r) => r.matchId), ["B"]);
});

// ── What the reporter is told ────────────────────────────────────────────────

test("the summary counts", () => {
  assert.deepEqual(summarize(7, 0), { message: "7 results published.", kind: "ok" });
  assert.deepEqual(summarize(1, 0), { message: "1 result published.", kind: "ok" });
  assert.equal(summarize(6, 1).message, "6 published; 1 still needs attention below.");
  assert.equal(summarize(5, 2).message, "5 published; 2 still need attention below.");
  assert.equal(summarize(0, 3).kind, "error");
});
