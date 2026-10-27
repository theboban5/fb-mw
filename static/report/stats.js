/* #/stats — period arithmetic and sums, with no DOM and no network in them.
 *
 * The screen answers the sentences reporters post: "27 goals on matchday 15
 * in the FDH Bank Premiership", "4 red cards this weekend across all
 * leagues". Every figure is a sum over rows of stats_match_facts (0062), one
 * row per played match carrying raw counts, so the questions worth testing
 * are all here: which dates "last weekend" means on a Tuesday, whether a 0-0
 * is two clean sheets, whether a red card from two team sheets is ever
 * allowed to read as a weekend's total. None of that can be asserted about a
 * rendered screen.
 *
 * It imports nothing, on purpose — results_grid.js's bargain. The moment this
 * file needs `document` or `supabase`, its tests stop meaning anything.
 *
 * Dates are "YYYY-MM-DD" strings throughout and the arithmetic is done in UTC,
 * so a phone's timezone cannot move a weekend by a day. "Today" is passed in
 * by the caller (the phone's local date, which in Malawi is CAT).
 */

// The order the chips appear in. "Dates" is the custom range; "matchday"
// needs exactly one competition, because matchday 15 means nothing across
// leagues that have played different numbers of rounds.
export const PERIODS = [
  { key: "this-weekend", label: "This weekend" },
  { key: "last-weekend", label: "Last weekend" },
  { key: "last-7",       label: "Last 7 days" },
  { key: "this-month",   label: "This month" },
  { key: "last-month",   label: "Last month" },
  { key: "season",       label: "This season" },
  { key: "matchday",     label: "Matchday" },
  { key: "custom",       label: "Dates" },
];

const DAY = 86400000;
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const ISO_RE = /^\d{4}-\d{2}-\d{2}$/;
export const isISODate = (s) => ISO_RE.test(String(s || ""))
  && !Number.isNaN(Date.parse(`${s}T00:00:00Z`));

const toTime = (iso) => Date.parse(`${iso}T00:00:00Z`);
const toISO = (t) => new Date(t).toISOString().slice(0, 10);
const addDays = (iso, n) => toISO(toTime(iso) + n * DAY);
const weekday = (iso) => new Date(toTime(iso)).getUTCDay();

/** The Friday–Sunday containing `iso`, or the next one if `iso` is Mon–Thu.
 *
 *  FRIDAY, NOT SATURDAY. A U20 match on a Friday evening is "the weekend's
 *  football" to everyone who reports it, and a Monday is not. Midweek rounds
 *  have their own chip (Last 7 days) and the label always prints the dates,
 *  so what a weekend meant is never a guess. */
function weekendOf(iso) {
  const d = weekday(iso);                  // 0 Sun … 6 Sat
  const toFriday = d === 0 ? -2 : d === 6 ? -1 : 5 - d;
  const fri = addDays(iso, toFriday);
  return { from: fri, to: addDays(fri, 2) };
}

/** Which chip a screen opens on: the weekend just played, until the next
 *  one starts. On Friday the new weekend is the one people are asking about. */
export function defaultPeriod(todayISO) {
  const d = weekday(todayISO);
  return d === 0 || d === 5 || d === 6 ? "this-weekend" : "last-weekend";
}

/** "Sat 24 Oct", "Fri 23 – Sun 25 Oct", "Fri 30 Oct – Sun 1 Nov",
 *  "Mon 28 Dec 2026 – Sun 3 Jan 2027". The year appears only when the range
 *  crosses one — or, with `year`, always. */
export function formatRange(from, to, { year = false } = {}) {
  const part = (iso, withMonth, withYear) => {
    const t = new Date(toTime(iso));
    return `${WEEKDAYS[t.getUTCDay()]} ${t.getUTCDate()}`
      + (withMonth ? ` ${MONTHS[t.getUTCMonth()]}` : "")
      + (withYear ? ` ${t.getUTCFullYear()}` : "");
  };
  if (!from && !to) return "";
  if (!to || from === to) return part(from || to, true, year);
  const crossYear = from.slice(0, 4) !== to.slice(0, 4);
  const sameMonth = from.slice(0, 7) === to.slice(0, 7);
  return `${part(from, !sameMonth, crossYear || (year && !sameMonth))} – `
    + `${part(to, true, crossYear || year)}`;
}

/** What a period chip means as a query.
 *
 *  Returns { kind: "dates", from, to, label } for anything bounded by the
 *  calendar, { kind: "season", label } for the active season (the season is
 *  seasons.status = 'active', never worked out from the clock — CLAUDE.md),
 *  and { kind: "matchday", label } for one competition's round. A custom
 *  range missing either end, or backwards, is null: there is nothing to ask
 *  the database yet, and the screen says so instead of guessing. */
