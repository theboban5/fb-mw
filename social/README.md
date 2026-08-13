# social — post packs from the match data

Turns the same Google Sheet the site builds from into ready-to-post social
packs: a 1080×1080 PNG plus a caption for WhatsApp, Facebook, X and Instagram,
and a phone-friendly page with a copy button for each.

**This module never publishes anything.** There are no platform credentials in
it, nothing is scheduled, and no API is called. It writes files; you post them.
That is a design constraint, not an unfinished feature.

## Setup

```bash
pip install -r social/requirements.txt
playwright install chromium          # ~150MB, one time
python -m social doctor              # should report all checks passed
```

The dependencies live in `social/requirements.txt` rather than the root
`requirements.txt` on purpose: the deploy workflow installs the root file and
then runs `build.py`, and the site build has no use for a browser engine.

Fonts are already vendored (`assets/fonts/`, Inter as two woff2 subsets), so
rendering never touches the network.

## The daily command

```bash
python -m social generate
```

Writes `out/social/YYYY-MM-DD/` and prints the path to a post pack. Open that
`index.html` **on your phone** — it is the whole interface:

1. Long-press the graphic to save it.
2. Tap the platform button; its caption is now on your clipboard.
3. Post, come back, tick the box. Ticks survive a reload, so an interrupted
   round of posting can be resumed.

To build offline from the last validated fetch, exactly as the site does:

```bash
DATASET_LOCAL_DIR=data/canonical python -m social generate
```

### Other commands

```bash
python -m social generate --date 2026-08-17
python -m social generate --types roundup,scorers
python -m social generate --types results --competition MW_SL
python -m social generate --match-id MW_SL_2627_090   # one match, hero board
python -m social generate --no-hashtags
python -m social generate --dry-run          # captions to stdout, no rendering
python -m social preview --type table        # render one board and open it
python -m social doctor                      # environment, assets, data health
```

A full Saturday produces a lot of boards — results and fixtures are one board
per competition, and seven competitions play. Narrow it with `--types` and
`--competition` rather than posting all of them.

## What gets generated

| Post type | What it is | Boards |
|---|---|---|
| `results` | Played matches in the last day, with scorers | one per competition |
| `fixtures` | Scheduled matches in the next three days | one per competition |
| `roundup` | The weekend's results, all competitions, compact | one (splits past 12 matches) |
| `scorers` | Top scorers, ties shown as joint positions | one per competition |
| `table` | Computed standings plus one derived stat line | one per competition |
| `flex` | Hand-written content in-brand — see below | one |

Each writes `<key>.png`, `<key>.captions.json`, and a `.txt` per platform, plus
a shared `index.html` and `manifest.json` recording what was skipped and why.

## The rules it will not break

These are the reason the module is shaped the way it is. Changing them is a
decision, not a refactor.

- **Never fabricate a value.** A missing scorer is a skipped line, never a
  guess. Where recorded goals are fewer than the score, the board says
  `+1 not recorded` rather than showing a partial list as if it were complete.
- **Validation blocks publication.** A match marked played with no score, a
  goal credited to a team not in the match, more goal rows than the scoreline,
  a duplicate fixture or a date outside its season stops the boards that
  depend on it. See `validate.py`. Warnings (missing crest, missing venue,
  missing minute) appear on the card instead.
- **The voice rules** live in `config.VOICE_RULES` and are enforced by tests
  where a machine can check them: no exclamation points, absolute times only,
  no predictions, no invented names.
- **No network at render time.** Fonts and crests are local files.

## How to add a crest

Drop a 512×512 transparent PNG into `static/logos/clubs/` named for the
**club_id** (`MW_BULL.png`). A team with its own crest can use its
`legacy_code` instead (`SRFA_WR.png`), which is checked first — the same
lookup order `src/render.py` uses, so the site picks it up too.

A team with no crest renders a monogram tile in brand neutrals; that is a
designed fallback, not a defect. `python -m social doctor` lists every team
currently falling back to one.

## How to add a post type

1. Write `social/posts/<name>.py` with a `PostType` subclass implementing
   `is_available(ctx) -> (bool, reason)` and `build(ctx) -> [Draft]`, and call
   `base.register(YourType())` at the bottom.
2. Add it to the import line in `posts/base.registry()`.
3. Write `social/templates/<name>.html` extending `_base.html`. Use the shared
   row system (`.rows`, `.fixture`, `.rowmeta`, `.figure`) if you are listing
   matches — that is what keeps the family looking like one product. Never put
   a colour literal in a template; the tokens come from `config.TOKENS`.
4. Populate `Draft.match_ids` so validation can block exactly your board.
5. Add a golden caption test (see below).

## Design

Tokens are the site's own dark-theme values from `static/style.css`, defined
once in `config.TOKENS` and written into `:root` by `_base.html`. The signature
device — an accent eyebrow bar closed by a rule, hairline-separated rows,
square crest tiles, the watermark bottom-left — is identical on every template
so a board is recognisable cropped to a thumbnail.

The renderer asserts, via Chrome DevTools, that every glyph on the board was
painted in Inter. A loaded-but-unused webfont is the classic way these
pipelines ship a graphic in the wrong typeface, and `document.fonts.ready`
does not catch it.

## Tests

```bash
python -m unittest discover -s tests            # everything
python -m unittest tests.test_social_captions   # just the captions
```

Caption tests are golden files in `tests/social_golden/`. After an intentional
wording change:

```bash
UPDATE_GOLDEN=1 python -m unittest discover -s tests -k social
```

Read the diff before committing it — those files are the house voice.

The tests live at `tests/test_social_*.py` rather than in a `tests/social/`
package: the repo's documented discovery command puts `tests/` on `sys.path`,
where a package named `social` would shadow this one.

## Flex posts

For a poll, a crosspost or an announcement, write
`social/flex/<date>.json` (see `example.json`) and run `generate`. Fields:
`headline`, `body`, optional `stat` / `stat_label` / `eyebrow` / `kind` /
`path` / `alt_text`. `.yml` also works if PyYAML happens to be installed;
JSON needs no extra dependency.

## Not wired up

Copying the pack into the site's publish output at `/social/<date>/` is
described in the original brief but deliberately not built. It would need a
`noindex` header and a sitemap exclusion, and it puts unposted drafts on a
public URL. Ask before adding it.
