# Key vault and usage log

`Code.gs` is a Google Apps Script that lives inside a Google Sheet in the
maintainer's Google account. It hands the group's shared Gemini and Groq
keys to any copy of the app that presents the group code, and it records
the app's anonymous usage reports as rows in the sheet.

## Setting it up (once)

1. Create a new Google Sheet (sheets.new). Name it "Bible Study Video Editor — keys and usage".
2. Extensions → Apps Script. Delete what is there, paste in `Code.gs`, save.
3. Run the `setup` function once (choose `setup` in the toolbar, click Run,
   approve the permission prompt). This lays out the Settings and Usage tabs.
4. Deploy → New deployment → type "Web app":
   - Execute as: **Me**
   - Who has access: **Anyone**
   Click Deploy, approve, and copy the web app URL (ends in `/exec`).
5. In the sheet's Settings tab fill in GEMINI_API_KEY, GROQ_API_KEY and a
   GROUP_CODE (any word or phrase the group will be told).
6. Put the URL in `control.json` as `vault_url` and `telemetry_url`, and in
   `control.VAULT_URL`, then commit.

## Day to day

- Rotate a key: paste the new one into the Settings tab. Every copy picks it
  up within a day, or immediately the next time the old key is refused.
- Stop everything: set ENABLED to FALSE.
- Change the group code: edit GROUP_CODE; people re-enter it under
  "Group code" in the app's sidebar.
- Usage: the Usage tab, one row per event. Nothing in it can identify a
  person or a lesson.

Re-deploying the script is only needed if `Code.gs` itself changes
(Deploy → Manage deployments → edit → new version).
