"""
updater.py
==========
Safe updates for a packaged copy — one installed from a download rather than
from the repository.

A packaged app carries its own Python and FFmpeg, which never change; only
the app's own code is updated. An update is:

  1. noticed — control.json in the repository names a newer version;
  2. downloaded — the code for that version's tag, from GitHub;
  3. checked — its packages must already be in this copy, and its own
     accuracy checks and a test render must pass on this computer;
  4. staged — only then is it put where the launcher will pick it up the
     next time the app opens. The version before it is kept, and the
     launcher falls back to it if the new one does not start.

Nothing is ever replaced in place, so a failed or interrupted update leaves
the copy exactly as it was.

    python updater.py --stage      # used by the launcher, in the background
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from typing import Optional, Tuple

import requests

import control

# What an update may replace. Python and FFmpeg are not on the list: they
# belong to the download and change only with a new download.
APP_FILES = ("app.py", "control.py", "control.json", "editor.py", "matcher.py",
             "selfcheck.py", "transcriber.py", "updater.py", "verifier.py",
             "requirements.txt")
APP_DIRS = ("assets", "fonts", "tests", ".streamlit")
DOWNLOAD_TIMEOUT = 120


def home() -> str:
    return os.environ.get("BSVE_HOME") or os.path.expanduser("~/.bible-study-video-editor")


def versions_dir() -> str:
    path = os.path.join(home(), "app")
    os.makedirs(path, exist_ok=True)
    return path


def _read(name: str) -> str:
    try:
        with open(os.path.join(versions_dir(), name), encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _write(name: str, value: str) -> None:
    path = os.path.join(versions_dir(), name)
    with open(f"{path}.tmp", "w", encoding="utf-8") as handle:
        handle.write(value)
    os.replace(f"{path}.tmp", path)


def staged_version() -> str:
    """The version the launcher will start next, if an update is staged."""
    version = _read("current")
    return version if version and os.path.isdir(os.path.join(versions_dir(), version)) else ""


def requirements_met(requirements_path: str) -> Tuple[bool, str]:
    """Are the packages this code asks for already in this copy's Python?"""
    from importlib import metadata

    try:
        from packaging.requirements import Requirement
    except Exception:
        return True, ""                    # cannot tell; the checks will catch it
    try:
        lines = open(requirements_path, encoding="utf-8").read().splitlines()
    except OSError:
        return True, ""
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        try:
            requirement = Requirement(line)
        except Exception:
            continue
        try:
            installed = metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            return False, f"needs {requirement.name}, which this download doesn't include"
        if requirement.specifier and not requirement.specifier.contains(installed, prereleases=True):
            return False, f"needs {requirement}, this download has {installed}"
    return True, ""


def _download(version: str) -> bytes:
    url = f"https://codeload.github.com/{control.REPO}/zip/refs/tags/v{version}"
    response = requests.get(url, timeout=DOWNLOAD_TIMEOUT)
    response.raise_for_status()
    return response.content


def _extract(archive: bytes, target: str) -> None:
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        names = bundle.namelist()
        root = names[0].split("/", 1)[0] + "/" if names else ""
        for name in names:
            relative = name[len(root):] if name.startswith(root) else name
            if not relative or relative.endswith("/"):
                continue
            top = relative.split("/", 1)[0]
            if relative not in APP_FILES and top not in APP_DIRS:
                continue
            if ".." in relative.split("/") or relative.startswith("/"):
                continue                               # never write outside target
            destination = os.path.join(target, relative)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with bundle.open(name) as source, open(destination, "wb") as out:
                shutil.copyfileobj(source, out)


def _passes_checks(folder: str) -> Tuple[bool, str]:
    run = subprocess.run(
        [sys.executable, os.path.join(folder, "selfcheck.py"), "--update-gate"],
        cwd=folder, capture_output=True, text=True, timeout=600,
        encoding="utf-8", errors="replace",
        env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"),
    )
    tail = [line for line in (run.stdout or "").splitlines() if line.strip()]
    if run.returncode == 0 and tail and tail[-1] == "ALL OK":
        return True, ""
    failures = [line for line in tail if line.startswith("FAIL")]
    return False, "; ".join(failures)[:300] or (run.stderr or "")[-300:]


