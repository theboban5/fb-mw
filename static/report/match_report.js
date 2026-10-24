/* A club's match report, turned into what the match screen already edits.
 *
 * WHY THIS FILE EXISTS. #/import reads a report — a club's post-match article,
 * a team-sheet graphic — into results[0] of an extraction: the XI, the bench,
 * the substitutions, the cards, the scorer and the assister, the officials
 * (extract.js, "match_report"). Nothing about that is publishable as it
 * stands. A name is a name, a side is a column of the picture, and a card is
 * attached to a person rather than to a lineups row. This is the step between,
 * and it is the same bargain results_grid.js and fixture_import.js make: plain
 * functions over plain objects, importing nothing, so `node --test` can ask
 * "does the team manager's red card get written anywhere?" without a browser,
 * a session or a database.
 *
 * WHAT IT PRODUCES IS STAGED, NEVER SAVED. Team-sheet rows in save_lineup's
 * shape, goals to add and goals to amend, officials to fill in. The match
 * screen draws them in the editors it already has and nothing leaves the
 * phone until a reporter taps Save. Every write goes through the RPC a typed
 * sheet goes through, so nothing here can put anything on the site that could
 * not have been typed.
 *
 * NOTHING ALREADY SAVED IS EVER REPLACED. A side with a saved sheet is not
 * offered one; a saved goal is amended only where its column is blank (0054);
 * an official is filled in only where the box is empty. A report enriches what
 * a reporter entered. It does not get to overrule it.
 *
 * AND EVERYTHING IT CANNOT PLACE IT SAYS. A card for a player on no sheet, a
 * red card for a team manager, a substitute with no minute — each becomes one
 * sentence in `unplaced`, because a fact that silently vanished between the
 * report and the screen is a fact the reporter will assume was recorded.
 */

// The one import, and it is a module that imports nothing either: "Mighty
// Wanderers'" is a sentence this file writes too, and two copies of the rule
// would be two answers.
import { possessive } from "./results_grid.js";

// ── Names ────────────────────────────────────────────────────────────────────

/** Case, accents, dots and doubled spaces removed, so "A. Josephy" and
 *  "a josephy" are one string. Letters only survive, because everything else
 *  on a printed sheet ("(C)", "GK", "-39'") is decoration. */
