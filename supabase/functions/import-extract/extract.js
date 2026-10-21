/* The extraction contract: what we ask a model for, and what we accept back.
 *
 * WHY THIS IS A SEPARATE FILE, AND WHY IT IS .js.
 * Everything here is a decision about trusting a model's output, and those are
 * exactly the decisions that must be tested — cheaply, offline, and without
 * spending money on every run. index.ts is the Deno HTTP wrapper and cannot be
 * loaded outside Deno; this file imports nothing, touches no global, and is
 * therefore loadable by `node --test` and by Deno alike. Plain JavaScript
 * rather than TypeScript for the same reason the rest of the repo is: no build
 * step, nothing to compile, nothing to get out of date.
 *
 * THE ONE RULE THAT SHAPES ALL OF IT: the model reads, the database resolves.
 * There is no field in the schema below that names an Everyleague row. Not a
 * match_id, not a team_id, not a competition_id. A model asked for an id will
 * produce one — well-formed, plausible, and wrong in a way nothing downstream
 * can detect. So it is never asked, and normalizeItem() drops anything that
 * turns up anyway.
 *
 * The second rule: MISSING IS A VALUE. Every field is nullable, and the model
 * is asked to list what the source did not show. A date invented to fill a
 * required field is worse than no date, because the matcher would then narrow
 * on it.
 */

// ── The status vocabulary ────────────────────────────────────────────────────
// A deliberate subset of matches.status. 'scheduled' is absent because a
// result graphic is not how a fixture gets created, and 'awarded' is absent
// because it is an administrative decision — a walkover, a forfeit — that
// submit_match_reports lets only an admin record. Leaving it out of the schema
// means the model cannot propose one even on a graphic that says "awarded",
// which is a refusal we would rather make here than at the database.
export const IMPORT_STATUSES = [
  "played", "postponed", "abandoned", "cancelled", "unknown",
];

// What kind of document this is. A kind the client does not yet handle is
// still stored, so the same submission can be reprocessed once it does, rather
// than the reporter being told to come back later and do it again — which is
// how every fixture graphic sent before 0049 became importable.
//
// "match_report" is ONE match described in detail: a club's post-match
// article, a team-sheet graphic. It is still read into results[0], so the
// matcher finds the fixture exactly as it finds a result; what it adds is the
// team sheets, substitutions, cards and officials on that one row.
export const DOCUMENT_KINDS = [
  "results", "fixtures", "mixed", "match_report", "other", "unreadable",
];

export const SIDES = ["home", "away", "unknown"];
export const POSITIONS = ["", "GK", "DF", "MF", "FW"];
export const CARDS = ["yellow", "second_yellow", "red"];

// ── The match-report half of a result row ────────────────────────────────────
// NONE OF THIS IS NULLABLE, AND THAT IS A BUDGET, NOT A STYLE. Structured
// outputs allow 16 parameters using `anyOf` and 24 optional parameters across
// the whole schema, and the results half already spent 15 and 13 of them. So
// "not shown" is the empty value here — "", false, [] — which is the same
// answer for the same reason the rest of the schema gives null: the source did
// not say. Every property of these objects is required, so none of them counts
// as optional; only the six containers on the result row do. The test
// "the schema stays inside the structured-outputs limits" counts both.
const sideField = { type: "string", enum: SIDES };
const plain = { type: "string" };
const strict = (properties) => ({
  type: "object", additionalProperties: false,
  required: Object.keys(properties), properties,
});
const SHEET_PLAYER = strict({
  name_raw: plain,
  shirt_number: plain,
  position: { type: "string", enum: POSITIONS },
  captain: { type: "boolean" },
});
const MATCH_REPORT_FIELDS = {
  // One per side the document lists. A club writes about its own XI, so one
  // sheet and not two is the ordinary case, not a failure.
  team_sheets: { type: "array", items: strict({
    team_side: sideField,
    starting: { type: "array", items: SHEET_PLAYER },
    bench: { type: "array", items: SHEET_PLAYER },
  }) },
  substitutions: { type: "array", items: strict({
    team_side: sideField, on_raw: plain, off_raw: plain, minute: plain,
  }) },
  // is_player is false for a coach or team manager. lineups is the only place
  // a card can be recorded, and it holds players, so those are read — they
  // are worth showing the reporter — and never written.
  cards: { type: "array", items: strict({
    team_side: sideField, person_raw: plain,
    card: { type: "string", enum: CARDS }, is_player: { type: "boolean" },
  }) },
  motm_raw: plain,
  officials: strict({
    referee: plain, assistant_1: plain, assistant_2: plain, fourth_official: plain,
  }),
  coaches: { type: "array", items: strict({ team_side: sideField, name_raw: plain }) },
};

