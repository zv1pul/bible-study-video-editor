/**
 * Bible Study Video Editor — key vault and usage log
 * ===================================================
 * A Google Apps Script bound to a Google Sheet. It does two small things so
 * that nobody using the app ever has to handle an API key:
 *
 *   GET  ?code=<group code>   -> the shared API keys, as JSON
 *   POST { event, ... }       -> one row appended to the "Usage" tab
 *
 * Everything you might change lives in the "Settings" tab of the sheet:
 *
 *   GEMINI_API_KEY   the shared Gemini key
 *   GROQ_API_KEY     the shared Groq key
 *   GROUP_CODE       the word people type once when they set the app up
 *   ENABLED          TRUE / FALSE — set FALSE to stop handing out keys
 *
 * Change a value in the sheet and every copy of the app picks it up the
 * next time it asks (it asks at most once a day, and whenever a key stops
 * working). Nothing here is deployed again when you edit the sheet.
 *
 * WHAT ARRIVES IN THE USAGE TAB
 * Only what the app's usage report contains: a random install id, version,
 * Mac or Windows, the event, and a few numbers (lesson length, how long it
 * took). Never the video, transcript, outline, keys, names or paths.
 */

var SETTINGS_SHEET = "Settings";
var USAGE_SHEET = "Usage";
var USAGE_COLUMNS = [
  "received", "install", "version", "platform", "event",
  "lesson_minutes", "seconds", "points", "verified", "cards", "quality",
  "megabytes", "model", "reason", "extra",
];

function doGet(e) {
  var params = (e && e.parameter) || {};
  setup();
  var settings = readSettings();
  var enabled = String(settings.ENABLED || "TRUE").toUpperCase() !== "FALSE";
  var code = String(params.code || "").trim();
  var expected = String(settings.GROUP_CODE || "").trim();

  if (!enabled) {
    return reply({ ok: false, error: "disabled",
                   message: "Shared keys are switched off at the moment." });
  }
  if (!expected || !code || !safeEqual(code, expected)) {
    logUsage({ event: "bad_group_code", install: params.install || "" });
    return reply({ ok: false, error: "bad_code",
                   message: "That group code is not right." });
  }
  logUsage({ event: "keys_fetched", install: params.install || "",
             version: params.version || "", platform: params.platform || "" });
  return reply({
    ok: true,
    keys: {
      GEMINI_API_KEY: String(settings.GEMINI_API_KEY || "").trim(),
      GROQ_API_KEY: String(settings.GROQ_API_KEY || "").trim(),
    },
  });
}

function doPost(e) {
  var body = {};
  try {
    body = JSON.parse((e && e.postData && e.postData.contents) || "{}");
  } catch (err) {
    return reply({ ok: false, error: "bad_json" });
  }
  if (typeof body !== "object" || body === null) body = {};
  setup();
  logUsage(body);
  return reply({ ok: true });
}

// ---------------------------------------------------------------------------

function readSettings() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SETTINGS_SHEET);
  var out = {};
  if (!sheet) return out;
  var rows = sheet.getDataRange().getValues();
  for (var i = 0; i < rows.length; i++) {
    var name = String(rows[i][0] || "").trim();
    if (name && name.charAt(0) !== "#") out[name] = rows[i][1];
  }
  return out;
}

function logUsage(fields) {
  try {
    var lock = LockService.getScriptLock();
    lock.waitLock(5000);
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = ss.getSheetByName(USAGE_SHEET);
    if (!sheet) {
      sheet = ss.insertSheet(USAGE_SHEET);
      sheet.appendRow(USAGE_COLUMNS);
      sheet.setFrozenRows(1);
    }
    var known = {};
    var row = [new Date()];
    for (var i = 1; i < USAGE_COLUMNS.length - 1; i++) {
      var key = USAGE_COLUMNS[i];
      known[key] = true;
      row.push(clean(fields[key]));
    }
    var extra = {};
    for (var k in fields) {
      if (!known[k] && k !== "at") extra[k] = clean(fields[k]);
    }
    row.push(Object.keys(extra).length ? JSON.stringify(extra) : "");
    sheet.appendRow(row);
    lock.releaseLock();
  } catch (err) {
    // Logging must never make the app fail.
  }
}

function clean(value) {
  if (value === undefined || value === null) return "";
  var text = String(value);
  return text.length > 200 ? text.slice(0, 200) : text;
}

function safeEqual(a, b) {
  if (a.length !== b.length) return false;
  var diff = 0;
  for (var i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function reply(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

/** Lays out the Settings and Usage tabs. Safe to run any number of times. */
function setup() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  if (ss.getSheetByName(SETTINGS_SHEET) && ss.getSheetByName(USAGE_SHEET)) return;
  var sheet = ss.getSheetByName(SETTINGS_SHEET) || ss.insertSheet(SETTINGS_SHEET);
  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 6, 3).setValues([
      ["# Setting", "Value", "What it does"],
      ["GEMINI_API_KEY", "", "Shared Gemini key (aistudio.google.com/apikey)"],
      ["GROQ_API_KEY", "", "Shared Groq key (console.groq.com/keys)"],
      ["GROUP_CODE", "", "The word people type once to connect their copy"],
      ["ENABLED", "TRUE", "Set to FALSE to stop handing out keys"],
      ["# Edit the Value column only. Changes apply within a day, or at once after 'Reconnect' in the app.", "", ""],
    ]);
    sheet.setFrozenRows(1);
    sheet.setColumnWidth(1, 180);
    sheet.setColumnWidth(2, 420);
    sheet.setColumnWidth(3, 420);
  }
  var usage = ss.getSheetByName(USAGE_SHEET) || ss.insertSheet(USAGE_SHEET);
  if (usage.getLastRow() === 0) {
    usage.appendRow(USAGE_COLUMNS);
    usage.setFrozenRows(1);
  }
  var first = ss.getSheets()[0];
  if (first.getName() === "Sheet1" && ss.getSheets().length > 1) ss.deleteSheet(first);
}
