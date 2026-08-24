# Sharing this app

The setup in use: one GitHub repository, deployed two ways.

* **Hugging Face Space** — a public link anyone in the fellowship can open,
  no login. Rebuilds itself every time you push.
* **Local install** — for whoever processes the full-length lessons. No
  upload, no size limit, and it uses that person's own processor.

---

## Keeping both up to date

Everything lives in one repository, with two remotes:

```bash
git remote -v
# origin  https://github.com/<you>/bible-study-video-editor.git
# space   https://huggingface.co/spaces/<you>/bible-study-video-editor
```

To publish a change:

```bash
git add -A && git commit -m "what changed"
git push origin main
git push space main
```

The Space rebuilds on its own, usually in a couple of minutes. People with a
local install double-click **update.command** (Mac) or **update.bat**
(Windows) — it pulls the new version and refreshes the packages without
touching their API key or their artwork in `assets/`.

---

## Setting up the Hugging Face Space

1. Create a Space at <https://huggingface.co/new-space>, SDK **Streamlit**,
   hardware **CPU basic (free)**.
2. Add it as a remote and push:

   ```bash
   git remote add space https://huggingface.co/spaces/<you>/bible-study-video-editor
   git push space main
   ```

   The Space configuration is already in the top of `README.md`, and
   `packages.txt` installs FFmpeg and the fonts.
3. In **Settings → Variables and secrets**, add `GEMINI_API_KEY` (and
   `GROQ_API_KEY` if you use hosted transcription). Never commit these — they
   are excluded by `.gitignore` for exactly this reason.

### What to expect from the free Space

2 vCPU and 16 GB of RAM, shared by everyone using the link at once.

* Comfortable for lessons up to about 30 minutes.
* One person renders at a time; a second person waits.
* The container sleeps after a period of no use and takes a minute to wake.
* Everyone's work counts against the one API key you configured. If that
  becomes a problem, remove the secret and each person pastes their own free
  key into the sidebar instead.

For a full-length lesson, use a local install instead — uploading several
gigabytes is slower than reading the file off the desk.

---

## Setting up a local install

Send the repository link. On the other end:

```bash
git clone https://github.com/<you>/bible-study-video-editor.git
```

Then **run.command** (Mac) or **run.bat** (Windows). The first launch builds a
private Python environment and installs everything, which takes 5–10 minutes
once. Every launch after that opens in seconds at <http://localhost:8501>.

FFmpeg comes bundled with the Python packages, so there is nothing else to
install.

> On a Mac, if you see *"cannot be opened because it is from an unidentified
> developer"*, right-click `run.command` → **Open** → **Open**. Once only.

Each person adds their own API key in the sidebar, or you create
`.streamlit/secrets.toml` for them (it is never committed).

---

## Docker

For anyone who keeps hitting Python version trouble:

```bash
docker compose up --build
```

Then open <http://localhost:8501>.

---

## Streamlit Community Cloud

Possible, but its containers have about 1 GB of RAM — not enough to run the
speech model locally, and long videos will run out of memory while rendering.
Hugging Face is the better free host for this app.
