/* What a resolved fixture list means, with no DOM and no network in it.
 *
 * WHY THIS IS A SEPARATE FILE, and it is results_grid.js's answer: everything
 * here is a decision about a proposal a reporter is looking at — which rows
 * are new, which are the graphic agreeing with the database, which need
 * correcting, and what to send for each. Those are the decisions that must
 * survive a dropped connection, and inside app.js they would be tangled with
 * innerHTML and checkable only by hand on a phone. Here they are ordinary
 * functions over ordinary objects, so `node --test tests/js/` can ask the
 * question that cannot be asked of a rendered screen: after a partial failure,
 * is everything still on the row?
 *
 * It imports nothing, on purpose.
 *
 * THE ROW IS #/add's ROW, PLUS WHAT THE RESOLVER SAID. The fields the fixture
 * form already uses — home, away, date, kickoff, venue — are the fields that
 * publish, through the same create_fixtures call a typed fixture goes through.
 * `item` is the proposal beside them, and nothing in it reaches the database
 * except through those five.
 */

/** The four things a reporter can be looking at. `state` comes from the
 *  resolver (0049); this is the client's name for what to DO about it.
 *
 *    new       not in the database — publishes through create_fixtures
 *    agrees    already there, and the graphic says the same thing. Nothing to
 *              do, and NOT an error: on a top-flight MATCH DAY poster this is
 *              nearly every row.
 *    differs   already there, and the graphic disagrees about the date, the
 *              kick-off or the ground. One tap corrects it.
 *    blocked   a name that resolved to nothing, a team playing itself, two
 *              candidate fixtures, the same pairing already listed the other
 *              way round. Not publishable, and a person's decision.
 */
export function classify(row) {
  const state = row.item?.state;
  if (state === "new") return "new";
  if (state === "existing_agrees") return "agrees";
  if (state === "existing_differs") return "differs";
  return "blocked";
}

export const isPublishable = (row) => classify(row) === "new"
  && Boolean(row.home) && Boolean(row.away);

/** A resolved proposal to a row of the fixture form.
 *
 *  THE GROUND IS PRE-FILLED ONLY WHEN IT WAS MATCHED. create_fixtures resolves
 *  a venue NAME and mints one it does not recognise (resolve_venue, 0014) —
 *  correct when a reporter typed it, wrong when a model read it off a
 *  compressed screenshot. So a matched ground arrives as the canonical name
 *  already in `venues`, which resolve_venue will find rather than create, and
 *  an unmatched one arrives BLANK with the printed name kept beside it in
 *  `venueRaw` for the reporter to look at. Typing it in is then their decision
 *  and their minting, exactly as it is on #/add.
 */
export function fixtureRow(item) {
  return {
    // The five fields that publish. Same names, same meanings, same form.
    home: item.home?.team_id || "",
    away: item.away?.team_id || "",
    homeText: item.home?.name || item.raw?.home || "",
    awayText: item.away?.name || item.raw?.away || "",
    date: item.date || "",
    kickoff: item.kickoff || "",
    venue: item.venue_name || "",

    // Everything else is context for the person looking at it.
    item,
    idx: item.idx,
    venueRaw: item.raw?.venue || "",
    // Set by tapping the offer on a `differs` row. Nothing is corrected
    // without it, which is what makes an untapped disagreement harmless.
    confirmed: false,
    error: "",
    done: false,
    updated: false,
  };
}

/** Rows that will be created, and the payload create_fixtures takes.
 *
 *  `shared` is the matchday or cup round chosen once at the top of the screen,
 *  exactly as #/add sends it down every line — a fixture list is published a
 *  week at a time and the round is the same for all of them.
 *
 *  A row is EXCLUDED rather than rejected wherever exclusion is honest: an
 *  `agrees` row is not a mistake, and a `differs` row nobody has tapped is a
 *  question rather than an error. Both leave the screen exactly as it is.
 */
export function collectFixtures(rows, shared = {}) {
  const sending = rows.filter(isPublishable);
  const fixtures = sending.map((row) => {
    const fixture = {
      home: row.home, away: row.away,
      date: row.date, kickoff: row.kickoff, venue: (row.venue || "").trim(),
    };
    if (shared.stage) fixture.stage = shared.stage;
    else if (shared.matchday) fixture.matchday = shared.matchday;
    return fixture;
  });
  return { sending, fixtures };
}

