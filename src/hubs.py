"""Club hub and player pages — the cross-competition views the schema unlocks.

Club hubs live at /clubs/{club_id}.html: the club's header, each of its
squads with its current competition + table position, and recent results
across every competition. Player pages live at /players/{player_id}.html:
goals by season and competition. Both reuse the site's existing CSS classes
so they inherit the league pages' styling.
"""

from html import escape
import os

from . import adapt, dataset, render

RECENT_RESULTS = 10

HOME_BACK = '<a href="../" class="back-link">&#x2190; All Leagues</a>'


def _page(base, title, content, updated, css_ver, header_logo=""):
    return (
        base.replace("{{TITLE}}", escape(title))
        .replace("{{LEAGUE_NAME}}", escape(title))
        .replace("{{LEAGUE_LOGO}}", header_logo)
        .replace("{{LAST_UPDATED}}", escape(updated))
        .replace("{{NAV}}", render._nav("", items=(("../", "Home"),)))
        .replace("{{CONTENT}}", content)
        .replace("{{CSS_PREFIX}}", "../")
        .replace("{{CSS_VER}}", css_ver)
        .replace("{{BACK_LINK}}", HOME_BACK)
    )


# ── Club hubs ────────────────────────────────────────────────────────────────

def _club_result_row(m, league):
    """One compact result row from the club's perspective, with league tag."""
    home = escape(league.teams[m.home_code].name if m.home_code in league.teams else m.home_code)
    away = escape(league.teams[m.away_code].name if m.away_code in league.teams else m.away_code)
    score_cell, fix_cls = render._score_cell(m)
    date = escape(render._format_date(m.date))
    meta = f"{date} &middot; {escape(league.league_name)}" if date else escape(league.league_name)
    return (
        f'<tr class="v2-res-meta-row"><td colspan="3">'
        f'<span class="v2-res-meta">{meta}</span></td></tr>'
        f'<tr class="v2-res-row v2-res-row-compact{fix_cls}">'
        f'<td class="v2-res-home">{home}</td>'
        f'{score_cell}'
        f'<td class="v2-res-away">{away}</td></tr>'
    )


def render_club_hub(club, club_teams, crest_url):
    """The hub page body for one club.

    `club_teams` is a list of (team, league, standing, position, played,
    recent_matches, code) tuples — one per squad that is entered in a built
    league. `code` is that squad's team code within its league, used to
    match the `#club-team-{code}` fragment a league page links in with
    (see build.py) so the row for the team the user came from can be
    highlighted via the CSS :target selector.
    """
    crest_img = (
        f'<img class="v2-mini-logo" src="{escape(crest_url)}" alt="">' if crest_url else ""
    )
    place = ", ".join(x for x in (club.city, club.region) if x)

    v2 = [
        '<div class="v2-content">',
        '<div class="v2-mini-banner">',
        crest_img,
        f'<p class="v2-season">{escape(place.upper())}</p>' if place else "",
        f'<h2 class="v2-mini-league">{escape(club.name.upper())}</h2>',
        "</div>",  # /v2-mini-banner
        f'<h3 class="v2-sec-title">All {escape(club.name)} Teams</h3>',
    ]

    if club_teams:
        rows = []
        for team, league, standing, position, _played, _recent, code in club_teams:
            pos_txt = (
                f"{render._ordinal(position)} &middot; {standing.points} pts"
                if standing is not None and position is not None else "&ndash;"
            )
            team_href = f"../{league.slug}/clubs/{team.legacy_code or team.team_id}.html"
            league_href = f"../{league.slug}/"
            rows.append(
                f'<tr class="v2-res-row" id="club-team-{escape(code)}">'
                f'<td class="v2-res-home"><a class="club-link" href="{escape(team_href)}">'
                f'{escape(league.teams[team.legacy_code].name if team.legacy_code in league.teams else team.team_id)}</a></td>'
                f'<td class="v2-res-away"><a class="club-link" href="{escape(league_href)}">'
                f'{escape(league.league_name)}</a></td>'
                f'<td class="v2-res-venue">{pos_txt}</td></tr>'
            )
        v2 += [
            '<div class="v2-results-outer">',
            '<table class="v2-results-table">',
            '<thead><tr><th class="v2-res-th-home">TEAM</th>'
            '<th class="v2-res-th-away">COMPETITION</th>'
            '<th class="v2-res-th-venue">POSITION</th></tr></thead>',
            "<tbody>", *rows, "</tbody></table></div>",
            # Highlights the row for window.__clubTeamTarget (set in
            # templates/base.html before the hash could scroll the page to
            # it). Runs inline, after the rows above, so the target already
            # exists in the DOM.
            "<script>(function(){"
            "var id=window.__clubTeamTarget;if(!id)return;"
            "var el=document.getElementById(id);"
            "if(el)el.classList.add('v2-current-team');"
            "})();</script>",
        ]
    else:
        v2.append('<p class="v2-empty">No teams in a current competition.</p>')

    # Recent results across all of the club's competitions, newest first.
    all_recent = []
    for _team, league, _standing, _position, _played, recent, _code in club_teams:
        all_recent += [(m.date or "", m, league) for m in recent]
    all_recent.sort(key=lambda x: x[0], reverse=True)
    all_recent = all_recent[:RECENT_RESULTS]

    v2.append('<h3 class="v2-sec-title">Recent Results</h3>')
    if all_recent:
        body = "".join(_club_result_row(m, league) for _d, m, league in all_recent)
        v2 += [
            '<div class="v2-results-outer">',
            '<table class="v2-results-table v2-results-compact">',
            '<thead><tr><th class="v2-res-th-home">HOME</th>'
            '<th class="v2-res-th-score">RESULT</th>'
            '<th class="v2-res-th-away">AWAY</th></tr></thead>',
            f"<tbody>{body}</tbody></table></div>",
        ]
    else:
        v2.append('<p class="v2-empty">No results yet.</p>')

    v2.append("</div>")  # /v2-content
    return "\n".join(v2)


