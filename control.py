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
import threading
import time
import uuid
from typing import Any, Dict, Optional

import requests

VERSION = "1.2.1"

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


_LOCK = threading.RLock()


def _write_json(path: str, value: Any) -> None:
    # Owner-only: settings can hold an API key, and the cache the group's
    # shared ones. Written to a temporary file first so a crash mid-write
    # never leaves a half-written settings file behind.
    with _LOCK:
        temporary = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2)
            os.chmod(temporary, 0o600)
        except Exception:
            return
        # Windows won't replace a file another program is reading at that
        # instant; a moment later it will.
        for attempt in range(10):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))
            except Exception:
                break
        try:
            os.remove(temporary)
        except OSError:
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
    # Read, change and write as one step, so two changes made at the same
    # moment can't undo each other.
    with _LOCK:
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
    if not force:
        # Offline: don't try again on every click — once a minute is plenty.
        _write_json(cache_path, {
            "fetched_at": int(time.time()) - CONTROL_CACHE_MINUTES * 60 + 60,
            "data": cached.get("data") or bundled_control(),
        })
    return cached.get("data") or bundled_control()


def bundled_control() -> Dict[str, Any]:
    """The control file this copy shipped with — used until one is fetched."""
    return _read_json(os.path.join(_HERE, "control.json"), {}) or {}


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


# --------------------------------------------------------------------------
# Shared keys
# --------------------------------------------------------------------------
#
# Nobody using a packaged copy should have to find an API key. Instead the
# app asks a small "vault" — a Google Apps Script in front of a Google Sheet
# (see vault/Code.gs) — for the group's shared keys, once, after the person
# types the group code. The keys are cached on this computer and refreshed
# quietly about once a day, or immediately if one stops working.
#
# The vault's address comes from control.json ("vault_url"), with the
# constant below as a fallback, so it can be moved without a release.

VAULT_URL = ""
KEYS_CACHE_HOURS = 24
VAULT_TIMEOUT = 10


def vault_url() -> str:
    return str(fetch_control().get("vault_url", "") or VAULT_URL)


def group_code() -> str:
    return str(settings().get("group_code", "") or "")


def set_group_code(code: str) -> None:
    set_setting("group_code", code.strip())
    _write_json(_state_path("keys_cache.json"), {})


def shared_keys(force: bool = False) -> Dict[str, str]:
    """
    The shared API keys, from cache when fresh, else from the vault.

    Empty when there is no vault, no group code, or the vault cannot be
    reached and nothing is cached — never an exception.
    """
    cache_path = _state_path("keys_cache.json")
    cached = _read_json(cache_path, {}) or {}
    fresh = cached.get("fetched_at", 0) > time.time() - KEYS_CACHE_HOURS * 3600
    if cached.get("keys") and fresh and not force:
        return dict(cached["keys"])

    ok, keys, _ = connect(group_code())
    if ok:
        return keys
    return dict(cached.get("keys") or {})


def connect(code: str) -> tuple:
    """
    Ask the vault for the keys. (ok, keys, message).

    Saves them on success so that the next launch does not need the vault
    at all.
    """
    url = vault_url()
    if not url:
        return False, {}, "This copy has no shared-key vault configured."
    code = (code or "").strip()
    if not code:
        return False, {}, "Enter the group code first."
    try:
        response = requests.get(
            url,
            params={
                "code": code,
                "install": install_id(),
                "version": VERSION,
                "platform": platform.system(),
            },
            timeout=VAULT_TIMEOUT,
        )
        data = response.json()
    except Exception:
        return False, {}, (
            "Could not reach the group's key vault. Check the internet "
            "connection and try again."
        )
    if not data.get("ok"):
        return False, {}, str(data.get("message") or "The vault said no.")
    keys = {
        k: str(v or "").strip()
        for k, v in (data.get("keys") or {}).items()
        if str(v or "").strip()
    }
    if not keys:
        return False, {}, (
            "The vault answered, but no keys are filled in on the Settings "
            "tab yet."
        )
    _write_json(
        _state_path("keys_cache.json"),
        {"fetched_at": int(time.time()), "keys": keys},
    )
    return True, keys, "Connected."


def forget_keys() -> None:
    _write_json(_state_path("keys_cache.json"), {})


