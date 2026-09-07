/* The matchday grid's rules, with no DOM and no network in them.
 *
 * WHY THIS IS A SEPARATE FILE. Everything here is a decision the grid makes
 * about a reporter's typing — which lines changed, which of them are safe to
 * send, what to do with the answer that comes back, and whether a result has
 * any provenance at all. Those are the rules that must survive a dropped
 * connection, and in app.js they would be tangled with innerHTML and be
 * checkable only by hand on a phone. Here they are ordinary functions over
 * ordinary objects, so `node --test tests/js/` can ask the one question that
 * matters most and cannot be asked of a rendered screen: after a failure, is
 * everything the reporter typed still there?
 *
 * It imports nothing, on purpose. The moment this file needs `document` or
 * `supabase`, it stops being testable and the tests quietly stop meaning
 * anything.
 *
 * The row is the unit. It holds TWO versions of the same three facts:
 *
 *   row.saved   what the database had when this screen was drawn
 *   row.home / row.away / row.status   what the reporter has since typed
 *
 * Everything below is a question about the difference between those two. That
 * is what makes "only changed rows are submitted", "unpublished changes look
 * different from saved data" and the conflict guard one idea rather than three.
 */

// The two statuses that carry a score. Same pair as validate.py check 4 and as
// apply_match_report in 0041 — a row can only ever be in one of the six
// statuses the database allows, and only these two may hold goals.
export const SCORED = ["played", "awarded"];

export const isScored = (status) => SCORED.includes(status);

/** A status the fixture has been settled by, one way or another. Anything
 *  other than 'scheduled' means somebody has already said what happened, which
 *  is what makes changing it a correction rather than a first report. */
export const isDecided = (status) => Boolean(status) && status !== "scheduled";

/** May a score be TYPED into this row?
 *
 *  Deliberately not isScored(). isScored asks what the database will store,
 *  and 'scheduled' stores no score — but 'scheduled' is the state a fixture
 *  is in before anyone has said anything, and typing the score is exactly how
 *  it leaves that state (see setScore). Gating the boxes on isScored disables
 *  them on the one row every reporter opens this screen to fill in.
 *
 *  What genuinely accepts nothing is a match somebody has said did not happen:
 *  postponed, abandoned, cancelled. Those boxes are disabled, and the reporter
 *  changes the status first if they meant something else. */
export const acceptsScore = (status) =>
  isScored(status) || status === "scheduled";

/** One line of the grid, built from the match row the portal already loads.
 *  `saved` is a snapshot, never mutated afterwards: it is the thing every
 *  comparison below is against, and updating it in place would make a changed
 *  row look unchanged. */
export function gridRow(match) {
  const saved = {
    home: match.home_goals ?? null,
    away: match.away_goals ?? null,
    status: match.status || "scheduled",
    sourceRef: (match.source_ref || "").trim(),
  };
  return {
    matchId: match.match_id,
    publicId: match.public_id,
    homeName: match.home?.display_name || match.home_team_id,
    awayName: match.away?.display_name || match.away_team_id,
    // KEPT NOW, AND THEY WERE NOT BEFORE. A score needs only the match; a goal
    // needs the side it counted for, and goals.team_id is the beneficiary
    // rather than anything derivable from the scorer's name. The names above
    // are for drawing and can fall back to an id; these two are identity and
    // never fall back to anything.
    homeTeamId: match.home_team_id,
    awayTeamId: match.away_team_id,
    // Goal rows the database already holds for this match, per side, so the
    // screen can say "all 2 of Bullets' goals already have a scorer" before
    // the RPC does. Absent on a match nobody has reported a scorer for, which
    // is nearly all of them — hence the zeroes.
    existing: {
      home: Number(match.scorer_count_home || 0),
      away: Number(match.scorer_count_away || 0),
    },
    // Staged, unsaved scorers. See the Scorers section at the foot of this
    // file: they are a SECOND pass over a row that has already published.
    scorers: [],
    date: match.date || "",
    kickoff: match.kickoff || "",
    stage: match.stage || "",
    saved,
    // Entered values start AS the saved ones, so an untouched row is
    // unchanged by definition and cannot be republished by accident.
    home: saved.home == null ? "" : String(saved.home),
    away: saved.away == null ? "" : String(saved.away),
    status: saved.status,
    // Set only by the reporter tapping "Replace 2–1" on a row that already
    // carries a result. It is what drops the conflict guard for that row.
    confirmed: false,
    error: "",
    // True once this session has actually published the row.
    published: false,
  };
}

