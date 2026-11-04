/* Two teams side by side, and a team's last five: the rules, without the
 * canvas.
 *
 * WHY THIS FILE EXISTS. The pre-match post a club makes after its line-up is
 * the comparison: where both sides stand, what they have scored and let in,
 * how they have been going, how they have done against each other. Every
 * figure on it is already in the database, mostly as scores. #/graphics
 * could draw a fixture and a line-up and nothing that said what the match
 * MEANT.
 *
 * WHAT IS COUNTED, and why each limit is there:
 *
 *   * This competition, this season — the period people already know as
 *     "the table", so the position on the board is the position on the site
 *     (src/standings.py's rules: points, then goal difference, then goals
 *     scored, then name, inside a cluster where there are clusters).
 *     Head-to-head is the one exception: it reaches back across every
 *     season and competition, because two meetings a season is not a
 *     history.
 *   * Strictly BEFORE the match (the match page's rule). A board drawn after
 *     the final whistle still describes the match going in, never a table
 *     the match itself moved.
 *   * played and awarded, with both scores — what the table counts.
 *   * A top scorer only where every one of that side's goals has a scorer
 *     row. The board is posted publicly, and "top scorer: 4" drawn from half
 *     the goals is a false claim about everybody else (stats.js's rule for
 *     cards, applied to goals).
 *
 * It imports nothing, so `node --test` can ask it without a browser.
 */

export const FORM_LENGTH = 5;

/** The rows a comparison board can show: [key, label, shown by default].
 *  The screen draws a tick box for each; the board draws what is ticked and
 *  what the data can answer. */
export const COMPARE_ROWS = [
  ["position", "League position", true],
  ["points", "Points", true],
  ["record", "Won · drawn · lost", true],
  ["goals_for", "Goals scored", true],
  ["goals_against", "Goals conceded", true],
  ["goal_diff", "Goal difference", false],
  ["clean_sheets", "Clean sheets", true],
  ["failed_to_score", "Failed to score", false],
  ["venue_record", "Home record v away record", false],
  ["ppg", "Points per game", false],
  ["form", `Form (last ${FORM_LENGTH})`, true],
  ["top_scorer", "Top scorer", true],
  ["h2h", "Head to head", true],
];

export function isPlayed(m) {
  return (m.status === "played" || m.status === "awarded")
    && Number.isInteger(m.home_goals) && Number.isInteger(m.away_goals)
    && m.source_type !== "placeholder";
}

/** Played matches strictly before `match` (by date; same-day matches are
 *  not "before", because a kick-off time is optional and an order inside a
 *  day would be a guess), never the match itself. A match with no date yet
 *  is preceded by everything played. Oldest first. */
export function playedBefore(matches, match) {
  const cut = match?.date || "";
  return (matches || [])
    .filter((m) => isPlayed(m) && m.match_id !== match?.match_id
      && (!cut || (m.date && m.date < cut)))
    .sort((a, b) => String(a.date).localeCompare(String(b.date))
      || String(a.kickoff || "").localeCompare(String(b.kickoff || "")));
}

const outcome = (gf, ga) => (gf > ga ? "W" : gf < ga ? "L" : "D");

/** One side's view of a match, or null when it was not in it. */
function sideOf(m, teamId) {
  if (m.home_team_id === teamId) {
    return { gf: m.home_goals, ga: m.away_goals, home: true, opponentId: m.away_team_id };
  }
  if (m.away_team_id === teamId) {
    return { gf: m.away_goals, ga: m.home_goals, home: false, opponentId: m.home_team_id };
  }
  return null;
}

const blankRecord = () => ({ played: 0, won: 0, drawn: 0, lost: 0 });

/** Everything a score says about one team over `matches` (already the
 *  period's). Points are the caller's, because they need the competition's
 *  points per win/draw and the entry's adjustment. */
export function teamRecord(matches, teamId) {
  const r = { ...blankRecord(), gf: 0, ga: 0, cleanSheets: 0, failedToScore: 0,
    home: blankRecord(), away: blankRecord() };
  (matches || []).forEach((m) => {
    const s = sideOf(m, teamId);
    if (!s) return;
    const res = outcome(s.gf, s.ga);
    const venue = s.home ? r.home : r.away;
    [r, venue].forEach((rec) => {
      rec.played += 1;
      if (res === "W") rec.won += 1;
      else if (res === "D") rec.drawn += 1;
      else rec.lost += 1;
    });
    r.gf += s.gf;
    r.ga += s.ga;
    if (s.ga === 0) r.cleanSheets += 1;
    if (s.gf === 0) r.failedToScore += 1;
  });
  return r;
}

