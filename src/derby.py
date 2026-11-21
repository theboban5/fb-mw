"""Bring your friends to the Derby: a referral race at /derby/ (0072).

WHY. After Know Your Flames the next big date is Nyasa Big Bullets v Mighty
Wanderers, and the point of this campaign is the list: people who have asked
EveryLeague for updates, and who follow it somewhere. A fan signs up, gets
their own link, and every NEW person who signs up through it counts for them.
The most wins MK50,000; two VIP derby tickets are drawn among everyone else
with at least three.

WHAT IS COUNTED is a new phone number, never a click. A UTM link or a visit
counter says how often a link was opened, which a fan can raise by refreshing
their own page, and GA4 cannot say whose link it was. 0072's referral_rows is
the one definition, read by the page's "you've brought N" and by the admin
screen the prize is decided on. UTMs still go on the share buttons, because
they are free and they answer the other question: which platform the friends
came from.

PROMOTED ON 18 NOV. It shipped unlisted (noindex, no links) and was tried
and rewritten twice before anyone was sent to it. Now the site-wide banner
points here (`banner_segments`, put in front of the quiz's by events.banner)
and the page is indexed; /events/ lists it beside Know Your Flames.

The page has three states, before the race, during it and after, all written
into the page with the build's one showing; static/derby/derby.js re-picks it
on the reader's clock, quiz.py's arrangement. In every state the form works:
signing up is worth something whether or not a race is on, and the window
only decides what COUNTS, which the database decides on its own clock.
"""

import json
import os
from datetime import datetime
from html import escape

from . import quiz, render

CAT = quiz.CAT

# The same id and times as the database (0072's seed, 0074's opening);
# tests/test_derby.py compares.
CAMPAIGN_ID = "derby-2026"
# Wednesday 15:00, not Thursday 00:00 (0074): opened early the afternoon it
# was promoted, with nobody yet brought through a link, so nobody's count
# moved. Two and a half days to share instead of two.
OPENS = datetime(2026, 11, 18, 15, 0, tzinfo=CAT)
CLOSES = datetime(2026, 11, 21, 12, 0, tzinfo=CAT)

# MW_SL_2627_158. Words, not a lookup: this page is about one match and says
# so, and an unconfirmed fixture moving would need the copy rewritten anyway.
HOME, AWAY = "Bullets", "Wanderers"
MATCH = "Nyasa Big Bullets v Mighty Wanderers"
KICKOFF = datetime(2026, 11, 22, 15, 0, tzinfo=CAT)

CASH = "MK50,000"
TICKETS = 2
MIN_REFERRALS = 3   # admin_draw_referral_tickets' p_min, as the rules say it

PAGE_URL = render.SITE_URL + "/derby/"
UTM_CAMPAIGN = "derby2026"

# On while the page was being tried; promoted 18 Nov.
NOINDEX = False

# The banner points here from this moment. It was to be a day before points
# counted; 0074 opened the race the same afternoon, so the "starts" segment
# ran for fifteen hours of a day nobody saw it on.
ANNOUNCED = datetime(2026, 11, 18, 0, 0, tzinfo=CAT)
# After the close the banner says where the winners are, until the week of
# the match is out: quiz.WINNERS_BANNER_UNTIL's reasoning.
WINNERS_BANNER_UNTIL = datetime(2026, 11, 25, 0, 0, tzinfo=CAT)
ICON = "\U0001F3C6"   # the banner's trophy

# Who won, filled in by hand once they are verified (the call, the follow).
# The cash winner first; empty renders nothing. quiz.Quiz.winner_names'
# arrangement, and /events/ reads the same two.
WINNER_NAMES = ("Emily Kapanga",)
# Nobody reached MIN_REFERRALS, so neither prize went as the rules wrote it:
# one VIP ticket to a fan who follows us and can get to the ground, in place
# of the cash, because a fan at the Derby promotes the match and the site.
WINNERS_NOTE = (f"Emily wins a VIP ticket to {MATCH} on {KICKOFF:%A} "
                f"{KICKOFF.day} {KICKOFF:%B}. Nobody reached {MIN_REFERRALS} "
                f"points, so the other prizes were not given. Thank you to "
                f"everyone who "
                f"shared a link: more events and competitions are coming "
                f"soon.")

