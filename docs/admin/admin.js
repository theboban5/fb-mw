/* EverLeague data-entry UI.
 *
 * Reads the same published-CSV tabs as the build (public, no auth) and writes
 * through the Apps Script web app in tools/entry/apps-script/WebApp.gs. The
 * sheet stays the single source of truth; validate.py remains the build gate.
 *
 * The published CSVs lag the sheet by ~5 minutes, so anything status-critical
 * (the result picker) prefers the script's live_matches action, and writes
 * made this session are overlaid from sessionStorage.
 */

"use strict";

// ── Data sources (mirror of src/dataset.py — that file is the source of truth
// for BASE_URL and the gid map; update both together) ────────────────────────

const BASE_URL =
  "https://docs.google.com/spreadsheets/d/e/" +
  "2PACX-1vSF7xMvjTyQLckW3IHBIip7msX2H4qj0MS8Yedatly3LJXDosMvjSz4MbSq42rxzL" +
  "-qa3ehnJuaMZP6/pub";

const TAB_GIDS = {
  clubs: 1571065713,
  teams: 1542712062,
  competitions: 1088082573,
  seasons: 232948228,
  competition_seasons: 667630842,
  entries: 1469327288,
  venues: 2142346215,
  matches: 783604265,
  goals: 247287352,
  players: 576599713,
  aliases: 1570860122,
};

const UNKNOWN_PLAYER_ID = "CAF_MW_UNKNOWN";

// Default write endpoint (the WebApp.gs deployment). Safe to hardcode — the
// URL is visible in this public page regardless; ENTRY_TOKEN is the only
// write gate and must NEVER appear in the repo. Enter it in ⚙ settings.
const DEFAULT_SCRIPT_URL =
  "https://script.google.com/macros/s/AKfycbxdz46jQhvW4wzBbXkf8cOS2wVH7Gd9nRCWFyob5PaDNlimzj-CR_PSih3H-lr0_dH2FQ/exec";

// Entry-facing enum subsets (full lists live in src/dataset.py); placeholder
// is a data-seeding value and deliberately not offered here.
const SOURCE_TYPES = ["facebook", "reporter", "rfa", "fa", "club", "newspaper", "whatsapp", "backfill", "unknown"];
const CONFIDENCES = ["confirmed", "unconfirmed", "official"];
const GOAL_TYPES = ["", "open_play", "penalty", "free_kick", "header", "own_goal"];

// ── Tiny helpers ─────────────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of children) {
    node.append(c);
  }
  return node;
}

function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

let toastTimer = null;
function toast(msg, isError = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = isError ? "error" : "";
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, isError ? 6000 : 3500);
}

function banner(msg) {
  const b = $("banner");
  if (!msg) { b.hidden = true; return; }
  b.textContent = msg;
  b.hidden = false;
}

// ── CSV (RFC 4180: quoted fields may contain commas, quotes, newlines) ───────

function parseCsv(text) {
  const rows = [];
  let row = [], field = "", inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else inQuotes = false;
      } else field += ch;
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      row.push(field); field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(field); field = "";
      rows.push(row); row = [];
    } else {
      field += ch;
    }
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }

  const header = rows.shift().map((h) => h.trim());
  return rows
    .map((r) => {
      const obj = {};
      header.forEach((h, i) => { if (h) obj[h] = (r[i] || "").trim(); });
      return obj;
    })
    .filter((obj) => Object.values(obj).some((v) => v !== ""));
}

async function fetchTab(tab) {
  const url = `${BASE_URL}?gid=${TAB_GIDS[tab]}&single=true&output=csv`;
  const resp = await fetch(url, { cache: "no-store" });
  if (!resp.ok) throw new Error(`${tab}: HTTP ${resp.status}`);
  const text = await resp.text();
  // The publish endpoint occasionally serves an HTML error page with 200.
  if (text.trimStart().startsWith("<")) throw new Error(`${tab}: got HTML, not CSV`);
  return parseCsv(text);
}

// ── Settings (script URL + token) and API client ─────────────────────────────

const settings = {
  load() {
    let s;
    try { s = JSON.parse(localStorage.getItem("entry.settings")) || {}; }
    catch { s = {}; }
    if (!s.url) s.url = DEFAULT_SCRIPT_URL;
    return s;
  },
  save(s) { localStorage.setItem("entry.settings", JSON.stringify(s)); },
  configured() { const s = this.load(); return Boolean(s.url && s.token); },
};

async function api(action, payload = {}) {
  const s = settings.load();
  if (!s.url) throw new Error("Set the script URL in Settings (⚙) first");
  if (!s.token) throw new Error("Enter the entry token in Settings (⚙) — it's not stored in this browser yet");
  // Plain string body = CORS "simple request" (text/plain): Apps Script can't
  // answer preflights, so never add custom headers here.
  const resp = await fetch(s.url, {
    method: "POST",
    body: JSON.stringify({ token: s.token, action, payload }),
    redirect: "follow",
  });
  const text = await resp.text();
  let data;
  try { data = JSON.parse(text); }
  catch { throw new Error(`script returned non-JSON (HTTP ${resp.status})`); }
  if (!data.ok) throw new Error(data.error || "unknown script error");
  return data;
}

// ── This session's writes (overlay for stale CSV data) ───────────────────────

const pending = {
  load() {
    try { return JSON.parse(sessionStorage.getItem("entry.pending")) || { created: {}, saved: {} }; }
    catch { return { created: {}, saved: {} }; }
  },
  addCreated(match) {
    const p = this.load();
    p.created[match.match_id] = match;
    sessionStorage.setItem("entry.pending", JSON.stringify(p));
  },
  addSaved(matchId, patch) {
    const p = this.load();
    p.saved[matchId] = patch;
    sessionStorage.setItem("entry.pending", JSON.stringify(p));
  },
};

// ── Batch-grid drafts ────────────────────────────────────────────────────────
// A half-entered matchday is a lot of typing to lose to a stray refresh, so
// both grids snapshot themselves (IDs and primitives only) on every edit.

const drafts = {
  key(kind, compId) { return `entry.draft.${kind}.${compId}`; },
  load(kind, compId) {
    try { return JSON.parse(sessionStorage.getItem(this.key(kind, compId))); }
    catch { return null; }
  },
  save(kind, compId, data) {
    sessionStorage.setItem(this.key(kind, compId), JSON.stringify(data));
  },
  clear(kind, compId) { sessionStorage.removeItem(this.key(kind, compId)); },
};

// ── Dataset + indexes ────────────────────────────────────────────────────────

const DB = {};        // raw rows per tab
const IX = {};        // derived indexes
const state = { league: null, match: null, stack: [] };

async function loadData() {
  const tabs = Object.keys(TAB_GIDS);
  const results = await Promise.all(tabs.map(fetchTab));
  tabs.forEach((t, i) => { DB[t] = results[i]; });
  buildIndexes();
}

