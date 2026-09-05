# Import evaluation set

Real Everyleague-style screenshots, and what is actually printed on them.

    python3 scripts/import_eval.py

**This costs money and is not part of the test suite.** `npm test` and
`python3 -m unittest` cover what the code *does* with an extraction — that a
malformed response degrades, that an invented id cannot survive, that an
abandoned match cannot carry a score. This measures something different and
softer: whether the reading is any good. The answer is a score, not a
pass/fail, so it is a thing a person runs and looks at.

Run it when you change `EVERYLEAGUE_IMPORT_MODEL`, when you change
`SYSTEM_PROMPT` in `supabase/functions/import-extract/extract.js`, or after a
batch of imports that reporters had to correct heavily.

## Adding a case

Two files here, sharing a stem:

    nrfa-md7.jpg      the screenshot, exactly as a reporter would send it
    nrfa-md7.json     what is actually printed on it

```json
{
  "note": "NRFA Division Two matchday 7, from the league's Facebook page",
  "document_kind": "results",
  "results": [
    {"home": "Blue Eagles", "away": "Silver Strikers",
     "home_score": 2, "away_score": 1, "status": "played"},
    {"home": "DD Sunshine", "away": "Ascent Academy",
     "home_score": null, "away_score": null, "status": "postponed"}
  ]
}
```

Write the names **exactly as printed on the image**, not as they appear in the
database. The extraction contract is that names come back as printed and the
alias tables resolve them afterwards; an expectation written from the database
would mark the model down for obeying its instructions.

Names are compared with the same normalization `import_normalize()` uses in
migration 0043 — case, accents and punctuation removed — so "Blue Eagles FC"
and "blue eagles fc." count as the same read.

## Which cases are worth having

The clean league graphics already work. What earns its place here is what
does not:

- a photograph of a screen, at an angle, with glare
- a hand-written or printed team sheet
- a graphic where the score sits between the badges rather than between names
- a crop that cuts off the last fixture
- abbreviations only a local would expand ("MW", "NBB")
- a **fixture list**, so the "coming next" path stays honest
- something genuinely unreadable — `"document_kind": "unreadable"` with
  `"results": []` is a valid and useful case

## What the score means

`correct` counts scorelines where the pairing, both scores and the status all
match. Rows are matched by team pairing rather than by position, since the
order is not something the extraction promises.

`invented` is called out separately and matters most. A **missed** row is
visible — the reporter sees a gap. An **invented** one arrives on the review
screen looking exactly like every other row.

## Privacy

These are public league posts. Do not add a screenshot of a private message,
and do not add anything with a phone number in it.
