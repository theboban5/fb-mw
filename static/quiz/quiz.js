/* Know Your Flames — the quiz page (0067). The DOM half; rules.js is the rest.
 *
 * The page is written by src/quiz.py with every round and every closed-state
 * message already in it, and the round the BUILD thought was current already
 * showing. This script only re-decides that on the reader's clock, keeps what
 * they typed, and sends the entry.
 *
 * NOTHING TYPED IS EVER LOST, the portal's rule: answers and details are kept
 * in localStorage as they are typed, so a hint link followed in the same tab,
 * a reload or a failed send all come back to a filled-in form. Storage can be
 * missing (private mode, a full phone); every touch of it is in a try and the
 * page works without it.
 */

import { emailOk, missingAnswers, normalisePhone, phaseAt, scoreValue } from "./rules.js";

const cfg = JSON.parse(document.getElementById("qz-config").textContent);
const DETAILS_KEY = "qz-details";
const TIMEOUT_MS = 25000;

function load(key) {
  try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (_) { return null; }
}
function save(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* fine */ }
}
function drop(key) {
  try { localStorage.removeItem(key); } catch (_) { /* fine */ }
}

function showPhase() {
  const phase = phaseAt(Date.now(), cfg.quizzes);
  for (const el of document.querySelectorAll("[data-qz-round]")) {
    el.hidden = !(phase.state === "open" && el.dataset.qzRound === phase.quiz.id);
  }
  for (const el of document.querySelectorAll("[data-qz-state]")) {
    el.hidden = !(phase.state !== "open" && el.dataset.qzState === phase.state);
  }
}

function questionsOf(form) {
  return [...form.querySelectorAll("[data-qz-q]")];
}

function answersOf(form) {
  const answers = {};
  for (const fs of questionsOf(form)) {
    const id = fs.dataset.qzQ;
    if (fs.dataset.kind === "score") {
      const [home, away] = fs.querySelectorAll("input");
      answers[id] = scoreValue(home.value, away.value);
    } else {
      const picked = fs.querySelector("input[type=radio]:checked");
      answers[id] = picked ? picked.value : "";
    }
  }
  return answers;
}

function field(form, name) {
  return form.querySelector(`[name="${name}"]`);
}

function snapshot(form) {
  const values = {};
  for (const el of form.querySelectorAll("input[name]")) {
    if (el.type === "radio") { if (el.checked) values[el.name] = el.value; }
    else if (el.type === "checkbox") values[el.name] = el.checked;
    else values[el.name] = el.value;
  }
  return values;
}

function restore(form, values) {
  if (!values) return;
  for (const el of form.querySelectorAll("input[name]")) {
    if (!(el.name in values)) continue;
    if (el.type === "radio") el.checked = values[el.name] === el.value;
    else if (el.type === "checkbox") el.checked = !!values[el.name];
    else if (!el.value) el.value = values[el.name];
  }
}

function setStatus(form, text, kind) {
  const el = form.querySelector(".qz-status");
  el.textContent = text;
  el.className = "qz-status" + (kind ? ` is-${kind}` : "");
}

function markInvalid(el, bad) {
  el.classList.toggle("is-missing", bad);
  if (bad) el.setAttribute("aria-invalid", "true");
  else el.removeAttribute("aria-invalid");
}

function finish(round, quizId, message) {
  round.querySelector(".qz-form").hidden = true;
  const done = round.querySelector(".qz-done");
  if (message) done.querySelector(".qz-done-note").textContent = message;
  done.hidden = false;
  save(`qz-entered:${quizId}`, true);
  drop(`qz-draft:${quizId}`);
}

