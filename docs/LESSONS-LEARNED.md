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

## Lesson 5 — Romans 2 (Nathan Minchen, recorded live with an audience)

| What was different | What it broke | What changed |
|---|---|---|
| **A real live recording**: the room discusses each question aloud for four and a half minutes | — | Nothing: the dead-time rule built after lesson 3 cut **8:53** of audience discussion and resumed both times on the teacher's own voice ("Okay, brothers, ready?"). The first real proof of that rule |
| Camcorder file: 5 GB for 30 minutes (≈22 Mbps) | The disk guard refused to render — it sized the job from the **recording**, so it demanded 10 GB for a video that needed about 1.5 GB | The guard now estimates the **finished** size from the chosen quality (`editor.estimated_output_mb`), which is what actually gets written |
| An extra data stream in the container | — | Nothing: the renderer already maps video and audio explicitly |
| An off-camera "You ready?" at the start, and paper-shuffling after the prayer | Same blemishes as lesson 4 | Trimmed by hand again. **Worth building in**: find the first and last spoken words and trim to them |

## Lesson 6 — Romans 3:1–20 (Raneil Ensomo, recorded for the countdown)

| What was different | What it broke | What changed |
|---|---|---|
| Says "three minutes in pairs" and carries straight on, trusting the countdown; for the final question says "let the countdown timer run for this one" | The countdown landed after the **first** reading of each question, before the repeat and the instructions | `speech_end_after` keeps the question's repeat and its instructions ("three minutes in pairs…", "30 seconds of silent reflection…") with it; the countdown follows them |
| Sentences split across recogniser fragments ("…when you" / "fail Him?") | The question looked finished mid-sentence | Sentences are split at punctuation or a real pause, never at fragment boundaries |
| — | "second division" matched the instruction word "seconds"; "wait a minute" would have matched "minute" | Instruction words are plural only, and a sentence naming a division, principle or verse is never an instruction |
| Division 2 is **never announced by name** — he goes from the first countdown straight into "All right, now we've established…" | The model placed Division 2 at the overview, where all three are recited | **Division order repair**: a division must sit between the previous division's material and its own. Outside that stretch it moves to where its words are spoken inside it — or, if never spoken, to where its material begins (after the previous question and its instructions) |
| Strong models out of quota again; the weakest answered | It put the final question at the closing "amen" | Principles and questions are read word for word, so **where their own words are spoken** is a candidate placement, not just the model's quote |
| Questions read twice | Card started at the second reading | Cards for principles and questions start at the **first** reading |
| A division announced in the same breath as its principle | (Regression caught by the `live_audience` test) the repair treated "same moment" as "too late" | The division's window is inclusive at both ends |
| Outline calls the last question a "Final Reflection Question" | Card header would read "Application #3" | **Now automatic**: a question that opens with its own name ("Final Reflection Question", "Discussion Question:", on the same line or the line above) is headed with that name; "[30-SECOND COUNTDOWN]" notes are dropped; the outline, not the model, decides every header (`matcher.parse_questions`) |
| A second AI answer placed question 1 correctly but gave it a 58-second span | The countdown drifted 40 seconds into the teaching | A question's or principle's end comes from its **last reading**, not the model's span |
| Question read twice back to back | The second reading was filtered out as a duplicate of the first (off by one) and a window shifted into the next sentence was used | Readings exactly one question-length apart are both kept, and each match ends at its last matching word |

## Lesson 7 — Romans 3:21–26 (Eric Roachford, live room, group breakouts)

A 31-minute live recording: after each question Eric said "three minutes to
discuss this amongst each other" and the room broke into small groups — with
one group right beside the camera.

| What was different | What it broke | What changed |
|---|---|---|
| A group member sharing near the camera, as clearly as the teacher | The cut ended on them (15:43) instead of on Eric calling the room back at 18:25 — **two and a half minutes of group talk would have stayed in**, twice | When a timed discussion is announced ("three minutes … amongst each other"), the cut lasts until the teacher calls the room back ("All right, brothers…", "So brothers, let's hope you had a good conversation…"). "Everybody has a different…" inside a group is not a call back. Unit check added |
| The recording fetched from a Drive link | The finished video was saved "next to the recording" — a temporary folder that is cleared after six hours | Always saved to Movies/Videos → *Bible Study Video Editor* |
| A Drive file not shared publicly | A garbled error about "connection adapters" | "That file isn't shared publicly … choose 'Anyone with the link'" |
| Google's models all overloaded that morning | Switched to Groq on its own — as designed — but every overload message was shown as a warning | Moved behind the scenes |
| A 3.5 GB 1080p recording | Estimated 2 minutes to render; took 7½ | The estimate learns from this computer's last render |

## Making it stand on its own (version 1.1)

Building the standalone download, and running it exactly as a recipient would
— unzipped fresh, no settings, no key — found three things no amount of
testing in the development setup could have:

