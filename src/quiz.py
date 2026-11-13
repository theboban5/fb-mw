"""Know Your Flames: the quiz at /quiz/, and the banner that points to it (0067).

WHY. The Flames play Egypt twice in November 2026, Cairo on the 13th and
Lilongwe on the 17th, and the point of the campaign is visits: every answer is
on a Flames page, so a fan who enters has been through the Results, Squad and
History tabs to do it. Each round is three questions and a prize draw; the
second opens after the Cairo match because its first question is the score.

WHAT THIS DOES. `QUIZZES` is the single source of both rounds: their windows,
questions, prizes and answers. The page is written with every round and
every closed-state message already in it, and the one the BUILD thought was
current already showing, so a page that is never rebuilt still says something
true; static/quiz/quiz.js re-decides it on the reader's clock. The entry goes
to submit_quiz_entry, which enforces the same windows on the database clock.
tests/test_quiz.py fails if 0067's seed and this file disagree about a time or
a question id.

THE ANSWERS ARE IN A PUBLIC REPO, and that is fine: the quiz is open-book by
design, and every answer is on a public page of the site. What is not public
is who entered: entries live only in quiz_entries, read with the secret key
(scripts/quiz.py), never in the Dataset.

THE BANNER rides the search bar (render.search_widget), because the search bar
is the one piece of chrome every page carries; threading a banner through six
renderers' signatures would be noise. It is a strip, not a pop-up. A pop-up on
arrival is the most-closed thing on a phone, and it covers the score a reader
came for. It can be dismissed, and the dismissal lasts until the next round.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape

from . import home, render

CAT = timezone(timedelta(hours=2), "CAT")

FACEBOOK_URL = "https://www.facebook.com/share/1F6z4wKNjo/"
LINKEDIN_URL = "https://www.linkedin.com/company/everyleague/"
# The WhatsApp channel's link. Empty renders no button; a channel can
# only be followed, never joined on someone's behalf, so this link is the
# whole of what a page can do for it.
WHATSAPP_CHANNEL_URL = "https://whatsapp.com/channel/0029VbDUjB13WHTPw1uiXF1a"

PAGE_URL = render.SITE_URL + "/quiz/"


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    hint: str            # where to look, in words ("Malawi Flames → Squad")
    hint_href: str       # that page, from the site root
    options: tuple = ()  # (value, label) for a choice
    answer: str = ""     # the winning value; "" means worked out at the draw
    kind: str = "choice"  # or "score": two boxes, Malawi first, "1-2"
    match_id: str = ""   # kind=score: the nt_matches row whose result it is


@dataclass(frozen=True)
class Quiz:
    id: str
    round: int
    title: str
    prize: str           # the headline, "Win a Malawi Flames jersey"
    prize_rule: str      # the rules' sentence about the prize
    banner: str          # the banner's text while this round is open
    opens: datetime
    closes: datetime
    draw: str            # when the winners are drawn, in words
    winners: int
    questions: tuple
    # Who won, as the page names them: FIRST NAMES ONLY, because the rules
    # every entrant agreed to say first names, on Facebook. Empty renders
    # nothing; filled in by hand once the winners are confirmed.
    winner_names: tuple = ()
    winners_note: str = ""   # a sentence under the names, when one is owed


QUIZZES = (
    Quiz(
        id="flames-egypt-1",
        round=1,
        title="Know Your Flames: Round 1",
        prize="Win a Malawi Flames jersey",
        prize_rule="Round 1: two winners. The first drawn wins a new Malawi "
                   "national team jersey, the second a football jersey.",
        banner="Know Your Flames: win a Flames jersey",
        opens=datetime(2026, 11, 11, 0, 0, tzinfo=CAT),
        closes=datetime(2026, 11, 13, 18, 0, tzinfo=CAT),
        draw="Friday 13 November, before kick-off in Cairo",
        winners=2,
        questions=(
            Question(
                id="last-win",
                text="When did Malawi last beat Egypt?",
                hint="Malawi Flames → History",
                hint_href="flames/history.html",
                options=(("1987", "1987"), ("1992", "1992"),
                         ("2008", "2008"), ("2015", "2015")),
                answer="2008"),
            Question(
                id="ss-scorers",
                text="Who scored Malawi's goals in the 2–1 win over South "
                     "Sudan in September?",
                hint="Malawi Flames → Results",
                hint_href="flames/results.html",
                options=(("adepoju-njaliwa", "Adepoju & Njaliwa"),
                         ("mhango-mbulu", "Mhango & Mbulu"),
                         ("kondowe-madinga", "Kondowe & Madinga"),
                         ("mwaungulu-salima", "Mwaungulu & Salima")),
                answer="adepoju-njaliwa"),
            Question(
                id="japan",
                text="Which Flames squad member plays club football in Japan?",
                hint="Malawi Flames → Squad",
                hint_href="flames/squad.html",
                options=(("jaziya", "Washall Jaziya"),
                         ("mhango", "Mwisho Mhango"),
                         ("petro", "Charles Petro"),
                         ("nkhoma", "Lanjesi Nkhoma")),
                answer="jaziya"),
        ),
        # The first five drawn (scripts/quiz.py, 25 in order) who follow
        # EveryLeague on Facebook. The rules promised two winners; the
        # jerseys came to five, and the page says so rather than letting
        # the rules and the list quietly disagree.
        winner_names=("Charles", "Allan", "Thomas", "Leenah", "Gwazanga"),
        winners_note="We found three more jerseys, so Round 1 has five "
                     "winners instead of two. Congratulations, and thank "
                     "you to everyone who played.",
    ),
    Quiz(
        id="flames-egypt-2",
        round=2,
        title="Know Your Flames: Round 2",
        prize="Win tickets for Malawi v Egypt",
        prize_rule="Round 2: two winners, for Malawi v Egypt at Bingu "
                   "National Stadium, Lilongwe, on Tuesday 17 November. The "
                   "first drawn wins a VIP ticket, the second a standard "
                   "ticket, both collected in Lilongwe before kick-off. To "
                   "win Round 2 you must follow EveryLeague on Facebook: we "
                   "check the people drawn in the order they were drawn, and "
                   "the first two who follow our page win.",
        banner="Know Your Flames: win tickets for Malawi v Egypt",
        # Midnight, not 23:00 (0069): an hour after the final whistle was too
        # little time for the Cairo score to be entered and the site rebuilt.
        opens=datetime(2026, 11, 14, 0, 0, tzinfo=CAT),
        closes=datetime(2026, 11, 16, 15, 0, tzinfo=CAT),
        draw="Monday 16 November, so the winners can collect their tickets",
        winners=2,
        questions=(
            Question(
                id="cairo",
                text="What was the score in Cairo on 13 November?",
                hint="Malawi Flames → Results",
                hint_href="flames/results.html",
                kind="score",
                match_id="16"),
            Question(
                id="venue",
                text="Where will Malawi play Egypt on 17 November?",
                hint="Malawi Flames → Fixtures",
                hint_href="flames/fixtures.html",
                options=(("bingu", "Bingu National Stadium"),
                         ("kamuzu", "Kamuzu Stadium"),
                         ("mzuzu", "Mzuzu Stadium"),
                         ("civo", "Civo Stadium")),
                answer="bingu"),
            Question(
                id="matches",
                text="Roughly how many official matches have the Flames "
                     "played since 1957?",
                hint="Malawi Flames → History",
                hint_href="flames/history.html",
                options=(("380", "About 380"), ("480", "About 480"),
                         ("580", "About 580"), ("680", "About 680")),
                answer="680"),
        ),
    ),
)


def by_id(quiz_id):
    return next((q for q in QUIZZES if q.id == quiz_id), None)


def _ms(dt):
    return int(dt.timestamp() * 1000)


def _when(dt):
    """"Friday 13 November, 18:00 CAT", in Malawi time whatever `dt` was."""
    dt = dt.astimezone(CAT)
    return f"{dt:%A} {dt.day} {dt:%B}, {dt:%H:%M} CAT"


def phase_at(now):
    """static/quiz/rules.js phaseAt, for the build's clock: (state, quiz)."""
    for q in QUIZZES:
        if q.opens <= now < q.closes:
            return "open", q
    upcoming = [q for q in QUIZZES if now < q.opens]
    if not upcoming:
        return "over", QUIZZES[-1]
    return ("before" if upcoming[0] is QUIZZES[0] else "between"), upcoming[0]


