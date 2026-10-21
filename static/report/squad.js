/* A team's squad and staff: the rules, without the screen.
 *
 * WHY THIS FILE EXISTS. #/squad (0057) takes a squad however it arrives — the
 * whole list copied off a club website, a table with a tab between each
 * column, three names off a signing graphic — and turns each line into one
 * person to add. Which part of "7 · John Banda · Midfielder · 23" is the shirt
 * and which the age, and whether "J. Banda" is the J. Banda who scored for this
 * club in August or a different one, are questions with answers that can be
 * written down. They are written down here, the bargain results_grid.js and
 * match_report.js make: plain functions over plain objects, importing only
 * each other, so `node --test` can ask them without a browser or a database.
 *
 * NOTHING HERE DECIDES A PERSON ON A GUESS. A name resolves to an existing
 * player only by findPerson's rule — unique or nothing — and only among people
 * already attached to THIS team: its squad list, its team sheets, its goals.
 * Anyone else the database holds under a similar name is offered, never
 * chosen, because a wrong link moves a stranger's career onto this club's page
 * and looks exactly like a right one.
 */

import { findPerson, normalizeName } from "./match_report.js";

// The screen dedupes staged lines by this, and one fold is the only safe
// number of folds.
export { normalizeName };

export const POSITIONS = ["GK", "DF", "MF", "FW"];

// Words a club prints for each line. Only unambiguous ones: a winger is a
// midfielder at one club and a forward at the next, so "winger" stays text.
const POSITION_WORDS = [
  ["GK", /^(gk|goal ?keepers?|goal-keepers?|keepers?|goalies?)$/],
  ["DF", /^(df|def|defenders?|defen[cs]e|cb|lb|rb|full[ -]?backs?|cent(re|er)[ -]?backs?)$/],
  ["MF", /^(mf|mid|midfielders?|midfield|cm|dm|cdm|am|cam)$/],
  ["FW", /^(fw|fwd|forwards?|strikers?|attackers?|st|cf)$/],
];

/** "Midfielder" -> "MF", "" when it is not a position. */
export function positionFrom(text) {
  const t = String(text || "").toLowerCase().replace(/[().:]/g, "").trim();
  if (!t) return "";
  const hit = POSITION_WORDS.find(([, re]) => re.test(t));
  return hit ? hit[0] : "";
}

const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun",
                "jul", "aug", "sep", "oct", "nov", "dec"];

const pad = (n) => String(n).padStart(2, "0");

/** A printed date of birth -> "YYYY-MM-DD", or "".
 *
 *  Day first when it is written with slashes, because that is how Malawi
 *  writes a date: 04/05/2003 is the 4th of May. A date that does not exist
 *  (31/02) is no date rather than a corrected one. */