| What was different | What it broke | What changed |
|---|---|---|
| A fresh build installs whatever library versions are newest that day | **PyAV 19 had removed an option faster-whisper uses: the new download could not transcribe at all**, though nothing in the app had changed | `constraints.txt` pins every library to the versions the checks passed with; every build and check installs with it. The self-check now reads sound the way transcription does, so a build like that refuses to package and an update like that is refused |
| The first sentence of a recording is often heard less surely | The new start/end trim cut a real opening sentence, and a closing with no "amen" | The trim only skips one- or two-word asides and near-silence at the start; without a closing prayer it keeps everything anybody clearly said |
| Closing the launcher | Left the app's server running in the background | The launcher stops its server when it is closed, and the app has a **Quit** button |
| A slightly different transcript of a test lesson (GitHub's processor heard it a little differently) with no AI key | The keyword fallback misplaced everything, and the leftover cards were **crammed into the last half-second, on top of each other, two countdowns silently lost** | A card with no room left is set aside and shown as not found rather than squeezed in; a countdown after the very last thing said is allowed; cards and countdowns are planned from one schedule, so the video always matches its plan. The exact transcript is now a permanent test (`tests/fixtures/`) |
| **A clean computer, like GitHub's** | The length check used ffprobe, which a developer's Mac has but the download doesn't: on everyone else's computer the self-check would have read "0 seconds" and **refused every update** | Lengths are read with PyAV, which ships with the app (identical readings). Found only by running the checks on a machine with nothing but the repository — which GitHub now does for every change |

And what the download now carries so that nothing goes wrong later, on
anyone's computer:

- **A self-check** (`selfcheck.py`): the app page loads, every accuracy check
  passes, a test video renders with cards, a countdown and trims, sound
  decodes, artwork is present. It runs the first time each version opens, and
  from **Check this computer**.
- **Safe updates** (`updater.py`, `packaging/launcher.py`): an update is
  downloaded, its libraries compared with this copy's, its checks run on this
  computer — and only then staged. The launcher starts the newest version
  that passed and falls back to the one before if it won't start.
  `tests/test_updater.py` proves each path.
- **Checks on every change** (GitHub Actions, Mac and Windows), and the
  downloads built only from a version that passed them — each build checking
  itself again before it is zipped.

## Simple to use, and nothing left to chance (version 1.2)

Asked for: a closing slide that stays changed, no keys to set up, nothing
that times out — then a line-by-line review of everything that touches the
network, the disk or another program. What it found, and what changed:

| What was found | What it would have done | What changed |
|---|---|---|
| **A countdown of 2½–4 minutes on Windows** | Each second of the countdown was a separate input to FFmpeg; three minutes made a command longer than Windows allows (32,767 characters) and **the render failed** — only on Windows, only for long discussions, so no test caught it | The countdown is one list of frames, a single input however long. `tests/run_long_countdown.py` renders a 3-minute one on Mac and Windows for every change, in a folder named like an awkward Windows user name |
| **A recording with glitches** | FFmpeg's complaints filled a pipe nobody was reading; FFmpeg stopped and **the render froze forever** | Its complaints go to a file |
| **Every upload was saved as "source.mp4"** | Each new lesson's finished video **replaced last week's** | Finished videos are named after the recording and never overwrite: a name already taken gets "(2)". Copied under a temporary name and renamed only when complete |
| A Windows user name with an accent or an apostrophe | The list used to join pieces was written in the wrong text encoding and unescaped: **the join failed or crashed** | Always UTF-8, quotes escaped |
| No overall time limit on the AI | A service that accepts a request and never answers could leave "Asking the AI…" up **for over an hour** | Five minutes in all, with time kept back for the other service, then keyword matching |
| A shared key turned down | Stopped the whole search, even with a second service available | Tries the other service first |
| One dropped connection | Ended an online transcription, or restarted a multi-gigabyte download from zero | Retried; downloads pick up where they stopped |
| Analysing again after fixing a typo in the outline | Listened to the whole recording again (10–30 minutes) | The transcript is kept per recording and reused |
| One slow self-check during an update | Marked that version bad on that computer for good | Tried once more before it is refused; one update at a time |
| macOS's "App Nap", a laptop going to sleep | Slowed or paused a long render while nobody was watching | The app tells macOS not to nap it, and holds off sleep (not the screen) only while it works |
| Opening the app again after closing its tab | **On a Mac nothing happened** (macOS just "activates" a running app); on Windows a second copy started | The launcher starts the app and steps aside; opening it again finds the running copy and reopens the page. The app stops itself a few minutes after its last tab closes — never mid-job |
| "2:30" typed as a question's discussion time | Read as 230 seconds | "90", "2:30" and "2 min" all mean what they say |
| Two settings saved at the same moment; Windows briefly locking a file | One silently lost | Saved as one locked step, retried if Windows is holding the file |

What a person now sees: no key to find (the group's free-tier keys come with
the app and can be swapped for everyone by editing `control.json`), settings
and slides remembered, past speakers one click away, a **Choose the video…**
button that opens Finder or File Explorer, a timings table without the
technical columns unless asked for, retries kept "behind the scenes", and a
**Start the next video** button.

## Easier for everyone (version 1.2.1)

| What was asked or found | What changed |
|---|---|
| Outlines arrive as one block of text, laid out differently every week | **Paste the whole outline**: `matcher.read_outline` sorts it into the boxes. Seven real outlines are permanent tests (`tests/fixtures/outlines/`) |
| New teachers keep coming | **Preset speakers** with Save / Update / Remove; the group's teachers ship in `control.json` (`"speakers"`) and can be added for everyone there. Typing a different name over a preset offers *Save as new* and *Replace* — never replaces anybody silently |
| The finished file was named after the camera's file | An optional **lesson title** names it |
| One switch turned both slides off; a removed slide couldn't be told from "never set" | Each slide and the logo: replace, put back the original, or remove — decided in one place (`control.artwork_choice`): removed → none, replaced → yours (kept through updates), otherwise the app's own (so a new series' artwork in an update reaches everyone who never changed theirs) |
| The AI service, model and engines reset every launch | Remembered like every other setting; a saved choice that no longer exists falls back to the default instead of breaking the page |
| A saved key that stops working was used silently | A warning, and *Use shared keys* |
| Nothing tested the page itself, or the downloads as people get them | `tests/test_app_flows.py` drives the real page — a whole lesson, presets, paste, slides, settings across restarts, wrong files, bad links, damaged settings — on Apple Silicon, Intel and Windows for every change. `tests/try_download.py` opens each finished download on its own kind of computer before a release is published (and `verify-v*` tags re-test a published one) |
| An Intel Mac library went missing at release time | Intel Macs are in the checks for every change |

## The video checks itself, and a helper (version 1.3)

Every check that was done by hand on the real lessons — measuring the
finished file, looking at the slides and cards, and above all listening to
what comes just before and just after each countdown — now runs after every
render (`reviewer.py`). It was tried first on lessons made to go wrong
(`tests/test_review.py`) and then on the eight test lessons, where any
"problem" on a video that is right fails the checks (`tests/run_corpus.py`).

| What testing found | What changed |
|---|---|
| A group member speaking clearly near the camera passed the "teacher is back" listen | The teacher's own call back ("All right, brothers…") anywhere after the point the video returns to means it returned too early — that's what catches it |
| "Let's close in prayer" was taken for a call back | A closing isn't a call back |
| A principle read straight after other speech wasn't seen inside a cut | The words inside each cut are compared directly |
| Short sentences and pauses were called "came back too early" | Only unclear talk (a room) or a later call back counts; a quiet stretch after a countdown is its own finding, with a fix |
| "Doesn't end with a prayer" was flagged on every lesson without one | Only an unclear or talky ending is flagged |
| The helper's light model picked the second question's "So brothers" for the first question | A "come back" must fall between its question and the next point; the moments the teacher calls the room back are listed for it |
| Ten questions in a minute hit Google's per-minute limit; the backup refused the long transcript | One short wait and retry; the backup gets a shorter transcript |
| The "look over" button proposed a change nobody asked for | It explains only |
| The helper's panel closed after each answer | It stays open while there's a conversation or a change waiting |
| The report fell over when the Desktop couldn't be used | It makes the folder, or uses the home folder |

The helper (`assistant.py`) can only propose a short list of known changes,
each checked and kept inside the recording, applied only on **Apply** and
undone with **Undo**; it has its own daily allowance (40 per computer), a
cooldown, a size limit and a time limit; and it, the check and the report can
each be switched off for every copy in `control.json` (`"settings":
{"assistant": false}` and so on).

## Left in the background (version 1.3.2)

| What happened | Why | What changed |
|---|---|---|
| Left open while the laptop slept or the tab sat in the background, the page said it had lost the connection, and refreshing loaded nothing | The app stopped itself 5 minutes after it stopped hearing from its tab — and a sleeping computer or a resting background tab goes quiet just like a closed one. The page engine also forgot a disconnected session after 2 minutes | Sessions are kept 12 hours (`server.disconnectedSessionTTL`), so a tab reconnects with all its work; the app stops only after an hour with no session at all (time asleep doesn't count), never during a job. After every analysis, render or change the work is saved, and a page that opens blank offers **Pick up where I left off** |

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
7. **Test the thing people actually get, on a computer that has nothing
   else.** The download, unzipped fresh, started the way they start it — and
   the checks run where no developer tools are installed.
8. **The outline's structure is evidence too.** Divisions come in order, each
   after the previous one's material; principles and questions are read word
   for word. A placement that contradicts the outline is wrong, whatever the
   model says.
9. **Every wait has an end.** Every network call, every program the app runs
   and the AI search as a whole has a time limit, and every limit has a
   fallback that still produces a video.
10. **Never overwrite what somebody made.** A finished video, a saved slide,
    a setting: written beside the old one and swapped in only when complete.
11. **A choice made once stays made.** Every setting, slide, speaker and key a
    person changes is the new default on that computer — through restarts and
    updates — until they change it again, and each can be put back.
12. **Being away is not leaving.** A sleeping computer or a background tab must
    never cost anybody their work; anything long-running is saved as it goes.
