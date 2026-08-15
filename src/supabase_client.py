"""A tiny PostgREST client: the only module that knows how to talk to Supabase.

Deliberately stdlib-only, in the same spirit as src/dataset.py. The build has
exactly one dependency (Pillow, and only for logo downscaling), and adding the
supabase-py SDK — which pulls httpx, pydantic, gotrue and more — to a static
site generator that needs plain table reads would be a poor trade. Everything
here is request/response over urllib; the browser side of the project uses the
real supabase-js SDK, where session handling actually earns its weight.

Credentials come from the environment, never from code:

  SUPABASE_URL               https://<ref>.supabase.co
  SUPABASE_SECRET_KEY        server-side only — full access, bypasses RLS
  SUPABASE_PUBLISHABLE_KEY   safe in a browser; subject to RLS

`key()` prefers the secret key and falls back to the publishable one, so a
local read-only build works with just the publishable key while CI — which
must also read `reporters`, whose rows are not publicly readable — uses the
secret key.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


class SupabaseError(Exception):
    """A Supabase request failed in a way the caller must not ignore."""


# PostgREST caps a response at its own max-rows setting (1000 by default on
# Supabase). Every read here pages until short, so a table larger than one page
# can never be silently truncated — which would look exactly like deleted rows
# to the validator's drift check.
PAGE_SIZE = 1000

# The build must not fail because one request lost a race with a cold start.
# Same shape as dataset.FETCH_*: a tight timeout with several attempts beats
# one long wait.
TIMEOUT = 20
ATTEMPTS = 4
RETRY_PAUSE = 1.0


def url() -> str:
    value = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if not value:
        raise SupabaseError(
            "SUPABASE_URL is not set (see .env.example; never commit .env)")
    return value


def key(*, require_secret: bool = False) -> str:
    """The API key to send. Secret when present, else publishable."""
    secret = os.environ.get("SUPABASE_SECRET_KEY", "")
    if secret:
        return secret
    if require_secret:
        raise SupabaseError(
            "SUPABASE_SECRET_KEY is required for this operation and is not set")
    publishable = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
    if not publishable:
        raise SupabaseError(
            "set SUPABASE_SECRET_KEY (server-side) or SUPABASE_PUBLISHABLE_KEY")
    return publishable


def _request(method, path, *, query="", body=None, headers=None,
             require_secret=False):
    api_key = key(require_secret=require_secret)
    endpoint = f"{url()}/rest/v1/{path}"
    if query:
        endpoint += f"?{query}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(endpoint, data=data, method=method)
    req.add_header("apikey", api_key)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        req.add_header(name, value)

    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else None
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace").strip()
            # 4xx is our bug — a bad column, a constraint violation, a policy
            # denial. Retrying cannot help and would only bury the message.
            if err.code < 500 or attempt == ATTEMPTS:
                raise SupabaseError(
                    f"{method} {path} -> HTTP {err.code}: {detail}") from None
            time.sleep(RETRY_PAUSE)
        except (urllib.error.URLError, TimeoutError) as err:
            if attempt == ATTEMPTS:
                raise SupabaseError(f"{method} {path}: {err}") from None
            time.sleep(RETRY_PAUSE)


def select(table, *, columns="*", order=None, params=None, require_secret=False):
    """Every row of `table`, paging until the response comes back short.

    `order` matters more here than it looks: the build's output depends on row
    order (see the `ord` note in 0001_core_schema.sql), so callers that rebuild
    a tab must pass it.
    """
    query = {"select": columns}
    if order:
        query["order"] = order
    query.update(params or {})
    rows = []
    while True:
        page = _request(
            "GET", table,
            query=urllib.parse.urlencode(
                {**query, "offset": len(rows), "limit": PAGE_SIZE}),
            require_secret=require_secret,
        ) or []
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows


def upsert(table, rows, *, on_conflict=None, chunk=500):
    """Insert-or-update `rows`, in chunks. Requires the secret key."""
    if not rows:
        return 0
    headers = {"Prefer": "resolution=merge-duplicates,return=minimal"}
    query = f"on_conflict={urllib.parse.quote(on_conflict)}" if on_conflict else ""
    for start in range(0, len(rows), chunk):
        _request("POST", table, query=query, body=rows[start:start + chunk],
                 headers=headers, require_secret=True)
    return len(rows)


def rpc(name, payload=None, *, require_secret=False):
    """Call a Postgres function through PostgREST."""
    return _request("POST", f"rpc/{name}", body=payload or {},
                    require_secret=require_secret)


def load_dotenv(path=".env"):
    """Read KEY=VALUE lines into os.environ without overwriting what is set.

    The repo's .env holds unquoted URLs with `&` in them, so it is not
    shell-sourceable; this parser only has to handle the simple assignment
    form, which is all the file has ever contained.
    """
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _sep, value = line.partition("=")
            name, value = name.strip(), value.strip().strip('"').strip("'")
            if name and name not in os.environ:
                os.environ[name] = value
