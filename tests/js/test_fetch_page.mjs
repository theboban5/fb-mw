/* The link fetcher, and the addresses it must refuse.
 *
 * This is the one part of the importer where being wrong is a security bug
 * rather than a bad reading. The function runs on infrastructure with a
 * service key in its environment and takes a URL from a user, which is the
 * textbook SSRF shape — so every rule gets a test, including the ones that
 * look obvious, because "obvious" is how a blocklist ends up with a hole in
 * it after a refactor.
 *
 * No network is used. fetchImpl is injected, so redirect chains, oversized
 * bodies and timeouts are all driven directly.
 */

import test from "node:test";
import assert from "node:assert/strict";

import { checkUrl, isLoginWalled, htmlToText, fetchPage }
  from "../../supabase/functions/import-extract/fetch_page.js";

/** A fetch double. `routes` maps URL -> response descriptor. */
function fakeFetch(routes) {
  const seen = [];
  const impl = async (url) => {
    seen.push(url);
    const route = routes[url];
    if (!route) throw Object.assign(new Error("no route"), { name: "TypeError" });
    if (route.throws) throw Object.assign(new Error("boom"), { name: route.throws });
    return {
      status: route.status ?? 200,
      headers: { get: (k) => (route.headers ?? {})[k.toLowerCase()] ?? null },
      text: async () => route.body ?? "",
      body: null,
    };
  };
  impl.seen = seen;
  return impl;
}

const html = (body) => ({ headers: { "content-type": "text/html" }, body });

// ── The address rules ────────────────────────────────────────────────────────

test("only http and https are allowed", () => {
  for (const bad of ["file:///etc/passwd", "gopher://x.com/", "ftp://x.com/",
                     "data:text/html,hi", "javascript:alert(1)"]) {
    assert.equal(checkUrl(bad).ok, false, bad);
    assert.equal(checkUrl(bad).reason, "bad_scheme", bad);
  }
  assert.equal(checkUrl("https://example.com/p").ok, true);
  assert.equal(checkUrl("http://example.com/p").ok, true);
});

test("loopback and link-local are refused", () => {
  // 169.254.169.254 is the cloud metadata endpoint — the single address this
  // whole function exists to not reach.
  for (const bad of ["http://127.0.0.1/", "http://127.1.2.3/",
                     "http://169.254.169.254/latest/meta-data/",
                     "http://0.0.0.0/", "http://[::1]/"]) {
    const out = checkUrl(bad);
    assert.equal(out.ok, false, bad);
    assert.ok(["private_address", "local_name"].includes(out.reason), bad);
  }
});

test("the RFC1918 ranges are refused, and their neighbours are not", () => {
  for (const bad of ["http://10.0.0.1/", "http://10.255.255.255/",
                     "http://172.16.0.1/", "http://172.31.255.254/",
                     "http://192.168.1.1/"]) {
    assert.equal(checkUrl(bad).reason, "private_address", bad);
  }
  // Just outside 172.16.0.0/12 in both directions — the off-by-one a hand
  // written mask gets wrong.
  for (const good of ["http://172.15.0.1/", "http://172.32.0.1/",
                      "http://11.0.0.1/", "http://9.255.255.255/"]) {
    assert.equal(checkUrl(good).ok, true, good);
  }
});

test("other reserved ranges are refused", () => {
  for (const bad of ["http://100.64.0.1/", "http://198.18.0.1/",
                     "http://224.0.0.1/", "http://240.0.0.1/",
                     "http://192.0.2.1/", "http://203.0.113.1/"]) {
    assert.equal(checkUrl(bad).reason, "private_address", bad);
  }
});

