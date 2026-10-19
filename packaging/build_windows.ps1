# Build the Windows download.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#
# Produces dist\"Bible Study Video Editor - Windows.zip": a folder holding
# its own Python and FFmpeg, the app, and "Start Bible Study Video Editor".
# Nothing has to be installed on the machine it runs on. The finished app
# checks itself before it is zipped; a build that fails is never produced.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$Root = (Get-Location).Path

$PythonRelease = "20260814"
$PythonVersion = "3.12.14"
$Name = "Bible Study Video Editor"
$Out = Join-Path $Root "build\$Name"
$Zip = Join-Path $Root "dist\$Name - Windows.zip"

Write-Host "==> Fetching a self-contained Python $PythonVersion"
Remove-Item -Recurse -Force "build\python", $Out -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path "build", "dist" | Out-Null
$Url = "https://github.com/astral-sh/python-build-standalone/releases/download/$PythonRelease/cpython-$PythonVersion+$PythonRelease-x86_64-pc-windows-msvc-install_only.tar.gz"
Invoke-WebRequest -Uri $Url -OutFile "build\py.tar.gz"
tar -xzf "build\py.tar.gz" -C build
Remove-Item "build\py.tar.gz"
$Py = Join-Path $Root "build\python\python.exe"

Write-Host "==> Installing dependencies into it"
& $Py -m pip install -q --upgrade pip
& $Py -m pip install -q -r requirements-local.txt -c constraints.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "==> Trimming what is never used at runtime"
$SP = "build\python\Lib\site-packages"
Get-ChildItem -Path "build\python" -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem -Path $SP -Recurse -Directory | Where-Object { $_.Name -in @("tests", "test") } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$SP\pip", "$SP\setuptools", "$SP\pkg_resources" -ErrorAction SilentlyContinue

Write-Host "==> Assembling"
New-Item -ItemType Directory -Force -Path "$Out\app\tests" | Out-Null
# The same list of files an update replaces (updater.APP_FILES / APP_DIRS).
foreach ($f in @("app.py", "control.py", "control.json", "editor.py", "matcher.py",
                 "selfcheck.py", "transcriber.py", "updater.py", "verifier.py", "requirements.txt")) {
  Copy-Item $f "$Out\app\"
}
Copy-Item "tests\test_units.py" "$Out\app\tests\"
Copy-Item -Recurse "tests\fixtures" "$Out\app\tests\fixtures"
foreach ($d in @("assets", "fonts", ".streamlit")) { Copy-Item -Recurse $d "$Out\app\$d" }
Remove-Item "$Out\app\.streamlit\secrets.toml" -ErrorAction SilentlyContinue
Copy-Item "packaging\launcher.py" "$Out\launcher.py"
Copy-Item "packaging\Start Bible Study Video Editor.bat" "$Out\"
Copy-Item "packaging\Read me first (Windows).txt" "$Out\Read me first.txt"
Move-Item "build\python" "$Out\python"

Write-Host "==> Checking the finished app on this machine"
$env:BSVE_HOME = Join-Path $Root "build\selfcheck-home"
Push-Location "$Out\app"
& "$Out\python\python.exe" selfcheck.py --update-gate
$ok = $LASTEXITCODE
Pop-Location
Remove-Item -Recurse -Force $env:BSVE_HOME -ErrorAction SilentlyContinue
if ($ok -ne 0) { throw "The finished app failed its own checks" }
Get-ChildItem -Path $Out -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

Write-Host "==> Packaging"
Remove-Item $Zip -ErrorAction SilentlyContinue
Compress-Archive -Path $Out -DestinationPath $Zip -CompressionLevel Optimal
$size = [math]::Round((Get-Item $Zip).Length / 1MB)
Write-Host ""
Write-Host "Done: $Zip  ($size MB)"
