/* Model responses, as the API would return them.
 *
 * These are the documents the importer actually meets, written down so the
 * handling of each is a test rather than a hope. They are FAKES, not
 * recordings: nothing here was produced by a live call, and no test in this
 * repo spends money or needs a key.
 *
 * ADDING ONE. Copy the shape below — a Messages API response object with a
 * single text block whose text is the JSON the schema asks for — and add a
 * test that says what should happen to it. The interesting fixtures are not
 * the clean ones; they are the ones where a plausible-looking answer is wrong
 * (invented ids, a score on an abandoned match, a fixture list posing as
 * results), because those are what the acceptance code exists for.
 */

/** Wrap a payload as the API returns it. */
export const asMessage = (payload, over = {}) => ({
  id: "msg_test",
  type: "message",
  role: "assistant",
  model: "claude-sonnet-5",
  stop_reason: "end_turn",
  content: [{ type: "text", text: JSON.stringify(payload) }],
  usage: { input_tokens: 2500, output_tokens: 600,
           cache_read_input_tokens: 0, cache_creation_input_tokens: 1400 },
  ...over,
});

const result = (home, away, hs, as_, over = {}) => ({
  home_team_raw: home, away_team_raw: away,
  home_score: hs, away_score: as_,
  status: "played", date: null, kickoff: null, matchday: null,
  competition_hint: null, venue_raw: null, scorers: [], evidence: null,
  fields_not_present: ["date", "venue_raw"],
  ...over,
});

// ── A clean results graphic ──────────────────────────────────────────────────
// The normal case: a league's Saturday round, posted as one image.
export const CLEAN_GRAPHIC = asMessage({
  document_kind: "results",
  competition_hint: "FDH Bank Premiership",
  date: "2026-09-05",
  matchday: "6",
  results: [
    result("Blue Eagles", "Silver Strikers", 2, 1,
           { evidence: "Blue Eagles 2 - 1 Silver Strikers" }),
    result("Mighty Wanderers", "Nyasa Big Bullets", 0, 0),
    result("Kamuzu Barracks", "Civil Service United", 3, 1),
  ],
  notes: null,
});

// ── Messy copied text ────────────────────────────────────────────────────────
// Pasted out of WhatsApp: inconsistent separators, a trailing fragment.
export const MESSY_TEXT = asMessage({
  document_kind: "results",
  competition_hint: null, date: null, matchday: null,
  results: [
    result("Bullets", "Wanderers", 1, 0,
           { evidence: "bullets 1-0 wandererz", fields_not_present: ["date"] }),
    result("Blue Eagles", "Karonga Utd", 2, 2,
           { evidence: "Blue Eagles 2:2 Karonga Utd" }),
  ],
  notes: "The last line was cut off mid-word.",
});

// ── Abbreviated names ────────────────────────────────────────────────────────
// Returned as printed, which is the contract — the alias tables resolve them.
export const ABBREVIATED = asMessage({
  document_kind: "results",
  competition_hint: null, date: null, matchday: null,
  results: [result("BE", "SS", 1, 1), result("MW", "NBB", 0, 2)],
  notes: null,
});

// ── A fixture list, not results ──────────────────────────────────────────────
// Recognised and deferred. The extraction is kept so the same submission can
// be reprocessed when fixture import ships.
export const FIXTURE_LIST = asMessage({
  document_kind: "fixtures",
  competition_hint: "NRFA Division Two",
  date: "2026-09-12", matchday: "8",
  results: [
    result("Blue Eagles", "Silver Strikers", null, null,
           { status: "unknown", fields_not_present: ["home_score", "away_score"] }),
    result("Mighty Wanderers", "Karonga United", null, null,
           { status: "unknown", fields_not_present: ["home_score", "away_score"] }),
  ],
  notes: "These look like upcoming fixtures — no scores are shown.",
});

// ── An abandoned match with a score printed beside it ────────────────────────
// Real, and a trap: the score is genuine information and it is NOT a result.
// apply_match_report refuses a non-played row carrying one, so it must be
// dropped before it reaches the grid.
export const ABANDONED_WITH_SCORE = asMessage({
  document_kind: "results",
  competition_hint: null, date: null, matchday: null,
  results: [
    result("Blue Eagles", "Silver Strikers", 1, 0,
           { status: "abandoned",
             evidence: "Blue Eagles 1-0 Silver Strikers (abandoned 63')" }),
    result("Karonga United", "Ekwendeni Hammers", 2, 0),
  ],
  notes: null,
});

