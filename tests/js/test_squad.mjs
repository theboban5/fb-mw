/* A team's squad and staff, from however the list arrived (0057).
 *
 *     node --test 'tests/js/*.mjs'            (or: npm test)
 *
 * The cases are the shapes a squad actually comes in: a club website's page
 * copied with its headings, a table with a tab between columns, a signing
 * graphic's three names, a staff list with the title on the line under the
 * name (Karonga United's /team page, the one that prompted this).
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  ageFrom, ageLabel, birthYearFromAge, dateFrom, dedupeLines, parseSquadList,
  parseStaffList, positionFrom, resolveSquadName, sortSquad,
} from "../../static/report/squad.js";

const TODAY = new Date(2026, 9, 21);   // 21 Oct 2026, local time

// ── Pieces ───────────────────────────────────────────────────────────────────

test("a printed position is one of the four, or nothing", () => {
  assert.equal(positionFrom("Goalkeeper"), "GK");
  assert.equal(positionFrom("DEFENDERS"), "DF");
  assert.equal(positionFrom("(MF)"), "MF");
  assert.equal(positionFrom("Striker"), "FW");
  // A winger is a midfielder at one club and a forward at the next.
  assert.equal(positionFrom("Winger"), "");
  assert.equal(positionFrom("Banda"), "");
});

test("a date of birth is read day first, and an impossible one is no date", () => {
  assert.equal(dateFrom("04/05/2003"), "2003-05-04");
  assert.equal(dateFrom("2003-05-04"), "2003-05-04");
  assert.equal(dateFrom("4 May 2003"), "2003-05-04");
  assert.equal(dateFrom("DoB: 12th April 2001"), "2001-04-12");
  assert.equal(dateFrom("31/02/2003"), "");
  assert.equal(dateFrom("23"), "");
});

test("an age is a footballer's age or not an age at all", () => {
  assert.equal(ageFrom("23"), 23);
  assert.equal(ageFrom("Age 23"), 23);
  assert.equal(ageFrom("23 yrs"), 23);
  assert.equal(ageFrom("7"), null);        // a shirt, not a seven-year-old
  assert.equal(ageFrom("2003"), null);
});

test("an age becomes a year, and the label says which kind of fact it is", () => {
  assert.equal(birthYearFromAge(23, TODAY), 2003);
  assert.equal(ageLabel({ dob: "2003-05-04" }, TODAY), "23");
  // Birthday still to come this year.
  assert.equal(ageLabel({ dob: "2003-12-25" }, TODAY), "22");
  assert.equal(ageLabel({ birth_year: 2003 }, TODAY), "about 23");
  assert.equal(ageLabel({}, TODAY), "");
});

// ── Squad lists ──────────────────────────────────────────────────────────────

test("a club page's squad keeps each heading's position until the next", () => {
  const rows = parseSquadList(`Goalkeepers
1. Charles Thom
30 Brave Kumwenda

Defenders (2)
2 Gomezgani Chirwa
5. Lloyd Aaron (C)

Midfielders:
8 Chikumbutso Salima`);
  assert.deepEqual(rows.map((r) => [r.shirt_number, r.name, r.position]), [
    ["1", "Charles Thom", "GK"],
    ["30", "Brave Kumwenda", "GK"],
    ["2", "Gomezgani Chirwa", "DF"],
    ["5", "Lloyd Aaron", "DF"],
    ["8", "Chikumbutso Salima", "MF"],
  ]);
});

test("a table copied with tabs reads each column for what it is", () => {
  const [row] = parseSquadList("9\tPatrick Phiri\tForward\t24\tMalawian");
  assert.deepEqual(row, {
    name: "Patrick Phiri", shirt_number: "9", position: "FW", dob: "", age: 24,
  });
  const [dated] = parseSquadList("Patrick Phiri | Striker | 04/05/2002");
  assert.equal(dated.dob, "2002-05-04");
  assert.equal(dated.position, "FW");
});

test("a single line takes its affixes off the name and leaves the name alone", () => {
  assert.deepEqual(parseSquadList("7. John Banda MF")[0],
    { name: "John Banda", shirt_number: "7", position: "MF", dob: "", age: null });
  assert.deepEqual(parseSquadList("John Banda (GK) (23)")[0],
    { name: "John Banda", shirt_number: "", position: "GK", dob: "", age: 23 });
  // A bare trailing number is not read as an age — it may be a shirt.
  assert.equal(parseSquadList("John Banda 23")[0].age, null);
  // A hyphen inside a name is part of it.
  assert.equal(parseSquadList("O'Neil-Banda")[0].name, "O'Neil-Banda");
});

test("three names off a signing graphic are three rows with nothing else", () => {
  const rows = parseSquadList("Gift Phiri\nStain Davie\n\nPeter Banda\n");
  assert.deepEqual(rows.map((r) => r.name), ["Gift Phiri", "Stain Davie", "Peter Banda"]);
  assert.ok(rows.every((r) => !r.shirt_number && !r.position && r.age === null));
});

test("the same name twice in one paste is one row", () => {
  const rows = dedupeLines(parseSquadList("John Banda\n7 john  banda\nPeter Banda"));
  assert.deepEqual(rows.map((r) => r.name), ["John Banda", "Peter Banda"]);
});

// ── Staff lists ──────────────────────────────────────────────────────────────

test("a website's staff list puts each title on the person above it", () => {
  const rows = parseStaffList(`Audlow Makonyola
Head Coach
Chris Chimkwita
Assistant Coach
Fortune Mhango
Second Assistant Coach
Fred Chiwambo
Team Doctor
Elick Elalio
Media & Communication Officer`);
  assert.deepEqual(rows, [
    { name: "Audlow Makonyola", role: "Head Coach" },
    { name: "Chris Chimkwita", role: "Assistant Coach" },
    { name: "Fortune Mhango", role: "Second Assistant Coach" },
    { name: "Fred Chiwambo", role: "Team Doctor" },
    { name: "Elick Elalio", role: "Media & Communication Officer" },
  ]);
});

test("a staff line written either way round reads the same", () => {
  assert.deepEqual(parseStaffList("Donald Zgambo - Goalkeeper Trainer"),
    [{ name: "Donald Zgambo", role: "Goalkeeper Trainer" }]);
  assert.deepEqual(parseStaffList("Fitness Coach: Lloyd Munthali"),
    [{ name: "Lloyd Munthali", role: "Fitness Coach" }]);
  // A title above the name waits for it.
  assert.deepEqual(parseStaffList("Head Coach\nAudlow Makonyola"),
    [{ name: "Audlow Makonyola", role: "Head Coach" }]);
  // A name with no title is still somebody.
  assert.deepEqual(parseStaffList("Lloyd Munthali"),
    [{ name: "Lloyd Munthali", role: "" }]);
});

// ── Who a name is ────────────────────────────────────────────────────────────

const TEAM = "MW_KU_M1";

test("someone already on the list is updated, not added twice", () => {
  const r = resolveSquadName("J. Banda", {
    teamId: TEAM, registered: [{ player_id: "P1", name: "John Banda" }],
  });
  assert.deepEqual(r, { kind: "squad", player_id: "P1", name: "John Banda" });
});

test("a player this team has fielded is linked by findPerson's rule", () => {
  const r = resolveSquadName("John Banda", {
    teamId: TEAM, played: [{ player_id: "P2", name: "J. Banda" }],
  });
  assert.equal(r.kind, "linked");
  assert.equal(r.player_id, "P2");
});

test("a search hit is linked only when it has played for THIS team", () => {
  const ours = { player_id: "P3", full_name: "Gift Phiri", team_ids: [TEAM] };
  const theirs = { player_id: "P4", full_name: "Gift Phiri", teams: "Chizumulu United",
                   team_ids: ["MW_CU_M1"] };
  assert.equal(resolveSquadName("Gift Phiri",
    { teamId: TEAM, candidates: [ours, theirs] }).player_id, "P3");

  // The same name at another club is offered, never chosen — and "new" next to
  // an exact match must be forced or create_player would link to them anyway.
  const r = resolveSquadName("Gift Phiri", { teamId: TEAM, candidates: [theirs] });
  assert.equal(r.kind, "choose");
  assert.equal(r.player_id, "");
  assert.equal(r.force, true);
  assert.deepEqual(r.options,
    [{ player_id: "P4", name: "Gift Phiri", teams: "Chizumulu United" }]);
});

test("two of this team's people under one surname are no answer", () => {
  const r = resolveSquadName("Banda", {
    teamId: TEAM,
    played: [{ player_id: "P5", name: "Peter Banda" }, { player_id: "P6", name: "Paul Banda" }],
  });
  assert.notEqual(r.kind, "linked");
});

test("nobody like them anywhere is a new player", () => {
  assert.deepEqual(resolveSquadName("Stain Davie", { teamId: TEAM }),
    { kind: "new", player_id: "" });
});

test("a squad sorts by line, then shirt, and keeps the unlabelled", () => {
  const rows = sortSquad([
    { name: "Zed", position: "", shirt_number: "" },
    { name: "Forward", position: "FW", shirt_number: "9" },
    { name: "Keeper two", position: "GK", shirt_number: "30" },
    { name: "Keeper one", position: "GK", shirt_number: "1" },
    { name: "No shirt", position: "DF", shirt_number: "" },
    { name: "Back", position: "DF", shirt_number: "4" },
  ]);
  assert.deepEqual(rows.map((r) => r.name),
    ["Keeper one", "Keeper two", "Back", "No shirt", "Forward", "Zed"]);
});
