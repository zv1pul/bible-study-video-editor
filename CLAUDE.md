# Bible Study Video Editor — project instructions

Turns a recorded Bible study lesson and its outline into a finished teaching
video: speaker lower third, a full-screen card for each point, discussion
countdowns that replace the dead time after each question, opening/closing
slides, setup and walk-off trimmed. Shared as a standalone download for Mac
(Apple Silicon, Intel) and Windows; installed copies update themselves.

## Start of every session

1. `git pull` — the repository is the source of truth; never work from an old copy.
2. Read **`docs/STATUS.md`** — current version, what's published, what's open.
3. Before touching `matcher.py`, `verifier.py`, `editor.py` or `reviewer.py`, read
   **`docs/LESSONS-LEARNED.md`**: every rule there came from a real lesson that
   once came out wrong. Don't undo one without a test proving it's safe.
4. `CLAUDE.local.md` (not in git) holds notes about the person and computer.

## End of every session

- Update **`docs/STATUS.md`** (version, what changed, what's open).
- A fix to how videos come out gets a row in `docs/LESSONS-LEARNED.md` **and** a
  test (`tests/test_units.py`, or a lesson in the corpus/review tests).
- Commit and push. Never leave work only on this computer.

## Where things are

| File | What it does |
|---|---|
| `app.py` | The page (Streamlit): recording, speaker presets, outline (paste whole outline), analyse, timings table, render, video check, helper, report |
| `transcriber.py` | Listening (faster-whisper locally, Groq online), downloads from Drive/Dropbox links, transcript cache |
| `matcher.py` | Finds each point in the transcript (Gemini → Groq → offline keywords), dead-time cuts, call-backs, outline reading |
| `verifier.py` | Checks every placement against what was said; lays out the timeline |
| `editor.py` | Renders with FFmpeg: pieces, countdown blocks, slides, joins |
| `reviewer.py` | Checks a finished video (looks and listens) and offers safe fixes |
| `assistant.py` | The chat helper: whitelisted changes only, Apply/Undo, own daily allowance |
| `control.py` / `control.json` | Settings and saved slides per computer; the live control file (latest version, shared keys, speakers, on/off switches) |
| `updater.py`, `packaging/` | Self-updating and the Mac/Windows downloads |
| `selfcheck.py` | The check each copy runs on its own computer |

## Testing — all must pass before anything is pushed

```bash
.venv/bin/python tests/test_units.py
.venv/bin/python tests/test_updater.py
.venv/bin/python tests/test_assistant.py
.venv/bin/python tests/run_long_countdown.py
.venv/bin/python tests/test_review.py
.venv/bin/python tests/test_app_flows.py
.venv/bin/python selfcheck.py
.venv/bin/python tests/build_corpus.py /tmp/corpus && .venv/bin/python tests/run_corpus.py /tmp/corpus offline
.venv/bin/python tests/build_formats.py /tmp/formats && .venv/bin/python tests/run_formats.py /tmp/formats
```

Pushing to `main` runs the same on GitHub on an Apple Silicon Mac, an Intel
Mac and Windows. Logs need a GitHub sign-in; failures are also written as
annotations, readable without one.

## Releasing — see `docs/RELEASING.md`

Version in `control.py` and `packaging/Info.plist` → notes in
`docs/release-notes/vX.Y.Z.md` → push tag `vX.Y.Z` (builds, checks and opens
each download on its own computer, makes a draft) → push tag `publish-vX.Y.Z`
(publishes, removes older releases) → set `latest_version` in `control.json`
→ push tag `verify-vX.Y.Z` (re-tests the published downloads).
**Publishing is outward-facing: only with the owner's explicit yes.**

## Rules that are never broken

- No secret is ever committed. Local keys live in `.streamlit/secrets.toml`
  (ignored). The group's shared keys in `control.json` are masked, not secret,
  and must only ever be free-tier keys.
- Nothing changes a person's recording; finished videos never overwrite one
  another; a saved setting, slide or speaker stays until the person changes it.
- Every network call and every wait has a time limit and a fallback that still
  produces a video.
- The extras (helper, video check, report) must never be able to break
  analysing or rendering; each can be switched off in `control.json`.
