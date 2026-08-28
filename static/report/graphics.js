/* Shareable "news card" graphics — a browser-native twin of social/render.py's
 * Jinja2 + Playwright pipeline, for the two admins to generate from a phone
 * with no local setup. Same design tokens, same row/crest/watermark layout
 * math as social/templates/_base.html, but drawn straight onto a <canvas>
 * and exported via toBlob() — no screenshot step, no server, no new
 * dependency (the Canvas 2D and FontFace APIs are native to the browser).
 *
 * This module touches no network beyond loading its own fonts/crest images
 * and knows nothing about Supabase: it takes plain data (team names, crest
 * URLs, scores, kickoff times) and draws it. app.js owns fetching that data
 * and wiring this to the DOM.
 *
 * Fidelity is deliberately "close, not pixel-identical" — a canvas port of a
 * CSS layout is always an approximation. See tests/test_graphics_tokens.py
 * for the one thing that IS mechanically checked (the colour tokens).
 */

// ── Tokens ───────────────────────────────────────────────────────────────
// Source of truth: social/config.py TOKENS. Keep these in sync by hand —
// tests/test_graphics_tokens.py asserts they match key-for-key.
export const TOKENS = {
  ground: "#15171a",
  panel: "#1b1e22",
  row_alt: "#23272d",
  line: "#2a2e34",
  ink: "#e9eaec",
  muted: "#9aa1ab",
  accent: "#3fb37a",
  accent_deep: "#0b6b3a",
  monogram_bg: "#23272d",
  monogram_ink: "#9aa1ab",
  warn: "#e2a33c",
};

export const BOARD = 1080;
export const DEVICE_SCALE = 2;
export const WATERMARK = "everyleague.co";

const PAD = 44;
const FONT_STACK = "'EL-Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";

// Row/crest/figure sizing per density — ported from social/templates/_base.html
// .rows / .d-roomy / .d-hero.
export const DENSITY = {
  default: { crest: 76, name: 36, figure: 88, figurew: 200, meta: 23, gap: 16 },
  roomy: { crest: 108, name: 45, figure: 124, figurew: 260, meta: 26, gap: 20 },
  hero: { crest: 250, name: 50, figure: 200, figurew: 380, meta: 30, gap: 26 },
};

export const TEMPLATE_LABELS = {
  fixtures: "Fixture board",
  results: "Results board",
  scorers: "Top scorers",
  editorial: "Announcement",
};

// ── Fonts ────────────────────────────────────────────────────────────────
// Vendored copies of the same two files social/assets/fonts/ ships, so a
// board rendered here never depends on the network and matches the CLI's
// output. Loaded under a distinct family name (EL-Inter) so a page that
// already has its own "Inter" (there isn't one today, but in case) can never
// collide with it.

let fontsReady = null;

/** Load the vendored Inter faces once. A canvas paints with whatever font is
 *  ready AT FILLTEXT TIME — there is no "wait for web font" the way CSS
 *  gives you for free — so every draw call routes through ensureFonts()
 *  first, and the live on-screen preview is what catches a fallback face
 *  that failed to load (the compensating control for not having Playwright's
 *  glyph-paint assertion here). */
export function ensureFonts() {
  if (fontsReady) return fontsReady;
  const files = ["inter-latin.woff2", "inter-latin-ext.woff2"];
  fontsReady = Promise.all(files.map((file) => {
    const url = new URL(`./fonts/${file}`, import.meta.url).href;
    return new FontFace("EL-Inter", `url(${url})`, { weight: "100 900" })
      .load().then((face) => { document.fonts.add(face); return face; });
  })).then(() => document.fonts.ready)
    .catch((err) => {
      console.warn("[everyleague] graphics: Inter failed to load, "
        + "boards will use the system font", err);
    });
  return fontsReady;
}

// ── Crests ───────────────────────────────────────────────────────────────