test("IPv6 private and mapped-IPv4 forms are refused", () => {
  for (const bad of ["http://[fc00::1]/", "http://[fd12:3456::1]/",
                     "http://[fe80::1]/", "http://[::1]/", "http://[::]/",
                     // WHATWG canonicalizes these to the compressed hex form
                     // before this code sees them, which is exactly how a
                     // regex looking for dots misses every one of them.
                     "http://[::ffff:127.0.0.1]/", "http://[::ffff:7f00:1]/",
                     "http://[::ffff:169.254.169.254]/",
                     "http://[::ffff:a9fe:a9fe]/",
                     "http://[::ffff:10.0.0.1]/", "http://[::ffff:192.168.1.1]/"]) {
    assert.equal(checkUrl(bad).ok, false, bad);
  }
});

test("an IPv6 literal that cannot be parsed is refused, not allowed", () => {
  // An address this code does not understand is precisely the one worth not
  // connecting to.
  for (const bad of ["http://[::ffff:zz]/", "http://[1:2:3]/",
                     "http://[1::2::3]/"]) {
    assert.equal(checkUrl(bad).ok, false, bad);
  }
});

test("a public IPv6 address is allowed", () => {
  // The refusals above must not have been achieved by refusing all of IPv6.
  for (const good of ["http://[2001:4860:4860::8888]/",
                      "http://[2a00:1450:4009:80f::200e]/",
                      "http://[::ffff:8.8.8.8]/"]) {
    assert.equal(checkUrl(good).ok, true, good);
  }
});

test("local names are refused", () => {
  for (const bad of ["http://localhost/", "http://localhost:8000/x",
                     "http://metadata.google.internal/",
                     "http://router/", "http://db.internal/",
                     "http://printer.local/"]) {
    assert.equal(checkUrl(bad).ok, false, bad);
    assert.equal(checkUrl(bad).reason, "local_name", bad);
  }
});

test("credentials in a URL are refused", () => {
  // The one way this function could leak something outward.
  assert.equal(checkUrl("https://user:pass@example.com/").reason,
               "has_credentials");
});

test("nonsense is refused as nonsense", () => {
  for (const bad of ["", "not a url", "://", null, undefined]) {
    assert.equal(checkUrl(bad).ok, false, String(bad));
  }
});

// ── Redirects ────────────────────────────────────────────────────────────────

test("a redirect into a private address is refused at the hop", async () => {
  // THE ONE THAT MATTERS. Checking the address the reporter pasted says
  // nothing about the address it forwards to, and an open redirect on a
  // legitimate host is the ordinary way this gets exploited.
  const impl = fakeFetch({
    "https://example.com/go": {
      status: 302, headers: { location: "http://169.254.169.254/latest/" } },
  });
  const out = await fetchPage("https://example.com/go", { fetchImpl: impl });
  assert.equal(out.ok, false);
  assert.equal(out.reason, "private_address");
  // It stopped BEFORE opening the second connection.
  assert.deepEqual(impl.seen, ["https://example.com/go"]);
});

test("a legitimate redirect is followed", async () => {
  const impl = fakeFetch({
    "https://example.com/a": { status: 301,
                               headers: { location: "https://example.com/b" } },
    "https://example.com/b": html("<p>Bullets 1-0 Wanderers</p>"),
  });
  const out = await fetchPage("https://example.com/a", { fetchImpl: impl });
  assert.equal(out.ok, true);
  assert.equal(out.text, "Bullets 1-0 Wanderers");
});

test("a relative redirect resolves against the current URL", async () => {
  const impl = fakeFetch({
    "https://example.com/a": { status: 302, headers: { location: "/b" } },
    "https://example.com/b": html("<p>ok</p>"),
  });
  assert.equal((await fetchPage("https://example.com/a", { fetchImpl: impl })).ok,
               true);
});

test("a redirect loop terminates", async () => {
  const impl = fakeFetch({
    "https://example.com/a": { status: 302,
                               headers: { location: "https://example.com/a" } },
  });
  const out = await fetchPage("https://example.com/a", { fetchImpl: impl });
  assert.equal(out.reason, "too_many_redirects");
  assert.ok(impl.seen.length <= 4);
});