/** A typed score. Digits only, 0..99 — a negative score is meaningless and a
 *  three-digit one is a fat finger, not a scoreline. (The same clamp the
 *  single-match steppers apply, and the same range apply_match_report
 *  enforces.) */
export function setScore(row, side, raw) {
  const digits = String(raw ?? "").replace(/[^0-9]/g, "").slice(0, 2);
  const next = digits === "" ? "" : String(Math.min(99, Number(digits)));
  // EDITING A ROW CLEARS ITS ERROR, and this is the only place that clears
  // one. See collectReports for what went wrong when clearing lived there.
  if (row[side] !== next) row.error = "";
  row[side] = next;
  // A SCORE MEANS IT WAS PLAYED. Typing 2–1 onto a fixture and then having to
  // also tell the app that it happened is a step with no information in it.
  // Only from 'scheduled', though: a reporter who has deliberately marked a
  // match abandoned and then types the score it was abandoned at has said
  // something specific, and quietly promoting that to a full-time result would
  // publish a lie. (The score boxes are disabled in that state anyway; this is
  // the rule underneath, not the UI.)
  if (row[side] !== "" && row.status === "scheduled") row.status = "played";
  return row;
}

/** A status that carries no score cannot keep one. Cleared rather than hidden:
 *  apply_match_report REFUSES a postponed row with a score rather than
 *  discarding it, so leaving the digits in place would send a line that is
 *  certain to be rejected. */
export function setStatus(row, status) {
  if (row.status !== status) row.error = "";
  row.status = status;
  if (!isScored(status)) { row.home = ""; row.away = ""; }
  return row;
}

/** Has the reporter changed anything the database would store?
 *
 *  Source is deliberately NOT part of this. The shared source applies to the
 *  rows being published for their score or status; it is never on its own a
 *  reason to rewrite a row's provenance, because that would mean opening the
 *  screen and pressing publish silently re-attributed a matchday somebody else
 *  reported. */
export function isChanged(row) {
  const home = row.home === "" ? null : Number(row.home);
  const away = row.away === "" ? null : Number(row.away);
  return home !== row.saved.home
      || away !== row.saved.away
      || row.status !== row.saved.status;
}

/** A row that already carries a result and is being changed, and that the
 *  reporter has not explicitly confirmed replacing.
 *
 *  This is the difference between reporting and overwriting. Reporting onto a
 *  scheduled fixture is what this screen is for and needs no ceremony;
 *  changing a result somebody has already published is a correction, and a
 *  correction made by accident — a mistyped digit on a line the reporter was
 *  only scrolling past — is invisible afterwards to everyone except the
 *  match_change_log. So it costs one tap. */
export function isConflict(row) {
  return isDecided(row.saved.status) && isChanged(row) && !row.confirmed;
}

/** What the row would replace, in words, for the confirmation button. */
export function savedScoreline(row) {
  const { home, away, status } = row.saved;
  if (isScored(status) && home != null && away != null) return `${home}–${away}`;
  return status;
}

/** Undo a confirmation. Used when a row is edited again after being confirmed:
 *  the reporter agreed to replace 1–1 with 2–1, and 2–4 is a different claim
 *  that has not been agreed to. */
export function unconfirm(row) {
  row.confirmed = false;
  return row;
}

