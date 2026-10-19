"""
selfcheck.py
============
"Check this computer": confirms that this copy works on this machine before
anyone trusts it with a real lesson.

    python selfcheck.py            # from a terminal; exit code 0 = all good

Runs in about half a minute and needs no API key and no recording:

  1. The unit checks — every case a real lesson once got wrong
     (tests/test_units.py).
  2. A real render of a short made-up clip with a name card, a point card,
     a countdown, the intro and outro, and both ends trimmed — then checks
     its length and that the picture and the sound match.
  3. The speech engine, the AI key (if one is set), the artwork, and free
     disk space.

The app runs it automatically the first time each version starts, and from
the "Check this computer" button.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Callable, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
Result = Tuple[str, bool, str]          # (what, passed, detail)


def _unit_checks() -> Result:
    script = os.path.join(HERE, "tests", "test_units.py")
    if not os.path.exists(script):
        return "Accuracy checks", False, "The checks are missing from this copy."
    try:
        run = subprocess.run([sys.executable, script], capture_output=True, text=True,
                             timeout=300, cwd=HERE)
    except Exception as exc:
        return "Accuracy checks", False, f"Could not run: {exc}"
    lines = [line for line in (run.stdout or "").splitlines() if line.strip()]
    if run.returncode == 0 and lines and lines[-1].strip() == "ALL OK":
        passed = next((line for line in lines if line.endswith("passed")), "")
        return "Accuracy checks", True, f"{passed} — every case a real lesson once got wrong."
    failures = [line.strip() for line in lines if "FAILED" in line][:3]
    detail = "; ".join(failures) or (run.stderr or "").strip()[-300:] or "failed"
    return "Accuracy checks", False, detail


_APP_TEST = """
import sys
from streamlit.testing.v1 import AppTest
at = AppTest.from_file("app.py", default_timeout=90).run()
if at.exception:
    print("EXCEPTION", at.exception[0].message[:300]); sys.exit(1)
if not any("Bible Study Video Editor" in t.value for t in at.title):
    print("NO TITLE"); sys.exit(1)
if not any("Analyse recording" in b.label for b in at.button):
    print("NO FORM"); sys.exit(1)
