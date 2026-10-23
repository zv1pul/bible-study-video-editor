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
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

import pandas as pd
import streamlit as st

import control
import selfcheck
import updater
import editor
import matcher
import transcriber
import verifier

# The helper, the video check and reports are extras: if one of them can't
# even load, the rest of the app must carry on exactly as before.
try:
    import assistant
    import reviewer
    EXTRAS = True
except Exception:                                    # pragma: no cover
    EXTRAS = False


def extra_on(name: str) -> bool:
    """An extra is on unless it's missing, or switched off in control.json."""
    if not EXTRAS:
        return False
    try:
        return control.fetch_control().get("settings", {}).get(name, True) is not False
    except Exception:
        return True

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
    "recording_ends": None,
    "silences": None,
    "fetched_path": None,
    "analysed_fingerprint": None,
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
    # bsve_* folders hold overlays, audio chunks and join lists. They are
    # deleted when a run finishes, but a run that is killed part way leaves
    # them behind — forty-odd were found after a day of testing.
    prefixes = (WORKDIR_PREFIX, "bsve_")
    try:
        for name in os.listdir(root):
            if not name.startswith(prefixes):
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
HOSTED_UPLOAD_WARN_MB = 150
HOSTED_UPLOAD_MAX_MB = 200

# Measured headroom needed for the heavy steps. Rendering peaked at ~130 MB
# on a full 1080p lesson; the rest is Streamlit itself and the working copy.
MEMORY_NEEDED_ANALYSE_MB = 250
MEMORY_NEEDED_RENDER_MB = 300


def memory_notice(needed_mb: float, doing: str) -> bool:
    """
    Say plainly whether there is room to do this, before starting it.

    Running out mid-way shows an unexplained error page and loses the work,
    which is the worst possible outcome for somebody who did not ask to think
    about memory. Checking first turns that into a sentence they can act on.
    """
    ok, available = transcriber.memory_headroom_ok(needed_mb)
    if available is None or ok:
        return True
    st.error(
        f"There is not enough free memory on this server to {doing} right now "
        f"({available:.0f} MB free, about {needed_mb:.0f} MB needed).\n\n"
        "This usually means somebody else is using the app at the same time. "
        "Wait a minute and try again — or run the app on your own computer, "
        "where there is no such limit."
    )
    return False

ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def asset_default(name: str) -> str:
    """Artwork kept in assets/ is used whenever nothing was uploaded."""
    for extension in (".png", ".jpg", ".jpeg", ".webp"):
        candidate = os.path.join(ASSET_DIR, name + extension)
        if os.path.exists(candidate):
            return candidate
    return ""


def secret(name: str) -> str:
    """
    Read a key: st.secrets, then the environment, then a key saved on this
    computer, then a group vault (if one is configured), and finally the
    group's shared keys that come with the app — so nobody has to set one up.

    BSVE_NO_AI=1 turns every key off (the automated checks use it, so they
    never spend the group's free allowance).
    """
    if os.environ.get("BSVE_NO_AI"):
        return ""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    if os.environ.get(name):
        return os.environ[name]
    own = control.saved_keys().get(name, "")
    if own:
        return own
    if control.vault_url():
        vaulted = control.shared_keys().get(name, "")
        if vaulted:
            return vaulted
    return control.built_in_keys().get(name, "")


def has_any_key() -> bool:
    return bool(secret("GEMINI_API_KEY") or secret("GROQ_API_KEY"))


def has_own_keys() -> bool:
    """True when keys were configured directly (secrets or environment)."""
    for name in ("GEMINI_API_KEY", "GROQ_API_KEY"):
        try:
            if name in st.secrets:
                return True
        except Exception:
            pass
        if os.environ.get(name):
            return True
    return False


def cues_from_table(edited, plans, discussion_seconds: float, caption_seconds: float):
    """
    Turn the (possibly edited) timings table into render cues.

    `plans` maps a point's text to its analysed Element, which carries the
    things the table does not show — the pause position, the cut, the
    overview's division list. A row's own "Discussion (s)" overrides the
    sidebar's discussion time for that one question.
    """
    active = edited[edited["Show"].fillna(False).astype(bool)]
    cues = []
    for _, row in active.iterrows():
        start = float(row["Start (s)"])
        end = float(row["End (s)"])
        span = end - start
        wants_timer = bool(row.get("Timer", False))
        plan = plans.get(str(row["Point"]))
        own = row.get("Discussion (s)")
        timer_seconds = editor.parse_seconds(own) or discussion_seconds
        cues.append(
            editor.Cue(
                text=str(row["Point"]),
                start=start,
                label=str(row.get("Header") or "").strip(),
                duration=span if span > 0.05 else caption_seconds,
                has_timer=wants_timer,
                timer_duration=timer_seconds if wants_timer else 0.0,
                pause_at=(float(plan.pause_at) if plan and wants_timer else 0.0)
                         or (end if wants_timer else 0.0),
                cut_start=float(plan.cut_start) if plan and wants_timer else 0.0,
                cut_end=float(plan.cut_end) if plan and wants_timer else 0.0,
                items=list(plan.items) if plan else [],
                kind=(plan.type if plan else ""),
            )
        )
    return cues


IDLE_QUIT_MINUTES = 5


@st.cache_resource
def _activity() -> dict:
    """Shared by every page load in this run of the app: how many jobs are going."""
    return {"busy": 0}


@st.cache_resource
def _quit_when_closed() -> bool:
    """
    In the packaged app, stop a few minutes after the last tab is closed.

    The app has no window of its own, so without this it would carry on
    running unseen after somebody closes the browser. Never while a lesson
    is being analysed or rendered — closing the tab mid-render must not
    lose the video.
    """
    if not os.environ.get("BSVE_PACKAGED"):
        return False
    import threading

    def watch():
        try:
            from streamlit.runtime import Runtime
        except Exception:
            return
        idle_since = None
        while True:
            time.sleep(20)
            try:
                viewers = Runtime.instance()._session_mgr.num_active_sessions()
            except Exception:
                return                       # can't tell, so never guess
            if viewers or _activity()["busy"]:
                idle_since = None
                continue
            idle_since = idle_since or time.time()
            if time.time() - idle_since >= IDLE_QUIT_MINUTES * 60:
                try:
                    os.remove(os.path.join(os.environ.get("BSVE_HOME", ""), "running.json"))
                except OSError:
                    pass
                os._exit(0)

    threading.Thread(target=watch, daemon=True, name="quit-when-closed").start()
    return True


_quit_when_closed()


