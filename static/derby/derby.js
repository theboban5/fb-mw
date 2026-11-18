/* Bring your friends to the Derby — the page (0072). The DOM half; rules.js
 * is the rest.
 *
 * The page is written by src/derby.py as three steps — 1 Join, 2 Get your
 * link, 3 Share it — with the build's phase line showing. This script moves
 * the step highlight as the reader moves, keeps what they typed, sends the
 * sign-up, and then puts them on step 3 with the WhatsApp button in front of
 * them. A reader who has a link and does not know to send it has done
 * nothing for anyone, so that hand-over is the part to get right.
 *
 * Two things live in localStorage, both per-phone conveniences and neither
 * trusted by the database: the code of the friend whose link brought this
 * reader (so it survives a detour round the site before joining), and this
 * reader's own code (so coming back shows their link, not the form).
 * Storage can be missing; every touch is in a try and the page works
 * without it, at the cost of typing the number again.
 */

import { facebookUrl, formProblem, inviteLine, linkFor, phaseAt, pointsView,
  refFromSearch, shareText, whatsappUrl } from "./rules.js";

const cfg = JSON.parse(document.getElementById("dy-config").textContent);
const REF_KEY = "dy-ref";
const ME_KEY = "dy-me";
const DRAFT_KEY = "dy-draft";
const DETAILS_KEY = "qz-details";   // the quiz's: a quiz entrant typed these once
const TIMEOUT_MS = 25000;

const form = document.querySelector(".dy-form");
const done = document.querySelector(".dy-done");
const invited = document.querySelector(".dy-invited");

function load(key) {
  try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (_) { return null; }
}
function save(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* fine */ }
}
function drop(key) {
  try { localStorage.removeItem(key); } catch (_) { /* fine */ }
}
function track(name, params) {
  try { if (typeof gtag === "function") gtag("event", name, params || {}); } catch (_) { /* fine */ }
}

const phase = () => phaseAt(Date.now(), cfg.opens, cfg.closes);

function showPhase() {
  const now = phase();
  for (const el of document.querySelectorAll("[data-dy-phase]")) {
    el.hidden = el.dataset.dyPhase !== now;
  }
}

/* The 1-2-3 strip under the prize: steps before `n` ticked, `n` lit. */
function showStep(n) {
  for (const li of document.querySelectorAll("[data-dy-step]")) {
    const step = Number(li.dataset.dyStep);
    li.classList.toggle("is-now", step === n);
    li.classList.toggle("is-done", step < n);
  }
}

function field(name) {
  return form.querySelector(`[name="${name}"]`);
}

function follows() {
  return [...form.querySelectorAll('input[name^="follow_"]:checked')].map((el) => el.value);
}

/* Each follow button wears its box's state: ✓ and filled once tapped. */
function showFollows() {
  for (const a of form.querySelectorAll("[data-dy-follow]")) {
    const box = field(`follow_${a.dataset.dyFollow}`);
    a.classList.toggle("is-on", !!(box && box.checked));
  }
}

function snapshot() {
  const values = {};
  for (const el of form.querySelectorAll("input[name]")) {
    if (el.name === "hp_field") continue;
    values[el.name] = el.type === "checkbox" ? el.checked : el.value;
  }
  return values;
}

function restore(values) {
  if (!values) return;
  for (const el of form.querySelectorAll("input[name]")) {
    if (!(el.name in values)) continue;
    if (el.type === "checkbox") el.checked = !!values[el.name];
    else if (!el.value) el.value = values[el.name];
  }
}

function setStatus(text, kind) {
  const el = form.querySelector(".qz-status");
  el.textContent = text;
  el.className = "qz-status" + (kind ? ` is-${kind}` : "");
}

function markInvalid(el, bad) {
  if (!el) return;
  el.classList.toggle("is-missing", bad);
  if (bad) el.setAttribute("aria-invalid", "true");
  else el.removeAttribute("aria-invalid");
}

async function post(url, payload) {
  const ctl = typeof AbortController === "function" ? new AbortController() : null;
  const timer = ctl ? setTimeout(() => ctl.abort(), TIMEOUT_MS) : null;
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", apikey: cfg.key },
      body: JSON.stringify(payload),
      signal: ctl ? ctl.signal : undefined,
    });
    if (!res.ok) return { ok: false, status: "server" };
    return await res.json();
  } finally {
    if (timer) clearTimeout(timer);
  }
}