async function send(payload) {
  const ctl = typeof AbortController === "function" ? new AbortController() : null;
  const timer = ctl ? setTimeout(() => ctl.abort(), TIMEOUT_MS) : null;
  try {
    const res = await fetch(cfg.rpc, {
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

function wire(round) {
  const quizId = round.dataset.qzRound;
  const form = round.querySelector(".qz-form");
  const draftKey = `qz-draft:${quizId}`;

  if (load(`qz-entered:${quizId}`)) {
    finish(round, quizId, "You're already in the draw for this round from this phone.");
    return;
  }
  restore(form, load(draftKey));
  const details = load(DETAILS_KEY);
  if (details) {
    for (const name of ["name", "phone", "email"]) {
      const el = field(form, name);
      if (el && !el.value && details[name]) el.value = details[name];
    }
  }

  const keep = () => {
    save(draftKey, snapshot(form));
    save(DETAILS_KEY, {
      name: field(form, "name").value,
      phone: field(form, "phone").value,
      email: field(form, "email").value,
    });
  };
  form.addEventListener("input", keep);
  form.addEventListener("change", (e) => {
    keep();
    const fs = e.target.closest("[data-qz-q]");
    if (fs) markInvalid(fs, false);
  });

  let busy = false;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (busy) return;

    const answers = answersOf(form);
    const ids = questionsOf(form).map((fs) => fs.dataset.qzQ);
    const missing = missingAnswers(ids, answers);
    for (const fs of questionsOf(form)) markInvalid(fs, missing.includes(fs.dataset.qzQ));
    if (missing.length) {
      const first = form.querySelector(`[data-qz-q="${missing[0]}"]`);
      setStatus(form, `Answer question ${ids.indexOf(missing[0]) + 1} first.`, "error");
      first.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }

    const name = field(form, "name");
    const phone = field(form, "phone");
    const email = field(form, "email");
    const consent = field(form, "consent");
    const problems = [
      [name, name.value.trim().length < 2, "Please enter your name."],
      [phone, !normalisePhone(phone.value),
        "Please enter a phone number we can reach you on, like 0991 234 567."],
      [email, !emailOk(email.value), "That email address does not look right. Fix it, or leave it empty."],
      [consent.closest("label"), !consent.checked,
        "Please tick the box agreeing to the rules, so we can contact you if you win."],
    ];
    for (const [el, bad] of problems) markInvalid(el, bad);
    const problem = problems.find(([, bad]) => bad);
    if (problem) {
      setStatus(form, problem[2], "error");
      problem[0].scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }

    if (!cfg.rpc) {
      setStatus(form, "Entries can't be sent from this copy of the site.", "error");
      return;
    }

    busy = true;
    const button = form.querySelector(".qz-submit");
    button.disabled = true;
    setStatus(form, "Sending your entry…");
    try {
      const out = await send({
        p_quiz: quizId,
        p_name: name.value,
        p_phone: phone.value,
        p_email: email.value,
        p_answers: answers,
        p_consent: consent.checked,
        p_updates: field(form, "updates").checked,
        p_website: field(form, "hp_field").value,
      });
      if (out && out.ok) {
        try { if (typeof gtag === "function") gtag("event", "quiz_entry", { quiz_id: quizId }); } catch (_) { /* fine */ }
        finish(round, quizId, "");
      } else if (out && out.status === "duplicate") {
        finish(round, quizId, out.message);
      } else if (out && out.message) {
        setStatus(form, out.message, "error");
      } else {
        setStatus(form, "Something went wrong on our side. Your answers are saved on this phone, so try again in a minute.", "error");
      }
    } catch (_) {
      setStatus(form, "We couldn't reach EveryLeague. Check your connection and tap Enter again. Your answers are saved on this phone.", "error");
    } finally {
      busy = false;
      button.disabled = false;
    }
  });
}

function wireShare() {
  for (const btn of document.querySelectorAll("[data-qz-share]")) {
    const url = btn.dataset.qzShare;
    const text = btn.dataset.qzText || "";
    if (navigator.share) {
      btn.hidden = false;
      btn.addEventListener("click", () => {
        navigator.share({ title: document.title, text, url }).catch(() => {});
      });
    } else if (navigator.clipboard) {
      btn.hidden = false;
      btn.textContent = "Copy the link";
      btn.addEventListener("click", () => {
        navigator.clipboard.writeText(url).then(() => { btn.textContent = "Link copied"; }, () => {});
      });
    }
  }
}

showPhase();
for (const round of document.querySelectorAll("[data-qz-round]")) wire(round);
wireShare();
// A page left open across a close or an opening catches up within a minute.
setInterval(showPhase, 60000);
