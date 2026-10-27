/* #/stats' arithmetic, tested without a browser.
 *
 *     node --test tests/js/            (or: npm test)
 *
 * The questions here are the ones a wrong answer gets posted publicly for:
 * which dates "last weekend" means on each day of the week, whether a 0-0 is
 * two clean sheets, and whether a red-card total built from two team sheets
 * is ever allowed to read as the weekend's. stats.js imports nothing, which
 * is what keeps these honest.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  periodRange, defaultPeriod, formatRange, summarize, byCompetition, byTeam,
  topTeams, byReferee, topMatches, headlines, scopeText,
} from "../../static/report/stats.js";

// A row as stats_match_facts (0062) returns it.
const fact = (h, a, over = {}) => ({
  match_id: "M1", public_id: "p1",
  competition_id: "MW_SL", competition_name: "FDH Bank Premiership",
  season_id: "MW_2026_27", date: "2026-10-24", matchday: 15,
  home_team_id: "MW_BE", away_team_id: "MW_SIL",
  home_name: "Blue Eagles", away_name: "Silver Strikers",
  home_goals: h, away_goals: a,
  home_sheet: false, away_sheet: false,
  home_yellow: 0, away_yellow: 0, home_second_yellow: 0, away_second_yellow: 0,
  home_red: 0, away_red: 0,
  goal_rows: 0, g_penalty: 0, g_free_kick: 0, g_header: 0, g_open_play: 0,
  g_own_goal: 0, g_untyped: 0,
  referee: null, referee_id: null, referee_name: null,
  ...over,
});

// ── Periods ──────────────────────────────────────────────────────────────────
// 2026-10-23 is a Friday.

test("last weekend on a Tuesday is the Friday–Sunday just played", () => {
  const p = periodRange("last-weekend", "2026-10-27");
  assert.deepEqual([p.from, p.to], ["2026-10-23", "2026-10-25"]);
  assert.equal(p.label, "Fri 23 – Sun 25 Oct");
});

test("this weekend on a Tuesday is the one coming", () => {
  const p = periodRange("this-weekend", "2026-10-27");
  assert.deepEqual([p.from, p.to], ["2026-10-30", "2026-11-01"]);
  assert.equal(p.label, "Fri 30 Oct – Sun 1 Nov");
});

test("on Friday, Saturday and Sunday, this weekend contains today", () => {
  for (const day of ["2026-10-23", "2026-10-24", "2026-10-25"]) {
    const p = periodRange("this-weekend", day);
    assert.deepEqual([p.from, p.to], ["2026-10-23", "2026-10-25"], day);
    const last = periodRange("last-weekend", day);
    assert.deepEqual([last.from, last.to], ["2026-10-16", "2026-10-18"], day);
  }
});

test("on Monday, last weekend is the one that ended yesterday", () => {
  const p = periodRange("last-weekend", "2026-10-26");
  assert.deepEqual([p.from, p.to], ["2026-10-23", "2026-10-25"]);
});

test("the screen opens on the weekend people are asking about", () => {
  assert.equal(defaultPeriod("2026-10-26"), "last-weekend");   // Mon
  assert.equal(defaultPeriod("2026-10-29"), "last-weekend");   // Thu
  assert.equal(defaultPeriod("2026-10-30"), "this-weekend");   // Fri
  assert.equal(defaultPeriod("2026-11-01"), "this-weekend");   // Sun
});

test("months: this month to today, last month whole, across a year", () => {
  const t = periodRange("this-month", "2026-10-27");
  assert.deepEqual([t.from, t.to], ["2026-10-01", "2026-10-27"]);
  const l = periodRange("last-month", "2026-03-10");
  assert.deepEqual([l.from, l.to], ["2026-02-01", "2026-02-28"]);
  assert.equal(l.label, "February 2026");
  const jan = periodRange("last-month", "2027-01-05");
  assert.deepEqual([jan.from, jan.to], ["2026-12-01", "2026-12-31"]);
});

test("last 7 days includes today", () => {
  const p = periodRange("last-7", "2026-10-27");
  assert.deepEqual([p.from, p.to], ["2026-10-21", "2026-10-27"]);
});

test("a custom range must be whole and forwards, or there is no query", () => {
  assert.equal(periodRange("custom", "2026-10-27", { from: "2026-10-01" }), null);
  assert.equal(periodRange("custom", "2026-10-27",
                           { from: "2026-10-10", to: "2026-10-01" }), null);
  const p = periodRange("custom", "2026-10-27", { from: "2026-10-01", to: "2026-10-10" });
  assert.deepEqual([p.from, p.to], ["2026-10-01", "2026-10-10"]);
});

test("the season is a season, never a date range worked out from the clock", () => {
  const p = periodRange("season", "2026-10-27", { seasonLabel: "2026/27" });
  assert.equal(p.kind, "season");
  assert.equal(p.label, "2026/27 season");
});

test("a range crossing a year names both years", () => {
  assert.equal(formatRange("2026-12-28", "2027-01-03"),
               "Mon 28 Dec 2026 – Sun 3 Jan 2027");
  assert.equal(formatRange("2026-10-24", "2026-10-24"), "Sat 24 Oct");
});

// ── Sums ─────────────────────────────────────────────────────────────────────

test("a clean sheet belongs to a side, so a 0-0 is two", () => {
  const s = summarize([fact(0, 0), fact(2, 0), fact(1, 1)]);
  assert.equal(s.cleanSheets, 3);
  assert.equal(s.goals, 4);
  assert.equal(s.matches, 3);
  assert.equal(s.draws, 2);
  assert.equal(s.homeWins, 1);
});

test("cards count only where a sheet exists, and say how many sides that is", () => {
  const s = summarize([
    fact(1, 0, { home_sheet: true, away_sheet: true, home_yellow: 2, away_red: 1,
                 away_second_yellow: 1 }),
    fact(2, 2),
  ]);
  assert.equal(s.sheetSides, 2);
  assert.equal(s.sides, 4);
  assert.equal(s.sheetMatches, 1);
  assert.equal(s.yellow, 2);
  assert.equal(s.sentOff, 2);       // a second yellow is a sending-off too
});

test("goal types add up, untyped kept apart", () => {
  const s = summarize([fact(3, 1, { goal_rows: 4, g_penalty: 1, g_header: 1, g_untyped: 2 })]);
  assert.equal(s.types.g_penalty, 1);
  assert.equal(s.typed, 2);
  assert.equal(s.untyped, 2);
});

test("competitions group, most matches first", () => {
  const rows = [fact(1, 0), fact(2, 1),
                fact(0, 0, { competition_id: "MW_WOM", competition_name: "Women's" })];
  const g = byCompetition(rows);
  assert.deepEqual(g.map((c) => [c.id, c.matches, c.goals]),
                   [["MW_SL", 2, 4], ["MW_WOM", 1, 0]]);
});

test("teams: clean sheets and goals over the period, zero never ranks", () => {
  const teams = byTeam([fact(2, 0), fact(0, 0, { home_team_id: "MW_X", home_name: "X" })]);
  const cs = topTeams(teams, "cleanSheets");
  // All on one clean sheet: fewer games played ranks first (a better rate),
  // then the name, so the order never shuffles between loads.
  assert.deepEqual(cs.map((t) => [t.id, t.cleanSheets]),
                   [["MW_BE", 1], ["MW_X", 1], ["MW_SIL", 1]]);
  assert.ok(topTeams(teams, "gf").every((t) => t.gf > 0));
  assert.equal(teams.find((t) => t.id === "MW_SIL").played, 2);
});

test("referees: one line per id, unresolved names by spelling, sorted by cards", () => {
  const rows = [
    fact(1, 0, { referee_id: "OFF1", referee_name: "Mphatso Gondwe",
                 home_sheet: true, home_yellow: 3 }),
    fact(1, 1, { referee_id: "OFF1", referee_name: "Mphatso Gondwe" }),
    fact(0, 2, { referee_name: "J. Phiri", home_sheet: true, away_sheet: true,
                 home_yellow: 4, away_red: 1 }),
    fact(0, 0, { referee_name: "j. phiri " }),
    fact(3, 0),
  ];
  const refs = byReferee(rows);
  assert.deepEqual(refs.map((r) => [r.name, r.matches, r.cards]),
                   [["J. Phiri", 2, 5], ["Mphatso Gondwe", 2, 3]]);
  // Cards per match divides by matches WITH a sheet, so missing sheets do
  // not make a referee look lenient.
  assert.equal(refs[1].perMatch, 3);
});

test("a draw is never a biggest win; a 0-0 is never most goals", () => {
  const { biggestWins, mostGoals } = topMatches([fact(0, 0), fact(2, 2), fact(4, 1)]);
  assert.deepEqual(biggestWins.map((r) => [r.home_goals, r.away_goals]), [[4, 1]]);
  assert.deepEqual(mostGoals.map((r) => [r.home_goals, r.away_goals]), [[4, 1], [2, 2]]);
});

// ── Sentences ────────────────────────────────────────────────────────────────

test("scope: one name, two names, a count, and 'all' only when it is all", () => {
  assert.equal(scopeText({ names: ["FDH Bank Premiership"] }), "FDH Bank Premiership");
  assert.equal(scopeText({ names: ["A", "B"] }), "A and B");
  assert.equal(scopeText({ names: ["A", "B", "C"] }), "across 3 competitions");
  assert.equal(scopeText({ names: ["A", "B", "C"], allSelected: true }),
               "across all competitions");
});

test("the matchday sentence reads the way it is posted", () => {
  const rows = Array.from({ length: 8 }, (_, i) => fact(i % 4, 1, { match_id: `M${i}` }));
  const [goals] = headlines(summarize(rows),
                            { names: ["FDH Bank Premiership"], when: "matchday 15" });
  assert.equal(goals, "20 goals in 8 matches (2.50 a match) · FDH Bank Premiership · matchday 15");
});

test("singular nouns stay singular", () => {
  const lines = headlines(summarize([fact(1, 0)]), { names: ["X"] });
  assert.equal(lines[0], "1 goal in 1 match · X");
  assert.equal(lines[1], "1 clean sheet · X");
});

test("a card total is claimed only when every side has a sheet", () => {
  const partial = headlines(summarize([
    fact(1, 0, { home_sheet: true, away_sheet: true, away_red: 1 }),
    fact(0, 0),
  ]), { names: ["A", "B", "C"], allSelected: true, when: "Fri 23 – Sun 25 Oct" });
  assert.equal(partial[2], "0 yellow cards, 1 red card in the 1 match with a team sheet"
                           + " · across all competitions · Fri 23 – Sun 25 Oct");
  const whole = headlines(summarize([
    fact(1, 0, { home_sheet: true, away_sheet: true, away_red: 2, home_yellow: 1 }),
  ]), { names: ["A"] });
  assert.equal(whole[2], "1 yellow card, 2 red cards · A");
});

test("no sheets at all means no card sentence", () => {
  const lines = headlines(summarize([fact(1, 0)]), { names: ["A"] });
  assert.ok(lines.every((l) => !/card/.test(l)));
});

test("nothing played, nothing to say", () => {
  assert.deepEqual(headlines(summarize([]), { names: ["A"] }), []);
});
