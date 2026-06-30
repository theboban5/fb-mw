"""Rendering layer: turn computed data into static HTML pages."""

from datetime import datetime
from html import escape
import hashlib
import os
import shutil

NAV_ITEMS = (
    ("index.html", "Standings"),
    ("results.html", "Matches"),
    ("overview.html", "Season Overview"),
)

# Glyph + CSS modifier for the position-change arrow in the standings table.
# up = slanted top-right, down = slanted bottom-right, same = sideways.
_ARROW = {
    "up": ("v2-arrow-up", "&#x2197;"),
    "down": ("v2-arrow-down", "&#x2198;"),
    "same": ("v2-arrow-same", "&#x2192;"),
}

# Distinct line colours for the season-overview chart, assigned by final rank.
_PALETTE = (
    "#e6194B", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
    "#1abc9c", "#f032e6", "#9A6324", "#469990", "#808000",
    "#e67e22", "#000075", "#7f8c8d", "#800000", "#2c3e50",
    "#d81b60", "#00897b", "#c0392b", "#8e44ad", "#27ae60",
)


# Logos are stored full-size in static/logos/ so you can drop in whatever you
# find; the build downscales them so pages stay light. Caps are generous enough
# for retina at the largest on-page size (hero league logo ~88px, crests ~54px).
_LOGO_MAX_PX = {"clubs": 128, "leagues": 256}
_LOGO_MAX_DEFAULT = 256


def copy_static_tree(static_dir, dist):
    """Copy static_dir -> dist, downscaling raster logos to keep pages light.

    Everything except logos/ is copied verbatim. PNG logos are shrunk to a sane
    cap with Pillow; if Pillow isn't installed they're copied as-is (with a
    warning) so the build never hard-fails on a missing optional dependency.
    """
    shutil.copytree(
        static_dir, dist, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".DS_Store", "logos"),
    )
    _copy_logos(os.path.join(static_dir, "logos"), os.path.join(dist, "logos"))


def _copy_logos(src_root, dst_root):
    if not os.path.isdir(src_root):
        return
    try:
        from PIL import Image
    except ImportError:
        Image = None
        print(
            "WARNING: Pillow not installed; copying logos at full size "
            "(run 'pip install -r requirements.txt' to enable downscaling).",
        )

    for dirpath, _dirs, files in os.walk(src_root):
        rel = os.path.relpath(dirpath, src_root)
        out_dir = dst_root if rel == "." else os.path.join(dst_root, rel)
        os.makedirs(out_dir, exist_ok=True)
        subdir = "" if rel == "." else rel.split(os.sep)[0]
        max_px = _LOGO_MAX_PX.get(subdir, _LOGO_MAX_DEFAULT)
        for fname in files:
            if fname == ".DS_Store":
                continue
            src = os.path.join(dirpath, fname)
            dst = os.path.join(out_dir, fname)
            if Image is not None and fname.lower().endswith(".png"):
                _downscale_png(Image, src, dst, max_px)
            else:
                shutil.copy(src, dst)


def _downscale_png(Image, src, dst, max_px):
    """Write a copy of src capped at max_px on its longest side (never upscaled)."""
    with Image.open(src) as im:
        im = im.convert("RGBA")
        im.thumbnail((max_px, max_px), Image.LANCZOS)
        im.save(dst, "PNG", optimize=True)


def css_version(static_dir):
    """Short content hash of style.css, used to cache-bust the <link> on deploy.

    GitHub Pages serves CSS with a 10-minute cache, so without a versioned URL a
    returning visitor sees stale styles after every change. The hash changes only
    when the file changes, so caching still works between deploys.
    """
    try:
        with open(os.path.join(static_dir, "style.css"), "rb") as fh:
            return hashlib.md5(fh.read()).hexdigest()[:8]
    except OSError:
        return ""


