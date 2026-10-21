/* A club's match report, staged as what the match screen edits.
 *
 *     node --test 'tests/js/*.mjs'            (or: npm test)
 *
 * Driven end to end from MATCH_REPORT — the silverstrikers.mw report of
 * Moyale Barracks 0-1 Silver Strikers, read through both passes and merged
 * exactly as the Edge Function stores it — because every awkward case worth testing is in
 * that one ordinary article: one side's sheet, a card for a player on no sheet,
 * a red card for a team manager, a goal that already exists without its assist.
 */

import test from "node:test";
import assert from "node:assert/strict";

import { mergeReport, parseExtraction }
  from "../../supabase/functions/import-extract/extract.js";
import {
  findPerson, hasReport, linkToSquad, normalizeName, officialsFromReport,
  orientationByName,
  reconcileGoals, reportFor, sheetProblems, sheetsFromReport, teamFor,
} from "../../static/report/match_report.js";
import { MATCH_REPORT, MATCH_REPORT_PAYLOAD } from "./fixtures/extractions.mjs";

const MB = "MW_MB";   // Moyale Barracks, home
const SS = "MW_SS";   // Silver Strikers, away
const NAMES = { [MB]: "Moyale Barracks", [SS]: "Silver Strikers" };
const ctx = (flipped = false) => ({
  homeTeamId: MB, awayTeamId: SS, flipped, teamName: (id) => NAMES[id],
});
// Both passes, merged, exactly as runExtraction stores the row.
const source = () =>
  mergeReport(parseExtraction(MATCH_REPORT).items[0], MATCH_REPORT_PAYLOAD);

// ── Names ────────────────────────────────────────────────────────────────────

test("a printed name is folded the way a squad list would be", () => {
  assert.equal(normalizeName("  A.  Jósephy "), "a josephy");
  assert.equal(normalizeName("O’Neil-Banda"), "oneil banda");
});

test("a person is found only when exactly one fits", () => {
  const people = [{ name: "Innocent Shema" }, { name: "Innocent Smart" },
                  { name: "Peter Banda" }, { name: "Paul Banda" }];
  assert.equal(findPerson("innocent shema", people).name, "Innocent Shema");
  assert.equal(findPerson("I. Shema", people).name, "Innocent Shema");
  assert.equal(findPerson("Shema", people).name, "Innocent Shema");
  // Two Bandas: a surname is no answer, and neither is the first of them.
  assert.equal(findPerson("Banda", people), null);
  assert.equal(findPerson("P. Banda", people), null);
  // A full first name that disagrees is never a match, however few there are.
  assert.equal(findPerson("John Shema", people), null);
});

test("an abbreviated saved name matches the report's full one", () => {
  // A league graphic wrote "I. Shema" on the day; the club's report writes
  // "Innocent Shema". Either can be the saved name, so it works both ways.
  assert.equal(findPerson("Innocent Shema", [{ name: "I. Shema" }]).name, "I. Shema");
});

// ── Which match, which side ──────────────────────────────────────────────────

test("the report is joined to its match by the resolver's idx", () => {
  const extracted = { results: [source()] };
  const resolved = { items: [{ idx: 1, confidence: "green",
    match: { match_id: "MW_SL_2627_120", orientation: "as_given" } }] };
  const got = reportFor(extracted, resolved, "MW_SL_2627_120");
  assert.equal(got.flipped, false);
  assert.equal(got.source.away_team_raw, "Silver Strikers");
  assert.equal(reportFor(extracted, resolved, "MW_SL_2627_999"), null);
});

test("a fixture the reporter picked from a red item's list is accepted", () => {
  // Two clubs meet twice a season, and a report without a trusted date
  // resolves red with both fixtures listed; the review links each one here.
  const resolved = { items: [{ idx: 1, confidence: "red", match: null,
    fixtures: [
      { match_id: "MW_SL_2627_011", orientation: "flipped" },
      { match_id: "MW_SL_2627_134", orientation: "as_given" },
    ] }] };
  const extracted = { results: [source()] };
  assert.equal(reportFor(extracted, resolved, "MW_SL_2627_134").flipped, false);
  assert.equal(reportFor(extracted, resolved, "MW_SL_2627_011").flipped, true);
  assert.equal(reportFor(extracted, resolved, "MW_SL_2627_999"), null,
               "never a fixture the resolver did not offer");
});

test("a man of the match printed with his club still finds his row", () => {
  const report = { ...source(), motm_raw: "Ernest Petro-Silver Strikers" };
  const rows = sheetsFromReport(report, ctx()).sheets[SS];
  assert.deepEqual(rows.filter((r) => r.motm).map((r) => r.player_name),
                   ["Ernest Petro"]);
});

