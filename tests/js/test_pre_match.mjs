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

import { boardName, boardSide, boxesFromReport, famRegNo, sheetText } from "../../static/report/pre_match.js";

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
