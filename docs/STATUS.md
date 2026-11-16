# Status

*Update this at the end of every working session.*

## Now

- **Published version: 1.3.4** (2026-11-15) —
  https://github.com/zv1pul/bible-study-video-editor/releases/latest
  (Mac Apple Silicon, Mac Intel, Windows). Older releases removed; their tags kept.
  The app always uses address 47823 (falls back only if something else uses it).
- `control.json` `latest_version` = 1.3.4, so installed copies offer it.
- Every check passes locally and on GitHub (Apple Silicon, Intel, Windows); the
  three published downloads were each opened and checked on their own computer.

## Ready, not yet published: 1.3.5

A question can come anywhere in its division — opening it, between principles
or at the end — on the offline backup too (the AI path already handled it).
All checks pass locally. Waiting for the owner's yes to publish.

## What 1.3.0 can do

Everything in `docs/release-notes/v1.3.0.md`, plus what came before: shared AI
keys (nothing to set up), remembered settings and slides, preset speakers,
paste-the-whole-outline, lesson title naming the file, group discussions cut
until the teacher calls the room back, the finished video checked after every
render with one-click fixes, the chat helper, Report a problem.

## Open


- **Report email** — `report_email` in `control.json` is empty, so reports are
  saved as a file but not emailed. Needs an address from the owner.
- **Signing** — downloads aren't signed by Apple/Microsoft, so first launch shows
  a security warning (Read me first explains it). Removing it needs an Apple
  Developer account (~$99/yr) and a Windows signing certificate.
- **Ideas not built** — automatic re-render after a fix; more change types for
  the helper (e.g. move a cut start).

## History

| Date | Version | What changed |
|---|---|---|
| 2026-10-19 | 1.2.0 | Standalone downloads, shared keys, remembered slides/settings, big robustness review |
| 2026-10-20 | — | Lesson 7 (Eric, Romans 3:21–26): announced group discussions cut to the call back |
| 2026-10-23 | 1.2.1 | Paste outline, preset speakers, lesson title, slides one at a time, every setting remembered, page tests on all computers |
| 2026-10-23 | 1.3.0 | Video check after every render, chat helper, Report a problem |
| 2026-11-15 | 1.3.5 | A question anywhere in its division, on the offline backup too — not yet published |
| 2026-11-15 | 1.3.4 | Scripture in every format the group uses (reference before/after, typos, short book names, verse numbers) |
| 2026-11-09 | 1.3.3 | Group feedback: m:ss.s timings, countdown waits for a breath, BSF titles, half verses (4:16a), references kept together on cards |
| 2026-10-24 | 1.3.2 | Leave it in the background: sessions survive sleep (12 h), no early shutdown, "Pick up where I left off" |
| 2026-10-23 | 1.3.1 | Always the same address, so an open tab reconnects after a restart/update/sleep (launcher — new download needed) |
