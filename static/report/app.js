/* Everyleague Reporter.
 *
 * One page, hash-routed, no framework. The reporter this is written for is on
 * an inexpensive Android phone at the side of a pitch with one or two bars of
 * signal, and the job is to get "2-1, full time" into the database in a few
 * seconds without losing it if the network drops.
 *
 * Routes
 *   #/            the reporter's fixtures, bucketed and filterable
 *   #/login       email + password (no signup — accounts are made by CLI)
 *   #/m/<uuid>    one match, the reporting screen. This is the WhatsApp link.
 *   #/add         add a whole fixture list to a competition you cover
 *   #/league/new  create a competition and its teams (administrators only)
 *   #/account     who am I, change password, sign out
 *
 * Three rules the code keeps:
 *   1. Entered data is never destroyed by a failure. The score lives in
 *      component state and a failed publish leaves it exactly as typed.
 *   2. Nothing is submitted twice. Every submit disables its own button for
 *      the duration of the request, whatever the outcome.
 *   3. The reporter is never shown a database error. Every failure is mapped
 *      to a sentence that says what to do about it.
 *
 * Authorization is NOT enforced here. The UI asks can_report_match() only so
 * it can show a useful message; the real boundary is RLS and the reporting
 * RPC, which do not trust this file at all.
 */

import { createClient } from "./vendor/supabase.min.js";
import { SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY } from "./config.js";

const view = document.querySelector("[data-view]");
const flashEl = document.querySelector("[data-flash]");
const accountBtn = document.querySelector("[data-account]");

let supabase = null;
let context = null;      // { reporter, competitions, isAdmin } once signed in
let flashTimer = null;

// ── Small helpers ────────────────────────────────────────────────────────────

const h = (html) => { view.innerHTML = html; };

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function flash(message, kind = "ok", ms = 4000) {
  clearTimeout(flashTimer);
  flashEl.textContent = message;
  flashEl.className = `rp-flash is-${kind}`;
  flashEl.hidden = false;
  // Errors stay until the next action: a reporter who looked away should not
  // come back to a screen that has quietly forgotten something went wrong.
  if (kind !== "error") flashTimer = setTimeout(() => { flashEl.hidden = true; }, ms);
}

const clearFlash = () => { clearTimeout(flashTimer); flashEl.hidden = true; };

/** Today's date on Malawi's clock, whatever the phone is set to.
 *  The whole site runs on CAT (UTC+2, no DST), and "today's fixtures" has to
 *  mean the Malawi calendar day or a reporter travelling — or a phone with a
 *  wrong timezone — sees the wrong list. */
function catToday() {
  return new Date(Date.now() + 2 * 3600 * 1000).toISOString().slice(0, 10);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function formatDate(iso) {
  if (!iso) return "Date TBC";
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]} ${y}`;
}

/** A kickoff is shown only when it is known — never a placeholder time. */
function formatKickoff(kickoff) {
  const value = (kickoff || "").trim();
  if (!/^\d{1,2}:\d{2}/.test(value)) return "";
  const [hh, mm] = value.split(":");
  return `${String(Number(hh)).padStart(2, "0")}:${mm} CAT`;
}

// The six statuses are the existing database vocabulary and are not negotiable
// — standings, results and every renderer key off them. Only the LABEL is
// reporter-facing: "Full time" publishes status='played'.
const STATUSES = [
  { value: "played", label: "Full time", short: "FT", scored: true },
  { value: "scheduled", label: "Not played yet", short: "", scored: false },
  { value: "postponed", label: "Postponed", short: "P-P", scored: false },
  { value: "abandoned", label: "Abandoned", short: "ABD", scored: false },
  { value: "cancelled", label: "Cancelled", short: "CAN", scored: false },
];
const statusMeta = (value) =>
  STATUSES.find((s) => s.value === value) || STATUSES[1];

/** Turn any failure into something a reporter can act on.
 *  Raw Postgres messages leak schema and help nobody holding a phone. */
function humanError(error) {
  const code = error?.code || "";
  const message = (error?.message || "").toLowerCase();

  if (message.includes("failed to fetch") || message.includes("networkerror")
      || error?.name === "TypeError") {
    return "No connection. Nothing was lost — try again when you have signal.";
  }
  if (code === "PGRST301" || error?.status === 401
      || message.includes("jwt") || message.includes("token is expired")) {
    return "Your session has expired. Please sign in again.";
  }
  if (code === "PGRST202") {
    // The reporting function is not deployed yet (arrives with 0003).
    return "Publishing is not enabled on this server yet.";
  }
  if (message.includes("not assigned") || message.includes("not authori")) {
    return "You are not assigned to this competition.";
  }
  if (message.includes("inactive")) {
    return "Your reporter account is inactive. Ask an administrator.";
  }
  if (message.includes("match not found")) {
    return "That match no longer exists. Go back and pick it again.";
  }
  if (message.includes("only an administrator")) {
    return error.message.charAt(0).toUpperCase() + error.message.slice(1) + ".";
  }
  // create_fixture and create_league phrase every rejection for a person to
  // read — they are the same rules validate.py enforces at build time, said
  // once here where the reporter can still do something about them. Passing
  // them straight through is the point; rewriting them would lose the detail
  // that makes them actionable.
  if (message.includes("cannot play itself")
      || message.includes("not entered")
      || message.includes("already in the list")
      || message.includes("kickoff must look like")
      || message.includes("at least two")
      || message.includes("already exists")
      || message.includes("short code")
      || message.includes("age group")
      || message.includes("round")
      || message.includes("no active season")
      || message.includes("outside the")
      || message.includes("both teams are required")
      // ...and the whole-list rules from 0014, which are about the SUBMISSION
      // rather than about any one fixture in it.
      || message.includes("at least one fixture")
      || message.includes("a list of fixtures")
      || message.includes("in two goes")
      || message.includes("gender must be")
      || message.includes("type must be")
      // ...and the scorer-identity rules from 0010, phrased the same way.
      || message.includes("name is too long")
      || message.includes("not in the database")
      || message.includes("already have a scorer")
      || message.includes("did not play in this match")
      || message.includes("publish the score before")
      || message.includes("needs a name")
      // ...and the reporter-pool rules from 0026. "that is the last
      // administrator" and "you cannot change your own role" are the two an
      // admin will actually meet, and both are refusals they can only
      // understand if the sentence survives.
      || message.includes("needs an email")
      || message.includes("role must be")
      || message.includes("last administrator")
      || message.includes("you cannot")
      || message.includes("no competition")
      || message.includes("no reporter")
      // ...and delete_fixture (0032): only an administrator, only a
      // scheduled fixture, only one with nothing already reported onto it.
      || message.includes("only an administrator can delete a fixture")
      || message.includes("can be deleted")
      || message.includes("cannot be deleted")) {
    return error.message.charAt(0).toUpperCase() + error.message.slice(1);
  }
  // The RPC's own validation, which is phrased for a person to read.
  if (message.includes("invalid score") || message.includes("invalid status")) {
    return error.message;
  }
  if (code === "23514" || code === "23503" || code === "23505") {
    return "That could not be saved — please check what you entered.";
  }
  return "Something went wrong. Your entry is still here — please try again.";
}

// ── Auth + context ───────────────────────────────────────────────────────────

async function loadContext() {
  // WHO AM I IS A FILTERED READ, NEVER "THE FIRST ROW".
  //
  // This used to take reporters[0], on the reasoning that RLS filters the
  // table to the caller. That is true for an ordinary reporter and FALSE for
  // an administrator: the policy is
  //
  //     using (auth_user_id = auth.uid() or public.is_admin())
  //
  // so an admin reads every row and [0] is whichever one Postgres felt like
  // returning. With a single reporter in the database that was always the
  // right answer, which is why it survived; the moment a second account
  // existed, one admin was greeted by the other's name.
  //
  // It was not only cosmetic. context.reporter.reporter_id is sent as
  // `reported_by` when saving a card, substitution, line-up or photo, and
  // those policies check `reported_by = current_reporter_id()` — so the
  // mismatched admin could not save any match detail at all. Scores and
  // scorers were unaffected: their RPCs derive the reporter server-side and
  // never trust this value.
  const { data: { session } } = await supabase.auth.getSession();
  const authUserId = session?.user?.id;
  if (!authUserId) return { reporter: null, isAdmin: false, competitions: [] };

  const [{ data: reporters, error: rErr }, { data: assignments },
         { data: isAdmin }] = await Promise.all([
    supabase.from("reporters").select("reporter_id,name,public_byline,active")
      .eq("auth_user_id", authUserId).limit(1),
    // Same policy shape, same trap: an admin sees everyone's assignments.
    // reporter_id comes back so the rows can be narrowed to this account
    // below — filtering here would mean waiting for the query above and
    // giving up the parallel fetch for a list that is at most a few rows.
    supabase.from("reporter_assignments")
      .select("reporter_id,competition_id,season_id"),
    supabase.rpc("is_admin"),
  ]);
  if (rErr) throw rErr;

  const reporter = (reporters || [])[0] || null;
  return {
    reporter,
    isAdmin: Boolean(isAdmin),
    // An admin is not assigned to anything, and does not need to be.
    competitions: (assignments || [])
      .filter((a) => a.reporter_id === reporter?.reporter_id)
      .map((a) => a.competition_id),
  };
}

// Competition names and the active season change about once a year, and every
// screen needs them. Fetching them per render would put three extra round
// trips in front of a list the phone could already draw.
let referenceCache = {};

const invalidateReference = () => { referenceCache = {}; };

function once(key, load) {
  if (!referenceCache[key]) {
    // The PROMISE is cached, not the value: two screens rendering together ask
    // once between them rather than racing.
    referenceCache[key] = load().catch((error) => {
      delete referenceCache[key];       // a failure must not be cached
      throw error;
    });
  }
  return referenceCache[key];
}

/** competition_id -> display name (sponsor_name wins, exactly as the site). */
function competitionNames() {
  return once("names", async () => {
    const [{ data: comps }, { data: seasons }] = await Promise.all([
      supabase.from("competitions").select("competition_id,name"),
      supabase.from("competition_seasons").select("competition_id,sponsor_name,status"),
    ]);
    const names = {};
    (comps || []).forEach((c) => { names[c.competition_id] = c.name; });
    (seasons || []).forEach((cs) => {
      if (cs.sponsor_name) names[cs.competition_id] = cs.sponsor_name;
    });
    return names;
  });
}

const MATCH_FIELDS =
  "match_id,public_id,competition_id,season_id,date,kickoff,status,stage," +
  "matchday,home_goals,away_goals,home_team_id,away_team_id,source_ref," +
  "home:teams!matches_home_team_id_fkey(display_name)," +
  "away:teams!matches_away_team_id_fkey(display_name)," +
  "venue:venues(name)";

// ── Views ────────────────────────────────────────────────────────────────────

function renderLogin(next) {
  h(`
    <h1 class="rp-login-head">Sign in</h1>
    <p class="rp-login-sub">Use the email and password you were given.</p>
    <form class="rp-form" data-login>
      <label class="rp-label" for="email">Email</label>
      <input class="rp-input" id="email" name="email" type="email" required
             autocomplete="username" inputmode="email" autocapitalize="off"
             autocorrect="off" spellcheck="false">
      <label class="rp-label" for="password">Password</label>
      <input class="rp-input" id="password" name="password" type="password"
             required autocomplete="current-password">
      <button class="rp-btn" type="submit" data-submit>Sign in</button>
      <p class="rp-hint">Accounts are created by an administrator. There is no
        public signup.</p>
    </form>
  `);

  const form = view.querySelector("[data-login]");
  const button = form.querySelector("[data-submit]");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (button.disabled) return;               // rule 2: never submit twice
    clearFlash();
    button.disabled = true;
    button.textContent = "Signing in…";
    const { error } = await supabase.auth.signInWithPassword({
      email: form.email.value.trim(),
      password: form.password.value,
    });
    button.disabled = false;
    button.textContent = "Sign in";
    if (error) {
      // Deliberately not "no such user" vs "wrong password": that difference
      // tells an outsider which addresses are reporters.
      flash(error.message?.includes("Invalid login")
        ? "Email or password not recognised."
        : humanError(error), "error");
      return;
    }
    // Straight back to the match they tapped in WhatsApp. A deep link renders
    // the login form in place, without changing the URL, so the hash is often
    // ALREADY the target — and assigning the same value fires no hashchange.
    // Re-route by hand in that case or the reporter is left staring at the
    // form they just completed.
    const target = next || "#/";
    if (location.hash === target) route();
    else location.hash = target;
  });
}

function matchCard(match, names, { showScore, from }) {
  const meta = [formatDate(match.date), formatKickoff(match.kickoff),
                match.venue?.name].filter(Boolean).join(" · ");
  const info = statusMeta(match.status);
  const scored = match.home_goals != null && match.away_goals != null;

  let badge = "";
  if (scored) badge = `<span class="rp-badge is-done">${esc(info.short || "Result")}</span>`;
  else if (match.status !== "scheduled") badge = `<span class="rp-badge is-off">${esc(info.label)}</span>`;
  else if (showScore) badge = '<span class="rp-badge is-late">Needs result</span>';

  return `
    <article class="rp-card">
      <div class="rp-card-comp">${esc(names[match.competition_id] || match.competition_id)}</div>
      <div class="rp-teams">
        <span class="rp-team">${esc(match.home?.display_name || match.home_team_id)}</span>
        <span class="rp-score">${scored ? esc(match.home_goals) : ""}</span>
        <span class="rp-team">${esc(match.away?.display_name || match.away_team_id)}</span>
        <span class="rp-score">${scored ? esc(match.away_goals) : ""}</span>
      </div>
      <p class="rp-card-meta">${esc(meta)} ${badge}</p>
      <a class="rp-btn${scored ? " is-ghost" : ""}" href="${esc(matchHref(match, from))}">
        ${scored ? "Edit result" : "Report match"}
      </a>
    </article>`;
}

function group(title, matches, names, options = {}) {
  if (!matches.length) return "";
  return `<h2 class="rp-group-head">${esc(title)}
            <span class="rp-count">${matches.length}</span></h2>`
    + matches.map((m) => matchCard(m, names, options)).join("");
}

// ── Carrying the list into the match, and back out ───────────────────────────
// A reporter working through a matchday sets three filters, taps a match,
// publishes, and used to land back on an unfiltered list with all of it to do
// again. The filters they chose travel with them as `from` — the home screen's
// own query string, url-encoded once so it survives being a value inside
// another query string.
//
// It is carried in the URL rather than in a variable for the same reason the
// filters themselves are: the browser back button, a reload and a shared link
// all keep working without anything remembering anything.

function matchHref(match, from) {
  const tail = from ? `?from=${encodeURIComponent(from)}` : "";
  return `#/m/${match.public_id}${tail}`;
}

/** Where "My matches" goes from a match screen: back to the list they came
 *  from, or the plain home screen when they arrived by a WhatsApp deep link. */
function backHash(params) {
  const from = params?.get("from") || "";
  // Re-read through readFilters/filterHash rather than trusting the string:
  // `from` is user input like every other part of the hash, and this is what
  // stops a hand-edited one from being echoed into an href.
  return from ? filterHash(readFilters(new URLSearchParams(from))) : "#/";
}

// ── Home filters ─────────────────────────────────────────────────────────────
// The unfiltered list is one long scroll in no order a reporter cares about,
// because it is ordered by date across every competition at once. These three
// filters are the questions actually asked of it: which league, which bucket,
// which day.

const SHOW_OPTIONS = [
  { value: "all", label: "Everything" },
  { value: "today", label: "Today" },
  { value: "awaiting", label: "Awaiting result" },
  { value: "upcoming", label: "Upcoming" },
  { value: "reported", label: "Recently reported" },
];

/** Filters live in the URL (#/?comp=…&show=…&date=…&md=…), not in a variable.
 *  The back button then works, a reload keeps the view, and "my league,
 *  matchday 5" is a link a reporter can keep. Every value is validated on the
 *  way in — the hash is user input. */
function readFilters(params) {
  const show = params.get("show");
  const date = params.get("date") || "";
  const md = params.get("md") || "";
  return {
    comp: params.get("comp") || "",
    show: SHOW_OPTIONS.some((o) => o.value === show) ? show : "all",
    date: /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : "",
    // A stage value: md_<n> on a league, a knockout round on a cup. Anything
    // else came from a hand-edited URL and is ignored rather than queried.
    md: /^(md_\d{1,3}|r64|r32|r16|qf|sf|final|3p)$/.test(md) ? md : "",
  };
}

function filterHash(filters) {
  const query = new URLSearchParams();
  if (filters.comp) query.set("comp", filters.comp);
  if (filters.show !== "all") query.set("show", filters.show);
  if (filters.date) query.set("date", filters.date);
  if (filters.md) query.set("md", filters.md);
  const encoded = query.toString();
  return encoded ? `#/?${encoded}` : "#/";
}

/** "md_5" → "Matchday 5"; "qf" → "Quarter-final".
 *
 *  `stage` is the field that works for both: create_fixture writes md_<n> for
 *  a league and the round for a cup, and the existing data agrees — 554 of 556
 *  rows carry one, where `matchday` is null on every cup tie. So the filter
 *  keys off stage and this is the only place its shape is interpreted. */
function stageLabel(stage) {
  const round = CUP_ROUNDS.find((r) => r.value === stage);
  if (round) return round.label;
  const md = /^md_(\d+)$/.exec(stage || "");
  return md ? `Matchday ${md[1]}` : (stage || "—");
}

/** Stages present in a set of matches, in the order they are played:
 *  matchdays numerically, then knockout rounds by depth. */
function stageOptions(matches) {
  const present = [...new Set(matches.map((m) => m.stage).filter(Boolean))];
  const rank = (stage) => {
    const md = /^md_(\d+)$/.exec(stage);
    if (md) return Number(md[1]);            // 1, 2, 3 … before any round
    const i = CUP_ROUNDS.findIndex((r) => r.value === stage);
    return i === -1 ? 9999 : 1000 + i;
  };
  return present.sort((a, b) => rank(a) - rank(b));
}

// The fixture list is re-read from the network only when it might have
// changed. The `show` and `md` filters are then applied locally: on the
// connection this app is written for, re-fetching a list the phone already
// holds just to hide half of it would be the slowest thing on the screen.
//
// The COMPETITION and DATE filters are different, and are part of the cache
// key, because both are ways of asking for matches the unfiltered list does
// not hold. Played results are capped in the unfiltered list — every league at
// once is hundreds of rows and a phone should not download them all — and a
// cap applied before filtering would silently hide an older league's results
// behind sixty newer ones from everywhere else. So each of those two filters
// asks the database again, scoped to what was asked for.
//
// WHAT WAS WRONG: the cap survived the narrowing. Choosing one competition
// still fetched only its sixty most recent results, so the FDH Bank
// Premiership — 104 played by August 2026 — began at matchday 6, halfway
// through it, and matchdays 1–5 could not be reached from the portal at all.
// The matchday menu is built from the matches on screen, so those rounds were
// not merely empty, they were not offered. It read like the pre-Supabase
// fixtures had been left behind by the migration; they had not, they were
// simply row 61 and after.
//
// A competition asked for by name is bounded by its own history — hundreds of
// rows across every season it has ever played, not thousands — so it is
// fetched whole. If a competition ever grows past what a phone should carry,
// the answer is a season filter in the UI, not a cap that decides in silence
// which half of the season a reporter is allowed to fix.
let homeCache = null;   // { key, matches, names }

// The list the reporter is currently working through, captured whenever the
// home screen draws: enough to offer "next match" from inside a match screen
// without a second query, and deliberately NOT cleared by invalidateHome().
//
// It is a snapshot on purpose. Publishing a result invalidates the fixture
// cache, and a queue rebuilt from fresh data would re-order itself under the
// reporter's feet — the match they just did would leave the "awaiting" bucket
// and every position after it would shift. Walking the list as it looked when
// they started is the only version that behaves like a list.
let queue = null;       // { from, items: [{ id, label, needsResult }] }

const invalidateHome = () => { homeCache = null; };

const RESULT_LIMIT = 60;

const DECIDED = ["played", "awarded", "postponed", "abandoned", "cancelled"];

/** What a cached list is a list OF. Anything that changes which rows the
 *  database is asked for belongs here, or a filter silently reuses the wrong
 *  cache. */
const homeKey = (filters) => `${filters.comp || "*"}|${filters.date || ""}`;

async function loadHome(filters) {
  const { comp, date } = filters;
  const key = homeKey(filters);
  if (homeCache && homeCache.key === key) return homeCache;

  // Scoping every query the same way, in one place: a reporter sees their own
  // competitions, an admin sees all of them, and a chosen one wins over both.
  const scope = (query) => {
    if (comp) return query.eq("competition_id", comp);
    // An admin reports everywhere and has no assignments to narrow by.
    if (!context.isAdmin) return query.in("competition_id", context.competitions);
    return query;
  };

  let pending = scope(supabase.from("matches").select(MATCH_FIELDS)
    .eq("status", "scheduled").order("date", { ascending: true }));
  // Not played, but decided: these belong in the list too, or a postponed
  // match vanishes and looks like a fixture nobody ever entered.
  let other = scope(supabase.from("matches").select(MATCH_FIELDS)
    .in("status", DECIDED).order("date", { ascending: false }));
  // The cap is for the list nobody has narrowed. See the note above homeCache.
  if (!comp) other = other.limit(RESULT_LIMIT);

  // A date is a precise question — "the match in that Facebook report, on the
  // 30th" — and one day across every competition is a handful of rows, so it
  // is asked in full rather than answered out of a capped list that may not
  // reach back that far. Merged into the list rather than replacing it so the
  // competition and matchday menus still hold everything they held before.
  const older = date
    ? scope(supabase.from("matches").select(MATCH_FIELDS)
        .eq("date", date).order("date", { ascending: false }))
    : null;

  const [pendingRes, otherRes, olderRes, names] = await Promise.all([
    pending, other, older, competitionNames(),
  ]);
  if (pendingRes.error || otherRes.error || olderRes?.error) {
    throw pendingRes.error || otherRes.error || olderRes.error;
  }

  // Deduped: a match on the chosen date is very likely in the first two
  // queries as well, and a reporter must never see the same fixture twice.
  const seen = new Set();
  const matches = [...(pendingRes.data || []), ...(otherRes.data || []),
                   ...(olderRes?.data || [])]
    .filter((m) => {
      if (seen.has(m.match_id)) return false;
      seen.add(m.match_id);
      return true;
    });

  homeCache = { key, matches, names };
  return homeCache;
}

/** Which bucket a match falls in. One function so the filter dropdown and the
 *  section headings can never disagree about what "awaiting" means. */
function bucketOf(match, today) {
  if (match.status !== "scheduled") return "reported";
  if (match.date === today) return "today";
  if (match.date && match.date < today) return "awaiting";
  return "upcoming";
}

const anyFilterSet = (filters) =>
  Boolean(filters.comp || filters.show !== "all" || filters.date || filters.md);

function filterBar(filters, names, competitions, stages) {
  // A competition in the hash that is not in the list — a hand-edited URL, or
  // an assignment removed since the link was saved — is still shown as the
  // chosen one. Dropping it silently would leave the menu reading "All
  // competitions" over a list that is anything but.
  const ids = filters.comp && !competitions.includes(filters.comp)
    ? [filters.comp, ...competitions] : competitions;
  const compOptions = ['<option value="">All competitions</option>']
    .concat(ids.map((id) => `
      <option value="${esc(id)}"${id === filters.comp ? " selected" : ""}>
        ${esc(names[id] || id)}</option>`))
    .join("");
  const showOptions = SHOW_OPTIONS.map((o) => `
    <option value="${o.value}"${o.value === filters.show ? " selected" : ""}>
      ${esc(o.label)}</option>`).join("");

  // Same reasoning as the competition menu: a matchday chosen before the list
  // narrowed must stay selectable, or the filter cannot be undone from the UI.
  const mdValues = filters.md && !stages.includes(filters.md)
    ? [filters.md, ...stages] : stages;
  const mdOptions = ['<option value="">All matchdays</option>']
    .concat(mdValues.map((s) => `
      <option value="${esc(s)}"${s === filters.md ? " selected" : ""}>
        ${esc(stageLabel(s))}</option>`))
    .join("");

  return `
    <div class="rp-filters" data-filters>
      <label class="rp-filter">
        <span class="rp-filter-label">Competition</span>
        <select class="rp-select" data-filter="comp">${compOptions}</select>
      </label>
      ${mdValues.length ? `
        <label class="rp-filter rp-filter-wide">
          <span class="rp-filter-label">Matchday</span>
          <select class="rp-select" data-filter="md">${mdOptions}</select>
        </label>` : ""}
      <label class="rp-filter">
        <span class="rp-filter-label">Show</span>
        <select class="rp-select" data-filter="show">${showOptions}</select>
      </label>
      <label class="rp-filter">
        <span class="rp-filter-label">Date</span>
        <input class="rp-input rp-date" type="date" data-filter="date"
               value="${esc(filters.date)}">
      </label>
      ${anyFilterSet(filters)
        ? '<button class="rp-btn is-quiet rp-filter-clear" type="button" data-clear>Clear filters</button>'
        : ""}
    </div>`;
}

