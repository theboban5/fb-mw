/* Leagues against each other — the rules, without the canvas.
 *
 * WHY THIS FILE EXISTS. #/ops?tab=compare (0039) answered the question the
 * reporters asked — is district youth football higher-scoring than the Super
 * League, where are clean sheets kept — and answered it on a screen only an
 * admin can open. The answer is the post: "Mzuzu's U20 league scores 3.78 a
 * game, the Premiership 1.99" is a graphic people share, and the only way to
 * make one was a screenshot of an ops screen. So #/graphics?type=leagues
 * draws the same figures as a board.
 *
 * ONE DEFINITION OF EVERY FIGURE. The metrics, levels and categories used to
 * live in app.js beside the Compare tab. A board that recomputed "clean sheet
 * %" its own way would publish a number the admin screen disagrees with, so
 * they moved here and both screens import them. A metric is still a
 * numerator, a denominator and a sentence: nothing arrives as a rate from
 * Postgres, which is what lets one definition serve a competition, a level
 * and a whole season.
 *
 * WHAT A BOARD WILL NOT SAY, and why:
 *
 *   * A rate over a handful of matches. Mangochi's U14 league has played
 *     three and averages 4.67 goals — it would top every scoring board while
 *     meaning nothing. Competitions under a minimum played are left off and
 *     the screen names them, rather than the board quietly ranking noise.
 *   * A rate without its denominator. Every bar prints its matches; four
 *     matches and four hundred look identical as a bar.
 *   * An order the board cannot show. Two entries with the same figure AS
 *     PRINTED are joint ("=2"), the scorer board's rule: 2.932 and 2.930
 *     both read 2.93, and "1st" and "2nd" beside them is a claim nobody
 *     looking at the board can check.
 *   * Scorer coverage. It is a fact about this site's data entry, not about
 *     football, and has no business on a public post (`board: false`).
 *
 * It imports nothing, so `node --test` can ask it without a browser.
 */

export const LEVELS = [
  { key: "", label: "All levels" },
  { key: "national", label: "National" },
  { key: "regional", label: "Regional" },
  { key: "district", label: "District" },
  { key: "none", label: "Unclassified" },
];

export const CATEGORIES = [
  { key: "", label: "All football" },
  { key: "men", label: "Men's" },
  { key: "women", label: "Women's" },
  { key: "youth", label: "Youth" },
];

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// `detail` is the admin screen's sentence; `boardDetail` is the same counts
// in the shorter form a bar has room for. `title` and `explain` head a board:
// a percentage with no sentence saying of what is a number nobody can repeat.
export const METRICS = [
  { key: "gpg", label: "Goals per match", pct: false, board: true,
    title: "Goals per match", explain: "Average goals in a played match",
    num: (r) => r.goals_total, den: (r) => r.played,
    detail: (n, d) => `${n} goals in ${d} matches`,
    boardDetail: (n, d) => `${plural(n, "goal")} · ${plural(d, "match", "matches")}` },
  { key: "clean", label: "Clean sheet %", pct: true, board: true,
    title: "Clean sheets", explain: "How often a side kept a clean sheet",
    num: (r) => r.clean_sheet_sides, den: (r) => r.sides,
    // A clean sheet belongs to a side, so a 0-0 is two of them and the
    // denominator is every side that took the field, not every match.
    detail: (n, d) => `${n} of ${d} sides kept one`,
    boardDetail: (n, d) => `${plural(n, "clean sheet")} · ${plural(d / 2, "match", "matches")}` },
  { key: "home", label: "Home win %", pct: true, board: true,
    title: "Home wins", explain: "Share of matches the home side won",
    num: (r) => r.home_wins, den: (r) => r.played,
    detail: (n, d) => `${n} of ${d} matches`,
    boardDetail: (n, d) => `${plural(n, "home win")} · ${plural(d, "match", "matches")}` },
  { key: "draw", label: "Draw %", pct: true, board: true,
    title: "Draws", explain: "Share of matches drawn",
    num: (r) => r.draws, den: (r) => r.played,
    detail: (n, d) => `${n} of ${d} matches`,
    boardDetail: (n, d) => `${plural(n, "draw")} · ${plural(d, "match", "matches")}` },
  { key: "rout", label: "Won by 3+ %", pct: true, board: true,
    title: "Won by 3 or more", explain: "Share of matches won by three goals or more",
    num: (r) => r.big_wins, den: (r) => r.played,
    detail: (n, d) => `${n} of ${d} matches`,
    boardDetail: (n, d) => `${n} of ${plural(d, "match", "matches")}` },
  { key: "margin", label: "Average margin", pct: false, board: true,
    title: "Average margin", explain: "Goals between the two sides at full time",
    num: (r) => r.margin_total, den: (r) => r.played,
    detail: (n, d) => `${n} goals of margin over ${d} matches`,
    boardDetail: (n, d) => plural(d, "match", "matches") },
  { key: "scorers", label: "Scorer coverage %", pct: true, board: false,
    num: (r) => r.goal_rows, den: (r) => r.goals_total,
    detail: (n, d) => `${n} of ${d} goals have a scorer` },
];

