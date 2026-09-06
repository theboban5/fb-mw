/* The fixture importer's rules, tested without a browser.
 *
 *     node --test tests/js/            (or: npm test)
 *
 * The assertions that cannot be made about a rendered screen. The two that
 * matter most are at the bottom: after a partial failure, is everything still
 * on the row — and does a correction that only moves the kick-off still carry
 * the date, which is the one way this feature could quietly delete data.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  classify, isPublishable, fixtureRow, collectFixtures, collectUpdates,
  applyFixtureResult, summarizeFixtures, differenceLabel,
} from "../../static/report/fixture_import.js";

/** An item as resolve_import_fixtures (0049) returns it. */
const item = (over = {}) => ({
  idx: 1,
  state: "new",
  confidence: "green",
  reasons: [],
  raw: { home: "EKHAYA FC", away: "KARONGA UNITED", venue: "MPIRA STADIUM" },
  home: { team_id: "MW_EFC_M1", name: "Ekhaya FC", rank: 1 },
  away: { team_id: "MW_KU_M1", name: "Karonga United", rank: 1 },
  date: "2026-09-06",
  kickoff: "14:30",
  kickoff_guessed: false,
  matchday: null,
  venue_id: "MW_B_001",
  venue_name: "Mpira Stadium",
  existing: null,
  differs: [],
  ...over,
});

const existing = (over = {}) => ({
  match_id: "MW_SL_2627_118",
  public_id: "11111111-1111-1111-1111-111111111111",
  date: "2026-09-06",
  kickoff: "14:30",
  venue_id: "MW_B_001",
  venue_name: "Mpira Stadium",
  status: "scheduled",
  ...over,
});

// ── What a row is ────────────────────────────────────────────────────────────

test("a resolved proposal becomes a row the fixture form can publish", () => {
  const row = fixtureRow(item());
  assert.equal(row.home, "MW_EFC_M1");
  assert.equal(row.away, "MW_KU_M1");
  assert.equal(row.homeText, "Ekhaya FC");
  assert.equal(row.date, "2026-09-06");
  assert.equal(row.kickoff, "14:30");
  assert.equal(classify(row), "new");
  assert.equal(isPublishable(row), true);
});

test("a matched ground is pre-filled with the name the database holds", () => {
  // Not the name the graphic printed: resolve_venue matches on the stored
  // spelling, and pre-filling "MPIRA STADIUM" would work only by luck.
  const row = fixtureRow(item());
  assert.equal(row.venue, "Mpira Stadium");
});

test("an unmatched ground is left blank, never invented", () => {
  const row = fixtureRow(item({ venue_id: null, venue_name: null,
                                raw: { home: "A", away: "B",
                                       venue: "Mkanda Primary School" } }));
  // Blank, so create_fixtures resolves nothing and mints nothing. The printed
  // name is kept beside it for the reporter to type in if they want it.
  assert.equal(row.venue, "");
  assert.equal(row.venueRaw, "Mkanda Primary School");
  // And it still publishes — as a fixture with no ground, which is legal.
  assert.equal(isPublishable(row), true);
});

test("an unresolved name is blocked, and carries what was printed", () => {
  const row = fixtureRow(item({
    state: "blocked", reasons: ["team_not_found"], away: null,
    raw: { home: "CHITIPA UNITED", away: "CRECK SC" },
  }));
  assert.equal(classify(row), "blocked");
  assert.equal(isPublishable(row), false);
  // The reporter has to see what the model read to know what to fix.
  assert.equal(row.awayText, "CRECK SC");
});

// ── Which rows are sent ──────────────────────────────────────────────────────

test("only new rows are published", () => {
  const rows = [
    fixtureRow(item({ idx: 1 })),
    fixtureRow(item({ idx: 2, state: "existing_agrees", existing: existing() })),
    fixtureRow(item({ idx: 3, state: "existing_differs", differs: ["kickoff"],
                      existing: existing() })),
    fixtureRow(item({ idx: 4, state: "blocked", home: null })),
  ];
  const { sending, fixtures } = collectFixtures(rows);
  assert.deepEqual(sending.map((r) => r.idx), [1]);
  assert.equal(fixtures.length, 1);
});

test("a matchday chosen once is sent down every line", () => {
  const rows = [fixtureRow(item({ idx: 1 })), fixtureRow(item({ idx: 2 }))];
  const { fixtures } = collectFixtures(rows, { matchday: "6" });
  assert.deepEqual(fixtures.map((f) => f.matchday), ["6", "6"]);
});

test("a cup round replaces the matchday rather than joining it", () => {
  const { fixtures } = collectFixtures([fixtureRow(item())],
                                       { stage: "qf", matchday: "6" });
  assert.equal(fixtures[0].stage, "qf");
  assert.equal(fixtures[0].matchday, undefined);
});

test("a graphic that is entirely already in the list sends nothing", () => {
  // The commonest case on a top-flight MATCH DAY poster, and it must be a
  // quiet no-op rather than a screen full of duplicate refusals.
  const rows = [
    fixtureRow(item({ idx: 1, state: "existing_agrees", existing: existing() })),
    fixtureRow(item({ idx: 2, state: "existing_agrees", existing: existing() })),
  ];
  assert.deepEqual(collectFixtures(rows).fixtures, []);
});

// ── Corrections ──────────────────────────────────────────────────────────────

test("a disagreement nobody has tapped corrects nothing", () => {
  const row = fixtureRow(item({ state: "existing_differs", differs: ["kickoff"],
                                kickoff: "15:00", existing: existing() }));
  assert.deepEqual(collectUpdates([row]), []);
});

