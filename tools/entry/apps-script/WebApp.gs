/**
 * EverLeague data-entry web app.
 *
 * Lives as an EXTRA FILE in the sheet's existing bound Apps Script project,
 * alongside the Fast Entry sidebar (Code.gs + Sidebar.html) — do not replace
 * those. All .gs files in a project share one global namespace, so everything
 * here except doGet/doPost is suffixed with "_" (which also makes it private:
 * hidden from google.script.run and the editor's Run menu) and constants are
 * namespaced under ENTRY. If the sidebar's Code.gs ever grows a doGet or
 * doPost of its own, the two will collide — keep those names unique to this
 * file.
 *
 * The static UI at /admin/ POSTs JSON here; this is the only write path into
 * the sheet. The sheet stays the single source of truth and validate.py
 * remains the build gate — this script only does cheap re-checks.
 *
 * Deploy: add this file to the bound project, then Deploy > New deployment >
 * Web app, "Execute as: Me", "Who has access: Anyone". Set the shared token
 * in Project Settings > Script Properties: ENTRY_TOKEN. Full procedure:
 * tools/entry/README.md. Bump ENTRY.VERSION on every change — the UI's ping
 * check compares it against the repo copy.
 *
 * API: POST body is JSON {token, action, payload}. Every response is JSON
 * {ok:true, ...} or {ok:false, error} with HTTP 200 (Apps Script cannot set
 * status codes or response headers; the client sends a text/plain body so the
 * request stays a CORS "simple request" — never require custom headers here).
 */

var ENTRY = {
  VERSION: "1",
  // Mirrors src/dataset.py enums — keep in sync by hand.
  MATCH_STATUSES: ["scheduled", "played", "postponed", "abandoned", "awarded", "cancelled"],
  SOURCE_TYPES: ["reporter", "rfa", "fa", "club", "facebook", "newspaper", "whatsapp", "backfill", "placeholder", "unknown"],
  CONFIDENCES: ["unconfirmed", "confirmed", "official"],
  GOAL_TYPES: ["", "open_play", "penalty", "free_kick", "header", "own_goal"],
  PERIODS: ["", "1h", "2h", "et", "pens"],
  UNKNOWN_PLAYER: "CAF_MW_UNKNOWN",
};

// ── Entry points (the only global names this file claims besides ENTRY) ──────

function doGet(e) {
  var token = e && e.parameter ? e.parameter.token : "";
  if (!entryTokenOk_(token)) return entryJson_({ ok: false, error: "bad token" });
  return entryJson_({
    ok: true,
    version: ENTRY.VERSION,
    spreadsheet_name: SpreadsheetApp.getActiveSpreadsheet().getName(),
  });
}

function doPost(e) {
  var body;
  try {
    body = JSON.parse(e.postData.contents);
  } catch (err) {
    return entryJson_({ ok: false, error: "body is not valid JSON" });
  }
  if (!entryTokenOk_(body.token)) return entryJson_({ ok: false, error: "bad token" });

  var payload = body.payload || {};
  try {
    switch (body.action) {
      case "ping":
        return doGet({ parameter: { token: body.token } });
      case "live_matches":
        return entryJson_(entryLiveMatches_(payload));
      case "create_fixture":
        return entryJson_(entryWithLock_(function () { return entryCreateFixture_(payload); }));
      case "save_result":
        return entryJson_(entryWithLock_(function () { return entrySaveResult_(payload); }));
      case "create_player":
        return entryJson_(entryWithLock_(function () { return entryCreatePlayer_(payload); }));
      default:
        return entryJson_({ ok: false, error: "unknown action: " + body.action });
    }
  } catch (err) {
    return entryJson_({ ok: false, error: String(err && err.message ? err.message : err) });
  }
}

// ── Actions ──────────────────────────────────────────────────────────────────

/**
 * Live read of the matches tab. The published-CSV endpoints the UI reads lag
 * by ~5 minutes; the result picker uses this to see authoritative statuses.
 */
function entryLiveMatches_(p) {
  var t = entryReadTab_("matches");
  var out = [];
  var want = ["match_id", "competition_id", "season_id", "stage", "matchday",
              "date", "kickoff", "venue_id", "home_team_id", "away_team_id",
              "home_goals", "away_goals", "status", "source_type"];
  for (var i = 0; i < t.rows.length; i++) {
    var r = t.rows[i];
    if (p.season_id && entryGet_(t, r, "season_id") !== p.season_id) continue;
    var m = {};
    for (var j = 0; j < want.length; j++) m[want[j]] = entryGet_(t, r, want[j]);
    out.push(m);
  }
  return { ok: true, matches: out };
}