function buildIndexes() {
  const active = DB.seasons.filter((s) => s.status === "active");
  if (active.length !== 1) throw new Error(`expected exactly 1 active season, found ${active.length}`);
  IX.season = active[0];

  IX.competitions = Object.fromEntries(DB.competitions.map((c) => [c.competition_id, c]));
  IX.clubs = Object.fromEntries(DB.clubs.map((c) => [c.club_id, c]));
  IX.teams = Object.fromEntries(DB.teams.map((t) => [t.team_id, t]));
  IX.venues = DB.venues
    .map((v) => ({
      venue_id: v.venue_id,
      label: v.name,
      city: v.city,
      search: `${v.name} ${v.city}`.toLowerCase(),
    }))
    .sort((a, b) => a.label.localeCompare(b.label));
  IX.venueIds = new Set(DB.venues.map((v) => v.venue_id.toUpperCase()));

  IX.leagues = DB.competition_seasons
    .filter((cs) => cs.season_id === IX.season.season_id)
    .map((cs) => ({
      ...cs,
      display: cs.sponsor_name || IX.competitions[cs.competition_id]?.name || cs.competition_id,
      type: IX.competitions[cs.competition_id]?.type || "league",
    }));

  // Fast-entry aliases are club-level: expose them on every team of the club.
  const aliasesByClub = {};
  for (const a of DB.aliases) {
    if (a.entity_type !== "club") continue;
    (aliasesByClub[a.entity_id] ||= []).push(a.alias_text);
  }

  // League-filtered team lists from entries (the membership the validator
  // enforces). Withdrawn/expelled teams stay pickable for historic results
  // but are labelled.
  IX.teamsByLeague = {};
  for (const e of DB.entries) {
    if (e.season_id !== IX.season.season_id) continue;
    const team = IX.teams[e.team_id];
    if (!team) continue;
    const club = IX.clubs[team.club_id] || {};
    (IX.teamsByLeague[e.competition_id] ||= []).push({
      team_id: e.team_id,
      label: team.display_name,
      entryStatus: e.status || "active",
      search: [
        team.display_name, club.short_name || "", club.name || "",
        ...(aliasesByClub[team.club_id] || []),
      ].join(" ").toLowerCase(),
    });
  }
  for (const list of Object.values(IX.teamsByLeague)) {
    list.sort((a, b) => a.label.localeCompare(b.label));
  }

  IX.matchById = Object.fromEntries(DB.matches.map((m) => [m.match_id, m]));

  // Player -> team inference from their most recent goal (registrations is
  // still empty; prefer it here if it ever gets populated).
  const latestGoal = {};
  for (const g of DB.goals) {
    const m = IX.matchById[g.match_id];
    if (!m || !g.player_id) continue;
    const when = m.date || "";
    const prev = latestGoal[g.player_id];
    if (!prev || when >= prev.when) latestGoal[g.player_id] = { when, team_id: g.team_id };
  }
  IX.players = DB.players
    .filter((p) => p.player_id !== UNKNOWN_PLAYER_ID)
    .map((p) => ({
      player_id: p.player_id,
      label: p.known_as || p.full_name || p.player_id,
      full_name: p.full_name,
      team_id: latestGoal[p.player_id]?.team_id || "",
      search: `${p.full_name} ${p.known_as}`.toLowerCase(),
    }));
}

function teamName(teamId) {
  return IX.teams[teamId]?.display_name || teamId;
}

// ── Search / ranking ─────────────────────────────────────────────────────────

// Lower = better; -1 = no match.
function matchQuality(search, label, query) {
  const q = query.toLowerCase().trim();
  if (!q) return 2;
  const l = label.toLowerCase();
  if (l.startsWith(q)) return 0;
  if (search.split(/\s+/).some((w) => w.startsWith(q))) return 1;
  if (search.includes(q)) return 2;
  return -1;
}

// ── Combobox (autocomplete input) ────────────────────────────────────────────

function makeCombo(container, { placeholder, getItems, onPick }) {
  const input = el("input", { type: "text", placeholder: placeholder || "Start typing…" });
  const list = el("div", { class: "options", hidden: "" });
  container.append(input, list);
  let items = [], hi = -1, selected = null;

  function render() {
    list.innerHTML = "";
    items.forEach((item, i) => {
      const row = el("div", { class: (i === hi ? "hi " : "") + (item.special ? "special" : "") }, item.label);
      if (item.sub) row.append(el("span", { class: "sub" }, item.sub));
      row.addEventListener("mousedown", (ev) => { ev.preventDefault(); pick(item); });
      list.append(row);
    });
    list.hidden = items.length === 0;
  }

  function open() {
    items = getItems(selected ? "" : input.value);
    hi = -1;
    render();
  }

  function pick(item) {
    selected = item.special ? selected : item;
    if (!item.special) {
      input.value = item.label;
      input.classList.add("selected");
    }
    items = []; render();
    onPick && onPick(item);
  }

  input.addEventListener("input", () => {
    selected = null;
    input.classList.remove("selected");
    open();
  });
  input.addEventListener("focus", open);
  input.addEventListener("blur", () => setTimeout(() => { items = []; render(); }, 150));
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown") { hi = Math.min(hi + 1, items.length - 1); render(); ev.preventDefault(); }
    else if (ev.key === "ArrowUp") { hi = Math.max(hi - 1, 0); render(); ev.preventDefault(); }
    else if (ev.key === "Enter") {
      if (items.length) { pick(items[hi >= 0 ? hi : 0]); ev.preventDefault(); }
    } else if (ev.key === "Escape") { items = []; render(); }
  });

  return {
    get value() { return selected; },
    // What has been typed but not picked — lets a "＋ New …" item prefill the
    // creation modal with the name the user was already halfway through.
    get query() { return input.value.trim(); },
    set(item) { pick(item); },
    clear() {
      selected = null; input.value = "";
      input.classList.remove("selected");
      items = []; render();
    },
    focus() { input.focus(); },
    setDisabled(v) { input.disabled = v; },
  };
}

function teamItems(compId, query, excludeId) {
  return (IX.teamsByLeague[compId] || [])
    .filter((t) => t.team_id !== excludeId)
    .map((t) => ({ ...t, q: matchQuality(t.search, t.label, query) }))
    .filter((t) => t.q >= 0)
    .sort((a, b) => a.q - b.q || a.label.localeCompare(b.label))
    .slice(0, 12)
    .map((t) => ({
      id: t.team_id,
      label: t.label,
      sub: t.entryStatus !== "active" ? t.entryStatus : "",
    }));
}

function playerItems(query, preferTeamId, leagueTeamIds) {
  const inLeague = new Set(leagueTeamIds);
  const ranked = IX.players
    .map((p) => ({ ...p, q: matchQuality(p.search, p.label, query) }))
    .filter((p) => p.q >= 0)
    .sort((a, b) => {
      const ta = a.team_id === preferTeamId ? 0 : inLeague.has(a.team_id) ? 1 : 2;
      const tb = b.team_id === preferTeamId ? 0 : inLeague.has(b.team_id) ? 1 : 2;
      return ta - tb || a.q - b.q || a.label.localeCompare(b.label);
    })
    .slice(0, 10)
    .map((p) => ({
      id: p.player_id,
      label: p.label,
      sub: p.team_id ? teamName(p.team_id) : "",
    }));
  ranked.push({ id: UNKNOWN_PLAYER_ID, label: "Unknown scorer", sub: "", special: false });
  ranked.push({ id: "__new__", label: "＋ New player…", special: true });
  return ranked;
}

function venueItems(query) {
  const ranked = IX.venues
    .map((v) => ({ ...v, q: matchQuality(v.search, v.label, query) }))
    .filter((v) => v.q >= 0)
    .sort((a, b) => a.q - b.q || a.label.localeCompare(b.label))
    .slice(0, 10)
    .map((v) => ({ id: v.venue_id, label: v.label, sub: v.city || "" }));
  ranked.push({ id: "__new__", label: "＋ New venue…", special: true });
  return ranked;
}

/** A venue picker wired to the new-venue modal. Replaces the container's DOM. */
function makeVenueCombo(container) {
  container.innerHTML = "";
  const combo = makeCombo(container, {
    placeholder: "Venue…",
    getItems: (q) => venueItems(q),
    onPick: (item) => { if (item.id === "__new__") openVenueModal(combo); },
  });
  return combo;
}

// Words that say "this is a sports ground", not which one — dropping them
// keeps a suggested code short and distinctive.
const VENUE_STOPWORDS = new Set([
  "stadium", "ground", "grounds", "community", "school", "secondary", "cdss",
  "park", "club", "field", "complex", "centre", "center", "mini", "the", "of", "and",
]);

