"""
app.py
======
The Streamlit front end and the main execution loop.

Flow:
  1. Upload the recording, type in the speaker details and the lesson outline.
  2. "Analyse recording"  -> transcribe (Step 1) + match points to times (Step 2)
  3. Review / nudge the timestamps in the table, check the still preview.
  4. "Render final video" -> burn in the graphics (Step 3) and download.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import traceback

import pandas as pd
import streamlit as st

import editor
import matcher
import transcriber
import verifier

# --------------------------------------------------------------------------
# Page setup
# --------------------------------------------------------------------------

st.set_page_config(
    page_title="Bible Study Video Editor",
    page_icon="📖",
    layout="wide",
)

DEFAULTS = {
    "video_path": None,
    "video_key": None,
    "video_info": None,
    "segments": None,
    "silences": None,
    "fetched_path": None,
    "matches": None,
    "verdicts": None,
    "notes": [],
    "output_path": None,
    "render_seconds": 0.0,
}
for key, value in DEFAULTS.items():
    st.session_state.setdefault(key, value)


WORKDIR_PREFIX = "bible_study_editor_"
WORKDIR_MAX_AGE_HOURS = 6


@st.cache_resource
def sweep_old_workdirs(max_age_hours: int = WORKDIR_MAX_AGE_HOURS) -> int:
    """
    Delete working folders left behind by earlier sessions.

    Each session keeps a copy of the video it is working on plus the render,
    which for a full-length lesson is well over a gigabyte. Streamlit gives no
    reliable "session ended" hook, so instead everything older than a few
    hours is swept on start-up. Cached so it runs once per process, not on
    every rerun.
    """
    root = tempfile.gettempdir()
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    try:
        for name in os.listdir(root):
            if not name.startswith(WORKDIR_PREFIX):
                continue
            path = os.path.join(root, name)
            try:
                if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                    shutil.rmtree(path, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
    except OSError:
        pass
    return removed


def workdir() -> str:
    if "workdir" not in st.session_state:
        sweep_old_workdirs()
        st.session_state.workdir = tempfile.mkdtemp(prefix=WORKDIR_PREFIX)
    return st.session_state.workdir


def discard_previous_render() -> None:
    """
    Drop the last render before starting another.

    Without this, re-rendering the same lesson three times leaves three
    finished videos on disk for the rest of the session.
    """
    previous = st.session_state.get("output_path")
    if previous and os.path.exists(previous):
        try:
            os.remove(previous)
        except OSError:
            pass
    st.session_state.output_path = None


def is_hosted() -> bool:
    """
    True when this is running on a shared server rather than someone's laptop.

    Two things must change when hosted: nobody may browse the server's own
    filesystem, and local transcription is a poor idea on a container shared
    with other people. Set BSVE_HOSTED=1 to force it on.
    """
    return bool(
        os.environ.get("BSVE_HOSTED")
        or os.environ.get("SPACE_ID")            # Hugging Face Spaces
        or os.environ.get("STREAMLIT_SHARING_MODE")  # Streamlit Community Cloud
        or os.environ.get("K_SERVICE")           # Cloud Run
    )


HOSTED = is_hosted()

# A browser upload is held in memory for the whole session, so on a small
# hosted container it — not the rendering — is what runs the machine out of
# memory. Measured: a full 1080p render peaks at about 130 MB, while a 500 MB
# upload sits in RAM the entire time it is being worked on.
HOSTED_UPLOAD_WARN_MB = 250
HOSTED_UPLOAD_MAX_MB = 400

ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def asset_default(name: str) -> str:
    """Artwork kept in assets/ is used whenever nothing was uploaded."""
    for extension in (".png", ".jpg", ".jpeg", ".webp"):
        candidate = os.path.join(ASSET_DIR, name + extension)
        if os.path.exists(candidate):
            return candidate
    return ""


def save_upload(upload, name: str) -> str:
    """Write a sidebar image upload to disk once and return its path."""
    if upload is None:
        return ""
    path = os.path.join(workdir(), f"{name}{os.path.splitext(upload.name)[1]}")
    marker = f"{name}_key"
    stamp = f"{upload.name}-{upload.size}"
    if st.session_state.get(marker) != stamp or not os.path.exists(path):
        with open(path, "wb") as handle:
            handle.write(upload.getbuffer())
        st.session_state[marker] = stamp
    return path


def secret(name: str) -> str:
    """Read a key from st.secrets or the environment, if one was configured."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.environ.get(name, "")


