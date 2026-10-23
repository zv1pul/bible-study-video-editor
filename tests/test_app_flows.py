"""
The app page, used the way a person uses it — on Mac and on Windows.

Drives the real page in Streamlit's test harness: a fresh computer, a short
spoken lesson, the outline typed in, Analyse, Render, Start the next video.
Then the mistakes people make — a wrong file location, a link that isn't
one, damaged settings — each of which must give a message, never a crash.

No AI is used (BSVE_NO_AI), so it never spends the group's free allowance,
and the finished video goes to a temporary folder, not anybody's Movies.

    python tests/test_app_flows.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from transcriber import ffmpeg_exe  # noqa: E402

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name if condition else f"{name}  {detail}")


SCRIPT = [
    ("Good evening brothers. My name is John and I am glad you are here tonight.", 1),
    ("Today's takeaway is that grace comes before obedience.", 2),
    ("Our first division is the rescue. God rescued his people before he gave them any law.", 1),
    ("Here is our first principle. God fights for his people.", 1),
    ("Our first question. Where do you need to stand still and let God fight? "
     "Take three minutes to discuss this amongst each other.", 20),
    ("All right brothers. Let's close in a word of prayer. Thank you Lord. Amen.", 1),
]


def speak(text: str, path: str) -> bool:
    """A spoken sentence as a WAV file, with the computer's own voice."""
    if sys.platform == "darwin" and shutil.which("say"):
        aiff = path + ".aiff"
        subprocess.run(["say", "-o", aiff, text], check=True)
        subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", aiff,
                        "-ar", "16000", "-ac", "1", path], check=True)
        return True
    if sys.platform.startswith("win"):
        script = ("Add-Type -AssemblyName System.Speech;"
                  "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                  f"$s.SetOutputToWaveFile('{path}');"
                  f"$s.Speak('{text.replace(chr(39), '')}');$s.Dispose()")
        result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                capture_output=True)
        return result.returncode == 0 and os.path.exists(path)
    return False


def make_lesson(folder: str) -> str:
    """A short lesson recording: speech, a discussion pause, the closing."""
    pieces = []
    for index, (text, pause) in enumerate(SCRIPT):
        spoken = os.path.join(folder, f"line{index}.wav")
        if not speak(text, spoken):
            return ""
        quiet = os.path.join(folder, f"quiet{index}.wav")
        subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", "anullsrc=r=16000:cl=mono", "-t", str(pause), quiet], check=True)
        pieces += [spoken, quiet]
    listing = os.path.join(folder, "audio.txt")
    with open(listing, "w", encoding="utf-8") as handle:
        handle.writelines(f"file '{os.path.basename(p)}'\n" for p in pieces)
    audio = os.path.join(folder, "audio.wav")
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", listing, "-c", "copy", audio], check=True)
    video = os.path.join(folder, "Test Lesson 1.mp4")
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=#335577:s=640x360:r=30", "-i", audio,
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    video], check=True)
    return video


def page(home: str, output: str, ai: bool = False):
    from streamlit.testing.v1 import AppTest

    os.environ.update({
        "BSVE_HOME": home, "BSVE_OUTPUT_DIR": output, "BSVE_NO_AI": "1",
        "BSVE_SKIP_AUTO_SELFCHECK": "1",
    })
    if ai:
        os.environ.pop("BSVE_NO_AI")
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=900)
    return at.run()


def texts(at) -> str:
    parts = []
    for kind in ("markdown", "caption", "error", "warning", "success", "info", "subheader"):
        for item in getattr(at, kind, []):
            parts.append(str(getattr(item, "value", "") or getattr(item, "body", "")))
    return "\n".join(parts)


def no_crash(name, at):
    check(name, not at.exception,
          at.exception[0].message[:300] if at.exception else "")