/**
 * Suggest a venue code from its name ("Mpira Stadium" -> MW_MPIRA). The venues
 * tab has no convention to follow (MW_ADL_G, MW_MCJ_001 and MW_NENO all
 * coexist) and nothing parses these IDs, so this only has to be unique and
 * legible — the user can overwrite it, and the script re-checks collisions
 * against live data.
 */
function suggestVenueId(name) {
  const words = String(name || "").toUpperCase().replace(/[^A-Z0-9]+/g, " ").trim().split(/\s+/).filter(Boolean);
  const significant = words.filter((w) => !VENUE_STOPWORDS.has(w.toLowerCase()));
  const parts = significant.length ? significant : words;
  if (!parts.length) return "";
  let base = parts.slice(0, 2).join("_");
  if (base.length > 12) base = parts[0].slice(0, 12);
  let id = `MW_${base}`;
  if (IX.venueIds.has(id)) {
    let n = 2;
    while (IX.venueIds.has(`${id}_${n}`)) n++;
    id = `${id}_${n}`;
  }
  return id;
}

// ── Views / navigation ───────────────────────────────────────────────────────

const VIEWS = ["view-home", "view-league", "view-fixture", "view-picker", "view-result",
               "view-batch-fixtures", "view-batch-results"];

function showView(id, push = true) {
  if (push && !state.stack.length) state.stack = ["view-home"];
  if (push && state.stack[state.stack.length - 1] !== id) state.stack.push(id);
  for (const v of VIEWS) $(v).hidden = v !== id;
  $("back-btn").hidden = id === "view-home";
  $("topbar-title").textContent = state.league && id !== "view-home"
    ? state.league.display : "Data entry";
  window.scrollTo(0, 0);
}

function goBack() {
  state.stack.pop();
  const prev = state.stack[state.stack.length - 1] || "view-home";
  if (prev === "view-picker") renderPicker();
  showView(prev, false);
}

// ── Home + league hub ────────────────────────────────────────────────────────

function renderHome() {
  $("season-line").textContent = `Season ${IX.season.label}`;
  const listEl = $("league-list");
  listEl.innerHTML = "";
  for (const league of IX.leagues) {
    const teams = (IX.teamsByLeague[league.competition_id] || []).length;
    const card = el("button", { class: "card", onclick: () => { state.league = league; renderLeague(); showView("view-league"); } },
      league.display,
      el("span", { class: "sub" }, `${teams} teams`));
    listEl.append(card);
  }
}

function renderLeague() {
  $("league-title").textContent = state.league.display;
}

// ── Matchday vs knockout round ───────────────────────────────────────────────
// Leagues put free-form md_<n> in matches.stage; cups use the fixed knockout
// vocabulary from src/dataset.py (KNOCKOUT_STAGES) and leave matchday blank.
// Both fixture forms swap the field to suit the competition they were opened
// from — no cup competition should ever be given an md_ stage.

const KNOCKOUT_STAGES = ["r64", "r32", "r16", "qf", "sf", "final", "3p"];
const STAGE_LABELS = {
  r64: "Round of 64", r32: "Round of 32", r16: "Round of 16",
  qf: "Quarter-final", sf: "Semi-final", final: "Final", "3p": "Third-place play-off",
};

function leagueIsCup() {
  return state.league.type === "cup";
}

function setupStageField(prefix) {
  const cup = leagueIsCup();
  $(`${prefix}-matchday-label`).hidden = cup;
  $(`${prefix}-stage-label`).hidden = !cup;
  if (!cup) return;
  const sel = $(`${prefix}-stage`);
  sel.innerHTML = "";
  for (const s of KNOCKOUT_STAGES) sel.append(el("option", { value: s }, STAGE_LABELS[s]));
}

function stagePayload(prefix) {
  if (leagueIsCup()) return { stage: $(`${prefix}-stage`).value, matchday: "" };
  const matchday = $(`${prefix}-matchday`).value.trim();
  return { stage: matchday ? `md_${matchday}` : "", matchday };
}

// ── New fixture ──────────────────────────────────────────────────────────────

let fxHome, fxAway, fxVenue;

function fillSelect(sel, values, current) {
  sel.innerHTML = "";
  for (const v of values) sel.append(el("option", { value: v }, v === "" ? "(none)" : v));
  sel.value = current;
}

function leagueMatches(compId) {
  const fromCsv = DB.matches.filter(
    (m) => m.competition_id === compId && m.season_id === IX.season.season_id
  );
  const created = Object.values(pending.load().created).filter(
    (m) => m.competition_id === compId && m.season_id === IX.season.season_id
  );
  const have = new Set(fromCsv.map((m) => m.match_id));
  return fromCsv.concat(created.filter((m) => !have.has(m.match_id)));
}

function nextMatchday(compId) {
  const mds = leagueMatches(compId).map((m) => parseInt(m.matchday, 10)).filter(Number.isFinite);
  return mds.length ? Math.max(...mds) + 1 : 1;
}

function provisionalMatchId(compId) {
  let best = null;
  for (const m of leagueMatches(compId)) {
    const match = /^(.*?)(\d+)$/.exec(m.match_id);
    if (!match) continue;
    const n = parseInt(match[2], 10);
    if (!best || n > best.n) best = { prefix: match[1], n, width: match[2].length };
  }
  if (!best) return "(first match of this competition-season)";
  return best.prefix + String(best.n + 1).padStart(best.width, "0");
}

function renderFixtureForm(keepSticky = false) {
  const compId = state.league.competition_id;
  setupStageField("fx");
  if (!keepSticky) {
    $("fx-matchday").value = leagueIsCup() ? "" : nextMatchday(compId);
    $("fx-date").value = "";
  }
  $("fx-date").min = IX.season.start_date;
  $("fx-date").max = IX.season.end_date;
  $("fx-kickoff").value = "";
  $("fx-ref").value = "";
  fxVenue = makeVenueCombo($("fx-venue"));

  fillSelect($("fx-source"), SOURCE_TYPES, "facebook");
  fillSelect($("fx-confidence"), CONFIDENCES, "confirmed");

  $("fx-home").innerHTML = "";
  $("fx-away").innerHTML = "";
  fxHome = makeCombo($("fx-home"), {
    getItems: (q) => teamItems(compId, q, fxAway?.value?.id),
  });
  fxAway = makeCombo($("fx-away"), {
    getItems: (q) => teamItems(compId, q, fxHome?.value?.id),
  });

  $("fx-provisional").textContent = provisionalMatchId(compId);
}