# --------------------------------------------------------------------------
# Sidebar: settings
# --------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ Settings")

    st.subheader("AI matching")
    provider = st.selectbox(
        "Provider",
        options=list(matcher.PROVIDERS.keys()),
        format_func=lambda key: matcher.PROVIDERS[key]["label"],
        help="Both have free tiers. Gemini is the most generous.",
    )
    provider_info = matcher.PROVIDERS[provider]
    default_key = secret("GEMINI_API_KEY" if provider == "gemini" else "GROQ_API_KEY")
    llm_api_key = st.text_input(
        "API key",
        value=default_key,
        type="password",
        help=f"Free key: {provider_info['key_url']}",
    )
    llm_model = st.selectbox(
        "Model",
        options=matcher.model_options(provider),
        help="'auto' tries the best model first and moves down the list if one "
             "is retired, rate limited or overloaded.",
    )
    double_check = st.checkbox(
        "Double-check with a second model",
        value=True,
        help="Runs the matching twice using two different models and compares "
             "the answers. Points both models agree on are marked verified. "
             "It uses a second model, so it draws on that model's separate "
             "daily allowance rather than doubling up on the first.",
    )
    backup_provider = "groq" if provider == "gemini" else "gemini"
    backup_key = st.text_input(
        f"Backup key — {matcher.PROVIDERS[backup_provider]['label']}",
        value=secret("GROQ_API_KEY" if backup_provider == "groq" else "GEMINI_API_KEY"),
        type="password",
        help="A completely separate free allowance. When the first provider "
             "is used up for the day, the app switches to this instead of "
             "dropping to offline matching.",
    )
    st.caption(
        f"Get free keys → [{provider}]({provider_info['key_url']}) · "
        f"[{backup_provider}]({matcher.PROVIDERS[backup_provider]['key_url']})"
    )

    # --- what is left of today's free allowance --------------------------
    with st.expander("Today's free usage", expanded=False):
        rows = matcher.remaining_today(provider)
        total_used = sum(u for _, u, _ in rows)
        total_cap = sum(c for _, _, c in rows)
        st.progress(
            min(total_used / max(total_cap, 1), 1.0),
            text=f"{total_used} of {total_cap} requests used today",
        )
        for name, used, cap in rows:
            bar = "█" * min(used, cap) + "·" * max(cap - used, 0)
            st.caption(f"`{bar}` {name} — {used}/{cap}")
        st.caption(
            f"The free tier allows {matcher.DAILY_FREE_REQUESTS} requests per "
            "model per day. Repeating an analysis you have already run costs "
            "nothing — the answer is reused."
        )

    st.divider()
    st.subheader("Transcription")
    # A shared container has neither the memory to hold a speech model nor the
    # processor to spare, so hosted deployments always use the hosted service.
    if HOSTED or not transcriber.LOCAL_AVAILABLE:
        engine_options = ["groq"]
        st.caption(
            "Transcription runs through Groq here. Running the app on your "
            "own computer adds an offline option that needs no key."
        )
    else:
        engine_options = ["local", "groq"]

    engine = st.radio(
        "Engine",
        options=engine_options,
        format_func=lambda key: {
            "local": "On this computer (faster-whisper, no key)",
            "groq": "Hosted (Groq Whisper, needs a free key)",
        }[key],
        help=(
            "On this computer keeps everything private and offline but is "
            "slower. Hosted is much faster and is the right choice when the "
            "app is running on a free web host."
        ),
    )
    if engine == "local":
        model_size = st.select_slider(
            "Accuracy vs speed",
            options=transcriber.LOCAL_MODEL_SIZES,
            value="base",
            help="'base' is a good balance. 'small' is noticeably better on "
                 "echoey room audio but roughly 2-3x slower.",
        )
        groq_key = ""
    else:
        model_size = "base"
        groq_key = st.text_input(
            "Groq API key",
            value=llm_api_key if provider == "groq" else secret("GROQ_API_KEY"),
            type="password",
        )
        st.caption("Free key → https://console.groq.com/keys")

    language = st.text_input("Spoken language code", value="en", max_chars=5)

    st.divider()
    st.subheader("Graphics")
    card_style = st.radio(
        "Point style",
        options=["fullscreen", "caption"],
        format_func=lambda key: {
            "fullscreen": "Full-screen cards (template)",
            "caption": "Caption over the video",
        }[key],
        help="Full-screen cards fill the frame with the beige template while "
             "the teaching continues underneath. Captions keep the picture "
             "visible and put a small card along the bottom.",
    )
    caption_seconds = st.slider("Seconds each point stays on screen", 3, 20, 8)
    soft_transitions = st.checkbox(
        "Soften the cuts",
        value=False,
        help="Off matches the reference style: graphics cut hard in and hard "
             "out. On gives a quarter-second dissolve.",
    )

    logo_file = st.file_uploader(
        "Logo for the top right of each card (optional)",
        type=["png", "jpg", "jpeg", "webp"],
        help=f"Sits in a reserved {editor.CARD_LOGO_BOX}×{editor.CARD_LOGO_BOX} "
             "pixel square. A PNG with a transparent background works best.",
    )

    st.markdown("**Intro and outro**")
    _found = [
        name for name, path in (
            ("intro", asset_default("intro")),
            ("outro", asset_default("outro")),
            ("logo", asset_default("logo")),
        ) if path
    ]
    if _found:
        st.caption(f"Using {', '.join(_found)} from the assets folder.")
    intro_file = st.file_uploader(
        "Opening image", type=["png", "jpg", "jpeg", "webp"], key="intro_img"
    )
    outro_file = st.file_uploader(
        "Closing image", type=["png", "jpg", "jpeg", "webp"], key="outro_img"
    )
    bookend_seconds = st.slider("Seconds each is held", 2, 15, 5)
    quality = st.select_slider(
        "Render speed vs file size",
        options=["ultrafast", "veryfast", "medium"],
        value="ultrafast",
        help="ultrafast = quickest render, biggest file. medium = smaller file, slower.",
    )
    threads = st.slider("CPU threads for rendering", 1, 16, 4)
    render_engine = st.radio(
        "Rendering engine",
        options=["auto", "moviepy"],
        format_func=lambda key: {
            "auto": "Fast (recommended)",
            "moviepy": "Compatible (slower)",
        }[key],
        help=(
            "Fast hands the whole job to FFmpeg in one pass — about 20x "
            "quicker on a full-length lesson. Switch to Compatible only if a "
            "particular file refuses to render."
        ),
    )
    st.caption(f"Overlay font: {editor.font_report()}")