test("a report the resolver could not place is turned round by its names", () => {
  const src = { home_team_raw: "Silver Strikers FC", away_team_raw: "Moyale" };
  assert.deepEqual(orientationByName(src, "Moyale Barracks", "Silver Strikers"),
                   { flipped: true });
  assert.deepEqual(orientationByName({ home_team_raw: "Moyale Barracks",
    away_team_raw: "Silver Strikers" }, "Moyale Barracks", "Silver Strikers"),
                   { flipped: false });
  // One name agreeing is not enough: that is a different match.
  assert.equal(orientationByName({ home_team_raw: "Moyale Barracks",
    away_team_raw: "Bullets" }, "Moyale Barracks", "Silver Strikers"), null);
});

test("a red item is never staged against a match", () => {
  const resolved = { items: [{ idx: 1, confidence: "red",
    match: { match_id: "M", orientation: "as_given" } }] };
  assert.equal(reportFor({ results: [source()] }, resolved, "M"), null);
});

test("a flipped report puts its sheet on the other side", () => {
  assert.equal(teamFor("away", ctx()), SS);
  assert.equal(teamFor("away", ctx(true)), MB);
  assert.equal(teamFor("unknown", ctx()), "");
  const { sheets } = sheetsFromReport(source(), ctx(true));
  assert.deepEqual(Object.keys(sheets), [MB]);
});

test("a results row with nothing but scorers is not a report", () => {
  assert.equal(hasReport(source()), true);
  assert.equal(hasReport({ scorers: [{ player_raw: "X" }], team_sheets: [],
                           officials: {} }), false);
});

// ── The sheet ────────────────────────────────────────────────────────────────

test("one club's report becomes one sheet, in save_lineup's shape", () => {
  const { sheets } = sheetsFromReport(source(), ctx());
  assert.deepEqual(Object.keys(sheets), [SS], "Moyale have no sheet in it");
  const rows = sheets[SS];
  assert.equal(rows.filter((r) => r.role === "starting").length, 11);
  assert.equal(rows.filter((r) => r.role === "sub_on").length, 4);
  assert.equal(rows.filter((r) => r.role === "unused_sub").length, 5);
  assert.deepEqual(sheetProblems(rows), [], "it would save as it stands");
  for (const key of ["player_name", "player_id", "shirt_number", "position",
                     "role", "captain", "motm", "minute_on", "minute_off",
                     "replaced_player", "yellow_card", "yellow_red_card",
                     "red_card"]) {
    assert.ok(key in rows[0], key);
  }
});

test("a substitution says who came on, when, and for whom", () => {
  const rows = sheetsFromReport(source(), ctx()).sheets[SS];
  const duwe = rows.find((r) => r.player_name === "Festus Duwe");
  assert.equal(duwe.role, "sub_on");
  assert.equal(duwe.minute_on, "39");
  assert.equal(duwe.replaced_player, "Zebron Kalima");
  // Minute off is save_lineup's to derive, never this file's to store.
  assert.equal(rows.find((r) => r.player_name === "Zebron Kalima").minute_off, "");
  const khumbula = rows.find((r) => r.player_name === "Aubrey Khumbula");
  assert.equal(khumbula.replaced_player, "Charles Chipala",
               "the explicit list, not the sentence in another order");
});

test("the armband, the cards and the star land on their rows", () => {
  const rows = sheetsFromReport(source(), ctx()).sheets[SS];
  assert.deepEqual(rows.filter((r) => r.captain).map((r) => r.player_name),
                   ["Zebron Kalima"]);
  assert.deepEqual(rows.filter((r) => r.yellow_card).map((r) => r.player_name),
                   ["Innocent Shema", "Nickson Mwase", "Charles Chipala"]);
  assert.deepEqual(rows.filter((r) => r.motm).map((r) => r.player_name),
                   ["Ernest Petro"]);
});

test("everything with nowhere to go is said, not dropped", () => {
  const { unplaced } = sheetsFromReport(source(), ctx());
  assert.equal(unplaced.length, 2);
  assert.match(unplaced.find((u) => u.includes("Viyuyi")),
               /no Moyale Barracks team sheet/);
  assert.match(unplaced.find((u) => u.includes("Chipatala")),
               /only be kept against a player/);
});

test("a second yellow and a red on one player is one most-severe card", () => {
  const report = { ...source(), cards: [
    { team_side: "away", person_raw: "Mwase", card: "yellow", is_player: true },
    { team_side: "away", person_raw: "Mwase", card: "second_yellow", is_player: true },
    { team_side: "away", person_raw: "Mwase", card: "yellow", is_player: true },
  ] };
  const mwase = sheetsFromReport(report, ctx()).sheets[SS]
    .find((r) => r.player_name === "Nickson Mwase");
  assert.deepEqual([mwase.yellow_card, mwase.yellow_red_card, mwase.red_card],
                   [false, true, false]);
});

test("a substitute with no minute arrives asking for one", () => {
  const report = { ...source(), substitutions: [
    { team_side: "away", on_raw: "Festus Duwe", off_raw: "Zebron Kalima", minute: "" },
  ] };
  const { sheets, unplaced } = sheetsFromReport(report, ctx());
  assert.match(unplaced[0], /no minute for Festus Duwe/);
  assert.deepEqual(sheetProblems(sheets[SS]), ["Festus Duwe came on but has no minute."]);
});