function entryCreateFixture_(p) {
  entryNeed_(p, ["competition_id", "season_id", "date", "home_team_id", "away_team_id"]);
  entryMustEnum_(p.source_type, ENTRY.SOURCE_TYPES, "source_type");
  entryMustEnum_(p.confidence, ENTRY.CONFIDENCES, "confidence");
  entryMustDate_(p.date, "date");
  if (p.home_team_id === p.away_team_id) entryFail_("home and away team are the same");

  var matchId = entryNextMatchId_(p.competition_id, p.season_id);
  entryAppendRow_("matches", {
    match_id: matchId,
    competition_id: p.competition_id,
    season_id: p.season_id,
    stage: p.stage || "",
    matchday: p.matchday || "",
    date: p.date,
    kickoff: p.kickoff || "",
    venue_id: p.venue_id || "",
    home_team_id: p.home_team_id,
    away_team_id: p.away_team_id,
    home_goals: "",
    away_goals: "",
    status: "scheduled",
    awarded_note: "",
    source_type: p.source_type,
    source_ref: p.source_ref || "",
    reported_by: p.reported_by || "",
    reported_at: entryNowIso_(),
    confidence: p.confidence,
    verified_by: "",
    verified_at: "",
  });
  return { ok: true, match_id: matchId };
}

function entrySaveResult_(p) {
  entryNeed_(p, ["match_id", "status"]);
  entryMustEnum_(p.status, ENTRY.MATCH_STATUSES, "status");
  if (p.status === "scheduled") entryFail_("save_result cannot set status back to scheduled");
  entryMustEnum_(p.source_type, ENTRY.SOURCE_TYPES, "source_type");
  entryMustEnum_(p.confidence, ENTRY.CONFIDENCES, "confidence");
  if (p.status === "awarded" && !p.awarded_note) entryFail_("awarded matches need awarded_note");

  // Mirrors validate.py check 4: played/awarded need a full score; the other
  // statuses may carry one (abandoned mid-game) or none (postponed), never
  // one-sided. Goal rows require a score (check 5).
  var hasScore = p.home_goals !== "" && p.home_goals != null;
  var hasAway = p.away_goals !== "" && p.away_goals != null;
  if (hasScore !== hasAway) entryFail_("provide both scores or neither");
  if ((p.status === "played" || p.status === "awarded") && !hasScore) {
    entryFail_("status " + p.status + " needs both scores");
  }
  var hg = hasScore ? entryMustCount_(p.home_goals, "home_goals") : null;
  var ag = hasScore ? entryMustCount_(p.away_goals, "away_goals") : null;
  if (!hasScore && (p.goals || []).length) entryFail_("goal rows need a score");

  var t = entryReadTab_("matches");
  var rowIdx = -1;
  for (var i = 0; i < t.rows.length; i++) {
    if (entryGet_(t, t.rows[i], "match_id") === p.match_id) { rowIdx = i; break; }
  }
  if (rowIdx === -1) entryFail_("match not found: " + p.match_id);
  var row = t.rows[rowIdx];
  var current = entryGet_(t, row, "status");
  if ((current === "played" || current === "awarded") && !p.replace_goals) {
    entryFail_("match " + p.match_id + " is already " + current +
               "; send replace_goals:true to overwrite it");
  }
  var home = entryGet_(t, row, "home_team_id");
  var away = entryGet_(t, row, "away_team_id");

  var goals = p.goals || [];
  var perSide = {};
  perSide[home] = 0;
  perSide[away] = 0;
  for (var g = 0; g < goals.length; g++) {
    entryNeed_(goals[g], ["team_id", "player_id"]);
    entryMustEnum_(goals[g].goal_type || "", ENTRY.GOAL_TYPES, "goal_type");
    entryMustEnum_(goals[g].period || "", ENTRY.PERIODS, "period");
    if (!(goals[g].team_id in perSide)) {
      entryFail_("goal team " + goals[g].team_id + " is not a participant of " + p.match_id);
    }
    perSide[goals[g].team_id]++;
  }
  if (hasScore && perSide[home] > hg) entryFail_("more home goal rows (" + perSide[home] + ") than home_goals (" + hg + ")");
  if (hasScore && perSide[away] > ag) entryFail_("more away goal rows (" + perSide[away] + ") than away_goals (" + ag + ")");

  if (p.replace_goals) entryDeleteGoalsForMatch_(p.match_id);

  // Update the match row in place (sheet row = data index + 2: 1-based + header).
  entryUpdateCells_("matches", rowIdx + 2, {
    home_goals: hasScore ? String(hg) : "",
    away_goals: hasScore ? String(ag) : "",
    status: p.status,
    awarded_note: p.awarded_note || "",
    source_type: p.source_type,
    source_ref: p.source_ref || "",
    reported_by: p.reported_by || "",
    reported_at: entryNowIso_(),
    confidence: p.confidence,
  });

  var names = entryPlayerNames_();
  var goalIds = [];
  for (var k = 0; k < goals.length; k++) {
    var gid = entryNextGoalId_(entryGet_(t, row, "competition_id"), entryGet_(t, row, "season_id"));
    var gp = goals[k];
    entryAppendRow_("goals", {
      goal_id: gid,
      match_id: p.match_id,
      team_id: gp.team_id,
      player_name: gp.player_id === ENTRY.UNKNOWN_PLAYER ? "" : (names[gp.player_id] || ""),
      player_id: gp.player_id,
      minute: gp.minute || "",
      stoppage: gp.stoppage || "",
      period: gp.period || "",
      goal_type: gp.goal_type || "",
      assist_player_id: gp.assist_player_id || "",
      source_type: p.source_type,
      source_ref: p.source_ref || "",
      reported_by: p.reported_by || "",
      reported_at: entryNowIso_(),
      confidence: p.confidence,
      verified_by: "",
      verified_at: "",
    });
    goalIds.push(gid);
  }
  return { ok: true, match_id: p.match_id, goal_ids: goalIds };
}

