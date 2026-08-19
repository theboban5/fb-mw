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


def _body(card):
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
    inner = media + _body(card)
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
# The auto-advance stops for good the first time the reader touches the track.
# Somebody who has swiped has chosen a card, and yanking it away from them two
# seconds later is the single most irritating thing a carousel does. It also
# never starts under prefers-reduced-motion, and pauses with the tab hidden so
# a backgrounded tab is not silently scrolling for an hour.
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
      dots[n].addEventListener('click',function(){ stop(); show(n,!calm); mark(n); });
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

  var timer=null;
  function stop(){ if(timer){ clearInterval(timer); timer=null; } }
  if(calm) return;
  function play(){
    stop();
    timer=setInterval(function(){
      if(document.hidden) return;
      var next=(current+1)%cards.length;
      show(next,true); mark(next);
    },7000);
  }
  // A tap, a swipe or a keyboard focus inside the carousel ends the rotation
  // permanently — see the note above.
  ['pointerdown','touchstart','keydown','wheel'].forEach(function(ev){
    root.addEventListener(ev,stop,{passive:true,once:true});
  });
  root.addEventListener('focusin',stop,{once:true});
  play();
})();
"""