def _logo_finder(static_dir, css_prefix, subdir):
    """Return f(key) -> web URL for static/logos/<subdir>/<key>.(svg|png), or None.

    Existence is checked against the source static dir at build time, so a club
    or league without an image simply renders nothing — no broken-image icons.
    SVG is preferred over PNG when both are present.
    """
    base = os.path.join(static_dir, "logos", subdir)

    def find(key):
        for ext in (".svg", ".png"):
            if os.path.exists(os.path.join(base, key + ext)):
                return f"{css_prefix}logos/{subdir}/{key}{ext}"
        return None

    return find


def _crest_img(url, extra_cls=""):
    """An <img> for a club crest, or empty string when there's no logo."""
    if not url:
        return ""
    cls = f"club-crest {extra_cls}".strip()
    return f'<img class="{cls}" src="{escape(url)}" alt="" loading="lazy">'


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _format_date(iso):
    dt = datetime.strptime(iso, "%Y-%m-%d")
    # Avoid platform-specific %-d so it builds on Linux, macOS and Windows.
    return f"{dt.day} {dt.strftime('%b %Y')}"


def _nav(active, items=NAV_ITEMS):
    out = []
    for href, label in items:
        cls = ' class="active"' if href == active else ""
        out.append(f'<a href="{href}"{cls}>{escape(label)}</a>')
    return "\n".join(out)


def _form_cell(results):
    """Render up to five W/D/L badges, oldest on the left, newest on the right."""
    if not results:
        return '<span class="v2-form-empty">&ndash;</span>'
    return "".join(
        f'<span class="v2-form-badge v2-form-{r.lower()}">{r}</span>' for r in results
    )


# ── Goalscorers (Super League only) ───────────────────────────────────────────
# Every helper below no-ops to "" when there are no goals, so the other leagues
# (which never supply goal data) render exactly as before.

def _scorers_block(match, goals_by_match, variant):
    """Two-column home/away scorer block under one match result, or "" if none.

    Home scorers sit left, away scorers right; each line is already minute-sorted
    by the caller. `variant` is "v1" (Designs B/C list) or "v2" (Design A table),
    which only changes the CSS class prefix.
    """
    if not goals_by_match or match.match_id is None:
        return ""
    gs = goals_by_match.get(match.match_id)
    if not gs:
        return ""
    home = [g for g in gs if g.team_code == match.home_code]
    away = [g for g in gs if g.team_code == match.away_code]
    if not home and not away:
        return ""
    pre = "v2-" if variant == "v2" else ""

    def column(side, side_cls):
        lines = "".join(
            f'<span class="{pre}ms-line">{escape(g.annotation)}</span>' for g in side
        )
        return f'<div class="{pre}ms-{side_cls}">{lines}</div>'

    return (
        f'<div class="{pre}match-scorers">'
        f'{column(home, "home")}{column(away, "away")}'
        "</div>"
    )


