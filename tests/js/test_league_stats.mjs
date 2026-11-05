/* Leagues against each other (static/report/league_stats.js).
 *
 *     node --test 'tests/js/*.mjs'            (or: npm test)
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  BOARD_METRICS, METRICS, TEAM_METRICS, cutAt, dashboard, groupRows, inScope,
  ranking, rate, teamLeaders,
} from "../../static/report/league_stats.js";

const metric = (key) => METRICS.find((m) => m.key === key);
const team = (key) => TEAM_METRICS.find((m) => m.key === key);

// ops_competition_stats rows, the counts only.
const comp = (id, name, played, goals, extra = {}) => ({
  competition_id: id, season_id: "S", competition_name: name, level: "national",
  category: "men", played, sides: played * 2, goals_total: goals,
  clean_sheet_sides: 0, home_wins: 0, draws: 0, big_wins: 0, margin_total: 0,
  goal_rows: 0, ...extra,
});

const ROWS = [
  comp("SL", "Premiership", 152, 302, { clean_sheet_sides: 112 }),
  comp("U20", "Mzuzu U20", 157, 594, { level: "district", category: "youth",
    clean_sheet_sides: 60 }),
  comp("WP", "Women's Premiership", 30, 104, { category: "women", clean_sheet_sides: 15 }),
  comp("U14", "Mangochi U14", 3, 14, { level: "district", category: "youth" }),
  comp("NEW", "Not started", 0, 0, { level: null }),
];

test("a rate is null, not zero, when nothing was played", () => {
  assert.equal(rate([ROWS[4]], metric("gpg")), null);
  assert.deepEqual(rate([ROWS[0]], metric("gpg")),
    { num: 302, den: 152, value: 302 / 152, text: "1.99" });
});

test("a clean sheet is a side's, so the denominator is sides", () => {
  assert.equal(rate([ROWS[0]], metric("clean")).text, "37%");
  assert.equal(metric("clean").boardDetail(112, 304), "112 clean sheets · 152 matches");
});

test("scorer coverage never reaches a board", () => {
  assert.ok(!BOARD_METRICS.some((m) => m.key === "scorers"));
  assert.ok(METRICS.some((m) => m.key === "scorers"));   // the admin screen keeps it
});

test("scope: Unclassified is filtered for, never away", () => {
  assert.deepEqual(inScope(ROWS, { level: "none" }).map((r) => r.competition_id), ["NEW"]);
  assert.deepEqual(inScope(ROWS, { cat: "youth" }).map((r) => r.competition_id), ["U20", "U14"]);
  assert.equal(inScope(ROWS, {}).length, ROWS.length);
});

test("ranking leaves off a competition under the minimum, and says so", () => {
  const { bars, leftOff } = ranking(groupRows(ROWS), metric("gpg"), { min: 20 });
  assert.deepEqual(bars.map((b) => b.key), ["U20|S", "WP|S", "SL|S"]);
  assert.deepEqual(leftOff.map((g) => g.key).sort(), ["NEW|S", "U14|S"]);
  assert.deepEqual(bars.map((b) => b.position), [1, 2, 3]);
  // Goals per match has no ceiling: the leader's bar is full.
  assert.equal(bars[0].share, 1);
  assert.equal(bars[0].detail, "594 goals · 157 matches");
});

test("a percentage is drawn against 100, not against the leader", () => {
  const { bars } = ranking(groupRows(ROWS), metric("clean"), { min: 20 });
  assert.deepEqual(bars.map((b) => b.key), ["SL|S", "WP|S", "U20|S"]);
  assert.ok(Math.abs(bars[0].share - 112 / 304) < 1e-12);
  assert.equal(bars[1].share, 0.25);
});

test("entries level as printed are joint, even when the fractions differ", () => {
  const rows = [
    comp("A", "A", 20, 40), comp("B", "B", 40, 80), comp("C", "C", 30, 50),
    comp("D", "D", 1000, 667), comp("E", "E", 1000, 666),
  ];
  const { bars } = ranking(groupRows(rows), metric("gpg"));
  // A and B are both 2.00 exactly; B played more, so it is listed first.
  // D (0.667) and E (0.666) both print 0.67: ranking them 4th and 5th would
  // be an order nobody reading the board can see.
  assert.deepEqual(bars.map((b) => [b.key, b.value, b.position, b.joint]), [
    ["B|S", "2.00", 1, true], ["A|S", "2.00", 1, true], ["C|S", "1.67", 3, false],
    ["D|S", "0.67", 4, true], ["E|S", "0.67", 4, true],
  ]);
});

test("grouping by level sums every competition in it, and drops empty levels", () => {
  const groups = groupRows(inScope(ROWS, { cat: "youth" }), "level");
  assert.deepEqual(groups.map((g) => g.key), ["district"]);
  const { bars } = ranking(groups, metric("gpg"));
  assert.equal(bars[0].value, ((594 + 14) / 160).toFixed(2));
  assert.equal(bars[0].detail, "2 competitions · 608 goals · 160 matches");
});

test("a cut never splits a tie, unless keeping it would flood the board", () => {
  const r = (position) => ({ position });
  assert.deepEqual(cutAt([r(1), r(2), r(2), r(4)], 2).map((x) => x.position), [1, 2, 2]);
  const flood = [r(1), r(2), r(2), r(2), r(2), r(2), r(2)];
  assert.deepEqual(cutAt(flood, 2).map((x) => x.position), [1]);
  const allLevel = [r(1), r(1), r(1), r(1), r(1), r(1)];
  assert.equal(cutAt(allLevel, 2).length, 2);
});

test("dashboard: one panel per answerable metric, the leaders in each", () => {
  const panels = dashboard(groupRows(ROWS), ["gpg", "clean", "scorers", "nope"],
    { min: 20, per: 2 });
  assert.deepEqual(panels.map((p) => p.key), ["gpg", "clean"]);
  assert.deepEqual(panels[0].bars.map((b) => b.label), ["Mzuzu U20", "Women's Premiership"]);
  assert.equal(panels[1].title, "Clean sheets");
});

const t = (id, comp, played, extra) => ({
  team_id: id, team_name: id, competition_id: comp, season_id: "S", played,
  won: 0, drawn: 0, lost: 0, gf: 0, ga: 0, clean_sheets: 0, ...extra,
});

test("team leaders: scoped, minimum played, lower is better for defence", () => {
  const teams = [
    t("KB", "SL", 20, { ga: 10, clean_sheets: 10 }),
    t("CU", "SL", 20, { ga: 14, clean_sheets: 8 }),
    t("XX", "SL", 2, { ga: 0, clean_sheets: 2 }),    // too few played
    t("YY", "WP", 20, { ga: 2, clean_sheets: 18 }),  // another competition
  ];
  const scope = new Set(["SL|S"]);
  const def = teamLeaders(teams, team("defence"), { compKeys: scope, min: 6 });
  assert.deepEqual(def.map((r) => r.team.team_id), ["KB", "CU"]);
  assert.equal(def[0].value, "0.50");
  const cs = teamLeaders(teams, team("clean"), { min: 6 });
  assert.deepEqual(cs.map((r) => [r.team.team_id, r.value]),
    [["YY", "90%"], ["KB", "50%"], ["CU", "40%"]]);
});

test("team leaders: level teams share a position", () => {
  const teams = [
    t("A", "SL", 10, { gf: 20 }), t("B", "SL", 10, { gf: 20 }), t("C", "SL", 10, { gf: 5 }),
  ];
  assert.deepEqual(teamLeaders(teams, team("attack")).map((r) => [r.position, r.joint]),
    [[1, true], [1, true], [3, false]]);
});
