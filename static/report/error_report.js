/* What to report when a screen breaks, and how often — with no DOM, no network
 * and no Supabase in it.
 *
 * WHY THIS IS A SEPARATE FILE. It is forty lines of rules, and every one of
 * them is about a failure that is already happening. An error handler that
 * throws, or that calls the database sixty times a second because the throw is
 * inside a render loop, turns one broken screen into an outage. Those are
 * exactly the properties you want to assert rather than hope for, and they
 * cannot be asserted about a browser that is already on fire.
 *
 * It imports nothing, on purpose.
 */

/** How many distinct problems one session will report before it stops.
 *
 *  Distinct, not total: a screen that throws the same TypeError on every draw
 *  is ONE problem, however many times it happens, and reporting it once is
 *  what the admin view wants anyway (it groups by route and message). The cap
 *  is for the other shape — a session that has genuinely gone wrong in several
 *  ways and would otherwise walk through them all. The database caps again at
 *  twenty an hour per reporter; neither is trusted to be the only one working.
 */
export const SESSION_CAP = 8;

const MESSAGE_MAX = 500;
const STACK_MAX = 2000;

/** The hash route a reporter was on, reduced to the screen's name.
 *
 *  '#/m/9f3c-…?from=ops' is a match id and a referrer, neither of which is the
 *  answer to "which screen is broken" — and the id is a fixture somebody is
 *  reporting on, which this table has no business keeping. So '/m/…' it is.
 *  The query string goes for the same reason: it can carry a search term.
 */
export function routeOf(hash) {
  const raw = String(hash || "").replace(/^#/, "").split("?")[0] || "/";
  const parts = raw.split("/").filter(Boolean);
  if (!parts.length) return "/";
  // One segment is a screen name (/add, /teams). A second is nearly always an
  // identifier — a match, a competition, a national team — so it is dropped
  // rather than recorded.
  return `/${parts[0]}${parts.length > 1 ? "/…" : ""}`;
}

/** An ErrorEvent, a PromiseRejectionEvent or anything else, reduced to what is
 *  worth sending. Everything is optional and every absence renders as blank,
 *  because a report that arrives incomplete beats one that throws on the way.
 */
export function errorPayload(reason, { hash = "", userAgent = "" } = {}) {
  let message = "";
  let stack = "";
  try {
    if (reason instanceof Error || (reason && typeof reason === "object")) {
      message = String(reason.message ?? reason.reason?.message ?? "");
      stack = String(reason.stack ?? reason.reason?.stack ?? "");
      // A DOM ErrorEvent carries the thrown value one level down.
      if (!message && reason.error) {
        message = String(reason.error.message ?? "");
        stack = String(reason.error.stack ?? "");
      }
    }
    if (!message) message = String(reason ?? "");
  } catch {
    // Some thrown values refuse to be stringified. That is itself worth
    // recording, and it must not be recorded by throwing.
    message = "an error that could not be described";
  }
  return {
    route: routeOf(hash),
    message: message.slice(0, MESSAGE_MAX),
    stack: stack.slice(0, STACK_MAX),
    user_agent: String(userAgent || "").slice(0, 300),
  };
}

/** The same problem twice is one problem. Route plus message, deliberately not
 *  the stack: the same bug reached through two draws has two stacks and is
 *  still one thing to go and fix. */
export const dedupeKey = (payload) => `${payload.route}|${payload.message}`;

/** Decides what actually gets sent this session.
 *
 *  `seen` is a Set the caller keeps, so the whole thing is a pure question
 *  about a set and a payload — which is what makes "a render loop cannot flood
 *  the database" a test rather than a hope. */
export function shouldReport(payload, seen) {
  if (!payload.message) return false;
  if (seen.has(dedupeKey(payload))) return false;
  if (seen.size >= SESSION_CAP) return false;
  return true;
}

/** What a reporter is told when a screen fails to draw.
 *
 *  NOT the message, and not a stack. "Cannot read properties of undefined" is
 *  true, useless and frightening; the useful sentence says the two things they
 *  can act on — nothing they typed went anywhere, and here is the way out.
 *  That is the half of this feature they actually experience: #/add's month of
 *  silence was a spinner, and a spinner is indistinguishable from a bad
 *  connection, which is why nobody ever said anything about it.
 */
export function brokenScreenMessage(route) {
  return {
    heading: "This screen did not load",
    body: "Something went wrong drawing it — not your connection, and nothing "
          + "you had already saved is affected. It has been reported "
          + "automatically.",
    route,
  };
}
