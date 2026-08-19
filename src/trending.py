"""The homepage carousel: the `trending` tab (0030) as markup.

The first thing on everyleague.co used to be one hand-written card. It lived
in an f-string in build.py, it named a tournament and a fixture in prose, and
its own docstring recorded what that cost — the card told readers the
Scorchers were "about to take on Cameroon on Sunday" for a day after Cameroon
had won that final. Editing it meant editing Python and waiting for CI, so it
was edited about once a month, and the front of the site aged in public.

This module renders the same slot from data an administrator writes in
/report: two to five cards, swipeable, rotating. Nothing in here dates,
because nothing in here is written here.

Shape, and why:

  * **A card is one <a>.** Same rule as the featured card it replaces and the
    date card beneath it — the whole card is the tap target, which is what
    matters on a phone, and the CTA is a styled <span> because an <a> may not
    nest. A card with no link renders as a <div> instead of a dead link.
  * **The track is a scroll-snap row, so the carousel works with no
    JavaScript at all.** Swiping is native browser behaviour; the script at
    the bottom of the landing page only adds the dots and the auto-advance.
    Script off, or script broken, and the reader still gets every card.
  * **The page body never scrolls sideways.** The track scrolls inside
    itself, which is the same rule every table on this site follows.
  * **Every part is optional except the headline.** No image renders a
    text-only card, no eyebrow renders no label, no body renders a headline
    and a link. Missing data renders nothing — never a placeholder.

Images are resolved OUTSIDE this module: build.py downloads each one, shrinks
it and hands in a {image_path: url} map, so everything here stays pure and
testable and nothing in the render path can touch the network.
"""

from html import escape

# What the button says when a card's author did not say. Deliberately vague,
# because a card links at anything: a league table, a player, a date.
DEFAULT_CTA = "Read more"

# Above this many live cards the homepage is carrying more photos than a phone
# on an expensive connection should be asked to fetch, and nobody swipes that
# far anyway. Not enforced — build.py prints it, the portal says it — because
# refusing to render a card somebody published is a worse failure than a heavy
# page, and this file is not where that decision belongs.
COMFORTABLE_LIVE = 5


def _media(card, images):
    """The photo, or "" — which is a whole, correct card, not a broken one."""
    url = images.get(card.image_path) if card.image_path else None
    if not url:
        return ""
    # width/height are the aspect box the CSS enforces, not the file's own
    # size: they exist to reserve the space before the bytes arrive, so the
    # headline underneath does not jump down the page mid-read.
    return (
        '<span class="el-trend-media">'
        f'<img src="{escape(url)}" alt="{escape(card.image_alt)}"'
        ' width="1200" height="675" loading="lazy" decoding="async">'
        "</span>"
    )


def _credit(card, media):
    """"Photo: FAM Media", or "" — and "" whenever there is no photo.

    A credit with no image to credit is nonsense, so it is dropped HERE rather
    than asked of whoever wrote the card: they may well clear a photo from a
    card months after typing its credit, and the site should not then thank
    somebody for a picture nobody can see.
    """
    if not (media and card.image_credit):
        return ""
    return (f'<span class="el-trend-credit">Photo: '
            f'{escape(card.image_credit)}</span>')


def _body(card, media=""):
    parts = []
    if card.eyebrow:
        parts.append(
            f'<span class="el-trend-eyebrow">{escape(card.eyebrow)}</span>')
    parts.append(f'<span class="el-trend-title">{escape(card.headline)}</span>')
    if card.body:
        parts.append(f'<span class="el-trend-copy">{escape(card.body)}</span>')
    if card.link_url:
        label = escape(card.link_label or DEFAULT_CTA)
        parts.append(
            f'<span class="el-trend-cta">{label}'
            ' <span class="el-trend-arrow" aria-hidden="true">&#x2192;</span>'
            "</span>")
    # Last, and smallest. It is an obligation to the photographer, not part of
    # the story — putting it above the words would make the card read as being
    # about the picture.
    parts.append(_credit(card, media))
    return f'<span class="el-trend-body">{"".join(parts)}</span>'