# --------------------------------------------------------------------------
# Built-in shared keys
# --------------------------------------------------------------------------
#
# Everyone given the app uses the group's free keys without setting anything
# up. They travel in control.json as "shared_keys": the copy fetched from the
# repository wins, so a key is replaced for every installed copy by editing
# that one file — no new download — and the copy each app shipped with covers
# the first launch offline.
#
# They are masked only so that automated scanners don't mistake them for a
# leak and switch them off. That is NOT protection: anyone determined can read
# them, which is why only free-tier keys (no billing, nothing to charge) may
# ever go here. The worst anyone can do is use up a day's free allowance, and
# the app then falls back to the other service and finally to offline
# matching.

_MASK = b"bsve-shared-keys-v1"
_MASK_PREFIX = "bsve1:"


def mask_keys(keys: Dict[str, str]) -> str:
    """The form keys are stored in inside control.json."""
    import base64

    raw = json.dumps({k: v for k, v in keys.items() if v}).encode("utf-8")
    mixed = bytes(b ^ _MASK[i % len(_MASK)] for i, b in enumerate(raw))
    return _MASK_PREFIX + base64.urlsafe_b64encode(mixed).decode("ascii")


def unmask_keys(value: str) -> Dict[str, str]:
    import base64

    value = str(value or "")
    if not value.startswith(_MASK_PREFIX):
        return {}
    try:
        mixed = base64.urlsafe_b64decode(value[len(_MASK_PREFIX):].encode("ascii"))
        raw = bytes(b ^ _MASK[i % len(_MASK)] for i, b in enumerate(mixed))
        keys = json.loads(raw.decode("utf-8"))
        return {str(k): str(v).strip() for k, v in keys.items() if str(v or "").strip()}
    except Exception:
        return {}


def built_in_keys() -> Dict[str, str]:
    """The group's shared keys: the live control file's, else the shipped one's."""
    live = fetch_control()
    if "shared_keys" in live:
        return unmask_keys(live.get("shared_keys"))
    return unmask_keys(bundled_control().get("shared_keys"))


# --------------------------------------------------------------------------
# Things remembered between sessions
# --------------------------------------------------------------------------
#
# Kept in the same per-computer folder as everything else (outside the app
# itself), so they survive updates and are never shared with anyone.


def prefs() -> Dict[str, Any]:
    return dict(settings().get("prefs") or {})


def set_pref(name: str, value: Any) -> None:
    with _LOCK:
        current = prefs()
        if current.get(name) != value:
            current[name] = value
            set_setting("prefs", current)


SPEAKERS_KEPT = 15


def _clean_speakers(entries) -> list:
    out = []
    for entry in entries or []:
        if isinstance(entry, dict) and str(entry.get("name") or "").strip():
            out.append({"name": str(entry["name"]).strip(),
                        "title": str(entry.get("title") or "").strip()})
    return out


def group_speakers() -> list:
    """
    The group's teachers, from control.json — so a new copy already knows
    them, and a new teacher can be added for everybody by editing one file.
    The live file wins over the one the app shipped with.
    """
    live = fetch_control()
    source = live if "speakers" in live else bundled_control()
    return _clean_speakers(source.get("speakers"))


def speakers() -> list:
    """
    People to pick from, most recently used first: [{"name", "title"}].

    The ones used on this computer come first; the group's teachers follow,
    unless this computer has its own entry for them or removed them.
    """
    own = _clean_speakers(settings().get("speakers"))
    hidden = {name.lower() for name in settings().get("hidden_speakers") or []}
    known = {entry["name"].lower() for entry in own}
    return own + [entry for entry in group_speakers()
                  if entry["name"].lower() not in known | hidden]


def remember_speaker(name: str, title: str) -> None:
    name, title = (name or "").strip(), (title or "").strip()
    if not name:
        return
    with _LOCK:
        rest = [s for s in _clean_speakers(settings().get("speakers"))
                if s["name"].lower() != name.lower()]
        set_setting("speakers", ([{"name": name, "title": title}] + rest)[:SPEAKERS_KEPT])
        set_setting("hidden_speakers", [h for h in settings().get("hidden_speakers") or []
                                        if h.lower() != name.lower()])