export function monogram(name) {
  const words = (name || "").split(/\s+/).filter((w) => /^[a-z0-9]/i.test(w));
  if (!words.length) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

const crestCache = new Map();

function loadOne(code) {
  if (!code) return Promise.resolve(null);
  if (crestCache.has(code)) return Promise.resolve(crestCache.get(code));
  return new Promise((resolve) => {
    const img = new Image();
    // Crests live at docs/logos/clubs/, one level up from docs/report/ where
    // this module is served — relative to import.meta.url rather than a
    // hardcoded absolute path, so it holds regardless of the site's root.
    img.src = new URL(`../logos/clubs/${encodeURIComponent(code)}.png`,
      import.meta.url).href;
    img.onload = () => { crestCache.set(code, img); resolve(img); };
    img.onerror = () => { crestCache.set(code, null); resolve(null); };
  });
}

/** Resolve a team's crest, legacy_code first then club_id — the same order
 *  as social/data.py's crest_path() / src/render.py's lookup. Resolves to
 *  null (draw the monogram fallback) rather than rejecting, so a missing
 *  crest never breaks a board. */
export async function loadCrest(legacyCode, clubId) {
  return (await loadOne(legacyCode)) || (await loadOne(clubId));
}

// ── Text helpers ─────────────────────────────────────────────────────────

function stepDown(text, base, longAt, longerAt, longFactor, longerFactor) {
  const len = (text || "").length;
  if (len > longerAt) return base * longerFactor;
  if (len > longAt) return base * longFactor;
  return base;
}

// Ported from _macros.html's team_name(): >21 chars -> 0.70x, >14 -> 0.84x.
function nameFontSize(name, base) {
  return stepDown(name, base, 14, 21, 0.84, 0.70);
}

// Ported from _base.html's .eyebrow-title(.long/.longer): fixed steps, not a
// multiplier, because the eyebrow has no --name-style variable to scale.
function eyebrowTitleSize(text) {
  const len = (text || "").length;
  if (len > 34) return 21;
  if (len > 26) return 25;
  return 31;
}

function wrapLines(ctx, text, maxWidth, maxLines) {
  const words = (text || "").split(/\s+/).filter(Boolean);
  if (!words.length) return [""];
  const lines = [];
  let cur = words[0];
  for (let i = 1; i < words.length; i++) {
    const test = `${cur} ${words[i]}`;
    if (ctx.measureText(test).width <= maxWidth || lines.length >= maxLines - 1) {
      cur = test;
    } else {
      lines.push(cur);
      cur = words[i];
    }
  }
  lines.push(cur);
  return lines.slice(0, maxLines);
}

function drawWrapped(ctx, text, x, cy, maxWidth, align, fontSize, maxLines = 2) {
  ctx.textAlign = align;
  ctx.textBaseline = "middle";
  const lines = wrapLines(ctx, text, maxWidth, maxLines);
  const lineHeight = fontSize * 1.08;
  let y = cy - (lineHeight * (lines.length - 1)) / 2;
  lines.forEach((line) => { ctx.fillText(line, x, y); y += lineHeight; });
}

/** "2–1" — the score's separator drawn in the accent colour, the same detail
 *  as _base.html's .figure .sep. */
function drawScore(ctx, text, cx, cy, fontSize, ink) {
  const [h, a] = String(text).split("-");
  if (h == null || a == null) {
    ctx.fillStyle = ink;
    ctx.textAlign = "center";
    ctx.fillText(text, cx, cy);
    return;
  }
  const sep = " – ";
  ctx.textAlign = "left";
  const totalWidth = ctx.measureText(h).width + ctx.measureText(sep).width
    + ctx.measureText(a).width;
  let x = cx - totalWidth / 2;
  ctx.fillStyle = ink;
  ctx.fillText(h, x, cy); x += ctx.measureText(h).width;
  ctx.fillStyle = TOKENS.accent;
  ctx.fillText(sep, x, cy); x += ctx.measureText(sep).width;
  ctx.fillStyle = ink;
  ctx.fillText(a, x, cy);
}

// ── Crest tile ───────────────────────────────────────────────────────────

/** Square, hairline-bordered tile — same treatment on every template. A
 *  missing crest draws a monogram in brand neutrals rather than an invented
 *  club colour, exactly _macros.html's crest() macro. */
function drawCrestTile(ctx, x, y, size, img, name) {
  ctx.fillStyle = TOKENS.monogram_bg;
  ctx.fillRect(x, y, size, size);
  ctx.strokeStyle = TOKENS.line;
  ctx.lineWidth = 1;
  ctx.strokeRect(x + 0.5, y + 0.5, size - 1, size - 1);
  if (img) {
    const scale = Math.min(size / img.naturalWidth, size / img.naturalHeight);
    const w = img.naturalWidth * scale;
    const h = img.naturalHeight * scale;
    ctx.drawImage(img, x + (size - w) / 2, y + (size - h) / 2, w, h);
  } else {
    ctx.fillStyle = TOKENS.monogram_ink;
    ctx.font = `900 ${Math.round(size * 0.36)}px ${FONT_STACK}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(monogram(name), x + size / 2, y + size / 2 + 1);
  }
}

// ── Board scaffold ───────────────────────────────────────────────────────
// The eyebrow bar + accent rule, standfirst line, and watermark are the same
// on every template — the "signature device" _base.html's comment names.

function fillGround(ctx) {
  ctx.fillStyle = TOKENS.ground;
  ctx.fillRect(0, 0, BOARD, BOARD);
}

function drawEyebrow(ctx, eyebrow, kind) {
  const H = 96;
  ctx.fillStyle = TOKENS.accent_deep;
  ctx.fillRect(0, 0, BOARD, H);
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  ctx.fillStyle = "#ffffff";
  ctx.font = `900 ${eyebrowTitleSize(eyebrow)}px ${FONT_STACK}`;
  ctx.fillText((eyebrow || "").toUpperCase(), PAD, H / 2 + 1, BOARD - PAD * 2 - 240);
  ctx.textAlign = "right";
  ctx.font = `700 25px ${FONT_STACK}`;
  ctx.fillStyle = "rgba(255,255,255,0.72)";
  ctx.fillText((kind || "").toUpperCase(), BOARD - PAD, H / 2 + 1);
  ctx.fillStyle = TOKENS.accent;
  ctx.fillRect(0, H, BOARD, 6);
  return H + 6;
}

function drawStandfirst(ctx, top, left, right) {
  if (!left && !right) return top;
  const fontSize = 26;
  ctx.font = `600 ${fontSize}px ${FONT_STACK}`;
  ctx.fillStyle = TOKENS.muted;
  ctx.textBaseline = "alphabetic";
  const y = top + 26 + fontSize * 0.8;
  ctx.textAlign = "left";
  ctx.fillText((left || "").toUpperCase(), PAD, y);
  ctx.textAlign = "right";
  ctx.fillText((right || "").toUpperCase(), BOARD - PAD, y);
  return y + fontSize * 0.6 + 14;
}

/** Draws the watermark strip and returns the y where the body must stop. */
function drawWatermark(ctx, note) {
  const H = 90;
  const top = BOARD - H;
  ctx.fillStyle = TOKENS.ground;
  ctx.fillRect(0, top, BOARD, H);
  const markSize = 20;
  const cy = top + H / 2;
  ctx.fillStyle = TOKENS.accent;
  ctx.fillRect(PAD, cy - markSize / 2, markSize, markSize);
  ctx.fillStyle = TOKENS.ink;
  ctx.font = `700 27px ${FONT_STACK}`;
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText(WATERMARK, PAD + markSize + 14, cy + 1);
  if (note) {
    ctx.fillStyle = TOKENS.muted;
    ctx.font = `600 23px ${FONT_STACK}`;
    ctx.textAlign = "right";
    ctx.fillText(note.toUpperCase(), BOARD - PAD, cy + 1);
  }
  return top;
}

function drawHairline(ctx, y) {
  ctx.strokeStyle = TOKENS.line;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(PAD, y);
  ctx.lineTo(BOARD - PAD, y);
  ctx.stroke();
}

/** fillText, clipped to a box — the canvas stand-in for CSS's implicit
 *  overflow:hidden on a fixed-width column. Used only where a name could in
 *  principle run into its neighbour (the scorer list's player/club column,
 *  hard against the goals tally); the match-row names use drawWrapped()
 *  instead, which has room to wrap. */
function fillTextClipped(ctx, text, x, y, width, height) {
  ctx.save();
  ctx.beginPath();
  ctx.rect(x, y - height, width, height * 2);
  ctx.clip();
  ctx.fillText(text, x, y);
  ctx.restore();
}

// ── The scorer-row system ────────────────────────────────────────────────
// Ported from social/templates/scorers.html's .scorer grid: rank | crest |
// player+club | goals tally. A joint tally reads '=3', never a bare rank —
// showing four players on 5 goals as 2nd/3rd/4th/5th asserts an order the
// data does not have.

function drawScorerRows(ctx, top, bottom, rows) {
  const many = rows.length > 8;
  const s = many
    ? { crest: 62, player: 32, club: 20, tally: 48, pos: 32 }
    : { crest: 84, player: 40, club: 24, tally: 62, pos: 40 };
  const posW = 78;
  const gap = 22;
  const goalsColW = 110;
  const crestX = PAD + posW + gap;
  const whoLeft = crestX + s.crest + gap;
  const whoWidth = BOARD - PAD - goalsColW - whoLeft;
  const rowH = (bottom - top) / rows.length;

  rows.forEach((r, i) => {
    const rowTop = top + i * rowH;
    const rowBottom = top + (i + 1) * rowH;
    const cy = (rowTop + rowBottom) / 2;

    ctx.font = `900 ${r.joint ? s.pos * 0.85 : s.pos}px ${FONT_STACK}`;
    ctx.fillStyle = TOKENS.accent;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.fillText(`${r.joint ? "=" : ""}${r.position}`, PAD + posW, cy);

    drawCrestTile(ctx, crestX, cy - s.crest / 2, s.crest,
      r.team?.img || null, r.team?.name || r.name);

    const nameY = r.team ? cy - s.club * 0.55 : cy;
    ctx.font = `600 ${r.name.length > 18 ? s.player * 0.85 : s.player}px ${FONT_STACK}`;
    ctx.fillStyle = TOKENS.ink;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    fillTextClipped(ctx, r.name, whoLeft, nameY, whoWidth, s.player);
    if (r.team) {
      ctx.font = `400 ${s.club}px ${FONT_STACK}`;
      ctx.fillStyle = TOKENS.muted;
      fillTextClipped(ctx, r.team.name, whoLeft, nameY + s.club + 4, whoWidth, s.club);
    }

    ctx.font = `900 ${s.tally}px ${FONT_STACK}`;
    ctx.fillStyle = TOKENS.ink;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.fillText(String(r.goals), BOARD - PAD, cy);

    if (i < rows.length - 1) drawHairline(ctx, rowBottom);
  });
}

// ── The match-row system ─────────────────────────────────────────────────
// Shared by fixtures and results — they differ only in what the centre
// "figure" shows (kickoff time vs. scoreline) and what the sub-line under it
// says. Ported from _base.html's .rows grid: crest | name | figure | name |
// crest, each row an equal share of the body height (CSS: flex: 1 1 0).

function drawRow(ctx, { top, bottom, density, home, away, figureText, figureVariant, when, note }) {
  const d = DENSITY[density];
  const innerW = BOARD - PAD * 2;
  const nameColW = (innerW - d.crest * 2 - d.figurew - d.gap * 4) / 2;
  let x = PAD;
  const crestLx = x; x += d.crest + d.gap;
  const nameHomeRight = x + nameColW; x += nameColW + d.gap;
  const figureCx = x + d.figurew / 2; x += d.figurew + d.gap;
  const nameAwayLeft = x; x += nameColW + d.gap;
  const crestRx = x;

  const extraLines = [];
  if (when) extraLines.push({ text: when, color: TOKENS.muted, weight: 400, size: d.meta, upper: false });
  if (note) extraLines.push({ text: note, color: TOKENS.accent, weight: 700, size: d.meta * 0.95, upper: true });

  const lineGap = 8;
  const extraH = extraLines.reduce((sum, l) => sum + l.size * 1.3, 0)
    + (extraLines.length ? lineGap * extraLines.length : 0);
  const blockH = d.crest + extraH;
  const blockTop = top + ((bottom - top) - blockH) / 2;
  const fixtureCy = blockTop + d.crest / 2;

  drawCrestTile(ctx, crestLx, blockTop, d.crest, home.img, home.name);
  drawCrestTile(ctx, crestRx, blockTop, d.crest, away.img, away.name);

  ctx.fillStyle = TOKENS.ink;
  ctx.font = `600 ${nameFontSize(home.name, d.name)}px ${FONT_STACK}`;
  drawWrapped(ctx, home.name, nameHomeRight, fixtureCy, nameColW, "right",
    nameFontSize(home.name, d.name));
  ctx.font = `600 ${nameFontSize(away.name, d.name)}px ${FONT_STACK}`;
  drawWrapped(ctx, away.name, nameAwayLeft, fixtureCy, nameColW, "left",
    nameFontSize(away.name, d.name));

  const isTbc = figureVariant === "tbc";
  const isTime = figureVariant === "time";
  const figSize = isTime ? d.figure * 0.62 : isTbc ? d.figure * 0.42 : d.figure;
  ctx.font = `${isTbc ? 700 : 900} ${figSize}px ${FONT_STACK}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  if (figureVariant === "score") {
    drawScore(ctx, figureText, figureCx, fixtureCy, figSize, TOKENS.ink);
  } else {
    ctx.fillStyle = isTbc ? TOKENS.muted : TOKENS.ink;
    ctx.fillText(figureText, figureCx, fixtureCy);
  }

  let ly = blockTop + d.crest + lineGap;
  extraLines.forEach((l) => {
    ly += l.size;
    ctx.font = `${l.weight} ${l.size}px ${FONT_STACK}`;
    ctx.fillStyle = l.color;
    ctx.textAlign = "center";
    ctx.textBaseline = "alphabetic";
    ctx.fillText(l.upper ? l.text.toUpperCase() : l.text, BOARD / 2, ly);
    ly += l.size * 0.3 + lineGap;
  });
}

/** The single-match layout: crest above name either side of a big centre
 *  figure, ported from _base.html's .d-hero grid-template-areas. Used when a
 *  board has exactly one match — a row layout has no room for a 250px crest
 *  and a 200px figure on the same line. */
function drawHeroRow(ctx, { top, bottom, home, away, figureText, figureVariant, venue }) {
  const d = DENSITY.hero;
  const cy = (top + bottom) / 2;
  const blockH = d.crest + 24 + d.name * 1.15;
  const blockTop = cy - blockH / 2;
  const colW = (BOARD - PAD * 2 - d.figurew) / 2;
  const leftCx = PAD + colW / 2;
  const rightCx = BOARD - PAD - colW / 2;
  const figCx = BOARD / 2;

  drawCrestTile(ctx, leftCx - d.crest / 2, blockTop, d.crest, home.img, home.name);
  drawCrestTile(ctx, rightCx - d.crest / 2, blockTop, d.crest, away.img, away.name);

  const nameCy = blockTop + d.crest + 24 + d.name * 0.5;
  ctx.font = `600 ${nameFontSize(home.name, d.name)}px ${FONT_STACK}`;
  ctx.fillStyle = TOKENS.ink;
  drawWrapped(ctx, home.name, leftCx, nameCy, colW, "center",
    nameFontSize(home.name, d.name));
  ctx.font = `600 ${nameFontSize(away.name, d.name)}px ${FONT_STACK}`;
  drawWrapped(ctx, away.name, rightCx, nameCy, colW, "center",
    nameFontSize(away.name, d.name));

  const isTbc = figureVariant === "tbc";
  const isTime = figureVariant === "time";
  const figSize = isTime ? d.figure * 0.62 : isTbc ? d.figure * 0.42 : d.figure;
  ctx.font = `${isTbc ? 700 : 900} ${figSize}px ${FONT_STACK}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  if (figureVariant === "score") {
    drawScore(ctx, figureText, figCx, blockTop + d.crest / 2, figSize, TOKENS.ink);
  } else {
    ctx.fillStyle = isTbc ? TOKENS.muted : TOKENS.ink;
    ctx.fillText(figureText, figCx, blockTop + d.crest / 2);
  }

  if (venue) {
    ctx.font = `600 25px ${FONT_STACK}`;
    ctx.fillStyle = TOKENS.muted;
    ctx.textAlign = "center";
    ctx.textBaseline = "alphabetic";
    ctx.fillText(venue.toUpperCase(), BOARD / 2, nameCy + d.name * 0.9 + 40);
  }
}

/** Density from match count: one match earns the hero treatment, a handful
 *  fit roomy, more than three drop to default — the same judgement the CLI's
 *  post types make from `--days`/row count. */
export function densityFor(count) {
  if (count <= 1) return "hero";
  if (count <= 3) return "roomy";
  return "default";
}

/** An optional headline + subtext, centred between the standfirst and
 *  whatever the template draws next — the same words an editorial card
 *  carries, but on top of a real fixture/result/leaderboard instead of a
 *  blank board. `compact` shrinks it for a board that also has to fit
 *  several rows; a single-match hero or an otherwise-empty board can afford
 *  the larger size. Returns the new top the caller should draw from —
 *  unchanged if there is nothing to show. */
function drawHeadlineBlock(ctx, top, headline, subtext, compact) {
  if (!headline && !subtext) return top;
  const innerW = BOARD - PAD * 2;
  let y = top + (compact ? 14 : 22);
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  if (headline) {
    const sizes = compact ? [30, 26, 22] : [46, 40, 34];
    const size = headline.length > 60 ? sizes[2] : headline.length > 36 ? sizes[1] : sizes[0];
    ctx.font = `900 ${size}px ${FONT_STACK}`;
    ctx.fillStyle = TOKENS.ink;
    wrapLines(ctx, headline, innerW, 2).forEach((line) => {
      y += size * 1.08;
      ctx.fillText(line, BOARD / 2, y);
    });
    y += compact ? 4 : 8;
  }
  if (subtext) {
    const size = compact ? 20 : 26;
    ctx.font = `500 ${size}px ${FONT_STACK}`;
    ctx.fillStyle = TOKENS.muted;
    wrapLines(ctx, subtext, innerW - (compact ? 20 : 60), 2).forEach((line) => {
      y += size * 1.3;
      ctx.fillText(line, BOARD / 2, y);
    });
  }
  return y + (compact ? 16 : 26);
}

function drawMatchBoard(ctx, { eyebrow, kind, standfirstLeft, standfirstRight,
  watermarkNote, matches, figureOf, headline, subtext }) {
  let top = drawEyebrow(ctx, eyebrow, kind);
  top = drawStandfirst(ctx, top, standfirstLeft, standfirstRight);
  const bottom = drawWatermark(ctx, watermarkNote);
  // Compact once there's more than one row to protect — a hero card (one
  // match, or none yet) has the room for the bigger size.
  top = drawHeadlineBlock(ctx, top, headline, subtext, matches.length > 1);
  if (!matches.length) return;   // an empty selection still previews the scaffold
  const density = densityFor(matches.length);
  if (density === "hero") {
    const m = matches[0];
    const fig = figureOf(m);
    drawHeroRow(ctx, {
      top, bottom, home: m.home, away: m.away,
      figureText: fig.text, figureVariant: fig.variant, venue: m.venue,
    });
    return;
  }
  const rowH = (bottom - top) / matches.length;
  matches.forEach((m, i) => {
    const fig = figureOf(m);
    drawRow(ctx, {
      top: top + i * rowH, bottom: top + (i + 1) * rowH, density,
      home: m.home, away: m.away,
      figureText: fig.text, figureVariant: fig.variant,
      when: fig.when || null, note: fig.note || null,
    });
    if (i < matches.length - 1) drawHairline(ctx, top + (i + 1) * rowH);
  });
}

// ── Templates ────────────────────────────────────────────────────────────

export const TEMPLATES = {
  fixtures: {
    label: TEMPLATE_LABELS.fixtures,
    draw(ctx, state) {
      drawMatchBoard(ctx, {
        eyebrow: state.competitionName, kind: "FIXTURES",
        standfirstLeft: state.standfirstLeft, standfirstRight: state.standfirstRight,
        watermarkNote: state.seasonLabel, matches: state.matches,
        headline: state.headline, subtext: state.subtext,
        figureOf: (m) => ({
          text: m.kickoff || "TBC",
          variant: m.kickoff ? "time" : "tbc",
          // `when` is app.js's to set (it knows whether this board spans more
          // than one competition, and names it there); this default only
          // covers the plain single-competition case.
          when: m.when != null ? m.when : (state.matches.length > 1 || m.venue
            ? [m.dateLabel, m.venue].filter(Boolean).join(" · ") : null),
        }),
      });
    },
  },
  results: {
    label: TEMPLATE_LABELS.results,
    draw(ctx, state) {
      drawMatchBoard(ctx, {
        eyebrow: state.competitionName, kind: "RESULTS",
        standfirstLeft: state.standfirstLeft, standfirstRight: state.standfirstRight,
        watermarkNote: state.seasonLabel, matches: state.matches,
        headline: state.headline, subtext: state.subtext,
        figureOf: (m) => ({
          text: `${m.homeGoals}-${m.awayGoals}`, variant: "score",
          when: m.when || null,
        }),
      });
    },
  },
  scorers: {
    label: TEMPLATE_LABELS.scorers,
    draw(ctx, state) {
      let top = drawEyebrow(ctx, state.competitionName, "TOP SCORERS");
      top = drawStandfirst(ctx, top, state.standfirstLeft, state.standfirstRight);
      const bottom = drawWatermark(ctx, state.seasonLabel);
      // A scorer list is already dense, so its headline always takes the
      // compact size regardless of how many rows are actually showing.
      top = drawHeadlineBlock(ctx, top, state.headline, state.subtext, true);
      if (!state.rows.length) return;
      drawScorerRows(ctx, top, bottom, state.rows);
    },
  },
  editorial: {
    label: TEMPLATE_LABELS.editorial,
    draw(ctx, state) {
      const top = drawEyebrow(ctx, state.eyebrow || "Announcement", "EVERYLEAGUE");
      const bottom = drawWatermark(ctx, null);
      const cx = BOARD / 2;
      const innerW = BOARD - PAD * 2;
      let y = top + (bottom - top) * 0.30;
      if (state.team && (state.team.img || state.team.name)) {
        const size = 176;
        drawCrestTile(ctx, cx - size / 2, y, size, state.team.img, state.team.name);
        y += size + 40;
      }
      const headline = state.headline || "";
      const headSize = headline.length > 60 ? 42 : headline.length > 36 ? 52 : 64;
      ctx.font = `900 ${headSize}px ${FONT_STACK}`;
      ctx.fillStyle = TOKENS.ink;
      ctx.textAlign = "center";
      ctx.textBaseline = "alphabetic";
      const headLines = wrapLines(ctx, headline, innerW - 40, 3);
      headLines.forEach((line) => { y += headSize * 1.05; ctx.fillText(line, cx, y); });
      if (state.subtext) {
        y += 34;
        ctx.font = `500 30px ${FONT_STACK}`;
        ctx.fillStyle = TOKENS.muted;
        const subLines = wrapLines(ctx, state.subtext, innerW - 80, 4);
        subLines.forEach((line) => { y += 40; ctx.fillText(line, cx, y); });
      }
    },
  },
};

// ── Draw + export ────────────────────────────────────────────────────────

/** Render one template into `canvas`, sized to a 2x backing store for crisp
 *  text (the same reasoning as social/render.py's DEVICE_SCALE=2, minus the
 *  screenshot step — canvas text at 2x is already native resolution). Caller
 *  is responsible for having called ensureFonts() first. */
export function drawBoard(canvas, templateId, state) {
  const template = TEMPLATES[templateId];
  if (!template) throw new Error(`unknown graphics template: ${templateId}`);
  canvas.width = BOARD * DEVICE_SCALE;
  canvas.height = BOARD * DEVICE_SCALE;
  const ctx = canvas.getContext("2d");
  ctx.save();
  ctx.scale(DEVICE_SCALE, DEVICE_SCALE);
  fillGround(ctx);
  template.draw(ctx, state);
  ctx.restore();
}

/** Downscale the 2x backing store to the final 1080x1080 PNG — the canvas
 *  equivalent of social/render.py's _downscale(), landing on the same pixel
 *  size as every social/-produced card. */
export function exportPng(canvas) {
  return new Promise((resolve, reject) => {
    const out = document.createElement("canvas");
    out.width = BOARD;
    out.height = BOARD;
    const octx = out.getContext("2d");
    octx.imageSmoothingEnabled = true;
    octx.imageSmoothingQuality = "high";
    octx.drawImage(canvas, 0, 0, canvas.width, canvas.height, 0, 0, BOARD, BOARD);
    out.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("PNG export failed"))),
      "image/png");
  });
}
