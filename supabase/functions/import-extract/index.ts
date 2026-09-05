// import-extract — read football results out of a screenshot, some text, or a
// link, and write what was read onto the import row.
//
// It exists as a server-side function for one reason, the same reason
// trigger-rebuild does: it holds a credential, and a credential must never be
// in browser JavaScript. The reporter app calls this; only this calls
// Anthropic.
//
// WHAT IT DOES NOT DO, DELIBERATELY:
//
//   * It does not match. Turning names into fixtures needs auth.uid() — the
//     caller's identity decides which competitions may even be proposed — and
//     this function cannot call PostgREST as the caller: the platform injects
//     SUPABASE_ANON_KEY, and on a project using the new API keys that slot
//     holds a digest rather than a usable key. (trigger-rebuild's comment
//     records the day that cost.) So the client calls resolve_and_save_import
//     with its own session afterwards. The split is not a workaround; it is
//     the right seam. This function does the part that needs the ANTHROPIC
//     secret, the client does the part that needs the REPORTER's identity.
//
//   * It does not publish. Nothing here writes to matches, goals, teams or
//     players, and it holds no grant that would let it.
//
// Flow:
//   client opens an import (create_report_import) and uploads any screenshot
//   -> calls this with the import_id
//   -> GoTrue resolves the caller's token to a user
//   -> that user is confirmed to be an active reporter, and to own the import
//   -> the source is assembled: image from the PRIVATE bucket, pasted text,
//      and the page behind the link if it can be safely read
//   -> the model reads it
//   -> extracted/model/usage/status are written back with the secret key
//   -> the extraction is returned to the client, which resolves it
//
// Secrets (set with `supabase secrets set`, never committed):
//   ANTHROPIC_API_KEY                   required; absent = feature off
//   EVERYLEAGUE_IMPORT_MODEL            default claude-sonnet-5
//   EVERYLEAGUE_IMPORT_FALLBACK_MODEL   default claude-opus-5; "" disables
//
// SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are injected by the platform.

import { anthropicProvider, runExtraction } from "./provider.js";
import { fetchPage } from "./fetch_page.js";

// Same rules, and the same reasoning, as trigger-rebuild's: the only caller
// that matters is a browser on everyleague.co, and a cross-origin POST
// carrying Authorization and apikey is never sent without a successful
// preflight first. VERIFY THIS FROM A BROWSER — curl sends no preflight, so a
// broken one always works from a terminal and never from a phone.
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

const MEDIA_BY_EXT: Record<string, string> = {
  jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp",
};

/** Base64 without blowing the stack. btoa(String.fromCharCode(...bytes))
 *  spreads the whole array as arguments and dies somewhere north of 100 kB —
 *  which is every screenshot. */