# (value stored in update_signups.follows, label, link)
FOLLOWS = (
    ("whatsapp", "WhatsApp channel", quiz.WHATSAPP_CHANNEL_URL),
    ("facebook", "Facebook", quiz.FACEBOOK_URL),
    ("linkedin", "LinkedIn", quiz.LINKEDIN_URL),
)


def _ms(dt):
    return int(dt.timestamp() * 1000)


def _when(dt):
    return quiz._when(dt)


def phase_at(now):
    """static/derby/rules.js phaseAt, for the build's clock."""
    if now < OPENS:
        return "before"
    return "open" if now < CLOSES else "closed"


def banner_segments():
    """The Derby's part of the site-wide strip (quiz.strip's shape)."""
    after = ("Derby race: meet the winners" if WINNER_NAMES
             else "Derby race: winners announced here soon")
    return [
        {"key": f"{CAMPAIGN_ID}:soon", "from": _ms(ANNOUNCED),
         "until": _ms(OPENS), "cta": "Get my link", "href": "derby/",
         "icon": ICON,
         "text": f"Derby race starts {OPENS:%A}: invite friends, win {CASH}"},
        {"key": CAMPAIGN_ID, "from": _ms(OPENS), "until": _ms(CLOSES),
         "cta": "Join", "href": "derby/", "icon": ICON,
         "text": f"Invite your friends: win {CASH} or Derby VIP tickets"},
        {"key": f"{CAMPAIGN_ID}:winners", "from": _ms(CLOSES),
         "until": _ms(WINNERS_BANNER_UNTIL), "cta": "See", "href": "derby/",
         "icon": ICON, "text": after},
    ]


def _phases(showing):
    lines = {
        "before": f"Points start counting {_when(OPENS)}. Get your link now "
                  f"and have it ready.",
        "open": f"Ends {_when(CLOSES)}.",
        # The promise to announce, until the names are in; then it would
        # sit above them still promising.
        "closed": ("It's over! Thank you to everyone who took part."
                   if WINNER_NAMES else
                   "It's over! We are checking the points and will announce "
                   "the winners here and on our Facebook page."),
    }
    return "".join(
        f'<p class="qz-when" data-dy-phase="{key}"'
        f'{"" if key == showing else " hidden"}>{escape(text)}</p>'
        for key, text in lines.items())


def _flow():
    """1 Join, 2 Get your link, 3 Share it: the whole idea, before anything
    is asked of anyone. derby.js moves the highlight as the reader moves,
    so the step they are on is never a guess."""
    steps = (("Join", "Follow us + your number"),
             ("Get your link", "Your own, made for you"),
             ("Share it", "1 friend who joins = 1 point"))
    items = "".join(
        f'<li class="dy-flow-step{" is-now" if n == 1 else ""}" data-dy-step="{n}">'
        f'<span class="dy-flow-n" aria-hidden="true">{n}</span>'
        f'<span class="dy-flow-text"><strong>{escape(title)}</strong>'
        f"<small>{escape(sub)}</small></span></li>"
        for n, (title, sub) in enumerate(steps, 1))
    return f'<ol class="dy-flow" aria-label="How it works">{items}</ol>'


def _follow_buttons():
    """One tap per platform: the tap opens it in a new tab AND says you
    follow there. A button and a tick box for each was six things to do
    where one is enough; the tick boxes are still there, hidden, as the
    form's state."""
    buttons = "".join(
        f'<a class="dy-follow-btn is-{value}" href="{escape(url)}" '
        f'target="_blank" rel="noopener" data-dy-follow="{value}">'
        f'<span class="dy-follow-tick" aria-hidden="true">&#x2713;</span>'
        f"{escape(label)}</a>"
        f'<input type="checkbox" name="follow_{value}" value="{value}" hidden>'
        for value, label, url in FOLLOWS if url)
    return f"""<fieldset class="qz-details dy-follows" data-dy-follows>
      <legend class="qz-q-text">Follow EveryLeague</legend>
      <p class="dy-note">Tap at least one. Already following? Tap it anyway.</p>
      <div class="dy-follow-grid">{buttons}</div>
    </fieldset>"""


