#!/usr/bin/env bash
# Build the double-clickable Mac app.
#
#   ./packaging/build_macos.sh
#
# Produces dist/"Bible Study Video Editor.zip" — everything the app needs is
# inside it, including Python and FFmpeg. Nothing has to be installed on the
# machine it runs on.
#
# Apple Silicon only. An Intel Mac needs this run on an Intel Mac, with
# aarch64 swapped for x86_64 below.
set -e
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

PYTHON_RELEASE="20260814"
PYTHON_VERSION="3.12.14"
ARCH="aarch64"
APP="build/Bible Study Video Editor.app"

echo "==> Fetching a self-contained Python ${PYTHON_VERSION}"
rm -rf build/python "$APP"
mkdir -p build dist
curl -sL -o build/py.tar.gz \
  "https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_RELEASE}/cpython-${PYTHON_VERSION}+${PYTHON_RELEASE}-${ARCH}-apple-darwin-install_only_stripped.tar.gz"
tar xzf build/py.tar.gz -C build && rm build/py.tar.gz

echo "==> Installing dependencies into it"
build/python/bin/python3 -m pip install -q --upgrade pip
build/python/bin/python3 -m pip install -q -r requirements-local.txt

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
for f in app.py transcriber.py matcher.py verifier.py editor.py control.py control.json; do
  cp "$f" "$APP/Contents/Resources/app/"
done
cp -R assets fonts .streamlit "$APP/Contents/Resources/app/"
rm -f "$APP/Contents/Resources/app/.streamlit/secrets.toml"
mv build/python "$APP/Contents/Resources/python"

echo "==> Signing"
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"

echo "==> Packaging"
rm -f "dist/Bible Study Video Editor.zip"
cp packaging/"Read me first.txt" dist/ 2>/dev/null || true
ditto -c -k --sequesterRsrc --keepParent "$APP" "dist/Bible Study Video Editor.zip"

echo
echo "Done: dist/Bible Study Video Editor.zip  ($(du -h "dist/Bible Study Video Editor.zip" | cut -f1))"
