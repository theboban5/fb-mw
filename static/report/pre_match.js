/* A team sheet as a line-up graphic: the rules, without the canvas.
 *
 * WHY THIS FILE EXISTS. Clubs and FAM have started publishing pre-match
 * reports — the competition system's PDF, both XIs, both benches, every
 * shirt, captain and keeper — and the thing the site's reporters want from
 * one first is the graphic every club already posts: "XI", eleven names,
 * the subs underneath. #/graphics could draw a fixture or a result and had
 * no idea what a team sheet was.
 *
 * THE TEXT IS THE EDITOR. A board's line-up is held as the same text the
 * match screen's paste box reads ("7 Micium Mhone (C)", a "Substitutes:"
 * line, the bench under it), parsed by lineup_paste.js. So a saved sheet,
 * an extracted PDF and a hand-typed list all arrive in one shape, a wrong
 * name is fixed by retyping it, and nothing here is a second reading of what
 * a team sheet looks like.
 *
 * WHAT A NAME LOOKS LIKE ON A BOARD is a choice made here and never stored:
 * club graphics print "MKUNGULA", ours may print "C. Mkungula" or the whole
 * name. Two Phiris in one XI is the case a surname board gets wrong, so a
 * shared surname takes its initial back.
 *
 * It imports only lineup_paste.js (which imports only match_report.js), so
 * `node --test` can ask it without a browser or a database.
 */

import { displayName, parseLineup } from "./lineup_paste.js";

export const NAME_STYLES = [
  ["surname", "Surname (MKUNGULA)"],
  ["initial", "Initial + surname (C. Mkungula)"],
  ["full", "Full name"],
];

// A suffix belongs to the surname: "Banda Jnr" on a surname board is
// "BANDA JNR", not "JNR". compatible() in match_report.js reads the same set.
const SUFFIX = /^(jnr|jr|snr|sr|ii|iii)\.?$/i;

function nameParts(name) {
  const words = displayName(name).replace(/\s+/g, " ").trim().split(" ")
    .filter(Boolean);
  if (words.length < 2) return { first: [], surname: words.join(" ") };
  const cut = words.length > 2 && SUFFIX.test(words[words.length - 1])
    ? words.length - 2 : words.length - 1;
  return { first: words.slice(0, cut), surname: words.slice(cut).join(" ") };
}

function initialOf(first) {
  const word = first.find((w) => /\p{L}/u.test(w));
  return word ? `${word.match(/\p{L}/u)[0].toUpperCase()}.` : "";
}

/** One printed name in a board's style. `clash` asks for the initial back on
 *  a surname board — the caller knows whether the surname is shared. */
export function boardName(name, style = "surname", clash = false) {
  const { first, surname } = nameParts(name);
  if (!surname) return "";
  if (style === "full") return displayName(name).replace(/\s+/g, " ").trim();
  const initial = initialOf(first);
  if (style === "initial") return initial ? `${initial} ${surname}` : surname;
  const caps = surname.toUpperCase();
  return clash && initial ? `${initial} ${caps}` : caps;
}

/** A side's rows as the editable text the screen shows. `rows` are lineups
 *  rows (role, shirt_number, player_name, captain, position), in sheet
 *  order. A substitute who came on was on the bench before kick-off, which
 *  is the moment a line-up graphic describes, so sub_on is bench here. */
export function sheetText(rows) {
  const line = (r) => {
    const marks = [r.position === "GK" ? "(GK)" : "", r.captain ? "(C)" : ""]
      .filter(Boolean).join(" ");
    return [String(r.shirt_number || "").trim(), String(r.player_name || "").trim(), marks]
      .filter(Boolean).join(" ");
  };
  const named = (rows || []).filter((r) => String(r.player_name || "").trim());
  const xi = named.filter((r) => r.role === "starting");
  const bench = named.filter((r) => r.role !== "starting");
  if (!xi.length && !bench.length) return "";
  return [...xi.map(line), ...(bench.length ? ["Substitutes:", ...bench.map(line)] : [])]
    .join("\n");
}

/** The text back into what a board draws: {starting, bench}, each a list of
 *  {shirt, name, captain, gk} with `name` already in the board's style.
 *
 *  Only the first block is read: a board is one side, and the screen keeps
 *  one box per side. A shirt printed "01" is already "1" (parseEntry's
 *  rule), and a row with no shirt is drawn without one. */
export function boardSide(text, style = "surname") {
  const block = parseLineup(text)[0];
  const rows = block ? block.rows : [];
  const surnames = new Map();
  rows.forEach((r) => {
    const key = nameParts(r.player_name).surname.toLowerCase();
    surnames.set(key, (surnames.get(key) || 0) + 1);
  });
  const shape = (r) => ({
    shirt: r.shirt_number || "",
    name: boardName(r.player_name, style,
      surnames.get(nameParts(r.player_name).surname.toLowerCase()) > 1),
    captain: Boolean(r.captain),
    gk: r.position === "GK",
  });
  return {
    starting: rows.filter((r) => r.role === "starting").map(shape),
    bench: rows.filter((r) => r.role !== "starting").map(shape),
  };
}

/** A FAM registration number as printed beside every name on a pre-match
 *  report — "000584M95" — or "" for anything that is not one. Six digits, a
 *  sex, two digits; checked here because an id read off a document is
 *  evidence, never something to store on the strength of having been read. */
export function famRegNo(raw) {
  const m = /^\(?\s*(\d{6}[MF]\d{2})\s*\)?$/i.exec(String(raw || "").trim());
  return m ? m[1].toUpperCase() : "";
}
