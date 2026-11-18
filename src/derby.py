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

UNLISTED FOR NOW. Nothing links here, the page carries noindex, and no banner
points at it, so it can be tried and changed before anyone is sent to it.
Promoting it is a separate step (the quiz banner's segments, and dropping
NOINDEX).

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

# The same id and times as 0072's seed; tests/test_derby.py compares.
CAMPAIGN_ID = "derby-2026"
OPENS = datetime(2026, 11, 19, 0, 0, tzinfo=CAT)
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

# Testing, not finished: off once the page is promoted.
NOINDEX = True

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


def _phases(showing):
    lines = {
        "before": f"The race starts {_when(OPENS)}. Get your link now: "
                  f"friends who sign up from then on count for you.",
        "open": f"Closes {_when(CLOSES)}.",
        "closed": "The race has closed. We are checking the counts and "
                  "will announce the winners here and on our Facebook page.",
    }
    return "".join(
        f'<p class="qz-when" data-dy-phase="{key}"'
        f'{"" if key == showing else " hidden"}>{escape(text)}</p>'
        for key, text in lines.items())


def _follow_rows():
    rows = "".join(
        f'<div class="dy-follow-row">'
        f'<a class="qz-follow-link" href="{escape(url)}" target="_blank" '
        f'rel="noopener" data-dy-follow="{value}">Follow on {escape(label)}</a>'
        f'<label class="qz-check"><input type="checkbox" name="follow_{value}" '
        f'value="{value}"><span>I follow on {escape(label)}</span></label>'
        f"</div>"
        for value, label, url in FOLLOWS if url)
    return f"""<fieldset class="qz-details dy-follows" data-dy-follows>
      <legend class="qz-q-text"><span class="qz-q-n">Step 1</span>Follow EveryLeague</legend>
      <p class="dy-note">On at least one of these, then tick where you follow
        us. Winners show us before they are paid.</p>
      {rows}
    </fieldset>"""


def _form():
    return f"""<form class="qz-form dy-form" novalidate>
    {_follow_rows()}
    <fieldset class="qz-details">
      <legend class="qz-q-text"><span class="qz-q-n">Step 2</span>Your details</legend>
      <label class="qz-field"><span>Your name</span>
        <input name="name" type="text" autocomplete="name" maxlength="80"></label>
      <label class="qz-field"><span>Phone number</span>
        <input name="phone" type="tel" inputmode="tel" autocomplete="tel"
               placeholder="0991 234 567" maxlength="24">
        <small>One sign-up per number. We call or WhatsApp it if you win.</small></label>
      <label class="qz-field"><span>Email <em>(optional)</em></span>
        <input name="email" type="email" autocomplete="email" maxlength="120"></label>
    </fieldset>
    <fieldset class="qz-q dy-pred" data-kind="score">
      <legend class="qz-q-text"><span class="qz-q-n">Optional</span>Your prediction</legend>
      <div class="qz-score">
        <span class="qz-score-team">{HOME}</span>
        <input class="qz-score-box" name="pred_home" type="text" inputmode="numeric"
               pattern="[0-9]*" maxlength="2" autocomplete="off" aria-label="{HOME}'s goals">
        <span class="qz-score-dash" aria-hidden="true">&ndash;</span>
        <input class="qz-score-box" name="pred_away" type="text" inputmode="numeric"
               pattern="[0-9]*" maxlength="2" autocomplete="off" aria-label="{AWAY}'s goals">
        <span class="qz-score-team">{AWAY}</span>
      </div>
      <p class="dy-note">Just for fun: it goes in the message your friends see.</p>
    </fieldset>
    <p class="dy-consent">Signing up means EveryLeague may send you updates
      (results, records and news) on WhatsApp or email, and that you agree to
      the <a href="#rules">rules</a>. You can ask us to stop at any time.</p>
    <div class="qz-hp" aria-hidden="true"><label>Leave this empty
      <input name="hp_field" type="text" tabindex="-1" autocomplete="off"></label></div>
    <button class="qz-submit" type="submit">Get my link</button>
    <p class="qz-status" role="status" aria-live="polite"></p>
    <noscript><p class="qz-status is-error">Signing up needs JavaScript,
      which this browser has turned off.</p></noscript>
  </form>"""