def build_club_hubs(dist, templates_dir, static_dir, ds, leagues, standings_by_slug,
                    updated):
    """Write /clubs/{club_id}.html for every club with a team in a built league.

    `leagues` is the list of LeagueData that were built; `standings_by_slug`
    maps slug -> the computed standings rows for that league.
    """
    base = render._read(os.path.join(templates_dir, "base.html"))
    css_ver = render.css_version(static_dir)
    out_dir = os.path.join(dist, "clubs")
    os.makedirs(out_dir, exist_ok=True)

    league_of_team = {}
    for league in leagues:
        for code, tv in league.teams.items():
            league_of_team[tv.team_id] = (league, code)

    crest = render._crest_lookup(static_dir, "../")

    count = 0
    for club in ds.clubs.values():
        club_teams = []
        for team in ds.teams.values():
            if team.club_id != club.club_id or team.team_id not in league_of_team:
                continue
            league, code = league_of_team[team.team_id]
            rows = standings_by_slug.get(league.slug, [])
            standing = next((s for s in rows if s.code == code), None)
            position = next(
                (i for i, s in enumerate(rows, start=1) if s.code == code), None)
            played = [m for m in league.matches
                      if code in (m.home_code, m.away_code) and m.played]
            played.sort(key=lambda m: (m.date, m.matchday), reverse=True)
            club_teams.append(
                (team, league, standing, position, len(played),
                 played[:RECENT_RESULTS], code))
        if not club_teams:
            continue

        crest_url = crest(club.club_id) or next(
            (crest(t.legacy_code) for t, *_ in club_teams if t.legacy_code and crest(t.legacy_code)),
            None)
        content = render_club_hub(club, club_teams, crest_url or "")
        html = _page(base, club.name, content, updated, css_ver)
        render._write(os.path.join(out_dir, f"{club.club_id}.html"), html)
        count += 1
    return count


# ── Player pages ─────────────────────────────────────────────────────────────

def build_player_pages(dist, templates_dir, static_dir, ds, updated):
    """Write /players/{player_id}.html for every player with a scorer credit.

    Goals grouped by season + competition, from the goals tab. Own goals are
    not scorer credits (shown as a separate note). CAF_MW_UNKNOWN and goals
    from placeholder matches are skipped.
    """
    base = render._read(os.path.join(templates_dir, "base.html"))
    css_ver = render.css_version(static_dir)
    out_dir = os.path.join(dist, "players")
    os.makedirs(out_dir, exist_ok=True)

    # player -> (season_id, competition_id) -> goals / own goals
    credits = {}
    own_goals = {}
    for g in ds.goals.values():
        if g.player_id == dataset.UNKNOWN_PLAYER_ID:
            continue
        m = ds.matches.get(g.match_id)
        if m is None or m.is_placeholder:
            continue
        key = (m.season_id, m.competition_id)
        if g.is_own_goal:
            own_goals[g.player_id] = own_goals.get(g.player_id, 0) + 1
        else:
            credits.setdefault(g.player_id, {})
            credits[g.player_id][key] = credits[g.player_id].get(key, 0) + 1

    count = 0
    for player_id in sorted(set(credits) | set(own_goals)):
        player = ds.players.get(player_id)
        if player is None:
            continue  # dangling id would already have failed validation
        name = player.display_name
        by_comp = credits.get(player_id, {})

        rows = []
        total = 0
        for (season_id, competition_id), n in sorted(
                by_comp.items(),
                key=lambda kv: (ds.seasons[kv[0][0]].start_date, kv[0][1]),
                reverse=True):
            season = ds.seasons[season_id]
            comp_name = ds.league_display_name(competition_id, season_id)
            slug = adapt.competition_slug(
                competition_id, ds.competitions[competition_id].country)
            total += n
            rows.append(
                f'<tr><td class="scr-player">{escape(season.label)}</td>'
                f'<td class="scr-team"><a class="club-link" href="../{escape(slug)}/">'
                f'{escape(comp_name)}</a></td>'
                f'<td class="scr-goals">{n}</td></tr>'
            )
        if total:
            rows.append(
                '<tr class="scr-og-row"><td class="scr-player">Total</td>'
                f'<td class="scr-team"></td><td class="scr-goals">{total}</td></tr>'
            )

        og_note = ""
        if own_goals.get(player_id):
            n = own_goals[player_id]
            og_note = (f'<p class="v2-res-legend">{n} own goal'
                       f'{"s" if n != 1 else ""} (not counted above).</p>')

        table = (
            '<div class="v2-table-outer">'
            '<table class="v2-standings scorers-table">'
            '<thead><tr><th class="scr-th-player">SEASON</th>'
            '<th class="scr-th-team">COMPETITION</th>'
            '<th class="scr-th-goals">GOALS</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            if rows else '<p class="v2-empty">No goals recorded.</p>'
        )

        content = "\n".join([
            '<div class="v2-content">',
            '<div class="v2-mini-banner">',
            '<p class="v2-season">PLAYER</p>',
            f'<h2 class="v2-mini-league">{escape(name.upper())}</h2>',
            "</div>",
            '<h3 class="v2-sec-title">Goals by Competition</h3>',
            table,
            og_note,
            "</div>",
        ])
        html = _page(base, name, content, updated, css_ver)
        render._write(os.path.join(out_dir, f"{player_id}.html"), html)
        count += 1
    return count