# ── The banner ───────────────────────────────────────────────────────────────

def _banner_segments():
    """What the banner says when: each open round, and the gap between them.

    The gap has its own words, not silence: between the rounds is the evening
    of the Cairo match, the busiest the site will be all campaign, and the
    reader should hear that a second round is coming.
    """
    segs = []
    for i, q in enumerate(QUIZZES):
        if i:
            prev = QUIZZES[i - 1]
            if prev.closes < q.opens:
                # "00:00 CAT tonight" reads as a typo; midnight is the word.
                at = ("midnight" if (q.opens.hour, q.opens.minute) == (0, 0)
                      else f"{q.opens:%H:%M} CAT")
                segs.append({
                    "key": f"{q.id}:soon", "from": _ms(prev.closes),
                    "until": _ms(q.opens), "cta": "How",
                    "text": f"Know Your Flames Round {q.round} opens at "
                            f"{at} tonight: "
                            f"{q.prize[0].lower()}{q.prize[1:]}"})
        segs.append({"key": q.id, "from": _ms(q.opens), "until": _ms(q.closes),
                     "cta": "Play", "text": q.banner})
    return segs


_BANNER_JS = """(function(){
var s=document.currentScript,b=s&&s.previousElementSibling;
if(!b||!b.hasAttribute('data-qz-banner'))return;
function gone(){if(b.parentNode)b.parentNode.removeChild(b);}
var segs,now=Date.now(),seg=null,i;
try{segs=JSON.parse(b.getAttribute('data-qz-banner'));}catch(e){return;}
for(i=0;i<segs.length;i++){if(now>=segs[i].from&&now<segs[i].until){seg=segs[i];break;}}
if(!seg){gone();return;}
var off=null;try{off=localStorage.getItem('qz-banner-hidden');}catch(e){}
if(off===seg.key){gone();return;}
b.querySelector('.qz-banner-text').textContent=seg.text;
b.querySelector('.qz-banner-go').textContent=seg.cta+' \\u2192';
b.hidden=false;
var x=b.querySelector('.qz-banner-x');x.hidden=false;
x.addEventListener('click',function(){gone();try{localStorage.setItem('qz-banner-hidden',seg.key);}catch(e){}});
})();"""


