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
 * It imports only lineup_paste.js and match_report.js, so `node --test` can
 * ask it without a browser or a database.
 */

import { displayName, parseLineup } from "./lineup_paste.js";
import { famRegNo } from "./match_report.js";

export { famRegNo };

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

/** A read report (one import result: team_sheets, coaches) as the two boxes
 *  and two coaches the line-up screen shows. `flipped` is true when the
 *  report printed the fixture the other way round from the match it is
 *  being drawn for (match_report.js orientationByName) — a pre-match report
 *  never does, a club's own graphic sometimes does.
 *
 *  A sheet whose side could not be told is left out and counted, never
 *  guessed onto one: the screen says so, and a wrong guess would put one
 *  club's XI under the other's crest on a graphic that gets shared. */
export function boxesFromReport(item, flipped = false) {
  const out = { home: "", away: "", homeCoach: "", awayCoach: "", unplaced: 0 };
  const sideOf = (s) => (s === "home" || s === "away"
    ? (flipped ? (s === "home" ? "away" : "home") : s) : "");
  const row = (role) => (p) => ({ role, shirt_number: p.shirt_number,
    player_name: p.name_raw, position: p.position, captain: p.captain });
  (item?.team_sheets || []).forEach((t) => {
    const key = sideOf(t.team_side);
    if (!key || out[key]) { out.unplaced += 1; return; }
    out[key] = sheetText([...(t.starting || []).map(row("starting")),
                          ...(t.bench || []).map(row("unused_sub"))]);
  });
  (item?.coaches || []).forEach((c) => {
    const key = sideOf(c.team_side);
    const field = key === "home" ? "homeCoach" : key === "away" ? "awayCoach" : "";
    if (field && !out[field]) out[field] = displayName(c.name_raw);
  });
  return out;
}

// ── FAM numbers on a sheet (0065) ────────────────────────────────────────────
// A pre-match report prints the FA's registration number beside every name,
// and a number names one person where a name names a crowd. So a staged sheet
// is linked by number before any name is tried, and saving it registers the
// numbers: the holder is linked, a linked player with none takes theirs, and
// anyone the site has never seen is created — the one place the portal makes
// players without a tap each, because here the FA has already said who they
// are. The rules are below; the round trips are app.js's.

/** Link rows by FAM number. `holders` is fam_players' reply
 *  ({fam_reg_no, player_id}). A row already linked keeps its link — the
 *  reporter chose it — and nobody is linked twice on one sheet. Returns how
 *  many rows it linked. */
export function linkByFam(rows, holders) {
  const who = new Map((holders || []).map((h) => [h.fam_reg_no, h.player_id]));
  const taken = new Set(rows.map((r) => r.player_id).filter(Boolean));
  let linked = 0;
  rows.forEach((r) => {
    const id = r.reg_no && !r.player_id ? who.get(r.reg_no) : "";
    if (!id || taken.has(id)) return;
    r.player_id = id;
    taken.add(id);
    linked += 1;
  });
  return linked;
}

/** What register_fam_players is sent when a sheet saves: each number once,
 *  with the row's link and its name as it should be stored ("EMMANUEL
 *  MWANJALA" is the PDF's typography). An unlinked row goes only when
 *  `create` — the reporter can save a sheet as names alone. */
export function famPayload(rows, create = true) {
  const seen = new Set();
  return (rows || []).filter((r) => {
    if (!r.reg_no || r.fam_done || seen.has(r.reg_no)) return false;
    seen.add(r.reg_no);
    return Boolean(r.player_id) || create;
  }).map((r) => ({
    reg_no: r.reg_no, player_id: r.player_id || "",
    name: displayName(r.player_name).replace(/\s+/g, " ").trim(),
  }));
}

/** register_fam_players' reply folded back onto the rows, BEFORE they go to
 *  save_lineup: a created or known player's id goes on the line that was
 *  unlinked. A conflict leaves the line as it was and is said. */
export function applyFamResults(rows, results) {
  const byReg = new Map((results || []).map((r) => [r.reg_no, r]));
  const taken = new Set(rows.map((r) => r.player_id).filter(Boolean));
  const out = { created: 0, linked: 0, namesakes: [], problems: [] };
  rows.forEach((row) => {
    const res = row.reg_no ? byReg.get(row.reg_no) : null;
    if (!res) return;
    row.fam_done = true;
    if (res.outcome === "conflict" || res.outcome === "invalid") {
      if (res.message && !out.problems.includes(res.message)) out.problems.push(res.message);
      return;
    }
    if (row.player_id || !res.player_id || taken.has(res.player_id)) return;
    row.player_id = res.player_id;
    taken.add(res.player_id);
    if (res.outcome === "created") {
      out.created += 1;
      if (res.namesakes > 0) out.namesakes.push(res.full_name);
    } else {
      out.linked += 1;
    }
  });
  return out;
}

/** The sentence the save says about it, or "" when there is nothing to say. */
export function famNote(summary) {
  if (!summary) return "";
  const parts = [];
  const n = summary.created;
  if (n) parts.push(`Added ${n} new player${n === 1 ? "" : "s"} with their FAM numbers.`);
  const same = summary.namesakes || [];
  if (same.length) {
    const names = same.length > 3 ? `${same.slice(0, 3).join(", ")} and ${same.length - 3} more`
      : same.join(same.length === 2 ? " and " : ", ");
    parts.push(`${names} share${same.length === 1 ? "s" : ""} a name with someone `
      + "already on the site — if that is the same person, merge them in Players.");
  }
  return [...parts, ...(summary.problems || [])].join(" ");
}
