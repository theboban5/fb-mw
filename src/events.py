"""Events and winners: /events/, every campaign the site has run, newest
first, and the banner that points at whichever one is on.

WHY. Know Your Flames ended and its winners were on /quiz/, under a quiz
nobody could play any more, reachable only from a banner that took itself
down on 21 November. The Derby race is the second campaign; there will be
more, and each one's winners deserve a page that is still there next month,
with their pictures once we have them. A fan deciding whether to trust the
next one should be able to see that the last one paid out.

WHAT THIS DOES. `events()` builds the list from what each campaign already
records (quiz.QUIZZES, the derby module's constants), so a winner's name is
typed once, where the campaign's own page reads it too. Photos are the one
thing only this page has: `PHOTOS`, keyed by event id, files under
static/events/. Shrink them before committing (1200px wide is plenty);
static/ is copied verbatim and test_events holds each under PHOTO_MAX_BYTES.

Each card ships its "Coming up / On now / Finished" for the build's clock,
and an inline script re-picks it on the reader's, quiz.py's bargain: a page
built on Friday is still served on Sunday.

THE BANNER moved here because it now speaks for more than one campaign.
quiz.strip renders it; this decides the order. The first segment that
matches the reader's clock wins, so the Derby goes first and takes the strip
from the quiz's "meet the winners" the day it was announced.
"""

import os
from dataclasses import dataclass
from datetime import datetime
from html import escape

from . import derby, quiz, render

CAT = quiz.CAT
PAGE_URL = render.SITE_URL + "/events/"


@dataclass(frozen=True)
class Photo:
    src: str          # from the site root: "events/flames-egypt-2/winner.jpg"
    alt: str          # what is in it, read aloud; never the byline
    credit: str = ""  # whose photo it is, printed small under it


@dataclass(frozen=True)
class Event:
    id: str
    title: str
    starts: datetime
    ends: datetime
    about: str
    prize: str
    href: str          # its own page, from the site root
    cta: str           # the link's words while it is on: "Join", "Play"
    icon: str = ""
    winners: tuple = ()
    winners_note: str = ""
    photos: tuple = ()


# Pictures of the winners, the prize handed over, the day itself. Empty
# renders nothing; a card is complete without them.
#   "flames-egypt-2": (Photo("events/flames-egypt-2/tuntufye.jpg",
#                            "Tuntufye Kuwali with his VIP ticket", "EveryLeague"),),
PHOTOS = {}

PHOTO_MAX_BYTES = 300_000


def events():
    """Every campaign, newest first."""
    out = [Event(
        id=derby.CAMPAIGN_ID,
        title="Bring your friends to the Derby",
        starts=derby.OPENS, ends=derby.CLOSES,
        about=f"Invite your friends to join EveryLeague before "
              f"{derby.MATCH}. Each friend who joins is a point, and the most "
              f"points wins.",
        prize=f"{derby.CASH}, and {derby.TICKETS} VIP tickets for the Derby",
        href="derby/", cta="Join", icon=derby.ICON,
        winners=derby.WINNER_NAMES, winners_note=derby.WINNERS_NOTE,
        photos=PHOTOS.get(derby.CAMPAIGN_ID, ()))]
    for q in reversed(quiz.QUIZZES):
        out.append(Event(
            id=q.id, title=q.title, starts=q.opens, ends=q.closes,
            about=("Three questions about the Malawi Flames, every answer on "
                   "EveryLeague, before Malawi v Egypt in Cairo."
                   if q.round == 1 else
                   "Three more questions after the Cairo match, for tickets to "
                   "the return in Lilongwe."),
            prize=_prize(q.prize),
            href="quiz/", cta="Play", icon=quiz.FLAG,
            winners=q.winner_names, winners_note=q.winners_note,
            photos=PHOTOS.get(q.id, ())))
    return sorted(out, key=lambda e: e.starts, reverse=True)


def _prize(headline):
    """"Win a Malawi Flames jersey" -> "A Malawi Flames jersey"."""
    p = headline.removeprefix("Win ")
    return p[:1].upper() + p[1:]


def banner(css_prefix, now):
    """The strip above every page's search bar (render.BANNER)."""
    return quiz.strip(css_prefix,
                      derby.banner_segments() + quiz._banner_segments(), now)


# ── The page ─────────────────────────────────────────────────────────────────

def _ms(dt):
    return int(dt.timestamp() * 1000)


def _span(a, b):
    """"19–21 November 2026", "30 November – 2 December 2026", in CAT."""
    a, b = a.astimezone(CAT), b.astimezone(CAT)
    if (a.year, a.month) == (b.year, b.month):
        days = f"{a.day}" if a.day == b.day else f"{a.day}–{b.day}"
        return f"{days} {b:%B %Y}"
    return f"{a.day} {a:%B} – {b.day} {b:%B %Y}"