# --------------------------------------------------------------------------
# Main form
# --------------------------------------------------------------------------

st.title("📖 Bible Study Video Editor")
st.write(
    "Upload the recording, paste in the lesson outline, and the app works out "
    "when each point is taught — then builds the finished video with the "
    "speaker's lower third, a full-screen card for every point, and your "
    "intro and outro."
)

left, right = st.columns([1, 1], gap="large")

with left:
    st.subheader("1 · The recording")
    # Reading an arbitrary path would mean reading the SERVER's disk, so that
    # option is never offered when shared. A link is offered instead: it
    # streams to disk a megabyte at a time and never sits in memory.
    options = ["link", "upload"] if HOSTED else ["path", "upload", "link"]
    source = st.radio(
        "Where is the recording?",
        options=options,
        format_func=lambda key: {
            "link": "Paste a link" + (" (recommended)" if HOSTED else ""),
            "upload": "Upload a file",
            "path": "Use a file already on this computer",
        }[key],
        horizontal=True,
        help=(
            "A link is fetched straight to disk, so a large recording will "
            "not exhaust this shared server. An upload is held in memory the "
            "whole time."
            if HOSTED else
            "A full-length lesson is often several gigabytes. Pointing at the "
            "file on disk skips the copy entirely and starts straight away."
        ),
    )

    uploaded = None
    local_path = ""
    link_url = ""

    if source == "link":
        link_url = st.text_input(
            "Link to the video",
            placeholder="https://drive.google.com/file/d/…/view",
            help="Google Drive and Dropbox links work. In Drive, set sharing "
                 "to 'Anyone with the link' first.",
        ).strip()
        if link_url and st.button("Fetch this recording", width="stretch"):
            try:
                target = os.path.join(workdir(), "source_from_link.mp4")
                bar = st.progress(0.0)
                line = st.empty()
                transcriber.download_video(
                    link_url, target,
                    max_bytes=(HOSTED_UPLOAD_MAX_MB * 4 * 1024 * 1024) if HOSTED else 0,
                    progress_cb=lambda f, m: (bar.progress(min(max(f, 0.0), 1.0)),
                                              line.write(m)),
                )
                st.session_state.fetched_path = target
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    elif source == "upload":
        uploaded = st.file_uploader(
            "Video file",
            type=["mp4", "mov", "m4v", "mkv", "webm"],
            accept_multiple_files=False,
        )
        if HOSTED:
            st.caption(
                "Uploads are held in memory while they transfer, and this "
                "shared server has about a gigabyte in total. Lessons up to "
                "roughly 30 minutes are comfortable here; for a full-length "
                "recording, use a local install instead."
            )
            if uploaded is not None:
                size_mb = uploaded.size / (1024 * 1024)
                if size_mb > HOSTED_UPLOAD_MAX_MB:
                    st.error(
                        f"That file is {size_mb:.0f} MB, which would run this "
                        "shared server out of memory part way through. Paste a "
                        "link to it instead — that streams to disk and has no "
                        "practical limit."
                    )
                    uploaded = None
                elif size_mb > HOSTED_UPLOAD_WARN_MB:
                    st.warning(
                        f"That file is {size_mb:.0f} MB. It may work, but a "
                        "link is safer on a shared server."
                    )
        else:
            st.caption(
                "Uploads are held in memory while they transfer. For a "
                "full-length lesson over about 1 GB, switch to *Use a file "
                "already on this computer* — it reads straight from disk, "
                "starts immediately and has no size limit."
            )
    else:
        local_path = st.text_input(
            "Full path to the video",
            placeholder="/Users/you/Movies/lesson.mp4",
            help="In Finder: right-click the file, hold Option, choose "
                 "'Copy … as Pathname', then paste it here.",
        ).strip().strip('"').strip("'")
        if local_path and not os.path.exists(local_path):
            st.error("No file at that path. Check it and try again.")

    st.subheader("2 · The speaker")
    speaker_name = st.text_input("Name", placeholder="Pastor John Smith")
    speaker_title = st.text_input("Title", placeholder="Senior Pastor, Grace Fellowship")

