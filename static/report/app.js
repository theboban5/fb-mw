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
 *   #/add         add a fixture to a competition you cover
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
      || message.includes("gender must be")
      || message.includes("type must be")
      // ...and the scorer-identity rules from 0010, phrased the same way.
      || message.includes("name is too long")
      || message.includes("not in the database")
      || message.includes("already have a scorer")
      || message.includes("did not play in this match")
      || message.includes("publish the score before")
      || message.includes("needs a name")) {
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
// changed. The `show` and `date` filters are then applied locally: on the
// connection this app is written for, re-fetching a list the phone already
// holds just to hide half of it would be the slowest thing on the screen.
//
// The COMPETITION filter is different, and is part of the cache key. Played
// results are capped — there are hundreds and a phone should not download them
// all — and a cap applied before filtering would silently hide an older
// league's results behind sixty newer ones from everywhere else. So narrowing
// to a competition asks the database again, scoped to it.
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

async function loadHome(comp) {
  const key = comp || "*";
  if (homeCache && homeCache.key === key) return homeCache;

  let pending = supabase.from("matches").select(MATCH_FIELDS)
    .eq("status", "scheduled").order("date", { ascending: true });
  // Not played, but decided: these belong in the list too, or a postponed
  // match vanishes and looks like a fixture nobody ever entered.
  let other = supabase.from("matches").select(MATCH_FIELDS)
    .in("status", ["played", "awarded", "postponed", "abandoned", "cancelled"])
    .order("date", { ascending: false }).limit(RESULT_LIMIT);

  if (comp) {
    pending = pending.eq("competition_id", comp);
    other = other.eq("competition_id", comp);
  } else if (!context.isAdmin) {
    // An admin reports everywhere and has no assignments to narrow by.
    pending = pending.in("competition_id", context.competitions);
    other = other.in("competition_id", context.competitions);
  }

  const [pendingRes, otherRes, names] = await Promise.all([
    pending, other, competitionNames(),
  ]);
  if (pendingRes.error || otherRes.error) {
    throw pendingRes.error || otherRes.error;
  }
  homeCache = {
    key,
    matches: [...(pendingRes.data || []), ...(otherRes.data || [])],
    names,
  };
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

  if (!homeCache || homeCache.key !== (filters.comp || "*")) {
    h('<div class="rp-loading"><span class="rp-spinner"></span><p>Loading your matches…</p></div>');
  }

  let data, choices;
  try {
    // The dropdown's options come from what this account may report, NOT from
    // the matches on screen: deriving them from a list that is itself filtered
    // would leave the menu holding only the league already chosen, with no way
    // back to any other.
    [data, choices] = await Promise.all([
      loadHome(filters.comp), entryCompetitions(),
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
      ${canAdd ? '<a class="rp-btn is-ghost" href="#/add">＋ Add fixture</a>' : ""}
      ${context.isAdmin ? '<a class="rp-btn is-ghost" href="#/league/new">＋ New league</a>' : ""}
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

// ── Adding a fixture ─────────────────────────────────────────────────────────
// A result can only be reported against a fixture that exists, so a reporter
// who covers a league nobody has entered a fixture list for cannot do anything
// at all. This is that missing step.
//
// The form deliberately offers ONLY teams entered in the chosen competition
// this season. That is validate.py check 3, which fails the whole build if it
// is broken — so rather than let it be typed wrong and rejected, the wrong
// answer is not offered. create_fixture checks it again anyway; the client is
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

async function renderAddFixture(params) {
  h('<div class="rp-loading"><span class="rp-spinner"></span><p>Loading…</p></div>');

  let competitions, season;
  try {
    [competitions, season] = await Promise.all([entryCompetitions(), activeSeason()]);
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

  function drawAddFixture() {
    const compOptions = competitions.map((c) => `
      <option value="${esc(c.competition_id)}"${
        c.competition_id === state.competition.competition_id ? " selected" : ""}>
        ${esc(c.label)}</option>`).join("");

    const teamOptions = () => (state.teams || []).map((t) => `
      <option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");

    const isCup = state.competition.type === "cup";

    const body = state.teams === null
      ? '<div class="rp-loading"><span class="rp-spinner"></span><p>Loading teams…</p></div>'
      : state.teams.length < 2
        ? `<p class="rp-empty">${esc(state.competition.label)} has fewer than
             two teams entered for ${esc(season.label)}, so it cannot hold a
             fixture yet.</p>`
        : `
      <label class="rp-label" for="home-team">Home team</label>
      <select class="rp-select" id="home-team" name="home" required>
        <option value="">Choose…</option>${teamOptions()}</select>

      <label class="rp-label" for="away-team">Away team</label>
      <select class="rp-select" id="away-team" name="away" required>
        <option value="">Choose…</option>${teamOptions()}</select>

      <label class="rp-label" for="fx-date">Date</label>
      <input class="rp-input" id="fx-date" name="date" type="date">
      <p class="rp-hint">Leave blank if the day is not fixed yet.</p>

      <label class="rp-label" for="fx-kickoff">Kick-off</label>
      <input class="rp-input" id="fx-kickoff" name="kickoff" type="time"
             placeholder="15:00">
      <p class="rp-hint">Malawi time. Leave blank if not announced.</p>

      ${isCup ? `
        <label class="rp-label" for="fx-stage">Round</label>
        <select class="rp-select" id="fx-stage" name="stage" required>
          <option value="">Choose…</option>
          ${CUP_ROUNDS.map((r) => `<option value="${r.value}">${esc(r.label)}</option>`).join("")}
        </select>`
      : `
        <label class="rp-label" for="fx-matchday">Matchday</label>
        <input class="rp-input" id="fx-matchday" name="matchday" type="number"
               min="1" step="1" inputmode="numeric">
        <p class="rp-hint">Optional.</p>`}

      <button class="rp-btn" type="submit" data-submit>Add fixture</button>`;

    const addedList = state.added.length ? `
      <h2 class="rp-field-head">Added just now</h2>
      ${state.added.map((m) => `
        <article class="rp-card">
          <div class="rp-teams">
            <span class="rp-team">${esc(m.homeName)}</span><span></span>
            <span class="rp-team">${esc(m.awayName)}</span><span></span>
          </div>
          <p class="rp-card-meta">${esc(formatDate(m.date))}</p>
          <a class="rp-btn is-ghost" href="#/m/${esc(m.public_id)}">Report this match</a>
        </article>`).join("")}` : "";

    h(`<a class="rp-btn is-quiet" href="#/">&larr; My matches</a>
       <h1 class="rp-login-head">Add a fixture</h1>
       <p class="rp-login-sub">${esc(season.label)} season.</p>
       <form class="rp-form" data-fixture>
         <label class="rp-label" for="fx-comp">Competition</label>
         <select class="rp-select" id="fx-comp" name="competition">${compOptions}</select>
         ${body}
       </form>
       ${addedList}`);

    view.querySelector("#fx-comp").addEventListener("change", (event) => {
      state.competition = competitions.find(
        (c) => c.competition_id === event.target.value) || competitions[0];
      loadTeams();
    });

    const form = view.querySelector("[data-fixture]");
    const button = form.querySelector("[data-submit]");
    if (!button) return;

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (state.busy) return;                  // rule 2: never submit twice
      clearFlash();

      if (form.home.value && form.home.value === form.away.value) {
        flash("A team cannot play itself — pick two different teams.", "error");
        return;
      }

      state.busy = true;
      button.disabled = true;
      button.textContent = "Adding…";

      const payload = {
        p_competition_id: state.competition.competition_id,
        p_home_team_id: form.home.value,
        p_away_team_id: form.away.value,
        p_date: form.date.value || null,
        // <input type="time"> yields HH:MM, which is what the column's own
        // constraint accepts.
        p_kickoff: form.kickoff.value || "",
      };
      if (isCup) payload.p_stage = form.stage.value;
      else if (form.matchday.value) payload.p_matchday = Number(form.matchday.value);

      const { data, error } = await supabase.rpc("create_fixture", payload);

      state.busy = false;
      button.disabled = false;
      button.textContent = "Add fixture";

      if (error) {
        // Rule 1: the form is untouched, so nothing typed is lost.
        flash(humanError(error), "error");
        return;
      }

      const row = (data || [])[0];
      const teamName = (id) => state.teams.find((t) => t.id === id)?.name || id;
      state.added.unshift({
        public_id: row?.public_id,
        homeName: teamName(payload.p_home_team_id),
        awayName: teamName(payload.p_away_team_id),
        date: payload.p_date,
      });
      // The home screen is now out of date in a way the reporter can see.
      invalidateHome();
      flash("Fixture added.", "ok");
      // A fixture list is entered a matchday at a time, so the form comes back
      // empty and ready rather than navigating away after one.
      drawAddFixture();
    });
  }

  loadTeams();
}

// ── Creating a league ────────────────────────────────────────────────────────
// Admin only. One screen, because a competition with no teams cannot hold a
// fixture and is not a useful thing to have made — so the teams are part of
// creating it, not a second step.

const AGE_GROUPS = ["senior", "u20", "u19", "u17", "u16", "u15"];

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

  const [{ data: allowed }, names] = await Promise.all([
    supabase.rpc("can_report_match", { p_match_id: match.match_id }),
    competitionNames(),
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

    <h2 class="rp-field-head">Where is this from?</h2>
    <input class="rp-input" type="text" data-source maxlength="500"
           value="${esc(state.source)}"
           placeholder="Facebook link, or how you know"
           autocapitalize="sentences" autocorrect="off" spellcheck="false">
    <p class="rp-hint">Optional, and never shown publicly — it is there so a
      result can be checked later. A link, or plain words like
      &ldquo;told to me by the referee&rdquo;.</p>

    <div class="rp-publish">
      <button class="rp-btn" type="button" data-publish></button>
      <p class="rp-publish-note" data-note></p>
    </div>

    ${section("reschedule", "Change date", 0, `
      <p class="rp-hint" style="margin-top:0">The fixture list said one day and
        it was played on another? Change it here. This saves on its own — it is
        not part of publishing the score.</p>
      <label class="rp-label" for="rs-date">Date</label>
      <input class="rp-input" id="rs-date" type="date" data-rs-date
             value="${esc(match.date || "")}">
      <p class="rp-hint">Clear it if the match no longer has a fixed day.</p>
      <label class="rp-label" for="rs-kickoff">Kick-off</label>
      <input class="rp-input" id="rs-kickoff" type="time" data-rs-kickoff
             value="${esc((match.kickoff || "").slice(0, 5))}">
      <p class="rp-hint">Malawi time.</p>
      <button class="rp-btn is-ghost" type="button" data-rs-save>Save new date</button>
    `, state.open?.reschedule)}

    <div data-detail></div>
  `);

  drawDetail(match, state);
  wireReschedule(match, names, state);

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

  let busy = false;

  save.addEventListener("click", async () => {
    if (busy) return;                          // rule 2: never submit twice
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

    busy = true;
    save.disabled = true;
    save.textContent = "Saving…";

    const { data, error } = await supabase.rpc("reschedule_match", {
      p_match_id: match.match_id,
      p_date: date,
      p_kickoff: kickoff,
    });

    busy = false;
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

// ── Optional match detail ────────────────────────────────────────────────────
// Everything below the result is optional, and the app is built so a reporter
// can ignore all of it: score, status, publish, leave. These sections stay
// collapsed until asked for, so the screen a reporter sees in a hurry is the
// short one.
//
// Each section saves independently and immediately. Nothing here is part of
// the publish, so a failed photo upload cannot cost someone the result they
// already got in — the mistake the whole design is trying to avoid.

const CARD_TYPES = [
  ["yellow_card", "Yellow"],
  ["second_yellow", "Second yellow"],
  ["red_card", "Red"],
];

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

async function searchPlayers(term) {
  const q = term.trim().replace(FILTER_UNSAFE, " ").replace(/\s+/g, " ").trim();
  // One letter matches most of the database and is not a search.
  if (q.length < 2) return [];
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
    const [goalsRes, incidentsRes, lineupRes, mediaRes] = await Promise.all([
      supabase.from("goals").select("goal_id,team_id,reported_player_name,minute,player_id")
        .eq("match_id", match.match_id).order("ord"),
      supabase.from("match_incidents").select("*")
        .eq("match_id", match.match_id).order("incident_id"),
      supabase.from("lineup_entries").select("*")
        .eq("match_id", match.match_id).order("ord"),
      supabase.from("match_media").select("*")
        .eq("match_id", match.match_id).order("created_at"),
    ]);
    state.detail = {
      goals: goalsRes.data || [],
      incidents: incidentsRes.data || [],
      lineup: lineupRes.data || [],
      media: mediaRes.data || [],
    };
  }

  const { goals, incidents, lineup, media } = state.detail;
  const cards = incidents.filter((i) => i.incident_type !== "substitution");
  const subs = incidents.filter((i) => i.incident_type === "substitution");
  const scored = match.home_goals != null;

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
      <div class="rp-pick" data-pick>
        <input class="rp-input" name="player" placeholder="Scorer's name" required
               autocomplete="off" autocapitalize="words" role="combobox"
               aria-expanded="false" aria-autocomplete="list"
               aria-controls="rp-player-list">
        <input type="hidden" name="player_id" value="">
        <ul class="rp-suggest" id="rp-player-list" role="listbox" data-suggest hidden></ul>
      </div>
      <p class="rp-hint" data-pick-note>Start typing and pick the player, so the
        goal counts towards their record and the league's top scorers.</p>
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

  host.innerHTML = `
    <h2 class="rp-field-head">Match detail <span class="rp-optional">optional</span></h2>

    ${section("goals", "Goalscorers",
      goals.length + (state.pendingGoals || []).length,
      `<ul class="rp-list">${goalList}${stagedList}</ul>${goalForm}`, open.goals)}

    ${section("cards", "Cards", cards.length, `
      <ul class="rp-list">${cards.map((c) => `
        <li><span>${esc(c.player_name)}
          <em>${esc(teamName(c.team_id))} · ${esc(
            (CARD_TYPES.find((t) => t[0] === c.incident_type) || [, c.incident_type])[1])}
          ${c.minute ? " · " + esc(c.minute) + "'" : ""}</em></span>
          <button class="rp-x" type="button" data-del-incident="${c.incident_id}"
                  aria-label="Remove card">&times;</button></li>`).join("")}</ul>
      <form data-card-form>
        ${sideButtons(match, "card-team", pick["card-team"])}
        <input class="rp-input" name="player" placeholder="Player's name" required
               autocomplete="off" autocapitalize="words">
        <div class="rp-row">
          <select class="rp-input" name="type">
            ${CARD_TYPES.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}
          </select>
          <input class="rp-input" name="minute" placeholder="Min" inputmode="numeric">
        </div>
        <button class="rp-btn is-ghost" type="submit">Add card</button>
      </form>`, open.cards)}

    ${section("subs", "Substitutions", subs.length, `
      <ul class="rp-list">${subs.map((s) => `
        <li><span>${esc(s.player_name)} <em>for ${esc(s.secondary_player_name)} ·
          ${esc(teamName(s.team_id))}${s.minute ? " · " + esc(s.minute) + "'" : ""}</em></span>
          <button class="rp-x" type="button" data-del-incident="${s.incident_id}"
                  aria-label="Remove substitution">&times;</button></li>`).join("")}</ul>
      <form data-sub-form>
        ${sideButtons(match, "sub-team", pick["sub-team"])}
        <input class="rp-input" name="on" placeholder="Player coming on" required
               autocomplete="off" autocapitalize="words">
        <input class="rp-input" name="off" placeholder="Player going off" required
               autocomplete="off" autocapitalize="words">
        <input class="rp-input" name="minute" placeholder="Min" inputmode="numeric">
        <button class="rp-btn is-ghost" type="submit">Add substitution</button>
      </form>`, open.subs)}

    ${section("lineup", "Line-ups", lineup.length, `
      <p class="rp-hint">One name per line. Paste a whole team in at once —
        no need to type them into separate boxes.</p>
      <form data-lineup-form>
        ${sideButtons(match, "lineup-team", pick["lineup-team"])}
        <div class="rp-row">
          <label class="rp-inline"><input type="radio" name="role" value="starter" checked>
            <span>Starting XI</span></label>
          <label class="rp-inline"><input type="radio" name="role" value="substitute">
            <span>Substitutes</span></label>
        </div>
        <textarea class="rp-input rp-textarea" name="names" rows="6"
                  placeholder="Yamikani Phiri&#10;Chikondi Banda&#10;..."></textarea>
        <button class="rp-btn is-ghost" type="submit">Save line-up</button>
      </form>
      <ul class="rp-list">${lineup.map((l) => `
        <li><span>${esc(l.player_name)}
          <em>${esc(teamName(l.team_id))} · ${l.role === "starter" ? "XI" : "sub"}</em></span>
          <button class="rp-x" type="button" data-del-lineup="${l.id}"
                  aria-label="Remove ${esc(l.player_name)}">&times;</button></li>`).join("")}</ul>`, open.lineup)}

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
  `;

  wireDetail(match, state, goals);
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

/** The scorer combobox: search as you type, tap to identify, or add a player.
 *
 *  The hidden player_id field is the output. Everything else here exists to
 *  fill it in, and to be honest on screen about whether it is filled — a
 *  reporter should never have to guess whether the goal they just entered
 *  reached the scorer table. */
function wirePlayerPicker(host, match) {
  const wrap = host.querySelector("[data-pick]");
  if (!wrap) return;

  const input = wrap.querySelector('input[name="player"]');
  const hidden = wrap.querySelector('input[name="player_id"]');
  const list = wrap.querySelector("[data-suggest]");
  const note = host.querySelector("[data-pick-note]");
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
    setNote(`${name} is identified — this goal counts towards their record.`,
            "good");
  }

  function render(players, term, known) {
    const rows = players.map((p) => {
      const name = playerLabel(p);
      const teamId = known[p.player_id];
      const hint = teamId
        ? `has scored for ${teamId === match.home_team_id
            ? (match.home?.display_name || "the home team")
            : (match.away?.display_name || "the away team")}`
        : (p.known_as && p.full_name && p.known_as !== p.full_name
            ? p.full_name : "");
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

  // A tap anywhere else means "not that list". Handed to the single listener
  // installed in start() rather than adding one per redraw: the detail block
  // is rebuilt after every save, and a listener per rebuild would pile up
  // holding a detached form each time.
  dismissPicker = (event) => { if (!wrap.contains(event.target)) close(); };
}

function wireDetail(match, state, goals) {
  const host = view.querySelector("[data-detail]");
  const pick = (name) =>
    host.querySelector(`input[name="${name}"]:checked`)?.value;
  const reporterId = context.reporter?.reporter_id;
  const teamName = (id) => id === match.home_team_id
    ? (match.home?.display_name || id) : (match.away?.display_name || id);

  wirePlayerPicker(host, match);

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

  host.querySelector("[data-card-form]")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    detailAction(f.querySelector("button"), "Adding…", async () => {
      const { error } = await supabase.from("match_incidents").insert({
        match_id: match.match_id, team_id: pick("card-team"),
        incident_type: f.type.value, player_name: f.player.value.trim(),
        minute: f.minute.value.trim(), reported_by: reporterId,
      });
      return error;
    }, match, state);
  });

  host.querySelector("[data-sub-form]")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    detailAction(f.querySelector("button"), "Adding…", async () => {
      const { error } = await supabase.from("match_incidents").insert({
        match_id: match.match_id, team_id: pick("sub-team"),
        incident_type: "substitution",
        // Documented convention: player_name is ON, secondary is OFF.
        player_name: f.on.value.trim(),
        secondary_player_name: f.off.value.trim(),
        minute: f.minute.value.trim(), reported_by: reporterId,
      });
      return error;
    }, match, state);
  });

  host.querySelector("[data-lineup-form]")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    const team = pick("lineup-team");
    const role = host.querySelector('input[name="role"]:checked').value;
    const names = f.names.value.split("\n")
      .map((n) => n.trim()).filter(Boolean);
    if (!names.length) { flash("Add at least one name.", "warn"); return; }
    detailAction(f.querySelector("button"), "Saving…", async () => {
      // Replace this side's rows for this role rather than appending, so
      // fixing a typo means editing the list and saving again.
      await supabase.from("lineup_entries").delete()
        .eq("match_id", match.match_id).eq("team_id", team).eq("role", role);
      const { error } = await supabase.from("lineup_entries").insert(
        names.map((player_name, i) => ({
          match_id: match.match_id, team_id: team, player_name,
          role, ord: i + 1, reported_by: reporterId,
        })));
      if (!error) f.names.value = "";
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

  host.addEventListener("click", (e) => {
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
    const incident = e.target.closest("[data-del-incident]");
    const line = e.target.closest("[data-del-lineup]");
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
    } else if (incident) {
      detailAction(incident, "…", async () =>
        (await supabase.from("match_incidents").delete()
          .eq("incident_id", incident.dataset.delIncident)).error, match, state);
    } else if (line) {
      detailAction(line, "…", async () =>
        (await supabase.from("lineup_entries").delete()
          .eq("id", line.dataset.delLineup)).error, match, state);
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

async function signOut() {
  await supabase.auth.signOut();
  context = null;
  // The next person to sign in on this phone must not see the last one's
  // fixture list, or a competition menu built from their assignments.
  invalidateHome();
  invalidateReference();
  location.hash = "#/login";
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
  + "penalty_shootout,extra_time_result";

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

    <div class="rp-publish">
      <button class="rp-btn" type="button" data-nt-publish></button>
      <p class="rp-publish-note" data-nt-note></p>
    </div>

    <div data-nt-detail></div>
  `);

  drawNTDetail(match, state);

  const valueEls = {
    ours: view.querySelector('[data-nt-value="ours"]'),
    theirs: view.querySelector('[data-nt-value="theirs"]'),
  };
  const publishBtn = view.querySelector("[data-nt-publish]");
  const note = view.querySelector("[data-nt-note]");
  const etEl = view.querySelector("[data-nt-et]");
  const pensEl = view.querySelector("[data-nt-pens]");

  etEl.addEventListener("change", () => { state.extraTime = etEl.checked; });
  pensEl.addEventListener("input", () => { state.pens = pensEl.value; });

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

// ── Scorers and the team sheet ───────────────────────────────────────────────

async function drawNTDetail(match, state, { local = false } = {}) {
  const host = view.querySelector("[data-nt-detail]");
  if (!host) return;
  captureDetailState(state);
  const open = state.open || {};

  if (!local || !state.ntDetail) {
    const [goalsRes, lineRes] = await Promise.all([
      supabase.from("nt_goals")
        .select("goal_id,team_id,player_name,minute,goal_type")
        .eq("match_id", match.match_id).order("ord"),
      supabase.from("nt_lineups").select("*")
        .eq("match_id", match.match_id).order("ord"),
    ]);
    state.ntDetail = {
      goals: goalsRes.data || [],
      lineup: lineRes.data || [],
    };
  }
  const { goals, lineup } = state.ntDetail;
  // The team sheet is edited as a whole, so it lives in state once loaded and
  // is only re-read from the database after a save.
  if (!state.sheet) {
    state.sheet = lineup.filter((r) => r.team_id === match.team_code)
      .map((r) => ({ ...r }));
  }

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

  const sheetRows = state.sheet.map((r, i) => `
    <li class="rp-sheet-row" data-sheet-row="${i}">
      <div class="rp-sheet-head">
        <span class="rp-sheet-name">${r.shirt_number ? esc(r.shirt_number) + ". " : ""}${esc(r.player_name)}
          ${r.captain ? '<b title="Captain">(C)</b>' : ""}</span>
        <button class="rp-x" type="button" data-sheet-del="${i}"
                aria-label="Remove ${esc(r.player_name)}">&times;</button>
      </div>
      <div class="rp-sheet-controls">
        <select class="rp-input" data-sheet-field="role" data-sheet-i="${i}">
          ${NT_ROLES.map(([v, l]) => `<option value="${v}"${
            r.role === v ? " selected" : ""}>${l}</option>`).join("")}
        </select>
        <select class="rp-input" data-sheet-field="position" data-sheet-i="${i}">
          ${NT_POSITIONS.map((p) => `<option value="${p}"${
            r.position === p ? " selected" : ""}>${p || "—"}</option>`).join("")}
        </select>
        ${r.role === "sub_on" ? `
          <input class="rp-input" data-sheet-field="minute_on" data-sheet-i="${i}"
                 value="${esc(r.minute_on || "")}" placeholder="On'" inputmode="numeric">
          <select class="rp-input" data-sheet-field="replaced_player" data-sheet-i="${i}">
            <option value="">for…</option>
            ${state.sheet.filter((o, j) => j !== i).map((o) => `
              <option value="${esc(o.player_name)}"${
                r.replaced_player === o.player_name ? " selected" : ""}>
              ${esc(o.player_name)}</option>`).join("")}
          </select>` : ""}
      </div>
      <div class="rp-sheet-flags">
        <label class="rp-inline"><input type="checkbox" data-sheet-field="captain"
          data-sheet-i="${i}" ${r.captain ? "checked" : ""}><span>C</span></label>
        <label class="rp-inline"><input type="checkbox" data-sheet-field="yellow_card"
          data-sheet-i="${i}" ${r.yellow_card ? "checked" : ""}><span>Yellow</span></label>
        <label class="rp-inline"><input type="checkbox" data-sheet-field="yellow_red_card"
          data-sheet-i="${i}" ${r.yellow_red_card ? "checked" : ""}><span>2nd yellow</span></label>
        <label class="rp-inline"><input type="checkbox" data-sheet-field="red_card"
          data-sheet-i="${i}" ${r.red_card ? "checked" : ""}><span>Red</span></label>
      </div>
    </li>`).join("");

  const starters = state.sheet.filter((r) => r.role === "starting").length;

  host.innerHTML = `
    <h2 class="rp-field-head">Match detail <span class="rp-optional">optional</span></h2>

    ${section("ntgoals", "Goalscorers", goals.length,
      `<ul class="rp-list">${goalList}</ul>${goalForm}`, open.ntgoals)}

    ${section("ntsheet", "Team sheet", state.sheet.length, `
      <p class="rp-hint" style="margin-top:0">The XI, the bench, the changes and
        the cards are one sheet here — that is how they are stored, and how a
        team sheet reads. Paste the names in, then set each row.</p>
      <textarea class="rp-input rp-textarea" data-sheet-paste rows="5"
                placeholder="1 Mercy Sikelo GK&#10;4 Tabitha Chawinga FW&#10;…"></textarea>
      <p class="rp-hint">One per line. A leading number is the shirt, a
        trailing GK/DF/MF/FW is the position; both optional.</p>
      <button class="rp-btn is-ghost" type="button" data-sheet-add>Add these players</button>
      <p class="rp-hint ${starters > 11 ? "is-warn" : ""}">
        ${starters} in the starting XI${starters > 11 ? " — that is too many" : ""}.</p>
      <ul class="rp-sheet">${sheetRows}</ul>
      <button class="rp-btn" type="button" data-sheet-save>Save team sheet</button>
    `, open.ntsheet)}
  `;

  wireNTDetail(match, state);
}

function wireNTDetail(match, state) {
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

  host.addEventListener("click", async (e) => {
    const del = e.target.closest("[data-nt-del-goal]");
    if (del) {
      if (del.disabled) return;
      del.disabled = true;
      const { error } = await supabase.rpc("delete_nt_goal",
        { p_goal_id: del.dataset.ntDelGoal });
      del.disabled = false;
      if (error) { flash(humanError(error), "error"); return; }
      requestRebuild();
      state.ntDetail = null;
      await drawNTDetail(match, state);
      return;
    }
    const drop = e.target.closest("[data-sheet-del]");
    if (drop) {
      state.sheet.splice(Number(drop.dataset.sheetDel), 1);
      drawNTDetail(match, state, { local: true });
    }
  });

  host.querySelector("[data-sheet-add]")?.addEventListener("click", () => {
    const box = host.querySelector("[data-sheet-paste]");
    const added = parseTeamSheet(box.value);
    if (!added.length) { flash("Type at least one name.", "warn"); return; }
    const seen = new Set(state.sheet.map((r) => r.player_name.toLowerCase()));
    added.forEach((row) => {
      if (!seen.has(row.player_name.toLowerCase())) state.sheet.push(row);
    });
    box.value = "";
    drawNTDetail(match, state, { local: true });
  });

  host.addEventListener("change", (e) => {
    const field = e.target.dataset?.sheetField;
    if (!field) return;
    const row = state.sheet[Number(e.target.dataset.sheetI)];
    if (!row) return;
    row[field] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    // The role decides which controls a row shows, so that one redraws.
    if (field === "role") drawNTDetail(match, state, { local: true });
  });

  host.querySelector("[data-sheet-save]")?.addEventListener("click", async (e) => {
    const button = e.target;
    if (button.disabled) return;
    clearFlash();
    button.disabled = true; button.textContent = "Saving…";
    const { error } = await supabase.rpc("save_nt_lineup", {
      p_match_id: match.match_id,
      p_team_id: match.team_code,
      p_rows: state.sheet.map((r) => ({
        player_name: r.player_name, player_id: r.player_id || "",
        shirt_number: r.shirt_number || "", position: r.position || "",
        role: r.role || "starting", captain: Boolean(r.captain),
        minute_on: r.minute_on || "", minute_off: r.minute_off || "",
        replaced_player: r.replaced_player || "",
        yellow_card: Boolean(r.yellow_card),
        yellow_red_card: Boolean(r.yellow_red_card),
        red_card: Boolean(r.red_card),
      })),
    });
    button.disabled = false; button.textContent = "Save team sheet";
    // Rule 1: the sheet stays in state on failure, so nothing typed is lost.
    if (error) { flash(humanError(error), "error"); return; }
    flash("Team sheet saved.", "ok");
    requestRebuild();
    state.ntDetail = null;
    state.sheet = null;
    await drawNTDetail(match, state);
  });
}

/** "10 Tabitha Chawinga FW" -> {shirt_number, player_name, position}.
 *
 *  Both affixes are optional and the middle is the name, so a line that is
 *  just a name still works. Written for pasting a team sheet off a phone
 *  screenshot, which is how these arrive. */
function parseTeamSheet(text) {
  return (text || "").split("\n").map((line) => {
    let rest = line.trim();
    if (!rest) return null;
    let shirt = "";
    const lead = /^(\d{1,2})[.)]?\s+/.exec(rest);
    if (lead) { shirt = lead[1]; rest = rest.slice(lead[0].length); }
    let position = "";
    const tail = /\s+(GK|DF|MF|FW)$/i.exec(rest);
    if (tail) { position = tail[1].toUpperCase(); rest = rest.slice(0, tail.index); }
    rest = rest.trim();
    if (!rest) return null;
    return {
      player_name: rest, shirt_number: shirt, position,
      role: "starting", captain: false, player_id: "",
      minute_on: "", minute_off: "", replaced_player: "",
      yellow_card: false, yellow_red_card: false, red_card: false,
    };
  }).filter(Boolean);
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
  if (path === "/add") return renderAddFixture(params);
  if (path === "/league/new") return renderNewLeague();
  if (path === "/account") return renderAccount();
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