def _done():
    """Shown once a sign-up comes back with a code, and on every later visit
    from the same phone. Every button ships hidden or inert and the script
    fills in the link: a page without JS never reaches this panel."""
    return """<section class="qz-done dy-done" hidden>
    <p class="qz-done-tick" aria-hidden="true">&#x2713;</p>
    <h2 class="qz-title">Your link</h2>
    <p class="qz-done-note">Send it to your friends. Everyone new who signs up
      through it counts for you.</p>
    <div class="dy-link">
      <p class="dy-link-box" aria-label="Your link"></p>
      <button class="dy-copy" type="button">Copy</button>
    </div>
    <div class="dy-share">
      <a class="qz-submit dy-share-wa" href="#" target="_blank" rel="noopener"
         data-dy-share="whatsapp">Send on WhatsApp</a>
      <a class="qz-share dy-share-fb" href="#" target="_blank" rel="noopener"
         data-dy-share="facebook">Share on Facebook</a>
      <button class="qz-share" type="button" hidden data-dy-share="native">More ways to share</button>
    </div>
    <p class="dy-count" aria-live="polite" hidden></p>
    <button class="dy-reset" type="button">Not you? Sign up with another number</button>
  </section>"""


def _rules():
    return f"""<section class="qz-rules" id="rules">
  <h2>Rules</h2>
  <ul>
    <li>Free. One sign-up per phone number.</li>
    <li>To take part, follow EveryLeague on at least one of Facebook, our
      WhatsApp channel or LinkedIn. Winners show us that they follow before
      they are paid or given tickets.</li>
    <li>Referrals count from {escape(_when(OPENS))} until
      {escape(_when(CLOSES))}. A referral is a new person who signs up through
      your link with their own phone number. People already on our list,
      including everyone who asked for updates in Know Your Flames, do not
      count.</li>
    <li>The person with the most referrals that count wins {CASH}. If two or
      more are level, the winner is drawn at random between them.</li>
    <li>{TICKETS} VIP tickets for {escape(MATCH)} on
      {KICKOFF:%A} {KICKOFF.day} {KICKOFF:%B} are drawn at random among
      everyone else with at least {MIN_REFERRALS} referrals that count. One
      prize per person.</li>
    <li>Before any prize is given we phone some of the people each winner
      referred. Sign-ups that were made up, or made without the person
      agreeing, are removed, and whoever made them is disqualified.</li>
    <li>We contact winners on the phone number they gave, by call, SMS or
      WhatsApp, after the race closes. A winner we cannot reach the same day
      is replaced by the next in line.</li>
    <li>We may announce winners' names on EveryLeague and on our
      Facebook page.</li>
    <li>Your details are used to run this campaign and to send you EveryLeague
      updates, which you can ask us to stop at any time. We never sell or
      share them; ask us to delete them through the &ldquo;Send us a
      message&rdquo; link at the bottom of the page.</li>
    <li>EveryLeague's own team cannot win.</li>
  </ul>
</section>"""


def render_page(*, css_ver, fl, updated, now, rpc_base="", key=""):
    """`rpc_base` is PostgREST's /rest/v1/rpc; "" renders a page that says it
    cannot send, build._quiz_endpoint's degradation."""
    showing = phase_at(now)
    title = f"Bring your friends to the Derby: win {CASH}"
    social = render.social_meta(
        title, url=PAGE_URL,
        description=f"Win {CASH} or VIP tickets for {MATCH}. Sign up on "
                    "EveryLeague, get your own link, and every friend who "
                    "joins counts for you.")
    config = json.dumps({
        "signup": f"{rpc_base}/submit_derby_signup" if rpc_base else "",
        "count": f"{rpc_base}/referral_count" if rpc_base else "",
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
  <header class="qz-hero">
    <p class="qz-eyebrow">{HOME} v {AWAY} &middot; {KICKOFF:%A} {KICKOFF.day} {KICKOFF:%B}, {KICKOFF:%H:%M}</p>
    <h2 class="qz-title">Bring your friends to the Derby</h2>
    <p class="qz-lede">Win <strong>{CASH}</strong>, or one of {TICKETS} VIP
      tickets for the Derby. Get your own link, send it to your friends, and
      every friend who signs up counts for you.</p>
    {_phases(showing)}
    <p class="dy-invited" hidden>A friend sent you here. Sign up below and it
      counts for them.</p>
  </header>
  <ol class="dy-steps">
    <li><strong>Follow</strong> EveryLeague and sign up.</li>
    <li><strong>Send</strong> your link to your friends.</li>
    <li>The most friends signed up by {CLOSES:%A} {CLOSES:%H:%M}
      wins <strong>{CASH}</strong>. {MIN_REFERRALS} or more puts you in the
      draw for {TICKETS} VIP tickets.</li>
  </ol>
  {_form()}
  {_done()}
  {_rules()}
  </main>
</div>
{render.footer(updated)}
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