with right:
    st.subheader("3 · The lesson outline")
    st.caption(
        "One point per line. Blank lines are ignored. Use ` | ` inside a line "
        "to put several items on a single card as a list."
    )
    takeaway = st.text_area(
        "Takeaway",
        height=80,
        placeholder="True worship begins with God's initiative, not ours.",
    )
    divisions = st.text_area(
        "Divisions",
        height=110,
        placeholder="I. Man-initiated Religion\nII. God-initiated Worship\nIII. The Response of Faith",
    )
    principles = st.text_area(
        "Principles",
        height=110,
        placeholder="God defines how He is to be approached.\nObedience precedes blessing.",
    )
    applications = st.text_area(
        "Applications",
        height=110,
        placeholder="Examine what you bring to worship this week.\nConfess where you have substituted effort for faith.",
    )

logo_path = save_upload(logo_file, "logo") or asset_default("logo")
intro_path = save_upload(intro_file, "intro") or asset_default("intro")
outro_path = save_upload(outro_file, "outro") or asset_default("outro")

outline = {
    "Takeaway": takeaway,
    "Division": divisions,
    "Principle": principles,
    "Application": applications,
}
points = matcher.build_lesson_points(outline)

# --------------------------------------------------------------------------
# Save the upload once and read its properties
# --------------------------------------------------------------------------

def _load_source(path: str, key: str) -> None:
    """Point the app at a video and clear any results from the previous one."""
    if st.session_state.video_key == key:
        return

    # Switching videos: the previous one's copy and render are now dead weight.
    discard_previous_render()
    old_path = st.session_state.get("video_path")
    if old_path and old_path != path and old_path.startswith(workdir()):
        try:
            os.remove(old_path)
        except OSError:
            pass

    st.session_state.update(
        video_key=key, video_path=path, segments=None, matches=None,
        notes=[], output_path=None,
    )
    try:
        st.session_state.video_info = editor.video_info(path)
    except Exception as exc:
        st.session_state.video_info = None
        st.error(f"Could not read that video file: {exc}")