def banner(css_prefix, now):
    """The strip above the search bar, or "" once every round has closed.

    It ships showing what was true at build time, so a reader without JS
    still sees it; the inline script re-picks the words on the reader's clock,
    removes it once the campaign is over (a page built on Sunday is still
    served on Tuesday), and remembers a dismissal per round, so closing Round
    1's banner does not hide Round 2's.
    """
    segs = _banner_segments()
    now_ms = _ms(now)
    current = next((s for s in segs if s["from"] <= now_ms < s["until"]),
                   None) or next((s for s in segs if now_ms < s["from"]), None)
    if current is None:
        return ""
    p = escape(css_prefix)
    return (
        f'<div class="qz-banner" data-qz-banner="{escape(json.dumps(segs))}">'
        f'<a class="qz-banner-link" href="{p}quiz/">'
        '<span class="qz-banner-flag" aria-hidden="true">&#x1F1F2;&#x1F1FC;</span>'
        f'<span class="qz-banner-text">{escape(current["text"])}</span>'
        f'<span class="qz-banner-go">{escape(current["cta"])} &rarr;</span></a>'
        '<button class="qz-banner-x" type="button" hidden '
        'aria-label="Hide this banner">&#x2715;</button>'
        f"</div><script>{_BANNER_JS}</script>"
    )


# ── The page ─────────────────────────────────────────────────────────────────

def _choice(quiz, q, n, total):
    opts = "".join(
        f'<label class="qz-opt"><input type="radio" '
        f'name="{escape(quiz.id)}:{escape(q.id)}" value="{escape(value)}">'
        f"<span>{escape(label)}</span></label>"
        for value, label in q.options)
    return _fieldset(q, n, total, f'<div class="qz-opts">{opts}</div>')


def _score(quiz, q, n, total):
    box = ('<input class="qz-score-box" name="{name}" type="text" '
           'inputmode="numeric" pattern="[0-9]*" maxlength="2" '
           'autocomplete="off" aria-label="{label}">')
    base = f"{escape(quiz.id)}:{escape(q.id)}"
    body = (
        '<div class="qz-score">'
        '<span class="qz-score-team">Malawi</span>'
        + box.format(name=f"{base}:home", label="Malawi's goals")
        + '<span class="qz-score-dash" aria-hidden="true">&ndash;</span>'
        + box.format(name=f"{base}:away", label="Egypt's goals")
        + '<span class="qz-score-team">Egypt</span></div>')
    return _fieldset(q, n, total, body)


def _fieldset(q, n, total, body):
    return (
        f'<fieldset class="qz-q" data-qz-q="{escape(q.id)}" '
        f'data-kind="{escape(q.kind)}">'
        f'<legend class="qz-q-text"><span class="qz-q-n">Question {n} of '
        f"{total}</span>{escape(q.text)}</legend>"
        f'<a class="qz-hint" href="../{escape(q.hint_href)}">Find it: '
        f"{escape(q.hint)}</a>{body}</fieldset>")


