// manage-reporters — create a reporter's login, or reset one, from /report.
//
// This exists as a server-side function for the same single reason
// trigger-rebuild does: it holds a credential a browser must never see. There
// the credential was a GitHub PAT; here it is the Supabase secret key, which
// is the only thing that may call the GoTrue admin API — and creating a login
// for somebody who has never had one is an admin-API operation and nothing
// else. supabase.auth.signUp() would be the browser-side equivalent, and it is
// wrong twice over: public signup is deliberately disabled on this project,
// and it would sign the ADMIN out of their own session and into the new
// account halfway through creating it.
//
// Everything that does NOT need the key stayed in Postgres, in 0026:
// assigning a competition, changing a role, deactivating an account. Those are
// ordinary RPCs the portal calls directly, gated on is_admin(). This function
// is only the half that cannot be done any other way.
//
// Flow:
//   admin submits the form -> this function
//   -> GoTrue resolves the caller's token to a user
//   -> that user is confirmed to be an ACTIVE ADMIN (not merely a reporter)
//   -> POST /auth/v1/admin/users            (email_confirm: true)
//   -> admin_create_reporter()              (row + assignments, one transaction)
//   -> on failure, the auth user is deleted again
//
// SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are injected by the platform.
// There is nothing to configure with `supabase secrets set`.

// ── CORS ─────────────────────────────────────────────────────────────────────
// Read the long note in trigger-rebuild/index.ts before touching this. The
// short version: the only caller that matters is a browser, a cross-origin
// POST carrying Authorization is preflighted with OPTIONS first, and a
// function that answers the preflight with 405 is unreachable from every phone
// while working perfectly under curl. That bug shipped twice. VERIFY FROM A
// BROWSER.
//
// Allow-Origin is `*` for the reason given there too: CORS is not the security
// boundary and cannot be, since anything can call this with curl. The boundary
// is the admin check below, on a bearer token another origin cannot read.
const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers":
    "authorization, apikey, content-type, x-client-info, x-supabase-api-version",
  "Access-Control-Max-Age": "86400",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}

// The same unambiguous alphabet scripts/reporters.py uses, and for the same
// reason: these passwords get read aloud over WhatsApp and typed on a phone
// keyboard, where O/0 and l/1/I are a support call.
const ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";

function generatePassword(length = 14): string {
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  // Modulo bias over a 56-character alphabet is negligible at 14 characters
  // and this is a temporary password the reporter is told to change.
  return Array.from(bytes, (b) => ALPHABET[b % ALPHABET.length]).join("");
}

interface Actor {
  reporterId: string;
  role: string;
  active: boolean;
}

/** Who is asking, resolved from their own token. Null when the token is not a
 *  user token at all — which is what the publishable key is. */
async function resolveActor(
  supabaseUrl: string,
  serviceKey: string,
  authHeader: string,
): Promise<Actor | null> {
  // GoTrue resolves a *user* token only. verify_jwt is not sufficient on its
  // own: it accepts any valid JWT for this project, and the publishable key in
  // the reporter app's config.js is itself one. This is the check that keeps
  // the endpoint from being open to anyone who has read that file.
  const who = await fetch(`${supabaseUrl}/auth/v1/user`, {
    headers: { Authorization: authHeader, apikey: serviceKey },
  });
  if (!who.ok) return null;
  const user = await who.json();
  if (!user?.id) return null;

  // Read with the secret key, deliberately bypassing RLS: the row is selected
  // by the user id GoTrue has just verified, and nothing else is returned.
  const rows = await fetch(
    `${supabaseUrl}/rest/v1/reporters` +
      `?select=reporter_id,role,active&auth_user_id=eq.${encodeURIComponent(user.id)}` +
      `&limit=1`,
    { headers: { Authorization: `Bearer ${serviceKey}`, apikey: serviceKey } },
  );
  if (!rows.ok) {
    console.error("reporter lookup failed", rows.status);
    return null;
  }
  const row = (await rows.json())?.[0];
  if (!row) return null;
  return {
    reporterId: row.reporter_id,
    role: row.role,
    active: Boolean(row.active),
  };
}

