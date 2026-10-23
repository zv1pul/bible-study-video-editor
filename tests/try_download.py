"""
Open a finished download the way a person would, on the computer it is for.

    python tests/try_download.py FOLDER_WITH_THE_ZIP_OR_WHAT_IT_UNPACKED_TO

Starts it with its own launcher (the double-click), waits for the page,
opens it a second time (which must reuse the running copy, not start
another), runs the self-check that lives inside the download, and stops it.
Uses only the standard library: the computer running it has nothing else.
"""
import glob
import json
import traceback
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

# Windows writes to a console or pipe in its old code page by default; the
# self-check's report has dashes and ticks it can't hold.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def say(message: str) -> None:
    print(message, flush=True)


folder = os.path.abspath(sys.argv[1])
zips = glob.glob(os.path.join(folder, "*.zip"))
if zips:
    import zipfile

    say(f"unpacking {os.path.basename(zips[0])}")
    unpacked = os.path.join(folder, "unpacked")
    if sys.platform == "darwin":
        # ditto keeps the app's permissions and signature, as Finder does.
        subprocess.run(["ditto", "-x", "-k", zips[0], unpacked], check=True)
    else:
        with zipfile.ZipFile(zips[0]) as bundle:
            bundle.extractall(unpacked)
    folder = unpacked
    say("unpacked")
home = tempfile.mkdtemp(prefix="bsve_try_home_")
env = dict(os.environ, BSVE_HOME=home, BROWSER="true" if os.name != "nt" else "echo",
           PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
problems = []

if sys.platform == "darwin":
    apps = glob.glob(os.path.join(folder, "**", "*.app"), recursive=True)
    resources = os.path.join(apps[0], "Contents", "Resources")
    python = os.path.join(resources, "python", "bin", "python3")
    start = [python, os.path.join(resources, "launcher.py")]
    app_dir = os.path.join(resources, "app")
else:
    found = glob.glob(os.path.join(folder, "**", "launcher.py"), recursive=True)
    say(f"launcher: {found}")
    launcher = found[0]
    root = os.path.dirname(launcher)
    python = os.path.join(root, "python", "python.exe")
    start = [os.path.join(root, "python", "pythonw.exe"), launcher]
    app_dir = os.path.join(root, "app")


def running_port(wait: float):
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            with open(os.path.join(home, "running.json"), encoding="utf-8") as handle:
                port = json.load(handle)["port"]
            urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=3)
            return port
        except Exception:
            time.sleep(2)
    return None


def servers() -> int:
    """How many copies of this download's app server are running."""
    if os.name == "nt":
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process | ForEach-Object CommandLine"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace").stdout or ""
    else:
        out = subprocess.run(["ps", "-axww", "-o", "command"], capture_output=True,
                             text=True).stdout
    return sum(1 for line in out.splitlines()
               if "streamlit" in line and "server.port" in line
               and folder.lower() in line.lower())


try:
    say(f"starting: {start}")
    started = time.time()
    subprocess.Popen(start, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    port = running_port(240)
    if not port:
        log = os.path.join(home, "last-run.log")
        tail = open(log, encoding="utf-8", errors="replace").read()[-800:] if os.path.exists(log) else ""
        problems.append(f"it didn't start within 4 minutes. {tail}")
    else:
        print(f"started in {time.time() - started:.0f}s on port {port}")
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).read()
        if b"<html" not in page.lower():
            problems.append("the page didn't load")

        subprocess.Popen(start, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(15)
        if running_port(5) != port:
            problems.append("opening it again started a different copy")
        count = servers()
        print(f"app servers running after opening it twice: {count}")
        if count != 1:
            problems.append(f"opening it again left {count} copies running")

        check = subprocess.run([python, "selfcheck.py"], cwd=app_dir, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               env=dict(env, BSVE_HOME=tempfile.mkdtemp()))
        print(check.stdout[-1500:])
        if "ALL OK" not in check.stdout:
            problems.append("its own self-check didn't pass")

        # Stop just the app — not every Python program, which on a computer
        # running this test with Python includes the test itself.
        def stop(at_port):
            if os.name == "nt":
                with open(os.path.join(home, "running.json"), encoding="utf-8") as handle:
                    pid = json.load(handle)["pid"]
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
            else:
                subprocess.run(["pkill", "-f", f"server.port {at_port}"])
            time.sleep(3)

        # Restarted, it must come back at the same address, so a tab left
        # open reconnects by itself.
        stop(port)
        subprocess.Popen(start, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        again = running_port(240)
        say(f"after a restart: port {again}")
        if again != port:
            problems.append(f"after a restart it came back at a different address ({again}, was {port})")
        if again:
            stop(again)
except Exception:
    problems.append("the test itself stopped: " + traceback.format_exc()[-1200:])

for problem in problems:
    print("FAILED:", problem)
    if os.environ.get("GITHUB_ACTIONS"):
        print("::error title=Trying the download::" + problem.replace("\n", "%0A"))
print("ALL OK" if not problems else f"{len(problems)} FAILURE(S)")
sys.exit(1 if problems else 0)