fetched = st.session_state.get("fetched_path")
if source == "link" and fetched and os.path.exists(fetched):
    stat = os.stat(fetched)
    _load_source(fetched, f"{fetched}-{stat.st_size}-{int(stat.st_mtime)}")
elif local_path and os.path.exists(local_path):
    stat = os.stat(local_path)
    # Read straight from disk: no copy, no upload, works for any file size.
    _load_source(local_path, f"{local_path}-{stat.st_size}-{int(stat.st_mtime)}")
elif uploaded is not None:
    key = f"{uploaded.name}-{uploaded.size}"
    if st.session_state.video_key != key:
        path = os.path.join(workdir(), "source" + os.path.splitext(uploaded.name)[1])
        with open(path, "wb") as handle:
            handle.write(uploaded.getbuffer())
        st.session_state.update(
            video_key=key,
            video_path=path,
            segments=None,
            matches=None,
            notes=[],
            output_path=None,
        )
        try:
            st.session_state.video_info = editor.video_info(path)
        except Exception as exc:
            st.session_state.video_info = None
            st.error(f"Could not read that video file: {exc}")

info = st.session_state.video_info
if info:
    columns = st.columns(4)
    columns[0].metric("Length", transcriber.format_timestamp(info["duration"]))
    columns[1].metric("Resolution", f"{info['size'][0]}×{info['size'][1]}")
    columns[2].metric("Frame rate", f"{info['fps']:.0f} fps")
    columns[3].metric("Audio", "Yes" if info["has_audio"] else "No")
    if not info["has_audio"]:
        st.error("This file has no audio track, so it cannot be transcribed.")

st.divider()

# --------------------------------------------------------------------------
# Step 1 + 2: analyse
# --------------------------------------------------------------------------

ready = bool(st.session_state.video_path) and bool(points)
if not ready:
    st.info("Upload a video and enter at least one lesson point to begin.")

if st.button("🔍 Analyse recording", type="primary", disabled=not ready, width="stretch"):
    st.session_state.output_path = None
    try:
        with st.status("Working…", expanded=True) as status:
            bar = st.progress(0.0)
            line = st.empty()

            def on_progress(fraction, message):
                bar.progress(min(max(fraction, 0.0), 1.0))
                line.write(message)

            status.update(label="Step 1 of 2 — transcribing the recording")
            started = time.time()
            segments = transcriber.transcribe_video(
                st.session_state.video_path,
                engine=engine,
                model_size=model_size,
                api_key=groq_key or (llm_api_key if provider == "groq" else ""),
                language=language.strip() or None,
                progress_cb=on_progress,
            )
            st.session_state.segments = segments
            status.update(label="Listening for reflection pauses")
            silences = transcriber.detect_silences(st.session_state.video_path, 2.0)
            st.session_state.silences = silences
            long_pauses = [s for s in silences if s["duration"] >= matcher.TIMER_MIN_SILENCE]
            if long_pauses:
                line.write(
                    f"Found {len(long_pauses)} reflection pause(s) of "
                    f"{matcher.TIMER_MIN_SILENCE:.0f}s or more."
                )
            line.write(
                f"Transcribed {len(segments)} segments in "
                f"{transcriber.format_timestamp(time.time() - started)}."
            )

            if not segments:
                status.update(label="No speech was detected", state="error")
                st.stop()

            status.update(label="Step 2 of 3 — matching your outline to the transcript")
            bar.progress(0.1)
            video_duration = (info or {}).get("duration", 0.0)
            matches, notes, model_used = matcher.match_lesson_points(
                points,
                segments,
                provider=provider,
                api_key=llm_api_key.strip(),
                model=llm_model,
                duration=video_duration,
                speaker=speaker_name,
                speaker_title=speaker_title,
                silences=silences,
                other_keys={backup_provider: backup_key.strip()},
                progress_cb=on_progress,
            )
            if model_used:
                line.write(f"Matched by {model_used}.")

            # An independent second opinion from a different model. Where two
            # models that never saw each other's answer agree, the placement
            # is about as trustworthy as this gets without a human.
            second = None
            if double_check and model_used:
                alternate = matcher.next_model(provider, model_used)
                if alternate:
                    status.update(label="Step 3 of 3 — double-checking with a second model")
                    line.write(f"Asking {alternate} the same question…")
                    second, second_notes, second_used = matcher.match_lesson_points(
                        points,
                        segments,
                        provider=provider,
                        api_key=llm_api_key.strip(),
                        model=alternate,
                        duration=video_duration,
                        speaker=speaker_name,
                        speaker_title=speaker_title,
                        silences=silences,
                        other_keys={backup_provider: backup_key.strip()},
                    )
                    if not second_used:
                        second = None
                        notes.append(
                            "The second model was unavailable, so these times "
                            "rest on a single opinion."
                        )
            elif double_check and not model_used:
                notes.append(
                    "No AI was reachable, so there was nothing to double-check."
                )

            status.update(label="Checking every placement against the transcript")
            verdicts = verifier.verify_matches(
                matches, points, segments, video_duration, second_opinion=second
            )
            st.session_state.matches = [v.match for v in verdicts]
            st.session_state.verdicts = verdicts
            st.session_state.notes = notes
            bar.progress(1.0)
            status.update(label="Analysis complete", state="complete", expanded=False)
    except Exception as exc:
        st.error(f"Something went wrong: {exc}")
        with st.expander("Technical details"):
            st.code(traceback.format_exc())