async function authAdmin(
  supabaseUrl: string,
  serviceKey: string,
  method: string,
  path: string,
  body?: unknown,
): Promise<Response> {
  return await fetch(`${supabaseUrl}/auth/v1/${path}`, {
    method,
    headers: {
      apikey: serviceKey,
      Authorization: `Bearer ${serviceKey}`,
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: CORS_HEADERS });
  }
  if (req.method !== "POST") {
    return json({ error: "method not allowed" }, 405);
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ??
    Deno.env.get("SUPABASE_SECRET_KEY");
  if (!supabaseUrl || !serviceKey) {
    console.error("missing SUPABASE_URL / service key");
    return json({ error: "server misconfigured" }, 500);
  }

  const authHeader = req.headers.get("Authorization") ?? "";
  if (!authHeader) return json({ error: "not authenticated" }, 401);

  let actor: Actor | null;
  try {
    actor = await resolveActor(supabaseUrl, serviceKey, authHeader);
  } catch (err) {
    console.error("identity check failed", err);
    return json({ error: "could not verify identity" }, 503);
  }

  // Every branch below either mints a credential or replaces one. An ordinary
  // reporter has no business here at all, so the bar is admin rather than the
  // "active reporter" trigger-rebuild settles for.
  if (!actor || !actor.active || actor.role !== "admin") {
    return json({ error: "only an administrator can manage reporters" }, 403);
  }

  let payload: Record<string, unknown>;
  try {
    payload = await req.json();
  } catch {
    return json({ error: "expected a JSON body" }, 400);
  }
  const action = String(payload.action ?? "");

  // ── Reset a password ───────────────────────────────────────────────────────
  if (action === "reset_password") {
    const reporterId = String(payload.reporter_id ?? "");
    if (!reporterId) return json({ error: "which reporter?" }, 400);

    const rows = await fetch(
      `${supabaseUrl}/rest/v1/reporters` +
        `?select=auth_user_id&reporter_id=eq.${encodeURIComponent(reporterId)}&limit=1`,
      { headers: { Authorization: `Bearer ${serviceKey}`, apikey: serviceKey } },
    );
    if (!rows.ok) {
      console.error("reporter lookup failed", rows.status);
      return json({ error: "could not read that reporter" }, 503);
    }
    const authUserId = (await rows.json())?.[0]?.auth_user_id;
    if (!authUserId) {
      return json({ error: "that reporter has no login to reset" }, 400);
    }

    const password = String(payload.password ?? "") || generatePassword();
    const reset = await authAdmin(
      supabaseUrl, serviceKey, "PUT", `admin/users/${authUserId}`, { password },
    );
    if (!reset.ok) {
      // Logged for the operator, never returned: a GoTrue error can quote the
      // password policy and the user's address back at whoever asked.
      console.error("password reset failed", reset.status, await reset.text());
      return json({ error: "could not reset that password" }, 502);
    }
    console.log(`password reset for ${reporterId} by ${actor.reporterId}`);
    return json({ reporter_id: reporterId, password });
  }

  // ── Create a reporter ──────────────────────────────────────────────────────
  if (action !== "create") {
    return json({ error: "unknown action" }, 400);
  }

  const name = String(payload.name ?? "").trim();
  const email = String(payload.email ?? "").trim().toLowerCase();
  const role = String(payload.role ?? "reporter");
  const competitions = Array.isArray(payload.competitions)
    ? payload.competitions.map(String)
    : [];
  const password = String(payload.password ?? "") || generatePassword();

  if (!name) return json({ error: "a reporter needs a name" }, 400);
  if (!email) return json({ error: "a reporter needs an email address" }, 400);
  if (role !== "reporter" && role !== "admin") {
    return json({ error: "role must be reporter or admin" }, 400);
  }

  // The login first: if this fails, no orphan reporters row is left behind.
  // email_confirm skips the round trip no reporter in the field can complete —
  // there is no SMTP on this project, and the password is handed over directly.
  const created = await authAdmin(
    supabaseUrl, serviceKey, "POST", "admin/users",
    { email, password, email_confirm: true },
  );
  if (!created.ok) {
    const detail = await created.text();
    console.error("auth user create failed", created.status, detail);
    // The one GoTrue failure worth naming, because it is the one an admin can
    // do something about and the one they will actually hit.
    if (created.status === 422 || detail.includes("already been registered")) {
      return json({ error: "that email address already has a login" }, 409);
    }
    return json({ error: "could not create that login" }, 502);
  }
  const authUserId = (await created.json())?.id;
  if (!authUserId) {
    console.error("auth user create returned no id");
    return json({ error: "could not create that login" }, 502);
  }

  // The row and its assignments, in one transaction (0026). p_actor is checked
  // again in there — the secret key has no auth.uid(), so is_admin() is false
  // inside that function and naming the administrator is the only way it can
  // know a person authorised this.
  const rpc = await fetch(`${supabaseUrl}/rest/v1/rpc/admin_create_reporter`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${serviceKey}`,
      apikey: serviceKey,
    },
    body: JSON.stringify({
      p_actor: actor.reporterId,
      p_name: name,
      p_email: email,
      p_auth_user_id: authUserId,
      p_role: role,
      p_competitions: competitions,
    }),
  });

  if (!rpc.ok) {
    const detail = await rpc.text();
    console.error("admin_create_reporter failed", rpc.status, detail);
    // Roll the login back, so a retry is not blocked by a half-made account
    // whose email is now taken. Exactly what scripts/reporters.py does.
    const undo = await authAdmin(
      supabaseUrl, serviceKey, "DELETE", `admin/users/${authUserId}`,
    );
    if (!undo.ok) {
      // Worth shouting about: the login now exists with no reporter behind it,
      // which resolves to a NULL current_reporter_id() and can do nothing —
      // harmless, but it will block this email being used again.
      console.error("ORPHAN LOGIN", authUserId, email, undo.status);
    }
    // These messages are written for a person (see 0026); passing them through
    // is the point.
    let message = "Could not create that reporter.";
    try {
      const parsed = JSON.parse(detail);
      if (parsed?.message) message = String(parsed.message);
    } catch { /* keep the generic sentence */ }
    return json({ error: message }, 400);
  }

  const reporterId = await rpc.json();
  console.log(`reporter ${reporterId} created by ${actor.reporterId}`);
  return json({ reporter_id: reporterId, email, password });
});
