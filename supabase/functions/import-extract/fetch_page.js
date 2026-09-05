/* Reading a link a reporter pasted, without becoming a way into the network.
 *
 * THE THREAT. This function runs on infrastructure with a service key in its
 * environment, and it takes a URL from a user and fetches it. That is the
 * classic SSRF shape: a link to http://169.254.169.254/ or to something on
 * localhost is a request made from inside, by something trusted, with the
 * answer handed back to whoever asked. So the address is checked before any
 * connection is opened, every redirect is re-checked as a fresh address, and
 * the response is capped in both size and time.
 *
 * WHAT THIS CANNOT DO, AND WHY THAT IS STATED RATHER THAN HIDDEN. Checking a
 * hostname is not the same as checking where it resolves: a name that answers
 * with 127.0.0.1 (a DNS rebind) passes every test below, because the platform
 * gives no hook between "resolved" and "connected". The literal-address and
 * private-range checks catch the ordinary cases and the deliberate-but-lazy
 * ones; they do not make this a safe fetcher for an untrusted internet. What
 * makes the residual risk acceptable is what it can reach if it succeeds:
 * nothing here reads a secret, and the body is fed to a model as text, capped,
 * and shown to the reporter who asked for it. If that ever stops being true —
 * if a fetched body is ever used to make a decision — this needs a resolver
 * that pins the address.
 *
 * FACEBOOK IS AN EXPECTED FAILURE, NOT AN ERROR. Most links reporters have are
 * Facebook posts, and Facebook serves a login wall to anything without a
 * session. The caller treats that as a normal outcome with its own sentence
 * ("I saved the link but couldn't read the post"), because telling a reporter
 * that something went wrong, when nothing did, trains them to ignore the times
 * something has.
 */

// Reserved and private ranges, as literals. Anything that parses as an IP is
// checked against these; a hostname is checked for the obvious local names.
const PRIVATE_V4 = [
  [10, 0, 0, 0, 8],          // RFC1918
  [172, 16, 0, 0, 12],       // RFC1918
  [192, 168, 0, 0, 16],      // RFC1918
  [127, 0, 0, 0, 8],         // loopback
  [169, 254, 0, 0, 16],      // link-local — the cloud metadata endpoint
  [0, 0, 0, 0, 8],           // "this network"
  [100, 64, 0, 0, 10],       // carrier-grade NAT
  [192, 0, 0, 0, 24],        // IETF protocol assignments
  [192, 0, 2, 0, 24],        // TEST-NET-1
  [198, 18, 0, 0, 15],       // benchmarking
  [198, 51, 100, 0, 24],     // TEST-NET-2
  [203, 0, 113, 0, 24],      // TEST-NET-3
  [224, 0, 0, 0, 4],         // multicast
  [240, 0, 0, 0, 4],         // reserved
];

const LOCAL_NAMES = new Set([
  "localhost", "localhost.localdomain", "metadata", "metadata.google.internal",
]);

function v4ToInt(host) {
  const parts = host.split(".");
  if (parts.length !== 4) return null;
  let n = 0;
  for (const part of parts) {
    if (!/^\d{1,3}$/.test(part)) return null;
    const byte = Number(part);
    if (byte > 255) return null;
    n = (n * 256) + byte;
  }
  return n;
}

function isPrivateInt(value) {
  return PRIVATE_V4.some(([a, b, c, d, bits]) => {
    const base = ((a * 256 + b) * 256 + c) * 256 + d;
    const mask = bits === 0 ? 0 : (-1 << (32 - bits)) >>> 0;
    return (value & mask) >>> 0 === (base & mask) >>> 0;
  });
}

function isPrivateV4(host) {
  const value = v4ToInt(host);
  return value === null ? false : isPrivateInt(value);
}

/** An IPv6 literal to its eight groups, or null if it is not one.
 *
 *  WHY THIS IS PARSED RATHER THAN PATTERN-MATCHED. The first version tested
 *  the hostname for the string "::ffff:" followed by a dotted quad, which
 *  never fires: `new URL()` CANONICALIZES the host, so
 *  http://[::ffff:127.0.0.1]/ arrives here as `::ffff:7f00:1` and sails past a
 *  regex looking for dots. Every IPv4-mapped address written the compressed
 *  way — which is the way the platform hands them over — would have been
 *  allowed straight through to whatever it pointed at.
 *
 *  There is no way to spot that class of bug by looking at the strings you
 *  thought of. The address has to be turned into numbers and the numbers
 *  checked. */
