"""
control.py
==========
The parts of the tool that stay under your control after it has been
installed on somebody else's computer.

Three things, all optional and all fail-quiet — nothing here may ever stop
somebody editing a video:

  * **Version and updates.** Every copy checks a small file in the repository
    on launch and offers to update itself.
  * **Remote control.** That same file can carry a message to show everybody,
    a minimum version to insist on, and settings that override the defaults —
    so a model chain can be changed, or a warning posted, without anybody
    installing anything.
  * **Usage reporting.** Optional, disclosed, and easy to switch off.

WHAT IS NEVER SENT
No video, no audio, no transcript, no lesson outline, no API key, no file
name and no file path. The reports carry counts and durations, nothing that
could reconstruct what a lesson was about. See `usage_payload` — it is
deliberately short enough to read in full.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
import uuid
from typing import Any, Dict, Optional

import requests

VERSION = "1.0.0"

# Where every installed copy looks for instructions. A plain file in the
# repository: no server to run, no bill, and you change it with a commit.
#
# Two routes, because one is not dependable. raw.githubusercontent.com sits
# behind a CDN that caches aggressively — including caching a 404 from before
# the file existed, which it will then serve for minutes after publishing. A
# cache-busting parameter avoids most of that, and the API is consulted when
# it does not.
REPO = "zv1pul/bible-study-video-editor"
CONTROL_RAW_URL = f"https://raw.githubusercontent.com/{REPO}/main/control.json"
CONTROL_API_URL = f"https://api.github.com/repos/{REPO}/contents/control.json"
CONTROL_URL = CONTROL_RAW_URL   # kept for anything referring to it by name
CONTROL_TIMEOUT = 6           # never keep somebody waiting on this
CONTROL_CACHE_MINUTES = 60

_HERE = os.path.dirname(os.path.abspath(__file__))

# Inside a packaged app the bundle is read-only, so anything we save goes to
# the folder the launcher hands us. Running from a checkout, it sits here.
_STATE_DIR = os.path.join(
    os.environ.get("BSVE_HOME") or _HERE, ".state"
)


# --------------------------------------------------------------------------
# Local state
# --------------------------------------------------------------------------


def _state_path(name: str) -> str:
    os.makedirs(_STATE_DIR, exist_ok=True)
    return os.path.join(_STATE_DIR, name)


def _read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _write_json(path: str, value: Any) -> None:
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
    except Exception:
        pass


def install_id() -> str:
    """
    A random identifier for this installation.

    Generated on this computer and never derived from anything about the
    person or the machine. It exists so that ten lessons from one copy can be
    told apart from one lesson from ten copies.
    """
    path = _state_path("install.json")
    saved = _read_json(path, {})
    if not saved.get("id"):
        saved = {"id": uuid.uuid4().hex[:16], "first_seen": int(time.time())}
        _write_json(path, saved)
    return saved["id"]


def settings() -> Dict[str, Any]:
    return _read_json(_state_path("settings.json"), {}) or {}


def set_setting(key: str, value: Any) -> None:
    current = settings()
    current[key] = value
    _write_json(_state_path("settings.json"), current)


# --------------------------------------------------------------------------
# Version comparison
# --------------------------------------------------------------------------


def _parts(version: str):
    out = []
    for piece in str(version or "0").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def is_newer(candidate: str, current: str = VERSION) -> bool:
    return _parts(candidate) > _parts(current)


# --------------------------------------------------------------------------
# Fetching instructions
# --------------------------------------------------------------------------


def fetch_control(force: bool = False) -> Dict[str, Any]:
    """
    Read the control file, falling back to the last copy seen.

    Cached for an hour and silent on failure: somebody working offline, or
    behind a firewall, must not notice this exists.
    """
    cache_path = _state_path("control_cache.json")
    cached = _read_json(cache_path, {}) or {}

    fresh_enough = (
        cached.get("fetched_at", 0) > time.time() - CONTROL_CACHE_MINUTES * 60
    )
    if cached and fresh_enough and not force:
        return cached.get("data", {})

    data = _fetch_raw() or _fetch_via_api()
    if data is not None:
        _write_json(cache_path, {"fetched_at": int(time.time()), "data": data})
        return data
    return cached.get("data", {})


def _fetch_raw() -> Optional[dict]:
    """The CDN copy, with a cache-buster so a stale answer is less likely."""
    try:
        response = requests.get(
            CONTROL_RAW_URL,
            params={"t": int(time.time() // 60)},   # changes every minute
            headers={"Cache-Control": "no-cache"},
            timeout=CONTROL_TIMEOUT,
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def _fetch_via_api() -> Optional[dict]:
    """
    The API copy, used when the CDN is serving something stale.

    Slower and rate limited, but it reads the repository directly and so is
    right immediately after a change.
    """
    try:
        response = requests.get(
            CONTROL_API_URL,
            headers={"Accept": "application/vnd.github.raw"},
            timeout=CONTROL_TIMEOUT,
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def update_status() -> Dict[str, Any]:
    """
    What this copy should know: is it behind, is it too old to trust, and is
    there anything to tell the person using it.
    """
    control = fetch_control()
    latest = str(control.get("latest_version", "") or "")
    minimum = str(control.get("minimum_version", "") or "")
    return {
        "version": VERSION,
        "latest": latest,
        "update_available": bool(latest) and is_newer(latest, VERSION),
        "too_old": bool(minimum) and is_newer(minimum, VERSION),
        "message": str(control.get("message", "") or ""),
        "message_kind": str(control.get("message_kind", "info") or "info"),
        "notes": str(control.get("release_notes", "") or ""),
        "overrides": control.get("settings", {}) or {},
        "telemetry_url": str(control.get("telemetry_url", "") or ""),
    }


# --------------------------------------------------------------------------
# Updating in place
# --------------------------------------------------------------------------


def is_git_install() -> bool:
    return os.path.isdir(os.path.join(_HERE, ".git"))


def apply_update() -> tuple:
    """
    Pull the latest version and refresh packages. (ok, message).

    Only possible for a copy installed from the repository; a packaged build
    updates by downloading a new one.
    """
    if not is_git_install():
        return False, (
            "This copy was not installed from the repository, so it cannot "
            "update itself. Download the latest version instead."
        )
    try:
        pull = subprocess.run(
            ["git", "pull", "--ff-only"], cwd=_HERE,
            capture_output=True, text=True, timeout=120,
        )
        if pull.returncode != 0:
            return False, f"Could not fetch the update: {pull.stderr[:200]}"

        venv_pip = os.path.join(_HERE, ".venv", "bin", "pip")
        if not os.path.exists(venv_pip):
            venv_pip = os.path.join(_HERE, ".venv", "Scripts", "pip.exe")
        if os.path.exists(venv_pip):
            subprocess.run(
                [venv_pip, "install", "-q", "-r", "requirements-local.txt"],
                cwd=_HERE, capture_output=True, text=True, timeout=600,
            )
        return True, "Updated. Close and reopen the app to use the new version."
    except Exception as exc:
        return False, f"Could not update: {exc}"


# --------------------------------------------------------------------------
# Usage reporting
# --------------------------------------------------------------------------


def telemetry_enabled() -> bool:
    return bool(settings().get("telemetry", True))


def set_telemetry(enabled: bool) -> None:
    set_setting("telemetry", bool(enabled))


def usage_payload(event: str, **fields) -> Dict[str, Any]:
    """
    Exactly what a report contains. Nothing is added elsewhere.

    Deliberately dull: which copy, which version, what kind of computer, what
    happened, and how long it took.
    """
    return {
        "install": install_id(),
        "version": VERSION,
        "platform": platform.system(),
        "event": event,
        "at": int(time.time()),
        **{k: v for k, v in fields.items() if v is not None},
    }


def report(event: str, **fields) -> bool:
    """
    Send one usage report. Silent, quick, and never blocks the app.

    Returns True if it was sent — useful for testing, ignored in normal use.
    """
    if not telemetry_enabled():
        return False
    url = update_status().get("telemetry_url", "")
    if not url:
        return False
    try:
        requests.post(url, json=usage_payload(event, **fields), timeout=4)
        return True
    except Exception:
        return False


def report_async(event: str, **fields) -> None:
    """Report on a background thread, so nothing ever waits for it."""
    import threading

    threading.Thread(
        target=lambda: report(event, **fields), daemon=True
    ).start()
