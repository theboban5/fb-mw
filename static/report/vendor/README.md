# Vendored supabase-js

`supabase.min.js` is `@supabase/supabase-js` **2.112.3**, bundled to a single
self-contained ES module. It is checked in rather than loaded from a CDN so a
reporter on a weak connection pays one same-origin request that caches
alongside the rest of the app, and so the reporter portal cannot be broken by a
third party being unreachable from Malawi.

Reproduce it exactly:

```sh
mkdir vendor && cd vendor
npm init -y
npm install @supabase/supabase-js@2.112.3
echo "export { createClient } from '@supabase/supabase-js';" > entry.js
npx esbuild@0.25 entry.js --bundle --format=esm --platform=browser --minify \
    --target=es2020 --legal-comments=none --outfile=supabase.min.js
```

211 KB raw, ~57 KB gzipped over the wire, fetched once and then served from
cache behind a content-hashed URL (`app.js` imports it with a `?v=` stamp
written by `build.py`).

That includes Realtime, which `/report` does not use. It was left in rather
than hand-assembling a partial client from `@supabase/auth-js` and
`@supabase/postgrest-js`: the saving is one-time and cached, whereas a
hand-rolled client would mean owning token refresh. Storage is used from
step 9 for match photos.

## Upgrading

Re-run the commands above with the new version, update the version number in
this file, and re-run `RLS_LIVE=1 python3 -m unittest tests.test_rls_live`
followed by a manual sign-in — the auth flow is the part a major version is
most likely to change.