export function periodRange(key, todayISO, { from = "", to = "",
                                             seasonLabel = "" } = {}) {
  const dates = (f, t) => ({ kind: "dates", from: f, to: t, label: formatRange(f, t) });
  switch (key) {
    case "this-weekend": {
      const w = weekendOf(todayISO);
      return dates(w.from, w.to);
    }
    case "last-weekend": {
      const w = weekendOf(todayISO);
      // On Mon–Thu weekendOf is the COMING weekend, so the one before it is
      // the one just played; on Fri–Sun it is this one, so last is a week
      // earlier. Either way: seven days before weekendOf.
      return dates(addDays(w.from, -7), addDays(w.to, -7));
    }
    case "last-7":
      return dates(addDays(todayISO, -6), todayISO);
    case "this-month":
      return dates(`${todayISO.slice(0, 7)}-01`, todayISO);
    case "last-month": {
      const first = `${todayISO.slice(0, 7)}-01`;
      const end = addDays(first, -1);
      return { kind: "dates", from: `${end.slice(0, 7)}-01`, to: end,
               label: monthLabel(end) };
    }
    case "season":
      return { kind: "season", label: seasonLabel ? `${seasonLabel} season` : "this season" };
    case "matchday":
      return { kind: "matchday", label: "" };
    case "custom":
      if (!isISODate(from) || !isISODate(to) || from > to) return null;
      return dates(from, to);
    default:
      return null;
  }
}

const monthLabel = (iso) => {
  const t = new Date(toTime(iso));
  return `${["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"][t.getUTCMonth()]} `
    + `${t.getUTCFullYear()}`;
};

// ── Sums ─────────────────────────────────────────────────────────────────────

const n = (v) => Number(v) || 0;

export const GOAL_TYPES = [
  { key: "g_penalty",   label: "Penalty" },
  { key: "g_free_kick", label: "Free kick" },
  { key: "g_header",    label: "Header" },
  { key: "g_open_play", label: "Open play" },
  { key: "g_own_goal",  label: "Own goal" },
];

/** Every total the screen shows, over any set of rows.
 *
 *  Clean sheets belong to a SIDE, so a 0-0 is two of them (0039's rule).
 *  Cards are counted only where a sheet exists and `sheetSides` says how
 *  many that is — a side with no sheet has no cards ON THE RECORD, which is
 *  not the same as none. `sentOff` is second yellows plus straight reds:
 *  both are a red card to anyone reading the result. */
export function summarize(rows) {
  const s = {
    matches: rows.length, sides: rows.length * 2, goals: 0,
    homeWins: 0, draws: 0, awayWins: 0, cleanSheets: 0,
    sheetSides: 0, sheetMatches: 0, yellow: 0, secondYellow: 0, red: 0, sentOff: 0,
    goalRows: 0, untyped: 0, typed: 0, withReferee: 0,
    types: Object.fromEntries(GOAL_TYPES.map((t) => [t.key, 0])),
  };
  const comps = new Set();
  rows.forEach((r) => {
    const h = n(r.home_goals);
    const a = n(r.away_goals);
    s.goals += h + a;
    if (h > a) s.homeWins += 1; else if (h < a) s.awayWins += 1; else s.draws += 1;
    if (a === 0) s.cleanSheets += 1;
    if (h === 0) s.cleanSheets += 1;
    s.sheetSides += (r.home_sheet ? 1 : 0) + (r.away_sheet ? 1 : 0);
    if (r.home_sheet || r.away_sheet) s.sheetMatches += 1;
    s.yellow += n(r.home_yellow) + n(r.away_yellow);
    s.secondYellow += n(r.home_second_yellow) + n(r.away_second_yellow);
    s.red += n(r.home_red) + n(r.away_red);
    s.goalRows += n(r.goal_rows);
    s.untyped += n(r.g_untyped);
    GOAL_TYPES.forEach((t) => { s.types[t.key] += n(r[t.key]); });
    if (r.referee_name) s.withReferee += 1;
    comps.add(r.competition_id);
  });
  s.sentOff = s.secondYellow + s.red;
  s.typed = s.goalRows - s.untyped;
  s.competitions = comps.size;
  s.goalsPerMatch = s.matches ? s.goals / s.matches : null;
  return s;
}

export const perMatchText = (v) => (v == null ? "—" : v.toFixed(2));

/** One summary per competition, most matches first. */
export function byCompetition(rows) {
  const groups = new Map();
  rows.forEach((r) => {
    if (!groups.has(r.competition_id)) {
      groups.set(r.competition_id, { id: r.competition_id,
                                     name: r.competition_name || r.competition_id,
                                     rows: [] });
    }
    groups.get(r.competition_id).rows.push(r);
  });
  return [...groups.values()]
    .map((g) => ({ id: g.id, name: g.name, ...summarize(g.rows) }))
    .sort((a, b) => b.matches - a.matches || a.name.localeCompare(b.name));
}

/** One line per team across whatever is selected: played, goals for and
 *  against, clean sheets. Keyed on team_id alone, so a club's first team in
 *  the league and the cup in the same weekend is one line — the period, not
 *  the competition, is the unit here. */