def _scorers_table(top_scorers, own_goal_total, teams, variant, crest=None, more_scorers=None):
    """The overall Top Scorers table (Rank/Player/Team/Goals + Own Goals row).

    Reuses the league-table classes (`standings` / `v2-standings`) so it inherits
    the page's existing table styling, with a few scr-* classes for alignment.

    `more_scorers` (list of (goals, num_players)) adds a summarised line per tier
    below the cutoff — e.g. "16 other scorers" with 1 in the goals column — so the
    reader knows how many further scorers there are without listing them.
    """
    crest = crest or (lambda code: None)
    if not top_scorers and not own_goal_total:
        return ""
    if variant == "v2":
        outer, table_cls, title_cls = "v2-table-outer", "v2-standings scorers-table", "v2-sec-title"
        head = ("#", "PLAYER", "TEAM", "GOALS")
    else:
        outer, table_cls, title_cls = "table-wrap", "standings scorers-table", "view-title"
        head = ("#", "Player", "Team", "Goals")

    body = []
    for t in top_scorers:
        team_name = teams[t.team_code].name if t.team_code in teams else t.team_code
        team_c = _crest_img(crest(t.team_code), "crest-pre")
        body.append(
            "<tr>"
            f'<td class="scr-rank">{t.rank}</td>'
            f'<td class="scr-player">{escape(t.player_name)}</td>'
            f'<td class="scr-team">{team_c}{escape(team_name)}</td>'
            f'<td class="scr-goals">{t.goals}</td>'
            "</tr>"
        )
    for g, num in (more_scorers or []):
        label = f"{num} other scorer{'s' if num != 1 else ''}"
        body.append(
            '<tr class="scr-more-row">'
            '<td class="scr-rank"></td>'
            f'<td class="scr-player">{label}</td>'
            '<td class="scr-team"></td>'
            f'<td class="scr-goals">{g}</td>'
            "</tr>"
        )
    if own_goal_total:
        body.append(
            '<tr class="scr-og-row">'
            '<td class="scr-rank"></td>'
            '<td class="scr-player">Own Goals</td>'
            '<td class="scr-team"></td>'
            f'<td class="scr-goals">{own_goal_total}</td>'
            "</tr>"
        )
    return (
        f'<h3 class="{title_cls}">Top Scorers</h3>'
        f'<div class="{outer}">'
        f'<table class="{table_cls}">'
        "<thead><tr>"
        f'<th class="scr-th-rank">{head[0]}</th>'
        f'<th class="scr-th-player">{head[1]}</th>'
        f'<th class="scr-th-team">{head[2]}</th>'
        f'<th class="scr-th-goals">{head[3]}</th>'
        "</tr></thead>"
        f"<tbody>{''.join(body)}</tbody>"
        "</table></div>"
    )


def _team_scorers_section(team_scorers, variant, crest=None):
    """Compact per-team top-3 cards below the overall table (own goals excluded)."""
    crest = crest or (lambda code: None)
    if not team_scorers:
        return ""
    title_cls = "v2-sec-title" if variant == "v2" else "view-title"
    cards = []
    for code, name, players in team_scorers:
        items = "".join(
            f'<li class="ts-item"><span class="ts-name">{escape(player)}</span>'
            f'<span class="ts-goals">{n}</span></li>'
            for player, n in players
        )
        team_c = _crest_img(crest(code), "ts-crest")
        cards.append(
            f'<div class="ts-card"><h4 class="ts-team">{team_c}{escape(name)}</h4>'
            f'<ul class="ts-list">{items}</ul></div>'
        )
    return (
        f'<h3 class="{title_cls}">Top Scorers by Team</h3>'
        f'<div class="ts-grid">{"".join(cards)}</div>'
    )