/** The JSON Schema the API is asked to constrain the response to.
 *
 *  Written against the documented limits of structured outputs, which are
 *  narrower than JSON Schema proper: `additionalProperties: false` is required
 *  on every object, and numeric bounds (`minimum`, `maximum`) and string
 *  lengths are NOT supported. So the 0..99 score range is not expressed here —
 *  it is enforced in normalizeItem() below, and again by apply_match_report at
 *  the database, which is the only place it actually has to hold.
 *
 *  NULLABLE IS `anyOf`, NOT A TYPE UNION. The first version wrote
 *  `type: ["string", "null"]`, which is ordinary JSON Schema and is not in the
 *  supported subset: the dialect takes `null` as a basic TYPE and `anyOf` as
 *  the way to compose, and a type-union is neither. Every request was rejected
 *  with a 400 before a single word was read — and because every field in this
 *  schema is nullable, it failed on plain pasted text as surely as on a
 *  photograph, which is what made it look like the model rather than the
 *  envelope. */
export const EXTRACTION_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["document_kind", "results", "notes"],
  properties: {
    document_kind: { type: "string", enum: DOCUMENT_KINDS },
    // Anything the source says about the competition as a whole. A hint, never
    // an answer — the matcher decides which competition this is.
    competition_hint: { anyOf: [{ type: "string" }, { type: "null" }] },
    date: { anyOf: [{ type: "string" }, { type: "null" }] },
    matchday: { anyOf: [{ type: "string" }, { type: "null" }] },
    results: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["home_team_raw", "away_team_raw", "home_score",
                   "away_score", "status", "fields_not_present"],
        properties: {
          // AS THEY APPEAR. Not cleaned, not expanded, not corrected — if the
          // graphic says "Big Bullets" that is what comes back, and the
          // alias tables are what know it might be Nyasa Big Bullets.
          home_team_raw: { type: "string" },
          away_team_raw: { type: "string" },
          home_score: { anyOf: [{ type: "integer" }, { type: "null" }] },
          away_score: { anyOf: [{ type: "integer" }, { type: "null" }] },
          status: { type: "string", enum: IMPORT_STATUSES },
          date: { anyOf: [{ type: "string" }, { type: "null" }] },
          kickoff: { anyOf: [{ type: "string" }, { type: "null" }] },
          matchday: { anyOf: [{ type: "string" }, { type: "null" }] },
          competition_hint: { anyOf: [{ type: "string" }, { type: "null" }] },
          venue_raw: { anyOf: [{ type: "string" }, { type: "null" }] },
          // Read if present, never published in this version. The shape is
          // here so scorer import is a later change to the CLIENT rather than
          // a new extraction contract and a re-run of every stored import.
          //
          // TEAM_SIDE IS WHERE THE NAME IS PRINTED, NOT WHO THE GOAL COUNTED
          // FOR, and until Sep 2026 the prompt did not say so — it did not
          // mention scorers at all, so every one of these fields was whatever
          // the model took them to mean. For an ordinary goal the two readings
          // agree. For an own goal they do not: goals.team_id is the
          // BENEFICIARY (DATA_MODEL.md) and the scorer plays for the other
          // side, so a stored own_goal row from before that prompt change
          // cannot be published without a person saying which side it was.
          // That is why #/results asks for the side outright rather than
          // deriving it, and why nothing here tries to repair the old rows:
          // there is no fact to repair them from.
          scorers: {
            type: "array",
            items: {
              type: "object",
              additionalProperties: false,
              required: ["player_raw", "team_side", "assist_raw", "method"],
              properties: {
                player_raw: { type: "string" },
                team_side: { type: "string", enum: ["home", "away", "unknown"] },
                minute: { anyOf: [{ type: "integer" }, { type: "null" }] },
                own_goal: { anyOf: [{ type: "boolean" }, { type: "null" }] },
                penalty: { anyOf: [{ type: "boolean" }, { type: "null" }] },
                // Required, so they cost nothing against the optional budget
                // above. "" is "not said", exactly as null is elsewhere.
                assist_raw: plain,
                method: { type: "string", enum: ["", "header", "free_kick"] },
              },
            },
          },
          ...MATCH_REPORT_FIELDS,
          // The words this row was read from. It is what a reporter checks
          // against the picture when a row looks wrong, and it is the reason
          // a bad extraction is diagnosable at all.
          evidence: { anyOf: [{ type: "string" }, { type: "null" }] },
          // The anti-invention device. Asking for an explicit list of what was
          // NOT shown makes filling those fields in a contradiction rather
          // than a convenience.
          fields_not_present: { type: "array", items: { type: "string" } },
        },
      },
    },
    notes: { anyOf: [{ type: "string" }, { type: "null" }] },
  },
};