export const BOARD_METRICS = METRICS.filter((m) => m.board);

// The leaderboard's metrics are per-team and deliberately separate: two of
// them (fewest conceded, best record) are not magnitudes a bar can encode
// honestly, which is why they render as ranked rows and never as bars.
export const TEAM_METRICS = [
  { key: "attack", label: "Best attack", asc: false,
    title: "Best attack", explain: "Goals scored per match",
    value: (t) => t.gf / t.played,
    text: (t) => (t.gf / t.played).toFixed(2),
    detail: (t) => `${t.gf} scored in ${t.played}` },
  { key: "defence", label: "Best defence", asc: true,
    title: "Best defence", explain: "Fewest goals conceded per match",
    value: (t) => t.ga / t.played,
    text: (t) => (t.ga / t.played).toFixed(2),
    detail: (t) => `${t.ga} conceded in ${t.played}` },
  { key: "clean", label: "Most clean sheets", asc: false,
    title: "Most clean sheets", explain: "Share of matches without conceding",
    value: (t) => t.clean_sheets / t.played,
    text: (t) => `${Math.round(100 * t.clean_sheets / t.played)}%`,
    detail: (t) => `${t.clean_sheets} of ${t.played} matches` },
  { key: "record", label: "Best record", asc: false,
    title: "Best record", explain: "Points per match, three for a win",
    value: (t) => (3 * t.won + t.drawn) / t.played,
    text: (t) => ((3 * t.won + t.drawn) / t.played).toFixed(2),
    detail: (t) => `${t.won}W ${t.drawn}D ${t.lost}L · ${t.played} played` },
];

/** Minimum matches for a TEAM to be ranked. A two-game team topping "best
 *  attack" is noise, not a finding; six is roughly a third of a Malawian
 *  league season. */
export const TEAM_MIN_PLAYED = [4, 6, 10];

/** Minimum matches for a COMPETITION to be ranked on a board. Twenty is a
 *  little under two rounds of a ten-team league — enough that one 7-0 does
 *  not carry the average. */
export const COMP_MIN_PLAYED = [10, 20, 40];

export const sum = (rows, fn) => rows.reduce((n, r) => n + (fn(r) || 0), 0);

/** A metric over a set of competition rows, or null when nothing was played.
 *  Null rather than zero on purpose: "0.00 goals per match" is a claim about
 *  football, and "no matches played yet" is the truth. */
export function rate(rows, metric) {
  const den = sum(rows, metric.den);
  if (!den) return null;
  const num = sum(rows, metric.num);
  return {
    num, den,
    value: metric.pct ? (100 * num) / den : num / den,
    text: metric.pct ? `${Math.round((100 * num) / den)}%` : (num / den).toFixed(2),
  };
}

/** Competition rows narrowed by the screen's filters. "none" is the
 *  Unclassified level: a competition with no level is filtered FOR, never
 *  filtered away. */
export function inScope(rows, { season = "", level = "", cat = "" } = {}) {
  return rows.filter((r) => (!season || r.season_id === season)
    && (!level || (r.level || "none") === level)
    && (!cat || r.category === cat));
}

/** Rows into the units a board compares: one per competition, or one per
 *  level or category with every competition in it summed. Only groups that
 *  have a competition are returned, so filtering to Women's does not draw
 *  three empty bars. */
export function groupRows(rows, by = "competition") {
  if (by === "level" || by === "category") {
    const options = by === "level" ? LEVELS : CATEGORIES;
    const keyOf = by === "level" ? (r) => r.level || "none" : (r) => r.category;
    return options.filter((o) => o.key)
      .map((o) => ({ key: o.key, label: o.label, rows: rows.filter((r) => keyOf(r) === o.key) }))
      .filter((g) => g.rows.length);
  }
  return rows.map((r) => ({
    key: `${r.competition_id}|${r.season_id}`, label: r.competition_name, rows: [r],
  }));
}

/** Equal as the board prints them. Regional and district football both
 *  averaged 2.93 in 2026/27 (2.932 and 2.930); ranking them 1 and 2 beside
 *  two identical figures asserts an order the reader cannot see and the
 *  board cannot justify, so they are joint. */
const sameRate = (a, b) => a.text === b.text;