def render_standings(rows, season="", league_name="", total_goals=0, goals_per_game=0.0,
                     updated="", form=None, changes=None, crest=None, league_logo=""):
    form = form or {}
    changes = changes or {}
    crest = crest or (lambda code: None)
    # ── V1 / V3 content (hidden in V2) ─────────────────────
    v1 = [
        '<div class="v1-content">',
        '<h2 class="view-title">Standings</h2>',
        '<div class="table-wrap">',
        '<table class="standings">',
        "<thead><tr>"
        '<th class="pos">#</th><th class="team">Team</th>'
        "<th>P</th><th>W</th><th>D</th><th>L</th>"
        "<th>GF</th><th>GA</th><th>GD</th>"
        '<th class="pts">Pts</th></tr></thead>',
        "<tbody>",
    ]
    for i, s in enumerate(rows, start=1):
        gd = f"+{s.gd}" if s.gd > 0 else str(s.gd)
        c = _crest_img(crest(s.code), "crest-pre")
        v1.append(
            f'<tr class="pos-{i}">'
            f'<td class="pos">{i}</td>'
            f'<td class="team">{c}{escape(s.name)}</td>'
            f"<td>{s.played}</td><td>{s.won}</td><td>{s.drawn}</td><td>{s.lost}</td>"
            f"<td>{s.gf}</td><td>{s.ga}</td><td>{gd}</td>"
            f'<td class="pts">{s.points}</td>'
            "</tr>"
        )
    v1 += [
        "</tbody></table></div>",
        '<p class="legend">P played &middot; W won &middot; D drawn &middot; '
        "L lost &middot; GF/GA goals for/against &middot; GD goal difference &middot; "
        "Pts points</p>",
        "</div>",  # /v1-content
    ]

    # ── V2 content (fussball.de style, hidden in V1/V3) ────
    gpg_str = f"{goals_per_game:.1f}" if total_goals > 0 else "0.0"
    v2 = [
        '<div class="v2-content">',
        '<div class="v2-hero">',
        f'<img class="v2-hero-logo" src="{escape(league_logo)}" alt="">' if league_logo else "",
        f'<p class="v2-season">SEASON {escape(season)}</p>',
        f'<h2 class="v2-league-name">{escape(league_name.upper())}</h2>',
        '<div class="v2-stats">',
        f'<div class="v2-stat">'
        f'<span class="v2-stat-num">{total_goals}</span>'
        f'<span class="v2-stat-label">GOALS</span>'
        f'</div>',
        f'<div class="v2-stat">'
        f'<span class="v2-stat-num">{gpg_str}</span>'
        f'<span class="v2-stat-label">GOALS/GAME</span>'
        f'</div>',
        "</div>",  # /v2-stats
        f'<p class="v2-updated">{escape(updated)}</p>' if updated else "",
        "</div>",  # /v2-hero
        '<div class="v2-table-outer">',
        '<table class="v2-standings">',
        "<thead><tr>",
        '<th class="v2-th-pos">POS</th>',
        '<th class="v2-th-team">TEAM</th>',
        "<th>P</th><th>W</th><th>D</th><th>L</th>",
        "<th>GOALS</th><th>DIFF</th>",
        '<th class="v2-th-pts">PTS</th>',
        '<th class="v2-th-form">FORM</th>',
        "</tr></thead>",
        "<tbody>",
    ]
    for i, s in enumerate(rows, start=1):
        gd = f"+{s.gd}" if s.gd > 0 else str(s.gd)
        tor = f"{s.gf}:{s.ga}"
        leader_cls = " v2-pos-leader" if i == 1 else ""
        arrow_cls, arrow_glyph = _ARROW[changes.get(s.code, "same")]
        c = _crest_img(crest(s.code), "crest-pre")
        v2.append(
            f'<tr class="pos-{i}">'
            f'<td class="v2-pos{leader_cls}"><span class="v2-arrow {arrow_cls}">{arrow_glyph}</span> {i}.</td>'
            f'<td class="v2-team-name">{c}{escape(s.name)}</td>'
            f"<td>{s.played}</td><td>{s.won}</td><td>{s.drawn}</td><td>{s.lost}</td>"
            f'<td class="v2-tor">{tor}</td><td>{gd}</td>'
            f'<td class="v2-pts">{s.points}</td>'
            f'<td class="v2-form">{_form_cell(form.get(s.code, []))}</td>'
            "</tr>"
        )
    v2 += [
        "</tbody></table>",
        "</div>",  # /v2-table-outer
        "</div>",  # /v2-content
    ]

    return "\n".join(v1 + v2)