async function renderHome(params) {
  const filters = readFilters(params || new URLSearchParams());

  if (!context.reporter) {
    // A signed-in auth user with no reporters row: the account exists but has
    // not been linked. Nothing they can fix themselves.
    h(`<h1 class="rp-greeting">Almost there</h1>
       <p class="rp-sub">This login is not linked to a reporter account yet.
         Ask an administrator to finish setting it up.</p>
       <button class="rp-btn is-quiet" data-signout>Sign out</button>`);
    view.querySelector("[data-signout]").onclick = signOut;
    return;
  }

  if (!context.isAdmin && !context.competitions.length) {
    h(`<h1 class="rp-greeting">Hi ${esc(firstName())}</h1>
       <p class="rp-sub">You have no competitions assigned yet. An
         administrator needs to assign you one before you can report.</p>`);
    return;
  }

  if (!homeCache || homeCache.key !== homeKey(filters)) {
    h('<div class="rp-loading"><span class="rp-spinner"></span><p>Loading your matches…</p></div>');
  }

  let data, choices;
  try {
    // The dropdown's options come from what this account may report, NOT from
    // the matches on screen: deriving them from a list that is itself filtered
    // would leave the menu holding only the league already chosen, with no way
    // back to any other.
    [data, choices] = await Promise.all([
      loadHome(filters), entryCompetitions(),
    ]);
  } catch (error) {
    h(`<p class="rp-empty">Could not load your matches.</p>
       <button class="rp-btn" data-retry>Try again</button>`);
    view.querySelector("[data-retry]").onclick = () => renderHome(params);
    flash(humanError(error), "error");
    return;
  }

  const today = catToday();
  const { names } = data;
  const competitions = choices.map((c) => c.competition_id);

  // The matchday menu lists the stages available in the CHOSEN COMPETITION,
  // not in the filtered result — otherwise picking matchday 5 would leave 5 as
  // the only option and no way back. Competition is the only filter that
  // narrows it, because "matchday 5" means different fixtures in each league.
  const inScope = filters.comp
    ? data.matches.filter((m) => m.competition_id === filters.comp)
    : data.matches;
  const stages = stageOptions(inScope);

  const shown = data.matches.filter((m) => {
    if (filters.comp && m.competition_id !== filters.comp) return false;
    if (filters.date && m.date !== filters.date) return false;
    if (filters.md && m.stage !== filters.md) return false;
    if (filters.show !== "all" && bucketOf(m, today) !== filters.show) return false;
    return true;
  });

  // The filters travel with every match link, so publishing a result and
  // coming back lands on the same list rather than on an unfiltered one.
  const from = filterHash(filters).replace(/^#\/\??/, "");

  // Filtered to one thing: a flat list, because the headings would each hold
  // the whole list and say nothing. Unfiltered: the buckets, which are the
  // reason the home screen is useful at all.
  let body;
  let ordered;
  if (filters.show === "all" && !filters.date && !filters.md) {
    const bucket = (name) => shown.filter((m) => bucketOf(m, today) === name);
    const today_ = bucket("today");
    const awaiting = bucket("awaiting");
    const upcoming = bucket("upcoming");
    const reported = bucket("reported").slice(0, 12);
    body = [
      group("Today", today_, names, { showScore: true, from }),
      group("Awaiting result", awaiting, names, { showScore: true, from }),
      group("Upcoming", upcoming, names, { from }),
      group("Recently reported", reported, names, { from }),
    ].join("");
    ordered = [...today_, ...awaiting, ...upcoming, ...reported];
  } else {
    // The most specific filter names the list, because that is what the
    // reporter just asked for.
    const heading = filters.md ? stageLabel(filters.md)
      : filters.date ? formatDate(filters.date)
      : SHOW_OPTIONS.find((o) => o.value === filters.show).label;
    body = group(heading, shown, names,
                 { showScore: filters.show !== "upcoming", from });
    ordered = shown;
  }

  // Captured in the order it is about to be drawn, so "next match" means the
  // next one down the screen and nothing cleverer.
  queue = {
    from,
    items: ordered.map((m) => ({
      id: m.public_id,
      label: `${m.home?.display_name || m.home_team_id} v `
             + `${m.away?.display_name || m.away_team_id}`,
      needsResult: m.status === "scheduled",
    })),
  };

  const canAdd = context.isAdmin || context.competitions.length > 0;
  // Only offered to someone who has a national team to report: for an ordinary
  // league reporter the whole section is empty and the link would be a dead
  // end. Resolved rather than assumed because an admin always has one.
  const hasNT = (await ntTeams().catch(() => [])).length > 0;
  const actions = `
    <div class="rp-actions">
      ${canAdd ? '<a class="rp-btn is-ghost" href="#/add">＋ Add fixtures</a>' : ""}
      ${context.isAdmin ? '<a class="rp-btn is-ghost" href="#/league/new">＋ New league</a>' : ""}
      ${context.isAdmin ? '<a class="rp-btn is-ghost" href="#/ops">Operations</a>' : ""}
      ${context.isAdmin ? '<a class="rp-btn is-ghost" href="#/trending">Homepage</a>' : ""}
      ${context.isAdmin ? '<a class="rp-btn is-ghost" href="#/reporters">Reporters</a>' : ""}
      ${hasNT ? '<a class="rp-btn is-ghost" href="#/nt">National teams</a>' : ""}
      <button class="rp-btn is-quiet" type="button" data-refresh>Refresh</button>
    </div>`;

  h(`<h1 class="rp-greeting">Hi ${esc(firstName())}</h1>
     <p class="rp-sub">${esc(formatDate(today))}${
        context.isAdmin ? " · administrator" : ""}</p>
     ${actions}
     ${filterBar(filters, names, competitions, stages)}
     ${body || `<p class="rp-empty">Nothing matches these filters.${
        anyFilterSet(filters) ? " Try clearing them." : ""}</p>`}`);

  view.querySelector("[data-filters]").addEventListener("change", (event) => {
    const key = event.target.dataset.filter;
    if (!key) return;
    const next = { ...filters, [key]: event.target.value };
    // Matchdays belong to a competition. Carrying "matchday 12" across to a
    // league that has only played six would show an empty list and read as a
    // bug rather than as a stale filter.
    if (key === "comp") next.md = "";
    location.hash = filterHash(next);
  });
  const clear = view.querySelector("[data-clear]");
  if (clear) clear.onclick = () => { location.hash = "#/"; };
  // Refresh means "forget everything you think you know" — including the
  // competition menu, which is stale after someone else creates a league.
  view.querySelector("[data-refresh]").onclick = () => {
    invalidateHome();
    invalidateReference();
    renderHome(params);
  };
}

function firstName() {
  const name = context.reporter?.name || "";
  return name.split(" ")[0] || "there";
}

// ── Adding fixtures ──────────────────────────────────────────────────────────
// A result can only be reported against a fixture that exists, so a reporter
// who covers a league nobody has entered a fixture list for cannot do anything
// at all. This is that missing step.
//
// A WHOLE LIST, NOT ONE FIXTURE. A fixture list does not arrive as a fixture.
// It arrives as one Facebook graphic: a week, seven matches, two dates, one
// kick-off time repeated seven times, and a ground printed under every
// pairing. Entering them one at a time meant seven round trips on a phone with
// one bar of signal, each able to fail on its own and leave the week half
// entered with nothing saying how far the reporter had got. So the form is the
// shape of the picture — the things the whole graphic shares said once at the
// top, a numbered line per match, and one button.
//
// PART OF IT FAILING MUST NOT COST THE REST. create_fixtures answers per line:
// the lines that saved leave the form and appear under "Added just now", and
// the lines that did not stay exactly as typed with the reason on them. That
// is rule 1 of this app — entered data is never destroyed by a failure — at
// the scale of a list rather than a field.
//
// The form deliberately offers ONLY teams entered in the chosen competition
// this season. That is validate.py check 3, which fails the whole build if it
// is broken — so rather than let it be typed wrong and rejected, the wrong
// answer is not offered. create_fixtures checks it again anyway; the client is
// not the boundary.

const CUP_ROUNDS = [
  { value: "r64", label: "Round of 64" },
  { value: "r32", label: "Round of 32" },
  { value: "r16", label: "Round of 16" },
  { value: "qf", label: "Quarter-final" },
  { value: "sf", label: "Semi-final" },
  { value: "final", label: "Final" },
  { value: "3p", label: "Third-place play-off" },
];

// Three lines to start with. Enough that the screen reads as a list rather
// than as a single form with an odd button under it, few enough that a
// reporter adding one midweek fixture is not looking at a wall.
const START_ROWS = 3;

// What create_fixtures accepts in one call. Nothing in Malawi plays a round
// this big; it is there so a malformed or replayed submission cannot sit on a
// connection inserting for minutes.
const MAX_ROWS = 60;

/** The competitions this account may enter data for. An admin may use any;
 *  everyone else gets exactly their assignments. */
function entryCompetitions() {
  return once("entryComps", async () => {
    const [{ data: comps, error }, names] = await Promise.all([
      supabase.from("competitions").select("competition_id,name,type"),
      competitionNames(),
    ]);
    if (error) throw error;
    const allowed = (comps || []).filter((c) =>
      context.isAdmin || context.competitions.includes(c.competition_id));
    allowed.forEach((c) => { c.label = names[c.competition_id] || c.name; });
    allowed.sort((a, b) => a.label.localeCompare(b.label));
    return allowed;
  });
}

function activeSeason() {
  return once("season", async () => {
    const { data } = await supabase.from("seasons")
      .select("season_id,label").eq("status", "active").limit(1);
    return (data || [])[0] || null;
  });
}

/** Every ground already in the database, for the suggestion list under each
 *  venue box. resolve_venue matches a typed name exactly (after case and
 *  punctuation), and mints a new venue when it does not match — so picking the
 *  name that is already there is what keeps one ground from acquiring two ids.
 *  This list is that prevention; the resolver is only the fallback. */
function venueNames() {
  return once("venues", async () => {
    const { data } = await supabase.from("venues").select("name").order("name");
    return (data || []).map((v) => v.name).filter(Boolean);
  });
}

// `home`/`away` are team_ids — the answer. `homeText`/`awayText` are what is
// half-typed in the box beside them, kept because adding or removing a line
// redraws every other line, and a name someone was in the middle of typing
// must survive that (rule 1).
const blankRow = () => ({
  home: "", away: "", homeText: "", awayText: "",
  date: "", kickoff: "", venue: "", error: "",
});

/** A per-line failure comes back as the sentence create_fixtures raised, not
 *  as a PostgREST error object — but they are the same sentences, raised by
 *  the same rules, so humanError already knows every one of them. */
const rowError = (message) => humanError({ message: message || "" });

async function renderAddFixture(params) {
  h('<div class="rp-loading"><span class="rp-spinner"></span><p>Loading…</p></div>');

  let competitions, season, venues;
  try {
    [competitions, season, venues] = await Promise.all([
      entryCompetitions(),
      activeSeason(),
      // The ground list is a convenience, not a requirement: a reporter can
      // still type one. Losing the whole screen because it did not load would
      // be a worse trade than losing the suggestions.
      venueNames().catch(() => []),
    ]);
  } catch (error) {
    h('<p class="rp-empty">Could not load your competitions.</p>'
      + '<a class="rp-btn is-ghost" href="#/">Back to my matches</a>');
    flash(humanError(error), "error");
    return;
  }

  if (!competitions.length) {
    h(`<a class="rp-btn is-quiet" href="#/">&larr; My matches</a>
       <p class="rp-empty">You have no competitions to add fixtures to.
         ${context.isAdmin ? "Create a league first."
                           : "Ask an administrator to assign you one."}</p>
       ${context.isAdmin ? '<a class="rp-btn" href="#/league/new">＋ New league</a>' : ""}`);
    return;
  }
  if (!season) {
    h(`<a class="rp-btn is-quiet" href="#/">&larr; My matches</a>
       <p class="rp-empty">No season is marked active, so there is nothing to
         add a fixture to. An administrator needs to set one.</p>`);
    return;
  }

  const wanted = params?.get("comp");
  const state = {
    competition: competitions.find((c) => c.competition_id === wanted)
                 || competitions[0],
    teams: null,
    // Shared by every line, because the graphic shares them.
    matchday: "",
    stage: "",
    date: "",
    kickoff: "",
    source: "",
    rows: Array.from({ length: START_ROWS }, blankRow),
    busy: false,
    added: [],
  };

  async function loadTeams() {
    state.teams = null;
    drawAddFixture();
    const { data, error } = await supabase.from("entries")
      .select("team_id,team:teams(display_name)")
      .eq("competition_id", state.competition.competition_id)
      .eq("season_id", season.season_id);
    if (error) {
      state.teams = [];
      flash(humanError(error), "error");
    } else {
      state.teams = (data || [])
        .map((e) => ({ id: e.team_id, name: e.team?.display_name || e.team_id }))
        .sort((a, b) => a.name.localeCompare(b.name));
    }
    drawAddFixture();
  }

  const teamName = (id) =>
    (state.teams || []).find((t) => t.id === id)?.name || id;

  /** Read the form back into state.
   *
   *  The alternative — a listener per field writing through on every keystroke
   *  — buys nothing here and costs a re-render argument on a slow phone. This
   *  runs before anything that redraws (adding a line, removing one, changing
   *  competition, submitting), which is the only time state and DOM can
   *  disagree in a way that matters. */
  function syncFromDom() {
    const form = view.querySelector("[data-fixtures]");
    if (!form) return;
    const value = (selector) => form.querySelector(selector)?.value ?? "";
    state.matchday = value("[data-matchday]");
    state.stage = value("[data-stage]");
    state.date = value("[data-all-date]");
    state.kickoff = value("[data-all-kickoff]");
    state.source = value("[data-source]");
    // [data-field] rather than [data-row]: a team picker carries BOTH a
    // visible box holding a name and a hidden input holding the team_id, and
    // only the second is the answer. The visible one has no data-field.
    form.querySelectorAll("[data-field]").forEach((el) => {
      const row = state.rows[Number(el.dataset.row)];
      if (row) row[el.dataset.field] = el.value;
    });
    form.querySelectorAll("[data-text]").forEach((el) => {
      const row = state.rows[Number(el.dataset.row)];
      if (row) row[`${el.dataset.text}Text`] = el.value;
    });
  }

  function drawAddFixture() {
    const isCup = state.competition.type === "cup";

    const compOptions = competitions.map((c) => `
      <option value="${esc(c.competition_id)}"${
        c.competition_id === state.competition.competition_id ? " selected" : ""}>
        ${esc(c.label)}</option>`).join("");

    /** A team box: type to narrow, tap to commit.
     *
     *  The scorer picker's pattern, and simpler — the teams are already in
     *  memory, so filtering is a string match rather than a search, and there
     *  is no "add a new one". A team not entered in this competition is
     *  exactly what validate.py check 3 refuses, so it is never offered; the
     *  box can only ever produce a team_id that is already legal here.
     *
     *  Two inputs, as in the scorer picker: what the reporter reads is a
     *  NAME, and what is submitted is the hidden id beside it. */
    const picker = (row, i, side, label) => `
      <div class="rp-pick" data-pick="${i}:${side}">
        <input class="rp-input" type="text" data-row="${i}" data-text="${side}"
               value="${esc(row[side] ? teamName(row[side]) : row[`${side}Text`] || "")}"
               placeholder="${esc(label)}" role="combobox" aria-expanded="false"
               aria-autocomplete="list" autocomplete="off" autocorrect="off"
               autocapitalize="words" aria-label="${esc(label)}, match ${i + 1}">
        <input type="hidden" data-row="${i}" data-field="${side}"
               value="${esc(row[side])}">
        <ul class="rp-suggest" role="listbox" data-suggest hidden></ul>
      </div>`;

    const line = (row, i) => `
      <li class="rp-fx${row.error ? " is-bad" : ""}">
        <div class="rp-fx-head">
          <span class="rp-fx-no">Match ${i + 1}</span>
          ${state.rows.length > 1
            ? `<button class="rp-fx-drop" type="button" data-drop="${i}">Remove</button>`
            : ""}
        </div>
        ${picker(row, i, "home", "Home team")}
        <span class="rp-fx-v">v</span>
        ${picker(row, i, "away", "Away team")}
        <div class="rp-row">
          <input class="rp-input rp-date" type="date" data-row="${i}" data-field="date"
                 value="${esc(row.date)}" aria-label="Date, match ${i + 1}">
          <input class="rp-input rp-date" type="time" data-row="${i}" data-field="kickoff"
                 value="${esc(row.kickoff)}" aria-label="Kick-off, match ${i + 1}">
        </div>
        <input class="rp-input" type="text" list="rp-venues" maxlength="120"
               data-row="${i}" data-field="venue" value="${esc(row.venue)}"
               placeholder="Ground" autocapitalize="words" autocorrect="off"
               aria-label="Ground, match ${i + 1}">
        ${row.error ? `<p class="rp-fx-error">${esc(row.error)}</p>` : ""}
      </li>`;

    const count = state.rows.filter((r) => r.home && r.away).length;

    const body = state.teams === null
      ? '<div class="rp-loading"><span class="rp-spinner"></span><p>Loading teams…</p></div>'
      : state.teams.length < 2
        ? `<p class="rp-empty">${esc(state.competition.label)} has fewer than
             two teams entered for ${esc(season.label)}, so it cannot hold a
             fixture yet.</p>`
        : `
      ${isCup ? `
        <label class="rp-label" for="fx-stage">Round</label>
        <select class="rp-select" id="fx-stage" data-stage>
          <option value="">Choose…</option>
          ${CUP_ROUNDS.map((r) => `<option value="${r.value}"${
            r.value === state.stage ? " selected" : ""}>${esc(r.label)}</option>`).join("")}
        </select>
        <p class="rp-hint">The round every match below is in.</p>`
      : `
        <label class="rp-label" for="fx-matchday">Matchday</label>
        <input class="rp-input" id="fx-matchday" type="number" data-matchday
               min="1" step="1" inputmode="numeric" value="${esc(state.matchday)}">
        <p class="rp-hint">Optional, and applies to every match below — a
          fixture list is published a week at a time.</p>`}

      <h2 class="rp-field-head">Applies to every match</h2>
      <div class="rp-row">
        <input class="rp-input rp-date" type="date" data-all-date
               value="${esc(state.date)}" aria-label="Date for every match">
        <input class="rp-input rp-date" type="time" data-all-kickoff
               value="${esc(state.kickoff)}" aria-label="Kick-off for every match">
      </div>
      <p class="rp-hint">Filled into the lines below. Change any line that is
        different — a list spread over two days is normal.</p>

      <label class="rp-label" for="fx-source">Source (where is this information from?)</label>
      <input class="rp-input" id="fx-source" type="text" data-source maxlength="500"
             value="${esc(state.source)}" placeholder="Facebook link, or how you know"
             autocapitalize="sentences" autocorrect="off" spellcheck="false">
      <p class="rp-hint">Never shown publicly, and recorded against every match
        added — it is there so a fixture can be checked later.</p>

      <h2 class="rp-field-head">The fixtures</h2>
      <p class="rp-hint" style="margin-top:0">Start typing a team and tap it
        from the list. Only teams entered in this competition are offered.</p>
      <ol class="rp-fixtures">${state.rows.map(line).join("")}</ol>
      <button class="rp-btn is-ghost" type="button" data-more>＋ Add another</button>

      <div class="rp-publish">
        <button class="rp-btn" type="submit" data-submit>${
          count ? `Add ${count} fixture${count === 1 ? "" : "s"}` : "Add fixtures"}</button>
      </div>`;

    const addedList = state.added.length ? `
      <h2 class="rp-field-head">Added just now</h2>
      ${state.added.map((m) => `
        <article class="rp-card">
          <div class="rp-teams">
            <span class="rp-team">${esc(m.homeName)}</span><span></span>
            <span class="rp-team">${esc(m.awayName)}</span><span></span>
          </div>
          <p class="rp-card-meta">${[formatDate(m.date), formatKickoff(m.kickoff),
                                     m.venue].filter(Boolean).map(esc).join(" · ")}</p>
          <a class="rp-btn is-ghost" href="#/m/${esc(m.public_id)}">Report this match</a>
        </article>`).join("")}` : "";

    h(`<a class="rp-btn is-quiet" href="#/">&larr; My matches</a>
       <h1 class="rp-login-head">Add fixtures</h1>
       <p class="rp-login-sub">${esc(season.label)} season.</p>
       <form class="rp-form" data-fixtures autocomplete="off">
         <label class="rp-label" for="fx-comp">Competition</label>
         <select class="rp-select" id="fx-comp" data-comp>${compOptions}</select>
         ${body}
       </form>
       <datalist id="rp-venues">${
         venues.map((n) => `<option value="${esc(n)}"></option>`).join("")}</datalist>
       ${addedList}`);

    wire(isCup);
  }

  function wire(isCup) {
    const form = view.querySelector("[data-fixtures]");

    form.querySelector("[data-comp]").addEventListener("change", (event) => {
      syncFromDom();
      state.competition = competitions.find(
        (c) => c.competition_id === event.target.value) || competitions[0];
      // A round chosen for a cup means nothing in a league and vice versa, and
      // the teams on every line belong to the competition that was showing
      // when they were picked — carrying either across would offer a fixture
      // that validate.py check 3 exists to refuse.
      state.stage = "";
      state.rows.forEach((row) => {
        row.home = ""; row.away = ""; row.homeText = ""; row.awayText = "";
        row.error = "";
      });
      loadTeams();
    });

    form.querySelector("[data-more]")?.addEventListener("click", () => {
      syncFromDom();
      // The same ceiling create_fixtures enforces, said before the reporter
      // has typed a line that would be refused.
      if (state.rows.length >= MAX_ROWS) {
        flash(`${MAX_ROWS} matches is the most in one go — add these, then`
              + " start the next lot.", "warn");
        return;
      }
      const row = blankRow();
      // A new line starts where the list said it would, so adding the seventh
      // match of a Saturday is one pair of taps rather than four.
      row.date = state.date;
      row.kickoff = state.kickoff;
      state.rows.push(row);
      drawAddFixture();
      view.querySelector(`[data-row="${state.rows.length - 1}"]`)?.focus();
    });

    form.querySelectorAll("[data-drop]").forEach((button) => {
      button.addEventListener("click", () => {
        syncFromDom();
        state.rows.splice(Number(button.dataset.drop), 1);
        drawAddFixture();
      });
    });

    // Changing "applies to every match" fills the lines that were following it
    // — the empty ones, and the ones still showing the previous shared value.
    // A line the reporter has already made different is left alone, because
    // the day that differs is the thing they went out of their way to say.
    const spread = (field, el) => el?.addEventListener("change", () => {
      const previous = state[field];
      syncFromDom();
      const next = state[field];
      state.rows.forEach((row) => {
        if (!row[field] || row[field] === previous) row[field] = next;
      });
      drawAddFixture();
    });
    spread("date", form.querySelector("[data-all-date]"));
    spread("kickoff", form.querySelector("[data-all-kickoff]"));

    // The button counts what will be sent, so "Add 7 fixtures" is a statement
    // about the screen rather than a label.
    const button = form.querySelector("[data-submit]");
    if (!button) return;
    const countLines = () => {
      const n = state.rows.filter((r) => r.home && r.away).length;
      button.textContent = n ? `Add ${n} fixture${n === 1 ? "" : "s"}` : "Add fixtures";
    };

    // ── The team pickers ─────────────────────────────────────────────────────
    // Delegated to the form rather than bound per box: there are two per line
    // and the whole list is redrawn whenever a line is added or removed, so
    // per-box listeners would be re-attached on every redraw and hold detached
    // nodes. The form is replaced with them, so these die at the right time.

    let blurTimer = null;

    const closeLists = () => {
      form.querySelectorAll("[data-suggest]").forEach((ul) => {
        if (ul.hidden) return;
        ul.hidden = true;
        ul.innerHTML = "";
        ul.parentElement.querySelector("[data-text]")
          ?.setAttribute("aria-expanded", "false");
      });
    };

    function openList(wrap, term) {
      const [index, side] = wrap.dataset.pick.split(":");
      const row = state.rows[Number(index)];
      const opposite = side === "home" ? row?.away : row?.home;
      const needle = term.trim().toLowerCase();
      const matches = (state.teams || []).filter(
        (t) => !needle || t.name.toLowerCase().includes(needle));
      const list = wrap.querySelector("[data-suggest]");

      list.innerHTML = matches.length
        ? matches.map((t) => `
            <li role="option"><button type="button" class="rp-suggest-btn"
              data-team="${esc(t.id)}" data-name="${esc(t.name)}"${
                t.id === opposite ? " disabled" : ""}>
              <span>${esc(t.name)}</span>${t.id === opposite
                ? "<em>already the other side of this match</em>" : ""}
            </button></li>`).join("")
        // Not a dead end, and not phrased as one: the reason a name is absent
        // is almost always that the team is not entered in this competition
        // this season, which is a thing an administrator fixes.
        : `<li><button type="button" class="rp-suggest-btn" disabled>
             <span>No team here matches that</span>
             <em>only teams entered in ${esc(state.competition.label)} this
                 season can be given a fixture</em></button></li>`;
      list.hidden = false;
      wrap.querySelector("[data-text]").setAttribute("aria-expanded", "true");
    }

    // Focusing a box shows everything, so it is still one tap to browse the
    // list the way the old dropdown worked. Typing narrows it.
    form.addEventListener("focusin", (event) => {
      if (event.target.closest("[data-suggest]")) return;
      clearTimeout(blurTimer);
      closeLists();
      const input = event.target.closest("[data-text]");
      if (input) openList(input.parentElement, "");
    });

    // Belt and braces for the same hazard: keeping the press from moving focus
    // at all means the box never blurs and the list is still there when the
    // click arrives. The deferred close above is the fallback for touch.
    form.addEventListener("mousedown", (event) => {
      if (event.target.closest("[data-suggest]")) event.preventDefault();
    });

    form.addEventListener("input", (event) => {
      const input = event.target.closest("[data-text]");
      if (!input) return;
      // The typed name no longer belongs to whoever was picked.
      input.parentElement.querySelector("[data-field]").value = "";
      syncFromDom();
      countLines();
      openList(input.parentElement, input.value);
    });

    // The tap that chooses an option lands just after the blur it causes, so
    // closing is deferred rather than immediate.
    form.addEventListener("focusout", (event) => {
      if (!event.target.closest("[data-text]")) return;
      clearTimeout(blurTimer);
      blurTimer = setTimeout(closeLists, 180);
    });

    form.addEventListener("click", (event) => {
      const option = event.target.closest(".rp-suggest-btn");
      if (!option || option.disabled || !option.dataset.team) return;
      const wrap = option.closest("[data-pick]");
      wrap.querySelector("[data-field]").value = option.dataset.team;
      wrap.querySelector("[data-text]").value = option.dataset.name;
      clearTimeout(blurTimer);
      closeLists();
      syncFromDom();
      countLines();
    });

    form.addEventListener("keydown", (event) => {
      const input = event.target.closest("[data-text]");
      if (!input || event.key !== "Enter") return;
      // Enter inside a picker means "the one at the top of the list", never
      // "submit the other six fixtures".
      event.preventDefault();
      input.parentElement
        .querySelector(".rp-suggest-btn[data-team]:not([disabled])")?.click();
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (state.busy) return;                  // rule 2: never submit twice
      clearFlash();
      syncFromDom();
      closeLists();

      // A name typed in full and never tapped is still an answer. The picker
      // is a convenience, not a checkpoint, so an exact match (bar case and
      // spacing) resolves itself rather than being refused for the sake of a
      // gesture the reporter did not know was required.
      state.rows.forEach((row) => {
        ["home", "away"].forEach((side) => {
          if (row[side]) return;
          const typed = (row[`${side}Text`] || "").trim().toLowerCase();
          if (!typed) return;
          const hit = (state.teams || []).find(
            (t) => t.name.trim().toLowerCase() === typed);
          if (hit) row[side] = hit.id;
        });
      });

      // Asked first, because it is one control and it applies to all of them:
      // sending seven cup fixtures with no round would fail seven times over
      // for a reason that is nothing to do with any of the seven lines.
      if (isCup && !state.stage) {
        flash("Choose the round these matches are in.", "warn");
        return;
      }

      // What is worth sending, and what is wrong before the network is
      // involved. A line with neither team is a spare line, not a mistake —
      // there are three on an empty form and nobody has to delete them.
      const sending = [];
      state.rows.forEach((row) => {
        row.error = "";
        const started = row.home || row.away || row.homeText || row.awayText;
        if (!started) return;
        if (!row.home || !row.away) {
          // Distinguishes "you left it blank" from "what you typed is not a
          // team here", which are different mistakes with different fixes.
          const unmatched = (!row.home && row.homeText)
                         || (!row.away && row.awayText);
          row.error = unmatched
            ? "Pick both teams from the list — type a few letters and tap one."
            : "Pick both teams.";
          return;
        }
        if (row.home === row.away) { row.error = "A team cannot play itself."; return; }
        sending.push(row);
      });

      if (!sending.length) {
        drawAddFixture();
        flash(state.rows.some((r) => r.error)
          ? "Some lines are not finished — see below."
          : "Fill in at least one match.", "warn");
        return;
      }
      state.busy = true;
      button.disabled = true;
      button.textContent = `Adding ${sending.length}…`;

      const { data, error } = await supabase.rpc("create_fixtures", {
        p_competition_id: state.competition.competition_id,
        p_source_ref: state.source.trim(),
        p_fixtures: sending.map((row) => {
          const fixture = {
            home: row.home, away: row.away,
            date: row.date, kickoff: row.kickoff, venue: row.venue.trim(),
          };
          if (isCup) fixture.stage = state.stage;
          else if (state.matchday) fixture.matchday = state.matchday;
          return fixture;
        }),
      });

      state.busy = false;
      button.disabled = false;

      if (error) {
        // The call itself failed, so nothing was written and nothing typed is
        // lost — the form is redrawn exactly as it stands.
        drawAddFixture();
        flash(humanError(error), "error");
        return;
      }

      // One result per line sent, in order, saying which. A line that saved
      // leaves the form; a line that did not keeps everything on it and gains
      // the reason, which is the only state worth being in after a partial
      // success.
      const results = new Map((data || []).map((r) => [r.idx, r]));
      let saved = 0;
      sending.forEach((row, i) => {
        const result = results.get(i + 1);
        if (result?.ok) {
          saved += 1;
          state.added.unshift({
            public_id: result.public_id,
            homeName: teamName(row.home), awayName: teamName(row.away),
            date: row.date, kickoff: row.kickoff, venue: row.venue.trim(),
          });
          // A ground typed on this screen joins the suggestion list for the
          // rest of it. The second half of a fixture list often repeats a
          // ground from the first, and re-typing it slightly differently is
          // precisely how one place ends up with two venue_ids.
          const ground = row.venue.trim();
          if (ground && !venues.some((n) => n.toLowerCase() === ground.toLowerCase())) {
            venues.push(ground);
            venues.sort((a, b) => a.localeCompare(b));
          }
          row.done = true;
        } else {
          row.error = result ? rowError(result.message)
                             : "That line was not saved — please try it again.";
        }
      });
      state.rows = state.rows.filter((row) => !row.done);
      const failed = state.rows.filter((row) => row.error).length;

      if (saved) {
        // The home screen is now out of date in a way the reporter can see,
        // and so is the published fixture list.
        invalidateHome();
        requestRebuild();
      }
      // Everything landed: come back empty and ready for the next graphic,
      // keeping the competition, matchday and source — which is what the
      // second week of a fixture list shares with the first.
      if (!state.rows.length) {
        state.rows = Array.from({ length: START_ROWS }, () => {
          const row = blankRow();
          row.date = state.date;
          row.kickoff = state.kickoff;
          return row;
        });
      }

      drawAddFixture();
      if (saved && !failed) {
        flash(`${saved} fixture${saved === 1 ? "" : "s"} added.`, "ok");
      } else if (saved) {
        flash(`${saved} added. ${failed} still ${failed === 1 ? "needs" : "need"}`
              + " attention below.", "warn", 9000);
      } else {
        flash("Nothing was added — see the lines below.", "error");
      }
    });
  }

  loadTeams();
}

// ── Creating a league ────────────────────────────────────────────────────────
// Admin only. One screen, because a competition with no teams cannot hold a
// fixture and is not a useful thing to have made — so the teams are part of
// creating it, not a second step.

const AGE_GROUPS = [
  "senior", "u20", "u19", "u17", "u16", "u15", "u14", "u13", "u12", "u11", "u10",
];

async function renderNewLeague() {
  if (!context.isAdmin) {
    h(`<a class="rp-btn is-quiet" href="#/">&larr; My matches</a>
       <p class="rp-empty">Only an administrator can create a competition.</p>`);
    return;
  }

  const season = await activeSeason();
  if (!season) {
    h(`<a class="rp-btn is-quiet" href="#/">&larr; My matches</a>
       <p class="rp-empty">No season is marked active. An administrator needs
         to set one before a competition can be created.</p>`);
    return;
  }

  h(`
    <a class="rp-btn is-quiet" href="#/">&larr; My matches</a>
    <h1 class="rp-login-head">New league</h1>
    <p class="rp-login-sub">Creates the competition, its teams and their
      entries for ${esc(season.label)} — ready for fixtures straight away.</p>

    <form class="rp-form" data-league>
      <label class="rp-label" for="lg-name">Name</label>
      <input class="rp-input" id="lg-name" name="name" required
             placeholder="SRFA Division 3" autocapitalize="words">

      <label class="rp-label" for="lg-code">Short code</label>
      <input class="rp-input" id="lg-code" name="code" required maxlength="12"
             placeholder="SRFA3" autocapitalize="characters" autocorrect="off"
             spellcheck="false">
      <p class="rp-hint">Letters and numbers, used in the competition's
        permanent id. It cannot be changed later.</p>

      <label class="rp-label" for="lg-type">Type</label>
      <select class="rp-select" id="lg-type" name="type">
        <option value="league" selected>League</option>
        <option value="cup">Cup</option>
      </select>

      <label class="rp-label" for="lg-gender">Gender</label>
      <select class="rp-select" id="lg-gender" name="gender">
        <option value="m" selected>Men</option>
        <option value="w">Women</option>
      </select>

      <label class="rp-label" for="lg-age">Age group</label>
      <select class="rp-select" id="lg-age" name="age">
        ${AGE_GROUPS.map((a) => `<option value="${a}">${
          a === "senior" ? "Senior" : a.toUpperCase()}</option>`).join("")}
      </select>

      <label class="rp-label" for="lg-tier">Tier</label>
      <input class="rp-input" id="lg-tier" name="tier" type="number" min="1"
             max="10" step="1" inputmode="numeric" placeholder="3">
      <p class="rp-hint">Optional. 1 is the top division.</p>

      <label class="rp-label" for="lg-region">Region</label>
      <input class="rp-input" id="lg-region" name="region" placeholder="SRFA">
      <p class="rp-hint">Optional.</p>

      <label class="rp-label" for="lg-teams">Teams</label>
      <textarea class="rp-input rp-textarea" id="lg-teams" name="teams" rows="10"
                required placeholder="One team per line, e.g.&#10;Chilomoni United&#10;Bangwe All Stars&#10;Ndirande Sparrows"></textarea>
      <p class="rp-hint">One per line. A club already in the database is
        reused; anything new gets a club and a team created for it.</p>

      <button class="rp-btn" type="submit" data-submit>Create league</button>
    </form>
  `);

  const form = view.querySelector("[data-league]");
  const button = form.querySelector("[data-submit]");
  let busy = false;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;                          // rule 2
    clearFlash();

    const teams = form.teams.value.split("\n")
      .map((line) => line.trim()).filter(Boolean);
    // Said here as well as in the RPC so the reporter is not made to wait for
    // a round trip to learn something the page already knew.
    const unique = new Set(teams.map((t) => t.toLowerCase()));
    if (unique.size < 2) {
      flash("Add at least two different teams, one per line.", "error");
      return;
    }

    busy = true;
    button.disabled = true;
    button.textContent = "Creating…";

    const { data, error } = await supabase.rpc("create_league", {
      p_name: form.name.value.trim(),
      p_short_code: form.code.value.trim(),
      p_teams: teams,
      p_type: form.type.value,
      p_gender: form.gender.value,
      p_age_group: form.age.value,
      p_tier: form.tier.value ? Number(form.tier.value) : null,
      p_region: form.region.value.trim(),
    });

    busy = false;
    button.disabled = false;
    button.textContent = "Create league";

    if (error) {
      // Rule 1: everything typed — including a long team list — is still here.
      flash(humanError(error), "error");
      return;
    }

    // A new competition changes what the home screen and the fixture form can
    // offer, and the assignment list the context was built from.
    invalidateHome();
    invalidateReference();
    context = await loadContext().catch(() => context);
    flash(`Created ${unique.size} teams. Now add its fixtures.`, "ok", 8000);
    location.hash = `#/add?comp=${encodeURIComponent(data)}`;
  });
}

async function renderMatch(publicId, params) {
  h('<div class="rp-loading"><span class="rp-spinner"></span><p>Loading match…</p></div>');
  const back = backHash(params);

  const { data: matches, error } = await supabase.from("matches")
    .select(MATCH_FIELDS).eq("public_id", publicId).limit(1);
  if (error) {
    h('<p class="rp-empty">Could not load this match.</p>');
    flash(humanError(error), "error");
    return;
  }
  const match = (matches || [])[0];
  if (!match) {
    h(`<p class="rp-empty">That match could not be found. The link may be
         out of date.</p>
       <a class="rp-btn is-ghost" href="${esc(back)}">Back to my matches</a>`);
    return;
  }

  const [{ data: allowed }, names, venues] = await Promise.all([
    supabase.rpc("can_report_match", { p_match_id: match.match_id }),
    competitionNames(),
    // For the ground box in "Change date or ground". A convenience, never a
    // requirement — the name can still be typed — so a failure here must not
    // cost the reporter the screen they came for.
    venueNames().catch(() => []),
  ]);

  if (!allowed) {
    // The UI mirroring the database's answer, not enforcing it.
    h(`<div class="rp-card">
         <div class="rp-card-comp">${esc(names[match.competition_id] || match.competition_id)}</div>
         <div class="rp-teams">
           <span class="rp-team">${esc(match.home?.display_name)}</span><span></span>
           <span class="rp-team">${esc(match.away?.display_name)}</span><span></span>
         </div>
       </div>
       <p class="rp-empty">You are not assigned to this competition, so you
         cannot report this match. If that looks wrong, ask an administrator.</p>
       <a class="rp-btn is-ghost" href="${esc(back)}">Back to my matches</a>`);
    return;
  }

  // Everything the reporter types lives here. A failed publish leaves it
  // untouched, which is what makes retrying safe (rule 1).
  const state = {
    home: match.home_goals ?? 0,
    away: match.away_goals ?? 0,
    status: match.home_goals != null ? match.status : "played",
    source: match.source_ref || "",
    published: match.home_goals != null || match.status !== "scheduled",
    busy: false,
    back,
    from: params?.get("from") || "",
    venues,
    // Which of the collapsed sections were open, so a redraw does not shut
    // them. See drawDetail.
    open: {},
    // Scorers typed BEFORE the score exists, waiting on the publish that will
    // give them a goal to belong to. Empty once the match is published: from
    // then on a scorer saves the moment it is added, because there is
    // something for it to attach to. See stageOrSaveGoal.
    pendingGoals: [],
  };

  drawMatch(match, names, state);
}

/** The next match in the list the reporter came from that still needs a
 *  result, or null. Sits on the queue captured by the home screen, so it costs
 *  nothing and is in the order they saw. */
function nextInQueue(publicId, from) {
  // A different list (or none) is not this reporter's run — offering to walk
  // it would send them somewhere they never asked to be.
  if (!queue || queue.from !== from) return null;
  const at = queue.items.findIndex((it) => it.id === publicId);
  if (at === -1) return null;
  return queue.items.slice(at + 1).find((it) => it.needsResult) || null;
}

/** Mark a match done in the queue so walking the list never offers it twice. */
function markQueued(publicId) {
  const item = queue?.items.find((it) => it.id === publicId);
  if (item) item.needsResult = false;
}

function drawMatch(match, names, state) {
  // Publishing, and saving a new date, both redraw this whole screen. Neither
  // should shut a section the reporter had opened underneath.
  captureDetailState(state);
  const meta = [formatDate(match.date), formatKickoff(match.kickoff),
                match.venue?.name].filter(Boolean).join(" · ");
  const homeName = match.home?.display_name || match.home_team_id;
  const awayName = match.away?.display_name || match.away_team_id;

  const statusOptions = STATUSES.map((s) => `
    <label class="rp-status-opt">
      <input type="radio" name="status" value="${s.value}"
             ${s.value === state.status ? "checked" : ""}>
      <span>${esc(s.label)}</span>
    </label>`).join("");

  // Walking a matchday: the next fixture in the list they came from that still
  // needs a result. Offered only after publishing, because before that the
  // reporter is still on the job in front of them.
  const next = state.published ? nextInQueue(match.public_id, state.from) : null;
  const nextBtn = next ? `
    <a class="rp-btn" href="${esc(matchHref({ public_id: next.id }, state.from))}">
      Next: ${esc(next.label)} &rarr;</a>` : "";

  const done = state.published ? `
    <div class="rp-done">
      <div class="rp-done-tick" aria-hidden="true">&#10003;</div>
      <p class="rp-done-head">Published</p>
      <p class="rp-done-score">${esc(homeName)} ${esc(match.home_goals ?? state.home)}&ndash;${esc(match.away_goals ?? state.away)} ${esc(awayName)}</p>
      <p class="rp-done-status">${esc(statusMeta(match.status).label)}</p>
      <p class="rp-done-status">Live on everyleague.co within a few minutes.</p>
      ${nextBtn}
    </div>` : "";

  h(`
    <a class="rp-btn is-quiet" href="${esc(state.back)}" style="margin-top:0">&larr; My matches</a>
    ${done}
    <p class="rp-report-comp">${esc(names[match.competition_id] || match.competition_id)}</p>
    <p class="rp-report-meta">${esc(meta)}</p>

    <section class="rp-side">
      <h2 class="rp-side-name">${esc(homeName)}</h2>
      <div class="rp-stepper">
        <button class="rp-step" type="button" data-step="home:-1" aria-label="One fewer goal for ${esc(homeName)}">&minus;</button>
        <span class="rp-step-value" data-value="home" aria-live="polite">${state.home}</span>
        <button class="rp-step" type="button" data-step="home:1" aria-label="One more goal for ${esc(homeName)}">+</button>
      </div>
    </section>

    <section class="rp-side">
      <h2 class="rp-side-name">${esc(awayName)}</h2>
      <div class="rp-stepper">
        <button class="rp-step" type="button" data-step="away:-1" aria-label="One fewer goal for ${esc(awayName)}">&minus;</button>
        <span class="rp-step-value" data-value="away" aria-live="polite">${state.away}</span>
        <button class="rp-step" type="button" data-step="away:1" aria-label="One more goal for ${esc(awayName)}">+</button>
      </div>
    </section>

    <h2 class="rp-field-head">Match status</h2>
    <div class="rp-status" data-status>${statusOptions}</div>

    <h2 class="rp-field-head">Source (where is this information from?)</h2>
    <input class="rp-input" type="text" data-source maxlength="500"
           value="${esc(state.source)}"
           placeholder="Facebook link, or how you know"
           autocapitalize="sentences" autocorrect="off" spellcheck="false">
    <p class="rp-hint">Never shown publicly — it is there so a result can be
      checked later. A link, or plain words like &ldquo;told to me by the
      referee&rdquo;.</p>

    <div class="rp-publish">
      <button class="rp-btn" type="button" data-publish></button>
      <p class="rp-publish-note" data-note></p>
    </div>

    ${section("reschedule", "Change date or ground", 0, `
      <p class="rp-hint" style="margin-top:0">The fixture list said one thing
        and it happened another way? Change it here. Each of these saves on its
        own — neither is part of publishing the score.</p>
      <label class="rp-label" for="rs-date">Date</label>
      <input class="rp-input" id="rs-date" type="date" data-rs-date
             value="${esc(match.date || "")}">
      <p class="rp-hint">Clear it if the match no longer has a fixed day.</p>
      <label class="rp-label" for="rs-kickoff">Kick-off</label>
      <input class="rp-input" id="rs-kickoff" type="time" data-rs-kickoff
             value="${esc((match.kickoff || "").slice(0, 5))}">
      <p class="rp-hint">Malawi time.</p>
      <button class="rp-btn is-ghost" type="button" data-rs-save>Save new date</button>

      <label class="rp-label" for="rs-venue">Ground</label>
      <input class="rp-input" id="rs-venue" type="text" list="rp-venues"
             maxlength="120" data-rs-venue value="${esc(match.venue?.name || "")}"
             placeholder="Ground" autocapitalize="words" autocorrect="off">
      <p class="rp-hint">Pick one that is already there where you can — a
        ground typed a second way becomes a second ground. Clear the box if it
        is no longer settled.</p>
      <button class="rp-btn is-ghost" type="button" data-rs-venue-save>Save ground</button>
      <datalist id="rp-venues">${
        (state.venues || []).map((n) => `<option value="${esc(n)}"></option>`).join("")}</datalist>
    `, state.open?.reschedule)}

    ${context.isAdmin && match.status === "scheduled" ? section("delete",
      "Delete this fixture", 0, `
      <p class="rp-hint" style="margin-top:0">For a duplicate or a fixture
        entered by mistake — not a way to take down a result. Only works
        while nothing has been reported onto it: no scorers, no team sheet.</p>
      <button class="rp-btn is-quiet" type="button" data-delete-fixture>Delete fixture</button>
    `, state.open?.delete) : ""}

    <div data-detail></div>
  `);

  drawDetail(match, state);
  wireReschedule(match, names, state);
  wireDeleteFixture(match, state);

  const valueEls = {
    home: view.querySelector('[data-value="home"]'),
    away: view.querySelector('[data-value="away"]'),
  };
  const publishBtn = view.querySelector("[data-publish]");
  const note = view.querySelector("[data-note]");
  const sourceEl = view.querySelector("[data-source]");

  // Kept in state on every keystroke, for the same reason the score is: a
  // failed publish redraws the screen, and a pasted link that vanished would
  // be the most annoying thing in the app to type again.
  sourceEl.addEventListener("input", () => { state.source = sourceEl.value; });

  function refresh() {
    const info = statusMeta(state.status);
    valueEls.home.textContent = state.home;
    valueEls.away.textContent = state.away;
    // A postponed or cancelled match has no score to enter.
    view.querySelectorAll("[data-step]").forEach((b) => {
      b.disabled = state.busy || !info.scored;
    });
    view.querySelectorAll('[data-status] input').forEach((i) => {
      i.disabled = state.busy;
    });
    const waiting = state.pendingGoals.length;
    const scorers = `${waiting} scorer${waiting === 1 ? "" : "s"}`;
    publishBtn.disabled = state.busy;
    publishBtn.textContent = state.busy
      ? "Publishing…"
      : info.scored
        // The button names everything it is about to do, scorers included —
        // they are staged on this phone and nowhere else until it is pressed.
        ? `Publish ${state.home}–${state.away} ${info.short}`
          + (waiting ? ` + ${scorers}` : "")
        : `Publish as ${info.label.toLowerCase()}`;
    // The button says exactly what will become public.
    note.textContent = info.scored
      ? (waiting ? `The result and ${scorers} will appear on everyleague.co.`
                 : "This will appear on everyleague.co.")
      : (waiting ? `No score will be published, so the ${scorers} you added `
                   + "cannot be saved yet."
                 : "No score will be published for this match.");
  }

  // drawDetail redraws only the detail block, but staging a scorer changes
  // what the publish button says. Handed over rather than exported so there is
  // one owner of the button's text.
  state.refreshPublish = refresh;

  view.querySelector("[data-status]").addEventListener("change", (event) => {
    state.status = event.target.value;
    refresh();
  });

  view.addEventListener("click", (event) => {
    const target = event.target.closest("[data-step]");
    if (!target || target.disabled) return;
    const [side, delta] = target.dataset.step.split(":");
    // 0..99: a negative score is meaningless and a fat-fingered 3-digit score
    // is a typo, not a scoreline.
    state[side] = Math.max(0, Math.min(99, state[side] + Number(delta)));
    refresh();
  });

  publishBtn.addEventListener("click", async () => {
    if (state.busy) return;                    // rule 2
    clearFlash();
    state.busy = true;
    refresh();

    const info = statusMeta(state.status);
    const { data, error } = await supabase.rpc("submit_match_report", {
      p_match_id: match.match_id,
      p_home_score: info.scored ? state.home : null,
      p_away_score: info.scored ? state.away : null,
      p_status: state.status,
      p_source_ref: state.source.trim(),
    });

    state.busy = false;
    if (error) {
      // Rule 1: state is untouched, so the score is still on screen and the
      // reporter can simply press publish again.
      refresh();
      flash(humanError(error), "error");
      return;
    }

    Object.assign(match, data && data[0] ? data[0] : {
      home_goals: info.scored ? state.home : null,
      away_goals: info.scored ? state.away : null,
      status: state.status,
    });
    state.published = true;
    // The home screen's buckets are now wrong for this match.
    invalidateHome();
    // ...and the run this match belongs to has one fewer match left in it.
    markQueued(match.public_id);

    // The goals now exist for the staged scorers to belong to. This is the
    // second half of the one button press, and it deliberately runs AFTER the
    // score is safely published — see flushPendingGoals.
    let message = "Published. The site updates in a few minutes.";
    let kind = "ok";
    if (state.pendingGoals.length) {
      if (!info.scored) {
        // Postponed, abandoned, cancelled: there is no score, so there are no
        // goals to attach to. The names are kept rather than thrown away.
        message = "Published. The scorers you added were not saved — a match "
                  + "with no score cannot have any.";
        kind = "warn";
      } else {
        state.busy = true;
        refresh();
        const { saved, failed, firstError } = await flushPendingGoals(match, state);
        state.busy = false;
        if (failed) {
          message = `Published${saved ? `, with ${saved} of `
            + `${saved + failed} scorers` : ""}. ${humanError(firstError)}`;
          kind = "error";
        } else {
          message = `Published with ${saved} scorer${saved === 1 ? "" : "s"}. `
                    + "The site updates in a few minutes.";
        }
      }
    }

    flash(message, kind, 6000);
    requestRebuild();
    drawMatch(match, names, state);
  });

  refresh();
}

