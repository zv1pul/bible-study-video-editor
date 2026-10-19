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

import editor  # noqa: E402
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

def headers(applications):
    pts = matcher.build_lesson_points({"divisions": [
        {"title": "I. One", "principles": "", "applications": applications}]})
    return [(matcher.header_for(p, pts), p.text) for p in pts if p.category == "Application"]


check(
    "a question named in the outline is headed with that name",
    headers("How has God been faithful to you?\n\nWhere are you tempted?\n\n"
            "Final Reflection Question  Considering how seriously God takes our sin, "
            "where have you become too casual about sin in your life?"),
    [("Application #1", "How has God been faithful to you?"),
     ("Application #2", "Where are you tempted?"),
     ("Final Reflection", "Considering how seriously God takes our sin, where have "
                          "you become too casual about sin in your life?")],
)
check(
    "the name may sit on its own line, and countdown notes are dropped",
    headers("FINAL REFLECTION QUESTION\nWhere have you become too casual about sin?\n[30-SECOND COUNTDOWN]"),
    [("Final Reflection", "Where have you become too casual about sin?")],
)
check(
    "\"Application Question:\" is the ordinary kind and keeps its usual header",
    headers("Application Question: Where are you most tempted?"),
    [("Application", "Where are you most tempted?")],
)
check(
    "a plain question is unchanged",
    headers("What is the state of your heart towards God?"),
    [("Application", "What is the state of your heart towards God?")],
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

# A model can place a question at the right moment yet give it a minute-long
# span; the countdown must still follow the last reading, not that span.
span_lesson = speech([
    ("How has God been faithful to you and how does this change the way you respond when you fail him", 100.0, 2.5, 0.95),
    ("How has God been faithful to you and how does this change the way you respond when you fail him", 110.0, 2.5, 0.95),
    ("All right now we have established that God is faithful in spite of our unfaithfulness", 125.0, 2.5, 0.95),
])
span_point = matcher.LessonPoint(id="application_1", category="Application",
                                 text="How has God been faithful to you, and how does this change the way you respond when you fail Him?")
long_span = [matcher.Element(type="application", header="Application", content=span_point.text,
                             start_time=100.0, end_time=160.0, id="application_1",
                             confidence=0.8, evidence="How has God been faithful to you")]
ended = verifier.verify_matches(long_span, [span_point], span_lesson, 200.0)[0].match.end_time
check_that("a question's end comes from its last reading, not the model's span",
           abs(ended - 117.6) < 1.0, f"got {ended:.1f}")

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
# Divisions: announced with their principle, or never announced at all
# --------------------------------------------------------------------------

def division_lesson(spoken_title):
    lines = [
        ("Our takeaway is that grace precedes obedience", 0.0, 2.5, 0.95),
        ("Our first division is the rescue at the sea", 4.0, 2.5, 0.95),
        ("The principle here is that God fights for his people", 30.0, 2.5, 0.95),
        ("Where do you need to stand still and let God fight", 60.0, 2.5, 0.95),
        ("Three minutes in pairs or just reflect if you are watching alone", 66.0, 2.5, 0.95),
    ]
    lines.append((spoken_title, 75.0, 2.5, 0.95))
    lines.append(("Holiness flows from rescue and that is the heart of it all", 100.0, 2.5, 0.95))
    return speech(lines)


div_points = matcher.build_lesson_points({
    "takeaway": "Grace precedes obedience.",
    "divisions": [
        {"title": "I. The Rescue", "principles": "God fights for His people.",
         "applications": "Where do you need to stand still and let God fight?"},
        {"title": "II. The Law", "principles": "Holiness flows from rescue.", "applications": ""},
    ]})


def placed_division_two(spoken_title, model_guess, principle_at):
    lesson_ = division_lesson(spoken_title)
    guesses = {
        "takeaway_1": 0.0, "division_1": 4.0, "principle_1": 30.0,
        "application_1": 60.0, "division_2": model_guess, "principle_2": principle_at,
    }
    els = [matcher.Element(type=p.type, header=p.category, content=p.text,
                           start_time=guesses[p.id], end_time=guesses[p.id] + 6,
                           id=p.id, confidence=0.8, evidence="") for p in div_points]
    out = verifier.verify_matches(els, div_points, lesson_, 130.0)
    return next(v.match.start_time for v in out if v.match.id == "division_2")


check_that("a division announced in the same breath as its principle stays put",
           abs(placed_division_two("Our second division the law and the principle is holiness flows from rescue",
                                   75.0, 75.0) - 75.0) < 1.0)
check_that("a division recited only in the overview moves to its own material",
           70.0 <= placed_division_two("All right now we have established the rescue but what about the law",
                                       5.0, 100.0) <= 80.0)

# --------------------------------------------------------------------------
# The ends of the recording: setup before the teacher, walk-off after
# --------------------------------------------------------------------------

TEACHING = "and so tonight we look at what the passage teaches us about the grace of God"


def recording(opening, closing, length):
    lines = list(opening)
    at = max(start for _t, start, _r, _p in opening) + 8.0
    while at < length - 60:
        lines.append((TEACHING + ".", at, 2.5, 0.93))
        at += 8.0
    lines += closing
    return speech(lines)


# Eric: six seconds of setup, then a sign-off after the amen, then he gets up.
eric = recording(
    [("Good evening brothers. My name is Eric and I am your substitute teaching leader.", 6.0, 2.5, 0.93)],
    [("Thank you Lord for your word. In Jesus' name we pray, amen.", 1780.0, 2.5, 0.93),
     ("Make it a blessed week, brothers.", 1785.0, 3.0, 0.92),
     ("Great day.", 1790.5, 2.0, 0.70)],
    1793.0)
start, end = matcher.find_recording_ends(eric, 1793.0)
check_that("Eric: the setup before he speaks is trimmed", abs(start - 5.6) < 0.3, f"start {start}")
check_that("Eric: the sign-off stays and the walk-off goes",
           1786.5 < end < 1789.5, f"end {end}")

# Nathan: someone off camera asks "You ready?"; chatter follows the prayer.
nathan = recording(
    [("You ready?", 0.0, 2.5, 0.75),
     ("Well, good evening brothers. My name is Nathan and I am thrilled to be here.", 1.9, 2.5, 0.93)],
    [("And we ask these things in Jesus' name.", 1300.0, 2.5, 0.93),
     ("Yeah, we'll do them.", 1303.3, 3.0, 0.80),
     ("Yeah.", 1305.0, 2.0, 0.70),
     ("And you can even put these guys on.", 1305.6, 3.0, 0.85),
     ("Thank you.", 1309.5, 2.0, 0.80)],
    1312.0)
start, end = matcher.find_recording_ends(nathan, 1312.0)
check_that("Nathan: the off-camera \"You ready?\" is trimmed", abs(start - 1.5) < 0.3, f"start {start}")
check_that("Nathan: it ends just after the prayer, not the chatter",
           1303.0 < end < 1305.0, f"end {end}")

# Raneil: starts speaking at once and smiles at the camera after the amen.
raneil = recording(
    [("Good evening, brothers. Last week Paul told the religious Jew something important.", 0.0, 2.5, 0.93)],
    [("In Jesus' name we pray, amen.", 1450.0, 2.5, 0.93)],
    1455.8)
start, end = matcher.find_recording_ends(raneil, 1455.8)
check("Raneil: a clean recording is left alone", (start, end), (0.0, 1455.8))

# Found running the packaged app: the first sentence is often heard less
# surely, and a lesson may close without an "amen". Neither may be cut.
plain = speech([
    ("Our takeaway is that grace precedes obedience.", 0.0, 2.5, 0.79),
    ("Our first division is the rescue at the sea.", 3.0, 2.5, 0.95),
    ("The principle here is that God fights for his people.", 6.0, 2.5, 0.95),
    ("Holiness flows from rescue.", 70.0, 2.5, 0.95),
    ("Let us close in prayer.", 74.0, 2.5, 0.95),
])
check("an unsure first sentence and a closing without \"amen\" are both kept",
      matcher.find_recording_ends(plain, 78.0), (0.0, 78.0))

# The renderer trims by leaving the ends out of the plan.
plan = editor.plan_pieces([], 100.0, 5.0, 90.0)
check("trimmed plan keeps only the middle", [(p["start"], p["end"]) for p in plan], [(5.0, 90.0)])

# --------------------------------------------------------------------------

print(f"{len(PASSED)} passed")
for failure in FAILED:
    print("  FAILED:", failure)
    if os.environ.get("GITHUB_ACTIONS"):
        print("::error title=Accuracy check::" + failure.replace("\n", "%0A"))
print("ALL OK" if not FAILED else f"{len(FAILED)} FAILURE(S)")
sys.exit(1 if FAILED else 0)
