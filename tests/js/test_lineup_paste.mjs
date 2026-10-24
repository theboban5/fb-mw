/* A pasted team sheet, the way FDH Bank Premiership clubs actually post one.
 *
 *     node --test 'tests/js/*.mjs'            (or: npm test)
 *
 * The three texts below are verbatim from the request that prompted this: a
 * matchday graphic's text (Ekhaya v Blue Eagles), a written XI (Bullets), and
 * a club's full report with the substitutions inside the starters' brackets
 * (MAFCO 0-3 Bullets, 18 Oct 2026).
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  displayName, fullerName, matchRowsToSquad, motmFrom, parseEntry, parseLineup,
  parseTeamSheet, routeBlocks,
} from "../../static/report/lineup_paste.js";
import { personCandidates, sameTeam } from "../../static/report/match_report.js";

const GRAPHIC = `KO 15:00 HRS
XI
31. MKUNGULA
17. MASINJA
19.MPHEPO
29.  RUKURA
14. LAMECK (C)
21. MAFAITI
4.  SALAWIRA
47. MALINDA
23. KAPONYA
20. CHIHANA
22.  MCHEMA
SUBS
01.SANDE 3 12. LUNGU 42. CHIZINGA
16. SAIWA 8. M. B ANDA 7. NYIRENDA
9. STAMBULI 30. KUMWEMBE, 05. SIMBI`;

const WRITTEN = `Bullets XI: Maclean Mwale; Yamikani Mologeni, Blessings Mpokera (C), Emmanuel Kaunga, Prince Chiumia; Yankho Singo, Chawanangwa Gumbo, Wongani Lungu; Clever Kalambo, Peter Banda; Ephraim Kondowe.

Substitutes: Pilirani Mapira (GK), Alihaji Mnjale, Kondwani Tebulo, Frank Willard, Paul Master, Misheck Billiat, Tinaye Tsimba, Maxwell Phodo, George Chaomba.`;

const REPORT = `FCB Nyasa Big Bullets XI

24 Innocent Nyasulu; 2 Yamikani Mologeni, 4 Blessings Mpokera (C), 26 Emmanuel Kaunga, 31 Prince Chiumia; 17 Yankho Singo, 29 Chawanangwa Gumbo (8 Paul Master 46’), 10 Wongani Lungu (22 George Chaomba 61’); 11 Clever Kalambo (19 Tinaye Tsimba 77’), 28 Misheck Billiat; 9 Ephraim Kondowe (6 Frank Willard 90+1’).

Substitutes not used

23 McLean Mwale, 52 Kondwani Tebulo, 27 Alihaji Mnjale, 5 Sheku Sheriff, 20 Maxwell Phodo.

MAFCO XI

99 Owen Mbale; 14 Paul Ndhlovu (C), Geoffrey Kadzitche (16 Ben Mbewe 77’), 3 Duncan Mwale, 12 Awalu Alli (27 Henry Mulaleya 63’), 18 Vitumbiko Phiri, 45 Clifford Msowoya (2 Zikani Sichinga 63’), 47 Prince Kachere (11 Chifundo Moloson 46’), 70 Francis Black, 78 Lumbani Mkandawire (22 Alexander Sukwambe 82’), 90 Innocent Makawano.

Substitutes not used

5 Wakisa Kalinga, 7 Kingsley Nkhonjera, 41 Ronald Mussa, 71 Benjamin Msaku.

Match Statistics

Goals: Clever Kalambo 13’, Tinaye Tsimba 87’, 90+4’ (Bullets).

Bookings: Duncan Mwale 73’ (MAFCO); Tinaye Tsimba 78’ (Bullets).

Player of the Match: Tinaye Tsimba (Bullets).

Match Details

Fixture: MAFCO vs FCB Nyasa Big Bullets
Competition: FDH Bank Premiership
Date: 18 October 2026
Venue: Civo Stadium, Lilongwe
Result: MAFCO 0-3 Bullets.`;

const byRole = (rows, role) => rows.filter((r) => r.role === role);
const names = (rows) => rows.map((r) => r.player_name);

// ── Reading ──────────────────────────────────────────────────────────────────

test("a graphic's capitals are typography, not spelling", () => {
  assert.equal(displayName("MKUNGULA"), "Mkungula");
  assert.equal(displayName("BANDA JNR"), "Banda Jnr");
  assert.equal(displayName("M. B ANDA"), "M. B Anda");
  assert.equal(displayName("McLean Mwale"), "McLean Mwale");
});

test("a matchday graphic: numbered XI, then a bench run together on lines", () => {
  const blocks = parseLineup(GRAPHIC);
  assert.equal(blocks.length, 1);
  const rows = blocks[0].rows;
  assert.equal(byRole(rows, "starting").length, 11);
  assert.deepEqual(byRole(rows, "starting").slice(0, 5).map((r) => [r.shirt_number, r.player_name]), [
    ["31", "Mkungula"], ["17", "Masinja"], ["19", "Mphepo"], ["29", "Rukura"],
    ["14", "Lameck"],
  ]);
  assert.equal(rows.find((r) => r.player_name === "Lameck").captain, true);
  assert.deepEqual(byRole(rows, "unused_sub").map((r) => [r.shirt_number, r.player_name]), [
    ["1", "Sande 3"], ["12", "Lungu"], ["42", "Chizinga"],
    ["16", "Saiwa"], ["8", "M. B Anda"], ["7", "Nyirenda"],
    ["9", "Stambuli"], ["30", "Kumwembe"], ["5", "Simbi"],
  ]);
});

test("a written XI: semicolons between lines, commas between players", () => {
  const [block, ...rest] = parseLineup(WRITTEN);
  assert.equal(rest.length, 0);
  assert.equal(block.heading, "Bullets");
  assert.deepEqual(names(byRole(block.rows, "starting")), [
    "Maclean Mwale", "Yamikani Mologeni", "Blessings Mpokera", "Emmanuel Kaunga",
    "Prince Chiumia", "Yankho Singo", "Chawanangwa Gumbo", "Wongani Lungu",
    "Clever Kalambo", "Peter Banda", "Ephraim Kondowe",
  ]);
  const bench = byRole(block.rows, "unused_sub");
  assert.equal(bench.length, 9);
  assert.deepEqual([bench[0].player_name, bench[0].position], ["Pilirani Mapira", "GK"]);
  assert.equal(block.rows.find((r) => r.player_name === "Blessings Mpokera").captain, true);
});

test("a club report: two teams, substitutions in brackets, no statistics", () => {
  const blocks = parseLineup(REPORT);
  assert.deepEqual(blocks.map((b) => b.heading), ["FCB Nyasa Big Bullets", "MAFCO"]);
  const [bullets, mafco] = blocks.map((b) => b.rows);

  assert.equal(byRole(bullets, "starting").length, 11);
  assert.equal(byRole(bullets, "unused_sub").length, 5);
  assert.deepEqual(byRole(bullets, "sub_on").map((r) =>
    [r.shirt_number, r.player_name, r.minute_on, r.replaced_player]), [
    ["8", "Paul Master", "46", "Chawanangwa Gumbo"],
    ["22", "George Chaomba", "61", "Wongani Lungu"],
    ["19", "Tinaye Tsimba", "77", "Clever Kalambo"],
    ["6", "Frank Willard", "90+1", "Ephraim Kondowe"],
  ]);
  // The starter keeps his own shirt and is not renamed by the bracket.
  const gumbo = bullets.find((r) => r.player_name === "Chawanangwa Gumbo");
  assert.deepEqual([gumbo.shirt_number, gumbo.role], ["29", "starting"]);

  assert.equal(byRole(mafco, "starting").length, 11);
  assert.equal(byRole(mafco, "sub_on").length, 5);
  assert.equal(byRole(mafco, "unused_sub").length, 4);
  // A starter with no number is still a starter.
  assert.equal(mafco.find((r) => r.player_name === "Geoffrey Kadzitche").shirt_number, "");

  // Nothing from Match Statistics or Match Details became a player.
  const all = names([...bullets, ...mafco]).join(" | ");
  assert.doesNotMatch(all, /Goals|Bookings|Civo|Fixture|Result|Competition/);
  assert.equal(motmFrom(REPORT), "Tinaye Tsimba");
});

test("the old one-a-line paste reads exactly as it did", () => {
  const rows = parseTeamSheet(`1 Mercy Sikelo GK (C)
5 Sabina Thom MF [Y]
Subs:
14 Rachel Kundananji 62' for Sabina Thom
Temwa Chawinga`);
  assert.deepEqual(rows.map((r) => [r.shirt_number, r.player_name, r.position, r.role]), [
    ["1", "Mercy Sikelo", "GK", "starting"],
    ["5", "Sabina Thom", "MF", "starting"],
    ["14", "Rachel Kundananji", "", "sub_on"],
    ["", "Temwa Chawinga", "", "unused_sub"],
  ]);
  assert.equal(rows[0].captain, true);
  assert.equal(rows[1].yellow_card, true);
  assert.deepEqual([rows[2].minute_on, rows[2].replaced_player], ["62", "Sabina Thom"]);
});

test("an entry with a number after the name takes it as the shirt", () => {
  const [row] = parseEntry("MWALE 23");
  assert.deepEqual([row.shirt_number, row.player_name], ["23", "Mwale"]);
  // …unless a number came first, in which case the trailing one is the name's.
  assert.equal(parseEntry("01.SANDE 3")[0].player_name, "Sande 3");
});

// ── Who they are ─────────────────────────────────────────────────────────────

const SQUAD = [
  { player_id: "P1", name: "McLean Mwale", shirt_number: "23" },
  { player_id: "P2", name: "Duncan Mwale", shirt_number: "3" },
  { player_id: "P3", name: "Clement Mkungula", shirt_number: "31" },
  { player_id: "P4", name: "Peter Banda", shirt_number: "8" },
  { player_id: "P5", name: "John Banda Jnr", shirt_number: "13" },
  { player_id: "", name: "Known only by name", shirt_number: "" },
];

const row = (shirt, name) => ({ shirt_number: shirt, player_name: name });

test("a surname alone links when the squad has one of them", () => {
  const [m] = matchRowsToSquad([row("31", "Mkungula")], SQUAD);
  assert.equal(m.kind, "squad");
  assert.equal(m.player.player_id, "P3");
});

test("two of one surname: the printed shirt settles it, nothing else does", () => {
  assert.equal(matchRowsToSquad([row("23", "Mwale")], SQUAD)[0].player.player_id, "P1");
  const unsure = matchRowsToSquad([row("", "Mwale")], SQUAD)[0];
  assert.equal(unsure.kind, "choose");
  assert.deepEqual(unsure.options.map((p) => p.player_id), ["P1", "P2"]);
  // A shirt that is neither of theirs is no tiebreak at all.
  assert.equal(matchRowsToSquad([row("99", "Mwale")], SQUAD)[0].kind, "choose");
});

test("one player, one row — and the second Mwale resolves once the first has", () => {
  const ms = matchRowsToSquad([row("", "Mwale"), row("23", "Mwale")], SQUAD);
  assert.deepEqual(ms.map((m) => m.player?.player_id), ["P2", "P1"]);
  const twice = matchRowsToSquad([row("31", "Mkungula"), row("", "C. Mkungula")], SQUAD);
  assert.deepEqual(twice.map((m) => m.kind), ["squad", "none"]);
});

test("a suffix finds the surname and then prefers whoever carries it", () => {
  assert.deepEqual(personCandidates("BANDA JNR", SQUAD).map((p) => p.player_id), ["P5"]);
  assert.deepEqual(personCandidates("Banda", SQUAD).map((p) => p.player_id), ["P4", "P5"]);
  // Nobody in the squad carries "Jnr": the graphic added it.
  assert.equal(personCandidates("Mkungula Jnr", SQUAD)[0].player_id, "P3");
});

test("a name the squad cannot place is 'none', and a nameless entry links nobody", () => {
  assert.equal(matchRowsToSquad([row("4", "Salawira")], SQUAD)[0].kind, "none");
  assert.equal(matchRowsToSquad([row("4", "Known only by name")], SQUAD)[0].kind, "none");
});

test("a fuller printed name is offered as a rename, never the other way", () => {
  assert.equal(fullerName("Joseph Banda", "Banda"), true);
  assert.equal(fullerName("Joseph Banda", "J. Banda"), true);
  assert.equal(fullerName("J. Banda", "Joseph Banda"), false);
  assert.equal(fullerName("Peter Banda", "John Banda"), false);
  assert.equal(fullerName("Banda", "Banda"), false);
});

// ── Which side ───────────────────────────────────────────────────────────────

const SIDES = [
  { key: "MW_MAFCO", names: ["MAFCO FC"] },
  { key: "MW_NBB", names: ["Nyasa Big Bullets"] },
];

test("a report's two blocks go to the sides their headings name", () => {
  const blocks = parseLineup(REPORT);
  // "MAFCO" is too short for sameTeam to trust; the Bullets heading places
  // itself and the other block takes the side that is left.
  assert.deepEqual(routeBlocks(blocks, SIDES, "MW_MAFCO", sameTeam), ["MW_NBB", "MW_MAFCO"]);
});

test("a lone block with nothing to go by is the box it was pasted into", () => {
  assert.deepEqual(routeBlocks(parseLineup(GRAPHIC), SIDES, "MW_MAFCO", sameTeam), ["MW_MAFCO"]);
  // …but one whose heading names the other side goes there.
  assert.deepEqual(routeBlocks(parseLineup(WRITTEN), SIDES, "MW_MAFCO", sameTeam), ["MW_NBB"]);
});

test("two blocks that both claim one side are not trusted", () => {
  const blocks = [{ heading: "Bullets", rows: [] }, { heading: "Nyasa Big Bullets", rows: [] }];
  assert.deepEqual(routeBlocks(blocks, SIDES, "MW_MAFCO", sameTeam), ["", ""]);
});
