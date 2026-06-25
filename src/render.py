"""Rendering layer: turn computed data into static HTML pages."""

from datetime import datetime
from html import escape
import os
import shutil

NAV_ITEMS = (("index.html", "Standings"), ("results.html", "Results"))


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


def render_standings(rows, season="", league_name="", total_goals=0, goals_per_game=0.0, updated=""):
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
        '<th class="v2-th-pos">PLATZ</th>',
        '<th class="v2-th-team">TEAM</th>',
        "<th>SP</th><th>G</th><th>U</th><th>V</th>",
        "<th>TOR</th><th>DIFF</th>",
        '<th class="v2-th-pts">PUNKTE</th>',
        "</tr></thead>",
        "<tbody>",
    ]
    for i, s in enumerate(rows, start=1):
        gd = f"+{s.gd}" if s.gd > 0 else str(s.gd)
        tor = f"{s.gf}:{s.ga}"
        leader_cls = " v2-pos-leader" if i == 1 else ""
        v2.append(
            f'<tr class="pos-{i}">'
            f'<td class="v2-pos{leader_cls}"><span class="v2-arrow">&#x2192;</span> {i}.</td>'
            f'<td class="v2-team-name">{escape(s.name)}</td>'
            f"<td>{s.played}</td><td>{s.won}</td><td>{s.drawn}</td><td>{s.lost}</td>"
            f'<td class="v2-tor">{tor}</td><td>{gd}</td>'
            f'<td class="v2-pts">{s.points}</td>'
            "</tr>"
        )
    v2 += [
        "</tbody></table>",
        "</div>",  # /v2-table-outer
        "</div>",  # /v2-content
    ]

    return "\n".join(v1 + v2)


def render_results(matches, teams):
    body = ['<h2 class="view-title">Results</h2>']
    played = [m for m in matches if m.played]
    if not played:
        body.append('<p class="empty">No results have been recorded yet.</p>')
        return "\n".join(body)

    by_day = {}
    for m in played:
        by_day.setdefault(m.matchday, []).append(m)

    for md in sorted(by_day, reverse=True):
        body.append('<section class="matchday">')
        body.append(f"<h3>Matchday {md}</h3>")
        body.append('<ul class="matches">')
        for m in sorted(by_day[md], key=lambda x: (x.date, x.home_code)):
            home = escape(teams[m.home_code].name)
            away = escape(teams[m.away_code].name)
            body.append(
                '<li class="match">'
                f'<span class="home">{home}</span>'
                f'<span class="score">{m.home_goals}'
                f'<span class="dash">&ndash;</span>{m.away_goals}</span>'
                f'<span class="away">{away}</span>'
                f'<span class="date">{escape(_format_date(m.date))}</span>'
                "</li>"
            )
        body.append("</ul></section>")
    return "\n".join(body)


def build_site(dist, templates_dir, static_dir, league_name, updated, rows, matches, teams,
               season="", total_goals=0, goals_per_game=0.0):
    os.makedirs(dist, exist_ok=True)
    base = _read(os.path.join(templates_dir, "base.html"))
    pages = {
        "index.html": ("Standings", render_standings(
            rows, season=season, league_name=league_name,
            total_goals=total_goals, goals_per_game=goals_per_game, updated=updated,
        )),
        "results.html": ("Results", render_results(matches, teams)),
    }
    for filename, (title, content) in pages.items():
        html = (
            base.replace("{{TITLE}}", escape(title))
            .replace("{{LEAGUE_NAME}}", escape(league_name))
            .replace("{{LAST_UPDATED}}", escape(updated))
            .replace("{{NAV}}", _nav(filename))
            .replace("{{CONTENT}}", content)
        )
        _write(os.path.join(dist, filename), html)

    shutil.copy(os.path.join(static_dir, "style.css"), os.path.join(dist, "style.css"))
    # Tell GitHub Pages to serve files as-is (no Jekyll processing).
    _write(os.path.join(dist, ".nojekyll"), "")