function entryCreatePlayer_(p) {
  entryNeed_(p, ["full_name"]);
  if (p.dob) entryMustDate_(p.dob, "dob");
  var playerId = entryNextPlayerId_();
  entryAppendRow_("players", {
    player_id: playerId,
    full_name: p.full_name,
    known_as: p.known_as || "",
    dob: p.dob || "",
    position: p.position || "",
    nationality: p.nationality || "",
    status: "active",
  });
  return { ok: true, player_id: playerId };
}

// ── ID minting ───────────────────────────────────────────────────────────────
// The sheet is the arbiter of IDs: mint from live data under LockService so
// the client's stale CSV view can never cause a collision. Constructing an ID
// follows the sibling convention; the fallback (no siblings yet) rebuilds the
// documented pattern from the season label (2026/27 -> "2627").

function entryNextMatchId_(competitionId, seasonId) {
  var t = entryReadTab_("matches");
  var sib = entryMaxSibling_(t, "match_id", function (r) {
    return entryGet_(t, r, "competition_id") === competitionId &&
           entryGet_(t, r, "season_id") === seasonId;
  });
  if (sib) return sib.prefix + entryPad_(sib.max + 1, sib.width);
  return competitionId + "_" + entrySeasonShort_(seasonId) + "_" + entryPad_(1, 3);
}

function entryNextGoalId_(competitionId, seasonId) {
  var matches = entryReadTab_("matches");
  var inScope = {};
  for (var i = 0; i < matches.rows.length; i++) {
    var r = matches.rows[i];
    if (entryGet_(matches, r, "competition_id") === competitionId &&
        entryGet_(matches, r, "season_id") === seasonId) {
      inScope[entryGet_(matches, r, "match_id")] = true;
    }
  }
  var t = entryReadTab_("goals");
  var sib = entryMaxSibling_(t, "goal_id", function (r) {
    return inScope[entryGet_(t, r, "match_id")] === true;
  });
  if (sib) return sib.prefix + entryPad_(sib.max + 1, sib.width);
  return competitionId + "_" + entrySeasonShort_(seasonId) + "_G-" + entryPad_(1, 4);
}

function entryNextPlayerId_() {
  var t = entryReadTab_("players");
  var max = 0;
  for (var i = 0; i < t.rows.length; i++) {
    var m = /^CAF_MW_(\d+)$/.exec(entryGet_(t, t.rows[i], "player_id"));
    if (m) max = Math.max(max, parseInt(m[1], 10));
  }
  return "CAF_MW_" + entryPad_(max + 1, 6);
}

/** Max numeric suffix (text after the last non-digit run) among matching rows. */
function entryMaxSibling_(t, idCol, match) {
  var best = null;
  for (var i = 0; i < t.rows.length; i++) {
    if (!match(t.rows[i])) continue;
    var id = entryGet_(t, t.rows[i], idCol);
    var m = /^(.*?)(\d+)$/.exec(id);
    if (!m) continue;
    var n = parseInt(m[2], 10);
    if (!best || n > best.max) {
      best = { prefix: m[1], max: n, width: m[2].length };
    }
  }
  return best;
}

/** "MW_2026_27" -> "2627", via the seasons tab label ("2026/27"). */
function entrySeasonShort_(seasonId) {
  var t = entryReadTab_("seasons");
  for (var i = 0; i < t.rows.length; i++) {
    if (entryGet_(t, t.rows[i], "season_id") !== seasonId) continue;
    var parts = entryGet_(t, t.rows[i], "label").split("/");
    if (parts.length === 2) return parts[0].slice(-2) + parts[1].slice(-2);
    entryFail_("cannot derive season short code from label " + entryGet_(t, t.rows[i], "label"));
  }
  entryFail_("unknown season_id: " + seasonId);
}