def _follow():
    links = [(WHATSAPP_CHANNEL_URL, "Follow our WhatsApp channel"),
             (FACEBOOK_URL, "Follow us on Facebook"),
             (LINKEDIN_URL, "EveryLeague on LinkedIn")]
    items = "".join(
        f'<a class="qz-follow-link" href="{escape(url)}" target="_blank" '
        f'rel="noopener">{escape(label)}</a>'
        for url, label in links if url)
    return f'<div class="qz-follow">{items}</div>'


def _share(quiz):
    text = f"{quiz.prize}! Know Your Flames: three questions on EveryLeague."
    return (f'<button class="qz-share" type="button" hidden '
            f'data-qz-share="{escape(PAGE_URL)}" data-qz-text="{escape(text)}">'
            "Share the quiz</button>")


def _round(quiz, showing):
    total = len(quiz.questions)
    questions = "".join(
        (_score if q.kind == "score" else _choice)(quiz, q, n, total)
        for n, q in enumerate(quiz.questions, 1))
    return f"""<section class="qz-round" data-qz-round="{escape(quiz.id)}"{"" if showing else " hidden"}>
  <header class="qz-hero">
    <p class="qz-eyebrow">&#x1F1F2;&#x1F1FC; Know Your Flames &middot; Round {quiz.round} of {len(QUIZZES)}</p>
    <h2 class="qz-title">{escape(quiz.prize)}</h2>
    <p class="qz-lede">{total} questions about the Malawi Flames. Every answer is
      somewhere on EveryLeague. Get them all right and you are in the draw.</p>
    <p class="qz-when">Closes {escape(_when(quiz.closes))}</p>
  </header>
  <form class="qz-form" novalidate>
    {questions}
    <fieldset class="qz-details">
      <legend class="qz-q-text">Your details</legend>
      <label class="qz-field"><span>Your name</span>
        <input name="name" type="text" autocomplete="name" maxlength="80"></label>
      <label class="qz-field"><span>Phone number</span>
        <input name="phone" type="tel" inputmode="tel" autocomplete="tel"
               placeholder="0991 234 567" maxlength="24">
        <small>We call or WhatsApp this number if you win.</small></label>
      <label class="qz-field"><span>Email <em>(optional)</em></span>
        <input name="email" type="email" autocomplete="email" maxlength="120"></label>
      <label class="qz-check"><input name="consent" type="checkbox">
        <span>I agree to the <a href="#rules">rules</a>, and EveryLeague may
        contact me on this number about the prize.</span></label>
      <label class="qz-check"><input name="updates" type="checkbox">
        <span>Send me EveryLeague updates (results, records and news) on
        WhatsApp or email. I can stop them any time.</span></label>
      <div class="qz-hp" aria-hidden="true"><label>Leave this empty
        <input name="hp_field" type="text" tabindex="-1" autocomplete="off"></label></div>
    </fieldset>
    <button class="qz-submit" type="submit">Enter the draw</button>
    <p class="qz-status" role="status" aria-live="polite"></p>
    <noscript><p class="qz-status is-error">Sending an entry needs JavaScript,
      which this browser has turned off.</p></noscript>
  </form>
  <div class="qz-done" hidden>
    <p class="qz-done-tick" aria-hidden="true">&#x2713;</p>
    <h2 class="qz-title">You're in!</h2>
    <p class="qz-done-note">If every answer is right, you are in the draw on
      {escape(quiz.draw)}. We will contact the winners on the number they gave.</p>
    {_share(quiz)}
    {_follow()}
    <a class="qz-back" href="../flames/">Malawi Flames: results, squad and history &rarr;</a>
  </div>
</section>"""


def _states(showing):
    first, last = QUIZZES[0], QUIZZES[-1]
    between = []
    for prev, q in zip(QUIZZES, QUIZZES[1:]):
        # Shown only between the two windows, so a midnight opening is always
        # tonight's; "Saturday 14 November, 00:00" read on Friday evening
        # sounds a day away.
        when = (f"at midnight tonight ({_when(q.opens)})"
                if (q.opens.hour, q.opens.minute) == (0, 0) else _when(q.opens))
        between.append(f"""<section class="qz-state" data-qz-state="between"{"" if showing == ("between", q) else " hidden"}>
  <p class="qz-eyebrow">&#x1F1F2;&#x1F1FC; Know Your Flames</p>
  <h2 class="qz-title">Round {prev.round} has closed</h2>
  <p class="qz-lede">Thank you to everyone who entered. Round {q.round} opens
    {escape(when)}, after the final whistle in Cairo. This time:
    {escape(q.prize[0].lower() + q.prize[1:])}.</p>
  {_follow()}
  <a class="qz-back" href="../flames/">Follow the match on the Flames page &rarr;</a>
</section>""")
    before = f"""<section class="qz-state" data-qz-state="before"{"" if showing == ("before", first) else " hidden"}>
  <p class="qz-eyebrow">&#x1F1F2;&#x1F1FC; Know Your Flames</p>
  <h2 class="qz-title">Opens {escape(_when(first.opens))}</h2>
</section>"""
    over = f"""<section class="qz-state" data-qz-state="over"{"" if showing == ("over", last) else " hidden"}>
  <p class="qz-eyebrow">&#x1F1F2;&#x1F1FC; Know Your Flames</p>
  <h2 class="qz-title">Know Your Flames has closed</h2>
  <p class="qz-lede">Thank you for playing. The winners are announced on our
    Facebook page.</p>
  {_follow()}
  <a class="qz-back" href="../flames/">Malawi Flames: results, squad and history &rarr;</a>
</section>"""
    return before + "".join(between) + over


