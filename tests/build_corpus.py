"""
Build the synthetic test lessons: short recordings made with the Mac's own
voices, each shaped to exercise one situation the editor must handle.

    python tests/build_corpus.py OUT_DIR

Writes OUT_DIR/<name>.mp4 for every case plus OUT_DIR/outlines.json, the
outline that goes with each one. Nothing here needs an API key.
"""
import json
import os
import subprocess
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "corpus"
os.makedirs(OUT, exist_ok=True)


def run(*cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def say(text, name, voice="Alex", rate=165, volume=1.0):
    path = os.path.join(OUT, f"_{name}")
    run("say", "-v", voice, "-r", str(rate), "-o", path + ".aiff", text)
    run("ffmpeg", "-v", "error", "-y", "-i", path + ".aiff", "-ar", "16000", "-ac", "1",
        "-af", f"volume={volume}", path + ".wav")
    return path + ".wav"


def silence(seconds, name):
    path = os.path.join(OUT, f"_{name}.wav")
    run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
        "-t", str(seconds), path)
    return path


def chatter(name, seconds=45):
    """Several people talking over each other, quietly — an audience."""
    voices = [
        ("Samantha", 190, "Yeah I think for me it is definitely work, I keep trying to control everything at the office and honestly I never let go of it, my wife says the same thing, we talked about this last week too, it is the same pattern every time."),
        ("Alex", 160, "For me it is my kids, I worry about them all the time, and I feel like I have to fix everything myself, but that is not standing still, that is running ahead, so I guess that is where I need to let God fight, what about you."),
        ("Karen", 200, "Mine is finances honestly, every month I am doing the numbers again and again, and I do not trust that it will be okay, so standing still for me means not checking the account ten times a day, that is a good one."),
    ]
    parts = [say(text, f"{name}_{i}", voice, rate) for i, (voice, rate, text) in enumerate(voices)]
    path = os.path.join(OUT, f"_{name}.wav")
    run("ffmpeg", "-v", "error", "-y", "-i", parts[0], "-i", parts[1], "-i", parts[2],
        "-filter_complex",
        "[0]adelay=0|0,volume=0.25[a];[1]adelay=3000|3000,volume=0.22[b];"
        f"[2]adelay=6500|6500,volume=0.2[c];[a][b][c]amix=inputs=3:duration=longest,apad=whole_dur={seconds}",
        path)
    return path


def lesson(name, parts, outline, size="1280x720", fps=30):
    listing = os.path.join(OUT, f"_{name}.txt")
    with open(listing, "w") as handle:
        for part in parts:
            handle.write(f"file '{os.path.abspath(part)}'\n")
    audio = os.path.join(OUT, f"_{name}_audio.wav")
    run("ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing,
        "-c", "pcm_s16le", audio)
    video = os.path.join(OUT, f"{name}.mp4")
    run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x334455:s={size}:r={fps}",
        "-i", audio, "-shortest", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", video)
    outlines[name] = outline


outlines = {}

lesson("minimal", [
    say("Good morning. Our takeaway today is that God keeps every promise He makes. Let us look at our one division, the faithfulness of God in Genesis fifteen. He promised Abraham a son.", "m1", "Samantha"),
], {"takeaway": "God keeps every promise He makes.",
    "divisions": [{"title": "I. The Faithfulness of God (Genesis 15)", "principles": "", "applications": ""}],
    "scripture": "Genesis 15:4\nHe promised Abraham a son."})

lesson("no_takeaway_slow", [
    say("Let us begin. Our first division is the call of Abram. God speaks and Abram goes. That brings us to our principle. Faith obeys before it understands. Faith obeys before it understands. Here is our application. Where is God asking you to go before you understand why?", "n1", "Karen", 130),
    silence(20, "n_gap"),
    say("Thank you. Our second division is the covenant.", "n2", "Karen", 130),
], {"takeaway": "",
    "divisions": [{"title": "I. The Call of Abram", "principles": "Faith obeys before it understands.", "applications": "Where is God asking you to go before you understand why?"},
                  {"title": "II. The Covenant", "principles": "", "applications": ""}]})

