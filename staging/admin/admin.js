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
  IX.venues = DB.venues;
  IX.venueByName = Object.fromEntries(DB.venues.map((v) => [v.name.toLowerCase(), v.venue_id]));

  IX.leagues = DB.competition_seasons
    .filter((cs) => cs.season_id === IX.season.season_id)
    .map((cs) => ({
      ...cs,
      display: cs.sponsor_name || IX.competitions[cs.competition_id]?.name || cs.competition_id,
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
    set(item) { pick(item); },
    clear() {
      selected = null; input.value = "";
      input.classList.remove("selected");
      items = []; render();
    },
    focus() { input.focus(); },
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

// ── Views / navigation ───────────────────────────────────────────────────────

const VIEWS = ["view-home", "view-league", "view-fixture", "view-picker", "view-result"];

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

// ── New fixture ──────────────────────────────────────────────────────────────

let fxHome, fxAway;

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
  if (!keepSticky) {
    const mds = leagueMatches(compId).map((m) => parseInt(m.matchday, 10)).filter(Number.isFinite);
    $("fx-matchday").value = mds.length ? Math.max(...mds) + 1 : 1;
    $("fx-date").value = "";
  }
  $("fx-date").min = IX.season.start_date;
  $("fx-date").max = IX.season.end_date;
  $("fx-kickoff").value = "";
  $("fx-venue").value = "";
  $("fx-ref").value = "";

  const dl = $("venue-list");
  dl.innerHTML = "";
  for (const v of IX.venues) dl.append(el("option", { value: v.name }));

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

  const venueText = $("fx-venue").value.trim();
  const venueId = venueText ? IX.venueByName[venueText.toLowerCase()] : "";
  if (venueText && !venueId) return toast("Pick a venue from the list (or clear it)", true);

  const matchday = $("fx-matchday").value.trim();
  const payload = {
    competition_id: compId,
    season_id: IX.season.season_id,
    stage: matchday ? `md_${matchday}` : "",
    matchday,
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

const scorers = []; // [{teamId, combo, row, minuteEl, stoppageEl, typeEl}]

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

  scorers.length = 0;
  $("scorer-rows").innerHTML = "";
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
  let home = 0, away = 0;
  for (const s of scorers) {
    if (s.teamId === m.home_team_id) home++;
    else away++;
  }
  $("scorer-counter").textContent =
    `— ${home}/${hg} ${teamName(m.home_team_id)}, ${away}/${ag} ${teamName(m.away_team_id)}`;
}

function defaultScorerTeam() {
  const m = state.match;
  const hg = scoreVal("rs-home-goals") ?? 0, ag = scoreVal("rs-away-goals") ?? 0;
  let home = 0, away = 0;
  for (const s of scorers) {
    if (s.teamId === m.home_team_id) home++;
    else away++;
  }
  return home < hg ? m.home_team_id : away < ag ? m.away_team_id : m.home_team_id;
}

function addScorerRow() {
  const m = state.match;
  const leagueTeams = (IX.teamsByLeague[m.competition_id] || []).map((t) => t.team_id);
  const entry = { teamId: defaultScorerTeam() };

  const toggle = el("button", { type: "button", class: "team-toggle" }, teamName(entry.teamId));
  toggle.addEventListener("click", () => {
    entry.teamId = entry.teamId === m.home_team_id ? m.away_team_id : m.home_team_id;
    toggle.textContent = teamName(entry.teamId);
    updateScorerUi();
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
  $("scorer-rows").append(row);

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
    scorers.splice(scorers.indexOf(entry), 1);
    updateScorerUi();
  });

  scorers.push(entry);
  updateScorerUi();
  entry.combo.focus();
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

  const goals = [];
  let home = 0, away = 0;
  for (const s of scorers) {
    if (!s.combo.value) return toast("Every scorer row needs a player (or remove the row)", true);
    if (s.teamId === m.home_team_id) home++; else away++;
    const minute = s.minuteEl.value.trim();
    goals.push({
      team_id: s.teamId,
      player_id: s.combo.value.id,
      minute,
      stoppage: s.stoppageEl.value.trim(),
      period: minute === "" ? "" : parseInt(minute, 10) <= 45 ? "1h" : "2h",
      goal_type: s.typeEl.value,
    });
  }
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

// ── New player modal ─────────────────────────────────────────────────────────

let playerModalTarget = null;

function openPlayerModal(scorerEntry) {
  playerModalTarget = scorerEntry;
  $("pl-name").value = "";
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
  $("fixture-form").addEventListener("submit", submitFixture);
  $("result-form").addEventListener("submit", submitResult);
  $("add-scorer").addEventListener("click", addScorerRow);
  $("rs-status").addEventListener("change", updateScorerUi);
  $("rs-home-goals").addEventListener("input", updateScorerUi);
  $("rs-away-goals").addEventListener("input", updateScorerUi);
  $("player-form").addEventListener("submit", submitPlayer);
  $("pl-cancel").addEventListener("click", () => $("player-modal").close());
  $("set-ping").addEventListener("click", pingScript);
  $("settings-form").addEventListener("submit", () => {
    settings.save({ url: $("set-url").value.trim(), token: $("set-token").value.trim() });
    toast("Settings saved");
  });
  boot();
});
