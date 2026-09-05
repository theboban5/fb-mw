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