/** The table, as {team_id: {position, points, group, played}}.
 *
 *  `entries` are the competition-season's ({team_id, group,
 *  points_adjustment, status, name}); a team with no match yet is in it with
 *  zeros, and a withdrawn or expelled one is not ranked. `rules` is
 *  competition_seasons' {points_win, points_draw}. */
export function leagueTable(matches, entries, rules = {}) {
  const win = Number.isInteger(rules.points_win) ? rules.points_win : 3;
  const draw = Number.isInteger(rules.points_draw) ? rules.points_draw : 1;
  const rows = (entries || [])
    .filter((e) => !["withdrawn", "expelled"].includes(e.status))
    .map((e) => {
      const rec = teamRecord(matches, e.team_id);
      return { team_id: e.team_id, name: e.name || e.team_id, group: e.group || "",
        played: rec.played, gd: rec.gf - rec.ga, gf: rec.gf,
        points: rec.won * win + rec.drawn * draw + (e.points_adjustment || 0) };
    });
  // src/standings.py: named groups A-Z, the unlabelled one last; then points,
  // goal difference, goals scored, name.
  rows.sort((a, b) => (a.group === "") - (b.group === "")
    || a.group.toLowerCase().localeCompare(b.group.toLowerCase())
    || b.points - a.points || b.gd - a.gd || b.gf - a.gf
    || a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
  const seen = {};
  const out = {};
  rows.forEach((r) => {
    seen[r.group] = (seen[r.group] || 0) + 1;
    out[r.team_id] = { position: seen[r.group], points: r.points, group: r.group,
      played: r.played, size: 0 };
  });
  Object.values(out).forEach((r) => { r.size = seen[r.group]; });
  return out;
}

/** A team's last `n` results, oldest first (the way a form guide is read,
 *  left to right towards today): {result, gf, ga, opponentId, home, date}. */
export function form(matches, teamId, n = FORM_LENGTH) {
  const mine = [];
  (matches || []).forEach((m) => {
    const s = sideOf(m, teamId);
    if (s) mine.push({ result: outcome(s.gf, s.ga), gf: s.gf, ga: s.ga,
      opponentId: s.opponentId, home: s.home, date: m.date || "" });
  });
  return mine.slice(-n);
}

/** Points from a form list, at `win`/`draw` a result. */
export function formPoints(list, win = 3, draw = 1) {
  return (list || []).reduce((sum, f) => sum
    + (f.result === "W" ? win : f.result === "D" ? draw : 0), 0);
}

/** Every earlier meeting between `a` and `b`, any competition or season:
 *  {played, aWins, draws, bWins, last}, `last` being the newest meeting as
 *  a's and b's goals plus its date and where it was. */
export function headToHead(matches, a, b) {
  const out = { played: 0, aWins: 0, draws: 0, bWins: 0, last: null };
  (matches || []).forEach((m) => {
    const s = sideOf(m, a);
    if (!s || s.opponentId !== b) return;
    out.played += 1;
    const res = outcome(s.gf, s.ga);
    if (res === "W") out.aWins += 1;
    else if (res === "L") out.bWins += 1;
    else out.draws += 1;
    out.last = { aGoals: s.gf, bGoals: s.ga, aHome: s.home, date: m.date || "",
      competitionId: m.competition_id || "" };
  });
  return out;
}

/** The side's top scorer over these matches, or null when the goals cannot
 *  say: `goals` are goal rows ({match_id, team_id, player_id, name,
 *  goal_type}) and `goalsFor` is the side's total from the scores. Every
 *  goal must have a row (an own goal for this side counts as covered and
 *  scores for nobody here), or there is no top scorer to name. Joint tops
 *  are all named, up to two; three or more is a count. */
export function topScorer(goals, teamId, goalsFor, matchIds = null) {
  const keep = matchIds ? new Set(matchIds) : null;
  const rows = (goals || []).filter((g) => g.team_id === teamId
    && (!keep || keep.has(g.match_id)));
  if (!goalsFor || rows.length !== goalsFor) return null;
  const tally = new Map();
  rows.forEach((g) => {
    if (g.goal_type === "own_goal" || g.player_id === "CAF_MW_UNKNOWN") return;
    const key = g.player_id || `name:${String(g.name || "").toLowerCase()}`;
    if (!g.player_id && !g.name) return;
    const t = tally.get(key) || { name: g.name || g.player_id, goals: 0 };
    t.goals += 1;
    tally.set(key, t);
  });
  if (!tally.size) return null;
  const best = Math.max(...[...tally.values()].map((t) => t.goals));
  const top = [...tally.values()].filter((t) => t.goals === best)
    .sort((x, y) => x.name.localeCompare(y.name));
  return { names: top.length <= 2 ? top.map((t) => t.name) : [], joint: top.length,
    goals: best };
}

const recordText = (r) => `${r.won}-${r.drawn}-${r.lost}`;
const ordinal = (n) => {
  const s = n % 100 >= 11 && n % 100 <= 13 ? "th"
    : ({ 1: "st", 2: "nd", 3: "rd" })[n % 10] || "th";
  return `${n}${s}`;
};

/** The numeric and text rows a comparison board draws, in COMPARE_ROWS
 *  order, for the keys ticked. Each is {key, label, home, away, homeValue,
 *  awayValue, better} — `home`/`away` the text drawn, the values the bar's,
 *  `better` the side drawn in the accent ("" for level, or a row with no
 *  better). Rows the data cannot fill (no table for a cup) are left out.
 *
 *  `input` is {home, away} each {record, table}, plus `pointsWin`/`pointsDraw`. */
export function statRows(input, keys) {
  const want = new Set(keys);
  const { home: h, away: a } = input;
  const win = input.pointsWin ?? 3;
  const draw = input.pointsDraw ?? 1;
  const out = [];
  const add = (key, label, hv, av, { text = String, lowerIsBetter = false,
    bar = true } = {}) => {
    if (!want.has(key)) return;
    const better = hv === av ? "" : (hv > av) !== lowerIsBetter ? "home" : "away";
    out.push({ key, label, home: text(hv, h), away: text(av, a),
      homeValue: bar ? hv : null, awayValue: bar ? av : null, better });
  };
  const pts = (side) => (side.table ? side.table.points
    : side.record.won * win + side.record.drawn * draw);
  const hasTable = Boolean(h.table && a.table);

  if (hasTable) {
    add("position", "POSITION", h.table.position, a.table.position,
      { text: (v) => ordinal(v), lowerIsBetter: true, bar: false });
  }
  if (hasTable) add("points", "POINTS", h.table.points, a.table.points);
  if (want.has("record")) {
    out.push({ key: "record", label: "W · D · L", home: recordText(h.record),
      away: recordText(a.record), homeValue: null, awayValue: null,
      better: h.record.won === a.record.won ? ""
        : h.record.won > a.record.won ? "home" : "away" });
  }
  add("goals_for", "GOALS SCORED", h.record.gf, a.record.gf);
  add("goals_against", "GOALS CONCEDED", h.record.ga, a.record.ga, { lowerIsBetter: true });
  add("goal_diff", "GOAL DIFFERENCE", h.record.gf - h.record.ga, a.record.gf - a.record.ga,
    { text: (v) => (v > 0 ? `+${v}` : String(v)), bar: false });
  add("clean_sheets", "CLEAN SHEETS", h.record.cleanSheets, a.record.cleanSheets);
  add("failed_to_score", "FAILED TO SCORE", h.record.failedToScore,
    a.record.failedToScore, { lowerIsBetter: true });
  if (want.has("venue_record")) {
    const hw = h.record.home;
    const aw = a.record.away;
    const share = (r) => (r.played ? (r.won * win + r.drawn * draw) / (r.played * win) : 0);
    out.push({ key: "venue_record", label: "HOME · AWAY RECORD", home: recordText(hw),
      away: recordText(aw), homeValue: null, awayValue: null,
      better: share(hw) === share(aw) ? "" : share(hw) > share(aw) ? "home" : "away" });
  }
  if (want.has("ppg")) {
    const ppg = (side) => (side.record.played ? pts(side) / side.record.played : 0);
    add("ppg", "POINTS PER GAME", Number(ppg(h).toFixed(2)), Number(ppg(a).toFixed(2)),
      { text: (v) => v.toFixed(2), bar: false });
  }
  return out;
}

/** "KB 4 · 2 draws · CU 1", and the last meeting as a line, for the strip
 *  under a comparison. Names are the caller's (short or full). */
export function headToHeadLines(h2h, homeName, awayName) {
  if (!h2h || !h2h.played) return null;
  const draws = `${h2h.draws} draw${h2h.draws === 1 ? "" : "s"}`;
  const tally = `${homeName} ${h2h.aWins}  ·  ${draws}  ·  ${awayName} ${h2h.bWins}`;
  const l = h2h.last;
  const score = l.aHome ? `${homeName} ${l.aGoals}–${l.bGoals} ${awayName}`
    : `${awayName} ${l.bGoals}–${l.aGoals} ${homeName}`;
  return { tally, last: score, lastDate: l.date };
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
  "Nov", "Dec"];

/** "18 Oct", or "18 Oct 2025" when it is not this season's year. */
export function shortDate(iso, year = "") {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  if (!m) return "";
  const day = `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]}`;
  return year && m[1] !== String(year) ? `${day} ${m[1]}` : day;
}

/** Everything both boards draw for one match, from what the screen fetched:
 *
 *    match       the match ({match_id, date, home_team_id, away_team_id})
 *    matches     this competition-season's matches, any status
 *    h2hMatches  every meeting of the two teams, any competition or season
 *    entries     this competition-season's entries (+ name), [] for a cup
 *    rules       competition_seasons' {points_win, points_draw}
 *    goals       goal rows for the two teams this competition-season
 *                ({match_id, team_id, player_id, name, goal_type})
 *    teamName    id → display name
 *    keys        the ticked COMPARE_ROWS keys
 *    league      false for a cup: no table, so no position or points
 *
 *  Returns {rows, form, topScorer, h2h, summaries}, each part null (or
 *  empty) when it was not ticked or the data cannot fill it. */
export function buildCompare(input) {
  const { match, teamName = (id) => id } = input;
  const keys = new Set(input.keys || COMPARE_ROWS.filter((r) => r[2]).map((r) => r[0]));
  const before = playedBefore(input.matches, match);
  const homeId = match.home_team_id;
  const awayId = match.away_team_id;
  const win = input.rules?.points_win ?? 3;
  const draw = input.rules?.points_draw ?? 1;
  const table = input.league === false || !(input.entries || []).length ? null
    : leagueTable(before, input.entries, input.rules || {});
  const side = (id) => ({ record: teamRecord(before, id), table: table?.[id] || null });
  const home = side(homeId);
  const away = side(awayId);

  const year = (match.date || "").slice(0, 4);
  const formOf = (id) => form(before, id).map((f) => ({ ...f,
    opponent: teamName(f.opponentId), dateLabel: shortDate(f.date) }));
  const forms = { home: formOf(homeId), away: formOf(awayId) };
  const summary = (list) => (list.length
    ? `${formPoints(list, win, draw)} pts from ${list.length * win}` : "");

  const ids = before.map((m) => m.match_id);
  const scorerText = (id, gf) => {
    const t = topScorer(input.goals, id, gf, ids);
    if (!t) return "";
    const who = t.names.length ? t.names.join(" & ") : `${t.joint} players`;
    return `${who} ${t.goals}`;
  };
  const topScorers = keys.has("top_scorer")
    ? { home: scorerText(homeId, home.record.gf), away: scorerText(awayId, away.record.gf) }
    : null;

  const meetings = playedBefore(input.h2hMatches, match);
  const h2hRaw = headToHead(meetings, homeId, awayId);
  let h2h = null;
  if (keys.has("h2h") && h2hRaw.played) {
    const lines = headToHeadLines(h2hRaw, teamName(homeId), teamName(awayId));
    h2h = { tally: lines.tally, last: `Last meeting: ${lines.last}  ·  ${
      shortDate(lines.lastDate, year)}` };
  }

  return {
    rows: statRows({ home, away, pointsWin: win, pointsDraw: draw }, [...keys]),
    form: keys.has("form") && (forms.home.length || forms.away.length) ? forms : null,
    forms,
    topScorer: topScorers && (topScorers.home || topScorers.away) ? topScorers : null,
    h2h,
    summaries: { home: summary(forms.home), away: summary(forms.away) },
    played: { home: home.record.played, away: away.record.played },
  };
}
