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

// What kind of document this is. Fixture lists are recognised and NOT
// processed in this version — the extraction is kept so the same submission can
// be reprocessed when they are supported, rather than the reporter being told
// to come back later and do it again.
export const DOCUMENT_KINDS = [
  "results", "fixtures", "mixed", "other", "unreadable",
];

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
          scorers: {
            type: "array",
            items: {
              type: "object",
              additionalProperties: false,
              required: ["player_raw", "team_side"],
              properties: {
                player_raw: { type: "string" },
                team_side: { type: "string", enum: ["home", "away", "unknown"] },
                minute: { anyOf: [{ type: "integer" }, { type: "null" }] },
                own_goal: { anyOf: [{ type: "boolean" }, { type: "null" }] },
                penalty: { anyOf: [{ type: "boolean" }, { type: "null" }] },
              },
            },
          },
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

The source is usually one of: a league's Facebook results graphic, a photograph of a printed or handwritten team sheet, a screenshot of a WhatsApp message, or plain text somebody typed out. Quality varies a lot. Photographs of screens, glare, crops that cut off an edge, and inconsistent spelling are all normal.

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
- "other": football-related but neither, e.g. a league table or a squad list.
- "unreadable": you cannot make out enough to return anything. Return an empty results array.

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
 *  trade. shouldEscalate() below decides when the second pass is worth it. */
export function buildRequest({ model, imageBase64, mediaType, text, sourceUrl,
                               effort = "low", maxTokens = 8000 } = {}) {
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
    scorers: Array.isArray(item.scorers)
      ? item.scorers.slice(0, 20).map((s) => ({
          player_raw: str(s?.player_raw) || "",
          team_side: ["home", "away", "unknown"].includes(s?.team_side)
            ? s.team_side : "unknown",
          minute: Number.isInteger(s?.minute) ? s.minute : null,
          own_goal: s?.own_goal === true,
          penalty: s?.penalty === true,
        })).filter((s) => s.player_raw)
      : [],
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
