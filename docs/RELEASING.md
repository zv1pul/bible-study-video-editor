# Releasing a new version

Almost all of it is automatic. Two steps are deliberately left to a person:
pressing **Publish**, and telling installed copies a new version exists.

## 1. Make the change and check it

```bash
python tests/test_units.py                  # seconds, no API key
python selfcheck.py                         # the app page, a test render, sound decoding
python tests/test_updater.py                # updates install, or are refused, or roll back
python tests/run_long_countdown.py          # a 3-minute countdown renders at the right length
python tests/build_corpus.py /tmp/corpus && python tests/run_corpus.py /tmp/corpus gemini
python tests/build_formats.py /tmp/formats && python tests/run_formats.py /tmp/formats
```

If a real lesson came out wrong, add its case to `tests/test_units.py` and a
row to `docs/LESSONS-LEARNED.md` before fixing it.

## 2. Raise the version

In `control.py`: `VERSION = "1.3.0"` (and the two version strings in
`packaging/Info.plist`). Commit and push to `main`.

GitHub runs **Checks** on a Mac and on Windows (the Actions tab on the
repository). Wait for it to go green. If it doesn't, nothing reaches anyone.

## 3. Tag it

```bash
git tag v1.3.0 && git push origin v1.3.0
```

The **Release** workflow runs every check again, builds the three downloads —
Mac (Apple Silicon), Mac (Intel), Windows — each of which checks itself before
it is zipped, and creates a **draft** release.

## 4. Publish — by hand

Open the draft under Releases, look it over, press **Publish**. From then on
the "latest release" link gives people the new version.

## 5. Tell installed copies — by hand

In `control.json` set `"latest_version": "1.3.0"`, commit and push. Within an
hour every installed copy fetches that version's code, runs its checks on its
own computer, and starts using it the next time it opens. If it fails there,
that copy keeps the version it has.

An update that needs a library the download doesn't include can't be
installed that way; those copies are shown a link to download the new version
instead (their settings are kept).

## Libraries

`constraints.txt` pins every library to the exact version the checks passed
with, and every build and check installs with it. To move to newer ones:
install without `-c constraints.txt`, run every check, and only if they all
pass, regenerate it with `pip freeze` (keep the note at the top).

## If something goes wrong after a release

- **Say something to everyone**: put a sentence in `control.json`'s
  `"message"` (and `"message_kind": "warning"`); every copy shows it within
  the hour.
- **Fix it**: release 1.3.1 the same way. Copies move to it on their own.

## The shared AI keys

Every copy uses the group's keys from `control.json` (`"shared_keys"`) — the
live file wins over the one a copy shipped with, so a change reaches every
installed copy within the hour, with no new download.

- **Only free-tier keys** (no billing on the Google project, no card on the
  Groq account). They are masked, not secret: anyone determined can read
  them out of the app, so the most they must ever be able to cost is a day's
  free allowance.
- **To replace them** (a key leaked, or keeps running out):

  ```bash
  python -c "import control, json; print(json.dumps(control.mask_keys({'GEMINI_API_KEY': 'NEW', 'GROQ_API_KEY': 'NEW'})))"
  ```

  Paste the output as `"shared_keys"` in `control.json`, commit, push.
- **To switch them off**: set `"shared_keys": ""`. Copies then match by
  keywords, or use a key somebody saved under More settings → AI keys.