def card_html(card, images, index, total):
    """One slide: a labelled wrapper around the card.

    THE WRAPPER IS NOT DECORATION. The ARIA carousel pattern wants each slide
    to be role="group" with aria-roledescription="slide", and the label counts
    it out loud ("2 of 3") because a screen reader meets these as three
    siblings with no other clue that they are one rotating thing. Putting that
    role on the card itself would OVERRIDE the <a>'s own role, so a card that
    is a link would stop being announced as one — the wrapper is what lets the
    slide be a slide and the card still be a link.
    """
    media = _media(card, images)
    inner = media + _body(card, media)
    # Every slide is the height of the tallest one — a carousel whose cards
    # change height shoves the whole page up and down on every swipe. That
    # leaves a photo-less card with space under its headline, so `is-plain`
    # centres the words in it rather than stranding them at the top.
    cls = f'class="el-trend-card{"" if media else " is-plain"}"'
    inner = (f"<div {cls}>{inner}</div>" if not card.link_url
             else f'<a {cls} href="{escape(card.link_url)}">{inner}</a>')
    return (
        '<div class="el-trend-slide" role="group" aria-roledescription="slide"'
        f' aria-label="{index + 1} of {total}">{inner}</div>'
    )


def carousel(cards, images=None):
    """The whole section, or "" when nothing is live.

    "" is the important return value: it is what makes the landing page fall
    back to its hand-written feature card, so a site with an empty `trending`
    table — every build before this one, and every offline build — looks
    exactly as it did before.
    """
    cards = list(cards)
    if not cards:
        return ""
    images = images or {}
    total = len(cards)
    slides = "".join(card_html(c, images, i, total) for i, c in enumerate(cards))

    # The dots ship hidden and the script unhides them. Without script they
    # would be buttons that do nothing, which is worse than no buttons: the
    # track is swipeable either way.
    dots = "".join(
        f'<button class="el-trend-dot{" is-on" if i == 0 else ""}" type="button"'
        f' data-trend-dot="{i}" aria-label="Card {i + 1} of {total}"'
        f' aria-current="{"true" if i == 0 else "false"}"></button>'
        for i in range(total)
    ) if total > 1 else ""

    nav = (f'<div class="el-trend-dots" data-trend-dots hidden>{dots}</div>'
           if dots else "")

    return (
        '<section class="el-trend" data-trend aria-roledescription="carousel"'
        ' aria-label="Trending on Everyleague">'
        f'<div class="el-trend-track" data-trend-track>{slides}</div>'
        f"{nav}"
        "</section>"
    )