/** The instructions. Stable byte-for-byte across every import, which is what
 *  makes it worth a cache breakpoint — nothing per-request is interpolated
 *  into it, deliberately. (Whether it actually caches depends on the model's
 *  minimum cacheable prefix: 1024 tokens on Sonnet 5, 512 on Opus 5, but 4096
 *  on Haiku 4.5, where a prompt this size silently will not cache.) */
export const SYSTEM_PROMPT = `You read football results off pictures and text from Malawi and return structured data.

The source is usually one of: a league's Facebook results graphic, a photograph of a printed or handwritten team sheet, a screenshot of a WhatsApp message, a club's match report from its website, or plain text somebody typed out. Quality varies a lot. Photographs of screens, glare, crops that cut off an edge, and inconsistent spelling are all normal.

WHAT YOU RETURN

For each match you can see, return the two team names EXACTLY AS THEY APPEAR, the score if shown, and the status. Do not expand abbreviations, do not correct spellings, and do not convert a nickname to a full club name. "Bullets", "Big Bullets" and "Nyasa Big Bullets" are three different strings and you return whichever one is printed. Something downstream knows which club each of them means; you do not, and guessing destroys the only evidence of what was actually written.

NEVER INVENT ANYTHING

Every field except the team names and status may be null, and null is the correct answer whenever the source does not show it. Most graphics do not print a date. Most do not print a matchday. Many do not name a venue. If it is not there, it is null, and you list the field name in fields_not_present for that match.

This matters more than completeness. A date you inferred from context is worse than no date at all, because it will be used to decide which fixture this is.

Do not return any kind of identifier, code, or database key. You will not be given any and you must not construct one.

SCORES AND STATUS

- status "played" with both scores, when a result is shown.
- status "postponed", "abandoned" or "cancelled" when the source says so. These carry NO score: set both scores to null even if a partial score is printed beside an abandoned match.
- status "unknown" when a match is listed but you cannot tell what happened. Set both scores to null.
- A score you cannot read confidently is null with status "unknown" — not a guess.

Read the scoreline in the order it is printed. Do not reorder teams to put a winner first, and do not assume the left or top team is the home side; report the order shown and let it be checked.

DOCUMENT KIND

- "results": played matches with scores. This is the normal case.
- "fixtures": a list of upcoming matches with no scores.
- "mixed": both.
- "match_report": ONE match described in detail — a club's post-match article, a team-sheet graphic, a line-up post. Return that match as the only row in results, with its team sheets and everything else under MATCH REPORTS below. A results graphic that happens to name scorers is still "results".
- "other": football-related but neither, e.g. a league table or a squad list.
- "unreadable": you cannot make out enough to return anything. Return an empty results array.

SCORERS

Return a scorer for every goal the source names, and none it does not. Most graphics name no scorers at all; an empty list is the ordinary answer and is never a failure.

- player_raw is the name EXACTLY AS PRINTED, under the same rule as the team names: no expanding "A. Josephy" into a full name, no correcting a spelling.
- team_side is WHERE THE NAME IS PRINTED — the column, the badge or the heading it is listed under. It is an observation about the picture, not a conclusion about who the goal counted for. Use "unknown" when the layout does not make it clear.
- own_goal is true only when the source marks it: "OG", "o.g.", "own goal". Do NOT work out for yourself that a goal was an own goal.
- Do not reorder, relabel or move a scorer to the side you think the goal counted for. On an own goal those two sides differ, graphics disagree with each other about which one to print it under, and something downstream asks a person. Moving it destroys the only evidence of what the picture actually said.
- penalty is true only when the source marks it — "(P)", "pen", "penalty". A goal marked both a penalty and an own goal is a misread: report what is printed and say so in notes.
- minute is the number printed beside the name, as an integer, and null when none is. "45+2" is 45. A scorer with no minute shown is normal and is not a reason to leave the scorer out.
- assist_raw is the player the source credits with SETTING UP the goal — "assisted by", "set up by", "X's cross found Y", "X's free kick found Y in the box" — named exactly as printed. "" when the source credits nobody. A player who merely won the free kick or was fouled is not the assist.
- method is "header" or "free_kick" only when the source says so ("free_kick" is a goal scored DIRECTLY from a free kick, not a header from one). "" otherwise. Penalties and own goals keep their own fields.

MATCH REPORTS

Only when document_kind is "match_report". On every other kind, leave team_sheets, substitutions, cards and coaches empty, motm_raw "", and every officials field "".

In this section team_side means the side of the match row you returned: "home" is the team in home_team_raw, "away" the team in away_team_raw. A club's report usually covers only its own team; that team may be either side, and "unknown" is for when you genuinely cannot tell. Text that is not shown is "", a list that is not shown is empty, and a flag that is not shown is false — the same rule as null everywhere else.

- team_sheets: one entry per team whose players the source LISTS. Never build a sheet from players merely mentioned in the story, and never add a second sheet for the opponent unless the source lists its players too. starting is the starting XI in the order printed. bench is every listed substitute, used or unused, in the order printed.
- For each player: name_raw exactly as printed, without the brackets or markers around it. shirt_number only if printed. position only when marked, as GK, DF, MF or FW ("(GK)" is GK); "" otherwise. captain true when marked "(C)" or when the text calls that player the captain.
- substitutions: one per change, the player coming on in on_raw and going off in off_raw, with minute as printed digits — "39", "90+2" — or "" when none is given. When a source gives the pairings twice, once in the story and once as a list such as "Aubrey Khumbula (Charles Chipala-63')", the list is the answer: a sentence that names three players on and three off does not say who replaced whom.
- cards: every booking or sending-off the source reports, for any person on either side. card is "yellow", "second_yellow" for a red that followed a second booking, or "red" for a straight red. is_player is false for a coach, team manager or anyone else not playing. A card with no minute is normal.
- motm_raw: the man of the match, or "".
- officials: the referee, first and second assistant and fourth official, each exactly as printed, or "".
- coaches: each team's head coach (or interim head coach) as printed, with that team's side. A team manager, assistant coach or chairman is not a coach.

Put anything a person should know in notes — a cut-off edge, a column you could not interpret, two matches that looked like duplicates.`;