def _winners():
    """A card per round that has named its winners, under whatever the page
    is showing: Round 1's winners stay up while Round 2 is open."""
    out = []
    for q in QUIZZES:
        if not q.winner_names:
            continue
        names = "".join(f"<li>{escape(n)}</li>" for n in q.winner_names)
        note = (f'<p class="qz-lede">{escape(q.winners_note)}</p>'
                if q.winners_note else "")
        out.append(f"""<section class="qz-winners">
  <p class="qz-eyebrow">&#x1F1F2;&#x1F1FC; {escape(q.title)}</p>
  <h2 class="qz-title">The winners</h2>
  <ul class="qz-winners-list">{names}</ul>
  {note}
</section>""")
    return "".join(out)


def _rules():
    rounds = "".join(
        f"<li>{escape(q.title)} is open from {escape(_when(q.opens))} until "
        f"{escape(_when(q.closes))}. {escape(q.prize_rule)}</li>"
        for q in QUIZZES)
    return f"""<section class="qz-rules" id="rules">
  <h2>Rules</h2>
  <ul>
    <li>Free to enter. One entry per phone number in each round.</li>
    {rounds}
    <li>Winners are drawn at random from the entries that answer every question
      correctly.</li>
    <li>We contact winners on the phone number they gave, by call, SMS or
      WhatsApp. A winner we cannot reach within 24 hours (for Round 2, by 10:00
      CAT on Tuesday 17 November) is replaced by another draw.</li>
    <li>We may announce winners' first names on our Facebook page.</li>
    <li>Your details are used to run this competition and to contact winners.
      If you tick the updates box we will also send you EveryLeague news, and
      you can ask us to stop at any time. We never sell or share your details;
      ask us to delete them through the &ldquo;Send us a message&rdquo; link at
      the bottom of the page.</li>
    <li>EveryLeague's own team cannot win.</li>
  </ul>
</section>"""


def _header(fl):
    # The wordmark row's "Leagues" pill jumps to #leagues on the homepage's
    # shell; this page has no league list, so it goes to the homepage's.
    return home.brand_header(fl, "../").replace(
        'href="#leagues"', 'href="../#leagues"')


def render_page(*, css_ver, fl, updated, now, rpc_url="", key=""):
    state, current = phase_at(now)
    showing = (state, current)
    rounds = "".join(_round(q, state == "open" and q is current)
                     for q in QUIZZES)
    lead = current if state == "open" else QUIZZES[0]
    title = f"Know Your Flames: {lead.prize[0].lower()}{lead.prize[1:]}"
    social = render.social_meta(
        title, url=PAGE_URL,
        description="Three questions about the Malawi Flames. Every answer is "
                    "on EveryLeague. Get them all right and you are in the "
                    "draw.")
    config = json.dumps({
        "rpc": rpc_url,
        "key": key,
        "quizzes": [{"id": q.id, "opens": _ms(q.opens), "closes": _ms(q.closes)}
                    for q in QUIZZES],
    }).replace("</", "<\\/")
    js_ver = render.file_version(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "static", "quiz", "quiz.js"))
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
<body class="landing quiz">
<div class="qz-wrap">
  {_header(fl)}
  <main class="qz-main">
{rounds}
{_states(showing)}
{_winners()}
{_rules()}
  </main>
</div>
{render.footer(updated)}
<script type="application/json" id="qz-config">{config}</script>
<script type="module" src="quiz.js?v={js_ver}"></script>
</body>
</html>"""


def build_page(dist, *, css_ver, fl, updated, now, rpc_url="", key=""):
    out = os.path.join(dist, "quiz")
    os.makedirs(out, exist_ok=True)
    render._write(os.path.join(out, "index.html"), render_page(
        css_ver=css_ver, fl=fl, updated=updated, now=now,
        rpc_url=rpc_url, key=key))
