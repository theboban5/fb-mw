/* How we talk to a model, behind a seam.
 *
 * A provider is one function:
 *
 *     async (request) -> { ok, message, errorCategory, status }
 *
 * `request` is whatever buildRequest() produced. That shape is Anthropic's
 * Messages API today, and the seam is deliberately here rather than at some
 * imagined vendor-neutral request object: a fake "universal" schema would be a
 * second thing to maintain that no provider actually speaks, and translating
 * INTO it and back out is more code than translating once, later, if there is
 * ever a second provider. What the seam buys now is the thing that matters —
 * every test in this repo runs against a fake, and no test run costs money or
 * needs a key.
 *
 * TWO PROVIDERS SHIP: the real one, and a fake. There is no third mode where
 * the real one is called with a recorded response, because that is a fake with
 * extra steps and a way to accidentally hit the network in CI.
 */

import { buildRequest, parseExtraction, shouldEscalate, usageRecord }
  from "./extract.js";

const API_VERSION = "2023-06-01";

/** The real one.
 *
 *  Every failure becomes a CATEGORY. The provider's own message goes to the
 *  console for an operator and never into the return value: an API error can
 *  name a model, an account, a rate-limit tier or an internal host, and all of
 *  that would end up in report_imports.error_category, which a reporter reads.
 */
export function anthropicProvider({ apiKey, workspaceId = "",
                                    fetchImpl = fetch,
                                    baseUrl = "https://api.anthropic.com",
                                    timeoutMs = 60_000 } = {}) {
  if (!apiKey) throw new Error("anthropicProvider needs an apiKey");

  // AN ORG-LEVEL KEY NEEDS TO BE TOLD WHICH WORKSPACE TO BILL. A key created
  // inside a workspace carries that already and needs nothing; a key created
  // at the organisation level does not, and every request without the header
  // comes back 400 invalid_request_error. Both kinds are ordinary things to
  // be handed, so both are supported rather than one being documented as the
  // only correct choice.
  const workspace = String(workspaceId || "").trim();

  return async function call(request) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    let response;
    try {
      response = await fetchImpl(`${baseUrl}/v1/messages`, {
        method: "POST",
        signal: controller.signal,
        headers: {
          "content-type": "application/json",
          "x-api-key": apiKey,
          "anthropic-version": API_VERSION,
          ...(workspace ? { "anthropic-workspace-id": workspace } : {}),
        },
        body: JSON.stringify(request),
      });
    } catch (err) {
      console.error("[import] provider request failed", err?.name, err?.message);
      return { ok: false,
               errorCategory: err?.name === "AbortError" ? "timeout"
                                                         : "provider_unreachable" };
    } finally {
      clearTimeout(timer);
    }

    if (!response.ok) {
      // Logged in full, returned as a category. 401 is the one worth telling
      // apart, because it is the only one an operator fixes rather than waits
      // out, and it is what an unset secret looks like.
      const detail = await response.text().catch(() => "");
      console.error("[import] provider error", response.status, detail.slice(0, 500));
      // `detail` is carried OUT as well as logged, and index.ts stores it on
      // the import row. Not in the reporter's message — a provider error can
      // name a model, an account or a rate-limit tier — but an import that
      // failed with no way to find out why is a hole this feature already fell
      // into once: a 400 on a malformed schema looked exactly like "the model
      // could not read it", and the only way to tell them apart was a log
      // nobody could reach. report_imports is readable by its owner and by
      // admins, never by anon and never by the site.
      const short = detail.slice(0, 400);
      if (response.status === 401 || response.status === 403) {
        return { ok: false, errorCategory: "not_configured",
                 status: response.status, detail: short };
      }
      if (response.status === 429) {
        return { ok: false, errorCategory: "rate_limited",
                 status: response.status, detail: short };
      }
      return { ok: false, errorCategory: "provider_error",
               status: response.status, detail: short };
    }

    let message;
    try {
      message = await response.json();
    } catch {
      return { ok: false, errorCategory: "bad_output" };
    }
    return { ok: true, message };
  };
}

/** The fake. Takes the messages to hand back, in order, and records what it
 *  was asked — so a test can assert that no database content was ever sent,
 *  which is a property no amount of reading the prompt can guarantee. */
export function fakeProvider(messages, { failWith = null } = {}) {
  const queue = Array.isArray(messages) ? [...messages] : [messages];
  const calls = [];
  const provider = async (request) => {
    calls.push(request);
    if (failWith) return { ok: false, errorCategory: failWith };
    const next = queue.length > 1 ? queue.shift() : queue[0];
    if (next && next.__error) {
      return { ok: false, errorCategory: next.__error };
    }
    return { ok: true, message: next };
  };
  provider.calls = calls;
  return provider;
}

/** One import, end to end: read it, and read it again properly if the first
 *  pass visibly failed.
 *
 *  THE ESCALATION IS ABOUT THE DOCUMENT, NEVER ABOUT CONFIDENCE. The model's
 *  own certainty is not consulted anywhere in this system — shouldEscalate()
 *  looks at whether anything came back and whether rows had to be dropped. A
 *  clean read of a clean graphic pays once, which is what makes the cheap
 *  default worth having.
 *
 *  BOTH ATTEMPTS ARE ACCOUNTED FOR. usage is an array, not a total, because
 *  "this import cost twice" is the fact worth being able to see when deciding
 *  whether the escalation is earning its place. */
export async function runExtraction({
  provider, model, fallbackModel = "", input = {}, maxTokens = 8000,
} = {}) {
  if (!provider) throw new Error("runExtraction needs a provider");
  if (!model) throw new Error("runExtraction needs a model");

  const usage = [];
  const attempt = async (useModel, effort, escalated) => {
    const outcome = await provider(buildRequest({
      model: useModel, effort, maxTokens, ...input }));
    if (!outcome.ok) {
      return { ok: false, errorCategory: outcome.errorCategory || "provider_error",
               detail: outcome.detail || "", status: outcome.status || 0,
               items: [] };
    }
    usage.push(usageRecord(useModel, outcome.message?.usage, { escalated }));
    const parsed = parseExtraction(outcome.message);
    parsed.model = useModel;
    return parsed;
  };

  const first = await attempt(model, "low", false);

  const canEscalate = Boolean(fallbackModel) && fallbackModel !== model;
  if (!shouldEscalate(first, { hasFallback: canEscalate })) {
    return { ...first, usage, escalated: false };
  }

  const second = await attempt(fallbackModel, "high", true);

  // The better of the two, and the FIRST is kept when the second is no better
  // — a stronger model that also read nothing has not earned the right to
  // overwrite a weaker one's partial answer with an empty one.
  const secondIsBetter = second.ok
    && (!first.ok || second.items.length > first.items.length
        || (second.items.length === first.items.length && (second.dropped || 0) < (first.dropped || 0)));

  const chosen = secondIsBetter ? second : first;
  return { ...chosen, usage, escalated: true, escalationHelped: secondIsBetter };
}