/** Rank `items` that each carry a `rate`, biggest first, with standard
 *  competition ranking: two level at second are both "=2" and the next is 4.
 *  `asc` reverses the order for a figure where lower is better. */
function rankByRate(items, asc = false) {
  const sorted = items.slice().sort((a, b) =>
    (asc ? 1 : -1) * (a.rate.value - b.rate.value)
    || b.played - a.played || a.label.localeCompare(b.label));
  return sorted.map((item, i) => {
    let first = i;
    while (first > 0 && sameRate(sorted[first - 1].rate, item.rate)) first -= 1;
    const tied = (i > 0 && sameRate(sorted[i - 1].rate, item.rate))
      || (i < sorted.length - 1 && sameRate(sorted[i + 1].rate, item.rate));
    return { ...item, position: first + 1, joint: tied };
  });
}

/** The first `limit` of a ranked list without splitting a tie. A cut through
 *  one would show one of two equal entries and drop the other, so the last
 *  place takes everyone level with it — unless that floods the board (five
 *  teams on 100%), in which case the whole tie goes and the board is short
 *  instead of wrong. */
export function cutAt(ranked, limit, slack = 3) {
  if (ranked.length <= limit) return ranked;
  const lastPos = ranked[limit - 1].position;
  const withTie = ranked.filter((r, i) => i < limit || r.position === lastPos);
  if (withTie.length <= limit + slack) return withTie;
  const without = ranked.filter((r) => r.position < lastPos);
  return without.length ? without : ranked.slice(0, limit);
}

/** One metric across groups, for a bar chart.
 *
 *  Returns `bars` (ranked, at most `limit`) and `leftOff` — the groups below
 *  `min` played, named so the screen can say why they are missing. A
 *  percentage is drawn against 100 so two boards are comparable; anything
 *  else against the largest bar shown, because goals per match has no
 *  natural ceiling. */
export function ranking(groups, metric, { min = 0, limit = Infinity } = {}) {
  const measured = groups.map((g) => ({
    ...g, played: sum(g.rows, (r) => r.played), rate: rate(g.rows, metric),
  }));
  const leftOff = measured.filter((g) => !g.rate || g.played < min);
  const ranked = rankByRate(measured.filter((g) => g.rate && g.played >= min));
  const shown = cutAt(ranked, limit);
  const peak = Math.max(0, ...shown.map((g) => g.rate.value));
  const bars = shown.map((g) => ({
    key: g.key, label: g.label, position: g.position, joint: g.joint,
    value: g.rate.text,
    share: metric.pct ? g.rate.value / 100 : (peak ? g.rate.value / peak : 0),
    detail: g.rows.length > 1
      ? `${plural(g.rows.length, "competition")} · ${metric.boardDetail(g.rate.num, g.rate.den)}`
      : metric.boardDetail(g.rate.num, g.rate.den),
  }));
  return { bars, leftOff, total: ranked.length };
}

/** Several metrics at once, the leaders in each — the "season in numbers"
 *  board. A metric no group can answer is dropped rather than drawn empty. */
export function dashboard(groups, metricKeys, { min = 0, per = 3 } = {}) {
  return metricKeys
    .map((key) => BOARD_METRICS.find((m) => m.key === key))
    .filter(Boolean)
    .map((metric) => ({ key: metric.key, title: metric.title, explain: metric.explain,
      ...ranking(groups, metric, { min, limit: per }) }))
    .filter((panel) => panel.bars.length);
}

/** The best teams by one team metric across the competitions in `compKeys`
 *  (`competition_id|season_id`), each with at least `min` played. Ties are
 *  joint, by the same rule as competitions: level as printed. */
export function teamLeaders(teams, metric, { compKeys = null, min = 0, limit = 10 } = {}) {
  const eligible = teams
    .filter((t) => !compKeys || compKeys.has(`${t.competition_id}|${t.season_id}`))
    .filter((t) => t.played >= Math.max(1, min));
  const sorted = eligible.slice().sort((a, b) =>
    (metric.asc ? 1 : -1) * (metric.value(a) - metric.value(b))
    || b.played - a.played || (a.team_name || "").localeCompare(b.team_name || ""));
  const shown = (t) => metric.text(t);
  const ranked = sorted.map((t, i) => {
    let first = i;
    while (first > 0 && shown(sorted[first - 1]) === shown(t)) first -= 1;
    const tied = (i > 0 && shown(sorted[i - 1]) === shown(t))
      || (i < sorted.length - 1 && shown(sorted[i + 1]) === shown(t));
    return { team: t, position: first + 1, joint: tied,
      value: metric.text(t), detail: metric.detail(t) };
  });
  return cutAt(ranked, limit);
}
