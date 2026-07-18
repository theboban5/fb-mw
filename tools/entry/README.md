# Data-entry tooling

A fast entry UI for fixtures, results, and scorers, replacing hand-typed rows
in the Google Sheet. Two halves:

- **`static/admin/`** — a static page (published as `everyleague.co/admin/` by
  every build; `copy_static_tree` copies it into `docs/` automatically). It
  reads the same published CSVs as `build.py` and gives league-filtered
  autocomplete for teams and scorers.
- **`tools/entry/apps-script/WebApp.gs`** — a web-app file added to the
  sheet's existing bound Apps Script project (the one with the Fast Entry
  sidebar). The page POSTs to it; it is the only write path. It mints all IDs
  server-side (under a lock, from live sheet data) and appends/updates rows
  with plain-text cell formatting so dates and kickoff times survive the
  published-CSV round trip byte-for-byte. Everything in it except
  `doGet`/`doPost` is `entry…_`-prefixed because all `.gs` files in a project
  share one global namespace — it must never redeclare a name the sidebar's
  `Code.gs` uses (which already has its own `readTab`, `SS`, `onOpen`).

The sheet stays the single source of truth and `validate.py` remains the build
gate — the UI and script only do cheap pre-checks (same-team match, goal rows
vs score, date in season, enum membership). Entered data appears on the site
at the next build (daily cron, or push/workflow_dispatch).

## Deploying the Apps Script

1. Open the spreadsheet → Extensions → Apps Script. This opens the existing
   bound project with the Fast Entry sidebar — keep its files untouched.
2. Files → ＋ → Script, name it `WebApp`, paste `WebApp.gs` into it. Before
   saving, confirm the sidebar's `Code.gs` has no `doGet` or `doPost` of its
   own (those two are the web app's entry points and must exist exactly once
   in the project).
3. Project Settings → Script Properties → add `ENTRY_TOKEN` with a long random
   value (e.g. `openssl rand -hex 24`). The token is the only write gate — the
   script URL itself is visible in the public page.
4. Deploy → New deployment → Web app → *Execute as: Me*, *Who has access:
   Anyone*. Copy the `/exec` URL.
5. Open `/admin/`, tap ⚙, paste the URL and token, hit **Ping**. It should
   report the script version and the spreadsheet name — confirm both.

### Keeping repo and deployment in sync

The deployed script and `WebApp.gs` in this repo drift silently if you edit
one without the other. Rules:

- Every change to `WebApp.gs` bumps `ENTRY.VERSION`.
- After redeploying (Deploy → Manage deployments → edit → new version), hit
  **Ping** in the UI settings and check the version matches the repo.

### Token rotation

Change `ENTRY_TOKEN` in Script Properties (no redeploy needed), then update it
in the UI settings drawer on each device.

## Testing changes — never against the production sheet

Use a full copy of the spreadsheet:

1. File → Make a copy. In the copy: File → Share → Publish to web.
2. **Verify the copy's gids**: copies usually keep per-tab gids, but check a
   few tab URLs (`{copy base}?gid={gid}&single=true&output=csv` with gids from
   `src/dataset.py`) before trusting them. If they differ, note the copy's gid
   map here.
3. Deploy the script on the *copy* (same steps as above, its own token). Point
   the UI at it via the settings drawer.
4. Enter test data (fixture → result with scorers → a new player), then verify
   from the repo without touching the canonical snapshot:

   ```sh
   DATASET_BASE_URL='<copy published base url>' python validate.py --no-snapshot
   DATASET_BASE_URL='<copy published base url>' python build.py --dist staging --no-snapshot
   ```

   Check the staging pages render the result, and open the copy's published
   matches CSV to confirm the new rows' `date`/`kickoff` are literal
   `YYYY-MM-DD`/`HH:MM` text (the cell-format risk the script guards against).

### Why no throwaway rows in production

CI commits the fetched CSVs to `data/canonical/` on every build (push to main,
daily cron at 05:07 UTC, or manual dispatch). Once a test `match_id` or
`player_id` lands in a committed snapshot, deleting it from the sheet
hard-fails the drift check and the next build needs
`python validate.py --allow-deletions` locally (or a temporary workflow edit).
So: smoke-test production with a *real* upcoming fixture. If a bad row does
slip in, delete it in the sheet and run one build with `--allow-deletions`.

## Notes and limits (v1)

- **Season setup is manual by design.** A team without an `entries` row for
  the competition+season cannot be picked — that mirrors validate.py check 3.
  At season start, add entries/clubs/teams in the sheet first.
- **Scorer suggestions** are ranked by each player's most recent goal (the
  `registrations` tab is empty; if it ever gets populated, prefer it in
  `buildIndexes`). New players and unknown scorers (`CAF_MW_UNKNOWN`) are
  always available.
- **Assists** are supported by the API (`goals[].assist_player_id`) but not
  exposed in the UI yet.
- Re-saving a played match requires the explicit "replace" checkbox and
  deletes + rewrites that match's goal rows (goals are not drift-checked, so
  this is safe).
- The published CSVs the page reads lag the sheet by ~5 minutes; the result
  picker uses the script's `live_matches` action instead, and this session's
  writes are overlaid from sessionStorage. A save landing mid-build (between
  CI's `matches` and `goals` fetches) can fail one nightly build with a
  transient FK error; it self-heals on the next run.
