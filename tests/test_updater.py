"""
The update path, simulated end to end — no network, no real release.

    python tests/test_updater.py

Builds fake releases from this checkout and checks that:
  - a good update is downloaded, checked and staged, and the launcher picks it;
  - an update that fails its checks is refused and remembered as bad;
  - an update needing a package this copy lacks asks for a new download;
  - an update that installs but will not start is rolled back by the launcher.
"""
import io
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

home = tempfile.mkdtemp(prefix="bsve_update_test_")
os.environ["BSVE_HOME"] = home

import control  # noqa: E402
import updater  # noqa: E402

FAILED = []


def check(name, condition, detail=""):
    print(("  ok    " if condition else "  FAIL  ") + name + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILED.append(name)


def release(version, tweak=None):
    """A zip shaped like GitHub's tag download, made from this checkout."""
    buffer = io.BytesIO()
    root = f"bible-study-video-editor-{version}/"
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in updater.APP_FILES:
            text = open(os.path.join(ROOT, name), encoding="utf-8").read()
            if name == "control.py":
                text = text.replace(f'VERSION = "{control.VERSION}"', f'VERSION = "{version}"')
            if tweak:
                text = tweak(name, text)
            archive.writestr(root + name, text)
        for folder in updater.APP_DIRS:
            for base, _dirs, files in os.walk(os.path.join(ROOT, folder)):
                for file in files:
                    if file == "secrets.toml" or file.endswith(".pyc"):
                        continue
                    full = os.path.join(base, file)
                    relative = os.path.relpath(full, ROOT)
                    data = open(full, "rb").read()
                    if tweak and relative.endswith((".py", ".toml", ".txt")):
                        data = tweak(relative, data.decode("utf-8")).encode("utf-8")
                    archive.writestr(root + relative, data)
    return buffer.getvalue()


def offer(version, archive):
    control.fetch_control = lambda force=False: {"latest_version": version}
    control.update_status = lambda: {"latest": version}
    updater._download = lambda v: archive


# 1. A good update.
newer = "9.0.0"
staged, message = updater.stage(force=True)  # nothing offered yet: real control.json
offer(newer, release(newer))
staged, message = updater.stage(force=True)
check("a good update is staged", staged and updater.staged_version() == newer, message)
check("the staged copy is complete",
      os.path.exists(os.path.join(home, "app", newer, "tests", "test_units.py")))

# 2. An update that fails its own checks.
def break_a_check(name, text):
    if name.endswith("test_units.py"):
        return text.replace('print(f"{len(PASSED)} passed")',
                            'check("deliberately broken", 1, 2)\nprint(f"{len(PASSED)} passed")')
    return text

offer("9.1.0", release("9.1.0", break_a_check))
staged, message = updater.stage(force=True)
check("an update failing its checks is refused", not staged and "failed its checks" in message, message)
check("…and remembered as bad", "9.1.0" in updater._read("bad").split())
check("…and the good one stays staged", updater.staged_version() == newer)

# 3. An update that needs a package this copy doesn't have.
def need_a_package(name, text):
    return text + "\nsome-package-nobody-has>=1\n" if name == "requirements.txt" else text

offer("9.2.0", release("9.2.0", need_a_package))
staged, message = updater.stage(force=True)
check("an update needing new packages asks for a new download",
      not staged and "download" in message.lower(), message)

# 4. The launcher: picks the staged version, and rolls back one that won't start.
sys.path.insert(0, os.path.join(ROOT, "packaging"))
import launcher  # noqa: E402

launcher.HOME, launcher.VERSIONS = home, os.path.join(home, "app")
launcher.BUNDLED = ROOT
folder, version = launcher.pick()
check("the launcher starts the staged update", version == newer, f"{folder} {version}")

def crash_on_start(name, text):
    return "raise RuntimeError('broken on purpose')\n" + text if name == "app.py" else text

offer("9.3.0", release("9.3.0", crash_on_start))
staged, message = updater.stage(force=True)
check("an update whose page crashes is caught before it is installed",
      not staged and "App starts" in message, message)

# A version can pass its checks and still fail to start — say its files are
# damaged on disk afterwards. Only the launcher can catch that, so plant one:
# a stray file that stops the server from starting at all.
corrupt = os.path.join(home, "app", "9.4.0")
shutil.copytree(os.path.join(home, "app", newer), corrupt)
with open(os.path.join(corrupt, "streamlit.py"), "w") as handle:
    handle.write("raise SystemExit('damaged on disk')\n")
launcher._write("previous", newer)
launcher._write("current", "9.4.0")
folder, version = launcher.pick()
check("the launcher would start the damaged version", version == "9.4.0", version)
launcher.START_TIMEOUT = 25
process, port, folder, version = launcher.start_with_fallback()
check("…it doesn't start, so the launcher goes back to the one before and starts that",
      port is not None and version == newer, f"port {port}, version {version}")
check("…and never tries the damaged one again", "9.4.0" in launcher._read("bad").split())
process.kill()

shutil.rmtree(home, ignore_errors=True)
print("ALL OK" if not FAILED else f"{len(FAILED)} FAILURE(S)")
sys.exit(1 if FAILED else 0)