// ── The four canned sources ──────────────────────────────────────────────────
// Not a closed list — the box beside them is free text and always wins. These
// are the four answers that actually get typed, made into one tap each, for
// the same reason the fixture form fills the ground down every line: on a
// phone, the typing IS the cost.

export const SOURCE_CHOICES = [
  { key: "witnessed", label: "At the match", text: "Witnessed at the match" },
  { key: "league", label: "League official", text: "League official" },
  { key: "club", label: "Club official", text: "Club official" },
  { key: "whatsapp", label: "WhatsApp", text: "Received via WhatsApp" },
];

/** The one source string this submission will attach to every changed row.
 *
 *  Free text wins over a tapped chip, because someone who typed a link after
 *  tapping "League official" has said the more specific thing.
 *
 *  BLANK IS A REAL ANSWER and means "leave each row's own source alone" —
 *  apply_match_report keeps the existing source_ref when it is sent an empty
 *  one, which is what stops a matchday published from a screen with an empty
 *  box from erasing the links somebody recorded last week. */
export function resolveSource({ text = "", choice = "", direct = false,
                                reporterName = "", today = "" } = {}) {
  const typed = String(text).trim();
  if (typed) return typed.slice(0, 500);
  if (direct) {
    // Attributed and dated, because "Direct report by me" read six months
    // later has to still say who "me" was. The reporter is already recorded in
    // reported_by; this is the human-readable half, in the column a person
    // actually looks at when checking a result.
    const who = String(reporterName).trim() || "the reporter";
    return `Direct report by ${who}${today ? `, ${today}` : ""}`.slice(0, 500);
  }
  const canned = SOURCE_CHOICES.find((c) => c.key === choice);
  return canned ? canned.text : "";
}

/** Rows that would be published with no provenance at all: nothing recorded on
 *  the match already, and nothing shared at the top of the screen.
 *
 *  A correction to a row that already has a source is fine — that source still
 *  explains the row. What is not fine is a brand new result arriving from
 *  nowhere, which is exactly the row a reader would later want to check. */
export function rowsNeedingSource(sending, resolvedSource) {
  if (String(resolvedSource || "").trim()) return [];
  return sending.filter((row) => !row.saved.sourceRef);
}

/** What to send, and what is wrong before the network is involved.
 *
 *  Returns the rows being sent (in the order their results come back) and the
 *  payload for submit_match_reports. Rows are EXCLUDED rather than rejected
 *  wherever exclusion is the honest answer: an unchanged row is not a mistake,
 *  and a conflict the reporter has not looked at yet is a question, not an
 *  error. Both leave the screen exactly as it is.
 *
 *  Anything genuinely unsendable gets `error` set here, before a round trip is
 *  spent finding out — an incomplete score is the same refusal
 *  apply_match_report would make, said without the wait. */
export function collectReports(rows) {
  const sending = [];
  const conflicts = [];
  rows.forEach((row) => {
    // THIS USED TO CLEAR row.error, AND THAT WAS A BUG WORTH RECORDING.
    // Both screens call collectReports while rendering, to count what the
    // publish button should say. So the message applyBatchResult had just
    // written onto a failed row was wiped on the very next draw: the reporter
    // got "1 still needs attention below" and an amber row with nothing on it
    // saying what went wrong — the one piece of information they needed.
    //
    // An error is cleared by EDITING the row (setScore/setStatus) or by
    // taking a correction back (the Keep button), because those are the
    // moments the message stops being true. Reading the list is not one.
    if (!isChanged(row)) return;
    if (isConflict(row)) { conflicts.push(row); return; }
    if (isScored(row.status) && (row.home === "" || row.away === "")) {
      row.error = "Enter both scores, or set what happened instead.";
      return;
    }
    sending.push(row);
  });

  const reports = sending.map((row) => {
    const scored = isScored(row.status);
    const report = {
      match_id: row.matchId,
      status: row.status,
      home: scored ? Number(row.home) : null,
      away: scored ? Number(row.away) : null,
    };
    // The conflict guard, sent for every row the reporter did NOT explicitly
    // confirm replacing. It says "this is what I believed was saved"; if the
    // database has moved since the screen was drawn, that row alone is
    // refused and the rest still publish. A confirmed replacement omits it,
    // which is what makes the confirmation mean something.
    if (!row.confirmed) {
      report.expect = {
        status: row.saved.status,
        home: row.saved.home,
        away: row.saved.away,
      };
    }
    return report;
  });

  const invalid = rows.filter((row) => row.error).length;
  return { sending, reports, conflicts, invalid };
}