export function normalizeName(name) {
  return String(name || "")
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[’'`]/g, "")
    .replace(/[^a-z\s-]/g, " ")
    .replace(/-/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

const tokens = (name) => normalizeName(name).split(" ").filter(Boolean);

/** Could these two printed names be one person?
 *
 *  The same surname, and nothing to disagree with in front of it: one side is
 *  a bare surname ("Shema", the way a report's card list writes it), or an
 *  initial that the other side's first name starts with ("I. Shema" against
 *  "Innocent Shema", the way a league graphic writes it). Either way round,
 *  because the abbreviated one is as likely to be the saved name as the
 *  printed one. Two full first names never match — "John Banda" is not Peter
 *  Banda however few Bandas there are. */
// "Banda Jnr" is a Banda. A suffix is how a club tells two of them apart, so
// it is set aside only to find the surname, and preferred again when it is
// there — see personCandidates.
const SUFFIX = /^(jnr|jr|junior|snr|sr)$/;
const suffixOf = (t) => (t.length > 1 && SUFFIX.test(t[t.length - 1]) ? t[t.length - 1] : "");
const core = (t) => (suffixOf(t) ? t.slice(0, -1) : t);

function compatible(rawA, rawB) {
  const a = core(rawA);
  const b = core(rawB);
  if (!a.length || !b.length || a[a.length - 1] !== b[b.length - 1]) return false;
  if (a.length === 1 || b.length === 1) return true;
  const initialOf = (x, y) => x.length === 2 && x[0].length === 1
    && y[0].startsWith(x[0]);
  return initialOf(a, b) || initialOf(b, a);
}

/** Who in `people` this printed name is — or null.
 *
 *  UNIQUE OR NOTHING, and that is the whole rule. A wrong link puts a match on
 *  the wrong person's profile, and it looks exactly like a right one; an
 *  unlinked name costs the reporter one tap on a box that is already on
 *  screen. So an exact name first, then compatible() — and two candidates at
 *  either step is no answer, never the first of them.
 *
 *  `nameOf` reads a candidate's name, so the same rule matches sheet rows,
 *  squad chips and saved goals. */
export function findPerson(raw, people, nameOf = (p) => p.name) {
  const hits = personCandidates(raw, people, nameOf);
  return hits.length === 1 ? hits[0] : null;
}

/** Everyone in `people` this printed name could be: the exact matches if
 *  there are any, otherwise every compatible() one. findPerson is this with
 *  "exactly one" on the end; the paste box wants the list itself, because two
 *  Mwales are a question the shirt number beside the name can answer.
 *
 *  A printed suffix narrows the list to the people who carry it — "Banda Jnr"
 *  against Peter Banda and John Banda Jnr is John — and is ignored when nobody
 *  does, because a graphic adds "Jnr" that the registry never had. */
export function personCandidates(raw, people, nameOf = (p) => p.name) {
  const want = tokens(raw);
  if (!want.length) return [];
  const pool = (people || []).map((p) => ({ p, t: tokens(nameOf(p)) }))
    .filter((c) => c.t.length);

  const exact = pool.filter((c) => c.t.join(" ") === want.join(" "));
  if (exact.length) return exact.map((c) => c.p);
  let hits = pool.filter((c) => compatible(want, c.t));
  const suffix = suffixOf(want);
  if (suffix && hits.some((c) => suffixOf(c.t) === suffix)) {
    hits = hits.filter((c) => suffixOf(c.t) === suffix);
  }
  return hits.map((c) => c.p);
}

// ── Which match, which side ──────────────────────────────────────────────────

/** Does this extracted row carry anything beyond a score? */
export function hasReport(source) {
  if (!source) return false;
  const o = source.officials || {};
  return Boolean((source.team_sheets || []).length
    || (source.substitutions || []).length
    || (source.cards || []).length
    || source.motm_raw
    || (source.coaches || []).length
    || o.referee || o.assistant_1 || o.assistant_2 || o.fourth_official
    || (source.scorers || []).some((s) => s.assist_raw));
}

/** The extracted row and resolved item for one match, or null.
 *
 *  Joined by idx exactly as importScorerDrafts joins scorers:
 *  resolve_import_candidates numbers its items 1..n over extracted.results in
 *  order, so results[idx - 1] IS the row that item came from.
 *
 *  TWO WAYS TO BE THIS MATCH. The resolver chose it (green or yellow), or the
 *  reporter did: a club's report often prints no match date the importer will
 *  trust, and two clubs meet twice a season, so the commonest red is "several
 *  fixtures" — and the review screen then lists them, each a link here. That
 *  tap is the choice; a fixture the resolver never offered is never accepted,
 *  and a red item with no fixtures ("team not found") gives nothing to choose.
 *  The orientation comes from whichever answered: the match, or the fixture. */
export function reportFor(extracted, resolved, matchId) {
  const items = resolved?.items || [];
  const chosen = items.find(
    (it) => it.match?.match_id === matchId && it.confidence !== "red");
  const picked = chosen ? null : items.find(
    (it) => (it.fixtures || []).some((f) => f.match_id === matchId));
  const item = chosen || picked;
  if (!item) return null;
  const source = (extracted?.results || [])[Number(item.idx) - 1];
  if (!source) return null;
  const orientation = chosen ? item.match.orientation
    : item.fixtures.find((f) => f.match_id === matchId).orientation;
  return { item, source, flipped: orientation === "flipped" };
}

const teamKey = (name) => String(name || "").normalize("NFD")
  .replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]/g, "");
export const sameTeam = (a, b) => {
  const x = teamKey(a);
  const y = teamKey(b);
  return Boolean(x && y) && (x === y
    || (Math.min(x.length, y.length) >= 6 && (x.includes(y) || y.includes(x))));
};

/** Which way round a report printed THIS match, by the team names alone —
 *  for a report read from the match's own screen that the resolver could not
 *  place (a name it has no alias for). The reporter chose the match, so the
 *  only question left is orientation, and it is answered only when both names
 *  agree one way and not the other. Otherwise null, and nothing is staged. */
export function orientationByName(source, homeName, awayName) {
  const h = source?.home_team_raw;
  const a = source?.away_team_raw;
  const asGiven = sameTeam(h, homeName) && sameTeam(a, awayName);
  const flipped = sameTeam(h, awayName) && sameTeam(a, homeName);
  if (asGiven === flipped) return null;
  return { flipped };
}

/** A printed side, as the fixture is stored. THE ORIENTATION GOES WITH IT:
 *  a report that wrote the fixture the other way round has its sheets on the
 *  other side too — scorersFromImport's rule, one field over. */
export function teamFor(teamSide, { homeTeamId, awayTeamId, flipped = false }) {
  if (teamSide === "home") return flipped ? awayTeamId : homeTeamId;
  if (teamSide === "away") return flipped ? homeTeamId : awayTeamId;
  return "";
}

// ── Team sheets ──────────────────────────────────────────────────────────────

function sheetRow(p, role) {
  return {
    player_name: p.name_raw, player_id: "",
    shirt_number: p.shirt_number || "", position: p.position || "",
    role, captain: Boolean(p.captain), motm: false,
    minute_on: "", minute_off: "", replaced_player: "",
    yellow_card: false, yellow_red_card: false, red_card: false,
  };
}

const CARD_RANK = { yellow: 1, second_yellow: 2, red: 3 };
const cardRank = (row) => (row.red_card ? 3 : row.yellow_red_card ? 2
  : row.yellow_card ? 1 : 0);

function giveCard(row, card) {
  // The most severe card a player received is the one the control can show,
  // and a second yellow arrives in most reports as "yellow" AND "red" — the
  // database refuses a row holding both (lineups_card_states).
  if (CARD_RANK[card] <= cardRank(row)) return;
  row.yellow_card = card === "yellow";
  row.yellow_red_card = card === "second_yellow";
  row.red_card = card === "red";
}

const CARD_WORDS = { yellow: "yellow card", second_yellow: "second yellow",
                     red: "red card" };

/** The report's sheets, as save_lineup rows keyed by team_id.
 *
 *  `ctx` is {homeTeamId, awayTeamId, flipped, teamName(id)}.
 *  Returns {sheets: {teamId: rows}, unplaced: [sentence]}. */
export function sheetsFromReport(source, ctx) {
  const sheets = {};
  const unplaced = [];
  const name = (id) => (ctx.teamName ? ctx.teamName(id) : id);

  (source?.team_sheets || []).forEach((t) => {
    const teamId = teamFor(t.team_side, ctx);
    if (!teamId) {
      unplaced.push("A team sheet in the report could not be tied to either "
        + "side, so it was left out.");
      return;
    }
    // Two sheets for one side is a misread; the first one wins rather than
    // the two being merged into a squad of thirty.
    if (sheets[teamId]) return;
    const rows = [];
    const seen = new Set();
    const push = (p, role) => {
      const key = normalizeName(p.name_raw);
      if (!key || seen.has(key)) return;
      seen.add(key);
      rows.push(sheetRow(p, role));
    };
    (t.starting || []).forEach((p) => push(p, "starting"));
    (t.bench || []).forEach((p) => push(p, "unused_sub"));
    // The armband is one per side. A second "(C)" is a vice-captain misread,
    // or the captain named twice; the first stands.
    let captained = false;
    rows.forEach((r) => {
      if (r.captain && captained) r.captain = false;
      if (r.captain) captained = true;
    });
    sheets[teamId] = rows;
  });

  const rowsOf = (teamId) => sheets[teamId] || [];
  const byName = (rows, raw) => findPerson(raw, rows, (r) => r.player_name);

  // ── Substitutions ──
  (source?.substitutions || []).forEach((x) => {
    const teamId = teamFor(x.team_side, ctx);
    const rows = rowsOf(teamId);
    if (!teamId || !rows.length) return;          // no sheet: nothing to edit
    let on = byName(rows.filter((r) => r.role !== "starting"), x.on_raw);
    if (!on) {
      // Named coming on but not listed on the bench. They played, so they
      // belong on the sheet; leaving them off would make the starter they
      // replaced look like they played ninety minutes.
      on = sheetRow({ name_raw: x.on_raw }, "unused_sub");
      rows.push(on);
    }
    const off = byName(rows.filter((r) => r.role === "starting"), x.off_raw);
    on.role = "sub_on";
    on.minute_on = x.minute || "";
    on.replaced_player = off ? off.player_name : "";
    if (!off) {
      unplaced.push(`${on.player_name} came on for ${x.off_raw || "someone"}, `
        + `who is not in ${possessive(name(teamId))} starting XI — set who `
        + "they replaced on their row.");
    }
    if (!on.minute_on) {
      unplaced.push(`The report gives no minute for ${on.player_name} coming `
        + "on — type it on their row, or the sheet cannot be saved.");
    }
  });

  // ── Cards ──
  (source?.cards || []).forEach((c) => {
    const word = CARD_WORDS[c.card] || "card";
    if (c.is_player === false) {
      unplaced.push(`${c.person_raw}'s ${word} is not recorded: a card can only `
        + "be kept against a player on the team sheet.");
      return;
    }
    const teamId = teamFor(c.team_side, ctx);
    const candidates = teamId ? [teamId]
      : [ctx.homeTeamId, ctx.awayTeamId].filter((id) => rowsOf(id).length);
    const hits = candidates
      .map((id) => byName(rowsOf(id), c.person_raw)).filter(Boolean);
    if (hits.length === 1) { giveCard(hits[0], c.card); return; }
    const where = teamId && !rowsOf(teamId).length
      ? `the report has no ${name(teamId)} team sheet`
      : teamId ? `they are not on ${possessive(name(teamId))} sheet`
        : "the report does not say which side";
    unplaced.push(`${c.person_raw}'s ${word} is not recorded: ${where}.`);
  });

  // ── Man of the match ──
  if (source?.motm_raw) {
    // "Ernest Petro-Silver Strikers" is how one club prints it: the name, then
    // the club. When the whole string names nobody, a sheet row whose full
    // name it STARTS with is the player — still one row or none.
    const motmKey = normalizeName(source.motm_raw);
    const byPrefix = (rows) => {
      const hits = rows.filter((r) => {
        const k = normalizeName(r.player_name);
        return k && motmKey.startsWith(`${k} `);
      });
      return hits.length === 1 ? hits[0] : null;
    };
    const hits = Object.values(sheets)
      .map((rows) => byName(rows, source.motm_raw) || byPrefix(rows))
      .filter(Boolean);
    if (hits.length === 1) {
      hits[0].motm = true;
      // An unused substitute cannot be man of the match (save_lineup), and a
      // report naming one has read the bench wrong — say so, keep the star off.
      if (hits[0].role === "unused_sub") {
        hits[0].motm = false;
        unplaced.push(`${source.motm_raw} is named man of the match but is an `
          + "unused substitute on the sheet, so the award was left off.");
      }
    } else {
      unplaced.push(`Man of the match ${source.motm_raw} is not recorded: the `
        + "award is kept on the team sheet, and they are not on one from "
        + "this report.");
    }
  }

  return { sheets, unplaced };
}

/** Who in this side's squad each sheet row is.
 *
 *  Returns one {kind, player, options} per row, in order:
 *
 *    squad    one person in the squad — by name, or by name AND the shirt
 *             when the name alone fits two ("23 MWALE" with two Mwales).
 *    choose   several squad members fit and the shirt does not settle it.
 *    none     nobody in the squad fits; the screen asks the database next.
 *
 *  A shirt on its own never links anyone: numbers are reissued, and a wrong
 *  link looks exactly like a right one. ONE PLAYER, ONE ROW — someone already
 *  claimed by a row is no longer a candidate for the others, which is what
 *  lets the second of two Mwales resolve once the first has. Settled to a
 *  fixed point, so the answer does not depend on which row was printed first. */
export function matchRowsToSquad(rows, squad) {
  const known = (squad || []).filter((p) => p.player_id);
  const cands = rows.map((r) => {
    const hits = personCandidates(r.player_name, known);
    if (hits.length <= 1 || !r.shirt_number) return hits;
    const byShirt = hits.filter((p) => String(p.shirt_number || "") === r.shirt_number);
    return byShirt.length === 1 ? byShirt : hits;
  });
  const chosen = rows.map(() => null);
  const taken = new Set();
  let moved = true;
  while (moved) {
    moved = false;
    cands.forEach((hits, i) => {
      if (chosen[i]) return;
      const free = hits.filter((p) => !taken.has(p.player_id));
      if (free.length === 1) {
        chosen[i] = free[0];
        taken.add(free[0].player_id);
        moved = true;
      }
    });
  }
  return rows.map((_r, i) => {
    if (chosen[i]) return { kind: "squad", player: chosen[i], options: [] };
    const free = cands[i].filter((p) => !taken.has(p.player_id));
    return free.length > 1
      ? { kind: "choose", player: null, options: free }
      : { kind: "none", player: null, options: [] };
  });
}

/** Link sheet rows to players this club has already fielded.
 *
 *  The paste box's rule (matchRowsToSquad): findPerson's, with the printed
 *  shirt as the tiebreak between two people of one surname. A squad entry
 *  with no player_id is someone known only by a name, and is not something to
 *  link TO. A linked row also takes the squad's shirt and position where the
 *  report gave none. Rows already linked keep their link and take their
 *  player out of the running. Returns how many rows it linked. */
export function linkToSquad(rows, squad) {
  const taken = new Set(rows.map((r) => r.player_id).filter(Boolean));
  const open = rows.filter((r) => !r.player_id);
  const free = (squad || []).filter((p) => p.player_id && !taken.has(p.player_id));
  let linked = 0;
  matchRowsToSquad(open, free).forEach((m, i) => {
    if (m.kind !== "squad") return;
    const row = open[i];
    row.player_id = m.player.player_id;
    row.shirt_number = row.shirt_number || m.player.shirt_number || "";
    row.position = row.position || m.player.position || "";
    linked += 1;
  });
  return linked;
}

/** What would stop this sheet saving, as sentences. save_lineup is the rule
 *  (a sub_on needs minute_on, at most eleven starters); this only says it
 *  before the round trip, naming the row. */
export function sheetProblems(rows) {
  const out = [];
  const starters = rows.filter((r) => r.role === "starting").length;
  if (starters > 11) out.push(`${starters} in the starting XI — that is too many.`);
  rows.filter((r) => r.role === "sub_on" && !String(r.minute_on || "").trim())
    .forEach((r) => out.push(`${r.player_name} came on but has no minute.`));
  return out;
}

// ── Goals ────────────────────────────────────────────────────────────────────

const goalTypeOf = (s) => (s.penalty ? "penalty"
  : s.method === "header" || s.method === "free_kick" ? s.method : "");

/** The report's scorers against the goals already saved for this match.
 *
 *  A scorer the match already has is PAIRED, and what it adds — an assist, a
 *  minute, a header — becomes an amendment for the columns that are blank
 *  (amend_match_goals, 0054). That is the common case in the Premiership: the
 *  league's graphic put the scorer in on the day, and the club's report is
 *  the first thing to say who set it up.
 *
 *  A scorer it does not have is a goal to ADD, if the side has a goal left
 *  without a scorer — offerScorers' room rule. One with a different name from
 *  the goal already saved is neither: the two sources disagree about who
 *  scored, and that is a sentence for a person, not a guess for this file.
 *
 *  An own goal is never paired by side and never added: goals.team_id is the
 *  beneficiary and the report only says where the name was printed, so it
 *  asks — #/results' rule, unchanged.
 *
 *  `saved` rows are {goal_id, team_id, reported_player_name, player_id,
 *  assist_player_id, minute, goal_type}; `nameOf(goal)` gives a saved goal's
 *  scorer as the screen shows it. `score` is {[teamId]: goals}. */
export function reconcileGoals(saved, scorers, ctx, score,
                               nameOf = (g) => g.reported_player_name) {
  const amend = [];
  const add = [];
  const unplaced = [];
  const used = new Set();
  const name = (id) => (ctx.teamName ? ctx.teamName(id) : id);
  const savedFor = (teamId) => (saved || []).filter(
    (g) => g.team_id === teamId && !used.has(g.goal_id));

  (scorers || []).forEach((s) => {
    if (!s.player_raw) return;
    if (s.own_goal) {
      const og = (saved || []).filter((g) => g.goal_type === "own_goal"
        && !used.has(g.goal_id));
      const hit = findPerson(s.player_raw, og, nameOf);
      if (hit) { used.add(hit.goal_id); return; }
      unplaced.push(`An own goal by ${s.player_raw} is in the report — add it `
        + "by hand under Goalscorers, choosing the side it counted for.");
      return;
    }
    const teamId = teamFor(s.team_side, ctx);
    if (!teamId) {
      unplaced.push(`The report does not say which side ${s.player_raw} scored `
        + "for — add the goal by hand if it is missing.");
      return;
    }
    const hit = findPerson(s.player_raw, savedFor(teamId), nameOf);
    const minute = Number.isInteger(s.minute) ? String(s.minute) : "";
    const goalType = goalTypeOf(s);
    if (hit) {
      used.add(hit.goal_id);
      const change = { goal_id: hit.goal_id, team_id: teamId,
                       scorer: nameOf(hit) };
      if (s.assist_raw && !hit.assist_player_id) change.assist_raw = s.assist_raw;
      if (minute && !hit.minute) change.minute = minute;
      if (goalType && !hit.goal_type) change.goal_type = goalType;
      if (change.assist_raw || change.minute || change.goal_type) amend.push(change);
      return;
    }
    const room = (score?.[teamId] ?? 0) - (saved || []).filter(
      (g) => g.team_id === teamId).length - add.filter((g) => g.team_id === teamId).length;
    if (room > 0) {
      add.push({ team_id: teamId, player_name: s.player_raw, minute,
                 goal_type: goalType, assist_raw: s.assist_raw || "" });
      return;
    }
    const already = savedFor(teamId).map(nameOf).filter(Boolean);
    unplaced.push(already.length
      ? `The report names ${s.player_raw} as a ${name(teamId)} scorer; this `
        + `match has ${already.join(", ")}. Check which is right before `
        + "changing anything."
      : `The report names ${s.player_raw} as a ${name(teamId)} scorer, but `
        + `every ${name(teamId)} goal already has one.`);
  });

  return { amend, add, unplaced };
}

// ── Officials ────────────────────────────────────────────────────────────────

/** The report's officials and coaches, for the columns that are still blank.
 *  Returns {column: name}. `current` is the matches row's officials columns. */
export function officialsFromReport(source, ctx, current = {}) {
  const out = {};
  const fill = (column, value) => {
    const v = String(value || "").trim();
    if (v && !String(current[column] || "").trim()) out[column] = v;
  };
  const o = source?.officials || {};
  fill("referee", o.referee);
  fill("assistant_referee_1", o.assistant_1);
  fill("assistant_referee_2", o.assistant_2);
  fill("fourth_official", o.fourth_official);
  (source?.coaches || []).forEach((c) => {
    const teamId = teamFor(c.team_side, ctx);
    if (teamId && teamId === ctx.homeTeamId) fill("home_coach", c.name_raw);
    if (teamId && teamId === ctx.awayTeamId) fill("away_coach", c.name_raw);
  });
  return out;
}
