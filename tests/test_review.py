"""
The check of a finished video, tried on a lesson made to go wrong.

A short spoken lesson with a group discussion: the teacher announces three
minutes "amongst each other", a second voice talks over the break, and the
teacher calls the room back. It is rendered three ways —

  * as the app plans it: the check must find nothing wrong;
  * coming back while the group is still talking (the mistake Lesson 7
    nearly shipped with): the check must say so and offer the right fix,
    and the fix must make the next render pass;
  * with a cut that swallows a principle: the check must catch it.

    python tests/test_review.py
"""
import copy
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("BSVE_HOME", tempfile.mkdtemp(prefix="bsve_review_home_"))

import editor  # noqa: E402
import reviewer  # noqa: E402
import transcriber  # noqa: E402
from transcriber import ffmpeg_exe  # noqa: E402

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name if condition else f"{name}  {detail}")


LINES = [   # (who, text, silence after)
    ("teacher", "Good evening brothers, welcome to tonight's lesson on the book of Exodus.", 1.0),
    ("teacher", "Our first principle tonight is that God fights for his people.", 1.0),
    ("teacher", "Where do you need to stand still and let God fight for you? "
                "Take three minutes to discuss this amongst each other.", 2.0),
    ("group", "Yeah I think for me it is work, you know, I always try to fix everything "
              "myself and I never really wait on anyone.", 1.0),
    ("group", "Same here, my family keeps telling me to slow down and I just keep going.", 2.0),
    ("teacher", "All right brothers. Let's move on to our second division. Holiness flows from "
                "the rescue, and the law was given to people who had already been saved.", 1.0),
    ("teacher", "Let's close in prayer. Thank you Lord for this time together. Amen.", 1.0),
]


def speak(text: str, voice: str, path: str) -> bool:
    if sys.platform == "darwin" and shutil.which("say"):
        aiff = path + ".aiff"
        args = ["say", "-o", aiff] + (["-v", "Samantha"] if voice == "group" else []) + [text]
        if subprocess.run(args, capture_output=True).returncode != 0:
            subprocess.run(["say", "-o", aiff, text], check=True)
        subprocess.run([ffmpeg_exe(), "-y", "-v", "error", "-i", aiff, "-ar", "16000",
                        "-ac", "1", path], check=True)
        return True
    if sys.platform.startswith("win"):
        rate = "2" if voice == "group" else "0"
        script = ("Add-Type -AssemblyName System.Speech;"
                  "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                  f"$s.Rate={rate};$s.SetOutputToWaveFile('{path}');"
                  f"$s.Speak('{text.replace(chr(39), '')}');$s.Dispose()")
        return subprocess.run(["powershell", "-NoProfile", "-Command", script],
                              capture_output=True).returncode == 0 and os.path.exists(path)
    return False


