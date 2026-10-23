"""
reviewer.py
===========
Checks a finished video the way a careful person would — the way every
lesson was checked by hand while this tool was being built.

After a render it:

  * measures the picture and the sound, and the whole video against the plan;
  * looks at the opening and closing slides, every card and every countdown;
  * LISTENS to the finished video just before and just after every countdown
    (the question should be heard before it, the teacher after it — not the
    room still talking), and to its first and last moments;
  * reads the recording's own transcript for teaching that a cut removed and
    for long dead time that was left in.

Each check gives a Finding. Where a problem has a safe, well-understood fix
(say, a countdown that comes back while the groups are still talking), the
Finding carries it, and the app offers it as a button — applied to the plan,
never to a file, and only when the person presses it.

Nothing here changes a video, a setting or a file, and nothing here uses the
AI allowance: listening is done on this computer.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

import numpy as np
from PIL import Image

import editor
import matcher
import transcriber
from transcriber import Segment, ffmpeg_exe

OK, CHECK, PROBLEM = "ok", "check", "problem"
LEVEL_ICON = {OK: "✅", CHECK: "⚠️", PROBLEM: "⛔"}

LISTEN_SECONDS = 10.0
LENGTH_TOLERANCE = 1.5          # seconds between the plan and the finished video
SYNC_TOLERANCE = 0.15           # seconds between its picture and its sound
CARD_MATCH = 0.45               # share of a frame that must be card-coloured
DEAD_TIME_SECONDS = 25.0        # silence left in worth mentioning
TEACHER_CONFIDENCE = 0.70


@dataclass
class Finding:
    level: str
    title: str
    detail: str = ""
    at: float = -1.0                     # seconds into the finished video
    fix: Optional[dict] = None           # see apply_fix in app.py
    fix_label: str = ""


@dataclass
class Plan:
    """Everything the review needs to know about how the video was made."""
    output_path: str
    cues: list
    duration: float
    keep_from: float = 0.0
    keep_to: float = 0.0
    intro_path: str = ""
    outro_path: str = ""
    bookend_seconds: float = 5.0
    card_style: str = "fullscreen"
    rows: list = field(default_factory=list)          # the timings table, as dicts
    language: str = "en"


# --------------------------------------------------------------------------
# Where everything lands in the finished video
# --------------------------------------------------------------------------


def timeline(plan: Plan) -> tuple:
    """([piece, ...], length before the closing slide). Mirrors render_video."""
    end = plan.keep_to if plan.keep_to and plan.keep_to < plan.duration else 0.0
    pieces = editor.plan_pieces(plan.cues, plan.duration, plan.keep_from, end)
    out = plan.bookend_seconds if plan.intro_path else 0.0
    mapped = []
    for piece in pieces:
        if piece["kind"] == "source":
            length = piece["end"] - piece["start"]
            mapped.append({**piece, "out_start": out, "out_end": out + length})
        else:
            length = float(piece["cue"].timer_duration)
            mapped.append({**piece, "out_start": out, "out_end": out + length})
        out += length
    return mapped, out


def to_output(seconds: float, mapped: list) -> Optional[float]:
    """Where a moment of the recording is in the finished video, or None if cut."""
    for piece in mapped:
        if piece["kind"] == "source" and piece["start"] - 0.01 <= seconds < piece["end"]:
            return piece["out_start"] + seconds - piece["start"]
    return None


# --------------------------------------------------------------------------
# Looking and listening
# --------------------------------------------------------------------------


def _frame(path: str, at: float, size=(64, 36)) -> Optional[np.ndarray]:
    try:
        result = subprocess.run(
            [ffmpeg_exe(), "-v", "error", "-ss", f"{max(at, 0):.3f}", "-i", path,
             "-frames:v", "1", "-vf", f"scale={size[0]}:{size[1]}",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, timeout=60,
        )
        data = np.frombuffer(result.stdout, dtype=np.uint8)
        if data.size != size[0] * size[1] * 3:
            return None
        return data.reshape(size[1], size[0], 3).astype(np.int16)
    except Exception:
        return None


def _card_share(frame: np.ndarray) -> float:
    """How much of a frame is the beige of the cards."""
    target = np.array(editor.CARD_BG[:3], dtype=np.int16)
    close = np.all(np.abs(frame - target) <= 22, axis=2)
    return float(close.mean())


def _slide_matches(frame: np.ndarray, slide_path: str, video_size: tuple) -> bool:
    """Is this frame the slide, fitted to the video the way the render fits it?"""
    try:
        width, height = video_size
        canvas = Image.new("RGB", (width, height), (0, 0, 0))
        with Image.open(slide_path) as slide:
            fitted = slide.convert("RGB")
            fitted.thumbnail((width, height), Image.LANCZOS)
            canvas.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
        expected = np.asarray(canvas.resize((frame.shape[1], frame.shape[0]))).astype(np.int16)
        return float(np.abs(frame - expected).mean()) < 28.0
    except Exception:
        return True                      # can't tell: don't cry wolf


def _listen(path: str, start: float, seconds: float, language: str) -> List[Segment]:
    """What is said in the finished video between start and start+seconds."""
    work = tempfile.mkdtemp(prefix="bsve_review_")
    clip = os.path.join(work, "clip.wav")
    try:
        subprocess.run(
            [ffmpeg_exe(), "-v", "error", "-y", "-ss", f"{max(start, 0):.3f}",
             "-t", f"{seconds:.3f}", "-i", path, "-vn", "-ac", "1", "-ar", "16000", clip],
            capture_output=True, timeout=120, check=True,
        )
        model = transcriber._load_local_model("base")
        with transcriber._TRANSCRIBE_LOCK:
            return transcriber._decode(model, clip, language or None, True, None, seconds)
    finally:
        try:
            os.remove(clip)
            os.rmdir(work)
        except OSError:
            pass


def _heard(segments: Sequence[Segment]) -> tuple:
    words = [w for s in segments for w in (s.get("words") or [])]
    text = " ".join(s.text.strip() for s in segments).strip()
    confidence = (sum(w.get("p", 1.0) for w in words) / len(words)) if words else 0.0
    return text, len(words), confidence


_WORD = re.compile(r"[a-z']{4,}")


def _overlap(expected: str, heard: str) -> float:
    """Share of the expected text's longer words that were heard."""
    want = set(_WORD.findall(expected.lower()))
    got = set(_WORD.findall(heard.lower()))
    return len(want & got) / len(want) if want else 1.0