# --------------------------------------------------------------------------
# Review and edit
# --------------------------------------------------------------------------

if st.session_state.verdicts:
    st.subheader("4 · Check the timings")

    verdicts = st.session_state.verdicts
    stats = verifier.summarise(verdicts)

    columns = st.columns(3)
    columns[0].metric("✅ Verified", stats[verifier.VERIFIED])
    columns[1].metric("⚠️ Worth a look", stats[verifier.REVIEW])
    columns[2].metric("⛔ Not found", stats[verifier.REJECTED])

    if stats[verifier.REJECTED]:
        st.error(
            f"{stats[verifier.REJECTED]} point(s) could not be located in the "
            "recording. They are switched off below — turn one on only if you "
            "set its time yourself."
        )
    if stats["total"] and stats["trusted_fraction"] < 0.5:
        st.warning(
            "Fewer than half of your points were verified. That usually means "
            "the outline is worded very differently from how it was taught, or "
            "the audio is hard to hear. Read every row before rendering."
        )

    for note in st.session_state.notes or []:
        st.warning(note)

    duration = (info or {}).get("duration", 0.0)
    frame = pd.DataFrame(
        [
            {
                # Anything that failed verification starts switched OFF, so a
                # bad placement cannot reach the video by inattention.
                "Show": v.verdict != verifier.REJECTED,
                "Status": verifier.VERDICT_LABEL[v.verdict],
                "Type": v.match.type,
                "Header": v.match.header or v.match.category,
                "Point": v.match.text,
                "Start (s)": float(v.match.start_time),
                "End (s)": float(v.match.end_time),
                "On screen": transcriber.format_timestamp(
                    max(v.match.end_time - v.match.start_time, 0)
                ),
                "Timer": bool(v.match.has_timer),
                "Pause": (
                    f"{v.match.timer_duration:.0f}s" if v.match.has_timer else "—"
                ),
                "Score": float(v.score),
                "Heard": v.match.evidence,
                "Why": v.reason_text,
            }
            for v in verdicts
            if v.match.type != "lower_third"
        ]
    )

    edited = st.data_editor(
        frame,
        width="stretch",
        hide_index=True,
        num_rows="fixed",
        column_config={
            "Show": st.column_config.CheckboxColumn("Show", width="small"),
            "Status": st.column_config.TextColumn("Status", disabled=True, width="small"),
            "Type": st.column_config.TextColumn("Type", disabled=True, width="small"),
            "Header": st.column_config.TextColumn(
                "Header", width="small",
                help="The bold line at the top of the card. Edit it freely — "
                     "a scripture reference works just as well as "
                     "\"Principle #1\".",
            ),
            "Point": st.column_config.TextColumn(width="large"),
            "Start (s)": st.column_config.NumberColumn(
                "Start (s)", min_value=0.0, max_value=max(duration, 1.0), step=0.5, format="%.1f"
            ),
            "End (s)": st.column_config.NumberColumn(
                "End (s)", min_value=0.0, max_value=max(duration, 1.0), step=0.5, format="%.1f"
            ),
            "On screen": st.column_config.TextColumn(
                "On screen", disabled=True, width="small"
            ),
            "Timer": st.column_config.CheckboxColumn(
                "Timer", width="small",
                help="Show a countdown during the reflection pause. Ticked "
                     "automatically when a long enough silence was measured "
                     "in the audio; untick to hide the countdown.",
            ),
            "Pause": st.column_config.TextColumn(
                "Pause", disabled=True, width="small",
                help="Length of the silence measured after the question.",
            ),
            "Score": st.column_config.ProgressColumn(
                "Score", min_value=0.0, max_value=1.0, format="%.2f",
                help="Combined result of every check: quote found in the "
                     "transcript, wording overlap, and whether a second model agreed.",
            ),
            "Heard": st.column_config.TextColumn("What the speaker said", disabled=True),
            "Why": st.column_config.TextColumn("Checks", disabled=True, width="medium"),
        },
        key="matches_editor",
    )

    active = edited[edited["Show"].fillna(False).astype(bool)]
    pauses = {
        v.match.text: float(v.match.timer_duration or 0.0) for v in verdicts
    }
    requested = []
    for _, row in active.iterrows():
        start = float(row["Start (s)"])
        end = float(row["End (s)"])
        span = end - start
        wants_timer = bool(row.get("Timer", False))
        pause = pauses.get(str(row["Point"]), 0.0)
        if wants_timer and pause <= 0:
            # Ticked by hand with no measured pause: fall back to the length
            # of the card itself so the countdown still means something.
            pause = max(span, 0.0)
        requested.append(
            editor.Cue(
                text=str(row["Point"]),
                start=start,
                label=str(row.get("Header") or row.get("Type", "")).strip(),
                duration=span if span > 0.05 else float(caption_seconds),
                has_timer=wants_timer,
                timer_duration=pause if wants_timer else 0.0,
            )
        )

    # The real schedule: overlaps trimmed, points matched to the same moment
    # queued one after another, anything past the end of the video dropped.
    cues = editor.schedule_cues(requested, duration, float(caption_seconds))
    lt_start, lt_duration = editor.lower_third_timing(
        [v.match for v in verdicts]
    )
    dropped = len(requested) - len(cues)

    if cues:
        st.caption(
            "On-screen order:  "
            + "   ·   ".join(
                f"{transcriber.format_timestamp(c.start)}–"
                f"{transcriber.format_timestamp(c.start + c.duration)} {c.text[:38]}"
                for c in cues
            )
        )
    if dropped:
        st.warning(
            f"{dropped} point(s) are too close to the end of the recording to "
            "fit on screen and will be left out. Move them earlier to include them."
        )

    with st.expander("👁 Preview how the graphics will look"):
        if cues:
            choices = {
                f"{transcriber.format_timestamp(c.start)} — {c.text[:60]}": index
                for index, c in enumerate(cues)
            }
            picked = st.selectbox("Preview this point", options=list(choices.keys()))
            cue = cues[choices[picked]]
            show_lower_third = st.checkbox(
                "Show the speaker lower third instead", value=False,
                help="A full-screen card covers the picture, so the lower "
                     "third has to be previewed on its own.",
            )
            try:
                image = editor.make_preview_frame(
                    st.session_state.video_path,
                    text="" if show_lower_third else cue.text,
                    label=cue.label,
                    speaker_name=speaker_name if show_lower_third else "",
                    speaker_title=speaker_title if show_lower_third else "",
                    at_time=cue.start + 1.0,
                    card_style=card_style,
                    logo_path=logo_path,
                )
                st.image(image, width="stretch")
            except Exception as exc:
                st.warning(f"Could not build a preview frame: {exc}")

    with st.expander("📝 Full transcript"):
        text = transcriber.segments_to_text(st.session_state.segments or [])
        st.text_area("Transcript", value=text, height=260, label_visibility="collapsed")
        st.download_button("Download transcript (.txt)", text, "transcript.txt", "text/plain")

    st.divider()

    # ----------------------------------------------------------------------
    # Step 3: render
    # ----------------------------------------------------------------------

    st.subheader("5 · Render the finished video")
    # Measured on an Apple silicon laptop at 1080p: the FFmpeg path runs at
    # roughly 20x realtime, MoviePy at roughly 1x.
    rate = 20.0 if render_engine == "auto" else 1.0
    estimate = duration / rate if duration else 0.0
    style_word = "full-screen card" if card_style == "fullscreen" else "caption"
    extras = []
    if speaker_name.strip() or speaker_title.strip():
        extras.append(
            f"the lower third at {transcriber.format_timestamp(lt_start)} for "
            f"{lt_duration:.0f}s"
        )
    if intro_path:
        extras.append(f"a {bookend_seconds}s intro")
    if outro_path:
        extras.append(f"a {bookend_seconds}s outro")
    tail = (", plus " + ", ".join(extras)) if extras else ""
    st.caption(
        f"{len(cues)} {style_word}(s), each on screen for as long as it is "
        f"taught{tail}. "
        f"Estimated render time: about "
        f"{transcriber.format_timestamp(max(estimate, 5))}."
    )

    if not cues:
        st.info("Nothing is switched on to show. Tick at least one point above.")

    if st.button(
        "🎬 Render final video",
        type="primary",
        width="stretch",
        disabled=not cues,
    ):
        try:
            discard_previous_render()
            output_path = os.path.join(workdir(), "bible_study_final.mp4")
            with st.status("Rendering…", expanded=True) as status:
                bar = st.progress(0.0)
                line = st.empty()

                def on_render(fraction, message):
                    bar.progress(min(max(fraction, 0.0), 1.0))
                    line.write(f"{message} {fraction * 100:.0f}%")

                started = time.time()
                editor.render_video(
                    st.session_state.video_path,
                    output_path,
                    speaker_name=speaker_name,
                    speaker_title=speaker_title,
                    cues=cues,
                    cue_duration=float(caption_seconds),
                    lower_third_start=lt_start,
                    lower_third_duration=lt_duration,
                    card_style=card_style,
                    logo_path=logo_path,
                    intro_image_path=intro_path,
                    outro_image_path=outro_path,
                    bookend_duration=float(bookend_seconds),
                    fade=0.25 if soft_transitions else 0.0,
                    preset=quality,
                    threads=int(threads),
                    engine=render_engine,
                    progress_cb=on_render,
                )
                st.session_state.output_path = output_path
                st.session_state.render_seconds = time.time() - started
                bar.progress(1.0)
                status.update(label="Render complete", state="complete", expanded=False)
        except Exception as exc:
            st.error(f"Rendering failed: {exc}")
            with st.expander("Technical details"):
                st.code(traceback.format_exc())