def _form():
    return f"""<form class="qz-form dy-form" novalidate>
    <h2 class="dy-step-head"><span class="dy-flow-n" aria-hidden="true">1</span>Join</h2>
    {_follow_buttons()}
    <fieldset class="qz-details">
      <legend class="qz-q-text">Your details</legend>
      <label class="qz-field"><span>Your name</span>
        <input name="name" type="text" autocomplete="name" maxlength="80"></label>
      <label class="qz-field"><span>Phone number</span>
        <input name="phone" type="tel" inputmode="tel" autocomplete="tel"
               placeholder="0991 234 567" maxlength="24">
        <small>We call or WhatsApp you on it if you win.</small></label>
      <label class="qz-field"><span>Email <em>(optional)</em></span>
        <input name="email" type="email" autocomplete="email" maxlength="120"></label>
    </fieldset>
    <p class="dy-consent">By joining you agree to the <a href="#rules">rules</a>
      and to EveryLeague sending you football updates. You can ask us to stop
      at any time.</p>
    <div class="qz-hp" aria-hidden="true"><label>Leave this empty
      <input name="hp_field" type="text" tabindex="-1" autocomplete="off"></label></div>
    <button class="qz-submit" type="submit">Join and get my link &rarr;</button>
    <p class="qz-status" role="status" aria-live="polite"></p>
    <noscript><p class="qz-status is-error">Joining needs JavaScript,
      which this browser has turned off.</p></noscript>
  </form>"""


def _done():
    """Steps 2 and 3, once a sign-up comes back with a code, and on every
    later visit from the same phone. It leads with the one thing to do next,
    WhatsApp, because a link someone does not know to send is no link."""
    return """<section class="qz-done dy-done" hidden tabindex="-1">
    <p class="dy-done-in">&#x2713; You're in!</p>
    <h2 class="dy-step-head"><span class="dy-flow-n" aria-hidden="true">2</span>Here is your link</h2>
    <div class="dy-link">
      <p class="dy-link-box" aria-label="Your link"></p>
      <button class="dy-copy" type="button">Copy</button>
    </div>
    <h2 class="dy-step-head"><span class="dy-flow-n" aria-hidden="true">3</span>Now share it</h2>
    <p class="qz-done-note">Send it to your friends and WhatsApp groups.
      <strong>Every friend who joins with your link = 1 point.</strong></p>
    <a class="qz-submit dy-share-wa" href="#" target="_blank" rel="noopener"
       data-dy-share="whatsapp">Send to friends on WhatsApp</a>
    <div class="dy-share-more">
      <a href="#" target="_blank" rel="noopener" data-dy-share="facebook">Share on Facebook</a>
      <button type="button" hidden data-dy-share="native">More ways to share</button>
    </div>
    <div class="dy-points" hidden>
      <p class="dy-points-n"><span>0</span> points</p>
      <p class="dy-points-note"></p>
    </div>
    <p class="dy-who" hidden></p>
    <button class="dy-reset" type="button">Not you? Join with another number</button>
  </section>"""


def _winners():
    """The winners, under the hero once they are named: the closed line
    promises they will be announced HERE."""
    if not WINNER_NAMES:
        return ""
    names = "".join(f"<li>{escape(n)}</li>" for n in WINNER_NAMES)
    note = (f'<p class="qz-lede">{escape(WINNERS_NOTE)}</p>'
            if WINNERS_NOTE else "")
    return f"""<section class="qz-winners">
  <p class="qz-eyebrow">{ICON} {escape(HOME)} v {escape(AWAY)}</p>
  <h2 class="qz-title">The winners</h2>
  <ul class="qz-winners-list">{names}</ul>
  {note}
</section>"""