/** Moving a fixture to another day.
 *
 *  Its own call to its own RPC, not part of publishing. submit_match_report
 *  cannot write `date` by design — being allowed to report a score is not
 *  permission to move a fixture — so rescheduling goes through
 *  reschedule_match, which writes date and kickoff and nothing else.
 *
 *  It also saves on its own, like every other section below the result: a
 *  reporter who came to enter a score must not have to think about the date,
 *  and someone fixing the date must not risk the score. */
function wireReschedule(match, names, state) {
  const dateEl = view.querySelector("[data-rs-date]");
  const timeEl = view.querySelector("[data-rs-kickoff]");
  const save = view.querySelector("[data-rs-save]");
  if (!dateEl || !save) return;

  // One lock for both buttons in this section. They write different columns
  // through different RPCs, but they share a screen: either save redraws the
  // whole match, so letting the second start while the first is in flight
  // would wipe what is typed in the other box.
  const lock = { busy: false };

  wireVenue(match, names, state, lock);

  save.addEventListener("click", async () => {
    if (lock.busy) return;                     // rule 2: never submit twice
    clearFlash();

    const date = dateEl.value || null;
    const kickoff = timeEl.value || "";
    if (date === (match.date || null)
        && kickoff === (match.kickoff || "").slice(0, 5)) {
      flash("That is already the date and kick-off.", "warn");
      return;
    }
    // A kickoff with no day is a time nobody can turn up for.
    if (!date && kickoff) {
      flash("Set a date as well, or clear the kick-off time.", "error");
      return;
    }

    lock.busy = true;
    save.disabled = true;
    save.textContent = "Saving…";

    const { data, error } = await supabase.rpc("reschedule_match", {
      p_match_id: match.match_id,
      p_date: date,
      p_kickoff: kickoff,
    });

    lock.busy = false;
    save.disabled = false;
    save.textContent = "Save new date";

    if (error) {
      // Rule 1: the inputs are untouched, so nothing typed is lost.
      flash(humanError(error), "error");
      return;
    }

    Object.assign(match, (data || [])[0] || { date, kickoff });
    // The match has probably moved between Today / Awaiting / Upcoming.
    invalidateHome();
    flash(match.date ? `Moved to ${formatDate(match.date)}.`
                     : "Date removed.", "ok");
    // The date is printed in the header line above the score, so the whole
    // screen is redrawn rather than patched. drawMatch records which sections
    // were open before it redraws, so this one comes back open and the
    // reporter can see the change landed where they made it.
    drawMatch(match, names, state);
    // Only a published result is live on the site; moving an unplayed fixture
    // still changes the fixture list, so both are worth a rebuild.
    requestRebuild();
  });
}


/** Moving a fixture to another ground.
 *
 *  Its own button and its own RPC, next to the date rather than merged with
 *  it. set_match_venue writes venue_id and nothing else, for the same reason
 *  reschedule_match writes date and kickoff and nothing else: two narrow doors
 *  are what make either of them safe to put in front of a reporter. One button
 *  saving both would also mean one failure that could have half happened, and
 *  a screen that cannot say which half.
 *
 *  The name typed here is resolved server-side, so what comes back may not be
 *  what was typed — "likuni ground" matches the Likuni Ground already in the
 *  database and the canonical spelling wins. That is the answer worth showing,
 *  so the box is redrawn from the response rather than left as typed. */
function wireVenue(match, names, state, lock) {
  const venueEl = view.querySelector("[data-rs-venue]");
  const save = view.querySelector("[data-rs-venue-save]");
  if (!venueEl || !save) return;

  save.addEventListener("click", async () => {
    if (lock.busy) return;                     // rule 2: never submit twice
    clearFlash();

    const typed = venueEl.value.trim();
    const current = match.venue?.name || "";
    if (typed.toLowerCase() === current.toLowerCase()) {
      flash(typed ? "That is already the ground." : "No ground is set.", "warn");
      return;
    }

    lock.busy = true;
    save.disabled = true;
    save.textContent = "Saving…";

    const { data, error } = await supabase.rpc("set_match_venue", {
      p_match_id: match.match_id,
      p_venue_name: typed,
    });

    lock.busy = false;
    save.disabled = false;
    save.textContent = "Save ground";

    if (error) {
      // Rule 1: the box is untouched, so nothing typed is lost.
      flash(humanError(error), "error");
      return;
    }

    const row = (data || [])[0];
    match.venue_id = row?.venue_id ?? null;
    // MATCH_FIELDS joins the name in as `venue`, and the rest of the screen
    // reads it from there — the header meta line above the score included. It
    // has to be patched to match, or the change lands in the database and the
    // screen goes on showing the old ground.
    match.venue = row?.venue_name ? { name: row.venue_name } : null;
    if (match.venue && !(state.venues || []).some(
          (n) => n.toLowerCase() === match.venue.name.toLowerCase())) {
      state.venues = (state.venues || []).concat(match.venue.name)
        .sort((a, b) => a.localeCompare(b));
    }

    // The ground is printed on the site's fixture list and on the home
    // screen's cards, so both are now out of date.
    invalidateHome();
    flash(match.venue ? `Ground set to ${match.venue.name}.`
                      : "Ground removed.", "ok");
    drawMatch(match, names, state);
    requestRebuild();
  });
}

/** Removing a fixture that never should have existed — a duplicate entry, or
 *  the wrong pairing tapped in on #/add. Admin-only (delete_fixture, 0032,
 *  checks again in Postgres) and only offered while the fixture is still
 *  'scheduled' — the section itself is absent from drawMatch's markup for
 *  anyone else or anything else. */
function wireDeleteFixture(match, state) {
  const del = view.querySelector("[data-delete-fixture]");
  if (!del) return;

  // Two taps, never a browser dialog — confirm() blocks every other event on
  // a page being read on a weak connection. Same rule trending's delete
  // follows (0030), for the same reason.
  let armed = false;
  del.addEventListener("click", async () => {
    if (del.disabled) return;
    if (!armed) {
      armed = true;
      del.textContent = "Tap again to delete for good";
      del.classList.add("is-danger");
      return;
    }

    del.disabled = true;
    del.textContent = "Deleting…";

    const { data: ok, error } = await supabase.rpc("delete_fixture", {
      p_match_id: match.match_id,
    });

    if (error || !ok) {
      del.disabled = false;
      armed = false;
      del.textContent = "Delete fixture";
      del.classList.remove("is-danger");
      flash(error ? humanError(error)
                  : "That fixture is already gone.", "error");
      return;
    }

    // Nothing left to redraw — the match this screen was showing no longer
    // exists, so leave rather than ask drawMatch to refetch it.
    invalidateHome();
    flash("Fixture deleted.", "ok");
    requestRebuild();
    location.hash = state.back;
  });
}

// ── Optional match detail ────────────────────────────────────────────────────
// Everything below the result is optional, and the app is built so a reporter
// can ignore all of it: score, status, publish, leave. These sections stay
// collapsed until asked for, so the screen a reporter sees in a hurry is the
// short one.
//
// Each section saves independently and immediately. Nothing here is part of
// the publish, so a failed photo upload cannot cost someone the result they
// already got in — the mistake the whole design is trying to avoid.

// ── Naming a scorer ──────────────────────────────────────────────────────────
// A goal has to say WHO, and "who" on everyleague.co is a player_id: that is
// what puts the goal in the league's top-scorer table and on the player's own
// page. A reporter has a name and a phone, so the gap between the two is this
// picker — type a few letters, tap the player, and if the player is genuinely
// new, tap once more to add them.
//
// It never blocks. A reporter with no signal, or one who simply types a name
// and presses Add, still gets the goal saved under that name (see
// submit_match_goal's optional p_player_id) — it shows on the match line and
// can be reconciled to a player later. Refusing to save a scorer because a
// lookup failed would be exactly the wrong trade for the connection this app
// is written for.

const UNKNOWN_PLAYER = "CAF_MW_UNKNOWN";

// Set by whichever scorer picker is currently on screen, so the one document
// click listener (see start()) can close its suggestion list.
let dismissPicker = null;

/** Characters that would be read as PostgREST filter syntax rather than as
 *  part of a name. Stripped rather than escaped: no Malawian player's name
 *  contains a bracket, and a stripped character costs one wrong suggestion
 *  where a mis-escaped one costs a 400. */
const FILTER_UNSAFE = /[(),"\\*]/g;

/** Find a player by whatever the reporter typed.
 *
 *  Through search_players (0022), which matches a substring, then an alias,
 *  then the surname with agreeing initials — so a sheet entered off a graphic
 *  as "A. Josephy" is found by someone typing "Andrew Josephy", instead of
 *  being invisible and inviting them to create a second Josephy. Duplicates
 *  are what this whole picker exists to avoid.
 *
 *  The ilike query it replaces is kept as a fallback, for one deployment-shaped
 *  reason: a migration is a separate deploy from git (see CLAUDE.md), so this
 *  file can reach a phone minutes before the function exists in the database.
 *  Falling back means those minutes cost recall, not the feature.
 */
async function searchPlayers(term) {
  const q = term.trim().replace(FILTER_UNSAFE, " ").replace(/\s+/g, " ").trim();
  // One letter matches most of the database and is not a search.
  if (q.length < 2) return [];
  const { data, error } = await supabase.rpc("search_players", { p_term: q });
  if (!error) return data || [];
  console.warn("[everyleague] search_players unavailable:", error);
  return await searchPlayersByName(q);
}

async function searchPlayersByName(q) {
  const like = `*${q}*`;
  const { data, error } = await supabase.from("players")
    .select("player_id,full_name,known_as")
    .or(`full_name.ilike.${like},known_as.ilike.${like}`)
    .neq("player_id", UNKNOWN_PLAYER)
    .limit(8);
  if (error) throw error;
  return data || [];
}

const playerLabel = (p) => p.known_as || p.full_name || p.player_id;

/** One scorer, saved. Returns the error rather than throwing, like every other
 *  save on this screen. */
async function saveGoal(match, entry) {
  const { error } = await supabase.rpc("submit_match_goal", {
    p_match_id: match.match_id,
    p_team_id: entry.team_id,
    p_player_name: entry.player_name,
    p_minute: entry.minute,
    p_goal_type: entry.goal_type,
    // Blank when the reporter typed a name without picking anyone. The goal
    // is still saved and still shown — see submit_match_goal.
    p_player_id: entry.player_id,
    // An assist has no name column to fall back on, so it is the picked id or
    // nothing at all. A typed name that resolved to nobody is simply dropped.
    p_assist_player_id: entry.assist_player_id || "",
  });
  return error;
}

/** Save the scorers staged before the score existed, now that it does.
 *
 *  ONE AT A TIME, IN ORDER, AND NOT IN PARALLEL. submit_match_goal takes a row
 *  lock on the match and counts the existing goals for that side before
 *  inserting, so concurrent calls would serialise on the lock anyway — and
 *  firing them together would make goal_id numbering depend on which request
 *  won, and turn one failure into an unattributable one.
 *
 *  A failure does NOT roll anything back. The score is already published and
 *  is the thing that mattered; a scorer that would not save stays staged so
 *  the reporter can press publish again. Losing a result because a name
 *  failed would be the trade this whole screen is built to avoid. */
async function flushPendingGoals(match, state) {
  const stuck = [];
  let saved = 0;
  let firstError = null;
  for (const entry of state.pendingGoals) {
    const error = await saveGoal(match, entry);
    if (error) {
      stuck.push(entry);
      firstError = firstError || error;
    } else {
      saved += 1;
    }
  }
  state.pendingGoals = stuck;
  return { saved, failed: stuck.length, firstError };
}

/** How many goals a side is credited with on the screen right now.
 *
 *  Before publishing that is the stepper the reporter is looking at; after, it
 *  is what the database holds. submit_match_goal enforces this again under a
 *  row lock — it has to, it is validate.py check 5 and a broken build — but a
 *  reporter should be told they are adding a third scorer to a 2-0 while they
 *  can still see the score, not by a rejection afterwards. */
function scoreFor(match, state, teamId) {
  const home = teamId === match.home_team_id;
  if (state.published) {
    const value = home ? match.home_goals : match.away_goals;
    return value == null ? 0 : value;
  }
  return home ? state.home : state.away;
}

/** Scorers already counted against a side: saved rows plus staged ones. */
function goalsNamedFor(saved, state, teamId) {
  return saved.filter((g) => g.team_id === teamId).length
    + (state.pendingGoals || []).filter((g) => g.team_id === teamId).length;
}

/** player_id -> team_id for everyone who has already scored for either side in
 *  this match. Two players share a name often enough that "has scored for
 *  Ekhaya" is usually the whole answer to which one the reporter means, and it
 *  is one query per match screen. */
function knownScorers(match) {
  return once(`scorers:${match.match_id}`, async () => {
    const { data } = await supabase.from("goals").select("player_id,team_id")
      .in("team_id", [match.home_team_id, match.away_team_id])
      .neq("player_id", UNKNOWN_PLAYER).limit(500);
    const seen = {};
    (data || []).forEach((g) => { seen[g.player_id] = g.team_id; });
    return seen;
  });
}

/** Which side did it. `checked` keeps the reporter's choice across the redraw
 *  that follows a save — a second scorer for the same team is the common case,
 *  and re-tapping the same button every time is the sort of small tax that
 *  stops people entering detail at all. */
function sideButtons(match, name, checked) {
  const home = checked ? checked === match.home_team_id : true;
  return `
    <div class="rp-side-pick" role="radiogroup">
      <label><input type="radio" name="${name}" value="${esc(match.home_team_id)}"${home ? " checked" : ""}>
        <span>${esc(match.home?.display_name || match.home_team_id)}</span></label>
      <label><input type="radio" name="${name}" value="${esc(match.away_team_id)}"${home ? "" : " checked"}>
        <span>${esc(match.away?.display_name || match.away_team_id)}</span></label>
    </div>`;
}

function section(key, title, count, inner, open) {
  const badge = count ? `<span class="rp-sec-count">${count}</span>` : "";
  return `<details class="rp-sec" data-sec="${key}"${open ? " open" : ""}>
    <summary>${esc(title)}${badge}</summary>
    <div class="rp-sec-body">${inner}</div>
  </details>`;
}

/** Everyone on a team-sheet graphic who is not a player (0023).
 *
 *  The coaches are held apart from the four officials because their labels
 *  name the two teams, which this list cannot know — and because the count on
 *  the section header deliberately does NOT include them: a coach is the thing
 *  a reporter copying a graphic will always have, so counting it would make
 *  every match look like its officials were entered when only the coaches
 *  were.
 */
const OFFICIAL_ROLES = [
  ["referee", "Referee", "referee"],
  ["assistant_referee_1", "Assistant referee 1", "referee"],
  ["assistant_referee_2", "Assistant referee 2", "referee"],
  ["fourth_official", "Fourth official", "referee"],
];
const OFFICIAL_COACHES = ["home_coach", "away_coach"];
const OFFICIAL_KEYS = OFFICIAL_ROLES.map(([key]) => key).concat(OFFICIAL_COACHES);
const OFFICIAL_COLUMNS = OFFICIAL_KEYS
  .concat(OFFICIAL_KEYS.map((key) => `${key}_id`))
  .join(",");

/** Find a referee or a coach by whatever the reporter typed.
 *
 *  search_officials (0024) is search_players with a kind filter, so the same
 *  thing is true of it: the name that gets typed first is the one off a
 *  graphic ("H. Nkhoma"), and someone later typing "Hassan Nkhoma" has to find
 *  that row rather than mint a second one.
 *
 *  Unlike searchPlayers there is no fallback query to fall back TO — before
 *  0024 there was no table. An error is therefore just an empty list, and the
 *  typed name still saves, which is the whole point of the id being optional.
 */
async function searchOfficials(term, kind) {
  const q = term.trim().replace(FILTER_UNSAFE, " ").replace(/\s+/g, " ").trim();
  if (q.length < 2) return [];
  const { data, error } = await supabase.rpc("search_officials", {
    p_term: q, p_kind: kind,
  });
  if (error) {
    console.warn("[everyleague] search_officials unavailable:", error);
    return [];
  }
  return data || [];
}

const officialLabel = (o) => o.known_as || o.full_name || o.official_id;

/** The officials panel: six optional boxes, saved as one.
 *
 *  One save for the whole panel, not one per box, because set_match_officials
 *  writes all twelve columns and a blank means blank — that is what lets a
 *  reporter delete a referee they mistyped. Splitting it per field would make
 *  "cleared" and "not sent" the same keystroke.
 *
 *  EVERY BOX CARRIES ITS LABEL ABOVE IT, not only inside it as a placeholder.
 *  A placeholder is gone the moment the first letter is typed, and six
 *  identical-looking boxes with nothing to tell them apart is how an assistant
 *  ends up in the fourth official's row. The two coach boxes name their team
 *  for the same reason.
 *
 *  Each box is a picker (0024): type, tap the person, and the hidden id beside
 *  it is what turns their name on everyleague.co into a link to their page.
 *  Tapping is optional and always was — a name with no id saves exactly as it
 *  did before, and renders as plain text.
 */
function officialsForm(match, officials) {
  const box = (name, label, kind) => `
    <label class="rp-label" for="off-${name}">${esc(label)}</label>
    <div class="rp-pick" data-official="${name}" data-kind="${kind}">
      <input class="rp-input" id="off-${name}" name="${name}"
             value="${esc(officials[name] || "")}" placeholder="${esc(label)}"
             role="combobox" aria-expanded="false" aria-autocomplete="list"
             autocomplete="off" autocapitalize="words" maxlength="80">
      <input type="hidden" name="${name}_id"
             value="${esc(officials[`${name}_id`] || "")}">
      <ul class="rp-suggest" role="listbox" data-suggest hidden></ul>
    </div>`;
  return `
    <form data-officials-form autocomplete="off">
      ${OFFICIAL_ROLES.map(([key, label, kind]) => box(key, label, kind)).join("")}
      ${box("home_coach", `${match.home?.display_name || "Home"} head coach`, "coach")}
      ${box("away_coach", `${match.away?.display_name || "Away"} head coach`, "coach")}
      <button class="rp-btn is-ghost" type="submit">Save officials</button>
      <p class="rp-hint" data-official-note>Every box is optional and an empty one
        shows nothing on everyleague.co — fill in whatever the graphic or the post
        actually says. Tap a name from the list and it becomes a link to that
        person's page. Clearing a box and saving removes that name.</p>
    </form>`;
}

/** Wire the six official comboboxes.
 *
 *  Deliberately not wirePlayerPicker: that one knows about goals, about which
 *  team the scorer is on, and about staging a name before the score exists.
 *  None of that is true here, and the half of it that would have to be
 *  disabled is more code than this whole function.
 */
function wireOfficialPickers(host) {
  host.querySelectorAll("[data-official]").forEach((wrap) => {
    const field = wrap.dataset.official;
    const kind = wrap.dataset.kind;
    const input = wrap.querySelector(`input[name="${field}"]`);
    const hidden = wrap.querySelector(`input[name="${field}_id"]`);
    const list = wrap.querySelector("[data-suggest]");
    const note = host.querySelector("[data-official-note]");
    if (!input || !hidden || !list) return;

    let timer = null;
    // Only the most recent search may paint: on a slow connection the answer
    // to "Nk" can arrive after the answer to "Nkhoma".
    let latest = 0;

    const close = () => {
      list.hidden = true;
      list.innerHTML = "";
      input.setAttribute("aria-expanded", "false");
    };
    wrap._closePicker = close;

    function choose(officialId, name) {
      hidden.value = officialId;
      input.value = name;
      close();
      if (note) {
        note.textContent = `${name} is identified — their name will link to `
          + "their own page. Save to keep it.";
        note.className = "rp-hint is-good";
      }
    }

    function paint(people, term) {
      const rows = people.map((o) => {
        const name = officialLabel(o);
        // Why this row is in the list: an alias hit is a spelling they used to
        // be filed under, and saying so is what makes it trustworthy.
        const hint = (o.matched && o.matched !== name)
          ? `also filed as ${o.matched}`
          : (o.known_as && o.full_name && o.known_as !== o.full_name
              ? o.full_name : "");
        return `<li role="option"><button type="button" class="rp-suggest-btn"
          data-official-id="${esc(o.official_id)}" data-name="${esc(name)}">
          <span>${esc(name)}</span>${hint ? `<em>${esc(hint)}</em>` : ""}</button></li>`;
      });
      const typed = term.trim();
      const exact = people.some(
        (o) => officialLabel(o).toLowerCase() === typed.toLowerCase());
      if (!exact) {
        rows.push(`<li role="option"><button type="button"
          class="rp-suggest-btn is-new" data-create="${esc(typed)}">
          <span>＋ Add “${esc(typed)}” as a new ${kind === "coach" ? "coach" : "referee"}</span>
          <em>only if they are not in the list above</em></button></li>`);
      }
      list.innerHTML = rows.join("");
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }

    input.addEventListener("input", () => {
      // The typed name no longer belongs to whoever was picked.
      hidden.value = "";
      clearTimeout(timer);
      const term = input.value;
      if (term.trim().length < 2) { close(); return; }
      timer = setTimeout(async () => {
        const mine = ++latest;
        const people = await searchOfficials(term, kind);
        if (mine !== latest) return;
        paint(people, term);
      }, 250);
    });

    input.addEventListener("focus", () => {
      if (!hidden.value && input.value.trim().length >= 2) {
        input.dispatchEvent(new Event("input"));
      }
    });

    list.addEventListener("click", async (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      if (button.dataset.officialId) {
        choose(button.dataset.officialId, button.dataset.name);
        return;
      }
      const name = button.dataset.create;
      if (!name || button.disabled) return;
      button.disabled = true;
      button.querySelector("span").textContent = "Adding…";
      const { data, error } = await supabase.rpc("create_official", {
        p_full_name: name, p_kind: kind,
      });
      if (error) {
        button.disabled = false;
        close();
        // Rule 3: a failed lookup must never read as a failed entry.
        if (note) {
          note.textContent = "Could not add that name to the register — it "
            + "will still be saved on the match as plain text.";
          note.className = "rp-hint is-warn";
        }
        console.warn("[everyleague] create_official failed:", error);
        return;
      }
      const created = (data || [])[0];
      // create_official is idempotent on (name, kind), so this may be someone
      // who already existed under a spelling the search did not surface.
      choose(created.official_id, officialLabel(created));
    });
  });

  // A tap anywhere outside every wrap means "not that list". The listener
  // goes on the body wrapper, NOT on `host`: host survives every redraw while
  // its innerHTML is replaced, so a listener added there would be added again
  // on the next draw and again on the one after that.
  host.querySelector("[data-detail-body]")?.addEventListener("click", (event) => {
    host.querySelectorAll("[data-official]").forEach((wrap) => {
      if (!wrap.contains(event.target)) wrap._closePicker?.();
    });
  });
}


/** Remember which sections are open and which side each form is set to.
 *
 *  Every save redraws the whole detail block from fresh data, which is what
 *  keeps the lists honest — but a <details> rebuilt from HTML is a CLOSED
 *  <details>. Entering two scorers therefore meant: type, save, watch the
 *  section shut, re-open it, re-pick the team, type. This is that bug's fix,
 *  and it is a read of the live DOM rather than a flag set on click, so it
 *  cannot drift out of step with what is actually on screen. */
function captureDetailState(state) {
  state.open = state.open || {};
  view.querySelectorAll("[data-sec]").forEach((el) => {
    state.open[el.dataset.sec] = el.open;
  });
  state.pick = state.pick || {};
  view.querySelectorAll(".rp-side-pick input:checked").forEach((el) => {
    state.pick[el.name] = el.value;
  });
}

/** Redraw the optional-detail block.
 *
 *  `local` means "nothing on the server changed" — staging a scorer, or
 *  removing one that was never saved. Those only move client state, and on the
 *  connection this app is written for, four round trips to redraw a list the
 *  phone just edited itself is the slowest thing on the screen. Every path
 *  that DOES touch the database still refetches, because that is what keeps
 *  the lists honest about what actually saved. */
async function drawDetail(match, state, { local = false } = {}) {
  const host = view.querySelector("[data-detail]");
  if (!host) return;

  // Read the screen BEFORE the queries below replace it: after the await the
  // reporter may already have collapsed something, and this has to record what
  // they last did, not what the page looked like when it finally answered.
  captureDetailState(state);
  const open = state.open || {};
  const pick = state.pick || {};

  const teamName = (id) => id === match.home_team_id
    ? (match.home?.display_name || id) : (match.away?.display_name || id);

  if (!local || !state.detail) {
    const [goalsRes, lineupRes, mediaRes, officialsRes] = await Promise.all([
      supabase.from("goals")
        .select("goal_id,team_id,reported_player_name,minute,player_id,assist_player_id")
        .eq("match_id", match.match_id).order("ord"),
      supabase.from("lineups").select("*")
        .eq("match_id", match.match_id).order("ord"),
      supabase.from("match_media").select("*")
        .eq("match_id", match.match_id).order("created_at"),
      // The officials AND the reporter's private notes (0025), which ride
      // along because they are columns on the same row and this is already
      // the one query that reads them.
      //
      // Its own query rather than six more columns on MATCH_FIELDS: that
      // constant is also the home screen's fixture list, and a phone loading
      // eighty fixtures should not be carrying eighty referees it will not
      // show. Separate also means that on a phone running this file before
      // 0023 reaches the database, the failure is an empty officials panel
      // and not a match screen with no scorers on it.
      supabase.from("matches").select(`${OFFICIAL_COLUMNS},notes`)
        .eq("match_id", match.match_id).limit(1),
    ]);
    state.detail = {
      goals: goalsRes.data || [],
      lineup: lineupRes.data || [],
      media: mediaRes.data || [],
      officials: (officialsRes.data || [])[0] || {},
    };
  }

  const { goals, lineup, media, officials } = state.detail;
  const scored = match.home_goals != null;

  // One team sheet per side, each with the squad that side has already
  // fielded. Both squads are fetched together and cached, so opening the
  // second sheet costs nothing.
  const sides = [
    { key: match.home_team_id, teamName: teamName(match.home_team_id) },
    { key: match.away_team_id, teamName: teamName(match.away_team_id) },
  ];
  const squads = await Promise.all(sides.map((side) => clubSquad(side.key)));
  sides.forEach((side, i) => {
    side.squad = squads[i];
    side.sheet = sheetState(state, side.key,
      lineup.filter((r) => r.team_id === side.key));
  });

  // A goal names its scorer twice over: reported_player_name is what someone
  // typed, and player_id is who that turned out to be. The list shows the
  // typed name — it is the one a reporter recognises — and marks whether it
  // reached a player page or is still just a name.
  const goalList = goals.map((g) => {
    const identified = g.player_id && g.player_id !== UNKNOWN_PLAYER;
    return `
    <li><span>${esc(g.reported_player_name || "Unknown")}
      <em>${esc(teamName(g.team_id))}${g.minute ? " · " + esc(g.minute) + "'" : ""}
        · ${identified ? "in the scorer table" : "name only"}</em></span>
      <button class="rp-x" type="button" data-del-goal="${esc(g.goal_id)}"
              aria-label="Remove ${esc(g.reported_player_name)}">&times;</button></li>`;
  }).join("");

  // Scorers typed before the score exists. They are shown in the same list as
  // saved ones — a reporter who has entered four names wants to see four
  // names — but marked as not yet saved, because until publish they exist
  // only on this phone.
  const stagedList = (state.pendingGoals || []).map((g, i) => `
    <li><span>${esc(g.player_name)}
      <em>${esc(teamName(g.team_id))}${g.minute ? " · " + esc(g.minute) + "'" : ""}
        · saves when you publish</em></span>
      <button class="rp-x" type="button" data-del-staged="${i}"
              aria-label="Remove ${esc(g.player_name)}">&times;</button></li>`).join("");

  const goalForm = `
    <form data-goal-form autocomplete="off">
      ${sideButtons(match, "goal-team", pick["goal-team"])}
      <div class="rp-pick" data-pick="player">
        <input class="rp-input" name="player" placeholder="Scorer's name" required
               autocomplete="off" autocapitalize="words" role="combobox"
               aria-expanded="false" aria-autocomplete="list"
               aria-controls="rp-player-list">
        <input type="hidden" name="player_id" value="">
        <ul class="rp-suggest" id="rp-player-list" role="listbox" data-suggest hidden></ul>
      </div>
      <p class="rp-hint" data-pick-note="player">Start typing and pick the player, so the
        goal counts towards their record and the league's top scorers.</p>
      <div class="rp-pick" data-pick="assist">
        <input class="rp-input" name="assist" placeholder="Assisted by (optional)"
               autocomplete="off" autocapitalize="words" role="combobox"
               aria-expanded="false" aria-autocomplete="list"
               aria-controls="rp-assist-list">
        <input type="hidden" name="assist_id" value="">
        <ul class="rp-suggest" id="rp-assist-list" role="listbox" data-suggest hidden></ul>
      </div>
      <p class="rp-hint" data-pick-note="assist">Leave blank if nobody set it up,
        or if you are not sure who did. An assist has to be picked from the list
        — there is nowhere to keep a name that matches no player.</p>
      <div class="rp-row">
        <input class="rp-input" name="minute" placeholder="Min" inputmode="numeric">
        <select class="rp-input" name="type">
          <option value="">Goal</option>
          <option value="penalty">Penalty</option>
          <option value="own_goal">Own goal</option>
          <option value="header">Header</option>
          <option value="free_kick">Free kick</option>
        </select>
      </div>
      <button class="rp-btn is-ghost" type="submit">Add scorer</button>
      <p class="rp-hint">${scored
        ? "Scorers show under the result on everyleague.co. Add one at a time — this stays open for the next."
        : "Add them now and they publish together with the score — one button, one go."}</p>
    </form>`;

  // Everything is inside one wrapper, and the delegated listener below goes on
  // THAT rather than on `host`. `host` survives every redraw while its
  // innerHTML is replaced, so a listener added to it would be added again on
  // the next draw — and one of these handlers splices a staged scorer out of
  // local state, which a duplicate would do twice. Redrawing is now what
  // happens on every tap of a squad name, so this stopped being theoretical.
  host.innerHTML = `
    <div data-detail-body>
    <h2 class="rp-field-head">Match detail <span class="rp-optional">optional</span></h2>

    ${section("goals", "Goalscorers",
      goals.length + (state.pendingGoals || []).length,
      `<ul class="rp-list">${goalList}${stagedList}</ul>${goalForm}`, open.goals)}

    ${sides.map((side) => section(
      `sheet-${side.key}`, `${side.teamName} team sheet`, side.sheet.rows.length,
      sheetHtml(side), open[`sheet-${side.key}`])).join("")}

    ${section("officials", "Officials and coaches",
      OFFICIAL_ROLES.filter(([key]) => officials[key]).length,
      officialsForm(match, officials), open.officials)}

    ${section("photo", "Photos", media.length, `
      <ul class="rp-list">${media.map((m) => `
        <li><span>${esc(m.caption || "Photo")}
          <em>${Math.round((m.byte_size || 0) / 1024)} KB</em></span>
          <button class="rp-x" type="button" data-del-media="${esc(m.id)}"
                  data-path="${esc(m.storage_path)}" aria-label="Remove photo">&times;</button></li>`).join("")}</ul>
      <form data-photo-form>
        <input class="rp-input" type="file" name="photo" accept="image/jpeg,image/png,image/webp">
        <input class="rp-input" name="caption" placeholder="Caption (optional)"
               autocomplete="off">
        <button class="rp-btn is-ghost" type="submit">Upload photo</button>
        <p class="rp-hint" data-upload-note>Large photos are shrunk on the phone
          before sending, so this works on a slow connection.</p>
      </form>`, open.photo)}

    ${section("notes", "Notes", officials.notes ? 1 : 0, `
      <form data-notes-form>
        <textarea class="rp-input rp-textarea" name="notes" rows="4"
                  maxlength="4000" autocapitalize="sentences"
                  placeholder="Anything you are not sure about, or want to check later"
                  >${esc(officials.notes || "")}</textarea>
        <button class="rp-btn is-ghost" type="submit">Save notes</button>
        <p class="rp-hint">Only reporters see this — it is never shown on
          everyleague.co and it is not in the public data files. Somewhere to
          write down that the second goal might be Phiri, or that the graphic
          lists twelve names. Clearing the box and saving deletes it.</p>
      </form>`, open.notes)}
    </div>`;

  wireDetail(match, state, goals, sides);
}

/** Run an action with a button locked, then redraw. Never throws at the caller. */
async function detailAction(button, busyLabel, fn, match, state) {
  if (button.disabled) return;                    // no double submits
  const original = button.textContent;
  button.disabled = true;
  button.textContent = busyLabel;
  try {
    const error = await fn();
    if (error) { flash(humanError(error), "error"); return; }
    flash("Saved.", "ok", 2500);
    await drawDetail(match, state);
  } catch (err) {
    flash(humanError(err), "error");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

/** Wire every player combobox on the screen.
 *
 *  There is more than one now: the scorer, and beside it whoever assisted. A
 *  wrap names its own fields through data-pick="<name>", so the two cannot
 *  read each other's input, and each carries its own note element. */
function wirePlayerPickers(host, match) {
  host.querySelectorAll("[data-pick]").forEach((wrap) => wirePlayerPicker(wrap, host, match));
  // A tap anywhere outside EVERY wrap means "not that list". One listener for
  // the screen rather than one per picker (see start()).
  dismissPicker = (event) => {
    host.querySelectorAll("[data-pick]").forEach((wrap) => {
      if (!wrap.contains(event.target)) wrap._closePicker?.();
    });
  };
}

/** The scorer combobox: search as you type, tap to identify, or add a player.
 *
 *  The hidden player_id field is the output. Everything else here exists to
 *  fill it in, and to be honest on screen about whether it is filled — a
 *  reporter should never have to guess whether the goal they just entered
 *  reached the scorer table. */
function wirePlayerPicker(wrap, host, match) {
  const field = wrap.dataset.pick || "player";
  const input = wrap.querySelector(`input[name="${field}"]`);
  const hidden = wrap.querySelector(`input[name="${field}_id"]`);
  const list = wrap.querySelector("[data-suggest]");
  const note = wrap.parentElement.querySelector(`[data-pick-note="${field}"]`)
    || host.querySelector("[data-pick-note]");
  if (!input || !hidden || !list || !note) return;
  const defaultNote = note.textContent;

  let timer = null;
  // Only the most recent search may paint. On a slow connection the answer to
  // "Th" can easily arrive after the answer to "Thandiwe", and a list that
  // rewinds under a moving finger is worse than no list.
  let latest = 0;

  const close = () => {
    list.hidden = true;
    list.innerHTML = "";
    input.setAttribute("aria-expanded", "false");
  };

  function setNote(text, kind) {
    note.textContent = text;
    note.className = kind ? `rp-hint is-${kind}` : "rp-hint";
  }

  function choose(playerId, name) {
    hidden.value = playerId;
    input.value = name;
    close();
    // Which record. Both boxes on this form resolve a name to a player and
    // both said "this goal counts towards their record", so picking an
    // assister told the reporter their goal tally had gone up — the one thing
    // an assist is not. The note is the only feedback either box gives, so it
    // has to be about the field it belongs to.
    setNote(field === "assist"
      ? `${name} is identified — the assist counts towards their record.`
      : `${name} is identified — this goal counts towards their record.`,
      "good");
  }

  // Read by the one document listener wirePlayerPickers installs.
  wrap._closePicker = close;

  function render(players, term, known) {
    const rows = players.map((p) => {
      const name = playerLabel(p);
      const teamId = known[p.player_id];
      // Why this row is in the list, in descending order of usefulness.
      // `matched` comes back from search_players when the hit was on an ALIAS
      // — a spelling this player used to be filed under — and saying so is
      // what makes a surname match trustworthy instead of surprising.
      const hint = teamId
        ? `has scored for ${teamId === match.home_team_id
            ? (match.home?.display_name || "the home team")
            : (match.away?.display_name || "the away team")}`
        : (p.matched && p.matched !== name ? `also filed as ${p.matched}`
          : (p.known_as && p.full_name && p.known_as !== p.full_name
              ? p.full_name : ""));
      return `<li role="option"><button type="button" class="rp-suggest-btn"
        data-player="${esc(p.player_id)}" data-name="${esc(name)}">
        <span>${esc(name)}</span>${hint ? `<em>${esc(hint)}</em>` : ""}</button></li>`;
    });

    // Offered unless the typed name IS one of the answers: a reporter looking
    // straight at "Thandiwe Phiri" in the list must not also be invited to
    // create a second one.
    const exact = players.some(
      (p) => playerLabel(p).toLowerCase() === term.trim().toLowerCase());
    if (!exact) {
      rows.push(`<li role="option"><button type="button"
        class="rp-suggest-btn is-new" data-create="${esc(term.trim())}">
        <span>＋ Add “${esc(term.trim())}” as a new player</span>
        <em>only if they are not in the list above</em></button></li>`);
    }
    list.innerHTML = rows.join("");
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  input.addEventListener("input", () => {
    // The typed name no longer belongs to whoever was picked.
    hidden.value = "";
    setNote(defaultNote);
    clearTimeout(timer);
    const term = input.value;
    if (term.trim().length < 2) { close(); return; }
    // Long enough that a two-finger typist does not fire a query per letter,
    // short enough to feel like it is keeping up.
    timer = setTimeout(async () => {
      const mine = ++latest;
      try {
        const [players, known] = await Promise.all([
          searchPlayers(term), knownScorers(match),
        ]);
        if (mine !== latest) return;
        // Familiar faces first: someone who has scored for one of these two
        // teams is far more likely to be the person meant.
        players.sort((a, b) =>
          Number(Boolean(known[b.player_id])) - Number(Boolean(known[a.player_id])));
        render(players, term, known);
      } catch (err) {
        if (mine !== latest) return;
        // Rule 3, and the reason the picker is optional: a failed lookup must
        // not read as a failed entry. The name they typed still saves.
        close();
        setNote("Could not search players just now — the name you typed will "
                + "still be saved with the goal.", "warn");
      }
    }, 250);
  });

  input.addEventListener("focus", () => {
    if (!hidden.value && input.value.trim().length >= 2) {
      input.dispatchEvent(new Event("input"));
    }
  });

  list.addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.player) {
      choose(button.dataset.player, button.dataset.name);
      return;
    }
    const name = button.dataset.create;
    if (!name || button.disabled) return;
    button.disabled = true;
    button.querySelector("span").textContent = "Adding…";
    const { data, error } = await supabase.rpc("create_player", {
      p_full_name: name,
    });
    if (error) {
      button.disabled = false;
      close();
      setNote("Could not add that player — the name will still be saved with "
              + "the goal.", "warn");
      console.warn("[everyleague] create_player failed:", error);
      return;
    }
    const created = (data || [])[0];
    // create_player is idempotent on the name, so this may be a player who
    // already existed under a spelling the search did not surface.
    choose(created.player_id, playerLabel(created));
  });

}

function wireDetail(match, state, goals, sides) {
  const host = view.querySelector("[data-detail]");
  const pick = (name) =>
    host.querySelector(`input[name="${name}"]:checked`)?.value;
  const reporterId = context.reporter?.reporter_id;
  const teamName = (id) => id === match.home_team_id
    ? (match.home?.display_name || id) : (match.away?.display_name || id);

  wirePlayerPickers(host, match);
  wireOfficialPickers(host);

  // ADDING A SCORER MEANS TWO DIFFERENT THINGS, DEPENDING ON ONE FACT.
  //
  // Before the score is published there is no goal for a scorer to belong to —
  // submit_match_goal refuses, and it is right to, because validate.py check 5
  // rejects goal rows on a match with no score. So the name is STAGED on the
  // phone and saved by the publish that creates the goals it needs. One
  // button: "Publish 2–0 FT + 2 scorers".
  //
  // Afterwards the goals exist, so a scorer saves the moment it is added, as
  // it always has. That is the right behaviour for editing a published result
  // and there was no reason to change it.
  host.querySelector("[data-goal-form]")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    const teamId = pick("goal-team");
    const playerName = f.player.value.trim();
    if (!playerName) { flash("Type the scorer's name first.", "warn"); return; }

    // Said here as well as in the RPC so the reporter is told while the score
    // is still on screen and editable, rather than by a rejection.
    const allowed = scoreFor(match, state, teamId);
    if (goalsNamedFor(goals, state, teamId) >= allowed) {
      flash(allowed === 0
        ? `${teamName(teamId)} did not score in this match.`
        : `All ${allowed} of ${teamName(teamId)}'s goals already have a scorer.`,
        "warn");
      return;
    }

    const entry = {
      team_id: teamId,
      player_name: playerName,
      player_id: f.player_id.value,
      assist_player_id: f.assist_id.value,
      minute: f.minute.value.trim(),
      goal_type: f.type.value,
    };

    if (!state.published) {
      state.pendingGoals.push(entry);
      // The publish button names what it is about to do, so it has to be
      // redrawn as well as the list. Nothing left the phone, hence local.
      drawDetail(match, state, { local: true });
      state.refreshPublish?.();
      flash("Added — it saves when you publish.", "ok", 2500);
      return;
    }

    detailAction(f.querySelector('button[type="submit"]'), "Adding…", async () => {
      const error = await saveGoal(match, entry);
      if (!error) {
        // Unlike a card or a line-up, a scorer is rendered on the public site,
        // so it is worth a build the same way a score is.
        requestRebuild();
      }
      return error;
    }, match, state);
  });

  wireSheets(host, (sides || []).map((side) => ({
    key: side.key, teamName: side.teamName, sheet: side.sheet, squad: side.squad,
    save: async (rows) => (await supabase.rpc("save_lineup", {
      p_match_id: match.match_id, p_team_id: side.key, p_rows: rows,
    })).error,
    saved: () => {
      // Re-read after a save: save_lineup derives each starter's minute_off
      // from the substitutions, so what came back is not quite what went up.
      state.detail = null;
      delete state.sheets[side.key];
      drawDetail(match, state);
    },
  })), () => drawDetail(match, state, { local: true }));

  host.querySelector("[data-officials-form]")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    detailAction(f.querySelector('button[type="submit"]'), "Saving…", async () => {
      const args = { p_match_id: match.match_id };
      // Name and id together, every box, every save. A box whose name was
      // typed and never tapped sends a blank id and saves as plain text —
      // which is the state almost every referee on this site is in.
      OFFICIAL_KEYS.forEach((key) => {
        args[`p_${key}`] = f[key].value.trim();
        args[`p_${key}_id`] = f[`${key}_id`].value.trim();
      });
      const { error } = await supabase.rpc("set_match_officials", args);
      // A referee and a coach both render on the public site, under the
      // result, so they earn a build the same way a scorer does.
      if (!error) requestRebuild();
      return error;
    }, match, state);
  });

  host.querySelector("[data-notes-form]")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    detailAction(f.querySelector('button[type="submit"]'), "Saving…", async () => {
      const { error } = await supabase.rpc("set_match_notes", {
        p_match_id: match.match_id, p_notes: f.notes.value,
      });
      // NO requestRebuild. A note changes nothing on the public site, and
      // asking CI to rebuild the world because someone wrote themselves a
      // reminder would be the one save on this screen that does nothing twice.
      return error;
    }, match, state);
  });

  host.querySelector("[data-photo-form]")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    const file = f.photo.files?.[0];
    if (!file) { flash("Choose a photo first.", "warn"); return; }
    const note = host.querySelector("[data-upload-note]");
    detailAction(f.querySelector("button"), "Uploading…", async () => {
      let blob;
      try {
        note.textContent = "Shrinking…";
        blob = await shrinkImage(file);
      } catch (err) {
        return { message: "That file is not a supported image." };
      }
      note.textContent = `Sending ${Math.round(blob.size / 1024)} KB…`;
      // Unique filename, never upsert: two reporters uploading at the same
      // second must not overwrite each other.
      const path = `${match.public_id}/${crypto.randomUUID()}.jpg`;
      const up = await supabase.storage.from("match-media")
        .upload(path, blob, { contentType: "image/jpeg", upsert: false });
      if (up.error) return up.error;
      const { error } = await supabase.from("match_media").insert({
        match_id: match.match_id, storage_path: path,
        caption: f.caption.value.trim(), content_type: "image/jpeg",
        byte_size: blob.size, reported_by: reporterId,
      });
      return error;
    }, match, state);
  });

  host.querySelector("[data-detail-body]")?.addEventListener("click", (e) => {
    // A staged scorer has never been saved, so removing it is a splice, not a
    // request. Handled before the others because it must not fall through to
    // detailAction, which would try to redraw against a network call.
    const stagedRow = e.target.closest("[data-del-staged]");
    if (stagedRow) {
      state.pendingGoals.splice(Number(stagedRow.dataset.delStaged), 1);
      drawDetail(match, state, { local: true });
      state.refreshPublish?.();
      return;
    }
    const goal = e.target.closest("[data-del-goal]");
    const photo = e.target.closest("[data-del-media]");
    if (goal) {
      detailAction(goal, "…", async () => {
        const { error } = await supabase.rpc("delete_match_goal",
          { p_goal_id: goal.dataset.delGoal });
        // A removed scorer is a change to a published page, same as an added
        // one — the site should stop showing a name the reporter took back.
        if (!error) requestRebuild();
        return error;
      }, match, state);
    } else if (photo) {
      detailAction(photo, "…", async () => {
        await supabase.storage.from("match-media").remove([photo.dataset.path]);
        return (await supabase.from("match_media").delete()
          .eq("id", photo.dataset.delMedia)).error;
      }, match, state);
    }
  });
}