def render_results(matches, teams, season="", league_name="", crest=None, league_logo="",
                   goals_by_match=None, compact=False):
    # `compact` (the Super League, which shows scorers) drops the DATE/VENUE
    # columns to a centred caption so the v2 table fits the 660px column without
    # horizontal scroll — which is what lets the away scorers stay on screen.
    crest = crest or (lambda code: None)
    # Group every match by matchday — played results and not-yet-played fixtures
    # alike. A row with blank goals is a fixture (Match.played is False): it shows
    # kickoff info instead of a score, and standings/scorers already ignore it.
    by_day = {}
    for m in matches:
        by_day.setdefault(m.matchday, []).append(m)
    all_days = sorted(by_day)
    has_venue = any(m.stadium for m in matches)

    # The season's "live edge": the earliest matchday that still has an unplayed
    # fixture (a round in progress, or the next one up). The Matches tab opens
    # here. If every match has been played, fall back to the latest matchday.
    unplayed_days = [md for md in all_days if any(not m.played for m in by_day[md])]
    default_md = unplayed_days[0] if unplayed_days else (all_days[-1] if all_days else None)

    # ── V1 / V3 content (played results only; v1 is slated for removal) ────
    played_by_day = {}
    for m in matches:
        if m.played:
            played_by_day.setdefault(m.matchday, []).append(m)
    v1 = ['<div class="v1-content">', '<h2 class="view-title">Matches</h2>']
    if not played_by_day:
        v1.append('<p class="empty">No results have been recorded yet.</p>')
    else:
        for md in sorted(played_by_day, reverse=True):
            v1.append('<section class="matchday">')
            v1.append(f"<h3>Matchday {md}</h3>")
            v1.append('<ul class="matches">')
            for m in sorted(played_by_day[md], key=lambda x: (x.date, x.home_code)):
                home = escape(teams[m.home_code].name)
                away = escape(teams[m.away_code].name)
                home_c = _crest_img(crest(m.home_code), "crest-post")
                away_c = _crest_img(crest(m.away_code), "crest-pre")
                date_str = escape(_format_date(m.date))
                if m.stadium:
                    date_str += f" &middot; {escape(m.stadium)}"
                v1.append(
                    '<li class="match">'
                    f'<span class="home">{home}{home_c}</span>'
                    f'<span class="score">{m.home_goals}'
                    f'<span class="dash">&ndash;</span>{m.away_goals}</span>'
                    f'<span class="away">{away_c}{away}</span>'
                    f'<span class="date">{date_str}</span>'
                    f"{_scorers_block(m, goals_by_match, 'v1')}"
                    "</li>"
                )
            v1.append("</ul></section>")
    v1.append("</div>")  # /v1-content

    # ── V2 content (fussball.de style) ─────────────────────
    v2 = [
        '<div class="v2-content">',
        '<div class="v2-mini-banner">',
        f'<img class="v2-mini-logo" src="{escape(league_logo)}" alt="">' if league_logo else "",
        f'<p class="v2-season">SEASON {escape(season)}</p>',
        f'<h2 class="v2-mini-league">{escape(league_name.upper())}</h2>',
        "</div>",  # /v2-mini-banner
    ]
    if not by_day:
        v2.append('<div class="v2-results-outer">')
        v2.append('<p class="v2-empty">No matches have been scheduled yet.</p>')
        v2.append("</div>")  # /v2-results-outer
    else:
        # Matchday pager. Progressive enhancement: it ships hidden and JS reveals
        # it, hides all but the selected matchday, and wires up the chips/arrows.
        # With no JS the pager stays hidden and every matchday shows (scroll).
        # data-md-default is the live edge the tab opens on; chips for rounds with
        # no played match yet get a modifier class so they read as upcoming.
        v2.append(f'<div class="v2-md-pager" data-md-pager '
                  f'data-md-default="{default_md}" hidden>')
        v2.append('<button type="button" class="v2-md-nav" data-md-prev '
                  'aria-label="Earlier matchday">&lsaquo;</button>')
        v2.append('<div class="v2-md-strip" data-md-strip>')
        for md in all_days:
            up_cls = "" if any(m.played for m in by_day[md]) else " v2-md-chip-upcoming"
            v2.append(f'<button type="button" class="v2-md-chip{up_cls}" '
                      f'data-md-chip="{md}">{md}</button>')
        v2.append("</div>")  # /v2-md-strip
        v2.append('<button type="button" class="v2-md-nav" data-md-next '
                  'aria-label="Later matchday">&rsaquo;</button>')
        v2.append("</div>")  # /v2-md-pager
        v2.append('<div class="v2-results-outer">')
        colspan = 3 if compact else (5 if has_venue else 4)
        table_cls = "v2-results-table v2-results-compact" if compact else "v2-results-table"
        v2.append(f'<table class="{table_cls}">')
        v2.append("<thead><tr>")
        if not compact:
            v2.append('<th class="v2-res-th-date">DATE</th>')
        v2 += [
            '<th class="v2-res-th-home">HOME</th>',
            '<th class="v2-res-th-score">RESULT</th>',
            '<th class="v2-res-th-away">AWAY</th>',
        ]
        if not compact and has_venue:
            v2.append('<th class="v2-res-th-venue">VENUE</th>')
        v2 += ["</tr></thead>"]
        for md in sorted(by_day, reverse=True):
            day_matches = sorted(by_day[md], key=lambda x: (x.date, x.home_code))
            v2.append(f'<tbody class="v2-md-group" data-md="{md}">')
            v2.append(
                f'<tr class="v2-md-row"><td colspan="{colspan}">MATCHDAY {md}</td></tr>'
            )
            for j, m in enumerate(day_matches):
                alt_cls = " alt" if j % 2 == 1 else ""
                home = escape(teams[m.home_code].name)
                away = escape(teams[m.away_code].name)
                home_c = _crest_img(crest(m.home_code), "crest-post")
                away_c = _crest_img(crest(m.away_code), "crest-pre")
                # A fixture (no goals yet) shows "vs" in place of the score.
                if m.played:
                    score_cell = f'<td class="v2-res-score">{m.home_goals}:{m.away_goals}</td>'
                    fix_cls = ""
                else:
                    score_cell = '<td class="v2-res-score v2-res-vs">vs</td>'
                    fix_cls = " v2-res-row-fixture"
                date = escape(_format_date(m.date))
                if compact:
                    meta = date
                    if m.stadium:
                        meta += f" &middot; {escape(m.stadium)}"
                    v2.append(
                        f'<tr class="v2-res-meta-row{alt_cls}">'
                        f'<td colspan="3"><span class="v2-res-meta">{meta}</span></td></tr>'
                    )
                    v2.append(
                        f'<tr class="v2-res-row v2-res-row-compact{fix_cls}{alt_cls}">'
                        f'<td class="v2-res-home">{home}{home_c}</td>'
                        f'{score_cell}'
                        f'<td class="v2-res-away">{away_c}{away}</td></tr>'
                    )
                else:
                    row = (
                        f'<tr class="v2-res-row{fix_cls}{alt_cls}">'
                        f'<td class="v2-res-date">{date}</td>'
                        f'<td class="v2-res-home">{home}{home_c}</td>'
                        f'{score_cell}'
                        f'<td class="v2-res-away">{away_c}{away}</td>'
                    )
                    if has_venue:
                        row += f'<td class="v2-res-venue">{escape(m.stadium)}</td>'
                    row += "</tr>"
                    v2.append(row)
                scorers_html = _scorers_block(m, goals_by_match, "v2")
                if scorers_html:
                    v2.append(
                        f'<tr class="v2-scorers-row{alt_cls}">'
                        f'<td colspan="{colspan}">{scorers_html}</td></tr>'
                    )
            v2.append("</tbody>")  # /v2-md-group
        v2 += ["</table>"]
        v2.append("</div>")  # /v2-results-outer
    v2.append("</div>")  # /v2-content

    return "\n".join(v1 + v2)