export function byTeam(rows) {
  const teams = new Map();
  const side = (id, name, gf, ga) => {
    if (!teams.has(id)) teams.set(id, { id, name: name || id, played: 0, gf: 0, ga: 0,
                                        cleanSheets: 0, wins: 0 });
    const t = teams.get(id);
    t.played += 1;
    t.gf += gf;
    t.ga += ga;
    if (ga === 0) t.cleanSheets += 1;
    if (gf > ga) t.wins += 1;
  };
  rows.forEach((r) => {
    side(r.home_team_id, r.home_name, n(r.home_goals), n(r.away_goals));
    side(r.away_team_id, r.away_name, n(r.away_goals), n(r.home_goals));
  });
  return [...teams.values()];
}

/** The top `limit` teams by a figure, ties broken by fewer games (a better
 *  rate), then by name so the order is stable. Zero never ranks. */
export function topTeams(teams, key, limit = 10) {
  return teams.filter((t) => t[key] > 0)
    .sort((a, b) => b[key] - a[key] || a.played - b.played || a.name.localeCompare(b.name))
    .slice(0, limit);
}

/** One line per referee: matches taken, and the cards shown in the ones with
 *  a team sheet.
 *
 *  Keyed on referee_id where the name resolved and on the name where it did
 *  not — the reported name lowercased, so "J. Phiri" typed twice is one line
 *  and two spellings are two, which is the honest cost of an unresolved name
 *  (it is the scorer table's rule, 0053). `perMatch` divides by matches WITH
 *  a sheet: dividing by every match would rank a referee lower for every
 *  team sheet nobody entered. */
export function byReferee(rows) {
  const refs = new Map();
  rows.forEach((r) => {
    if (!r.referee_name) return;
    const key = r.referee_id ? `id:${r.referee_id}` : `name:${r.referee_name.trim().toLowerCase()}`;
    if (!refs.has(key)) {
      refs.set(key, { key, id: r.referee_id || "", name: r.referee_name, matches: 0,
                      sheetMatches: 0, yellow: 0, secondYellow: 0, red: 0, cards: 0 });
    }
    const x = refs.get(key);
    x.matches += 1;
    if (r.home_sheet || r.away_sheet) x.sheetMatches += 1;
    x.yellow += n(r.home_yellow) + n(r.away_yellow);
    x.secondYellow += n(r.home_second_yellow) + n(r.away_second_yellow);
    x.red += n(r.home_red) + n(r.away_red);
  });
  return [...refs.values()]
    .map((x) => {
      const cards = x.yellow + x.secondYellow + x.red;
      return { ...x, cards, perMatch: x.sheetMatches ? cards / x.sheetMatches : null };
    })
    .sort((a, b) => b.cards - a.cards || b.matches - a.matches || a.name.localeCompare(b.name));
}

/** The matches worth naming: the biggest wins and the most goals. A draw is
 *  never a "biggest win", and a 0-0 is never a "most goals". */
export function topMatches(rows, limit = 3) {
  const total = (r) => n(r.home_goals) + n(r.away_goals);
  const margin = (r) => Math.abs(n(r.home_goals) - n(r.away_goals));
  const byDateDesc = (a, b) => String(b.date || "").localeCompare(String(a.date || ""));
  return {
    biggestWins: rows.filter((r) => margin(r) > 0)
      .sort((a, b) => margin(b) - margin(a) || total(b) - total(a) || byDateDesc(a, b))
      .slice(0, limit),
    mostGoals: rows.filter((r) => total(r) > 0)
      .sort((a, b) => total(b) - total(a) || margin(b) - margin(a) || byDateDesc(a, b))
      .slice(0, limit),
  };
}

// ── The sentences ────────────────────────────────────────────────────────────

const plural = (count, one, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;

/** Where the figures come from, as words: one competition by name, two by
 *  name, otherwise a count — and "all competitions" only when every one the
 *  screen offered is selected, because "across all leagues" about four of
 *  seventeen would be the sentence a reader catches. */
export function scopeText({ names = [], allSelected = false } = {}) {
  if (allSelected && names.length > 1) return "across all competitions";
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `across ${names.length} competitions`;
}

/** The lines the Copy button copies, in the order they are worth posting.
 *
 *  A card line claims a total only when EVERY side in scope has a team
 *  sheet. Otherwise it says how many matches it is counting, because "4 red
 *  cards this weekend" built from two team sheets is a false statement about
 *  every other match, and this is the text that gets posted publicly. */
export function headlines(s, { names = [], allSelected = false, when = "" } = {}) {
  if (!s.matches) return [];
  const tail = [scopeText({ names, allSelected }), when].filter(Boolean).join(" · ");
  const line = (text) => (tail ? `${text} · ${tail}` : text);
  const out = [line(`${plural(s.goals, "goal")} in ${plural(s.matches, "match", "matches")}`
                    + (s.matches > 1 ? ` (${perMatchText(s.goalsPerMatch)} a match)` : ""))];
  out.push(line(plural(s.cleanSheets, "clean sheet")));
  if (s.sheetSides) {
    const note = s.sheetSides === s.sides ? ""
      : ` in the ${plural(s.sheetMatches, "match", "matches")} with a team sheet`;
    out.push(line(`${plural(s.yellow, "yellow card")}, `
                  + `${plural(s.sentOff, "red card")}${note}`));
  }
  if (s.types.g_penalty) out.push(line(plural(s.types.g_penalty, "penalty", "penalties") + " scored"));
  return out;
}