/** Shrink a phone photo before sending it over a weak connection.
 *
 * A modern Android camera produces 4-8 MB; the interesting content survives
 * 1600px and ~200 KB perfectly well. The bucket also caps size and MIME type
 * server-side, so a client that skipped this could not sneak a huge file past
 * it — this is about the reporter's data bundle, not about trust.
 */
async function shrinkImage(file, maxPx = 1600, quality = 0.82) {
  if (!/^image\/(jpeg|png|webp)$/.test(file.type)) {
    throw new Error("unsupported image type");
  }
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, maxPx / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise((resolve) =>
    canvas.toBlob(resolve, "image/jpeg", quality));
  bitmap.close?.();
  return blob || file;
}

/** Ask for a site rebuild. Deliberately not awaited, and never shown to the
 *  reporter — but no longer silent to anyone.
 *
 * everyleague.co is static, so the result is in the database but not yet on
 * the page until GitHub Actions rebuilds. This nudges that along.
 *
 * It is best-effort by design: the publish has ALREADY succeeded, and the
 * reporter's job is done. If this request never lands — the phone dropped
 * signal the moment after publishing, which is exactly when it would — the
 * daily cron still ships the result, and the next reporter to publish
 * dispatches a build that includes it. Turning a successful publish into a
 * visible failure over a deploy nudge would be a lie about what happened.
 *
 * WHAT CHANGED, AND WHY. The failure was previously swallowed by `() => {}`,
 * which is how two separate faults — a dead anon key, then a missing CORS
 * preflight — each ran for a day with results piling up unbuilt and nothing
 * anywhere saying so. "Do not alarm the reporter" is right; "leave no trace"
 * was not. The outcome now goes to the console either way, so the next time
 * this breaks, opening the browser console on the phone answers it in one
 * line instead of requiring a database forensics session.
 */
function requestRebuild() {
  try {
    supabase.functions.invoke("trigger-rebuild", { body: {} })
      .then(({ data, error }) => {
        if (error) console.warn("[everyleague] rebuild nudge failed:", error);
        else console.info("[everyleague] rebuild nudge:", data);
      }, (err) => console.warn("[everyleague] rebuild nudge threw:", err));
  } catch (err) {
    console.warn("[everyleague] rebuild nudge could not be sent:", err);
  }
}

async function renderAccount() {
  const { data: { user } } = await supabase.auth.getUser();
  h(`
    <h1 class="rp-login-head">Account</h1>
    <div class="rp-account-row"><span>Name</span><span>${esc(context.reporter?.name || "—")}</span></div>
    <div class="rp-account-row"><span>Email</span><span>${esc(user?.email || "—")}</span></div>
    <div class="rp-account-row"><span>Role</span><span>${context.isAdmin ? "Administrator" : "Reporter"}</span></div>

    <h2 class="rp-field-head">Change password</h2>
    <form class="rp-form" data-password>
      <label class="rp-label" for="new-password">New password</label>
      <input class="rp-input" id="new-password" name="password" type="password"
             required minlength="8" autocomplete="new-password">
      <p class="rp-hint">At least 8 characters.</p>
      <button class="rp-btn" type="submit" data-submit>Change password</button>
    </form>

    <h2 class="rp-field-head">Players</h2>
    <p class="rp-hint">Fix a name that was entered short — "A. Josephy" off a
      graphic, before anyone knew the first name — or merge two ids that turned
      out to be one person.</p>
    <a class="rp-btn is-ghost" href="#/players">Find a player</a>

    ${context.isAdmin ? `
    <h2 class="rp-field-head">Reporters</h2>
    <p class="rp-hint">Create an account for somebody new, give them leagues,
      or reset a password.</p>
    <a class="rp-btn is-ghost" href="#/reporters">Manage reporters</a>` : ""}

    <div class="rp-btn-row" style="margin-top:24px">
      <a class="rp-btn is-ghost" href="#/">My matches</a>
      <button class="rp-btn is-quiet" type="button" data-signout>Sign out</button>
    </div>
  `);

  const form = view.querySelector("[data-password]");
  const button = form.querySelector("[data-submit]");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true;
    button.textContent = "Saving…";
    const { error } = await supabase.auth.updateUser({
      password: form.password.value,
    });
    button.disabled = false;
    button.textContent = "Change password";
    if (error) { flash(humanError(error), "error"); return; }
    form.reset();
    flash("Password changed.", "ok");
  });

  view.querySelector("[data-signout]").onclick = signOut;
}

// ── Players ──────────────────────────────────────────────────────────────────
// THE SCREEN THAT MAKES A SHORT NAME SAFE TO ENTER.
//
// A team-sheet graphic gives an initial and a surname — "4. A. Josephy" — and
// that is what goes into the sheet, because the alternative is not entering
// the line-up at all. The id minted for that name is the person; the name is a
// label on it, and the site renders the label from the players row wherever an
// id resolves (see src/lineups.py). So the first name turning up later is not
// a problem, PROVIDED there is somewhere to say so. This is that somewhere.
//
// Two operations, and the split in who may run them is deliberate:
//
//   * Rename — any reporter. It is the other half of create_player, which
//     they already have, and it is reversible.
//   * Merge  — admins only. It deletes a row and repoints history across
//     seven tables, and nothing in this portal can undo it.

const PLAYER_SEARCH_MIN = 2;

async function renderPlayers(params) {
  const term = params.get("q") || "";
  h(`
    <h1 class="rp-login-head">Players</h1>
    <p class="rp-login-sub">Correct a name, or merge two ids that turned out to
      be one person. A rename reaches every team sheet, scorer line and profile
      at the next build.</p>
    <form class="rp-form" data-player-search autocomplete="off">
      <input class="rp-input" name="q" value="${esc(term)}" autocomplete="off"
             autocapitalize="words" placeholder="Search by name">
    </form>
    <div data-player-results></div>
    <div class="rp-btn-row" style="margin-top:24px">
      <a class="rp-btn is-ghost" href="#/">My matches</a>
    </div>
  `);

  const form = view.querySelector("[data-player-search]");
  const input = form.querySelector('input[name="q"]');
  let timer = null;
  // Only the most recent search may paint — the same rule the scorer picker
  // follows, and for the same reason: a list that rewinds under a moving
  // finger is worse than no list.
  let latest = 0;

  async function run() {
    const q = input.value.trim();
    // The URL carries the term so a back button, or a reload after a rename,
    // comes back to the same list rather than to an empty box.
    history.replaceState(null, "",
      `#/players${q ? "?q=" + encodeURIComponent(q) : ""}`);
    const host = view.querySelector("[data-player-results]");
    if (q.length < PLAYER_SEARCH_MIN) {
      host.innerHTML = '<p class="rp-hint">Type at least two letters.</p>';
      return;
    }
    const mine = ++latest;
    let players;
    try {
      players = await searchPlayers(q);
    } catch (error) {
      if (mine !== latest) return;
      host.innerHTML = "";
      flash(humanError(error), "error");
      return;
    }
    if (mine !== latest) return;
    if (!players.length) {
      host.innerHTML = `<p class="rp-empty">Nobody found for
        “${esc(q)}”. Players are created from the match screen, never here.</p>`;
      return;
    }
    host.innerHTML = players.map(playerCard).join("");
    wirePlayerCards(host, run);
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); run(); });
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(run, 250);
  });
  // A tap anywhere outside a merge box means "not that list" — one listener
  // for the screen rather than one per card, the same arrangement the scorer
  // pickers use. route() clears it on the way out.
  dismissPicker = (event) => {
    view.querySelectorAll("[data-merge]").forEach((wrap) => {
      if (!wrap.contains(event.target)) wrap._closeMerge?.();
    });
  };
  run();
}

function playerCard(p) {
  const name = playerLabel(p);
  const alias = p.matched && p.matched !== name
    ? `<p class="rp-hint">Also filed as “${esc(p.matched)}”.</p>` : "";
  return `
    <details class="rp-sec" data-player-card data-player-id="${esc(p.player_id)}"
             data-player-name="${esc(name)}">
      <summary>${esc(name)}<span class="rp-sec-count">${esc(p.player_id.replace(/^CAF_MW_/, ""))}</span></summary>
      <div class="rp-sec-body">
        ${alias}
        <p class="rp-hint" data-player-activity>Counting what is attached…</p>

        <h2 class="rp-field-head">Name</h2>
        <form data-rename-form autocomplete="off">
          <input class="rp-input" name="full_name" value="${esc(p.full_name || "")}"
                 autocapitalize="words" maxlength="80" required
                 placeholder="Full name">
          <input class="rp-input" name="known_as" value="${esc(p.known_as || "")}"
                 autocapitalize="words" maxlength="80"
                 placeholder="Known as (optional)">
          <button class="rp-btn is-ghost" type="submit">Save name</button>
          <p class="rp-hint">The old spelling is kept, so this player is still
            found by searching for it.</p>
        </form>

        ${context.isAdmin ? `
        <h2 class="rp-field-head">Merge</h2>
        <div class="rp-pick" data-merge>
          <input class="rp-input" name="merge" placeholder="Merge THIS player into…"
                 autocomplete="off" autocapitalize="words">
          <ul class="rp-suggest" data-suggest hidden></ul>
        </div>
        <p class="rp-hint" data-merge-note>Everything ${esc(name)} is named on
          moves to the player you pick, and ${esc(name)} is deleted. This cannot
          be undone here.</p>` : ""}
      </div>
    </details>`;
}

/** Wire every player card on the screen. `redraw` re-runs the search. */
function wirePlayerCards(host, redraw) {
  host.querySelectorAll("[data-player-card]").forEach((card) => {
    const playerId = card.dataset.playerId;
    const name = card.dataset.playerName;

    // What is attached, fetched only when the card is opened: it is two
    // queries per player and the answer only matters to someone deciding which
    // of two duplicates should survive.
    card.addEventListener("toggle", () => {
      if (!card.open || card._counted) return;
      card._counted = true;
      playerActivity(playerId).then((text) => {
        const el = card.querySelector("[data-player-activity]");
        if (el) el.textContent = text;
      });
    });

    card.querySelector("[data-rename-form]")?.addEventListener("submit", (e) => {
      e.preventDefault();
      const f = e.target;
      const button = f.querySelector('button[type="submit"]');
      if (button.disabled) return;
      button.disabled = true;
      button.textContent = "Saving…";
      supabase.rpc("rename_player", {
        p_player_id: playerId,
        p_full_name: f.full_name.value.trim(),
        p_known_as: f.known_as.value.trim(),
      }).then(({ error }) => {
        button.disabled = false;
        button.textContent = "Save name";
        if (error) { flash(humanError(error), "error"); return; }
        // A name is on every page this player appears on, so correcting one is
        // a change to the published site, exactly as a scorer is.
        requestRebuild();
        flash("Name saved.", "ok");
        redraw();
      });
    });

    const merge = card.querySelector("[data-merge]");
    if (merge) wireMergePicker(merge, playerId, name, redraw);
  });
}

/** goals + team sheets attached to a player, as one readable line. */
async function playerActivity(playerId) {
  try {
    const [goals, sheets] = await Promise.all([
      supabase.from("goals").select("goal_id", { count: "exact", head: true })
        .eq("player_id", playerId),
      supabase.from("lineups").select("match_id", { count: "exact", head: true })
        .eq("player_id", playerId),
    ]);
    const g = goals.count || 0;
    const s = sheets.count || 0;
    if (!g && !s) return "Nothing attached to this id yet.";
    return `${g} goal${g === 1 ? "" : "s"} · ${s} team sheet${s === 1 ? "" : "s"}.`;
  } catch (err) {
    // A count that will not load costs information, never the screen.
    return "";
  }
}

/** The "merge into…" box on one card.
 *
 *  Two taps, never a browser dialog: the first tap names what is about to
 *  happen in the button itself, the second does it. A confirm() would block
 *  every other event on the page while it sits there, and this portal is
 *  written for a phone on a bad connection where that reads as a freeze.
 */
function wireMergePicker(wrap, loserId, loserName, redraw) {
  const input = wrap.querySelector('input[name="merge"]');
  const list = wrap.querySelector("[data-suggest]");
  const note = wrap.parentElement.querySelector("[data-merge-note]");
  let timer = null;
  let latest = 0;
  let armed = "";

  const close = () => { list.hidden = true; list.innerHTML = ""; armed = ""; };
  // Read by the one document listener renderPlayers installs.
  wrap._closeMerge = close;

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const term = input.value.trim();
    if (term.length < PLAYER_SEARCH_MIN) { close(); return; }
    timer = setTimeout(async () => {
      const mine = ++latest;
      let players;
      try {
        players = await searchPlayers(term);
      } catch (error) {
        if (mine !== latest) return;
        close();
        flash(humanError(error), "error");
        return;
      }
      if (mine !== latest) return;
      const rows = players
        .filter((p) => p.player_id !== loserId)
        .map((p) => `<li role="option"><button type="button" class="rp-suggest-btn"
          data-winner="${esc(p.player_id)}" data-winner-name="${esc(playerLabel(p))}">
          <span>${esc(playerLabel(p))}</span>
          <em>keep this one</em></button></li>`);
      list.innerHTML = rows.length ? rows.join("")
        : '<li><button type="button" class="rp-suggest-btn" disabled><span>Nobody else found</span></button></li>';
      list.hidden = false;
    }, 250);
  });

  list.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-winner]");
    if (!button || button.disabled) return;
    const winnerId = button.dataset.winner;
    const winnerName = button.dataset.winnerName;

    if (armed !== winnerId) {
      armed = winnerId;
      button.querySelector("span").textContent =
        `Tap again: delete ${loserName}, keep ${winnerName}`;
      button.classList.add("is-new");
      return;
    }

    button.disabled = true;
    button.querySelector("span").textContent = "Merging…";
    const { error } = await supabase.rpc("merge_players", {
      p_loser: loserId, p_winner: winnerId,
    });
    if (error) {
      button.disabled = false;
      close();
      flash(humanError(error), "error");
      return;
    }
    requestRebuild();
    close();
    input.value = "";
    if (note) note.textContent = `Merged into ${winnerName}.`;
    flash(`Merged ${loserName} into ${winnerName}.`, "ok");
    redraw();
  });
}


async function signOut() {
  await supabase.auth.signOut();
  context = null;
  // The next person to sign in on this phone must not see the last one's
  // fixture list, or a competition menu built from their assignments.
  invalidateHome();
  invalidateReference();
  location.hash = "#/login";
}

// ── Trending ─────────────────────────────────────────────────────────────────
// THE FRONT OF EVERYLEAGUE.CO, EDITABLE WITHOUT A DEPLOY.
//
// The homepage used to lead with one card written into build.py as an
// f-string. Changing a sentence on it meant editing Python and pushing to
// main, so it was changed roughly never and the first thing a reader saw was
// usually last month's story — at one point it was still inviting people to
// follow a final that had been played and lost.
//
// This is the lite CMS behind that slot (0030): write a card, put a photo on
// it, point it at a page on the site, publish. Three states, and the middle
// one is the point:
//
//   Drafts    written, not on the site. Thursday's weekend preview.
//   On site   live, in the carousel, in the order set here.
//   Archive   taken down and KEPT. Last month's preview is the skeleton of
//             this month's, which is what Duplicate is for.
//
// Admin only, in the router and in Postgres both — every write below is an
// is_admin()-gated RPC, so a reporter who reaches this URL gets a refusal from
// the database and not just a hidden button.
//
// EVERY CHANGE THAT TOUCHES A LIVE CARD NUDGES A REBUILD, and only those do.
// The site is static: publishing a card puts it in the database and nowhere a
// reader can see it until GitHub Actions runs. Saving a draft changes no
// published page and asking CI to rebuild the world for it would be a build
// that does nothing.

const TRENDING_BUCKET = "trending-media";
const TRENDING_HEADLINE_MAX = 90;
const TRENDING_BODY_MAX = 400;
// Matches src/trending.py. Above this the homepage is carrying more photos
// than a phone on an expensive connection should be asked to fetch — a warning
// on the screen, never a refusal, because which card comes down is an
// editorial decision and not this app's.
const TRENDING_COMFORTABLE_LIVE = 5;

// Tappable suggestions rather than a fixed enum. The column is free text on
// purpose — the useful set is not knowable in advance — but a card that says
// "Weekend preview" this week and "Weekend Preview!!" next week is exactly the
// inconsistency this screen exists to fix, so the common ones are one tap.
//
// These are only the STARTING vocabulary. trendingLabels() adds every label
// already used on any card, so a label typed once through ＋ is a chip from
// then on. That is why nothing new was stored to remember them: the cards
// already are the record of what this site calls things, and a second list of
// "known labels" would be one more thing to keep in step with them.
const TRENDING_EYEBROWS = [
  "Weekend preview", "Matchday review", "Player of the week",
  "Top scorers", "Team of the week", "Cup special", "Transfer news",
];

/** The chip row: the presets, then everything already in use, deduped.
 *
 *  Case-insensitively, keeping the first spelling seen — so the presets win
 *  the casing and "weekend preview" typed in a hurry does not become a second
 *  chip beside "Weekend preview". Used labels sort alphabetically after the
 *  presets rather than by recency: a row that reorders itself between renders
 *  makes the tap you were about to make land on something else.
 */
function trendingLabels(cards) {
  const out = [];
  const seen = new Set();
  const add = (label) => {
    const key = (label || "").trim().toLowerCase();
    if (!key || seen.has(key)) return;
    seen.add(key);
    out.push(label.trim());
  };
  TRENDING_EYEBROWS.forEach(add);
  const used = cards.map((c) => c.eyebrow || "").filter(Boolean)
    .sort((a, b) => a.localeCompare(b));
  used.forEach(add);
  return out;
}

const sameLabel = (a, b) =>
  (a || "").trim().toLowerCase() === (b || "").trim().toLowerCase();

const TRENDING_TABS = [
  ["live", "On the site"],
  ["draft", "Drafts"],
  ["archived", "Archive"],
];

const TRENDING_STATUS_WORD = {
  live: "On the site", draft: "Draft", archived: "Archived",
};

/** Every card, drafts and archive included. Public read (the cards are
 *  published on the homepage), so this needs no special key — the WRITES are
 *  what is gated. */
async function loadTrending() {
  const { data, error } = await supabase.from("trending")
    .select("card_id,status,eyebrow,headline,body,link_url,link_label," +
            "image_path,image_alt,image_credit,sort_order,published_at,updated_at")
    .order("sort_order", { ascending: true })
    .order("card_id", { ascending: true });
  if (error) throw error;
  return data || [];
}

/** The bucket is public — the static site has to be able to <img src> the
 *  result and a signed URL would expire long before the next rebuild. */
function trendingImageUrl(path) {
  if (!path) return "";
  return supabase.storage.from(TRENDING_BUCKET).getPublicUrl(path).data.publicUrl;
}

/** Shrink and send one photo; returns its object name.
 *
 *  The path is dated rather than keyed on the card, because an image is
 *  uploaded BEFORE a new card has an id — and because duplicating a card
 *  copies the path, so an object never belonged to exactly one card anyway.
 *  Nothing here ever deletes an object: see 0030's header.
 */
async function uploadTrendingImage(file) {
  const blob = await shrinkImage(file);
  const now = new Date();
  const folder = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const path = `${folder}/${crypto.randomUUID()}.jpg`;
  const { error } = await supabase.storage.from(TRENDING_BUCKET)
    .upload(path, blob, { contentType: "image/jpeg", upsert: false });
  if (error) throw error;
  return path;
}

/** What the card will look like on the homepage. Not decoration: the whole
 *  ask was a front page that grips, and a headline that is three words too
 *  long is obvious here and invisible in a form field. */
function trendingPreview(card) {
  const url = trendingImageUrl(card.image_path);
  return `
    <div class="rp-trend-preview" data-trend-preview>
      ${url ? `<img src="${esc(url)}" alt="${esc(card.image_alt || "")}">`
            : '<div class="rp-trend-noimg">No photo</div>'}
      <div class="rp-trend-text">
        ${card.eyebrow ? `<span class="rp-trend-eyebrow">${esc(card.eyebrow)}</span>` : ""}
        <span class="rp-trend-title">${esc(card.headline || "Untitled")}</span>
        ${card.body ? `<span class="rp-trend-copy">${esc(card.body)}</span>` : ""}
        ${card.link_url
          ? `<span class="rp-trend-cta">${esc(card.link_label || "Read more")} &rarr;</span>`
          : ""}
        ${card.image_path && card.image_credit
          ? `<span class="rp-trend-credit">Photo: ${esc(card.image_credit)}</span>`
          : ""}
      </div>
    </div>`;
}

/** The editor. One function for a new card and an existing one — it is the
 *  same form either way, which is also why save_trending_card is one RPC. */
function trendingFields(card, labels) {
  const c = card || {};
  return `
    <label class="rp-label" for="tr-eyebrow-${esc(c.card_id || "new")}">Label</label>
    <input class="rp-input" id="tr-eyebrow-${esc(c.card_id || "new")}" name="eyebrow"
           maxlength="40" autocapitalize="sentences" autocomplete="off"
           value="${esc(c.eyebrow || "")}" placeholder="Weekend preview">
    <div class="rp-chip-row" data-eyebrow-chips>
      ${labels.map((e) =>
        `<button class="rp-chip${sameLabel(c.eyebrow, e) ? " is-on" : ""}" type="button"
                 data-eyebrow="${esc(e)}">${esc(e)}</button>`).join("")}
      <button class="rp-chip is-add" type="button" data-eyebrow-new
              aria-label="Write a new label" title="Write a new label">＋</button>
    </div>
    <p class="rp-hint">Tap one, or <strong>＋</strong> to write your own — the
      box above takes anything. A label you use once is a chip here from then
      on, so the site keeps calling the same thing the same name.</p>

    <label class="rp-label" for="tr-headline-${esc(c.card_id || "new")}">Headline</label>
    <input class="rp-input" id="tr-headline-${esc(c.card_id || "new")}" name="headline"
           maxlength="${TRENDING_HEADLINE_MAX}" required autocapitalize="sentences"
           autocomplete="off" value="${esc(c.headline || "")}"
           placeholder="The journey to the final">
    <p class="rp-hint" data-count="headline"></p>

    <label class="rp-label" for="tr-body-${esc(c.card_id || "new")}">Words</label>
    <textarea class="rp-input rp-textarea" id="tr-body-${esc(c.card_id || "new")}"
              name="body" rows="4" maxlength="${TRENDING_BODY_MAX}"
              autocapitalize="sentences"
              placeholder="Two or three sentences. Say what happened, not that something happened."
              >${esc(c.body || "")}</textarea>
    <p class="rp-hint" data-count="body"></p>

    <label class="rp-label" for="tr-link-${esc(c.card_id || "new")}">Link</label>
    <div class="rp-btn-row">
      <input class="rp-input" id="tr-link-${esc(c.card_id || "new")}" name="link_url"
             autocapitalize="off" autocorrect="off" spellcheck="false"
             autocomplete="off" value="${esc(c.link_url || "")}"
             placeholder="/scorchers/">
      <button class="rp-btn is-quiet" type="button" data-open-link>Open</button>
    </div>
    <p class="rp-hint">Where the card goes when it is tapped — almost always a
      page on this site, written from the slash: <code>/scorchers/</code>,
      <code>/matches/</code>, <code>/players/CAF_MW_000123.html</code>.
      An outside link must start with <code>https://</code>. Open it first;
      a card pointing at a 404 is worse than no card. Leave it blank for a
      card that is only an announcement.</p>

    <label class="rp-label" for="tr-linklabel-${esc(c.card_id || "new")}">Button</label>
    <input class="rp-input" id="tr-linklabel-${esc(c.card_id || "new")}" name="link_label"
           maxlength="40" autocapitalize="sentences" autocomplete="off"
           value="${esc(c.link_label || "")}" placeholder="Read more">

    <h2 class="rp-field-head">Photo</h2>
    <input class="rp-input" type="file" name="photo"
           accept="image/jpeg,image/png,image/webp">
    <p class="rp-hint" data-upload-note>Landscape works best — the card crops to
      a wide box. Large photos are shrunk on the phone before sending, so this
      works on a slow connection. Choosing one replaces what is there when you
      save.</p>
    <input type="hidden" name="image_path" value="${esc(c.image_path || "")}">
    ${c.image_path ? `
      <button class="rp-btn is-quiet" type="button" data-drop-photo>Remove photo</button>` : ""}

    <label class="rp-label" for="tr-credit-${esc(c.card_id || "new")}">Photo credit</label>
    <input class="rp-input" id="tr-credit-${esc(c.card_id || "new")}" name="image_credit"
           maxlength="80" autocapitalize="words" autocomplete="off"
           value="${esc(c.image_credit || "")}" placeholder="FAM Media">
    <p class="rp-hint">Whose photo it is — a club, an association, a
      photographer. It renders small under the card as “Photo: …”, and only
      when the card has a photo. Worth filling in on anything that is not your
      own picture.</p>

    <label class="rp-label" for="tr-alt-${esc(c.card_id || "new")}">Photo description</label>
    <input class="rp-input" id="tr-alt-${esc(c.card_id || "new")}" name="image_alt"
           maxlength="140" autocapitalize="sentences" autocomplete="off"
           value="${esc(c.image_alt || "")}" placeholder="Optional">
    <p class="rp-hint">Read aloud to somebody who cannot see the photo — so it
      describes the picture and carries no byline, which is what the credit
      above is for. Leave it blank when the headline already says the same
      thing.</p>`;
}

// The move arrows sit OUTSIDE the <details> so a card can be reordered
// without opening it — a phone reading "3 of 3" and wanting it first
// shouldn't have to expand every card between here and there. (Buttons
// can't nest inside <summary> either, so outside is also where they have
// to live.)
function trendingMoveControl(cardId, index, total) {
  return `
    <div class="rp-trend-move" role="group" aria-label="Reorder on the homepage">
      <button class="rp-trend-arrow" type="button" data-move="up"
              data-card-id="${esc(cardId)}" aria-label="Move earlier"
              ${index === 0 ? "disabled" : ""}>&uarr;</button>
      <button class="rp-trend-arrow" type="button" data-move="down"
              data-card-id="${esc(cardId)}" aria-label="Move later"
              ${index === total - 1 ? "disabled" : ""}>&darr;</button>
    </div>`;
}

function trendingCard(card, index, total, labels) {
  const live = card.status === "live";
  const details = `
    <details class="rp-sec" data-trend-card="${esc(card.card_id)}">
      <summary>${esc(card.headline)}
        <span class="rp-sec-count">${esc(TRENDING_STATUS_WORD[card.status])}${
          live ? ` · ${index + 1} of ${total}` : ""}</span></summary>
      <div class="rp-sec-body">
        ${trendingPreview(card)}

        ${live ? `<p class="rp-hint">The first card is the one most people
          see — the carousel starts there and most readers never swipe. Use
          the &uarr;&darr; beside the card to reorder it.</p>` : ""}

        <form class="rp-form" data-trend-form autocomplete="off">
          ${trendingFields(card, labels)}
          <button class="rp-btn" type="submit">Save changes</button>
        </form>

        <h2 class="rp-field-head">Where it is</h2>
        <div class="rp-chip-row">
          ${TRENDING_TABS.map(([key, label]) =>
            `<button class="rp-chip${card.status === key ? " is-on" : ""}"
                     type="button" data-status="${esc(key)}"
                     aria-pressed="${card.status === key}">${esc(label)}</button>`
          ).join("")}
        </div>
        <p class="rp-hint">${live
          ? "On the site now. Archiving takes it down and keeps it here."
          : "Not on the site. Tap “On the site” to publish it."}${
          card.published_at
            ? ` First published ${esc(formatDate(card.published_at.slice(0, 10)))}.`
            : ""}</p>

        <h2 class="rp-field-head">Reuse</h2>
        <div class="rp-btn-row">
          <button class="rp-btn is-ghost" type="button" data-duplicate>Duplicate</button>
          <button class="rp-btn is-quiet" type="button" data-delete>Delete</button>
        </div>
        <p class="rp-hint">A duplicate arrives as a draft with the same photo
          and link — the quick way to turn last month's preview into this
          month's. Deleting is for a card typed by mistake; to take one off the
          site and keep it, archive it instead.</p>
      </div>
    </details>`;

  if (!live) return details;
  return `
    <div class="rp-trend-item">
      ${trendingMoveControl(card.card_id, index, total)}
      ${details}
    </div>`;
}