function toBase64(bytes: Uint8Array): string {
  let binary = "";
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    binary += String.fromCharCode(...bytes.subarray(i, i + step));
  }
  return btoa(binary);
}

Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: CORS_HEADERS });
  }
  if (req.method !== "POST") return json({ error: "method not allowed" }, 405);

  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ??
    Deno.env.get("SUPABASE_SECRET_KEY");
  const apiKey = Deno.env.get("ANTHROPIC_API_KEY");
  const model = Deno.env.get("EVERYLEAGUE_IMPORT_MODEL") ?? "claude-sonnet-5";
  const fallbackModel =
    Deno.env.get("EVERYLEAGUE_IMPORT_FALLBACK_MODEL") ?? "claude-opus-5";

  if (!supabaseUrl || !serviceKey) {
    console.error("missing SUPABASE_URL / service key");
    return json({ error: "server misconfigured" }, 500);
  }
  // THE KILL SWITCH. No key, no importer — and a clear answer rather than a
  // failure, so the client can hide the entry point and the manual grid
  // carries on untouched. Unsetting the secret disables the feature.
  if (!apiKey) {
    return json({ error: "import_disabled",
                  message: "Reading from screenshots is not switched on." }, 503);
  }

  // ── 1. The caller must be an active reporter ───────────────────────────────
  // verify_jwt alone is not enough: it accepts any valid JWT for this project,
  // and the publishable key is one. Two steps rather than one call to
  // current_reporter_id(), for the reason in the header — this function has no
  // usable publishable key to call PostgREST as the caller with.
  const authHeader = req.headers.get("Authorization") ?? "";
  if (!authHeader) return json({ error: "not authenticated" }, 401);

  let reporterId: string | null = null;
  try {
    const who = await fetch(`${supabaseUrl}/auth/v1/user`, {
      headers: { Authorization: authHeader, apikey: serviceKey },
    });
    if (who.ok) {
      const user = await who.json();
      if (user?.id) {
        const rows = await fetch(
          `${supabaseUrl}/rest/v1/reporters` +
            `?select=reporter_id&auth_user_id=eq.${encodeURIComponent(user.id)}` +
            `&active=is.true&limit=1`,
          { headers: { Authorization: `Bearer ${serviceKey}`, apikey: serviceKey } },
        );
        if (rows.ok) reporterId = (await rows.json())?.[0]?.reporter_id ?? null;
        else console.error("reporter lookup failed", rows.status);
      }
    }
  } catch (err) {
    console.error("identity check failed", err);
    return json({ error: "could not verify identity" }, 503);
  }
  if (!reporterId) return json({ error: "not an active reporter" }, 403);

  // ── 2. The import must exist and belong to them ────────────────────────────
  let importId = "";
  try {
    const body = await req.json();
    importId = String(body?.import_id ?? "");
  } catch {
    return json({ error: "send an import_id" }, 400);
  }
  if (!/^[0-9a-fA-F-]{36}$/.test(importId)) {
    return json({ error: "send an import_id" }, 400);
  }

  const rest = (path: string, init: RequestInit = {}) =>
    fetch(`${supabaseUrl}/rest/v1/${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${serviceKey}`, apikey: serviceKey,
        "content-type": "application/json", ...(init.headers ?? {}),
      },
    });

  let row: Record<string, unknown> | null = null;
  try {
    const res = await rest(
      `report_imports?select=*&import_id=eq.${encodeURIComponent(importId)}&limit=1`);
    if (res.ok) row = (await res.json())?.[0] ?? null;
  } catch (err) {
    console.error("import lookup failed", err);
  }
  if (!row) return json({ error: "that import no longer exists" }, 404);
  // Read with the secret key, so RLS did not do this for us. The row's owner
  // is checked explicitly instead.
  if (row.reporter_id !== reporterId) {
    return json({ error: "that import belongs to another reporter" }, 403);
  }

  // ── 3. Already done? Hand it back rather than paying again ─────────────────
  // Reopening a processed import — a reload, a back button, a second reviewer
  // — must not be a second charge. This is why extraction is stored at all.
  if (row.extracted) {
    return json({ import_id: importId, extracted: row.extracted,
                  model: row.model, cached: true });
  }

  const finish = async (patch: Record<string, unknown>) => {
    try {
      await rest(`report_imports?import_id=eq.${encodeURIComponent(importId)}`,
                 { method: "PATCH", headers: { Prefer: "return=minimal" },
                   body: JSON.stringify(patch) });
    } catch (err) {
      console.error("could not write back the extraction", err);
    }
  };

  // ── 4. Assemble what there is to read ──────────────────────────────────────
  let imageBase64 = "";
  let mediaType = "image/jpeg";
  const storagePath = String(row.storage_path ?? "");
  if (storagePath) {
    try {
      // The bucket is private, so this is a service-key read of an object
      // whose path the row already named. It is not a signed URL and is never
      // handed to the browser — the browser reads its own uploads through the
      // storage policy in 0042.
      const object = await fetch(
        `${supabaseUrl}/storage/v1/object/report-imports/${storagePath}`,
        { headers: { Authorization: `Bearer ${serviceKey}`, apikey: serviceKey } });
      if (object.ok) {
        const bytes = new Uint8Array(await object.arrayBuffer());
        imageBase64 = toBase64(bytes);
        mediaType = MEDIA_BY_EXT[storagePath.split(".").pop()?.toLowerCase() ?? ""]
          ?? "image/jpeg";
      } else {
        console.error("image fetch failed", object.status, storagePath);
      }
    } catch (err) {
      console.error("image fetch threw", err);
    }
  }

  let text = String(row.pasted_text ?? "");
  const sourceUrl = String(row.source_url ?? "");
  let linkNote = "";
  if (sourceUrl && !imageBase64 && !text) {
    const page = await fetchPage(sourceUrl);
    if (page.ok) {
      text = page.text;
    } else {
      // AN UNREADABLE LINK IS AN EXPECTED STATE, NOT A FAILURE. Facebook serves
      // a login wall to anything without a session, and that is where most of
      // these links come from. The link is kept as the source either way; the
      // reporter is asked for a screenshot, and can add one to this same
      // import rather than starting again.
      linkNote = page.reason;
      console.log("link not readable", page.reason, page.url);
    }
  }

  if (!imageBase64 && !text.trim()) {
    await finish({ status: "failed",
                   error_category: linkNote ? `link_${linkNote}` : "nothing_to_read" });
    return json({
      import_id: importId,
      error: linkNote === "login_required" ? "link_login_required" : "nothing_to_read",
      link_reason: linkNote,
      message: linkNote
        ? "I saved the link but couldn't read the post. Please add a screenshot "
          + "or paste the text."
        : "There was nothing to read in that submission.",
    }, 200);
  }

  // ── 5. Read it ─────────────────────────────────────────────────────────────
  let result;
  try {
    result = await runExtraction({
      provider: anthropicProvider({ apiKey }),
      model, fallbackModel,
      input: { imageBase64, mediaType, text, sourceUrl },
    });
  } catch (err) {
    console.error("extraction threw", err);
    await finish({ status: "failed", error_category: "model_error" });
    return json({ import_id: importId, error: "extraction_failed" }, 200);
  }

  if (!result.ok) {
    await finish({ status: "failed", error_category: result.errorCategory,
                   usage: result.usage ?? null, model });
    return json({ import_id: importId, error: result.errorCategory,
                  message: "I couldn't read that one. You can still enter the "
                           + "results by hand." }, 200);
  }

  await finish({
    status: "extracted",
    extracted: {
      document_kind: result.documentKind,
      competition_hint: result.competitionHint ?? null,
      date: result.date ?? null,
      matchday: result.matchday ?? null,
      notes: result.notes ?? null,
      results: result.items,
      link_reason: linkNote || null,
    },
    model: result.model ?? model,
    usage: result.usage ?? null,
  });

  return json({
    import_id: importId,
    document_kind: result.documentKind,
    extracted: {
      document_kind: result.documentKind,
      competition_hint: result.competitionHint ?? null,
      date: result.date ?? null,
      matchday: result.matchday ?? null,
      notes: result.notes ?? null,
      results: result.items,
      link_reason: linkNote || null,
    },
    model: result.model ?? model,
    escalated: Boolean(result.escalated),
    link_reason: linkNote || null,
    cached: false,
  });
});