def _rules():
    return f"""<details class="qz-rules" id="rules">
  <summary>Full rules</summary>
  <ul>
    <li>Free. One entry per phone number.</li>
    <li>To take part, follow EveryLeague on at least one of Facebook, our
      WhatsApp channel or LinkedIn. Winners show us that they follow before
      they are paid or given tickets.</li>
    <li>Points count from {escape(_when(OPENS))} until
      {escape(_when(CLOSES))}. You get 1 point for each new person who joins
      through your link with their own phone number. People already on our
      list, including everyone who asked for updates in Know Your Flames, do
      not earn points.</li>
    <li>The person with the most points wins {CASH}. If two or more are
      level, the winner is drawn at random between them.</li>
    <li>{TICKETS} VIP tickets for {escape(MATCH)} on
      {KICKOFF:%A} {KICKOFF.day} {KICKOFF:%B} are drawn at random among
      everyone else with at least {MIN_REFERRALS} points. One prize per
      person.</li>
    <li>Before any prize is given we phone some of the people each winner
      brought. Entries that were made up, or made without the person
      agreeing, are removed, and whoever made them is disqualified.</li>
    <li>We contact winners on the phone number they gave, by call, SMS or
      WhatsApp, after it ends. A winner we cannot reach the same day is
      replaced by the next in line.</li>
    <li>We may announce winners' names on EveryLeague and on our
      Facebook page.</li>
    <li>Your details are used to run this competition and to send you
      EveryLeague updates, which you can ask us to stop at any time. We never
      sell or share them; ask us to delete them through the &ldquo;Send us a
      message&rdquo; link at the bottom of the page.</li>
    <li>EveryLeague's own team cannot win.</li>
  </ul>
</details>"""


def render_page(*, css_ver, fl, updated, now, rpc_base="", key=""):
    """`rpc_base` is PostgREST's /rest/v1/rpc; "" renders a page that says it
    cannot send, build._quiz_endpoint's degradation."""
    showing = phase_at(now)
    title = f"Invite your friends, win {CASH}"
    social = render.social_meta(
        title, url=PAGE_URL,
        description=f"Win {CASH} or VIP tickets for {MATCH}. Join, get your "
                    "own link, and every friend who joins with it is a point.")
    config = json.dumps({
        "signup": f"{rpc_base}/submit_derby_signup" if rpc_base else "",
        "count": f"{rpc_base}/referral_count" if rpc_base else "",
        "inviter": f"{rpc_base}/referral_inviter" if rpc_base else "",
        "key": key,
        "campaign": CAMPAIGN_ID,
        "opens": _ms(OPENS),
        "closes": _ms(CLOSES),
        "page": PAGE_URL,
        "utm": UTM_CAMPAIGN,
        "home": HOME,
        "away": AWAY,
        "cash": CASH,
    }).replace("</", "<\\/")
    js_ver = render.file_version(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "static", "derby", "derby.js"))
    robots = '<meta name="robots" content="noindex">\n' if NOINDEX else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
{robots}<title>{escape(title)} | Everyleague</title>
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
<body class="landing quiz derby">
<div class="qz-wrap">
  {quiz._header(fl)}
  <main class="qz-main">
  <header class="qz-hero dy-hero">
    <p class="dy-invited" hidden>&#x1F44B; <span class="dy-invited-text">A friend
      invited you. Join below and they get a point.</span></p>
    <p class="qz-eyebrow">{HOME} v {AWAY} &middot; {KICKOFF:%A} {KICKOFF.day} {KICKOFF:%B}</p>
    <p class="dy-prize">Win <strong>{CASH}</strong></p>
    <p class="dy-prize-sub">or one of {TICKETS} VIP tickets for the Derby</p>
    <p class="dy-rule">Invite your friends. <strong>Each friend who joins = 1
      point.</strong> Most points wins.</p>
    {_phases(showing)}
  </header>
  {_winners()}
  {_flow()}
  {_form()}
  {_done()}
  {_rules()}
  <p class="ev-more"><a href="../events/">Past events and winners &rarr;</a></p>
  </main>
</div>
{render.footer(updated, "../")}
<script type="application/json" id="dy-config">{config}</script>
<script type="module" src="derby.js?v={js_ver}"></script>
</body>
</html>"""


def build_page(dist, *, css_ver, fl, updated, now, rpc_base="", key=""):
    out = os.path.join(dist, "derby")
    os.makedirs(out, exist_ok=True)
    render._write(os.path.join(out, "index.html"), render_page(
        css_ver=css_ver, fl=fl, updated=updated, now=now,
        rpc_base=rpc_base, key=key))
