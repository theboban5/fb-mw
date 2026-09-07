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
  acceptsScorers, addScorer, removeScorer, scorerRoom, collectGoals,
  applyGoalsResult, summarizeGoals, possessive,
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

// A FIXTURE ROW IS NOT A GRID ROW, AND THIS IS WHY IT MATTERS.
//
// #/add builds its own rows — home/away/date/kickoff/venue and no `saved`,
// because a fixture that does not exist yet has no saved state to compare
// against. When gridRowHtml moved to module scope for the import review to
// share, #/add's own `line()` was replaced by a call to it, and every draw of
// the fixture form threw here: `row.saved.status` on an object with no
// `saved`. The screen painted "Loading teams…" and died, so no fixture could
// be added by anybody for a month before anyone noticed.
//
// The throw is correct — a row with no saved state cannot answer "is this a
// correction?" and guessing would be worse. What was missing was a test
// saying so, in the file that owns the rule.
test("a row with no saved state is not a grid row, and says so loudly", () => {
  const fixtureRow = { home: "", away: "", homeText: "", awayText: "",
                       date: "", kickoff: "", venue: "", error: "" };
  assert.throws(() => isConflict(fixtureRow), TypeError);
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

// ── Scorers: the second pass ─────────────────────────────────────────────────
//
// The rule underneath all of these is that a goal needs a SIDE, that side is
// the one that benefited, and nothing derives it from the scorer. An own goal
// is the case where those two differ, and where getting it wrong is invisible.

test("a row carries the team ids a goal needs", () => {
  // gridRow deliberately kept only display names until scorers existed: a
  // score needs the match, a goal needs the side it counted for.
  const row = gridRow(played(2, 1));
  assert.equal(row.homeTeamId, "MW_BE_M1");
  assert.equal(row.awayTeamId, "MW_SIL_M1");
  assert.deepEqual(row.scorers, []);
});

test("scorers cannot be named until the score is published", () => {
  const row = gridRow(match());
  setScore(row, "home", "2");
  // Typed but not published: there are no goals for a scorer to belong to,
  // and apply_match_goal would refuse. Said here so the reporter is not
  // staging names against a line that may still become 0-0.
  assert.equal(acceptsScorers(row), false);
  assert.equal(addScorer(row, { teamId: row.homeTeamId, playerName: "A. Josephy" }),
               "Publish the score before adding scorers.");
  assert.equal(row.scorers.length, 0);
});

test("a postponed row accepts no scorers", () => {
  const row = gridRow(match({ status: "postponed" }));
  assert.equal(acceptsScorers(row), false);
});

test("a side cannot have more scorers than it scored", () => {
  const row = gridRow(played(2, 1));
  assert.equal(addScorer(row, { teamId: "MW_BE_M1", playerName: "A. Josephy" }), "");
  assert.equal(addScorer(row, { teamId: "MW_BE_M1", playerName: "G. Phiri" }), "");
  // validate.py check 5, said on the phone rather than by a rejection.
  assert.equal(addScorer(row, { teamId: "MW_BE_M1", playerName: "S. Banda" }),
               "All 2 of Blue Eagles' goals already have a scorer.");
  assert.equal(row.scorers.length, 2);
});

test("a side that did not score is told so in its own words", () => {
  const row = gridRow(played(2, 0));
  assert.equal(addScorer(row, { teamId: "MW_SIL_M1", playerName: "S. Banda" }),
               "Silver Strikers did not score in this match.");
});

test("goals already in the database count against the room left", () => {
  // Nearly every match has none, but a reporter coming back to add the second
  // scorer must not be offered room for three.
  const row = gridRow(played(2, 1, { scorer_count_home: 1 }));
  assert.equal(scorerRoom(row, "MW_BE_M1"), 1);
  assert.equal(addScorer(row, { teamId: "MW_BE_M1", playerName: "A. Josephy" }), "");
  assert.equal(scorerRoom(row, "MW_BE_M1"), 0);
  assert.equal(addScorer(row, { teamId: "MW_BE_M1", playerName: "G. Phiri" }),
               "All 2 of Blue Eagles' goals already have a scorer.");
});

test("a one-goal side gets a sentence written for one goal", () => {
  // A 1-0 is the commonest score here, so "All 1 of ... goals already have a
  // scorer" would have been the branch most reporters actually read.
  const row = gridRow(played(1, 0));
  addScorer(row, { teamId: "MW_BE_M1", playerName: "A. Josephy" });
  assert.equal(addScorer(row, { teamId: "MW_BE_M1", playerName: "G. Phiri" }),
               "Blue Eagles' only goal already has a scorer.");
});

test("a scorer needs a name and a side that played", () => {
  const row = gridRow(played(1, 1));
  assert.equal(addScorer(row, { teamId: "MW_BE_M1", playerName: "  " }),
               "Type the scorer's name first.");
  assert.equal(addScorer(row, { teamId: "MW_OTHER", playerName: "A. Josephy" }),
               "Pick which side the goal counted for.");
  assert.equal(row.scorers.length, 0);
});

test("an own goal counts for the side that benefited, not the scorer's", () => {
  // The whole reason teamId is asked for rather than derived. Blue Eagles win
  // 1-0 through a Silver Strikers defender: the goal is Blue Eagles'.
  const row = gridRow(played(1, 0));
  assert.equal(addScorer(row, {
    teamId: "MW_BE_M1", playerName: "S. Banda", goalType: "own_goal" }), "");
  assert.equal(row.scorers[0].teamId, "MW_BE_M1");
  assert.equal(collectGoals([row]).goals[0].team_id, "MW_BE_M1");
  // And the side the scorer actually plays for has no room at all, which is
  // what would have been silently wrong had the side been inferred.
  assert.equal(scorerRoom(row, "MW_SIL_M1"), 0);
});

test("an unknown goal type is dropped rather than sent", () => {
  // goals.goal_type has a CHECK constraint; a typo must not become a failed
  // line the reporter cannot interpret.
  const row = gridRow(played(1, 0));
  addScorer(row, { teamId: "MW_BE_M1", playerName: "A. Josephy", goalType: "bicycle" });
  assert.equal(row.scorers[0].goalType, "");
});

test("a name with nobody picked is still sent, unidentified", () => {
  // The deliberate trade: an unidentified goal counts in the team total and
  // never reaches a scorer table. Refusing it would lose the name entirely.
  const row = gridRow(played(1, 0));
  addScorer(row, { teamId: "MW_BE_M1", playerName: "A. Josephy" });
  const { goals } = collectGoals([row]);
  assert.equal(goals[0].player_id, "");
  assert.equal(goals[0].player_name, "A. Josephy");
});

test("a staged scorer can be taken back, a saved one cannot", () => {
  const row = gridRow(played(2, 0));
  addScorer(row, { teamId: "MW_BE_M1", playerName: "A. Josephy" });
  addScorer(row, { teamId: "MW_BE_M1", playerName: "G. Phiri" });
  row.scorers[0].saved = true;
  assert.equal(removeScorer(row, 0), false, "delete_match_goal's job, not this screen's");
  assert.equal(removeScorer(row, 1), true);
  assert.equal(row.scorers.length, 1);
});

test("scorers flatten across matches into one call, each carrying its match", () => {
  const a = gridRow(played(1, 0));
  const b = gridRow(played(0, 1, { match_id: "MW_SL_2627_002",
                                   home_team_id: "MW_MW_M1", away_team_id: "MW_KB_M1" }));
  addScorer(a, { teamId: "MW_BE_M1", playerName: "A. Josephy", minute: "12" });
  addScorer(b, { teamId: "MW_KB_M1", playerName: "S. Banda", goalType: "penalty" });
  const { goals } = collectGoals([a, b]);
  assert.equal(goals.length, 2);
  assert.deepEqual(goals.map((g) => g.match_id),
                   ["MW_SL_2627_001", "MW_SL_2627_002"]);
  assert.equal(goals[0].minute, "12");
  assert.equal(goals[1].goal_type, "penalty");
});

// ── After a failure, is everything the reporter typed still there? ───────────

test("a partial failure saves what it can and keeps the rest exactly as typed",
     () => {
  const row = gridRow(played(2, 1));
  addScorer(row, { teamId: "MW_BE_M1", playerName: "A. Josephy", minute: "12" });
  addScorer(row, { teamId: "MW_BE_M1", playerName: "G. Phiri", minute: "67" });
  addScorer(row, { teamId: "MW_SIL_M1", playerName: "S. Banda", minute: "81" });
  const { sending } = collectGoals([row]);

  const { saved, failed } = applyGoalsResult(sending, [
    { idx: 1, ok: true, goal_id: "MW_SL_2627_001_G1", message: "" },
    { idx: 2, ok: false, goal_id: null,
      message: "that player is not in the database" },
    { idx: 3, ok: true, goal_id: "MW_SL_2627_001_G2", message: "" },
  ]);

  assert.equal(saved, 2);
  assert.equal(failed, 1);
  assert.equal(row.scorers[1].playerName, "G. Phiri", "the name survives");
  assert.equal(row.scorers[1].minute, "67", "and so does the minute");
  assert.equal(row.scorers[1].saved, false);
  assert.match(row.scorers[1].error, /not in the database/);
  assert.equal(summarizeGoals(saved, failed).kind, "warn");
});

test("pressing save again sends only what did not go", () => {
  const row = gridRow(played(2, 1));
  addScorer(row, { teamId: "MW_BE_M1", playerName: "A. Josephy" });
  addScorer(row, { teamId: "MW_BE_M1", playerName: "G. Phiri" });
  const first = collectGoals([row]);
  applyGoalsResult(first.sending, [
    { idx: 1, ok: true, goal_id: "G1", message: "" },
    { idx: 2, ok: false, message: "boom" },
  ]);
  const second = collectGoals([row]);
  assert.equal(second.goals.length, 1);
  assert.equal(second.goals[0].player_name, "G. Phiri");
});

test("a scorer with no answer at all is treated as unsaved, not as saved", () => {
  const row = gridRow(played(1, 0));
  addScorer(row, { teamId: "MW_BE_M1", playerName: "A. Josephy" });
  const { sending } = collectGoals([row]);
  const { saved, failed } = applyGoalsResult(sending, []);
  assert.equal(saved, 0);
  assert.equal(failed, 1);
  assert.equal(row.scorers[0].saved, false);
  assert.equal(summarizeGoals(0, 1).kind, "error");
});

test("the failure message reaches the scorer through humanError", () => {
  const row = gridRow(played(1, 0));
  addScorer(row, { teamId: "MW_BE_M1", playerName: "A. Josephy" });
  const { sending } = collectGoals([row]);
  applyGoalsResult(sending, [{ idx: 1, ok: false, message: "raw pg text" }],
                   () => "Something went wrong — please try again.");
  assert.equal(row.scorers[0].error, "Something went wrong — please try again.");
});

test("a club name ending in s takes a bare apostrophe", () => {
  // Nearly every club in this dataset: Blue Eagles, Silver Strikers, Mighty
  // Wanderers, Bullets, Kamuzu Barracks. The bare 's this replaces had been
  // shipping "Mighty Wanderers's goals" on the single-match screen.
  assert.equal(possessive("Blue Eagles"), "Blue Eagles'");
  assert.equal(possessive("Mighty Wanderers"), "Mighty Wanderers'");
  assert.equal(possessive("Moyale Barracks"), "Moyale Barracks'");
  assert.equal(possessive("Karonga United"), "Karonga United's");
  assert.equal(possessive(""), "");
});