def build(folder: str):
    """The recording, and when each line starts and ends in it."""
    pieces, times, at = [], [], 0.0
    for index, (who, text, pause) in enumerate(LINES):
        spoken = os.path.join(folder, f"l{index}.wav")
        if not speak(text, who, spoken):
            return None, None
        length = transcriber.probe_duration(spoken)
        quiet = os.path.join(folder, f"q{index}.wav")
        subprocess.run([ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i",
                        "anullsrc=r=16000:cl=mono", "-t", str(pause), quiet], check=True)
        times.append((at, at + length))
        at += length + pause
        pieces += [spoken, quiet]
    listing = os.path.join(folder, "a.txt")
    with open(listing, "w", encoding="utf-8") as handle:
        handle.writelines(f"file '{os.path.basename(p)}'\n" for p in pieces)
    audio = os.path.join(folder, "a.wav")
    subprocess.run([ffmpeg_exe(), "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", listing, "-c", "copy", audio], check=True)
    video = os.path.join(folder, "lesson.mp4")
    subprocess.run([ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=#335577:s=640x360:r=30", "-i", audio, "-shortest",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", video], check=True)
    return video, times


work = tempfile.mkdtemp(prefix="bsve_review_")
try:
    video, times = build(work)
    if not video:
        print("SKIPPED: this computer has no built-in voice to make a test lesson")
        print("ALL OK")
        sys.exit(0)
    duration = transcriber.probe_duration(video)
    segments = transcriber.transcribe_video(video, engine="local", model_size="base", language="en")
    principle_start, principle_end = times[1]
    question_end = times[2][1]
    call_back = times[5][0]

    def cues_for(cut_end):
        return [
            editor.Cue(text="God fights for His people.", start=principle_start, label="Principle",
                       duration=principle_end - principle_start + 0.5, kind="principle"),
            editor.Cue(text="Where do you need to stand still and let God fight?",
                       start=times[2][0], label="Application",
                       duration=question_end - times[2][0], has_timer=True, timer_duration=10.0,
                       pause_at=question_end + 0.3, cut_start=question_end + 0.3,
                       cut_end=cut_end, kind="application"),
        ]

    rows = [{"Show": True, "Point": "God fights for His people."},
            {"Show": True, "Point": "Where do you need to stand still and let God fight?"}]

    def render_and_review(name, cues):
        out = os.path.join(work, f"{name}.mp4")
        editor.render_video(video, out, cues=cues, cue_duration=4.0, quality="small", threads=2,
                            intro_image_path=os.path.join(ROOT, "assets", "intro.png"),
                            outro_image_path=os.path.join(ROOT, "assets", "outro.png"),
                            bookend_duration=2.0)
        plan = reviewer.Plan(output_path=out, cues=cues, duration=duration,
                             intro_path=os.path.join(ROOT, "assets", "intro.png"),
                             outro_path=os.path.join(ROOT, "assets", "outro.png"),
                             bookend_seconds=2.0, rows=rows)
        found = reviewer.review(plan, segments)
        print(f"\n{name}: {reviewer.summary(found)}")
        for f in found:
            print(f"   {f.level:8} {f.title} | {f.detail[:110]}" + (f"  FIX {f.fix}" if f.fix else ""))
        return found

    # 1. As planned.
    good = render_and_review("as-planned", cues_for(call_back - 0.2))
    check("a video made as planned has no problems",
          not [f for f in good if f.level == reviewer.PROBLEM])
    check("…the countdown is followed by the teacher",
          any(f.level == reviewer.OK and "question, then the teacher" in f.title for f in good),
          [f.title for f in good])
    check("…the length, slides and cards check out",
          any("in step" in f.title for f in good) and any("opening slide is there" in f.title
                                                             for f in good))

    # 2. Comes back while the group is still talking.
    early = render_and_review("too-early", cues_for(times[3][0] - 0.2))
    flagged = [f for f in early if f.fix and f.fix.get("op") == "resume"]
    check("coming back during the group discussion is caught", bool(flagged),
          [f.title for f in early])
    target = flagged[0].fix["at"] if flagged else 0
    check("…and the fix offered comes back on the teacher's call back",
          abs(target - call_back) < 2.5, f"offered {target}, call back at {call_back:.1f}")
    if flagged:
        fixed = render_and_review("fixed", cues_for(target))
        check("…and after the fix, the next render passes",
              not [f for f in fixed if f.fix and f.fix.get("op") == "resume"])

    # 3. A cut that swallows the principle.
    swallow = cues_for(call_back - 0.2)
    swallow = copy.deepcopy(swallow)
    swallow[0].start = 0.0
    swallow[0].duration = 1.0
    swallow[1].pause_at = swallow[1].cut_start = times[0][1] + 0.3
    swallow[1].start = 1.2
    swallow[1].duration = 1.0
    swallowed = render_and_review("swallowed", swallow)
    check("a cut that removes a principle is caught",
          any(f.level == reviewer.PROBLEM and "inside a part that is cut" in f.title
              for f in swallowed), [f.title for f in swallowed])
finally:
    shutil.rmtree(work, ignore_errors=True)

print(f"\n{len(PASSED)} passed")
for line in PASSED:
    print("  ok   ", line)
for failure in FAILED:
    print("  FAIL ", failure)
    if os.environ.get("GITHUB_ACTIONS"):
        print("::error title=Checking a finished video::" + failure.replace("\n", "%0A"))
print("ALL OK" if not FAILED else f"{len(FAILED)} FAILURE(S)")
sys.exit(1 if FAILED else 0)
