"""
headlines.py
============
The latest stories from news feeds you choose - a link per feed, RSS or Atom
(right-click -> Feeds...) - newest first, each with its source, how long ago
it was published and, where the feed has one, its picture. A click opens a
story in the browser; a drag still moves the card.

Feeds come through net.fetch (HTTPS only, at most FEED_LIMIT bytes) on a
thread of their own every FETCH_EVERY seconds, and the pictures of the stories
on show the same way, scaled down as they arrive. What's taken from a feed is
text, links and picture addresses, never markup: titles have their tags
stripped and their entities unescaped. A new card starts on BBC News and The
Verge, whose formats - RSS with a media:thumbnail, Atom with the picture in
the entry's HTML - were read before this was written, as were NPR's and Ars
Technica's.

Laid out after Apple's News widget: the top story's picture across a small
card under its headline, two stories with pictures beside them at medium, and
a large card's top story under a banner with the rest below.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import html
import os
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QFontMetricsF, QImage,
                           QLinearGradient, QMouseEvent, QPainter, QPainterPath, QPen,
                           QTextLayout)
from PySide6.QtWidgets import QInputDialog

from ..core import net
from ..core.card import RADIUS, WidgetCard, squircle

DEFAULT_FEEDS = ("https://feeds.bbci.co.uk/news/rss.xml",
                 "https://www.theverge.com/rss/index.xml")
FETCH_EVERY = 15 * 60          # s between fetches: news feeds don't move faster
RETRY_AFTER = 2 * 60           # s after a fetch that got nothing
FEED_LIMIT = 2 * 1024 * 1024   # bytes a feed may be
PICTURE_LIMIT = 3 * 1024 * 1024
KEEP = 12                      # stories kept, newest first
MIX_WITHIN = 3 * 60 * 60       # s older another source's story may be and still go next (merge)
PICTURES = 5                   # stories whose picture is fetched: as many as the large card shows
PICTURE_SIZE = 640             # px: pictures scaled down to fit this as they arrive
DRAG_AFTER = 6                 # px a press on a story moves before it's a drag, not a click
ROW_MIN = 52                   # px the large card's rows need: byline, two lines, a little air
HEADERS = {"User-Agent": "pc-widgets/1.1"}     # nothing about the person or the board

NS = {"atom": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/"}
_IMG = re.compile(r"""<img\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']""", re.I)
_TAG = re.compile(r"<[^>]+>")
# iOS's system colours in their higher-contrast forms, for telling sources
# apart - a feed's by its place in the list, so up to seven never share one:
# the dark theme's lighter, the light theme's deeper, so the small names
# stay readable on glass over any wallpaper.
SOURCE_COLOURS = {True: ("#ff6961", "#409cff", "#30db5b", "#ffb340", "#da8fff", "#70d7ff", "#ff6482"),
                  False: ("#d70015", "#0040dd", "#248a3d", "#c93400", "#8944ab", "#0071a4", "#d30f45")}


@dataclass
class Story:
    title: str
    link: str
    source: str
    when: Optional[float] = None           # when it was published, as a timestamp
    picture_url: Optional[str] = None
    picture: Optional[QImage] = None
    feed: int = 0                          # its feed's place in the list: its colour


# --------------------------------------------------------------------------- #
#  Reading a feed
# --------------------------------------------------------------------------- #
def clean(text: Optional[str]) -> str:
    """Feed text as plain words: tags out, entities in, spaces closed up. Run
    twice through the unescaping, since Atom's type="html" titles arrive with
    their markup escaped once more."""
    if not text:
        return ""
    plain = html.unescape(_TAG.sub(" ", html.unescape(text)))
    return " ".join(plain.split())


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el, *names: str):
    """The first child of ``el`` whose tag, namespace aside, is one of ``names``."""
    for child in el:
        if _local(child.tag) in names:
            return child
    return None


def _text(el, *names: str) -> str:
    child = _child(el, *names)
    return (child.text or "") if child is not None else ""