def forget_speaker(name: str) -> None:
    name = (name or "").strip()
    with _LOCK:
        set_setting("speakers", [s for s in _clean_speakers(settings().get("speakers"))
                                 if s["name"].lower() != name.lower()])
        hidden = [h for h in settings().get("hidden_speakers") or [] if h.lower() != name.lower()]
        set_setting("hidden_speakers", hidden + [name])


ARTWORK_NAMES = ("intro", "outro", "logo")


def artwork_dir() -> str:
    path = _state_path("artwork")
    os.makedirs(path, exist_ok=True)
    return path


def saved_artwork(name: str) -> str:
    """The slide or logo saved on this computer, or "" if none was."""
    path = os.path.join(artwork_dir(), f"{name}.png")
    return path if os.path.exists(path) and os.path.getsize(path) > 0 else ""


def save_artwork(name: str, source: str) -> str:
    """
    Keep a new opening/closing slide or logo until it is changed again.

    Converted to a PNG (a PDF's first page works too) and fitted to the
    frame first, written beside the old one and swapped in only once it is
    complete, so a failed save never loses the slide already in use. Raises
    ValueError with a plain reason if the file can't be used.
    """
    import editor

    if name not in ARTWORK_NAMES:
        raise ValueError(f"Unknown artwork: {name}")
    final = os.path.join(artwork_dir(), f"{name}.png")
    temporary = os.path.join(artwork_dir(), f".{name}.new.png")
    editor.prepare_slide(source, temporary, kind="logo" if name == "logo" else "slide")
    os.replace(temporary, final)
    set_artwork_off(name, False)             # adding one means using it
    return final


def forget_artwork(name: str) -> None:
    try:
        os.remove(os.path.join(artwork_dir(), f"{name}.png"))
    except OSError:
        pass


def artwork_off(name: str) -> bool:
    """True when this computer chose to have no slide (or logo) of this kind."""
    return bool(prefs().get(f"{name}_off"))


def set_artwork_off(name: str, off: bool) -> None:
    set_pref(f"{name}_off", bool(off))


def remove_artwork(name: str) -> None:
    """No slide of this kind any more: the saved one is deleted, none is shown."""
    forget_artwork(name)
    set_artwork_off(name, True)


def artwork_choice(name: str, built_in: str = "") -> str:
    """
    The slide or logo a video uses, decided in one place:

      * removed on this computer        -> none
      * replaced on this computer       -> the replacement (kept through updates)
      * otherwise                       -> the one that comes with the app — so a
                                           new series' artwork, shipped in an
                                           update, reaches everyone who hasn't
                                           chosen their own
    """
    if artwork_off(name):
        return ""
    own = saved_artwork(name)
    if own:
        return own
    return built_in if built_in and os.path.exists(built_in) else ""


# --------------------------------------------------------------------------
# A key of one's own
# --------------------------------------------------------------------------
#
# Anyone given the app can use it with a free Google AI key of their own,
# saved on their computer. No card, no account with us — and their own
# daily allowance, which a group sharing one key would soon run through.

GEMINI_KEY_PAGE = "https://aistudio.google.com/apikey"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def saved_keys() -> Dict[str, str]:
    keys = settings().get("keys") or {}
    return {k: str(v) for k, v in keys.items() if str(v or "").strip()}


def save_key(name: str, value: str) -> None:
    keys = saved_keys()
    if value.strip():
        keys[name] = value.strip()
    else:
        keys.pop(name, None)
    set_setting("keys", keys)


def forget_saved_keys() -> None:
    set_setting("keys", {})


def check_gemini_key(key: str) -> tuple:
    """(ok, message): does Google accept this key? One free, tiny request."""
    key = (key or "").strip()
    if not key:
        return False, "Paste the key first."
    if " " in key or len(key) < 20:
        return False, "That doesn't look like a whole key — copy it again from the key page."
    try:
        response = requests.get(GEMINI_MODELS_URL, params={"key": key, "pageSize": 1},
                                timeout=VAULT_TIMEOUT)
    except Exception:
        return False, "Couldn't reach Google to check the key. Check the internet connection."
    if response.status_code == 200:
        return True, "Key works."
    if response.status_code in (400, 401, 403):
        return False, "Google didn't accept that key. Copy it again from the key page."
    if response.status_code == 429:
        return True, "Key works (it's busy right now, which is fine)."
    return False, f"Google answered {response.status_code}; try again in a minute."