def _stamp(seconds: float) -> str:
    return transcriber.format_timestamp(max(seconds, 0.0))


# --------------------------------------------------------------------------
# The review
# --------------------------------------------------------------------------


def review(plan: Plan, segments: Sequence[Segment],
           progress_cb: Optional[Callable[[str], None]] = None) -> List[Finding]:
    """Every check, in the order a person watching would meet them."""
    def say(message: str) -> None:
        if progress_cb:
            try:
                progress_cb(message)
            except Exception:
                pass

    findings: List[Finding] = []
    path = plan.output_path
    mapped, before_outro = timeline(plan)
    expected_total = before_outro + (plan.bookend_seconds if plan.outro_path else 0.0)
    info = editor.video_info(path)
    size = info.get("size") or (1920, 1080)
    fullscreen = plan.card_style == "fullscreen"
    segments = list(segments or [])

    # 1. Length, and picture against sound.
    say("Measuring the finished video…")
    lengths = editor.track_lengths(path)
    if abs(lengths["video"] - lengths["audio"]) > SYNC_TOLERANCE:
        findings.append(Finding(PROBLEM, "Picture and sound are out of step",
                                f"The picture runs {lengths['video']:.1f}s and the sound "
                                f"{lengths['audio']:.1f}s. Render again."))
    elif abs(lengths["video"] - expected_total) > LENGTH_TOLERANCE:
        findings.append(Finding(PROBLEM, "The video isn't the length it should be",
                                f"It runs {_stamp(lengths['video'])}; the plan was "
                                f"{_stamp(expected_total)}. Render again."))
    else:
        findings.append(Finding(OK, "Picture and sound are in step",
                                f"{_stamp(lengths['video'])} long, exactly as planned."))

    # 2. The slides.
    say("Looking at the opening and closing slides…")
    for slide, at, name in ((plan.intro_path, min(1.0, plan.bookend_seconds / 2), "opening"),
                            (plan.outro_path, expected_total - min(1.0, plan.bookend_seconds / 2),
                             "closing")):
        if not slide:
            continue
        frame = _frame(path, at)
        if frame is not None and _slide_matches(frame, slide, size):
            findings.append(Finding(OK, f"The {name} slide is there", at=at))
        else:
            findings.append(Finding(CHECK, f"The {name} slide doesn't look right",
                                    "Have a look at it in the video.", at=at))

    # 3. Every card, and every countdown.
    say("Looking at every card and countdown…")
    cues = sorted(plan.cues, key=lambda c: c.start)
    for first, second in zip(cues, cues[1:]):
        if first.start + first.duration > second.start + 0.05:
            findings.append(Finding(PROBLEM, "Two cards overlap",
                                    f"“{first.text[:50]}” and “{second.text[:50]}”."))
    missing_cards = []
    if fullscreen:
        for cue in cues:
            moment = cue.start + min(1.0, max(cue.duration / 2, 0.2))
            at = to_output(moment, mapped)
            if at is None:
                if not cue.has_timer:
                    missing_cards.append((cue, None))
                continue
            frame = _frame(path, at)
            if frame is not None and _card_share(frame) < CARD_MATCH:
                missing_cards.append((cue, at))
    for cue, at in missing_cards:
        findings.append(Finding(
            CHECK, f"A card may not be showing: “{cue.text[:60]}”",
            "It falls inside a part that was cut." if at is None else
            "The frame where it should be doesn't look like a card.", at=at if at else -1))
    if fullscreen and cues and not missing_cards:
        findings.append(Finding(OK, f"All {len(cues)} cards appear where planned"))

    blocks = [p for p in mapped if p["kind"] == "pause"]
    for block in blocks:
        frame = _frame(path, (block["out_start"] + block["out_end"]) / 2)
        if fullscreen and frame is not None and _card_share(frame) < CARD_MATCH * 0.6:
            findings.append(Finding(CHECK, "A countdown doesn't look right",
                                    f"“{block['cue'].text[:60]}”", at=block["out_start"]))

    # 4. Listen around every countdown, and at the very start and end.
    listening = transcriber.LOCAL_AVAILABLE
    if not listening:
        findings.append(Finding(CHECK, "Couldn't listen to the finished video",
                                "The speech engine isn't available on this computer."))
    rows = plan.rows or []
    for index, block in enumerate(blocks, start=1):
        cue = block["cue"]
        say(f"Listening around countdown {index} of {len(blocks)}…")
        row = next((i for i, r in enumerate(rows) if str(r.get("Point")) == cue.text), None)
        resume_source = cue.cut_end if cue.cut_end > cue.cut_start else cue.pause_at
        next_start = min([c.start for c in cues if c.start > cue.pause_at + 1.0] or
                         [plan.keep_to or plan.duration])

        if listening:
            try:
                before = _heard(_listen(path, block["out_start"] - 2 * LISTEN_SECONDS,
                                        2 * LISTEN_SECONDS, plan.language))
                after = _heard(_listen(path, block["out_end"] + 0.2,
                                       LISTEN_SECONDS, plan.language))
            except Exception as exc:
                findings.append(Finding(CHECK, f"Couldn't listen around countdown {index}",
                                        str(exc)[:160]))
                continue
            if _overlap(cue.text, before[0]) < 0.25 and before[1] >= 3:
                findings.append(Finding(
                    CHECK, f"Countdown {index} may not follow its question",
                    f"Heard just before it: “{before[0][:160]}”", at=block["out_start"]))
            remaining = before_outro - block["out_end"]
            chatter = after[1] >= 3 and after[2] < TEACHER_CONFIDENCE
            next_word = next((w["start"] for w in transcriber.transcript_words(segments)
                              if w["start"] >= resume_source - 0.2), None) if segments else None
            quiet = (after[1] < 3 and remaining > LISTEN_SECONDS + 2 and next_word is not None
                     and next_word - resume_source >= 6.0)
            if chatter:
                fix_at = _teacher_back(segments, resume_source, next_start)
                findings.append(Finding(
                    CHECK, f"After countdown {index}, the video may come back while the room "
                           "is still talking",
                    f"Heard: “{after[0][:160]}” — spoken unclearly, like a room of people."
                    + (f" The teacher seems to resume at {_stamp(fix_at)} in the recording."
                       if fix_at else ""),
                    at=block["out_end"],
                    fix={"op": "resume", "row": row, "at": fix_at} if fix_at and row is not None
                    else None,
                    fix_label=f"Come back at {_stamp(fix_at)} instead" if fix_at else ""))
            elif quiet:
                fix_at = round(next_word - 0.3, 2)
                findings.append(Finding(
                    CHECK, f"After countdown {index}, the video comes back to "
                           f"{next_word - resume_source:.0f} seconds of silence",
                    "Nothing is said until the teacher speaks again.", at=block["out_end"],
                    fix={"op": "resume", "row": row, "at": fix_at} if row is not None else None,
                    fix_label=f"Come back at {_stamp(fix_at)} instead"))
            else:
                findings.append(Finding(OK, f"Countdown {index}: question, then the teacher",
                                        f"Comes back on: “{after[0][:110] or '(the end)'}”",
                                        at=block["out_end"]))

        # A discussion that was announced, and the teacher's call back to the
        # room coming AFTER the point the video returns to: it returned while
        # the groups were still talking, however clearly one of them spoke.
        minutes = matcher.announced_breakout(segments, cue.pause_at) if segments else 0
        if minutes and segments:
            back = _call_back_after(segments, resume_source + 1.0,
                                    min(next_start, cue.pause_at + minutes * 90 + 60))
            if back and back > resume_source + 3.0:
                findings.append(Finding(
                    CHECK, f"Countdown {index}: the group discussion may not be fully cut",
                    f"The teacher calls the room back at {_stamp(back)} in the recording; "
                    f"the video comes back at {_stamp(resume_source)}.",
                    at=block["out_end"],
                    fix={"op": "resume", "row": row, "at": back} if row is not None else None,
                    fix_label=f"Cut to {_stamp(back)}"))

    if listening:
        say("Listening to the start and the end…")
        start_at = plan.bookend_seconds if plan.intro_path else 0.0
        try:
            opening = _heard(_listen(path, start_at, LISTEN_SECONDS, plan.language))
            if opening[1] < 3:
                findings.append(Finding(CHECK, "The video opens on silence or setup",
                                        f"Heard: “{opening[0] or '(nothing)'}”", at=start_at))
            else:
                findings.append(Finding(OK, "Opens on the teacher",
                                        f"“{opening[0][:110]}”", at=start_at))
            ending = _heard(_listen(path, max(before_outro - LISTEN_SECONDS, 0),
                                    LISTEN_SECONDS, plan.language))
            unclear = ending[1] >= 3 and ending[2] < TEACHER_CONFIDENCE
            findings.append(Finding(
                CHECK if unclear else OK,
                "The ending may include people getting up and talking" if unclear
                else "Ends cleanly",
                f"Last words: “{ending[0][-140:] or '(quiet)'}”", at=before_outro))
        except Exception as exc:
            findings.append(Finding(CHECK, "Couldn't listen to the start and end", str(exc)[:160]))

    # 5. From the transcript: teaching inside a cut, dead time left in.
    if segments:
        say("Reading the transcript for anything cut by mistake…")
        findings += _teaching_cut(plan, segments, rows, cues)
        findings += _dead_time(segments, mapped)

    hidden = [r for r in rows if not r.get("Show", True)]
    if hidden:
        findings.append(Finding(CHECK, f"{len(hidden)} point(s) are switched off",
                                "They're not in the video: " +
                                "; ".join(str(r.get('Point'))[:40] for r in hidden[:4])))
    return findings


