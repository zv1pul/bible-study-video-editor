"""
editor.py
=========
Step 3 of the pipeline: burn the graphics into the video.

Graphics are drawn with Pillow and layered on top of the original footage as
transparent PNG overlays. Drawing them ourselves (instead of using MoviePy's
TextClip) means:

  * no ImageMagick or system font configuration to install,
  * identical output on macOS, Windows and Linux,
  * full control over the rounded, semi-transparent caption box.

What gets rendered:
  * a lower third with the speaker's name and title, at 00:05 for 5 seconds,
  * one caption per matched lesson point, 8 seconds each, bottom centre.
"""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from moviepy import CompositeVideoClip, ImageClip, VideoFileClip

# --------------------------------------------------------------------------
# Look and feel
# --------------------------------------------------------------------------

WHITE = (255, 255, 255, 255)
ACCENT = (255, 202, 92, 255)
BOX_FILL = (0, 0, 0, 190)
BOX_FILL_SOLID = (0, 0, 0, 215)

# --- Full-screen point card template --------------------------------------
# #CDC7B8. Taken from the background of the supplied logo, which was cropped
# from a real card — so this is the actual brand colour rather than a guess,
# and the logo now sits on it without a visible square.
CARD_BG = (205, 199, 184, 255)
CARD_TEXT = (18, 18, 18, 255)         # near-black, easier on the eye than #000
CARD_LOGO_BOX = 120                   # reserved square, top right, in pixels
CARD_LOGO_MARGIN = 40

# Type sizes as a fraction of frame height, matching the reference card.
CARD_HEADER_SCALE = 0.115             # "Principle #1" — large and heavy
CARD_BODY_SCALE = 0.090               # the point itself, a little smaller
CARD_HEADER_TOP = 0.10                # where the header sits from the top

# --- Lower third template --------------------------------------------------
LOWER_THIRD_START = 3.0               # hard cut in at 00:03
LOWER_THIRD_DURATION = 25.0           # ...and hard cut out at 00:28
# Template metrics, quoted at 1080p and scaled proportionally so a 720p or
# 4K master looks the same rather than having a tiny or gigantic caption.
REFERENCE_HEIGHT = 1080.0
LOWER_THIRD_INSET = 50.0              # px from the left and bottom edges
LOWER_THIRD_NAME_PT = 52.0
LOWER_THIRD_TITLE_PT = 34.0
_TEXT_PAD = 4                         # internal padding that leaves room for the halo

# --- Countdown timer -------------------------------------------------------
# A ring that drains clockwise from the top, with the digits inside it.
# Sizes are quoted at 1080p and scaled.
TIMER_FONT_PT = 76.0
TIMER_RING_DIAMETER = 224.0
TIMER_RING_WIDTH = 10.0
TIMER_Y = 0.70                        # fraction of frame height, its top edge
TIMER_RESERVE = 0.30                  # room kept clear under the body text
TIMER_MAX_STEPS = 240                 # ceiling on how many second-frames we draw

# Encoder settings, measured on a real 1080p lesson (1324 kb/s source):
#
#   ultrafast/23   672 MB for 23 minutes   -- three times the size...
#   veryfast/23    224 MB                  -- ...for 1.9 seconds saved a minute
#   veryfast/26    156 MB
#
# "ultrafast" is a false economy here: the file it produces is too large to
# download from a hosted app, and the time it saves is negligible.
QUALITY_PRESETS = {
    "small": ("veryfast", 26),      # smallest file, still good for teaching
    "balanced": ("veryfast", 23),   # the default
    "best": ("medium", 20),
}
DEFAULT_QUALITY = "balanced"

# Bookends
BOOKEND_DURATION = 5.0

CATEGORY_LABELS = {
    "Takeaway": "Takeaway",
    "Division": "Division",
    "Principle": "Principle",
    "Application": "Application",
}

# A point written with " | " separators becomes a list on one card, which is
# how several divisions are shown together:
#   "I. Man-initiated Religion | II. God-initiated Worship"
LINE_BREAK = "|"

# --------------------------------------------------------------------------
# Fonts — found on the machine, or bundled in ./fonts, or Pillow's built-in
# --------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))

# The bundled typeface. Shipping it is the whole point: without it the app
# picks whatever the machine happens to have — Arial on a Mac, DejaVu on a
# Linux server, Arial on Windows — and the same lesson renders differently
# depending on who made it. This is one variable font carrying every weight,
# so a single file keeps every deployment identical.
BUNDLED_FONT = os.path.join(_HERE, "fonts", "Inter.ttf")
BUNDLED_WEIGHTS = {False: "Regular", True: "Bold"}

