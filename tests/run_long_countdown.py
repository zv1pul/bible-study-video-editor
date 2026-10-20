"""
A three-minute discussion countdown, rendered end to end.

Windows refuses any command longer than 32,767 characters. The countdown was
once drawn with a separate input per second, so a three-minute countdown
built a command too long for Windows and the whole render failed — while
every shorter test passed. This renders one, in a folder whose name has an
apostrophe and accented letters (as a Windows user name can), and checks the
result is exactly as long as it should be, picture and sound alike.

    python tests/run_long_countdown.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import editor  # noqa: E402
from transcriber import ffmpeg_exe  # noqa: E402

COUNTDOWN = 180.0
SOURCE = 12.0

root = tempfile.mkdtemp(prefix="bsve_countdown_")
folder = os.path.join(root, "O'Brien José Nguyễn")
os.makedirs(folder)
try:
    source = os.path.join(folder, "lesson.mp4")
    subprocess.run([
        ffmpeg_exe(), "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=30:duration={SOURCE}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={SOURCE}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", source,
    ], check=True)

    cue = editor.Cue(
        text="Where do you need to stand still and let God fight?", start=2.0,
        label="Application #1", duration=4.0, has_timer=True,
        timer_duration=COUNTDOWN, pause_at=6.0, kind="application",
    )
    output = os.path.join(folder, "finished.mp4")
    editor.render_video(source, output, cues=[cue], cue_duration=4.0,
                        quality="small", threads=2)
    lengths = editor.track_lengths(output)
    want = SOURCE + COUNTDOWN
    problems = []
    if abs(lengths["video"] - want) > 0.5:
        problems.append(f"picture runs {lengths['video']:.2f}s, expected {want:.2f}s")
    if abs(lengths["audio"] - lengths["video"]) > 0.1:
        problems.append(f"sound {lengths['audio']:.2f}s vs picture {lengths['video']:.2f}s")
finally:
    shutil.rmtree(root, ignore_errors=True)

if problems:
    for problem in problems:
        print("FAILED:", problem)
        if os.environ.get("GITHUB_ACTIONS"):
            print("::error title=Three-minute countdown::" + problem)
    sys.exit(1)
print(f"A {COUNTDOWN:.0f}-second countdown rendered at exactly the right length.")
print("ALL OK")