async function renderTrending(params) {
  if (!context.isAdmin) {
    h(`<a class="rp-btn is-quiet" href="#/">&larr; My matches</a>
       <p class="rp-empty">Only an administrator can change the homepage.</p>`);
    return;
  }

  const tab = TRENDING_TABS.some(([k]) => k === params.get("tab"))
    ? params.get("tab") : "live";

  h(`<div class="rp-loading"><span class="rp-spinner" aria-hidden="true"></span>
       <p>Loading the homepage…</p></div>`);

  let cards;
  try {
    cards = await loadTrending();
  } catch (error) {
    h(`<a class="rp-btn is-quiet" href="#/">&larr; My matches</a>
       <p class="rp-empty">Could not load the homepage cards.</p>`);
    flash(humanError(error), "error");
    return;
  }

  const live = cards.filter((c) => c.status === "live");
  // The archive reads newest-first: it is a history, and the card somebody
  // wants to duplicate is nearly always the last one down.
  const shown = tab === "live" ? live
    : cards.filter((c) => c.status === tab)
        .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));

  const counts = {};
  TRENDING_TABS.forEach(([key]) => {
    counts[key] = cards.filter((c) => c.status === key).length;
  });

  // Built from EVERY card, not just the tab on screen: a label used on an
  // archived card a year ago is exactly the one worth offering again.
  const labels = trendingLabels(cards);

  const tabs = TRENDING_TABS.map(([key, label]) =>
    `<a class="ops-tab${key === tab ? " is-on" : ""}" href="#/trending?tab=${key}"
       >${esc(label)} <em>${counts[key]}</em></a>`).join("");

  const heavy = live.length > TRENDING_COMFORTABLE_LIVE
    ? `<p class="rp-hint rp-trend-warn">${live.length} cards are on the site.
       Every one is a photo the homepage has to load; ${TRENDING_COMFORTABLE_LIVE}
       or fewer keeps it quick on a phone. Archive the older ones.</p>`
    : "";

  const blank = {
    live: "Nothing is on the homepage. Until a card is published the site "
        + "shows the built-in Scorchers card, exactly as it always has.",
    draft: "No drafts. Write one below and it stays here until you publish it.",
    archived: "Nothing archived yet. Cards taken off the site land here.",
  }[tab];

  h(`
    <a class="rp-btn is-quiet" href="#/">&larr; My matches</a>
    <h1 class="rp-login-head">Homepage</h1>
    <p class="rp-login-sub">The cards at the top of everyleague.co. Write one,
      give it a photo and a link, publish it. They go live at the next build —
      a minute or two after you publish.</p>

    <nav class="ops-tabs">${tabs}</nav>
    ${heavy}

    <details class="rp-sec" data-trend-new>
      <summary>＋ Write a card</summary>
      <div class="rp-sec-body">
        <form class="rp-form" data-trend-form autocomplete="off">
          ${trendingFields(null, labels)}
          <button class="rp-btn" type="submit">Save as draft</button>
          <p class="rp-hint">It is saved as a draft — nothing reaches the
            homepage until you publish it.</p>
        </form>
      </div>
    </details>

    <div data-trend-list>
      ${shown.map((c, i) => trendingCard(c, i, shown.length, labels)).join("")
        || `<p class="rp-empty">${blank}</p>`}
    </div>
  `);

  wireTrending(tab);
}

/** Read one editor form into the argument list save_trending_card takes,
 *  uploading a chosen photo first. Throws only on a failed upload — every
 *  other failure is the RPC's to report. */
async function trendingArgs(form, cardId, note) {
  let imagePath = form.image_path.value;
  const file = form.photo.files?.[0];
  if (file) {
    if (note) note.textContent = "Shrinking…";
    const blob = await shrinkImage(file);
    if (note) note.textContent = `Sending ${Math.round(blob.size / 1024)} KB…`;
    imagePath = await uploadTrendingImage(file);
  }
  return {
    p_card_id: cardId || null,
    p_eyebrow: form.eyebrow.value.trim(),
    p_headline: form.headline.value.trim(),
    p_body: form.body.value.trim(),
    p_link_url: form.link_url.value.trim(),
    p_link_label: form.link_label.value.trim(),
    p_image_path: imagePath,
    p_image_alt: form.image_alt.value.trim(),
    p_image_credit: form.image_credit.value.trim(),
  };
}

/** Lock a button, run, unlock. The portal's rule everywhere: a form NEVER
 *  loses what was typed into it, whatever comes back. */
async function trendingAction(button, busyLabel, fn) {
  if (button.disabled) return false;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = busyLabel;
  try {
    await fn();
    return true;
  } catch (error) {
    flash(humanError(error), "error");
    button.disabled = false;
    button.textContent = original;
    return false;
  }
}

function wireTrending(tab) {
  const reload = () => renderTrending(new URLSearchParams(`tab=${tab}`));
  // A new card and a duplicate both land in drafts, so both go there to show
  // it. Assigning the hash it ALREADY holds fires no hashchange and would
  // leave the list looking as though nothing had been created — the same trap
  // renderLogin documents about its `next` redirect.
  const showDrafts = () => {
    if (tab === "draft") reload();
    else location.hash = "#/trending?tab=draft";
  };

  // The live counters under the headline and the body. A limit you only meet
  // by being refused is a limit you meet at the wrong moment.
  view.querySelectorAll("[data-trend-form]").forEach((form) => {
    const counters = [
      [form.headline, form.querySelector('[data-count="headline"]'), TRENDING_HEADLINE_MAX],
      [form.body, form.querySelector('[data-count="body"]'), TRENDING_BODY_MAX],
    ];
    counters.forEach(([field, out, max]) => {
      if (!field || !out) return;
      const paint = () => {
        const left = max - field.value.length;
        out.textContent = left > max / 4 ? "" : `${left} characters left`;
      };
      field.addEventListener("input", paint);
      paint();
    });

    // THE BOX IS THE TRUTH; THE CHIPS ARE A SHORTCUT TO IT. Every chip writes
    // into the field, and the lit chip is worked out FROM the field — so
    // typing a label by hand lights its chip, and a label that matches no chip
    // simply lights none. Nothing here can put a value on a card that is not
    // the one on screen in the box.
    const chipRow = form.querySelector("[data-eyebrow-chips]");
    const paintChips = () => {
      const value = form.eyebrow.value.trim();
      chipRow.querySelectorAll("[data-eyebrow]").forEach((chip) =>
        chip.classList.toggle("is-on",
          Boolean(value) && sameLabel(chip.dataset.eyebrow, value)));
    };
    chipRow.querySelectorAll("[data-eyebrow]").forEach((chip) => {
      chip.onclick = () => {
        // Tapping the lit chip takes the label off — one tap to add, one to
        // remove, the same gesture in both directions.
        const on = chip.classList.contains("is-on");
        form.eyebrow.value = on ? "" : chip.dataset.eyebrow;
        paintChips();
      };
    });
    // ＋ empties the box and puts the cursor in it. It is not a second way to
    // store a label — a new one becomes a chip by being SAVED on a card, which
    // is why there is nothing here to confirm.
    chipRow.querySelector("[data-eyebrow-new]").onclick = () => {
      form.eyebrow.value = "";
      paintChips();
      form.eyebrow.focus();
    };
    form.eyebrow.addEventListener("input", paintChips);

    // Check the link before publishing a card that points at it. A new tab,
    // never this one: the form is full of unsaved words.
    form.querySelector("[data-open-link]").onclick = () => {
      const href = form.link_url.value.trim();
      if (!href) { flash("There is no link to open.", "warn"); return; }
      window.open(href, "_blank", "noopener");
    };

    form.querySelector("[data-drop-photo]")?.addEventListener("click", (e) => {
      form.image_path.value = "";
      form.photo.value = "";
      // The credit goes with the photo it credits. The site already drops an
      // orphaned one, so leaving it would be invisible — right up until a NEW
      // photo went on this card and inherited somebody else's byline.
      form.image_credit.value = "";
      e.target.remove();
      const preview = form.closest(".rp-sec-body")?.querySelector("[data-trend-preview] img");
      if (preview) preview.replaceWith(
        Object.assign(document.createElement("div"),
          { className: "rp-trend-noimg", textContent: "No photo" }));
      flash("Photo and credit removed here — save the card to make it stick.", "warn");
    });
  });

  // A new card.
  const fresh = view.querySelector("[data-trend-new]");
  const newForm = fresh.querySelector("[data-trend-form]");
  newForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const note = newForm.querySelector("[data-upload-note]");
    const button = newForm.querySelector('button[type="submit"]');
    const ok = await trendingAction(button, "Saving…", async () => {
      const args = await trendingArgs(newForm, null, note);
      const { error } = await supabase.rpc("save_trending_card", args);
      if (error) throw error;
    });
    // NO requestRebuild: a draft changes no published page.
    if (ok) {
      flash("Saved as a draft.", "ok");
      showDrafts();
    }
  });

  // Reorder arrows — outside the per-card <details>, so wired on their own.
  view.querySelectorAll("[data-move]").forEach((button) => {
    button.onclick = async () => {
      const ok = await trendingAction(button, "…", async () => {
        const { error } = await supabase.rpc("move_trending_card", {
          p_card_id: button.dataset.cardId, p_direction: button.dataset.move,
        });
        if (error) throw error;
      });
      if (!ok) return;
      requestRebuild();
      reload();
    };
  });

  // Every existing card.
  view.querySelectorAll("[data-trend-card]").forEach((host) => {
    const cardId = host.dataset.trendCard;
    const wasLive = tab === "live";
    const form = host.querySelector("[data-trend-form]");

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const note = form.querySelector("[data-upload-note]");
      const button = form.querySelector('button[type="submit"]');
      const ok = await trendingAction(button, "Saving…", async () => {
        const args = await trendingArgs(form, cardId, note);
        const { error } = await supabase.rpc("save_trending_card", args);
        if (error) throw error;
      });
      if (!ok) return;
      // Only a card that is ON the site has changed a published page.
      if (wasLive) requestRebuild();
      flash(wasLive ? "Saved — the homepage updates at the next build."
                    : "Saved.", "ok");
      reload();
    });

    host.querySelectorAll("[data-status]").forEach((chip) => {
      chip.onclick = async () => {
        const next = chip.dataset.status;
        // The chip for where the card already IS does nothing. It has to stay
        // on screen — it is what says where the card is — but running the RPC
        // would nudge a rebuild of the whole site for a tap that changed
        // nothing.
        if (chip.classList.contains("is-on")) return;
        const ok = await trendingAction(chip, "…", async () => {
          const { error } = await supabase.rpc("set_trending_status", {
            p_card_id: cardId, p_status: next,
          });
          if (error) throw error;
        });
        if (!ok) return;
        // Either leaving the site or arriving on it changes the homepage.
        if (wasLive || next === "live") requestRebuild();
        flash(next === "live"
          ? "Published — it appears on the homepage at the next build."
          : `Moved to ${TRENDING_STATUS_WORD[next].toLowerCase()}.`, "ok");
        reload();
      };
    });

    host.querySelector("[data-duplicate]").onclick = async (event) => {
      const ok = await trendingAction(event.target, "Copying…", async () => {
        const { error } = await supabase.rpc("duplicate_trending_card", {
          p_card_id: cardId,
        });
        if (error) throw error;
      });
      // The copy is a draft, so nothing published moved.
      if (ok) {
        flash("Copied into drafts.", "ok");
        showDrafts();
      }
    };

    // Two taps, never a browser dialog — the same rule the merge picker
    // follows, and for the same reason: a confirm() blocks every other event
    // on a page being read on a phone with a bad connection.
    const del = host.querySelector("[data-delete]");
    let armed = false;
    del.onclick = async () => {
      if (!armed) {
        armed = true;
        del.textContent = "Tap again to delete for good";
        del.classList.add("is-danger");
        return;
      }
      const ok = await trendingAction(del, "Deleting…", async () => {
        const { error } = await supabase.rpc("delete_trending_card", {
          p_card_id: cardId,
        });
        if (error) throw error;
      });
      if (!ok) return;
      if (wasLive) requestRebuild();
      flash("Deleted.", "ok");
      reload();
    };
  });
}


// ── Reporters ────────────────────────────────────────────────────────────────
// THE SCREEN THAT MEANS A NEW REPORTER DOES NOT HAVE TO WAIT FOR A LAPTOP.
//
// Everything here was scripts/reporters.py, which needs the secret key and so
// needs a trusted machine. That is still true of the half that mints a login —
// see supabase/functions/manage-reporters — but it was never true of the rest.
// Giving somebody a league is a two-column insert, and it was gated behind the
// same door as creating credentials, so a reporter who turned up on a Saturday
// waited until somebody was sitting at a checkout of this repo.
//
// Two paths out of this screen, for the two different things it does:
//
//   * Creating a reporter goes to the Edge Function, because only the secret
//     key may make an auth.users row. It comes back with a password, shown
//     ONCE — there is no SMTP on this project and nothing will ever mail it.
//   * Everything else is an RPC (0026), gated on is_admin() in Postgres.
//
// Nothing here nudges a rebuild. No page on everyleague.co renders a reporter,
// so unlike a rename or a scoreline none of this changes the published site.

/** Everyone, with their assignments folded in. Admin-only by RLS: the
 *  `reporters` policy is `auth_user_id = auth.uid() or is_admin()`, so an
 *  ordinary reporter reading this table gets exactly themselves — which is why
 *  the screen is gated in the router as well, rather than trusting a list that
 *  would quietly come back with one row. */
async function loadReporters() {
  const [{ data: reporters, error }, { data: assignments }] = await Promise.all([
    supabase.from("reporters")
      .select("reporter_id,name,email,role,active,auth_user_id")
      .order("reporter_id", { ascending: true }),
    supabase.from("reporter_assignments").select("reporter_id,competition_id"),
  ]);
  if (error) throw error;
  const byReporter = {};
  (assignments || []).forEach((a) => {
    (byReporter[a.reporter_id] ||= []).push(a.competition_id);
  });
  return (reporters || []).map((r) => ({
    ...r, competitions: byReporter[r.reporter_id] || [],
  }));
}

// The same unambiguous alphabet the CLI and the Edge Function use: no O/0, no
// l/1/I. A temporary password gets read aloud over WhatsApp and typed on a
// phone keyboard, where an ambiguous character is a support call.
const PASSWORD_ALPHABET =
  "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";

function suggestPassword(length = 14) {
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  return Array.from(bytes,
    (b) => PASSWORD_ALPHABET[b % PASSWORD_ALPHABET.length]).join("");
}

/** The password panel. Deliberately loud, and deliberately not dismissed by
 *  the next render: this string exists nowhere else and cannot be looked up
 *  again — only reset, which invalidates the one already handed over. */
function credentialsPanel(email, password, heading) {
  return `
    <div class="rp-creds" data-creds>
      <h2 class="rp-field-head">${esc(heading)}</h2>
      <div class="rp-account-row"><span>Email</span><span>${esc(email)}</span></div>
      <div class="rp-account-row"><span>Password</span>
        <span><code class="rp-code">${esc(password)}</code></span></div>
      <button class="rp-btn is-ghost" type="button" data-copy
              data-value="${esc(password)}">Copy password</button>
      <p class="rp-hint">Shown once. Send it over a private channel — they can
        change it in Account. Nobody can read it back, only reset it.</p>
    </div>`;
}

function wireCopy(host) {
  host.querySelectorAll("[data-copy]").forEach((button) => {
    button.onclick = async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.value);
        button.textContent = "Copied";
        setTimeout(() => { button.textContent = "Copy password"; }, 2000);
      } catch {
        // Clipboard access is refused often enough on a phone browser that
        // failing silently would look like a dead button. The password is on
        // screen either way.
        flash("Could not copy — select the password and copy it by hand.",
              "warn");
      }
    };
  });
}

/** A chip per competition, lit when assigned. Buttons rather than checkboxes
 *  for the reason the rest of this app gives: a 38px chip is a far easier tap
 *  than a 16px box. */
function competitionChips(comps, selected, attr) {
  return `<div class="rp-chip-row">${comps.map((c) => `
    <button class="rp-chip${selected.includes(c.competition_id) ? " is-on" : ""}"
            type="button" ${attr}="${esc(c.competition_id)}"
            aria-pressed="${selected.includes(c.competition_id)}"
            >${esc(c.label)}</button>`).join("")}</div>`;
}

async function renderReporters() {
  if (!context.isAdmin) {
    h(`<a class="rp-btn is-quiet" href="#/">&larr; My matches</a>
       <p class="rp-empty">Only an administrator can manage reporters.</p>`);
    return;
  }

  h(`<div class="rp-loading"><span class="rp-spinner" aria-hidden="true"></span>
       <p>Loading reporters…</p></div>`);

  let reporters, comps;
  try {
    [reporters, comps] = await Promise.all([loadReporters(), entryCompetitions()]);
  } catch (error) {
    h(`<a class="rp-btn is-quiet" href="#/">&larr; My matches</a>
       <p class="rp-empty">Could not load the reporters.</p>`);
    flash(humanError(error), "error");
    return;
  }

  const self = context.reporter?.reporter_id;

  h(`
    <a class="rp-btn is-quiet" href="#/">&larr; My matches</a>
    <h1 class="rp-login-head">Reporters</h1>
    <p class="rp-login-sub">Create an account, give it leagues, and hand over
      the password. A reporter can only see and report the competitions ticked
      on their card.</p>

    <details class="rp-sec" data-new-reporter>
      <summary>＋ Add a reporter</summary>
      <div class="rp-sec-body">
        <form class="rp-form" data-create autocomplete="off">
          <label class="rp-label" for="rp-name">Name</label>
          <input class="rp-input" id="rp-name" name="name" required
                 autocapitalize="words" placeholder="James Banda">

          <label class="rp-label" for="rp-email">Email</label>
          <input class="rp-input" id="rp-email" name="email" type="email" required
                 autocapitalize="off" autocorrect="off" spellcheck="false"
                 placeholder="james@example.com">
          <p class="rp-hint">This is what they sign in with. It cannot be
            changed from here afterwards.</p>

          <label class="rp-label" for="rp-password">Password</label>
          <div class="rp-btn-row">
            <input class="rp-input" id="rp-password" name="password" required
                   minlength="8" autocapitalize="off" autocorrect="off"
                   spellcheck="false" value="${esc(suggestPassword())}">
            <button class="rp-btn is-ghost" type="button" data-regen>New</button>
          </div>
          <p class="rp-hint">Generated for you, and safe to send. Shown again
            once when the account is made, then never.</p>

          <label class="rp-label" for="rp-role">Role</label>
          <select class="rp-select" id="rp-role" name="role">
            <option value="reporter" selected>Reporter</option>
            <option value="admin">Administrator</option>
          </select>
          <p class="rp-hint">An administrator reports every competition without
            being assigned one, and can manage this screen.</p>

          <label class="rp-label">Leagues</label>
          ${comps.length
            ? competitionChips(comps, [], "data-new-comp")
            : '<p class="rp-hint">No competitions exist yet.</p>'}
          <p class="rp-hint">Tap to give access. An administrator does not need
            any of these.</p>

          <button class="rp-btn" type="submit" data-submit>Create reporter</button>
        </form>
      </div>
    </details>

    <div data-created></div>

    <h2 class="rp-group-head">Everyone
      <span class="rp-count">${reporters.length}</span></h2>
    <div data-reporter-list>
      ${reporters.map((r) => reporterCard(r, comps, self)).join("")}
    </div>
  `);

  wireCreateForm(comps);
  wireReporterCards(comps);
}

function reporterCard(r, comps, self) {
  const flags = [];
  if (r.role === "admin") flags.push("Administrator");
  if (!r.active) flags.push("Inactive");
  if (!r.auth_user_id) flags.push("No login");
  const isSelf = r.reporter_id === self;

  const covers = r.role === "admin"
    ? '<p class="rp-hint">An administrator reports every competition. These are recorded but not needed.</p>'
    : (r.competitions.length
        ? ""
        : '<p class="rp-hint">No leagues yet — this account can see nothing.</p>');

  return `
    <details class="rp-sec" data-reporter="${esc(r.reporter_id)}"
             data-email="${esc(r.email || "")}">
      <summary>${esc(r.name)}${flags.length
        ? `<span class="rp-sec-count">${esc(flags.join(" · "))}</span>` : ""}</summary>
      <div class="rp-sec-body">
        <div class="rp-account-row"><span>Email</span><span>${esc(r.email || "—")}</span></div>
        <div class="rp-account-row"><span>Id</span><span>${esc(r.reporter_id)}</span></div>

        <h2 class="rp-field-head">Leagues</h2>
        ${covers}
        ${comps.length
          ? competitionChips(comps, r.competitions, "data-comp")
          : '<p class="rp-hint">No competitions exist yet.</p>'}

        <h2 class="rp-field-head">Role</h2>
        ${isSelf
          ? `<p class="rp-hint">This is you. Your own role and account cannot be
               changed here — ask another administrator, or use the CLI.</p>`
          : `<div class="rp-chip-row">
               <button class="rp-chip${r.role === "reporter" ? " is-on" : ""}"
                       type="button" data-role-set="reporter">Reporter</button>
               <button class="rp-chip${r.role === "admin" ? " is-on" : ""}"
                       type="button" data-role-set="admin">Administrator</button>
             </div>`}

        <h2 class="rp-field-head">Access</h2>
        <div class="rp-btn-row">
          ${r.auth_user_id
            ? '<button class="rp-btn is-ghost" type="button" data-reset>Reset password</button>'
            : '<span class="rp-hint">No login to reset.</span>'}
          ${isSelf ? "" : `<button class="rp-btn is-quiet" type="button"
              data-active-set="${r.active ? "false" : "true"}"
              >${r.active ? "Deactivate" : "Reactivate"}</button>`}
        </div>
        ${isSelf ? "" : `<p class="rp-hint">Deactivating keeps their history and
          their leagues — it is reversible, and is not the same as forgetting
          what somebody covered.</p>`}
        <div data-card-creds></div>
      </div>
    </details>`;
}

function wireCreateForm(comps) {
  const details = view.querySelector("[data-new-reporter]");
  const form = details.querySelector("[data-create]");
  const chosen = [];

  // FIELDS ARE LOOKED UP BY SELECTOR, NOT AS form.name.
  //
  // Two of these four names collide with properties a form already has:
  // HTMLFormElement.prototype.name, and Element.prototype.role (ARIA
  // reflection). `form.name` and `form.role` DO still return the controls —
  // HTMLFormElement carries [LegacyOverrideBuiltIns], so named controls beat
  // the built-ins, and this was checked in a browser rather than assumed.
  // It is spelled out anyway because that is a lot of spec to have to know
  // before trusting three characters, and because the failure it would cause
  // is silent: `form.name.value` on a form whose `name` resolved to a string
  // is `undefined`, which submits an empty name rather than throwing.
  // Everywhere else in this app the field names happen not to collide
  // (`full_name`, `known_as`, `players`); here they do.
  const field = (name) => form.querySelector(`[name="${name}"]`);

  form.querySelector("[data-regen]").onclick = () => {
    field("password").value = suggestPassword();
  };

  form.querySelectorAll("[data-new-comp]").forEach((chip) => {
    chip.onclick = () => {
      const id = chip.dataset.newComp;
      const at = chosen.indexOf(id);
      if (at === -1) chosen.push(id); else chosen.splice(at, 1);
      chip.classList.toggle("is-on", at === -1);
      chip.setAttribute("aria-pressed", String(at === -1));
    };
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("[data-submit]");
    if (button.disabled) return;
    button.disabled = true;
    button.textContent = "Creating…";

    const body = {
      action: "create",
      name: field("name").value.trim(),
      email: field("email").value.trim(),
      password: field("password").value,
      role: field("role").value,
      competitions: chosen,
    };

    let data, error;
    try {
      ({ data, error } = await supabase.functions.invoke("manage-reporters", { body }));
    } catch (err) {
      error = err;
    }
    button.disabled = false;
    button.textContent = "Create reporter";

    // NOTHING TYPED IS LOST ON FAILURE. The form keeps every field, including
    // the password, so a rejected email address is one correction away from a
    // retry rather than a re-type.
    if (error || !data?.reporter_id) {
      flash(await functionError(error, data), "error");
      return;
    }

    // The password panel goes OUTSIDE the form, above the list, because the
    // form is about to be cleared and reused and this is the one thing on the
    // screen that cannot be recovered if it scrolls away unread.
    const host = view.querySelector("[data-created]");
    host.innerHTML = credentialsPanel(data.email, data.password,
      `${body.name} can now sign in`);
    wireCopy(host);
    host.scrollIntoView({ block: "center" });

    form.reset();
    field("password").value = suggestPassword();
    chosen.length = 0;
    form.querySelectorAll("[data-new-comp]").forEach((chip) => {
      chip.classList.remove("is-on");
      chip.setAttribute("aria-pressed", "false");
    });
    details.open = false;
    flash(`${data.reporter_id} created.`, "ok");

    // The list is now wrong by one row. Redrawn rather than patched, so it
    // matches the database exactly. It replaces ONLY [data-reporter-list],
    // which is what keeps the password panel above it on screen — redrawing
    // the whole view here would wipe the one string that cannot be fetched
    // again.
    await refreshReporterList(comps);
  });
}

/** Redraw just the list, keeping the create form and its password panel.
 *
 *  A role change rewrites the card that describes it — the summary flags, and
 *  which of the two role chips is lit — so the row has to come back from the
 *  database rather than be patched in place. Two things are carried across the
 *  redraw, because losing either of them is worse than the staleness it fixes:
 *
 *    * WHICH CARDS WERE OPEN. Without this, promoting somebody collapses the
 *      card you are standing in and you have to find them again.
 *    * ANY PASSWORD ON SCREEN. It exists nowhere else and cannot be fetched
 *      again, only reset — so a reset followed by a role change would destroy
 *      the credential that had just been issued.
 */
async function refreshReporterList(comps) {
  let reporters;
  try {
    reporters = await loadReporters();
  } catch (error) {
    flash(humanError(error), "error");
    return;
  }
  const host = view.querySelector("[data-reporter-list]");
  if (!host) return;

  const open = new Set();
  const creds = new Map();
  host.querySelectorAll("[data-reporter]").forEach((card) => {
    if (card.open) open.add(card.dataset.reporter);
    const panel = card.querySelector("[data-card-creds]");
    if (panel?.innerHTML.trim()) creds.set(card.dataset.reporter, panel.innerHTML);
  });

  const self = context.reporter?.reporter_id;
  host.innerHTML = reporters.map((r) => reporterCard(r, comps, self)).join("");
  const count = view.querySelector(".rp-group-head .rp-count");
  if (count) count.textContent = String(reporters.length);

  host.querySelectorAll("[data-reporter]").forEach((card) => {
    if (open.has(card.dataset.reporter)) card.open = true;
    const kept = creds.get(card.dataset.reporter);
    if (kept) {
      const panel = card.querySelector("[data-card-creds]");
      panel.innerHTML = kept;
      wireCopy(panel);
    }
  });

  wireReporterCards(comps);
}

function wireReporterCards(comps) {
  view.querySelectorAll("[data-reporter]").forEach((card) => {
    const reporterId = card.dataset.reporter;

    // Assignments save on the tap, one competition at a time. There is no Save
    // button because there is nothing to batch: each chip is its own row, and
    // a reporter half-way through being given three leagues is a valid state.
    card.querySelectorAll("[data-comp]").forEach((chip) => {
      chip.onclick = async () => {
        if (chip.disabled) return;
        const on = chip.classList.contains("is-on");
        chip.disabled = true;
        const { error } = await supabase.rpc(
          on ? "admin_unassign_competition" : "admin_assign_competition",
          { p_reporter: reporterId, p_competition: chip.dataset.comp });
        chip.disabled = false;
        if (error) { flash(humanError(error), "error"); return; }
        // Painted only after the database agrees, so a chip never shows an
        // access level that was refused.
        chip.classList.toggle("is-on", !on);
        chip.setAttribute("aria-pressed", String(!on));
      };
    });

    card.querySelectorAll("[data-role-set]").forEach((chip) => {
      chip.onclick = async () => {
        const role = chip.dataset.roleSet;
        if (chip.classList.contains("is-on")) return;
        chip.disabled = true;
        const { error } = await supabase.rpc("admin_set_reporter_role",
          { p_reporter: reporterId, p_role: role });
        chip.disabled = false;
        if (error) { flash(humanError(error), "error"); return; }
        flash(role === "admin"
          ? "Promoted to administrator."
          : "Now an ordinary reporter.", "ok");
        refreshReporterList(comps);
      };
    });

    const activeBtn = card.querySelector("[data-active-set]");
    if (activeBtn) {
      activeBtn.onclick = async () => {
        const next = activeBtn.dataset.activeSet === "true";
        activeBtn.disabled = true;
        const { error } = await supabase.rpc("admin_set_reporter_active",
          { p_reporter: reporterId, p_active: next });
        activeBtn.disabled = false;
        if (error) { flash(humanError(error), "error"); return; }
        flash(next ? "Account reactivated." : "Account deactivated.", "ok");
        refreshReporterList(comps);
      };
    }

    const reset = card.querySelector("[data-reset]");
    if (reset) {
      reset.onclick = async () => {
        reset.disabled = true;
        reset.textContent = "Resetting…";
        let data, error;
        try {
          ({ data, error } = await supabase.functions.invoke("manage-reporters", {
            body: { action: "reset_password", reporter_id: reporterId },
          }));
        } catch (err) {
          error = err;
        }
        reset.disabled = false;
        reset.textContent = "Reset password";
        if (error || !data?.password) {
          flash(await functionError(error, data), "error");
          return;
        }
        // Inside the card, not at the top of the screen: the admin is looking
        // at one person, and a password panel anywhere else would leave them
        // checking which reporter it belonged to.
        const host = card.querySelector("[data-card-creds]");
        host.innerHTML = credentialsPanel(
          card.dataset.email, data.password, "New password");
        wireCopy(host);
        flash("Password reset. The old one no longer works.", "warn");
      };
    }
  });
}

/** An Edge Function failure, said in a sentence.
 *
 *  supabase-js reports a non-2xx as a FunctionsHttpError whose useful half —
 *  the `error` this function put in the body — is inside `context`, an
 *  unread Response. Without this, every refusal from manage-reporters reached
 *  the admin as "Edge Function returned a non-2xx status code", which names
 *  neither the problem nor what to do about it. The messages on the other side
 *  are written for a person; this is what lets them arrive.
 */
async function functionError(error, data) {
  const body = data?.error;
  if (body) return String(body);
  try {
    const response = error?.context;
    if (response && typeof response.json === "function") {
      const parsed = await response.json();
      if (parsed?.error) return String(parsed.error);
    }
  } catch { /* fall through to the generic sentence */ }
  return humanError(error);
}

// ── National teams ───────────────────────────────────────────────────────────
// A parallel world to everything above, and deliberately not folded into it.
// The nt_* tables record ONE team's perspective — `opponent` is a display name
// with no row to resolve, so there is no fixture between two teams here, only
// "Malawi played somebody". Trying to reuse the club screens would have meant
// pretending an opponent is a team; keeping them apart costs some repetition
// and keeps both honest.
//
// The other structural difference: a booking and a substitution are not
// records of their own. A card is a flag on the player's line-up row and a
// change is a role=sub_on row naming who it replaced, so the team sheet IS the
// stats screen and saves in one piece (see save_nt_lineup).

const NT_STATUSES = [
  { value: "played", label: "Full time", short: "FT", scored: true },
  { value: "scheduled", label: "Not played yet", short: "", scored: false },
  { value: "awarded", label: "Awarded", short: "AWD", scored: true },
];
const ntStatusMeta = (value) =>
  NT_STATUSES.find((s) => s.value === value) || NT_STATUSES[1];

const NT_ROLES = [
  ["starting", "Starting XI"],
  ["sub_on", "Came on"],
  ["unused_sub", "Unused sub"],
];
const NT_POSITIONS = ["", "GK", "DF", "MF", "FW"];
const NT_STAGES = [
  ["r64", "Round of 64"], ["r32", "Round of 32"], ["r16", "Round of 16"],
  ["qf", "Quarter-final"], ["sf", "Semi-final"], ["final", "Final"],
  ["3p", "Third-place play-off"],
];
const ntStageLabel = (v) => (NT_STAGES.find((s) => s[0] === v) || [v, v])[1];

/** The national teams this account may report. An admin gets all of them;
 *  everyone else gets exactly their nt_assignments. */
function ntTeams() {
  return once("ntTeams", async () => {
    const [{ data: teams, error }, { data: mine }] = await Promise.all([
      supabase.from("nt_teams").select("team_code,team_name,category"),
      supabase.from("nt_assignments").select("team_code"),
    ]);
    if (error) throw error;
    const allowed = new Set((mine || []).map((a) => a.team_code));
    return (teams || []).filter(
      (t) => context.isAdmin || allowed.has(t.team_code));
  });
}

const NT_MATCH_FIELDS =
  "match_id,team_code,date,kickoff,competition,opponent,home_away,neutral,"
  + "venue,city,country,team_score,opponent_score,status,coach,extra_time,"
  + "penalty_shootout,extra_time_result,source_ref";

/** A Malawi-perspective scoreline, written the way the page reads it. */
function ntScoreline(m) {
  if (m.team_score == null) return "";
  return m.home_away === "A" ? `${m.opponent_score}–${m.team_score}`
                             : `${m.team_score}–${m.opponent_score}`;
}

function ntCard(m, teamName) {
  const meta = [formatDate(m.date), formatKickoff(m.kickoff), m.venue]
    .filter(Boolean).join(" · ");
  const info = ntStatusMeta(m.status);
  const scored = m.team_score != null;
  const badge = scored
    ? `<span class="rp-badge is-done">${esc(info.short || "Result")}</span>`
    : (m.date && m.date < catToday()
        ? '<span class="rp-badge is-late">Needs result</span>' : "");
  return `
    <article class="rp-card">
      <div class="rp-card-comp">${esc(m.competition || "Friendly")}</div>
      <div class="rp-teams">
        <span class="rp-team">${esc(teamName)}</span>
        <span class="rp-score">${scored ? esc(m.team_score) : ""}</span>
        <span class="rp-team">${esc(m.opponent)}</span>
        <span class="rp-score">${scored ? esc(m.opponent_score) : ""}</span>
      </div>
      <p class="rp-card-meta">${esc(meta)} ${badge}</p>
      <a class="rp-btn${scored ? " is-ghost" : ""}" href="#/nt/m/${esc(m.match_id)}">
        ${scored ? "Edit result" : "Report match"}
      </a>
    </article>`;
}

async function renderNTHome(params) {
  h('<div class="rp-loading"><span class="rp-spinner"></span><p>Loading national teams…</p></div>');

  let teams;
  try {
    teams = await ntTeams();
  } catch (error) {
    h('<p class="rp-empty">Could not load the national teams.</p>'
      + '<a class="rp-btn is-ghost" href="#/">Back to my matches</a>');
    flash(humanError(error), "error");
    return;
  }
  if (!teams.length) {
    h(`<a class="rp-btn is-quiet" href="#/">&larr; My matches</a>
       <p class="rp-empty">You are not assigned to a national team. An
         administrator can grant one.</p>`);
    return;
  }

  const wanted = params?.get("team") || "";
  const team = teams.find((t) => t.team_code === wanted) || teams[0];

  const [{ data: matches, error }, { data: comps }] = await Promise.all([
    supabase.from("nt_matches").select(NT_MATCH_FIELDS)
      .eq("team_code", team.team_code).order("date", { ascending: false }),
    supabase.from("nt_competitions").select("competition_name"),
  ]);
  if (error) {
    h('<p class="rp-empty">Could not load fixtures.</p>');
    flash(humanError(error), "error");
    return;
  }

  const today = catToday();
  const rows = matches || [];
  // "tbd" is a legal date meaning the fixture is agreed with no day yet, so it
  // sorts as upcoming rather than as something overdue.
  const undated = rows.filter((m) => !/^\d{4}-\d{2}-\d{2}$/.test(m.date || ""));
  const dated = rows.filter((m) => /^\d{4}-\d{2}-\d{2}$/.test(m.date || ""));
  const awaiting = dated.filter((m) => m.team_score == null && m.date <= today);
  const upcoming = dated.filter((m) => m.team_score == null && m.date > today);
  const done = dated.filter((m) => m.team_score != null);

  const list = (title, set) => set.length
    ? `<h2 class="rp-group-head">${esc(title)}
         <span class="rp-count">${set.length}</span></h2>`
      + set.map((m) => ntCard(m, team.team_name)).join("")
    : "";

  const teamPicker = teams.length > 1 ? `
    <label class="rp-filter rp-filter-wide">
      <span class="rp-filter-label">Team</span>
      <select class="rp-select" data-nt-team>
        ${teams.map((t) => `<option value="${esc(t.team_code)}"${
          t.team_code === team.team_code ? " selected" : ""}>
          ${esc(t.team_name)}</option>`).join("")}
      </select>
    </label>` : "";

  const names = [...new Set((comps || []).map((c) => c.competition_name))].sort();

  h(`<a class="rp-btn is-quiet" href="#/" style="margin-top:0">&larr; My matches</a>
     <h1 class="rp-greeting">${esc(team.team_name)}</h1>
     <p class="rp-sub">National team${team.category === "youth" ? " · youth" : ""}</p>
     <div class="rp-actions">
       <a class="rp-btn is-ghost" href="#/nt/add?team=${esc(team.team_code)}">＋ Add fixture</a>
       <a class="rp-btn is-ghost" href="#/nt/comps">Competitions</a>
       <button class="rp-btn is-quiet" type="button" data-refresh>Refresh</button>
     </div>
     ${teamPicker ? `<div class="rp-filters">${teamPicker}</div>` : ""}
     ${list("Awaiting result", awaiting)}
     ${list("Upcoming", upcoming)}
     ${list("Date to be confirmed", undated)}
     ${list("Results", done.slice(0, 20))}
     ${rows.length ? "" : '<p class="rp-empty">No fixtures for this team yet.</p>'}
     ${names.length ? `<p class="rp-hint">Competitions: ${names.map(esc).join(" · ")}</p>` : ""}`);

  const picker = view.querySelector("[data-nt-team]");
  if (picker) {
    picker.onchange = () => {
      location.hash = `#/nt?team=${encodeURIComponent(picker.value)}`;
    };
  }
  view.querySelector("[data-refresh]").onclick = () => {
    invalidateReference();
    renderNTHome(params);
  };
}

