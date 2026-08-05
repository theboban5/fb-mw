/* Everyleague site search — predictive combobox + the /search/ results page.
 *
 * Loaded on every page from render.search_widget(). The markup it enhances is
 * already a working <form method="get" action="search/">, so Enter and the
 * phone keyboard's Go button reach the results page with this file absent,
 * blocked or still downloading. Everything here is an upgrade on top of that,
 * and every failure path falls back to it.
 *
 * The index (search-index.json, ~550 records) is fetched on first interaction,
 * never on page load — most visits never touch search, and this site's traffic
 * is largely mobile data in Malawi.
 *
 * Row shape, mirrored from src/search.py:
 *     [type, name, url, meta, weight, extra]
 *
 * At ~550 records a full scan is well under a millisecond, so matching runs
 * synchronously on every keystroke with no debounce. Revisit that (and add a
 * first-letter bucket map) only if the index ever passes ~5,000 records.
 */
(function () {
  "use strict";

  if (window.__elSearchInit) return;   // one widget per page, but be certain
  window.__elSearchInit = true;

  var root = document.querySelector("[data-search]");
  if (!root || !window.fetch || !window.JSON) return;   // leave the plain form

  var form = root.querySelector(".ss-form");
  var input = root.querySelector(".ss-input");
  var panel = root.querySelector(".ss-panel");
  var status = root.querySelector(".ss-status");
  var clearBtn = root.querySelector(".ss-clear");
  var resultsBox = document.querySelector("[data-search-results]");
  if (!form || !input || !panel) return;

  // On /search/ the page itself is the result list, so the dropdown stays shut
  // and every keystroke re-renders the groups instead.
  var PAGE_MODE = !!resultsBox;

  var INDEX_URL = root.getAttribute("data-search-index");
  var PREFIX = root.getAttribute("data-search-prefix") || "";
  var MAX_SUGGESTIONS = 8;
  var MAX_PER_GROUP = 50;

  var TYPE_LABELS = ["League", "National", "Club", "Team", "Player"];
  var GROUP_LABELS = ["Competitions", "National Team", "Clubs", "Teams", "Players"];

  var recs = null;        // precomputed records, once loaded
  var loading = false;
  var failed = false;
  var pending = null;     // query typed while the index was in flight
  var active = -1;        // highlighted suggestion, -1 = none
  var shown = [];         // records currently rendered in the panel

  /* ── Normalisation ──────────────────────────────────────────────────────
   * Apostrophes are stripped rather than turned into spaces: Malawian club
   * names carry them mid-word (Ngw'Angw'Azi, Balang'Ombre, M'mbelwa), and
   * "ngwangwazi" is what someone actually types. Everything else
   * non-alphanumeric collapses to a single space, so "FC", hyphens and "&"
   * stop mattering. Kept deliberately in step with tests/test_search.py.
   */
  function norm(s) {
    s = String(s == null ? "" : s).toLowerCase();
    if (s.normalize) s = s.normalize("NFD").replace(/[̀-ͯ]/g, "");
    return s.replace(/['’`]/g, "").replace(/[^a-z0-9]+/g, " ").trim();
  }

  /* ── Index loading ─────────────────────────────────────────────────────── */

  function prepare(payload) {
    var docs = (payload && payload.docs) || [];
    recs = [];
    for (var i = 0; i < docs.length; i++) {
      var d = docs[i];
      var n = norm(d[1]);
      var w = n ? n.split(" ") : [];
      var acronym = "";
      for (var j = 0; j < w.length; j++) acronym += w[j].charAt(0);
      var x = norm(d[3] + " " + (d[5] || ""));
      recs.push({
        type: d[0], name: d[1], url: d[2], meta: d[3], weight: d[4],
        n: n, w: w, a: acronym, x: x, xw: x ? x.split(" ") : []
      });
    }
  }

  function loadIndex(then) {
    if (recs) { if (then) then(); return; }
    if (failed || loading) return;
    loading = true;

    // sessionStorage removes even the revalidation round-trip when moving
    // between pages in one visit — worth it on a high-latency link. Safari in
    // private mode throws on setItem, hence the try/catch on both sides.
    var cacheKey = "el-search:" + INDEX_URL;
    try {
      var hit = window.sessionStorage.getItem(cacheKey);
      if (hit) {
        prepare(JSON.parse(hit));
        loading = false;
        if (then) then();
        return;
      }
    } catch (e) { /* no session storage; fall through to the network */ }

    fetch(INDEX_URL, { credentials: "omit" })
      .then(function (r) {
        if (!r.ok) throw new Error(r.status);
        return r.text();
      })
      .then(function (text) {
        prepare(JSON.parse(text));
        try {
          // Drop older versions first so a long-lived tab can't accumulate them.
          for (var k = window.sessionStorage.length - 1; k >= 0; k--) {
            var key = window.sessionStorage.key(k);
            if (key && key.indexOf("el-search:") === 0 && key !== cacheKey) {
              window.sessionStorage.removeItem(key);
            }
          }
          window.sessionStorage.setItem(cacheKey, text);
        } catch (e) { /* quota or private mode — the index still works */ }
        loading = false;
        if (then) then();
        if (pending !== null) { var q = pending; pending = null; update(q); }
      })
      .catch(function () {
        // Offline or a bad deploy: stop trying, close the panel, and leave the
        // form to do what it always could. Never strand a dead input box.
        loading = false;
        failed = true;
        pending = null;
        close();
        say("");
      });
  }

  /* ── Ranking ────────────────────────────────────────────────────────────
   * Lower tier wins. A record that matches nothing is dropped.
   */
  function tier(r, q, qw) {
    if (r.n === q) return 0;
    if (r.n.indexOf(q) === 0) return 1;
    if (startsAny(r.w, q)) return 2;
    // A single letter must not tip 200 substring matches onto a phone screen,
    // so the looser tiers only open up from two characters.
    if (q.length < 2) return -1;
    if (r.a.indexOf(q) === 0) return 3;
    if (startsAny(r.xw, q)) return 4;
    if (qw.length > 1 && everyTokenHitsAWord(r.w, qw)) return 5;
    if (r.n.indexOf(q) > 0) return 6;
    if (r.x.indexOf(q) >= 0) return 7;
    return -1;
  }

  function startsAny(words, q) {
    for (var i = 0; i < words.length; i++) {
      if (words[i].indexOf(q) === 0) return true;
    }
    return false;
  }

  // "big bull" and "bull nyasa" both find Nyasa Big Bullets: every query token
  // has to prefix some word, in any order.
  function everyTokenHitsAWord(words, qw) {
    for (var i = 0; i < qw.length; i++) {
      if (!startsAny(words, qw[i])) return false;
    }
    return true;
  }

  function search(raw, limit) {
    var q = norm(raw);
    if (!q || !recs) return [];
    var qw = q.split(" ");
    var hits = [];
    for (var i = 0; i < recs.length; i++) {
      var t = tier(recs[i], q, qw);
      if (t >= 0) hits.push({ t: t, r: recs[i] });
    }
    hits.sort(function (a, b) {
      if (a.t !== b.t) return a.t - b.t;                    // match quality
      if (a.r.weight !== b.r.weight) return b.r.weight - a.r.weight;
      if (a.r.name.length !== b.r.name.length) {            // Bullets < Bullets Reserve
        return a.r.name.length - b.r.name.length;
      }
      return a.r.name < b.r.name ? -1 : a.r.name > b.r.name ? 1 : 0;
    });
    return limit ? hits.slice(0, limit) : hits;
  }

  /* ── Rendering ──────────────────────────────────────────────────────────
   * Names come from a Google Sheet, so every field goes in through
   * textContent and <mark> is a real element — never innerHTML with data.
   */
  function nameNode(name, raw) {
    var span = document.createElement("span");
    span.className = "ss-name";
    // Highlight only a literal case-insensitive hit in the displayed string.
    // Accent- or apostrophe-folded matches simply render unhighlighted rather
    // than risk marking the wrong span.
    var at = raw ? name.toLowerCase().indexOf(raw.toLowerCase().trim()) : -1;
    if (at < 0 || !raw.trim()) {
      span.textContent = name;
      return span;
    }
    var end = at + raw.trim().length;
    span.appendChild(document.createTextNode(name.slice(0, at)));
    var mark = document.createElement("mark");
    mark.textContent = name.slice(at, end);
    span.appendChild(mark);
    span.appendChild(document.createTextNode(name.slice(end)));
    return span;
  }

  function optionNode(rec, raw, id) {
    var a = document.createElement("a");
    a.className = "ss-opt";
    a.href = PREFIX + rec.url;
    if (id) { a.id = id; a.setAttribute("role", "option"); a.setAttribute("aria-selected", "false"); }

    var main = document.createElement("span");
    main.className = "ss-main";
    main.appendChild(nameNode(rec.name, raw));
    if (rec.meta) {
      var meta = document.createElement("span");
      meta.className = "ss-meta";
      meta.textContent = rec.meta;
      main.appendChild(meta);
    }
    a.appendChild(main);

    var badge = document.createElement("span");
    badge.className = "ss-badge ss-badge-" + rec.type;
    badge.textContent = TYPE_LABELS[rec.type] || "";
    a.appendChild(badge);

    // role="option" wins over the link role for screen readers, but keeping a
    // real href preserves long-press / middle-click "open in new tab", which
    // people do use on results lists.
    a.addEventListener("click", function () { track(input.value, rec.type); });
    return a;
  }

  function say(msg) { if (status) status.textContent = msg; }

  function render(raw) {
    var hits = search(raw, MAX_SUGGESTIONS + 1);
    var overflow = hits.length > MAX_SUGGESTIONS;
    if (overflow) hits = hits.slice(0, MAX_SUGGESTIONS);

    panel.textContent = "";
    shown = [];
    active = -1;

    if (!hits.length) {
      var none = document.createElement("p");
      none.className = "ss-none";
      none.textContent = 'No matches for "' + raw.trim() + '"';
      panel.appendChild(none);
      open();
      say("No results");
      return;
    }

    for (var i = 0; i < hits.length; i++) {
      var rec = hits[i].r;
      shown.push(rec);
      panel.appendChild(optionNode(rec, raw, "ss-o" + i));
    }
    if (overflow) {
      var more = document.createElement("a");
      more.className = "ss-more";
      more.href = form.getAttribute("action") + "?q=" + encodeURIComponent(raw.trim());
      more.textContent = "See all results for “" + raw.trim() + "” →";
      panel.appendChild(more);
    }
    open();
    say(hits.length + (hits.length === 1 ? " result" : " results"));
  }

  /* ── Panel open/close and keyboard ─────────────────────────────────────── */

  function open() {
    panel.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  function close() {
    panel.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  }

  function highlight(i) {
    var opts = panel.querySelectorAll(".ss-opt");
    if (!opts.length) return;
    if (active >= 0 && opts[active]) opts[active].setAttribute("aria-selected", "false");
    active = (i + opts.length) % opts.length;
    var el = opts[active];
    el.setAttribute("aria-selected", "true");
    // Focus stays in the input throughout: moving it would dismiss the
    // on-screen keyboard mid-search.
    input.setAttribute("aria-activedescendant", el.id);
    el.scrollIntoView({ block: "nearest" });
  }

  function update(raw) {
    if (clearBtn) clearBtn.hidden = !raw;
    if (!raw.trim()) { close(); say(""); return; }
    if (!recs) {
      pending = raw;
      loadIndex();
      if (!failed) {
        panel.textContent = "";
        var wait = document.createElement("p");
        wait.className = "ss-none";
        wait.textContent = "Searching…";
        panel.appendChild(wait);
        open();
      }
      return;
    }
    render(raw);
  }

  input.addEventListener("input", function () {
    if (clearBtn) clearBtn.hidden = !input.value;
    if (PAGE_MODE) { close(); syncUrl(); renderPage(input.value); return; }
    update(input.value);
  });

  // Keep the address bar in step with what is on screen, so the URL stays
  // shareable and Back still works — replaceState, not pushState, or every
  // keystroke would become a history entry.
  function syncUrl() {
    var v = input.value.trim();
    try {
      window.history.replaceState(
        null, "",
        window.location.pathname + (v ? "?q=" + encodeURIComponent(v) : ""));
    } catch (e) { /* file:// or a browser without pushState — cosmetic only */ }
  }

  /* The descriptive placeholder only goes in once the input is wide enough to
   * show it whole — on a narrow phone it was being cut mid-word. The short
   * form is what the HTML ships, so this only ever upgrades. Re-evaluated on
   * change so rotating the phone doesn't leave a clipped label behind. */
  (function () {
    var long = input.getAttribute("data-ss-placeholder");
    var min = parseInt(input.getAttribute("data-ss-placeholder-min"), 10);
    if (!long || !min || !window.matchMedia) return;
    var short = input.getAttribute("placeholder");
    var mq = window.matchMedia("(min-width: " + min + "px)");
    function apply() { input.placeholder = mq.matches ? long : short; }
    apply();
    if (mq.addEventListener) mq.addEventListener("change", apply);
    else if (mq.addListener) mq.addListener(apply);   // Safari < 14
  })();

  // Warm the index the moment there is intent, so the first keystroke already
  // has data to work with.
  input.addEventListener("focus", function () { loadIndex(); });
  root.addEventListener("pointerenter", function () { loadIndex(); });
  root.addEventListener("touchstart", function () { loadIndex(); }, { passive: true });

  input.addEventListener("keydown", function (ev) {
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      if (PAGE_MODE) return;          // no dropdown here; let the page scroll
      if (panel.hidden) { update(input.value); return; }
      ev.preventDefault();
      highlight(active + (ev.key === "ArrowDown" ? 1 : -1));
      return;
    }
    if (ev.key === "Enter") {
      if (active >= 0 && shown[active]) {
        ev.preventDefault();
        track(input.value, shown[active].type);
        window.location.href = PREFIX + shown[active].url;
      }
      // Otherwise let the form submit: /search/?q=… , which works with or
      // without this script.
      return;
    }
    if (ev.key === "Escape") {
      if (!panel.hidden) { close(); }
      else { input.value = ""; update(""); input.blur(); }
    }
  });

  // mousedown+preventDefault so the input's blur doesn't tear the panel down
  // before the click lands — the same fix static/admin/admin.js uses.
  panel.addEventListener("mousedown", function (ev) { ev.preventDefault(); });

  input.addEventListener("blur", function () {
    window.setTimeout(function () {
      if (!root.contains(document.activeElement)) { trackAbandon(); close(); }
    }, 150);
  });

  if (clearBtn) {
    clearBtn.addEventListener("click", function () {
      input.value = "";
      update("");
      input.focus();
    });
  }

  // "/" focuses search from anywhere, unless the user is already typing.
  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "/" || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    var t = ev.target;
    var tag = t && t.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" ||
        (t && t.isContentEditable)) return;
    ev.preventDefault();
    input.focus();
  });

  /* ── Analytics ──────────────────────────────────────────────────────────
   * GA4's recommended `search` event, on commit only — never per keystroke.
   */
  var lastTracked = "";

  function track(term, type) {
    term = String(term || "").trim();
    if (typeof window.gtag !== "function" || term.length < 2) return;
    if (term === lastTracked) return;
    lastTracked = term;
    window.gtag("event", "search", {
      search_term: term.toLowerCase(),
      selected_type: type === undefined ? "none" : TYPE_LABELS[type]
    });
  }

  // A query typed and then abandoned is the interesting signal: it is usually a
  // team or player the site does not cover yet.
  function trackAbandon() {
    if (input.value.trim().length >= 2) track(input.value, undefined);
  }

  /* ── /search/ results page ─────────────────────────────────────────────── */

  if (resultsBox) {
    var q = "";
    var m = /[?&]q=([^&]*)/.exec(window.location.search);
    if (m) { try { q = decodeURIComponent(m[1].replace(/\+/g, " ")); } catch (e) { q = m[1]; } }

    input.value = q;
    if (clearBtn) clearBtn.hidden = !q;

    // Already on the results page: submitting must not reload it.
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      syncUrl();
      renderPage(input.value);
      input.blur();          // let the phone keyboard out of the way
    });

    loadIndex(function () { renderPage(input.value); });
    renderPage(q);           // shows the loading note until the index lands
  }

  function renderPage(raw) {
    resultsBox.textContent = "";
    if (!raw || !raw.trim()) {
      resultsBox.appendChild(note("Type above to search clubs, players and competitions."));
      return;
    }
    if (!recs) {
      resultsBox.appendChild(note(failed ? "Search is unavailable right now." : "Searching…"));
      return;
    }

    var hits = search(raw, 0);
    if (!hits.length) {
      resultsBox.appendChild(note('No matches for "' + raw.trim() + '".'));
      say("No results");
      return;
    }

    var byType = {};
    for (var i = 0; i < hits.length; i++) {
      (byType[hits[i].r.type] = byType[hits[i].r.type] || []).push(hits[i].r);
    }
    var total = 0;
    for (var t = 0; t < GROUP_LABELS.length; t++) {
      var group = byType[t];
      if (!group || !group.length) continue;
      var h = document.createElement("h3");
      h.className = "v2-sec-title";
      h.textContent = GROUP_LABELS[t] + " (" + group.length + ")";
      resultsBox.appendChild(h);

      var list = document.createElement("div");
      list.className = "ss-group";
      var capped = group.slice(0, MAX_PER_GROUP);
      for (var k = 0; k < capped.length; k++) {
        list.appendChild(optionNode(capped[k], raw, ""));
      }
      resultsBox.appendChild(list);
      total += capped.length;
    }
    say(total + (total === 1 ? " result" : " results"));
    track(raw, undefined);
  }

  function note(text) {
    var p = document.createElement("p");
    p.className = "v2-empty";
    p.textContent = text;
    return p;
  }
})();