/** The corrections a reporter has confirmed, and which call each one needs.
 *
 *  RESCHEDULE_MATCH SETS BOTH THE DATE AND THE KICK-OFF, so a row where only
 *  the kick-off moved must still send the date it already has — passing null
 *  would take the date off the fixture. That is the whole reason this returns
 *  a prepared pair rather than "the fields that differ".
 *
 *  A row where only the ground changed sends no reschedule at all: two calls
 *  where one is needed is a second chance to fail on a bad connection.
 */
export function collectUpdates(rows) {
  return rows
    .filter((row) => classify(row) === "differs" && row.confirmed && !row.updated)
    .map((row) => {
      const differs = row.item?.differs || [];
      const existing = row.item?.existing || {};
      const update = { row, matchId: existing.match_id, reschedule: null, venue: null };
      if (differs.includes("date") || differs.includes("kickoff")) {
        update.reschedule = {
          date: row.date || existing.date || null,
          kickoff: row.kickoff || existing.kickoff || "",
        };
      }
      if (differs.includes("venue")) {
        update.venue = (row.venue || "").trim();
      }
      return update;
    });
}

/** Fold create_fixtures' answer back onto the lines that produced it.
 *
 *  One result per line sent, in order, saying which. A line that saved is
 *  marked done and drops out of the next submission; a line that did not keeps
 *  EVERYTHING on it and gains the reason, which is the only state worth being
 *  in after a partial success.
 *
 *  `humanize` is app.js's humanError, passed in rather than imported, so this
 *  file stays free of the app's dependencies and the tests can watch exactly
 *  which message reached the row.
 */
export function applyFixtureResult(sending, data, humanize = (e) => e.message) {
  const results = new Map((data || []).map((r) => [r.idx, r]));
  let added = 0;
  let failed = 0;
  sending.forEach((row, i) => {
    const result = results.get(i + 1);
    if (result?.ok) {
      added += 1;
      row.done = true;
      row.error = "";
      row.publicId = result.public_id;
      row.matchId = result.match_id;
    } else {
      failed += 1;
      // A line with no result at all did not come back. Treated as failed
      // rather than as added, because the one thing worse than retrying a
      // saved fixture is not retrying an unsaved one — and insert_fixture's
      // duplicate guard makes the retry safe.
      row.error = result
        ? humanize({ message: result.message || "" })
        : "That fixture was not added — please try it again.";
    }
  });
  return { added, failed };
}

/** The sentence at the top afterwards. Three numbers, because they are three
 *  different things that happened and a reporter's question is "did it all go
 *  in?" — which a tick does not answer. */
export function summarizeFixtures(added, updated, failed) {
  const parts = [];
  if (added) parts.push(`${added} fixture${added === 1 ? "" : "s"} added`);
  if (updated) parts.push(`${updated} corrected`);
  if (!parts.length && !failed) {
    return { message: "Nothing to add — everything on this list was already "
                      + "in the fixture list.", kind: "ok" };
  }
  if (!failed) return { message: `${parts.join(", ")}.`, kind: "ok" };
  if (!parts.length) {
    return { message: "Nothing was added — see the lines below.", kind: "error" };
  }
  return { message: `${parts.join(", ")}; ${failed} still `
                    + `${failed === 1 ? "needs" : "need"} attention below.`,
           kind: "warn" };
}

/** What the row is offering to change, in words, for the confirmation button.
 *  Reads off `differs` so it can never describe a field the resolver did not
 *  actually find a disagreement in. */
export function differenceLabel(row) {
  const differs = row.item?.differs || [];
  const existing = row.item?.existing || {};
  const said = { date: row.date, kickoff: row.kickoff, venue: row.venue };
  const had = { date: existing.date, kickoff: existing.kickoff,
                venue: existing.venue_name };
  const words = { date: "date", kickoff: "kick-off", venue: "ground" };
  return differs
    .map((f) => `${words[f] || f} ${had[f] || "—"} → ${said[f] || "—"}`)
    .join(" · ");
}
