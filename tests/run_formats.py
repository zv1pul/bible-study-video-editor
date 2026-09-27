"""
Render a mixed set of cards over every awkward source format.

    python tests/run_formats.py FORMATS_DIR

Checks that the finished file is the exact expected length and that the
picture and the sound are the same length — a video track longer than its
audio means the pieces were joined with mismatched timing.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import editor  # noqa: E402
import transcriber as T  # noqa: E402

ROOT = os.path.dirname(HERE)
FORMATS = sys.argv[1] if len(sys.argv) > 1 else "formats"
failures = 0

for name in sorted(n for n in os.listdir(FORMATS) if not n.startswith(("out_", "."))):
    src = os.path.join(FORMATS, name)
    out = os.path.join(FORMATS, f"out_{name.rsplit('.', 1)[0]}.mp4")
    try:
        duration = T.probe_duration(src)
        cues = [
            editor.Cue(text="God keeps every promise.", start=1.0, label="Takeaway",
                       duration=6.0, items=["I. First", "II. Second"], kind="overview"),
            editor.Cue(text="Faith obeys before it understands.", start=8.0,
                       label="Principle #1", duration=5.0, kind="principle"),
            editor.Cue(text="Where is God asking you to go?", start=13.0,
                       label="Application #1", duration=4.0, has_timer=True,
                       timer_duration=12.0, pause_at=17.0, kind="application"),
            editor.Cue(text="“For all have sinned and fall short of the glory of God.”",
                       start=19.0, label="Romans 3:23", duration=5.0, kind="scripture"),
            editor.Cue(text="What rule have you kept?", start=25.0, label="Application #2",
                       duration=4.0, has_timer=True, timer_duration=7.0, pause_at=29.0,
                       kind="application"),
        ]
        cues = [c for c in cues if c.start + 2 < duration]
        started = time.time()
        editor.render_video(
            src, out, speaker_name="Test Speaker", speaker_title="Leader", cues=cues,
            lower_third_start=3.0, lower_third_duration=8.0,
            logo_path=os.path.join(ROOT, "assets", "logo.png"),
            intro_image_path=os.path.join(ROOT, "assets", "intro.png"),
            outro_image_path=os.path.join(ROOT, "assets", "outro.png"),
            quality="small", engine="ffmpeg", threads=4,
        )
        info = editor.video_info(out)
        expected = duration + sum(c.timer_duration for c in cues if c.has_timer) + 10
        lengths = editor.track_lengths(out)
        drift = abs(lengths["video"] - lengths["audio"]) if lengths["audio"] else 0.0
        ok = abs(info["duration"] - expected) < 1.5 and drift < 1.0
        failures += not ok
        print(f"  {'OK ' if ok else 'BAD'} {name:<22} {duration:6.1f}s -> {info['duration']:6.1f}s "
              f"(exp {expected:6.1f}) {info['size'][0]}x{info['size'][1]} "
              f"picture/sound drift {drift:.2f}s  {round(time.time() - started)}s")
        os.remove(out)
    except Exception as exc:
        failures += 1
        print(f"  CRASH {name}: {type(exc).__name__}: {exc}")

print("ALL OK" if not failures else f"{failures} FAILURE(S)")
sys.exit(1 if failures else 0)
