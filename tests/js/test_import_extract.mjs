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
  EXTRACTION_SCHEMA, IMPORT_STATUSES, SYSTEM_PROMPT, buildContent, buildRequest,
  normalizeItem, parseExtraction, shouldEscalate, usageRecord,
} from "../../supabase/functions/import-extract/extract.js";
import { fakeProvider, runExtraction }
  from "../../supabase/functions/import-extract/provider.js";
import {
  CLEAN_GRAPHIC, MESSY_TEXT, ABBREVIATED, FIXTURE_LIST, ABANDONED_WITH_SCORE,
  POSTPONED, INVENTED_IDS, HALF_SCORE, ABSURD_SCORES, WITH_SCORERS, UNREADABLE,
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