// ── Adding an international fixture ──────────────────────────────────────────
// Opponent and competition are FREE TEXT, unlike everywhere else in this app.
// That is the schema's shape, not an oversight: there is no table of national
// teams other than ours, and inventing one would mean maintaining a register
// of every country to record a friendly against Zambia.

async function renderNTAddFixture(params) {
  const teams = await ntTeams().catch(() => []);
  if (!teams.length) { location.hash = "#/nt"; return; }
  const wanted = params?.get("team") || "";
  const team = teams.find((t) => t.team_code === wanted) || teams[0];
  const { data: comps } = await supabase.from("nt_competitions")
    .select("competition_name");
  const names = [...new Set((comps || []).map((c) => c.competition_name))].sort();

  h(`<a class="rp-btn is-quiet" href="#/nt?team=${esc(team.team_code)}">&larr; ${esc(team.team_name)}</a>
     <h1 class="rp-login-head">Add an international</h1>
     <p class="rp-login-sub">${esc(team.team_name)}</p>
     <form class="rp-form" data-nt-fixture>
       <label class="rp-label" for="nt-comp">Competition</label>
       <input class="rp-input" id="nt-comp" name="competition" required
              list="nt-comp-list" placeholder="WAFCON, AFCON Qualification, Friendly"
              autocapitalize="words">
       <datalist id="nt-comp-list">
         ${names.map((n) => `<option value="${esc(n)}"></option>`).join("")}
       </datalist>
       <p class="rp-hint">Type a new name or pick one already in use.</p>

       <label class="rp-label" for="nt-opponent">Opponent</label>
       <input class="rp-input" id="nt-opponent" name="opponent" required
              placeholder="Cameroon" autocapitalize="words">

       <label class="rp-label" for="nt-date">Date</label>
       <input class="rp-input" id="nt-date" name="date" type="date">
       <p class="rp-hint">Leave blank if the day is not fixed yet.</p>

       <label class="rp-label" for="nt-kickoff">Kick-off</label>
       <input class="rp-input" id="nt-kickoff" name="kickoff" type="time">
       <p class="rp-hint">Malawi time.</p>

       <label class="rp-label">Home or away</label>
       <div class="rp-side-pick" role="radiogroup">
         <label><input type="radio" name="home_away" value="H" checked><span>Home</span></label>
         <label><input type="radio" name="home_away" value="A"><span>Away</span></label>
       </div>
       <label class="rp-inline" style="margin-top:8px">
         <input type="checkbox" name="neutral"><span>Neutral venue</span></label>

       <label class="rp-label" for="nt-venue">Venue</label>
       <input class="rp-input" id="nt-venue" name="venue" autocapitalize="words">
       <div class="rp-row">
         <input class="rp-input" name="city" placeholder="City" autocapitalize="words">
         <input class="rp-input" name="country" placeholder="Country" autocapitalize="words">
       </div>

       <button class="rp-btn" type="submit" data-submit>Add fixture</button>
     </form>`);

  const form = view.querySelector("[data-nt-fixture]");
  const button = form.querySelector("[data-submit]");
  let busy = false;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;                            // rule 2
    clearFlash();
    busy = true; button.disabled = true; button.textContent = "Adding…";
    const { data, error } = await supabase.rpc("create_nt_match", {
      p_team_code: team.team_code,
      p_competition: form.competition.value,
      p_opponent: form.opponent.value,
      p_date: form.date.value || "",
      p_kickoff: form.kickoff.value || "",
      p_home_away: form.home_away.value,
      p_neutral: form.neutral.checked,
      p_venue: form.venue.value,
      p_city: form.city.value,
      p_country: form.country.value,
    });
    busy = false; button.disabled = false; button.textContent = "Add fixture";
    if (error) { flash(humanError(error), "error"); return; }   // rule 1
    flash("Fixture added.", "ok");
    location.hash = `#/nt/m/${(data || [])[0]?.match_id}`;
  });
}

// ── One international ────────────────────────────────────────────────────────

async function renderNTMatch(matchId) {
  h('<div class="rp-loading"><span class="rp-spinner"></span><p>Loading match…</p></div>');

  const [{ data: matches, error }, teams] = await Promise.all([
    supabase.from("nt_matches").select(NT_MATCH_FIELDS)
      .eq("match_id", matchId).limit(1),
    ntTeams().catch(() => []),
  ]);
  if (error) {
    h('<p class="rp-empty">Could not load this match.</p>');
    flash(humanError(error), "error");
    return;
  }
  const match = (matches || [])[0];
  if (!match) {
    h(`<p class="rp-empty">That match could not be found.</p>
       <a class="rp-btn is-ghost" href="#/nt">Back to national teams</a>`);
    return;
  }
  const team = teams.find((t) => t.team_code === match.team_code);
  if (!team) {
    h(`<p class="rp-empty">You are not assigned to that national team.</p>
       <a class="rp-btn is-ghost" href="#/nt">Back to national teams</a>`);
    return;
  }

  const state = {
    ours: match.team_score ?? 0,
    theirs: match.opponent_score ?? 0,
    status: match.team_score != null ? match.status : "played",
    extraTime: Boolean(match.extra_time),
    pens: match.penalty_shootout || "",
    source: match.source_ref || "",
    published: match.team_score != null,
    busy: false,
    open: {},
    teamName: team.team_name,
  };
  drawNTMatch(match, state);
}

function drawNTMatch(match, state) {
  captureDetailState(state);
  const meta = [formatDate(match.date), formatKickoff(match.kickoff),
                match.venue, match.city].filter(Boolean).join(" · ");
  const statusOptions = NT_STATUSES.map((s) => `
    <label class="rp-status-opt">
      <input type="radio" name="nt-status" value="${s.value}"
             ${s.value === state.status ? "checked" : ""}>
      <span>${esc(s.label)}</span>
    </label>`).join("");

  const done = state.published ? `
    <div class="rp-done">
      <div class="rp-done-tick" aria-hidden="true">&#10003;</div>
      <p class="rp-done-head">Published</p>
      <p class="rp-done-score">${esc(state.teamName)} ${esc(match.team_score)}&ndash;${esc(match.opponent_score)} ${esc(match.opponent)}</p>
      <p class="rp-done-status">Live on everyleague.co within a few minutes.</p>
    </div>` : "";

  h(`
    <a class="rp-btn is-quiet" href="#/nt?team=${esc(match.team_code)}" style="margin-top:0">&larr; ${esc(state.teamName)}</a>
    ${done}
    <p class="rp-report-comp">${esc(match.competition || "Friendly")}</p>
    <p class="rp-report-meta">${esc(meta)}</p>

    <section class="rp-side">
      <h2 class="rp-side-name">${esc(state.teamName)}</h2>
      <div class="rp-stepper">
        <button class="rp-step" type="button" data-nt-step="ours:-1" aria-label="One fewer">&minus;</button>
        <span class="rp-step-value" data-nt-value="ours" aria-live="polite">${state.ours}</span>
        <button class="rp-step" type="button" data-nt-step="ours:1" aria-label="One more">+</button>
      </div>
    </section>

    <section class="rp-side">
      <h2 class="rp-side-name">${esc(match.opponent)}</h2>
      <div class="rp-stepper">
        <button class="rp-step" type="button" data-nt-step="theirs:-1" aria-label="One fewer">&minus;</button>
        <span class="rp-step-value" data-nt-value="theirs" aria-live="polite">${state.theirs}</span>
        <button class="rp-step" type="button" data-nt-step="theirs:1" aria-label="One more">+</button>
      </div>
    </section>

    <h2 class="rp-field-head">Match status</h2>
    <div class="rp-status" data-nt-status>${statusOptions}</div>

    <label class="rp-inline" style="margin-top:12px">
      <input type="checkbox" data-nt-et ${state.extraTime ? "checked" : ""}>
      <span>Went to extra time</span></label>
    <label class="rp-label" for="nt-pens">Penalty shoot-out</label>
    <input class="rp-input" id="nt-pens" data-nt-pens value="${esc(state.pens)}"
           placeholder="e.g. 4-3" autocomplete="off">
    <p class="rp-hint">Leave blank unless it went to penalties.</p>

    <h2 class="rp-field-head">Source (where is this information from?)</h2>
    <input class="rp-input" type="text" data-nt-source maxlength="500"
           value="${esc(state.source)}"
           placeholder="Facebook link, or how you know"
           autocapitalize="sentences" autocorrect="off" spellcheck="false">
    <p class="rp-hint">Never shown publicly — it is there so a result can be
      checked later. A link, or plain words like &ldquo;told to me by the
      federation&rdquo;.</p>

    <div class="rp-publish">
      <button class="rp-btn" type="button" data-nt-publish></button>
      <p class="rp-publish-note" data-nt-note></p>
    </div>

    ${section("nt-reschedule", "Change date or ground", 0, `
      <p class="rp-hint" style="margin-top:0">The fixture was agreed one way
        and it happened another? Change it here. This saves on its own —
        it is not part of publishing the score.</p>
      <label class="rp-label" for="nt-rs-date">Date</label>
      <input class="rp-input" id="nt-rs-date" type="date" data-nt-rs-date
             value="${esc(/^\d{4}-\d{2}-\d{2}$/.test(match.date || "") ? match.date : "")}">
      <p class="rp-hint">Clear it if the match no longer has a fixed day.</p>
      <label class="rp-label" for="nt-rs-kickoff">Kick-off</label>
      <input class="rp-input" id="nt-rs-kickoff" type="time" data-nt-rs-kickoff
             value="${esc((match.kickoff || "").slice(0, 5))}">
      <p class="rp-hint">Malawi time.</p>
      <label class="rp-label" for="nt-rs-venue">Venue</label>
      <input class="rp-input" id="nt-rs-venue" data-nt-rs-venue maxlength="120"
             value="${esc(match.venue || "")}" autocapitalize="words">
      <div class="rp-row">
        <input class="rp-input" data-nt-rs-city placeholder="City"
               maxlength="120" value="${esc(match.city || "")}" autocapitalize="words">
        <input class="rp-input" data-nt-rs-country placeholder="Country"
               maxlength="120" value="${esc(match.country || "")}" autocapitalize="words">
      </div>
      <button class="rp-btn is-ghost" type="button" data-nt-rs-save>Save date and ground</button>
    `, state.open?.["nt-reschedule"])}

    <div data-nt-detail></div>
  `);

  drawNTDetail(match, state);
  wireNTReschedule(match, state);

  const valueEls = {
    ours: view.querySelector('[data-nt-value="ours"]'),
    theirs: view.querySelector('[data-nt-value="theirs"]'),
  };
  const publishBtn = view.querySelector("[data-nt-publish]");
  const note = view.querySelector("[data-nt-note]");
  const etEl = view.querySelector("[data-nt-et]");
  const pensEl = view.querySelector("[data-nt-pens]");
  const sourceEl = view.querySelector("[data-nt-source]");

  etEl.addEventListener("change", () => { state.extraTime = etEl.checked; });
  pensEl.addEventListener("input", () => { state.pens = pensEl.value; });
  sourceEl.addEventListener("input", () => { state.source = sourceEl.value; });

  function refresh() {
    const info = ntStatusMeta(state.status);
    valueEls.ours.textContent = state.ours;
    valueEls.theirs.textContent = state.theirs;
    view.querySelectorAll("[data-nt-step]").forEach((b) => {
      b.disabled = state.busy || !info.scored;
    });
    publishBtn.disabled = state.busy;
    publishBtn.textContent = state.busy ? "Publishing…"
      : info.scored ? `Publish ${state.ours}–${state.theirs} ${info.short}`
                    : "Save as not played yet";
    note.textContent = info.scored
      ? "This will appear on everyleague.co."
      : "This clears any score already recorded.";
  }
  state.refreshPublish = refresh;

  view.querySelector("[data-nt-status]").addEventListener("change", (e) => {
    state.status = e.target.value;
    refresh();
  });
  view.addEventListener("click", (event) => {
    const target = event.target.closest("[data-nt-step]");
    if (!target || target.disabled) return;
    const [side, delta] = target.dataset.ntStep.split(":");
    state[side] = Math.max(0, Math.min(99, state[side] + Number(delta)));
    refresh();
  });

  publishBtn.addEventListener("click", async () => {
    if (state.busy) return;                      // rule 2
    clearFlash();
    state.busy = true; refresh();
    const info = ntStatusMeta(state.status);
    const { data, error } = await supabase.rpc("submit_nt_result", {
      p_match_id: match.match_id,
      p_team_score: info.scored ? state.ours : null,
      p_opponent_score: info.scored ? state.theirs : null,
      p_status: state.status,
      p_extra_time: state.extraTime,
      p_penalty_shootout: state.pens.trim(),
      p_source_ref: state.source.trim(),
    });
    state.busy = false;
    if (error) { refresh(); flash(humanError(error), "error"); return; }  // rule 1
    Object.assign(match, (data || [])[0] || {});
    state.published = match.team_score != null;
    invalidateReference();
    flash("Published. The site updates in a few minutes.", "ok", 6000);
    requestRebuild();
    drawNTMatch(match, state);
  });

  refresh();
}

/** Moving an international to another day or ground.
 *
 *  One button rather than club football's two: update_nt_fixture (0012)
 *  already writes date, kickoff, venue, city and country together — nt_matches
 *  has no venues table to resolve a name against, so there is no second RPC to
 *  split the save across. Competition and opponent travel along unchanged;
 *  this screen has no box for either, so the values already on the match are
 *  sent straight back rather than exposed as editable here. */
function wireNTReschedule(match, state) {
  const dateEl = view.querySelector("[data-nt-rs-date]");
  const save = view.querySelector("[data-nt-rs-save]");
  if (!dateEl || !save) return;

  const kickoffEl = view.querySelector("[data-nt-rs-kickoff]");
  const venueEl = view.querySelector("[data-nt-rs-venue]");
  const cityEl = view.querySelector("[data-nt-rs-city]");
  const countryEl = view.querySelector("[data-nt-rs-country]");

  save.addEventListener("click", async () => {
    if (save.disabled) return;                  // rule 2: never submit twice
    clearFlash();

    const date = dateEl.value || "";
    const kickoff = kickoffEl.value || "";
    if (!date && kickoff) {
      flash("Set a date as well, or clear the kick-off time.", "error");
      return;
    }

    save.disabled = true;
    save.textContent = "Saving…";

    const { data, error } = await supabase.rpc("update_nt_fixture", {
      p_match_id: match.match_id,
      p_competition: match.competition,
      p_opponent: match.opponent,
      p_date: date,
      p_kickoff: kickoff,
      p_home_away: match.home_away,
      p_neutral: Boolean(match.neutral),
      p_venue: venueEl.value.trim(),
      p_city: cityEl.value.trim(),
      p_country: countryEl.value.trim(),
    });

    save.disabled = false;
    save.textContent = "Save date and ground";

    if (error) {
      // Rule 1: the inputs are untouched, so nothing typed is lost.
      flash(humanError(error), "error");
      return;
    }

    Object.assign(match, (data || [])[0] || {});
    flash("Saved.", "ok");
    drawNTMatch(match, state);
    requestRebuild();
  });
}

// ── The team sheet ───────────────────────────────────────────────────────────
//
// One screen for the XI, the bench, the changes, the cards and the armband,
// because that is how a team sheet is written, how it is stored, and how it is
// read back on everyleague.co. It replaced four separate accordions on the
// league screen — Cards, Substitutions, Line-ups, each with its own free-text
// name box — where entering one substitution meant typing two names that
// nothing checked against the eleven already entered.
//
// SQUAD-FIRST, PASTE AS A FALLBACK. A reporter covering a club covers it every
// week, so the second week should not be the first week's typing again. The
// squad is everyone that club has already fielded or scored through: tap a
// name and it joins the sheet carrying its player_id, its last shirt number
// and its last position. Typing is what happens when someone is genuinely new.
//
// WHY THE player_id MATTERS SO MUCH HERE. A name on a team sheet is worth
// something; a name that resolves to a player is worth much more — it is what
// makes the name clickable on the results page, what gives them a profile, and
// what makes "games played" a number rather than a guess. The tap is the whole
// design: it is faster than typing AND it is the path that carries the id.

const SHEET_ROLES = [
  ["starting", "Starting XI"],
  ["sub_on", "Came on"],
  ["unused_sub", "Unused sub"],
];
const SHEET_POSITIONS = ["", "GK", "DF", "MF", "FW"];

// One control, four states, cycled by tapping. Three checkboxes could express
// "yellow AND red AND second yellow", which is not a thing that can happen —
// and the database refuses it — so the control cannot offer it.
const CARD_STATES = [
  { key: "", label: "No card", short: "—" },
  { key: "yellow_card", label: "Yellow card", short: "Y" },
  { key: "yellow_red_card", label: "Second yellow", short: "YR" },
  { key: "red_card", label: "Red card", short: "R" },
];

const cardStateOf = (row) => row.red_card ? "red_card"
  : row.yellow_red_card ? "yellow_red_card"
  : row.yellow_card ? "yellow_card" : "";

function setCardState(row, key) {
  row.yellow_card = key === "yellow_card";
  row.yellow_red_card = key === "yellow_red_card";
  row.red_card = key === "red_card";
}

const STARTING_XI = 11;

function blankSheetRow(player = {}) {
  return {
    player_name: player.name || "", player_id: player.player_id || "",
    shirt_number: player.shirt_number || "", position: player.position || "",
    role: "starting", captain: false, motm: false,
    minute_on: "", minute_off: "", replaced_player: "",
    yellow_card: false, yellow_red_card: false, red_card: false,
  };
}

/** The value this player carried most often, ties broken by the most recent.
 *
 *  THE LATEST ROW USED TO WIN OUTRIGHT, and one slip poisoned every sheet
 *  after it: a keeper typed in as 1 for eight weeks and 11 once by accident
 *  came back pre-filled as 11 for the rest of the season, so the correction
 *  had to be made again every single week. A number a reporter has entered
 *  twice for this team this season is a number they meant; a number entered
 *  once is a guess that the next agreeing pair quietly overrules.
 *
 *  Callers pass the counts in most-recent-first order and the strict `>`
 *  below keeps the earliest-inserted winner, so a player with no repeats at
 *  all still gets their latest — which is exactly the old behaviour. */
function commonest(counts) {
  let best = "";
  let bestN = 0;
  counts.forEach((n, value) => { if (n > bestN) { best = value; bestN = n; } });
  return best;
}

/** Everyone this side has already fielded or scored through.
 *
 *  Deliberately derived rather than maintained: there is no squad-registration
 *  screen for club football, and asking for one before team sheets exist would
 *  be asking reporters to enter the same names twice. The list grows by itself
 *  as sheets are saved, which means week two is taps and week one is typing.
 *  A failure returns an empty list — the paste box still works, so a squad
 *  that cannot load costs convenience, never the entry. */
function clubSquad(teamId) {
  return once(`squad:${teamId}`, async () => {
    // The season each row belongs to rides along on the embed, because the
    // rule below is "this season" — a shirt is a fact about a squad list, and
    // squad lists are reissued every year.
    //
    // 600 rows where this was 300: a side plays some thirty matches a season
    // at eighteen names a sheet, and a window that does not cover one season
    // cannot count within it. It is one request per team per session, cached
    // by `once`, and a row is four short strings and a season id.
    const [season, [sheets, goals]] = await Promise.all([
      activeSeason().catch(() => null),
      Promise.all([
        // `created_at`, NOT `ord`. This ordered by ord for a year and the
        // comment under it claimed that meant "most recent first" — but
        // save_lineup numbers ord 1..N within each sheet and writes it
        // explicitly, so ord.desc means "everyone who was last on their own
        // team sheet", in no particular match order. The shirt this pre-filled
        // was therefore never the latest one; it was an arbitrary one. Every
        // sheet is deleted and re-inserted whole on save, so created_at is
        // when that sheet was last written, which is the order wanted here.
        supabase.from("lineups")
          .select("player_id,player_name,shirt_number,position,matches(season_id)")
          .eq("team_id", teamId)
          .order("created_at", { ascending: false }).limit(600),
        supabase.from("goals").select("player_id")
          .eq("team_id", teamId).neq("player_id", UNKNOWN_PLAYER).limit(300),
      ]),
    ]);
    const by = new Map();
    const bump = (counts, value) => {
      if (value) counts.set(value, (counts.get(value) || 0) + 1);
    };
    // Most recent first, so the first ANSWER seen for a player is their latest
    // shirt and position — the fallback, for anyone this season has not seen.
    // A blank is not an answer: a sheet entered as bare names must not put a
    // player's number beyond the reach of the sheet that did record it.
    (sheets.data || []).forEach((r) => {
      const key = r.player_id || `name:${r.player_name.toLowerCase()}`;
      if (!by.has(key)) {
        by.set(key, {
          player_id: r.player_id || "", name: r.player_name,
          shirt_number: "", position: "",
          shirts: new Map(), positions: new Map(),
        });
      }
      const entry = by.get(key);
      entry.shirt_number = entry.shirt_number || r.shirt_number || "";
      entry.position = entry.position || r.position || "";
      // A season that could not be read, or an embed the server declined,
      // leaves every tally empty and that fallback standing — degrading to
      // one sheet's worth of guess, never to nothing.
      if (!season || r.matches?.season_id !== season.season_id) return;
      bump(entry.shirts, r.shirt_number);
      bump(entry.positions, r.position);
    });
    by.forEach((entry) => {
      entry.shirt_number = commonest(entry.shirts) || entry.shirt_number;
      entry.position = commonest(entry.positions) || entry.position;
      delete entry.shirts;
      delete entry.positions;
    });
    // A scorer who has never been on a sheet is still someone this club has
    // played, so they belong in the list — with no shirt and no position,
    // because nothing here knows them.
    const missing = [...new Set((goals.data || []).map((g) => g.player_id))]
      .filter((id) => !by.has(id));
    if (missing.length) {
      const { data } = await supabase.from("players")
        .select("player_id,full_name,known_as").in("player_id", missing);
      (data || []).forEach((p) => by.set(p.player_id, {
        player_id: p.player_id, name: playerLabel(p),
        shirt_number: "", position: "",
      }));
    }
    return [...by.values()].sort((a, b) => a.name.localeCompare(b.name));
  }).catch(() => []);
}

/** The same, for a national team: the latest squad announcement plus anyone
 *  who has appeared in a line-up since. */
function nationalSquad(teamCode) {
  return once(`ntsquad:${teamCode}`, async () => {
    const [squads, sheets] = await Promise.all([
      supabase.from("nt_squads")
        .select("player_id,player_name,shirt_number,position,announcement_date")
        .eq("team_id", teamCode).limit(300),
      // created_at, not ord — see clubSquad: ord is a place on a sheet, not a
      // date, and sorting by it never meant what this said it meant.
      supabase.from("nt_lineups")
        .select("player_id,player_name,shirt_number,position,created_at")
        .eq("team_id", teamCode)
        .order("created_at", { ascending: false }).limit(300),
    ]);
    const rows = squads.data || [];
    // The current squad is the row group sharing the most recent
    // announcement_date — the same rule src/nt.py applies when it renders one.
    const latest = rows.reduce(
      (best, r) => (r.announcement_date || "") > best ? r.announcement_date : best, "");
    const by = new Map();
    const put = (r) => {
      const key = r.player_id || `name:${r.player_name.toLowerCase()}`;
      if (!by.has(key)) {
        by.set(key, {
          player_id: r.player_id || "", name: r.player_name,
          shirt_number: r.shirt_number || "", position: r.position || "",
        });
      }
    };
    rows.filter((r) => (r.announcement_date || "") === latest).forEach(put);
    (sheets.data || []).forEach(put);
    // The same repeat rule as the club squad, with no season to scope it by:
    // a cap list is not seasonal, and a number worn in four internationals
    // beats one typed once. An announcement that carried a number keeps it —
    // that list IS the squad, where a team sheet is a report of one.
    const tally = new Map();
    (sheets.data || []).forEach((r) => {
      const key = r.player_id || `name:${r.player_name.toLowerCase()}`;
      if (!tally.has(key)) tally.set(key, { shirts: new Map(), positions: new Map() });
      const counts = tally.get(key);
      const bump = (map, value) => {
        if (value) map.set(value, (map.get(value) || 0) + 1);
      };
      bump(counts.shirts, r.shirt_number);
      bump(counts.positions, r.position);
    });
    const announced = new Set(rows.filter((r) => (r.announcement_date || "") === latest)
      .map((r) => r.player_id || `name:${r.player_name.toLowerCase()}`));
    tally.forEach((counts, key) => {
      const entry = by.get(key);
      if (!entry) return;
      // Per field, not per player: an announcement routinely lists numbers and
      // no positions, and the half it left blank is worth filling in.
      const keep = announced.has(key);
      if (!keep || !entry.shirt_number) {
        entry.shirt_number = commonest(counts.shirts) || entry.shirt_number;
      }
      if (!keep || !entry.position) {
        entry.position = commonest(counts.positions) || entry.position;
      }
    });
    return [...by.values()].sort((a, b) => a.name.localeCompare(b.name));
  }).catch(() => []);
}

/** The mutable sheet for one side, created on first sight from what is saved. */
function sheetState(state, key, saved) {
  state.sheets = state.sheets || {};
  if (!state.sheets[key]) {
    // `linking` is the index of the row whose "link to a player" box is open,
    // or null. One at a time: it is a repair, not a step in normal entry.
    //
    // `adding` is which end of the sheet a tapped name lands on. It starts
    // wherever the saved sheet left off — a sheet reopened with eleven
    // starters is being added to at the back, not the front.
    const rows = (saved || []).map((r) => ({ ...r }));
    state.sheets[key] = {
      rows, filter: "", linking: null,
      adding: rows.filter((r) => r.role === "starting").length >= STARTING_XI
        ? "unused_sub" : "starting",
    };
  }
  return state.sheets[key];
}

const onSheet = (sheet, entry) => sheet.rows.some((r) =>
  (entry.player_id && r.player_id === entry.player_id)
  || r.player_name.toLowerCase() === entry.name.toLowerCase());

function squadListHtml(sheet, squad, key) {
  const term = sheet.filter.trim().toLowerCase();
  const free = squad.filter((p) => !onSheet(sheet, p));
  const shown = term ? free.filter((p) => p.name.toLowerCase().includes(term)) : free;

  const chips = shown.slice(0, 40).map((p) => `
    <button type="button" class="rp-squad-chip" data-squad-add="${esc(p.player_id)}"
            data-squad-name="${esc(p.name)}" data-sheet-key="${esc(key)}">
      ${p.shirt_number ? `<b>${esc(p.shirt_number)}</b>` : ""}${esc(p.name)}
    </button>`).join("");

  // Offered only once the filter is a plausible name and matches nobody, so a
  // reporter part-way through typing a name that IS in the list is never
  // invited to create a second copy of that person.
  const canCreate = term.length >= 2
    && !shown.some((p) => p.name.toLowerCase() === term);
  const create = canCreate ? `
    <button type="button" class="rp-squad-chip is-new"
            data-squad-create="${esc(sheet.filter.trim())}"
            data-sheet-key="${esc(key)}">
      ＋ Add “${esc(sheet.filter.trim())}”
    </button>` : "";

  if (!chips && !create) {
    return `<p class="rp-hint">${squad.length
      ? "Everyone already on the sheet."
      : "No squad on file yet — type the names below, or paste the sheet."}</p>`;
  }
  return `<div class="rp-squad-chips">${chips}${create}</div>`;
}

function sheetRowHtml(sheet, row, i, key) {
  const state = cardStateOf(row);
  const card = CARD_STATES.find((c) => c.key === state);
  const others = sheet.rows.filter((_o, j) => j !== i);
  // What a substitution on some other row has already decided about this one.
  const derivedOff = offMinuteFor(sheet.rows, row);
  // AN UNLINKED ROW HAS TO BE FIXABLE WITHOUT DELETING IT. A pasted sheet
  // arrives with every name that matched nobody carrying no player_id, and
  // until this button existed the only way to give one an id was to remove the
  // row and add the person again — losing their shirt, position, card and
  // whatever substitution named them. One row at a time, opened by tapping,
  // because the whole point is that most rows do not need it.
  const linking = sheet.linking === i;
  return `
    <li class="rp-sheet-row" data-sheet-row="${i}">
      <div class="rp-sheet-head">
        ${/* The shirt is its own element so the box below can write into it
              without redrawing the row — see wireSheet's change handler, and
              the tap that redrawing used to eat. */ ""}
        <span class="rp-sheet-name"><b data-sheet-shirt>${
          row.shirt_number ? esc(row.shirt_number) + ". " : ""}</b>${esc(row.player_name)}
          ${row.role === "sub_on"
            ? '<em class="rp-sheet-on" title="Came on as a substitute">&#8593; on</em>' : ""}
          ${row.player_id ? "" : '<em class="rp-sheet-warn" title="Not linked to a player — they will show as plain text">name only</em>'}
        </span>
        <button class="rp-x" type="button" data-sheet-del="${i}" data-sheet-key="${esc(key)}"
                aria-label="Remove ${esc(row.player_name)}">&times;</button>
      </div>
      <div class="rp-sheet-controls">
        <label class="rp-sheet-field">
          <span class="rp-sheet-field-label">Role</span>
          <select class="rp-input" data-sheet-field="role" data-sheet-i="${i}" data-sheet-key="${esc(key)}">
            ${SHEET_ROLES.map(([v, l]) => `<option value="${v}"${
              row.role === v ? " selected" : ""}>${l}</option>`).join("")}
          </select>
        </label>
        <label class="rp-sheet-field">
          <span class="rp-sheet-field-label">Position</span>
          <select class="rp-input" data-sheet-field="position" data-sheet-i="${i}" data-sheet-key="${esc(key)}">
            ${SHEET_POSITIONS.map((p) => `<option value="${p}"${
              row.position === p ? " selected" : ""}>${p || "—"}</option>`).join("")}
          </select>
        </label>
        <label class="rp-sheet-field">
          <span class="rp-sheet-field-label">Shirt number</span>
          <input class="rp-input" data-sheet-field="shirt_number"
                 data-sheet-i="${i}" data-sheet-key="${esc(key)}" inputmode="numeric"
                 value="${esc(row.shirt_number || "")}">
        </label>
        ${row.role === "sub_on" ? `
          <label class="rp-sheet-field">
            <span class="rp-sheet-field-label">On (min)</span>
            <input class="rp-input" data-sheet-field="minute_on" data-sheet-i="${i}"
                   data-sheet-key="${esc(key)}" value="${esc(row.minute_on || "")}"
                   inputmode="numeric">
          </label>
          <label class="rp-sheet-field rp-sheet-field-wide">
            <span class="rp-sheet-field-label">Replaced</span>
            <select class="rp-input" data-sheet-field="replaced_player"
                    data-sheet-i="${i}" data-sheet-key="${esc(key)}">
              <option value="">nobody named</option>
              ${others.map((o) => `
                <option value="${esc(o.player_name)}"${
                  row.replaced_player === o.player_name ? " selected" : ""}>
                ${esc(o.player_name)}</option>`).join("")}
            </select>
          </label>` : ""}
        ${/* OFF' FILLS ITSELF IN. Naming a starter on the substitute's row is
              already the sentence "he came off in the 60th"; asking for the
              minute again on his own row was asking the same question twice,
              and the answer sat in a placeholder so pale that reporters typed
              it in anyway. Filled and read-only when a substitution says it —
              the value is not stored (save_lineup derives the same minute, and
              a copy would go stale the moment the substitution was corrected),
              which is also why it carries no data-sheet-field.
              Editable when nothing says it, for the sending-off and the
              withdrawal nobody replaced — and editable again the moment a
              minute IS typed, so an explicit one can always be taken back
              out. `data-off-derived` is what the repaint below compares. */ ""}
        ${row.role !== "starting" ? "" : derivedOff && !row.minute_off ? `
          <label class="rp-sheet-field">
            <span class="rp-sheet-field-label">Off (min)</span>
            <input class="rp-input is-derived" data-sheet-off
                   data-off-derived="${esc(derivedOff)}" readonly tabindex="-1"
                   value="${esc(derivedOff)}"
                   title="From the substitution below — change it there">
          </label>` : `
          <label class="rp-sheet-field">
            <span class="rp-sheet-field-label">Off (min)</span>
            <input class="rp-input" data-sheet-field="minute_off" data-sheet-i="${i}"
                   data-sheet-key="${esc(key)}" data-sheet-off
                   data-off-derived="${esc(derivedOff)}"
                   value="${esc(row.minute_off || "")}"
                   title="Only needed when no substitution says it — a sending-off, or coming off unreplaced"
                   inputmode="numeric">
          </label>`}
      </div>
      <div class="rp-sheet-flags">
        <button type="button" class="rp-chip${row.captain ? " is-on" : ""}"
                data-sheet-captain="${i}" data-sheet-key="${esc(key)}"
                aria-pressed="${row.captain}">Captain</button>
        ${row.role === "unused_sub" ? "" : `
          <button type="button" class="rp-chip rp-motm-chip${row.motm ? " is-on" : ""}"
                  data-sheet-motm="${i}" data-sheet-key="${esc(key)}"
                  aria-pressed="${Boolean(row.motm)}"
                  aria-label="Man of the match"
                  title="Man of the match — one player in the whole match">
            <span aria-hidden="true">&#9733;</span>
            ${/* SHORT UNTIL IT MEANS SOMETHING. Spelled out on every row, three
                  chips no longer fit a 390px line and the flags wrapped onto a
                  second one — on all twenty rows, to label a thing that is true
                  of one of them. Abbreviated it fits; set, it spells itself
                  out on the one row that is it. */ ""}
            ${row.motm ? "Man of the match" : "MOTM"}</button>`}
        <button type="button" class="rp-chip rp-card-chip is-card-${state || "none"}"
                data-sheet-card="${i}" data-sheet-key="${esc(key)}"
                title="Tap to change: none, yellow, second yellow, red">
          ${esc(card.short)} <em>${esc(card.label)}</em>
        </button>
        ${row.player_id ? "" : `
          <button type="button" class="rp-chip${linking ? " is-on" : ""}"
                  data-sheet-link="${i}" data-sheet-key="${esc(key)}">
            ${linking ? "Cancel" : "Link to a player"}</button>`}
      </div>
      ${linking ? `
        <div class="rp-pick" data-sheet-picker="${i}">
          <input class="rp-input" data-sheet-link-input data-sheet-i="${i}"
                 data-sheet-key="${esc(key)}" autocomplete="off"
                 autocapitalize="words"
                 value="${esc(row.player_name)}"
                 placeholder="Who is this?">
          <ul class="rp-suggest" data-sheet-link-list hidden></ul>
        </div>
        <p class="rp-hint">Linking keeps the sheet exactly as it is and adds the
          id behind it — the name on everyleague.co then comes from the player,
          so correcting it later corrects every match they are in.</p>` : ""}
    </li>`;
}

/** Which of the two lists below a row is drawn in. */
const sheetGroupOf = (row) => (row.role || "starting") === "starting"
  ? "starting" : "bench";

/** The rows, in two lists: the XI, and the bench.
 *
 *  TWO GROUPS, NOT THREE. Sub-on used to be its own heading between the other
 *  two, so marking a substitute as having come on threw them out of the bench
 *  and up the screen — the reporter's eye went with them, and the next name
 *  they wanted was no longer where they had left it. Filling in a sheet is
 *  reading one list top to bottom, and a list that reorders itself under you
 *  while you read it is the most disorienting thing a form can do. A
 *  substitute now stays exactly where they were put and grows two boxes; the
 *  ↑ beside the name is what says they came on.
 *
 *  The heading counts both kinds together for the same reason — a number that
 *  moved on every role change would have to be repainted on every role change.
 *
 *  The index passed down is the row's index in `sheet.rows`, never its place
 *  in the group — every control on a row addresses state by that index, so
 *  grouping is allowed to change what is drawn where and nothing else. An
 *  empty group is not drawn: a heading with nothing under it reads like
 *  something is missing rather than like nothing is there yet. */