class stay_awake:
    """
    Keep the computer from going to sleep while a lesson is being worked on.

    A full lesson takes several minutes to analyse and render; a laptop that
    dozes off half way would pause the job and leave the person wondering
    why it is taking so long. The screen may still turn off — only sleep is
    held off, and only until the job finishes.
    """

    def __enter__(self):
        self._process = None
        _activity()["busy"] += 1
        try:
            if sys.platform == "darwin":
                self._process = subprocess.Popen(
                    ["caffeinate", "-i", "-w", str(os.getpid())],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            elif sys.platform.startswith("win"):
                import ctypes

                # ES_CONTINUOUS | ES_SYSTEM_REQUIRED, for this working thread.
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        except Exception:
            pass
        return self

    def __exit__(self, *exc):
        _activity()["busy"] = max(_activity()["busy"] - 1, 0)
        try:
            if self._process is not None:
                self._process.terminate()
            elif sys.platform.startswith("win"):
                import ctypes

                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception:
            pass
        return False


VIDEO_EXTENSIONS = ("mp4", "mov", "m4v", "mkv", "webm", "avi", "mts", "3gp")


def windows_picker_command(show: bool = True) -> list:
    """The PowerShell command for File Explorer's own "choose a file" window."""
    patterns = ";".join(f"*.{e}" for e in VIDEO_EXTENSIONS)
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "$owner=New-Object System.Windows.Forms.Form -Property @{TopMost=$true};"
        "$d=New-Object System.Windows.Forms.OpenFileDialog;"
        "$d.Title='Choose the lesson recording';"
        f"$d.Filter='Videos|{patterns}|All files|*.*';"
        + ("if($d.ShowDialog($owner) -eq 'OK'){Write-Output $d.FileName}" if show
           else "Write-Output 'PICKER READY'")
    )
    return ["powershell", "-NoProfile", "-STA", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-Command", script]


def pick_video_file() -> tuple:
    """
    Open the computer's own "choose a file" window. (path, problem).

    The app runs on this computer, so the ordinary Finder / File Explorer
    window can hand over the file where it is — nothing is copied or
    uploaded, whatever its size. Returns ("", "") if the person cancelled.
    """
    try:
        if sys.platform == "darwin":
            script = (
                'tell me to activate\n'
                'POSIX path of (choose file with prompt '
                '"Choose the lesson recording" of type {"public.movie", '
                '"public.audiovisual-content"})'
            )
            result = subprocess.run(["osascript", "-e", script],
                                    capture_output=True, text=True, timeout=1800)
            if result.returncode != 0:
                return "", ""                    # cancelled
            return result.stdout.strip(), ""
        if sys.platform.startswith("win"):
            result = subprocess.run(
                windows_picker_command(), capture_output=True, timeout=1800,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return result.stdout.decode("utf-8", "replace").strip(), ""
    except subprocess.TimeoutExpired:
        return "", ""
    except Exception:
        pass
    return "", ("The file window couldn't be opened here — paste where the "
                "video is in the box below instead.")


def apply_changes(changes: list, records: list) -> None:
    """
    Apply approved changes from the helper or a fix to the plan — never to a
    file. The table's current values (including anything typed into it) are
    taken over first, so nothing the person did by hand is lost, and the
    whole plan is kept so Undo can put it back exactly.
    """
    import copy

    table = [v for v in st.session_state.verdicts or [] if v.match.type != "lower_third"]
    overrides = st.session_state.setdefault("row_overrides", {})
    st.session_state.undo_plan = {
        "matches": [copy.deepcopy(v.match) for v in table],
        "overrides": copy.deepcopy(overrides),
        "ends": st.session_state.get("recording_ends"),
    }
    for i, row in enumerate(records[:len(table)]):
        match = table[i].match
        try:
            match.start_time = float(row.get("Start (s)", match.start_time))
            match.end_time = float(row.get("End (s)", match.end_time))
        except (TypeError, ValueError):
            pass
        match.header = str(row.get("Header") or match.header or "")
        match.has_timer = bool(row.get("Timer", match.has_timer))
        overrides[i] = {"Show": bool(row.get("Show", True)),
                        "Discussion (s)": str(row.get("Discussion (s)") or "")}
    duration = float((st.session_state.video_info or {}).get("duration") or 0.0)
    for change in changes:
        op = change.get("op")
        i = int(change.get("point") or 0) - 1
        match = table[i].match if 0 <= i < len(table) else None
        if op == "move" and match:
            if "start" in change:
                length = max(match.end_time - match.start_time, 1.0)
                match.start_time = change["start"]
                if "end" not in change and match.end_time <= match.start_time:
                    match.end_time = min(match.start_time + length, duration or 1e9)
            if "end" in change and change["end"] > match.start_time:
                match.end_time = change["end"]
        elif op == "show" and match:
            overrides.setdefault(i, {})["Show"] = bool(change["value"])
        elif op == "timer" and match:
            match.has_timer = bool(change["value"])
        elif op == "countdown" and match:
            match.has_timer = True
            overrides.setdefault(i, {})["Discussion (s)"] = str(int(change["seconds"]))
        elif op == "resume" and match:
            pause = match.pause_at or match.end_time
            if change["at"] > pause + 0.5:
                match.cut_start = pause
                match.cut_end = change["at"]
        elif op == "header" and match:
            match.header = change["text"]
        elif op == "trim":
            start, end = st.session_state.get("recording_ends") or (0.0, duration)
            if "start" in change and change["start"] < end:
                start = change["start"]
            if "end" in change and change["end"] > start:
                end = change["end"]
            st.session_state.recording_ends = (start, end)
            st.session_state.pref_trim_ends = True
        elif op == "setting":
            st.session_state[f"pref_{change['name']}"] = change["value"]
    st.session_state.table_rev = st.session_state.get("table_rev", 0) + 1
    st.session_state.pending_changes = []
    st.session_state.changed_since_render = True


def undo_changes() -> None:
    """Put the plan back as it was before the last changes were applied."""
    saved = st.session_state.pop("undo_plan", None)
    if not saved:
        return
    table = [v for v in st.session_state.verdicts or [] if v.match.type != "lower_third"]
    for verdict, match in zip(table, saved["matches"]):
        verdict.match = match
    st.session_state.row_overrides = saved["overrides"]
    st.session_state.recording_ends = saved["ends"]
    st.session_state.table_rev = st.session_state.get("table_rev", 0) + 1


def helper_panel(records: list, duration: float) -> None:
    """Questions and changes in plain words, every change approved first."""
    talking = bool(st.session_state.get("chat") or st.session_state.get("pending_changes")
                   or st.session_state.get("undo_plan"))
    with st.expander("💬 Ask the helper — questions, or changes in plain words",
                     expanded=talking):
        left = assistant.left_today()
        st.caption(
            "Ask anything about this lesson or the app — or ask for a change, like "
            "“show the first principle 5 seconds later” or “make question 2's "
            "countdown 45 seconds”. Nothing changes until you press **Apply**. "
            f"({left} of {assistant.DAILY_LIMIT} questions left today on this computer.)"
        )
        history = st.session_state.setdefault("chat", [])
        for question, answer in history[-6:]:
            with st.chat_message("user"):
                st.write(question)
            with st.chat_message("assistant"):
                st.write(answer)

        pending = st.session_state.get("pending_changes") or []
        if pending:
            with st.container(border=True):
                st.markdown("**Proposed changes**")
                for change in pending:
                    st.markdown(f"- {assistant.describe(change, records)}")
                columns = st.columns(2)
                columns[0].button("✅ Apply", type="primary", width="stretch",
                                  key="apply_changes", on_click=apply_changes,
                                  args=(pending, records))
                columns[1].button("Discard", width="stretch", key="discard_changes",
                                  on_click=lambda: st.session_state.update(pending_changes=[]))
        if st.session_state.get("undo_plan"):
            st.button("↩️ Undo the last changes", key="undo_changes", on_click=undo_changes)

        quick = st.columns(2)
        preset = ""
        if quick[0].button("Look over the timings for problems", width="stretch",
                           key="helper_look"):
            preset = ("Look over the timings and the checks for anything that seems wrong, "
                      "and explain it simply. Don't change anything — just tell me what to "
                      "look at.")
        if quick[1].button("What do the statuses mean?", width="stretch", key="helper_status"):
            preset = "What do the statuses in the timings table mean, and what should I check?"
        with st.form("helper_form", clear_on_submit=True, border=False):
            typed = st.text_input("Your question or change", max_chars=assistant.MAX_QUESTION_CHARS,
                                  label_visibility="collapsed",
                                  placeholder="e.g. Why is the second principle marked worth a look?")
            sent = st.form_submit_button("Send", width="stretch")
        question = preset or (typed if sent else "")
        if question:
            review = st.session_state.get("review") or {}
            findings = [f"{f.level}: {f.title} — {f.detail}"[:240]
                        for f in review.get("findings", []) if f.level != "ok"]
            with st.spinner("Thinking…"):
                reply = assistant.ask(
                    question, rows=records, duration=duration,
                    trims=st.session_state.get("recording_ends") or (0.0, duration),
                    settings={k[5:]: v for k, v in st.session_state.items()
                              if str(k).startswith("pref_") and isinstance(v, (int, float, str, bool))},
                    findings=findings, segments=st.session_state.segments or [],
                    history=history,
                    keys={"GEMINI_API_KEY": secret("GEMINI_API_KEY"),
                          "GROQ_API_KEY": secret("GROQ_API_KEY")},
                )
            if reply.problem:
                st.warning(reply.problem)
            else:
                history.append((question, reply.answer))
                st.session_state.pending_changes = reply.changes
                st.rerun()


def review_panel(path: str) -> None:
    """Check the finished video, and offer the fixes it knows are safe."""
    record = st.session_state.get("render_record") or {}
    if record.get("output_path") != path:
        return
    st.subheader("6 · Check the finished video")
    review = st.session_state.get("review")
    if not review or review.get("path") != path:
        with st.status("Watching and listening to the finished video…", expanded=False) as box:
            plan = reviewer.Plan(**record)
            with stay_awake():
                findings = reviewer.review(plan, st.session_state.segments or [],
                                           progress_cb=lambda m: box.update(label=m))
            review = {"path": path, "findings": findings}
            st.session_state.review = review
            box.update(label="Checked the finished video", state="complete")
    findings = review["findings"]
    problems = [f for f in findings if f.level != reviewer.OK]
    (st.success if not problems else st.warning)(reviewer.summary(findings))
    for index, finding in enumerate(problems):
        with st.container(border=True):
            where = (f" · at {transcriber.format_timestamp(finding.at)} in the video"
                     if finding.at >= 0 else "")
            st.markdown(f"{reviewer.LEVEL_ICON[finding.level]} **{finding.title}**{where}")
            if finding.detail:
                st.caption(finding.detail)
            if finding.fix and finding.fix.get("row") is not None:
                change = {"op": finding.fix["op"], "point": finding.fix["row"] + 1,
                          "at": finding.fix["at"]}
                st.button(f"🔧 {finding.fix_label}", key=f"fix_{index}",
                          on_click=apply_changes,
                          args=([change], st.session_state.get("last_rows") or []))
    with st.expander(f"Everything that checked out ({len(findings) - len(problems)})"):
        for finding in findings:
            if finding.level == reviewer.OK:
                st.caption(f"✅ {finding.title}" + (f" — {finding.detail}" if finding.detail else ""))
    if st.session_state.get("changed_since_render"):
        st.info("The changes are in the timings above. Press **Render final video** again — the "
                "new video is saved alongside this one, so nothing is lost.")


def report_panel() -> None:
    """A file to send when something goes wrong — never the video or a key."""
    with st.expander("🐞 Report a problem"):
        st.caption(
            "Makes a small file describing what happened — the settings, the outline "
            "and timings, the checks and any error. It never includes the video or "
            "any key. Send it to whoever gave you the app."
        )
        include = st.checkbox("Include the transcript (helps find the problem faster)",
                              value=True, key="report_transcript")
        if st.button("Make the report file", width="stretch", key="make_report"):
            try:
                review = st.session_state.get("review") or {}
                info = st.session_state.get("video_info") or {}
                state = {
                    "recording": {k: info.get(k) for k in ("duration", "size", "fps", "has_audio")},
                    "outline": {str(k): v for k, v in st.session_state.items()
                                if str(k) in ("takeaway_box", "divisions_box", "lesson_title_box")
                                or str(k).startswith(("principles_", "applications_"))},
                    "timings": st.session_state.get("last_rows") or [],
                    "trims": st.session_state.get("recording_ends"),
                    "notes": st.session_state.get("notes") or [],
                    "checks": [f"{f.level}: {f.title} — {f.detail}" for f in review.get("findings", [])],
                    "helper": st.session_state.get("chat", [])[-10:],
                    "error": st.session_state.get("last_error", ""),
                    "transcript": transcriber.segments_to_text(st.session_state.get("segments") or []),
                }
                folder = os.environ.get("BSVE_REPORT_DIR") or \
                    os.path.join(os.path.expanduser("~"), "Desktop")
                try:
                    os.makedirs(folder, exist_ok=True)
                except OSError:
                    pass
                if not os.path.isdir(folder) or not os.access(folder, os.W_OK):
                    folder = os.path.expanduser("~")
                st.session_state.report_path = assistant.make_report(
                    folder, include_transcript=include, state=state)
            except Exception as exc:
                st.error(f"Couldn't make the report: {exc}")
        made = st.session_state.get("report_path")
        if made and os.path.exists(made):
            st.success(f"Saved as **{os.path.basename(made)}** in "
                       f"{os.path.basename(os.path.dirname(made)) or 'your home folder'}")
            address = str(control.fetch_control().get("report_email") or "").strip()
            if address:
                from urllib.parse import quote

                st.markdown(
                    f"[✉️ Write the email]({'mailto:' + address + '?subject=' + quote('Video editor problem') + '&body=' + quote('The report file is attached. What happened: ')})"
                    " — then attach the file.")
            if sys.platform == "darwin" and st.button("Show it in Finder", key="report_reveal"):
                subprocess.run(["open", "-R", made], check=False)
            elif sys.platform.startswith("win") and st.button("Show it in Explorer",
                                                              key="report_reveal"):
                subprocess.run(["explorer", "/select,", made], check=False)


# --------------------------------------------------------------------------
# Sidebar: settings
# --------------------------------------------------------------------------
#
# Everything here is remembered on this computer: change a setting once and
# it stays that way for every video until it is changed again.

PREF_CHOICES = {
    "quality": ["small", "balanced", "best"],
    "card_style": ["fullscreen", "caption"],
    "model_size": transcriber.LOCAL_MODEL_SIZES,
}
PREF_RANGES = {
    "discussion_seconds": (10, 300),
    "bookend_seconds": (2, 15),
    "caption_seconds": (3, 20),
    "threads": (1, 16),
}
PREF_DEFAULTS = {
    "discussion_seconds": int(matcher.APPLICATION_PAUSE_SECONDS),
    "quality": "small" if HOSTED else "balanced",
    "trim_ends": True,
    "provider": "gemini",
    "llm_model": "auto",
    "engine": "local",
    "render_engine": "auto",
    "bookend_seconds": 5,
    "card_style": "fullscreen",
    "overview_card": True,
    "soft_transitions": False,
    "caption_seconds": 8,
    "double_check": True,
    "model_size": "base",
    "language": "en",
    "threads": max(1, min(os.cpu_count() or 4, 16)),
}


def remembered(name: str, options: list = None) -> str:
    """
    The widget key for a remembered setting, started from what was saved.

    A saved value that no longer fits (an option renamed, a range narrowed
    in an update) falls back to the default instead of breaking the page.
    """
    key = f"pref_{name}"
    if key not in st.session_state:
        value = control.prefs().get(name, PREF_DEFAULTS[name])
        default = PREF_DEFAULTS[name]
        choices = options if options is not None else PREF_CHOICES.get(name)
        if choices is not None and value not in choices:
            value = default if default in choices else choices[0]
        elif name in PREF_RANGES:
            low, high = PREF_RANGES[name]
            try:
                value = int(min(max(int(value), low), high))
            except (TypeError, ValueError):
                value = default
        elif isinstance(default, bool):
            value = bool(value)
        elif not isinstance(value, type(default)):
            value = default
        st.session_state[key] = value
    return key


def save_prefs() -> None:
    for name in PREF_DEFAULTS:
        key = f"pref_{name}"
        if key in st.session_state:
            control.set_pref(name, st.session_state[key])


def artwork_in_use(name: str) -> str:
    """The slide or logo a video will use (see control.artwork_choice)."""
    return control.artwork_choice(name, asset_default(name))


def artwork_editor(name: str, label: str, types: list, where: str = "") -> None:
    """Show a slide; replace it for good, put the original back, or remove it."""
    own = control.saved_artwork(name)
    built_in = asset_default(name)
    current = artwork_in_use(name)
    if current:
        st.image(current, caption=f"{label}" + (" — your own" if own else ""),
                 width="stretch")
    else:
        st.caption(f"No {label.lower()}{where}.")
    round_key = f"{name}_upload_round"
    upload = st.file_uploader(
        f"Replace the {label.lower()}" if current else f"Add a {label.lower()}",
        type=types,
        key=f"{name}_upload_{st.session_state.get(round_key, 0)}",
        help="Saved on this computer and used for every video — and kept "
             "through updates — until you change it again. A PDF works too "
             "(its first page). Any shape is fitted to a widescreen frame.",
    )
    if upload is not None:
        temporary = os.path.join(workdir(), f"upload_{name}{os.path.splitext(upload.name)[1]}")
        with open(temporary, "wb") as handle:
            handle.write(upload.getbuffer())
        try:
            control.save_artwork(name, temporary)
            st.session_state[round_key] = st.session_state.get(round_key, 0) + 1
            st.session_state.flash = (
                f"New {label.lower()} saved — every video will use it until "
                "you change it."
            )
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
        finally:
            try:
                os.remove(temporary)
            except OSError:
                pass

    def _restore():
        control.forget_artwork(name)
        control.set_artwork_off(name, False)

    actions = []
    if own and built_in:
        actions.append(("Put back the original", _restore, f"{name}_reset"))
    if current:
        actions.append(("Remove", lambda: control.remove_artwork(name), f"{name}_remove"))
    elif built_in:
        actions.append((f"Use the original {label.lower()}", _restore, f"{name}_reset"))
    if actions:
        for column, (text, action, key) in zip(st.columns(len(actions)), actions):
            column.button(text, key=key, on_click=action, width="stretch")


with st.sidebar:
    if st.session_state.get("flash"):
        # A pop-up, so it's seen wherever on the page the button was.
        st.toast(st.session_state.pop("flash"), icon="✅")

    st.header("⚙️ Settings")
    st.caption("Remembered on this computer until you change them.")

    discussion_seconds = st.slider(
        "Discussion time after each application (seconds)", 10, 300, step=5,
        key=remembered("discussion_seconds"),
        help="A countdown of this length follows every application question. "
             "The speaker's own wait in the recording, however long, is cut "
             "out and replaced by it, so the video moves straight on to where "
             "they resume. To give one question a different time, change its "
             "'Discussion (s)' in the timings table after analysing.",
    )
    quality = st.selectbox(
        "Video quality",
        options=PREF_CHOICES["quality"],
        key=remembered("quality"),
        format_func=lambda key: {
            "small": "Smaller file — quickest to share",
            "balanced": "Balanced (recommended)",
            "best": "Best quality — largest file",
        }[key],
        help="Measured on a 23-minute lesson: smaller ≈ 156 MB, balanced "
             "≈ 224 MB, best ≈ 400 MB. Balanced is a good teaching video; "
             "smaller is barely distinguishable and much easier to send.",
    )
    trim_ends = st.checkbox(
        "Trim the setup and the walk-off",
        key=remembered("trim_ends"),
        help="Starts the video on the teacher's first sentence — not the "
             "seconds of setting up the camera — and ends it just after the "
             "closing prayer and any sign-off, before they get up.",
    )

    st.subheader("🖼 Opening and closing slides")
    # Before 1.2.1 one switch turned both slides off; carry that choice over.
    if control.prefs().get("use_bookends") is False and \
            "intro_off" not in control.prefs():
        control.set_artwork_off("intro", True)
        control.set_artwork_off("outro", True)
    bookend_seconds = st.slider(
        "Seconds each is shown", 2, 15, key=remembered("bookend_seconds"),
    )
    artwork_editor("intro", "Opening slide", editor.SLIDE_TYPES,
                   " — videos start straight on the recording")
    artwork_editor("outro", "Closing slide", editor.SLIDE_TYPES,
                   " — videos end on the recording")

    with st.expander("More settings", expanded=False):
        st.markdown("**Cards**")
        card_style = st.radio(
            "Point style",
            options=PREF_CHOICES["card_style"],
            key=remembered("card_style"),
            format_func=lambda key: {
                "fullscreen": "Full-screen cards (template)",
                "caption": "Caption over the video",
            }[key],
            help="Full-screen cards fill the frame with the beige template while "
                 "the teaching continues underneath. Captions keep the picture "
                 "visible and put a small card along the bottom.",
        )
        overview_card = st.checkbox(
            "Show the takeaway and divisions together on one card",
            key=remembered("overview_card"),
            help="Shown from just before the takeaway is spoken until the first "
                 "division is introduced — long enough to be written down. The "
                 "divisions then each get their own card when introduced.",
        )
        soft_transitions = st.checkbox(
            "Soften the cuts", key=remembered("soft_transitions"),
            help="Off matches the reference style: graphics cut hard in and hard "
                 "out. On gives a quarter-second dissolve.",
        )
        caption_seconds = st.slider(
            "Fallback seconds on screen", 3, 20, key=remembered("caption_seconds"),
            help="Only used when a point has no end time of its own. Normally "
                 "each card stays up for as long as the point is being taught, "
                 "with a floor so it can be written down.",
        )
        st.markdown("**Logo on each card**")
        artwork_editor("logo", "Logo", ["png", "jpg", "jpeg", "webp"], " on the cards")

        st.divider()
        st.markdown("**Listening to the recording**")
        # A shared container has neither the memory to hold a speech model nor the
        # processor to spare, so hosted deployments always use the hosted service.
        if HOSTED or not transcriber.LOCAL_AVAILABLE:
            engine_options = ["groq"]
            st.caption(
                "Transcription runs through Groq here. Running the app on your "
                "own computer adds an offline option."
            )
        else:
            engine_options = ["local", "groq"]
        engine = st.radio(
            "Engine",
            options=engine_options,
            key=remembered("engine", engine_options),
            format_func=lambda key: {
                "local": "On this computer (private, works offline)",
                "groq": "Online (Groq — faster)",
            }[key],
            help="On this computer keeps the recording private and works "
                 "offline. Online is faster on an older computer.",
        )
        if engine == "local":
            model_size = st.select_slider(
                "Accuracy vs speed", options=PREF_CHOICES["model_size"],
                key=remembered("model_size"),
                help="'base' is a good balance. 'small' is noticeably better on "
                     "echoey room audio but roughly 2-3x slower.",
            )
        else:
            model_size = "base"
        language = st.text_input("Spoken language code", max_chars=5,
                                 key=remembered("language"))

        st.divider()
        st.markdown("**Finding each point (AI)**")
        provider = st.selectbox(
            "Service",
            options=list(matcher.PROVIDERS.keys()),
            key=remembered("provider", list(matcher.PROVIDERS.keys())),
            format_func=lambda key: matcher.PROVIDERS[key]["label"],
            help="Both are free. If one is busy or used up for the day, the "
                 "app switches to the other automatically.",
        )
        provider_info = matcher.PROVIDERS[provider]
        backup_provider = "groq" if provider == "gemini" else "gemini"
        _model_options = matcher.model_options(provider)
        _model_key = remembered("llm_model", _model_options)
        if st.session_state[_model_key] not in _model_options:
            st.session_state[_model_key] = _model_options[0]      # the service changed
        llm_model = st.selectbox(
            "Model",
            options=_model_options,
            key=_model_key,
            help="'auto' tries the best model first and moves down the list if one "
                 "is retired, rate limited or overloaded.",
        )
        double_check = st.checkbox(
            "Double-check with a second model", key=remembered("double_check"),
            help="Runs the matching twice using two different models and compares "
                 "the answers. Points both models agree on are marked verified.",
        )
        st.markdown("**AI keys**")
        _own = control.saved_keys()
        if has_own_keys():
            st.caption("🔑 Keys are set by whoever runs this copy.")
        elif _own.get("GEMINI_API_KEY") or _own.get("GROQ_API_KEY"):
            st.caption("🔑 Using your own key, saved on this computer.")
        elif control.built_in_keys() or control.shared_keys():
            st.caption("🔑 Using the group's shared keys — nothing to set up.")
        else:
            st.caption("No AI key available — points are matched by keywords only.")
        _replacement = st.text_input(
            "Use my own Google key instead (optional)", type="password",
            help=f"Only needed if the shared keys are busy every day. Free from "
                 f"{control.GEMINI_KEY_PAGE}",
        )
        _kc = st.columns(2)
        if _kc[0].button("Save my key", width="stretch", disabled=not _replacement):
            with st.spinner("Checking the key with Google…"):
                _ok, _note = control.check_gemini_key(_replacement)
            if _ok:
                control.save_key("GEMINI_API_KEY", _replacement)
                st.session_state.flash = "Your key is saved and will be used from now on."
                st.rerun()
            else:
                st.error(_note)
        if _kc[1].button("Use shared keys", width="stretch",
                         disabled=not (_own.get("GEMINI_API_KEY") or _own.get("GROQ_API_KEY"))):
            control.forget_saved_keys()
            st.rerun()
        if control.vault_url() and not has_own_keys():
            _new_code = st.text_input(
                "Group code", value=control.group_code(), type="password",
                help="Connects this copy to the group's key vault.",
            )
            if st.button("Reconnect", width="stretch"):
                control.set_group_code(_new_code)
                _ok, _, _note = control.connect(_new_code)
                (st.success if _ok else st.error)(_note)

        # --- what is left of today's free allowance ----------------------
        rows = matcher.remaining_today(provider)
        total_used = sum(u for _, u, _ in rows)
        total_cap = sum(c for _, _, c in rows)
        st.progress(
            min(total_used / max(total_cap, 1), 1.0),
            text=f"This computer: {total_used} of {total_cap} free requests used today",
        )

        st.divider()
        st.markdown("**This computer**")
        threads = st.slider("Processor threads for rendering", 1, 16,
                            key=remembered("threads"))
        render_engine = st.radio(
            "Rendering engine",
            options=["auto", "moviepy"],
            key=remembered("render_engine", ["auto", "moviepy"]),
            format_func=lambda key: {
                "auto": "Fast (recommended)",
                "moviepy": "Compatible (slower)",
            }[key],
            help="Fast hands the whole job to FFmpeg — about 20x quicker on a "
                 "full-length lesson. Switch to Compatible only if a "
                 "particular file refuses to render.",
        )
        st.caption(f"Overlay font: {editor.font_report()}")

    save_prefs()

    # The keys actually used: a key typed for this session beats everything.
    llm_api_key = secret("GEMINI_API_KEY" if provider == "gemini" else "GROQ_API_KEY")
    backup_key = secret("GROQ_API_KEY" if backup_provider == "groq" else "GEMINI_API_KEY")
    groq_key = secret("GROQ_API_KEY") if engine == "groq" else ""

    st.divider()
    st.subheader("Help")
    st.caption(f"Bible Study Video Editor · version {control.VERSION}")
    if st.button("Check for updates", width="stretch"):
        control.fetch_control(force=True)
        _now = control.update_status()
        st.session_state.flash = (
            f"Version {_now['latest']} is available — see the top of the page."
            if _now["update_available"] else "You have the latest version."
        )
        st.rerun()
    if st.button("Check this computer", width="stretch",
                 help="Runs every accuracy check and a short test render to "
                      "confirm this copy works here. About half a minute."):
        st.session_state.run_self_check = True
    if os.environ.get("BSVE_LAUNCHER_PID") and st.button(
        "Quit the app", width="stretch",
        help="Stops the app on this computer. Open it again any time.",
    ):
        st.success("The app has stopped. You can close this tab.")
        import signal as _signal

        try:
            os.remove(os.path.join(os.environ.get("BSVE_HOME", ""), "running.json"))
        except OSError:
            pass
        try:
            os.kill(int(os.environ["BSVE_LAUNCHER_PID"]), _signal.SIGTERM)
        except Exception:
            pass
        os._exit(0)

    if extra_on("report"):
        try:
            report_panel()
        except Exception:
            pass

    with st.expander("Privacy"):
        _reporting = st.toggle(
            "Send anonymous usage statistics",
            value=control.telemetry_enabled(),
            help="Helps whoever maintains this see which versions are in use and "
                 "whether renders are succeeding.",
        )
        if _reporting != control.telemetry_enabled():
            control.set_telemetry(_reporting)
        st.caption(
            "Sent, if switched on: a random identifier for this installation, "
            "the version, Mac or Windows, what happened (a lesson analysed, a "
            "render finished or failed), how long the lesson was and how long "
            "it took.\n\n"
            "**Never sent:** the video, the audio, the transcript, your "
            "outline, your keys, or any file name or path. The recording is "
            "listened to on this computer; only the transcript text goes to "
            "the AI service to find each point."
        )

    _available = transcriber.available_memory_mb()
    if _available is not None:
        st.divider()
        st.subheader("This server")
        st.progress(
            max(0.0, min(1.0, 1 - _available / 1024.0)),
            text=f"{_available:.0f} MB free of about 1024 MB",
        )
        if _available < MEMORY_NEEDED_RENDER_MB:
            st.warning(
                "Memory is low, most likely because somebody else is using "
                "the app. Give it a minute."
            )

# --------------------------------------------------------------------------
# Main form
# --------------------------------------------------------------------------

st.title("📖 Bible Study Video Editor")
st.write(
    "Choose the recording, pick the speaker, paste in the lesson outline — "
    "the app finds when each point is taught and builds the finished video "
    "with the speaker's name, a card for every point, discussion countdowns "
    "and your opening and closing slides."
)

_status = control.update_status()

if _status["message"]:
    {"warning": st.warning, "error": st.error}.get(
        _status["message_kind"], st.info
    )(_status["message"])

if _status["too_old"]:
    st.error(
        f"This copy is version {_status['version']}, and version "
        f"{_status['latest']} is required. Update before carrying on — "
        "**Check for updates** in the sidebar."
    )
elif _status["update_available"]:
    _packaged = os.environ.get("BSVE_PACKAGED") == "1"
    _ready = _packaged and updater.staged_version() == _status["latest"]
    _download = _packaged and updater.pending_download()
    with st.container(border=True):
        if _ready:
            st.markdown(
                f"**Version {_status['latest']} is installed and checked.** Quit "
                "the app and open it again to start using it."
                + (f"  \n{_status['notes']}" if _status["notes"] else "")
            )
        elif _download:
            st.markdown(
                f"**Version {_status['latest']} needs a new download** — it "
                "includes changes this copy can't take as an update. "
                f"[Get it here](https://github.com/{control.REPO}/releases/latest), "
                "then replace this app with it. Your settings are kept."
            )
        else:
            columns = st.columns([4, 1])
            columns[0].markdown(
                f"**Version {_status['latest']} is available** — you have "
                f"{_status['version']}."
                + (f"  \n{_status['notes']}" if _status["notes"] else "")
            )
            if columns[1].button("Update now", width="stretch"):
                with st.spinner("Downloading the update and checking it on this "
                                "computer — about a minute…"):
                    if _packaged:
                        ok, note = updater.stage(force=True)
                    else:
                        ok, note = control.apply_update()
                (st.success if ok else st.error)(note)

# The first time each version runs — and whenever asked — check that it
# works on this computer before anyone trusts it with a real lesson.
if not HOSTED and not os.environ.get("BSVE_SKIP_AUTO_SELFCHECK") and (
    st.session_state.pop("run_self_check", False)
    or control.settings().get("self_check_version") != control.VERSION
):
    with st.status("Checking this copy works on this computer…", expanded=True) as _check:
        _results = selfcheck.run(lambda name: _check.update(label=f"Checking: {name}…"))
        for _name, _passed, _detail in _results:
            st.markdown(f"{'✅' if _passed else '❌'} **{_name}** — {_detail}")
        if selfcheck.all_passed(_results):
            control.set_setting("self_check_version", control.VERSION)
            control.set_setting("self_check_problems", [])
            _check.update(label="This copy works on this computer.", state="complete",
                          expanded=False)
        else:
            control.set_setting("self_check_problems",
                                [f"{n}: {d}" for n, p, d in _results if not p])
            _check.update(label="Something on this computer needs attention.",
                          state="error", expanded=True)

if control.settings().get("self_check_problems"):
    st.error(
        "The last check of this computer found a problem, so a video made now "
        "may not come out right:\n\n"
        + "\n".join(f"- {p}" for p in control.settings()["self_check_problems"])
        + "\n\nFix it and press **Check this computer** in the sidebar."
    )

# First run: no AI key anywhere yet. One box, a minute's work, and never again.
if not has_any_key() and not st.session_state.get("skip_key_setup") \
        and not os.environ.get("BSVE_NO_AI"):
    with st.container(border=True):
        st.markdown("**One-time setup — connect the AI that finds each point**")
        _ways = ["Use my own free Google key"]
        if control.vault_url():
            _ways.insert(0, "I have a group code")
        _way = st.radio("How", _ways, horizontal=True, label_visibility="collapsed")

        if _way == "I have a group code":
            st.caption("Ask whoever gave you the app for the code.")
            _cols = st.columns([3, 1])
            _code = _cols[0].text_input(
                "Group code", value=control.group_code(), type="password",
                label_visibility="collapsed", placeholder="Group code",
            )
            if _cols[1].button("Connect", width="stretch", type="primary"):
                control.set_group_code(_code)
                _ok, _, _note = control.connect(_code)
                if _ok:
                    st.success(_note)
                    st.rerun()
                else:
                    st.error(_note)
        else:
            st.markdown(
                f"1. Open **[Google AI Studio]({control.GEMINI_KEY_PAGE})** and sign in "
                "with any Google account.\n"
                "2. Click **Create API key** and copy it.\n"
                "3. Paste it here. It's free, needs no card, and is saved only on "
                "this computer."
            )
            _cols = st.columns([3, 1])
            _key = _cols[0].text_input(
                "Google AI key", type="password", label_visibility="collapsed",
                placeholder="Paste the key here",
            )
            if _cols[1].button("Save", width="stretch", type="primary"):
                with st.spinner("Checking the key with Google…"):
                    _ok, _note = control.check_gemini_key(_key)
                if _ok:
                    control.save_key("GEMINI_API_KEY", _key)
                    st.success(_note)
                    st.rerun()
                else:
                    st.error(_note)

        if st.button("Skip for now"):
            st.session_state.skip_key_setup = True
            st.rerun()
        st.stop()

if not has_any_key() and not os.environ.get("BSVE_NO_AI"):
    st.warning(
        "No AI key is connected, so points are matched by keywords only — much "
        "less accurate. Add one under **More settings → AI keys** in the "
        "sidebar."
    )

if HOSTED:
    st.info(
        "**Before you start — put the recording in Google Drive and paste the "
        "link below.**\n\n"
        "Share it so that *anyone with the link* can view it. Pasting a link "
        "works for a lesson of any length. Uploading a file is limited to "
        f"{HOSTED_UPLOAD_MAX_MB} MB here, because this shared server has to "
        "hold an upload in memory the whole time."
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
            "path": "On this computer",
        }[key],
        horizontal=True,
        help=(
            "A link is fetched straight to disk, so a large recording will "
            "not exhaust this shared server. An upload is held in memory the "
            "whole time."
            if HOSTED else
            "On this computer reads the file where it is — no copying, no "
            "size limit, and it starts straight away."
        ),
    )

    uploaded = None
    local_path = ""
    link_url = ""

    if source == "link":
        link_url = st.text_input(
            "Link to the video",
            key="link_box",
            placeholder="https://drive.google.com/file/d/…/view",
            help="Google Drive and Dropbox links work. In Drive, set sharing "
                 "to 'Anyone with the link' first.",
        ).strip()
        if link_url and st.button("Fetch this recording", width="stretch"):
            try:
                target = os.path.join(workdir(), "source_from_link.mp4")
                bar = st.progress(0.0)
                line = st.empty()
                target = transcriber.download_video(
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
            key="video_upload",
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
                "full-length lesson over about 1 GB, choose *On this "
                "computer* instead — it reads straight from disk, starts "
                "immediately and has no size limit."
            )
    else:
        if st.button("📂  Choose the video…", width="stretch", type="primary"
                     if not st.session_state.get("local_path_box") else "secondary"):
            with st.spinner("Waiting for you to pick the file in the window that "
                            "just opened…"):
                _picked, _problem = pick_video_file()
            if _picked:
                st.session_state.local_path_box = _picked
                st.rerun()
            elif _problem:
                st.warning(_problem)
        local_path = st.text_input(
            "Or paste where it is",
            key="local_path_box",
            placeholder=(r"C:\Users\you\Videos\lesson.mp4" if sys.platform.startswith("win")
                         else "/Users/you/Movies/lesson.mp4"),
            help=("In File Explorer: hold Shift, right-click the file, choose "
                  "'Copy as path', then paste it here."
                  if sys.platform.startswith("win") else
                  "In Finder: right-click the file, hold Option, choose "
                  "'Copy … as Pathname', then paste it here."),
        ).strip().strip('"').strip("'")
        if local_path and not os.path.exists(local_path):
            st.error("No file at that location. Choose it again with the button above.")
        elif local_path:
            st.caption(f"🎞 **{os.path.basename(local_path)}**")

    st.subheader("2 · The speaker")
    NEW_SPEAKER = "— Someone new —"
    _known = control.speakers()
    _names = [entry["name"] for entry in _known]
    if st.session_state.get("speaker_pick", NEW_SPEAKER) not in [NEW_SPEAKER] + _names:
        st.session_state.speaker_pick = NEW_SPEAKER     # removed elsewhere since

    def _fill_speaker():
        picked = st.session_state.get("speaker_pick")
        entry = next((e for e in control.speakers() if e["name"] == picked), None)
        st.session_state.speaker_name_box = entry["name"] if entry else ""
        st.session_state.speaker_title_box = entry["title"] if entry else ""

    def _speaker_label(name: str) -> str:
        entry = next((e for e in _known if e["name"] == name), None)
        if not entry:
            return name
        return f"{entry['name']} — {entry['title']}" if entry["title"] else entry["name"]

    if _known:
        st.selectbox(
            "Preset speakers",
            options=[NEW_SPEAKER] + _names,
            key="speaker_pick",
            format_func=_speaker_label,
            on_change=_fill_speaker,
            help="Fills in the name and title. You can still change either "
                 "box afterwards — for this video only, or press Update "
                 "preset to keep the change.",
        )
    speaker_name = st.text_input("Name", placeholder="Pastor John Smith",
                                 key="speaker_name_box")
    speaker_title = st.text_input("Title", placeholder="Teaching Leader",
                                  key="speaker_title_box")

    _picked = next((e for e in _known
                    if e["name"] == st.session_state.get("speaker_pick")), None)
    _typed_name, _typed_title = speaker_name.strip(), speaker_title.strip()
    _same_name = next((e for e in _known
                       if e["name"].lower() == _typed_name.lower()), None)

    def _save_preset():
        control.remember_speaker(_typed_name, _typed_title)
        st.session_state.speaker_pick = _typed_name
        st.session_state.flash = f"{_typed_name} is saved as a preset speaker."

    def _update_preset():
        if _picked and _picked["name"].lower() != _typed_name.lower():
            control.forget_speaker(_picked["name"])
        control.remember_speaker(_typed_name, _typed_title)
        st.session_state.speaker_pick = _typed_name
        st.session_state.flash = f"Preset updated: {_typed_name}."

    def _remove_preset():
        control.forget_speaker(_picked["name"])
        st.session_state.speaker_pick = NEW_SPEAKER
        st.session_state.speaker_name_box = ""
        st.session_state.speaker_title_box = ""

    _buttons = []
    _renamed = bool(_picked and _typed_name
                    and _picked["name"].lower() != _typed_name.lower())
    if _renamed and not _same_name:
        # A different person typed over a preset: keep both choices open,
        # and never replace somebody by accident.
        _buttons.append(("⭐ Save as a new preset", _save_preset, "save_preset"))
        _buttons.append((f"💾 Replace {_picked['name'].split()[0]}", _update_preset,
                         "update_preset"))
    elif _picked and _typed_name and not _renamed and (
            _picked["name"] != _typed_name or _picked["title"] != _typed_title):
        _buttons.append(("💾 Update preset", _update_preset, "update_preset"))
    elif _typed_name and not _same_name:
        _buttons.append(("⭐ Save as a preset", _save_preset, "save_preset"))
    elif _same_name and not _picked and _same_name["title"] != _typed_title:
        _picked = _same_name
        _buttons.append(("💾 Update preset", _update_preset, "update_preset"))
    if _picked and st.session_state.get("speaker_pick") == _picked["name"] and not _renamed:
        _buttons.append(("Remove from presets", _remove_preset, "remove_preset"))
    if _buttons:
        for _column, (_label, _action, _key) in zip(st.columns(len(_buttons)), _buttons):
            _column.button(_label, key=_key, on_click=_action, width="stretch")

with right:
    st.subheader("3 · The lesson outline")

    def _fill_from_paste():
        found = matcher.read_outline(st.session_state.get("paste_box", ""))
        if not (found["takeaway"] or found["divisions"]):
            st.session_state.flash = ("Couldn't find a takeaway or divisions in that text — "
                                      "fill in the boxes below by hand.")
            return
        if found["title"]:
            st.session_state.lesson_title_box = found["title"]
        st.session_state.takeaway_box = found["takeaway"]
        st.session_state.divisions_box = "\n".join(d["title"] for d in found["divisions"])
        for key in [k for k in st.session_state.keys()
                    if str(k).startswith(("principles_", "applications_"))]:
            st.session_state[key] = ""
        for index, item in enumerate(found["divisions"], start=1):
            st.session_state[f"principles_{index}"] = item["principles"]
            st.session_state[f"applications_{index}"] = item["applications"]
        st.session_state.paste_box = ""
        st.session_state.flash = (
            f"Filled in {len(found['divisions'])} division(s) — check the boxes below.")

    with st.expander("📋 Paste the whole outline at once", expanded=False):
        st.text_area(
            "Outline", key="paste_box", height=200, label_visibility="collapsed",
            placeholder="TAKEAWAY\nGrace precedes obedience.\n\nDIVISION 1 — The Rescue "
                        "(Exodus 14)\nPrinciple\nGod fights for His people.\nApplication "
                        "Question\nWhere do you need to stand still?",
            help="Paste the outline as it was sent to you — the takeaway, the "
                 "divisions, their principles and questions are sorted into the "
                 "boxes below, where you can check and change them.",
        )
        st.button("Fill in the boxes below", on_click=_fill_from_paste, width="stretch",
                  disabled=not st.session_state.get("paste_box", "").strip())

    lesson_title = st.text_input(
        "Lesson title (optional)", key="lesson_title_box",
        placeholder="Romans 3:21-26 - Lesson 5",
        help="Names the finished video file. Left empty, it's named after the recording.",
    )
    st.caption(
        "Enter the divisions first. A section then appears under each one for "
        "the principles and applications that belong to it."
    )

    takeaway = st.text_area(
        "Takeaway",
        key="takeaway_box",
        height=80,
        placeholder="God seeks sincere relationship, not hollow religion.",
        help="The one thing the lesson is for. Usually stated near the start.",
    )

    divisions_raw = st.text_area(
        "Divisions — one per line",
        key="divisions_box",
        height=110,
        placeholder="I. Man-initiated Religion (Zechariah 7)\n"
                    "II. God-Initiated Relationship (Zechariah 8)",
        help="Type each division on its own line. A scripture reference on the "
             "line under a division (\"Romans 1:1–7\") is added to that "
             "division. Sections for its principles and applications appear "
             "below as you add them.",
    )
    division_titles = matcher.split_divisions(divisions_raw)

    divisions: list = []
    if not division_titles:
        st.info(
            "Add a division above and its own principles and applications "
            "will appear here."
        )
    for index, title in enumerate(division_titles, start=1):
        with st.container(border=True):
            st.markdown(f"**{title}**")
            principles = st.text_area(
                "Principles",
                height=90,
                key=f"principles_{index}",
                placeholder="Religion is deceptively tempting, but neither "
                            "saves nor satisfies.",
                help=f"The principles taught under {title}. One per line.",
            )
            applications = st.text_area(
                "Applications",
                height=90,
                key=f"applications_{index}",
                placeholder="Where in your life have you substituted activity "
                            "for intimacy with God?",
                help=f"The application questions asked under {title}. "
                     "One per line — or, for a question written over several "
                     "lines, leave a blank line between questions. Start a "
                     "question with its own name, like \"Final Reflection "
                     "Question\", and the card is headed with that name.",
            )
        divisions.append({
            "title": title,
            "principles": principles,
            "applications": applications,
        })

    with st.expander("Scripture read aloud — shown on screen as you read it (optional)",
                     expanded=False):
        scripture = st.text_area(
            "Passages",
            key="scripture_box",
            height=180,
            label_visibility="collapsed",
            placeholder="ROMANS 3:23\n“For all have sinned and fall short of the "
                        "glory of God.”\n\nROMANS 5:8\n“But God demonstrates his "
                        "own love for us in this: While we were still sinners, "
                        "Christ died for us.”",
            help="Passages you read out word for word. Put the reference on "
                 "its own line, the verse under it, and a blank line between "
                 "passages. Each appears on screen from a moment before you "
                 "start reading it until a moment after you finish — found by "
                 "matching the words themselves, so it lands to the second.",
        )
        _passages = matcher.parse_scripture(scripture)
        if _passages:
            st.caption("  ·  ".join(ref for ref, _ in _passages))

    _counts = {
        "takeaway": len(matcher.split_items(takeaway)),
        "divisions": len(division_titles),
        "principles": sum(
            len(matcher.split_items(d["principles"]))
            for d in divisions
        ),
        "applications": sum(
            len(matcher.parse_questions(d["applications"]))
            for d in divisions
        ),
        "scripture": len(_passages),
    }
    if any(_counts.values()):
        st.caption(
            "  ·  ".join(
                f"{count} {name}" for name, count in _counts.items() if count
            )
            + f"  ·  {sum(_counts.values())} cards in total"
        )

logo_path = artwork_in_use("logo")
intro_path = artwork_in_use("intro")
outro_path = artwork_in_use("outro")

outline = {"takeaway": takeaway, "divisions": divisions, "scripture": scripture}
points = matcher.build_lesson_points(outline)

# Results belong to the outline they were made from. Change a point after
# analysing and the table would still show the old timings — so the
# fingerprint of what was analysed is kept, and a change clears the results.
# The speaker's name is not part of it: the lower third is drawn at render
# time from whatever is in the box, so changing it needs no re-analysis.
_fingerprint = repr([(p.category, p.text, p.division) for p in points])
if st.session_state.get("verdicts") and st.session_state.get("analysed_fingerprint") != _fingerprint:
    st.session_state.update(verdicts=None, matches=None, notes=[], output_path=None)
    st.info("The outline changed since the last analysis — analyse again to refresh the timings.")

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
        notes=[], output_path=None, source_name=os.path.basename(path),
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
            source_name=uploaded.name,
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
    st.info("Choose the recording and enter at least one lesson point to begin.")

if st.button("🔍 Analyse recording", type="primary", disabled=not ready, width="stretch"):
    if not memory_notice(MEMORY_NEEDED_ANALYSE_MB, "analyse this recording"):
        st.stop()
    st.session_state.output_path = None
    control.remember_speaker(speaker_name, speaker_title)
    try:
        with stay_awake(), st.status("Working…", expanded=True) as status:
            bar = st.progress(0.0)
            line = st.empty()

            def on_progress(fraction, message):
                bar.progress(min(max(fraction, 0.0), 1.0))
                line.write(message)

            status.update(label="Step 1 of 3 — listening to the recording")
            started = time.time()
            _language = language.strip() or None
            # The same recording listened to the same way gives the same
            # words, so a second analysis — after fixing a typo in the
            # outline, say — skips straight to matching.
            segments = transcriber.cached_transcript(
                st.session_state.video_path, engine, model_size, _language)
            if segments:
                line.write("Using the transcript made earlier for this recording.")
            else:
                segments = transcriber.transcribe_video(
                    st.session_state.video_path,
                    engine=engine,
                    model_size=model_size,
                    api_key=groq_key or (llm_api_key if provider == "groq" else ""),
                    language=_language,
                    progress_cb=on_progress,
                )
                transcriber.remember_transcript(
                    st.session_state.video_path, segments, engine, model_size, _language)
            st.session_state.segments = segments
            status.update(label="Listening for reflection pauses")
            # The transcript already tells us where nobody was speaking, and
            # costs nothing to read: Whisper produces no segment during
            # silence, so a long gap between segments is a pause however noisy
            # the room is. Scanning the waveform means decoding the audio
            # twice more, so it is kept in reserve for the case where the
            # transcript shows no pause at all.
            silences = transcriber.silences_from_transcript(
                segments, matcher.APPLICATION_GAP_MIN
            )
            if not silences:
                silences = transcriber.detect_silences(
                    st.session_state.video_path, matcher.APPLICATION_GAP_MIN
                )
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
            st.session_state.recording_ends = matcher.find_recording_ends(
                segments, video_duration)
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
                pause_seconds=float(discussion_seconds),
                overview_card=overview_card,
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
                        pause_seconds=float(discussion_seconds),
                        overview_card=overview_card,
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
            # Layout is last: verification may have moved start times.
            verdicts = verifier.lay_out(
                verdicts, video_duration, overview=overview_card,
                segments=segments, silences=silences,
                pause_seconds=float(discussion_seconds),
            )
            st.session_state.matches = [v.match for v in verdicts]
            st.session_state.verdicts = verdicts
            st.session_state.analysed_fingerprint = _fingerprint
            st.session_state.notes = notes
            bar.progress(1.0)
            status.update(label="Analysis complete", state="complete", expanded=False)
            control.report_async(
                "analysed",
                lesson_minutes=round(video_duration / 60, 1),
                points=len(points),
                verified=verifier.summarise(verdicts)[verifier.VERIFIED],
                model=model_used or "offline",
                engine=engine,
            )
    except Exception as exc:
        if getattr(exc, "is_auth_failure", False) and not has_own_keys():
            # A shared key was replaced: fetch the new one and say so.
            _before = control.built_in_keys()
            control.fetch_control(force=True)
            _ok = control.built_in_keys() != _before
            if not _ok and control.vault_url():
                _ok, _, _ = control.connect(control.group_code())
            st.error(
                "The AI key was turned down. "
                + ("A fresh one has been fetched — press Analyse again."
                   if _ok else
                   "Press Analyse again to carry on with keyword matching, or "
                   "add your own free key under **More settings → AI keys**.")
            )
        else:
            st.session_state.last_error = traceback.format_exc()[-3000:]
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

    # Retries and model hand-offs are the app doing its job; only what changes
    # how far the timings can be trusted is worth a warning.
    _technical = re.compile(
        r"attempt \d|answered by|retrying|reused the saved answer|failed \(|failed:|"
        r"high demand|no longer available|"
        r"^attempt|switching to|too slow today|turned down|matched by |"
        r"none of the known models|allowance is used up",
        re.IGNORECASE,
    )
    _notes = list(st.session_state.notes or [])
    _plain = [n for n in _notes if not _technical.search(n)]
    _behind = [n for n in _notes if _technical.search(n)]
    for note in _plain:
        if "offline keyword matcher" in note or "AI matching unavailable" in note:
            continue
        st.warning(note)
    if any("offline keyword matcher" in n for n in _notes):
        st.warning(
            "The AI couldn't be reached, so points were placed by matching "
            "keywords. That is usually close, but read every row before "
            "rendering — or press Analyse again in a few minutes."
        )
    if control.saved_keys() and any("key was turned down" in n for n in _notes):
        st.warning(
            "The AI key saved on this computer was turned down, so the backup "
            "service was used. Under **More settings → AI keys**, press **Use "
            "shared keys** — or save a new key of your own."
        )
    if _behind or any("AI matching unavailable" in n for n in _notes):
        with st.expander("Behind the scenes"):
            for note in _notes:
                st.caption(note)

    duration = (info or {}).get("duration", 0.0)
    table_verdicts = [v for v in verdicts if v.match.type != "lower_third"]
    _overrides = st.session_state.setdefault("row_overrides", {})
    frame = pd.DataFrame(
        [
            {
                # Anything that failed verification starts switched OFF, so a
                # bad placement cannot reach the video by inattention.
                "Show": _overrides.get(i, {}).get("Show", v.verdict != verifier.REJECTED),
                "Status": verifier.VERDICT_LABEL[v.verdict],
                "Header": v.match.header or v.match.category,
                "Point": v.match.text,
                "Start (s)": float(v.match.start_time),
                "End (s)": float(v.match.end_time),
                "On screen": transcriber.format_timestamp(
                    max(v.match.end_time - v.match.start_time, 0)
                ),
                "Timer": bool(v.match.has_timer),
                # Every application gets the sidebar's discussion time unless
                # a different number is typed here for that one question.
                "Discussion (s)": _overrides.get(i, {}).get("Discussion (s)") or (
                    f"{int(discussion_seconds)}" if v.match.type == "application"
                    else ""                     # blank for anything that is not a question
                ),
                "Cut": (
                    f"{transcriber.format_timestamp(v.match.cut_end - v.match.cut_start)} "
                    f"removed, resumes {transcriber.format_timestamp(v.match.cut_end)}"
                    if v.match.type == "application" and v.match.cut_end > v.match.cut_start
                    else ""
                ),
                "Heard": v.match.evidence,
                "Why": v.reason_text,
            }
            for i, v in enumerate(table_verdicts)
        ]
    )

    _details = st.toggle(
        "Show what was heard and why",
        value=False,
        help="Adds the words the speaker actually said at each point and the "
             "checks behind every status — handy when a row needs a closer look.",
    )
    _columns = ["Show", "Status", "Header", "Point", "Start (s)", "End (s)",
                "On screen", "Timer", "Discussion (s)", "Cut"]
    if _details:
        _columns += ["Heard", "Why"]
    st.caption(
        "Untick **Show** to leave a card out. Change **Start**/**End** (in "
        "seconds) to move one, or **Discussion (s)** to give one question a "
        "different countdown."
    )
    edited = st.data_editor(
        frame,
        width="stretch",
        hide_index=True,
        num_rows="fixed",
        column_order=_columns,
        column_config={
            "Show": st.column_config.CheckboxColumn("Show", width="small"),
            "Status": st.column_config.TextColumn("Status", disabled=True, width="small"),
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
            "Cut": st.column_config.TextColumn(
                "Cut", disabled=True, width="medium",
                help="Dead time after the question — silence, or the room "
                     "discussing — that is removed and replaced by the "
                     "countdown. The video picks up where the teacher resumes.",
            ),
            "Discussion (s)": st.column_config.TextColumn(
                "Discussion (s)", width="small", max_chars=8,
                help="How long the countdown runs after this question. "
                     "Starts at the sidebar's discussion time; type another "
                     "for this one question only — 90, 2:30 or 2 min all work.",
            ),
            "Heard": st.column_config.TextColumn("What the speaker said", disabled=True),
            "Why": st.column_config.TextColumn("Checks", disabled=True, width="medium"),
        },
        key=f"matches_editor_{st.session_state.get('table_rev', 0)}",
    )

    requested = cues_from_table(
        edited, {v.match.text: v.match for v in verdicts},
        float(discussion_seconds), float(caption_seconds),
    )
    _records = edited.to_dict("records")
    for _i, _row in enumerate(_records):
        _m = table_verdicts[_i].match if _i < len(table_verdicts) else None
        if _m is not None and _m.cut_end > _m.cut_start:
            _row["_resume"] = float(_m.cut_end)
    st.session_state.last_rows = _records

    # The real schedule: overlaps trimmed, points matched to the same moment
    # queued one after another, anything past the end of the video dropped.
    cues = editor.schedule_cues(requested, duration, float(caption_seconds))
    lt_start, lt_duration = editor.lower_third_timing(
        [v.match for v in verdicts]
    )
    keep_from, keep_to = st.session_state.get("recording_ends") or (0.0, duration)
    if not trim_ends:
        keep_from, keep_to = 0.0, duration
    keep_to = min(keep_to or duration, duration) if duration else keep_to
    # The name card comes up a few seconds into what is kept, not into the
    # raw recording — otherwise trimming the setup would swallow most of it.
    lt_start = lt_start + keep_from
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

    if extra_on("assistant"):
        try:
            helper_panel(_records, duration)
        except Exception as _exc:                       # never let it break the page
            st.caption(f"The helper isn't available right now ({type(_exc).__name__}).")

    with st.expander("📝 Full transcript"):
        text = transcriber.segments_to_text(st.session_state.segments or [])
        st.text_area("Transcript", value=text, height=260, label_visibility="collapsed")
        st.download_button("Download transcript (.txt)", text, "transcript.txt", "text/plain")

    st.divider()

    # ----------------------------------------------------------------------
    # Step 3: render
    # ----------------------------------------------------------------------

    st.subheader("5 · Render the finished video")
    # Measured: about 9x realtime on the shared server and 12x on a laptop
    # at the balanced setting, against roughly 1x through MoviePy.
    if render_engine != "auto":
        rate = 1.0
    else:
        # Learned from this computer's own last render where there is one:
        # a 1080p phone recording renders far slower than a small test file.
        rate = float(control.prefs().get("render_rate") or (9.0 if HOSTED else 4.0))
    removed = sum(c.cut_end - c.cut_start for c in cues if c.cut_end > c.cut_start)
    inserted = sum(c.timer_duration for c in cues if c.has_timer)
    bookends = (float(bookend_seconds) if intro_path else 0.0) + \
               (float(bookend_seconds) if outro_path else 0.0)
    trimmed = keep_from + max(duration - keep_to, 0.0) if duration else 0.0
    final_length = max(duration - trimmed - removed + inserted + bookends, 0.0)
    estimate = final_length / rate if duration else 0.0
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
    timed = [c for c in cues if c.has_timer]
    if timed:
        extras.append(
            f"a countdown on {len(timed)} application"
            + ("s" if len(timed) > 1 else "")
        )
    tail = (", plus " + ", ".join(extras)) if extras else ""
    length_note = ""
    if removed or inserted or trimmed:
        parts = []
        if removed:
            parts.append(f"{transcriber.format_timestamp(removed)} of waiting cut out")
        if inserted:
            parts.append(f"{transcriber.format_timestamp(inserted)} of discussion time added")
        if keep_from:
            parts.append(f"{transcriber.format_timestamp(keep_from)} of setup trimmed")
        if duration and keep_to < duration:
            parts.append(f"{transcriber.format_timestamp(duration - keep_to)} after the "
                         "closing trimmed")
        length_note = (
            f" The finished video will run about "
            f"{transcriber.format_timestamp(final_length)} — " + ", ".join(parts) + "."
        )
    st.caption(
        f"{len(cues)} {style_word}(s), each on screen for as long as it is "
        f"taught{tail}.{length_note} "
        f"Estimated render time: about "
        f"{transcriber.format_timestamp(max(estimate, 5))}."
    )

    if not intro_path and not outro_path:
        st.info(
            "No intro or outro image is set, so the video will start and end "
            "on the recording itself. Add them in the sidebar under "
            "**Intro and outro**."
        )
    if not cues:
        st.info("Nothing is switched on to show. Tick at least one point above.")

    if st.button(
        "🎬 Render final video",
        type="primary",
        width="stretch",
        disabled=not cues,
    ):
        if not memory_notice(MEMORY_NEEDED_RENDER_MB, "render this video"):
            st.stop()
        # Rendering writes the pieces and then the finished file, so it needs
        # room for about two copies of the FINISHED video — which is sized by
        # the quality chosen, not by the recording. A camera file can be ten
        # times the size of the video made from it, and judging by the
        # recording would turn people away with plenty of room to spare.
        need_disk = max(editor.estimated_output_mb(duration, quality) * 2.2, 500)
        disk_ok, free_disk = transcriber.disk_headroom_ok(need_disk, workdir())
        if not disk_ok:
            st.error(
                f"There is not enough disk space left to render this "
                f"({free_disk:.0f} MB free, about {need_disk:.0f} MB needed).\n\n"
                "Rendering needs room for the recording, a working copy and "
                "the finished video at the same time. On a shared server this "
                "usually clears within a few hours; on your own computer, free "
                "up some space and try again."
            )
            st.stop()
        try:
            discard_previous_render()
            output_path = os.path.join(workdir(), "bible_study_final.mp4")
            with stay_awake(), st.status("Rendering…", expanded=True) as status:
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
                    quality=quality,
                    threads=int(threads),
                    engine=render_engine,
                    progress_cb=on_render,
                    trim_start=keep_from,
                    trim_end=keep_to if duration and keep_to < duration else 0.0,
                )
                st.session_state.output_path = output_path
                st.session_state.render_seconds = time.time() - started
                st.session_state.render_record = {
                    "output_path": output_path, "cues": cues, "duration": duration,
                    "keep_from": keep_from,
                    "keep_to": keep_to if duration and keep_to < duration else 0.0,
                    "intro_path": intro_path or "", "outro_path": outro_path or "",
                    "bookend_seconds": float(bookend_seconds), "card_style": card_style,
                    "rows": _records, "language": (language.strip() or "en"),
                }
                st.session_state.pop("review", None)
                st.session_state.changed_since_render = False
                if final_length > 120 and st.session_state.render_seconds > 5:
                    control.set_pref("render_rate", round(
                        final_length / st.session_state.render_seconds, 2))
                control.report_async(
                    "rendered",
                    lesson_minutes=round(duration / 60, 1),
                    seconds=round(st.session_state.render_seconds),
                    cards=len(cues),
                    quality=quality,
                    megabytes=round(os.path.getsize(output_path) / 1e6),
                )
                bar.progress(1.0)
                status.update(label="Render complete", state="complete", expanded=False)
        except Exception as exc:
            control.report_async("render_failed", reason=type(exc).__name__)
            # Something on this computer may have changed since it was last
            # checked; look again before the next attempt.
            control.set_setting("self_check_version", "")
            st.session_state.last_error = traceback.format_exc()[-3000:]
            st.error(f"Rendering failed: {exc}\n\nThe app will check this "
                     "computer the next time the page loads — press **Check "
                     "this computer** in the sidebar to do it now.")
            with st.expander("Technical details"):
                st.code(traceback.format_exc())