work = tempfile.mkdtemp(prefix="bsve_flows_")
try:
    home, output = os.path.join(work, "home"), os.path.join(work, "Finished videos")
    os.makedirs(home)

    # 1. A brand-new computer: no settings, nothing to set up.
    at = page(home, output)
    no_crash("a brand-new copy opens without an error", at)
    check("…with the usual settings: 30-second discussions",
          at.slider(key="pref_discussion_seconds").value == 30)
    check("…balanced quality, trims on, the Romans slides at both ends",
          at.selectbox(key="pref_quality").value == "balanced"
          and at.checkbox(key="pref_trim_ends").value
          and not any(c.value.startswith(("No opening slide", "No closing slide"))
                      for c in at.caption))

    # Slides: remove one, the other stays; bring it back.
    at.button(key="outro_remove").click().run()
    no_crash("removing the closing slide works", at)
    check("…only the closing slide goes; the opening one stays",
          any("No closing slide" in c.value for c in at.caption)
          and not any("No opening slide" in c.value for c in at.caption))
    at = page(home, output)
    check("…and stays removed the next time the app opens",
          any("No closing slide" in c.value for c in at.caption))
    at.button(key="outro_reset").click().run()
    check("…and the original can be brought back",
          not any("No closing slide" in c.value for c in at.caption))

    # Any setting changed stays changed the next time the app opens.
    at.slider(key="pref_discussion_seconds").set_value(45).run()
    at.selectbox(key="pref_provider").set_value("groq").run()
    at.radio(key="pref_render_engine").set_value("moviepy").run()
    at = page(home, output)
    check("changed settings are still changed after the app is reopened",
          at.slider(key="pref_discussion_seconds").value == 45
          and at.selectbox(key="pref_provider").value == "groq"
          and at.radio(key="pref_render_engine").value == "moviepy")
    at.slider(key="pref_discussion_seconds").set_value(30).run()
    at.selectbox(key="pref_provider").set_value("gemini").run()
    at.radio(key="pref_render_engine").set_value("auto").run()

    # A key of one's own replaces the shared ones, and can be undone.
    import control as _control
    _control._STATE_DIR = os.path.join(home, ".state")
    _control.save_key("GEMINI_API_KEY", "AIza-a-key-of-my-own")
    at = page(home, output, ai=True)
    # A developer's own copy has keys of its own in .streamlit/secrets.toml,
    # which rightly come first; a download never does.
    developer = os.path.exists(os.path.join(ROOT, ".streamlit", "secrets.toml"))
    check("a key saved on this computer is the one used",
          any("Using your own key" in c.value
              or (developer and "set by whoever runs this copy" in c.value)
              for c in at.caption))
    labels = [b.label for b in at.button]
    at.button[labels.index("Use shared keys")].click().run()
    check("…and Use shared keys goes back to the group's",
          _control.saved_keys() == {})
    at = page(home, output)
    check("…the group's teachers ready to pick",
          any("Raneil Ensomo" in o for o in at.selectbox(key="speaker_pick").options))

    # 2. Mistakes give a message, never a crash.
    at.text_input(key="local_path_box").input(os.path.join(work, "no such file.mp4")).run()
    no_crash("a wrong file location doesn't crash", at)
    check("…and says so", "No file at that location" in texts(at))
    at.text_input(key="local_path_box").input("").run()

    at.radio[0].set_value("link").run()
    at.text_input(key="link_box").input("not a link at all").run()
    at.button[[b.label for b in at.button].index("Fetch this recording")].click().run()
    no_crash("a link that isn't one doesn't crash", at)
    check("…and says what's wrong", any(e.value for e in at.error), texts(at)[-300:])
    at.radio[0].set_value("path").run()

    # 3. Preset speakers: save one, edit after picking, update, remove.
    at.text_input(key="speaker_name_box").input("Pastor Test").run()
    at.text_input(key="speaker_title_box").input("Guest Teacher").run()
    at.button(key="save_preset").click().run()
    no_crash("saving a new preset speaker works", at)
    check("…and they're in the list, picked",
          at.selectbox(key="speaker_pick").value == "Pastor Test"
          and any(o.startswith("Pastor Test") for o in at.selectbox(key="speaker_pick").options))

    at.selectbox(key="speaker_pick").set_value("Eric Roachford").run()
    check("picking a preset fills both boxes",
          at.text_input(key="speaker_name_box").value == "Eric Roachford")
    at.text_input(key="speaker_title_box").input("Guest Teacher, Romans").run()
    check("the boxes can still be changed after picking a preset",
          at.text_input(key="speaker_title_box").value == "Guest Teacher, Romans"
          and at.text_input(key="speaker_name_box").value == "Eric Roachford")
    at.button(key="update_preset").click().run()
    no_crash("updating a preset works", at)
    check("…and the preset keeps the new title",
          any(o == "Eric Roachford — Guest Teacher, Romans"
              for o in at.selectbox(key="speaker_pick").options),
          at.selectbox(key="speaker_pick").options)

    at.selectbox(key="speaker_pick").set_value("Raneil Ensomo").run()
    at.text_input(key="speaker_name_box").input("John Visitor").run()
    labels = [b.label for b in at.button]
    check("typing a different name over a preset never replaces them by accident",
          "⭐ Save as a new preset" in labels and "💾 Replace Raneil" in labels, labels)
    at.button(key="save_preset").click().run()
    check("…saving keeps both people",
          sum(o.startswith(("Raneil Ensomo", "John Visitor"))
              for o in at.selectbox(key="speaker_pick").options) == 2)
    at.button(key="remove_preset").click().run()

    at.selectbox(key="speaker_pick").set_value("Pastor Test").run()
    at.button(key="remove_preset").click().run()
    no_crash("removing a preset works", at)
    check("…and they're gone, with the boxes cleared",
          not any(o.startswith("Pastor Test") for o in at.selectbox(key="speaker_pick").options)
          and at.text_input(key="speaker_name_box").value == "")

    # 4. Paste a whole outline, as it arrives by email.
    with open(os.path.join(HERE, "fixtures", "outlines", "lesson7_righteousness.txt"),
              encoding="utf-8") as handle:
        at.text_area(key="paste_box").input(handle.read()).run()
    labels = [b.label for b in at.button]
    at.button[labels.index("Fill in the boxes below")].click().run()
    no_crash("pasting a whole outline works", at)
    check("…filling the title, takeaway, divisions, principles and questions",
          at.text_input(key="lesson_title_box").value == "Romans 3:21-26 - Lesson 5"
          and at.text_area(key="takeaway_box").value.startswith("All people can access")
          and at.text_area(key="principles_2").value.startswith("God’s love provided")
          and at.text_area(key="applications_2").value.startswith("What upside-down"))
    check("…7 cards in all", any("7 cards in total" in c.value for c in at.caption),
          [c.value for c in at.caption if "cards" in c.value])

    # 5. A whole lesson, start to finish.
    lesson_dir = os.path.join(work, "lesson")
    os.makedirs(lesson_dir)
    video = make_lesson(lesson_dir)
    if not video:
        print("SKIPPED the full lesson: this computer has no built-in voice to make one")
    else:
        for key in ("takeaway_box", "divisions_box", "lesson_title_box"):
            (at.text_input if key == "lesson_title_box" else at.text_area)(key=key).input("").run()
        at.selectbox(key="speaker_pick").set_value("Raneil Ensomo").run()
        check("picking a past speaker fills in the name and title",
              at.text_input(key="speaker_name_box").value == "Raneil Ensomo"
              and "Teaching Leader" in at.text_input(key="speaker_title_box").value)
        at.text_input(key="local_path_box").input(video).run()
        at.text_area(key="takeaway_box").input("Grace comes before obedience.").run()
        at.text_area(key="divisions_box").input("I. The Rescue").run()
        at.text_area(key="principles_1").input("God fights for His people.").run()
        at.text_area(key="applications_1").input(
            "Where do you need to stand still and let God fight?").run()
        at.text_input(key="lesson_title_box").input("Exodus 14: Lesson 1").run()
        no_crash("the outline goes in without an error", at)

        labels = [b.label for b in at.button]
        at.button[labels.index("🔍 Analyse recording")].click().run()
        no_crash("Analyse runs without an error", at)
        check("…and shows the timings to check", "4 · Check the timings" in texts(at),
              texts(at)[-400:])

        labels = [b.label for b in at.button]
        if "🎬 Render final video" in labels:
            at.button[labels.index("🎬 Render final video")].click().run()
            no_crash("Render runs without an error", at)
            made = [f for f in os.listdir(output)] if os.path.isdir(output) else []
            check("…and the finished video is saved", any(f.endswith(".mp4") for f in made),
                  texts(at)[-400:])
            check("…named after the lesson title (a colon can't be in a file name)",
                  "Exodus 14- Lesson 1 - captioned.mp4" in made, made)

            labels = [b.label for b in at.button]
            at.button[labels.index("➕  Start the next video")].click().run()
            no_crash("Start the next video runs without an error", at)
            check("…and the next lesson starts on a clean page",
                  at.text_area(key="takeaway_box").value == ""
                  and at.text_input(key="local_path_box").value == "")
            check("…keeping the speakers, now with the one just used first",
                  at.selectbox(key="speaker_pick").options[1].startswith("Raneil Ensomo"))
        else:
            FAILED.append("the Render button never appeared")

    # 6. Damaged or out-of-date settings never stop the app opening.
    state = os.path.join(home, ".state")
    with open(os.path.join(state, "settings.json"), "w", encoding="utf-8") as handle:
        handle.write("{ this is not valid")
    no_crash("damaged settings don't stop it opening", page(home, output))
    with open(os.path.join(state, "settings.json"), "w", encoding="utf-8") as handle:
        json.dump({"prefs": {"quality": "enormous", "discussion_seconds": "lots",
                             "threads": 999}, "speakers": [{"oops": 1}, "nonsense"]}, handle)
    at = page(home, output)
    no_crash("settings from an older or newer version don't stop it opening", at)
    check("…and fall back to the usual ones",
          at.selectbox(key="pref_quality").value == "balanced"
          and at.slider(key="pref_discussion_seconds").value == 30)
finally:
    shutil.rmtree(work, ignore_errors=True)

print(f"{len(PASSED)} passed")
for line in PASSED:
    print("  ok   ", line)
for failure in FAILED:
    print("  FAIL ", failure)
    if os.environ.get("GITHUB_ACTIONS"):
        print("::error title=Using the app::" + failure.replace("\n", "%0A"))
print("ALL OK" if not FAILED else f"{len(FAILED)} FAILURE(S)")
sys.exit(1 if FAILED else 0)
