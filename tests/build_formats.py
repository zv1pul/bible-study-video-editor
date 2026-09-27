"""
Build the awkward source formats the editor must cope with.

    python tests/build_formats.py OUT_DIR

Portrait phone video, 4K at 24fps, a low 480p 15fps file, uncompressed
audio in a .mov, VP9 in .webm — and a recording at 30.01 fps, the slightly
off rate real phones produce, which is what exposes timing mismatches.
"""
import os
import subprocess
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "formats"
os.makedirs(OUT, exist_ok=True)
HERE = os.path.dirname(os.path.abspath(__file__))
VOICE = os.path.join(OUT, "_speech.wav")

subprocess.run(["say", "-v", "Alex", "-r", "165", "-o", VOICE + ".aiff",
                "Our takeaway is that God keeps every promise. Our first division is the call. "
                "The principle is that faith obeys before it understands. Here is our application "
                "question. Where is God asking you to go? And a second question. What rule have you "
                "kept without remembering the rescue behind it? Let us close in prayer."],
               check=True, capture_output=True)
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", VOICE + ".aiff", "-af", "apad=whole_dur=57",
                "-ar", "48000", "-ac", "2", VOICE], check=True, capture_output=True)

CASES = [
    ("portrait_phone.mp4", ["-s", "1080x1920", "-r", "30"], ["-c:v", "libx264", "-c:a", "aac"]),
    ("uhd_24fps.mp4", ["-s", "3840x2160", "-r", "24"], ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac"]),
    ("low_480p_15fps.mp4", ["-s", "854x480", "-r", "15"], ["-c:v", "libx264", "-c:a", "aac"]),
    ("pcm_audio.mov", ["-s", "1280x720", "-r", "30"], ["-c:v", "libx264", "-c:a", "pcm_s16le"]),
    ("vp9.webm", ["-s", "1280x720", "-r", "30"], ["-c:v", "libvpx-vp9", "-b:v", "500k", "-c:a", "libopus"]),
    # 30.01 fps: what a phone actually records, and the case that catches
    # pieces being joined with mismatched timescales.
    ("phone_30_01fps.mp4", ["-s", "1920x1080", "-r", "30000/999"], ["-c:v", "libx264", "-c:a", "aac"]),
]

for name, source_args, encode_args in CASES:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c=0x334455:s={source_args[1]}:r={source_args[3]}",
         "-i", VOICE, "-shortest", *encode_args, "-preset", "veryfast",
         os.path.join(OUT, name)],
        check=True, capture_output=True)
    print("built", name)

os.remove(VOICE + ".aiff")
os.remove(VOICE)
