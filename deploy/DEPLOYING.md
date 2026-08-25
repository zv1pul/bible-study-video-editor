# Sharing this app

One GitHub repository, deployed two ways:

* **Streamlit Community Cloud** — a public link anyone in the fellowship can
  open, no login, rebuilt automatically every time you push.
* **Local install** — for whoever processes the full-length lessons. No
  upload, no size limit, and it uses that person's own processor.

> **Hugging Face Spaces is no longer an option.** Their free tier now only
> offers *Static* Spaces, which serve HTML and JavaScript with no Python
> process behind them — Gradio and Docker require a paid plan, and Streamlit
> is not offered at all. A Static Space cannot transcribe or render video.
> If you ever subscribe to HF PRO, the Space configuration that used to sit
> at the top of `README.md` is preserved at the bottom of this file.

---

## Streamlit Community Cloud

Free, and viewers need no account.

1. Go to <https://share.streamlit.io> and sign in with GitHub.
2. **Create app** → **Deploy a public app from GitHub**.
3. Repository `zv1pul/bible-study-video-editor`, branch `main`, main file
   `app.py`. If the repository is private, grant the extra GitHub permission
   it asks for — the free plan allows one private app.
4. Open **Advanced settings → Secrets** and paste:

   ```toml
   GEMINI_API_KEY = "your-key"
   GROQ_API_KEY = "your-key"
   BSVE_HOSTED = "1"
   ```

5. **Deploy.** The first build takes a few minutes.

`BSVE_HOSTED` is what tells the app it is on a shared server. It then hides
the option to browse the server's own filesystem, transcribes through Groq
rather than loading a speech model into the container's memory, and warns
about uploads large enough to exhaust it.

### What to expect

The free plan gives roughly **1 GB of memory** for everything — the Python
process, the recording, and the render.

Measured on a full 1080p lesson, rendering itself peaks at about **130 MB**.
It is not the demanding part. The demanding part is the *recording*: a browser
upload is held in memory for the whole session, so a 500 MB file occupies
500 MB the entire time it is being worked on, and that is what exhausts the
container.

So on the hosted app, **paste a link instead of uploading**. A link is
streamed to disk a megabyte at a time and never sits in memory — verified at
5 MB of growth while fetching a 16 MB file. Google Drive and Dropbox share
links both work; set Drive sharing to "Anyone with the link" first.

Drive answers the plain download link for any file over about 100 MB — which
is every real lesson — with a "we can't scan this for viruses" confirmation
page rather than the video. The app reads that form and submits it, so the
fetch simply works. Measured on a real lesson: 327 MB in 43 seconds.

* Uploads are capped at 200 MB, enforced by Streamlit itself, which says so
  plainly in the uploader. The cap is set low on purpose: an upload large
  enough to exhaust the container would otherwise kill it and show an
  unexplained error page, which is the worst thing that can happen to
  somebody who did not sign up to think about memory.
* Free memory is checked before analysing and before rendering. If another
  person is mid-render there is not enough left, and the app says so in a
  sentence rather than dying part way through.
* A link has no practical limit.
* One person renders at a time; a second waits.
* Finished videos are served straight from disk rather than through the app,
  so downloading costs no memory and the length of the lesson does not
  matter. Measured: a file served at 450 MB/s with zero memory growth.
* Free memory and free disk are both checked before rendering, so a long
  lesson is refused with an explanation rather than failing part way.

### How long a lesson can this handle?

From measured rates, per stage, on the free tier:

| Lesson | Recording | Fetch | Render | Finished file |
|--------|-----------|-------|--------|---------------|
| 23 min | 327 MB | 43 s | 2.6 min | 126 MB |
| 45 min | 639 MB | 84 s | 5.0 min | 248 MB |
| 60 min | 852 MB | 112 s | 6.7 min | 330 MB |
| 90 min | 1.3 GB | 168 s | 10.0 min | 495 MB |

Every stage streams, so none of them holds the video in memory. The practical
limits that remain are the container's disk (checked before rendering) and
the browser needing to stay open while the render runs.
* The app sleeps after 12 quiet hours and wakes when somebody visits.

For a full-length lesson, use a local install. Sending several gigabytes to a
shared container is slower than reading the file off the desk, and more
likely to run out of memory part way through.

---

## Local install

```bash
git clone git@github.com:zv1pul/bible-study-video-editor.git
```

Then **run.command** (Mac) or **run.bat** (Windows). The first launch builds a
private Python environment from `requirements-local.txt`, which takes 5-10
minutes once. Every launch after that opens in seconds at
<http://localhost:8501>.

The local install adds what the hosted one cannot have: offline transcription
that needs no key and never sends the recording anywhere, and the "use a file
already on this computer" option, which has no size limit at all.

> On a Mac, if you see *"cannot be opened because it is from an unidentified
> developer"*, right-click `run.command` → **Open** → **Open**. Once only.

---

## Publishing a change

```bash
./deploy/publish.sh
```

Streamlit Community Cloud redeploys on its own within a minute or two. People
with a local install double-click **update.command** or **update.bat**, which
pulls the new version and refreshes packages without touching their API key or
their artwork in `assets/`.

---

## Docker

For anyone who keeps hitting Python version trouble:

```bash
docker compose up --build
```

Then open <http://localhost:8501>.

---

## If you ever pay for Hugging Face

A PRO subscription re-enables Spaces that run compute. Put this back at the
very top of `README.md`, push to a Space remote, and it will build:

```
---
title: Bible Study Video Editor
emoji: 📖
colorFrom: indigo
colorTo: yellow
sdk: streamlit
app_file: app.py
pinned: false
---
```