// ── Arriving through a friend's link ─────────────────────────────────────────

/* "Chikondi invited you": the generic line is in the page already, and the
 * name replaces it when the lookup answers (0073). A failed lookup leaves the
 * generic line, which is still true. */
async function showInvite(code) {
  invited.hidden = false;
  if (!cfg.inviter) return;
  try {
    const name = await post(cfg.inviter, { p_code: code });
    if (typeof name === "string" && name) {
      invited.querySelector(".dy-invited-text").textContent = inviteLine(name);
    }
  } catch (_) { /* the generic line stands */ }
}

// ── Steps 2 and 3: the link ──────────────────────────────────────────────────

function showPoints(counts) {
  const view = pointsView(counts, phase());
  const box = done.querySelector(".dy-points");
  box.querySelector(".dy-points-n span").textContent = view.n;
  box.querySelector(".dy-points-n").lastChild.textContent = view.n === 1 ? " point" : " points";
  box.querySelector(".dy-points-note").textContent = view.note;
  box.hidden = false;
}

async function refreshPoints(code) {
  if (!cfg.count) return;
  try {
    const out = await post(cfg.count, { p_campaign: cfg.campaign, p_code: code });
    if (out && typeof out.counted === "number") showPoints(out);
  } catch (_) { /* the link is what matters; the points can wait */ }
}

/* Whose link this is. The page remembers a sign-up per browser, so a phone
 * that joined and then opens a FRIEND's link shows its own link, not the
 * form: it cannot be referred again. Without a sentence saying so, that
 * reads as the friend's link being broken. */
function whoLine(me, incoming) {
  const as = me.name ? `Joined as ${me.name} on this phone.` : "This phone has joined already.";
  if (incoming && incoming !== me.code) {
    return `${as} You opened a friend's link, but this phone already has its own, so it is shown here. Someone else using this phone? Tap "Not you?" below.`;
  }
  return as;
}