def published(text: Optional[str]) -> Optional[float]:
    """A feed's date as a timestamp: RSS's RFC 822 (Mon, 23 Nov 2026 23:10:27
    GMT) or Atom's ISO 8601 (2026-11-23T19:02:52-05:00). None if neither."""
    if not text or not text.strip():
        return None
    text = text.strip()
    try:
        return email.utils.parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        when = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.timestamp()


def _bigger(url: str) -> str:
    """BBC's thumbnails come 240 px wide, soft across a large card's banner;
    its image server makes any of its standard widths from the same address."""
    return re.sub(r"(//ichef\.bbci\.co\.uk/[^/]+/standard/)240/", r"\g<1>480/", url)


def _picture(item, html_parts: Sequence[Optional[str]]) -> Optional[str]:
    """A story's picture: a media:thumbnail, an image media:content or
    enclosure, or else the first <img> in its HTML."""
    for el in item.iter():
        name = _local(el.tag)
        url = el.get("url")
        if not url:
            continue
        if name == "thumbnail":
            return url
        if name == "content" and el.tag.startswith("{" + NS["media"]) and (
                el.get("medium") == "image" or el.get("type", "").startswith("image/")
                or (not el.get("medium") and not el.get("type")
                    and re.search(r"\.(jpe?g|png|webp)(\?|$)", url, re.I))):
            return url
        if name == "enclosure" and el.get("type", "").startswith("image/"):
            return url
    for text in html_parts:
        found = _IMG.search(text or "")
        if found:
            return html.unescape(found.group(1))
    return None


def short_source(name: str) -> str:
    """A feed's own title, cut to the publication's name where it runs on:
    "Ars Technica - All content" is Ars Technica."""
    name = " ".join(name.split())
    if len(name) > 18:
        for cut in (" - ", " | ", " – ", ": "):
            if cut in name and len(name.split(cut)[0]) >= 3:
                return name.split(cut)[0]
    return name


def parse(data: bytes, fallback: str = "") -> List[Story]:
    """The stories in one feed - RSS 2.0, RSS 1.0 or Atom - in its order.
    Ones without a title or a web link are left out."""
    root = ET.fromstring(data)
    stories: List[Story] = []
    if _local(root.tag) == "feed":                                       # Atom
        source = short_source(clean(_text(root, "title"))) or fallback
        for entry in root:
            if _local(entry.tag) != "entry":
                continue
            link = ""
            for el in entry:
                if _local(el.tag) == "link" and el.get("href") and el.get("rel", "alternate") == "alternate":
                    link = el.get("href")
                    break
            stories.append(Story(
                title=clean(_text(entry, "title")), link=link, source=source,
                when=published(_text(entry, "published") or _text(entry, "updated")),
                picture_url=_picture(entry, [_text(entry, "content"), _text(entry, "summary")])))
    else:                                                                # RSS 2.0 and 1.0
        channel = _child(root, "channel")
        source = short_source(clean(_text(channel, "title"))) if channel is not None else ""
        source = source or fallback
        for item in root.iter():
            if _local(item.tag) != "item":
                continue
            stories.append(Story(
                title=clean(_text(item, "title")), link=_text(item, "link").strip(), source=source,
                when=published(_text(item, "pubDate") or _text(item, "date")),
                picture_url=_picture(item, [_text(item, "encoded"), _text(item, "description")])))
    kept = []
    for story in stories:
        scheme = urllib.parse.urlsplit(story.link).scheme.lower()
        if story.title and scheme in ("http", "https"):
            if story.picture_url:
                story.picture_url = _bigger(story.picture_url)
            kept.append(story)
    return kept


def merge(stories: Sequence[Story], keep: int = KEEP) -> List[Story]:
    """Stories from every feed, newest first - undated ones after - and the
    same story from two feeds once, mixed so one busy feed doesn't fill the
    card (BBC News publishes so often that, by time alone, it did): after a
    story, another source's goes next if it's at most MIX_WITHIN older than
    the next one, and never three from one source in a row while another
    has one waiting."""
    seen, pool = set(), []
    for story in sorted(stories, key=lambda s: -(s.when or 0.0)):
        key = story.link.split("?")[0].split("#")[0].rstrip("/").lower() or story.title.lower()
        if key not in seen:
            seen.add(key)
            pool.append(story)
    out: List[Story] = []
    while pool and len(out) < keep:
        pick = 0
        if out and pool[0].source == out[-1].source:
            other = next((i for i, s in enumerate(pool) if s.source != out[-1].source), None)
            if other is not None:
                twice = len(out) >= 2 and out[-2].source == out[-1].source
                older = (pool[0].when or 0.0) - (pool[other].when or 0.0)
                if twice or older <= MIX_WITHIN:
                    pick = other
        out.append(pool.pop(pick))
    return out