// ── Building the request ─────────────────────────────────────────────────────

/** The user-turn content: the picture and/or the words, and nothing else.
 *
 *  NO DATABASE CONTENT IS EVER SENT. Not the team list, not the fixture list,
 *  not the reporter's assignments. It would be a plausible way to improve
 *  matching and it is the wrong place to do it: the model would then be
 *  choosing between rows we handed it, which is the same thing as asking it
 *  for an id. Matching happens in Postgres, against everything, afterwards. */
export function buildContent({ imageBase64, mediaType, text, sourceUrl } = {}) {
  const content = [];
  if (imageBase64) {
    content.push({
      type: "image",
      source: { type: "base64", media_type: mediaType || "image/jpeg",
                data: imageBase64 },
    });
  }
  const parts = [];
  if (text && text.trim()) {
    parts.push(`Text supplied with this submission:\n\n${text.trim()}`);
  }
  // The URL is given as CONTEXT, never as something to go and read: the model
  // has no network here, and a link that could not be fetched is still worth
  // showing it because the address often names the league.
  if (sourceUrl && sourceUrl.trim()) {
    parts.push(`It was submitted with this link (you cannot open it; it is `
               + `context only): ${sourceUrl.trim()}`);
  }
  parts.push(imageBase64
    ? "Read the football results in this image."
    : "Read the football results in the text above.");
  content.push({ type: "text", text: parts.join("\n\n") });
  return content;
}