function expandV6(input) {
  let h = input;
  // A dotted tail (::ffff:127.0.0.1) becomes two hex groups first, so the
  // rest of this only ever deals with one notation.
  const dotted = /^(.*:)(\d{1,3}(?:\.\d{1,3}){3})$/.exec(h);
  if (dotted) {
    const v4 = v4ToInt(dotted[2]);
    if (v4 === null) return null;
    h = `${dotted[1]}${((v4 >>> 16) & 0xffff).toString(16)}:`
      + `${(v4 & 0xffff).toString(16)}`;
  }
  const halves = h.split("::");
  if (halves.length > 2) return null;
  const head = halves[0] ? halves[0].split(":") : [];
  const tail = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
  const fill = 8 - head.length - tail.length;
  if (fill < 0) return null;
  if (halves.length === 1 && head.length !== 8) return null;
  const groups = halves.length === 2
    ? [...head, ...Array(fill).fill("0"), ...tail]
    : head;
  if (groups.length !== 8) return null;
  const out = [];
  for (const g of groups) {
    if (!/^[0-9a-f]{1,4}$/.test(g)) return null;
    out.push(parseInt(g, 16));
  }
  return out;
}

function isPrivateV6(host) {
  // Only a literal is an address. A domain name reaches here too and is not
  // this function's business.
  if (!host.startsWith("[") && !host.includes(":")) return false;
  const h = host.replace(/^\[|\]$/g, "").toLowerCase();
  const g = expandV6(h);
  // An IPv6 literal we cannot parse is REFUSED, not allowed. An address this
  // code does not understand is exactly the one worth not connecting to.
  if (!g) return true;

  if (g.every((x) => x === 0)) return true;                       // ::
  if (g.slice(0, 7).every((x) => x === 0) && g[7] === 1) return true;  // ::1
  if ((g[0] & 0xfe00) === 0xfc00) return true;                    // fc00::/7
  if ((g[0] & 0xffc0) === 0xfe80) return true;                    // fe80::/10
  // IPv4-mapped (::ffff:0:0/96) and the deprecated IPv4-compatible form: the
  // low 32 bits are a v4 address, so they get the v4 rules.
  const leadingZero = g.slice(0, 5).every((x) => x === 0);
  if (leadingZero && (g[5] === 0xffff || g[5] === 0)) {
    const v4 = (((g[6] << 16) >>> 0) + g[7]) >>> 0;
    if (v4 !== 0 && isPrivateInt(v4)) return true;
  }
  return false;
}

/** Is this an address we are willing to open a connection to?
 *
 *  Returns a REASON rather than a bare false, so the caller can log why
 *  without having to re-derive it — and so the tests can assert on which rule
 *  fired rather than merely that something did. */
export function checkUrl(input) {
  let url;
  try {
    url = new URL(String(input));
  } catch {
    return { ok: false, reason: "not_a_url" };
  }
  // http(s) only. Everything else — file:, gopher:, data:, and the ones that
  // do not exist yet — is refused by allow-list rather than by blocklist.
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return { ok: false, reason: "bad_scheme" };
  }
  // Credentials in a URL are never something a reporter meant to paste, and
  // forwarding them would be the one way this function could leak something.
  if (url.username || url.password) {
    return { ok: false, reason: "has_credentials" };
  }
  const host = url.hostname.toLowerCase();
  if (!host) return { ok: false, reason: "no_host" };
  if (LOCAL_NAMES.has(host)) return { ok: false, reason: "local_name" };
  // A name with no dot is a bare host on the local network ("router",
  // "supabase-db"), which is never a public results page.
  if (!host.includes(".") && !host.includes(":")) {
    return { ok: false, reason: "local_name" };
  }
  if (host.endsWith(".local") || host.endsWith(".internal")) {
    return { ok: false, reason: "local_name" };
  }
  if (isPrivateV4(host) || isPrivateV6(host)) {
    return { ok: false, reason: "private_address" };
  }
  return { ok: true, url: url.toString() };
}

/** Hosts that will not serve a readable page to a server, however well we ask.
 *  Recognised so the caller can say the useful thing immediately instead of
 *  spending three seconds proving it. */
const LOGIN_WALLED = [
  "facebook.com", "www.facebook.com", "m.facebook.com", "fb.com", "fb.watch",
  "instagram.com", "www.instagram.com", "x.com", "twitter.com",
];