def load_picture(url: Optional[str]) -> Optional[QImage]:
    """A story's picture, fetched (HTTPS only) and scaled down to PICTURE_SIZE.
    None if there isn't one or it won't load. Safe off the board's thread."""
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    elif url.lower().startswith("http://"):
        url = "https://" + url[7:]
    try:
        data = net.fetch(url, headers=HEADERS, timeout=10, limit=PICTURE_LIMIT)
    except Exception:
        return None
    img = QImage()
    if not img.loadFromData(data) or img.isNull():
        return None
    if max(img.width(), img.height()) > PICTURE_SIZE:
        img = img.scaled(PICTURE_SIZE, PICTURE_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return img.convertToFormat(QImage.Format_ARGB32_Premultiplied)


def fetch(feeds: Sequence[str], pictures: int = PICTURES,
          known: Optional[Dict[str, QImage]] = None) -> Tuple[List[Story], List[str]]:
    """Every feed's stories merged, with the pictures of the first
    ``pictures``: (the stories, the feeds that couldn't be read). A picture
    in ``known`` - the ones on the card already, by address - is used as it
    is, not fetched again: a top story stays for hours, and its picture
    would otherwise come down again every FETCH_EVERY."""
    found: List[Story] = []
    failed: List[str] = []
    for place, url in enumerate(feeds):
        try:
            data = net.fetch(url, headers=HEADERS, timeout=10, limit=FEED_LIMIT)
            stories = parse(data, urllib.parse.urlsplit(url).hostname or "")
        except Exception:
            failed.append(url)
            continue
        for story in stories:
            story.feed = place
        found += stories
    stories = merge(found)
    for story in stories[:pictures]:
        if known and story.picture_url in known:
            story.picture = known[story.picture_url]
        else:
            story.picture = load_picture(story.picture_url)
    return stories, failed


def clean_feeds(text: str) -> List[str]:
    """Feed links typed one to a line: https:// ones kept, http:// ones asked
    for over https:// (the board fetches nothing else), anything else - and
    repeats - dropped."""
    feeds: List[str] = []
    for line in text.splitlines():
        url = line.strip()
        if not url:
            continue
        if url.lower().startswith("http://"):
            url = "https://" + url[7:]
        elif "://" not in url:
            url = "https://" + url
        parts = urllib.parse.urlsplit(url)
        if parts.scheme.lower() == "https" and parts.hostname and "." in parts.hostname \
                and url not in feeds:
            feeds.append(url)
    return feeds


def age_text(when: Optional[float], now: float) -> str:
    """How long ago, as Apple News says it: 12m ago, 3h ago, Yesterday, then
    the weekday, then the date."""
    if when is None:
        return ""
    seconds = now - when
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    then = dt.datetime.fromtimestamp(when)
    days = (dt.date.fromtimestamp(now) - then.date()).days
    if days <= 1:
        return "Yesterday"
    if days < 7:
        return then.strftime("%A")
    return f"{then.day} {then.strftime('%b')}"


# --------------------------------------------------------------------------- #
#  The card
# --------------------------------------------------------------------------- #
class HeadlinesWidget(WidgetCard):
    kind = "headlines"
    title = "Headlines"
    customizable = True
    allow_image = True              # a photo can sit behind the stories, as on every card but Now Playing
    supported_sizes = ("small", "medium", "large")
    refresh_ms = 60 * 1000          # the ages tick over; the feeds are fetched every FETCH_EVERY
    repaint_on_tick = False
    # Emitted from the fetch thread: Qt queues it across to the board's.
    data_ready = Signal()

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._now: Callable[[], float] = time.time       # the self-test's and samples' way in
        self._open: Callable[[str], None] = lambda url: QDesktopServices.openUrl(QUrl(url))
        self.stories: List[Story] = []
        self.failed: List[str] = []
        self._result: Tuple[List[Story], List[str]] = ([], [])
        self._busy = False
        self._fetched_at = 0.0
        self._ok: Optional[bool] = None       # the last fetch: None before any
        self._rows: List[Tuple[int, QRectF]] = []
        self._press = None                    # (story, where, where on screen) under a press
        self._pressed_row: Optional[int] = None
        self._hover_row: Optional[int] = None
        self._shown_ages: Optional[tuple] = None
        self.data_ready.connect(self._landed)
        self.setAccessibleName("Headlines")
        self.speak()

    @classmethod
    def new_config(cls) -> dict:
        return {"feeds": list(DEFAULT_FEEDS)}

    @property
    def feeds(self) -> List[str]:
        feeds = self.config.get("feeds")
        if not isinstance(feeds, list):
            feeds = list(DEFAULT_FEEDS)
        return [f for f in feeds if isinstance(f, str)]

    # -- fetching --------------------------------------------------------- #
    def on_start(self) -> None:
        self.refresh()

    def on_tick(self) -> None:
        now = self._now()
        if now - self._fetched_at >= (RETRY_AFTER if self._ok is False else FETCH_EVERY):
            self.refresh()
        if tuple(age_text(s.when, now) for s in self.stories[:6]) != self._shown_ages:
            self.update()

    def refresh(self) -> None:
        """Fetch the feeds again, on a thread of their own. Not in the
        self-test (PCWIDGETS_NO_FEEDS): its boards must never reach out."""
        if self._busy or os.environ.get("PCWIDGETS_NO_FEEDS"):
            return
        feeds = self.feeds
        if not feeds:
            self.stories, self.failed, self._ok = [], [], True
            self.update()
            return
        self._busy = True
        self._fetched_at = self._now()
        # QImages are safe to read from another thread while nothing writes them.
        known = {s.picture_url: s.picture for s in self.stories
                 if s.picture_url and s.picture is not None}

        def work() -> None:
            try:
                result = fetch(feeds, known=known)
            except Exception:
                result = ([], list(feeds))
            self._result = result
            self._busy = False
            try:
                self.data_ready.emit()        # hops back to the board's thread
            except RuntimeError:
                pass                          # the card has gone meanwhile

        threading.Thread(target=work, name="headlines", daemon=True).start()

    def _landed(self) -> None:
        stories, failed = self._result
        if stories or not self.stories:       # a fetch that got nothing keeps what's shown
            self.stories = stories
        self.failed = failed
        self._ok = bool(stories) or not failed
        self.speak()
        self.update()

    def speak(self) -> None:
        """What a screen reader says: everything shown is painted."""
        if not self.stories:
            self.setAccessibleDescription("No headlines yet.")
            return
        self.setAccessibleDescription(" ".join(
            f"{s.source}: {s.title}." for s in self.stories[:3]))

    # -- input: a click opens a story, a drag moves the card -------------- #
    def _row_at(self, pos) -> Optional[int]:
        for index, rect in self._rows:
            if rect.contains(pos):
                return index
        return None

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            row = self._row_at(ev.position())
            if row is not None:
                # Not picked up yet: it's a click unless the mouse moves.
                self._press = (row, QPointF(ev.position()), QPointF(ev.globalPosition()))
                self._pressed_row = row
                self.update()
                ev.accept()
                return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self._press is not None:
            row, at, at_screen = self._press
            moved = ev.position() - at
            if abs(moved.x()) + abs(moved.y()) > DRAG_AFTER:
                # A drag after all: pick the card up where the press was.
                self._press = self._pressed_row = None
                super().mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, at, at_screen,
                                                    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
                super().mouseMoveEvent(ev)
                self.update()
            ev.accept()
            return
        hover = None if self._drag_from is not None else self._row_at(ev.position())
        if hover != self._hover_row:
            self._hover_row = hover
            self.setCursor(Qt.PointingHandCursor if hover is not None else Qt.ArrowCursor)
            self.update()
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if self._press is not None:
            row = self._press[0]
            self._press = self._pressed_row = None
            if self._row_at(ev.position()) == row and row < len(self.stories):
                link = self.stories[row].link
                if urllib.parse.urlsplit(link).scheme.lower() in ("http", "https"):
                    self._open(link)
            self.update()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._hover_row = None
        self.unsetCursor()
        super().leaveEvent(ev)

    # -- menu ------------------------------------------------------------- #
    def context_actions(self, menu) -> None:
        menu.addAction("Feeds…", self._ask_feeds)
        menu.addAction("Refresh now", self.refresh)

    def _ask_feeds(self) -> None:
        text, ok = QInputDialog.getMultiLineText(
            self, "Headlines feeds",
            "A news feed's link on each line (RSS or Atom, https://):",
            "\n".join(self.feeds))
        if not ok:
            return
        self.config["feeds"] = clean_feeds(text)
        self.changed.emit()
        self.refresh()

    # -- drawing ---------------------------------------------------------- #
    def _source_colour(self, story: Story) -> str:
        colours = SOURCE_COLOURS[self.palette_.dark]
        return colours[story.feed % len(colours)]

    def _font(self, size: float, weight=QFont.Normal, caps: bool = False) -> QFont:
        f = self.font(size, weight)
        if caps:
            f.setCapitalization(QFont.AllUppercase)
            f.setLetterSpacing(QFont.PercentageSpacing, 104)
        return f

    @staticmethod
    def _spans(text: str, font: QFont, width: float) -> List[Tuple[int, int]]:
        """Where ``text`` breaks into lines ``width`` wide: (start, length)s."""
        layout = QTextLayout(text, font)
        layout.beginLayout()
        spans = []
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            spans.append((line.textStart(), line.textLength()))
        layout.endLayout()
        return spans

    def _wrapped(self, p, rect: QRectF, text: str, font: QFont, colour, lines: int) -> float:
        """``text`` in up to ``lines`` lines across ``rect``, the last cut with
        an ellipsis if it runs on. The height used."""
        fm = QFontMetricsF(font)
        spans = self._spans(text, font, rect.width())
        p.setFont(font)
        p.setPen(QColor(colour))
        step = fm.lineSpacing()
        shown = spans[:lines]
        for i, (start, length) in enumerate(shown):
            s = text[start:start + length].strip()
            if i == len(shown) - 1 and len(spans) > lines:
                s = fm.elidedText(text[start:].strip(), Qt.ElideRight, rect.width())
            p.drawText(QRectF(rect.left(), rect.top() + i * step, rect.width(), step),
                       Qt.AlignLeft | Qt.AlignVCenter, s)
        return len(shown) * step

    def _byline(self, p, rect: QRectF, story: Story, now: float, on_photo: bool = False,
                size: float = 8.0) -> None:
        """The source in its colour, in small capitals, then how long ago if
        there's room for both - the source is cut short only when it won't
        fit even alone."""
        font = self._font(size, QFont.DemiBold, caps=True)
        age_font = self._font(size + 0.5)
        fm = QFontMetricsF(font)
        source = (story.source or "News").upper()
        age = age_text(story.when, now)
        if age and fm.horizontalAdvance(source) + 6 \
                + QFontMetricsF(age_font).horizontalAdvance(age) > rect.width():
            age = ""
        p.setFont(font)
        p.setPen(QColor(255, 255, 255, 215) if on_photo else QColor(self._source_colour(story)))
        name = fm.elidedText(source, Qt.ElideRight, rect.width())
        p.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, name)
        if age:
            x = rect.left() + fm.horizontalAdvance(name) + 6
            p.setFont(age_font)
            p.setPen(QColor(255, 255, 255, 170) if on_photo else QColor(self.palette_.text_dim))
            p.drawText(QRectF(x, rect.top(), max(0.0, rect.right() - x), rect.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, age)

    def _photo(self, p, img: Optional[QImage], rect: QRectF, radius: float) -> bool:
        """``img`` filling ``rect``, cut to fit and rounded. False if none."""
        if img is None or img.isNull():
            return False
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)
        p.setClipPath(clip, Qt.IntersectClip)
        scale = max(rect.width() / img.width(), rect.height() / img.height())
        w, h = img.width() * scale, img.height() * scale
        p.drawImage(QRectF(rect.center().x() - w / 2, rect.center().y() - h / 2, w, h), img)
        p.restore()
        return True

    def _highlight(self, p, index: int, rect: QRectF) -> None:
        """A row under the pointer, or pressed, shows it."""
        if index == self._pressed_row:
            alpha = 30
        elif index == self._hover_row and self._press is None:
            alpha = 16
        else:
            return
        fill = QColor(self.palette_.text)
        fill.setAlpha(alpha)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(rect, 10, 10)

    def paint_content(self, p: QPainter, r: QRectF) -> None:
        self._rows = []
        now = self._now()
        self._shown_ages = tuple(age_text(s.when, now) for s in self.stories[:6])
        if not self.stories:
            self._paint_empty(p, r)
        elif self.size_class == "small":
            self._paint_small(p, r, now)
        elif self.size_class == "medium":
            self._paint_medium(p, r, now)
        else:
            self._paint_large(p, r, now)

    def _paint_empty(self, p, r: QRectF) -> None:
        pal = self.palette_
        self.label(p, QRectF(r.left(), r.top(), r.width(), 12), "Headlines")
        if not self.feeds:
            lines = ("No feeds yet", "Right-click, then Feeds…, to add one.")
        elif self._ok is False:
            lines = ("Couldn't reach the news", "Trying again in a couple of minutes.")
        else:
            # Before the first fetch lands: the shape of what's coming.
            bar = QColor(pal.text)
            bar.setAlpha(38)
            p.setPen(Qt.NoPen)
            p.setBrush(bar)
            y = r.top() + 26
            rows = 1 if self.size_class == "small" else (2 if self.size_class == "medium" else 4)
            step = (r.height() - 26) / rows
            for i in range(rows):
                top = y + i * step
                p.drawRoundedRect(QRectF(r.left(), top, r.width() * 0.35, 8), 4, 4)
                p.drawRoundedRect(QRectF(r.left(), top + 16, r.width() * 0.85, 10), 5, 5)
                if step > 44:
                    p.drawRoundedRect(QRectF(r.left(), top + 32, r.width() * 0.6, 10), 5, 5)
            return
        # Wrapped: "Couldn't reach the news" is wider than a small card.
        used = self._wrapped(p, QRectF(r.left(), r.top() + 22, r.width(), 44), lines[0],
                             self._font(12.5, QFont.DemiBold), pal.text, 2)
        top = r.top() + 22 + used + 3
        p.setFont(self.font(10))
        p.setPen(QColor(pal.text_dim))
        p.drawText(QRectF(r.left(), top, r.width(), max(0.0, r.bottom() - top)),
                   Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, lines[1])

    def _paint_small(self, p, r: QRectF, now: float) -> None:
        """The top story: its picture across the whole card, the headline in
        white over the bottom of it - or, with no picture, the headline large."""
        pal = self.palette_
        story = self.stories[0]
        card = self.card_rect()
        self._rows.append((0, QRectF(card)))
        if story.picture is not None:
            p.save()
            p.setClipPath(squircle(card, RADIUS))
            self._photo(p, story.picture, card, 0)
            # Darkest where the words sit, so white reads over any picture.
            scrim = QLinearGradient(0, card.top(), 0, card.bottom())
            scrim.setColorAt(0.0, QColor(0, 0, 0, 0))
            scrim.setColorAt(0.35, QColor(0, 0, 0, 30))
            scrim.setColorAt(1.0, QColor(0, 0, 0, 205))
            p.setPen(Qt.NoPen)
            p.setBrush(scrim)
            p.drawRect(card)
            if self._pressed_row == 0:
                p.setBrush(QColor(0, 0, 0, 40))
                p.drawRect(card)
            p.restore()
            font = self._font(12, QFont.Bold)
            step = QFontMetricsF(font).lineSpacing()
            lines = max(1, min(3, len(self._spans(story.title, font, r.width()))))
            top = r.bottom() - lines * step + 2
            self._wrapped(p, QRectF(r.left(), top, r.width(), lines * step), story.title, font,
                          "#ffffff", 3)
            self._byline(p, QRectF(r.left(), top - 16, r.width(), 14), story, now, on_photo=True)
            return
        self._highlight(p, 0, card.adjusted(6, 6, -6, -6))
        self._byline(p, QRectF(r.left(), r.top() - 2, r.width(), 14), story, now)
        self._wrapped(p, QRectF(r.left(), r.top() + 16, r.width(), r.height() - 16), story.title,
                      self._font(13, QFont.Bold), pal.text, 5)

    def _row(self, p, index: int, rect: QRectF, now: float, title_size: float,
             thumb: float) -> None:
        """A story as a row: the byline and a two-line headline, its picture
        beside them on the right."""
        story = self.stories[index]
        self._rows.append((index, QRectF(rect)))
        self._highlight(p, index, rect.adjusted(-6, -3, 6, 3))
        text_w = rect.width()
        if story.picture is not None and thumb > 0:
            box = QRectF(rect.right() - thumb, rect.center().y() - thumb / 2, thumb, thumb)
            self._photo(p, story.picture, box, 9)
            text_w -= thumb + 12
        font = self._font(title_size, QFont.DemiBold)
        step = QFontMetricsF(font).lineSpacing()
        block_h = 14 + 2 * step
        top = rect.center().y() - block_h / 2
        self._byline(p, QRectF(rect.left(), top, text_w, 14), story, now)
        self._wrapped(p, QRectF(rect.left(), top + 15, text_w, 2 * step), story.title, font,
                      self.palette_.text, 2)

    def _divider(self, p, x0: float, x1: float, y: float) -> None:
        edge = QColor(self.palette_.text_faint)
        edge.setAlpha(55)
        p.setPen(QPen(edge, 0.8))
        p.drawLine(QPointF(x0, y), QPointF(x1, y))

    def _paint_medium(self, p, r: QRectF, now: float) -> None:
        """Two stories, each beside its picture."""
        count = min(2, len(self.stories))
        step = r.height() / 2
        for i in range(count):
            row = QRectF(r.left(), r.top() + i * step + (2 if i else 0), r.width(), step - 4)
            self._row(p, i, row, now, 11.5, step - 10)
            if i == 0 and count > 1:
                self._divider(p, r.left(), r.right(), r.top() + step)

    def _paint_large(self, p, r: QRectF, now: float) -> None:
        """The top story under a banner of its picture, as many more as fit
        below it - the rows sharing out the room, so none is left empty."""
        pal = self.palette_
        story = self.stories[0]
        font = self._font(14.5, QFont.Bold)
        step = QFontMetricsF(font).lineSpacing()
        banner = round(r.height() * 0.38) if story.picture is not None else 0
        lines = max(1, min(2 if banner else 3, len(self._spans(story.title, font, r.width()))))
        text_top = r.top() + (banner + 10 if banner else 0)
        head = QRectF(r.left(), r.top(), r.width(), text_top + 16 + lines * step - r.top())
        self._rows.append((0, head))
        self._highlight(p, 0, head.adjusted(-6, -4, 6, 4))
        if banner:
            self._photo(p, story.picture, QRectF(r.left(), r.top(), r.width(), banner), 12)
        self._byline(p, QRectF(r.left(), text_top, r.width(), 14), story, now, size=8.5)
        self._wrapped(p, QRectF(r.left(), text_top + 16, r.width(), lines * step), story.title,
                      font, pal.text, lines)
        y = head.bottom() + 8
        room = r.bottom() + 4 - y
        fits = int(room // ROW_MIN)
        if fits < 1:
            return
        row_h = room / fits           # spaced for a full card; fewer stories leave room below
        for index in range(1, min(len(self.stories), fits + 1)):
            self._divider(p, r.left(), r.right(), y)
            self._row(p, index, QRectF(r.left(), y + 4, r.width(), row_h - 6), now, 11, 44)
            y += row_h