def _overview_svg(days, history, rows, color):
    """Build a self-contained SVG line chart of league position by matchday.

    Position 1 is at the top; the y-axis is inverted so a rising line means a
    rising team. One coloured polyline per team, ordered/coloured by final rank.
    """
    n = len(days)
    teams_n = len(rows)
    pad_l, pad_r, pad_t, pad_b = 30, 16, 16, 30
    step_x = 46  # horizontal gap between matchdays
    step_y = 30  # vertical gap between positions
    plot_w = (n - 1) * step_x if n > 1 else step_x
    plot_h = (teams_n - 1) * step_y if teams_n > 1 else step_y
    width = pad_l + plot_w + pad_r
    height = pad_t + plot_h + pad_b

    def x(i):
        return pad_l + (i * step_x if n > 1 else plot_w / 2)

    def y(pos):
        return pad_t + (pos - 1) * step_y

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="League position by matchday">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
    ]

    # Horizontal gridlines + position labels down the left edge.
    for pos in range(1, teams_n + 1):
        gy = y(pos)
        parts.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{pad_l + plot_w}" y2="{gy:.1f}" '
            f'stroke="#ededed" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{pad_l - 8}" y="{gy + 3:.1f}" text-anchor="end" '
            f'font-size="9" fill="#888">{pos}</text>'
        )

    # Matchday labels along the bottom.
    for i, md in enumerate(days):
        parts.append(
            f'<text x="{x(i):.1f}" y="{height - 10}" text-anchor="middle" '
            f'font-size="9" fill="#888">{md}</text>'
        )

    # One polyline per team, plus a marker on its current (latest) position.
    for s in rows:
        positions = history[s.code]
        pts = " ".join(f"{x(i):.1f},{y(p):.1f}" for i, p in enumerate(positions))
        c = color[s.code]
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{c}" '
            f'stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        last_i = len(positions) - 1
        parts.append(
            f'<circle cx="{x(last_i):.1f}" cy="{y(positions[last_i]):.1f}" '
            f'r="3.2" fill="{c}"/>'
        )

    parts.append("</svg>")
    return "".join(parts)


