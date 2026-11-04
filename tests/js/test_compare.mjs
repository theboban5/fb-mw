/* Two teams side by side, and a team's last five (static/report/compare.js).
 *
 *     node --test 'tests/js/*.mjs'            (or: npm test)
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  form, formPoints, headToHead, headToHeadLines, leagueTable, playedBefore,
  statRows, teamRecord, topScorer,
} from "../../static/report/compare.js";

const m = (id, date, home, away, hg, ag, extra = {}) => ({
  match_id: id, date, home_team_id: home, away_team_id: away,
  home_goals: hg, away_goals: ag, status: "played", ...extra,
});

// KB and CU, plus two others, in one league.
const SEASON = [
  m("1", "2026-09-06", "KB", "CU", 2, 0),
  m("2", "2026-09-13", "CU", "XX", 1, 1),
  m("3", "2026-09-20", "YY", "KB", 0, 0),
  m("4", "2026-09-27", "KB", "XX", 3, 1),
  m("5", "2026-10-04", "CU", "YY", 0, 2),
  m("6", "2026-10-11", "XX", "KB", 1, 0),
  m("7", "2026-10-18", "CU", "KB", 1, 1, { status: "awarded" }),
  m("8", "2026-10-25", "YY", "CU", 1, 3),
  // Not counted: a fixture, a postponed one, a placeholder.
  m("9", "2026-11-01", "KB", "YY", null, null, { status: "scheduled" }),
  m("10", "2026-10-26", "KB", "YY", 1, 0, { status: "postponed" }),
  m("11", "2026-10-27", "XX", "CU", 9, 0, { source_type: "placeholder" }),
];
const MATCH = { match_id: "12", date: "2026-11-08", home_team_id: "KB", away_team_id: "CU" };

test("only what was played before the match counts, oldest first", () => {
  const before = playedBefore(SEASON, MATCH);
  assert.deepEqual(before.map((x) => x.match_id), ["1", "2", "3", "4", "5", "6", "7", "8"]);
  // Drawn after the whistle, a board still describes the match going in:
  // the match itself and anything on its day are left out.
  const played = { ...MATCH, status: "played", home_goals: 4, away_goals: 0 };
  const sameDay = m("13", "2026-11-08", "XX", "YY", 1, 0);
  assert.deepEqual(playedBefore([...SEASON, played, sameDay], played).length, 8);
});

test("a team's record from its scores", () => {
  const kb = teamRecord(playedBefore(SEASON, MATCH), "KB");
  assert.deepEqual(
    [kb.played, kb.won, kb.drawn, kb.lost, kb.gf, kb.ga, kb.cleanSheets, kb.failedToScore],
    [5, 2, 2, 1, 6, 3, 2, 2]);
  assert.deepEqual(kb.home, { played: 2, won: 2, drawn: 0, lost: 0 });
  assert.deepEqual(kb.away, { played: 3, won: 0, drawn: 2, lost: 1 });
});

test("the table follows the site's tiebreaks, adjustments and clusters", () => {
  const before = playedBefore(SEASON, MATCH);
  const entries = ["KB", "CU", "XX", "YY", "ZZ"].map((id) => ({ team_id: id, name: id }));
  const t = leagueTable(before, entries);
  // KB 8, CU 5, YY 4 (GD 0), XX 4 (GD -1): goal difference splits the last two.
  assert.equal(t.KB.points, 8);
  assert.equal(t.KB.position, 1);
  assert.equal(t.ZZ.played, 0);           // in the table with zeros
  assert.equal(t.ZZ.position, 5);
  assert.deepEqual([t.CU.position, t.YY.position, t.XX.position], [2, 3, 4]);
  // A deduction moves a team; a withdrawn one is not ranked.
  const docked = leagueTable(before, entries.map((e) => (e.team_id === "KB"
    ? { ...e, points_adjustment: -6 } : e.team_id === "ZZ" ? { ...e, status: "withdrawn" } : e)));
  assert.equal(docked.KB.points, 2);
  assert.equal(docked.ZZ, undefined);
  // Clusters: a position is a position inside one.
  const grouped = leagueTable(before, entries.map((e) => ({
    ...e, group: ["KB", "CU"].includes(e.team_id) ? "Cluster A" : "Cluster B" })));
  assert.deepEqual([grouped.KB.position, grouped.CU.position, grouped.KB.size], [1, 2, 2]);
  // Custom points per win (competition_seasons).
  assert.equal(leagueTable(before, entries, { points_win: 2, points_draw: 1 }).KB.points, 6);
});

test("form is the last five, oldest first, with who and where", () => {
  const before = playedBefore(SEASON, MATCH);
  const cu = form(before, "CU");
  assert.deepEqual(cu.map((f) => f.result), ["L", "D", "L", "D", "W"]);
  assert.deepEqual(cu[4], { result: "W", gf: 3, ga: 1, opponentId: "YY", home: false,
                            date: "2026-10-25" });
  assert.equal(formPoints(cu), 5);
  assert.equal(form(before, "KB", 3).length, 3);
  assert.deepEqual(form([], "KB"), []);
});

test("head to head reads either way round, newest meeting last", () => {
  const before = playedBefore(SEASON, MATCH);
  const h2h = headToHead(before, "KB", "CU");
  assert.deepEqual([h2h.played, h2h.aWins, h2h.draws, h2h.bWins], [2, 1, 1, 0]);
  assert.deepEqual(h2h.last, { aGoals: 1, bGoals: 1, aHome: false, date: "2026-10-18",
                               competitionId: "" });
  assert.deepEqual(headToHeadLines(h2h, "KB", "CU"),
    { tally: "KB 1  ·  1 draw  ·  CU 0", last: "CU 1–1 KB", lastDate: "2026-10-18" });
  assert.equal(headToHeadLines(headToHead(before, "KB", "ZZ"), "KB", "ZZ"), null);
});

test("a top scorer only when every goal has a scorer", () => {
  const goals = [
    { match_id: "1", team_id: "KB", player_id: "P1", name: "Kenson Mlenga" },
    { match_id: "1", team_id: "KB", player_id: "P1", name: "Kenson Mlenga" },
    { match_id: "4", team_id: "KB", player_id: "P2", name: "John Banda" },
    { match_id: "4", team_id: "KB", player_id: "P1", name: "Kenson Mlenga" },
    // An own goal FOR KB covers a goal and scores for nobody here.
    { match_id: "4", team_id: "KB", player_id: "P9", name: "Opponent", goal_type: "own_goal" },
    { match_id: "7", team_id: "KB", player_id: "", name: "Hope Namadzunda" },
  ];
  assert.deepEqual(topScorer(goals, "KB", 6),
    { names: ["Kenson Mlenga"], joint: 1, goals: 3 });
  // One goal with no row: no claim at all.
  assert.equal(topScorer(goals.slice(1), "KB", 6), null);
  assert.equal(topScorer([], "KB", 0), null);
  // Joint tops are named, two at most.
  const joint = [{ team_id: "CU", player_id: "A", name: "Zed" },
                 { team_id: "CU", player_id: "B", name: "Abe" }];
  assert.deepEqual(topScorer(joint, "CU", 2), { names: ["Abe", "Zed"], joint: 2, goals: 1 });
  const three = [...joint, { team_id: "CU", player_id: "C", name: "Cy" }];
  assert.deepEqual(topScorer(three, "CU", 3), { names: [], joint: 3, goals: 1 });
  // Only the period's matches.
  assert.equal(topScorer(goals, "KB", 2, ["1"]).goals, 2);
});

test("stat rows: the better side, lower-is-better, and no table for a cup", () => {
  const before = playedBefore(SEASON, MATCH);
  const entries = ["KB", "CU", "XX", "YY"].map((id) => ({ team_id: id, name: id }));
  const table = leagueTable(before, entries);
  const side = (id) => ({ record: teamRecord(before, id), table: table[id] });
  const rows = statRows({ home: side("KB"), away: side("CU") },
    ["position", "points", "record", "goals_for", "goals_against", "goal_diff",
     "clean_sheets", "failed_to_score", "venue_record", "ppg"]);
  const by = Object.fromEntries(rows.map((r) => [r.key, r]));
  assert.deepEqual([by.position.home, by.position.away, by.position.better], ["1st", "2nd", "home"]);
  assert.equal(by.position.homeValue, null);         // a rank is not a bar
  assert.deepEqual([by.points.home, by.points.away], ["8", "5"]);
  assert.deepEqual([by.record.home, by.record.away], ["2-2-1", "1-2-2"]);
  assert.equal(by.goals_against.better, "home");      // 3 conceded beats 7
  assert.equal(by.goal_diff.home, "+3");
  assert.equal(by.goal_diff.away, "-2");
  assert.deepEqual([by.venue_record.home, by.venue_record.away], ["2-0-0", "1-0-1"]);
  assert.deepEqual([by.ppg.home, by.ppg.away], ["1.60", "1.00"]);
  // The order is COMPARE_ROWS', whatever order the keys were ticked in.
  assert.deepEqual(statRows({ home: side("KB"), away: side("CU") }, ["goals_for", "points"])
    .map((r) => r.key), ["points", "goals_for"]);
  // A cup: no table, so no position and no points — and nothing invented.
  const cup = statRows({ home: { record: teamRecord(before, "KB"), table: null },
                         away: { record: teamRecord(before, "CU"), table: null } },
                       ["position", "points", "goals_for"]);
  assert.deepEqual(cup.map((r) => r.key), ["goals_for"]);
  // Level is level.
  const lvl = statRows({ home: side("KB"), away: side("KB") }, ["goals_for"]);
  assert.equal(lvl[0].better, "");
});

test("buildCompare: one call from fetched rows to what the boards draw", async () => {
  const { buildCompare, shortDate } = await import("../../static/report/compare.js");
  const names = { KB: "Kamuzu Barracks", CU: "Chitipa United", XX: "Ex", YY: "Why" };
  const entries = Object.keys(names).map((id) => ({ team_id: id, name: names[id] }));
  const out = buildCompare({
    match: MATCH, matches: SEASON, h2hMatches: [m("0", "2025-03-01", "CU", "KB", 0, 2),
      ...SEASON.filter((x) => ["1", "7"].includes(x.match_id))],
    entries, rules: { points_win: 3, points_draw: 1 },
    goals: [{ match_id: "8", team_id: "CU", player_id: "P", name: "Gift Magola" }],
    teamName: (id) => names[id] || id,
  });
  assert.deepEqual(out.rows.map((r) => r.key),
    ["position", "points", "record", "goals_for", "goals_against", "clean_sheets"]);
  assert.equal(out.form.home.at(-1).opponent, "Chitipa United");
  assert.equal(out.form.home.at(-1).dateLabel, "18 Oct");
  assert.equal(out.summaries.away, "5 pts from 15");
  // CU scored 5 and one has a scorer row: no top scorer for either side.
  assert.equal(out.topScorer, null);
  assert.deepEqual(out.h2h, { tally: "Kamuzu Barracks 2  ·  1 draw  ·  Chitipa United 0",
    last: "Last meeting: Chitipa United 1–1 Kamuzu Barracks  ·  18 Oct" });
  assert.equal(shortDate("2025-03-01", "2026"), "1 Mar 2025");
  // Unticked is absent, and a cup has no table.
  const cup = buildCompare({ match: MATCH, matches: SEASON, h2hMatches: [], entries: [],
    keys: ["position", "goals_for", "form"], teamName: (id) => id });
  assert.deepEqual(cup.rows.map((r) => r.key), ["goals_for"]);
  assert.equal(cup.h2h, null);
  assert.ok(cup.form);
});