function showDone(me, counts, { jump = false } = {}) {
  const short = linkFor(cfg.page, me.code);
  const tagged = (source) => linkFor(cfg.page, me.code, source, cfg.utm);
  const words = { cash: cfg.cash, home: cfg.home, away: cfg.away };

  // Shown without the scheme, so the code at the end fits a phone's width;
  // Copy takes the whole link.
  const box = done.querySelector(".dy-link-box");
  box.textContent = short.replace(/^https?:\/\//, "");
  box.dataset.link = short;
  done.querySelector('[data-dy-share="whatsapp"]').href =
    whatsappUrl(shareText(words, tagged("whatsapp")));
  done.querySelector('[data-dy-share="facebook"]').href = facebookUrl(tagged("facebook"));

  const native = done.querySelector('[data-dy-share="native"]');
  if (navigator.share) native.hidden = false;
  native.onclick = () => {
    track("referral_share", { method: "native" });
    navigator.share({ title: document.title, text: shareText(words, ""),
      url: tagged("share") }).catch(() => {});
  };

  const who = done.querySelector(".dy-who");
  who.textContent = whoLine(me, incoming);
  who.hidden = false;

  form.hidden = true;
  invited.hidden = true;
  done.hidden = false;
  showStep(3);
  if (counts) showPoints(counts);
  else refreshPoints(me.code);

  // Straight after joining, the reader is at the bottom of a form that just
  // vanished: take them to "You're in!" so the next thing they see is the
  // button to send their link.
  if (jump) {
    done.scrollIntoView({ behavior: "smooth", block: "start" });
    try { done.focus({ preventScroll: true }); } catch (_) { /* fine */ }
  }
}

function wireDone() {
  for (const a of done.querySelectorAll("a[data-dy-share]")) {
    a.addEventListener("click", () => track("referral_share", { method: a.dataset.dyShare }));
  }
  const copy = done.querySelector(".dy-copy");
  const box = done.querySelector(".dy-link-box");
  // Without a clipboard (an old browser, or no https), select the text so a
  // long-press copies it.
  const selectBox = () => {
    try {
      const range = document.createRange();
      range.selectNodeContents(box);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    } catch (_) { /* fine */ }
  };
  copy.addEventListener("click", () => {
    track("referral_share", { method: "copy" });
    const ok = () => { copy.textContent = "Copied!"; setTimeout(() => { copy.textContent = "Copy"; }, 2000); };
    if (navigator.clipboard) navigator.clipboard.writeText(box.dataset.link).then(ok, selectBox);
    else selectBox();
  });
  box.addEventListener("click", selectBox);
  done.querySelector(".dy-reset").addEventListener("click", () => {
    drop(ME_KEY);
    done.hidden = true;
    form.hidden = false;
    showStep(1);
    setStatus("");
    const ref = load(REF_KEY);
    if (ref) showInvite(ref);
    field("phone").focus();
  });
}

// ── Step 1: the form ─────────────────────────────────────────────────────────

function wireForm() {
  restore(load(DRAFT_KEY));
  showFollows();
  const details = load(DETAILS_KEY);
  if (details) {
    for (const name of ["name", "phone", "email"]) {
      const el = field(name);
      if (el && !el.value && details[name]) el.value = details[name];
    }
  }

  // A tap opens the platform in a new tab and counts as "I follow there".
  // Already following is the same tap: the tab opens, they come back.
  for (const a of form.querySelectorAll("[data-dy-follow]")) {
    a.addEventListener("click", () => {
      const box = field(`follow_${a.dataset.dyFollow}`);
      if (box) box.checked = true;
      showFollows();
      save(DRAFT_KEY, snapshot());
      markInvalid(form.querySelector("[data-dy-follows]"), false);
      if (form.querySelector(".qz-status.is-error")) setStatus("");
      track("derby_follow", { platform: a.dataset.dyFollow });
    });
  }

  form.addEventListener("input", () => save(DRAFT_KEY, snapshot()));

  let busy = false;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (busy) return;

    const values = { follows: follows(), name: field("name").value,
      phone: field("phone").value, email: field("email").value };
    const targets = {
      follows: form.querySelector("[data-dy-follows]"), name: field("name"),
      phone: field("phone"), email: field("email"),
    };
    const problem = formProblem(values);
    for (const [key, el] of Object.entries(targets)) markInvalid(el, !!problem && problem[0] === key);
    if (problem) {
      setStatus(problem[1], "error");
      targets[problem[0]].scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    if (!cfg.signup) {
      setStatus("Joining can't be sent from this copy of the site.", "error");
      return;
    }

    busy = true;
    const button = form.querySelector(".qz-submit");
    button.disabled = true;
    setStatus("Making your link…");
    const ref = load(REF_KEY) || "";
    try {
      const out = await post(cfg.signup, {
        p_name: values.name,
        p_phone: values.phone,
        p_email: values.email,
        p_ref: ref,
        p_follows: values.follows,
        p_campaign: cfg.campaign,
        p_website: field("hp_field").value,
      });
      if (out && out.ok && out.code) {
        const me = { code: out.code, name: values.name.trim() };
        save(ME_KEY, me);
        drop(DRAFT_KEY);
        save(DETAILS_KEY, { name: values.name, phone: values.phone, email: values.email });
        track("derby_signup", { referred: !!ref, status: out.status });
        showDone(me, out, { jump: true });
        if (out.status === "already" && out.message) {
          done.querySelector(".dy-done-in").textContent = `✓ ${out.message}`;
        }
      } else if (out && out.message) {
        setStatus(out.message, "error");
      } else {
        setStatus("Something went wrong on our side. What you typed is saved on this phone, so try again in a minute.", "error");
      }
    } catch (_) {
      setStatus("We couldn't reach EveryLeague. Check your connection and tap the button again. What you typed is saved on this phone.", "error");
    } finally {
      busy = false;
      button.disabled = false;
    }
  });
}

// The rules fold away under "Full rules"; a link to them opens the fold.
for (const a of document.querySelectorAll('a[href="#rules"]')) {
  a.addEventListener("click", () => {
    const rules = document.getElementById("rules");
    if (rules) rules.open = true;
  });
}

// ── Start ────────────────────────────────────────────────────────────────────

const me = load(ME_KEY);
const incoming = refFromSearch(location.search);
// Your own link opened on your own phone is not a referral.
if (incoming && !(me && me.code === incoming)) save(REF_KEY, incoming);

showPhase();
wireForm();
wireDone();
if (me && me.code) {
  showDone(me, null);
} else {
  showStep(1);
  const ref = load(REF_KEY);
  if (ref) showInvite(ref);
}
// A page left open across the start or the close catches up within a minute.
setInterval(showPhase, 60000);
