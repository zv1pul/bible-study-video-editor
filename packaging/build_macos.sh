#!/usr/bin/env bash
# Build the double-clickable Mac app.
#
#   ./packaging/build_macos.sh
#
# Produces dist/"Bible Study Video Editor.zip" — everything the app needs is
# inside it, including Python and FFmpeg. Nothing has to be installed on the
# machine it runs on.
#
# Builds for the kind of Mac it runs on — Apple Silicon or Intel — so run it
# on each (GitHub's release workflow does both). The finished app checks
# itself before it is zipped: a build that fails its own checks is never
# produced.
set -e
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

PYTHON_RELEASE="20260814"
PYTHON_VERSION="3.12.14"
case "${ARCH:-$(uname -m)}" in
  arm64|aarch64) ARCH="aarch64"; LABEL="Apple Silicon" ;;
  x86_64)        ARCH="x86_64";  LABEL="Intel" ;;
  *) echo "Unsupported Mac: $(uname -m)"; exit 1 ;;
esac
APP="build/Bible Study Video Editor.app"
ZIP="dist/Bible Study Video Editor - Mac (${LABEL}).zip"

echo "==> Fetching a self-contained Python ${PYTHON_VERSION}"
rm -rf build/python "$APP"
mkdir -p build dist
curl -sL -o build/py.tar.gz \
  "https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_RELEASE}/cpython-${PYTHON_VERSION}+${PYTHON_RELEASE}-${ARCH}-apple-darwin-install_only_stripped.tar.gz"
tar xzf build/py.tar.gz -C build && rm build/py.tar.gz

echo "==> Installing dependencies into it"
build/python/bin/python3 -m pip install -q --upgrade pip
build/python/bin/python3 -m pip install -q -r requirements-local.txt -c constraints.txt

echo "==> Trimming what is never used at runtime"
SP=build/python/lib/python3.12/site-packages
find build/python -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find build/python -name "*.pyc" -delete 2>/dev/null || true
find "$SP" -type d \( -name tests -o -name test \) -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf "$SP"/pip "$SP"/pip-*.dist-info "$SP"/setuptools "$SP"/setuptools-*.dist-info \
       "$SP"/pkg_resources "$SP"/pyarrow/include 2>/dev/null || true

echo "==> Assembling the app bundle"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"
cp packaging/Info.plist "$APP/Contents/Info.plist"
cp packaging/launch "$APP/Contents/MacOS/launch"
chmod +x "$APP/Contents/MacOS/launch"
# The same list of files an update replaces (updater.APP_FILES / APP_DIRS).
for f in app.py control.py control.json editor.py matcher.py selfcheck.py \
         transcriber.py updater.py verifier.py requirements.txt; do
  cp "$f" "$APP/Contents/Resources/app/"
done
mkdir -p "$APP/Contents/Resources/app/tests"
cp tests/test_units.py "$APP/Contents/Resources/app/tests/"
cp -R tests/fixtures "$APP/Contents/Resources/app/tests/"
cp -R assets fonts .streamlit "$APP/Contents/Resources/app/"
rm -f "$APP/Contents/Resources/app/.streamlit/secrets.toml"
cp packaging/launcher.py "$APP/Contents/Resources/launcher.py"
mv build/python "$APP/Contents/Resources/python"

echo "==> Checking the finished app on this machine"
( cd "$APP/Contents/Resources/app" && \
  BSVE_HOME="$ROOT/build/selfcheck-home" ../python/bin/python3 selfcheck.py --update-gate )
rm -rf "$ROOT/build/selfcheck-home"
find "$APP" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "==> Signing"
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"

echo "==> Packaging"
rm -f "$ZIP" "dist/Bible Study Video Editor.zip"
cp packaging/"Read me first.txt" dist/ 2>/dev/null || true
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"

echo
echo "Done: $ZIP  ($(du -h "$ZIP" | cut -f1))"