export const isLoginWalled = (input) => {
  try {
    const host = new URL(String(input)).hostname.toLowerCase();
    return LOGIN_WALLED.some((h) => host === h || host.endsWith(`.${h}`));
  } catch {
    return false;
  }
};

const TEXTUAL = /^(text\/|application\/(json|xhtml\+xml|xml))/i;

/** Very small HTML-to-text. Not a parser and not trying to be: scripts and
 *  styles out, tags out, entities for the five that matter, whitespace
 *  collapsed. The result is fed to a model, which does not need markup. */
export function htmlToText(html) {
  return String(html)
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/\s+/g, " ")
    .trim();
}

/** Fetch a page, defensively.
 *
 *  `fetchImpl` is injected so the tests can drive every branch — redirects to
 *  private addresses, oversized bodies, wrong content types — without a
 *  network and without waiting for timeouts.
 *
 *  Redirects are followed MANUALLY, one at a time, with checkUrl re-run on
 *  every hop. `redirect: "follow"` would let the first response send us
 *  anywhere: the check on the address the reporter pasted says nothing about
 *  the address it forwards to, and an open redirect on a legitimate host is
 *  the ordinary way that gets exploited. */
export async function fetchPage(input, {
  fetchImpl = fetch,
  maxBytes = 400_000,
  timeoutMs = 8000,
  maxRedirects = 3,
  maxChars = 20_000,
} = {}) {
  if (isLoginWalled(input)) {
    return { ok: false, reason: "login_required", url: String(input) };
  }

  let current = input;
  for (let hop = 0; hop <= maxRedirects; hop += 1) {
    const check = checkUrl(current);
    if (!check.ok) return { ok: false, reason: check.reason, url: String(current) };

    let response;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      response = await fetchImpl(check.url, {
        redirect: "manual",
        signal: controller.signal,
        headers: {
          // Honest about what we are. A site that would rather not be read by
          // a robot can say so, and we would rather be told than disguised.
          "User-Agent": "everyleague-import/1.0 (+https://everyleague.co)",
          "Accept": "text/html,application/xhtml+xml,text/plain",
        },
      });
    } catch (err) {
      return { ok: false,
               reason: err?.name === "AbortError" ? "timeout" : "unreachable",
               url: check.url };
    } finally {
      clearTimeout(timer);
    }

    const status = response.status;
    if (status >= 300 && status < 400) {
      const location = response.headers?.get?.("location");
      if (!location) return { ok: false, reason: "bad_redirect", url: check.url };
      // Resolved against the current URL so a relative Location works, and fed
      // back into checkUrl at the top of the loop.
      current = new URL(location, check.url).toString();
      continue;
    }
    if (status === 401 || status === 403) {
      return { ok: false, reason: "login_required", url: check.url };
    }
    if (status >= 400) {
      return { ok: false, reason: "http_error", status, url: check.url };
    }

    const type = response.headers?.get?.("content-type") || "";
    if (!TEXTUAL.test(type)) {
      return { ok: false, reason: "not_text", url: check.url };
    }

    // Content-Length is a claim, so it is used as an early out and not as the
    // limit. The real cap is on what is actually read.
    const declared = Number(response.headers?.get?.("content-length") || 0);
    if (declared && declared > maxBytes) {
      return { ok: false, reason: "too_large", url: check.url };
    }

    const body = await readCapped(response, maxBytes);
    if (body === null) return { ok: false, reason: "too_large", url: check.url };

    const text = htmlToText(body).slice(0, maxChars);
    if (!text) return { ok: false, reason: "empty_page", url: check.url };
    return { ok: true, url: check.url, text };
  }
  return { ok: false, reason: "too_many_redirects", url: String(current) };
}

/** Read at most maxBytes, stopping the stream rather than buffering a
 *  response that lied about its length. Falls back to .text() where the body
 *  is not a stream (which is what a test double usually hands back). */
async function readCapped(response, maxBytes) {
  const body = response.body;
  if (!body || typeof body.getReader !== "function") {
    const text = await response.text();
    return text.length > maxBytes ? null : text;
  }
  const reader = body.getReader();
  const chunks = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.length;
      if (total > maxBytes) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock?.();
  }
  const joined = new Uint8Array(total);
  let at = 0;
  for (const chunk of chunks) { joined.set(chunk, at); at += chunk.length; }
  return new TextDecoder("utf-8", { fatal: false }).decode(joined);
}
