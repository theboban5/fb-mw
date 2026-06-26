"""Rendering layer: turn computed data into static HTML pages."""

from datetime import datetime
from html import escape
import os
import shutil

NAV_ITEMS = (
    ("index.html", "Standings"),
    ("results.html", "Results"),
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


def _nav(active):
    out = []
    for href, label in NAV_ITEMS:
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


def render_standings(rows, season="", league_name="", total_goals=0, goals_per_game=0.0,
                     updated="", form=None, changes=None):
    form = form or {}
    changes = changes or {}
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
        v1.append(
            f'<tr class="pos-{i}">'
            f'<td class="pos">{i}</td>'
            f'<td class="team">{escape(s.name)}</td>'
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
        v2.append(
            f'<tr class="pos-{i}">'
            f'<td class="v2-pos{leader_cls}"><span class="v2-arrow {arrow_cls}">{arrow_glyph}</span> {i}.</td>'
            f'<td class="v2-team-name">{escape(s.name)}</td>'
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


def render_results(matches, teams, season="", league_name=""):
    played = [m for m in matches if m.played]
    by_day = {}
    for m in played:
        by_day.setdefault(m.matchday, []).append(m)

    has_venue = any(m.stadium for m in played)

    # ── V1 / V3 content ────────────────────────────────────
    v1 = ['<div class="v1-content">', '<h2 class="view-title">Results</h2>']
    if not played:
        v1.append('<p class="empty">No results have been recorded yet.</p>')
    else:
        for md in sorted(by_day, reverse=True):
            v1.append('<section class="matchday">')
            v1.append(f"<h3>Matchday {md}</h3>")
            v1.append('<ul class="matches">')
            for m in sorted(by_day[md], key=lambda x: (x.date, x.home_code)):
                home = escape(teams[m.home_code].name)
                away = escape(teams[m.away_code].name)
                date_str = escape(_format_date(m.date))
                if m.stadium:
                    date_str += f" &middot; {escape(m.stadium)}"
                v1.append(
                    '<li class="match">'
                    f'<span class="home">{home}</span>'
                    f'<span class="score">{m.home_goals}'
                    f'<span class="dash">&ndash;</span>{m.away_goals}</span>'
                    f'<span class="away">{away}</span>'
                    f'<span class="date">{date_str}</span>'
                    "</li>"
                )
            v1.append("</ul></section>")
    v1.append("</div>")  # /v1-content

    # ── V2 content (fussball.de style) ─────────────────────
    v2 = [
        '<div class="v2-content">',
        '<div class="v2-mini-banner">',
        f'<p class="v2-season">SEASON {escape(season)}</p>',
        f'<h2 class="v2-mini-league">{escape(league_name.upper())}</h2>',
        "</div>",  # /v2-mini-banner
        '<div class="v2-results-outer">',
    ]
    if not played:
        v2.append('<p class="v2-empty">No results have been recorded yet.</p>')
    else:
        colspan = 5 if has_venue else 4
        v2 += [
            '<table class="v2-results-table">',
            "<thead><tr>",
            '<th class="v2-res-th-date">DATE</th>',
            '<th class="v2-res-th-home">HOME</th>',
            '<th class="v2-res-th-score">RESULT</th>',
            '<th class="v2-res-th-away">AWAY</th>',
        ]
        if has_venue:
            v2.append('<th class="v2-res-th-venue">VENUE</th>')
        v2 += ["</tr></thead>", "<tbody>"]
        for md in sorted(by_day, reverse=True):
            day_matches = sorted(by_day[md], key=lambda x: (x.date, x.home_code))
            v2.append(
                f'<tr class="v2-md-row"><td colspan="{colspan}">MATCHDAY {md}</td></tr>'
            )
            for j, m in enumerate(day_matches):
                alt_cls = " alt" if j % 2 == 1 else ""
                home = escape(teams[m.home_code].name)
                away = escape(teams[m.away_code].name)
                score = f"{m.home_goals}:{m.away_goals}"
                date = escape(_format_date(m.date))
                row = (
                    f'<tr class="v2-res-row{alt_cls}">'
                    f'<td class="v2-res-date">{date}</td>'
                    f'<td class="v2-res-home">{home}</td>'
                    f'<td class="v2-res-score">{score}</td>'
                    f'<td class="v2-res-away">{away}</td>'
                )
                if has_venue:
                    row += f'<td class="v2-res-venue">{escape(m.stadium)}</td>'
                row += "</tr>"
                v2.append(row)
        v2 += ["</tbody></table>"]
    v2 += [
        "</div>",  # /v2-results-outer
        "</div>",  # /v2-content
    ]

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


def render_overview(matches, teams, days, history, rows, season="", league_name=""):
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


def build_site(dist, templates_dir, static_dir, league_name, updated, rows, matches, teams,
               season="", total_goals=0, goals_per_game=0.0,
               form=None, changes=None, days=None, history=None,
               css_prefix="", back_link="", copy_static=True):
    os.makedirs(dist, exist_ok=True)
    base = _read(os.path.join(templates_dir, "base.html"))
    pages = {
        "index.html": ("Standings", render_standings(
            rows, season=season, league_name=league_name,
            total_goals=total_goals, goals_per_game=goals_per_game, updated=updated,
            form=form, changes=changes,
        )),
        "results.html": ("Results", render_results(matches, teams, season=season, league_name=league_name)),
        "overview.html": ("Season Overview", render_overview(
            matches, teams, days or [], history or {}, rows,
            season=season, league_name=league_name,
        )),
    }
    for filename, (title, content) in pages.items():
        html = (
            base.replace("{{TITLE}}", escape(title))
            .replace("{{LEAGUE_NAME}}", escape(league_name))
            .replace("{{LAST_UPDATED}}", escape(updated))
            .replace("{{NAV}}", _nav(filename))
            .replace("{{CONTENT}}", content)
            .replace("{{CSS_PREFIX}}", css_prefix)
            .replace("{{BACK_LINK}}", back_link)
        )
        _write(os.path.join(dist, filename), html)

    if copy_static:
        for fname in os.listdir(static_dir):
            shutil.copy(os.path.join(static_dir, fname), os.path.join(dist, fname))
        _write(os.path.join(dist, ".nojekyll"), "")