export function dateFrom(text) {
  const t = String(text || "").trim().replace(/^(dob|d\.o\.b\.?|born)[:\s]*/i, "");
  let y, m, d;
  let hit = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(t);
  if (hit) [, y, m, d] = hit.map(Number);
  if (!hit && (hit = /^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$/.exec(t))) {
    [, d, m, y] = hit.map(Number);
  }
  if (!hit && (hit = /^(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?,?\s+(\d{4})$/i.exec(t))) {
    d = Number(hit[1]);
    m = MONTHS.indexOf(hit[2].slice(0, 3).toLowerCase()) + 1;
    y = Number(hit[3]);
  }
  if (!hit || !m) return "";
  const date = new Date(Date.UTC(y, m - 1, d));
  if (date.getUTCFullYear() !== y || date.getUTCMonth() !== m - 1
      || date.getUTCDate() !== d) return "";
  return `${y}-${pad(m)}-${pad(d)}`;
}

/** "23", "Age 23", "23 yrs" -> 23, or null. Bounded to ages a footballer has:
 *  outside them the number was something else — a shirt, a year, a cap count. */
export function ageFrom(text) {
  const hit = /^(?:age[:\s]*)?(\d{1,2})(?:\s*(?:yrs?|years?(?:\s+old)?))?$/i
    .exec(String(text || "").trim());
  if (!hit) return null;
  const n = Number(hit[1]);
  return n >= 10 && n <= 75 ? n : null;
}

/** The year an age says someone was born in. One year late for anyone whose
 *  birthday is still to come this year — the trade 0057's header states. */
export function birthYearFromAge(age, today = new Date()) {
  return today.getFullYear() - age;
}

/** What the squad list shows for a person's age: exact from a date, "about"
 *  from a year, nothing when neither is known. */
export function ageLabel(person, today = new Date()) {
  if (person?.dob) {
    const [y, m, d] = person.dob.split("-").map(Number);
    let age = today.getFullYear() - y;
    if (today.getMonth() + 1 < m || (today.getMonth() + 1 === m && today.getDate() < d)) {
      age -= 1;
    }
    return String(age);
  }
  if (person?.birth_year) return `about ${today.getFullYear() - person.birth_year}`;
  return "";
}

// A list arrives as a table (tabs), a sentence (" - ", " | ", commas) or a
// column of names. Hyphens INSIDE a name ("O'Neil-Banda") survive, because
// only a hyphen with spaces round it separates.
const FIELDS = /\t+|\s+[|–—-]\s+|\s*\|\s*|\s*,\s*|\s{3,}/;

/** A pasted squad -> [{name, shirt_number, position, dob, age}].
 *
 *  A heading that is a position ("Goalkeepers", "DEFENDERS:") applies to the
 *  lines under it until the next one, because that is how a club's page lays a
 *  squad out. Anything else that ends in a colon is a heading too, and resets
 *  it. A number before the name is the shirt; a bare number after it is an
 *  age; anything after the name that is none of those is dropped (a
 *  nationality, a club the player came from) rather than glued onto it. */
export function parseSquadList(text) {
  const out = [];
  let heading = "";

  String(text || "").split(/\r?\n/).forEach((raw) => {
    let line = raw.replace(/\((?:c|capt(?:ain)?)\)/ig, " ").trim();
    if (!line) return;

    const asHeading = line.replace(/\s*\(\d+\)\s*$/, "").replace(/:$/, "").trim();
    const headPos = positionFrom(asHeading);
    if (headPos && asHeading.split(/\s+/).length <= 2) { heading = headPos; return; }
    if (/:$/.test(line)) { heading = ""; return; }

    const row = { name: "", shirt_number: "", position: "", dob: "", age: null };
    let fields = line.split(FIELDS).map((f) => f.trim()).filter(Boolean);

    // One field: the affixes are inside it. "7. John Banda MF".
    if (fields.length === 1) {
      let rest = fields[0];
      const lead = /^#?(\d{1,2})[.):]?\s+/.exec(rest);
      if (lead) { row.shirt_number = lead[1]; rest = rest.slice(lead[0].length); }
      // An age inside one field needs saying as one — "(23)", "23 yrs",
      // "age 23". A bare trailing number is as likely a shirt written after
      // the name, and guessing wrong puts a false age on a profile.
      const marked = /\s*(?:\((\d{1,2})\)|\bage\s*(\d{1,2})|(\d{1,2})\s*(?:yrs?|years?(?:\s+old)?))\s*$/i
        .exec(rest);
      if (marked && ageFrom(marked[1] || marked[2] || marked[3]) !== null) {
        row.age = ageFrom(marked[1] || marked[2] || marked[3]);
        rest = rest.slice(0, marked.index);
      }
      const bracket = /\s*\(([^)]+)\)\s*$/.exec(rest);
      if (bracket && positionFrom(bracket[1])) {
        row.position = positionFrom(bracket[1]);
        rest = rest.slice(0, bracket.index);
      }
      const words = rest.split(/\s+/);
      if (!row.position && words.length > 1 && positionFrom(words[words.length - 1])) {
        row.position = positionFrom(words.pop());
        rest = words.join(" ");
      }
      fields = [rest];
    }

    fields.forEach((field) => {
      const shirt = /^#?(\d{1,2})[.)]?$/.exec(field);
      if (shirt && !row.name && !row.shirt_number) { row.shirt_number = shirt[1]; return; }
      const pos = positionFrom(field);
      if (pos) { row.position = row.position || pos; return; }
      const dob = dateFrom(field);
      if (dob) { row.dob = dob; return; }
      if (row.name) {
        const age = ageFrom(field);
        if (age !== null && row.age === null) row.age = age;
        return;
      }
      if (/\p{L}/u.test(field)) row.name = field.replace(/\s+/g, " ");
    });

    if (!row.name) return;
    row.position = row.position || heading;
    out.push(row);
  });
  return out;
}

// What a staff title is made of. Matched as a word anywhere in the field, so
// "Second Assistant Coach" and "Media & Communication Officer" both read as
// roles, and a name never does unless someone is called Coach.
const ROLE_WORD = /\b(coach|coaches|trainer|manager|doctor|physio|physiotherapist|medic|medical|officer|analyst|kit|secretary|director|chairman|chairperson|president|treasurer|scout|masseur|masseuse|nurse|assistant|technical|team\s+leader)\b/i;