def _overview_legend(rows, color):
    items = [
        f'<li class="ov-legend-item">'
        f'<span class="ov-swatch" style="background:{color[s.code]}"></span>'
        f'<span class="ov-legend-name">{escape(s.name)}</span></li>'
        for s in rows
    ]
    return '<ul class="ov-legend">' + "".join(items) + "</ul>"


def render_overview(matches, teams, days, history, rows, season="", league_name="", league_logo=""):
    played = bool(days) and bool(rows)
    if played:
        color = {s.code: _PALETTE[i % len(_PALETTE)] for i, s in enumerate(rows)}
        chart = (
            f'<div class="ov-chart-wrap">{_overview_svg(days, history, rows, color)}</div>'
            f"{_overview_legend(rows, color)}"
        )
    else:
        chart = '<p class="empty">No matches have been played yet.</p>'

    caption = '<p class="ov-caption">League position after each matchday &middot; position 1 is top.</p>'

    v1 = [
        '<div class="v1-content">',
        '<h2 class="view-title">Season Overview</h2>',
        caption,
        chart,
        "</div>",  # /v1-content
    ]
    v2 = [
        '<div class="v2-content">',
        '<div class="v2-mini-banner">',
        f'<img class="v2-mini-logo" src="{escape(league_logo)}" alt="">' if league_logo else "",
        f'<p class="v2-season">SEASON {escape(season)}</p>',
        f'<h2 class="v2-mini-league">{escape(league_name.upper())}</h2>',
        "</div>",  # /v2-mini-banner
        '<div class="ov-outer">',
        caption,
        chart,
        "</div>",  # /ov-outer
        "</div>",  # /v2-content
    ]
    return "\n".join(v1 + v2)


def render_goalscorers(teams, top_scorers, own_goal_total, team_scorers,
                       season="", league_name="", league_logo="", crest=None,
                       more_scorers=None):
    """The Goal Scorers tab: overall Top Scorers table + per-team top-3 cards.

    Only built for the Super League (the only league with goal data), so this is
    never reached with empty data in practice — but it degrades gracefully if so.
    """
    table_v1 = _scorers_table(top_scorers, own_goal_total, teams, "v1", crest, more_scorers)
    teams_v1 = _team_scorers_section(team_scorers, "v1", crest)
    table_v2 = _scorers_table(top_scorers, own_goal_total, teams, "v2", crest, more_scorers)
    teams_v2 = _team_scorers_section(team_scorers, "v2", crest)
    empty = not (top_scorers or own_goal_total or team_scorers)

    v1 = ['<div class="v1-content">', '<h2 class="view-title">Goal Scorers</h2>']
    if empty:
        v1.append('<p class="empty">No goals have been recorded yet.</p>')
    else:
        v1 += [table_v1, teams_v1]
    v1.append("</div>")  # /v1-content

    v2 = [
        '<div class="v2-content">',
        '<div class="v2-mini-banner">',
        f'<img class="v2-mini-logo" src="{escape(league_logo)}" alt="">' if league_logo else "",
        f'<p class="v2-season">SEASON {escape(season)}</p>',
        f'<h2 class="v2-mini-league">{escape(league_name.upper())}</h2>',
        "</div>",  # /v2-mini-banner
        '<div class="ov-outer">',
    ]
    if empty:
        v2.append('<p class="v2-empty">No goals have been recorded yet.</p>')
    else:
        v2 += [table_v2, teams_v2]
    v2 += ["</div>", "</div>"]  # /ov-outer /v2-content
    return "\n".join(v1 + v2)


