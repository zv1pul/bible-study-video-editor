"""
launcher.py — starts Bible Study Video Editor. Mac and Windows share it.

It lives beside the app's own Python, outside the code that updates, so a
bad update can never break the thing that starts the app:

  1. Start the newest version that has passed its checks on this computer
     (an update staged by updater.py), or the version that came with the
     download.
  2. If that version doesn't start within a minute and a half, remember it
     as bad, go back to the one before, and start that instead.
  3. Open the browser, then quietly fetch, check and stage the next update
     for the next time the app opens.

Opening the app while it is already running just opens the browser on it
again — never a second copy. The launcher then steps aside: the app runs on
its own and stops itself a few minutes after its browser tab is closed
(unless it is in the middle of a job), so nothing is left running unseen.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

RES = os.path.dirname(os.path.abspath(__file__))
# On Windows the launcher runs under pythonw (no console window); the app
# itself runs under python with its window suppressed, so its output can be
# logged.
PYTHON = sys.executable
if PYTHON.lower().endswith("pythonw.exe"):
    candidate = PYTHON[:-len("pythonw.exe")] + "python.exe"
    if os.path.exists(candidate):
        PYTHON = candidate
BUNDLED = os.path.join(RES, "app")
HOME = os.environ.get("BSVE_HOME") or os.path.join(os.path.expanduser("~"), ".bible-study-video-editor")
VERSIONS = os.path.join(HOME, "app")
START_TIMEOUT = 90
RUNNING = os.path.join(HOME, "running.json")


def _read(name):
    try:
        with open(os.path.join(VERSIONS, name), encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _write(name, value):
    os.makedirs(VERSIONS, exist_ok=True)
    with open(os.path.join(VERSIONS, name), "w", encoding="utf-8") as handle:
        handle.write(value)


def pick():
    """(folder, version) to start: the staged update if it's good, else the download's."""
    current = _read("current")
    if current and current not in _read("bad").split():
        folder = os.path.join(VERSIONS, current)
        if os.path.exists(os.path.join(folder, "app.py")):
            return folder, current
    return BUNDLED, ""


# Always the same address when it's free, so a browser tab left open — over
# a restart, an update or the computer sleeping — reconnects by itself
# instead of showing "This site can't be reached". Another free one only if
# something else on the computer already uses it.
PREFERRED_PORT = 47823


def free_port():
    for port in (PREFERRED_PORT, 0):
        try:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
                return probe.getsockname()[1]
        except OSError:
            continue
    return 0


def environment():
    env = dict(os.environ)
    env.update({
        "BSVE_HOME": HOME,
        "BSVE_PACKAGED": "1",
        "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
        "STREAMLIT_SERVER_HEADLESS": "true",
        "STREAMLIT_GLOBAL_DEVELOPMENT_MODE": "false",
        "PYTHONDONTWRITEBYTECODE": "1",
        "BSVE_LAUNCHER_PID": str(os.getpid()),
    })
    return env


def start(folder):
    """(process, port) once the app answers, or (process, None) if it doesn't."""
    port = free_port()
    log = open(os.path.join(HOME, "last-run.log"), "w", encoding="utf-8")
    flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    process = subprocess.Popen(
        [PYTHON, "-m", "streamlit", "run", "app.py",
         "--server.port", str(port), "--server.address", "127.0.0.1",
         "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        cwd=folder, stdout=log, stderr=subprocess.STDOUT, env=environment(),
        creationflags=flags,
        # Its own session, so it carries on after the launcher has finished.
        start_new_session=not sys.platform.startswith("win"),
    )
    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        if process.poll() is not None:
            return process, None
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2)
            return process, port
        except Exception:
            time.sleep(0.5)
    return process, None


def complain(message):
    """Tell the person, in a window — they may never see a terminal."""
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e",
                        f'display dialog "{message}" with title "Bible Study Video Editor" '
                        'buttons {"OK"} default button "OK" with icon caution'])
    elif sys.platform.startswith("win"):
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, "Bible Study Video Editor", 0x30)
    else:
        print(message, file=sys.stderr)


def start_with_fallback():
    """(process, port, folder, version): the newest version that starts."""
    folder, version = pick()
    process, port = start(folder)
    if port is None and version:
        # The staged update won't start here: never try it again, go back.
        process.kill()
        _write("bad", (_read("bad") + " " + version).strip())
        previous = _read("previous")
        _write("current", previous if previous and previous != version else "")
        folder, version = pick()
        process, port = start(folder)
    return process, port, folder, version


def already_running():
    """The port of a copy that is running and answering, or None."""
    try:
        with open(RUNNING, encoding="utf-8") as handle:
            port = int(json.load(handle)["port"])
        urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=3)
        return port
    except Exception:
        return None


def main():
    os.makedirs(HOME, exist_ok=True)

    port = already_running()
    if port:
        webbrowser.open(f"http://127.0.0.1:{port}")
        return

    process, port, folder, version = start_with_fallback()
    if port is None:
        try:
            process.kill()
        except Exception:
            pass
        complain("The app couldn't start. Restart your computer and try again; if it "
                 "still won't open, send the file last-run.log from " + HOME +
                 " to whoever gave you the app.")
        sys.exit(1)

    try:
        with open(RUNNING, "w", encoding="utf-8") as handle:
            json.dump({"port": port, "pid": process.pid, "version": version}, handle)
    except OSError:
        pass
    webbrowser.open(f"http://127.0.0.1:{port}")

    # Next time's update, fetched and checked in the background.
    try:
        quiet = open(os.path.join(HOME, "last-update.log"), "w", encoding="utf-8")
        subprocess.Popen([PYTHON, os.path.join(folder, "updater.py"), "--stage"],
                         cwd=folder, env=environment(), stdout=quiet,
                         stderr=subprocess.STDOUT,
                         creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)),
                         start_new_session=not sys.platform.startswith("win"))
    except Exception:
        pass


if __name__ == "__main__":
    main()