async function submitFixture(ev) {
  ev.preventDefault();
  const compId = state.league.competition_id;
  const home = fxHome.value, away = fxAway.value;
  if (!home || !away) return toast("Pick both teams from the list", true);
  if (home.id === away.id) return toast("Home and away can't be the same team", true);

  if (!fxVenue.value && fxVenue.query) {
    return toast("Pick the venue from the list, or add it with ＋ New venue…", true);
  }
  const venueId = fxVenue.value ? fxVenue.value.id : "";

  const payload = {
    competition_id: compId,
    season_id: IX.season.season_id,
    ...stagePayload("fx"),
    date: $("fx-date").value,
    kickoff: $("fx-kickoff").value,
    venue_id: venueId || "",
    home_team_id: home.id,
    away_team_id: away.id,
    source_type: $("fx-source").value,
    source_ref: $("fx-ref").value.trim(),
    confidence: $("fx-confidence").value,
  };

  const btn = $("fx-submit");
  btn.disabled = true;
  try {
    const res = await api("create_fixture", payload);
    pending.addCreated({ ...payload, match_id: res.match_id, status: "scheduled" });
    toast(`Saved ${res.match_id}: ${home.label} v ${away.label}`);
    // Sticky league/matchday/date so a whole round can be entered quickly.
    fxHome.clear(); fxAway.clear();
    $("fx-ref").value = "";
    $("fx-provisional").textContent = provisionalMatchId(compId);
    fxHome.focus();
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

// ── Result entry: match picker ───────────────────────────────────────────────

async function renderPicker() {
  const compId = state.league.competition_id;
  const listEl = $("match-list");
  listEl.innerHTML = "";
  $("picker-status").textContent = "Loading…";

  let matches, live = false;
  if (settings.configured()) {
    try {
      const res = await api("live_matches", { season_id: IX.season.season_id });
      matches = res.matches.filter((m) => m.competition_id === compId);
      live = true;
    } catch (err) {
      toast(`Live fetch failed (${err.message}); using cached data`, true);
    }
  }
  if (!matches) matches = leagueMatches(compId);
  matches = matches.filter((m) => m.source_type !== "placeholder");

  const savedNow = pending.load().saved;
  const todo = matches.filter((m) => ["scheduled", "postponed"].includes(m.status) && !savedNow[m.match_id]);
  const done = matches.filter((m) => !todo.includes(m));
  todo.sort((a, b) => (a.date || "9999").localeCompare(b.date || "9999") || a.match_id.localeCompare(b.match_id));
  done.sort((a, b) => (b.date || "").localeCompare(a.date || "") || b.match_id.localeCompare(a.match_id));

  $("picker-status").textContent = live
    ? "Live from the sheet."
    : "From the published CSV (can lag ~5 min) — configure Settings (⚙) for live data.";

  const addCard = (m) => {
    const saved = savedNow[m.match_id];
    const status = saved ? saved.status : m.status;
    const score = status === "played" || status === "awarded"
      ? ` ${saved ? saved.home_goals : m.home_goals}–${saved ? saved.away_goals : m.away_goals}`
      : "";
    const badgeClass = saved ? "badge saved" : "badge";
    const badgeText = saved ? "saved ✓" : status;
    const card = el("button", { class: "card", onclick: () => { state.match = m; renderResultForm(); showView("view-result"); } },
      el("span", { class: badgeClass }, badgeText),
      `${teamName(m.home_team_id)} v ${teamName(m.away_team_id)}${score}`,
      el("span", { class: "sub" }, [m.matchday ? `MD ${m.matchday}` : m.stage, m.date || "no date", m.match_id].filter(Boolean).join(" · ")));
    listEl.append(card);
  };

  if (todo.length) {
    listEl.append(el("p", { class: "matchday-head" }, "To enter"));
    todo.forEach(addCard);
  }
  if (done.length) {
    listEl.append(el("p", { class: "matchday-head" }, "Recorded"));
    done.forEach(addCard);
  }
  if (!todo.length && !done.length) {
    listEl.append(el("p", { class: "muted" }, "No matches in this league yet."));
  }
}

// ── Result entry: form + scorer rows ─────────────────────────────────────────

/**
 * Goal-attribution rows for one match, mounted into `host`. Owns its own DOM
 * and "＋ Add scorer" button, so the batch view can hang one off every result
 * row without the single-result form and the grid fighting over globals.
 *
 * opts.getScore -> {hg, ag} (either may be null); decides which side a fresh
 *                  row defaults to. opts.onChange fires on any row/team change.
 */
function makeScorerPanel(host, match, opts = {}) {
  const getScore = opts.getScore || (() => ({ hg: null, ag: null }));
  const changed = () => opts.onChange && opts.onChange();
  const leagueTeams = (IX.teamsByLeague[match.competition_id] || []).map((t) => t.team_id);

  const rowsEl = el("div", { class: "scorer-rows" });
  const addBtn = el("button", { type: "button", class: "ghost" }, "＋ Add scorer");
  host.innerHTML = "";
  host.append(rowsEl, addBtn);
  const rows = [];

  function counts() {
    let home = 0, away = 0;
    for (const s of rows) { if (s.teamId === match.home_team_id) home++; else away++; }
    return { home, away };
  }

  function defaultTeam() {
    const { hg, ag } = getScore();
    const { home, away } = counts();
    if (home < (hg ?? 0)) return match.home_team_id;
    if (away < (ag ?? 0)) return match.away_team_id;
    return match.home_team_id;
  }

  function addRow(preset = {}) {
    const entry = { teamId: preset.teamId || defaultTeam() };

    const toggle = el("button", { type: "button", class: "team-toggle" }, teamName(entry.teamId));
    toggle.addEventListener("click", () => {
      entry.teamId = entry.teamId === match.home_team_id ? match.away_team_id : match.home_team_id;
      toggle.textContent = teamName(entry.teamId);
      changed();
    });

    const comboWrap = el("div", { class: "combo" });
    const minute = el("input", { type: "number", min: "1", max: "130", placeholder: "min", inputmode: "numeric" });
    const stoppage = el("input", { type: "number", min: "1", max: "15", placeholder: "+", inputmode: "numeric" });
    const type = el("select");
    for (const gt of GOAL_TYPES) type.append(el("option", { value: gt }, gt === "" ? "goal" : gt.replace("_", " ")));

    const remove = el("button", { type: "button", class: "remove", title: "Remove" }, "✕");
    const row = el("div", { class: "scorer-row" },
      el("div", { class: "line1" }, toggle, comboWrap, remove),
      el("div", { class: "line2" }, minute, stoppage, type));
    rowsEl.append(row);

    entry.row = row;
    entry.minuteEl = minute;
    entry.stoppageEl = stoppage;
    entry.typeEl = type;
    entry.combo = makeCombo(comboWrap, {
      placeholder: "Scorer…",
      getItems: (q) => playerItems(q, entry.teamId, leagueTeams),
      onPick: (item) => { if (item.id === "__new__") openPlayerModal(entry); },
    });

    remove.addEventListener("click", () => {
      row.remove();
      rows.splice(rows.indexOf(entry), 1);
      changed();
    });

    if (preset.playerId) entry.combo.set({ id: preset.playerId, label: preset.label || preset.playerId });
    if (preset.minute) minute.value = preset.minute;
    if (preset.stoppage) stoppage.value = preset.stoppage;
    if (preset.goalType) type.value = preset.goalType;

    rows.push(entry);
    changed();
    if (!preset.playerId) entry.combo.focus();
    return entry;
  }

  addBtn.addEventListener("click", () => addRow());

  return {
    rows,
    counts,
    addRow,
    /** goals[] for save_result; throws a user-facing Error if a row is empty. */
    collect() {
      return rows.map((s) => {
        if (!s.combo.value) throw new Error("Every scorer row needs a player (or remove the row)");
        const minute = s.minuteEl.value.trim();
        return {
          team_id: s.teamId,
          player_id: s.combo.value.id,
          minute,
          stoppage: s.stoppageEl.value.trim(),
          period: minute === "" ? "" : parseInt(minute, 10) <= 45 ? "1h" : "2h",
          goal_type: s.typeEl.value,
        };
      });
    },
    /** Plain-data form for drafts; feed each item back to addRow(). */
    snapshot() {
      return rows.map((s) => ({
        teamId: s.teamId,
        playerId: s.combo.value ? s.combo.value.id : "",
        label: s.combo.value ? s.combo.value.label : "",
        minute: s.minuteEl.value.trim(),
        stoppage: s.stoppageEl.value.trim(),
        goalType: s.typeEl.value,
      }));
    },
    setDisabled(v) {
      addBtn.disabled = v;
      for (const s of rows) {
        s.combo.setDisabled(v);
        s.minuteEl.disabled = v;
        s.stoppageEl.disabled = v;
        s.typeEl.disabled = v;
      }
    },
  };
}

let resultPanel = null;

function renderResultForm() {
  const m = state.match;
  $("result-title").textContent = `${teamName(m.home_team_id)} v ${teamName(m.away_team_id)}`;
  $("result-meta").textContent = [m.match_id, m.date || "no date", m.matchday ? `MD ${m.matchday}` : ""].filter(Boolean).join(" · ");
  $("rs-home-name").textContent = teamName(m.home_team_id);
  $("rs-away-name").textContent = teamName(m.away_team_id);

  const alreadyDone = ["played", "awarded"].includes(m.status) || pending.load().saved[m.match_id];
  $("replace-warning").hidden = !alreadyDone;
  $("rs-replace").checked = false;

  $("rs-home-goals").value = "";
  $("rs-away-goals").value = "";
  $("rs-status").value = "played";
  $("rs-ref").value = "";
  fillSelect($("rs-source"), SOURCE_TYPES, "facebook");
  fillSelect($("rs-confidence"), CONFIDENCES, "confirmed");

  resultPanel = makeScorerPanel($("scorer-host"), m, {
    getScore: () => ({ hg: scoreVal("rs-home-goals"), ag: scoreVal("rs-away-goals") }),
    onChange: updateScorerUi,
  });
  updateScorerUi();
}

function scoreVal(id) {
  const v = $(id).value.trim();
  return v === "" ? null : parseInt(v, 10);
}

function updateScorerUi() {
  const m = state.match;
  const hg = scoreVal("rs-home-goals"), ag = scoreVal("rs-away-goals");
  const status = $("rs-status").value;
  $("scorers-wrap").hidden = !["played", "abandoned"].includes(status);

  if (hg === null || ag === null) {
    $("scorer-counter").textContent = "— enter the score to track attribution";
    return;
  }
  const { home, away } = resultPanel.counts();
  $("scorer-counter").textContent =
    `— ${home}/${hg} ${teamName(m.home_team_id)}, ${away}/${ag} ${teamName(m.away_team_id)}`;
}

async function submitResult(ev) {
  ev.preventDefault();
  const m = state.match;
  const status = $("rs-status").value;
  const hg = scoreVal("rs-home-goals"), ag = scoreVal("rs-away-goals");

  if ((hg === null) !== (ag === null)) return toast("Enter both scores or neither", true);
  if (status === "played" && hg === null) return toast("A played match needs a score", true);

  const alreadyDone = ["played", "awarded"].includes(m.status) || pending.load().saved[m.match_id];
  if (alreadyDone && !$("rs-replace").checked) {
    return toast("Tick the replace box to overwrite the existing result", true);
  }

  let goals;
  try { goals = resultPanel.collect(); }
  catch (err) { return toast(err.message, true); }
  const { home, away } = resultPanel.counts();
  if (hg !== null && (home > hg || away > ag)) return toast("More scorer rows than goals on one side", true);

  const btn = $("rs-submit");
  btn.disabled = true;
  try {
    const res = await api("save_result", {
      match_id: m.match_id,
      home_goals: hg === null ? "" : String(hg),
      away_goals: ag === null ? "" : String(ag),
      status,
      source_type: $("rs-source").value,
      source_ref: $("rs-ref").value.trim(),
      confidence: $("rs-confidence").value,
      replace_goals: Boolean(alreadyDone && $("rs-replace").checked),
      goals,
    });
    pending.addSaved(m.match_id, { status, home_goals: hg, away_goals: ag });
    toast(`Saved ${res.match_id} (${goals.length} goal${goals.length === 1 ? "" : "s"})`);
    goBack();
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

// ── Batch: a matchday of fixtures ────────────────────────────────────────────
// Both grids save by looping the same one-at-a-time script actions the single
// forms use — no bulk endpoint, so nothing here can outrun the deployed
// WebApp.gs. The cost is one round-trip per row, which is why each row carries
// its own ✓/✗ and a failure leaves that row editable for a retry.

const bfx = { rows: [], venue: null };

function renderBatchFixtures() {
  const compId = state.league.competition_id;
  setupStageField("bfx");
  $("bfx-matchday").value = leagueIsCup() ? "" : nextMatchday(compId);
  $("bfx-date").value = "";
  $("bfx-date").min = IX.season.start_date;
  $("bfx-date").max = IX.season.end_date;
  $("bfx-kickoff").value = "";
  $("bfx-ref").value = "";
  fillSelect($("bfx-source"), SOURCE_TYPES, "facebook");
  fillSelect($("bfx-confidence"), CONFIDENCES, "confirmed");
  bfx.venue = makeVenueCombo($("bfx-venue"));

  bfx.rows.length = 0;
  $("bfx-rows").innerHTML = "";

  const draft = drafts.load("fixtures", compId);
  if (draft && draft.rows && draft.rows.length) {
    $("bfx-matchday").value = draft.shared.matchday;
    if (draft.shared.stage) $("bfx-stage").value = draft.shared.stage;
    $("bfx-date").value = draft.shared.date;
    $("bfx-kickoff").value = draft.shared.kickoff;
    $("bfx-ref").value = draft.shared.ref;
    $("bfx-source").value = draft.shared.source || "facebook";
    $("bfx-confidence").value = draft.shared.confidence || "confirmed";
    if (draft.shared.venueId) bfx.venue.set({ id: draft.shared.venueId, label: draft.shared.venueLabel });
    draft.rows.forEach(addFixtureRow);
    toast("Restored your unsaved fixtures");
  }
  while (bfx.rows.length < 3) addFixtureRow();
}

function saveFixtureDraft() {
  const compId = state.league.competition_id;
  const rows = bfx.rows.filter((r) => !r.saved && (r.home.value || r.away.value)).map((r) => ({
    homeId: r.home.value ? r.home.value.id : "",
    homeLabel: r.home.value ? r.home.value.label : "",
    awayId: r.away.value ? r.away.value.id : "",
    awayLabel: r.away.value ? r.away.value.label : "",
    date: r.dateEl.value,
    kickoff: r.kickoffEl.value,
    venueId: r.venue.value ? r.venue.value.id : "",
    venueLabel: r.venue.value ? r.venue.value.label : "",
  }));
  if (!rows.length) return drafts.clear("fixtures", compId);
  drafts.save("fixtures", compId, {
    shared: {
      matchday: $("bfx-matchday").value,
      stage: leagueIsCup() ? $("bfx-stage").value : "",
      date: $("bfx-date").value,
      kickoff: $("bfx-kickoff").value,
      ref: $("bfx-ref").value,
      source: $("bfx-source").value,
      confidence: $("bfx-confidence").value,
      venueId: bfx.venue.value ? bfx.venue.value.id : "",
      venueLabel: bfx.venue.value ? bfx.venue.value.label : "",
    },
    rows,
  });
}

function addFixtureRow(preset = {}) {
  const compId = state.league.competition_id;
  const entry = { saved: false };

  const homeWrap = el("div", { class: "combo" });
  const awayWrap = el("div", { class: "combo" });
  const venueWrap = el("div", { class: "combo" });
  const remove = el("button", { type: "button", class: "remove", title: "Remove this row" }, "✕");
  const date = el("input", { type: "date", min: IX.season.start_date, max: IX.season.end_date, title: "Date (overrides the shared date)" });
  const kickoff = el("input", { type: "time", title: "Kickoff (overrides the shared kickoff)" });
  const status = el("p", { class: "row-status" });

  const row = el("div", { class: "batch-row" },
    el("div", { class: "line1" }, homeWrap, el("span", { class: "v" }, "v"), awayWrap, remove),
    el("div", { class: "line-overrides" }, date, kickoff, venueWrap),
    status);
  $("bfx-rows").append(row);

  entry.row = row;
  entry.dateEl = date;
  entry.kickoffEl = kickoff;
  entry.statusEl = status;
  entry.home = makeCombo(homeWrap, {
    placeholder: "Home…",
    getItems: (q) => teamItems(compId, q, entry.away && entry.away.value ? entry.away.value.id : null),
  });
  entry.away = makeCombo(awayWrap, {
    placeholder: "Away…",
    getItems: (q) => teamItems(compId, q, entry.home && entry.home.value ? entry.home.value.id : null),
    onPick: () => {
      // Filling the last row's away team means there is probably another
      // match to come — keep an empty row waiting.
      if (bfx.rows[bfx.rows.length - 1] === entry) addFixtureRow();
      saveFixtureDraft();
    },
  });
  entry.venue = makeVenueCombo(venueWrap);

  remove.addEventListener("click", () => {
    row.remove();
    bfx.rows.splice(bfx.rows.indexOf(entry), 1);
    if (!bfx.rows.length) addFixtureRow();
    saveFixtureDraft();
  });

  if (preset.homeId) entry.home.set({ id: preset.homeId, label: preset.homeLabel });
  if (preset.awayId) entry.away.set({ id: preset.awayId, label: preset.awayLabel });
  if (preset.venueId) entry.venue.set({ id: preset.venueId, label: preset.venueLabel });
  date.value = preset.date || "";
  kickoff.value = preset.kickoff || "";

  bfx.rows.push(entry);
  return entry;
}

function lockFixtureRow(entry) {
  entry.home.setDisabled(true);
  entry.away.setDisabled(true);
  entry.venue.setDisabled(true);
  entry.dateEl.disabled = true;
  entry.kickoffEl.disabled = true;
  entry.row.classList.add("done");
}

async function submitBatchFixtures(ev) {
  ev.preventDefault();
  const compId = state.league.competition_id;
  const sharedDate = $("bfx-date").value;
  const stage = stagePayload("bfx");

  const rows = bfx.rows.filter((r) => !r.saved && (r.home.value || r.away.value || r.home.query || r.away.query));
  if (!rows.length) return toast("Nothing to save yet — pick some teams", true);
  if (rows.some((r) => !r.home.value || !r.away.value)) {
    return toast("Every match needs both teams picked from the list (or clear the row)", true);
  }
  if (rows.some((r) => r.home.value.id === r.away.value.id)) {
    return toast("A match has the same team home and away", true);
  }
  if (rows.some((r) => !(r.dateEl.value || sharedDate))) {
    return toast("Set the shared date, or give every match its own", true);
  }
  if (bfx.venue.query && !bfx.venue.value) {
    return toast("Pick the shared venue from the list, or clear it", true);
  }
  if (rows.some((r) => r.venue.query && !r.venue.value)) {
    return toast("Pick each row's venue from the list, or clear it", true);
  }
  // A team twice in one matchday is nearly always a typo, but double-headers
  // and per-row date overrides make it legal — warn, don't block.
  const seen = new Map();
  for (const r of rows) {
    for (const t of [r.home.value, r.away.value]) seen.set(t.id, (seen.get(t.id) || 0) + 1);
  }
  const twice = [...seen.entries()].filter(([, n]) => n > 1).map(([id]) => teamName(id));
  if (twice.length && !confirm(`${twice.join(", ")} appear${twice.length === 1 ? "s" : ""} in more than one match. Save anyway?`)) {
    return;
  }

  const btn = $("bfx-submit");
  btn.disabled = true;
  let saved = 0, failed = 0;
  for (const [i, r] of rows.entries()) {
    btn.textContent = `Saving ${i + 1}/${rows.length}…`;
    r.statusEl.textContent = "Saving…";
    r.statusEl.className = "row-status";
    const payload = {
      competition_id: compId,
      season_id: IX.season.season_id,
      ...stage,
      date: r.dateEl.value || sharedDate,
      kickoff: r.kickoffEl.value || $("bfx-kickoff").value,
      venue_id: (r.venue.value && r.venue.value.id) || (bfx.venue.value && bfx.venue.value.id) || "",
      home_team_id: r.home.value.id,
      away_team_id: r.away.value.id,
      source_type: $("bfx-source").value,
      source_ref: $("bfx-ref").value.trim(),
      confidence: $("bfx-confidence").value,
    };
    try {
      const res = await api("create_fixture", payload);
      pending.addCreated({ ...payload, match_id: res.match_id, status: "scheduled" });
      r.saved = true;
      r.statusEl.textContent = `✓ saved as ${res.match_id}`;
      r.statusEl.className = "row-status ok";
      lockFixtureRow(r);
      saved++;
    } catch (err) {
      r.statusEl.textContent = `✗ ${err.message}`;
      r.statusEl.className = "row-status bad";
      failed++;
    }
  }
  btn.textContent = "Save all";
  btn.disabled = false;
  saveFixtureDraft();
  toast(failed
    ? `${saved} saved, ${failed} failed — fix the marked rows and save again`
    : `Saved ${saved} fixture${saved === 1 ? "" : "s"}`, Boolean(failed));
}

// ── Batch: a day of results ──────────────────────────────────────────────────

const brs = { matches: [], live: false, rows: [] };

async function renderBatchResults(refetch = true) {
  const compId = state.league.competition_id;
  if (refetch) {
    $("brs-status").textContent = "Loading…";
    $("brs-rows").innerHTML = "";
    fillSelect($("brs-source"), SOURCE_TYPES, "facebook");
    fillSelect($("brs-confidence"), CONFIDENCES, "confirmed");
    $("brs-ref").value = "";

    let matches, live = false;
    if (settings.configured()) {
      try {
        const res = await api("live_matches", { season_id: IX.season.season_id });
        matches = res.matches.filter((m) => m.competition_id === compId);
        live = true;
      } catch (err) {
        toast(`Live fetch failed (${err.message}); using cached data`, true);
      }
    }
    if (!matches) matches = leagueMatches(compId);
    brs.matches = matches.filter((m) => m.source_type !== "placeholder");
    brs.live = live;

    // Cup rows carry no matchday, so that filter would come up empty.
    $("brs-filter-kind").value = leagueIsCup() ? "date" : "matchday";
    const draft = drafts.load("results", compId);
    if (draft && draft.shared) {
      $("brs-source").value = draft.shared.source || "facebook";
      $("brs-confidence").value = draft.shared.confidence || "confirmed";
      $("brs-ref").value = draft.shared.ref || "";
      if (draft.filterKind) $("brs-filter-kind").value = draft.filterKind;
      $("brs-include-done").checked = Boolean(draft.includeDone);
    }
    populateBatchResultFilter(draft ? draft.filterValue : null);
  }
  renderBatchResultRows();
}

/** Matchday / date choices, defaulting to the earliest one still outstanding. */
function populateBatchResultFilter(preferred) {
  const kind = $("brs-filter-kind").value;
  const sel = $("brs-filter-value");
  sel.innerHTML = "";
  sel.disabled = kind === "all";
  if (kind === "all") {
    sel.append(el("option", { value: "" }, "everything outstanding"));
    return;
  }
  const key = (m) => (kind === "matchday" ? m.matchday : m.date);
  const outstanding = new Set(brs.matches.filter(isOutstanding).map(key).filter(Boolean));
  const all = [...new Set(brs.matches.map(key).filter(Boolean))].sort((a, b) =>
    kind === "matchday" ? parseInt(a, 10) - parseInt(b, 10) : a.localeCompare(b));
  for (const v of all) {
    sel.append(el("option", { value: v },
      (kind === "matchday" ? `MD ${v}` : v) + (outstanding.has(v) ? "" : " · all entered")));
  }
  const fallback = all.find((v) => outstanding.has(v)) || all[all.length - 1] || "";
  sel.value = preferred && all.includes(preferred) ? preferred : fallback;
}

function isOutstanding(m) {
  return ["scheduled", "postponed"].includes(m.status) && !pending.load().saved[m.match_id];
}

function saveResultDraft() {
  const compId = state.league.competition_id;
  const rows = {};
  for (const r of brs.rows) {
    if (r.saved) continue;
    const snap = {
      hg: r.homeGoalsEl.value,
      ag: r.awayGoalsEl.value,
      status: r.statusSelect.value,
      replace: r.replaceEl.checked,
      scorers: r.panel.snapshot(),
    };
    const touched = snap.hg !== "" || snap.ag !== "" || snap.status !== "played" || snap.scorers.length;
    if (touched) rows[r.match.match_id] = snap;
  }
  const shared = {
    source: $("brs-source").value,
    confidence: $("brs-confidence").value,
    ref: $("brs-ref").value,
  };
  if (!Object.keys(rows).length) return drafts.clear("results", compId);
  drafts.save("results", compId, {
    shared,
    rows,
    filterKind: $("brs-filter-kind").value,
    filterValue: $("brs-filter-value").value,
    includeDone: $("brs-include-done").checked,
  });
}

function renderBatchResultRows() {
  const kind = $("brs-filter-kind").value;
  const value = $("brs-filter-value").value;
  const includeDone = $("brs-include-done").checked;
  const draft = drafts.load("results", state.league.competition_id);
  const draftRows = (draft && draft.rows) || {};

  let matches = brs.matches.filter((m) => includeDone || isOutstanding(m));
  if (kind === "matchday") matches = matches.filter((m) => m.matchday === value);
  else if (kind === "date") matches = matches.filter((m) => m.date === value);
  matches.sort((a, b) => (a.date || "9999").localeCompare(b.date || "9999") || a.match_id.localeCompare(b.match_id));

  $("brs-status").textContent = (brs.live
    ? "Live from the sheet."
    : "From the published CSV (can lag ~5 min) — configure Settings (⚙) for live data.") +
    ` ${matches.length} match${matches.length === 1 ? "" : "es"} shown.`;

  brs.rows.length = 0;
  $("brs-rows").innerHTML = "";
  for (const m of matches) addResultRow(m, draftRows[m.match_id]);
  if (!matches.length) {
    $("brs-rows").append(el("p", { class: "muted" },
      includeDone ? "Nothing here." : "Every match here already has a result — tick the box above to edit them."));
  }
}

function addResultRow(m, preset) {
  const done = ["played", "awarded"].includes(m.status) || Boolean(pending.load().saved[m.match_id]);
  const entry = { match: m, saved: false, alreadyDone: done };

  const homeGoals = el("input", { type: "number", min: "0", inputmode: "numeric" });
  const awayGoals = el("input", { type: "number", min: "0", inputmode: "numeric" });
  const statusSelect = el("select", { title: "Status" });
  for (const s of ["played", "postponed", "abandoned", "cancelled"]) {
    statusSelect.append(el("option", { value: s }, s));
  }
  const scorerToggle = el("button", { type: "button", class: "ghost scorer-toggle" }, "Scorers");
  const replaceBox = el("input", { type: "checkbox" });
  const replaceLabel = el("label", { class: "inline replace" }, replaceBox, " replace existing");
  replaceLabel.hidden = !done;
  const scorerHost = el("div", { class: "scorer-host" });
  scorerHost.hidden = true;
  const status = el("p", { class: "row-status" });

  const row = el("div", { class: "batch-row result" + (done ? " already" : "") },
    el("p", { class: "row-meta" },
      [m.matchday ? `MD ${m.matchday}` : m.stage, m.date || "no date", m.match_id].filter(Boolean).join(" · ")),
    el("div", { class: "scoreline" },
      el("div", { class: "scorebox" }, el("span", {}, teamName(m.home_team_id)), homeGoals),
      el("span", { class: "dash" }, "–"),
      el("div", { class: "scorebox" }, awayGoals, el("span", {}, teamName(m.away_team_id)))),
    el("div", { class: "line2" }, statusSelect, scorerToggle, replaceLabel),
    scorerHost,
    status);
  $("brs-rows").append(row);

  Object.assign(entry, {
    row,
    homeGoalsEl: homeGoals,
    awayGoalsEl: awayGoals,
    statusSelect,
    replaceEl: replaceBox,
    statusEl: status,
  });

  const refreshToggle = () => {
    const { home, away } = entry.panel.counts();
    const hg = homeGoals.value === "" ? "?" : homeGoals.value;
    const ag = awayGoals.value === "" ? "?" : awayGoals.value;
    scorerToggle.textContent = `Scorers ${home}/${hg} – ${away}/${ag}`;
    scorerToggle.classList.toggle("filled", home + away > 0);
    saveResultDraft();
  };
  entry.panel = makeScorerPanel(scorerHost, m, {
    getScore: () => ({
      hg: homeGoals.value === "" ? null : parseInt(homeGoals.value, 10),
      ag: awayGoals.value === "" ? null : parseInt(awayGoals.value, 10),
    }),
    onChange: refreshToggle,
  });

  scorerToggle.addEventListener("click", () => {
    scorerHost.hidden = !scorerHost.hidden;
    // Opening an empty panel on a scoring match: start it off with one row.
    if (!scorerHost.hidden && !entry.panel.rows.length && (homeGoals.value || awayGoals.value)) {
      entry.panel.addRow();
    }
  });
  for (const input of [homeGoals, awayGoals]) {
    input.addEventListener("input", refreshToggle);
  }
  statusSelect.addEventListener("change", saveResultDraft);
  replaceBox.addEventListener("change", saveResultDraft);

  if (preset) {
    homeGoals.value = preset.hg || "";
    awayGoals.value = preset.ag || "";
    statusSelect.value = preset.status || "played";
    replaceBox.checked = Boolean(preset.replace);
    for (const s of preset.scorers || []) entry.panel.addRow(s);
    if ((preset.scorers || []).length) scorerHost.hidden = false;
  }
  refreshToggle();

  brs.rows.push(entry);
  return entry;
}

async function submitBatchResults(ev) {
  ev.preventDefault();
  const jobs = [];
  for (const r of brs.rows) {
    if (r.saved) continue;
    const hg = r.homeGoalsEl.value.trim() === "" ? null : parseInt(r.homeGoalsEl.value, 10);
    const ag = r.awayGoalsEl.value.trim() === "" ? null : parseInt(r.awayGoalsEl.value, 10);
    const status = r.statusSelect.value;
    const hasScorers = r.panel.rows.length > 0;
    if (hg === null && ag === null && status === "played" && !hasScorers) continue; // untouched

    const fail = (msg) => {
      r.statusEl.textContent = `✗ ${msg}`;
      r.statusEl.className = "row-status bad";
      r.row.scrollIntoView({ block: "center", behavior: "smooth" });
      toast(`${teamName(r.match.home_team_id)} v ${teamName(r.match.away_team_id)}: ${msg}`, true);
      return null;
    };
    if ((hg === null) !== (ag === null)) return fail("enter both scores or neither");
    if (status === "played" && hg === null) return fail("a played match needs a score");
    if (r.alreadyDone && !r.replaceEl.checked) return fail("tick “replace existing” to overwrite this result");
    let goals;
    try { goals = r.panel.collect(); }
    catch (err) { return fail(err.message); }
    const { home, away } = r.panel.counts();
    if (hg !== null && (home > hg || away > ag)) return fail("more scorer rows than goals on one side");
    if (hg === null && goals.length) return fail("goal rows need a score");

    r.statusEl.textContent = "";
    jobs.push({ row: r, hg, ag, status, goals });
  }
  if (!jobs.length) return toast("Nothing filled in yet", true);

  const btn = $("brs-submit");
  btn.disabled = true;
  let saved = 0, failed = 0;
  for (const [i, job] of jobs.entries()) {
    const r = job.row;
    btn.textContent = `Saving ${i + 1}/${jobs.length}…`;
    r.statusEl.textContent = "Saving…";
    r.statusEl.className = "row-status";
    try {
      await api("save_result", {
        match_id: r.match.match_id,
        home_goals: job.hg === null ? "" : String(job.hg),
        away_goals: job.ag === null ? "" : String(job.ag),
        status: job.status,
        source_type: $("brs-source").value,
        source_ref: $("brs-ref").value.trim(),
        confidence: $("brs-confidence").value,
        replace_goals: Boolean(r.alreadyDone && r.replaceEl.checked),
        goals: job.goals,
      });
      pending.addSaved(r.match.match_id, { status: job.status, home_goals: job.hg, away_goals: job.ag });
      r.saved = true;
      r.statusEl.textContent = `✓ saved (${job.goals.length} goal${job.goals.length === 1 ? "" : "s"})`;
      r.statusEl.className = "row-status ok";
      r.homeGoalsEl.disabled = true;
      r.awayGoalsEl.disabled = true;
      r.statusSelect.disabled = true;
      r.replaceEl.disabled = true;
      r.panel.setDisabled(true);
      r.row.classList.add("done");
      saved++;
    } catch (err) {
      r.statusEl.textContent = `✗ ${err.message}`;
      r.statusEl.className = "row-status bad";
      failed++;
    }
  }
  btn.textContent = "Save all";
  btn.disabled = false;
  saveResultDraft();
  toast(failed
    ? `${saved} saved, ${failed} failed — fix the marked rows and save again`
    : `Saved ${saved} result${saved === 1 ? "" : "s"}`, Boolean(failed));
}

// ── New player modal ─────────────────────────────────────────────────────────

let playerModalTarget = null;

function openPlayerModal(scorerEntry) {
  playerModalTarget = scorerEntry;
  $("pl-name").value = scorerEntry ? scorerEntry.combo.query : "";
  $("pl-known").value = "";
  $("pl-position").value = "";
  $("pl-dob").value = "";
  $("player-modal").showModal();
  $("pl-name").focus();
}

async function submitPlayer(ev) {
  ev.preventDefault();
  const fullName = $("pl-name").value.trim();
  if (!fullName) return;
  const dup = IX.players.find((p) => p.full_name.toLowerCase() === fullName.toLowerCase());
  if (dup && !confirm(`"${dup.full_name}" already exists (${dup.player_id}). Create another player with the same name?`)) {
    return;
  }
  const btn = $("pl-save");
  btn.disabled = true;
  try {
    const res = await api("create_player", {
      full_name: fullName,
      known_as: $("pl-known").value.trim(),
      dob: $("pl-dob").value,
      position: $("pl-position").value,
      nationality: "",
    });
    const item = {
      player_id: res.player_id,
      label: $("pl-known").value.trim() || fullName,
      full_name: fullName,
      team_id: playerModalTarget ? playerModalTarget.teamId : "",
      search: `${fullName} ${$("pl-known").value}`.toLowerCase(),
    };
    IX.players.push(item);
    if (playerModalTarget) {
      playerModalTarget.combo.set({ id: res.player_id, label: item.label });
    }
    $("player-modal").close();
    toast(`Created ${res.player_id}: ${item.label}`);
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

// ── New venue modal ──────────────────────────────────────────────────────────

let venueModalTarget = null;
let venueCodeEdited = false;

function openVenueModal(combo) {
  venueModalTarget = combo;
  venueCodeEdited = false;
  $("vn-name").value = combo ? combo.query : "";
  $("vn-city").value = "";
  $("vn-capacity").value = "";
  $("vn-code").value = suggestVenueId($("vn-name").value);
  $("venue-modal").showModal();
  $("vn-name").focus();
}

async function submitVenue(ev) {
  ev.preventDefault();
  const name = $("vn-name").value.trim();
  const code = $("vn-code").value.trim().toUpperCase();
  if (!name || !code) return;
  const dup = IX.venues.find((v) => v.label.toLowerCase() === name.toLowerCase());
  if (dup && !confirm(`"${dup.label}" already exists (${dup.venue_id}). Add a second venue with the same name?`)) {
    return;
  }
  const btn = $("vn-save");
  btn.disabled = true;
  try {
    const res = await api("create_venue", {
      name,
      city: $("vn-city").value.trim(),
      capacity: $("vn-capacity").value.trim(),
      venue_id: code,
    });
    const item = {
      venue_id: res.venue_id,
      label: name,
      city: $("vn-city").value.trim(),
      search: `${name} ${$("vn-city").value}`.toLowerCase(),
    };
    IX.venues.push(item);
    IX.venues.sort((a, b) => a.label.localeCompare(b.label));
    IX.venueIds.add(res.venue_id.toUpperCase());
    if (venueModalTarget) venueModalTarget.set({ id: res.venue_id, label: name });
    $("venue-modal").close();
    // The script appends a suffix if the code was taken since the page loaded.
    toast(res.venue_id === code
      ? `Created ${res.venue_id}: ${name}`
      : `Created ${res.venue_id}: ${name} (${code} was taken)`);
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

// ── Settings modal ───────────────────────────────────────────────────────────

function openSettings() {
  const s = settings.load();
  $("set-url").value = s.url || "";
  $("set-token").value = s.token || "";
  $("set-ping-result").textContent = "";
  $("settings-modal").showModal();
}

async function pingScript() {
  const out = $("set-ping-result");
  out.textContent = "Pinging…";
  settings.save({ url: $("set-url").value.trim(), token: $("set-token").value.trim() });
  try {
    const res = await api("ping");
    out.textContent = `OK — script v${res.version}, sheet "${res.spreadsheet_name}"`;
  } catch (err) {
    out.textContent = `Failed: ${err.message}`;
  }
}

// ── Boot ─────────────────────────────────────────────────────────────────────

async function boot() {
  banner("");
  try {
    await loadData();
  } catch (err) {
    const b = $("banner");
    b.innerHTML = "";
    b.append(`Could not load data: ${err.message}`, el("button", { class: "ghost", onclick: boot }, "Retry"));
    b.hidden = false;
    return;
  }
  renderHome();
  showView("view-home", false);
  state.stack = ["view-home"];
}

document.addEventListener("DOMContentLoaded", () => {
  $("back-btn").addEventListener("click", goBack);
  $("settings-btn").addEventListener("click", openSettings);
  $("goto-fixture").addEventListener("click", () => { renderFixtureForm(); showView("view-fixture"); });
  $("goto-result").addEventListener("click", () => { renderPicker(); showView("view-picker"); });
  $("goto-batch-fixture").addEventListener("click", () => { renderBatchFixtures(); showView("view-batch-fixtures"); });
  $("goto-batch-result").addEventListener("click", () => { renderBatchResults(); showView("view-batch-results"); });
  $("fixture-form").addEventListener("submit", submitFixture);
  $("result-form").addEventListener("submit", submitResult);
  $("rs-status").addEventListener("change", updateScorerUi);
  $("rs-home-goals").addEventListener("input", updateScorerUi);
  $("rs-away-goals").addEventListener("input", updateScorerUi);

  $("bfx-add").addEventListener("click", () => addFixtureRow().home.focus());
  $("bfx-form").addEventListener("submit", submitBatchFixtures);
  $("bfx-form").addEventListener("input", debounce(saveFixtureDraft, 400));
  $("brs-form").addEventListener("submit", submitBatchResults);
  $("brs-form").addEventListener("input", debounce(saveResultDraft, 400));
  $("brs-filter-kind").addEventListener("change", () => { populateBatchResultFilter(); renderBatchResults(false); });
  $("brs-filter-value").addEventListener("change", () => renderBatchResults(false));
  $("brs-include-done").addEventListener("change", () => renderBatchResults(false));

  $("player-form").addEventListener("submit", submitPlayer);
  $("pl-cancel").addEventListener("click", () => $("player-modal").close());
  $("venue-form").addEventListener("submit", submitVenue);
  $("vn-cancel").addEventListener("click", () => $("venue-modal").close());
  $("vn-code").addEventListener("input", () => { venueCodeEdited = true; });
  $("vn-name").addEventListener("input", () => {
    if (!venueCodeEdited) $("vn-code").value = suggestVenueId($("vn-name").value);
  });
  $("set-ping").addEventListener("click", pingScript);
  $("settings-form").addEventListener("submit", () => {
    settings.save({ url: $("set-url").value.trim(), token: $("set-token").value.trim() });
    toast("Settings saved");
  });
  boot();
});