export const STAFF_ROLES = [
  "Head coach", "Assistant coach", "Goalkeeper coach", "Fitness coach",
  "Team manager", "Team doctor", "Physio", "Kit manager",
];

const isRole = (text) => ROLE_WORD.test(text);

/** A pasted staff list -> [{name, role}].
 *
 *  "Audlow Makonyola - Head Coach", "Head Coach: Audlow Makonyola", and the
 *  shape a club website copies as — the name on one line and the title on the
 *  next. A line that is only a title belongs to the person above it when they
 *  have none yet, and otherwise waits for the name below it. */
export function parseStaffList(text) {
  const out = [];
  let pendingRole = "";

  String(text || "").split(/\r?\n/).forEach((raw) => {
    const line = raw.replace(/\s+/g, " ").trim();
    if (!line) return;

    const colon = /^([^:]+):\s*(.+)$/.exec(line);
    let fields = colon ? [colon[1], colon[2]] : line.split(FIELDS);
    fields = fields.map((f) => f.trim()).filter(Boolean);

    // "Head Coach Audlow Makonyola" is undecidable and stays a name; only a
    // separated title is read as one.
    if (fields.length === 1) {
      if (isRole(fields[0])) {
        const last = out[out.length - 1];
        if (last && !last.role) last.role = fields[0];
        else pendingRole = fields[0];
        return;
      }
      out.push({ name: fields[0], role: pendingRole });
      pendingRole = "";
      return;
    }

    const role = fields.find(isRole) || "";
    const name = fields.find((f) => f !== role && /\p{L}/u.test(f)) || "";
    if (!name) { if (role) pendingRole = role; return; }
    out.push({ name, role: role || pendingRole });
    pendingRole = "";
  });
  return out;
}

/** Who a pasted name is, given what this team already knows.
 *
 *    squad    already on this season's list — saving updates their row.
 *    linked   one person this team has fielded or recorded a goal for.
 *    choose   the database holds similar names, none of them tied to this
 *             team; the reporter picks one or says "new player".
 *    new      nobody like them anywhere.
 *
 *  `registered` and `played` are this team's people ({player_id, name});
 *  `candidates` is search_players' answer for this name, with team_ids. A
 *  candidate who has played for THIS team counts as `played`, so a
 *  search hit is never what makes a row green on its own. */
export function resolveSquadName(name, { teamId, registered = [], played = [],
                                         candidates = [] }) {
  const onList = findPerson(name, registered);
  if (onList) return { kind: "squad", player_id: onList.player_id, name: onList.name };

  const label = (c) => c.known_as || c.full_name || "";
  const ours = candidates.filter((c) => (c.team_ids || []).includes(teamId))
    .map((c) => ({ player_id: c.player_id, name: label(c) }));
  const pool = [...played.filter((p) => p.player_id), ...ours]
    .filter((p, i, all) => all.findIndex((q) => q.player_id === p.player_id) === i);
  const known = findPerson(name, pool);
  if (known) return { kind: "linked", player_id: known.player_id, name: known.name };

  const options = candidates.filter((c) => c.player_id)
    .map((c) => ({ player_id: c.player_id, name: label(c), teams: c.teams || "" }));
  if (options.length) {
    // create_player is idempotent on an exact name, so "new" next to an exact
    // match has to be sent with force or it would quietly link to them.
    const want = normalizeName(name);
    const exact = options.some((o) => normalizeName(o.name) === want);
    return { kind: "choose", player_id: "", options, force: exact };
  }
  return { kind: "new", player_id: "" };
}

/** The same name twice in one paste is one person listed twice, not two. */
export function dedupeLines(rows) {
  const seen = new Set();
  return rows.filter((r) => {
    const key = normalizeName(r.name);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** Goalkeepers first, then by shirt, then by name — a squad list's order.
 *  Anyone with no position sorts after the four groups, never out of the list. */
export function sortSquad(rows) {
  const rank = (p) => { const i = POSITIONS.indexOf(p); return i < 0 ? 9 : i; };
  const shirt = (s) => (s === "" || s == null ? 999 : Number(s));
  return [...rows].sort((a, b) =>
    rank(a.position) - rank(b.position)
    || shirt(a.shirt_number) - shirt(b.shirt_number)
    || String(a.name).localeCompare(String(b.name)));
}