def state_at(e, now):
    if now < e.starts:
        return "soon"
    return "on" if now < e.ends else "over"


_STATES = (("soon", "Coming up"), ("on", "On now"), ("over", "Finished"))


def _card(e, now, prefix):
    showing = state_at(e, now)
    chips = "".join(
        f'<span class="ev-chip is-{key}" data-ev-when="{key}"'
        f'{"" if key == showing else " hidden"}>{label}</span>'
        for key, label in _STATES)
    href = escape(prefix + e.href)
    go = "".join(
        f'<a class="ev-go{" is-live" if key != "over" else ""}" href="{href}" '
        f'data-ev-when="{key}"{"" if key == showing else " hidden"}>'
        f'{escape(e.cta if key != "over" else "See the page")} &rarr;</a>'
        for key, _label in _STATES)
    photos = ""
    if e.photos:
        figs = "".join(
            f'<figure class="ev-photo"><img src="{escape(prefix + ph.src)}" '
            f'alt="{escape(ph.alt)}" loading="lazy" decoding="async">'
            + (f"<figcaption>{escape(ph.credit)}</figcaption>"
               if ph.credit else "")
            + "</figure>"
            for ph in e.photos)
        photos = f'<div class="ev-photos">{figs}</div>'
    winners = ""
    if e.winners:
        names = "".join(f"<li>{escape(n)}</li>" for n in e.winners)
        note = (f'<p class="ev-note">{escape(e.winners_note)}</p>'
                if e.winners_note else "")
        winners = (f'<h3 class="ev-h">The winners</h3>'
                   f'<ul class="qz-winners-list ev-winners">{names}</ul>{note}')
    return f"""<li class="ev-card" data-ev-starts="{_ms(e.starts)}" data-ev-ends="{_ms(e.ends)}">
  <p class="ev-meta">{chips}<span class="ev-dates">{escape(_span(e.starts, e.ends))}</span></p>
  <h2 class="ev-title"><span aria-hidden="true">{e.icon}</span> {escape(e.title)}</h2>
  <p class="ev-about">{escape(e.about)}</p>
  <p class="ev-prize"><strong>Prize:</strong> {escape(e.prize)}</p>
  {photos}{winners}
  {go}
</li>"""


_JS = """(function(){
var now=Date.now(),cards=document.querySelectorAll('.ev-card');
for(var i=0;i<cards.length;i++){var c=cards[i],
w=now<+c.getAttribute('data-ev-starts')?'soon':now<+c.getAttribute('data-ev-ends')?'on':'over',
el=c.querySelectorAll('[data-ev-when]');
for(var j=0;j<el.length;j++)el[j].hidden=el[j].getAttribute('data-ev-when')!==w;}
})();"""


def render_page(*, css_ver, fl, updated, now):
    title = "Events and winners"
    social = render.social_meta(
        title, url=PAGE_URL,
        description="Quizzes and prize competitions on EveryLeague, and the "
                    "people who won them.")
    cards = "".join(_card(e, now, "../") for e in events())
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{escape(title)} | Everyleague</title>
{social}
<link rel="stylesheet" href="../style.css?v={css_ver}">
<link rel="icon" href="../favicon.ico" sizes="any">
<link rel="icon" type="image/png" href="../favicon-48.png" sizes="48x48">
<link rel="apple-touch-icon" href="../apple-touch-icon.png">
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-RCV8V3DEKV"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());

  gtag('config', 'G-RCV8V3DEKV');
</script>
</head>
<body class="landing quiz events">
<div class="qz-wrap">
  {quiz._header(fl)}
  <main class="qz-main">
  <header class="qz-hero ev-hero">
    <p class="qz-eyebrow">EveryLeague</p>
    <h2 class="qz-title">Events and winners</h2>
    <p class="qz-lede">Quizzes and prize competitions we have run, and the
      people who won them.</p>
  </header>
  <ol class="ev-list">
{cards}
  </ol>
  <p class="ev-more"><a href="../quiz/#updates">Tell me when the next one opens &rarr;</a></p>
  </main>
</div>
{render.footer(updated, "../")}
<script>{_JS}</script>
</body>
</html>"""


def build_page(dist, *, css_ver, fl, updated, now):
    out = os.path.join(dist, "events")
    os.makedirs(out, exist_ok=True)
    render._write(os.path.join(out, "index.html"), render_page(
        css_ver=css_ver, fl=fl, updated=updated, now=now))