/** The full Messages request body.
 *
 *  `effort` is low on the first pass and high on the escalation: reading a
 *  clean graphic is not a hard problem and paying for depth on every import to
 *  cover the one in twenty that is a photograph of a screen is the wrong
 *  trade. shouldEscalate() below decides when the second pass is worth it.
 *
 *  max_tokens was 8000, which was sized for a results graphic. A match
 *  report is two sheets of eighteen, the subs, the cards and the officials, on
 *  top of the thinking — and a truncated answer is "too_long", a failure,
 *  rather than a shorter one. A ceiling costs nothing until it is used. */
export function buildRequest({ model, imageBase64, mediaType, text, sourceUrl,
                               effort = "low", maxTokens = 16000 } = {}) {
  return {
    model,
    max_tokens: maxTokens,
    system: [{
      type: "text",
      text: SYSTEM_PROMPT,
      // Identical on every request by construction, so this is the one
      // breakpoint worth having.
      cache_control: { type: "ephemeral" },
    }],
    // Adaptive thinking: a creased photograph of a results sheet genuinely
    // benefits from the model working before it answers, and on this model
    // family adaptive is the only on-mode.
    thinking: { type: "adaptive" },
    output_config: {
      effort,
      format: { type: "json_schema", schema: EXTRACTION_SCHEMA },
    },
    messages: [{ role: "user", content: buildContent({
      imageBase64, mediaType, text, sourceUrl }) }],
  };
}

// ── Accepting the answer ─────────────────────────────────────────────────────

const clampScore = (value) => {
  if (value === null || value === undefined) return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  const i = Math.trunc(n);
  // The same 0..99 range apply_match_report enforces. Out of range is dropped
  // rather than clamped: 150 is not evidence that the score was 99, it is
  // evidence that the row was misread.
  return i >= 0 && i <= 99 ? i : null;
};

const str = (value) => {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed.slice(0, 200);
};