# Streamlit's download button holds the whole file in memory. That is fine for
# a short clip and a bad idea for a two-hour lesson, so past this size the
# finished file is left on disk and its location shown instead.
MAX_INMEMORY_DOWNLOAD_MB = 400

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
# A packaged app cannot write inside its own bundle, so finished videos go
# somewhere obvious instead: Movies on a Mac, Videos on Windows.
def _videos_folder() -> str:
    """Movies on a Mac; on Windows the real Videos folder, even if moved to OneDrive."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            import uuid as _uuid

            folder_id = _uuid.UUID("{18989B1D-99B5-455B-841C-AB7C74E4DDFC}")  # Videos
            guid = (ctypes.c_byte * 16).from_buffer_copy(folder_id.bytes_le)
            found = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(guid), 0, None, ctypes.byref(found)
            ) == 0 and found.value:
                path = found.value
                ctypes.windll.ole32.CoTaskMemFree(found)
                return path
        except Exception:
            pass
        return os.path.join(os.path.expanduser("~"), "Videos")
    return os.path.join(os.path.expanduser("~"), "Movies")


VIDEOS_DIR = _videos_folder()


def save_finished_copy(path: str, folder: str, stem: str) -> str:
    """
    Copy the finished video somewhere permanent, never over another one.

    Each render gets its own file: if the name is taken by an earlier video
    — last week's lesson recorded with the same camera name, say — a number
    is added rather than replacing it. Copied under a temporary name and
    renamed only once complete, so a half-copied file is never left looking
    finished. Returns the saved path ("" if it couldn't be saved).
    """
    made = f"{path}:{os.path.getmtime(path)}"
    done = st.session_state.setdefault("saved_copies", {})
    if done.get(made) and os.path.exists(done[made]):
        return done[made]
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, f"{stem} - captioned.mp4")
    number = 2
    while os.path.exists(target):
        target = os.path.join(folder, f"{stem} - captioned ({number}).mp4")
        number += 1
    partial = target + ".partial"
    try:
        shutil.copyfile(path, partial)
        os.replace(partial, target)
    except OSError:
        try:
            os.remove(partial)
        except OSError:
            pass
        return ""
    done[made] = target
    return target
PACKAGED_OUTPUT_DIR = os.environ.get("BSVE_OUTPUT_DIR") or \
    os.path.join(VIDEOS_DIR, "Bible Study Video Editor")
IS_PACKAGED = bool(os.environ.get("BSVE_HOME"))
STATIC_MAX_AGE_HOURS = 6


def publish_for_download(path: str, name: str) -> str:
    """
    Put the finished video where the browser can fetch it directly.

    Streamlit serves anything in ./static over HTTP, so handing over a link
    costs no memory at all — where st.download_button would first load the
    entire file into RAM. That is the difference between a 90-minute lesson
    downloading cleanly and the app dying at the last step.

    Returns the URL path, or "" if static serving is unavailable.
    """
    try:
        os.makedirs(STATIC_DIR, exist_ok=True)

        # Clear out anything left from earlier sessions; these are large.
        cutoff = time.time() - STATIC_MAX_AGE_HOURS * 3600
        for old in os.listdir(STATIC_DIR):
            full = os.path.join(STATIC_DIR, old)
            try:
                if os.path.isfile(full) and os.path.getmtime(full) < cutoff:
                    os.remove(full)
            except OSError:
                pass

        target = os.path.join(STATIC_DIR, name)
        if os.path.exists(target):
            os.remove(target)
        # A hard link where possible: instant, and no second copy on disk.
        try:
            os.link(path, target)
        except OSError:
            shutil.copy2(path, target)
        return f"app/static/{name}"
    except Exception:
        return ""

LESSON_BOXES = ("takeaway_box", "divisions_box", "scripture_box", "link_box",
                "local_path_box", "speaker_name_box", "speaker_title_box",
                "lesson_title_box", "paste_box")


def start_next_video() -> None:
    """Clear this lesson so the next one starts on a clean page."""
    discard_previous_render()
    # Text boxes are emptied by giving them an empty value; simply forgetting
    # them lets the browser hand the old text straight back.
    for key in list(st.session_state.keys()):
        name = str(key)
        if name in LESSON_BOXES or name.startswith(("principles_", "applications_")):
            st.session_state[key] = ""
    st.session_state.speaker_pick = "— Someone new —"
    for key, value in DEFAULTS.items():
        st.session_state[key] = value
    for key in ("source_name", "saved_copies", "video_upload", "chat", "pending_changes",
                "undo_plan", "row_overrides", "table_rev", "review", "render_record",
                "last_rows", "last_error", "report_path"):
        st.session_state.pop(key, None)


if st.session_state.output_path and os.path.exists(st.session_state.output_path):
    path = st.session_state.output_path
    size_mb = os.path.getsize(path) / (1024 * 1024)
    st.success(
        f"Done in {transcriber.format_timestamp(st.session_state.render_seconds)} — "
        f"{size_mb:.1f} MB"
    )

    if size_mb <= 60:
        st.video(path)

    stem = (st.session_state.get("lesson_title_box") or "").strip() or os.path.splitext(
        st.session_state.get("source_name")
        or os.path.basename(st.session_state.video_path or "lesson")
    )[0].strip()
    if stem.lower() in ("", "source", "source_from_link", "lesson"):
        stem = f"{speaker_name.strip() or 'Lesson'} {time.strftime('%Y-%m-%d')}"
    # Underscores are for the URL, where spaces are awkward. A file saved on
    # somebody's own disk should keep the name they gave the recording.
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem)[:60] or "lesson"
    friendly = re.sub(r'[/\\:*?"<>|]', "-", stem)[:80].strip(" .") or "lesson"

    if not HOSTED:
        # Running on the user's own computer, so the file is already on their
        # disk. Downloading it through the browser would only make a second
        # copy in Downloads and leave them hunting for it. Save it somewhere
        # obvious and offer to open the folder instead.
        # Always the same, findable place — never "next to the recording",
        # which for a fetched link is a temporary folder that is cleared.
        saved = save_finished_copy(path, PACKAGED_OUTPUT_DIR, friendly) or \
            save_finished_copy(path, VIDEOS_DIR, friendly)
        if not saved:
            st.error("The video was made but couldn't be saved — the disk may be "
                     "full. Free some space and press Render again.")
            st.stop()

        st.success(f"Saved as **{os.path.basename(saved)}**")
        st.code(saved, language=None)
        if sys.platform == "darwin":
            if st.button("📂  Show it in Finder", width="stretch"):
                subprocess.run(["open", "-R", saved], check=False)
        elif sys.platform.startswith("win"):
            if st.button("📂  Show it in Explorer", width="stretch"):
                subprocess.run(["explorer", "/select,", saved], check=False)
        st.button("➕  Start the next video", width="stretch", on_click=start_next_video,
                  help="Clears the recording, outline and timings. Your settings, "
                       "slides and speakers stay as they are.")
        if extra_on("review"):
            try:
                review_panel(path)
            except Exception as _exc:                   # never let it break the page
                st.caption(f"The video check couldn't run this time ({type(_exc).__name__}). "
                           "The video itself is fine and saved.")
        st.stop()

    # Served straight from disk, so the size of the lesson does not matter.
    url = publish_for_download(path, f"{safe}-captioned.mp4")

    if url:
        # st.html, not st.markdown. Markdown links get target="_blank" added,
        # and a browser ignores the download attribute on a link that opens in
        # a new tab — so the video played in a tab instead of being saved.
        link = (
            f'<a href="{url}" download style="display:block;text-align:center;'
            'background:#FFCA5C;color:#111;padding:0.75rem 1rem;'
            'border-radius:0.5rem;font-weight:600;text-decoration:none;">'
            '⬇️  Download the finished MP4</a>'
        )
        try:
            st.html(link)
        except AttributeError:      # very old Streamlit
            st.markdown(link, unsafe_allow_html=True)
        st.caption(
            "Served straight from disk, so it downloads at full speed however "
            "long the lesson is. It will land in your Downloads folder."
        )
    elif size_mb <= MAX_INMEMORY_DOWNLOAD_MB:
        with open(path, "rb") as handle:
            st.download_button(
                "⬇️ Download the finished MP4",
                handle.read(),
                file_name="bible_study_final.mp4",
                mime="video/mp4",
                type="primary",
                width="stretch",
            )
    elif HOSTED:
        st.error(
            f"The finished video is {size_mb:.0f} MB and could not be prepared "
            "for download on this server. Set *Video quality* to "
            '"Smaller file" in the sidebar and render again.'
        )
    else:
        source = st.session_state.video_path or ""
        folder = (
            PACKAGED_OUTPUT_DIR if IS_PACKAGED
            else (os.path.dirname(os.path.abspath(source)) if source else "")
        )
        if IS_PACKAGED:
            os.makedirs(folder, exist_ok=True)
        final = ""
        if folder and os.path.isdir(folder) and os.access(folder, os.W_OK):
            stem2 = os.path.splitext(os.path.basename(source))[0]
            final = os.path.join(folder, f"{stem2} - captioned.mp4")
            try:
                if not os.path.exists(final) or os.path.getmtime(final) < os.path.getmtime(path):
                    shutil.copy2(path, final)
            except OSError:
                final = ""
        st.info(f"This file is {size_mb:.0f} MB — saved next to your recording.")
        st.code(final or path, language=None)
        st.caption("Open that location in Finder or Explorer to get the video.")
