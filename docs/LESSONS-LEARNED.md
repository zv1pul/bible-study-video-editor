# What each real lesson taught the editor

Every recording we have processed has been different from the last, and each
one found something the tool got wrong. This is the record: what was
different, what it broke, what changed, and the test that now stands guard so
it cannot come back.

Read it before changing `matcher.py`, `verifier.py` or `editor.py` — most of
the rules that look fussy are here because a real lesson needed them.

**Run the guards after any change:**

```
python tests/test_units.py                  # seconds, no API key
python tests/build_corpus.py  /tmp/corpus   # once
python tests/run_corpus.py    /tmp/corpus   # whole pipeline, 8 lessons
python tests/build_formats.py /tmp/formats  # once
python tests/run_formats.py   /tmp/formats  # 6 awkward source formats
```

---

## Lesson 1 — Zechariah 7–8 (Raneil Ensomo, solo recording)

| What was different | What it broke | What changed |
|---|---|---|
| Speaker leaves a real silent wait after each question | — | The wait is cut and replaced by the countdown |
| Fast delivery; points close together | Cards overlapped; slivers of bare video flashed between them | Layout moved to run **after** verification; cards join edge to edge (`CLOSE_GAP_SECONDS`) |
| Long takeaway, several divisions | Takeaway card unreadably short | Takeaway and divisions share one card, held for reading time |
| Low-bitrate source | Lower third looked blurry | Crisp stroke instead of a soft halo |

## Lesson 2 — Romans, grace (Raneil Ensomo, three-minute discussions)

| What was different | What it broke | What changed |
|---|---|---|
| Questions written over several lines, with (a)/(b)/(c) options | Each line became its own card | `matcher.split_items` joins continuation lines and a short "Why?" tail |
| Discussion time of three minutes | Slider stopped at two | Slider goes to five minutes; each question can also have its own time |
| Scripture read aloud during the lesson | Nothing on screen | Scripture box: each passage is found by its own words and timed to the reading (`locate_quote`) |
| Teacher chats for two minutes between takeaway and first division | Takeaway card covered him the whole time | Overview card capped at `OVERVIEW_MAX_SECONDS` |
| Two models disagreed on a division | Wrong placement kept | The placement whose surrounding words match the point wins |

## Lesson 3 — Romans 1 (Raneil Ensomo, long waits)

| What was different | What it broke | What changed |
|---|---|---|
| A 92-second silence that Whisper spanned with a single segment | No gap was visible, so nothing was cut | Gaps are measured **between words**, not between segments (`transcript_words`) |
| (anticipated) a live audience discussing the question aloud | Chatter is speech, so silence-hunting cannot find it | `find_dead_time`: the cut runs to where the **teacher** resumes — sustained, confidently transcribed speech — so an audience is cut like silence. Guarded by the `live_audience` test lesson |
| Scripture references on their own line under a division | Each became a separate division | `matcher.split_divisions` folds a reference line into the division above |

## Lesson 4 — Romans 1:18–32 (Eric Roachford, substitute teacher)

| What was different | What it broke | What changed |
|---|---|---|
| **Recorded on a phone at 30.01 fps** (not exactly 30) | Pieces joined with stretched video timestamps: a 30:54 video reported 39:26 and the picture drifted behind the sound | Every piece is written with one shared media timescale and steady frame timing (`PIECE_TIMING`); the join is checked against the length of its pieces and re-encoded if it disagrees. Guarded by `phone_30_01fps.mp4` and a picture/sound drift check |
| Google's better models were overloaded; the weakest fallback answered | It quoted every right line but converted the timestamps wrong — cards scattered across the wrong half of the lesson | **The words outrank the number**: every place the model's quote is spoken is scored on how much of the point's own wording is said there, and the claimed time has to beat them (`quote_occurrences`, verifier Layer 2b). All ten points landed correctly from the weakest model |
| Two principles introduced with near-identical phrases ("our first/second principle") | Quote matching alone could not tell them apart | The score weighs the point's own words, not just the transition phrase |
| A second model (the weak one) disagreed everywhere | Correct cards demoted to "worth a look" | A disagreement the recording does not support no longer counts against a placement |
| Says "three minutes" but waits only a few seconds | Block landed mid-sentence, before he finished reading | The block goes at the end of that stretch of speech (`speech_end_after`), including a short trailing "Three minutes." |
| Outline pasted with tabs and `Romans 1: 21-27` | Reference not recognised; tabs drawn on the card | Reference matching tolerates spaces around the colon; pasted whitespace is collapsed |
| — | A question moved by verification kept its old discussion block | Blocks are planned in `verifier.lay_out`, **after** verification |

---

## The rules these add up to

1. **The recording is the evidence.** A timestamp from a model is a
   suggestion; words spoken in the audio are fact. Where they disagree,
   the words win.
2. **Measure between words, not between segments.** Segment boundaries are an
   artefact of the speech recogniser; word timings are not.
3. **Cut dead time, never teaching.** When the tool cannot tell, it cuts less.
   Dead time left in is dull; teaching cut out is gone.
4. **Plan in the right order:** match → verify (which may move a card) →
   plan discussion blocks → lay out. Anything that depends on a card's
   position must come after the last thing that can move it.
5. **Never trust a stream copy.** Check the result against what went in.
6. **Every fix gets a test lesson or a unit check**, or it will come back.
