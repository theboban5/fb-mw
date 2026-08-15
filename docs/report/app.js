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
      || message.includes("both teams are required")
      || message.includes("gender must be")
      || message.includes("type must be")
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
  // reporters and reporter_assignments are invisible to anon and filtered to
  // the caller for a reporter, so these two reads ARE the identity.
  const [{ data: reporters, error: rErr }, { data: assignments },
         { data: isAdmin }] = await Promise.all([
    supabase.from("reporters").select("reporter_id,name,public_byline,active"),
    supabase.from("reporter_assignments").select("competition_id,season_id"),
    supabase.rpc("is_admin"),
  ]);
  if (rErr) throw rErr;

  const reporter = (reporters || [])[0] || null;
  return {
    reporter,
    isAdmin: Boolean(isAdmin),
    // An admin is not assigned to anything, and does not need to be.
    competitions: (assignments || []).map((a) => a.competition_id),
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
  "match_id,public_id,competition_id,season_id,date,kickoff,status," +
  "home_goals,away_goals,home_team_id,away_team_id,source_ref," +
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

function matchCard(match, names, { showScore }) {
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
      <a class="rp-btn${scored ? " is-ghost" : ""}" href="#/m/${esc(match.public_id)}">
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

/** Filters live in the URL (#/?comp=…&show=…&date=…), not in a variable.
 *  The back button then works, a reload keeps the view, and "my league,
 *  awaiting result" is a link a reporter can keep. Both values are validated
 *  on the way in — the hash is user input. */
function readFilters(params) {
  const show = params.get("show");
  const date = params.get("date") || "";
  return {
    comp: params.get("comp") || "",
    show: SHOW_OPTIONS.some((o) => o.value === show) ? show : "all",
    date: /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : "",
  };
}

function filterHash(filters) {
  const query = new URLSearchParams();
  if (filters.comp) query.set("comp", filters.comp);
  if (filters.show !== "all") query.set("show", filters.show);
  if (filters.date) query.set("date", filters.date);
  const encoded = query.toString();
  return encoded ? `#/?${encoded}` : "#/";
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

function filterBar(filters, names, competitions) {
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

  return `
    <div class="rp-filters" data-filters>
      <label class="rp-filter">
        <span class="rp-filter-label">Competition</span>
        <select class="rp-select" data-filter="comp">${compOptions}</select>
      </label>
      <label class="rp-filter">
        <span class="rp-filter-label">Show</span>
        <select class="rp-select" data-filter="show">${showOptions}</select>
      </label>
      <label class="rp-filter">
        <span class="rp-filter-label">Date</span>
        <input class="rp-input rp-date" type="date" data-filter="date"
               value="${esc(filters.date)}">
      </label>
      ${(filters.comp || filters.show !== "all" || filters.date)
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

  const shown = data.matches.filter((m) => {
    if (filters.comp && m.competition_id !== filters.comp) return false;
    if (filters.date && m.date !== filters.date) return false;
    if (filters.show !== "all" && bucketOf(m, today) !== filters.show) return false;
    return true;
  });

  // Filtered to one thing: a flat list, because the headings would each hold
  // the whole list and say nothing. Unfiltered: the buckets, which are the
  // reason the home screen is useful at all.
  let body;
  if (filters.show === "all" && !filters.date) {
    body = [
      group("Today", shown.filter((m) => bucketOf(m, today) === "today"),
            names, { showScore: true }),
      group("Awaiting result", shown.filter((m) => bucketOf(m, today) === "awaiting"),
            names, { showScore: true }),
      group("Upcoming", shown.filter((m) => bucketOf(m, today) === "upcoming"),
            names, {}),
      group("Recently reported", shown.filter((m) => bucketOf(m, today) === "reported")
            .slice(0, 12), names, {}),
    ].join("");
  } else {
    const heading = filters.date
      ? formatDate(filters.date)
      : SHOW_OPTIONS.find((o) => o.value === filters.show).label;
    body = group(heading, shown, names,
                 { showScore: filters.show !== "upcoming" });
  }

  const canAdd = context.isAdmin || context.competitions.length > 0;
  const actions = `
    <div class="rp-actions">
      ${canAdd ? '<a class="rp-btn is-ghost" href="#/add">＋ Add fixture</a>' : ""}
      ${context.isAdmin ? '<a class="rp-btn is-ghost" href="#/league/new">＋ New league</a>' : ""}
      <button class="rp-btn is-quiet" type="button" data-refresh>Refresh</button>
    </div>`;

  h(`<h1 class="rp-greeting">Hi ${esc(firstName())}</h1>
     <p class="rp-sub">${esc(formatDate(today))}${
        context.isAdmin ? " · administrator" : ""}</p>
     ${actions}
     ${filterBar(filters, names, competitions)}
     ${body || `<p class="rp-empty">Nothing matches these filters.${
        (filters.comp || filters.show !== "all" || filters.date)
          ? " Try clearing them." : ""}</p>`}`);

  view.querySelector("[data-filters]").addEventListener("change", (event) => {
    const key = event.target.dataset.filter;
    if (!key) return;
    location.hash = filterHash({ ...filters, [key]: event.target.value });
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

async function renderMatch(publicId) {
  h('<div class="rp-loading"><span class="rp-spinner"></span><p>Loading match…</p></div>');

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
       <a class="rp-btn is-ghost" href="#/">Back to my matches</a>`);
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
       <a class="rp-btn is-ghost" href="#/">Back to my matches</a>`);
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
  };

  drawMatch(match, names, state);
}

function drawMatch(match, names, state) {
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

  const done = state.published ? `
    <div class="rp-done">
      <div class="rp-done-tick" aria-hidden="true">&#10003;</div>
      <p class="rp-done-head">Published</p>
      <p class="rp-done-score">${esc(homeName)} ${esc(match.home_goals ?? state.home)}&ndash;${esc(match.away_goals ?? state.away)} ${esc(awayName)}</p>
      <p class="rp-done-status">${esc(statusMeta(match.status).label)}</p>
      <p class="rp-done-status">Live on everyleague.co within a few minutes.</p>
    </div>` : "";

  h(`
    <a class="rp-btn is-quiet" href="#/" style="margin-top:0">&larr; My matches</a>
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

    <div data-detail></div>
  `);

  drawDetail(match, state);

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
    publishBtn.disabled = state.busy;
    publishBtn.textContent = state.busy
      ? "Publishing…"
      : info.scored
        ? `Publish ${state.home}–${state.away} ${info.short}`
        : `Publish as ${info.label.toLowerCase()}`;
    // The button says exactly what will become public.
    note.textContent = info.scored
      ? "This will appear on everyleague.co."
      : "No score will be published for this match.";
  }

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
    flash("Published. The site updates in a few minutes.", "ok", 6000);
    requestRebuild();
    drawMatch(match, names, state);
  });

  refresh();
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

function sideButtons(match, name) {
  return `
    <div class="rp-side-pick" role="radiogroup">
      <label><input type="radio" name="${name}" value="${esc(match.home_team_id)}" checked>
        <span>${esc(match.home?.display_name || match.home_team_id)}</span></label>
      <label><input type="radio" name="${name}" value="${esc(match.away_team_id)}">
        <span>${esc(match.away?.display_name || match.away_team_id)}</span></label>
    </div>`;
}

function section(key, title, count, inner) {
  const badge = count ? `<span class="rp-sec-count">${count}</span>` : "";
  return `<details class="rp-sec" data-sec="${key}">
    <summary>${esc(title)}${badge}</summary>
    <div class="rp-sec-body">${inner}</div>
  </details>`;
}

async function drawDetail(match, state) {
  const host = view.querySelector("[data-detail]");
  if (!host) return;

  const teamName = (id) => id === match.home_team_id
    ? (match.home?.display_name || id) : (match.away?.display_name || id);

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

  const goals = goalsRes.data || [];
  const incidents = incidentsRes.data || [];
  const lineup = lineupRes.data || [];
  const media = mediaRes.data || [];
  const cards = incidents.filter((i) => i.incident_type !== "substitution");
  const subs = incidents.filter((i) => i.incident_type === "substitution");
  const scored = match.home_goals != null;

  const goalList = goals.map((g) => `
    <li><span>${esc(g.reported_player_name || "Unknown")}
      <em>${esc(teamName(g.team_id))}${g.minute ? " · " + esc(g.minute) + "'" : ""}</em></span>
      <button class="rp-x" type="button" data-del-goal="${esc(g.goal_id)}"
              aria-label="Remove ${esc(g.reported_player_name)}">&times;</button></li>`).join("");

  const goalForm = scored ? `
    <form data-goal-form>
      ${sideButtons(match, "goal-team")}
      <input class="rp-input" name="player" placeholder="Scorer's name" required
             autocomplete="off" autocapitalize="words">
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
    </form>`
    : `<p class="rp-hint">Publish the score first — a scorer needs a goal to
         belong to.</p>`;

  host.innerHTML = `
    <h2 class="rp-field-head">Match detail <span class="rp-optional">optional</span></h2>

    ${section("goals", "Goalscorers", goals.length,
      `<ul class="rp-list">${goalList}</ul>${goalForm}`)}

    ${section("cards", "Cards", cards.length, `
      <ul class="rp-list">${cards.map((c) => `
        <li><span>${esc(c.player_name)}
          <em>${esc(teamName(c.team_id))} · ${esc(
            (CARD_TYPES.find((t) => t[0] === c.incident_type) || [, c.incident_type])[1])}
          ${c.minute ? " · " + esc(c.minute) + "'" : ""}</em></span>
          <button class="rp-x" type="button" data-del-incident="${c.incident_id}"
                  aria-label="Remove card">&times;</button></li>`).join("")}</ul>
      <form data-card-form>
        ${sideButtons(match, "card-team")}
        <input class="rp-input" name="player" placeholder="Player's name" required
               autocomplete="off" autocapitalize="words">
        <div class="rp-row">
          <select class="rp-input" name="type">
            ${CARD_TYPES.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}
          </select>
          <input class="rp-input" name="minute" placeholder="Min" inputmode="numeric">
        </div>
        <button class="rp-btn is-ghost" type="submit">Add card</button>
      </form>`)}

    ${section("subs", "Substitutions", subs.length, `
      <ul class="rp-list">${subs.map((s) => `
        <li><span>${esc(s.player_name)} <em>for ${esc(s.secondary_player_name)} ·
          ${esc(teamName(s.team_id))}${s.minute ? " · " + esc(s.minute) + "'" : ""}</em></span>
          <button class="rp-x" type="button" data-del-incident="${s.incident_id}"
                  aria-label="Remove substitution">&times;</button></li>`).join("")}</ul>
      <form data-sub-form>
        ${sideButtons(match, "sub-team")}
        <input class="rp-input" name="on" placeholder="Player coming on" required
               autocomplete="off" autocapitalize="words">
        <input class="rp-input" name="off" placeholder="Player going off" required
               autocomplete="off" autocapitalize="words">
        <input class="rp-input" name="minute" placeholder="Min" inputmode="numeric">
        <button class="rp-btn is-ghost" type="submit">Add substitution</button>
      </form>`)}

    ${section("lineup", "Line-ups", lineup.length, `
      <p class="rp-hint">One name per line. Paste a whole team in at once —
        no need to type them into separate boxes.</p>
      <form data-lineup-form>
        ${sideButtons(match, "lineup-team")}
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
                  aria-label="Remove ${esc(l.player_name)}">&times;</button></li>`).join("")}</ul>`)}

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
      </form>`)}
  `;

  wireDetail(match, state);
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

function wireDetail(match, state) {
  const host = view.querySelector("[data-detail]");
  const pick = (name) =>
    host.querySelector(`input[name="${name}"]:checked`)?.value;
  const reporterId = context.reporter?.reporter_id;

  host.querySelector("[data-goal-form]")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    detailAction(f.querySelector("button"), "Adding…", async () => {
      const { error } = await supabase.rpc("submit_match_goal", {
        p_match_id: match.match_id,
        p_team_id: pick("goal-team"),
        p_player_name: f.player.value,
        p_minute: f.minute.value,
        p_goal_type: f.type.value,
      });
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
    const goal = e.target.closest("[data-del-goal]");
    const incident = e.target.closest("[data-del-incident]");
    const line = e.target.closest("[data-del-lineup]");
    const photo = e.target.closest("[data-del-media]");
    if (goal) {
      detailAction(goal, "…", async () =>
        (await supabase.rpc("delete_match_goal",
          { p_goal_id: goal.dataset.delGoal })).error, match, state);
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

/** Ask for a site rebuild. Deliberately not awaited, and never surfaced.
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
 */
function requestRebuild() {
  try {
    supabase.functions.invoke("trigger-rebuild", { body: {} })
      .then(() => {}, () => {});
  } catch (_err) {
    /* nothing to tell the reporter */
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

  if (path.startsWith("/m/")) return renderMatch(path.slice(3));
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
