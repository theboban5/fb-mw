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
        '<th class="v2-th-pos">POS</th>',
        '<th class="v2-th-team">TEAM</th>',
        "<th>P</th><th>W</th><th>D</th><th>L</th>",
        "<th>GOALS</th><th>DIFF</th>",
        '<th class="v2-th-pts">PTS</th>',
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


def render_results(matches, teams, season="", league_name=""):
    played = [m for m in matches if m.played]
    by_day = {}
    for m in played:
        by_day.setdefault(m.matchday, []).append(m)

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
                v1.append(
                    '<li class="match">'
                    f'<span class="home">{home}</span>'
                    f'<span class="score">{m.home_goals}'
                    f'<span class="dash">&ndash;</span>{m.away_goals}</span>'
                    f'<span class="away">{away}</span>'
                    f'<span class="date">{escape(_format_date(m.date))}</span>'
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
        v2 += [
            '<table class="v2-results-table">',
            "<thead><tr>",
            '<th class="v2-res-th-date">DATE</th>',
            '<th class="v2-res-th-home">HOME</th>',
            '<th class="v2-res-th-score">RESULT</th>',
            '<th class="v2-res-th-away">AWAY</th>',
            "</tr></thead>",
            "<tbody>",
        ]
        for md in sorted(by_day, reverse=True):
            day_matches = sorted(by_day[md], key=lambda x: (x.date, x.home_code))
            v2.append(
                f'<tr class="v2-md-row"><td colspan="4">MATCHDAY {md}</td></tr>'
            )
            for j, m in enumerate(day_matches):
                alt_cls = " alt" if j % 2 == 1 else ""
                home = escape(teams[m.home_code].name)
                away = escape(teams[m.away_code].name)
                score = f"{m.home_goals}:{m.away_goals}"
                date = escape(_format_date(m.date))
                v2.append(
                    f'<tr class="v2-res-row{alt_cls}">'
                    f'<td class="v2-res-date">{date}</td>'
                    f'<td class="v2-res-home">{home}</td>'
                    f'<td class="v2-res-score">{score}</td>'
                    f'<td class="v2-res-away">{away}</td>'
                    "</tr>"
                )
        v2 += ["</tbody></table>"]
    v2 += [
        "</div>",  # /v2-results-outer
        "</div>",  # /v2-content
    ]

    return "\n".join(v1 + v2)


def build_site(dist, templates_dir, static_dir, league_name, updated, rows, matches, teams,
               season="", total_goals=0, goals_per_game=0.0):
    os.makedirs(dist, exist_ok=True)
    base = _read(os.path.join(templates_dir, "base.html"))
    pages = {
        "index.html": ("Standings", render_standings(
            rows, season=season, league_name=league_name,
            total_goals=total_goals, goals_per_game=goals_per_game, updated=updated,
        )),
        "results.html": ("Results", render_results(matches, teams, season=season, league_name=league_name)),
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
