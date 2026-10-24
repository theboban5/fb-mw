/* A pasted team sheet: the rules, without the screen.
 *
 * WHY THIS FILE EXISTS. The match screen's paste box read one player per line
 * and linked a name only when it was letter-for-letter the squad's. That is
 * not how a single sheet in the FDH Bank Premiership arrives. They come as:
 *
 *   - a graphic's text: "31. MKUNGULA", "19.MPHEPO", with the bench run
 *     together on one line — "01.SANDE 3 12. LUNGU 42. CHIZINGA";
 *   - a sentence: "Bullets XI: Maclean Mwale; Yamikani Mologeni, Blessings
 *     Mpokera (C), …" then "Substitutes: Pilirani Mapira (GK), …";
 *   - a club's full report, both teams, every substitution written inside the
 *     starter's brackets — "29 Chawanangwa Gumbo (8 Paul Master 46’)" — and
 *     a "Match Statistics" block underneath that is not a list of players.
 *
 * So every name came in unlinked, and "MKUNGULA" against a squad that knows
 * Clement Mkungula was a tap per row to repair. This reads all three, and says
 * who each printed name is by the squad's own record — findPerson's rule,
 * unique or nothing, with the shirt number as the tiebreak a graphic prints
 * beside every name. It imports only match_report.js, results_grid.js' bargain,
 * so `node --test` can ask it without a browser or a database.
 */

import { findPerson, matchRowsToSquad, normalizeName } from "./match_report.js";

// The squad matcher lives beside findPerson, because the match report's
// sheets use it too; it is re-exported so the paste box has one module to ask.
export { matchRowsToSquad };

const POSITION = /^(gk|df|mf|fw|goal ?keeper)$/i;
const POSITION_CODE = { gk: "GK", df: "DF", mf: "MF", fw: "FW",
                        goalkeeper: "GK", "goal keeper": "GK" };

// A heading that opens a team's block: "MAFCO XI", "Bullets XI:", "Starting
// XI", "Line-up". The list may follow on the same line after a colon.
const XI_HEAD = /^(.*?)\s*\b(?:starting\s+(?:xi|eleven|line-?up)|xi|line-?ups?)\b\s*(?::\s*(.*))?$/i;

// A heading that opens the bench. "Substitutes not used" is a heading with no
// colon, which is why this cannot simply be "anything ending in a colon".
const BENCH_HEAD = /^(?:subs(?:titutes)?|bench|reserves|replacements)(?:\s+(?:not\s+used|unused))?\s*(?::\s*(.*))?$/i;

// What follows a team sheet in a club report and is not a list of players. A
// line opening with one of these is skipped, and so is everything after it
// until the next team heading — a coach's line can sit between two sheets.
const NOT_PLAYERS = /^(?:match\s+(?:statistics|stats|details|facts|report)|goals?|scorers?|goalscorers?|bookings?|cards?|discipline|player\s+of\s+the\s+match|man\s+of\s+the\s+match|motm|potm|fixture|competition|date|venue|result|score|referee|assistant|fourth\s+official|officials?|attendance|coach(?:es)?|head\s+coach|manager|kick-?\s*off|ko)\b/i;

/** "MKUNGULA" -> "Mkungula", "M. B ANDA" -> "M. B Anda". A name in capitals
 *  is the graphic's typography, not its spelling; a name with any lower case
 *  in it is left exactly as written. */