lesson("three_divisions_fast", [
    say("Our takeaway is this. Grace precedes obedience. We have three divisions today. First, the rescue. Second, the law. Third, the tabernacle. Let us begin with our first division, the rescue at the sea. The principle here is that God fights for His people. God fights for His people. Application. Where do you need to stand still and let God fight?", "t1", "Daniel", 190),
    silence(18, "t_g1"),
    say("Our second division, the law at Sinai. The principle is that holiness flows from rescue. Holiness flows from rescue. The application question. What rule have you kept without remembering the rescue behind it?", "t2", "Daniel", 190),
    silence(18, "t_g2"),
    say("And our third division, the tabernacle. The principle is that God desires to dwell among His people. The application. Where have you kept God at a distance this week?", "t3", "Daniel", 190),
    silence(18, "t_g3"),
    say("Let us close in prayer.", "t4", "Daniel", 190),
], {"takeaway": "Grace precedes obedience.",
    "divisions": [{"title": "I. The Rescue", "principles": "God fights for His people.", "applications": "Where do you need to stand still and let God fight?"},
                  {"title": "II. The Law", "principles": "Holiness flows from rescue.", "applications": "What rule have you kept without remembering the rescue behind it?"},
                  {"title": "III. The Tabernacle", "principles": "God desires to dwell among His people.", "applications": "Where have you kept God at a distance this week?"}]})

lesson("two_principles_no_pause", [
    say("Today's takeaway: worship is a response, not a performance. Our division is the psalms of ascent. Our first principle. Worship begins with remembering what God has done. Worship begins with remembering. And a second principle. Worship ends in trust, not in answers. Worship ends in trust. Now our application. What has God done this month that you have not yet thanked Him for? Think about that as we move on. Let me tell you a story about my grandmother.", "d1", "Alex"),
], {"takeaway": "Worship is a response, not a performance.",
    "divisions": [{"title": "I. The Psalms of Ascent", "principles": "Worship begins with remembering what God has done.\nWorship ends in trust, not in answers.", "applications": "What has God done this month that you have not yet thanked Him for?"}]})

lesson("unspoken_point", [
    say("Our takeaway is that God is patient. Our division is the flood. The principle is that judgment is never God's first word. Let us pray.", "u1", "Samantha"),
], {"takeaway": "God is patient.",
    "divisions": [{"title": "I. The Flood", "principles": "Judgment is never God's first word.\nThe ark was a doorway of mercy.", "applications": "Where have you mistaken God's patience for absence?"}],
    "scripture": "Psalm 23:1\nThe Lord is my shepherd, I lack nothing."})

lesson("very_short", [
    say("Takeaway. Trust God. Division one, the wilderness. Application. Where do you need to trust today?", "s1", "Alex", 150),
    silence(15, "s_g"),
], {"takeaway": "Trust God.",
    "divisions": [{"title": "I. The Wilderness", "principles": "", "applications": "Where do you need to trust today?"}]})

# A live audience: the room discusses the question out loud, then the
# teacher calls everyone back. All of the chatter must be cut.
lesson("live_audience", [
    say("Our takeaway is that grace precedes obedience. Our first division is the rescue at the sea. The principle here is that God fights for His people. God fights for His people. Here is our application question. Discuss this in pairs for three minutes. Where do you need to stand still and let God fight? Where do you need to stand still and let God fight? Three minutes.", "l1", "Daniel", 175),
    silence(4, "l_g1"),
    chatter("l_chat"),
    say("Okay. Let us come back together. Our second division, the law at Sinai. The principle is that holiness flows from rescue. Holiness flows from rescue. Let us close in prayer.", "l2", "Daniel", 175),
], {"takeaway": "Grace precedes obedience.",
    "divisions": [{"title": "I. The Rescue", "principles": "God fights for His people.", "applications": "Where do you need to stand still and let God fight?"},
                  {"title": "II. The Law", "principles": "Holiness flows from rescue.", "applications": ""}]})

# A long recorded wait with nobody speaking, longer than any window the
# old detector searched — the teacher lets the tape run for two minutes.
lesson("long_wait", [
    say("Our takeaway is that God provides. Our division is the manna. The principle: God gives enough for today. God gives enough for today. Our application question. Where are you gathering more than today's bread? Take three minutes.", "w1", "Samantha", 170),
    silence(120, "w_gap"),
    say("Welcome back. Our second division, the water from the rock.", "w2", "Samantha", 170),
], {"takeaway": "God provides.",
    "divisions": [{"title": "I. The Manna", "principles": "God gives enough for today.", "applications": "Where are you gathering more than today's bread?"},
                  {"title": "II. The Water from the Rock", "principles": "", "applications": ""}]})

with open(os.path.join(OUT, "outlines.json"), "w") as handle:
    json.dump(outlines, handle, indent=1)
for name in os.listdir(OUT):
    if name.startswith("_"):
        os.remove(os.path.join(OUT, name))
print("built", ", ".join(outlines))
