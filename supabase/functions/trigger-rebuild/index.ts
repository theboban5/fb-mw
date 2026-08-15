// trigger-rebuild — ask GitHub Actions to rebuild and redeploy the site.
//
// everyleague.co is static HTML on GitHub Pages, so a result saved to Postgres
// is not live until the site is rebuilt. This function is what closes that
// gap, and it exists as a server-side function for exactly one reason: it
// holds a GitHub credential, and a GitHub credential must never be in browser
// JavaScript. The reporter app calls this; only this calls GitHub.
//
// Flow:
//   reporter publishes -> submit_match_report succeeds -> app invokes this
//   -> GoTrue resolves the caller's token to a user
//   -> that user is confirmed to be an active reporter
//   -> claim_rebuild() debounces
//   -> workflow_dispatch on the existing deploy.yml
//
// Secrets (set with `supabase secrets set`, never committed):
//   GH_TOKEN     fine-grained PAT, Actions: read and write, this repo only
//   GH_REPO      owner/repo            (default theboban5/fb-mw)
//   GH_WORKFLOW  workflow file name    (default deploy.yml)
//   GH_REF       branch to build       (default main)
//   REBUILD_COOLDOWN_SECONDS           (default 60)
//
// SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are injected by the platform.

const GITHUB_API = "https://api.github.com";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method !== "POST") {
    return json({ error: "method not allowed" }, 405);
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  // The platform injects the legacy name; a project using only the new API
  // keys can supply SUPABASE_SECRET_KEY as a function secret instead.
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ??
    Deno.env.get("SUPABASE_SECRET_KEY");
  const githubToken = Deno.env.get("GH_TOKEN");
  const repo = Deno.env.get("GH_REPO") ?? "theboban5/fb-mw";
  const workflow = Deno.env.get("GH_WORKFLOW") ?? "deploy.yml";
  const ref = Deno.env.get("GH_REF") ?? "main";
  const cooldown = Number(Deno.env.get("REBUILD_COOLDOWN_SECONDS") ?? "60");

  if (!supabaseUrl || !serviceKey) {
    console.error("missing SUPABASE_URL / service key");
    return json({ error: "server misconfigured" }, 500);
  }
  if (!githubToken) {
    console.error("missing GH_TOKEN");
    return json({ error: "server misconfigured" }, 500);
  }

  // ── 1. The caller must be an active reporter ───────────────────────────────
  // verify_jwt (on by default) is NOT sufficient on its own: it accepts any
  // valid JWT for this project, and the publishable key is itself one. Anyone
  // who has read the reporter app's config.js could therefore get this far.
  // current_reporter_id() runs as the caller and returns null unless there is
  // an active reporters row behind the token — for the publishable key,
  // auth.uid() is null and so is the answer. Deploys cost build minutes; this
  // must not be an open endpoint.
  // This runs in TWO steps rather than one call to current_reporter_id(),
  // because that RPC has to be invoked AS the caller, and calling PostgREST as
  // the caller needs a publishable key in the `apikey` header — which this
  // function cannot get. The platform injects SUPABASE_ANON_KEY, but on a
  // project using the new API keys that slot holds a 64-char digest, not a
  // usable key: PostgREST answers "Invalid API key", the lookup returns null,
  // and every reporter is silently told they are "not an active reporter".
  // That is exactly the bug this replaces — no rebuild was ever dispatched.
  //
  // So: ask GoTrue who the token belongs to (it authenticates with the secret
  // key, which this function does have), then resolve the reporter with the
  // secret key. The trust boundary is unchanged — the caller's own token still
  // decides the identity, and the reporters row still decides the answer.
  const authHeader = req.headers.get("Authorization") ?? "";
  if (!authHeader) return json({ error: "not authenticated" }, 401);

  let reporterId: string | null = null;
  try {
    // GoTrue resolves a *user* token only. The publishable key satisfies
    // verify_jwt but is not a user token, so it stops here with a 401 — which
    // is the check that keeps this from being an open endpoint.
    const who = await fetch(`${supabaseUrl}/auth/v1/user`, {
      headers: { Authorization: authHeader, apikey: serviceKey },
    });
    if (who.ok) {
      const user = await who.json();
      if (user?.id) {
        // Reading `reporters` with the secret key deliberately bypasses RLS:
        // the row is selected by the user id GoTrue just verified, and only
        // an active reporter matches. `select=reporter_id` returns nothing
        // else about them.
        const rows = await fetch(
          `${supabaseUrl}/rest/v1/reporters` +
            `?select=reporter_id&auth_user_id=eq.${encodeURIComponent(user.id)}` +
            `&active=is.true&limit=1`,
          {
            headers: {
              Authorization: `Bearer ${serviceKey}`,
              apikey: serviceKey,
            },
          },
        );
        if (rows.ok) reporterId = (await rows.json())?.[0]?.reporter_id ?? null;
        else console.error("reporter lookup failed", rows.status);
      }
    }
  } catch (err) {
    console.error("identity check failed", err);
    return json({ error: "could not verify identity" }, 503);
  }

  if (!reporterId) {
    return json({ error: "not an active reporter" }, 403);
  }

  // ── 2. Debounce ────────────────────────────────────────────────────────────
  // The decision is a single atomic UPDATE in Postgres (see 0004), so two
  // reports landing together cannot both dispatch.
  let claimed = false;
  try {
    const claim = await fetch(`${supabaseUrl}/rest/v1/rpc/claim_rebuild`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${serviceKey}`,
        apikey: serviceKey,
      },
      body: JSON.stringify({ p_cooldown_seconds: cooldown }),
    });
    if (!claim.ok) {
      console.error("claim_rebuild failed", claim.status, await claim.text());
      return json({ error: "could not schedule a rebuild" }, 503);
    }
    claimed = await claim.json();
  } catch (err) {
    console.error("claim_rebuild threw", err);
    return json({ error: "could not schedule a rebuild" }, 503);
  }

  if (!claimed) {
    // Not a failure. A build is already on its way and will read the database
    // when it starts, so this result is in it.
    return json({ dispatched: false, reason: "coalesced" });
  }

  // ── 3. Dispatch ────────────────────────────────────────────────────────────
  try {
    const response = await fetch(
      `${GITHUB_API}/repos/${repo}/actions/workflows/${workflow}/dispatches`,
      {
        method: "POST",
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${githubToken}`,
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "everyleague-trigger-rebuild",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref }),
      },
    );

    if (response.status !== 204) {
      // Log the detail for the operator; never return it. A GitHub error can
      // name the repo, the token's identity and its scopes.
      console.error("workflow_dispatch failed", response.status,
        await response.text());
      return json({ error: "could not start the rebuild" }, 502);
    }
  } catch (err) {
    console.error("workflow_dispatch threw", err);
    return json({ error: "could not start the rebuild" }, 502);
  }

  console.log(`rebuild dispatched by ${reporterId}`);
  return json({ dispatched: true });
});