function sheetGroupsHtml(sheet, key) {
  const numbered = sheet.rows.map((row, i) => [row, i]);
  return [["starting", "Starting XI"], ["bench", "Substitutes"]].map(([group, heading]) => {
    const mine = numbered.filter(([r]) => sheetGroupOf(r) === group);
    if (!mine.length) return "";
    const starting = group === "starting";
    const over = starting && mine.length > STARTING_XI;
    return `
      <h3 class="rp-sheet-group${over ? " is-warn" : ""}">${heading}
        <span>${mine.length}${starting ? ` of ${STARTING_XI}` : ""}</span></h3>
      <ul class="rp-sheet">${
        mine.map(([r, i]) => sheetRowHtml(sheet, r, i, key)).join("")}</ul>`;
  }).join("");
}

/** One side's whole team sheet, ready to drop into a section body.
 *
 *  WHICH END OF THE SHEET AM I FILLING IN? The rule — the XI fills first, then
 *  the bench — was written in a sentence above the squad and nowhere else, so
 *  the moment the eleventh name went on, taps silently started meaning
 *  something different and the only way to notice was to read every row's Role
 *  box. It is a switch now: it says where the next tap lands, it moves by
 *  itself when the XI fills, and it can be moved by hand for the sheet that
 *  arrives subs-first. The list below it is grouped under the same three
 *  headings, so the sheet reads the way a team sheet is written. */
function sheetHtml({ key, teamName, sheet, squad }) {
  const starters = sheet.rows.filter((r) => r.role === "starting").length;
  const subs = sheet.rows.filter((r) => r.role !== "starting").length;
  const unlinked = sheet.rows.filter((r) => !r.player_id).length;
  const adding = sheet.adding === "unused_sub" ? "unused_sub" : "starting";

  const segment = (role, label, count) => `
    <button type="button" class="rp-seg-btn${adding === role ? " is-on" : ""}"
            data-sheet-adding="${role}" data-sheet-key="${esc(key)}"
            aria-pressed="${adding === role}">
      ${label} <em>${count}</em>
    </button>`;

  return `
    <div class="rp-sheet-block" data-sheet-block="${esc(key)}">
      <p class="rp-sheet-adding-label" id="adding-${esc(key)}">Tapping a name adds them to</p>
      <div class="rp-seg" role="group" aria-labelledby="adding-${esc(key)}">
        ${segment("starting", "Starting XI", `${starters} of ${STARTING_XI}`)}
        ${segment("unused_sub", "Substitutes", subs)}
      </div>
      <p class="rp-hint">${adding === "starting"
        ? `Names go into the starting XI. It switches to substitutes by itself
           once there are ${STARTING_XI} — or tap to switch now.`
        : "Names go on the bench. Set who came on, and when, on their row below."}</p>
      <input class="rp-input" data-sheet-filter data-sheet-key="${esc(key)}"
             placeholder="Find or add a player" autocomplete="off"
             autocapitalize="words" value="${esc(sheet.filter)}">
      <div data-squad-list>${squadListHtml(sheet, squad, key)}</div>

      <details class="rp-sec rp-paste" data-sec="paste-${esc(key)}">
        <summary>Paste a sheet instead</summary>
        <div class="rp-sec-body">
          <textarea class="rp-input rp-textarea" data-sheet-paste
                    data-sheet-key="${esc(key)}" rows="7"
                    placeholder="1 Mercy Sikelo GK (C)&#10;5 Sabina Thom MF [Y]&#10;Subs:&#10;14 Rachel Kundananji 62' for Sabina Thom"></textarea>
          <p class="rp-hint">One player per line. Everything except the name is
            optional:</p>
          <ul class="rp-legend">
            <li><code>10</code> leading number — shirt</li>
            <li><code>GK DF MF FW</code> — position</li>
            <li><code>(C)</code> — captain</li>
            <li><code>[Y]</code> <code>[YR]</code> <code>[R]</code> — yellow, second
              yellow, red</li>
            <li><code>Subs:</code> — a heading; everyone under it is a substitute
              until the next heading</li>
            <li><code>62'</code> and <code>for Sabina Thom</code> — came on, and for
              whom</li>
          </ul>
          <p class="rp-hint">Pasted names are matched against the squad above.
            Anyone who matches nobody comes in as a name only — tap them in the
            list above afterwards to link them.</p>
          <button class="rp-btn is-ghost" type="button" data-sheet-paste-add
                  data-sheet-key="${esc(key)}">Add these players</button>
        </div>
      </details>

      ${sheet.rows.length ? `
        ${starters > STARTING_XI ? `<p class="rp-hint is-warn" style="margin-top:14px">
          ${starters} in the starting XI — that is too many.</p>` : ""}
        ${unlinked ? `<p class="rp-hint" style="margin-top:14px">${unlinked} not
          linked to a player — tap “Link to a player” on their row.</p>` : ""}
        ${sheetGroupsHtml(sheet, key)}
        <button class="rp-btn" type="button" data-sheet-save data-sheet-key="${esc(key)}">
          Save ${esc(teamName)}’s sheet</button>`
        : '<p class="rp-hint" style="margin-top:14px">Nobody on the sheet yet.</p>'}
    </div>`;
}

/** Wire every sheet on the screen. `sheets` is [{key, teamName, sheet, squad,
 *  save, saved}].
 *
 *  Listeners go on each sheet's own block, NOT on the detail host. The host
 *  survives every redraw while its innerHTML is replaced, so a listener added
 *  there would be added again on the next draw and again on the one after —
 *  and unlike the network handlers around it these mutate local state, so a
 *  duplicate would splice two rows out for one tap. A block is rebuilt with
 *  the HTML, which takes its listeners with it. */
function wireSheets(host, sheets, redraw) {
  sheets.forEach((ctx) => {
    const root = host.querySelector(`[data-sheet-block="${CSS.escape(ctx.key)}"]`);
    // The other side's sheet, so man of the match can be taken off it here
    // rather than only on the server. The award is one per MATCH and the two
    // sheets are two screens; without this a reporter starring an away player
    // would see the home star still lit until the page was reloaded.
    if (root) wireSheet(root, { ...ctx, siblings: sheets.filter((o) => o !== ctx) },
                        redraw);
  });
}

function wireSheet(root, ctx, redraw) {
  const { sheet, squad } = ctx;

  // The filter repaints only the chip list, never the whole block: redrawing
  // the block would take the focus out of the box being typed into, which on a
  // phone also closes the keyboard.
  root.addEventListener("input", (e) => {
    const box = e.target.closest("[data-sheet-filter]");
    if (box) {
      sheet.filter = box.value;
      const list = root.querySelector("[data-squad-list]");
      if (list) list.innerHTML = squadListHtml(sheet, squad, ctx.key);
      return;
    }
    const link = e.target.closest("[data-sheet-link-input]");
    if (link) paintLinkSuggestions(link);
  });

  // Searching from a "link to a player" box. Only the list repaints, never the
  // block — a redraw here would take the focus out of the box being typed into
  // and shut the keyboard, the same reason the squad filter repaints in place.
  let linkTimer = null;
  let linkLatest = 0;
  function paintLinkSuggestions(input) {
    clearTimeout(linkTimer);
    const list = input.parentElement.querySelector("[data-sheet-link-list]");
    if (!list) return;
    const term = input.value.trim();
    if (term.length < 2) { list.hidden = true; list.innerHTML = ""; return; }
    linkTimer = setTimeout(async () => {
      const mine = ++linkLatest;
      let players;
      try {
        players = await searchPlayers(term);
      } catch (err) {
        if (mine !== linkLatest) return;
        // Rule 3: a failed lookup costs the link, never the row. The name is
        // already on the sheet and still saves.
        list.hidden = true;
        list.innerHTML = "";
        flash("Could not search players just now — the name still saves.", "warn");
        return;
      }
      if (mine !== linkLatest) return;
      const rows = players.map((p) => `<li role="option"><button type="button"
        class="rp-suggest-btn" data-sheet-link-pick="${esc(p.player_id)}"
        data-link-name="${esc(playerLabel(p))}">
        <span>${esc(playerLabel(p))}</span>${p.matched && p.matched !== playerLabel(p)
          ? `<em>also filed as ${esc(p.matched)}</em>` : ""}</button></li>`);
      const exact = players.some(
        (p) => playerLabel(p).toLowerCase() === term.toLowerCase());
      if (!exact) {
        rows.push(`<li role="option"><button type="button"
          class="rp-suggest-btn is-new" data-sheet-link-create="${esc(term)}">
          <span>＋ Add “${esc(term)}” as a new player</span>
          <em>only if they are not in the list above</em></button></li>`);
      }
      list.innerHTML = rows.join("");
      list.hidden = false;
    }, 250);
  }

  /** Give an existing row an id, and the registry's spelling with it.
   *
   *  Anyone this row was named BY moves with it. A substitution stores
   *  `replaced_player` as a NAME — that is all the schema records — so
   *  renaming the starter and not the sub_on row that named them would leave a
   *  dangling name, which save_lineup and validate.py both reject. Same rule
   *  the renderer follows (lineups.with_canonical_names), same reason.
   */
  function link(row, playerId, name) {
    const was = row.player_name;
    row.player_id = playerId;
    row.player_name = name;
    if (was !== name) {
      sheet.rows.forEach((r) => {
        if (r.replaced_player === was) r.replaced_player = name;
      });
    }
    if (!squad.some((p) => p.player_id === playerId)) {
      squad.push({ player_id: playerId, name, shirt_number: "", position: "" });
    }
    sheet.linking = null;
    redraw();
  }

  /** Put someone on the sheet, wherever the switch above the squad is pointing
   *  — the XI first, then the bench, so nobody has to set a role for the
   *  ordinary case of eleven and then seven. The switch moves itself once the
   *  XI is full, which is the same rule as before; the difference is that it
   *  is now visible on the screen rather than only in the saved row. */
  function put(row) {
    const starters = sheet.rows.filter((r) => r.role === "starting").length;
    const toXI = sheet.adding !== "unused_sub" && starters < STARTING_XI;
    row.role = toXI ? "starting" : "unused_sub";
    if (!toXI || starters + 1 >= STARTING_XI) sheet.adding = "unused_sub";
    sheet.rows.push(row);
    sheet.filter = "";
    // `linking` is an index into rows, so anything that changes the list has
    // to close it rather than leave it pointing at whoever moved into that
    // slot. See sheetRowHtml.
    sheet.linking = null;
    redraw();
  }

  root.addEventListener("click", async (e) => {
    const seg = e.target.closest("[data-sheet-adding]");
    if (seg) {
      sheet.adding = seg.dataset.sheetAdding;
      redraw();
      return;
    }

    const add = e.target.closest("[data-squad-add]");
    if (add) {
      const id = add.dataset.squadAdd;
      const known = squad.find((p) => (id && p.player_id === id)
        || p.name === add.dataset.squadName);
      put(blankSheetRow({
        player_id: id, name: add.dataset.squadName,
        shirt_number: known?.shirt_number || "", position: known?.position || "",
      }));
      return;
    }

    const create = e.target.closest("[data-squad-create]");
    if (create) {
      if (create.disabled) return;
      const name = create.dataset.squadCreate;
      create.disabled = true;
      create.textContent = "Adding…";
      const { data, error } = await supabase.rpc("create_player", { p_full_name: name });
      const made = (data || [])[0];
      if (error || !made) {
        // Rule 3 again: a lookup that fails must not cost the entry. The name
        // goes on the sheet unlinked, and can be linked later.
        console.warn("[everyleague] create_player failed:", error);
        flash("Could not add that player — the name goes on the sheet unlinked.",
              "warn");
      }
      // create_player is idempotent on the name, so a player created here may
      // be one who already existed; either way they belong in the squad now.
      if (made && !squad.some((p) => p.player_id === made.player_id)) {
        squad.push({ player_id: made.player_id, name: playerLabel(made),
                     shirt_number: "", position: "" });
      }
      put(blankSheetRow({
        player_id: made ? made.player_id : "",
        name: made ? playerLabel(made) : name,
      }));
      return;
    }

    const openLink = e.target.closest("[data-sheet-link]");
    if (openLink) {
      const i = Number(openLink.dataset.sheetLink);
      sheet.linking = sheet.linking === i ? null : i;
      redraw();
      return;
    }

    const pick = e.target.closest("[data-sheet-link-pick]");
    if (pick) {
      const row = sheet.rows[sheet.linking];
      if (row) link(row, pick.dataset.sheetLinkPick, pick.dataset.linkName);
      return;
    }

    const linkNew = e.target.closest("[data-sheet-link-create]");
    if (linkNew) {
      if (linkNew.disabled) return;
      const row = sheet.rows[sheet.linking];
      if (!row) return;
      const name = linkNew.dataset.sheetLinkCreate;
      linkNew.disabled = true;
      linkNew.querySelector("span").textContent = "Adding…";
      const { data, error } = await supabase.rpc("create_player",
        { p_full_name: name });
      const made = (data || [])[0];
      if (error || !made) {
        linkNew.disabled = false;
        console.warn("[everyleague] create_player failed:", error);
        flash("Could not add that player — the name stays on the sheet unlinked.",
              "warn");
        return;
      }
      link(row, made.player_id, playerLabel(made));
      return;
    }

    const drop = e.target.closest("[data-sheet-del]");
    if (drop) {
      sheet.rows.splice(Number(drop.dataset.sheetDel), 1);
      sheet.linking = null;
      redraw();
      return;
    }

    const cap = e.target.closest("[data-sheet-captain]");
    if (cap) {
      const row = sheet.rows[Number(cap.dataset.sheetCaptain)];
      if (!row) return;
      // One armband per side: giving it to someone takes it off whoever had it.
      const on = !row.captain;
      sheet.rows.forEach((r) => { r.captain = false; });
      row.captain = on;
      redraw();
      return;
    }

    const star = e.target.closest("[data-sheet-motm]");
    if (star) {
      const row = sheet.rows[Number(star.dataset.sheetMotm)];
      if (!row) return;
      // One star in the whole MATCH, where the armband above it is one per
      // side — so this clears both sheets before lighting one name. The
      // server does the same thing on save (0028); doing it here as well is
      // what makes the screen agree with what will be stored.
      const on = !row.motm;
      sheet.rows.forEach((r) => { r.motm = false; });
      (ctx.siblings || []).forEach((o) => o.sheet.rows.forEach((r) => { r.motm = false; }));
      row.motm = on;
      redraw();
      return;
    }

    const card = e.target.closest("[data-sheet-card]");
    if (card) {
      const row = sheet.rows[Number(card.dataset.sheetCard)];
      if (!row) return;
      const next = (CARD_STATES.findIndex((c) => c.key === cardStateOf(row)) + 1)
        % CARD_STATES.length;
      setCardState(row, CARD_STATES[next].key);
      redraw();
      return;
    }

    const paste = e.target.closest("[data-sheet-paste-add]");
    if (paste) {
      const box = root.querySelector("[data-sheet-paste]");
      const added = parseTeamSheet(box?.value || "");
      if (!added.length) { flash("Type at least one name.", "warn"); return; }
      const seen = new Set(sheet.rows.map((r) => r.player_name.toLowerCase()));
      added.forEach((row) => {
        if (seen.has(row.player_name.toLowerCase())) return;
        // A pasted name that matches somebody already known arrives LINKED.
        // That is the whole difference between a sheet of text and a sheet of
        // players, and it costs the reporter nothing.
        const hit = squad.find(
          (p) => p.name.toLowerCase() === row.player_name.toLowerCase());
        if (hit) {
          row.player_id = hit.player_id;
          row.shirt_number = row.shirt_number || hit.shirt_number;
          row.position = row.position || hit.position;
        }
        // A pasted line could carry both [YR] and [R]; the control below
        // cannot express that and the database refuses it, so it is collapsed
        // to the single most severe card here rather than rejected on save.
        setCardState(row, cardStateOf(row));
        sheet.rows.push(row);
        seen.add(row.player_name.toLowerCase());
      });
      if (box) box.value = "";
      redraw();
      return;
    }

    const save = e.target.closest("[data-sheet-save]");
    if (save) {
      if (save.disabled) return;
      clearFlash();
      save.disabled = true;
      const label = save.textContent;
      save.textContent = "Saving…";
      const error = await ctx.save(sheet.rows.map((r) => ({
        player_name: r.player_name, player_id: r.player_id || "",
        shirt_number: r.shirt_number || "", position: r.position || "",
        role: r.role || "starting", captain: Boolean(r.captain),
        motm: Boolean(r.motm),
        minute_on: r.minute_on || "", minute_off: r.minute_off || "",
        replaced_player: r.replaced_player || "",
        yellow_card: Boolean(r.yellow_card),
        yellow_red_card: Boolean(r.yellow_red_card),
        red_card: Boolean(r.red_card),
      })));
      save.disabled = false;
      save.textContent = label;
      // Rule 1: the sheet stays in state on failure, so nothing typed is lost.
      if (error) { flash(humanError(error), "error"); return; }
      flash("Team sheet saved.", "ok");
      requestRebuild();
      ctx.saved?.();
    }
  });

  /** Re-render one row where it stands, leaving every other row's DOM alone. */
  function drawRow(i) {
    const li = root.querySelector(`[data-sheet-row="${i}"]`);
    if (li && sheet.rows[i]) {
      li.outerHTML = sheetRowHtml(sheet, sheet.rows[i], i, ctx.key);
    }
  }

  /** Show the starters whose Off' a substitution has just answered — or
   *  un-answered, when the substitution that named them was changed or undone.
   *
   *  Only the rows whose derived minute actually CHANGED are redrawn, which is
   *  normally one and often none. That precision is the point: this runs on a
   *  text box losing focus, i.e. as a finger is already landing somewhere
   *  else, and every row it touches needlessly is a tap it could swallow. */
  function paintDerivedOff() {
    sheet.rows.forEach((row, i) => {
      if (row.role !== "starting") return;
      const box = root.querySelector(`[data-sheet-row="${i}"] [data-sheet-off]`);
      if (box && box.dataset.offDerived !== offMinuteFor(sheet.rows, row)) drawRow(i);
    });
  }

  // NOTHING HERE MAY REDRAW THE BLOCK ON A TEXT BOX. `change` fires when a box
  // loses focus, and on a phone that is the same gesture as the tap onto the
  // next box: redrawing threw that box away mid-tap, so the tap did nothing
  // and every field after the first cost two. Typing into a box now writes the
  // one thing on the screen that box controls — its own row's shirt in the
  // heading, or the Off' the substitution decides — and touches no other node.
  // A <select> is safe to redraw from, because its change lands when the
  // native picker closes and the next tap is a fresh one.
  root.addEventListener("change", (e) => {
    const field = e.target.dataset?.sheetField;
    if (!field) return;
    const i = Number(e.target.dataset.sheetI);
    const row = sheet.rows[i];
    if (!row) return;
    const wasGroup = sheetGroupOf(row);
    row[field] = e.target.value;

    if (field === "shirt_number") {
      const shirt = root.querySelector(`[data-sheet-row="${i}"] [data-sheet-shirt]`);
      if (shirt) shirt.textContent = row.shirt_number ? `${row.shirt_number}. ` : "";
      return;
    }

    if (field === "minute_off") {
      // Clearing an explicit minute hands the row back to the derivation, and
      // the box says so without being replaced — see the rule above.
      const derived = offMinuteFor(sheet.rows, row);
      if (derived && !row.minute_off) {
        e.target.value = derived;
        e.target.readOnly = true;
        e.target.classList.add("is-derived");
        delete e.target.dataset.sheetField;
      }
      return;
    }

    // A substitution names who it replaced and when, so both are answers to
    // some starter's Off' — including the starter it has just stopped naming.
    if (field === "minute_on" || field === "replaced_player") {
      paintDerivedOff();
      return;
    }

    if (field === "role") {
      // An unused substitute did not play, so cannot be man of the match — the
      // RPC refuses it. Cleared here rather than reported: a reporter moving
      // someone to the bench has already said what they mean, and a save that
      // fails on a flag they cannot see is the worst way to learn it.
      if (row.role === "unused_sub") row.motm = false;
      // Between the XI and the bench the row changes list and both counts
      // move, which is a redraw. Within the bench — the common one, a
      // substitute marked as having come on — it keeps its place and only
      // grows the two boxes that go with the new role.
      if (sheetGroupOf(row) !== wasGroup) { redraw(); return; }
      drawRow(i);
      paintDerivedOff();
    }
  });
}

// ── Officials, coaches and notes (0033) ──────────────────────────────────────
// The same three optional sections a league match has had since 0023–0025,
// reusing the identical registry and the identical pickers: wireOfficialPickers
// only ever reads data-official/data-kind off whatever is on screen, so it
// needs nothing NT-specific to work here too.

const NT_OFFICIAL_ROLES = [
  ["referee", "Referee", "referee"],
  ["assistant_referee_1", "Assistant referee 1", "referee"],
  ["assistant_referee_2", "Assistant referee 2", "referee"],
  ["fourth_official", "Fourth official", "referee"],
];
const NT_OFFICIAL_COACHES = ["coach", "opponent_coach"];
const NT_OFFICIAL_KEYS = NT_OFFICIAL_ROLES.map(([key]) => key).concat(NT_OFFICIAL_COACHES);
const NT_OFFICIAL_COLUMNS = NT_OFFICIAL_KEYS
  .concat(NT_OFFICIAL_KEYS.map((key) => `${key}_id`))
  .join(",");

function ntOfficialsForm(match, officials, teamName) {
  const box = (name, label, kind) => `
    <label class="rp-label" for="nt-off-${name}">${esc(label)}</label>
    <div class="rp-pick" data-official="${name}" data-kind="${kind}">
      <input class="rp-input" id="nt-off-${name}" name="${name}"
             value="${esc(officials[name] || "")}" placeholder="${esc(label)}"
             role="combobox" aria-expanded="false" aria-autocomplete="list"
             autocomplete="off" autocapitalize="words" maxlength="80">
      <input type="hidden" name="${name}_id"
             value="${esc(officials[`${name}_id`] || "")}">
      <ul class="rp-suggest" role="listbox" data-suggest hidden></ul>
    </div>`;
  return `
    <form data-nt-officials-form autocomplete="off">
      ${NT_OFFICIAL_ROLES.map(([key, label, kind]) => box(key, label, kind)).join("")}
      ${box("coach", `${teamName} head coach`, "coach")}
      ${box("opponent_coach", `${match.opponent || "Opponent"} head coach`, "coach")}
      <button class="rp-btn is-ghost" type="submit">Save officials</button>
      <p class="rp-hint" data-official-note>Every box is optional and an empty one
        shows nothing on everyleague.co — fill in whatever the graphic or the post
        actually says. Tap a name from the list and it becomes a link to that
        person's page. Clearing a box and saving removes that name.</p>
    </form>`;
}

/** ntDetailAction mirrors detailAction (see drawDetail) but redraws the
 *  national-team detail block, not the league one — the two screens keep
 *  separate cached state (state.detail vs state.ntDetail). */
async function ntDetailAction(button, busyLabel, fn, match, state) {
  if (button.disabled) return;                    // no double submits
  const original = button.textContent;
  button.disabled = true;
  button.textContent = busyLabel;
  try {
    const error = await fn();
    if (error) { flash(humanError(error), "error"); return; }
    flash("Saved.", "ok", 2500);
    state.ntDetail = null;
    await drawNTDetail(match, state);
  } catch (err) {
    flash(humanError(err), "error");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

// ── Scorers and the team sheet ───────────────────────────────────────────────

async function drawNTDetail(match, state, { local = false } = {}) {
  const host = view.querySelector("[data-nt-detail]");
  if (!host) return;
  captureDetailState(state);
  const open = state.open || {};

  if (!local || !state.ntDetail) {
    const [goalsRes, lineRes, officialsRes] = await Promise.all([
      supabase.from("nt_goals")
        .select("goal_id,team_id,player_name,minute,goal_type")
        .eq("match_id", match.match_id).order("ord"),
      supabase.from("nt_lineups").select("*")
        .eq("match_id", match.match_id).order("ord"),
      // The officials AND the reporter's private notes, riding along for the
      // same reason the league match's detail query pairs them: one column on
      // the same row, one query that already reads it. See drawDetail.
      supabase.from("nt_matches").select(`${NT_OFFICIAL_COLUMNS},notes`)
        .eq("match_id", match.match_id).limit(1),
    ]);
    state.ntDetail = {
      goals: goalsRes.data || [],
      lineup: lineRes.data || [],
      officials: (officialsRes.data || [])[0] || {},
    };
  }
  const { goals, lineup, officials } = state.ntDetail;
  // The team sheet is edited as a whole, so it lives in state once loaded and
  // is only re-read from the database after a save.
  const sheetKey = match.team_code;
  const sheet = sheetState(state, sheetKey,
    lineup.filter((r) => r.team_id === match.team_code));
  const squad = await nationalSquad(match.team_code);

  const scored = match.team_score != null;
  const sideName = (id) => id === match.team_code ? state.teamName : match.opponent;

  const goalList = goals.map((g) => `
    <li><span>${esc(g.player_name)}
      <em>${esc(sideName(g.team_id))}${g.minute ? " · " + esc(g.minute) + "'" : ""}
        ${g.goal_type ? " · " + esc(g.goal_type) : ""}</em></span>
      <button class="rp-x" type="button" data-nt-del-goal="${esc(g.goal_id)}"
              aria-label="Remove ${esc(g.player_name)}">&times;</button></li>`).join("");

  const goalForm = scored ? `
    <form data-nt-goal-form autocomplete="off">
      <div class="rp-side-pick" role="radiogroup">
        <label><input type="radio" name="nt-goal-side" value="${esc(match.team_code)}" checked>
          <span>${esc(state.teamName)}</span></label>
        <label><input type="radio" name="nt-goal-side" value="OPPONENT">
          <span>${esc(match.opponent)}</span></label>
      </div>
      <input class="rp-input" name="player" placeholder="Scorer's name" required
             autocomplete="off" autocapitalize="words">
      <div class="rp-row">
        <input class="rp-input" name="minute" placeholder="Min" inputmode="numeric">
        <select class="rp-input" name="type">
          <option value="">Goal</option>
          <option value="penalty">Penalty</option>
          <option value="own goal">Own goal</option>
        </select>
      </div>
      <button class="rp-btn is-ghost" type="submit">Add scorer</button>
    </form>`
    : `<p class="rp-hint">Publish the score first — a scorer needs a goal to
         belong to.</p>`;

  // One wrapper, so the delegated listener below is rebuilt with the markup
  // rather than added again to a host that outlives it. See drawDetail.
  host.innerHTML = `
    <div data-detail-body>
    <h2 class="rp-field-head">Match detail <span class="rp-optional">optional</span></h2>

    ${section("ntgoals", "Goalscorers", goals.length,
      `<ul class="rp-list">${goalList}</ul>${goalForm}`, open.ntgoals)}

    ${section("ntsheet", "Team sheet", sheet.rows.length,
      sheetHtml({ key: sheetKey, teamName: state.teamName, sheet, squad }),
      open.ntsheet)}

    ${section("nt-officials", "Officials and coaches",
      NT_OFFICIAL_ROLES.filter(([key]) => officials[key]).length,
      ntOfficialsForm(match, officials, state.teamName), open["nt-officials"])}

    ${section("nt-notes", "Notes", officials.notes ? 1 : 0, `
      <form data-nt-notes-form>
        <textarea class="rp-input rp-textarea" name="notes" rows="4"
                  maxlength="4000" autocapitalize="sentences"
                  placeholder="Anything you are not sure about, or want to check later"
                  >${esc(officials.notes || "")}</textarea>
        <button class="rp-btn is-ghost" type="submit">Save notes</button>
        <p class="rp-hint">Only reporters see this — it is never shown on
          everyleague.co and it is not in the public data files. Clearing the
          box and saving deletes it.</p>
      </form>`, open["nt-notes"])}
    </div>`;

  wireNTDetail(match, state, { sheetKey, sheet, squad });
}

function wireNTDetail(match, state, { sheetKey, sheet, squad }) {
  const host = view.querySelector("[data-nt-detail]");
  if (!host) return;

  host.querySelector("[data-nt-goal-form]")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    const side = host.querySelector('input[name="nt-goal-side"]:checked').value;
    const button = f.querySelector('button[type="submit"]');
    if (button.disabled) return;
    button.disabled = true; button.textContent = "Adding…";
    (async () => {
      // The opponent has no code of its own — anything that is not our
      // team_code counts as theirs, so a stable label is enough.
      const teamId = side === "OPPONENT"
        ? (match.opponent || "OPPONENT").toUpperCase().replace(/\s+/g, "_")
        : match.team_code;
      const { error } = await supabase.rpc("submit_nt_goal", {
        p_match_id: match.match_id,
        p_team_id: teamId,
        p_player_name: f.player.value,
        p_minute: f.minute.value,
        p_goal_type: f.type.value,
      });
      button.disabled = false; button.textContent = "Add scorer";
      if (error) { flash(humanError(error), "error"); return; }
      flash("Saved.", "ok", 2500);
      requestRebuild();
      state.ntDetail = null;
      await drawNTDetail(match, state);
    })();
  });

  host.querySelector("[data-detail-body]")?.addEventListener("click", async (e) => {
    const del = e.target.closest("[data-nt-del-goal]");
    if (!del || del.disabled) return;
    del.disabled = true;
    const { error } = await supabase.rpc("delete_nt_goal",
      { p_goal_id: del.dataset.ntDelGoal });
    del.disabled = false;
    if (error) { flash(humanError(error), "error"); return; }
    requestRebuild();
    state.ntDetail = null;
    await drawNTDetail(match, state);
  });

  wireSheets(host, [{
    key: sheetKey, teamName: state.teamName, sheet, squad,
    save: async (rows) => (await supabase.rpc("save_nt_lineup", {
      p_match_id: match.match_id,
      p_team_id: match.team_code,
      p_rows: rows,
    })).error,
    saved: () => {
      // Re-read after a save: save_nt_lineup derives each starter's minute_off
      // from the substitutions, so what came back is not quite what went up.
      state.ntDetail = null;
      delete state.sheets[sheetKey];
      drawNTDetail(match, state);
    },
  }], () => drawNTDetail(match, state, { local: true }));

  wireOfficialPickers(host);

  host.querySelector("[data-nt-officials-form]")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    ntDetailAction(f.querySelector('button[type="submit"]'), "Saving…", async () => {
      const args = { p_match_id: match.match_id };
      NT_OFFICIAL_KEYS.forEach((key) => {
        args[`p_${key}`] = f[key].value.trim();
        args[`p_${key}_id`] = f[`${key}_id`].value.trim();
      });
      const { error } = await supabase.rpc("set_nt_match_officials", args);
      return error;
    }, match, state);
  });

  host.querySelector("[data-nt-notes-form]")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    ntDetailAction(f.querySelector('button[type="submit"]'), "Saving…", async () => {
      const { error } = await supabase.rpc("set_nt_match_notes", {
        p_match_id: match.match_id, p_notes: f.notes.value,
      });
      // NO requestRebuild: a note changes nothing on the public site.
      return error;
    }, match, state);
  });
}

/** "10 Tabitha Chawinga FW" -> {shirt_number, player_name, position}.
 *
 *  Both affixes are optional and the middle is the name, so a line that is
 *  just a name still works. Written for pasting a team sheet off a phone
 *  screenshot, which is how these arrive. */
/** The minute a substitution already says this starter came off, or "".
 *
 *  Shown as the placeholder on their Off' box so the reporter can see the
 *  derivation has happened and does not type it a second time. save_nt_lineup
 *  works the same value out server-side — this is the same answer said early,
 *  not a second source of it. */
function offMinuteFor(sheet, row) {
  if (row.role !== "starting") return "";
  const sub = sheet.find((o) => o.role === "sub_on"
    && o.replaced_player === row.player_name && o.minute_on);
  return sub ? `${sub.minute_on}'` : "";
}

function parseTeamSheet(text) {
  const out = [];
  // A heading changes what follows, and holds until the next one. A team sheet
  // is written that way — "Subs:" then the bench — so reading it that way
  // means a whole sheet pastes in one go instead of being typed into
  // fourteen dropdowns afterwards.
  let role = "starting";

  (text || "").split("\n").forEach((line) => {
    let rest = line.trim();
    if (!rest) return;

    if (/:$/.test(rest)) {
      const head = rest.slice(0, -1).toLowerCase();
      if (/sub|bench|replac/.test(head)) role = "unused_sub";
      else if (/start|xi|line/.test(head)) role = "starting";
      return;                                  // a heading is not a player
    }

    let rowRole = role;
    let captain = false;
    let yellow = false, secondYellow = false, red = false;
    let minuteOn = "";
    let replaced = "";

    // Cards are bracketed rather than bare letters: a lone Y or R in a name is
    // far more likely to be an initial than a booking.
    rest = rest.replace(/\[([^\]]+)\]/g, (_, token) => {
      const t = token.trim().toUpperCase();
      if (t === "Y") yellow = true;
      else if (t === "YR" || t === "Y2" || t === "2Y") secondYellow = true;
      else if (t === "R") red = true;
      else if (t === "C") captain = true;
      return " ";
    });

    // "(C)" is how a captain is written on every team sheet.
    rest = rest.replace(/\((?:c|capt(?:ain)?)\)/i, () => { captain = true; return " "; });

    // "for Vanessa Chikupira" — naming who they replaced also says they came
    // on, so the role follows from it rather than having to be set as well.
    rest = rest.replace(/\bfor\s+(.+)$/i, (_, name) => {
      replaced = name.trim();
      rowRole = "sub_on";
      return " ";
    });

    // A minute, written 62' or 62" or "on 62".
    rest = rest.replace(/\b(?:on\s+)?(\d{1,3}(?:\+\d{1,2})?)\s*['’"]/, (_, m) => {
      minuteOn = m; rowRole = "sub_on"; return " ";
    });

    let shirt = "";
    const lead = /^(\d{1,2})[.)]?\s+/.exec(rest);
    if (lead) { shirt = lead[1]; rest = rest.slice(lead[0].length); }

    let position = "";
    const tail = /\s+(GK|DF|MF|FW)\b/i.exec(rest);
    if (tail) { position = tail[1].toUpperCase(); rest = rest.replace(tail[0], " "); }

    rest = rest.replace(/\s+/g, " ").trim();
    if (!rest) return;

    out.push({
      player_name: rest, shirt_number: shirt, position,
      role: rowRole, captain, player_id: "",
      minute_on: minuteOn, minute_off: "", replaced_player: replaced,
      yellow_card: yellow, yellow_red_card: secondYellow, red_card: red,
    });
  });
  return out;
}

// ── Competitions: groups, brackets and squads ────────────────────────────────

async function renderNTCompetitions() {
  h('<div class="rp-loading"><span class="rp-spinner"></span><p>Loading competitions…</p></div>');
  const [{ data: rows, error }, teams] = await Promise.all([
    supabase.from("nt_competitions").select("competition_name,group_name,team_code"),
    ntTeams().catch(() => []),
  ]);
  if (error) {
    h('<p class="rp-empty">Could not load competitions.</p>');
    flash(humanError(error), "error");
    return;
  }
  const names = [...new Set((rows || []).map((r) => r.competition_name))].sort();
  h(`<a class="rp-btn is-quiet" href="#/nt">&larr; National teams</a>
     <h1 class="rp-login-head">International competitions</h1>
     <p class="rp-login-sub">Group tables, brackets and squads.</p>
     ${teams.length ? '<a class="rp-btn is-ghost" href="#/nt/comp/new">＋ New competition</a>' : ""}
     ${names.length ? names.map((n) => `
       <article class="rp-card">
         <div class="rp-team">${esc(n)}</div>
         <a class="rp-btn is-ghost" href="#/nt/c/${encodeURIComponent(n)}">Open</a>
       </article>`).join("")
       : '<p class="rp-empty">No competitions yet.</p>'}`);
}

async function renderNTNewCompetition() {
  const teams = await ntTeams().catch(() => []);
  if (!teams.length) { location.hash = "#/nt"; return; }
  h(`<a class="rp-btn is-quiet" href="#/nt/comps">&larr; Competitions</a>
     <h1 class="rp-login-head">New competition</h1>
     <p class="rp-login-sub">A competition exists once one of our teams has a
       row in it — that group table is what gives it a page, and what a bracket
       hangs off.</p>
     <form class="rp-form" data-nt-newcomp>
       <label class="rp-label" for="nc-name">Name</label>
       <input class="rp-input" id="nc-name" name="name" required
              placeholder="2027 Women's Africa Cup of Nations" autocapitalize="words">
       <label class="rp-label" for="nc-team">Our team</label>
       <select class="rp-select" id="nc-team" name="team">
         ${teams.map((t) => `<option value="${esc(t.team_code)}">${esc(t.team_name)}</option>`).join("")}
       </select>
       <label class="rp-label" for="nc-group">Group</label>
       <input class="rp-input" id="nc-group" name="group" placeholder="Group A">
       <p class="rp-hint">Optional — leave blank for a competition with no
         group stage.</p>
       <label class="rp-label" for="nc-wiki">Wikipedia link</label>
       <input class="rp-input" id="nc-wiki" name="wiki" type="url"
              inputmode="url" autocapitalize="off" spellcheck="false">
       <p class="rp-hint">Optional.</p>
       <button class="rp-btn" type="submit" data-submit>Create competition</button>
     </form>`);

  const form = view.querySelector("[data-nt-newcomp]");
  const button = form.querySelector("[data-submit]");
  let busy = false;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    clearFlash();
    busy = true; button.disabled = true; button.textContent = "Creating…";
    const { error } = await supabase.rpc("create_nt_competition", {
      p_competition_name: form.name.value,
      p_team_code: form.team.value,
      p_group_name: form.group.value,
      p_wikipedia_url: form.wiki.value,
    });
    busy = false; button.disabled = false; button.textContent = "Create competition";
    if (error) { flash(humanError(error), "error"); return; }
    invalidateReference();
    flash("Competition created.", "ok");
    location.hash = `#/nt/c/${encodeURIComponent(form.name.value.trim())}`;
  });
}

