/* A team sheet as a line-up graphic (static/report/pre_match.js).
 *
 *     node --test 'tests/js/*.mjs'            (or: npm test)
 *
 * The sheets below are from the first two FAM pre-match reports the site
 * received (FDH Bank Premiership, 1 Nov 2026: Blue Eagles v Mitundu Baptist,
 * match no 149, and Kamuzu Barracks v Chitipa United, 147), typed the way
 * sheetText writes them — the blank shirts, the "01" shirts, the capitals and
 * the wrapped three-word names are all theirs.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  applyFamResults, boardName, boardSide, boxesFromReport, famNote, famPayload, famRegNo,
  linkByFam, sheetText,
} from "../../static/report/pre_match.js";
import { sheetsFromReport } from "../../static/report/match_report.js";

const MITUNDU = `01 Emmanuel Kanthiti (GK)
14 Leo Kachikho (C)
Redson Nkhoma
Moses Shaba
03 Loyd Siyeni
04 Moses Nyirenda
06 EMMANUEL NYAKUTEPA
08 Limbani Mujahid Phiri
12 Rahim Mtondera
19 Emmanuel Chikadza
24 Tamandani Ngulube
Substitutes:
Fletchar Bandawe
Gama Dayson
02 Henderson Banda
5 Nicholas Kalondore
10 Precious Segula
11 Samson Phiri
23 Simeon Singa`;

test("a pre-match sheet reads as eleven starters and a bench", () => {
  const side = boardSide(MITUNDU);
  assert.equal(side.starting.length, 11);
  assert.equal(side.bench.length, 7);
  assert.deepEqual(side.starting[0], { shirt: "1", name: "KANTHITI", captain: false, gk: true });
  assert.deepEqual(side.starting[1], { shirt: "14", name: "KACHIKHO", captain: true, gk: false });
  // No shirt printed is no shirt drawn, never a guess.
  assert.equal(side.starting[2].shirt, "");
  assert.equal(side.starting[2].name, "NKHOMA");
});

test("capitals are the PDF's typography, not the name", () => {
  const side = boardSide(MITUNDU, "initial");
  assert.equal(side.starting[6].name, "E. Nyakutepa");
  assert.equal(boardName("EMMANUEL NYAKUTEPA", "full"), "Emmanuel Nyakutepa");
});

test("a shared surname takes its initial back on a surname board", () => {
  // Limbani Mujahid Phiri starts, Samson Phiri is on the bench: two PHIRIs.
  const side = boardSide(MITUNDU);
  assert.equal(side.starting[7].name, "L. PHIRI");
  assert.equal(side.bench[5].name, "S. PHIRI");
  // Moses Shaba and Moses Nyirenda share a FIRST name, which changes nothing.
  assert.equal(side.starting[3].name, "SHABA");
});

test("a three-word name is surnamed by its last word, a suffix kept with it", () => {
  assert.equal(boardName("Geoffrey Christian Maluwa"), "MALUWA");
  assert.equal(boardName("J. Issac Simfukwe", "initial"), "J. Simfukwe");
  assert.equal(boardName("Gift Banda Jnr"), "BANDA JNR");
  assert.equal(boardName("Gift Banda Jnr", "initial"), "G. Banda Jnr");
  assert.equal(boardName("Mkungula", "initial"), "Mkungula");
  assert.equal(boardName("", "full"), "");
});

test("a saved sheet round-trips through the editable text", () => {
  const rows = [
    { role: "starting", shirt_number: "23", player_name: "Hastings Katoma Banda",
      position: "GK", captain: false },
    { role: "starting", shirt_number: "8", player_name: "Gregory Nachipo",
      position: "", captain: true },
    // Came on in the match: before kick-off, which a line-up board shows, he
    // was on the bench.
    { role: "sub_on", shirt_number: "12", player_name: "Michael Mtonyo",
      position: "", captain: false, minute_on: "60" },
    { role: "unused_sub", shirt_number: "", player_name: "Raphael Phiri",
      position: "", captain: false },
  ];
  const text = sheetText(rows);
  assert.equal(text, [
    "23 Hastings Katoma Banda (GK)", "8 Gregory Nachipo (C)", "Substitutes:",
    "12 Michael Mtonyo", "Raphael Phiri",
  ].join("\n"));
  const side = boardSide(text, "full");
  assert.deepEqual(side.starting.map((p) => [p.shirt, p.name, p.gk, p.captain]), [
    ["23", "Hastings Katoma Banda", true, false],
    ["8", "Gregory Nachipo", false, true],
  ]);
  assert.deepEqual(side.bench.map((p) => p.name), ["Michael Mtonyo", "Raphael Phiri"]);
});

test("an empty sheet is empty text and an empty board, never a throw", () => {
  assert.equal(sheetText([]), "");
  assert.equal(sheetText(undefined), "");
  assert.deepEqual(boardSide(""), { starting: [], bench: [] });
});

test("a FAM registration number is checked, not trusted", () => {
  assert.equal(famRegNo("000584M95"), "000584M95");
  assert.equal(famRegNo("(008699F00)"), "008699F00");
  assert.equal(famRegNo(" 010126m04 "), "010126M04");
  assert.equal(famRegNo("00584M95"), "");
  assert.equal(famRegNo("000584X95"), "");
  assert.equal(famRegNo("Micium Mhone (000584M95)"), "");
  assert.equal(famRegNo(null), "");
});

test("a read pre-match report fills both boxes and both coaches", () => {
  // The shape import-extract stores (normalizeMatchReport), from report 147.
  const item = {
    team_sheets: [
      { team_side: "home", starting: [
          { name_raw: "Hastings Katoma Banda", shirt_number: "23", position: "GK",
            captain: false, reg_no: "000317M01" },
          { name_raw: "Gregory Nachipo", shirt_number: "8", position: "",
            captain: true, reg_no: "000413M98" }],
        bench: [{ name_raw: "Michael Mtonyo", shirt_number: "12", position: "",
                  captain: false, reg_no: "000534M95" }] },
      { team_side: "away", starting: [
          { name_raw: "EMMANUEL MWANJALA", shirt_number: "15", position: "",
            captain: false, reg_no: "023902M01" }], bench: [] },
    ],
    coaches: [{ team_side: "home", name_raw: "Jimmy Mzunga", reg_no: "" },
              { team_side: "away", name_raw: "Kondwa Mwamlima Ikwanga", reg_no: "" }],
  };
  const boxes = boxesFromReport(item);
  assert.equal(boxes.home, "23 Hastings Katoma Banda (GK)\n8 Gregory Nachipo (C)\n"
    + "Substitutes:\n12 Michael Mtonyo");
  assert.equal(boxes.away, "15 EMMANUEL MWANJALA");
  assert.equal(boxes.homeCoach, "Jimmy Mzunga");
  assert.equal(boxes.awayCoach, "Kondwa Mwamlima Ikwanga");
  assert.equal(boxes.unplaced, 0);
  assert.equal(boardSide(boxes.home).starting[1].captain, true);

  // Printed the other way round: Kamuzu Barracks' XI is the away box.
  const turned = boxesFromReport(item, true);
  assert.equal(turned.away, boxes.home);
  assert.equal(turned.homeCoach, "Kondwa Mwamlima Ikwanga");
});

test("a sheet with no side is counted, never guessed onto one", () => {
  const boxes = boxesFromReport({ team_sheets: [{ team_side: "unknown",
    starting: [{ name_raw: "A. Phiri", shirt_number: "", position: "", captain: false }],
    bench: [] }] });
  assert.deepEqual(boxes, { home: "", away: "", homeCoach: "", awayCoach: "", unplaced: 1 });
  assert.deepEqual(boxesFromReport(null).unplaced, 0);
});

// ── FAM numbers on a sheet (0065) ──

const row = (name, reg, id = "") => ({ player_name: name, reg_no: reg, player_id: id });

test("a staged report row carries its FAM number, checked", () => {
  const { sheets } = sheetsFromReport({ team_sheets: [{ team_side: "home",
    starting: [{ name_raw: "Gregory Nachipo", shirt_number: "8", position: "",
                 captain: true, reg_no: "000413M98" },
               { name_raw: "Bright Songa", shirt_number: "77", position: "",
                 captain: false, reg_no: "not one" }],
    bench: [] }] }, { homeTeamId: "MW_KB_M1", awayTeamId: "MW_CU_M1", flipped: false });
  assert.deepEqual(sheets.MW_KB_M1.map((r) => r.reg_no), ["000413M98", ""]);
});

test("a FAM number links before any name, and never twice", () => {
  const rows = [row("J. Issac Simfukwe", "023900M02"), row("Gift Magola", "023901M03"),
                row("Kenson Mlenga", "000686M99", "CAF_MW_000686"),
                row("Repeat", "023900M02")];
  const linked = linkByFam(rows, [
    { fam_reg_no: "023900M02", player_id: "CAF_MW_000700" },
    // Held by someone the reporter did not choose: their link stands.
    { fam_reg_no: "000686M99", player_id: "CAF_MW_000999" }]);
  assert.equal(linked, 1);
  assert.deepEqual(rows.map((r) => r.player_id),
    ["CAF_MW_000700", "", "CAF_MW_000686", ""]);
});

test("the save sends each number once, unlinked rows only when creating", () => {
  const rows = [row("EMMANUEL MWANJALA", "023902M01"), row("Kenson Mlenga", "000686M99", "CAF_MW_000686"),
                row("No number", ""), row("Again", "023902M01")];
  assert.deepEqual(famPayload(rows), [
    { reg_no: "023902M01", player_id: "", name: "Emmanuel Mwanjala" },
    { reg_no: "000686M99", player_id: "CAF_MW_000686", name: "Kenson Mlenga" }]);
  assert.deepEqual(famPayload(rows, false).map((r) => r.reg_no), ["000686M99"]);
});

test("the reply links the new players, says what clashed, and is not sent twice", () => {
  const rows = [row("Emmanuel Mwanjala", "023902M01"), row("Raphael Phiri", "000534M95"),
                row("Kenson Mlenga", "000686M99", "CAF_MW_000686"), row("Gift Magola", "023901M03")];
  const summary = applyFamResults(rows, [
    { reg_no: "023902M01", player_id: "CAF_MW_001300", full_name: "Emmanuel Mwanjala",
      outcome: "created", namesakes: 0, message: "" },
    { reg_no: "000534M95", player_id: "CAF_MW_001301", full_name: "Raphael Phiri",
      outcome: "created", namesakes: 1, message: "" },
    { reg_no: "000686M99", player_id: "CAF_MW_000999", full_name: "Kenson Mlenga",
      outcome: "conflict", namesakes: 0, message: "000686M99 is someone else's." },
    { reg_no: "023901M03", player_id: "CAF_MW_000500", full_name: "Gift Magola",
      outcome: "known", namesakes: 0, message: "" }]);
  assert.deepEqual(rows.map((r) => r.player_id),
    ["CAF_MW_001300", "CAF_MW_001301", "CAF_MW_000686", "CAF_MW_000500"]);
  assert.deepEqual(summary, { created: 2, linked: 1, namesakes: ["Raphael Phiri"],
                              problems: ["000686M99 is someone else's."] });
  assert.deepEqual(famPayload(rows), []);
  assert.equal(famNote(summary), "Added 2 new players with their FAM numbers. Raphael "
    + "Phiri shares a name with someone already on the site — if that is the same "
    + "person, merge them in Players. 000686M99 is someone else's.");
  assert.equal(famNote({ created: 0, linked: 3, namesakes: [], problems: [] }), "");
});
