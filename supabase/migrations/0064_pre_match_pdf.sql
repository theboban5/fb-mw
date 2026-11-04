-- 0064 — a pre-match report can be uploaded as the PDF it arrives as.
--
-- WHAT WAS WRONG
--
-- The league's competition system has started issuing a "Pre-match Report"
-- per match: both XIs, both benches, every shirt, captain, keeper, coach and
-- official, and a FAM registration number beside every name. It arrives as a
-- PDF — on WhatsApp, from the club or the league — and the report-imports
-- bucket (0042) took only JPEG, PNG and WebP. So the one document that
-- carries a whole team sheet exactly, as text, could reach the importer only
-- as a phone screenshot of an A4 page, which is that same text at the size
-- of a footnote.
--
-- WHAT THIS DOES
--
-- Adds application/pdf to the bucket's allowed types. Nothing else changes:
--
--   * The object's path is still '<reporter_id>/<uuid>.<ext>', so 0042's
--     storage policies — the path IS the authorization — cover it as they
--     stand. The extension is what tells import-extract to send a document
--     block rather than an image.
--   * The channel stays 'image' (or 'image_url'). It means "a file was
--     uploaded", which a PDF is; the path's extension says which kind, and a
--     new channel would be a CHECK constraint and a function rewritten for a
--     distinction already on the row.
--   * The size cap stays 10 MB. A two-page report is about 250 kB.
--
-- The registration numbers are read (reg_no on each player and coach in
-- report_imports.extracted) and kept there, beside the sheet they came from.
-- Nothing writes them to `players` yet: that is a column, a collision rule
-- and a write path of its own, and the evidence is safe where it is until
-- then. The number looks like six digits, a sex and a two-digit birth year;
-- nothing reads a birth year out of it, here or anywhere.
--
-- THE TRADE
--
-- A PDF is not shrunk on the phone the way a photo is, so it costs the
-- reporter its whole size in data. At ~250 kB it is smaller than most of the
-- screenshots this bucket already holds.

update storage.buckets
   set allowed_mime_types = array['image/jpeg', 'image/png', 'image/webp',
                                  'application/pdf']
 where id = 'report-imports';