_CLOSING = re.compile(r"\blet'?s close\b", re.IGNORECASE)


def _call_back_after(segments: Sequence[Segment], after: float, limit: float) -> Optional[float]:
    """
    The first time the teacher calls the room back between two moments.

    If the video already comes back ON a call back, it came back at the right
    place. "Let's close in prayer" is a closing, not a call back.
    """
    landing = next((s for s in segments if s.end > after - 1.0), None)
    if landing is not None and landing.start <= after + 1.0 \
            and matcher._CALL_BACK.search(landing.text.strip()):
        return None
    for segment in segments:
        text = segment.text.strip()
        if after <= segment.start <= limit and matcher._CALL_BACK.search(text) \
                and not (_CLOSING.search(text) and not re.search(
                    r"brothers|hope you had|welcome back", text, re.IGNORECASE)):
            return round(float(segment.start), 2)
    return None


def _teacher_back(segments: Sequence[Segment], after: float, limit: float) -> Optional[float]:
    """When the teacher next addresses the room after `after` (and before `limit`)."""
    back = matcher.find_call_back(segments, after - 30.0, limit, 3.0)
    if back and back > after + 2.0:
        return back
    for run in transcriber.speech_runs(segments, max_gap=2.0):
        if after + 3.0 < run["start"] < limit and run["words"] >= 12 \
                and run["p"] >= TEACHER_CONFIDENCE:
            return round(run["start"], 2)
    return None