def stage(force: bool = False) -> Tuple[bool, str]:
    """
    Fetch, check and stage the newest version. (staged, message).

    Safe to call at any time and from more than one place: it does nothing
    if this copy is current, if the newer version needs a new download, or if
    it fails its checks.
    """
    status = control.update_status() if not force else None
    latest = (status or {}).get("latest") or str(control.fetch_control(force=True).get("latest_version") or "")
    if not latest or not control.is_newer(latest, control.VERSION):
        return False, "This copy is up to date."
    if latest == staged_version():
        return True, f"Version {latest} is ready — it starts next time the app opens."
    if latest in _read("bad").split():
        return False, f"Version {latest} didn't work on this computer, so it was skipped."

    lock = _take_lock()
    if not lock:
        return False, "An update is already being installed — give it a minute."
    work = tempfile.mkdtemp(prefix="bsve_update_", dir=versions_dir())
    try:
        _extract(_download(latest), work)
        if not os.path.exists(os.path.join(work, "app.py")):
            return False, "The download didn't contain the app."
        ok, why = requirements_met(os.path.join(work, "requirements.txt"))
        if not ok:
            _write("needs_download", latest)
            return False, (f"Version {latest} {why}. Download the new version of the "
                           "app to get it.")
        ok, why = _passes_checks(work)
        if not ok:
            # One failure might be a busy computer; two is the version.
            if latest in _read("failed_once").split():
                _write("bad", (_read("bad") + " " + latest).strip())
            else:
                _write("failed_once", (_read("failed_once") + " " + latest).strip())
            return False, f"Version {latest} failed its checks on this computer, so it wasn't installed. ({why})"

        target = os.path.join(versions_dir(), latest)
        shutil.rmtree(target, ignore_errors=True)
        os.replace(work, target)
        previous = staged_version()
        _write("current", latest)
        if previous and previous != latest:
            _write("previous", previous)
        _tidy(keep={latest, previous, control.VERSION})
        return True, f"Version {latest} is installed and checked — it starts next time the app opens."
    except requests.RequestException:
        return False, "Couldn't reach GitHub to fetch the update. Try again later."
    except Exception as exc:
        return False, f"The update couldn't be installed ({type(exc).__name__}). This copy is unchanged."
    finally:
        shutil.rmtree(work, ignore_errors=True)
        _release_lock(lock)


LOCK_STALE_SECONDS = 30 * 60


def _take_lock() -> str:
    """One update at a time: the launcher's and the Update button's can't overlap."""
    import time

    path = os.path.join(versions_dir(), ".updating")
    try:
        if os.path.exists(path) and os.path.getmtime(path) < time.time() - LOCK_STALE_SECONDS:
            os.remove(path)                      # left by an update that was killed
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(descriptor, str(os.getpid()).encode())
        os.close(descriptor)
        return path
    except OSError:
        return ""


def _release_lock(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _tidy(keep: set) -> None:
    for name in os.listdir(versions_dir()):
        path = os.path.join(versions_dir(), name)
        if os.path.isdir(path) and name not in keep and not name.startswith("bsve_update_"):
            shutil.rmtree(path, ignore_errors=True)


def mark_bad(version: str) -> None:
    """The launcher calls this when a staged version fails to start."""
    _write("bad", (_read("bad") + " " + version).strip())
    _write("current", _read("previous") if _read("previous") != version else "")


def pending_download() -> Optional[str]:
    version = _read("needs_download")
    return version if version and control.is_newer(version, control.VERSION) else None


if __name__ == "__main__":
    if "--stage" in sys.argv:
        staged, message = stage()
        print(message)
        sys.exit(0)
    print(__doc__)
