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
from dataclasses import dataclass
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
CARD_BG = (223, 218, 209, 255)        # #DFDAD1 beige
CARD_TEXT = (18, 18, 18, 255)         # near-black, easier on the eye than #000
CARD_LOGO_BOX = 120                   # reserved square, top right, in pixels
CARD_LOGO_MARGIN = 40

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
TIMER_Y = 0.79                        # fraction of frame height, its top edge
TIMER_RESERVE = 0.20                  # room kept clear under the body text
TIMER_MAX_STEPS = 240                 # ceiling on how many second-frames we draw

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

_REGULAR_CANDIDATES = [
    os.environ.get("OVERLAY_FONT", ""),
    *sorted(glob.glob(os.path.join(_HERE, "fonts", "*Regular*.tt*"))),
    *sorted(glob.glob(os.path.join(_HERE, "fonts", "*.tt*"))),
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]

_BOLD_CANDIDATES = [
    os.environ.get("OVERLAY_FONT_BOLD", ""),
    *sorted(glob.glob(os.path.join(_HERE, "fonts", "*Bold*.tt*"))),
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]

_FONT_CACHE: dict = {}


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    size = max(10, int(size))
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

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
    for path in _BOLD_CANDIDATES + _REGULAR_CANDIDATES:
        if path and os.path.exists(path):
            return os.path.basename(path)
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
    header_font = load_font(video_h * 0.072, bold=True)
    body_font = load_font(video_h * 0.052, bold=False)

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
    top = int(video_h * 0.16)
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
    max_body_w = int(video_w * (0.72 if as_list else 0.76))

    lines: List[str] = []
    for entry in raw_lines:
        lines.extend(_wrap(entry, body_font, max_body_w, 4))

    line_h = int(body_font.size * 1.42)
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


def format_countdown(seconds: float) -> str:
    """`MM:SS`, never negative."""
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def make_timer_image(
    video_w: int,
    video_h: int,
    seconds_remaining: float,
    *,
    on_card: bool = True,
) -> np.ndarray:
    """
    One frame of the reflection countdown: a clean MM:SS.

    Drawn on transparent background so it can sit over the beige card (black
    text) or straight over the footage (white text with a halo, since there is
    no telling what is behind it).
    """
    font = load_font(video_h * 0.078, bold=True)
    text = format_countdown(seconds_remaining)

    width = int(_text_width(text, font)) + 24
    height = int(font.size * 1.45)
    img = Image.new("RGBA", (max(width, 1), max(height, 1)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    x = (width - _text_width(text, font)) / 2
    if on_card:
        draw.text((x, 4), text, font=font, fill=CARD_TEXT)
    else:
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (2, 2)):
            draw.text((x + dx, 4 + dy), text, font=font, fill=(0, 0, 0, 110))
        draw.text((x, 4), text, font=font, fill=WHITE)
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

    def write(text: str, font, x: float, y: float) -> None:
        if shadow:
            # A cheap halo: the same glyphs in translucent black, offset in
            # four directions, drawn underneath.
            for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (2, 2)):
                draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 90))
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
                           cue.has_timer, cue.timer_duration))

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
                             round(duration, 2), cue.has_timer, cue.timer_duration))
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
                "-c:v", "libx264", "-preset", preset, "-crf", "23",
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
             "-c:v", "libx264", "-preset", preset, "-crf", "23",
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
) -> List[dict]:
    """
    Draw every overlay to a PNG on disk and return where and when each one
    belongs. Shared by both rendering paths.
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

        if card_style == "fullscreen":
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

        if not show_timer:
            continue

        # The countdown runs at the END of the card: the question is read
        # first, then the group is given the silence to think.
        timer_start = max(cue.start, cue.start + cue.duration - cue.timer_duration)
        for step, (offset, remaining, length) in enumerate(
            timer_steps(min(cue.timer_duration, cue.duration))
        ):
            frame = make_timer_image(
                width, height, remaining, on_card=(card_style == "fullscreen")
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
) -> bool:
    """
    Fast path: hand the whole composite to FFmpeg in one pass.

    FFmpeg does the layering in C and the original audio is copied through
    untouched, which is roughly twenty times quicker than decoding every frame
    into Python. Returns False if anything goes wrong, so the caller can fall
    back to the MoviePy path.
    """
    import re as _re
    import subprocess

    from transcriber import ffmpeg_exe

    inputs: List[str] = ["-i", video_path]
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
                    "-c:v", "libx264", "-preset", preset, "-crf", "23",
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
    preset: str = "ultrafast",
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
            specs = _overlay_specs(
                video_path, width, height, duration,
                speaker_name, speaker_title, cues,
                lower_third_start, lower_third_duration, cue_duration, workdir,
                card_style=card_style, logo_path=logo_path,
                lower_third_shadow=lower_third_shadow,
            )

            # With bookends the overlays land in a scratch file first, then the
            # three pieces are joined; without them we write straight to target.
            body_path = (
                os.path.join(workdir, "body.mp4") if has_bookends else output_path
            )
            if _render_with_ffmpeg(
                video_path, body_path, specs, duration,
                preset, threads, fade, progress_cb,
                force_audio_encode=has_bookends,
            ):
                if not has_bookends:
                    return output_path
                if progress_cb:
                    try:
                        progress_cb(0.97, "Adding the intro and outro…")
                    except Exception:
                        pass
                if _concat_bookends(
                    body_path, output_path, intro_image_path, outro_image_path,
                    width, height, fps, bookend_duration, preset, threads,
                ):
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