test("a redirect with no location is refused", async () => {
  const impl = fakeFetch({ "https://example.com/a": { status: 302 } });
  assert.equal((await fetchPage("https://example.com/a",
                                { fetchImpl: impl })).reason, "bad_redirect");
});

// ── Responses ────────────────────────────────────────────────────────────────

test("a non-text response is refused", async () => {
  const impl = fakeFetch({
    "https://example.com/x": { headers: { "content-type": "image/png" },
                               body: "\x89PNG" },
  });
  assert.equal((await fetchPage("https://example.com/x",
                                { fetchImpl: impl })).reason, "not_text");
});

test("an oversized body is refused", async () => {
  const impl = fakeFetch({
    "https://example.com/x": { headers: { "content-type": "text/html",
                                          "content-length": "9999999" },
                               body: "x" },
  });
  assert.equal((await fetchPage("https://example.com/x",
                                { fetchImpl: impl })).reason, "too_large");
});

test("a body that lied about its length is still capped", async () => {
  // Content-Length is a claim. The real cap is on what is actually read.
  const impl = fakeFetch({
    "https://example.com/x": { headers: { "content-type": "text/html" },
                               body: "x".repeat(500_000) },
  });
  assert.equal((await fetchPage("https://example.com/x",
                                { fetchImpl: impl, maxBytes: 1000 })).reason,
               "too_large");
});

test("401 and 403 read as a login wall, not as an error", async () => {
  for (const status of [401, 403]) {
    const impl = fakeFetch({ "https://example.com/x": { status } });
    assert.equal((await fetchPage("https://example.com/x",
                                  { fetchImpl: impl })).reason, "login_required");
  }
});

test("other HTTP errors are their own category", async () => {
  const impl = fakeFetch({ "https://example.com/x": { status: 500 } });
  const out = await fetchPage("https://example.com/x", { fetchImpl: impl });
  assert.equal(out.reason, "http_error");
  assert.equal(out.status, 500);
});

test("a timeout is a timeout and not a mystery", async () => {
  const impl = fakeFetch({ "https://example.com/x": { throws: "AbortError" } });
  assert.equal((await fetchPage("https://example.com/x",
                                { fetchImpl: impl })).reason, "timeout");
});

test("an empty page is refused rather than sent to the model", async () => {
  const impl = fakeFetch({ "https://example.com/x": html("<div></div>") });
  assert.equal((await fetchPage("https://example.com/x",
                                { fetchImpl: impl })).reason, "empty_page");
});

// ── Facebook ─────────────────────────────────────────────────────────────────

test("a login-walled host is known in advance, without a request", async () => {
  // Saying the useful thing immediately beats spending three seconds proving
  // what we already know.
  for (const url of ["https://www.facebook.com/x/posts/1",
                     "https://m.facebook.com/x", "https://fb.watch/abc",
                     "https://www.instagram.com/p/x", "https://x.com/a/status/1"]) {
    assert.equal(isLoginWalled(url), true, url);
  }
  assert.equal(isLoginWalled("https://nrfa.mw/results"), false);

  const impl = fakeFetch({});
  const out = await fetchPage("https://www.facebook.com/x/posts/1",
                              { fetchImpl: impl });
  assert.equal(out.reason, "login_required");
  assert.deepEqual(impl.seen, [], "no request was made at all");
  assert.equal(out.url, "https://www.facebook.com/x/posts/1",
               "the link is kept — it is still where the result came from");
});

// ── HTML to text ─────────────────────────────────────────────────────────────

test("scripts and styles do not reach the model", () => {
  const text = htmlToText(
    "<style>.a{color:red}</style><script>var x=1;</script><p>Bullets 1-0</p>");
  assert.equal(text, "Bullets 1-0");
});

test("entities and whitespace are tidied", () => {
  assert.equal(htmlToText("<p>A&nbsp;&amp;&nbsp;B</p>\n\n  <p>C</p>"), "A & B C");
});