export function displayName(name) {
  const s = String(name || "").trim();
  if (/\p{Ll}/u.test(s)) return s;
  return s.toLowerCase().replace(/(^|[\s.'’-])(\p{L})/gu,
    (_, lead, ch) => lead + ch.toUpperCase());
}

/** A list split on commas and semicolons, but never inside brackets — the
 *  substitution in "Gumbo (8 Paul Master 46’), 10 Wongani Lungu" is part of
 *  Gumbo's entry. */
function splitList(line) {
  const out = [];
  let depth = 0;
  let cur = "";
  for (const ch of line) {
    if (ch === "(" || ch === "[") depth += 1;
    if (ch === ")" || ch === "]") depth = Math.max(0, depth - 1);
    if (depth === 0 && (ch === "," || ch === ";")) { out.push(cur); cur = ""; continue; }
    cur += ch;
  }
  out.push(cur);
  return out;
}

/** "01.SANDE 3 12. LUNGU 42. CHIZINGA" -> three entries. A number opens a new
 *  entry only when a letter follows it and it is not the first thing on the
 *  line — "62' for X" is a minute, and "Sande 3" before "12." keeps its 3. */
function splitNumbered(piece) {
  return piece.split(/\s+(?=#?\d{1,3}(?:[.)]\s*|\s+)\p{L})/u);
}

const blankRow = (role) => ({
  player_name: "", player_id: "", shirt_number: "", position: "",
  role, captain: false, motm: false,
  minute_on: "", minute_off: "", replaced_player: "",
  yellow_card: false, yellow_red_card: false, red_card: false,
});

const MINUTE = /(\d{1,3}(?:\s*\+\s*\d{1,2})?)\s*(?:['’"`]|mins?\b|min\.?)/i;
const tidyMinute = (m) => m.replace(/\s+/g, "");
const shirtOf = (n) => String(Number(n));       // "01" is shirt 1

/** One printed entry -> [row, ...substitutes it names].
 *
 *  The per-entry rules are the old paste box's, unchanged — [Y] [YR] [R], (C),
 *  62', "for X", a leading shirt, a trailing position — plus the two things a
 *  club report adds: a position in brackets, "(GK)", and a substitution in
 *  brackets, "(8 Paul Master 46’)", which is a second player who came on for
 *  this one. A bracket that is neither is decoration and goes. */
export function parseEntry(text, role = "starting") {
  const row = blankRow(role);
  const subs = [];
  let rest = String(text || "");

  // Cards are bracketed rather than bare letters: a lone Y or R in a name is
  // far more likely to be an initial than a booking.
  rest = rest.replace(/\[([^\]]+)\]/g, (_, token) => {
    const t = token.trim().toUpperCase();
    if (t === "Y") row.yellow_card = true;
    else if (t === "YR" || t === "Y2" || t === "2Y") row.yellow_red_card = true;
    else if (t === "R") row.red_card = true;
    else if (t === "C") row.captain = true;
    return " ";
  });

  rest = rest.replace(/\(([^()]*)\)/g, (_, inner) => {
    const t = inner.trim();
    if (/^(c|capt(ain)?)$/i.test(t)) { row.captain = true; return " "; }
    if (POSITION.test(t)) { row.position = POSITION_CODE[t.toLowerCase()]; return " "; }
    // "(46')" on a substitute's own line: when they came on.
    const own = new RegExp(`^(?:on\\s+)?${MINUTE.source}$`, "i").exec(t);
    if (own) { row.minute_on = tidyMinute(own[1]); row.role = "sub_on"; return " "; }
    // "(8 Paul Master 46’)": somebody else, on for this player.
    const sub = new RegExp(`^(?:#?(\\d{1,3})[.)]?\\s+)?(.*?\\p{L}.*?)\\s+${MINUTE.source}$`, "iu")
      .exec(t);
    if (sub) {
      const on = blankRow("sub_on");
      on.shirt_number = sub[1] ? shirtOf(sub[1]) : "";
      on.player_name = displayName(sub[2].replace(/\s+/g, " "));
      on.minute_on = tidyMinute(sub[3]);
      subs.push(on);
    }
    return " ";
  });

  // "(C)" written without its brackets closing, or as "©".
  rest = rest.replace(/©/g, () => { row.captain = true; return " "; });

  // "for Vanessa Chikupira" — naming who they replaced also says they came
  // on, so the role follows from it rather than having to be set as well.
  rest = rest.replace(/\bfor\s+(.+)$/i, (_, name) => {
    row.replaced_player = displayName(name.trim().replace(/[.\s]+$/, ""));
    row.role = "sub_on";
    return " ";
  });

  // A minute, written 62' or 62" or "on 62'".
  rest = rest.replace(new RegExp(`\\b(?:on\\s+)?${MINUTE.source}`, "i"), (_, m) => {
    row.minute_on = tidyMinute(m); row.role = "sub_on"; return " ";
  });

  rest = rest.trim();
  const lead = /^#?(\d{1,3})(?:[.)]\s*|\s+)/.exec(rest);
  if (lead) { row.shirt_number = shirtOf(lead[1]); rest = rest.slice(lead[0].length); }

  const tail = /\s+(GK|DF|MF|FW)\.?$/i.exec(rest);
  if (tail) { row.position = tail[1].toUpperCase(); rest = rest.slice(0, tail.index); }

  // A number after the name is the shirt when none came before it ("MWALE
  // 23"). With one before it, a trailing number is part of the name as
  // printed — "01.SANDE 3" — and is kept rather than guessed at.
  if (!row.shirt_number) {
    const after = /\s+#?(\d{1,2})$/.exec(rest);
    if (after) { row.shirt_number = shirtOf(after[1]); rest = rest.slice(0, after.index); }
  }

  rest = rest.replace(/^[\s.:–—-]+|[\s.,:–—-]+$/g, "").replace(/\s+/g, " ");
  if (!/\p{L}/u.test(rest)) return [];
  row.player_name = displayName(rest);
  subs.forEach((s) => { s.replaced_player = row.player_name; });
  return [row, ...subs];
}

/** A pasted sheet -> [{heading, rows}], one block per team in it.
 *
 *  A text with no "XI" heading is one block, so the old one-name-a-line paste
 *  reads exactly as it did. A heading that is a team ("MAFCO XI") starts a new
 *  block once the current one has anyone in it; the bench heading under it
 *  turns what follows into unused substitutes, and a substitute the report
 *  wrote inside a starter's brackets sits between the XI and the bench, as it
 *  does on the screen. Repeats within a block are one person listed twice. */
export function parseLineup(text) {
  const blocks = [];
  let block = null;
  let role = "starting";
  let skipping = false;

  const open = (heading) => {
    block = { heading: heading || "", starting: [], on: [], bench: [] };
    blocks.push(block);
    role = "starting";
    skipping = false;
  };
  const add = (entry) => {
    if (!block) open("");
    const [row, ...subs] = parseEntry(entry, role);
    if (!row) return;
    (row.role === "starting" ? block.starting
      : row.role === "sub_on" ? block.on : block.bench).push(row);
    block.on.push(...subs);
  };
  const addList = (line) => splitList(line)
    .flatMap(splitNumbered).forEach((e) => { if (e.trim()) add(e); });

  String(text || "").split(/\r?\n/).forEach((raw) => {
    const line = raw.trim();
    if (!line) return;

    const xi = XI_HEAD.exec(line);
    // A whole line that says "XI", not a name with "Xi" in it: the heading
    // part must be short, or end in a colon, or be the line.
    if (xi && (xi[2] !== undefined || line.split(/\s+/).length <= 6)) {
      const heading = xi[1].replace(/[:\s]+$/, "").trim();
      const empty = block && !block.starting.length && !block.on.length
        && !block.bench.length;
      if (empty) { block.heading = heading || block.heading; skipping = false; role = "starting"; }
      else open(heading);
      if (xi[2]) addList(xi[2]);
      return;
    }

    const bench = BENCH_HEAD.exec(line);
    if (bench) {
      if (!block) open("");
      role = "unused_sub";
      skipping = false;
      if (bench[1]) addList(bench[1]);
      return;
    }

    if (NOT_PLAYERS.test(line)) { skipping = true; return; }
    if (skipping) return;

    // Any other heading ending in a colon: a bench word sends what follows to
    // the bench, a starting word back to the XI, anything else changes
    // nothing. The old paste box's rule, kept for the sheets typed to it.
    if (/:$/.test(line)) {
      const head = line.slice(0, -1).toLowerCase();
      if (/sub|bench|replac/.test(head)) role = "unused_sub";
      else if (/start|xi|line/.test(head)) role = "starting";
      return;
    }
    addList(line);
  });

  return blocks.map((b) => {
    const seen = new Set();
    const rows = [...b.starting, ...b.on, ...b.bench].filter((r) => {
      const key = normalizeName(r.player_name);
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    // The armband is one per side; a second "(C)" is a misread, and the first
    // stands — sheetsFromReport's rule.
    let captained = false;
    rows.forEach((r) => {
      if (r.captain && captained) r.captain = false;
      if (r.captain) captained = true;
    });
    return { heading: b.heading, rows };
  }).filter((b) => b.rows.length);
}

/** "Player of the Match: Tinaye Tsimba (Bullets)." -> "Tinaye Tsimba", or "".
 *  A club report names the award under the sheets, where parseLineup skips
 *  it; the screen gives the star to the one sheet row it names, if one does. */
export function motmFrom(text) {
  const hit = /^\s*(?:player|man)\s+of\s+the\s+match\s*[:\-–]\s*(.+)$/im.exec(String(text || ""))
    || /^\s*(?:motm|potm)\s*[:\-–]\s*(.+)$/im.exec(String(text || ""));
  if (!hit) return "";
  return displayName(hit[1].replace(/\([^)]*\)/g, " ").replace(/[.\s]+$/, "").trim());
}

/** Every row in a paste, whatever block it was in — for the screens that take
 *  one list (the national-team squad form). */
export function parseTeamSheet(text) {
  return parseLineup(text).flatMap((b) => b.rows);
}

// ── Who each printed name is ─────────────────────────────────────────────────

/** Does `printed` say more of this person's name than `saved` does?
 *
 *  "Joseph Banda" against a player saved as "Banda" or "J. Banda" — the same
 *  person by findPerson's rule, with a first name the registry does not have
 *  yet. The screen offers a rename (never makes one): on a graphic that prints
 *  surnames, this is how a squad gains first names. */
export function fullerName(printed, saved) {
  const p = normalizeName(printed).split(" ").filter(Boolean);
  const s = normalizeName(saved).split(" ").filter(Boolean);
  if (p.length < 2 || p[0].length < 2) return false;
  if (!findPerson(printed, [{ name: saved }])) return false;
  return s.length === 1 || (s.length === 2 && s[0].length === 1);
}

/** Which side each block belongs to.
 *
 *  `sides` is [{key, names: [display names]}]; `pastedInto` is the key of the
 *  box it was pasted in. A heading naming one side goes there. With two
 *  blocks and one placed, the other takes the remaining side. A lone block
 *  with no heading to go by is the box's own. Anything else is "" — the
 *  screen asks, because a sheet filed under the wrong team is eleven wrong
 *  appearances that look exactly like right ones. */
export function routeBlocks(blocks, sides, pastedInto, sameTeam) {
  const placed = blocks.map((b) => {
    if (!b.heading) return "";
    const hits = sides.filter((s) => s.names.some((n) => sameTeam(b.heading, n)));
    return hits.length === 1 ? hits[0].key : "";
  });
  if (blocks.length === 1 && !placed[0]) return [pastedInto];
  if (blocks.length === 2 && new Set(placed.filter(Boolean)).size === 1
      && placed.filter(Boolean).length === 1) {
    const other = sides.find((s) => s.key !== placed.find(Boolean));
    return placed.map((p) => p || other?.key || "");
  }
  // Two blocks claiming one side is a misread; neither is trusted.
  const counts = placed.reduce((m, k) => m.set(k, (m.get(k) || 0) + 1), new Map());
  return placed.map((k) => (k && counts.get(k) === 1 ? k : ""));
}