# Only used if the bundled file is missing or unreadable.
_REGULAR_CANDIDATES = [
    os.environ.get("OVERLAY_FONT", ""),
    *sorted(glob.glob(os.path.join(_HERE, "fonts", "*Regular*.tt*"))),
    *sorted(glob.glob(os.path.join(_HERE, "fonts", "*.tt*"))),
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

_BOLD_CANDIDATES = [
    os.environ.get("OVERLAY_FONT_BOLD", ""),
    *sorted(glob.glob(os.path.join(_HERE, "fonts", "*Bold*.tt*"))),
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

_FONT_CACHE: dict = {}


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    size = max(10, int(size))
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    # The bundled face first, always, so output does not depend on the machine.
    if os.path.exists(BUNDLED_FONT):
        try:
            font = ImageFont.truetype(BUNDLED_FONT, size)
            try:
                font.set_variation_by_name(BUNDLED_WEIGHTS[bool(bold)])
            except Exception:
                pass  # a static build of the same face is fine too
            _FONT_CACHE[key] = font
            return font
        except Exception:
            pass

    candidates = (_BOLD_CANDIDATES if bold else []) + _REGULAR_CANDIDATES
    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            font = ImageFont.truetype(path, size)
            _FONT_CACHE[key] = font
            return font
        except Exception:
            continue

    try:  # Pillow >= 10.1 ships a scalable default face
        font = ImageFont.load_default(size)
    except TypeError:  # pragma: no cover - very old Pillow
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def font_report() -> str:
    """Which font file the overlays will actually use (shown in the UI)."""
    if os.path.exists(BUNDLED_FONT):
        return f"{os.path.basename(BUNDLED_FONT)} (bundled — identical everywhere)"
    for path in _BOLD_CANDIDATES + _REGULAR_CANDIDATES:
        if path and os.path.exists(path):
            return f"{os.path.basename(path)} (system font — varies by machine)"
    return "Pillow built-in font"


# --------------------------------------------------------------------------
# Drawing helpers
# --------------------------------------------------------------------------

_MEASURE = ImageDraw.Draw(Image.new("RGBA", (4, 4)))


def _text_width(text: str, font) -> float:
    return _MEASURE.textlength(text, font=font)


def _wrap(text: str, font, max_width: float, max_lines: int = 3) -> List[str]:
    words = (text or "").split()
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or _text_width(candidate, font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" .,;:") + "…"
    return lines or [""]


def make_caption_image(
    video_w: int, video_h: int, text: str, label: str = ""
) -> np.ndarray:
    """
    The older style: a small caption card sitting over the bottom of the
    footage. Kept because it is useful when the video itself must stay
    visible; the default template is the full-screen card below.
    """
    base = min(video_h, int(video_w * 9 / 16))
    text_font = load_font(base * 0.050, bold=True)
    label_font = load_font(base * 0.026, bold=True)

    pad_x = int(base * 0.045)
    pad_y = int(base * 0.032)
    max_card_w = int(video_w * 0.88)
    inner_w = max_card_w - 2 * pad_x

    lines = _wrap(text, text_font, inner_w)
    line_h = int(text_font.size * 1.28)
    label_h = int(label_font.size * 1.85) if label else 0

    content_w = max([_text_width(line, text_font) for line in lines] or [0])
    if label:
        content_w = max(content_w, _text_width(label, label_font))
    card_w = int(min(max_card_w, content_w + 2 * pad_x))
    card_h = int(len(lines) * line_h + label_h + 2 * pad_y)

    img = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [0, 0, card_w - 1, card_h - 1], radius=int(base * 0.018), fill=BOX_FILL
    )

    y = pad_y
    if label:
        width = _text_width(label, label_font)
        draw.text(((card_w - width) / 2, y), label, font=label_font, fill=ACCENT)
        y += label_h
    for line in lines:
        width = _text_width(line, text_font)
        draw.text(((card_w - width) / 2, y), line, font=text_font, fill=WHITE)
        y += line_h

    return np.array(img)


def _split_body(text: str) -> List[str]:
    """A point may hold several lines, separated by '|', to form a list."""
    parts = [part.strip() for part in str(text or "").split(LINE_BREAK)]
    return [part for part in parts if part]


_LIST_MARKER = re.compile(
    r"^\s*(?:[0-9]{1,2}|[IVXivx]{1,5}|[A-Za-z])\s*[.)\]:-]\s+\S"
)


def _is_list(lines: Sequence[str]) -> bool:
    """
    True when the body reads as a numbered or lettered list.

    A list is set left-aligned so the numbers stack in a column; anything else
    is centred. One line on its own is never treated as a list.
    """
    if len(lines) < 2:
        return False
    marked = sum(1 for line in lines if _LIST_MARKER.match(line))
    return marked >= max(2, len(lines) // 2)


def make_point_card_image(
    video_w: int,
    video_h: int,
    header: str,
    body: str,
    logo_path: Optional[str] = None,
    *,
    logo_box: int = CARD_LOGO_BOX,
    reserve_bottom: float = 0.0,
) -> np.ndarray:
    """
    A full-screen point card: beige background, black text, header near the
    top, body centred, and a reserved square in the top right for a logo.

    Lists are left-aligned as a block but the block itself is centred, so the
    numbers line up while the card still looks balanced.
    """
    # Proportions taken from the reference card: a large, heavy header and a
    # body only a little smaller, both filling the frame confidently.
    header_font = load_font(video_h * CARD_HEADER_SCALE, bold=True)
    body_font = load_font(video_h * CARD_BODY_SCALE, bold=False)

    img = Image.new("RGBA", (video_w, video_h), CARD_BG)
    draw = ImageDraw.Draw(img)

    # Reserved logo square, top right. Filled only if a logo was supplied;
    # otherwise it simply stays empty so nothing collides with it later.
    if logo_path and os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo.thumbnail((logo_box, logo_box), Image.LANCZOS)
            img.alpha_composite(
                logo,
                (
                    video_w - CARD_LOGO_MARGIN - logo.width,
                    CARD_LOGO_MARGIN,
                ),
            )
        except Exception:
            pass  # a bad logo file must never stop the render

    # --- header ------------------------------------------------------------
    top = int(video_h * CARD_HEADER_TOP)
    if header:
        # Keep the header clear of the reserved logo square.
        available = video_w - 2 * (CARD_LOGO_MARGIN + logo_box)
        header_lines = _wrap(header, header_font, max(available, video_w * 0.5), 2)
        for line in header_lines:
            width = _text_width(line, header_font)
            draw.text(((video_w - width) / 2, top), line, font=header_font,
                      fill=CARD_TEXT)
            top += int(header_font.size * 1.25)

    # --- body --------------------------------------------------------------
    raw_lines = _split_body(body)
    as_list = _is_list(raw_lines)
    # A narrower measure than the frame allows, so lines break into short
    # readable phrases the way the reference cards do.
    max_body_w = int(video_w * (0.70 if as_list else 0.72))

    lines: List[str] = []
    for entry in raw_lines:
        lines.extend(_wrap(entry, body_font, max_body_w, 5))

    line_h = int(body_font.size * 1.24)
    block_h = len(lines) * line_h

    # Centre the block in the space left under the header.
    region_top = top + int(video_h * 0.04)
    # A countdown sits under the body, so the text is lifted to make room.
    region_bottom = video_h - int(video_h * (0.10 + max(reserve_bottom, 0.0)))
    y = region_top + max((region_bottom - region_top - block_h) // 2, 0)

    if as_list:
        widest = max([_text_width(line, body_font) for line in lines] or [0])
        left = (video_w - widest) / 2
        for line in lines:
            draw.text((left, y), line, font=body_font, fill=CARD_TEXT)
            y += line_h
    else:
        for line in lines:
            width = _text_width(line, body_font)
            draw.text(((video_w - width) / 2, y), line, font=body_font,
                      fill=CARD_TEXT)
            y += line_h

    return np.array(img)


def lower_third_position(video_w: int, video_h: int, image_height: int) -> tuple:
    """
    Bottom-left, inset by 50px at 1080p (scaled elsewhere).

    The internal padding is subtracted so the glyphs themselves land on the
    inset rather than the transparent border around them.
    """
    scale = video_h / REFERENCE_HEIGHT
    inset = LOWER_THIRD_INSET * scale
    x = int(round(inset)) - _TEXT_PAD
    y = int(round(video_h - inset - image_height)) + _TEXT_PAD
    return max(x, 0), max(y, 0)


def format_countdown(seconds: float, total: float = 0.0) -> str:
    """
    Plain seconds for a short countdown ("30"), M:SS for a long one ("2:30").

    A thirty-second discussion timer reads better as a single number inside
    a ring than as 00:30, and it fits.
    """
    seconds = max(0, int(round(seconds)))
    if max(total, seconds) < 100:
        return str(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def make_timer_image(
    video_w: int,
    video_h: int,
    seconds_remaining: float,
    *,
    on_card: bool = True,
    total: float = 0.0,
) -> np.ndarray:
    """
    One frame of the countdown: MM:SS inside a ring that empties as time runs.

    The ring starts full at the top and drains clockwise, so a glance at the
    shape says how much is left without reading the digits. Drawn at four
    times the size and scaled down, which is what keeps the curve smooth.

    `total` is the length of the whole countdown; without it there is no ring,
    just the digits.
    """
    scale = video_h / REFERENCE_HEIGHT
    font = load_font(TIMER_FONT_PT * scale, bold=True)
    text = format_countdown(seconds_remaining, total)

    diameter = int(TIMER_RING_DIAMETER * scale)
    ring = max(2, int(TIMER_RING_WIDTH * scale))
    pad = ring + 2
    size = diameter + 2 * pad

    if on_card:
        ink = CARD_TEXT
        track = (CARD_TEXT[0], CARD_TEXT[1], CARD_TEXT[2], 38)     # faint
    else:
        ink = WHITE
        track = (255, 255, 255, 70)

    # Supersample for anti-aliasing.
    ss = 4
    big = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    box = [pad * ss, pad * ss, (pad + diameter) * ss, (pad + diameter) * ss]

    if total > 0:
        # The full track, faint, so the drained part still reads as a circle.
        draw.arc(box, start=0, end=360, fill=track, width=ring * ss)
        fraction = max(0.0, min(1.0, seconds_remaining / total))
        if fraction > 0.002:
            # Pillow measures angles clockwise from 3 o'clock. The gap opens
            # at 12 and grows clockwise, so the remainder is the arc that
            # finishes back at 12.
            draw.arc(box, start=-90 + 360 * (1 - fraction), end=270,
                     fill=ink, width=ring * ss)

    img = big.resize((size, size), Image.LANCZOS)
    draw = ImageDraw.Draw(img)

    # Digits, centred in the ring.
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    tw, th = right - left, bottom - top
    draw.text(((size - tw) / 2 - left, (size - th) / 2 - top), text,
              font=font, fill=ink)

    return np.array(img)


def timer_steps(duration: float) -> List[tuple]:
    """
    The countdown broken into frames: [(offset, seconds_left, length), ...].

    One frame per second normally. A very long pause is stepped more coarsely
    so a single render never has to juggle hundreds of overlays.
    """
    duration = max(float(duration or 0.0), 0.0)
    if duration < 1.0:
        return []
    step = 1.0
    while duration / step > TIMER_MAX_STEPS:
        step *= 5.0

    steps: List[tuple] = []
    offset = 0.0
    while offset < duration - 0.05:
        length = min(step, duration - offset)
        steps.append((offset, duration - offset, length))
        offset += step
    return steps


def make_overview_card_image(
    video_w: int,
    video_h: int,
    takeaway: str,
    divisions: Sequence[str],
    logo_path: Optional[str] = None,
) -> np.ndarray:
    """
    The opening card: the takeaway with the divisions listed beneath it.

    Shown from just before the takeaway is spoken until the first division
    is introduced, so it stays up through the whole of that opening section —
    long enough to be written down, which a card that flashed for eight
    seconds never was.
    """
    header_font = load_font(video_h * CARD_HEADER_SCALE * 0.78, bold=True)
    body_font = load_font(video_h * CARD_BODY_SCALE * 0.86, bold=False)
    sub_font = load_font(video_h * CARD_HEADER_SCALE * 0.52, bold=True)
    list_font = load_font(video_h * CARD_BODY_SCALE * 0.66, bold=False)

    img = Image.new("RGBA", (video_w, video_h), CARD_BG)
    draw = ImageDraw.Draw(img)

    if logo_path and os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo.thumbnail((CARD_LOGO_BOX, CARD_LOGO_BOX), Image.LANCZOS)
            img.alpha_composite(
                logo, (video_w - CARD_LOGO_MARGIN - logo.width, CARD_LOGO_MARGIN)
            )
        except Exception:
            pass

    # Lay everything out first so the whole block can be centred vertically.
    header = "Takeaway"
    body_lines = _wrap(takeaway, body_font, int(video_w * 0.76), 4)
    items = [str(d) for d in divisions if str(d).strip()]
    list_lines: List[str] = []
    for item in items:
        list_lines.extend(_wrap(item, list_font, int(video_w * 0.82), 2))

    header_h = int(header_font.size * 1.35)
    body_h = int(body_font.size * 1.24)
    sub_h = int(sub_font.size * 1.5)
    list_h = int(list_font.size * 1.3)
    gap = int(video_h * 0.06)

    total = header_h + len(body_lines) * body_h
    if items:
        total += gap + sub_h + len(list_lines) * list_h

    y = max(int(video_h * 0.09), (video_h - total) // 2)

    draw.text(((video_w - _text_width(header, header_font)) / 2, y), header,
              font=header_font, fill=CARD_TEXT)
    y += header_h
    for line in body_lines:
        draw.text(((video_w - _text_width(line, body_font)) / 2, y), line,
                  font=body_font, fill=CARD_TEXT)
        y += body_h

    if items:
        y += gap
        sub = "Divisions"
        draw.text(((video_w - _text_width(sub, sub_font)) / 2, y), sub,
                  font=sub_font, fill=CARD_TEXT)
        y += sub_h
        widest = max(_text_width(line, list_font) for line in list_lines)
        left = (video_w - widest) / 2
        for line in list_lines:
            draw.text((left, y), line, font=list_font, fill=CARD_TEXT)
            y += list_h

    return np.array(img)


def make_lower_third_image(
    video_w: int,
    video_h: int,
    name: str,
    title: str = "",
    *,
    shadow: bool = True,
) -> np.ndarray:
    """
    Plain white text in the bottom left: no box, no bar, nothing behind it.

    Line 1 is the name — bold, all caps, larger.
    Line 2 is the title — regular weight, all caps, smaller.

    `shadow` draws a soft dark halo behind the letters. It is invisible on
    dark footage and is the only thing keeping white text readable when the
    speaker is lit against a bright wall or a window. Pass shadow=False for
    text with nothing at all behind it.
    """
    scale = video_h / REFERENCE_HEIGHT
    name_font = load_font(LOWER_THIRD_NAME_PT * scale, bold=True)
    title_font = load_font(LOWER_THIRD_TITLE_PT * scale, bold=False)

    name_text = (name or "").strip().upper()
    title_text = (title or "").strip().upper()

    max_w = int(video_w * 0.62)
    name_lines = _wrap(name_text, name_font, max_w, 2) if name_text else []
    title_lines = _wrap(title_text, title_font, max_w, 2) if title_text else []

    name_h = int(name_font.size * 1.22)
    title_h = int(title_font.size * 1.35)
    gap = int(12 * scale) if name_lines and title_lines else 0

    width = int(max(
        [_text_width(line, name_font) for line in name_lines]
        + [_text_width(line, title_font) for line in title_lines]
        or [1]
    )) + 2 * _TEXT_PAD
    height = (
        len(name_lines) * name_h + len(title_lines) * title_h + gap + 2 * _TEXT_PAD
    )

    img = Image.new("RGBA", (max(width, 1), max(height, 1)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # A thin, hard outline rather than a soft halo. The halo — translucent
    # copies of the text offset in four directions — looked fine on a still
    # but turned to fuzz once the video was compressed, and the name read as
    # blurry. A crisp one-pixel-per-540-lines stroke survives encoding.
    stroke = max(1, int(round(video_h / 540)))

    def write(text: str, font, x: float, y: float) -> None:
        if shadow:
            draw.text((x, y), text, font=font, fill=WHITE,
                      stroke_width=stroke, stroke_fill=(0, 0, 0, 200))
        else:
            draw.text((x, y), text, font=font, fill=WHITE)

    y = _TEXT_PAD
    for line in name_lines:
        write(line, name_font, _TEXT_PAD, y)
        y += name_h
    y += gap
    for line in title_lines:
        write(line, title_font, _TEXT_PAD, y)
        y += title_h

    return np.array(img)


# --------------------------------------------------------------------------
# Cue scheduling
# --------------------------------------------------------------------------


@dataclass
class Cue:
    text: str
    start: float
    label: str = ""
    duration: float = 8.0
    has_timer: bool = False
    timer_duration: float = 0.0
    # Applications: where discussion time is inserted, and what to cut.
    pause_at: float = 0.0
    cut_start: float = 0.0
    cut_end: float = 0.0
    # Overview card: the divisions listed under the takeaway.
    items: list = field(default_factory=list)
    kind: str = ""


def schedule_cues(
    cues: Sequence[Cue],
    video_duration: float,
    default_duration: float = 8.0,
    min_duration: float = 2.0,
    gap: float = 0.2,
) -> List[Cue]:
    """
    Turn the requested cue times into a clean, non-overlapping schedule.

    Two situations have to be handled, and both are common in practice:
      * the next caption starts while this one is still showing -> shorten
        this one so it clears the screen first;
      * several points were matched to the same moment (the speaker rattled
        them off together) -> queue them one after another instead of
        stacking them on top of each other.
    """
    ordered = sorted(
        [c for c in cues if (c.text or "").strip()], key=lambda c: c.start
    )

    cleaned: List[Cue] = []
    for cue in ordered:
        start = max(0.0, min(cue.start, max(video_duration - min_duration, 0.0)))
        if cleaned and cleaned[-1].text == cue.text and abs(cleaned[-1].start - start) < 0.5:
            continue  # the same point matched twice at the same moment
        cleaned.append(Cue(cue.text, start, cue.label,
                           cue.duration or default_duration,
                           cue.has_timer, cue.timer_duration,
                           cue.pause_at, cue.cut_start, cue.cut_end,
                           list(cue.items), cue.kind))

    scheduled: List[Cue] = []
    cursor = 0.0
    for index, cue in enumerate(cleaned):
        start = max(cue.start, cursor)
        if video_duration and start >= video_duration - 0.4:
            break  # no room left in the video for this one

        end = start + (cue.duration or default_duration)
        if video_duration:
            end = min(end, video_duration)

        # Give way to the next caption -- but only when that caption really
        # does belong later in the video. If it was matched to this same
        # moment it simply queues up behind us, so this one keeps its full
        # time on screen and stays readable.
        if index + 1 < len(cleaned):
            next_start = cleaned[index + 1].start
            if next_start > start and next_start - gap > start + min_duration:
                end = min(end, next_start - gap)

        duration = end - start
        if duration < min_duration:
            duration = min_duration
            if video_duration:
                duration = min(duration, max(video_duration - start, 0.0))
        if duration <= 0.05:
            continue

        scheduled.append(Cue(cue.text, round(start, 2), cue.label,
                             round(duration, 2), cue.has_timer, cue.timer_duration,
                             cue.pause_at, cue.cut_start, cue.cut_end,
                             list(cue.items), cue.kind))
        cursor = start + duration + gap

    return scheduled


def cues_from_matches(matches, default_duration: float = 8.0) -> List[Cue]:
    """
    Convert matcher Elements (or plain dicts) into Cue objects.

    Each element now carries its own end_time, worked out from where the
    speaker actually finishes with the point, so the card stays up for
    exactly as long as it is being taught rather than a fixed eight seconds.
    The lower third is handled separately and is skipped here.
    """
    cues: List[Cue] = []
    for match in matches:
        if isinstance(match, dict):
            kind = match.get("type", "")
            text = match.get("content", match.get("text", ""))
            start = float(match.get("start_time", 0.0))
            end = float(match.get("end_time", 0.0) or 0.0)
            header = match.get("header") or str(match.get("category", "")).title()
            has_timer = bool(match.get("has_timer", False))
            timer_duration = float(match.get("timer_duration", 0.0) or 0.0)
        else:
            kind = getattr(match, "type", "")
            text = match.text
            start = float(match.start_time)
            end = float(getattr(match, "end_time", 0.0) or 0.0)
            header = getattr(match, "header", "") or match.category
            has_timer = bool(getattr(match, "has_timer", False))
            timer_duration = float(getattr(match, "timer_duration", 0.0) or 0.0)

        if kind == "lower_third":
            continue

        span = end - start
        cues.append(
            Cue(
                text=text,
                start=start,
                label=header,
                duration=span if span > 0.05 else default_duration,
                has_timer=has_timer,
                timer_duration=timer_duration,
                pause_at=float(getattr(match, "pause_at", 0.0) or 0.0),
                cut_start=float(getattr(match, "cut_start", 0.0) or 0.0),
                cut_end=float(getattr(match, "cut_end", 0.0) or 0.0),
                items=list(getattr(match, "items", []) or []),
                kind=kind,
            )
        )
    return cues


def lower_third_timing(elements, fallback_start: float = LOWER_THIRD_START,
                       fallback_duration: float = LOWER_THIRD_DURATION) -> tuple:
    """Pull the lower third's start and length out of the element list."""
    for element in elements or []:
        kind = element.get("type") if isinstance(element, dict) else getattr(element, "type", "")
        if kind != "lower_third":
            continue
        start = float(
            element.get("start_time") if isinstance(element, dict) else element.start_time
        )
        end = float(
            element.get("end_time") if isinstance(element, dict) else element.end_time
        )
        if end > start:
            return start, end - start
        return start, fallback_duration
    return fallback_start, fallback_duration


# --------------------------------------------------------------------------
# Intro and outro bookends
# --------------------------------------------------------------------------


def add_bookends(
    video_clip,
    intro_image_path: Optional[str] = None,
    outro_image_path: Optional[str] = None,
    duration: float = BOOKEND_DURATION,
):
    """
    Put a still image on the front and the back of a clip.

    Takes and returns a MoviePy clip, so it composes with the rest of the
    MoviePy API:

        clip = add_bookends(VideoFileClip("lesson.mp4"), "intro.png", "outro.png")

    Each image is held for `duration` seconds (5 by default), scaled to the
    video's own frame size, and padded with silence so the audio track stays
    in step. Passing None for either end simply skips it.
    """
    from moviepy import concatenate_videoclips

    width, height = video_clip.size
    fps = video_clip.fps or 30

    def still(path: str):
        if not path or not os.path.exists(path):
            return None
        image = Image.open(path).convert("RGB")
        # Fit inside the frame without distorting, on a black field.
        canvas = Image.new("RGB", (width, height), (0, 0, 0))
        scaled = image.copy()
        scaled.thumbnail((width, height), Image.LANCZOS)
        canvas.paste(
            scaled, ((width - scaled.width) // 2, (height - scaled.height) // 2)
        )
        clip = ImageClip(np.array(canvas)).with_duration(duration)
        return clip.with_fps(fps)

    pieces = [c for c in (still(intro_image_path), video_clip, still(outro_image_path))
              if c is not None]
    if len(pieces) == 1:
        return video_clip
    return concatenate_videoclips(pieces, method="compose")


def _concat_bookends(
    main_path: str,
    output_path: str,
    intro_image_path: Optional[str],
    outro_image_path: Optional[str],
    width: int,
    height: int,
    fps: float,
    duration: float,
    preset: str,
    threads: int,
    crf: int = 23,
) -> bool:
    """
    FFmpeg equivalent of add_bookends, used by the fast rendering path.

    The stills are encoded with exactly the settings the main file already
    uses, which lets the three pieces be joined by stream copy — no second
    pass over the whole lesson.
    """
    import subprocess
    import tempfile as _tempfile

    from transcriber import ffmpeg_exe

    stills = [(intro_image_path, "intro"), (outro_image_path, "outro")]
    if not any(path for path, _ in stills):
        return False

    workdir = _tempfile.mkdtemp(prefix="bsve_bookends_")
    try:
        made: dict = {}
        for path, name in stills:
            if not path or not os.path.exists(path):
                continue
            piece = os.path.join(workdir, f"{name}.mp4")
            command = [
                ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
                "-loop", "1", "-t", f"{duration:.3f}", "-i", path,
                "-f", "lavfi", "-t", f"{duration:.3f}",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-vf", (
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                    f"setsar=1,fps={fps}"
                ),
                "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                "-pix_fmt", "yuv420p", "-threads", str(threads),
                "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
                "-shortest", piece,
            ]
            if subprocess.run(command, capture_output=True).returncode == 0:
                made[name] = piece

        if not made:
            return False

        order = [made.get("intro"), main_path, made.get("outro")]
        order = [item for item in order if item]

        listing = os.path.join(workdir, "pieces.txt")
        with open(listing, "w") as handle:
            for item in order:
                handle.write(f"file '{os.path.abspath(item)}'\n")

        joined = subprocess.run(
            [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", listing,
             "-c", "copy", "-movflags", "+faststart", output_path],
            capture_output=True,
        )
        if joined.returncode == 0 and os.path.exists(output_path):
            return True

        # Stream copy is fussy about mismatched timebases; re-encode instead.
        inputs: List[str] = []
        for item in order:
            inputs += ["-i", item]
        streams = "".join(f"[{i}:v][{i}:a]" for i in range(len(order)))
        redone = subprocess.run(
            [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", *inputs,
             "-filter_complex",
             f"{streams}concat=n={len(order)}:v=1:a=1[v][a]",
             "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
             "-pix_fmt", "yuv420p", "-threads", str(threads),
             "-c:a", "aac", "-b:a", "160k",
             "-movflags", "+faststart", output_path],
            capture_output=True,
        )
        return redone.returncode == 0 and os.path.exists(output_path)
    finally:
        import shutil as _shutil
        _shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# Rendering helpers
# --------------------------------------------------------------------------


def _progress_logger(progress_cb):
    """Bridge MoviePy's proglog progress bars to a simple callback."""
    if progress_cb is None:
        return None
    try:
        from proglog import ProgressBarLogger
    except Exception:  # pragma: no cover
        return None

    class _Logger(ProgressBarLogger):
        def bars_callback(self, bar, attr, value, old_value=None):
            if attr != "index":
                return
            try:
                total = self.bars[bar].get("total") or 0
                if total:
                    progress_cb(min(float(value) / float(total), 1.0), "Rendering video…")
            except Exception:
                pass

    return _Logger()


def _with_fade(clip, seconds: float):
    """Soft fade in/out if this MoviePy build supports it; otherwise a cut."""
    if not seconds:
        return clip
    try:
        from moviepy.video.fx import CrossFadeIn, CrossFadeOut

        return clip.with_effects([CrossFadeIn(seconds), CrossFadeOut(seconds)])
    except Exception:
        return clip


def _overlay_specs(
    video_path: str,
    width: int,
    height: int,
    duration: float,
    speaker_name: str,
    speaker_title: str,
    cues: Sequence[Cue],
    lower_third_start: float,
    lower_third_duration: float,
    cue_duration: float,
    workdir: str,
    *,
    card_style: str = "fullscreen",
    logo_path: Optional[str] = None,
    lower_third_shadow: bool = True,
    countdown_on_source: bool = True,
) -> List[dict]:
    """
    Draw every overlay to a PNG on disk and return where and when each one
    belongs. Shared by both rendering paths.

    countdown_on_source=False when discussion blocks are being inserted: the
    countdown then lives in the block, not on the recording.
    """
    from PIL import Image as _Image

    specs: List[dict] = []

    if speaker_name.strip() or speaker_title.strip():
        start = min(lower_third_start, max(duration - 1.0, 0.0))
        length = min(lower_third_duration, max(duration - start, 0.0))
        if length > 0.2:
            array = make_lower_third_image(
                width, height, speaker_name.strip(), speaker_title.strip(),
                shadow=lower_third_shadow,
            )
            path = os.path.join(workdir, "ov_lower_third.png")
            _Image.fromarray(array).save(path)
            lt_x, lt_y = lower_third_position(width, height, array.shape[0])
            specs.append({
                "path": path, "x": lt_x, "y": lt_y,
                "start": round(start, 3),
                "duration": round(length, 3),
            })

    for index, cue in enumerate(schedule_cues(cues, duration, cue_duration)):
        show_timer = bool(cue.has_timer and cue.timer_duration >= 1.0)

        if card_style == "fullscreen" and cue.items:
            array = make_overview_card_image(
                width, height, cue.text, cue.items, logo_path
            )
            x, y = 0, 0
        elif card_style == "fullscreen":
            array = make_point_card_image(
                width, height, cue.label, cue.text, logo_path,
                reserve_bottom=TIMER_RESERVE if show_timer else 0.0,
            )
            x, y = 0, 0
        else:
            array = make_caption_image(width, height, cue.text, cue.label)
            x = int((width - array.shape[1]) / 2)
            y = max(int(height - array.shape[0] - height * 0.07), 0)

        path = os.path.join(workdir, f"ov_cue_{index:03d}.png")
        _Image.fromarray(array).save(path)
        specs.append({
            "path": path, "x": x, "y": y,
            "start": round(cue.start, 3),
            "duration": round(cue.duration, 3),
        })

        if not show_timer or not countdown_on_source:
            continue

        # The countdown runs at the END of the card: the question is read
        # first, then the group is given the silence to think.
        timer_start = max(cue.start, cue.start + cue.duration - cue.timer_duration)
        for step, (offset, remaining, length) in enumerate(
            timer_steps(min(cue.timer_duration, cue.duration))
        ):
            frame = make_timer_image(
                width, height, remaining, on_card=(card_style == "fullscreen"),
                total=min(cue.timer_duration, cue.duration),
            )
            frame_path = os.path.join(workdir, f"ov_timer_{index:03d}_{step:04d}.png")
            _Image.fromarray(frame).save(frame_path)
            specs.append({
                "path": frame_path,
                "x": int((width - frame.shape[1]) / 2),
                "y": int(height * TIMER_Y),
                "start": round(timer_start + offset, 3),
                "duration": round(length, 3),
            })

    return specs


def _render_with_ffmpeg(
    video_path: str,
    output_path: str,
    specs: Sequence[dict],
    duration: float,
    preset: str,
    threads: int,
    fade: float,
    progress_cb,
    force_audio_encode: bool = False,
    crf: int = 23,
    src_start: float = 0.0,
    src_end: Optional[float] = None,
) -> bool:
    """
    Fast path: hand the whole composite to FFmpeg in one pass.

    src_start/src_end render only that stretch of the recording. Overlay
    times in `specs` are absolute source times and are shifted here.

    FFmpeg does the layering in C and the original audio is copied through
    untouched, which is roughly twenty times quicker than decoding every frame
    into Python. Returns False if anything goes wrong, so the caller can fall
    back to the MoviePy path.
    """
    import re as _re
    import subprocess

    from transcriber import ffmpeg_exe

    # Keep only overlays that touch this stretch, clipped and shifted so
    # their times are relative to it.
    window_end = src_end if src_end is not None else float("inf")
    local: List[dict] = []
    for spec in specs:
        s0 = spec["start"]; s1 = spec["start"] + spec["duration"]
        a = max(s0, src_start); b = min(s1, window_end)
        if b - a < 0.05:
            continue
        local.append({**spec, "start": a - src_start, "duration": b - a})
    specs = local
    if src_end is not None:
        duration = src_end - src_start

    inputs: List[str] = []
    if src_start > 0:
        inputs += ["-ss", f"{src_start:.3f}"]
    if src_end is not None:
        inputs += ["-t", f"{src_end - src_start:.3f}"]
    inputs += ["-i", video_path]
    for spec in specs:
        inputs += ["-loop", "1", "-t", f"{spec['duration']:.3f}", "-i", spec["path"]]

    filters: List[str] = []
    last = "0:v"
    for index, spec in enumerate(specs, start=1):
        seconds = min(fade, spec["duration"] / 4.0) if fade else 0.0
        chain = "format=rgba"
        if seconds > 0.01:
            chain += (
                f",fade=t=in:st=0:d={seconds:.3f}:alpha=1"
                f",fade=t=out:st={max(spec['duration'] - seconds, 0):.3f}"
                f":d={seconds:.3f}:alpha=1"
            )
        chain += f",setpts=PTS+{spec['start']:.3f}/TB"
        filters.append(f"[{index}:v]{chain}[ov{index}]")

        end = spec["start"] + spec["duration"]
        label = f"[v{index}]"
        filters.append(
            f"[{last}][ov{index}]overlay=x={spec['x']}:y={spec['y']}"
            f":enable='between(t,{spec['start']:.3f},{end:.3f})'{label}"
        )
        last = f"v{index}"

    def build(audio_args: List[str]) -> List[str]:
        command = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
                   "-nostats", "-progress", "pipe:1", *inputs]
        if filters:
            command += ["-filter_complex", ";".join(filters), "-map", f"[{last}]"]
        else:
            command += ["-map", "0:v"]
        command += ["-map", "0:a?", *audio_args,
                    "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    "-threads", str(threads), output_path]
        return command

    time_re = _re.compile(r"out_time_us=(\d+)")

    encoded_audio = ["-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]
    # Copying the audio is quickest, but pieces that are about to be joined
    # must share identical audio parameters, so bookends force a re-encode.
    attempts = [encoded_audio] if force_audio_encode else [["-c:a", "copy"], encoded_audio]

    for audio_args in attempts:
        process = subprocess.Popen(
            build(audio_args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace",
        )
        for line in process.stdout:
            match = time_re.search(line)
            if match and progress_cb and duration:
                done = int(match.group(1)) / 1_000_000 / duration
                try:
                    progress_cb(min(done, 1.0), "Rendering video…")
                except Exception:
                    pass
        process.wait()
        if process.returncode == 0 and os.path.exists(output_path):
            return True

    return False


# --------------------------------------------------------------------------
# Discussion blocks and the piece-by-piece timeline
# --------------------------------------------------------------------------


def plan_pieces(cues: Sequence[Cue], duration: float) -> List[dict]:
    """
    Decide the order of the finished video.

    A stretch of the recording, then a discussion block, then the recording
    again from wherever the speaker resumed — for every application that has
    one. The speaker's own wait is what gets left out.

        [{"kind": "source", "start": 0.0,   "end": 209.5},
         {"kind": "pause",  "cue": <application>},
         {"kind": "source", "start": 251.0, "end": 1392.0}]
    """
    pauses = sorted(
        (c for c in cues if c.has_timer and c.timer_duration >= 1.0 and c.pause_at > 0),
        key=lambda c: c.pause_at,
    )
    pieces: List[dict] = []
    cursor = 0.0
    for cue in pauses:
        at = min(max(cue.pause_at, cursor), duration)
        if at - cursor > 0.05:
            pieces.append({"kind": "source", "start": cursor, "end": at})
        pieces.append({"kind": "pause", "cue": cue})
        resume = cue.cut_end if cue.cut_end > cue.cut_start else at
        cursor = min(max(resume, at), duration)
    if duration - cursor > 0.05:
        pieces.append({"kind": "source", "start": cursor, "end": duration})
    return pieces


def _render_pause_block(
    cue: Cue,
    output_path: str,
    width: int,
    height: int,
    fps: float,
    preset: str,
    crf: int,
    threads: int,
    workdir: str,
    logo_path: Optional[str],
    card_style: str,
) -> bool:
    """
    The discussion block: the application card held for the agreed time,
    with the countdown ticking beneath the question, over silence.
    """
    import subprocess

    from PIL import Image as _Image
    from transcriber import ffmpeg_exe

    seconds = float(cue.timer_duration)
    if card_style == "fullscreen":
        base = make_point_card_image(
            width, height, cue.label, cue.text, logo_path,
            reserve_bottom=TIMER_RESERVE,
        )
    else:
        base = np.zeros((height, width, 4), dtype=np.uint8)
        base[:, :, 3] = 255
    base_path = os.path.join(workdir, "pause_base.png")
    _Image.fromarray(base).convert("RGB").save(base_path)

    inputs = ["-loop", "1", "-t", f"{seconds:.3f}", "-i", base_path,
              "-f", "lavfi", "-t", f"{seconds:.3f}",
              "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

    # One overlay per countdown second, each enabled only for its own second.
    filters: List[str] = [f"[0:v]fps={fps},format=rgba[base]"]
    last = "base"
    for index, (offset, remaining, length) in enumerate(timer_steps(seconds), start=2):
        frame = make_timer_image(width, height, remaining,
                                 on_card=(card_style == "fullscreen"),
                                 total=seconds)
        frame_path = os.path.join(workdir, f"pause_timer_{index:04d}.png")
        _Image.fromarray(frame).save(frame_path)
        inputs += ["-loop", "1", "-t", f"{length:.3f}", "-i", frame_path]
        x = int((width - frame.shape[1]) / 2)
        y = int(height * TIMER_Y)
        filters.append(f"[{index}:v]format=rgba,setpts=PTS+{offset:.3f}/TB[t{index}]")
        filters.append(
            f"[{last}][t{index}]overlay=x={x}:y={y}"
            f":enable='between(t,{offset:.3f},{offset + length:.3f})'[v{index}]"
        )
        last = f"v{index}"
    filters.append(f"[{last}]format=yuv420p[vout]")

    result = subprocess.run([
        ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", "1:a",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-threads", str(threads),
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-t", f"{seconds:.3f}", output_path,
    ], capture_output=True, text=True)
    if result.returncode != 0:
        _last_error["pause_block"] = result.stderr[-600:]
    return result.returncode == 0 and os.path.exists(output_path)


# The most recent FFmpeg failure, for diagnosis.
_last_error: dict = {}


def _render_still_piece(
    image_path: str, output_path: str, seconds: float,
    width: int, height: int, fps: float, preset: str, crf: int, threads: int,
) -> bool:
    """An image held for `seconds` over silence, encoded like every other piece."""
    import subprocess

    from transcriber import ffmpeg_exe

    result = subprocess.run([
        ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-t", f"{seconds:.3f}", "-i", image_path,
        "-f", "lavfi", "-t", f"{seconds:.3f}",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-vf", (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"setsar=1,fps={fps},format=yuv420p"),
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-threads", str(threads),
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-shortest", output_path,
    ], capture_output=True)
    return result.returncode == 0 and os.path.exists(output_path)


def _concat_pieces(paths: Sequence[str], output_path: str, preset: str,
                   threads: int, crf: int) -> bool:
    """Join encoded pieces. Stream copy first; re-encode only if it refuses."""
    import subprocess
    import tempfile as _tempfile

    from transcriber import ffmpeg_exe

    if len(paths) == 1:
        import shutil as _shutil
        _shutil.move(paths[0], output_path)
        return True

    listing = os.path.join(_tempfile.mkdtemp(prefix="bsve_join_"), "pieces.txt")
    with open(listing, "w") as handle:
        for item in paths:
            handle.write(f"file '{os.path.abspath(item)}'\n")
    joined = subprocess.run(
        [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", listing,
         "-c", "copy", "-movflags", "+faststart", output_path],
        capture_output=True,
    )
    if joined.returncode == 0 and os.path.exists(output_path):
        return True

    inputs: List[str] = []
    for item in paths:
        inputs += ["-i", item]
    streams = "".join(f"[{i}:v][{i}:a]" for i in range(len(paths)))
    redone = subprocess.run(
        [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", *inputs,
         "-filter_complex", f"{streams}concat=n={len(paths)}:v=1:a=1[v][a]",
         "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
         "-pix_fmt", "yuv420p", "-threads", str(threads),
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", output_path],
        capture_output=True,
    )
    return redone.returncode == 0 and os.path.exists(output_path)


def render_video(
    video_path: str,
    output_path: str,
    *,
    speaker_name: str = "",
    speaker_title: str = "",
    cues: Sequence[Cue] = (),
    lower_third_start: float = LOWER_THIRD_START,
    lower_third_duration: float = LOWER_THIRD_DURATION,
    cue_duration: float = 8.0,
    card_style: str = "fullscreen",
    logo_path: Optional[str] = None,
    lower_third_shadow: bool = True,
    intro_image_path: Optional[str] = None,
    outro_image_path: Optional[str] = None,
    bookend_duration: float = BOOKEND_DURATION,
    quality: str = DEFAULT_QUALITY,
    preset: Optional[str] = None,
    crf: Optional[int] = None,
    threads: int = 4,
    fade: float = 0.0,
    engine: str = "auto",
    progress_cb: Optional[Callable[[float, str], None]] = None,
) -> str:
    """
    Composite every overlay onto the source video and write the final MP4.

    card_style="fullscreen"  beige full-screen point cards (the template)
    card_style="caption"     the older small card over the bottom of the video

    fade defaults to 0: the reference style cuts hard in and hard out. Set it
    to 0.25 or so for a soft dissolve instead.

    engine="auto"    try FFmpeg first, fall back to MoviePy   (the default)
    engine="ffmpeg"  FFmpeg only
    engine="moviepy" MoviePy only

    The FFmpeg path is roughly twenty times quicker on a full-length lesson
    because the frames never have to travel through Python.

    Encoding uses libx264 with +faststart, which plays everywhere:
    QuickTime, Windows Media Player, phones, YouTube, PowerPoint.
    """
    import shutil as _shutil
    import tempfile as _tempfile

    chosen_preset, chosen_crf = QUALITY_PRESETS.get(
        quality, QUALITY_PRESETS[DEFAULT_QUALITY]
    )
    preset = preset or chosen_preset
    crf = chosen_crf if crf is None else crf

    has_bookends = bool(
        (intro_image_path and os.path.exists(intro_image_path))
        or (outro_image_path and os.path.exists(outro_image_path))
    )

    if engine in ("auto", "ffmpeg"):
        probe = VideoFileClip(video_path)
        try:
            width, height = probe.size
            duration = float(probe.duration or 0.0)
            fps = float(probe.fps or 30)
        finally:
            probe.close()

        workdir = _tempfile.mkdtemp(prefix="bsve_overlays_")
        try:
            pieces = plan_pieces(cues, duration)
            has_pauses = any(piece["kind"] == "pause" for piece in pieces)

            specs = _overlay_specs(
                video_path, width, height, duration,
                speaker_name, speaker_title, cues,
                lower_third_start, lower_third_duration, cue_duration, workdir,
                card_style=card_style, logo_path=logo_path,
                lower_third_shadow=lower_third_shadow,
                countdown_on_source=not has_pauses,
            )

            if not has_pauses and not has_bookends:
                # The simple case: one pass over the whole recording.
                if _render_with_ffmpeg(
                    video_path, output_path, specs, duration,
                    preset, threads, fade, progress_cb, crf=crf,
                ):
                    return output_path
            else:
                # Piece by piece: stretches of the recording, discussion
                # blocks, and the bookends, each encoded identically and
                # then joined without a second pass over the video.
                rendered: List[str] = []
                total = sum(
                    (piece["end"] - piece["start"]) if piece["kind"] == "source"
                    else piece["cue"].timer_duration
                    for piece in pieces
                )
                done_so_far = [0.0]

                def piece_progress(fraction: float, message: str, span: float):
                    if progress_cb:
                        try:
                            progress_cb(
                                min((done_so_far[0] + fraction * span) / max(total, 1), 0.97),
                                "Rendering video…",
                            )
                        except Exception:
                            pass

                if intro_image_path and os.path.exists(intro_image_path):
                    path = os.path.join(workdir, "piece_intro.mp4")
                    if _render_still_piece(intro_image_path, path, bookend_duration,
                                           width, height, fps, preset, crf, threads):
                        rendered.append(path)

                for index, piece in enumerate(pieces):
                    path = os.path.join(workdir, f"piece_{index:03d}.mp4")
                    if piece["kind"] == "source":
                        span = piece["end"] - piece["start"]
                        ok = _render_with_ffmpeg(
                            video_path, path, specs, duration, preset, threads, fade,
                            lambda f, m, span=span: piece_progress(f, m, span),
                            force_audio_encode=True, crf=crf,
                            src_start=piece["start"], src_end=piece["end"],
                        )
                        done_so_far[0] += span
                    else:
                        ok = _render_pause_block(
                            piece["cue"], path, width, height, fps, preset, crf,
                            threads, workdir, logo_path, card_style,
                        )
                        done_so_far[0] += piece["cue"].timer_duration
                    if not ok:
                        rendered = []
                        break
                    rendered.append(path)

                if rendered and outro_image_path and os.path.exists(outro_image_path):
                    path = os.path.join(workdir, "piece_outro.mp4")
                    if _render_still_piece(outro_image_path, path, bookend_duration,
                                           width, height, fps, preset, crf, threads):
                        rendered.append(path)

                if rendered:
                    if progress_cb:
                        try:
                            progress_cb(0.98, "Joining the pieces…")
                        except Exception:
                            pass
                    if _concat_pieces(rendered, output_path, preset, threads, crf):
                        return output_path
        finally:
            _shutil.rmtree(workdir, ignore_errors=True)

        if engine == "ffmpeg":
            raise RuntimeError(
                "FFmpeg could not render this video. Try the MoviePy engine."
            )

    # ---- MoviePy fallback --------------------------------------------------
    base = VideoFileClip(video_path)
    overlays = []
    try:
        width, height = base.size
        duration = float(base.duration or 0.0)

        if speaker_name.strip() or speaker_title.strip():
            start = min(lower_third_start, max(duration - 1.0, 0.0))
            length = min(lower_third_duration, max(duration - start, 0.0))
            if length > 0.2:
                image = make_lower_third_image(
                    width, height, speaker_name.strip(), speaker_title.strip(),
                    shadow=lower_third_shadow,
                )
                clip = (
                    ImageClip(image, transparent=True)
                    .with_start(start)
                    .with_duration(length)
                    .with_position(lower_third_position(width, height, image.shape[0]))
                )
                overlays.append(_with_fade(clip, fade))

        for cue in schedule_cues(cues, duration, cue_duration):
            if card_style == "fullscreen":
                image = make_point_card_image(
                    width, height, cue.label, cue.text, logo_path
                )
                position = (0, 0)
            else:
                image = make_caption_image(width, height, cue.text, cue.label)
                position = (
                    int((width - image.shape[1]) / 2),
                    max(int(height - image.shape[0] - height * 0.07), 0),
                )
            clip = (
                ImageClip(image, transparent=True)
                .with_start(cue.start)
                .with_duration(cue.duration)
                .with_position(position)
            )
            overlays.append(_with_fade(clip, min(fade, cue.duration / 4)))

        final = CompositeVideoClip([base, *overlays])
        if has_bookends:
            final = add_bookends(
                final, intro_image_path, outro_image_path, bookend_duration
            )
        try:
            final.write_videofile(
                output_path,
                codec="libx264",
                audio_codec="aac",
                audio_bitrate="160k",
                preset=preset,
                threads=threads,
                fps=base.fps or 30,
                temp_audiofile=os.path.join(
                    os.path.dirname(output_path) or ".", "_bsve_temp_audio.m4a"
                ),
                remove_temp=True,
                ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
                logger=_progress_logger(progress_cb),
            )
        finally:
            final.close()
    finally:
        for clip in overlays:
            try:
                clip.close()
            except Exception:
                pass
        base.close()

    return output_path


def make_preview_frame(
    video_path: str,
    text: str,
    label: str = "",
    speaker_name: str = "",
    speaker_title: str = "",
    at_time: float = 5.0,
    *,
    card_style: str = "fullscreen",
    logo_path: Optional[str] = None,
    lower_third_shadow: bool = True,
) -> np.ndarray:
    """
    A single still showing exactly what will be rendered, so the look can be
    checked before committing to a full render.
    """
    with VideoFileClip(video_path) as clip:
        width, height = clip.size
        time = max(0.0, min(at_time, max((clip.duration or 1.0) - 0.1, 0.0)))
        frame = Image.fromarray(clip.get_frame(time)).convert("RGBA")

    if text and card_style == "fullscreen":
        # The card covers the whole frame, so nothing else is visible.
        card = Image.fromarray(
            make_point_card_image(width, height, label, text, logo_path)
        )
        return np.array(card.convert("RGB"))

    if speaker_name or speaker_title:
        lower = Image.fromarray(
            make_lower_third_image(width, height, speaker_name, speaker_title,
                                   shadow=lower_third_shadow)
        )
        frame.alpha_composite(
            lower, lower_third_position(width, height, lower.height)
        )

    if text:
        caption = Image.fromarray(make_caption_image(width, height, text, label))
        x = int((width - caption.width) / 2)
        y = int(height - caption.height - height * 0.07)
        frame.alpha_composite(caption, (max(x, 0), max(y, 0)))

    return np.array(frame.convert("RGB"))


def video_info(video_path: str) -> dict:
    with VideoFileClip(video_path) as clip:
        return {
            "duration": float(clip.duration or 0.0),
            "size": tuple(clip.size),
            "fps": float(clip.fps or 0.0),
            "has_audio": clip.audio is not None,
        }