/** Fold submit_match_reports' answer back onto the lines that produced it.
 *
 *  One result per line sent, in order, saying which. A line that published
 *  becomes the new saved state — so it stops counting as changed, stops
 *  offering to publish again, and reads as saved data rather than as a pending
 *  edit. A line that did not keeps EVERYTHING the reporter typed and gains the
 *  reason, which is the only state worth being in after a partial success.
 *
 *  `humanize` is app.js's humanError, passed in rather than imported: this
 *  file stays free of the app's dependencies, and the tests can watch exactly
 *  which message reached the row. */
export function applyBatchResult(sending, data,
                                 humanize = (error) => error.message) {
  const results = new Map((data || []).map((r) => [r.idx, r]));
  let saved = 0;
  let failed = 0;
  sending.forEach((row, i) => {
    const result = results.get(i + 1);
    if (result?.ok) {
      saved += 1;
      row.saved = {
        home: result.home_goals ?? null,
        away: result.away_goals ?? null,
        status: result.status,
        // The server may have kept the row's own source when the shared box
        // was blank, so what is recorded now is "something", which is all this
        // field is ever asked. It only gates rowsNeedingSource.
        sourceRef: row.saved.sourceRef || "recorded",
      };
      row.home = row.saved.home == null ? "" : String(row.saved.home);
      row.away = row.saved.away == null ? "" : String(row.saved.away);
      row.status = row.saved.status;
      row.confirmed = false;
      row.error = "";
      row.published = true;
    } else {
      failed += 1;
      // A line with no result at all did not come back — treat it as failed
      // rather than as published, because the one thing worse than retrying a
      // saved result is not retrying an unsaved one.
      row.error = result
        ? humanize({ message: result.message || "" })
        : "That result was not saved — please try it again.";
    }
  });
  return { saved, failed };
}

/** The sentence at the top after a submission. Says the number, because the
 *  reporter's question is "did all eight go?" and a tick does not answer it. */
export function summarize(saved, failed) {
  if (saved && !failed) {
    return { message: `${saved} result${saved === 1 ? "" : "s"} published.`,
             kind: "ok" };
  }
  if (saved) {
    return { message: `${saved} published; ${failed} still `
                      + `${failed === 1 ? "needs" : "need"} attention below.`,
             kind: "warn" };
  }
  return { message: "Nothing was published — see the lines below.",
           kind: "error" };
}


/* ── Scorers ─────────────────────────────────────────────────────────────────
 *
 * THE SECOND PASS, AND WHY IT HAS TO BE ONE. apply_match_goal refuses a goal
 * on a match with no score — that is validate.py check 5, and an ERROR there
 * deploys nothing for anybody — so a scorer cannot be typed into a grid row
 * beside the score that does not exist yet. The single-match screen has lived
 * with this since 0007 by STAGING scorers on the phone and flushing them once
 * the score publishes (state.pendingGoals / flushPendingGoals). This is that
 * bargain for a whole matchday: publish the scores, then name the scorers
 * against rows that now have one.
 *
 * It is also why the row is not four elements taller. At 390px a grid line is
 * already two teams, two boxes and a status; scorers open under a published
 * line, on demand, for the rows a reporter actually has names for.
 *
 * teamId IS THE SIDE THAT BENEFITED, ALWAYS EXPLICIT. goals.team_id is the
 * beneficiary (DATA_MODEL.md), which for an own goal is NOT the side the
 * scorer plays for. Nothing here derives it from the scorer, the same way
 * sideButtons on the single-match screen asks the reporter outright: a rule
 * that reads "invert when own_goal" is one confident line away from filing a
 * goal against the wrong team, and the wrongness is invisible on the screen
 * that entered it.
 */

