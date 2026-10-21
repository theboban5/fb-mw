/* The extraction contract, tested against fakes.
 *
 *     node --test 'tests/js/*.mjs'            (or: npm test)
 *
 * NOTHING HERE CALLS ANTHROPIC. Every response comes from
 * fixtures/extractions.mjs and every provider is fakeProvider, so the suite
 * costs nothing, needs no key, and runs offline — which is the only way these
 * assertions get run often enough to be worth having.
 *
 * What is being tested is not "does the model read graphics well". That is a
 * question for the evaluation set, and it is answered by a person looking at
 * real screenshots. What is tested here is everything we do with the answer:
 * whether a plausible-but-wrong response can get through, whether a failure
 * degrades or destroys, and whether anything we did not ask for can reach the
 * database.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  EXTRACTION_SCHEMA, IMPORT_STATUSES, REPORT_PROMPT, REPORT_SCHEMA, SYSTEM_PROMPT,
  buildContent, buildReportRequest, buildRequest, mergeReport, normalizeItem,
  normalizeMatchReport, parseExtraction, parseReport, shouldEscalate, usageRecord,
} from "../../supabase/functions/import-extract/extract.js";
import { anthropicProvider, fakeProvider, runExtraction }
  from "../../supabase/functions/import-extract/provider.js";
import {
  CLEAN_GRAPHIC, MESSY_TEXT, ABBREVIATED, FIXTURE_LIST, ABANDONED_WITH_SCORE,
  POSTPONED, INVENTED_IDS, HALF_SCORE, ABSURD_SCORES, WITH_SCORERS, WITH_OWN_GOAL,
  MATCH_REPORT, MATCH_REPORT_PASS, MATCH_REPORT_PAYLOAD, UNREADABLE,
  RESCUED, NOT_JSON, EMPTY_CONTENT, REFUSED, TRUNCATED, WRONG_SHAPE,
} from "./fixtures/extractions.mjs";

// ── The request we send ──────────────────────────────────────────────────────

test("no database content is ever sent to the model", () => {
  // The property that cannot be guaranteed by reading the prompt, because the
  // temptation is always to add "here are the teams in this league" later.
  const request = buildRequest({
    model: "claude-sonnet-5", imageBase64: "AAAA", text: "Bullets 1-0 Wanderers",
    sourceUrl: "https://example.com/post",
  });
  const serialized = JSON.stringify(request);
  for (const leak of ["MW_SL", "MW_BE_M1", "team_id", "competition_id",
                      "match_id", "season_id"]) {
    assert.equal(serialized.includes(leak), false, `leaked ${leak}`);
  }
});

test("the schema asks for no identifiers at all", () => {
  const serialized = JSON.stringify(EXTRACTION_SCHEMA);
  for (const forbidden of ["match_id", "team_id", "competition_id", "season_id",
                           "player_id", "public_id"]) {
    assert.equal(serialized.includes(forbidden), false, forbidden);
  }
});

test("the schema cannot propose an awarded result", () => {
  // 'awarded' is an administrative decision only an admin may record. Leaving
  // it out of the enum means the model cannot propose one even off a graphic
  // that says so.
  assert.equal(IMPORT_STATUSES.includes("awarded"), false);
  assert.equal(IMPORT_STATUSES.includes("scheduled"), false);
});

test("nullable fields use anyOf, not a type union", () => {
  // THE BUG THAT MADE EVERY IMPORT FAIL. `type: ["string","null"]` is ordinary
  // JSON Schema and is not in the supported structured-outputs subset, so the
  // API rejected every request with a 400 before reading a word — and because
  // every field here is nullable, plain pasted text failed exactly as surely
  // as a photograph, which is what disguised it as a model problem.
  const walk = (node, path) => {
    if (!node || typeof node !== "object") return;
    assert.equal(Array.isArray(node.type), false,
      `${path} uses a type union; use anyOf`);
    for (const [key, value] of Object.entries(node.properties ?? {})) {
      walk(value, `${path}.${key}`);
    }
    (node.anyOf ?? []).forEach((sub, i) => walk(sub, `${path}|${i}`));
    if (node.items) walk(node.items, `${path}[]`);
  };
  walk(EXTRACTION_SCHEMA, "root");
});

// Counted the way the API counts: a property its object does not list in
// `required` is optional; one using anyOf or a type array is a union.
const budget = (schema) => {
  let unions = 0;
  let optional = 0;
  const walk = (node) => {
    if (!node || typeof node !== "object") return;
    const required = new Set(node.required ?? []);
    for (const [key, value] of Object.entries(node.properties ?? {})) {
      if (value.anyOf || Array.isArray(value.type)) unions += 1;
      if (!required.has(key)) optional += 1;
      walk(value);
    }
    (node.anyOf ?? []).forEach(walk);
    if (node.items) walk(node.items);
  };
  walk(schema);
  return { unions, optional };
};

test("each schema stays inside the structured-outputs limits", () => {
  // THE API COUNTS, AND REFUSES EVERY REQUEST PAST THE LINE: 16 parameters
  // using anyOf and 24 optional ones, per request. Each pass sends one schema.
  for (const [name, schema] of [["results", EXTRACTION_SCHEMA],
                                ["report", REPORT_SCHEMA]]) {
    const { unions, optional } = budget(schema);
    assert.ok(unions <= 16, `${name}: ${unions} union-typed parameters; the API allows 16`);
    assert.ok(optional <= 24, `${name}: ${optional} optional parameters; the API allows 24`);
  }
});

test("the report schema has nothing optional and nothing nullable", () => {
  // The limit that broke production was neither of the published two: "the
  // compiled grammar is too large". Optional properties and unions are what
  // multiply a grammar, so the second pass's schema has none at all.
  assert.deepEqual(budget(REPORT_SCHEMA), { unions: 0, optional: 0 });
});

test("the results schema is the one that already compiled", () => {
  // THE FIRST VERSION GREW EVERY RESULT ROW, and every import of every kind
  // came back "The compiled grammar is too large" until it was reverted. A
  // results row is in an array of sixty; anything a match report needs goes
  // in REPORT_SCHEMA, the second pass, never here.
  const row = EXTRACTION_SCHEMA.properties.results.items.properties;
  assert.deepEqual(Object.keys(row).sort(), [
    "away_score", "away_team_raw", "competition_hint", "date", "evidence",
    "fields_not_present", "home_score", "home_team_raw", "kickoff", "matchday",
    "scorers", "status", "venue_raw",
  ]);
  assert.deepEqual(Object.keys(row.scorers.items.properties).sort(),
                   ["minute", "own_goal", "penalty", "player_raw", "team_side"]);
});

test("every nullable field can still express null", () => {
  const nullable = EXTRACTION_SCHEMA.properties.results.items
    .properties.home_score;
  assert.deepEqual(nullable.anyOf, [{ type: "integer" }, { type: "null" }]);
});

test("every object in the schema forbids extra properties", () => {
  const walk = (node, path) => {
    if (!node || typeof node !== "object") return;
    if (node.type === "object") {
      assert.equal(node.additionalProperties, false,
        `${path} must set additionalProperties:false`);
    }
    for (const [key, value] of Object.entries(node.properties ?? {})) {
      walk(value, `${path}.${key}`);
    }
    if (node.items) walk(node.items, `${path}[]`);
  };
  walk(EXTRACTION_SCHEMA, "root");
});

test("the system prompt is stable, so the cache breakpoint is worth having", () => {
  const a = buildRequest({ model: "claude-sonnet-5", text: "one" });
  const b = buildRequest({ model: "claude-sonnet-5", text: "two" });
  assert.deepEqual(a.system, b.system);
  assert.equal(a.system[0].cache_control.type, "ephemeral");
  assert.equal(a.system[0].text, SYSTEM_PROMPT);
});

test("an image is sent as an image block before the text", () => {
  const content = buildContent({ imageBase64: "AAAA", mediaType: "image/png",
                                 text: "hello" });
  assert.equal(content[0].type, "image");
  assert.equal(content[0].source.media_type, "image/png");
  assert.equal(content.at(-1).type, "text");
});

test("a source URL is given as context and never as something to fetch", () => {
  const content = buildContent({ text: "x", sourceUrl: "https://example.com/p" });
  const text = content.map((c) => c.text ?? "").join(" ");
  assert.match(text, /cannot open it/);
});

test("the cheap pass asks for low effort", () => {
  assert.equal(buildRequest({ model: "m" }).output_config.effort, "low");
  assert.equal(buildRequest({ model: "m", effort: "high" }).output_config.effort,
               "high");
});

// ── Accepting the answer ─────────────────────────────────────────────────────

test("a clean graphic reads straight through", () => {
  const out = parseExtraction(CLEAN_GRAPHIC);
  assert.equal(out.ok, true);
  assert.equal(out.documentKind, "results");
  assert.equal(out.items.length, 3);
  assert.equal(out.items[0].home_team_raw, "Blue Eagles");
  assert.equal(out.items[0].home_score, 2);
  assert.equal(out.items[0].status, "played");
  assert.equal(out.competitionHint, "FDH Bank Premiership");
  assert.equal(out.dropped, 0);
});

test("names come back as printed, never expanded", () => {
  const out = parseExtraction(ABBREVIATED);
  assert.deepEqual(out.items.map((i) => i.home_team_raw), ["BE", "MW"]);
  const messy = parseExtraction(MESSY_TEXT);
  assert.equal(messy.items[0].home_team_raw, "Bullets");
  assert.equal(messy.items[1].away_team_raw, "Karonga Utd");
});

test("invented identifiers cannot survive normalization", () => {
  // The one that matters most. Every id in this fixture is well-formed and
  // made up; none of them is a field we asked for, so none is picked up.
  const out = parseExtraction(INVENTED_IDS);
  assert.equal(out.items.length, 1);
  const item = out.items[0];
  for (const forbidden of ["match_id", "competition_id", "home_team_id",
                           "away_team_id", "season_id", "public_id",
                           "confidence"]) {
    assert.equal(forbidden in item, false, `${forbidden} survived`);
  }
  // ...and the football in the same row is still read.
  assert.equal(item.home_score, 2);
  assert.equal(item.away_score, 1);
});

test("an abandoned match cannot carry a score", () => {
  // The score printed beside it is real and it is not a result;
  // apply_match_report would refuse the row.
  const out = parseExtraction(ABANDONED_WITH_SCORE);
  const abandoned = out.items.find((i) => i.status === "abandoned");
  assert.equal(abandoned.home_score, null);
  assert.equal(abandoned.away_score, null);
  // The good row beside it is untouched.
  assert.equal(out.items.find((i) => i.status === "played").home_score, 2);
});

test("postponed and cancelled come through with no score", () => {
  const out = parseExtraction(POSTPONED);
  assert.deepEqual(out.items.map((i) => i.status), ["postponed", "cancelled"]);
  assert.ok(out.items.every((i) => i.home_score === null && i.away_score === null));
});

test("half a scoreline is dropped rather than shown as a result", () => {
  const out = parseExtraction(HALF_SCORE);
  assert.equal(out.items.length, 1);
  assert.equal(out.items[0].home_team_raw, "Karonga United");
  assert.equal(out.dropped, 1, "the dropped row is counted, not hidden");
});

test("a score no football match produces is dropped, not clamped", () => {
  // 150 is not evidence that the score was 99. It is evidence of a misread.
  const out = parseExtraction(ABSURD_SCORES);
  assert.equal(out.items.length, 1);
  assert.equal(out.items[0].home_team_raw, "Mighty Wanderers");
});

test("scorers are read and kept, ready for a later version", () => {
  const out = parseExtraction(WITH_SCORERS);
  assert.equal(out.items[0].scorers.length, 3);
  assert.equal(out.items[0].scorers[0].player_raw, "A. Josephy");
  assert.equal(out.items[0].scorers[1].penalty, true);
  // A null own_goal is a missing value, not a true one.
  assert.equal(out.items[0].scorers[2].own_goal, false);
});

test("an own goal is carried through exactly as printed, not re-sided", () => {
  // THE CASE NO FIXTURE COVERED, WHICH IS WHY THE PROMPT NEVER DEFINED IT.
  // team_side says where the name was printed. goals.team_id is the side that
  // BENEFITED, and on an own goal the scorer plays for the other one — so this
  // is the single row where the two readings differ. Nothing in this layer may
  // resolve that: #/results asks a person which side it counted for, and a
  // helpful swap here would destroy the only evidence of what the picture said.
  const out = parseExtraction(WITH_OWN_GOAL);
  const scorer = out.items[0].scorers[0];
  assert.equal(scorer.own_goal, true);
  assert.equal(scorer.player_raw, "S. Banda");
  assert.equal(scorer.team_side, "home", "printed under the home column");
  assert.equal(scorer.minute, 34);
  assert.equal(scorer.penalty, false);
});

test("the prompt tells the model what a scorer field means", () => {
  // It did not, for the whole life of the importer: the schema asked for
  // team_side and own_goal and the instructions never mentioned either, so
  // every stored scorer row was read under a convention nobody wrote down.
  // These are the two sentences that stop that being true again.
  assert.match(SYSTEM_PROMPT, /team_side is WHERE THE NAME IS PRINTED/);
  assert.match(SYSTEM_PROMPT, /own_goal is true only when the source marks it/);
});

const mergedReport = () =>
  mergeReport(parseExtraction(MATCH_REPORT).items[0], MATCH_REPORT_PAYLOAD);

test("a club's match report is one result, then its sheet on top", () => {
  const first = parseExtraction(MATCH_REPORT);
  assert.equal(first.documentKind, "match_report");
  assert.equal(first.items.length, 1);
  assert.deepEqual(first.items[0].team_sheets, [], "the first pass reads no sheet");

  const item = mergedReport();
  assert.equal(item.away_score, 1, "the result is the first pass's");
  assert.equal(item.team_sheets.length, 1, "one sheet is the ordinary case");
  const sheet = item.team_sheets[0];
  assert.equal(sheet.team_side, "away");
  assert.equal(sheet.starting.length, 11);
  assert.equal(sheet.bench.length, 9);
  assert.equal(sheet.starting[0].position, "GK");
  assert.equal(sheet.starting.find((p) => p.captain).name_raw, "Zebron Kalima");
  assert.deepEqual(item.substitutions.map((x) => x.minute), ["39", "63", "63", "63"]);
  assert.deepEqual(item.scorers, [{ player_raw: "Innocent Shema", team_side: "away",
    minute: 7, own_goal: false, penalty: false, assist_raw: "Chinsisi Maonga",
    method: "header" }]);
  assert.equal(item.motm_raw, "Ernest Petro");
  assert.equal(item.officials.fourth_official, "Mercy Kayira");
  assert.deepEqual(item.coaches, [{ team_side: "away", name_raw: "Patricio Kulemeka" }]);
});

test("a report printing the fixture the other way round is turned to match", () => {
  // The row is what the matcher read, so the row's orientation wins.
  const item = parseExtraction(MATCH_REPORT).items[0];
  const reversed = { ...MATCH_REPORT_PAYLOAD,
    home_team_raw: "Silver Strikers FC", away_team_raw: "Moyale Barracks",
    players: MATCH_REPORT_PAYLOAD.players.map((p) => ({ ...p, team_side: "home" })),
    goals: [{ team_side: "home", scorer_raw: "Innocent Shema", assist_raw: "",
              minute: "7", kind: "" }] };
  const merged = mergeReport(item, reversed);
  assert.equal(merged.team_sheets[0].team_side, "away");
  assert.equal(merged.scorers[0].team_side, "away");
});

test("teams the row does not recognise leave every side unknown", () => {
  const item = parseExtraction(MATCH_REPORT).items[0];
  const merged = mergeReport(item, { ...MATCH_REPORT_PAYLOAD,
    home_team_raw: "Wanderers", away_team_raw: "Bullets" });
  assert.ok(merged.team_sheets.every((t) => t.team_side === "unknown"));
});

test("a report silent about goals leaves the first pass's scorers", () => {
  const merged = mergeReport(parseExtraction(MATCH_REPORT).items[0],
                             { ...MATCH_REPORT_PAYLOAD, goals: [] });
  assert.equal(merged.scorers[0].player_raw, "Innocent Shema");
  assert.equal(merged.scorers[0].assist_raw, "");
});

test("a card for someone who is not a player is kept, and says so", () => {
  // The team manager's red is worth showing the reporter and has nowhere to
  // be written; is_player is how the client tells the two apart.
  const cards = mergedReport().cards;
  assert.equal(cards.length, 5);
  const manager = cards.find((c) => c.person_raw === "Emmanuel Chipatala");
  assert.equal(manager.is_player, false);
  assert.equal(manager.card, "red");
});

test("an unrecognised card is dropped, never read as a yellow", () => {
  const out = normalizeMatchReport({ cards: [
    { team_side: "home", person_raw: "A", card: "orange", is_player: true },
    { team_side: "home", person_raw: "B", card: "second_yellow" },
  ] });
  assert.deepEqual(out.cards.map((c) => c.person_raw), ["B"]);
  assert.equal(out.cards[0].is_player, true, "only an explicit false is not a player");
});

test("a minute is kept only in a shape a team sheet accepts", () => {
  const minutes = ["39'", "90 + 2", "63’", "second half", "", "1000"].map((minute) =>
    normalizeMatchReport({ substitutions: [{ on_raw: "X", minute }] })
      .substitutions[0].minute);
  assert.deepEqual(minutes, ["39", "90+2", "63", "", "", ""]);
});

test("a position that landed in the shirt box is not a shirt", () => {
  const out = normalizeMatchReport({ team_sheets: [{ team_side: "home",
    starting: [{ name_raw: "A", shirt_number: "GK", position: "goalie" },
               { name_raw: "B", shirt_number: " 7 ", position: "MF" }],
    bench: [] }] });
  assert.deepEqual(out.team_sheets[0].starting.map((p) => [p.shirt_number, p.position]),
                   [["", ""], ["7", "MF"]]);
});

test("a row stored before match reports existed reads as an empty report", () => {
  // report_imports keeps every extraction, and the client re-reads old ones
  // through normalizeMatchReport. None of those rows has these keys.
  const out = normalizeMatchReport(normalizeItem(
    { home_team_raw: "A", away_team_raw: "B", status: "played",
      home_score: 1, away_score: 0, scorers: [{ player_raw: "X", team_side: "home" }] }, 1));
  assert.deepEqual(out.team_sheets, []);
  assert.deepEqual(out.cards, []);
  assert.equal(out.motm_raw, "");
  assert.equal(out.officials.referee, "");
});

test("the report prompt says which of two tellings of a substitution to believe", () => {
  assert.match(REPORT_PROMPT, /the list is the answer/);
  assert.match(REPORT_PROMPT, /Never add a player who is merely mentioned/);
  assert.match(REPORT_PROMPT, /assist_raw is the player the source credits/);
  // ...and none of it leaked into the results prompt, which is cached and
  // read by every results import.
  assert.doesNotMatch(SYSTEM_PROMPT, /assist_raw/);
});

test("the report pass sends the same picture with its own schema", () => {
  const request = buildReportRequest({ model: "m", text: "Moyale 0-1 Silver" });
  assert.equal(request.output_config.format.schema, REPORT_SCHEMA);
  assert.equal(request.system[0].text, REPORT_PROMPT);
  assert.match(request.messages[0].content.at(-1).text, /Read the match report/);
  assert.match(buildRequest({ model: "m", text: "x" }).messages[0].content.at(-1).text,
               /Read the football results/);
});

test("a report answer that is not one becomes a category", () => {
  assert.equal(parseReport(REFUSED).errorCategory, "refused");
  assert.equal(parseReport(TRUNCATED).errorCategory, "too_long");
  assert.equal(parseReport(NOT_JSON).errorCategory, "bad_output");
  assert.equal(parseReport(MATCH_REPORT_PASS).ok, true);
});

test("a fixture list is recognised rather than published", () => {
  const out = parseExtraction(FIXTURE_LIST);
  assert.equal(out.ok, true);
  assert.equal(out.documentKind, "fixtures");
  // Every row is status unknown with no score, so nothing here can be
  // mistaken for a result by the screen that renders it.
  assert.ok(out.items.every((i) => i.status === "unknown"));
  assert.ok(out.items.every((i) => i.home_score === null));
});

test("an unknown status becomes unknown rather than being trusted", () => {
  const item = normalizeItem({ home_team_raw: "A", away_team_raw: "B",
                               status: "won_on_penalties", home_score: 3,
                               away_score: 2 }, 1);
  assert.equal(item.status, "unknown");
  assert.equal(item.home_score, null);
});

test("a row with a missing team name is not usable", () => {
  const out = parseExtraction({
    ...CLEAN_GRAPHIC,
    content: [{ type: "text", text: JSON.stringify({
      document_kind: "results",
      results: [{ home_team_raw: "", away_team_raw: "B", home_score: 1,
                  away_score: 0, status: "played", fields_not_present: [] }],
      notes: null }) }],
  });
  assert.equal(out.items.length, 0);
});

// ── Malformed output ─────────────────────────────────────────────────────────

test("every malformed shape becomes a category, never an exception", () => {
  const cases = [
    [NOT_JSON, "bad_output"],
    [EMPTY_CONTENT, "empty_output"],
    [REFUSED, "refused"],
    [TRUNCATED, "too_long"],
    [null, "no_response"],
    [undefined, "no_response"],
  ];
  for (const [message, expected] of cases) {
    const out = parseExtraction(message);
    assert.equal(out.ok, false, expected);
    assert.equal(out.errorCategory, expected);
    assert.deepEqual(out.items, []);
  }
});

test("a results field that is not an array is survivable", () => {
  const out = parseExtraction(WRONG_SHAPE);
  assert.equal(out.ok, true);
  assert.deepEqual(out.items, []);
});

test("a category never leaks provider detail", () => {
  // Everything parseExtraction can return is from a closed vocabulary, so a
  // provider message can never reach a reporter through it.
  const allowed = new Set(["", "no_response", "refused", "too_long",
                           "empty_output", "bad_output"]);
  for (const message of [NOT_JSON, EMPTY_CONTENT, REFUSED, TRUNCATED, null,
                         CLEAN_GRAPHIC]) {
    assert.ok(allowed.has(parseExtraction(message).errorCategory));
  }
});

// ── Escalation ───────────────────────────────────────────────────────────────

test("a clean read never escalates", () => {
  assert.equal(shouldEscalate(parseExtraction(CLEAN_GRAPHIC)), false);
});

test("an unreadable or empty read escalates", () => {
  assert.equal(shouldEscalate(parseExtraction(UNREADABLE)), true);
  assert.equal(shouldEscalate(parseExtraction(HALF_SCORE)), true,
    "a dropped row means something was misread");
});

test("a refusal and an over-long response are not retried", () => {
  // Neither is a reading problem, so neither is worth paying twice for.
  assert.equal(shouldEscalate(parseExtraction(REFUSED)), false);
  assert.equal(shouldEscalate(parseExtraction(TRUNCATED)), false);
});

test("a failed CALL is never escalated", () => {
  // Escalation is for a document the cheap model read badly. If the call did
  // not succeed there is no reading to improve on, and a bigger model hits the
  // identical wall — which is how a schema bug that 400'd every request
  // quietly cost two calls per import instead of one.
  for (const category of ["provider_error", "not_configured", "rate_limited",
                          "provider_unreachable", "timeout", "bad_output",
                          "no_response", "empty_output"]) {
    assert.equal(shouldEscalate({ ok: false, errorCategory: category }), false,
                 category);
  }
  assert.equal(shouldEscalate(null), false);
});

test("a provider failure costs exactly one call", async () => {
  const provider = fakeProvider(null, { failWith: "provider_error" });
  const out = await runExtraction({
    provider, model: "claude-sonnet-5", fallbackModel: "claude-opus-5",
    input: { text: "x" },
  });
  assert.equal(provider.calls.length, 1, "a rejected request is not re-sent");
  assert.equal(out.ok, false);
});

test("escalation is off when there is no fallback model", () => {
  assert.equal(shouldEscalate(parseExtraction(UNREADABLE),
                              { hasFallback: false }), false);
});

test("the common case costs exactly one call", async () => {
  const provider = fakeProvider(CLEAN_GRAPHIC);
  const out = await runExtraction({
    provider, model: "claude-sonnet-5", fallbackModel: "claude-opus-5",
    input: { text: "x" },
  });
  assert.equal(provider.calls.length, 1);
  assert.equal(out.escalated, false);
  assert.equal(out.items.length, 3);
  assert.equal(out.usage.length, 1);
  assert.equal(out.usage[0].model, "claude-sonnet-5");
});

test("a blurry photo escalates to the stronger model and keeps the better read",
     async () => {
  const provider = fakeProvider([UNREADABLE, RESCUED]);
  const out = await runExtraction({
    provider, model: "claude-sonnet-5", fallbackModel: "claude-opus-5",
    input: { imageBase64: "AAAA" },
  });
  assert.equal(provider.calls.length, 2);
  assert.equal(provider.calls[0].model, "claude-sonnet-5");
  assert.equal(provider.calls[1].model, "claude-opus-5");
  assert.equal(provider.calls[1].output_config.effort, "high");
  assert.equal(out.escalated, true);
  assert.equal(out.escalationHelped, true);
  assert.equal(out.items.length, 2);
  assert.equal(out.usage.length, 2, "both attempts are accounted for");
  // The verdict has to survive into report_imports.usage, which is the only
  // thing index.ts stores. Returning it alongside is what made it invisible.
  assert.equal(out.usage[1].helped, true);
});

test("whether escalation helped is stored, not just returned", async () => {
  // "This import cost twice" was recorded from the start; "it was worth paying
  // twice" was computed and thrown away, so no import ever run can answer it.
  const provider = fakeProvider([HALF_SCORE, UNREADABLE]);
  const out = await runExtraction({
    provider, model: "claude-sonnet-5", fallbackModel: "claude-opus-5",
    input: { imageBase64: "AAAA" },
  });
  assert.equal(out.usage[1].escalated, true);
  assert.equal(out.usage[1].helped, false);
  assert.equal(out.usage[0].helped, undefined,
               "the verdict belongs to the escalated attempt, not the first");
});

test("a failed second call leaves no verdict rather than a false one",
     async () => {
  // The second attempt never reaches usageRecord, so the array holds one
  // record — the FIRST model's. Stamping the last one would file "escalation
  // did not help" against a model that was never escalated to. An absent flag
  // says "no second read came back", which is the truth.
  const provider = fakeProvider([UNREADABLE, { __error: "timeout" }]);
  const out = await runExtraction({
    provider, model: "claude-sonnet-5", fallbackModel: "claude-opus-5",
    input: { imageBase64: "AAAA" },
  });
  assert.equal(out.escalated, true);
  assert.equal(out.usage.length, 1);
  assert.equal(out.usage[0].escalated, false);
  assert.equal(out.usage[0].helped, undefined);
});

test("a second read that is no better does not overwrite the first", async () => {
  // A stronger model that also read nothing has not earned the right to
  // replace a weaker one's partial answer with an empty one.
  const provider = fakeProvider([HALF_SCORE, UNREADABLE]);
  const out = await runExtraction({
    provider, model: "claude-sonnet-5", fallbackModel: "claude-opus-5",
    input: { imageBase64: "AAAA" },
  });
  assert.equal(out.escalationHelped, false);
  assert.equal(out.items.length, 1);
  assert.equal(out.items[0].home_team_raw, "Karonga United");
});

test("with no fallback configured the import still returns what it got",
     async () => {
  const provider = fakeProvider(HALF_SCORE);
  const out = await runExtraction({
    provider, model: "claude-sonnet-5", fallbackModel: "",
    input: { text: "x" },
  });
  assert.equal(provider.calls.length, 1);
  assert.equal(out.items.length, 1);
});

test("an org-level key sends the workspace header; a scoped one does not", async () => {
  // A key created inside a workspace carries that already. A key created at
  // the ORGANISATION level does not, and every request without this header
  // comes back 400 invalid_request_error — which is what made the first live
  // import fail on plain text as surely as on a photograph.
  const seen = [];
  const fetchImpl = async (_url, init) => {
    seen.push(init.headers);
    return { ok: true, status: 200, json: async () => ({ content: [] }) };
  };
  await anthropicProvider({ apiKey: "k", workspaceId: "wrkspc_1", fetchImpl })({});
  assert.equal(seen[0]["anthropic-workspace-id"], "wrkspc_1");

  await anthropicProvider({ apiKey: "k", fetchImpl })({});
  assert.equal("anthropic-workspace-id" in seen[1], false);

  // Whitespace is not a workspace id.
  await anthropicProvider({ apiKey: "k", workspaceId: "  ", fetchImpl })({});
  assert.equal("anthropic-workspace-id" in seen[2], false);
});

test("a provider failure is a category and costs nothing to record", async () => {
  const provider = fakeProvider(null, { failWith: "rate_limited" });
  const out = await runExtraction({
    provider, model: "claude-sonnet-5", fallbackModel: "",
    input: { text: "x" },
  });
  assert.equal(out.ok, false);
  assert.equal(out.errorCategory, "rate_limited");
  assert.deepEqual(out.usage, []);
});

// ── Cost accounting ──────────────────────────────────────────────────────────

test("usage records tokens, the model, and an estimate", () => {
  const record = usageRecord("claude-sonnet-5", CLEAN_GRAPHIC.usage);
  assert.equal(record.model, "claude-sonnet-5");
  assert.equal(record.input_tokens, 2500);
  assert.equal(record.output_tokens, 600);
  assert.equal(record.cache_creation_input_tokens, 1400);
  assert.ok(record.usd_estimate > 0 && record.usd_estimate < 0.1);
});

test("an unknown model records tokens but claims no price", () => {
  // Prices change without this file changing, so the tokens are the durable
  // record and the dollar figure is a convenience that knows when to be quiet.
  const record = usageRecord("some-future-model", CLEAN_GRAPHIC.usage);
  assert.equal(record.input_tokens, 2500);
  assert.equal("usd_estimate" in record, false);
});

test("missing usage does not throw", () => {
  const record = usageRecord("claude-sonnet-5", undefined);
  assert.equal(record.input_tokens, 0);
});

// ── The second pass ──────────────────────────────────────────────────────────

test("a match report is read twice and the sheet lands on its row", async () => {
  const provider = fakeProvider([MATCH_REPORT, MATCH_REPORT_PASS]);
  const out = await runExtraction({ provider, model: "claude-sonnet-5",
                                    input: { text: "report" } });
  assert.equal(provider.calls.length, 2);
  assert.equal(provider.calls[1].output_config.format.schema, REPORT_SCHEMA);
  assert.equal(out.ok, true);
  assert.equal(out.items[0].team_sheets[0].starting.length, 11);
  assert.equal(out.items[0].scorers[0].assist_raw, "Chinsisi Maonga");
  assert.deepEqual(out.usage.map((u) => u.pass ?? "results"), ["results", "report"]);
  assert.equal(out.reportError, undefined);
});

test("a results graphic is read once, exactly as before", async () => {
  const provider = fakeProvider(CLEAN_GRAPHIC);
  await runExtraction({ provider, model: "claude-sonnet-5", input: { text: "x" } });
  assert.equal(provider.calls.length, 1);
});

test("a report pass that fails leaves the result importable", async () => {
  // THE PROPERTY THE FIRST VERSION DID NOT HAVE. A match report's schema made
  // every import fail; now the worst a report can do is arrive without its
  // line-ups, with the reason kept for whoever looks.
  const provider = fakeProvider([MATCH_REPORT, { __error: "provider_error" },
                                 { __error: "provider_error" }]);
  const out = await runExtraction({ provider, model: "claude-sonnet-5",
                                    fallbackModel: "claude-opus-5",
                                    input: { text: "report" } });
  assert.equal(out.ok, true);
  assert.equal(out.items[0].away_score, 1);
  assert.deepEqual(out.items[0].team_sheets, []);
  assert.equal(out.reportError.category, "provider_error");
  assert.equal(provider.calls.length, 3, "main model, then the fallback once");
});
