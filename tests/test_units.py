"""
Fast checks — no video, no network, no API key.

Every case here comes from a real lesson that once came out wrong. They run
in a second or two, so run them after any change:

    python tests/test_units.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import matcher  # noqa: E402
import verifier  # noqa: E402
from transcriber import Segment  # noqa: E402

PASSED = []
FAILED = []


def check(name, got, want):
    if got == want:
        PASSED.append(name)
    else:
        FAILED.append(f"{name}\n      got:  {got!r}\n      want: {want!r}")


def check_that(name, condition, detail=""):
    if condition:
        PASSED.append(name)
    else:
        FAILED.append(f"{name}  {detail}")


def words(text, start, rate=2.5, confidence=0.95):
    """Turn a line into timed words, `rate` words a second."""
    out = []
    for index, word in enumerate(text.split()):
        at = start + index / rate
        out.append({"start": at, "end": at + 1 / rate, "word": word, "p": confidence})
    return out


def speech(lines):
    """[(text, start, words-per-second, confidence), ...] -> segments."""
    segments = []
    for text, start, rate, confidence in lines:
        spoken = words(text, start, rate, confidence)
        segments.append(Segment(spoken[0]["start"], spoken[-1]["end"], text, spoken))
    return segments


# --------------------------------------------------------------------------
# Outlines as people actually paste them
# --------------------------------------------------------------------------

check(
    "a scripture reference under a division joins that division",
    matcher.split_divisions(
        "DIVISION 1 — GOSPEL CLAIM\nRomans 1:1–7\n\nDIVISION 2 — GOSPEL PASSION\nRomans 1:8–15"
    ),
    ["DIVISION 1 — GOSPEL CLAIM (Romans 1:1–7)",
     "DIVISION 2 — GOSPEL PASSION (Romans 1:8–15)"],
)
check(
    "tabs, runs of spaces and a spaced colon survive a paste",
    matcher.split_divisions(
        "DIVISION 2 —\tMAN EXCHANGES GOD'S REVELATION           \n"
        "                       Romans 1: 21-27"
    ),
    ["DIVISION 2 — MAN EXCHANGES GOD'S REVELATION (Romans 1:21-27)"],
)
check(
    "a division that already names its passage is left alone",
    matcher.split_divisions("I. Man-initiated Religion (Zechariah 7)"),
    ["I. Man-initiated Religion (Zechariah 7)"],
)
check(
    "a question written over several lines stays one card",
    matcher.split_items(
        "When you know you've done something wrong, are you most likely to\n"
        "(a) make excuses,\n(b) try harder, or\n(c) admit your need for grace?\n\nWhy?"
    ),
    ["When you know you've done something wrong, are you most likely to "
     "(a) make excuses, (b) try harder, or (c) admit your need for grace? Why?"],
)
check(
    "two separate points stay two points",
    matcher.split_items("Trust God.\nObey God."),
    ["Trust God.", "Obey God."],
)
check(
    "scripture: reference, then the verse over several lines",
    [ref for ref, _ in matcher.parse_scripture(
        "ROMANS 3:21–22\n“But now apart from the law the righteousness of God\n"
        "has been made known.\n\nEXODUS 20:3\n“You shall have no other gods before me.”"
    )],
    ["Romans 3:21–22", "Exodus 20:3"],
)

# --------------------------------------------------------------------------
# Dead time after a question: silence, a room discussing, or neither
# --------------------------------------------------------------------------

QUESTION = "Here is our application question where do you need to stand still and let God fight three minutes"

silent_wait = speech([
    (QUESTION, 10.0, 2.5, 0.95),
    ("Welcome back our second division is the law at Sinai and the principle is that holiness flows from rescue", 160.0, 2.5, 0.95),
])
quiet = matcher.find_dead_time(silent_wait, 17.0, 200.0)
check_that(
    "a long silent wait is cut, up to where the teacher resumes",
    abs(quiet[0] - 17.2) < 0.6 and abs(quiet[1] - 160.0) < 0.6 and quiet[2] == "silence",
    str(quiet),
)

# The room answers the question out loud: fragments, unsure transcription.
audience = speech([
    (QUESTION, 10.0, 2.5, 0.95),
    ("yeah for me it is work I keep trying to", 25.0, 4.0, 0.55),
    ("control everything and honestly I never", 31.0, 4.0, 0.45),
    ("let go of it same here it is hard", 37.0, 4.0, 0.50),
    ("okay let us come back together our second division is the law at Sinai and holiness flows from rescue", 70.0, 2.5, 0.94),
])
dead = matcher.find_dead_time(audience, 17.0, 120.0)
check_that("an audience discussing out loud is cut like silence",
           abs(dead[0] - 17.2) < 0.6 and abs(dead[1] - 70.0) < 0.6 and dead[2] == "discussion",
           str(dead))

straight_on = speech([
    (QUESTION, 10.0, 2.5, 0.95),
    ("and that brings us straight to our second division at Sinai where holiness flows from the rescue", 21.0, 2.5, 0.95),
])
check("a teacher who carries straight on has nothing cut",
      matcher.find_dead_time(straight_on, 17.0, 60.0), None)
check_that(
    "the block goes at the end of the question, not mid-sentence",
    abs(matcher.speech_end_after(straight_on, 14.0, 60.0) - 17.2) < 0.6,
    f"got {matcher.speech_end_after(straight_on, 14.0, 60.0):.2f}",
)

# --------------------------------------------------------------------------
# The words outrank the model's number
# --------------------------------------------------------------------------

lesson = speech([
    ("Our takeaway is that God's wrath is a revelation of living life without him", 20.0, 2.5, 0.95),
    ("This brings us to our first principle human wickedness and rebellion justify God's wrath", 200.0, 2.5, 0.95),
    ("This brings us to our second principle God will deliver you over to the desires of your heart", 600.0, 2.5, 0.95),
])
points = [
    matcher.LessonPoint(id="principle_1", category="Principle",
                        text="Human wickedness and rebellion justify God's wrath."),
    matcher.LessonPoint(id="principle_2", category="Principle",
                        text="God will deliver you over to the desires of your heart."),
]
# A weak model: right quotes, wrong seconds (both moved to the same late spot).
wrong = [
    matcher.Element(type="principle", header="Principle #1", content=points[0].text,
                    start_time=800.0, end_time=812.0, id="principle_1", confidence=0.8,
                    evidence="This brings us to our first principle"),
    matcher.Element(type="principle", header="Principle #2", content=points[1].text,
                    start_time=820.0, end_time=832.0, id="principle_2", confidence=0.8,
                    evidence="This brings us to our second principle"),
]
verdicts = verifier.verify_matches(wrong, points, lesson, 900.0)
placed = {v.match.id: v.match.start_time for v in verdicts}
check_that("a card whose quote is elsewhere moves to the words",
           abs(placed["principle_1"] - 200.0) < 3.0, f"got {placed['principle_1']:.1f}")
check_that("near-identical transitions are told apart by the point's own words",
           abs(placed["principle_2"] - 600.0) < 3.0, f"got {placed['principle_2']:.1f}")
check_that("both end up verified", all(v.verdict == verifier.VERIFIED for v in verdicts),
           str([(v.match.id, v.verdict) for v in verdicts]))

# A second opinion the recording does not support must not demote a good card.
right = [matcher.Element(type="principle", header="Principle #1", content=points[0].text,
                         start_time=200.0, end_time=212.0, id="principle_1", confidence=0.8,
                         evidence="This brings us to our first principle")]
second = [matcher.Element(type="principle", header="Principle #1", content=points[0].text,
                          start_time=700.0, end_time=712.0, id="principle_1", confidence=0.9,
                          evidence="")]
only = verifier.verify_matches(right, [points[0]], lesson, 900.0, second_opinion=second)[0]
check("an unsupported second opinion does not demote a good placement",
      only.verdict, verifier.VERIFIED)

# --------------------------------------------------------------------------
# A moved question takes its discussion block with it
# --------------------------------------------------------------------------

q_lesson = speech([
    ("Our application question is where do you need to stand still and let God fight three minutes", 100.0, 2.5, 0.95),
    ("Welcome back everyone and now our second division is the law at Sinai where holiness flows", 300.0, 2.5, 0.95),
])
q_point = matcher.LessonPoint(id="application_1", category="Application",
                              text="Where do you need to stand still and let God fight?")
q_wrong = [matcher.Element(type="application", header="Application #1", content=q_point.text,
                           start_time=500.0, end_time=515.0, id="application_1", confidence=0.8,
                           evidence="Our application question is where do you need to stand still")]
laid = verifier.lay_out(
    verifier.verify_matches(q_wrong, [q_point], q_lesson, 600.0), 600.0,
    segments=q_lesson, silences=[], pause_seconds=30.0, overview=False,
)
block = laid[0].match
check_that("the block follows the question to its corrected place",
           100.0 < block.pause_at < 130.0 and abs(block.cut_end - 300.0) < 3.0,
           f"pause_at {block.pause_at:.1f}, cut {block.cut_start:.1f}->{block.cut_end:.1f}")

# --------------------------------------------------------------------------

print(f"{len(PASSED)} passed")
for failure in FAILED:
    print("  FAILED:", failure)
print("ALL OK" if not FAILED else f"{len(FAILED)} FAILURE(S)")
sys.exit(1 if FAILED else 0)