// The goal types the portal offers, in the order the single-match screen's
// <select> lists them. '' is an ordinary goal and is what a blank means.
export const SCORER_TYPES = ["", "penalty", "own_goal", "header", "free_kick"];

/** One staged scorer. Everything optional except the name and the side —
 *  a name with no player picked is the normal case and saves as an
 *  unidentified goal (CAF_MW_UNKNOWN + the typed name), which renders as plain
 *  text and earns no player page. That is a real cost, paid deliberately: it
 *  keeps a scorer nobody can identify off the site's player pages rather than
 *  keeping them out of the database. */
export function scorer({ teamId, playerName, playerId = "", assistPlayerId = "",
                         minute = "", goalType = "" } = {}) {
  return {
    teamId: teamId || "",
    playerName: String(playerName ?? "").trim(),
    playerId: playerId || "",
    assistPlayerId: assistPlayerId || "",
    minute: String(minute ?? "").trim(),
    goalType: SCORER_TYPES.includes(goalType) ? goalType : "",
    // Set by applyGoalsResult, exactly as row.error is by applyBatchResult.
    error: "",
    // True once the database holds it. A saved scorer is never re-sent.
    saved: false,
    goalId: "",
  };
}

/** May scorers be named against this row at all?
 *
 *  Deliberately about row.saved rather than about what is typed: a score the
 *  reporter has entered but not published has no goals for a scorer to belong
 *  to, and offering the box would stage names against a row that may still be
 *  corrected to 0-0. Published first, then named. */
export function acceptsScorers(row) {
  return isScored(row.saved.status)
    && row.saved.home != null && row.saved.away != null;
}

/** How many goals a side is credited with in the published result. */
export function goalsFor(row, teamId) {
  if (!acceptsScorers(row)) return 0;
  if (teamId === row.homeTeamId) return row.saved.home ?? 0;
  if (teamId === row.awayTeamId) return row.saved.away ?? 0;
  return 0;
}

/** Scorers already counted against a side: rows the database holds plus the
 *  ones staged on this screen. The RPC checks this again under a row lock — it
 *  has to, it is check 5 — but a reporter should be told they are naming a
 *  third scorer in a 2-1 while the line is still in front of them, not by a
 *  rejection after they have typed seven more. */
export function scorersNamedFor(row, teamId) {
  const existing = teamId === row.homeTeamId
    ? (row.existing?.home || 0)
    : teamId === row.awayTeamId ? (row.existing?.away || 0) : 0;
  return existing + row.scorers.filter((s) => s.teamId === teamId).length;
}

/** Room for another name on that side, and how much. */
export function scorerRoom(row, teamId) {
  return Math.max(0, goalsFor(row, teamId) - scorersNamedFor(row, teamId));
}

/** "Blue Eagles'", not "Blue Eagles's".
 *
 *  Football club names are plural far more often than not, and in Malawi
 *  overwhelmingly so — Blue Eagles, Silver Strikers, Mighty Wanderers,
 *  Bullets, Kamuzu Barracks. The single-match screen has been interpolating a
 *  bare 's since 0007 and therefore saying "Mighty Wanderers's goals" to every
 *  reporter who overfilled a side. It is one character and it is in the
 *  sentence a reporter reads when they are already being told they are wrong,
 *  which is the worst moment to look careless. */
export function possessive(name) {
  const clean = String(name ?? "").trim();
  if (!clean) return "";
  return /s$/i.test(clean) ? `${clean}'` : `${clean}'s`;
}

/** Stage a scorer, or say why not. Returns the reason as a sentence rather
 *  than throwing: every caller here is a tap on a phone, and the message goes
 *  beside the box that produced it. */