def _teaching_cut(plan: Plan, segments, rows, cues) -> List[Finding]:
    """An outline point read out inside a part that is cut away."""
    out = []
    words = transcriber.transcript_words(segments)
    for cue in cues:
        if not (cue.has_timer and cue.cut_end > cue.cut_start):
            continue
        inside = [w for w in words if cue.cut_start <= w["start"] and w["end"] <= cue.cut_end]
        said = " ".join(str(w.get("word", "")) for w in inside)
        for row in rows:
            text = str(row.get("Point") or "")
            if not text or text == cue.text or len(_WORD.findall(text.lower())) < 2:
                continue
            if _overlap(text, said) >= 0.75:
                first = next((w["start"] for w in inside
                              if _WORD.findall(str(w.get("word", "")).lower())
                              and _WORD.findall(str(w.get("word", "")).lower())[0]
                              in set(_WORD.findall(text.lower()))), cue.cut_start)
                out.append(Finding(
                    PROBLEM, f"“{text[:60]}” seems to be read inside a part that is cut",
                    f"Around {_stamp(first)} in the recording, between "
                    f"{_stamp(cue.cut_start)} and {_stamp(cue.cut_end)} — it would be missing "
                    "from the video. Check the countdown before it in the timings table."))
    return out


def _dead_time(segments, mapped) -> List[Finding]:
    """Long silence left in the parts of the recording that are kept."""
    out = []
    words = transcriber.transcript_words(segments)
    for piece in [p for p in mapped if p["kind"] == "source"]:
        inside = [w for w in words if piece["start"] <= w["start"] < piece["end"]]
        edges = [piece["start"]] + [w["end"] for w in inside]
        starts = [w["start"] for w in inside] + [piece["end"]]
        for gap_start, gap_end in zip(edges, starts):
            if gap_end - gap_start >= DEAD_TIME_SECONDS:
                at = to_output(gap_start + 0.1, mapped)
                out.append(Finding(
                    CHECK, f"{gap_end - gap_start:.0f} seconds of silence are left in",
                    f"From {_stamp(gap_start)} in the recording — fine if it's a planned "
                    "pause; otherwise it could be cut.", at=at if at is not None else -1))
    return out


def summary(findings: Sequence[Finding]) -> str:
    problems = sum(1 for f in findings if f.level == PROBLEM)
    checks = sum(1 for f in findings if f.level == CHECK)
    if problems:
        return f"{problems} problem(s) and {checks} thing(s) to check"
    if checks:
        return f"Looks good — {checks} thing(s) worth a look"
    return "Everything checks out"
