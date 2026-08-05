/* The date picker on the /matches/ pages.
 *
 * Progressive enhancement, the same deal as the matchday pager: the button
 * ships hidden and this file reveals it. With JS off the three chips still
 * step a day at a time, which is all the arrows this replaced ever did — the
 * calendar exists so nobody has to tap "previous day" fourteen times.
 *
 * Every cell is a real <a href="YYYY-MM-DD.html">, so long-press and
 * middle-click open a date in a new tab like any other link on the site.
 *
 * The data comes from the <script type="application/json"> the page inlines
 * beside the button (see matches_page._cal_data): `win` is the contiguous
 * window of days that have a page, `match` the dates that have football. A
 * date is reachable if it is in the window or in that list — the same rule
 * matches_page.page_dates uses to decide what to write, so a cell is a link
 * exactly when the page behind it exists.
 */
(function () {
  var root = document.querySelector('[data-day-cal-root]');
  if (!root) return;
  var btn = root.querySelector('[data-day-cal-btn]');
  var dataEl = root.querySelector('[data-day-cal]');
  if (!btn || !dataEl) return;

  var cfg;
  try {
    cfg = JSON.parse(dataEl.textContent);
  } catch (e) {
    return;                      // no data, no picker — the chips still work
  }

  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                'August', 'September', 'October', 'November', 'December'];
  // Monday-first, matching how a fixture list is read.
  var DOW = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];

  var hasMatch = Object.create(null);
  (cfg.match || []).forEach(function (d) { hasMatch[d] = true; });

  function pageFor(d) {
    if ((d >= cfg.win[0] && d <= cfg.win[1]) || hasMatch[d]) return d + '.html';
    return '';
  }

  // How far the month arrows may travel: the earliest and latest date that
  // has a page at all.
  var bounds = (cfg.match || []).concat(cfg.win).sort();
  var minD = bounds[0] || cfg.today;
  var maxD = bounds[bounds.length - 1] || cfg.today;

  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function iso(y, m, d) { return y + '-' + pad(m + 1) + '-' + pad(d); }
  function monthNum(s) {
    var p = s.split('-');
    return +p[0] * 12 + (+p[1] - 1);
  }

  // UTC throughout: these are calendar dates, and local-time arithmetic would
  // slide a day either side of a DST boundary.
  function firstWeekday(y, m) {
    return (new Date(Date.UTC(y, m, 1)).getUTCDay() + 6) % 7;
  }
  function daysIn(y, m) {
    return new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
  }

  var panel = null;
  var view = 0;                  // year * 12 + month currently displayed

  function render() {
    var y = Math.floor(view / 12), m = view % 12;
    var out = [];

    out.push('<div class="dc-head">');
    out.push('<button type="button" class="dc-nav" data-dc-step="-1"' +
             (view <= monthNum(minD) ? ' disabled' : '') +
             ' aria-label="Previous month">&lsaquo;</button>');
    out.push('<span class="dc-title" aria-live="polite">' + MONTHS[m] + ' ' + y +
             '</span>');
    out.push('<button type="button" class="dc-nav" data-dc-step="1"' +
             (view >= monthNum(maxD) ? ' disabled' : '') +
             ' aria-label="Next month">&rsaquo;</button>');
    out.push('</div>');

    out.push('<div class="dc-grid">');
    DOW.forEach(function (d) {
      out.push('<span class="dc-dow" aria-hidden="true">' + d + '</span>');
    });
    var blanks = firstWeekday(y, m);
    for (var b = 0; b < blanks; b++) out.push('<span class="dc-pad"></span>');

    var n = daysIn(y, m);
    for (var d = 1; d <= n; d++) {
      var s = iso(y, m, d);
      var cls = 'dc-day';
      if (hasMatch[s]) cls += ' has-matches';
      if (s === cfg.today) cls += ' is-today';
      if (s === cfg.sel) cls += ' is-sel';
      var href = pageFor(s);
      if (href) {
        out.push('<a class="' + cls + '" href="' + href + '"' +
                 (s === cfg.sel ? ' aria-current="date"' : '') + '>' + d + '</a>');
      } else {
        out.push('<span class="' + cls + ' is-off">' + d + '</span>');
      }
    }
    out.push('</div>');
    out.push('<a class="dc-today" href="' + cfg.today + '.html">Jump to today</a>');
    panel.innerHTML = out.join('');
  }

  function ensurePanel() {
    if (panel) return;
    panel = document.createElement('div');
    panel.className = 'day-cal-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'Pick a date');
    panel.hidden = true;
    root.appendChild(panel);
    panel.addEventListener('click', function (e) {
      var step = e.target.closest('[data-dc-step]');
      if (!step) return;
      view += +step.dataset.dcStep;
      render();
      // Keep the keyboard on the arrow the user just pressed.
      var again = panel.querySelector('[data-dc-step="' + step.dataset.dcStep + '"]');
      if (again && !again.disabled) again.focus();
    });
  }

  function open() {
    ensurePanel();
    view = monthNum(cfg.sel);
    render();
    panel.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
  }

  function close(focus) {
    if (panel) panel.hidden = true;
    btn.setAttribute('aria-expanded', 'false');
    if (focus) btn.focus();
  }

  btn.addEventListener('click', function () {
    if (panel && !panel.hidden) close(false);
    else open();
  });
  document.addEventListener('click', function (e) {
    if (panel && !panel.hidden && !root.contains(e.target)) close(false);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && panel && !panel.hidden) close(true);
  });

  btn.hidden = false;
})();