# Streamlit's download button holds the whole file in memory. That is fine for
# a short clip and a bad idea for a two-hour lesson, so past this size the
# finished file is left on disk and its location shown instead.
MAX_INMEMORY_DOWNLOAD_MB = 400

if st.session_state.output_path and os.path.exists(st.session_state.output_path):
    path = st.session_state.output_path
    size_mb = os.path.getsize(path) / (1024 * 1024)
    st.success(
        f"Done in {transcriber.format_timestamp(st.session_state.render_seconds)} — "
        f"{size_mb:.1f} MB"
    )

    if size_mb <= 60:
        st.video(path)

    if size_mb <= MAX_INMEMORY_DOWNLOAD_MB:
        with open(path, "rb") as handle:
            st.download_button(
                "⬇️ Download the finished MP4",
                handle.read(),
                file_name="bible_study_final.mp4",
                mime="video/mp4",
                type="primary",
                width="stretch",
            )
    else:
        # Put a copy next to the source video, where the user will look for it.
        source = st.session_state.video_path or ""
        folder = os.path.dirname(os.path.abspath(source)) if source else ""
        final = ""
        if folder and os.path.isdir(folder) and os.access(folder, os.W_OK):
            stem = os.path.splitext(os.path.basename(source))[0]
            final = os.path.join(folder, f"{stem} - captioned.mp4")
            try:
                if not os.path.exists(final) or os.path.getmtime(final) < os.path.getmtime(path):
                    shutil.copy2(path, final)
            except OSError:
                final = ""
        st.info(
            f"This file is {size_mb:.0f} MB — too big to push through the "
            "browser without the app running out of memory."
        )
        st.code(final or path, language=None)
        st.caption("Open that location in Finder or Explorer to get the video.")