async function renderNTCompetition(name) {
  h('<div class="rp-loading"><span class="rp-spinner"></span><p>Loading…</p></div>');
  const [{ data: rows, error }, { data: ties }, { data: squads }, teams] =
    await Promise.all([
      supabase.from("nt_competitions").select("*").eq("competition_name", name),
      supabase.from("nt_knockout").select("*").eq("competition_name", name)
        .order("ord"),
      supabase.from("nt_squads").select("*").eq("competition", name).order("ord"),
      ntTeams().catch(() => []),
    ]);
  if (error) {
    h('<p class="rp-empty">Could not load this competition.</p>');
    flash(humanError(error), "error");
    return;
  }
  const groupRows = rows || [];
  if (!groupRows.length) {
    h(`<p class="rp-empty">No competition by that name.</p>
       <a class="rp-btn is-ghost" href="#/nt/comps">Back to competitions</a>`);
    return;
  }
  const state = { name, groupRows, ties: ties || [], squads: squads || [],
                  teams, open: {} };
  drawNTCompetition(state);
}

function drawNTCompetition(state) {
  captureDetailState(state);
  const open = state.open || {};
  const ourCodes = new Set(state.teams.map((t) => t.team_code));

  const byGroup = {};
  state.groupRows.forEach((r) => {
    (byGroup[r.group_name] = byGroup[r.group_name] || []).push(r);
  });

  const groupTables = Object.entries(byGroup).map(([group, rows]) => `
    <h3 class="rp-field-head">${esc(group || "Table")}</h3>
    <ul class="rp-list">${rows
      .slice().sort((a, b) => (a.position || "99").localeCompare(b.position || "99"))
      .map((r) => `
        <li><span>${esc(r.position ? r.position + ". " : "")}${esc(r.team_name || r.team_code)}
          <em>P${esc(r.played || "0")} · ${esc(r.points || "0")} pts${
            ourCodes.has(r.team_code) ? " · ours" : ""}</em></span>
          <button class="rp-x" type="button" data-del-group="${esc(r.team_code)}"
                  aria-label="Remove ${esc(r.team_name || r.team_code)}">&times;</button></li>`)
      .join("")}</ul>`).join("");

  const tieList = state.ties.map((t) => `
    <li><span>${esc(ntStageLabel(t.stage))} ${t.slot ? "· slot " + esc(t.slot) : ""}
      <em>${esc(t.home_name || t.home_from || "—")} v ${esc(t.away_name || t.away_from || "—")}
        ${t.nt_match_id ? " · from match " + esc(t.nt_match_id)
          : (t.home_score != null ? ` · ${esc(t.home_score)}–${esc(t.away_score)}` : "")}</em></span>
      <button class="rp-x" type="button" data-del-tie="${esc(t.tie_id)}"
              aria-label="Remove tie">&times;</button></li>`).join("");

  const squadIds = [...new Set(state.squads.map((s) => s.squad_id))];

  h(`
    <a class="rp-btn is-quiet" href="#/nt/comps" style="margin-top:0">&larr; Competitions</a>
    <h1 class="rp-login-head">${esc(state.name)}</h1>

    ${section("ntgroup", "Group table", state.groupRows.length, `
      ${groupTables}
      <h3 class="rp-field-head">Add or update a row</h3>
      <form data-group-form autocomplete="off">
        <input class="rp-input" name="team_code" placeholder="Team code (NIGERIA_W)"
               required autocapitalize="characters">
        <input class="rp-input" name="team_name" placeholder="Team name (Nigeria)"
               autocapitalize="words">
        <p class="rp-hint">One of our own teams takes its name automatically;
          anyone else needs one here, or the row renders nameless.</p>
        <input class="rp-input" name="group_name" placeholder="Group A">
        <div class="rp-row">
          <input class="rp-input" name="position" placeholder="Pos" inputmode="numeric">
          <input class="rp-input" name="played" placeholder="P" inputmode="numeric">
          <input class="rp-input" name="points" placeholder="Pts" inputmode="numeric">
        </div>
        <div class="rp-row">
          <input class="rp-input" name="won" placeholder="W" inputmode="numeric">
          <input class="rp-input" name="drawn" placeholder="D" inputmode="numeric">
          <input class="rp-input" name="lost" placeholder="L" inputmode="numeric">
        </div>
        <div class="rp-row">
          <input class="rp-input" name="goals_for" placeholder="GF" inputmode="numeric">
          <input class="rp-input" name="goals_against" placeholder="GA" inputmode="numeric">
        </div>
        <button class="rp-btn is-ghost" type="submit">Save row</button>
      </form>`, open.ntgroup)}

    ${section("ntbracket", "Knockout bracket", state.ties.length, `
      <ul class="rp-list">${tieList}</ul>
      <h3 class="rp-field-head">Add or update a tie</h3>
      <form data-tie-form autocomplete="off">
        <div class="rp-row">
          <select class="rp-input" name="stage">
            ${NT_STAGES.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}
          </select>
          <input class="rp-input" name="slot" placeholder="Slot" inputmode="numeric" value="1">
        </div>
        <input class="rp-input" name="home_name" placeholder="Home team"
               autocapitalize="words">
        <input class="rp-input" name="away_name" placeholder="Away team"
               autocapitalize="words">
        <div class="rp-row">
          <input class="rp-input" name="home_score" placeholder="Home" inputmode="numeric">
          <input class="rp-input" name="away_score" placeholder="Away" inputmode="numeric">
        </div>
        <label class="rp-inline" style="margin-top:8px">
          <input type="checkbox" name="extra_time"><span>Went to extra time</span></label>
        <div class="rp-row">
          <input class="rp-input" name="home_pens" placeholder="Home pens" inputmode="numeric">
          <input class="rp-input" name="away_pens" placeholder="Away pens" inputmode="numeric">
        </div>
        <p class="rp-hint">Penalties only if it went to a shoot-out. The score
          above stays the score after extra time, as it is recorded everywhere
          else — 1&ndash;1, won 3&ndash;2 on penalties.</p>
        <input class="rp-input" name="nt_match_id"
               placeholder="Our match id (leave blank if we are not in it)"
               inputmode="numeric">
        <p class="rp-hint">Give the match id for a tie we play, and the score
          comes from that match instead — entered once, where every other
          result is entered.</p>
        <div class="rp-row">
          <input class="rp-input" name="date" type="date">
          <input class="rp-input" name="venue" placeholder="Venue">
        </div>
        <button class="rp-btn is-ghost" type="submit">Save tie</button>
      </form>`, open.ntbracket)}

    ${section("ntsquad", "Squad", state.squads.length, `
      ${squadIds.map((id) => {
        const players = state.squads.filter((s) => s.squad_id === id);
        return `<h3 class="rp-field-head">${esc(players[0].announcement_date
          || "Squad " + id)}${players[0].coach ? " · " + esc(players[0].coach) : ""}</h3>
          <ul class="rp-list">${players.map((p) => `
            <li><span>${esc(p.shirt_number ? p.shirt_number + ". " : "")}${esc(p.player_name)}
              <em>${esc(p.position || "")}${p.club ? " · " + esc(p.club) : ""}</em></span></li>`).join("")}</ul>`;
      }).join("")}
      <h3 class="rp-field-head">Publish a squad</h3>
      <form data-squad-form autocomplete="off">
        <select class="rp-select" name="team">
          ${state.teams.map((t) => `<option value="${esc(t.team_code)}">${esc(t.team_name)}</option>`).join("")}
        </select>
        <div class="rp-row">
          <input class="rp-input" name="announced" type="date">
          <input class="rp-input" name="coach" placeholder="Coach" autocapitalize="words">
        </div>
        <textarea class="rp-input rp-textarea" name="players" rows="8"
                  placeholder="1 Mercy Sikelo GK&#10;10 Tabitha Chawinga FW&#10;…"></textarea>
        <p class="rp-hint">One per line, same shape as a team sheet. Saving
          replaces the squad it belongs to rather than adding a second.</p>
        <button class="rp-btn is-ghost" type="submit">Publish squad</button>
      </form>`, open.ntsquad)}
  `);

  wireNTCompetition(state);
}

function wireNTCompetition(state) {
  const done = async (message) => {
    flash(message, "ok");
    requestRebuild();
    await renderNTCompetition(state.name);
  };

  const lock = async (button, label, fn) => {
    if (button.disabled) return;
    clearFlash();
    const original = button.textContent;
    button.disabled = true; button.textContent = label;
    const error = await fn();
    button.disabled = false; button.textContent = original;
    if (error) flash(humanError(error), "error");
    return !error;
  };

  view.querySelector("[data-group-form]")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    lock(f.querySelector("button"), "Saving…", async () => {
      const { error } = await supabase.rpc("upsert_nt_group_row", {
        p_competition_name: state.name,
        p_group_name: f.group_name.value,
        p_team_code: f.team_code.value.trim(),
        p_team_name: f.team_name.value,
        p_position: f.position.value, p_played: f.played.value,
        p_won: f.won.value, p_drawn: f.drawn.value, p_lost: f.lost.value,
        p_goals_for: f.goals_for.value, p_goals_against: f.goals_against.value,
        p_points: f.points.value,
      });
      return error;
    }).then((ok) => ok && done("Row saved."));
  });

  view.querySelector("[data-tie-form]")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    const num = (v) => v.trim() === "" ? null : Number(v);
    lock(f.querySelector("button"), "Saving…", async () => {
      const { error } = await supabase.rpc("upsert_nt_tie", {
        p_competition_name: state.name,
        p_stage: f.stage.value,
        p_slot: Number(f.slot.value || 1),
        p_home_name: f.home_name.value, p_away_name: f.away_name.value,
        p_home_score: num(f.home_score.value), p_away_score: num(f.away_score.value),
        p_home_pens: num(f.home_pens.value), p_away_pens: num(f.away_pens.value),
        p_extra_time: f.extra_time.checked,
        p_date: f.date.value, p_venue: f.venue.value,
        p_status: num(f.home_score.value) == null ? "scheduled" : "played",
        p_nt_match_id: f.nt_match_id.value.trim(),
      });
      return error;
    }).then((ok) => ok && done("Tie saved."));
  });

  view.querySelector("[data-squad-form]")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    const players = parseTeamSheet(f.players.value).map((p) => ({
      player_name: p.player_name, shirt_number: p.shirt_number,
      position: p.position,
    }));
    if (!players.length) { flash("Add at least one player.", "warn"); return; }
    lock(f.querySelector("button"), "Publishing…", async () => {
      const { error } = await supabase.rpc("save_nt_squad", {
        p_team_id: f.team.value,
        p_competition: state.name,
        p_players: players,
        p_announcement_date: f.announced.value,
        p_coach: f.coach.value,
      });
      return error;
    }).then((ok) => ok && done("Squad published."));
  });

  view.addEventListener("click", async (e) => {
    const group = e.target.closest("[data-del-group]");
    const tie = e.target.closest("[data-del-tie]");
    if (group) {
      await lock(group, "…", async () =>
        (await supabase.rpc("delete_nt_group_row", {
          p_competition_name: state.name,
          p_team_code: group.dataset.delGroup,
        })).error).then((ok) => ok && done("Row removed."));
    } else if (tie) {
      await lock(tie, "…", async () =>
        (await supabase.rpc("delete_nt_tie", {
          p_tie_id: tie.dataset.delTie,
        })).error).then((ok) => ok && done("Tie removed."));
    }
  });
}

// ── Operations: coverage and data backlog ────────────────────────────────────
// Administrators only. Ten questions, one screen: which competitions have
// incomplete past rounds, what is due next, what is overdue, and what is
// missing from what has already been published.
//
// It is a READ-ONLY lens. Nothing here writes football data — every row links
// back to its existing #/m/<public_id> screen, which owns the write path. That
// keeps one way into public.matches rather than two.
//
// The counts come from the ops_* views (0016), which carry the is_admin()
// predicate in their own bodies. The gate below is a courtesy so an ordinary
// reporter gets a sentence instead of an empty screen; it is not the boundary.

const OPS_TABS = [
  { key: "results",      label: "Results",      flag: "is_overdue",
    head: "Overdue results", blank: "Every past fixture has a result." },
  { key: "fixtures",     label: "Fixtures",     flag: null,
    head: "Rounds and fixture gaps", blank: "Every round is the right size." },
  { key: "scorers",      label: "Scorers",      flag: "missing_scorers",
    head: "Missing scorers", blank: "Every goal is accounted for." },
  { key: "venues",       label: "Venues",       flag: "missing_venue",
    head: "Missing venues", blank: "Every fixture has a ground." },
  { key: "sources",      label: "Sources",      flag: "missing_source",
    head: "Missing sources", blank: "Every result cites a source." },
  { key: "verification", label: "Verification", flag: "is_unconfirmed",
    head: "Unconfirmed results", blank: "Nothing is waiting on confirmation." },
  { key: "crests",       label: "Crests",       flag: null,
    head: "Clubs without a crest", blank: "Every club has a crest." },
];

const opsTab = (key) => OPS_TABS.find((t) => t.key === key) || null;

// The manifest build.py writes into the site root. It answers the two questions
// Postgres cannot: when this site last read the database, and which clubs have
// no crest file — crests live in static/logos/, not in any table.
//
// Same-origin (/report/ and /build-info.json are the same site), so no CORS and
// no credential. no-store because a cached copy would report a stale build as
// current, which is the one thing this file exists to detect.
function loadBuildInfo() {
  return once("build-info", async () => {
    try {
      const res = await fetch("../build-info.json", { cache: "no-store" });
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null;                 // local build, or offline: reported as unknown
    }
  });
}

async function loadOpsSummary() {
  const [{ data: totals, error: tErr }, { data: comps, error: cErr }] =
    await Promise.all([
      supabase.from("ops_dashboard_totals").select("*").limit(1),
      supabase.from("ops_competition_summary").select("*"),
    ]);
  if (tErr) throw tErr;
  if (cErr) throw cErr;
  // tier ascending, unranked last, then by name — the order an administrator
  // reads them in, not the order Postgres returns them.
  const rows = (comps || []).slice().sort((a, b) =>
    (a.competition_tier ?? 99) - (b.competition_tier ?? 99)
    || a.competition_name.localeCompare(b.competition_name));
  return { totals: (totals || [])[0] || null, comps: rows };
}

/** Site freshness: is everything saved actually on everyleague.co? */
async function loadOpsFreshness() {
  const info = await loadBuildInfo();
  let state = null;
  try {
    const { data } = await supabase.rpc("ops_rebuild_status", {
      p_since: info?.data_read_at || null,
    });
    state = (data || [])[0] || null;
  } catch {
    state = null;
  }
  if (!info || !state) return { info, state, verdict: "unknown", minutes: null };

  const read = new Date(info.data_read_at).getTime();
  const newest = state.newest_match_update
    ? new Date(state.newest_match_update).getTime() : 0;
  // How far the site is behind the database, in minutes. Negative means the
  // build read after the last change — i.e. the site is current.
  const behind = newest > read ? Math.round((newest - read) / 60000) : 0;
  const pendingFor = state.pending && state.state_updated_at
    ? Math.round((Date.now() - new Date(state.state_updated_at).getTime()) / 60000)
    : 0;
  const verdict = (behind > 30 || pendingFor > 15) ? "stale" : "ok";
  return { info, state, verdict, minutes: behind, pendingFor,
           since: state.matches_since || 0 };
}

// ── The urgent strip ─────────────────────────────────────────────────────────
// Only non-zero items appear. A clean day should say so in one line rather than
// showing seven zeros, which trains an administrator to stop reading it.

function opsUrgent(totals, fresh) {
  const items = [];
  const add = (n, label, tab) => {
    if (n > 0) items.push(
      `<a class="ops-urgent-item" href="#/ops?tab=${tab}">
         <span class="ops-urgent-n">${n}</span>
         <span class="ops-urgent-l">${esc(label)}</span></a>`);
  };
  add(totals.overdue, "overdue results", "results");
  add(totals.incomplete_rounds, "incomplete past rounds", "fixtures");
  add(totals.unscheduled, "fixtures with no date", "fixtures");
  add(totals.competitions_without_fixtures, "leagues with nothing upcoming", "fixtures");
  add(totals.awaiting_reschedule, "awaiting a new date", "fixtures");

  const site = fresh.verdict === "ok"
    ? `<a class="ops-urgent-item is-ok"><span class="ops-urgent-n">✓</span>
         <span class="ops-urgent-l">site up to date</span></a>`
    : fresh.verdict === "stale"
      ? `<a class="ops-urgent-item is-bad" href="#/ops?tab=site">
           <span class="ops-urgent-n">${fresh.minutes}m</span>
           <span class="ops-urgent-l">site behind</span></a>`
      : `<a class="ops-urgent-item" href="#/ops?tab=site">
           <span class="ops-urgent-n">?</span>
           <span class="ops-urgent-l">site status unknown</span></a>`;

  if (!items.length) {
    return `<div class="ops-urgent">
      <a class="ops-urgent-item is-ok"><span class="ops-urgent-n">✓</span>
        <span class="ops-urgent-l">nothing urgent</span></a>${site}</div>`;
  }
  return `<div class="ops-urgent">${items.join("")}${site}</div>`;
}

// ── At-a-glance: one row per competition ─────────────────────────────────────
// The question this screen exists to answer, and for now the only one: is
// every competition caught up? Two things go stale — a played match with no
// result, and a next round nobody has entered yet — so each row shows exactly
// those two, unmissably green or red. Everything else the dashboard tracks
// (scorers, venues, sources, verification) still has its own tab; it just
// doesn't compete for attention here.

function opsResultsChip(r) {
  if (!r.overdue) {
    return `<span class="ops-glance-chip is-ok">✓ results up to date</span>`;
  }
  const n = r.overdue;
  return `<a class="ops-glance-chip is-bad"
             href="#/ops?tab=results&comp=${encodeURIComponent(r.competition_id)}">
             ⚠ ${n} result${n === 1 ? "" : "s"} missing</a>`;
}

function opsFixturesChip(r) {
  const href = `#/ops?tab=fixtures&comp=${encodeURIComponent(r.competition_id)}`;
  if (r.next_round_state === "none") {
    return `<a class="ops-glance-chip is-bad" href="${href}">⚠ next matchday not added</a>`;
  }
  const label = r.next_round_label || "next round";
  if (r.next_round_state === "undated") {
    return `<a class="ops-glance-chip is-warn" href="${href}">
               ◐ ${esc(label)} added, no date yet</a>`;
  }
  return `<span class="ops-glance-chip is-ok">
             ✓ ${esc(label)} added · ${esc(formatDate(r.next_date))}</span>`;
}

function opsGlance(comps) {
  if (!comps.length) return '<p class="rp-empty">No active competitions this season.</p>';
  const rows = comps.map((r) => {
    const attention = r.overdue > 0 || r.next_round_state !== "dated";
    return `
      <div class="ops-glance-row${attention ? " is-attn" : " is-good"}">
        <div class="ops-glance-comp">
          <span class="ops-comp">${esc(r.competition_name)}</span>
          <span class="ops-sub">${esc(r.competition_id)} · ${r.matches_total} fixtures</span>
        </div>
        <div class="ops-glance-status">
          ${opsResultsChip(r)}
          ${opsFixturesChip(r)}
        </div>
      </div>`;
  }).join("");
  return `<div class="ops-glance">${rows}</div>`;
}

// ── Backlog rows ─────────────────────────────────────────────────────────────

function opsMatchRow(m, names) {
  const bits = [formatDate(m.date), formatKickoff(m.kickoff)].filter(Boolean);
  if (m.round_key) bits.push(m.competition_type === "cup" ? m.round_key : `md${m.round_key}`);
  const score = (m.home_goals != null && m.away_goals != null)
    ? `${m.home_goals}–${m.away_goals}` : "";
  return `
    <a class="ops-row" href="#/m/${esc(m.public_id)}?from=${encodeURIComponent("ops")}">
      <span class="ops-row-teams">${esc(m.home_name)} <span class="ops-row-v">v</span> ${esc(m.away_name)}</span>
      <span class="ops-row-score">${esc(score)}</span>
      <span class="ops-row-meta">${esc(names[m.competition_id] || m.competition_id)}
        · ${esc(bits.join(" · "))}${m.is_pre_tracker ? ' · <span class="ops-tag">imported</span>' : ""}</span>
    </a>`;
}

async function opsBacklog(tab, comp, showAll) {
  let q = supabase.from("ops_match_flags")
    .select("match_id,public_id,competition_id,competition_type,round_key,date," +
            "kickoff,status,home_name,away_name,home_goals,away_goals," +
            "source_type,is_pre_tracker")
    .is(tab.flag, true)
    .order("date", { ascending: true, nullsFirst: false })
    .limit(400);
  if (comp) q = q.eq("competition_id", comp);
  const { data: all, error } = await q;
  if (error) throw error;
  const rows = showAll ? (all || []) : (all || []).filter((m) => !m.is_pre_tracker);
  return { rows, hidden: (all || []).length - rows.length };
}

// ── The Fixtures tab: rounds, their size, and what is missing ────────────────
// This is the working surface for decision 1 — a matchday is a logical round,
// so a round holding the wrong number of fixtures means some are filed under
// the wrong matchday. The reconciliation line says which of the two problems
// it is: an exact multiple means nothing is missing and it is purely
// mislabelling; a remainder means fixtures were never entered.

async function opsRounds(comp) {
  let q = supabase.from("ops_matchday_status").select("*")
    .order("competition_id").order("round_sort");
  if (comp) q = q.eq("competition_id", comp);
  const { data, error } = await q;
  if (error) throw error;
  return data || [];
}

function opsRoundsPanel(rounds, comps) {
  const byComp = {};
  rounds.forEach((r) => { (byComp[r.competition_id] ||= []).push(r); });

  return comps.map((c) => {
    const list = byComp[c.competition_id] || [];
    const faults = list.filter((r) => r.size_delta || r.round_key === null
                                   || r.is_incomplete);
    const reconcile = c.expected_per_round == null ? ""
      : c.fixtures_spare === 0
        ? `<span class="ops-ok">${c.whole_rounds} whole rounds — nothing missing,
             so any fault below is a fixture filed under the wrong matchday.</span>`
        : `<span class="ops-warn">${c.whole_rounds} whole rounds + ${c.fixtures_spare}
             spare — fixtures are genuinely missing.</span>`;

    const items = faults.map((r) => {
      const delta = r.size_delta;
      const tag = r.round_key === null
        ? `<span class="ops-tag is-bad">no matchday</span>`
        : delta > 0 ? `<span class="ops-tag is-bad">+${delta} too many</span>`
        : delta < 0 ? `<span class="ops-tag is-warn">${delta} short</span>` : "";
      const late = r.is_incomplete
        ? `<span class="ops-tag is-warn">${r.awaiting_result} awaiting a result</span>` : "";
      return `<li><a href="#/ops?tab=round&comp=${encodeURIComponent(c.competition_id)}&round=${encodeURIComponent(r.round_key ?? "")}">
                <strong>${esc(r.round_label)}</strong></a>
              <span class="ops-sub">${r.entered}${r.expected != null ? ` of ${r.expected}` : ""}
                · ${esc(r.first_date ? formatDate(r.first_date) : "no dates")}</span>
              ${tag}${late}</li>`;
    }).join("");

    return section(`ops-${c.competition_id}`,
      c.competition_name, faults.length,
      `<p class="ops-sub ops-recon">${reconcile}</p>`
      + (items ? `<ul class="ops-list">${items}</ul>`
               : `<p class="rp-empty">Every round is the right size.</p>`),
      Boolean(faults.length) && comps.length === 1);
  }).join("");
}

async function opsRoundFixtures(comp, roundKey) {
  let q = supabase.from("ops_match_flags")
    .select("match_id,public_id,competition_id,competition_type,round_key,date," +
            "kickoff,status,home_name,away_name,home_goals,away_goals,is_pre_tracker")
    .eq("competition_id", comp).order("date", { nullsFirst: false });
  q = roundKey ? q.eq("round_key", roundKey) : q.is("round_key", null);
  const { data, error } = await q;
  if (error) throw error;
  return data || [];
}

// ── The Crests tab ───────────────────────────────────────────────────────────
// The one list that is not matches, and the one the database cannot answer:
// a crest is a FILE in static/logos/clubs/, and clubs.crest disagrees with what
// is actually on disk. build.py resolves it the same way the site does and
// publishes the answer in build-info.json.

async function opsCrests() {
  const info = await loadBuildInfo();
  if (!info) return null;
  const ids = info.clubs_missing_crest || [];
  if (!ids.length) return { rows: [], total: info.counts?.clubs_with_page || 0 };
  // Names and tier, so the clubs a reader is most likely to see come first.
  const { data: clubs } = await supabase.from("clubs")
    .select("club_id,name").in("club_id", ids.slice(0, 300));
  const { data: teams } = await supabase.from("teams")
    .select("team_id,club_id").in("club_id", ids.slice(0, 300));
  const teamIds = (teams || []).map((t) => t.team_id);
  const { data: entries } = await supabase.from("entries")
    .select("team_id,competition_id").in("team_id", teamIds.slice(0, 400));
  const { data: comps } = await supabase.from("competitions")
    .select("competition_id,name,tier");

  const tierOf = {};
  (comps || []).forEach((c) => { tierOf[c.competition_id] = c.tier ?? 99; });
  const clubOfTeam = {};
  (teams || []).forEach((t) => { clubOfTeam[t.team_id] = t.club_id; });
  const bestTier = {};
  (entries || []).forEach((e) => {
    const club = clubOfTeam[e.team_id];
    const tier = tierOf[e.competition_id] ?? 99;
    if (club && (bestTier[club] == null || tier < bestTier[club])) bestTier[club] = tier;
  });
  const nameOf = {};
  (clubs || []).forEach((c) => { nameOf[c.club_id] = c.name; });

  const rows = ids.map((id) => ({
    club_id: id, name: nameOf[id] || id, tier: bestTier[id] ?? 99,
  })).sort((a, b) => a.tier - b.tier || a.name.localeCompare(b.name));
  return { rows, total: info.counts?.clubs_with_page || 0 };
}

// ── Site freshness panel ─────────────────────────────────────────────────────

function opsSitePanel(fresh) {
  if (fresh.verdict === "unknown") {
    return `<p class="rp-empty">Cannot read <code>/build-info.json</code>.
      A local build writes it; the deployed site publishes it every rebuild.</p>`;
  }
  const { info, state } = fresh;
  const when = (iso) => (iso ? new Date(iso).toLocaleString("en-GB",
    { dateStyle: "medium", timeStyle: "short" }) : "—");
  const verdict = fresh.verdict === "ok"
    ? `<span class="ops-ok">Up to date</span>`
    : `<span class="ops-warn">${fresh.minutes} minutes behind</span>`;
  return `
    <div class="rp-account-row"><span>Status</span><span>${verdict}</span></div>
    <div class="rp-account-row"><span>Site built</span><span>${esc(when(info.built_at))}</span></div>
    <div class="rp-account-row"><span>Data read at</span><span>${esc(when(info.data_read_at))}</span></div>
    <div class="rp-account-row"><span>Commit</span><span>${esc(info.commit || "—")}</span></div>
    <div class="rp-account-row"><span>Newest saved change</span><span>${esc(when(state.newest_match_update))}</span></div>
    <div class="rp-account-row"><span>Changes since that build</span><span>${fresh.since}</span></div>
    <div class="rp-account-row"><span>Rebuild queued</span><span>${state.pending ? "yes" : "no"}</span></div>
    <div class="rp-account-row"><span>Last rebuild asked for</span><span>${esc(when(state.last_dispatched_at))}</span></div>
    <p class="rp-hint">This reports the last <em>successful</em> build. A build
      that fails validation publishes nothing, so a repeated failure shows here
      as growing staleness rather than an error.</p>`;
}

// ── The screen ───────────────────────────────────────────────────────────────

function opsTabBar(active, comp) {
  const q = comp ? `&comp=${encodeURIComponent(comp)}` : "";
  const tabs = [{ key: "", label: "Overview" }]
    .concat(OPS_TABS, [{ key: "site", label: "Site" }]);
  return `<nav class="ops-tabs">` + tabs.map((t) =>
    `<a class="ops-tab${t.key === active ? " is-on" : ""}"
        href="#/ops${t.key ? `?tab=${t.key}${q}` : ""}">${esc(t.label)}</a>`).join("")
    + `</nav>`;
}

async function renderOps(params) {
  if (!context.isAdmin) {
    h(`<a class="rp-btn is-quiet" href="#/">&larr; My matches</a>
       <p class="rp-empty">Only an administrator can open operations.</p>`);
    return;
  }

  // The overview compares eleven competitions across nine columns; 560px
  // cannot show that. Every other screen keeps the reading width.
  view.classList.add("is-wide");
  // A hash change does not reset the scroll position, so opening a tab from
  // halfway down the overview used to land halfway down the next screen with
  // its heading off-screen. Deliberately NOT done globally in route(): the
  // fixture list depends on keeping its place when a reporter returns from a
  // match they have just published.
  window.scrollTo(0, 0);

  const tab = params.get("tab") || "";
  const comp = params.get("comp") || "";
  const showAll = params.get("all") === "1";
  const round = params.get("round");

  h(`<div class="rp-loading"><span class="rp-spinner" aria-hidden="true"></span>
       <p>Loading operations…</p></div>`);

  try {
    const names = await competitionNames();
    const header = (body) => h(
      `<a class="rp-btn is-quiet" href="#/">&larr; My matches</a>
       <h1 class="rp-greeting">Operations</h1>
       ${opsTabBar(tab, comp)}${body}`);

    // A single round's fixtures, reached from the Fixtures tab.
    if (tab === "round") {
      const rows = await opsRoundFixtures(comp, round);
      header(`<a class="rp-btn is-ghost" href="#/ops?tab=fixtures&comp=${encodeURIComponent(comp)}">&larr; Rounds</a>
              <h2 class="rp-group-head">
                ${esc(names[comp] || comp)} · ${esc(round ? `md${round}` : "unassigned")}
                <span class="rp-count">${rows.length}</span></h2>
              ${rows.map((m) => opsMatchRow(m, names)).join("")
                || '<p class="rp-empty">No fixtures in this round.</p>'}`);
      return;
    }

    if (tab === "site") {
      header(opsSitePanel(await loadOpsFreshness()));
      return;
    }

    if (tab === "crests") {
      const data = await opsCrests();
      if (!data) {
        header(`<p class="rp-empty">Crest coverage comes from
          <code>/build-info.json</code>, which this build has not published.</p>`);
        return;
      }
      const rows = data.rows.map((c) => `
        <a class="ops-row" href="/clubs/${esc(c.club_id)}.html" target="_blank" rel="noopener">
          <span class="ops-row-teams">${esc(c.name)}</span>
          <span class="ops-row-meta">${esc(c.club_id)}${c.tier < 99 ? ` · tier ${c.tier}` : ""}</span>
        </a>`).join("");
      header(`<h2 class="rp-group-head">Clubs without a crest
                <span class="rp-count">${data.rows.length} of ${data.total}</span></h2>
              <p class="rp-hint">Highest tier first. A crest is a file in
                <code>static/logos/clubs/</code> named for the club id or a
                team's legacy code.</p>
              ${rows || '<p class="rp-empty">Every club has a crest.</p>'}`);
      return;
    }

    if (tab === "fixtures") {
      const [{ comps }, rounds] = await Promise.all([
        loadOpsSummary(), opsRounds(comp)]);
      const shown = comp ? comps.filter((c) => c.competition_id === comp) : comps;
      header(`<h2 class="rp-group-head">Rounds and fixture gaps</h2>
              ${opsRoundsPanel(rounds, shown)}`);
      return;
    }

    const meta = opsTab(tab);
    if (meta && meta.flag) {
      const { rows, hidden } = await opsBacklog(meta, comp, showAll);
      const toggle = hidden || showAll
        ? `<p class="rp-hint">${rows.length} outstanding${hidden ? ` · ${hidden} imported rows hidden` : ""}
             — <a href="#/ops?tab=${meta.key}${comp ? `&comp=${encodeURIComponent(comp)}` : ""}${showAll ? "" : "&all=1"}">
             ${showAll ? "hide the import" : "show everything"}</a></p>`
        : "";
      header(`<h2 class="rp-group-head">${esc(meta.head)}
                <span class="rp-count">${rows.length}</span></h2>
              ${toggle}
              ${rows.map((m) => opsMatchRow(m, names)).join("")
                || `<p class="rp-empty">${esc(meta.blank)}</p>`}`);
      return;
    }

    // Overview.
    const [{ totals, comps }, fresh] = await Promise.all([
      loadOpsSummary(), loadOpsFreshness()]);
    if (!totals) {
      header('<p class="rp-empty">No active competitions this season.</p>');
      return;
    }
    header(opsUrgent(totals, fresh) + opsGlance(comps));
  } catch (error) {
    h('<p class="rp-empty">Could not load operations.</p>');
    flash(humanError(error), "error");
  }
}

// ── Router ───────────────────────────────────────────────────────────────────

function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/";
  const [path, query] = raw.split("?");
  return { path, params: new URLSearchParams(query || "") };
}

async function route() {
  // An error stays put until the reporter does something (see flash), but
  // "something" includes leaving the screen: a failure from the last match
  // must not follow them onto the next one.
  clearFlash();
  // The scorer picker belongs to the screen being left.
  dismissPicker = null;
  // Only /ops widens the column (see main.is-wide). Cleared on every route so
  // leaving it cannot strand another screen at the wrong width.
  view.classList.remove("is-wide");
  const { path, params } = parseHash();
  const { data: { session } } = await supabase.auth.getSession();

  accountBtn.hidden = !session;
  if (session && context?.reporter) {
    accountBtn.textContent = firstName().slice(0, 1).toUpperCase();
  }

  if (!session) {
    // Remember where they were going so the WhatsApp link still lands on the
    // right match after signing in.
    const next = path.startsWith("/m/") ? `#${path}` : params.get("next");
    renderLogin(next);
    return;
  }

  if (!context) {
    try {
      context = await loadContext();
    } catch (error) {
      h('<p class="rp-empty">Could not load your account.</p>');
      flash(humanError(error), "error");
      return;
    }
    if (context.reporter) {
      accountBtn.textContent = firstName().slice(0, 1).toUpperCase();
    }
  }

  if (path.startsWith("/m/")) return renderMatch(path.slice(3), params);
  // National teams. A separate branch of the router for a separate schema —
  // see the section above for why they are not folded together.
  if (path === "/nt") return renderNTHome(params);
  if (path === "/nt/add") return renderNTAddFixture(params);
  if (path === "/nt/comps") return renderNTCompetitions();
  if (path === "/nt/comp/new") return renderNTNewCompetition();
  if (path.startsWith("/nt/m/")) return renderNTMatch(decodeURIComponent(path.slice(6)));
  if (path.startsWith("/nt/c/")) return renderNTCompetition(decodeURIComponent(path.slice(6)));
  if (path === "/ops") return renderOps(params);
  if (path === "/add") return renderAddFixture(params);
  if (path === "/league/new") return renderNewLeague();
  if (path === "/account") return renderAccount();
  if (path === "/players") return renderPlayers(params);
  if (path === "/trending") return renderTrending(params);
  if (path === "/reporters") return renderReporters();
  if (path === "/login") { location.hash = "#/"; return; }
  return renderHome(params);
}

// ── Boot ─────────────────────────────────────────────────────────────────────

function start() {
  if (!SUPABASE_URL || !SUPABASE_PUBLISHABLE_KEY) {
    h(`<p class="rp-empty">This build has no Supabase configuration.
         <code>build.py</code> writes <code>report/config.js</code> from
         SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY.</p>`);
    return;
  }
  supabase = createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, {
    auth: {
      // The point of persisting: a reporter opens a WhatsApp link days later
      // and goes straight to the match without signing in again.
      persistSession: true,
      autoRefreshToken: true,
      storageKey: "everyleague-reporter",
    },
  });

  accountBtn.addEventListener("click", () => { location.hash = "#/account"; });
  document.addEventListener("click", (event) => { dismissPicker?.(event); });
  window.addEventListener("hashchange", route);
  supabase.auth.onAuthStateChange((event) => {
    if (event === "SIGNED_OUT") { context = null; route(); }
  });
  // A reporter who walks back into signal should get a working page rather
  // than a stale error.
  window.addEventListener("online", () => { clearFlash(); route(); });

  route();
}

start();