export function addScorer(row, entry) {
  const next = scorer(entry);
  if (!next.playerName) return "Type the scorer's name first.";
  if (next.teamId !== row.homeTeamId && next.teamId !== row.awayTeamId) {
    return "Pick which side the goal counted for.";
  }
  if (!acceptsScorers(row)) return "Publish the score before adding scorers.";
  const allowed = goalsFor(row, next.teamId);
  if (scorersNamedFor(row, next.teamId) >= allowed) {
    const name = next.teamId === row.homeTeamId ? row.homeName : row.awayName;
    // Three sentences rather than one with numbers substituted into it. "All 1
    // of Silver Strikers' goals already have a scorer" is what one template
    // gives, and a 1-0 is the commonest score in this dataset — so the awkward
    // branch would have been the one most reporters read.
    if (allowed === 0) return `${name} did not score in this match.`;
    if (allowed === 1) {
      return `${possessive(name)} only goal already has a scorer.`;
    }
    return `All ${allowed} of ${possessive(name)} goals already have a scorer.`;
  }
  row.scorers.push(next);
  return "";
}

/** Take one back. Only an unsaved one: a scorer already in the database is
 *  removed by delete_match_goal on the match screen, which checks that it was
 *  yours — a rule this screen has no business reimplementing. */
export function removeScorer(row, index) {
  const target = row.scorers[index];
  if (!target || target.saved) return false;
  row.scorers.splice(index, 1);
  return true;
}

/** Every staged scorer across the matchday, flattened into what
 *  submit_match_goals takes: one object per goal, each carrying its own
 *  match_id. `sending` is the parallel list of the scorer objects themselves,
 *  so the answer can be folded back onto exactly the lines that produced it.
 *
 *  Saved ones are skipped, which is what makes the button safe to press twice
 *  after a partial failure: it sends what did not go, and nothing else. */
export function collectGoals(rows) {
  const sending = [];
  const goals = [];
  rows.forEach((row) => {
    row.scorers.forEach((s) => {
      if (s.saved) return;
      sending.push(s);
      goals.push({
        match_id: row.matchId,
        team_id: s.teamId,
        player_name: s.playerName,
        player_id: s.playerId,
        assist_player_id: s.assistPlayerId,
        minute: s.minute,
        goal_type: s.goalType,
      });
    });
  });
  return { sending, goals };
}

/** Fold submit_match_goals' answer back onto the scorers that produced it.
 *
 *  The same contract applyBatchResult has, for the same reason: a scorer that
 *  saved becomes saved data and stops being re-sent; one that did not keeps
 *  every field the reporter typed and gains the reason. Nothing typed is ever
 *  discarded by a failure. */
export function applyGoalsResult(sending, data,
                                 humanize = (error) => error.message) {
  const results = new Map((data || []).map((r) => [r.idx, r]));
  let saved = 0;
  let failed = 0;
  sending.forEach((entry, i) => {
    const result = results.get(i + 1);
    if (result?.ok) {
      saved += 1;
      entry.saved = true;
      entry.goalId = result.goal_id || "";
      entry.error = "";
    } else {
      failed += 1;
      // A line with no result at all did not come back. Treated as failed for
      // applyBatchResult's reason exactly: the one thing worse than sending a
      // scorer twice is never sending them at all — and the RPC's own count
      // check is what stops a genuine double-send becoming a duplicate goal.
      entry.error = result
        ? humanize({ message: result.message || "" })
        : "That scorer was not saved — please try again.";
    }
  });
  return { saved, failed };
}

/** The sentence at the top after saving scorers. */
export function summarizeGoals(saved, failed) {
  if (saved && !failed) {
    return { message: `${saved} scorer${saved === 1 ? "" : "s"} saved.`,
             kind: "ok" };
  }
  if (saved) {
    return { message: `${saved} saved; ${failed} still `
                      + `${failed === 1 ? "needs" : "need"} attention below.`,
             kind: "warn" };
  }
  return { message: "No scorers were saved — see the lines below.",
           kind: "error" };
}