print("PAGE OK")
"""


def _app_starts() -> Result:
    """Load the app's page as a visitor would, in Streamlit's test harness."""
    work = tempfile.mkdtemp(prefix="bsve_apptest_")
    env = dict(os.environ)
    env.update({
        "BSVE_HOME": work,
        "BSVE_SKIP_AUTO_SELFCHECK": "1",          # not the check within the check
        "GEMINI_API_KEY": env.get("GEMINI_API_KEY") or "selfcheck-placeholder-key",
    })
    try:
        run = subprocess.run([sys.executable, "-c", _APP_TEST], cwd=HERE, env=env,
                             capture_output=True, text=True, timeout=240)
    except Exception as exc:
        return "App starts", False, f"Could not load the page: {exc}"
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if run.returncode == 0 and "PAGE OK" in run.stdout:
        return "App starts", True, "The page loads without errors."
    detail = (run.stdout.strip().splitlines() or [""])[-1] or (run.stderr or "").strip()[-300:]
    return "App starts", False, detail


def _test_render() -> Result:
    import editor
    import transcriber

    work = tempfile.mkdtemp(prefix="bsve_selfcheck_")
    try:
        source = os.path.join(work, "clip.mp4")
        made = subprocess.run([
            transcriber.ffmpeg_exe(), "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=0x334455:s=1280x720:r=30",
            "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
            "-t", "16", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
            "-pix_fmt", "yuv420p", source,
        ], capture_output=True, text=True, timeout=120)
        if made.returncode != 0:
            return "Video engine", False, f"Could not make a test clip: {made.stderr[-200:]}"

        cues = [
            editor.Cue(text="Faith obeys before it understands.", start=3.0,
                       label="Principle", duration=4.0, kind="principle"),
            editor.Cue(text="Where is God asking you to go?", start=8.0,
                       label="Application", duration=3.0, has_timer=True,
                       timer_duration=3.0, pause_at=11.0, kind="application"),
        ]
        assets = os.path.join(HERE, "assets")
        output = os.path.join(work, "out.mp4")
        editor.render_video(
            source, output, speaker_name="Test Speaker", speaker_title="Teaching Leader",
            cues=cues, lower_third_start=2.0, lower_third_duration=4.0,
            logo_path=os.path.join(assets, "logo.png"),
            intro_image_path=os.path.join(assets, "intro.png"),
            outro_image_path=os.path.join(assets, "outro.png"),
            bookend_duration=2.0, quality="small", engine="ffmpeg", threads=2,
            trim_start=1.0, trim_end=15.0,
        )
        tracks = editor.track_lengths(output)
        expected = 2.0 + 14.0 + 3.0 + 2.0          # intro, kept clip, countdown, outro
        drift = abs(tracks["video"] - tracks["audio"])
        if abs(tracks["video"] - expected) > 0.6 or drift > 0.3:
            return ("Video engine", False,
                    f"Test video came out {tracks['video']:.1f}s (expected {expected:.0f}s), "
                    f"picture and sound {drift:.2f}s apart.")
        return "Video engine", True, "Rendered a test video with cards, a countdown and trims."
    except Exception as exc:
        return "Video engine", False, f"{type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _speech_engine() -> Result:
    try:
        import faster_whisper
    except Exception:
        return ("Speech engine", True,
                "Local speech engine not installed — transcription will use the "
                "hosted service (needs a Groq key).")

    # Read the sound out of a real file the way transcription does. This is
    # the step a mismatched audio library breaks (see constraints.txt), and
    # it needs no speech model, so it runs anywhere in a second.
    import transcriber

    work = tempfile.mkdtemp(prefix="bsve_speech_")
    try:
        clip = os.path.join(work, "tone.mp4")
        made = subprocess.run([
            transcriber.ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi",
            "-i", "sine=frequency=330:sample_rate=48000", "-t", "2", "-c:a", "aac", clip,
        ], capture_output=True, text=True, timeout=60)
        if made.returncode != 0:
            return "Speech engine", False, "Could not make a test sound."
        samples = faster_whisper.decode_audio(clip)
        if len(samples) < 16000:
            return "Speech engine", False, "Read the test sound back too short."
    except Exception as exc:
        return "Speech engine", False, f"Can't read sound for transcription: {exc}"
    finally:
        shutil.rmtree(work, ignore_errors=True)

    cache = os.path.expanduser("~/.cache/huggingface/hub")
    ready = os.path.isdir(cache) and any("whisper" in n.lower() for n in os.listdir(cache))
    return ("Speech engine", True,
            "Installed." + ("" if ready else
                            " The speech model (about 150 MB) downloads the first "
                            "time a lesson is analysed."))


def _ai_key() -> Result:
    import control

    key = os.environ.get("GEMINI_API_KEY") or control.saved_keys().get("GEMINI_API_KEY") \
        or control.shared_keys().get("GEMINI_API_KEY", "")
    if not key:
        return ("AI key", True,
                "None connected — matching falls back to keywords. Connect a free "
                "key from the setup box or the sidebar.")
    ok, note = control.check_gemini_key(key)
    return "AI key", ok, note


def _artwork() -> Result:
    missing = [name for name in ("assets/intro.png", "assets/outro.png",
                                 "assets/logo.png", "fonts/Inter.ttf")
               if not os.path.exists(os.path.join(HERE, name))]
    if missing:
        return "Artwork and font", False, "Missing: " + ", ".join(missing)
    return "Artwork and font", True, "Intro, outro, logo and font present."


def _disk() -> Result:
    import transcriber

    free = transcriber.free_disk_mb(os.path.expanduser("~"))
    if free is None:
        return "Free space", True, "Could not measure."
    if free < 2000:
        return ("Free space", False,
                f"Only {free / 1024:.1f} GB free; a 30-minute lesson needs about 2 GB "
                "while rendering.")
    return "Free space", True, f"{free / 1024:.0f} GB free."


CHECKS: List[Tuple[str, Callable[[], Result]]] = [
    ("App starts", _app_starts),
    ("Accuracy checks", _unit_checks),
    ("Video engine", _test_render),
    ("Speech engine", _speech_engine),
    ("AI key", _ai_key),
    ("Artwork and font", _artwork),
    ("Free space", _disk),
]


def run(progress_cb: Optional[Callable[[str], None]] = None) -> List[Result]:
    results: List[Result] = []
    for name, check in CHECKS:
        if progress_cb:
            progress_cb(name)
        started = time.time()
        try:
            result = check()
        except Exception as exc:
            result = (name, False, f"{type(exc).__name__}: {exc}")
        results.append(result)
        if os.environ.get("BSVE_SELFCHECK_VERBOSE"):
            print(f"  {name}: {time.time() - started:.1f}s", file=sys.stderr)
    return results


def _annotate(title, message):
    """On GitHub, also report a failure where it can be read without signing in."""
    import os as _os
    if _os.environ.get("GITHUB_ACTIONS"):
        text = str(message).replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::error title={title}::{text}"[:4000], flush=True)


def all_passed(results: List[Result]) -> bool:
    return all(passed for _name, passed, _detail in results)


# Run before an update is allowed in: everything that proves the code
# works, nothing that depends on the network or on this person's settings.
UPDATE_GATE = ("App starts", "Accuracy checks", "Video engine", "Speech engine",
               "Artwork and font")


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    if "--update-gate" in sys.argv:
        CHECKS[:] = [(name, check) for name, check in CHECKS if name in UPDATE_GATE]
    outcome = run(lambda name: print(f"Checking: {name}…", flush=True))
    for name, passed, detail in outcome:
        print(f"{'PASS' if passed else 'FAIL'}  {name} — {detail}")
        if not passed:
            _annotate(f"Self-check: {name}", detail)
    print("ALL OK" if all_passed(outcome) else "PROBLEMS FOUND")
    sys.exit(0 if all_passed(outcome) else 1)