def build_site(dist, templates_dir, static_dir, league_name, updated, rows, matches, teams,
               season="", total_goals=0, goals_per_game=0.0,
               form=None, changes=None, days=None, history=None,
               css_prefix="", back_link="", copy_static=True,
               goals_by_match=None, top_scorers=None, own_goal_total=0, team_scorers=None,
               more_scorers=None):
    os.makedirs(dist, exist_ok=True)
    base = _read(os.path.join(templates_dir, "base.html"))

    # Logos are keyed off the data: a club crest by its team code, the league
    # logo by this league's output-directory slug (sl / ndl / wp / u16).
    crest = _logo_finder(static_dir, css_prefix, "clubs")
    css_ver = css_version(static_dir)
    slug = os.path.basename(os.path.normpath(dist))
    league_logo = _logo_finder(static_dir, css_prefix, "leagues")(slug) or ""
    header_logo = (
        f'<img class="site-logo" src="{escape(league_logo)}" alt="">' if league_logo else ""
    )

    pages = {
        "index.html": ("Standings", render_standings(
            rows, season=season, league_name=league_name,
            total_goals=total_goals, goals_per_game=goals_per_game, updated=updated,
            form=form, changes=changes, crest=crest, league_logo=league_logo,
        )),
        "results.html": ("Matches", render_results(
            matches, teams, season=season, league_name=league_name,
            crest=crest, league_logo=league_logo,
            # Every league uses the compact (centred date/venue caption above
            # each result) layout — it fits without horizontal scroll and leaves
            # room for an optional scorer block. The block stays empty until a
            # league supplies goal data, so leagues without goals are unchanged
            # apart from the tidier layout.
            goals_by_match=goals_by_match, compact=True,
        )),
        "overview.html": ("Season Overview", render_overview(
            matches, teams, days or [], history or {}, rows,
            season=season, league_name=league_name, league_logo=league_logo,
        )),
    }

    # The Goal Scorers tab/page exists only when there is goal data (the Super
    # League). Other leagues keep the original three-tab nav untouched.
    nav_items = list(NAV_ITEMS)
    if top_scorers or own_goal_total or team_scorers:
        nav_items.insert(2, ("goalscorers.html", "Goal Scorers"))
        pages["goalscorers.html"] = ("Goal Scorers", render_goalscorers(
            teams, top_scorers or [], own_goal_total, team_scorers or [],
            season=season, league_name=league_name, league_logo=league_logo,
            crest=crest, more_scorers=more_scorers or [],
        ))

    for filename, (title, content) in pages.items():
        html = (
            base.replace("{{TITLE}}", escape(title))
            .replace("{{LEAGUE_NAME}}", escape(league_name))
            .replace("{{LEAGUE_LOGO}}", header_logo)
            .replace("{{LAST_UPDATED}}", escape(updated))
            .replace("{{NAV}}", _nav(filename, nav_items))
            .replace("{{CONTENT}}", content)
            .replace("{{CSS_PREFIX}}", css_prefix)
            .replace("{{CSS_VER}}", css_ver)
            .replace("{{BACK_LINK}}", back_link)
        )
        _write(os.path.join(dist, filename), html)

    if copy_static:
        copy_static_tree(static_dir, dist)
        _write(os.path.join(dist, ".nojekyll"), "")