test("a kick-off correction still carries the date it already had", () => {
  // THE ONE WAY THIS FEATURE COULD DELETE DATA. reschedule_match writes both
  // columns from its arguments, so sending a null date to move a kick-off
  // would take the date off the fixture.
  const row = fixtureRow(item({ state: "existing_differs", differs: ["kickoff"],
                                date: null, kickoff: "15:00",
                                existing: existing({ date: "2026-09-06" }) }));
  row.confirmed = true;
  const [update] = collectUpdates([row]);
  assert.equal(update.reschedule.date, "2026-09-06");
  assert.equal(update.reschedule.kickoff, "15:00");
  assert.equal(update.venue, null);
});

test("a ground correction sends no reschedule at all", () => {
  const row = fixtureRow(item({ state: "existing_differs", differs: ["venue"],
                                venue_name: "Chitipa Stadium",
                                existing: existing({ venue_name: "Mpira Stadium" }) }));
  row.confirmed = true;
  const [update] = collectUpdates([row]);
  assert.equal(update.reschedule, null);
  assert.equal(update.venue, "Chitipa Stadium");
  assert.equal(update.matchId, "MW_SL_2627_118");
});

test("a moved fixture sends both halves of the reschedule", () => {
  const row = fixtureRow(item({ state: "existing_differs",
                                differs: ["date", "kickoff"],
                                date: "2026-09-13", kickoff: "15:00",
                                existing: existing() }));
  row.confirmed = true;
  const [update] = collectUpdates([row]);
  assert.deepEqual(update.reschedule, { date: "2026-09-13", kickoff: "15:00" });
});

test("a correction already made is not made twice", () => {
  const row = fixtureRow(item({ state: "existing_differs", differs: ["venue"],
                                existing: existing() }));
  row.confirmed = true;
  row.updated = true;
  assert.deepEqual(collectUpdates([row]), []);
});

test("the offer says what it would replace, both sides", () => {
  const row = fixtureRow(item({ state: "existing_differs",
                                differs: ["kickoff", "venue"],
                                kickoff: "15:00", venue_name: "Chitipa Stadium",
                                existing: existing({ kickoff: "14:30",
                                                     venue_name: "Mpira Stadium" }) }));
  assert.equal(differenceLabel(row),
               "kick-off 14:30 → 15:00 · ground Mpira Stadium → Chitipa Stadium");
});

// ── Folding the answer back ──────────────────────────────────────────────────

test("a fixture that saved drops out of the next submission", () => {
  const rows = [fixtureRow(item({ idx: 1 })), fixtureRow(item({ idx: 2 }))];
  const { sending } = collectFixtures(rows);
  applyFixtureResult(sending, [
    { idx: 1, ok: true, match_id: "MW_SL_2627_200", public_id: "a" },
    { idx: 2, ok: true, match_id: "MW_SL_2627_201", public_id: "b" },
  ]);
  assert.equal(rows.every((r) => r.done), true);
});

test("a line that failed keeps everything on it and gains the reason", () => {
  const good = fixtureRow(item({ idx: 1 }));
  const bad = fixtureRow(item({ idx: 2, date: "2026-09-13" }));
  const { sending } = collectFixtures([good, bad]);

  const { added, failed } = applyFixtureResult(sending, [
    { idx: 1, ok: true, match_id: "MW_SL_2627_200", public_id: "a" },
    { idx: 2, ok: false, message: "that fixture is already in the list for 2026-09-13" },
  ], (e) => e.message);

  assert.equal(added, 1);
  assert.equal(failed, 1);
  // Rule 1: nothing typed is destroyed by a failure.
  assert.equal(bad.date, "2026-09-13");
  assert.equal(bad.home, "MW_EFC_M1");
  assert.match(bad.error, /already in the list/);
  assert.equal(bad.done, false);
});

test("a line with no result at all is a failure, not a success", () => {
  const row = fixtureRow(item());
  const { sending } = collectFixtures([row]);
  const { added, failed } = applyFixtureResult(sending, []);
  assert.equal(added, 0);
  assert.equal(failed, 1);
  assert.equal(row.done, false);
});

test("the retry sends only what did not land", () => {
  const good = fixtureRow(item({ idx: 1 }));
  const bad = fixtureRow(item({ idx: 2 }));
  applyFixtureResult(collectFixtures([good, bad]).sending, [
    { idx: 1, ok: true, match_id: "A", public_id: "a" },
    { idx: 2, ok: false, message: "no connection" },
  ]);
  // `done` is not publishable, so pressing add again cannot duplicate what
  // already saved — and insert_fixture's duplicate guard is the belt to this.
  good.item.state = "existing_agrees";
  assert.deepEqual(collectFixtures([good, bad]).sending.map((r) => r.idx), [2]);
});

// ── What the reporter is told ────────────────────────────────────────────────

test("the summary counts what actually happened", () => {
  assert.equal(summarizeFixtures(7, 0, 0).message, "7 fixtures added.");
  assert.equal(summarizeFixtures(1, 0, 0).message, "1 fixture added.");
  assert.equal(summarizeFixtures(3, 2, 0).message, "3 fixtures added, 2 corrected.");
  assert.equal(summarizeFixtures(0, 1, 0).message, "1 corrected.");
  assert.equal(summarizeFixtures(6, 0, 1).kind, "warn");
  assert.equal(summarizeFixtures(0, 0, 3).kind, "error");
});

test("a list that was already complete says so rather than saying nothing", () => {
  const { message, kind } = summarizeFixtures(0, 0, 0);
  assert.match(message, /already in the fixture list/);
  assert.equal(kind, "ok");
});