// ── Postponed and cancelled, correctly with no score ─────────────────────────
export const POSTPONED = asMessage({
  document_kind: "results",
  competition_hint: null, date: null, matchday: null,
  results: [
    result("Blue Eagles", "Silver Strikers", null, null, { status: "postponed" }),
    result("Karonga United", "Ekwendeni Hammers", null, null, { status: "cancelled" }),
  ],
  notes: null,
});

// ── A model that invented database identifiers ───────────────────────────────
// The failure this whole design is arranged around. Every id below is
// well-formed, plausible, and made up.
export const INVENTED_IDS = asMessage({
  document_kind: "results",
  competition_hint: null, date: null, matchday: null,
  results: [
    {
      ...result("Blue Eagles", "Silver Strikers", 2, 1),
      match_id: "MW_SL_2627_045",
      competition_id: "MW_SL",
      home_team_id: "MW_BE_M1",
      away_team_id: "MW_SIL_M1",
      season_id: "2026-27",
      confidence: 0.97,
      public_id: "11111111-1111-1111-1111-111111111111",
    },
  ],
  notes: null,
});

// ── Half a scoreline ─────────────────────────────────────────────────────────
// The commonest misread: a column boundary in the wrong place.
export const HALF_SCORE = asMessage({
  document_kind: "results",
  competition_hint: null, date: null, matchday: null,
  results: [
    result("Blue Eagles", "Silver Strikers", 2, null),
    result("Karonga United", "Ekwendeni Hammers", 1, 0),
  ],
  notes: null,
});

// ── Scores outside anything a football match produces ────────────────────────
export const ABSURD_SCORES = asMessage({
  document_kind: "results",
  competition_hint: null, date: null, matchday: null,
  results: [
    result("Blue Eagles", "Silver Strikers", 150, 2),
    result("Karonga United", "Ekwendeni Hammers", -1, 0),
    result("Mighty Wanderers", "Civil Service United", 3, 1),
  ],
  notes: null,
});

// ── Scorers present ──────────────────────────────────────────────────────────
// Read and stored; deliberately not published in this version.
export const WITH_SCORERS = asMessage({
  document_kind: "results",
  competition_hint: null, date: null, matchday: null,
  results: [
    result("Blue Eagles", "Silver Strikers", 2, 1, {
      scorers: [
        { player_raw: "A. Josephy", team_side: "home", minute: 12,
          own_goal: false, penalty: false },
        { player_raw: "G. Phiri", team_side: "home", minute: 67,
          own_goal: false, penalty: true },
        { player_raw: "S. Banda", team_side: "away", minute: 81,
          own_goal: null, penalty: null },
      ],
    }),
  ],
  notes: null,
});

// ── Nothing readable ─────────────────────────────────────────────────────────
export const UNREADABLE = asMessage({
  document_kind: "unreadable",
  competition_hint: null, date: null, matchday: null,
  results: [],
  notes: "Too blurred to make out any team names.",
});

// ── A better second read of the same blurry photo ────────────────────────────
export const RESCUED = asMessage({
  document_kind: "results",
  competition_hint: null, date: null, matchday: null,
  results: [
    result("Blue Eagles", "Silver Strikers", 2, 1),
    result("Karonga United", "Ekwendeni Hammers", 0, 3),
  ],
  notes: null,
}, { model: "claude-opus-5" });

// ── Malformed, in the several ways it can be ─────────────────────────────────
export const NOT_JSON = {
  id: "msg_test", type: "message", role: "assistant", model: "claude-sonnet-5",
  stop_reason: "end_turn",
  content: [{ type: "text", text: "Sorry, I can't read that image." }],
  usage: { input_tokens: 100, output_tokens: 12 },
};

export const EMPTY_CONTENT = {
  id: "msg_test", type: "message", role: "assistant", model: "claude-sonnet-5",
  stop_reason: "end_turn", content: [],
  usage: { input_tokens: 100, output_tokens: 0 },
};

export const REFUSED = {
  id: "msg_test", type: "message", role: "assistant", model: "claude-sonnet-5",
  stop_reason: "refusal",
  stop_details: { type: "refusal", category: null, explanation: "" },
  content: [], usage: { input_tokens: 100, output_tokens: 0 },
};

export const TRUNCATED = {
  id: "msg_test", type: "message", role: "assistant", model: "claude-sonnet-5",
  stop_reason: "max_tokens",
  content: [{ type: "text", text: '{"document_kind":"results","results":[{"home' }],
  usage: { input_tokens: 2500, output_tokens: 8000 },
};

export const WRONG_SHAPE = asMessage({
  document_kind: "results",
  results: "not an array",
  notes: null,
});