// ── Sheet access (header-mapped, text-formatted) ─────────────────────────────

function entrySheetByName_(name) {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(name);
  if (!sh) entryFail_("no sheet tab named " + name);
  return sh;
}

/** {sheet, cols:{name:index}, width, rows:[[...display strings]]} for one tab. */
function entryReadTab_(name) {
  var sh = entrySheetByName_(name);
  var values = sh.getDataRange().getDisplayValues();
  var cols = {};
  for (var c = 0; c < values[0].length; c++) {
    var h = String(values[0][c]).trim();
    if (h) cols[h] = c;
  }
  return { sheet: sh, cols: cols, width: values[0].length, rows: values.slice(1) };
}

function entryGet_(t, row, col) {
  if (!(col in t.cols)) entryFail_(t.sheet.getName() + " has no column " + col);
  return String(row[t.cols[col]] == null ? "" : row[t.cols[col]]).trim();
}

/**
 * Append one row, mapped by header name. Errors on a payload key with no
 * column and on any sheet column absent from the record — a schema change in
 * either place must break loudly, not write a misaligned row. The range is
 * set to plain-text format BEFORE values land so Sheets cannot coerce dates
 * or times into serials that would re-emerge mangled in the published CSV.
 */
function entryAppendRow_(name, record) {
  var t = entryReadTab_(name);
  var arr = [];
  for (var c = 0; c < t.width; c++) arr.push("");
  for (var col in t.cols) {
    if (!(col in record)) entryFail_(name + ": record is missing column " + col);
    arr[t.cols[col]] = String(record[col]);
  }
  for (var key in record) {
    if (!(key in t.cols)) entryFail_(name + " has no column " + key);
  }
  var rowNum = t.sheet.getLastRow() + 1;
  var range = t.sheet.getRange(rowNum, 1, 1, t.width);
  range.setNumberFormat("@");
  range.setValues([arr]);
}

/** Update named cells of one sheet row (1-based, header = row 1), text-formatted. */
function entryUpdateCells_(name, rowNum, record) {
  var t = entryReadTab_(name);
  for (var col in record) {
    if (!(col in t.cols)) entryFail_(name + " has no column " + col);
    var cell = t.sheet.getRange(rowNum, t.cols[col] + 1);
    cell.setNumberFormat("@");
    cell.setValue(String(record[col]));
  }
}

/** Delete all goal rows of one match, bottom-up so indices stay valid. */
function entryDeleteGoalsForMatch_(matchId) {
  var t = entryReadTab_("goals");
  for (var i = t.rows.length - 1; i >= 0; i--) {
    if (entryGet_(t, t.rows[i], "match_id") === matchId) {
      t.sheet.deleteRow(i + 2);
    }
  }
}

/** {player_id: display name} for filling the denormalized goals.player_name. */
function entryPlayerNames_() {
  var t = entryReadTab_("players");
  var out = {};
  for (var i = 0; i < t.rows.length; i++) {
    var r = t.rows[i];
    out[entryGet_(t, r, "player_id")] = entryGet_(t, r, "known_as") || entryGet_(t, r, "full_name");
  }
  return out;
}

// ── Plumbing ─────────────────────────────────────────────────────────────────

function entryTokenOk_(token) {
  var expected = PropertiesService.getScriptProperties().getProperty("ENTRY_TOKEN");
  return Boolean(expected) && token === expected;
}

function entryWithLock_(fn) {
  var lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    return fn();
  } finally {
    lock.releaseLock();
  }
}

function entryJson_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function entryNowIso_() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function entryFail_(msg) {
  throw new Error(msg);
}

function entryNeed_(obj, keys) {
  for (var i = 0; i < keys.length; i++) {
    var v = obj[keys[i]];
    if (v === undefined || v === null || v === "") entryFail_("missing " + keys[i]);
  }
}

function entryMustEnum_(value, allowed, label) {
  if (allowed.indexOf(value) === -1) {
    entryFail_(label + " " + JSON.stringify(value) + " not in " +
               allowed.filter(function (a) { return a; }).join("|"));
  }
}

function entryMustDate_(value, label) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) entryFail_(label + " must be YYYY-MM-DD, got " + JSON.stringify(value));
}

function entryMustCount_(value, label) {
  var n = Number(value);
  if (!Number.isInteger(n) || n < 0) entryFail_(label + " must be an integer >= 0");
  return n;
}

function entryPad_(n, width) {
  var s = String(n);
  while (s.length < width) s = "0" + s;
  return s;
}