const list = (value, cap) => (Array.isArray(value) ? value.slice(0, cap) : []);
const side = (value) => (SIDES.includes(value) ? value : "unknown");
// The shapes lineups.minute_on accepts on the site: "62", "90+3". Anything
// else — "62'", "second half" — is not a minute save_lineup should be handed.
const minuteText = (value) => {
  const m = /^\s*(\d{1,3})\s*(?:\+\s*(\d{1,2}))?\s*['’]?\s*$/.exec(String(value ?? ""));
  return m ? (m[2] ? `${m[1]}+${m[2]}` : m[1]) : "";
};

const sheetPlayer = (p) => ({
  name_raw: str(p?.name_raw) || "",
  // A shirt is a number on a back. "GK" in this field is a position that
  // landed in the wrong box, and is not kept as a shirt.
  shirt_number: /^\d{1,2}$/.test(String(p?.shirt_number ?? "").trim())
    ? String(p.shirt_number).trim() : "",
  position: POSITIONS.includes(p?.position) ? p.position : "",
  captain: p?.captain === true,
});

/** The match-report fields, by name and capped, exactly as normalizeItem.
 *
 *  ALWAYS PRESENT, EMPTY WHEN UNSAID, so a results row and a match report have
 *  one shape. An import stored before match reports existed has none of these
 *  keys, and the client reads it through the same function the next time it
 *  is opened — which is the reason it lives here, exported, rather than inline. */
export function normalizeMatchReport(raw) {
  const item = raw && typeof raw === "object" ? raw : {};
  const officials = item.officials && typeof item.officials === "object"
    ? item.officials : {};
  return {
    // 30 a side is two benches' worth more than any sheet holds; the cap is
    // against a model that has listed a whole squad page, not against football.
    team_sheets: list(item.team_sheets, 2).map((t) => ({
      team_side: side(t?.team_side),
      starting: list(t?.starting, 30).map(sheetPlayer).filter((p) => p.name_raw),
      bench: list(t?.bench, 30).map(sheetPlayer).filter((p) => p.name_raw),
    })).filter((t) => t.starting.length || t.bench.length),
    substitutions: list(item.substitutions, 20).map((x) => ({
      team_side: side(x?.team_side),
      on_raw: str(x?.on_raw) || "",
      off_raw: str(x?.off_raw) || "",
      minute: minuteText(x?.minute),
    })).filter((x) => x.on_raw),
    cards: list(item.cards, 30).map((c) => ({
      team_side: side(c?.team_side),
      person_raw: str(c?.person_raw) || "",
      // An unrecognised card is dropped, never read as a yellow: inventing a
      // booking is the one direction this layer must not err in.
      card: CARDS.includes(c?.card) ? c.card : "",
      // Anything but an explicit false is a player: a card is shown to a
      // player in all but a handful of cases, and a wrong guess here is a
      // card the reporter sees attached to nobody, never one written wrongly.
      is_player: c?.is_player !== false,
    })).filter((c) => c.person_raw && c.card),
    motm_raw: str(item.motm_raw) || "",
    officials: {
      referee: str(officials.referee) || "",
      assistant_1: str(officials.assistant_1) || "",
      assistant_2: str(officials.assistant_2) || "",
      fourth_official: str(officials.fourth_official) || "",
    },
    coaches: list(item.coaches, 4).map((c) => ({
      team_side: side(c?.team_side),
      name_raw: str(c?.name_raw) || "",
    })).filter((c) => c.name_raw),
  };
}

/** One result, reduced to the fields we asked for.
 *
 *  ALLOW-LISTED, NOT SANITIZED. Every field is copied across by name; anything
 *  else the model returned — an invented `match_id`, a `confidence`, a
 *  `team_id` it constructed from a pattern it saw — is not removed so much as
 *  never picked up. That is the difference between a filter someone has to
 *  remember to update and a shape that cannot carry what it does not name. */
export function normalizeItem(raw, idx) {
  const item = raw && typeof raw === "object" ? raw : {};
  const status = IMPORT_STATUSES.includes(item.status) ? item.status : "unknown";
  const scored = status === "played";

  return {
    idx,
    home_team_raw: str(item.home_team_raw) || "",
    away_team_raw: str(item.away_team_raw) || "",
    // A status that carries no score cannot keep one, whatever came back. A
    // partial score printed beside an abandoned match is real information and
    // it is not a result; apply_match_report would refuse the row, so it is
    // dropped here where the reason can be shown.
    home_score: scored ? clampScore(item.home_score) : null,
    away_score: scored ? clampScore(item.away_score) : null,
    status,
    date: str(item.date),
    kickoff: str(item.kickoff),
    matchday: str(item.matchday),
    competition_hint: str(item.competition_hint),
    venue_raw: str(item.venue_raw),
    scorers: list(item.scorers, 20).map((s) => ({
      player_raw: str(s?.player_raw) || "",
      team_side: side(s?.team_side),
      minute: Number.isInteger(s?.minute) ? s.minute : null,
      own_goal: s?.own_goal === true,
      penalty: s?.penalty === true,
      assist_raw: str(s?.assist_raw) || "",
      method: ["header", "free_kick"].includes(s?.method) ? s.method : "",
    })).filter((s) => s.player_raw),
    ...normalizeMatchReport(item),
    evidence: str(item.evidence),
    fields_not_present: Array.isArray(item.fields_not_present)
      ? item.fields_not_present.filter((f) => typeof f === "string").slice(0, 20)
      : [],
  };
}

/** A played row with only one score is not a result. It is the commonest way a
 *  graphic is misread — a column boundary in the wrong place — and letting it
 *  through would put a row on the review screen that cannot be published. */
const isUsable = (item) =>
  Boolean(item.home_team_raw) && Boolean(item.away_team_raw)
  && (item.status !== "played"
      || (item.home_score !== null && item.away_score !== null));

/** Turn an API response into what we will store, or into a category.
 *
 *  Every failure here is a CATEGORY, never a raw error: the string that comes
 *  out of this reaches a reporter, and a provider error can name
 *  infrastructure. The detail goes to the function's log, which an operator
 *  can read and a reporter cannot. */
export function parseExtraction(message) {
  if (!message || typeof message !== "object") {
    return { ok: false, errorCategory: "no_response", items: [], raw: null };
  }
  // A safety decline is not a bug and not something to retry — the source was
  // something the model would not read.
  if (message.stop_reason === "refusal") {
    return { ok: false, errorCategory: "refused", items: [], raw: null };
  }
  // Truncated output is structurally invalid JSON, so it must be caught before
  // parsing rather than diagnosed as malformed.
  if (message.stop_reason === "max_tokens") {
    return { ok: false, errorCategory: "too_long", items: [], raw: null };
  }

  let payload = message.parsed_output ?? null;
  if (!payload) {
    const text = (message.content || [])
      .filter((b) => b && b.type === "text")
      .map((b) => b.text).join("");
    if (!text.trim()) {
      return { ok: false, errorCategory: "empty_output", items: [], raw: null };
    }
    try {
      payload = JSON.parse(text);
    } catch {
      // Structured outputs should make this impossible. It is handled anyway,
      // because "should be impossible" is not a thing to find out about from a
      // reporter whose afternoon just disappeared.
      return { ok: false, errorCategory: "bad_output", items: [], raw: null };
    }
  }
  if (!payload || typeof payload !== "object") {
    return { ok: false, errorCategory: "bad_output", items: [], raw: null };
  }

  const kind = DOCUMENT_KINDS.includes(payload.document_kind)
    ? payload.document_kind : "other";
  const rows = Array.isArray(payload.results) ? payload.results : [];
  // A cap, not a guess at what a graphic looks like: submit_match_reports takes
  // 60 and there is no point proposing more than can be published at once.
  const items = rows.slice(0, 60).map((r, i) => normalizeItem(r, i + 1))
                    .filter(isUsable);

  return {
    ok: true,
    errorCategory: "",
    documentKind: kind,
    items,
    dropped: Math.min(rows.length, 60) - items.length,
    competitionHint: str(payload.competition_hint),
    date: str(payload.date),
    matchday: str(payload.matchday),
    notes: str(payload.notes),
    raw: payload,
  };
}

/** Is the cheap pass good enough, or is this one of the hard ones?
 *
 *  Escalation is for documents, not for confidence: the model's own certainty
 *  is not consulted anywhere in this system. What triggers a second pass is
 *  the first pass having visibly failed to read the thing — nothing came back,
 *  it could not be parsed, or rows were dropped because they were incomplete.
 *
 *  A clean read of a clean graphic never escalates, which is the point: the
 *  common case pays once. */
export function shouldEscalate(result, { hasFallback = true } = {}) {
  if (!hasFallback) return false;
  if (!result) return false;
  // A FAILED CALL IS NEVER ESCALATED. Escalation exists for one thing: a
  // document the cheap model could not read well. If the call itself did not
  // succeed there is no reading to improve on, and a bigger model will hit the
  // identical wall — a malformed request is malformed at every price, a bad
  // key is bad at every price, and a rate limit is a rate limit.
  //
  // This used to retry everything except refusals and truncations, which meant
  // the schema bug that made every request a 400 quietly cost two calls per
  // import instead of one, for two identical rejections.
  if (!result.ok) return false;
  if (result.documentKind === "unreadable") return true;
  if (result.items.length === 0) return true;
  if (result.dropped > 0) return true;
  return false;
}

// ── What it cost ─────────────────────────────────────────────────────────────
// Recorded because "should we still be on Sonnet?" can only be answered from
// data. The dollar figure is an ESTIMATE and says so: prices change without
// this file changing, so the tokens and the model name are the durable record
// and the number is a convenience for the ops screen.

const PRICES = {                       // USD per million tokens
  "claude-opus-5":   { input: 5, output: 25 },
  "claude-sonnet-5": { input: 2, output: 10 },
  "claude-haiku-4-5": { input: 1, output: 5 },
};

export function usageRecord(model, usage, { escalated = false } = {}) {
  const u = usage || {};
  const input = (u.input_tokens || 0);
  const output = (u.output_tokens || 0);
  const cacheRead = (u.cache_read_input_tokens || 0);
  const cacheWrite = (u.cache_creation_input_tokens || 0);
  const price = PRICES[model];
  const record = {
    model,
    escalated,
    input_tokens: input,
    output_tokens: output,
    cache_read_input_tokens: cacheRead,
    cache_creation_input_tokens: cacheWrite,
  };
  if (price) {
    // Cached reads bill at about a tenth and cache writes at about 1.25x. Both
    // are approximations, which is what `_estimate` in the name is for.
    record.usd_estimate = Number((
      (input * price.input
       + cacheRead * price.input * 0.1
       + cacheWrite * price.input * 1.25
       + output * price.output) / 1_000_000
    ).toFixed(6));
  }
  return record;
}
