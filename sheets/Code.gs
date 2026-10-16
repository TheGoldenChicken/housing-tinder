/**
 * Housing Tinder — Google Apps Script backend.
 *
 * Stores swipes in the Google Sheet this script is bound to:
 *   - "Likes"  : one row per right-swipe with all listing details + link (review these later)
 *   - "Swipes" : every swipe (like / pass / unlike), used to sync state between devices
 *
 * Setup (once):
 *   1. Create a Google Sheet. Extensions → Apps Script. Replace the code with this file. Save.
 *   2. Deploy → New deployment → type "Web app". Execute as: Me. Who has access: Anyone. Deploy.
 *   3. Copy the Web app URL (ends in /exec) into the app's Settings → "Sheet endpoint".
 *
 * API:
 *   GET  ?user=karl                     → {"liked":[ids], "superliked":[ids], "passed":[ids]}
 *   POST body (text/plain JSON):
 *        {"user":"karl","action":"like"|"superlike"|"pass"|"unlike","listing":{...}}   → {"ok":true}
 *        {"user":"karl","action":"batch","swipes":[{action, listing}, ...]} → {"ok":true,"n":N}
 */

var LIKE_COLUMNS = [
  "liked_at", "user", "id", "category", "property_type", "address", "zip_code", "city", "municipality",
  "price", "monthly_cost", "deposit", "size_m2", "lot_m2", "rooms", "floor", "bathrooms", "year_built",
  "energy_label", "balcony", "elevator", "pets_allowed", "available_from", "listed_date", "first_seen",
  "url", "other_urls", "title", "description", "realtor", "image", "lat", "lon", "sources", "super"
];
var SWIPE_COLUMNS = ["at", "user", "action", "id", "category", "address", "url"];

function doGet(e) {
  var user = ((e && e.parameter && e.parameter.user) || "").trim();
  var swipes = sheet_("Swipes", SWIPE_COLUMNS);
  var rows = swipes.getDataRange().getValues();
  var state = {}; // id -> last action
  for (var i = 1; i < rows.length; i++) {
    if (String(rows[i][1]) !== user) continue;
    state[String(rows[i][3])] = String(rows[i][2]);
  }
  var liked = [], superliked = [], passed = [];
  for (var id in state) {
    if (state[id] === "like") liked.push(id);
    else if (state[id] === "superlike") superliked.push(id);
    else if (state[id] === "pass") passed.push(id);
  }
  return json_({ liked: liked, superliked: superliked, passed: passed });
}

function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var body = JSON.parse(e.postData.contents);
    var user = String(body.user || "").trim();
    if (!user) return json_({ ok: false, error: "missing user" });
    var swipes = body.action === "batch" ? body.swipes : [{ action: body.action, listing: body.listing }];
    var n = 0;
    swipes.forEach(function (s) { record_(user, s.action, s.listing || {}); n++; });
    return json_({ ok: true, n: n });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

function record_(user, action, l) {
  var now = new Date();
  var swipes = sheet_("Swipes", SWIPE_COLUMNS);
  swipes.appendRow([now, user, action, l.id || "", l.category || "", l.address || "", l.url || ""]);
  if (action === "like" || action === "superlike") {
    var likes = sheet_("Likes", LIKE_COLUMNS);
    likes.appendRow(LIKE_COLUMNS.map(function (c) {
      if (c === "liked_at") return now;
      if (c === "user") return user;
      if (c === "super") return action === "superlike";
      if (c === "image") return (l.images && l.images[0]) || "";
      if (c === "other_urls") return l.extra && l.extra.urls ? Object.values(l.extra.urls).join(" ") : "";
      if (c === "sources") return (l.sources || []).join(",");
      var v = l[c];
      return v === undefined || v === null ? "" : v;
    }));
  } else if (action === "unlike") {
    removeLike_(user, l.id);
  }
}

function removeLike_(user, id) {
  var likes = sheet_("Likes", LIKE_COLUMNS);
  var rows = likes.getDataRange().getValues();
  for (var i = rows.length - 1; i >= 1; i--) {
    if (String(rows[i][1]) === user && String(rows[i][2]) === String(id)) likes.deleteRow(i + 1);
  }
}

function sheet_(name, columns) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.appendRow(columns);
    sh.setFrozenRows(1);
  }
  return sh;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
