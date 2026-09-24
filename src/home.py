"""The homepage shell: a day's football first, every competition beside it.

WHAT WAS WRONG: the homepage led with a brand hero, a search box, a card
ABOUT today's football and an editorial feature, and only then the list of
competitions. The matches themselves were one tap away on /matches/ — so the
question almost every visitor arrives with ("what's on today, what were the
scores") was answered on the second page, on a phone, on a connection where
every page is a wait. FotMob's answer is the right one: the day IS the front
page.

WHAT THIS DOES: one layout, written for the homepage and for every
/matches/YYYY-MM-DD.html alike, so stepping to yesterday keeps you on the same
page with a different date rather than sending you to a different kind of
page. Three regions, one DOM order, and CSS decides where they sit:

  * **day**      — the date bar and that date's matches (matches_page).
  * **feature**  — the editorial slot (trending carousel / Scorchers card).
                   Homepage only; a date page is a fixture list, not a front.
  * **leagues**  — the Men's / Women's / Youth competition list that used to
                   be the whole homepage.

On a phone (most readers) that order is the reading order: matches, then the
feature, then every competition — with a "Leagues" link in the header that
jumps there, because a list of 29 Saturday matches is a long scroll to reach
the thing that used to be at the top. From 960px the leagues become a sticky
left sidebar; from 1240px the feature takes a right rail.

A date with NO football is a different page, not an empty column: the grid
collapses to one centred column and the leagues sit directly under the "No
matches" line, because on a blank Tuesday the competitions are the useful
thing on the screen and a sidebar beside an empty box reads as broken.

The trade: every date page now carries the competition list too (~2 kB
gzipped). That buys a sidebar that is the same on every date, and nothing
that needs a second request to draw.
"""

from html import escape

from . import render


def brand_header(fl, prefix: str) -> str:
    """The wordmark row: logo + name (a link home), country, a jump to leagues.

    The old hero's headline and tagline are gone from the top of the page —
    on a phone they were 90px between a visitor and the first score. The
    wordmark is the page's <h1> instead, which is what it always named.
    """
    flag = fl.img_for("Malawi", cls="el-flag")
    home = escape(prefix or "./")
    return f"""<header class="hm-top">
    <div class="el-brand-row">
      <a class="el-brand" href="{home}">
        <img class="el-brand-logo" src="{escape(prefix)}everyleague_logo.png" alt=""
             width="360" height="242" decoding="async">
        <h1 class="el-brand-name">Everyleague</h1>
      </a>
      <p class="el-locale">{flag}<span class="el-locale-text">Malawi <span class="el-locale-sep">&middot;</span> Beta</span></p>
      <a class="hm-jump" href="#leagues"><svg class="hm-jump-icon" viewBox="0 0 24 24"
         aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.4"
         stroke-linecap="round"><path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"></path></svg>Leagues</a>
    </div>
  </header>"""


def page(*, title, social, css_prefix, css_ver, fl, day_html, leagues_html,
         updated, feature_html="", empty=False, scripts="", head_extra=""):
    """A whole page: <head>, header, search, the three regions, footer.

    `day_html` is matches_page.render_day's output, `leagues_html` the
    competition tabs (build.py), `feature_html` the editorial slot or "".
    `empty` is True when the date has no football — see the module docstring.
    """
    p = escape(css_prefix)
    grid_cls = "hm-grid" + (" is-empty" if empty else "") + (
        " has-feature" if feature_html else "")
    feature = (f'<aside class="hm-feature" aria-label="Featured">'
               f"{feature_html}</aside>" if feature_html else "")
    leagues = (
        '<nav class="hm-leagues" id="leagues" aria-labelledby="hm-leagues-h">'
        '<h2 class="hm-leagues-h" id="hm-leagues-h">All leagues</h2>'
        f"{leagues_html}</nav>" if leagues_html else "")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{escape(title)}</title>
{social}
<link rel="stylesheet" href="{p}style.css?v={css_ver}">
<link rel="icon" href="{p}favicon.ico" sizes="any">
<link rel="icon" type="image/png" href="{p}favicon-48.png" sizes="48x48">
<link rel="apple-touch-icon" href="{p}apple-touch-icon.png">{head_extra}
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-RCV8V3DEKV"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());

  gtag('config', 'G-RCV8V3DEKV');
</script>
</head>
<body class="landing home">
<div class="hm-wrap">
  {brand_header(fl, css_prefix)}
  {render.search_widget(css_prefix)}
  <main class="{grid_cls}">
    <section class="hm-day" aria-label="Matches">
{day_html}
    </section>
    {feature}
    {leagues}
  </main>
</div>
{render.footer(updated)}
{scripts}
</body>
</html>"""
