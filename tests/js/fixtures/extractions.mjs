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

// ── An own goal, printed under the side it was credited to ───────────────────
// THE FIXTURE WHOSE ABSENCE WAS THE BUG. WITH_SCORERS is three ordinary goals,
// so nothing in this suite ever exercised own_goal — and the prompt, until Sep
// 2026, never told the model what team_side meant on one. goals.team_id is the
// BENEFICIARY and the scorer plays for the other side, so the two readings of
// team_side differ on exactly this row and agree everywhere else.
//
// Here Blue Eagles win 1-0 through a Silver Strikers defender, printed in the
// Blue Eagles column the way most Malawian graphics print it. What normalizeItem
// must do is carry it through unchanged: it is not this layer's job to decide
// which side it counted for, and #/results asks a person instead.
export const WITH_OWN_GOAL = asMessage({
  document_kind: "results",
  competition_hint: null, date: null, matchday: null,
  results: [
    result("Blue Eagles", "Silver Strikers", 1, 0, {
      scorers: [
        { player_raw: "S. Banda", team_side: "home", minute: 34,
          own_goal: true, penalty: false },
      ],
    }),
  ],
  notes: "The goal is marked OG in the graphic.",
});

// ── A club's match report ────────────────────────────────────────────────────
// silverstrikers.mw, 18 Oct 2026: Moyale Barracks 0-1 Silver Strikers, read
// the way the two prompts ask. It is the shape every FDH Bank Premiership club
// report takes, and every awkward thing in it is ordinary:
//   - ONE team sheet. The club lists its own XI; Moyale's players are named
//     only where they did something.
//   - A card for a player on no sheet (Viyuyi), and a red card for a TEAM
//     MANAGER, who is not a player and has no lineups row to carry it.
//   - The 63' changes are told twice, once as a sentence in another order;
//     the substitutions here are the explicit list, which is the answer.
//   - The goal already exists in the database; the assist does not.
//
// TWO ANSWERS, because it is read twice: MATCH_REPORT is the first pass (the
// results schema, which only says what kind of document this is and reads the
// result), MATCH_REPORT_PASS is the second (REPORT_SCHEMA).
export const MATCH_REPORT = asMessage({
  document_kind: "match_report",
  competition_hint: null, date: "2026-10-18", matchday: null,
  results: [
    result("Moyale Barracks", "Silver Strikers", 0, 1, {
      date: "2026-10-18", venue_raw: "Mzuzu Stadium",
      fields_not_present: ["kickoff", "matchday"],
      scorers: [
        { player_raw: "Innocent Shema", team_side: "away", minute: 7,
          own_goal: false, penalty: false },
      ],
      evidence: "Moyale Barracks 0-1 Silver Strikers",
    }),
  ],
  notes: "The report covers Silver Strikers only; Moyale Barracks' line-up is not given.",
});

const ssPlayer = (name_raw, role, over = {}) => ({
  team_side: "away", role, name_raw, shirt_number: "", position: "",
  captain: false, ...over });

export const MATCH_REPORT_PAYLOAD = {
  home_team_raw: "Moyale Barracks", away_team_raw: "Silver Strikers",
  players: [
    ssPlayer("George Chikooka", "starting", { position: "GK" }),
    ...["Innocent Shema", "Dan Sandukira", "Lumbani Nyondo", "Nickson Mwase",
        "Yankho Billiat"].map((n) => ssPlayer(n, "starting")),
    ssPlayer("Zebron Kalima", "starting", { captain: true }),
    ...["Ernest Petro", "Charles Chipala", "Chinsisi Maonga", "Emmanuel Allan"]
      .map((n) => ssPlayer(n, "starting")),
    ...["Festus Duwe", "Aubrey Khumbula", "Daah Rashid", "Andrew Joseph",
        "Christopher Mikuwa", "Austin Yasin", "Felix Dumakude", "Innocent Smart",
        "Lasuli Jali"].map((n) => ssPlayer(n, "bench")),
  ],
  substitutions: [
    { team_side: "away", on_raw: "Festus Duwe", off_raw: "Zebron Kalima", minute: "39" },
    { team_side: "away", on_raw: "Aubrey Khumbula", off_raw: "Charles Chipala", minute: "63" },
    { team_side: "away", on_raw: "Daah Rashid", off_raw: "Chinsisi Maonga", minute: "63" },
    { team_side: "away", on_raw: "Andrew Joseph", off_raw: "Emmanuel Allan", minute: "63" },
  ],
  goals: [
    { team_side: "away", scorer_raw: "Innocent Shema", assist_raw: "Chinsisi Maonga",
      minute: "7", kind: "header" },
  ],
  cards: [
    { team_side: "away", person_raw: "Nickson Mwase", card: "yellow", is_player: true },
    { team_side: "away", person_raw: "Charles Chipala", card: "yellow", is_player: true },
    { team_side: "away", person_raw: "Innocent Shema", card: "yellow", is_player: true },
    { team_side: "away", person_raw: "Emmanuel Chipatala", card: "red", is_player: false },
    { team_side: "home", person_raw: "Jimmy Viyuyi", card: "yellow", is_player: true },
  ],
  motm_raw: "Ernest Petro",
  referee: "Melluce Nyirongo", assistant_1: "Stammy Msukwa",
  assistant_2: "Jailosi Ngoma", fourth_official: "Mercy Kayira",
  coaches: [{ team_side: "away", name_raw: "Patricio Kulemeka" }],
};

export const MATCH_REPORT_PASS = asMessage(MATCH_REPORT_PAYLOAD);

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