# The only script the carousel needs, and it needs none of it to work: the
# track is a scroll-snap row that swipes natively. This adds the dots (hidden
# in the markup until it runs, so they are never buttons that do nothing) and
# an auto-advance.
#
# THE ROTATION HOLDS RATHER THAN STOPS. It used to stop for good the first
# time anyone touched the track, on the reasoning that somebody who has swiped
# has chosen a card. Half right: yanking a card away from someone two seconds
# after they picked it is the single most irritating thing a carousel does —
# but a reader who swiped once, read it, and then sat still should get the
# rest of the cards rather than a carousel that quietly died. So a swipe, a
# dot tap, a wheel or a key HOLDS the rotation for RESUME_MS from the last
# input, and it picks up again from wherever they left it.
#
# Four things stop it moving outright, and each is somebody saying "not now":
#   * prefers-reduced-motion — then it never starts, and dot taps jump rather
#     than glide. A reader who asked the system for less motion asked for all
#     of it.
#   * the tab being in the background, so a forgotten tab is not silently
#     scrolling for an hour.
#   * a MOUSE resting over the carousel, or focus inside it — the pointerType
#     test matters: on a phone, pointerenter fires on a tap and the matching
#     pointerleave may never come, which would freeze the carousel for good.
#   * any input in the last RESUME_MS, per the paragraph above.
#
# Written as ES5-flavoured plain script for the same reason the tab switcher
# above it is: it is inlined into index.html and must parse on whatever
# browser a five-year-old Android is carrying.
CAROUSEL_JS = """
(function(){
  var root=document.querySelector('[data-trend]');
  if(!root) return;
  var track=root.querySelector('[data-trend-track]');
  var dotBox=root.querySelector('[data-trend-dots]');
  var cards=track?track.querySelectorAll('.el-trend-slide'):[];
  if(!track||cards.length<2) return;
  // Five seconds a card, and it wraps — the last one hands back to the first.
  // Long enough to read a headline and the first line under it, short enough
  // that somebody who lands on the page and does nothing sees all three.
  var STEP_MS=5000;
  // How long an input holds it. Comfortably longer than STEP_MS, so a reader
  // who swipes to a card gets to finish it before anything else moves.
  var RESUME_MS=15000;
  var dots=[];
  if(dotBox){
    dotBox.hidden=false;
    dots=dotBox.querySelectorAll('[data-trend-dot]');
  }
  var current=0;
  // Read once, up here, because it governs BOTH the auto-advance (which never
  // starts) and the dot taps (which jump rather than glide). A reader who has
  // asked the system for less motion asked for all of it.
  var calm=!!(window.matchMedia
              &&window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  // Centred, matching scroll-snap-align: center — so the dot a reader taps
  // and the card that arrives cannot disagree. offsetLeft is safe to subtract
  // this way because the track is position:static, which makes both it and
  // the card resolve against the same offsetParent.
  function show(i,smooth){
    var card=cards[i];
    if(!card) return;
    var left=card.offsetLeft-track.offsetLeft
             -(track.clientWidth-card.offsetWidth)/2;
    track.scrollTo({left:left,behavior:smooth?'smooth':'auto'});
  }
  function mark(i){
    current=i;
    for(var d=0;d<dots.length;d++){
      var on=(d===i);
      dots[d].classList.toggle('is-on',on);
      dots[d].setAttribute('aria-current',on?'true':'false');
    }
  }
  for(var d=0;d<dots.length;d++){
    (function(n){
      dots[n].addEventListener('click',function(){ hold(); show(n,!calm); mark(n); });
    })(d);
  }
  // Which card is nearest the middle of the viewport wins. Reading the scroll
  // position beats listening for a snap event, which older Safari never fires.
  var pending;
  track.addEventListener('scroll',function(){
    clearTimeout(pending);
    pending=setTimeout(function(){
      var middle=track.scrollLeft+track.clientWidth/2, best=0, gap=Infinity;
      for(var i=0;i<cards.length;i++){
        var c=cards[i];
        var centre=c.offsetLeft-track.offsetLeft+c.offsetWidth/2;
        var away=Math.abs(centre-middle);
        if(away<gap){ gap=away; best=i; }
      }
      if(best!==current) mark(best);
    },90);
  },{passive:true});

  // `until` is the clock the whole hold runs on: one number, checked on each
  // tick, rather than a timer that has to be cancelled and rebuilt on every
  // one of a swipe's many events.
  var until=0, resting=false;
  function hold(){ until=Date.now()+RESUME_MS; }
  function tick(){
    if(document.hidden||resting||Date.now()<until) return;
    var next=(current+1)%cards.length;
    show(next,true); mark(next);
  }
  // Any real input holds it. NOT the track's scroll event — show() scrolls the
  // track itself, so listening there would make the carousel hold itself off
  // the moment it advanced once, and it would never move again.
  ['pointerdown','touchstart','keydown','wheel'].forEach(function(ev){
    root.addEventListener(ev,hold,{passive:true});
  });
  // A mouse resting on the card, or focus inside it, means somebody is
  // reading. Guarded on pointerType: see the note above about phones, where
  // pointerenter fires on a tap and pointerleave may never arrive.
  root.addEventListener('pointerenter',function(e){
    if(!e.pointerType||e.pointerType==='mouse') resting=true;
  });
  root.addEventListener('pointerleave',function(e){
    if(!e.pointerType||e.pointerType==='mouse') resting=false;
  });
  root.addEventListener('focusin',function(){ resting=true; });
  root.addEventListener('focusout',function(){ resting=false; });
  if(calm) return;
  setInterval(tick,STEP_MS);
})();
"""