test("a man of the match left on the bench keeps the star off", () => {
  const report = { ...source(), motm_raw: "Lasuli Jali" };
  const { sheets, unplaced } = sheetsFromReport(report, ctx());
  assert.equal(sheets[SS].some((r) => r.motm), false);
  assert.ok(unplaced.some((u) => u.includes("unused substitute")));
});

// ── Linking to the squad ─────────────────────────────────────────────────────

test("a sheet links to the players this club has fielded, uniquely", () => {
  const rows = sheetsFromReport(source(), ctx()).sheets[SS];
  const squad = [
    { player_id: "CAF_MW_1", name: "Innocent Shema", shirt_number: "5", position: "DF" },
    { player_id: "CAF_MW_2", name: "C. Maonga", shirt_number: "10", position: "" },
    { player_id: "CAF_MW_3", name: "Innocent Smart" },
    { player_id: "", name: "Ernest Petro" },             // known only as a name
  ];
  assert.equal(linkToSquad(rows, squad), 3);
  const by = (n) => rows.find((r) => r.player_name === n);
  assert.equal(by("Innocent Shema").player_id, "CAF_MW_1");
  assert.equal(by("Innocent Shema").shirt_number, "5", "the squad's shirt comes too");
  assert.equal(by("Chinsisi Maonga").player_id, "CAF_MW_2");
  assert.equal(by("Ernest Petro").player_id, "", "a name is not something to link to");
});

test("two printed names never link to one player", () => {
  const rows = [{ player_name: "Shema", player_id: "" },
                { player_name: "I. Shema", player_id: "" }];
  linkToSquad(rows, [{ player_id: "CAF_MW_1", name: "Innocent Shema" }]);
  assert.deepEqual(rows.map((r) => r.player_id), ["CAF_MW_1", ""]);
});

// ── Goals ────────────────────────────────────────────────────────────────────

const SAVED_SHEMA = { goal_id: "G1", team_id: SS, reported_player_name: "I. Shema",
                      player_id: "CAF_MW_1", assist_player_id: null,
                      minute: "", goal_type: "" };

test("a goal already saved gains the report's assist, minute and type", () => {
  const { amend, add, unplaced } = reconcileGoals(
    [SAVED_SHEMA], source().scorers, ctx(), { [MB]: 0, [SS]: 1 });
  assert.deepEqual(amend, [{ goal_id: "G1", team_id: SS, scorer: "I. Shema",
    assist_raw: "Chinsisi Maonga", minute: "7", goal_type: "header" }]);
  assert.deepEqual(add, []);
  assert.deepEqual(unplaced, []);
});

test("what the saved goal already says is never offered again", () => {
  const saved = { ...SAVED_SHEMA, assist_player_id: "CAF_MW_2", minute: "8" };
  const { amend } = reconcileGoals([saved], source().scorers, ctx(),
                                   { [MB]: 0, [SS]: 1 });
  assert.deepEqual(amend, [{ goal_id: "G1", team_id: SS, scorer: "I. Shema",
                             goal_type: "header" }],
                   "a different minute is not an amendment either");
});

test("a goal the match does not have yet is added if there is room", () => {
  const { add } = reconcileGoals([], source().scorers, ctx(), { [MB]: 0, [SS]: 1 });
  assert.deepEqual(add, [{ team_id: SS, player_name: "Innocent Shema",
    minute: "7", goal_type: "header", assist_raw: "Chinsisi Maonga" }]);
});

test("a report naming a different scorer is a question, not an edit", () => {
  const saved = { ...SAVED_SHEMA, reported_player_name: "Charles Chipala" };
  const { amend, add, unplaced } = reconcileGoals(
    [saved], source().scorers, ctx(), { [MB]: 0, [SS]: 1 });
  assert.deepEqual(amend, []);
  assert.deepEqual(add, []);
  assert.match(unplaced[0], /this match has Charles Chipala/);
});

test("an own goal is never placed by side", () => {
  const scorers = [{ player_raw: "Jimmy Viyuyi", team_side: "home",
                     own_goal: true, minute: 7 }];
  const { add, unplaced } = reconcileGoals([], scorers, ctx(), { [MB]: 0, [SS]: 1 });
  assert.deepEqual(add, []);
  assert.match(unplaced[0], /choosing the side it counted for/);
});

// ── Officials ────────────────────────────────────────────────────────────────

test("officials and the coach fill only the boxes that are empty", () => {
  const got = officialsFromReport(source(), ctx(), { referee: "M. Nyirongo" });
  assert.deepEqual(got, {
    assistant_referee_1: "Stammy Msukwa", assistant_referee_2: "Jailosi Ngoma",
    fourth_official: "Mercy Kayira", away_coach: "Patricio Kulemeka",
  });
  assert.equal(officialsFromReport(source(), ctx(true), {}).home_coach,
               "Patricio Kulemeka", "the coach flips with the sheet");
});
