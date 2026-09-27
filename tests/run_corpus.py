"""
Run every synthetic lesson through the whole pipeline and check the result.

    python tests/run_corpus.py CORPUS_DIR [gemini|groq|offline]

For each lesson: transcribe, match (AI or the offline matcher), verify, lay
out, render, and check that no cards overlap and the finished length is
exactly source − cuts + discussion blocks + intro + outro.
"""
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import editor  # noqa: E402
import matcher  # noqa: E402
import transcriber as T  # noqa: E402
import verifier  # noqa: E402

CORPUS = sys.argv[1] if len(sys.argv) > 1 else "corpus"
PROVIDER = sys.argv[2] if len(sys.argv) > 2 else "offline"
ROOT = os.path.dirname(HERE)


def secret(name):
    try:
        text = open(os.path.join(ROOT, ".streamlit", "secrets.toml")).read()
        return text.split(f'{name} = "')[1].split('"')[0]
    except Exception:
        return ""


KEYS = {"gemini": secret("GEMINI_API_KEY"), "groq": secret("GROQ_API_KEY")}
outlines = json.load(open(os.path.join(CORPUS, "outlines.json")))
failures = 0

for name, outline in outlines.items():
    src = os.path.join(CORPUS, f"{name}.mp4")
    out = os.path.join(CORPUS, f"{name}_out.mp4")
    t0 = time.time()
    try:
        segs = T.transcribe_video(src, "base")
        dur = T.probe_duration(src)
        sil = T.silences_from_transcript(segs, matcher.APPLICATION_GAP_MIN)
        pts = matcher.build_lesson_points(outline)
        els, notes, used = matcher.match_lesson_points(
            pts, segs, provider=PROVIDER if PROVIDER != "offline" else "gemini",
            api_key=KEYS.get(PROVIDER, ""), duration=dur, silences=sil,
            speaker="Test Speaker", speaker_title="Teaching Leader",
            other_keys={k: v for k, v in KEYS.items() if k != PROVIDER} if PROVIDER != "offline" else None,
        )
        vs = verifier.lay_out(
            verifier.verify_matches(els, pts, segs, dur), dur,
            segments=segs, silences=sil,
            pause_seconds=matcher.APPLICATION_PAUSE_SECONDS,
        )
        keep = [v.match for v in vs if v.verdict != verifier.REJECTED]
        cues = editor.cues_from_matches(keep)
        editor.render_video(
            src, out, speaker_name="Test Speaker", speaker_title="Teaching Leader",
            cues=cues, lower_third_start=3.0, lower_third_duration=25.0,
            logo_path=os.path.join(ROOT, "assets", "logo.png"),
            intro_image_path=os.path.join(ROOT, "assets", "intro.png"),
            outro_image_path=os.path.join(ROOT, "assets", "outro.png"),
            quality="small", engine="ffmpeg", threads=4,
        )
        got = editor.video_info(out)["duration"]
        apps = [e for e in keep if e.type == "application" and e.has_timer]
        removed = sum(e.cut_end - e.cut_start for e in apps if e.cut_end > e.cut_start)
        expected = dur - removed + sum(e.timer_duration for e in apps) + 10
        cards = sorted((e for e in keep if e.type != "lower_third"), key=lambda e: e.start_time)
        overlaps = []
        for a, b in zip(cards, cards[1:]):
            limit = a.pause_at if (a.type == "application" and a.has_timer) else a.end_time
            if b.start_time < limit - 0.01:
                overlaps.append((a.type, b.type, round(limit - b.start_time, 1)))
        summ = verifier.summarise(vs)
        ok = not overlaps and abs(got - expected) < 1.5
        failures += not ok
        cuts = ", ".join(f"{T.format_timestamp(e.cut_start)}->{T.format_timestamp(e.cut_end)}"
                         for e in apps if e.cut_end > e.cut_start) or "none"
        print(f"  {'OK ' if ok else 'BAD'} {name:<24} v{summ['verified']}/r{summ['review']}/x{summ['rejected']} "
              f"of {len(pts)}  {dur:.1f}s -> {got:.1f}s (exp {expected:.1f})  cuts: {cuts}  "
              f"{'OVERLAP ' + str(overlaps) if overlaps else ''}{round(time.time() - t0)}s")
        os.remove(out)
    except Exception as exc:
        failures += 1
        print(f"  CRASH {name}: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-600:]}")

print("ALL OK" if not failures else f"{failures} FAILURE(S)")
sys.exit(1 if failures else 0)
